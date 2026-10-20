# /// script
# dependencies = ["numpy", "pandas", "xlrd"]
# ///
"""How Greek pump prices follow refinery and international prices, and whether that predicts them.

Usage: uv run --script passthrough.py <project_root>

Chain measured, all in EUR per litre:
  international product (EIA NY Harbor spot, ECB EUR/USD)  ->  Greek ex-refinery price  ->  pump price
Pump prices are national daily averages from the bulletins; refinery prices are the ELPE/Motor Oil
mean from oil.gge.gov.gr (taxes included, before VAT). Prints every number it reports.
"""
import functools
import pathlib
import sys

import numpy as np
import pandas as pd
import xlrd

GAL = 3.785411784
BBL = 158.987294928
EXCISE = {"diesel": 0.410, "unleaded95": 0.700, "unleaded100": 0.700, "heating_oil": 0.280}
REFINERY_FUEL = {"diesel": "DIΕSEL AUTO BIO", "unleaded95": "UNLEADED 95 BIO", "unleaded100": "UNLEADED 100 BIO",
                 "heating_oil": "HEATING GASOIL (ΧΠ)"}
DIESEL_SPOT, GASOLINE_SPOT = "EER_EPD2DXL0_PF4_Y35NY_DPG", "EER_EPMRU_PF4_Y35NY_DPG"
INTL = {"diesel": DIESEL_SPOT, "unleaded95": GASOLINE_SPOT, "unleaded100": GASOLINE_SPOT, "heating_oil": DIESEL_SPOT}
REGIONS = {"national": "national", "attica": "attiki"}
# EIA publishes its daily spot prices about a week after the fact, so on any day the model may only
# use international prices from a week earlier. Features are built with that delay.
INTL_LAG_DAYS = 7
START = "2019-01-01"
END = pd.Timestamp.today().normalize().strftime("%Y-%m-%d")


def eia(path):
    book = xlrd.open_workbook(path)
    sh = book.sheet_by_name("Data 1")
    rows = [(pd.Timestamp(*xlrd.xldate_as_tuple(sh.cell_value(r, 0), book.datemode)[:3]), sh.cell_value(r, 1))
            for r in range(3, sh.nrows) if sh.cell_value(r, 0) != "" and sh.cell_value(r, 1) != ""]
    return pd.Series(dict(rows)).sort_index()


@functools.cache
def markets(root):
    """Everything that does not depend on the area: FX, refinery and international prices, all pump rows.

    Reads the repository's open data (data/) and the EIA files fetched into build/ by build_open_data.py.
    """
    days = pd.date_range(START, END, freq="D")
    fx = pd.read_csv(root / "data" / "international" / "ecb_eurusd.csv", parse_dates=["date"])
    fx = fx.set_index("date")["usd_per_eur"].reindex(days).ffill(limit=5)

    ref = pd.read_csv(root / "data" / "refinery" / "refinery_prices.csv", usecols=["date", "product", "mean"], parse_dates=["date"])
    ref = ref[ref["mean"].notna()]
    refinery = {f: (ref[ref["product"] == name].set_index("date")["mean"] / 1000).reindex(days).ffill(limit=5)
                for f, name in REFINERY_FUEL.items()}

    eia_dir = root / "build" / "eia"
    intl = {f: eia(eia_dir / f"{INTL[f]}.xls").reindex(days).ffill(limit=5) / GAL / fx for f in EXCISE}
    brent = eia(eia_dir / "RBRTE.xls").reindex(days).ffill(limit=5) / BBL / fx
    pump = pd.concat([pd.read_csv(p, parse_dates=["date"]) for p in sorted((root / "data" / "pump").glob("[0-9]*.csv"))])
    return days, refinery, intl, brent, pump


