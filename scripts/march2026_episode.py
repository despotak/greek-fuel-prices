# /// script
# dependencies = ["numpy", "pandas", "xlrd"]
# ///
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "scripts"))
import passthrough as pt
d = pt.load(pathlib.Path(sys.argv[1]))
h = d["heating_oil"]; dz = d["diesel"]
w = h.loc["2026-02-01":"2026-04-30", ["intl", "refinery", "pump"]].resample("W-MON").mean().round(3)
w["intl+excise+VAT"] = ((w["intl"] + 0.28) * 1.24).round(3)
print(w.to_string())
# first week each series rose >10% above its Feb mean
for c in ("intl", "refinery", "pump"):
    base = h.loc["2026-02-01":"2026-02-28", c].mean()
    s = h.loc["2026-03-01":"2026-04-30", c]
    hit = s[s > base * 1.10]
    print(f"{c:9} Feb mean {base:.3f}; first day >+10%: {hit.index[0].date() if len(hit) else None}")
