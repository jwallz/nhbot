# NHDataHub — Data-Source & Computation Inventory

**Purpose.** This document is the single source of truth for *what data the site
contains, where each piece comes from, how often it changes, how it is acquired,
and what the site computes on top of it.* It is the specification the Phase-2
refresh automation (N8N + Python on AWS) is built against, and the schema map an
AI agent (Phase 2.3) needs to answer questions from the database.

Last updated: **September 2026** (end of Phase 1).

---

## 1. How the data flows

```
  external source            data/raw/…            data/processed/…           Postgres (schema: nh)         web app
  (agency PDF/Excel/  ──►  raw snapshot   ──►   nhbot <cmd> parses   ──►   nhbot load writes tables  ──►  FastAPI + Jinja
   CSV/HTML/shapefile)      (gitignored)          → CSV (committed)          (self-creating/upsert)        renders pages
```

* **Acquisition** — a raw file is downloaded into `data/raw/<…>/`. `data/raw/` is
  **gitignored**; only the *processed* CSVs are committed (so the repo is
  reproducible without redistributing source PDFs).
* **Ingest** — `nhbot <command>` runs one module in `src/nhbot/ingest/` that parses
  the raw file and writes one or more CSVs to `data/processed/`.
* **Load** — `nhbot load` runs the `load_*` functions in `src/nhbot/db/load.py`,
  which create/upsert the Postgres tables (schema `nh`). Most loaders are
  self-creating (`CREATE TABLE IF NOT EXISTS`) and TRUNCATE-and-reload.
* **Serve** — `src/nhbot/web/` (app.py routes, repo.py queries, mapsvg.py maps,
  Jinja templates) reads the tables and computes the derived figures in §5.

`nhbot all` runs the four CORE steps in order: `crosswalk → dra-official →
dra-estimate → load`. Every other module (the `EXTRA` set) is run on demand.
Config: `src/nhbot/config.py` (`DATA_DIR`, `RAW_DIR`, `PROCESSED_DIR`; DSN from
`NHBOT_DSN`).

---

## 2. ⚠️ Acquisition constraint (critical for the refresh pipeline)

Throughout Phase 1, **New Hampshire government hosts blocked automated fetches**
from both the cloud sandbox and the device bridge (HTTP 403): `revenue.nh.gov`,
`das.nh.gov`, `gc.nh.gov`, and the Tax Foundation PDF host. `WebFetch` could read
some *HTML* pages (e.g. `gc.nh.gov`, `taxfoundation.org` article pages, summarized)
but **not** the binary PDF/Excel downloads. The practical result was that **every
raw file had to be downloaded manually through a browser** and then handed to the
parser.

**Implication for Phase 2 automation (good news):** this block was a property of
*this sandbox's egress*, not of the sources themselves. An **AWS EC2 host on your
own infra will not have that restriction** and can fetch these files directly on a
schedule. So the "download step" that was manual in Phase 1 becomes automatable in
the N8N/EC2 design — that is exactly the gap the pipeline closes. Each source below
notes whether it exposes a stable URL (directly fetchable) or is published as a
"find the current year's file" landing page (needs a small locate-then-download
step, a good fit for a scripted or agentic fetcher).

---

## 3. Data-source catalog

Columns: **Cadence** = how often the upstream source actually changes / republishes.
**Acquire** = current acquisition reality. **Fragility** = what tends to break a refresh.

### 3.1 Foundational geography

| Dataset | Source | Format | Cadence | CLI / module | Processed CSV | DB tables | Fragility |
|---|---|---|---|---|---|---|---|
| **GEOID crosswalk** (the join spine) | US Census 2023 Gazetteer (county subdivisions) + NH DRA canonical name list | TXT (tab) + Excel | Rarely (boundary/name changes) | `crosswalk` / `geoid_crosswalk.py` | `nh_municipality_geoid_crosswalk.csv` | `county`, `municipality`, `municipality_alias`, `village_district` | Name normalization; 3 Census-unnormalizable names use `GEOID_OVERRIDE` |
| **Boundaries** (map polygons) | US Census cartographic boundary shapefile `cb_2023_33_cousub_500k` (1:500k, EPSG:4269) | Shapefile (.shp/.shx/.dbf/.prj) | Annual release, changes rarely | `boundaries` / `boundaries.py` `[geo]` | `nh_municipalities.geojson`, `.topojson` | none (map asset) | Multi-file download; joins on GEOID |
| **Map labels** (label anchors) | *Derived* from boundaries GeoJSON (shapely pole-of-inaccessibility) | — | Recompute when boundaries change | `map-labels` / `map_labels.py` `[geo]` | `nh_map_labels.json` | none | Depends on boundaries running first |

