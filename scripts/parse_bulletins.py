"""Parse fuelprices.gr per-prefecture daily bulletins (IMERISIO_DELTIO_ANA_NOMO).

Usage: python3 -I parse_bulletins.py <pdf_dir> <out_csv>

Each PDF is named <YYYY-MM-DD>.pdf by the downloader. Emits one row per
region per day, prices in EUR/litre incl. VAT; an empty cell means the
bulletin had no price ("-", "0,000" or a blank cell).

The archive is not one format:
- the column order changes (heating oil and autogas swap places in Oct 2020,
  Super disappears in 2022, heating oil is absent off-season), so the order is
  read from each file's own header;
- a few files have no text header at all; they borrow the order of the
  nearest-dated file with the same column count, and say so on stderr;
- rows with fewer cells than columns (blank cells before Oct 2020) are placed
  by character position against the full rows on the same page;
- some 2019 files have an off-by-one Greek font map (ΑΤΤΙΚΗΣ -> ΑΣΣΙΚΗ΢).
Any file that does not parse cleanly is reported on stderr, never dropped silently.
"""
import csv
import datetime as dt
import pathlib
import re
import statistics
import subprocess
import sys

MONTHS = {
    "Ιανουαρίου": 1, "Φεβρουαρίου": 2, "Μαρτίου": 3, "Απριλίου": 4,
    "Μαΐου": 5, "Μαίου": 5, "Ιουνίου": 6, "Ιουλίου": 7, "Αυγούστου": 8,
    "Σεπτεμβρίου": 9, "Οκτωβρίου": 10, "Νοεμβρίου": 11, "Δεκεμβρίου": 12,
}
# Header words that pin each fuel's column. Case matters: "Κίνησης" is the
# diesel column, "κίνησης" belongs to "Υγραέριο κίνησης". "\w" absorbs the
# broken font map that renders έ as ζ.
HEADER_KEYS = {
    "unleaded95": re.compile(r"(?<!\d)95(?!\d)"),
    "unleaded100": re.compile(r"(?<!\d)100(?!\d)"),
    "super": re.compile(r"Super"),
    "diesel": re.compile(r"Κίνησης"),
    "autogas": re.compile(r"Autoga|Υγρα\wριο"),
    "heating_oil": re.compile(r"Θ\wρμαν"),
}
FUELS = list(HEADER_KEYS)
CELL = re.compile(r"(?<!\S)(\d,\d{1,3}|-)(?!\S)")
# A row label is a run of single-spaced words. Only the stable letters are
# pinned, because garbled files print ΝΟΜΟΣ as ΝΟΜΟ΢ and ΠΑΝΕΛΛΗΝΙΟΣ as ΠΑΝΔΛΛΖΝΗΟ΢.
NAME = re.compile(r"^\s*((?:ΝΟΜΟ\S|ΠΑΝ\S{8})(?: \S+)*?)(?=\s{2,}|\s*$)")
GLUED = re.compile(r"([^\W\d_])(\d,\d{1,3})")
DATE_WORDS = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})")
DATE_NUM = re.compile(r"για\s+τ\S{2}\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})", re.IGNORECASE)
DATE_GARBLED = re.compile(r"(\d{1,2})\s+[^\W\d_]{4,}\s+(\d{4})")
NATIONAL = "Πανελλαδικός σταθμισμένος Μ.Ο."
# The 51 prefectures in the order every bulletin lists them (Attica first).
PREFECTURES = [
    "ΑΤΤΙΚΗΣ", "ΑΙΤΩΛΙΑΣ ΚΑΙ ΑΚΑΡΝΑΝΙΑΣ", "ΑΡΓΟΛΙΔΟΣ", "ΑΡΚΑΔΙΑΣ", "ΑΡΤΗΣ",
    "ΑΧΑΪΑΣ", "ΒΟΙΩΤΙΑΣ", "ΓΡΕΒΕΝΩΝ", "ΔΡΑΜΑΣ", "ΔΩΔΕΚΑΝΗΣΟΥ", "ΕΒΡΟΥ",
    "ΕΥΒΟΙΑΣ", "ΕΥΡΥΤΑΝΙΑΣ", "ΖΑΚΥΝΘΟΥ", "ΗΛΕΙΑΣ", "ΗΜΑΘΙΑΣ", "ΗΡΑΚΛΕΙΟΥ",
    "ΘΕΣΠΡΩΤΙΑΣ", "ΘΕΣΣΑΛΟΝΙΚΗΣ", "ΙΩΑΝΝΙΝΩΝ", "ΚΑΒΑΛΑΣ", "ΚΑΡΔΙΤΣΗΣ",
    "ΚΑΣΤΟΡΙΑΣ", "ΚΕΡΚΥΡΑΣ", "ΚΕΦΑΛΛΗΝΙΑΣ", "ΚΙΛΚΙΣ", "ΚΟΖΑΝΗΣ", "ΚΟΡΙΝΘΙΑΣ",
    "ΚΥΚΛΑΔΩΝ", "ΛΑΚΩΝΙΑΣ", "ΛΑΡΙΣΗΣ", "ΛΑΣΙΘΙΟΥ", "ΛΕΣΒΟΥ", "ΛΕΥΚΑΔΟΣ",
    "ΜΑΓΝΗΣΙΑΣ", "ΜΕΣΣΗΝΙΑΣ", "ΞΑΝΘΗΣ", "ΠΕΛΛΗΣ", "ΠΙΕΡΙΑΣ", "ΠΡΕΒΕΖΗΣ",
    "ΡΕΘΥΜΝΗΣ", "ΡΟΔΟΠΗΣ", "ΣΑΜΟΥ", "ΣΕΡΡΩΝ", "ΤΡΙΚΑΛΩΝ", "ΦΘΙΩΤΙΔΟΣ",
    "ΦΛΩΡΙΝΗΣ", "ΦΩΚΙΔΟΣ", "ΧΑΛΚΙΔΙΚΗΣ", "ΧΑΝΙΩΝ", "ΧΙΟΥ",
]
BY_KEY = {p.replace(" ", ""): p for p in PREFECTURES}


