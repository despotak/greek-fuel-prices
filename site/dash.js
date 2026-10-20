/*
 * dash.js — a static stand-in for the claude.ai Dashboard host.
 *
 * The page was written against the `dash` API that claude.ai injects. This file
 * provides the same API on a plain static site, so the page runs unchanged.
 *
 * Load order: d3 v7, then `window.DASH_DATASETS` / `window.DASH_PARAMS`, then this
 * file, then the page's own scripts.
 *
 *   window.DASH_DATASETS = { heating_oil: 'data/heating_oil.csv', ... }
 *   window.DASH_PARAMS   = { area: 'Αττική', fuel: 'heating_oil', range: 'all' }
 *
 * Contracts the page relies on:
 *   - dash.data(id) returns the SAME `data` array until that dataset is refetched
 *     (the page caches parsed series by array identity). Calcs are recomputed only
 *     when the identity of one of their inputs changes.
 *   - Reading an id starts its fetch, so a dataset is fetched only once something
 *     asks for it. The data-source stamping below peeks and never fetches.
 *   - CSV cells stay strings; an empty cell is ''.
 */
(() => {
  'use strict';

  const SOURCES = { ...(window.DASH_DATASETS || {}) };
  const DEFAULTS = { ...(window.DASH_PARAMS || {}) };
  const COLORS = Array.from({ length: 8 }, (_, i) => `var(--cds-chart-categorical-${i + 1})`);
  const EMPTY = Object.freeze([]);

  const datasets = new Map(); // id -> { status, data, columns, message, refreshing, meta, gen }
  const calcs = new Map(); // id -> { inputs, fn, title, description, seen, result, columns, error }
  const listeners = [];

  // ---- datasets -----------------------------------------------------------------

  const fetchDataset = (id) => {
    const url = SOURCES[id];
    let d = datasets.get(id);
    if (!d) {
      d = { status: 'loading', data: EMPTY, columns: [], message: null, refreshing: false, meta: { id, kind: 'dataset', url }, gen: 0 };
      datasets.set(id, d);
    } else if (d.status === 'ok') {
      d.refreshing = true;
    } else {
      d.status = 'loading';
      d.message = null;
    }
    const gen = ++d.gen;
    fetch(url, { cache: 'no-cache' })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
        return Promise.all([res.text(), res.headers.get('Last-Modified')]);
      })
      .then(([text, modified]) => {
        if (gen !== d.gen) return;
        const rows = window.d3.csvParse(text.replace(/^﻿/, ''));
        d.data = rows;
        d.columns = rows.columns || [];
        d.status = 'ok';
        d.message = null;
        d.refreshing = false;
        d.meta = { ...d.meta, rows: rows.length, loadedAt: new Date().toISOString(), lastModified: modified };
      })
      .catch((err) => {
        if (gen !== d.gen) return;
        console.error(`dash: dataset "${id}" failed to load:`, err);
        d.refreshing = false;
        d.message = String(err && err.message ? err.message : err);
        // A failed refresh keeps the rows it already had.
        if (d.status !== 'ok') d.status = 'error';
      })
      .finally(() => { if (gen === d.gen) schedule(); });
    return d;
  };

  // ---- calcs ----------------------------------------------------------------------

  const sameRefs = (a, b) => a && b && a.length === b.length && a.every((x, i) => x === b[i]);

  const columnsOf = (rows) => {
    if (!Array.isArray(rows)) return [];
    const cols = new Set();
    for (const r of rows) if (r && typeof r === 'object') for (const k of Object.keys(r)) cols.add(k);
    return [...cols];
  };

  const evalCalc = (id, c, stack) => {
    const meta = { id, kind: 'calc', title: c.title, description: c.description, inputs: c.inputs };
    const ins = c.inputs.map((i) => read(i, stack));
    const bad = ins.findIndex((s) => s.status === 'error' || s.status === 'missing');
    if (bad >= 0) {
      const s = ins[bad];
      return { status: 'error', data: EMPTY, columns: [], message: `input "${c.inputs[bad]}" ${s.status === 'missing' ? 'does not exist' : 'failed: ' + s.message}`, refreshing: false, meta };
    }
    const refreshing = ins.some((s) => s.refreshing);
    if (ins.some((s) => s.status !== 'ok')) {
      return { status: 'loading', data: EMPTY, columns: [], message: null, refreshing, meta };
    }
    const arrays = ins.map((s) => s.data);
    if (!sameRefs(arrays, c.seen)) {
      c.seen = arrays;
      c.result = null;
      c.error = null;
      try {
        const out = c.fn(...arrays);
        c.result = out == null ? [] : out;
        c.columns = columnsOf(c.result);
      } catch (err) {
        // Cached against the same inputs, so a throwing calc logs once, not once per read.
        console.error(`dash: calc "${id}" threw:`, err);
        c.error = String(err && err.message ? err.message : err);
      }
    }
    if (c.error) return { status: 'error', data: EMPTY, columns: [], message: c.error, refreshing, meta };
    return { status: 'ok', data: c.result, columns: c.columns, message: null, refreshing, meta };
  };

  // ---- reading --------------------------------------------------------------------

  const missing = (id) => ({ status: 'missing', data: EMPTY, columns: [], message: `No dataset or calc named "${id}"`, refreshing: false, meta: { id } });

  const read = (id, stack = new Set()) => {
    const c = calcs.get(id);
    if (c) {
      if (stack.has(id)) return { status: 'error', data: EMPTY, columns: [], message: `calc "${id}" depends on itself`, refreshing: false, meta: { id, kind: 'calc' } };
      stack.add(id);
      try { return evalCalc(id, c, stack); } finally { stack.delete(id); }
    }
    if (!(id in SOURCES)) return missing(id);
    const d = datasets.get(id) || fetchDataset(id);
    return { status: d.status, data: d.data, columns: d.columns, message: d.message, refreshing: d.refreshing, meta: d.meta };
  };

  // Status without side effects: never starts a fetch, never runs a calc.
  // 'idle' means nothing has asked for it yet.
  const peek = (id, stack = new Set()) => {
    const c = calcs.get(id);
    if (c) {
      if (stack.has(id)) return { status: 'error' };
      stack.add(id);
      const ins = c.inputs.map((i) => peek(i, stack));
      stack.delete(id);
      const refreshing = ins.some((s) => s.refreshing);
      if (ins.some((s) => s.status === 'error' || s.status === 'missing')) return { status: 'error' };
      if (ins.some((s) => s.status === 'idle')) return { status: 'idle' };
      if (ins.some((s) => s.status !== 'ok')) return { status: 'loading', refreshing };
      if (!c.seen) return { status: 'idle' };
      if (c.error) return { status: 'error' };
      return { status: 'ok', rows: Array.isArray(c.result) ? c.result.length : 1, refreshing };
    }
    if (!(id in SOURCES)) return { status: 'missing' };
    const d = datasets.get(id);
    if (!d) return { status: 'idle' };
    return { status: d.status, rows: d.data.length, refreshing: d.refreshing };
  };

  // Mirror the host's data-dash-source-state on every [data-source] element.
  // Re-queried each pass: the page adds and removes data-source as it draws.
  const STATE = 'data-dash-source-state';
  const stamp = () => {
    for (const el of document.querySelectorAll('[data-source]')) {
      const states = el.getAttribute('data-source').split(/\s+/).filter(Boolean).map((id) => peek(id));
      let state = null;
      if (states.some((s) => s.status === 'loading')) state = 'loading';
      else if (states.some((s) => s.status === 'error' || s.status === 'missing')) state = 'error';
      else if (states.length && states.every((s) => s.status === 'ok')) {
        if (states.some((s) => s.refreshing)) state = 'refreshing';
        else if (states.every((s) => s.rows === 0)) state = 'empty';
      }
      if (state) el.setAttribute(STATE, state);
      else el.removeAttribute(STATE);
    }
    for (const el of document.querySelectorAll(`[${STATE}]:not([data-source])`)) el.removeAttribute(STATE);
    bind();
  };

  // The host also fills a mark declaratively from data-source + data-row + data-field.
  // The page fills nearly all its marks itself and clears their .dash-skeleton when it
  // does, so only a mark still waiting once its one source is ready gets filled here.
  const formatMark = (v, kind) => {
    if (v == null || v === '') return '—';
    if (kind === 'date' && /^\d{4}-\d{2}-\d{2}/.test(v)) return window.d3.utcFormat('%-d %b %Y')(new Date(String(v).slice(0, 10) + 'T00:00:00Z'));
    return String(v);
  };
  const bind = () => {
    for (const el of document.querySelectorAll('.dash-skeleton[data-source][data-field][data-row]')) {
      const ids = el.getAttribute('data-source').split(/\s+/).filter(Boolean);
      if (ids.length !== 1 || peek(ids[0]).status !== 'ok') continue;
      const row = read(ids[0]).data[Number(el.dataset.row)];
      el.textContent = formatMark(row ? row[el.dataset.field] : null, el.dataset.kind);
      el.classList.remove('dash-skeleton');
    }
  };

  // ---- listeners ------------------------------------------------------------------

  const call = (fn) => {
    try { fn(); } catch (err) { console.error('dash: onData listener threw:', err); }
  };

  let pending = null;
  const flush = () => {
    pending = null;
    for (const fn of [...listeners]) call(fn);
    stamp();
  };
  // Coalesces bursts (several datasets landing together, a param change) into one draw.
  const schedule = () => { if (pending == null) pending = setTimeout(flush, 0); };

  // Charts size themselves to their box, so redraw when the width changes:
  // window resizes, and also a scrollbar appearing once the data makes the page tall.
  let lastWidth = null;
  let resizeTimer = null;
  const onResize = () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const root = document.getElementById('dash-root') || document.documentElement;
      const w = root.clientWidth;
      if (w === lastWidth) return;
      lastWidth = w;
      schedule();
    }, 150);
  };
  window.addEventListener('resize', onResize);
  const watchWidth = () => {
    const root = document.getElementById('dash-root') || document.documentElement;
    lastWidth = root.clientWidth;
    if ('ResizeObserver' in window) new ResizeObserver(onResize).observe(root);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watchWidth, { once: true });
  else watchWidth();

  // ---- params, mirrored into the query string ----------------------------------------

  const urlKeys = () => (Object.keys(DEFAULTS).length ? Object.keys(DEFAULTS) : null);

  const fromUrl = () => {
    const q = new URLSearchParams(location.search);
    const keys = urlKeys() || [...q.keys()];
    const out = {};
    for (const k of keys) if (q.has(k)) out[k] = q.get(k);
    return out;
  };

  let params = { ...DEFAULTS, ...fromUrl() };

  const writeUrl = (keys) => {
    try {
      const u = new URL(location.href);
      for (const k of keys) {
        const v = params[k];
        if (v == null || v === '' || String(v) === String(DEFAULTS[k])) u.searchParams.delete(k);
        else u.searchParams.set(k, String(v));
      }
      if (u.href !== location.href) history.replaceState(history.state, '', u);
    } catch (err) {
      console.warn('dash: could not update the URL:', err);
    }
  };

  // ---- the API ----------------------------------------------------------------------

  const dash = {
    colors: COLORS,

    data: (id) => read(id),

    calc(id, { inputs = [], fn, title = '', description = '' } = {}) {
      if (typeof fn !== 'function') throw new TypeError(`dash.calc("${id}"): fn must be a function`);
      if (id in SOURCES) console.warn(`dash.calc("${id}") shadows the dataset of the same name`);
      calcs.set(id, { inputs: [...inputs], fn, title, description, seen: null, result: null, columns: [], error: null });
      schedule();
    },

    onData(fn) {
      if (typeof fn !== 'function') return;
      listeners.push(fn);
      call(fn);
      stamp();
    },

    params: () => ({ ...params }),

    setParams(obj) {
      if (!obj || typeof obj !== 'object') return;
      const next = { ...params };
      for (const [k, v] of Object.entries(obj)) {
        if (v == null) delete next[k];
        else next[k] = v;
      }
      const keys = new Set([...Object.keys(params), ...Object.keys(next)]);
      params = { ...DEFAULTS, ...next };
      writeUrl(keys);
      schedule();
    },

    resetParams() {
      const keys = new Set([...Object.keys(params), ...Object.keys(DEFAULTS)]);
      params = { ...DEFAULTS };
      writeUrl(keys);
      schedule();
    },

    setLink(name) {
      try {
        const u = new URL(location.href);
        u.hash = name ? String(name) : '';
        if (u.href !== location.href) history.replaceState(history.state, '', u);
      } catch (err) {
        console.warn('dash: could not set the link:', err);
      }
    },

    // Refetch a dataset, or every dataset a calc depends on. No id: everything loaded so far.
    // The old rows stay in place, marked refreshing, until the new ones arrive.
    refresh(id) {
      const seen = new Set();
      const walk = (k) => {
        if (seen.has(k)) return;
        seen.add(k);
        const c = calcs.get(k);
        if (c) c.inputs.forEach(walk);
        else if (k in SOURCES) fetchDataset(k);
      };
      if (id == null) [...datasets.keys()].forEach(walk);
      else walk(id);
      schedule();
    },
  };

  window.dash = dash;
})();
