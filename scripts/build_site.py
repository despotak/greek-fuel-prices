# /// script
# dependencies = ["pandas", "pyarrow"]
# ///
"""Assemble the GitHub Pages site in build/site/ from site/ and the data.

Usage: uv run --script build_site.py <root>

The dashboard reads one wide CSV per fuel (a row per bulletin day, a column per area: "national",
then the 51 prefectures by their Greek name), the model outputs, and the EIA-based series that
build_outlook.py wrote to build/site-data/.

For other programs and agents (see site/llms.txt) it also publishes the canonical data/ tree as is,
so datapackage.json resolves on the site, plus long, typed Parquet tables, latest.json and
manifest.json under data/agents/.
"""
import datetime as dt
import json
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


PUBLISHED_DATA = ["areas.csv", "pump", "refinery", "eu", "international", "model"]


def for_agents(root, out, pump):
    """data/ as published, three long Parquet tables with real date columns and units in the names,
    the newest bulletin day as JSON, and when each source was last refreshed."""
    # The published parts of data/ only: a local checkout also holds git-ignored working files there.
    for part in PUBLISHED_DATA:
        src = root / "data" / part
        if src.is_dir():
            shutil.copytree(src, out / "data" / part, dirs_exist_ok=True)
        else:
            shutil.copy(src, out / "data" / part)
    agents = out / "data" / "agents"
    agents.mkdir(exist_ok=True)

    long = pump.melt(id_vars=["date", "area_id"], value_vars=FUELS, var_name="fuel", value_name="eur_per_litre")
    long = long[long["eur_per_litre"].notna() & (long["eur_per_litre"] != "")]
    long = long.astype({"eur_per_litre": float})
    long["date"] = pd.to_datetime(long["date"]).dt.date
    long.sort_values(["date", "area_id", "fuel"]).to_parquet(agents / "pump_prices.parquet", index=False)

    ref = pd.read_csv(root / "data" / "refinery" / "refinery_prices.csv").rename(
        columns={"elpe": "elpe_eur_per_unit", "motor_oil": "motor_oil_eur_per_unit", "mean": "mean_eur_per_unit",
                 "mean_incl_vat": "mean_incl_vat_eur_per_unit"})
    ref["date"] = pd.to_datetime(ref["date"]).dt.date
    ref.to_parquet(agents / "refinery_prices.parquet", index=False)

    eu = pd.read_csv(root / "data" / "eu" / "weekly_oil_bulletin.csv").rename(
        columns={"price_with_taxes": "eur_per_litre_with_taxes", "price_without_taxes": "eur_per_litre_without_taxes"})
    eu["date"] = pd.to_datetime(eu["date"]).dt.date
    eu.to_parquet(agents / "eu_weekly_oil_bulletin.parquet", index=False)

    day = long["date"].max()
    names = {a: (el, en) for a, el, en, _ in AREAS}
    latest = {"date": day.isoformat(), "unit": "EUR per litre, 24% VAT included", "areas": {}}
    for _, r in long[long["date"] == day].iterrows():
        a = latest["areas"].setdefault(r["area_id"], {"name_el": names[r["area_id"]][0], "name_en": names[r["area_id"]][1]})
        a[r["fuel"]] = r["eur_per_litre"]
    (agents / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def newest(table):
        return {"newest_date": table["date"].max().isoformat(), "rows": len(table)}
    manifest = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "pump_prices": {**newest(long), "cadence": "daily bulletin, posted the next morning", "stale_after_days": 4},
        "refinery_prices": {**newest(ref), "cadence": "every working day", "stale_after_days": 5},
        "eu_weekly_oil_bulletin": {**newest(eu), "cadence": "weekly, prices for Monday", "stale_after_days": 16},
    }
    (agents / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


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
    for_agents(root, out, pump)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"site: {len(list(data.iterdir()))} data files, {size:.1f} MB, last bulletin {pump['date'].max()}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
