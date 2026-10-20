# Greek fuel prices

Daily average pump prices for **every Greek prefecture since March 2017**: unleaded 95 and 100, diesel, autogas, heating oil (and Super until 2022). The data is read from the Ministry of Development's daily PDF bulletins and published here as plain CSV. The repository also holds Greek refinery prices and a model of how pump prices follow them. Everything refreshes itself every day.

**Dashboard:** https://despotak.github.io/greek-fuel-prices/. Pick any prefecture and fuel to see:
- today's prices and their history;
- how this season or year compares with earlier ones;
- where the area ranks;
- an outlook for the next two weeks.

## The data

All files are UTF-8 CSV, described in [`datapackage.json`](datapackage.json) ([Frictionless Data Package](https://specs.frictionlessdata.io/), validated on every update).

| File | What it holds |
|---|---|
| `data/areas.csv` | The 51 prefectures (νομοί) and the national average: `area_id`, Greek and English name, and the label as printed in the bulletins |
| `data/pump/2017.csv` … | One row per area per day: `date, area_id, unleaded95, unleaded100, super, diesel, autogas, heating_oil`. EUR per litre **including 24% VAT**, the averages exactly as the bulletin prints them. One file per year. |
| `data/pump/missing_days.csv` | Every day between the first and last bulletin without data, and why: never published, a notice instead of prices, or a mislabelled copy of another day |
| `data/pump/transcribed.csv` | Days no parser can read, typed in by hand from the bulletin's page image (twelve scanned bulletins of summer 2020, one broken layout in 2017), each checked against a machine reading of the page (OCR, or the PDF's text layer for 2017), the neighbouring days, and the national average rebuilt from the prefecture prices for unleaded 95, 100 and diesel. The same rows are in the yearly files. |
| `data/pump/day_notes.csv` | Days that needed care to read, and how: a garbled font map, a borrowed column order, a mislabelled file filed under the day its body names, a bulletin without its national average, a hand transcription |
| `data/pump/accepted_anomalies.csv` | Days whose large price jumps were checked against the bulletin and accepted as published |
| `data/refinery/refinery_prices.csv` | Daily ex-refinery prices of ELPE and Motor Oil per product; `basis` says what is included and the unit |
| `data/international/ecb_eurusd.csv` | ECB euro reference rate (USD per EUR) |
| `data/eu/weekly_oil_bulletin.csv` | The European Commission's Weekly Oil Bulletin since 2005: every EU country, the EU and euro-area averages, with and without taxes, in EUR per litre (95, diesel, heating oil, LPG). © European Union, CC BY 4.0; converted from EUR per 1,000 litres, fuel oil left out. Greece's figures match the daily bulletins' national average to the tenth of a cent on all but 11 of 474 shared Mondays, so `validate.py` warns when a recent week disagrees. |
| `data/eu/tax_rates.csv` | Every change of VAT, excise duty and other indirect taxes on those four fuels in each EU country, with the date it came into force (from the same bulletin, CC BY 4.0). Excise is per litre, in the currency the country used at the time. |
| `data/model/forecast.csv` | The two-week outlook per area and fuel, with its backtest record |
| `data/model/passthrough_curve.csv` | How much of a price move reaches the refinery and the pump after 0–28 days |

Notes on the pump data:
- **Heating oil** is "Diesel Θέρμανσης Κατ' οίκον", delivered to the home. It is reported only **from 15 October to 30 April**, the season with reduced excise duty, so it is empty every summer.
- **Super** was sold by a handful of stations by late 2021 (5 nationwide in November 2021). Its averages say little, and the bulletins stopped listing it in 2022.
- **`national`** is the average the bulletin prints, weighted by each prefecture's sales (2019 volumes in recent bulletins). Five bulletins leave it out; it stays blank on those days rather than being rebuilt.
- **Autogas** includes two known glitches in the source: the national price is printed as 1.123 on 8 and 10 August 2022, against about 0.98 on the days around them. The data keeps what the bulletin says.

## How it updates

A GitHub Actions workflow runs three times a day, at 07:17, 13:47 and 19:37 UTC (`.github/workflows/daily.yml`). GitHub delays or drops scheduled runs when it is busy, so one is not enough; a run with nothing new commits nothing.

1. **New bulletins.**
   - Lists every bulletin fuelprices.gr publishes and downloads the ones the data does not have yet.
   - Parses and appends them, then deletes the PDFs.
   - A bulletin that cannot be read goes into `missing_days.csv` with the reason. A bulletin posted late is picked up on the next run.
2. **Refinery prices, the ECB rate and the EU Weekly Oil Bulletin** are refreshed into `data/` (a failed EU download keeps the last good file). **EIA prices** are downloaded for the model and the dashboard only (see below).
3. **The model** is refitted on all history and writes the outlook.
4. **Validation gates** stop a bad day from being published (`scripts/validate.py`):
   - Every prefecture is present on each new day (a missing national average only warns: some bulletins leave it out).
   - Prices are in range.
   - The national price lies between the cheapest and dearest area.
   - The newest bulletin is recent, and files never shrink.
   - The run fails if more than five areas jump by more than 0.15 €/L on the same day. That has never happened since 2017, while a misread column moves every area at once.
   - If the source itself is wrong, add the date to `accepted_anomalies.csv`.
5. The data is committed and the dashboard is rebuilt and deployed to GitHub Pages.

## Reading 3,400 PDFs, honestly

The bulletin archive is not one format, and `scripts/parse_bulletins.py` handles each variation explicitly:
- **The column order changes over time.** Heating oil and autogas swap places in October 2020, and Super appears and disappears in late 2022. So each file's own header decides which column is which.
- **Some 2017–2020 files have broken font encodings,** where ΑΤΤΙΚΗΣ prints as ΑΣΣΙΚΗ΢ or ΑΣΣΗΚΖ΢. Prefecture names are recovered by aligning the rows to the fixed prefecture order, and accepted only through one consistent letter mapping.
- **A few files have no text header,** some are scans, and a few are another day's bulletin under the wrong name. One, filed as 2 February 2020, is really 2 August 2020: its garbled date line is decoded and checked.
- **Checked against the EU:** the national prices match the EU Weekly Oil Bulletin's Greek series for unleaded 95, diesel and heating oil, with a median difference of 0.000 €/L on every Monday since 2017.

## The outlook

Pump prices follow the refinery with about a week's lag, and the refinery follows international wholesale prices within days. The model (`scripts/passthrough.py`, `scripts/build_outlook.py`) combines three things known on the day:
- how far the pump price is from the refinery price plus its usual margin;
- the refinery price's move over the last week;
- the international price's move over the last week. EIA publishes international prices about a week late, so the model only uses them as they were known then.

It is backtested every day since January 2024 against assuming no change. Over 7 days it cuts the average error by about a quarter to a third, depending on the fuel, and gets the direction right 77–81% of the time (a "same as last week" guess: about 70%).

There is no open source of oil futures prices. Exchange settlements (CME/NYMEX, ICE) are licensed, and the EIA stopped republishing them in 2024. The dashboard therefore shows **EIA's monthly Short-Term Energy Outlook** as the forward view. That is EIA's forecast, not a market price.

## Sources and terms

- **Pump prices:** Ministry of Development, Fuel Price Observatory, [fuelprices.gr](https://www.fuelprices.gr/deltia_dn.view).
- **Refinery prices:** the Ministry's price observatory at [oil.gge.gov.gr](https://oil.gge.gov.gr), read from the feed that site uses (its last ten working days) on every run. The history before October 2026, and a backup when the feed is down, come from [akosiaris/greek-oil-refinery-prices](https://github.com/akosiaris/greek-oil-refinery-prices) (whose code is AGPL-3.0); both give identical rows for the days they share. `validate.py` warns when the newest refinery price is more than 5 days old, or the newest EU week more than 16.
- **EUR/USD:** [European Central Bank](https://data.ecb.europa.eu/), reused with attribution.
- **International prices and outlook:** [US Energy Information Administration](https://www.eia.gov/). EIA's daily spot prices come from Refinitiv (LSEG), so they are **downloaded when the dashboard is built and not republished** in `data/`.
- **Derived data** (`data/model/`): CC BY 4.0. **Code:** MIT (see `LICENSE`).

Credit each source when you reuse its data.

## Running it locally

Requires [uv](https://docs.astral.sh/uv/) and `pdftotext` (poppler).

```bash
uv run --script scripts/update_bulletins.py .   # new bulletins
uv run --script scripts/build_open_data.py daily .
uv run --script scripts/build_outlook.py .
python3 scripts/make_datapackage.py .
uv run --script scripts/validate.py .
uv run --script scripts/build_site.py .          # the dashboard in build/site/
```

`scripts/fetch_bulletins.sh` mirrors the full PDF archive locally (about 2.3 GB), for re-parsing from scratch with `scripts/parse_bulletins.py`.
