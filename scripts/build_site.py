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
    for src in [root / "data" / "model" / "forecast.csv", root / "data" / "model" / "passthrough_curve.csv",
                root / "build" / "site-data" / "chain.csv", root / "build" / "site-data" / "steo.csv"]:
        shutil.copy(src, data / src.name)
    shutil.copy(root / "datapackage.json", out / "datapackage.json")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"site: {len(list(data.iterdir()))} data files, {size:.1f} MB, last bulletin {pump['date'].max()}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