The crosswalk is the universal 10-digit **GEOID** key; almost every other module
joins to it by normalized name. 259 rows: 13 cities + 221 towns + 25 unincorporated.

### 3.2 Property-tax rates & valuation (the core of the site)

| Dataset | Source | Format | Cadence | CLI / module | Processed CSV | DB tables | Fragility |
|---|---|---|---|---|---|---|---|
| **Equalized rates (official)** | NH DRA "Comparison of Full Value Tax Rates (Ranking Order)" | PDF, one/yr (2019–2024) | **Annual** (prior year, published ~fall) | `dra-official` / `dra_official.py` | `nh_equalized_rates_official.csv` | `equalized_rate` (official), `equalization_ratio`, `tax_rate`, `source_load` | Born-digital PDF table extraction; `difflib` name repair |
| **2025 rates + ratios + valuations (estimate)** | NH DRA workbooks: municipal & village tax rates; ratio/COD/PRD 10-yr history; tables-by-county | 3× Excel | **Annual** | `dra-estimate` / `dra_estimate.py` | `nh_2025_equalized_rates.csv` | `tax_rate` (4-way split + commitment/valuation), `equalized_rate` (estimate), `equalization_ratio` | Estimate bridges until DRA publishes the official comparison; ~+0.13 bias caveat; joins 3 workbooks by name |
| **Valuation by class** (tax-base composition) | NH DRA "Tables by County" (equalization / MS-1 figures) | PDF, one per county (10) | **Annual** | `valuation` / `valuation.py` | `nh_valuation_class.csv` | `valuation_class` | County-by-county (10 PDFs); reconciles res/C&I/util/other to DRA gross; "strafford" misspelled in a filename |

DRA URL base seen in code: `https://www.revenue.nh.gov/sites/g/files/ehbemt736/files/documents/`
(direct file links, but 403 from the sandbox). The **official 2025 comparison was
not yet published** as of Aug 2026, which is why `dra_estimate` computes an estimate.

### 3.3 Schools

| Dataset | Source | Format | Cadence | CLI / module | Processed CSV | DB tables | Fragility |
|---|---|---|---|---|---|---|---|
| **School structure / CPP / enrollment** | NH DOE (district-town map, cost-per-pupil, student ratios) | TSV + CSV | **Annual** (school year) | `doe-schools` / `doe_schools.py` | `nh_school_structure.csv`, `_sau.csv`, `_district.csv`, `nh_district_finance.csv`, `_enrollment.csv` | `sau`, `school_district`, `town_district`, `district_finance`, `district_enrollment` | Hardcoded vintages `CPP_YEAR=2025`, `ENROLL_YEAR=2022`; academies/facilities unmatched by design |
| **DOE-25 district financials** (where school money comes from / goes) | NH DOE-25 Annual Financial Report, "District Profile" sheet, via iPlatform | Excel, one per district per year | **Annual** | `doe-finance` / `doe_finance.py` | `nh_district_expenditure.csv`, `_revenue.csv`, `_cpp.csv` | `district_expenditure`, `district_revenue`, `district_finance` | Multi-file (13 FY folders `fy2013…fy2025`); tolerant "Function" header scan; FK-guards to `school_district` |

### 3.4 Municipal budgets (hardest layer)

| Dataset | Source | Format | Cadence | CLI / module | Processed CSV | DB tables | Fragility |
|---|---|---|---|---|---|---|---|
| **Town budgets (MS-535 actuals / MS-232 / MS-737)** | Each town's annual report, UNH Scholars Repository `scholars.unh.edu/nh_town_reports` | PDF per town (text **and scanned/OCR**) | **Annual** (town meeting / fiscal year) | `municipal` / `municipal.py` | `nh_municipal_expenditure.csv`, `nh_municipal_status.csv` | `municipal_expenditure` | **Highest difficulty.** Per-town formats vary wildly; text vs scanned (OCR via pymupdf+pytesseract) vs `needs_review` (name-only summaries; the 13 cities' GASB format). Per-town subprocess w/ SIGKILL timeout; skip-list; resumable |
| **Municipal coverage roster** | *Derived* (which towns are loaded / needs_review / missing) | — | Recompute after municipal | `municipal-coverage` / `municipal_coverage.py` | `nh_municipal_coverage.csv` | `municipality.ms535_*` cols | "What's left to gather" tracker over 234 incorporated munis |

