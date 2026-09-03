# NHDataHub — Project Overview & Phase-2 Handoff

*Drop this file (and `DATA_SOURCES.md`) into a fresh Claude project to continue
work without the Phase-1 chat history. It captures what the project is, how it's
built, the hard-won lessons, where it stands, and where it's going.*

---

## 1. What this is

**NHDataHub** (repo `NHbot`, Python package `nhbot`) is a **nonpartisan New
Hampshire civic-data website**. It presents per-town and statewide public data —
property tax rates, town and school budgets, valuations, school spending, elected
officials and legislative representation, the state budget, and how NH's tax
burden compares nationally.

**Audience & mission.** It is built for **NH residents and people considering a
move to NH** — to help them understand how communities differ and why (especially
the property-tax picture), in plain language, from primary sources, with no
partisan slant. Design values throughout: **accuracy, clear sourcing, plain
explanation of mechanisms, and neutrality.** That credibility is the project's
core asset and every feature decision protects it.

**Owner:** John Wallace (johnwallace2@gmail.com). GitHub: `jwallz/nhbot`.

---

## 2. Tech stack & architecture

* **Backend:** Python, **FastAPI**, server-side rendering with **Jinja2**, **HTMX**
  for interaction (compare/schools tables filter/sort without full reloads).
* **Data layer:** **PostgreSQL**, schema `nh`. Two schema variants exist — a
  PostGIS one (`schema.sql`) and a no-PostGIS one (`schema_no_postgis.sql`); the
  app does not require PostGIS (maps are pre-projected to GeoJSON/SVG).
* **Maps:** server-rendered **SVG** (`web/mapsvg.py`) — the statewide town map, the
  per-town locator map, and the political-makeup map — projected from a Census
  boundary GeoJSON. No client map library.
* **CLI:** `nhbot <command>` (`src/nhbot/cli.py`) drives ingestion and loading.
  `nhbot all` = crosswalk → dra-official → dra-estimate → load; other modules run
  on demand. `nhbot load` ingests all processed CSVs into Postgres.
* **Ingestion:** `src/nhbot/ingest/*.py` — one module per source; parses a raw
  file (mostly PDF via **pdfplumber**, Excel via **openpyxl**, plus OCR via
  pymupdf+pytesseract for scanned town reports) into committed CSVs.

### Repo layout
```
src/nhbot/
  cli.py                 # command registry (CORE + EXTRA)
  config.py              # DATA_DIR / RAW_DIR / PROCESSED_DIR, NHBOT_DSN
  ingest/                # one module per data source (see DATA_SOURCES.md §3)
  db/
    schema.sql           # PostGIS variant
    schema_no_postgis.sql
    load.py              # load_* funcs: CSV -> Postgres (self-creating/upsert)
  web/
    app.py               # FastAPI routes + usd/ord/slugify filters + /state,/national logic
    repo.py              # all SQL queries + derivations + constants
    mapsvg.py            # town_map, locator_map, political_map (+ _lean_color)
    slug.py              # slugify
    static/app.css
    templates/           # base, index, town, compare, schools, state, national,
                         # political, about, about_equalized, contact, towns, partials/
data/
  raw/                   # gitignored source snapshots (PDF/Excel/etc.)
  processed/             # committed CSVs (the reproducible artifacts)
docs/                    # DATA_SOURCES.md, PROJECT_OVERVIEW.md (this file)
```

### Running it locally
```bash
export NHBOT_DSN="dbname=nhbot"           # Postgres connection
nhbot load                                 # (re)load CSVs into the DB
uvicorn nhbot.web.app:app --reload         # serve at http://127.0.0.1:8000
```
Ingestion (only when refreshing data): drop the raw file in the right
`data/raw/…` folder, run the module's `nhbot <command>`, then `nhbot load`.

---

## 3. The pages (Phase-1 surface)

