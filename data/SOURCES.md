# Data sources

`data/raw/` is **gitignored** — it holds source-file snapshots. This manifest is
the record of what to fetch and from where. DRA (`revenue.nh.gov`) is behind
Akamai bot management: plain `curl`/`requests` get "Access Denied" (TLS
fingerprinting), so DRA files are downloaded via a real browser. The Census
Gazetteer is a keyless static download.

## NH DRA — tax rates (Excel)
`data/raw/<year>/`
- Municipal & Village District Tax Rates (4-way split + commitment) — used for the 2025 estimate.
  https://www.revenue.nh.gov/.../documents/2025-municipal-and-village-district-tax-rates.xlsx
- Equalization ratio ten-year history — `ratio-median-ratio-cod-prd-ten-year-history.xlsx`
- Tables by county / tax-rate calculation data — supporting.

## NH DRA — Comparison of Full Value Tax Rates (PDF)  [OFFICIAL equalized rate]
`data/raw/<year>/<year>-comparison-of-full-value-tax-rates-ranking-order.pdf`
- Path differs by year: 2019–2022 under `inline-documents/sonh/municipal-property/`,
  2023–2024 under `documents/`.
- Loaded years: 2019, 2020, 2021, 2022, 2023, 2024. (2025 not yet published by DRA.)

## US Census — Gazetteer (county subdivisions)  [GEOID crosswalk]
`data/raw/geo/2023_Gaz_cousubs_national.txt`
- https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_cousubs_national.zip
- Filter USPS == NH. Keyless. (The Census *API* now requires a free key.)

## Vintages currently loaded
| dataset | vintages |
|---|---|
| Municipality GEOID crosswalk | Census 2023 |
| Equalized rate — DRA official | 2019–2024 |
| Equalized rate — estimate | 2025 |
| Equalization ratio | 2019–2025 |
| Tax rate (4-way split) | 2025 |