def load(root, region="national"):
    """Per fuel, a daily frame of pump (for the area), refinery and international prices in EUR/L.

    `region` is a key of REGIONS or an area_id from data/areas.csv (e.g. "thessaloniki").
    """
    days, refinery, intl, brent, pump_all = markets(pathlib.Path(root).resolve())
    pump = pump_all[pump_all["area_id"] == REGIONS.get(region, region)].set_index("date")

    out = {}
    for fuel, excise in EXCISE.items():
        df = pd.DataFrame({
            "pump": pump[fuel].reindex(days),
            "refinery": refinery[fuel],
            "intl": intl[fuel],
            "brent": brent,
        }, index=days)
        if fuel == "heating_oil":
            season = (df.index.month >= 11) | (df.index.month <= 3) | ((df.index.month == 10) & (df.index.day >= 15)) | (df.index.month == 4)
            df.loc[~season, ["pump", "refinery"]] = np.nan
            # Same product as road diesel at a different excise rate, so its refinery *change* stands in
            # where the heating price does not exist yet (the first week of a season).
            df["refinery_alt"] = out["diesel"]["refinery"]
        df["pump_prevat"] = df["pump"] / 1.24
        df["margin"] = df["pump_prevat"] - df["refinery"]
        df["refinery_ex_excise"] = df["refinery"] - excise
        df["premium"] = df["refinery_ex_excise"] - df["intl"]
        out[fuel] = df
    return out


def lag_fit(y, x, max_lag):
    """OLS of dy_t on dx_{t-k}, k = 0..max_lag, on days where all terms exist. Returns betas and n."""
    dx = x.diff()
    X = pd.concat({k: dx.shift(k) for k in range(max_lag + 1)}, axis=1)
    data = pd.concat([y.diff().rename("y"), X], axis=1).dropna()
    beta, *_ = np.linalg.lstsq(data.drop(columns="y").to_numpy(), data["y"].to_numpy(), rcond=None)
    return beta, len(data)


def asym_fit(y, x, max_lag):
    dx = x.diff()
    up, down = dx.clip(lower=0), dx.clip(upper=0)
    X = pd.concat({**{("u", k): up.shift(k) for k in range(max_lag + 1)}, **{("d", k): down.shift(k) for k in range(max_lag + 1)}}, axis=1)
    data = pd.concat([y.diff().rename("y"), X], axis=1).dropna()
    beta, *_ = np.linalg.lstsq(data.drop(columns="y").to_numpy(), data["y"].to_numpy(), rcond=None)
    return beta[: max_lag + 1].sum(), beta[max_lag + 1:].sum()


FEATS = ["gap", "dref7", "dintl7"]


def features(df, h):
    """Target pump(t+h) - pump(t) and what is known at t.

    gap: distance from the pump price to refinery + the median margin of the last 90 priced days
    (counted in observations, so heating oil's summer break does not empty the window).
    """
    margin = df["margin"].dropna()
    m90 = margin.rolling(90, min_periods=30).median().reindex(df.index).ffill()
    dref7 = df["refinery"] - df["refinery"].shift(7)
    if "refinery_alt" in df:
        dref7 = dref7.fillna(df["refinery_alt"] - df["refinery_alt"].shift(7))
    return pd.DataFrame({
        "y": df["pump"].shift(-h) - df["pump"],
        "gap": (df["refinery"] + m90) * 1.24 - df["pump"],
        "dref7": dref7,
        "dintl7": df["intl"].shift(INTL_LAG_DAYS) - df["intl"].shift(INTL_LAG_DAYS + 7),
    })


def fit(train):
    A = np.column_stack([np.ones(len(train)), train[FEATS].to_numpy()])
    coef, *_ = np.linalg.lstsq(A, train["y"].to_numpy(), rcond=None)
    return coef


def backtest(df, horizons=(7, 14), start="2024-01-01"):
    """Predict pump(t+h) - pump(t) with information known at t. Compare against 'no change'.

    gap:  close the distance to refinery + trailing median margin (no fitted parameters).
    ecm:  expanding-window OLS on the margin gap and the last 7 days of refinery and international moves.
    """
    res = {}
    for h in horizons:
        data = features(df, h)
        test_days = data.loc[start:].dropna().index
        preds, naive, simple, used = [], [], [], []
        for t in test_days:
            train = data.loc[: t - pd.Timedelta(days=h)].dropna()
            if len(train) < 200:
                continue
            coef = fit(train)
            row = data.loc[t]
            preds.append(coef[0] + coef[1:] @ row[FEATS].to_numpy())
            simple.append(row["gap"])
            naive.append(0.0)
            used.append(row["y"])
        y = np.array(used)
        res[h] = {
            "n": len(y),
            "mae_naive": np.mean(np.abs(y)),
            "mae_gap": np.mean(np.abs(y - np.array(simple))),
            "mae_ecm": np.mean(np.abs(y - np.array(preds))),
            "dir_hit_ecm": np.mean(np.sign(np.array(preds)) == np.sign(y)),
            "last_coef": coef,
        }
    return res


