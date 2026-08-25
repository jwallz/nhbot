# New Hampshire Civic Data Project — Project Brief

> Seed document for a Claude Project. Paste into Project Instructions or add as Project Knowledge.
> Last updated: 2026-08-19

---

## 1. What this is

A single, complete, structured reference site for New Hampshire at the **municipal** level. The organizing metaphor is a clickable map of NH's counties and towns; the substance is a comparable dataset covering every municipality in the state.

The core user question the site should answer better than anything else online:

> "What is this town actually like — how it's governed, what it costs, how it votes, how its schools perform — and how does it compare to the other 233?"

Secondary: what's happening right now at the state and town level (news, calendars, meetings) without the national-politics filter.

**This is a hobby / learning project, not a revenue project.** Design decisions should favor data quality and personal interest over monetization or growth hacking.

---

## 2. Guiding principles

1. **Nonpartisan civic infrastructure, not a movement asset.** There is an active libertarian relocation movement (Free State Project) whose members are a natural early audience. Serve them by being neutral and correct, not by branding to them. Neutrality is what earns corrections and contributions from town clerks, librarians, and local reporters — which is the real long-term data moat.
2. **Don't rebuild what exists.** See §3. The state-legislature layer is well covered by others. The municipal layer is not.
3. **State-level sources before town-level scraping.** Nearly everything for the MVP can come from ~6 centralized state datasets. Scraping 234 heterogeneous town websites is phase 2 and should be deferred.
4. **Correctness over coverage.** One properly equalized tax rate beats fifty raw numbers that mislead.
5. **Static where possible.** Most of this data changes annually. Generate static pages from a database; don't build a live app that queries at request time.
6. **Cite and link everything.** Every number on a town page should have a source and vintage attached.

---

## 3. Prior art — what already exists (do not rebuild)

| Resource | What it covers | Implication |
|---|---|---|
| **Citizens Count** (citizenscount.org) | Nonpartisan nonprofit. Plain-English summaries of ~1,000 bills/session, profiles of every state/federal candidate, voting records, attendance, sponsorship, partisanship for every state elected official. Address→ward map, town clerk contacts. | The "who represents me + how do they vote" layer is done. Link to them; don't duplicate. |
| **GenCourtMobile** (NH Liberty Alliance) | Town/zip → state reps, Executive Council district, US congressional district. NHLA Liberty Rating on graded votes. "Votes Most Like" legislator similarity. Bill subscriptions. | Liberty-audience legislative tracking is served. |
| **gencourt.org** | Independent bill/vote/calendar tracker for NH legislators and citizens. | Another legislative tracker. |
| **LegiScan** (legiscan.com/NH) | Weekly bulk snapshots of all NH bill, vote, and legislator data in CSV/JSON/XML. Public API. | **Use as an ingest source.** Solved problem — don't scrape gencourt.nh.gov directly. |
| **Ballotpedia** | Broad but shallow; national template applied to NH. | Not a real competitor at town level. |
| **NH Municipal Association** | Member-facing; some town directory data. | Reference, not a source. |

**The whitespace:** no one has a complete, comparable, machine-readable dataset across all NH municipalities covering fiscal, governance, school, and electoral characteristics side by side.

---

## 4. NH structural facts that shape the data model

