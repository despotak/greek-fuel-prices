"""Write datapackage.json (Frictionless Data Package) from the CSVs actually in data/.

Usage: python3 make_datapackage.py <root>
Field order is read from each file's header, so the descriptor cannot drift from the data.
"""
import csv
import json
import pathlib
import sys

FUEL_DOC = {
    "unleaded95": "Unleaded 95 octane (Αμόλυβδη 95)", "unleaded100": "Unleaded 100 octane (Αμόλυβδη 100)",
    "super": "Super (until 2022; very few stations)", "diesel": "Road diesel (Diesel κίνησης)",
    "autogas": "Autogas, LPG (Υγραέριο κίνησης)", "heating_oil": "Heating oil delivered to the home (Diesel θέρμανσης κατ' οίκον); 15 Oct to 30 Apr only",
}
TYPES = {"date": "date", "month": "string", "day": "integer", "n_7": "integer", "n_14": "integer"}
PUMP_SRC = {"title": "Ministry of Development, Fuel Price Observatory: daily price bulletins per prefecture", "path": "https://www.fuelprices.gr/deltia_dn.view"}
REFINERY_SRC = [{"title": "Ministry price observatory, refinery prices (ELPE, Motor Oil)", "path": "https://oil.gge.gov.gr"},
                {"title": "read directly from the feed behind oil.gge.gov.gr (the last ten working days)", "path": "https://oil-be.tcloudapp.net/api/Posts/GetLastPosts"},
                {"title": "history and backup: greek-oil-refinery-prices (AGPL-3.0 code)", "path": "https://github.com/akosiaris/greek-oil-refinery-prices"}]
ECB_SRC = {"title": "European Central Bank, euro reference exchange rate (USD)", "path": "https://data.ecb.europa.eu/data/datasets/EXR"}
EU_SRC = {"title": "European Commission, DG Energy, Weekly Oil Bulletin, price history (© European Union, CC BY 4.0)", "path": "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"}
CC_BY = [{"name": "CC-BY-4.0", "path": "https://creativecommons.org/licenses/by/4.0/", "title": "Creative Commons Attribution 4.0"}]

RESOURCES = [
    ("areas", "data/areas.csv", "The 51 prefectures (νομοί) and the national average, with the label the bulletins print.",
     [PUMP_SRC], {"area_id": "Stable identifier used across the data"}, None),
    ("pump_prices", "data/pump/[0-9]*.csv", "Average retail price per litre by prefecture and day, in EUR including 24% VAT, as printed in each daily bulletin. One file per year.",
     [PUMP_SRC], {**FUEL_DOC, "area_id": "See areas.csv"}, ("area_id", "areas", "area_id")),
    ("missing_days", "data/pump/missing_days.csv", "Days between the first and last bulletin with no data, and why.",
     [PUMP_SRC], {}, None),
    ("transcribed", "data/pump/transcribed.csv", "Days no parser can read, typed in by hand from the bulletin's page image: twelve scanned bulletins of July-August 2020 and one with a broken text layout (27 March 2017). Each was checked against a machine reading of the page (OCR, or for 2017-03-27 the PDF's own text layer, which it matches cell for cell), against the neighbouring days, and against the national average rebuilt from the prefecture prices for unleaded 95, 100 and diesel; the rows also appear in the yearly pump files.",
     [PUMP_SRC], {**FUEL_DOC, "area_id": "See areas.csv"}, ("area_id", "areas", "area_id")),
    ("day_notes", "data/pump/day_notes.csv", "Days whose data needed care to read, and how: a garbled font map aligned to the prefecture order, a column order borrowed from a neighbouring day, a mislabelled file filed under the day its body names, a bulletin without its national average (left blank, never rebuilt), or a bulletin with no date line accepted under its filename's day after cross-checks.",
     [PUMP_SRC], {}, None),
    ("accepted_anomalies", "data/pump/accepted_anomalies.csv", "Days whose large price jumps were checked against the bulletin and accepted as published.",
     [PUMP_SRC], {}, None),
    ("eu_weekly_oil_bulletin", "data/eu/weekly_oil_bulletin.csv", "Weekly consumer prices in every EU country with and without taxes, since January 2005, as published by the European Commission in its Weekly Oil Bulletin. Changes made here: prices converted from EUR per 1,000 litres to EUR per litre; four of the six fuels kept (Euro-super 95, road diesel, heating gas oil, LPG; the two fuel oil grades left out); reshaped to one row per week, country and fuel, leaving out weeks a country reported nothing for that fuel. The taxes are the difference between the two prices. As published, Greece's heating-oil price is carried unchanged from May to mid-October, when it is not sold at the reduced rate; the dashboard leaves those weeks out.",
     [EU_SRC], {"country": "Two-letter country code; EU and EUR are the Commission's own EU and euro-area averages; UK only until it left the EU", "fuel": "euro95, diesel, heating_oil or lpg", "price_with_taxes": "EUR per litre, all duties and taxes included", "price_without_taxes": "EUR per litre, net of duties and taxes"}, None),
    ("eu_tax_rates", "data/eu/tax_rates.csv", "Every change of VAT, excise duty and other indirect taxes on 95, road diesel, heating gas oil and LPG in each EU country, from the European Commission's Weekly Oil Bulletin, with the date it came into force. Changes made here: excise and other taxes converted from per 1,000 litres to per litre; four of the six fuels kept; reshaped to one row per change (the source leaves a fuel's cell blank when its rate did not change). Excise and other taxes are in the currency the country used when the Commission recorded them, so Croatia (euro from 2023) and Bulgaria (from 2026) switch currency partway through; the per-component (bio/fossil) excise sheet is not included.",
     [EU_SRC], {"date": "Date the rate came into force", "country": "Two-letter country code (UK only until it left the EU)", "fuel": "euro95, diesel, heating_oil or lpg", "tax": "vat, excise or other_indirect", "value": "The rate: percent for VAT, an amount per litre for the others", "unit": "percent, or national currency per litre"}, None),
    ("refinery_prices", "data/refinery/refinery_prices.csv", "Daily ex-refinery prices of ELPE and Motor Oil per product. 'mean' and 'mean_incl_vat' as published; 'unit' and 'basis' are the source's Greek labels, and the last three columns say the same in plain terms.",
     REFINERY_SRC, {"unit_code": "m3 (prices per cubic metre, 1,000 litres) or t (per tonne)", "taxes_included": "excise_not_vat: excise and levies included, VAT not; none: before all taxes", "eur_per_litre_incl_vat": "For per-cubic-metre products: the price per litre with excise and 24% VAT, comparable to the pump price. Blank for per-tonne products: the source gives no density to convert with."}, None),
    ("ecb_eurusd", "data/international/ecb_eurusd.csv", "ECB euro reference rate, US dollars per euro.",
     [ECB_SRC], {}, None),
    ("model_forecast", "data/model/forecast.csv", "Expected change of the pump price 7 and 14 days after the latest bulletin per area and fuel (EUR/L), with the model's backtest record since 2024: mean absolute error (mae_*), the error of assuming no change (mae_naive_*), and the share of days the direction was right (hit_*) and the share a 'same as last week' guess got right (hit_momentum_*).",
     [PUMP_SRC, *REFINERY_SRC], {}, None),
    ("model_passthrough_curve", "data/model/passthrough_curve.csv", "Cumulative share of a price change passed on after 0-28 days: refinery after an international (EIA NY Harbor) move, and pump after a refinery move. Distributed-lag regression on daily data since 2019, March-June 2022 excluded.",
     [PUMP_SRC, *REFINERY_SRC], {}, None),
]