* **`/` home** — statewide town map (click a town) + type-ahead search.
* **`/{slug}` town page** — the flagship. Property-tax rate + history (advertised vs
  equalized, with the assessment-ratio explainer and DRA rank); **tax-base
  composition** (residential vs commercial/industrial — the "where the money comes
  from" mechanism: a bigger commercial base lowers the rate on homes); "where your
  tax dollar goes" (education/municipal/county split); total public operating
  budget; town budget by department; schools box (cost-per-pupil vs state);
  school finance buckets; spending-over-time trend charts; select board;
  state legislators; a mini state locator map.
* **`/compare`** — every town's tax rate, sortable/filterable (advertised or
  equalized), HTMX.
* **`/schools`** — cost-per-pupil comparison across town-districts.
* **`/state`** — the State of NH's own budget & revenue (not town rollups):
  spending by category/department, how the budget is funded, and an **info-icon
  drill-down** on the Federal Funds bar breaking federal money down by receiving
  agency (surfaces that ~65% is the federal Medicaid share via DHHS). State tax &
  fee revenue with year-over-year deltas.
* **`/national`** — how NH's **household** tax burden compares to other states
  (property + income + sales + excise as a share of income). Hero tiles, a
  per-household bill breakdown, and a 50-state ranking (low→high). NH is 2nd-lowest
  burden but ~5th-highest effective property rate — "the trade-off."
* **`/political`** — statewide political-makeup map colored by each town's General
  Court delegation on a continuous blue↔purple↔red gradient (every legislator
  weighted equally), with all-R / split / all-D summary tiles.
* **`/about`, `/about/equalized-rates`, `/contact`** — explainers + contact form
  (stores to `contact_message`, optional SMTP). About/Contact live in the footer.

---

## 4. Environment & workflow notes (important for a fresh session)

This project has been developed from a **cloud sandbox linked to John's Mac** via a
connected folder (`~/Claude/Projects/NHbot`). Key realities a new session should
know:

* **The Postgres DB and the running uvicorn server live on John's Mac**, not in the
  cloud sandbox. The sandbox has a working *copy* of the source at
  `/tmp/nhbot/pkg` used for editing/previewing; changes are synced to the Mac.
* **Sync pattern used in Phase 1:** edit in the sandbox → `tar -czf` the changed
  files → deliver + commit into `~/Claude/Projects/NHbot/_sync_X.tgz` on the Mac →
  `tar --overwrite -xzf` there. The device bridge **cannot delete files** (`rm`
  fails), so stray files are `mv`'d into a `_to_delete/` folder (gitignored).
* **Git through the bridge quirk:** because the bridge can't delete, git leaves a
  stray `.git/index.lock` after commands that would normally remove it (even
  `git status`). It blocks the next write; clear it by `mv`-ing it aside, or run
  git from the Mac's own terminal. The Mac's git identity isn't set inside the
  bridge VM — set it locally (`John Wallace <johnwallace2@gmail.com>`) if committing
  from the sandbox.
* **Government-host egress was blocked** from the sandbox (see DATA_SOURCES.md §2);
  raw files were downloaded manually via browser. **John's AWS pipeline will not
  have this limit** — that's the point of Phase 2.1's automation.
* **Preview pattern for template work:** render a template standalone with Jinja +
  inlined `app.css`, screenshot with Playwright (`executable_path=
  '/opt/pw-browsers/chromium'`), because the live server is on the Mac.
* **Cache-proofing:** critical fill colors (SVG maps, legend bars) are inlined into
  markup so a stale cached `app.css` can't blank a visual (learned from a
  black-locator-map bug).

---

## 5. Hard-won lessons / gotchas

* **Per-town budget PDFs (MS-535) are wildly heterogeneous** — text vs scanned vs
  name-only summaries; the 13 cities use a different GASB format. Bespoke parsing
  per town is a rabbit hole. Phase-1 outcome: parse what parses cleanly, mark the
  rest `needs_review`, and a **DRA RSA 91-A right-to-know request was drafted** for
  the underlying data. (Not yet sent.) *For Phase 2.3, note that RAG over these PDFs
  needs only text extraction, not the painful line-item parsing — much more
  tractable.*