class NoHeader(ValueError):
    pass


class WrongDate(ValueError):
    def __init__(self, found, day):
        super().__init__(f"body says {found}, filename says {day}")
        self.found = found


def clean_lines(text):
    for ln in text.splitlines():
        yield GLUED.sub(r"\1  \2", ln)


def check_date(text, day):
    """Confirm the bulletin is for `day`. Returns a note when only partly checkable."""
    head = re.split(r"\n\s*ΝΟΜΟ\S\s", text, maxsplit=1)[0]
    if m := DATE_WORDS.search(head):
        found = dt.date(int(m[3]), MONTHS[m[2]], int(m[1]))
    elif m := DATE_NUM.search(head):
        found = dt.date(int(m[3]), int(m[2]), int(m[1]))
    elif m := DATE_GARBLED.search(head):
        # A broken font map garbles the month name (Αυγούστου -> Αυγοφστου); take the month whose
        # name has the same length and the most letters in place. The day and year are digits.
        word = m[0].split()[1]
        scored = [(sum(a == b for a, b in zip(word, name)), num) for name, num in MONTHS.items() if len(name) == len(word)]
        if not scored or max(scored)[0] < len(word) // 2:
            raise ValueError(f"garbled date line {m[0]!r} matches no month")
        found = dt.date(int(m[2]), max(scored)[1], int(m[1]))
    else:
        raise ValueError("no date line in body")
    if found != day:
        raise WrongDate(found, day)


def font_map(keys, canon):
    """The real->printed letter map that turns `canon` into `keys`, or None.

    A broken font prints each real letter as one fixed wrong letter (two real
    letters may share a printed one, so this only runs real->printed). A label
    may be cut short where its name wrapped onto a second line.
    """
    printed = {}
    for got, want in zip(keys, canon):
        if len(got) > len(want) or (len(got) < len(want) and len(got) < 4):
            return None
        for g, w in zip(got, want):
            if printed.setdefault(w, g) != g:
                return None
    return printed


