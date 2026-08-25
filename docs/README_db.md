# NHbot Database — canonical schema & loader

GEOID-anchored PostgreSQL + PostGIS schema for the NH municipal dataset, plus an
idempotent loader for the Phase 0 CSVs. Validated end-to-end on PostgreSQL 16 /
PostGIS 3.4.

## Setup

```bash
# 1. create the database and enable PostGIS (schema.sql also enables it)
createdb nhbot
psql -d nhbot -f db/schema.sql

# 2. load the Phase 0 CSVs (idempotent — safe to re-run)
pip install psycopg2-binary
export NHBOT_DSN="dbname=nhbot user=<you>"        # any libpq DSN
python3 db/load.py
```

`load.py` reads the CSVs from `../phase0/` relative to itself; adjust `P` in the
script if you relocate them.

## What's in it

Reference / spine:
- **county** — 10 NH counties (FIPS + name).
- **entity_type** — city / town / unincorporated / village_district.
- **municipality** — the canonical spine, PK = 10-digit **GEOID**. Name, entity
  type, county, Census name, land/water area, and PostGIS **centroid** (point).
  A `boundary` MultiPolygon column is defined and waiting for GRANIT polygons.
- **municipality_alias** — alternate names/codes per source (census loaded; DRA/
  DOE/SOS slots ready) so any source joins back to a GEOID.
- **village_district** — Penacook → host Concord (village districts have no GEOID).

Facts (one row per municipality per tax year, upserted on the natural key):
- **equalized_rate** — the flagship number. Holds DRA-**official** rows
  (`is_official = true`, 2019–2024) and the current-year **estimate**
  (`is_official = false`, 2025) side by side, each with its `method`.
- **equalization_ratio** — weighted-mean ratio per year (2019–2025 loaded;
  median/COD/PRD columns ready).
- **tax_rate** — advertised rate + 4-way component split (2025 loaded).

Provenance:
- **source_load** — one row per (source, file, vintage); every fact row carries a
  `load_id` FK, so each number traces to a source + retrieval date + data vintage.

View:
- **equalized_rate_latest** — most recent rate per municipality, preferring an
  official figure over an estimate.

## Loaded now vs pending

| Data | Status |
|---|---|
| Municipality spine + GEOID + centroids | ✅ 259 rows |
| Equalized rate, DRA official | ✅ 2019–2024 (1,554 rows) |
| Equalized rate, estimate | ✅ 2025 (259 rows) |
| Equalization ratio | ✅ 2019–2025 |
| Tax rate + 4-way split | ✅ 2025 only |
| Tax rate + split, historical | ⬜ needs 2019–2024 tax-rate workbooks |
| Boundary polygons | ⬜ GRANIT (Phase 1 map) |
| DOE / SOS / ACS facts | ⬜ next MVP sources |

## Design notes

- **GEOID is the only join key.** Names never join facts to the spine — the
  loader resolves each source's name to a GEOID once, via the crosswalk.
- **Idempotent by construction.** Every write is `INSERT … ON CONFLICT DO UPDATE`
  on the natural key, so re-running a load reconciles instead of duplicating
  (verified: two consecutive loads yield identical row counts).
- **Official and estimate coexist** in one table via `is_official`; when DRA
  publishes the 2025 comparison, re-run the official ingester and the 2025 row
  flips from estimate to official on the same PK.
- **Entity typing follows DRA**, not Census (e.g. Livermore is unincorporated
  here though Census calls it a town) — the Census name is retained for tracing.