* **Individual town MS-1 valuation PDFs are scanned images**; the born-digital DRA
  **"Tables by County"** reports (10 files) were the clean statewide source and all
  234 towns reconcile to DRA gross valuation.
* **Tax Foundation "Facts & Figures" parser reads hardcoded page/table indices** —
  it happened to be stable between the 2025 and 2026 editions, but it is the most
  pagination-fragile ingest and should be re-verified each new edition.
* **The household tax-burden % is basis-invariant** (per-capita vs per-household
  cancels), so per-household *dollars* (component × avg household size) are
  consistent with the % ranking — that's how /national shows believable dollar
  figures ($8k+ property) while keeping the ranking honest.
* **Name normalization is everything** — the GEOID crosswalk + `_norm()` (lowercase,
  strip apostrophes/punct) is the join key across all sources; a handful of
  Census-unnormalizable names use explicit overrides.

---

## 6. Status

**Phase 1 is complete and committed** (`main`, commit tag "Phase 1: statewide
context layers…"). All pages above are live. `main` is a few commits ahead of
`origin/main` — **not yet pushed** (John pushes when ready to launch). The site is
launchable as-is.

---

## 7. Phase 2 roadmap

* **Phase 2.1 — Data-source inventory (this deliverable).** Done: see
  `DATA_SOURCES.md`. Foundation for both the refresh pipeline and the AI agent.
* **Phase 2.2 — Town-level real estate data.** Add macro real-estate signals per
  town: median home price, days on market, price/sqft trend. Candidate free
  sources: **Redfin Data Center** and **Zillow Research** (downloadable market
  files at city/place level, attribution licenses — verify commercial-use terms),
  Realtor.com inventory files; authoritative but licensed: the regional MLS
  (**NEREN / PrimeMLS**). Caveat: small towns have sparse/suppressed data — fall
  back to county context. A focused sourcing research pass is the first step.
* **Phase 2.3 — RAG pipeline + AI-agent chatbot.** Let users ask questions of the
  data and of NH/town documents beyond what any page shows. Recommended shape:
  a **hybrid** agent — **SQL/tool access over the structured Postgres schema** for
  quantitative questions (the data is already clean and structured; "we don't need
  a page for everything"), **plus RAG** over unstructured docs (town reports,
  town/state websites). On AWS: **Bedrock** (Claude) for reasoning, **pgvector on
  Aurora Postgres** as the vector store (no separate vector DB needed), optionally
  **Bedrock Knowledge Bases** to manage chunk/embed/retrieve, crawling for the
  websites. Non-negotiables for a civic site: **grounded answers with citations**;
  never invent a figure. Freshness depends on the 2.1 refresh pipeline.
* **Monetization (concept only, last / maybe).** "Sponsor a town" flat placements
  and/or a find-an-agent-or-contractor tool (Google **Places** API + labeled
  sponsored slots). Strategic guardrail: sponsored content must be clearly labeled
  and walled off from the factual data so it never compromises neutrality. Note
  RESPA considerations for real-estate lead/referral compensation (get professional
  advice). Not a near-term build.

### Infra direction (John's plan, deferred)
N8N workflows orchestrating the Python ingest scripts, on AWS: a small managed
**Aurora PostgreSQL** + the app on a small **EC2**. N8N handles scheduling and
"has the source published a new file?" checks; Python does the parse/load/recompute.
Aurora's pgvector then doubles as the Phase-2.3 vector store.

---

## 8. Suggested first move in the new project

Start with **Phase 2.2 real-estate sourcing research** (concrete, standalone value
and the on-ramp to any future monetization), *or* stand up the **Phase 2.1 refresh
pipeline** against this inventory if the infra is ready. The AI agent (2.3) is the
higher-ceiling effort and benefits from everything else being in place first.