def resolve_names(names):
    """Map row labels to canonical prefecture names.

    Clean files match directly (ignoring stray spaces and a wrapped label).
    Files with a broken font map are aligned to the canonical order (allowing
    one missing row) and accepted only through one consistent letter map.
    """
    keys = [n.replace(" ", "") for n in names]
    canon = [p.replace(" ", "") for p in PREFECTURES]
    direct = [next((c for c in canon if c == k or (len(k) >= 8 and c.startswith(k))), None) for k in keys]
    if all(direct) and len(set(direct)) == len(direct):
        return [BY_KEY[c] for c in direct], None
    if len(keys) == len(canon):
        candidates = [(None, canon)]
    elif len(keys) == len(canon) - 1:
        candidates = [(i, canon[:i] + canon[i + 1:]) for i in range(len(canon))]
    else:
        raise ValueError(f"unrecognised names and {len(keys)} rows, cannot align")
    fits = [(gap, c, m) for gap, c in candidates if (m := font_map(keys, c))]
    if len(fits) != 1:
        raise ValueError(f"{len(fits)} consistent alignments for garbled names")
    gap, aligned, printed = fits[0]
    swaps = " ".join(f"{w}>{g}" for w, g in sorted(printed.items()) if g != w)
    note = f"garbled names aligned ({swaps})" + (f", {PREFECTURES[gap]} row missing" if gap is not None else "")
    return [BY_KEY[c] for c in aligned], note


def header_order(lines):
    """Fuels present in this bulletin, left to right, from the header block."""
    first_row = next(i for i, ln in enumerate(lines) if NAME.match(ln) and CELL.search(ln))
    found = {}
    for fuel, rx in HEADER_KEYS.items():
        centers = [m.start() + len(m[0]) / 2 for ln in lines[:first_row] for m in rx.finditer(ln)]
        if centers and max(centers) - min(centers) > 12:
            raise ValueError(f"header word for {fuel} found in different columns: {centers}")
        if centers:
            found[fuel] = statistics.mean(centers)
    order = sorted(found, key=found.get)
    if not {"unleaded95", "diesel"} <= set(order):
        raise NoHeader(f"no usable header, found {order}")
    return order


def page_rows(lines):
    """(region name, [(cell text, end position)]) for every data row on a page."""
    rows, pending = [], None
    for ln in lines:
        cells = [(m[1], m.end()) for m in CELL.finditer(ln)]
        name = NAME.match(ln)
        if name and not cells:
            pending = name[1]
            continue
        if name:
            rows.append((name[1], cells))
        elif pending and cells and not ln[:cells[0][1] - len(cells[0][0])].strip():
            rows.append((pending, cells))
        pending = None if cells else pending
    return rows


def place(cells, refs):
    """Assign a short row's cells to columns by nearest reference position."""
    out = [None] * len(refs)
    for text, end in cells:
        col = min(range(len(refs)), key=lambda i: abs(refs[i] - end))
        if out[col] is not None or abs(refs[col] - end) > 4:
            raise ValueError(f"cannot place cell {text!r} at {end} against {refs}")
        out[col] = text
    return out


def extract(pdf, taken):
    """Text pages of a bulletin and the day it covers.

    The day comes from the filename, unless the body names a different day the
    archive has no other file for: then the body wins (the file was mislabelled).
    """
    text = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    day = dt.date.fromisoformat(pdf.stem)
    if not CELL.search(text):
        raise ValueError("no prices in the text layer (scanned image or notice)")
    try:
        note = check_date(text, day)
    except WrongDate as exc:
        if exc.found in taken:
            raise ValueError(f"{exc}; that day has its own file, skipping duplicate") from exc
        day, note = exc.found, f"filed under {exc.found}: {exc}"
    return day, [list(clean_lines(p)) for p in text.split("\f")], note


def width(pages):
    """Widest data row: the bulletin's column count."""
    return max(len(c) for p in pages for _, c in page_rows(p))