- **234 municipalities**: 13 cities, 221 towns. Plus ~25 unincorporated places (grants, purchases, locations, townships) that have almost no data and mostly zero population — handle as a distinct entity type, don't force them into the town schema.
- **10 counties.**
- **NH House: 400 members** — largest lower chamber in the US, roughly one rep per 3,400 residents. **Senate: 24. Executive Council: 5.**
- **Floterial districts.** NH House districts do not nest cleanly. Small towns share a base district, and *floterial* districts overlay multiple base districts to true up population. A voter can be in both a base and a floterial district simultaneously. This is the single most confusing part of NH civic geography and the thing a good tool could most usefully clarify.
- **Wards.** 12 cities are subdivided into voting wards (Claremont, Concord, Dover, Franklin, Keene, Laconia, Lebanon, Manchester, Nashua, Portsmouth, Rochester, Somersworth). District assignment happens at ward level, not town level, for these.
- **Property tax is the whole story.** NH has no general income or sales tax. The total tax rate decomposes into four components: **municipal, county, local education, state education**. Showing the split tells you where the money actually goes.
- **Equalization ratio.** Assessed values drift from market value between revaluations. Raw advertised tax rates are **not comparable across towns**. Equalized rate ≈ local rate × equalization ratio. Getting this right is the project's flagship correctness feature.
- **School governance is messy.** Districts may be single-town, cooperative (multi-town), or AREA agreements. Some towns operate no high school and tuition students out to a neighboring district or a private academy. **SAUs** (School Administrative Units) are administrative bodies that may serve one or several districts. Town → district → SAU is many-to-many-ish and must be modeled as such.
- **Government form varies**: traditional town meeting, SB 2 (official ballot referendum), town council, city council/mayor. Materially changes how budgets get set.

---

## 5. Data sources — MVP (no town-website scraping required)

| # | Source | Provides | Format | Cadence |
|---|---|---|---|---|
| 1 | **NH Dept. of Revenue Administration (DRA)** — revenue.nh.gov | Annual tax rates by municipality with 4-way component breakdown; equalization ratios / equalization survey; MS-1 (Summary Inventory of Valuation); MS-535 municipal financial reports | XLS / PDF | Annual |
| 2 | **NH Employment Security — ELMI** — nhes.nh.gov | Community Profiles for every municipality: population, labor force, unemployment, largest employers, commuting patterns, housing | HTML / PDF per town | Annual |
| 3 | **NH Secretary of State** — sos.nh.gov | Election results by town and ward (president, governor, US House/Senate, state offices), going back decades; candidate filings | PDF / XLS | Biennial |
| 4 | **NH Dept. of Education** — education.nh.gov | Fall enrollment, per-pupil cost (DOE-25), assessment results, district and SAU directory, town→district mapping | XLS / data portal | Annual |
| 5 | **US Census Bureau ACS** — api.census.gov | NH towns are census MCDs (county subdivisions), so ACS gives demographics, income, housing, tenure at exactly the right granularity. State FIPS = 33. | JSON API | Annual (5-yr) |
| 6 | **NH GRANIT** (UNH) — granit.unh.edu | Authoritative GIS boundaries: towns, counties, school districts, legislative districts. Source for the map. | Shapefile / GeoJSON | Occasional |
| 7 | **LegiScan** — legiscan.com/NH | Bills, roll calls, legislators, sponsorships. Bulk weekly snapshots + API. | CSV / JSON | Weekly |
| 8 | **NH General Court** — gencourt.nh.gov | Town → House/Senate/floterial district mapping; member rosters | HTML | Biennial |

**Verify exact download paths before building each pipeline** — the source names and domains above are reliable, the specific file URLs shift year to year.

### Phase 2 sources (hard, defer)

- **234 town websites.** A dozen different CMSes, no APIs, and much of the substance (warrant articles, annual reports, select board minutes, budget committee recommendations) locked in scanned PDFs. This is where agentic extraction earns its keep — but only after the state-level layer is solid.
- **Assessing / property records.** Vendor-dependent (Vision, Avitar, Axis), inconsistent access.
- **County registries of deeds.** Sales data, useful for real market values.

---

## 6. Deliberately out of scope for now

