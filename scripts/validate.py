# /// script
# dependencies = ["pandas"]
# ///
"""Gates that stop a bad day from being published. Exit status 1 on any failure.

Usage: uv run --script validate.py <root>

New days are the days not in the last commit (git HEAD). On them:
  every prefecture is present (a missing national average only warns: some bulletins leave it out);
  the national price lies between the cheapest and dearest area;
  FAIL if more than MAX_JUMPERS areas move more than MAX_STEP for the same fuel on the same day.
  Since 2017 that never happened (at most one area), while a column swap or a font-map relapse
  moves every area at once. Single-area jumps and national moves above NATIONAL_WARN are warnings:
  the bulletins themselves contain such glitches (national autogas 1.123 on 8 and 10 Aug 2022).
  A day listed in data/pump/accepted_anomalies.csv (date, note) skips the jump check.
On everything: prices in range, files never shrink, the newest bulletin is recent, model outputs exist.
"""
import datetime as dt
import io
import pathlib
import subprocess
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from areas import AREAS, FUELS

PRICE_RANGE = (0.3, 3.5)
MAX_STEP = 0.15  # EUR/L between consecutive bulletins, per area
MAX_JUMPERS = 5  # areas allowed above MAX_STEP on one day for one fuel
NATIONAL_WARN = 0.10  # largest national step since 2017: 0.069 (heating oil, March 2022), autogas glitch aside
STALE_WARN, STALE_FAIL = 4, 10
EU_PAIRS = {"euro95": "unleaded95", "diesel": "diesel", "heating_oil": "heating_oil", "lpg": "autogas"}


def committed(root, rel):
    try:
        out = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{rel}"], capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return pd.read_csv(io.BytesIO(out), dtype={"date": str})


def main(root):
    root = pathlib.Path(root).resolve()
    errors, warnings = [], []
    files = sorted((root / "data" / "pump").glob("[0-9]*.csv"))
    pump = pd.concat([pd.read_csv(p, dtype={"date": str}) for p in files])

    before = {}
    for p in files:
        old = committed(root, f"data/pump/{p.name}")
        if old is not None:
            before[p.name] = old
            now = (pump["date"].str[:4] == p.stem).sum()
            if now < len(old):
                errors.append(f"{p.name} shrank from {len(old)} to {now} rows")
    old_days = set(pd.concat(before.values())["date"]) if before else set(pump["date"])
    new_days = sorted(set(pump["date"]) - old_days)

    vals = pump[FUELS].stack()
    bad = vals[(vals < PRICE_RANGE[0]) | (vals > PRICE_RANGE[1])]
    if len(bad):
        errors.append(f"{len(bad)} prices outside {PRICE_RANGE}, e.g. {bad.head(3).to_dict()}")

    last = dt.date.fromisoformat(pump["date"].max())
    age = (dt.date.today() - last).days
    (errors if age > STALE_FAIL else warnings if age > STALE_WARN else []).append(f"newest bulletin is {last}, {age} days old")

    expected = {a[0] for a in AREAS}
    accepted_path = root / "data" / "pump" / "accepted_anomalies.csv"
    accepted = set(pd.read_csv(accepted_path, dtype=str)["date"]) if accepted_path.exists() else set()
    wide = {f: pump.pivot(index="date", columns="area_id", values=f).sort_index() for f in FUELS}
    for day in new_days:
        have = set(pump.loc[pump["date"] == day, "area_id"])
        if expected - have == {"national"} and have <= expected:
            warnings.append(f"{day}: no national average (the bulletin leaves it out)")
        elif have != expected:
            errors.append(f"{day}: areas missing {sorted(expected - have)[:5]} / unexpected {sorted(have - expected)[:5]}")
        for f, w in wide.items():
            row = w.loc[day]
            areas = row.drop("national").dropna()
            nat = row.get("national")
            if pd.notna(nat) and len(areas) and not (areas.min() - 1e-9 <= nat <= areas.max() + 1e-9):
                errors.append(f"{day} {f}: national {nat} outside the area range {areas.min()}..{areas.max()}")
            hist = w.loc[:day].iloc[:-1]
            jumps = []
            for area, v in row.dropna().items():
                prev = hist[area].dropna()
                if len(prev) and (pd.Timestamp(day) - pd.Timestamp(prev.index[-1])).days <= 10:
                    step = abs(v - prev.iloc[-1])
                    if (area == "national" and step > NATIONAL_WARN) or (area != "national" and step > MAX_STEP):
                        jumps.append(f"{area} {prev.iloc[-1]}->{v}")
            movers = [j for j in jumps if not j.startswith("national ")]
            if len(movers) > MAX_JUMPERS and day not in accepted:
                errors.append(f"{day} {f}: {len(movers)} areas moved more than {MAX_STEP} EUR/L in one step "
                              f"(a column swap or garbled names?): {movers[:6]}")
            elif jumps:
                warnings.append(f"{day} {f}: large steps {jumps[:6]}")

    for rel in ("data/model/forecast.csv", "data/model/passthrough_curve.csv", "data/refinery/refinery_prices.csv",
                "data/international/ecb_eurusd.csv", "data/eu/weekly_oil_bulletin.csv"):
        p = root / rel
        if not p.exists() or len(pd.read_csv(p)) == 0:
            errors.append(f"{rel} missing or empty")

    # The EU Weekly Oil Bulletin carries Greece's national average for the Monday of each week: it has
    # matched ours to the tenth of a cent on all but 11 of 474 shared Mondays since 2017. A recent
    # disagreement points at a misread column on one side (a warning: the Commission revises figures).
    eu_path = root / "data" / "eu" / "weekly_oil_bulletin.csv"
    if eu_path.exists():
        eu = pd.read_csv(eu_path, dtype={"date": str})
        if (old_eu := committed(root, "data/eu/weekly_oil_bulletin.csv")) is not None and len(eu) < len(old_eu):
            errors.append(f"data/eu/weekly_oil_bulletin.csv shrank from {len(old_eu)} to {len(eu)} rows")
        recent = (pd.Timestamp(pump["date"].max()) - pd.Timedelta(days=60)).date().isoformat()
        gr = eu[(eu["country"] == "GR") & (eu["date"] >= recent)]
        national = pump[pump["area_id"] == "national"].set_index("date")
        for f, ours in EU_PAIRS.items():
            for _, r in gr[gr["fuel"] == f].iterrows():
                ours_v = national[ours].get(r["date"]) if r["date"] in national.index else None
                if pd.notna(ours_v) and pd.notna(r["price_with_taxes"]) and abs(ours_v - r["price_with_taxes"]) > 0.02:
                    warnings.append(f"{r['date']} {ours}: EU bulletin says {r['price_with_taxes']}, our national is {ours_v}")

    print(f"validate: {len(pump)} pump rows, {pump['date'].nunique()} days, {len(new_days)} new ({', '.join(new_days[-5:])})", file=sys.stderr)
    for w in warnings:
        print(f"WARNING {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