> **Phase-1 decision:** rather than write a bespoke parser per town for the ~156
> `needs_review` reports, we drafted a **DRA RSA 91-A right-to-know request** for the
> underlying MS-535 data. Revisit in Phase 2 — either send that request or accept
> partial coverage. This is the one layer where full automation is not yet solved.

### 3.5 Municipal profile & civic metadata

| Dataset | Source | Format | Cadence | CLI / module | Processed CSV | DB tables | Fragility |
|---|---|---|---|---|---|---|---|
| **Town websites** | GRANIT "NH Municipal Sites (Towns)" `granit.unh.edu/pages/nh-municipal-sites-towns` | JSON (JS-rendered capture) | Rarely | `municipal-websites` / `municipal_websites.py` | `nh_municipality_website.csv` | `municipality.website`, `.website_source` | JS app omits ~17 munis → in-code `GAPFILL` |
| **Gov form / incorporation / 2020 pop** | Wikipedia "List of municipalities in NH" | JSON | Slowly | `municipal-profile` / `municipal_profile.py` | `nh_municipality_profile.csv` | `municipality.form_of_government`, `governing_body`, `sb2`, `year_incorporated`, `population_2020` | Wikipedia table shape |
| **Town history blurb** | Each town's Wikipedia article | JSON (pre-extracted via WebFetch) | Slowly | `town-history` / `town_history.py` | `nh_town_history.csv` | `municipality.history`, `.history_source`; may upgrade `sb2` | Refresh = re-run WebFetch extraction, overwrite raw JSON |

### 3.6 Officials & legislature (political layer)

| Dataset | Source | Format | Cadence | CLI / module | Processed CSV | DB tables | Fragility |
|---|---|---|---|---|---|---|---|
| **Select boards / councils** | NH DOT "City and Town Officials" directory | PDF (grid) | **Annual** | `select-board` / `select_board.py` | `nh_select_board.csv` | `select_board` | Grid PDF; name only on block's first row; detects chair (excludes vice) |
| **Legislators (roster)** | NH General Court `gc.nh.gov` roster | TXT (cp1252) | **Every election** (2-yr); + special elections / party switches | `legislature` / `legislature.py` | `nh_legislators.csv` | `legislator` | Manual download (gc.nh.gov 403) |
| **House / Senate districts → towns** | RSA 662:5 (House) / 662:3 (Senate) statute text | HTML | **Decennial redistricting** (last 2022) | `legislature` / `legislature.py` | `nh_town_house_district.csv`, `nh_town_senate_district.csv` | `town_house_district`, `town_senate_district` | Greedy longest-match town segmentation of delimiter-free statute text; base + floterial + ward-split cities |

### 3.7 State fiscal & national comparison (statewide layer)

| Dataset | Source | Format | Cadence | CLI / module | Processed CSV | DB tables | Fragility |
|---|---|---|---|---|---|---|---|
| **State operating budget + funding** | NH LBA enacted "HB 1" operating-budget workbook | Excel | **Biennial** (odd-year session) | `state-fiscal` / `state_fiscal.py` | `nh_state_budget.csv`, `nh_state_funding.csv`, `nh_state_federal_funds.csv` | `state_budget`, `state_funding`, `state_federal_funds` | TYPE=E→expenditure, TYPE=F→funding; Federal Funds split by agency (Phase-1 add) |
| **State tax & fee revenue** | NH DAS "Monthly Revenue Focus" (June year-end) | PDF | **Monthly** published; use **annual** June year-end | `state-fiscal` / `state_fiscal.py` | `nh_state_revenue.csv` | `state_revenue` | Page-3 YTD table parsed via Actual−Plan=variance arithmetic |
| **NH vs. other states (tax burden)** | Tax Foundation "Facts & Figures: How Does Your State Compare?" (figures from US Census / BEA) | PDF | **Annual** edition | `tax-comparison` / `tax_comparison.py` | `nh_state_comparison.csv` | `state_tax_comparison` | **Parses hardcoded page/table indices** (Tables 2,5,7,13,20,32,33,34,41,42); fragile to any pagination change between editions |