def main(root):
    root = pathlib.Path(root)
    resources = []
    for name, pattern, desc, sources, docs, fk in RESOURCES:
        paths = sorted(str(p.relative_to(root)) for p in root.glob(pattern))
        with open(root / paths[0], encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        fields = []
        for col in header:
            t = TYPES.get(col, "number" if col in FUEL_DOC or col.startswith(("change_", "mae_", "hit_", "share", "pump", "elpe", "motor_oil", "mean", "usd_per", "price_", "value", "eur_per")) else "string")
            field = {"name": col, "type": t}
            if col in docs:
                field["description"] = docs[col]
            fields.append(field)
        res = {"name": name, "path": paths if len(paths) > 1 else paths[0], "profile": "tabular-data-resource",
               "format": "csv", "encoding": "utf-8", "description": desc, "sources": sources,
               "schema": {"fields": fields, "missingValues": [""]}}
        if name.startswith(("model_", "eu_")):
            res["licenses"] = CC_BY
        if fk:
            res["schema"]["foreignKeys"] = [{"fields": fk[0], "reference": {"resource": fk[1], "fields": fk[2]}}]
        if name == "areas":
            res["schema"]["primaryKey"] = "area_id"
        resources.append(res)
    pkg = {
        "name": "greek-fuel-prices",
        "title": "Greek fuel prices by prefecture, daily",
        "description": "Daily average pump prices for every Greek prefecture since March 2017, read from the Ministry of Development's daily bulletins, with Greek refinery prices, the EU Weekly Oil Bulletin for comparison, and a model of how pump prices follow them. Updated every day.",
        "homepage": "https://despotak.github.io/greek-fuel-prices/",
        "profile": "tabular-data-package",
        "keywords": ["fuel prices", "Greece", "heating oil", "diesel", "gasoline", "open data"],
        "licenses": [{"name": "other-attribution", "title": "Each resource keeps the terms of its source; credit the source. Model outputs: CC BY 4.0."}],
        "contributors": [{"title": "Christos Despotakis", "role": "author", "path": "https://github.com/despotak"}],
        "resources": resources,
    }
    (root / "datapackage.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"datapackage.json: {len(resources)} resources", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
