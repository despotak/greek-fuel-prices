# /// script
# dependencies = ["openpyxl", "pandas"]
# ///
"""Build and refresh the repository's open data.

Usage:
  uv run --script build_open_data.py init  <root> <bulletins_daily.csv> <parse.log> <pdf_dir>
  uv run --script build_open_data.py daily <root>

init   one-off: today's parsed archive -> data/pump/YYYY.csv, data/pump/missing_days.csv, data/areas.csv
daily  refinery prices and the ECB rate -> data/ (committed);
       EIA spot prices and EIA's Short-Term Energy Outlook -> build/ (not committed: EIA's
       prices come from Refinitiv, an LSEG business, so they are fetched, not republished)
"""
import csv
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request

import openpyxl
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from areas import AREAS, BY_LABEL, FUELS, write_csv

UA = {"User-Agent": "greek-fuel-prices (https://github.com/despotak/greek-fuel-prices)"}
REFINERY_URL = "https://raw.githubusercontent.com/akosiaris/greek-oil-refinery-prices/main/fuels.csv"
REFINERY_API = "https://oil-be.tcloudapp.net/api/Posts/GetLastPosts"  # the feed oil.gge.gov.gr itself reads
REFINERY_VAT = 0.24  # on fuels since 1 June 2016
ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?format=csvdata&startPeriod=2017-01-01"
EIA_XLS = "https://www.eia.gov/dnav/pet/hist_xls/{}d.xls"
EIA_SERIES = ["EER_EPD2DXL0_PF4_Y35NY_DPG", "EER_EPMRU_PF4_Y35NY_DPG", "EER_EPD2F_PF4_Y35NY_DPG",
              "EER_EPLLPA_PF4_Y44MB_DPG", "RBRTE", "RWTC"]
STEO_URL = "https://www.eia.gov/outlooks/steo/xls/STEO_m.xlsx"
STEO_SERIES = {"BREPUUS": ("brent", "Brent crude oil spot price", "USD/bbl"),
               "DSWHUUS_$": ("diesel", "US diesel fuel, refiner wholesale price", "USD/gal"),
               "MGWHUUS_$": ("gasoline", "US gasoline, refiner wholesale price", "USD/gal")}
PUMP_COLUMNS = ["date", "area_id", *FUELS]
# European Commission, Weekly Oil Bulletin: the full price history since 2005, one workbook. CC BY 4.0.
EU_WOB_URL = ("https://energy.ec.europa.eu/document/download/906e60ca-8b6a-44e7-8589-652854d2fd3f_en"
              "?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx")
# The workbook's fuel keys -> ours. Fuel oil (two industrial grades) is left out: no pump counterpart.
EU_FUELS = {"euro95": "euro95", "diesel": "diesel", "heating_oil": "heating_oil", "LPG": "lpg"}


