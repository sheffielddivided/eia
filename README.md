# eia

Data collection (GitHub Actions) and pages for EIA, Eurostat and European gas data.

## STEO (Short-Term Energy Outlook)

| File | Script | Content |
|---|---|---|
| `data/eia/steo.json` | `scripts/fetch-steo.py` | Current STEO release, Tables 3a and 3c (world oil balance), monthly/quarterly/annual, from the EIA API |
| `data/eia/steo_vintages.json` | `scripts/fetch-steo-vintages.py` | Earlier STEO releases (last 24 months), same series, from EIA's archive of STEO workbooks (`https://www.eia.gov/outlooks/steo/archives/<mon><yy>_base.xlsx`); the API only serves the current release. Quarterly and annual values are derived as in the API (day-weighted averages, inventories at end of period). Each series is stored as `[first period, values]`. Only missing releases are downloaded. Needs `openpyxl` |

Both run daily in `update-eia-data.yml`. ncs-production copies them (`fetch_eugas.py`) to Macro → Oil market.
