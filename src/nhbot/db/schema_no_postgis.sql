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
    lat          numeric,                          -- Census internal point (centroid); swap to PostGIS geometry when GRANIT lands
    lon          numeric,
    UNIQUE (name)
);
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