def column_refs(pages, n):
    """Per-page column end positions, from that page's full rows.

    A page with no full row reuses another page's positions, shifted by how
    far its first column (unleaded 95, never blank) sits from that page's.
    """
    refs = []
    for lines in pages:
        rows = page_rows(lines)
        full = [c for _, c in rows if len(c) == n]
        first = statistics.median(c[0][1] for _, c in rows) if rows else None
        refs.append(([statistics.median(c[i][1] for c in full) for i in range(n)], first) if full else (None, first))
    known = [(r, f) for r, f in refs if r]
    if not known:
        return [None] * len(pages)
    base, base_first = known[0]
    return [r if r else ([x + f - base_first for x in base] if f is not None else None) for r, f in refs]


def parse(day, pages, order):
    n = len(order)
    out, labels = [], []
    for lines, refs in zip(pages, column_refs(pages, n)):
        rows = page_rows(lines)
        for name, cells in rows:
            if len(cells) == n:
                texts = [c[0] for c in cells]
            elif len(cells) < n and refs:
                texts = place(cells, refs)
            else:
                raise ValueError(f"{len(cells)} cells for {n} columns on row {name!r}")
            values = {}
            for fuel, t in zip(order, texts):
                v = None if t in (None, "-") else float(t.replace(",", "."))
                if v == 0:
                    v = None
                if v is not None and not 0.3 < v < 3.5:
                    raise ValueError(f"out-of-range {fuel}={v} on row {name!r}")
                values[fuel] = v
            labels.append(None if name.startswith("ΠΑΝ") else re.sub(r"^ΝΟΜΟ\S\s+", "", name))
            out.append([day.isoformat(), None, *(values.get(f) for f in FUELS)])

    if labels.count(None) != 1:
        raise ValueError(f"{labels.count(None)} national rows")
    names, note = resolve_names([lb for lb in labels if lb is not None])
    it = iter(names)
    for row, label in zip(out, labels):
        row[1] = NATIONAL if label is None else next(it)
    if "ΑΤΤΙΚΗΣ" not in names:
        raise ValueError("no Attica row")
    return out, note


def main(pdf_dir, out_csv):
    failures, parsed, orders, headless = 0, {}, {}, []
    pdfs = sorted(pathlib.Path(pdf_dir).glob("*.pdf"))
    taken = {dt.date.fromisoformat(p.stem) for p in pdfs}
    for pdf in pdfs:
        try:
            day, pages, date_note = extract(pdf, taken)
            order = header_order(pages[0])
            parsed[day], name_note = parse(day, pages, order)
            orders[day] = order
            for note in (date_note, name_note):
                if note:
                    print(f"NOTE {day}: {note}", file=sys.stderr)
        except NoHeader:
            headless.append((day, pages))
        except (ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as exc:
            failures += 1
            print(f"FAIL {pdf.name}: {type(exc).__name__} {exc}", file=sys.stderr)

    for day, pages in headless:
        n = width(pages)
        donors = [d for d, o in orders.items() if len(o) == n]
        if not donors:
            failures += 1
            print(f"FAIL {day}: no header and no {n}-column donor", file=sys.stderr)
            continue
        donor = min(donors, key=lambda d: abs((d - day).days))
        try:
            parsed[day], _ = parse(day, pages, orders[donor])
            orders[day] = orders[donor]
            print(f"NOTE {day}: no header, borrowed column order from {donor}", file=sys.stderr)
        except (ValueError, KeyError) as exc:
            failures += 1
            print(f"FAIL {day}: borrowed order from {donor}: {exc}", file=sys.stderr)

    layouts = []
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow(["date", "region", *FUELS])
        for day in sorted(parsed):
            out.writerows(parsed[day])
            if not layouts or layouts[-1][0] != orders[day]:
                layouts.append([orders[day], day, day, 0])
            layouts[-1][2], layouts[-1][3] = day, layouts[-1][3] + 1
    for order, first, last, count in layouts:
        print(f"LAYOUT {first}..{last} ({count} files): {' '.join(order)}", file=sys.stderr)
    print(f"parsed={len(parsed)} failures={failures}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
