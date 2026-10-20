# /// script
# dependencies = ["openpyxl", "pandas"]
# ///
"""Daily: fetch every bulletin fuelprices.gr lists that the data does not hold yet, parse it,
append it to data/pump/YYYY.csv, and delete the PDF. A bulletin that cannot be read is recorded in
data/pump/missing_days.csv with the reason, so it is not retried every day.

Usage: uv run --script update_bulletins.py <root>
Exit status 1 only when the site's index cannot be read (the run should go red).
"""
import csv
import pathlib
import re
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import parse_bulletins as pb
from areas import BY_LABEL
from build_open_data import UA, write_pump_years

SITE = "https://www.fuelprices.gr/"
# Layouts seen since late 2022, tried in order for a bulletin with no readable header.
FALLBACK_LAYOUTS = [
    ["unleaded95", "unleaded100", "diesel", "autogas", "heating_oil"],
    ["unleaded95", "unleaded100", "diesel", "autogas"],
]


def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return r.read()
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    return None


def index():
    html = fetch(SITE + "deltia_dn.view").decode("windows-1253", errors="replace")
    out = {}
    for href in set(re.findall(r"deltia/IMERISIO_DELTIO_ANA_NOMO_(\d{1,2})_(\d{2})_(\d{4})\.pdf", html)):
        d, m, y = href
        out[f"{y}-{m}-{int(d):02d}"] = f"deltia/IMERISIO_DELTIO_ANA_NOMO_{d}_{m}_{y}.pdf"
    return out


def known(root):
    have = set()
    for p in (root / "data" / "pump").glob("[0-9][0-9][0-9][0-9].csv"):
        with open(p, encoding="utf-8") as fh:
            have.update(r["date"] for r in csv.DictReader(fh))
    with open(root / "data" / "pump" / "missing_days.csv", encoding="utf-8") as fh:
        missing = {r["date"] for r in csv.DictReader(fh)}
    return have, missing


def parse_one(pdf, taken):
    day, pages, _ = pb.extract(pdf, taken)
    try:
        order = pb.header_order(pages[0])
        rows, _ = pb.parse(day, pages, order)
        return day, rows
    except pb.NoHeader:
        n = pb.width(pages)
        for order in FALLBACK_LAYOUTS:
            if len(order) == n:
                rows, _ = pb.parse(day, pages, order)
                return day, rows
        raise


def main(root):
    root = pathlib.Path(root)
    listed = index()
    have, missing = known(root)
    first = min(have)
    todo = sorted(d for d in listed if d >= first and d not in have and d not in missing)
    print(f"index lists {len(listed)} bulletins; held {len(have)}; recorded missing {len(missing)}; new {len(todo)}", file=sys.stderr)
    if not todo:
        return 0
    # A mislabelled file may be filed under the day its body names, unless that day already has data
    # or its own file is about to be parsed in this run.
    taken = have | set(todo)
    rows, failed = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for d in todo:
            pdf = pathlib.Path(tmp) / f"{d}.pdf"
            try:
                pdf.write_bytes(fetch(SITE + listed[d]))
                day, parsed = parse_one(pdf, taken)
                rows += [[day.isoformat(), BY_LABEL[r[1]], *("" if v is None else v for v in r[2:])] for r in parsed]
                print(f"  {d}: {len(parsed)} rows" + (f" (filed under {day})" if day.isoformat() != d else ""), file=sys.stderr)
            except (ValueError, KeyError, StopIteration, OSError) as exc:
                reason = ("bulletin published as a scanned image or a notice, no readable prices"
                          if "no prices in the text layer" in str(exc) else f"bulletin could not be read: {exc}")
                failed.append([d, reason])
                print(f"  {d}: FAILED {exc}", file=sys.stderr)
            finally:
                pdf.unlink(missing_ok=True)
            time.sleep(0.3)
    if rows:
        write_pump_years(root, rows)
    if failed:
        with open(root / "data" / "pump" / "missing_days.csv", "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh, lineterminator="\n").writerows(failed)
    print(f"appended {len({r[0] for r in rows})} days, recorded {len(failed)} failures", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
