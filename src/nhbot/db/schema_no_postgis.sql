-- =====================================================================
-- NHbot canonical schema  (PostgreSQL 14+ / PostGIS 3)
-- Anchored on the 10-digit Census GEOID for every NH municipality.
-- NO-POSTGIS variant: centroid stored as lat/lon numeric (no geometry columns).
-- Idempotent: safe to re-run. Load with load.py.
-- =====================================================================


CREATE SCHEMA IF NOT EXISTS nh;
SET search_path = nh, public;

-- ---------------------------------------------------------------------
-- Provenance: one row per (source, file, vintage) load. Facts reference it,
-- so every number is traceable to a URL + retrieval date + data vintage.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_load (
    load_id       serial PRIMARY KEY,
    source_name   text        NOT NULL,          -- e.g. 'DRA Comparison of Full Value Tax Rates'
    source_url    text,
    file_name     text,
    data_vintage  int,                            -- tax year the data describes
    retrieved_at  date,                           -- when the file was pulled from the source
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_name, file_name, data_vintage)
);

-- ---------------------------------------------------------------------
-- Reference dimensions
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS county (
    county_fips  char(3) PRIMARY KEY,             -- 3-digit within-state FIPS
    name         text NOT NULL,
    state_fips   char(2) NOT NULL DEFAULT '33'
);

CREATE TABLE IF NOT EXISTS entity_type (
    code text PRIMARY KEY,
    description text NOT NULL
);
INSERT INTO entity_type(code, description) VALUES
    ('city',            '13 NH cities'),
    ('town',            'Incorporated town'),
    ('unincorporated',  'Unincorporated place: grant, purchase, township, or location'),
    ('village_district','Precinct/village district overlaying a host municipality')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- Canonical municipality spine (GEOID = the durable join key)
