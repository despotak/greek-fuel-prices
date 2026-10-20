# /// script
# dependencies = ["numpy", "pandas", "xlrd"]
# ///
"""Build the dashboard's Outlook datasets from the pass-through model.

Usage: uv run --script build_outlook.py <project_root>

Writes the committed model outputs to data/model/:
  forecast.csv          expected pump change in 7 and 14 days, per area and fuel, with its backtest record
  passthrough_curve.csv cumulative share of a price move passed on, by day
and the site-only series built from EIA prices (not republished) to build/site-data/:
  chain.csv             daily international and refinery prices, in EUR/L at the pump
  steo.csv              EIA Short-Term Energy Outlook, monthly, in USD and EUR/L
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import passthrough as pt
from areas import AREAS

FUELS = ["heating_oil", "diesel", "unleaded95", "unleaded100"]
HORIZONS = (7, 14)
TEST_FROM = "2024-01-01"
MIN_TRAIN = 200


def backtest_fast(df, h):
    """The expanding-window backtest of passthrough.backtest, with running normal equations.

    For each test day t the model is fitted on every complete row dated t - h or earlier,
    exactly as the slow version does, then predicts pump(t+h) - pump(t).
    """
    data = pt.features(df, h).dropna()
    mom = (df["pump"] - df["pump"].shift(7)).reindex(data.index)
    X = np.column_stack([np.ones(len(data)), data[pt.FEATS].to_numpy()])
    y = data["y"].to_numpy()
    xtx = np.cumsum(np.einsum("ni,nj->nij", X, X), axis=0)
    xty = np.cumsum(X * y[:, None], axis=0)
    dates = data.index.to_numpy()
    test = np.where(data.index >= TEST_FROM)[0]
    pred, real, momentum = [], [], []
    for i in test:
        k = np.searchsorted(dates, dates[i] - np.timedelta64(h, "D"), side="right") - 1
        if k + 1 < MIN_TRAIN:
            continue
        coef = np.linalg.solve(xtx[k], xty[k])
        pred.append(X[i] @ coef)
        real.append(y[i])
        momentum.append(mom.iloc[i])
    pred, real, momentum = map(np.array, (pred, real, momentum))
    if not len(real):
        return None
    return {
        "n": len(real),
        "mae": np.mean(np.abs(real - pred)),
        "mae_naive": np.mean(np.abs(real)),
        "hit": np.mean(np.sign(pred) == np.sign(real)),
        "hit_momentum": np.mean(np.sign(momentum) == np.sign(real)),
    }


def chain(root):
    frames = pt.load(root)
    out = pd.DataFrame(index=frames["diesel"].index)
    for fuel in FUELS:
        df = frames[fuel]
        out[f"intl_{fuel}"] = ((df["intl"] + pt.EXCISE[fuel]) * 1.24).round(4)
        out[f"refinery_{fuel}"] = (df["refinery"] * 1.24).round(4)
    out = out.dropna(how="all")
    out.index.name = "date"
    return out.reset_index().assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d"))


def curve(root):
    frames = pt.load(root)
    excl = ~((frames["diesel"].index >= "2022-03-01") & (frames["diesel"].index < "2022-07-01"))
    rows = []
    for fuel in FUELS:
        d = frames[fuel].loc[excl]
        b1, _ = pt.lag_fit(d["refinery"], d["intl"], 14)
        b2, _ = pt.lag_fit(d["pump_prevat"], d["refinery"], 28)
        rows += [{"fuel": fuel, "stage": "refinery", "day": k, "share": round(float(v), 3)} for k, v in enumerate(np.cumsum(b1))]
        rows += [{"fuel": fuel, "stage": "pump", "day": k, "share": round(float(v), 3)} for k, v in enumerate(np.cumsum(b2))]
    return pd.DataFrame(rows)


def forecasts(root):
    rows = []
    for area_id, name_el, _, _ in AREAS:
        frames = pt.load(root, area_id)
        area = "national" if area_id == "national" else name_el
        for fuel in FUELS:
            df = frames[fuel]
            if df["pump"].notna().sum() < MIN_TRAIN:
                continue
            row = {"area_id": area_id, "area": area, "fuel": fuel}
            for h in HORIZONS:
                now = pt.nowcast(df, (h,))[h]
                bt = backtest_fast(df, h)
                row.update({"date": str(now["date"]), "pump": round(float(now["pump"]), 3),
                            f"change_{h}": round(float(now["change"]), 4)})
                if bt:
                    row.update({f"mae_{h}": round(bt["mae"], 4), f"mae_naive_{h}": round(bt["mae_naive"], 4),
                                f"hit_{h}": round(bt["hit"], 3), f"hit_momentum_{h}": round(bt["hit_momentum"], 3),
                                f"n_{h}": bt["n"]})
            rows.append(row)
        print(f"  {area}", file=sys.stderr)
    return pd.DataFrame(rows)


def steo(root):
    """EIA's Short-Term Energy Outlook, monthly, in EUR/L at the latest ECB rate (held for the forecast)."""
    fx = pd.read_csv(root / "data" / "international" / "ecb_eurusd.csv").iloc[-1]
    s = pd.read_csv(root / "build" / "steo_monthly.csv")
    per = s["unit"].map({"USD/bbl": pt.BBL, "USD/gal": pt.GAL})
    s["eur_per_litre"] = (s["value"] / per / fx["usd_per_eur"]).round(4)
    s["eurusd"], s["eurusd_date"] = fx["usd_per_eur"], fx["date"]
    return s


def main(root):
    root = pathlib.Path(root).resolve()
    model, site = root / "data" / "model", root / "build" / "site-data"
    model.mkdir(parents=True, exist_ok=True)
    site.mkdir(parents=True, exist_ok=True)
    national = pt.load(root)["diesel"]
    check = backtest_fast(national, 7)
    print(f"consistency: national diesel 7d MAE {check['mae']*100:.2f}c vs no-change {check['mae_naive']*100:.2f}c, hit {check['hit']:.0%}", file=sys.stderr)
    # Committed: our own model outputs. Site only: series built from EIA prices (not republished in data/).
    outputs = ((model, "forecast", forecasts(root)), (model, "passthrough_curve", curve(root)),
               (site, "chain", chain(root)), (site, "steo", steo(root)))
    for folder, name, frame in outputs:
        path = folder / f"{name}.csv"
        frame.to_csv(path, index=False, lineterminator="\n")
        print(f"{path.relative_to(root)}: {len(frame)} rows, {path.stat().st_size / 1e3:.0f} kB", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