- **Social media chatter.** X's API is ~$200/mo for a still-restrictive tier. Facebook is effectively closed to automation — and Facebook groups are where the majority of actual NH town-level chatter lives. The piece most wanted is the piece least obtainable. Reddit (r/newhampshire + town subs) is workable within free-tier limits if this gets revisited.
- **News aggregation beyond RSS.** Headline + link + one-line summary only, from InDepthNH, NH Bulletin, Union Leader, Concord Monitor, and local weeklies. Never full text — that's both a copyright problem and a bad relationship with the newsrooms whose survival the project depends on.
- **UGC / events calendar.** Requires moderation, which requires time. Revisit only if the static data layer draws a real audience.
- **Accounts, auth, comments.**

---

## 7. Flagship feature: the town comparison tool

The single highest-value thing to build, and the direct answer to "where should I live in NH":

- Filterable, sortable table of all 234 municipalities
- **Equalized** tax rate, not advertised rate, with the 4-way component split visible
- Per-pupil spending and enrollment trend
- Presidential/gubernatorial margin over the last 4–6 cycles (as a proxy for town political character)
- Population and median home value trend
- Government form (town meeting / SB 2 / council)
- Commute distance to Manchester, Nashua, Concord, Portsmouth, Boston

The equalization correction is the moat. Everyone else publishes the misleading raw number.

---

## 8. Proposed stack

- **Ingest:** Python. One module per source, each producing a normalized staging table. Idempotent, re-runnable, with source URL + retrieval timestamp + data vintage recorded per row.
- **Storage:** PostgreSQL + PostGIS. (Existing Postgres-on-EC2 setup is fine to extend.)
- **Agentic layer (phase 2):** Claude API for PDF/annual-report extraction with structured output + a verification pass. This is the part worth building carefully — it's the reusable piece.
- **Site:** static generation. 234 town pages + 10 county pages + comparison tool + map. Astro, Eleventy, or plain Jinja2 — whatever's least friction.
- **Map:** TopoJSON derived from GRANIT boundaries. A pre-rendered SVG choropleth with a click handler is sufficient and far simpler than a tile-based map. Don't reach for MapLibre/Leaflet until there's a reason.

---

## 9. Roadmap

**Phase 0 — Spike (first sitting)**
- Pull DRA tax rates + equalization ratios for one recent year
- Compute equalized rates for all 234 municipalities
- Sanity-check ~5 towns by hand against published figures

**Phase 1 — MVP**
- Sources 1–6 ingested into Postgres
- Canonical municipality table with stable IDs (use Census GEOIDs as the anchor)
- 234 static town pages + 10 county pages
- Clickable state map
- Comparison table

**Phase 2**
- Legislative layer: town → districts (incl. floterials) → current members, linking out to Citizens Count for voting records rather than duplicating
- RSS news aggregation
- Historical time series (tax rates and election results back 20+ years)

**Phase 3**
- Agentic extraction from town annual reports and warrant articles
- Town meeting / select board calendar aggregation
- Whatever the audience actually asks for

---

## 10. Open questions

- Canonical municipality ID: Census GEOID, or DRA's own municipal codes? (Leaning GEOID — stable and joins to Census cleanly — with a crosswalk table to DRA, DOE, and SOS identifiers.)
- How to model floterial districts so a town page can correctly list *all* the reps a resident can vote for.
- How far back is it worth going on historical series? Election results are available much further back than clean fiscal data.
- Wards: model as first-class geographic entities, or as attributes of the 12 cities?
- Hosting and domain. Static hosting is nearly free; the question is naming.

---

## 11. Working notes for Claude in this project

- Author is a 20+ year software engineer: Python, PHP, C#/ASP.NET, SQL Server/MySQL/Postgres, AWS, iPaaS and data integration work. **Skip beginner explanation.** Talk architecture and tradeoffs, not syntax.
- Prefer concrete artifacts — schemas, working ingest scripts, actual parsed data — over plans and outlines.
- Push back on scope creep. This project's main failure mode is trying to do the news, social, and UGC layers before the boring municipal data layer is complete and correct.
- Flag any source URL or data structure that needs verification rather than asserting it confidently.
- Political neutrality is a design constraint, not a disclaimer. Present electoral data as data.
