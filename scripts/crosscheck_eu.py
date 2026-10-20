"""Cross-check the parsed national averages against the EU Weekly Oil Bulletin.

Usage: uv run --script crosscheck_eu.py <eu_history.xlsx> <daily_all.csv>

For every EU week (dated Monday) that has a Greek bulletin on the same day,
compares the four fuels both sources carry. A column mapped to the wrong fuel
shows up as a gap of tens of cents, not a fraction of one.
"""
# /// script
# dependencies = ["openpyxl"]
# ///
import collections
import csv
import statistics
import sys

import openpyxl

PAIRS = {"euro95": "unleaded95", "diesel": "diesel", "heating_oil": "heating_oil", "LPG": "autogas"}


def eu_greece(path):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["Prices with taxes"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    cols = {fuel: header.index(f"GR_price_with_tax_{fuel}") for fuel in PAIRS}
    out = {}
    for r in rows:
        if hasattr(r[0], "date"):
            out[r[0].date().isoformat()] = {f: r[i] / 1000 for f, i in cols.items() if isinstance(r[i], (int, float))}
    return out


def main(xlsx, daily):
    eu = eu_greece(xlsx)
    gr = {r["date"]: r for r in csv.DictReader(open(daily, encoding="utf-8")) if r["region"].startswith("Πανελλαδικός")}
    diffs = collections.defaultdict(list)
    worst = []
    for day, prices in sorted(eu.items()):
        if day not in gr:
            continue
        for eu_fuel, ours in PAIRS.items():
            if eu_fuel in prices and gr[day][ours]:
                d = float(gr[day][ours]) - prices[eu_fuel]
                diffs[(ours, day[:4])].append(d)
                worst.append((abs(d), day, ours, float(gr[day][ours]), prices[eu_fuel]))
    print(f"{'fuel':12} {'year':5} {'weeks':>5} {'median diff':>12} {'max |diff|':>11}  (EUR/L, ours - EU)")
    for (fuel, year), ds in sorted(diffs.items()):
        print(f"{fuel:12} {year:5} {len(ds):5d} {statistics.median(ds):+12.4f} {max(map(abs, ds)):11.4f}")
    print("\nlargest disagreements:")
    for a, day, fuel, ours, theirs in sorted(worst, reverse=True)[:10]:
        print(f"  {day} {fuel:12} ours {ours:.3f}  EU {theirs:.3f}  diff {ours - theirs:+.3f}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