Current national vintage: **Census FY2023 / BEA 2024, via Facts & Figures 2026**.

---

## 4. Refresh-cadence summary (the scheduling spec)

Group work in the pipeline by how often the upstream actually changes:

* **Annual — property/tax core (highest value):** DRA official comparison, DRA
  rate/ratio/valuation workbooks, DRA Tables-by-County valuation, DOE schools,
  DOE-25 finance, DOT officials directory, town budgets. These are the pages
  residents care about most; schedule a yearly refresh window keyed to each
  agency's typical publication month.
* **Annual — statewide:** DAS revenue (June year-end), Tax Foundation Facts &
  Figures (new edition).
* **Biennial:** State operating budget (HB 1) — only republished each budget cycle.
* **Every election cycle (~2 yr) + ad-hoc:** legislator roster (plus special
  elections and party changes between cycles — worth a lighter periodic check).
* **Decennial:** legislative district→town maps (next after 2030 census).
* **Rare / slow-drift:** Census Gazetteer + boundaries, GRANIT town-site index,
  Wikipedia profile & history. Poll infrequently or on notification.
* **Derived (recompute, don't re-download):** map labels, municipal coverage, and
  **all of §5** — these must recompute whenever their inputs change.

**Freshness monitoring** (a natural N8N job): watch each agency's landing page for
a new file (hash/last-modified/new-year-in-filename) and only trigger the
download→parse→load→recompute chain when something actually changed.

---

## 5. Computed / derived values (what the pipeline must recompute)

These are **not** raw columns — they are computed at query/render time. A refresh
must re-run them after new data loads. Grouped by page.

### Global display helpers (`app.py`)
* `usd` — adaptive `$X.XXB / $XM / $XK / $X`, `—` for null.
* `ord` — integer → ordinal ("2nd", "11th").
* `slugify` — town name → URL slug.

### Home map (`mapsvg.town_map`)
* Cos-latitude projection (`k=cos(mean lat)`) → pixel coords; SVG paths from rings.
* Greedy in-region label placement (largest area first; fits chord width and no
  bbox collision, else hover-only). Type-ahead list = cities+towns, alphabetical.

### Town page (`/{slug}`)
* **Rate history** — per-year UNION of `equalized_rate` + `tax_rate` + ratio;
  "assesses at ~{ratio}% of market"; "rank {dra_rank} of 234" (**234 hardcoded**);
  official/estimate badge from `is_official`.
* **Tax-base composition** — each class `pct = round(100·value/gross,1)` for
  residential / commercial_industrial / utilities / other; C&I % is the headline;
  "Other" legend shown only if `≥0.5%`.
* **Where your tax dollar goes** — education = `local_ed+state_ed`; three-way
  education/municipal/county each `pct = rate/total_rate·100`; only when the 2025
  split exists.
* **Total public operating budget** — municipal total (latest `kind`, prefers
  `actual`) + each district's latest-year recurring spend (`pct IS NOT NULL`);
  `shared` if district serves >1 town; per-part `pct`; gated to ≥2 parts.
* **Town budget by department** — category rollup via `MUNI_SLUG` (else "Other"),
  top 8 departments, each `pct`.
* **Schools box** — latest CPP & enrollment per district (LATERAL); grade-span
  sort; "vs state" `= (cpp_total − 22699.85)/22699.85·100`.
* **School finance** — expenditure/revenue function/source codes bucketed into
  `EXP_GROUPS`/`REV_GROUPS`; `admin_pct` and `proptax_pct` callouts.
* **Spending-over-time** — CPP series, admin-share and SpEd-share series (by
  function code), y-scales, `_points` SVG projection, `cpp_change`.
* **Select board** — board label from most-common role; chair-first sort.
* **Legislators** — House (base+floterial) & Senate rosters (exclude Former);
  party normalization; district labels.
* **Locator map** (`locator_map`) — same projection; target geoid `here` vs `ctx`.

### `/compare`
* Default year `= max(tax_year)`; whitelisted sort column (injection-safe);
  equalized pick prefers official over estimate (LATERAL); per-town CPP; rank =
  `loop.index`; estimate `*` footnote.

### `/schools`
* Whitelisted sort; one row per town-district; "vs state" %; rank = `loop.index`.

### `/state`
* Primary FY `= min(budget years)`; biennium total; by-category / by-department
  aggregations; revenue total (×1e6); **revenue YoY delta** vs FY−1;
  **federal-funds drill-down** — by agency, top 6 + "Other" combined, each
  `pct = round(100·amount/ftot)`, bar scaled to largest agency; DHHS callout from
  the biggest agency.

### `/national`
* `n_states` = count with a burden rank; `hh_list` sorted ascending (ex-DC);
  **prop_share** = property's % of NH's 4-part household basket; **per_household**
  = each per-capita component × `hh_persons_per_household`; breakdown/burden bar
  scaling; effective-property tile "of 51" (**hardcoded**); I&D footnote shown when
  `hh_income_pc ≠ 0`.

### `/political`
* Per-town D/R/I/N tallies (House+Senate, exclude Former); **`lean` = (r−d)/(r+d)**
  over major parties → −1…+1; **`_lean_color`** interpolates blue→purple→red;
  no-major = grey `#9a9a95`, unincorporated = `#d9d9d4`; summary tiles all-R /
  split / all-D.

---

## 6. Reference constants & vintages to review periodically

These are **hardcoded** and must be checked/bumped when data is refreshed:

| Constant | Current value | Where | Review trigger |
|---|---|---|---|
| `STATE_CPP_TOTAL` | 22699.85 | repo.py (town, schools "vs state") | New DOE CPP year |
| `STATE_CPP_YEAR` | 2025 | repo.py | New DOE year |
| DRA rank denominator | "of 234" | town.html | Municipality count change |
| Municipality count | "234 municipalities" | index.html, about.html | Rare |
| Eff-property-rank denom | "of 51" | national.html | Facts & Figures structure |
| National vintage | Census FY2023 / BEA 2024 / **Facts & Figures 2026** | national.html method | New edition |
| I&D repeal note | "repealed at the start of 2025" | national.html | Stable (historical) |
| Budget cite | HB 1, Laws of 2025 | state.html | New biennium |
| Select-board vintage | NH DOT directory, Sept 2025 | town.html | New directory |
| Legislature vintage | 2025–2026 General Court; 2022 redistricting | political.html, town.html | New session / redistricting |
| Enrollment vintage | NH DOE fall 2021–22 | schools/town | New DOE year |
| DOE CPP hardcode | `CPP_YEAR=2025`, `ENROLL_YEAR=2022` | doe_schools.py | New DOE year |

> Consider migrating these into a single config table or module so a refresh can
> bump them in one place instead of hunting through templates.

---

## 7. Database tables (schema `nh`)

Foundational: `source_load`, `county`, `entity_type`, `municipality` (the wide
per-town table — geography + website + gov form + history + ms535 status),
`municipality_alias`, `village_district`.

Tax: `tax_rate`, `equalization_ratio`, `equalized_rate` (+ view
`equalized_rate_latest`), `valuation_class`.

Schools: `sau`, `school_district`, `town_district`, `district_finance`,
`district_enrollment`, `district_expenditure`, `district_revenue`.

Municipal budgets: `municipal_expenditure`.

Politics: `legislator`, `town_house_district`, `town_senate_district`,
`select_board`.

State/national: `state_budget`, `state_funding`, `state_federal_funds`,
`state_revenue`, `state_tax_comparison`.

Web: `contact_message`.

Full column definitions live in `src/nhbot/db/schema_no_postgis.sql` (and the
PostGIS variant `schema.sql`). Note that the politics, state, valuation, and
comparison tables are **also** self-created inside their `load_*` functions, so
they load even without applying the SQL file first.

---

## 8. Validation / non-pipeline artifacts

`nh_2024_equalized_rates_DRA_official.csv` and
`validation_2024_method_comparison.csv` are QA artifacts comparing our estimate
method to DRA's official figures (used to validate the `dra_estimate` approach);
they have no ingest module and are not served. `nh_equalized_rate_2024_map.html`
is a generated preview map.