-- 259 rows: 13 cities + 221 towns + 25 unincorporated places.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS municipality (
    geoid        char(10) PRIMARY KEY,            -- state(2)+county(3)+cousub(5)
    name         text NOT NULL,                   -- canonical (DRA) name
    entity_type  text NOT NULL REFERENCES entity_type(code),
    county_fips  char(3) NOT NULL REFERENCES county(county_fips),
    cousub_fips  char(5) NOT NULL,
    ansicode     text,
    census_name  text,                            -- Census gazetteer name (differs from canonical)
    aland_sqmi   numeric,
    awater_sqmi  numeric,
    website      text,                             -- official municipal website (for the town page header)
    website_source text,                           -- 'granit' | 'verified'
    form_of_government text,                        -- Mayor–council | Council–manager | Town council | Town meeting
    governing_body    text,                         -- chief local body (Select board, Town council, ...)
    sb2               boolean DEFAULT false,        -- official-ballot (SB2, RSA 40:13) town meeting
    year_incorporated integer,
    population_2020   integer,
    history           text,                         -- short town-history snippet (for the About section)
    history_source    text,                         -- URL the snippet was drawn from
    lat          numeric,                          -- Census internal point (centroid); swap to PostGIS geometry when GRANIT lands
    lon          numeric,
    UNIQUE (name)
);
-- migrate existing installs (CREATE TABLE IF NOT EXISTS won't add new columns)
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS website text;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS website_source text;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS form_of_government text;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS governing_body text;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS sb2 boolean DEFAULT false;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS year_incorporated integer;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS population_2020 integer;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS history text;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS history_source text;
-- MS-535 / town-budget coverage flag (which towns we have a parsed budget for)
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS ms535_status text DEFAULT 'missing'; -- loaded | needs_review | missing
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS ms535_kind   text;                   -- actual (MS-535) | appropriation
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS ms535_year   integer;
ALTER TABLE municipality ADD COLUMN IF NOT EXISTS ms535_source text;
CREATE INDEX IF NOT EXISTS municipality_county_idx  ON municipality(county_fips);
CREATE INDEX IF NOT EXISTS municipality_etype_idx   ON municipality(entity_type);

-- Alternate identifiers/names from other agencies (DRA codes, DOE SAU/district,
-- SOS names, Census variants). Lets every source join back to a GEOID.
CREATE TABLE IF NOT EXISTS municipality_alias (
    geoid       char(10) NOT NULL REFERENCES municipality(geoid),
    source      text NOT NULL,                    -- 'census','dra','doe','sos'
    alias_name  text,
    alias_code  text,
    PRIMARY KEY (geoid, source, alias_name)
);

-- Village districts overlay a host municipality (e.g. Penacook in Concord).
-- They have no GEOID of their own; they reference their host.
CREATE TABLE IF NOT EXISTS village_district (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    host_geoid  char(10) REFERENCES municipality(geoid),
    UNIQUE (name)
);

-- ---------------------------------------------------------------------
-- Facts (one row per municipality per tax year; upserted on natural key)
-- ---------------------------------------------------------------------

-- Advertised tax rate with the 4-way component split.
CREATE TABLE IF NOT EXISTS tax_rate (
    geoid            char(10) NOT NULL REFERENCES municipality(geoid),
    tax_year         int NOT NULL,
    municipal_rate   numeric,
    county_rate      numeric,
    local_ed_rate    numeric,
    state_ed_rate    numeric,
    total_rate       numeric,
    total_commitment numeric,
    valuation        numeric,          -- assessed, not including utilities
    valuation_incl_util numeric,       -- assessed, including utilities
    load_id          int REFERENCES source_load(load_id),
    PRIMARY KEY (geoid, tax_year)
);

-- Equalization ratio + assessment-quality stats.
CREATE TABLE IF NOT EXISTS equalization_ratio (
    geoid        char(10) NOT NULL REFERENCES municipality(geoid),
    tax_year     int NOT NULL,
    ratio_pct    numeric,              -- weighted-mean assessment ratio
    median_ratio numeric,
    cod          numeric,              -- coefficient of dispersion
    prd          numeric,              -- price-related differential
    load_id      int REFERENCES source_load(load_id),
    PRIMARY KEY (geoid, tax_year)
);

-- Equalized (full-value) tax rate. Holds BOTH DRA-official rows
-- (is_official = true) and current-year estimates (is_official = false).
-- The flagship number for the town comparison tool.
CREATE TABLE IF NOT EXISTS equalized_rate (
    geoid              char(10) NOT NULL REFERENCES municipality(geoid),
    tax_year           int NOT NULL,
    full_value_rate    numeric,        -- $ per $1,000 of equalized (market) value
    equalized_valuation numeric,       -- incl. utilities + equalized railroad (official rows)
    dra_rank           int,
    is_official        boolean NOT NULL,   -- true = DRA published; false = our estimate
    method             text,               -- e.g. 'DRA official' / 'total_rate * ratio (est, +0.13 bias)'
    load_id            int REFERENCES source_load(load_id),
    PRIMARY KEY (geoid, tax_year)
);
CREATE INDEX IF NOT EXISTS equalized_rate_year_idx ON equalized_rate(tax_year);

-- ---------------------------------------------------------------------
-- Convenience view: latest equalized rate per municipality, preferring an
-- official figure over an estimate when both exist for the same year.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW equalized_rate_latest AS
SELECT DISTINCT ON (e.geoid)
       e.geoid, m.name, m.entity_type, m.county_fips,
       e.tax_year, e.full_value_rate, e.is_official, e.method
FROM equalized_rate e
JOIN municipality m USING (geoid)
ORDER BY e.geoid, e.tax_year DESC, e.is_official DESC;

-- ---------------------------------------------------------------------
-- Schools (NH DOE). District-level facts; towns link via town_district.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sau (
    sau_id  int  PRIMARY KEY,
    name    text NOT NULL
);
CREATE TABLE IF NOT EXISTS school_district (
    district_id int  PRIMARY KEY,
    name        text NOT NULL,
    sau_id      int REFERENCES sau(sau_id)
);
CREATE TABLE IF NOT EXISTS town_district (
    geoid       char(10) NOT NULL REFERENCES municipality(geoid),
    district_id int      NOT NULL REFERENCES school_district(district_id),
    grade_span  text,
    PRIMARY KEY (geoid, district_id)
);
CREATE INDEX IF NOT EXISTS town_district_geoid_idx    ON town_district(geoid);
CREATE INDEX IF NOT EXISTS town_district_district_idx ON town_district(district_id);

CREATE TABLE IF NOT EXISTS district_finance (
    district_id    int NOT NULL REFERENCES school_district(district_id),
    year           int NOT NULL,
    cpp_elementary numeric, cpp_middle numeric, cpp_high numeric, cpp_total numeric,
    load_id        int REFERENCES source_load(load_id),
    PRIMARY KEY (district_id, year)
);
CREATE TABLE IF NOT EXISTS district_enrollment (
    district_id           int NOT NULL REFERENCES school_district(district_id),
    year                  int NOT NULL,
    enrollment            numeric, teacher_fte numeric, student_teacher_ratio numeric,
    load_id               int REFERENCES source_load(load_id),
    PRIMARY KEY (district_id, year)
);

-- DOE-25 finance: expenditure-by-function and revenue-by-source (District Profile rollup)
CREATE TABLE IF NOT EXISTS district_expenditure (
    district_id   int  NOT NULL REFERENCES school_district(district_id),
    year          int  NOT NULL,
    function_code text NOT NULL,
    function_name text,
    amount        numeric,
    pct           numeric,          -- DOE's own share of recurring expenditures (null = non-recurring line)
    load_id       int REFERENCES source_load(load_id),
    PRIMARY KEY (district_id, year, function_code)
);
CREATE INDEX IF NOT EXISTS district_expenditure_dy_idx ON district_expenditure(district_id, year);

CREATE TABLE IF NOT EXISTS district_revenue (
    district_id  int  NOT NULL REFERENCES school_district(district_id),
    year         int  NOT NULL,
    source_code  text NOT NULL,
    source_name  text,
    amount       numeric,
    pct          numeric,
    load_id      int REFERENCES source_load(load_id),
    PRIMARY KEY (district_id, year, source_code)
);
CREATE INDEX IF NOT EXISTS district_revenue_dy_idx ON district_revenue(district_id, year);

-- Municipal (town-side) budget by department, from MS-232/MS-535 chart of accounts.
CREATE TABLE IF NOT EXISTS municipal_expenditure (
    geoid         char(10) NOT NULL REFERENCES municipality(geoid),
    year          int  NOT NULL,
    function_code text NOT NULL,
    department    text,
    category      text,
    amount        numeric,
    kind          text,          -- 'appropriation' (voted budget) | 'actual' (MS-535)
    source        text,
    load_id       int REFERENCES source_load(load_id),
    PRIMARY KEY (geoid, year, function_code, kind)
);
CREATE INDEX IF NOT EXISTS municipal_expenditure_gy_idx ON municipal_expenditure(geoid, year);

-- ============================================================================
-- State legislators (House + Senate) and town→district mappings.
-- Roster: gc.nh.gov members.txt. Districts: RSA 662:5 (House) + 662:3 (Senate).
-- A town's reps = legislators(body=house) whose (county,district) is any of the town's
-- house districts (base + floterial). A town's senator(s) = legislators(body=senate)
-- whose district is any of the town's senate districts (split cities have several).
-- ============================================================================
CREATE TABLE IF NOT EXISTS legislator (
    id             integer PRIMARY KEY,
    body           text NOT NULL,        -- 'house' | 'senate'
    county         text,                 -- house district county (senate: residence, not a join key)
    district       integer,              -- house: district no within county; senate: 1-24
    first_name     text,
    last_name      text,
    party          text,
    town_residence text,
    title          text,
    email          text,
    phone          text,
    elected_status text                  -- 'Incumbent' | 'new' | 'Former' (Former hidden on pages)
);
CREATE INDEX IF NOT EXISTS legislator_house_idx  ON legislator(body, county, district);
CREATE INDEX IF NOT EXISTS legislator_senate_idx ON legislator(body, district);

CREATE TABLE IF NOT EXISTS town_house_district (
    geoid    char(10) NOT NULL REFERENCES municipality(geoid),
    county   text NOT NULL,
    district integer NOT NULL,
    PRIMARY KEY (geoid, county, district)
);
CREATE TABLE IF NOT EXISTS town_senate_district (
    geoid           char(10) NOT NULL REFERENCES municipality(geoid),
    senate_district integer NOT NULL,
    PRIMARY KEY (geoid, senate_district)
);

-- Municipal governing board: Select Board (towns), Town Council (council-manager),
-- or Board of Aldermen (some cities). Source: NH DOT officials directory.
CREATE TABLE IF NOT EXISTS select_board (
    geoid    char(10) NOT NULL REFERENCES municipality(geoid),
    seq      integer NOT NULL,
    role     text,                 -- Selectman | Town Councilor | Alderman
    name     text NOT NULL,
    is_chair boolean DEFAULT false,
    phone    text,
    email    text,
    PRIMARY KEY (geoid, seq)
);
CREATE INDEX IF NOT EXISTS select_board_geoid_idx ON select_board(geoid);

-- Contact-form submissions (also emailed via SMTP/SES when configured; see app.py).
CREATE TABLE IF NOT EXISTS contact_message (
    id         serial PRIMARY KEY,
    created_at timestamptz DEFAULT now(),
    name       text,
    email      text,
    category   text,
    message    text,
    ip         text,
    emailed    boolean DEFAULT false
);

-- ============================================================================
-- State of New Hampshire fiscal data (statewide; not per-municipality).
-- ============================================================================
-- Enacted operating-budget appropriations by department, per fiscal year
-- (LBA HB 1 chaptered-final Excel, TYPE=E expenditure lines).
CREATE TABLE IF NOT EXISTS state_budget (
    fiscal_year integer NOT NULL,
    category    text,
    department  text NOT NULL,
    amount      numeric,
    PRIMARY KEY (fiscal_year, department)
);
CREATE INDEX IF NOT EXISTS state_budget_year_idx ON state_budget(fiscal_year);

-- How the budget is funded, by fund/source, per fiscal year (HB 1 TYPE=F lines).
CREATE TABLE IF NOT EXISTS state_funding (
    fiscal_year integer NOT NULL,
    source      text NOT NULL,
    amount      numeric,
    PRIMARY KEY (fiscal_year, source)
);

-- The single 'Federal Funds' funding class broken down by the agency that
-- receives it (HB 1 TYPE=F lines where class = Federal Funds). Lets the /state
-- page show that federal money is mostly the federal share of Medicaid (DHHS).
CREATE TABLE IF NOT EXISTS state_federal_funds (
    fiscal_year integer NOT NULL,
    category    text,
    department  text NOT NULL,
    amount      numeric,
    PRIMARY KEY (fiscal_year, department)
);

-- State tax & fee revenue by source (General & Education funds, $ millions),
-- actual vs plan, per fiscal year (DAS Monthly Revenue Focus, June year-end).
CREATE TABLE IF NOT EXISTS state_revenue (
    fiscal_year integer NOT NULL,
    source      text NOT NULL,
    actual_musd numeric,
    plan_musd   numeric,
    PRIMARY KEY (fiscal_year, source)
);

-- National comparison: how NH's tax burden & mix compare to other states
-- (Tax Foundation "Facts & Figures"; figures from U.S. Census / BEA).
CREATE TABLE IF NOT EXISTS state_tax_comparison (
    state                 text PRIMARY KEY,
    burden_pct            numeric,   -- state-local tax burden as % of income
    burden_rank           integer,   -- 1 = lowest burden
    collections_percap    integer,   -- state & local tax collections per capita ($)
    collections_rank      integer,
    prop_pct              numeric,   -- share of collections from each source
    sales_pct             numeric,
    individual_income_pct numeric,
    corporate_income_pct  numeric,
    other_pct             numeric,
    eff_property_rate     numeric,   -- property tax as % of owner-occupied home value
    eff_property_rank     integer,
    hh_property_pc        integer,   -- household basket, per capita ($): property tax
    hh_income_pc          integer,   -- individual income tax
    hh_sales_pc           integer,   -- general sales tax
    hh_excise_pc          integer,   -- excise tax
    hh_income_percap      integer,   -- income per capita (denominator)
    hh_persons_per_household numeric, -- avg people per household (per-household $ conversion)
    hh_burden_pct         numeric,   -- household tax burden = basket / income
    hh_burden_rank        integer
);

-- Tax-base composition: a town's assessed valuation by property class
-- (NH DRA "Tables by County" / MS-1). Reconciles to DRA's gross valuation.
CREATE TABLE IF NOT EXISTS valuation_class (
    geoid                 char(10) PRIMARY KEY,
    year                  integer,
    residential           numeric,
    commercial_industrial numeric,
    utilities             numeric,
    other                 numeric,
    gross                 numeric
);