def nowcast(df, horizons=(7, 14)):
    """Expected pump change from the latest day with a pump price, fitted on all history before it."""
    out = {}
    for h in horizons:
        data = features(df, h)
        t = df["pump"].last_valid_index()
        coef = fit(data.loc[: t - pd.Timedelta(days=h)].dropna())
        row = data.loc[t, FEATS]
        out[h] = {"date": t.date(), "pump": df.at[t, "pump"], "change": coef[0] + coef[1:] @ row.to_numpy(),
                  **row.round(4).to_dict(), "intl_last": df["intl"].last_valid_index().date()}
    return out


def main(root):
    root = pathlib.Path(root)
    for region in REGIONS:
        print(f"\n##### nowcast, {region}")
        for fuel, df in load(root, region).items():
            for h, r in nowcast(df).items():
                print(f"{fuel:12} from {r['date']} at {r['pump']:.3f}: expected change in {h:2d}d {r['change']*100:+.1f}c "
                      f"(gap {r['gap']*100:+.1f}c, refinery 7d {r['dref7']*100:+.1f}c, intl 7d {r['dintl7']*100:+.1f}c; intl to {r['intl_last']})")
    data = load(root)
    for fuel, df in data.items():
        print(f"\n=== {fuel} ===")
        lv = df.dropna(subset=["pump", "refinery", "intl"])
        print(f"days with all three: {len(lv)}  ({lv.index[0].date()} .. {lv.index[-1].date()})")
        for col, label in (("margin", "pump pre-VAT minus refinery (distribution margin)"),
                           ("premium", "refinery ex-excise minus NY Harbor spot")):
            s = lv[col]
            by_year = s.groupby(s.index.year).median().round(3).to_dict()
            print(f"{label}: median {s.median():.3f}  p10 {s.quantile(.1):.3f}  p90 {s.quantile(.9):.3f}  by year {by_year}")

        excl = ~((df.index >= "2022-03-01") & (df.index < "2022-07-01"))
        for name, mask in (("all", slice(None)), ("excl Mar-Jun 2022", excl)):
            d = df.loc[mask] if name != "all" else df
            b1, n1 = lag_fit(d["refinery"], d["intl"], 14)
            b2, n2 = lag_fit(d["pump_prevat"], d["refinery"], 28)
            c1, c2 = np.cumsum(b1), np.cumsum(b2)
            print(f"[{name}] refinery <- intl   (n={n1}): cumulative pass-through day0 {c1[0]:.2f}, 1d {c1[1]:.2f}, 3d {c1[3]:.2f}, 7d {c1[7]:.2f}, 14d {c1[14]:.2f}")
            print(f"[{name}] pump     <- refinery (n={n2}): cumulative pass-through day0 {c2[0]:.2f}, 3d {c2[3]:.2f}, 7d {c2[7]:.2f}, 14d {c2[14]:.2f}, 28d {c2[28]:.2f}")
        up, down = asym_fit(df["pump_prevat"], df["refinery"], 28)
        print(f"asymmetry pump <- refinery over 28d: rises pass {up:.2f}, falls pass {down:.2f}")
        for h, r in backtest(df).items():
            print(f"backtest h={h}d (n={r['n']}, from 2024): MAE no-change {r['mae_naive']*100:.2f}c  gap-rule {r['mae_gap']*100:.2f}c  "
                  f"ecm {r['mae_ecm']*100:.2f}c  (ratio {r['mae_ecm']/r['mae_naive']:.2f})  direction hit {r['dir_hit_ecm']:.0%}")


if __name__ == "__main__":
    main(sys.argv[1])