def get(url, path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as fh:
        fh.write(resp.read())
    return path


def write_pump_years(root, rows):
    """rows: [date, area_id, *FUELS] -> data/pump/YYYY.csv, merged with what is there, sorted."""
    order = {a[0]: i for i, a in enumerate(AREAS)}
    by_year = {}
    for r in rows:
        by_year.setdefault(r[0][:4], []).append(r)
    for year, new in by_year.items():
        path = root / "data" / "pump" / f"{year}.csv"
        old = []
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                old = [list(x.values()) for x in csv.DictReader(fh)]
        merged = {(r[0], r[1]): r for r in old}
        merged.update({(r[0], r[1]): r for r in new})
        out = sorted(merged.values(), key=lambda r: (r[0], order[r[1]]))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(PUMP_COLUMNS)
            w.writerows(out)


def init(root, daily_csv, parse_log, pdf_dir):
    root = pathlib.Path(root)
    write_csv(root / "data" / "areas.csv")
    rows = []
    with open(daily_csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append([r["date"], BY_LABEL[r["region"]], *(r[f] for f in FUELS)])
    # Days no parser can read (scanned images, a broken text layout), read by hand from the page
    # image and kept as their own source so a rebuild from the archive keeps them.
    hand = []
    transcribed = root / "data" / "pump" / "transcribed.csv"
    if transcribed.exists():
        with open(transcribed, encoding="utf-8") as fh:
            hand = [[r["date"], r["area_id"], *(r[f] for f in FUELS)] for r in csv.DictReader(fh)]
    write_pump_years(root, rows + hand)

    parsed = {r[0] for r in rows} | {r[0] for r in hand}
    fails = {}
    notes = [[d, ("read by hand from the bulletin's page image (see transcribed.csv); checked against a machine "
                  "reading of the page (OCR, or the PDF's own text layer for 2017-03-27), the neighbouring days, and "
                  "the national average rebuilt from the prefecture prices for unleaded 95, 100 and diesel")]
             for d in sorted({r[0] for r in hand})]
    for line in pathlib.Path(parse_log).read_text(encoding="utf-8").splitlines():
        if (n := re.match(r"NOTE (\d{4}-\d{2}-\d{2}): (.*)", line)) and n[1] in parsed:
            notes += [[n[1], part] for part in n[2].split("; ")]
            if moved := re.search(r"body says ([\d-]+), filename says ([\d-]+)", n[2]):
                fails.setdefault(moved[2], "file for this day is a mislabelled copy of " + moved[1])
        m = re.match(r"FAIL (\d{4}-\d{2}-\d{2})(?:\.pdf)?: (?:\w+Error )?(.*)", line)
        if m:
            msg = m[2]
            if "no prices in the text layer" in msg:
                reason = "bulletin published as a scanned image or a notice, no readable prices"
            elif "skipping duplicate" in msg:
                reason = "file for this day is a mislabelled copy of " + re.search(r"body says ([\d-]+)", msg)[1]
            else:
                reason = "bulletin could not be read: " + msg
            fails[m[1]] = reason
    have_pdf = {p.stem for p in pathlib.Path(pdf_dir).glob("*.pdf")}
    first, last = min(parsed), max(parsed)
    missing = []
    for d in pd.date_range(first, last, freq="D").strftime("%Y-%m-%d"):
        if d in parsed:
            continue
        missing.append([d, fails.get(d) or ("bulletin could not be read" if d in have_pdf else "no bulletin published on fuelprices.gr")])
    with open(root / "data" / "pump" / "missing_days.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "reason"])
        w.writerows(missing)
    with open(root / "data" / "pump" / "day_notes.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "note"])
        w.writerows(sorted(notes))
    print(f"pump: {len(rows)} rows over {len(parsed)} days; missing days: {len(missing)}", file=sys.stderr)


REFINERY_COLUMNS = ["date", "category", "product", "elpe", "motor_oil", "mean", "mean_incl_vat", "unit", "basis"]


def refinery_backup(tmp):
    """The full history as collected by akosiaris/greek-oil-refinery-prices (twice a day)."""
    raw = pd.read_csv(get(REFINERY_URL, tmp / "refinery_raw.csv"))
    raw["date"] = raw["date"].str[:10]
    raw = raw.drop_duplicates(["date", "fuel"], keep="last")
    return pd.DataFrame({
        "date": raw["date"], "category": raw["category"], "product": raw["fuel"],
        "elpe": raw["elpePrice"], "motor_oil": raw["motoroilPrice"], "mean": raw["meanPrice"],
        "mean_incl_vat": raw["vatPrice"], "unit": raw["unit"], "basis": raw["notes"],
    })


def refinery_direct(known):
    """The ministry's own feed behind oil.gge.gov.gr: its last ten posts, in the same shape and with the
    same arithmetic as akosiaris' file (mean of the two refineries to 3 decimals; 24% VAT on top where
    the basis is "before VAT"). Category, unit and basis come from each product's latest row in `known`."""
    req = urllib.request.Request(REFINERY_API, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        feed = json.load(resp)
    meta = known.sort_values("date").groupby("product").last()[["category", "unit", "basis"]]
    rows = []
    for post in feed["posts"]:
        prices = {}
        for d in feed["postDetails"]:
            if d["iD_Post"] == post["id"]:
                prices.setdefault(d["productName"], {})[d["vendorName"]] = d["value"]
        for product, by_vendor in prices.items():
            if product not in meta.index:
                print(f"refinery feed: new product {product!r}, no category yet, skipped", file=sys.stderr)
                continue
            elpe, motor_oil = by_vendor.get("ΕΛ.ΠΕ."), by_vendor.get("Motor Oil")
            found = [v for v in (elpe, motor_oil) if v]
            mean = round(sum(found) / len(found), 3) if found else None
            category, unit, basis = meta.loc[product]
            vat = round(mean * (1 + REFINERY_VAT), 3) if mean is not None and "προ ΦΠΑ" in basis and "προ φόρων" not in basis else None
            rows.append([post["postDate"][:10], category, product, elpe, motor_oil, mean, vat, unit, basis])
    return pd.DataFrame(rows, columns=REFINERY_COLUMNS)


def refinery(root, tmp):
    """Refinery prices: the committed file, topped up from akosiaris' collection (the backup, and the
    source of the history) and then from the ministry's feed directly (it wins where both have a day).
    Either source can fail on its own; with both down the committed file stays as it is."""
    path = root / "data" / "refinery" / "refinery_prices.csv"
    have = pd.read_csv(path, dtype={"date": str}) if path.exists() else pd.DataFrame(columns=REFINERY_COLUMNS)
    parts, used = [have], []
    try:
        parts.append(refinery_backup(tmp))
        used.append("akosiaris")
    except (OSError, KeyError, ValueError) as exc:
        print(f"refinery: backup collection unreachable: {exc}", file=sys.stderr)
    try:
        direct = refinery_direct(pd.concat(parts))
        parts.append(direct)
        used.append(f"ministry feed ({direct['date'].min()} .. {direct['date'].max()})")
        if len(parts) == 3:
            both = parts[1].merge(direct, on=["date", "product"], suffixes=("_a", "_d"))
            off = both[(both["mean_a"] - both["mean_d"]).abs() > 0.0015]
            if len(off):
                print(f"refinery: feed and backup disagree on {len(off)} of {len(both)} shared rows", file=sys.stderr)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        print(f"refinery: ministry feed unreachable: {exc}", file=sys.stderr)
    today = dt.date.today().isoformat()
    out = pd.concat(parts)
    out = out[out["date"] <= today].drop_duplicates(["date", "product"], keep="last").sort_values(["date", "product"])
    path.parent.mkdir(parents=True, exist_ok=True)
    out[REFINERY_COLUMNS].to_csv(path, index=False, lineterminator="\n")
    print(f"refinery: {len(out)} rows, {out['date'].min()} .. {out['date'].max()}, from {' + '.join(used) or 'nothing new'}",
          file=sys.stderr)


def ecb(root, tmp):
    raw = pd.read_csv(get(ECB_URL, tmp / "ecb.csv"), usecols=["TIME_PERIOD", "OBS_VALUE"])
    out = raw.rename(columns={"TIME_PERIOD": "date", "OBS_VALUE": "usd_per_eur"}).sort_values("date")
    path = root / "data" / "international" / "ecb_eurusd.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, lineterminator="\n")
    print(f"ecb: {len(out)} rows to {out['date'].max()}", file=sys.stderr)


# Columns of the bulletin's tax sheets, left to right after the country and the "Since:" date.
EU_TAX_COLUMNS = ["euro95", "diesel", "heating_oil", "fuel_oil_1", "fuel_oil_2", "lpg"]
EU_TAX_SHEETS = {"VAT": ("vat", "percent"),
                 "Excise duties": ("excise", "national currency per litre"),
                 "Other Indirect Taxes": ("other_indirect", "national currency per litre")}


def eu_tax_rates(wb):
    """Every tax-rate change in the bulletin's VAT, excise and other-indirect-tax sheets:
    [date in force, country, fuel, tax, value, unit], for the four fuels the prices file keeps.

    Each sheet is one block per country, newest change first; a blank cell means that fuel's rate
    did not change on that date, so only filled cells are changes. Excise and other taxes are in the
    currency the country used when the Commission recorded them, per 1,000 litres in the sheet.
    """
    out = []
    for sheet, (tax, unit) in EU_TAX_SHEETS.items():
        country = None
        for r in wb[sheet].iter_rows(values_only=True):
            if isinstance(r[0], str) and re.fullmatch(r"[A-Z]{2}_", r[0]):
                country = r[0][:2]
            if country is None or not hasattr(r[1], "date"):
                continue
            for fuel, v in zip(EU_TAX_COLUMNS, r[2:8]):
                if fuel in EU_FUELS.values() and isinstance(v, (int, float)):
                    out.append([r[1].date().isoformat(), country, fuel, tax,
                                round(v, 4) if tax == "vat" else round(v / 1000, 6), unit])
    if len({r[1] for r in out}) < 27:
        raise ValueError(f"tax sheets name only {len({r[1] for r in out})} countries")
    return sorted(out)


def eu_oil_bulletin(root, tmp):
    """EU Weekly Oil Bulletin -> data/eu/weekly_oil_bulletin.csv, every country and the EU and euro-area
    averages, in EUR per litre with and without taxes (the workbook gives EUR per 1,000 litres).

    A failed download or an unexpected workbook keeps the committed file: this source is a weekly
    extra, and a broken fetch must not block the daily pump data.
    """
    path = root / "data" / "eu" / "weekly_oil_bulletin.csv"
    try:
        wb = openpyxl.load_workbook(get(EU_WOB_URL, tmp / "eu_wob.xlsx"), read_only=True, data_only=True)
        prices = {}
        for sheet, kind in (("Prices with taxes", "with_tax"), ("Prices wo taxes", "wo_tax")):
            rows = wb[sheet].iter_rows(values_only=True)
            header = next(rows)
            cols = {}
            for i, h in enumerate(header):
                if isinstance(h, str) and (m := re.fullmatch(rf"([A-Z]+)_price_{kind}_(\w+)", h)) and m[2] in EU_FUELS:
                    cols[i] = (m[1], EU_FUELS[m[2]])
            if len(cols) < 100:
                raise ValueError(f"sheet {sheet!r} has {len(cols)} price columns, expected well over 100")
            for r in rows:
                if not hasattr(r[0], "date"):
                    continue
                day = r[0].date().isoformat()
                for i, (country, fuel) in cols.items():
                    if isinstance(r[i], (int, float)) and r[i] > 0:
                        prices.setdefault((day, country, fuel), {})[kind] = round(r[i] / 1000, 6)
        taxes = eu_tax_rates(wb)
    except (OSError, KeyError, ValueError, StopIteration) as exc:
        print(f"eu weekly oil bulletin: NOT refreshed, keeping {path.name}: {exc}", file=sys.stderr)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / "tax_rates.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "country", "fuel", "tax", "value", "unit"])
        w.writerows(taxes)
    out = sorted([d, c, f, v.get("with_tax", ""), v.get("wo_tax", "")] for (d, c, f), v in prices.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "country", "fuel", "price_with_taxes", "price_without_taxes"])
        w.writerows(out)
    print(f"eu weekly oil bulletin: {len(out)} rows, {out[0][0]} .. {out[-1][0]}, "
          f"{len({r[1] for r in out})} countries/aggregates", file=sys.stderr)


def eia(root):
    for s in EIA_SERIES:
        get(EIA_XLS.format(s), root / "build" / "eia" / f"{s}.xls")
    path = get(STEO_URL, root / "build" / "STEO_m.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    # Labels in the "Dates" sheet sit in varying columns and carry trailing spaces and dashes,
    # so each row is reduced to its non-empty cells and matched on the first one by prefix.
    dates = [cells for r in wb["Dates"].iter_rows(values_only=True) if (cells := [c for c in r if c is not None])]

    def row(prefix):
        return next(cells for cells in dates if str(cells[0]).strip().startswith(prefix))

    months = [m for m in row("AAAA_DATEX")[1:] if isinstance(m, int)]
    last_hist = int(row("Last Historical Month")[1])
    vintage = row("Forecast Month")[1]
    rows = []
    for r in wb["2tab"].iter_rows(values_only=True):
        if r and r[0] in STEO_SERIES:
            key, name, unit = STEO_SERIES[r[0]]
            for m, v in zip(months, r[2:]):
                if isinstance(v, (int, float)):
                    rows.append([f"{m // 100}-{m % 100:02d}", key, name, round(float(v), 4), unit,
                                 "history" if m <= last_hist else "forecast", vintage])
    with open(root / "build" / "steo_monthly.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["month", "series", "name", "value", "unit", "kind", "vintage"])
        w.writerows(rows)
    print(f"eia: {len(EIA_SERIES)} spot series; steo {vintage}: {len(rows)} rows, last history {last_hist}", file=sys.stderr)


def daily(root):
    root = pathlib.Path(root)
    tmp = root / "build" / "tmp"
    refinery(root, tmp)
    ecb(root, tmp)
    eu_oil_bulletin(root, tmp)
    eia(root)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"init": init, "daily": daily}[cmd](*args)
