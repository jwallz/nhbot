# NHbot — New Hampshire Civic Data

A comparable, machine-readable dataset for **every New Hampshire municipality**
(234 towns/cities + 25 unincorporated places), anchored on Census GEOIDs. The
flagship is an **equalized (full-value) property-tax** dataset that makes towns
actually comparable — the advertised rate does not, because assessment ratios
vary widely.

Reranking towns by equalized vs. advertised rate moves **126 of 234 more than 20
positions**. This project publishes the honest number.

## Status

Phase 0 complete: DRA official equalized rates **2019–2024**, a labeled **2025
estimate**, the equalization-ratio series, and a canonical **GEOID crosswalk**,
all loaded into a provenance-tracked PostgreSQL schema.

## Quick start

```bash
# 1. environment
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# 2. database (local Postgres; no-PostGIS variant stores centroids as lat/lon)
createdb nhbot
psql -d nhbot -f src/nhbot/db/schema_no_postgis.sql
cp .env.example .env         # set NHBOT_DSN if needed

# 3. run the pipeline
export NHBOT_DSN="dbname=nhbot"
nhbot all                    # crosswalk -> dra-official -> dra-estimate -> load
#   or step by step: nhbot crosswalk | dra-official | dra-estimate | load

# 4. verify
pytest -q
psql -d nhbot -c "SELECT name, tax_year, full_value_rate, is_official
                  FROM nh.equalized_rate_latest ORDER BY full_value_rate DESC LIMIT 5;"
```

PostGIS is optional. Use `schema.sql` (geometry centroid) if you have PostGIS;
`schema_no_postgis.sql` otherwise. The loader auto-detects which schema it's
talking to.

## Layout

```
src/nhbot/
  config.py            paths + DSN from env
  cli.py               `nhbot` command
  ingest/
    geoid_crosswalk.py Census gazetteer -> municipality GEOID crosswalk
    dra_official.py    DRA "Comparison of Full Value Tax Rates" PDFs -> official series
    dra_estimate.py    current-year equalized-rate ESTIMATE (labeled)
  db/
    schema.sql, schema_no_postgis.sql, load.py
scripts/validate_2024.py   methodology calibration vs DRA published
data/
  raw/         source snapshots (gitignored; see data/SOURCES.md)
  processed/   generated CSVs (committed)
docs/          project brief + findings
tests/
```

## Data & methodology

- **Join key is the 10-digit Census GEOID**, never names. See `data/SOURCES.md`
  for where each source comes from (and why DRA needs a browser, not `curl`).
- **Equalized rate**: for any published year, ingest DRA's official figure; for
  the current year before DRA publishes, compute `total_rate × ratio` and label
  it an estimate (~+0.13 bias vs official, median |err| 0.085). See
  `docs/VALIDATION_2024.md`.

## Roadmap

Next: GRANIT boundary polygons (the clickable map), then DOE (schools), SOS
(elections), and ACS (demographics) — each a new fact table on the same GEOID
spine. Production target: Python + PostgreSQL/PostGIS on EC2.

## License

MIT (see `LICENSE`). Nonpartisan civic infrastructure — corrections welcome.
