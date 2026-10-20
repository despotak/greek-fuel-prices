# /// script
# dependencies = ["pandas"]
# ///
"""Assemble the GitHub Pages site in build/site/ from site/ and the data.

Usage: uv run --script build_site.py <root>

The dashboard reads one wide CSV per fuel (a row per bulletin day, a column per area: "national",
then the 51 prefectures by their Greek name), the model outputs, and the EIA-based series that
build_outlook.py wrote to build/site-data/.
"""
import pathlib
import shutil
import sys
import unicodedata

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from areas import AREAS, FUELS


def unaccented(name):
    return "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c))


EU_MEMBERS = ["AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
              "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"]


def eu_greece(root, data):
    """The EU Weekly Oil Bulletin, cut down for the comparison card: Greece against the EU average week
    by week, and every member's price in the latest week Greece reported, for its rank."""
    eu = pd.read_csv(root / "data" / "eu" / "weekly_oil_bulletin.csv", dtype={"date": str})
    # The bulletin carries Greece's last heating-oil price unchanged from May to mid-October (1.606
    # from 2 May to 10 October 2022), when it is not sold at the reduced rate: those are placeholders.
    md = eu["date"].str[5:]
    out_of_season = (eu["country"] == "GR") & (eu["fuel"] == "heating_oil") & (md > "04-30") & (md < "10-15")
    eu = eu[~out_of_season]
    both = eu[eu["country"].isin(["GR", "EU"])].pivot_table(
        index=["date", "fuel"], columns="country", values=["price_with_taxes", "price_without_taxes"])
    both.columns = [f"{c.lower()}_{'with' if v == 'price_with_taxes' else 'without'}" for v, c in both.columns]
    both = both.reindex(columns=["gr_without", "eu_without", "gr_with", "eu_with"]).reset_index()
    both.to_csv(data / "eu_greece.csv", index=False, lineterminator="\n")
    members = eu[eu["country"].isin(EU_MEMBERS)]
    latest = []
    for fuel, g in members.groupby("fuel"):
        greek = g.loc[g["country"] == "GR", "date"]
        if len(greek):
            latest.append(g[g["date"] == greek.max()])
    pd.concat(latest).to_csv(data / "eu_latest.csv", index=False, lineterminator="\n")


def main(root):
    root = pathlib.Path(root).resolve()
    out = root / "build" / "site"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(root / "site", out)
    data = out / "data"
    data.mkdir(exist_ok=True)

    pump = pd.concat([pd.read_csv(p, dtype=str) for p in sorted((root / "data" / "pump").glob("[0-9]*.csv"))])
    names = {a: ("national" if a == "national" else el) for a, el, _, _ in AREAS}
    columns = ["national", *sorted((el for a, el, _, _ in AREAS if a != "national"), key=unaccented)]
    for fuel in FUELS:
        wide = pump.pivot(index="date", columns="area_id", values=fuel).rename(columns=names)
        wide = wide.reindex(columns=columns).dropna(how="all").sort_index()
        wide.to_csv(data / f"{fuel}_daily_by_area.csv", lineterminator="\n")
    eu_greece(root, data)
    for src in [root / "data" / "model" / "forecast.csv", root / "data" / "model" / "passthrough_curve.csv",
                root / "build" / "site-data" / "chain.csv", root / "build" / "site-data" / "steo.csv"]:
        shutil.copy(src, data / src.name)
    shutil.copy(root / "datapackage.json", out / "datapackage.json")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"site: {len(list(data.iterdir()))} data files, {size:.1f} MB, last bulletin {pump['date'].max()}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
