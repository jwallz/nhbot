#!/usr/bin/env python3
"""
NHbot -- load the Phase 0 CSVs into the canonical schema (schema.sql).

Idempotent: every insert is an upsert on the natural key, so re-running
reconciles rather than duplicates. Provenance is recorded per load in
nh.source_load and referenced by every fact row.

Loads:
  phase0/nh_municipality_geoid_crosswalk.csv  -> county, municipality, municipality_alias, village_district(Penacook)
  phase0/nh_equalized_rates_official.csv       -> equalized_rate (official, 2019-2024) + equalization_ratio
  phase0/nh_2025_equalized_rates.csv           -> equalized_rate (estimate, 2025) + tax_rate + equalization_ratio

Connection: set NHBOT_DSN, e.g.
  export NHBOT_DSN="host=/tmp port=5433 dbname=nhbot user=nhbot"
  python3 phase0/load.py

Dependencies: psycopg2 (or psycopg2-binary)
"""
import os, csv, psycopg2, psycopg2.extras

from nhbot.config import PROCESSED_DIR, DSN
P = str(PROCESSED_DIR)

NH_COUNTIES = {"001":"Belknap","003":"Carroll","005":"Cheshire","007":"Coos",
               "009":"Grafton","011":"Hillsborough","013":"Merrimack",
               "015":"Rockingham","017":"Strafford","019":"Sullivan"}

def rows(name):
    with open(os.path.join(P, name)) as f:
        return list(csv.DictReader(f))

def num(v):
    if v is None or v == "" or v == "N/A":
        return None
    try:
        return float(v)
    except ValueError:
        return None

def source_load(cur, source_name, url, file_name, vintage, retrieved):
    cur.execute("""
        INSERT INTO nh.source_load(source_name, source_url, file_name, data_vintage, retrieved_at)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (source_name, file_name, data_vintage)
        DO UPDATE SET source_url=EXCLUDED.source_url, retrieved_at=EXCLUDED.retrieved_at,
                      loaded_at=now()
        RETURNING load_id
    """, (source_name, url, file_name, vintage, retrieved))
    return cur.fetchone()[0]

def has_column(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema='nh' AND table_name=%s AND column_name=%s""", (table, col))
    return cur.fetchone() is not None

def load_geography(cur):
    xw = rows("nh_municipality_geoid_crosswalk.csv")
    postgis = has_column(cur, "municipality", "centroid")   # geometry schema vs lat/lon schema
    # counties
    for fips, nm in NH_COUNTIES.items():
        cur.execute("""INSERT INTO nh.county(county_fips,name) VALUES (%s,%s)
                       ON CONFLICT (county_fips) DO UPDATE SET name=EXCLUDED.name""", (fips, nm))
    # municipalities
    for r in xw:
        lat, lon = num(r["intptlat"]), num(r["intptlon"])
        common = (r["geoid"], r["municipality"], r["entity_type"], r["county_fips"],
                  r["cousub_fips"], r["ansicode"] or None, r["census_name"],
                  num(r["aland_sqmi"]), num(r["awater_sqmi"]))
        if postgis:
            cur.execute("""
                INSERT INTO nh.municipality
                  (geoid,name,entity_type,county_fips,cousub_fips,ansicode,census_name,
                   aland_sqmi,awater_sqmi,centroid)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        CASE WHEN %s IS NULL OR %s IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_MakePoint(%s,%s),4326) END)
                ON CONFLICT (geoid) DO UPDATE SET
                  name=EXCLUDED.name, entity_type=EXCLUDED.entity_type,
                  county_fips=EXCLUDED.county_fips, cousub_fips=EXCLUDED.cousub_fips,
                  ansicode=EXCLUDED.ansicode, census_name=EXCLUDED.census_name,
                  aland_sqmi=EXCLUDED.aland_sqmi, awater_sqmi=EXCLUDED.awater_sqmi,
                  centroid=EXCLUDED.centroid
            """, common + (lon, lat, lon, lat))
        else:
            cur.execute("""
                INSERT INTO nh.municipality
                  (geoid,name,entity_type,county_fips,cousub_fips,ansicode,census_name,
                   aland_sqmi,awater_sqmi,lat,lon)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (geoid) DO UPDATE SET
                  name=EXCLUDED.name, entity_type=EXCLUDED.entity_type,
                  county_fips=EXCLUDED.county_fips, cousub_fips=EXCLUDED.cousub_fips,
                  ansicode=EXCLUDED.ansicode, census_name=EXCLUDED.census_name,
                  aland_sqmi=EXCLUDED.aland_sqmi, awater_sqmi=EXCLUDED.awater_sqmi,
                  lat=EXCLUDED.lat, lon=EXCLUDED.lon
            """, common + (lat, lon))
        # census alias
        cur.execute("""INSERT INTO nh.municipality_alias(geoid,source,alias_name)
                       VALUES (%s,'census',%s)
                       ON CONFLICT (geoid,source,alias_name) DO NOTHING""",
                    (r["geoid"], r["census_name"]))
    # Penacook: village district in Concord (host GEOID looked up by name)
    cur.execute("SELECT geoid FROM nh.municipality WHERE name='Concord'")
    concord = cur.fetchone()
    if concord:
        cur.execute("""INSERT INTO nh.village_district(name,host_geoid) VALUES ('Penacook',%s)
                       ON CONFLICT (name) DO UPDATE SET host_geoid=EXCLUDED.host_geoid""",
                    (concord[0],))
    return len(xw)

def geoid_by_name(cur):
    cur.execute("SELECT name, geoid FROM nh.municipality")
    return {n: g for n, g in cur.fetchall()}

def load_official(cur, g):
    data = rows("nh_equalized_rates_official.csv")
    n_eq = n_missing = 0
    loads = {}
    for r in data:
        geoid = g.get(r["municipality"])
        if not geoid:
            n_missing += 1; continue
        yr = int(r["vintage"])
        lid = loads.get(yr) or source_load(
            cur, "DRA Comparison of Full Value Tax Rates",
            None, r["source"], yr, None)
        loads[yr] = lid
        cur.execute("""
            INSERT INTO nh.equalized_rate
              (geoid,tax_year,full_value_rate,equalized_valuation,dra_rank,is_official,method,load_id)
            VALUES (%s,%s,%s,%s,%s,true,'DRA official',%s)
            ON CONFLICT (geoid,tax_year) DO UPDATE SET
              full_value_rate=EXCLUDED.full_value_rate,
              equalized_valuation=EXCLUDED.equalized_valuation,
              dra_rank=EXCLUDED.dra_rank, is_official=true,
              method='DRA official', load_id=EXCLUDED.load_id
        """, (geoid, yr, num(r["full_value_rate_official"]),
              num(r["equalized_valuation_incl_util_rr"]),
              int(r["rank"]) if r["rank"].isdigit() else None, lid))
        n_eq += 1
        # equalization ratio from the same source
        if num(r["equalization_ratio"]) is not None:
            cur.execute("""
                INSERT INTO nh.equalization_ratio(geoid,tax_year,ratio_pct,load_id)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (geoid,tax_year) DO UPDATE SET
                  ratio_pct=EXCLUDED.ratio_pct, load_id=EXCLUDED.load_id
            """, (geoid, yr, num(r["equalization_ratio"]), lid))
        # advertised total rate (the "Local Tax Rate" column) -> tax_rate.
        # Historical years have the total only; the 4-way split exists for 2025.
        if num(r["local_total_rate"]) is not None:
            cur.execute("""
                INSERT INTO nh.tax_rate(geoid,tax_year,total_rate,load_id)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (geoid,tax_year) DO UPDATE SET
                  total_rate=EXCLUDED.total_rate, load_id=EXCLUDED.load_id
            """, (geoid, yr, num(r["local_total_rate"]), lid))
    return n_eq, n_missing

def load_estimate(cur, g):
    data = rows("nh_2025_equalized_rates.csv")
    lid = source_load(cur, "DRA 2025 tax rates + equalization ratio (NHbot estimate)",
                      None, "2025 DRA workbooks", 2025, "2026-08-21")
    n_rate = n_eq = n_skip = 0
    for r in data:
        geoid = g.get(r["municipality"])
        if not geoid:      # Penacook / non-municipality rows
            n_skip += 1; continue
        yr = int(r["vintage"])
        cur.execute("""
            INSERT INTO nh.tax_rate
              (geoid,tax_year,municipal_rate,county_rate,local_ed_rate,state_ed_rate,
               total_rate,total_commitment,valuation,valuation_incl_util,load_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (geoid,tax_year) DO UPDATE SET
              municipal_rate=EXCLUDED.municipal_rate, county_rate=EXCLUDED.county_rate,
              local_ed_rate=EXCLUDED.local_ed_rate, state_ed_rate=EXCLUDED.state_ed_rate,
              total_rate=EXCLUDED.total_rate, total_commitment=EXCLUDED.total_commitment,
              valuation=EXCLUDED.valuation, valuation_incl_util=EXCLUDED.valuation_incl_util,
              load_id=EXCLUDED.load_id
        """, (geoid, yr, num(r["municipal_rate"]), num(r["county_rate"]),
              num(r["local_ed_rate"]), num(r["state_ed_rate"]), num(r["total_rate"]),
              num(r["net_tax_commitment"]), num(r["net_assessed_valuation"]),
              None, lid))
        n_rate += 1
        cur.execute("""
            INSERT INTO nh.equalized_rate
              (geoid,tax_year,full_value_rate,is_official,method,load_id)
            VALUES (%s,%s,%s,false,%s,%s)
            ON CONFLICT (geoid,tax_year) DO UPDATE SET
              full_value_rate=EXCLUDED.full_value_rate, is_official=false,
              method=EXCLUDED.method, load_id=EXCLUDED.load_id
        """, (geoid, yr, num(r["equalized_rate_estimate"]),
              r.get("estimate_method") or "total_rate * ratio (estimate)", lid))
        n_eq += 1
        if num(r["equalization_ratio_pct"]) is not None:
            cur.execute("""
                INSERT INTO nh.equalization_ratio(geoid,tax_year,ratio_pct,load_id)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (geoid,tax_year) DO UPDATE SET
                  ratio_pct=EXCLUDED.ratio_pct, load_id=EXCLUDED.load_id
            """, (geoid, yr, num(r["equalization_ratio_pct"]), lid))
    return n_rate, n_eq, n_skip

def load_schools(cur):
    """DOE schools layer: sau -> school_district -> town_district, plus
    district_finance (cost per pupil) and district_enrollment. Tables only
    exist when the DOE schema block was applied; skip gracefully if not."""
    if not has_column(cur, "school_district", "district_id"):
        print("  (skools tables absent -- run schema with the DOE block; skipping)")
        return None
    lid = source_load(cur, "NH DOE schools (structure, cost-per-pupil, enrollment)",
                      None, "DOE district-town + cost-per-pupil-fy2025 + stud-ratio21-22",
                      2025, None)
    # SAUs first (school_district.sau_id references them)
    for r in rows("nh_school_sau.csv"):
        if not r["sau_id"]:
            continue
        cur.execute("""INSERT INTO nh.sau(sau_id,name) VALUES (%s,%s)
                       ON CONFLICT (sau_id) DO UPDATE SET name=EXCLUDED.name""",
                    (int(r["sau_id"]), r["sau"]))
    # district -> sau_id, recovered from the structure table (district CSV has no SAU)
    struct = rows("nh_school_structure.csv")
    dist_sau = {}
    for r in struct:
        if r["sau_id"]:
            dist_sau.setdefault(int(r["district_id"]), int(r["sau_id"]))
    # districts (before town_district, which FKs to them)
    valid = set()
    for r in rows("nh_school_district.csv"):
        did = int(r["district_id"])
        cur.execute("""INSERT INTO nh.school_district(district_id,name,sau_id) VALUES (%s,%s,%s)
                       ON CONFLICT (district_id) DO UPDATE SET
                         name=EXCLUDED.name, sau_id=EXCLUDED.sau_id""",
                    (did, r["district"], dist_sau.get(did)))
        valid.add(did)
    # town -> district links (many-to-many, with grade span)
    n_link = 0
    for r in struct:
        cur.execute("""INSERT INTO nh.town_district(geoid,district_id,grade_span)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (geoid,district_id) DO UPDATE SET
                         grade_span=EXCLUDED.grade_span""",
                    (r["geoid"], int(r["district_id"]), r["grade_span"] or None))
        n_link += 1
    # cost per pupil
    n_fin = fin_skip = 0
    for r in rows("nh_district_finance.csv"):
        did = int(r["district_id"])
        if did not in valid:
            fin_skip += 1; continue
        cur.execute("""INSERT INTO nh.district_finance
              (district_id,year,cpp_elementary,cpp_middle,cpp_high,cpp_total,load_id)
              VALUES (%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (district_id,year) DO UPDATE SET
                cpp_elementary=EXCLUDED.cpp_elementary, cpp_middle=EXCLUDED.cpp_middle,
                cpp_high=EXCLUDED.cpp_high, cpp_total=EXCLUDED.cpp_total,
                load_id=EXCLUDED.load_id""",
            (did, int(r["year"]), num(r["cpp_elementary"]), num(r["cpp_middle"]),
             num(r["cpp_high"]), num(r["cpp_total"]), lid))
        n_fin += 1
    # enrollment / staffing
    n_enr = enr_skip = 0
    for r in rows("nh_district_enrollment.csv"):
        did = int(r["district_id"])
        if did not in valid:
            enr_skip += 1; continue
        cur.execute("""INSERT INTO nh.district_enrollment
              (district_id,year,enrollment,teacher_fte,student_teacher_ratio,load_id)
              VALUES (%s,%s,%s,%s,%s,%s)
              ON CONFLICT (district_id,year) DO UPDATE SET
                enrollment=EXCLUDED.enrollment, teacher_fte=EXCLUDED.teacher_fte,
                student_teacher_ratio=EXCLUDED.student_teacher_ratio,
                load_id=EXCLUDED.load_id""",
            (did, int(r["year"]), num(r["enrollment"]), num(r["teacher_fte"]),
             num(r["student_teacher_ratio"]), lid))
        n_enr += 1
    return {"districts": len(valid), "links": n_link,
            "finance": n_fin, "fin_skip": fin_skip,
            "enrollment": n_enr, "enr_skip": enr_skip}

def load_finance(cur):
    """DOE-25 District Profile: expenditure-by-function + revenue-by-source.
    Skips gracefully if the tables or CSVs are absent. Rows for districts not in
    school_district are skipped (FK safety)."""
    if not has_column(cur, "district_expenditure", "function_code"):
        print("  (finance tables absent -- run schema with the finance block; skipping)")
        return None
    try:
        exp = rows("nh_district_expenditure.csv")
        rev = rows("nh_district_revenue.csv")
    except FileNotFoundError:
        print("  (finance CSVs absent -- run 'nhbot doe-finance'; skipping)")
        return None
    cur.execute("SELECT district_id FROM nh.school_district")
    valid = {r[0] for r in cur.fetchall()}
    lid = source_load(cur, "NH DOE-25 Annual Financial Report (District Profile)",
                      None, "iPlatform DOE25 xlsx", 2025, None)
    n_e = e_skip = 0
    for r in exp:
        did = int(r["district_id"])
        if did not in valid:
            e_skip += 1; continue
        cur.execute("""INSERT INTO nh.district_expenditure
              (district_id,year,function_code,function_name,amount,pct,load_id)
              VALUES (%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (district_id,year,function_code) DO UPDATE SET
                function_name=EXCLUDED.function_name, amount=EXCLUDED.amount,
                pct=EXCLUDED.pct, load_id=EXCLUDED.load_id""",
            (did, int(r["year"]), r["function_code"], r["function_name"],
             num(r["amount"]), num(r["pct"]), lid))
        n_e += 1
    n_r = r_skip = 0
    for r in rev:
        did = int(r["district_id"])
        if did not in valid:
            r_skip += 1; continue
        cur.execute("""INSERT INTO nh.district_revenue
              (district_id,year,source_code,source_name,amount,pct,load_id)
              VALUES (%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (district_id,year,source_code) DO UPDATE SET
                source_name=EXCLUDED.source_name, amount=EXCLUDED.amount,
                pct=EXCLUDED.pct, load_id=EXCLUDED.load_id""",
            (did, int(r["year"]), r["source_code"], r["source_name"],
             num(r["amount"]), num(r["pct"]), lid))
        n_r += 1
    # multi-year cost-per-pupil -> district_finance (upsert cpp cols only; leaves
    # enrollment/ratio that load_schools set for its year). Enables the trend view.
    n_c = c_skip = 0
    try:
        cpp = rows("nh_district_cpp.csv")
    except FileNotFoundError:
        cpp = []
    for r in cpp:
        did = int(r["district_id"])
        if did not in valid:
            c_skip += 1; continue
        cur.execute("""INSERT INTO nh.district_finance
              (district_id,year,cpp_elementary,cpp_middle,cpp_high,cpp_total,load_id)
              VALUES (%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (district_id,year) DO UPDATE SET
                cpp_elementary=EXCLUDED.cpp_elementary, cpp_middle=EXCLUDED.cpp_middle,
                cpp_high=EXCLUDED.cpp_high, cpp_total=EXCLUDED.cpp_total""",
            (did, int(r["year"]), num(r["cpp_elementary"]), num(r["cpp_middle"]),
             num(r["cpp_high"]), num(r["cpp_total"]), lid))
        n_c += 1
    return {"exp": n_e, "exp_skip": e_skip, "rev": n_r, "rev_skip": r_skip,
            "cpp": n_c, "cpp_skip": c_skip}

def load_municipal(cur):
    """Town-side department budgets (municipal_expenditure). Skips gracefully if
    the table or CSV is absent. Rows for unknown geoids are skipped (FK safety)."""
    if not has_column(cur, "municipal_expenditure", "function_code"):
        print("  (municipal table absent -- run schema with the municipal block; skipping)")
        return None
    try:
        muni = rows("nh_municipal_expenditure.csv")
    except FileNotFoundError:
        print("  (municipal CSV absent -- run 'nhbot municipal'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    lid = source_load(cur, "NH municipal budgets (MS-232/MS-535, town annual reports)",
                      None, "town annual reports", 2025, None)
    n = skip = 0
    for r in muni:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("""INSERT INTO nh.municipal_expenditure
              (geoid,year,function_code,department,category,amount,kind,source,load_id)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (geoid,year,function_code,kind) DO UPDATE SET
                department=EXCLUDED.department, category=EXCLUDED.category,
                amount=EXCLUDED.amount, source=EXCLUDED.source, load_id=EXCLUDED.load_id""",
            (r["geoid"], int(r["year"]), r["function_code"], r["department"],
             r["category"], num(r["amount"]), r["kind"], r["source"], lid))
        n += 1
    return {"rows": n, "skip": skip, "towns": len({r["geoid"] for r in muni})}

def load_municipality_websites(cur):
    """Official municipal website per GEOID, onto the municipality spine. Adds the
    website columns if an older DB lacks them (idempotent), then upserts. Rows for
    unknown geoids are skipped (FK safety). Skips gracefully if the CSV is absent."""
    cur.execute("ALTER TABLE nh.municipality ADD COLUMN IF NOT EXISTS website text")
    cur.execute("ALTER TABLE nh.municipality ADD COLUMN IF NOT EXISTS website_source text")
    try:
        data = rows("nh_municipality_website.csv")
    except FileNotFoundError:
        print("  (website CSV absent -- run 'nhbot municipal-websites'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    n = skip = 0
    for r in data:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("""UPDATE nh.municipality
                       SET website=%s, website_source=%s WHERE geoid=%s""",
                    (r["website"], r["source"], r["geoid"]))
        n += 1
    return {"rows": n, "skip": skip}


def load_municipality_profile(cur):
    """Form of government, governing body, SB2 flag, year incorporated, 2020
    population onto the municipality spine. Self-migrates columns; upserts by geoid."""
    for col, typ in [("form_of_government", "text"), ("governing_body", "text"),
                     ("sb2", "boolean DEFAULT false"), ("year_incorporated", "integer"),
                     ("population_2020", "integer")]:
        cur.execute(f"ALTER TABLE nh.municipality ADD COLUMN IF NOT EXISTS {col} {typ}")
    try:
        data = rows("nh_municipality_profile.csv")
    except FileNotFoundError:
        print("  (profile CSV absent -- run 'nhbot municipal-profile'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    n = skip = 0
    for r in data:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("""UPDATE nh.municipality SET
                         form_of_government=%s, governing_body=%s, sb2=%s,
                         year_incorporated=%s, population_2020=%s
                       WHERE geoid=%s""",
                    (r["form_of_government"] or None, r["governing_body"] or None,
                     r["sb2"] == "true",
                     int(r["year_incorporated"]) if r["year_incorporated"] else None,
                     int(r["population_2020"]) if r["population_2020"] else None,
                     r["geoid"]))
        n += 1
    return {"rows": n, "skip": skip}


def load_town_history(cur):
    """Short town-history snippet + source URL onto the municipality spine.
    Self-migrates columns; upserts by geoid. Also upgrades sb2=true where the
    history capture confirmed a town is SB2 (never downgrades)."""
    cur.execute("ALTER TABLE nh.municipality ADD COLUMN IF NOT EXISTS history text")
    cur.execute("ALTER TABLE nh.municipality ADD COLUMN IF NOT EXISTS history_source text")
    try:
        data = rows("nh_town_history.csv")
    except FileNotFoundError:
        print("  (history CSV absent -- run 'nhbot town-history'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    n = skip = sb2 = 0
    for r in data:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("""UPDATE nh.municipality
                       SET history=%s, history_source=%s WHERE geoid=%s""",
                    (r["history"] or None, r["source_url"] or None, r["geoid"]))
        if str(r.get("sb2", "")).lower() == "true":
            cur.execute("UPDATE nh.municipality SET sb2=true WHERE geoid=%s", (r["geoid"],))
            sb2 += 1
        n += 1
    return {"rows": n, "skip": skip, "sb2_upgraded": sb2}


def load_municipal_coverage(cur):
    """MS-535 / town-budget coverage flag onto the municipality spine. Self-migrates
    columns; upserts by geoid. Towns absent from the CSV are left at their default
    ('missing')."""
    for col, typ in [("ms535_status", "text"), ("ms535_kind", "text"),
                     ("ms535_year", "integer"), ("ms535_source", "text")]:
        cur.execute(f"ALTER TABLE nh.municipality ADD COLUMN IF NOT EXISTS {col} {typ}")
    try:
        data = rows("nh_municipal_coverage.csv")
    except FileNotFoundError:
        print("  (coverage CSV absent -- run 'nhbot municipal-coverage'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    n = skip = 0
    from collections import Counter
    seen = Counter()
    for r in data:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("""UPDATE nh.municipality SET
                         ms535_status=%s, ms535_kind=%s, ms535_year=%s, ms535_source=%s
                       WHERE geoid=%s""",
                    (r["ms535_status"] or "missing", r["ms535_kind"] or None,
                     int(r["ms535_year"]) if r["ms535_year"] else None,
                     r["ms535_source"] or None, r["geoid"]))
        seen[r["ms535_status"] or "missing"] += 1
        n += 1
    return {"rows": n, "skip": skip, "by_status": dict(seen)}


def load_legislature(cur):
    """Legislators (House + Senate) + town→district mappings. Self-creates its tables,
    then truncates and reloads. Skips gracefully if the CSVs aren't built yet."""
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.legislator(
        id integer PRIMARY KEY, body text NOT NULL, county text, district integer,
        first_name text, last_name text, party text, town_residence text,
        title text, email text, phone text, elected_status text)""")
    cur.execute("CREATE INDEX IF NOT EXISTS legislator_house_idx ON nh.legislator(body,county,district)")
    cur.execute("CREATE INDEX IF NOT EXISTS legislator_senate_idx ON nh.legislator(body,district)")
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.town_house_district(
        geoid char(10) NOT NULL, county text NOT NULL, district integer NOT NULL,
        PRIMARY KEY(geoid,county,district))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.town_senate_district(
        geoid char(10) NOT NULL, senate_district integer NOT NULL,
        PRIMARY KEY(geoid,senate_district))""")
    try:
        legs = rows("nh_legislators.csv")
        hd = rows("nh_town_house_district.csv")
        sd = rows("nh_town_senate_district.csv")
    except FileNotFoundError:
        print("  (legislature CSVs absent -- run 'nhbot legislature'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    cur.execute("TRUNCATE nh.legislator, nh.town_house_district, nh.town_senate_district")
    for r in legs:
        cur.execute("""INSERT INTO nh.legislator
            (id,body,county,district,first_name,last_name,party,town_residence,title,email,phone,elected_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (int(r["id"]), r["body"], r["county"] or None,
             int(r["district"]) if r["district"] else None,
             r["first_name"], r["last_name"], r["party"], r["town_residence"],
             r["title"], r["email"], r["phone"], r["elected_status"]))
    hn = sn = skip = 0
    for r in hd:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("INSERT INTO nh.town_house_district(geoid,county,district) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (r["geoid"], r["county"], int(r["district"])))
        hn += 1
    for r in sd:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("INSERT INTO nh.town_senate_district(geoid,senate_district) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                    (r["geoid"], int(r["senate_district"])))
        sn += 1
    return {"legislators": len(legs), "house_rows": hn, "senate_rows": sn, "skip": skip}


def load_select_board(cur):
    """Municipal governing-board members (select board / town council / aldermen).
    Self-creates its table, truncates and reloads. Skips if the CSV isn't built."""
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.select_board(
        geoid char(10) NOT NULL, seq integer NOT NULL, role text, name text NOT NULL,
        is_chair boolean DEFAULT false, phone text, email text,
        PRIMARY KEY(geoid,seq))""")
    cur.execute("CREATE INDEX IF NOT EXISTS select_board_geoid_idx ON nh.select_board(geoid)")
    try:
        data = rows("nh_select_board.csv")
    except FileNotFoundError:
        print("  (select-board CSV absent -- run 'nhbot select-board'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    cur.execute("TRUNCATE nh.select_board")
    n = skip = 0
    for r in data:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("""INSERT INTO nh.select_board(geoid,seq,role,name,is_chair,phone,email)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (r["geoid"], int(r["seq"]), r["role"], r["name"],
                     r["is_chair"] == "true", r["phone"] or None, r["email"] or None))
        n += 1
    return {"rows": n, "skip": skip}


def load_state_budget(cur):
    """State operating-budget appropriations by department & funding sources.
    Self-creates tables, truncates and reloads. Skips if the CSVs aren't built."""
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.state_budget(
        fiscal_year integer NOT NULL, category text, department text NOT NULL,
        amount numeric, PRIMARY KEY(fiscal_year, department))""")
    cur.execute("CREATE INDEX IF NOT EXISTS state_budget_year_idx ON nh.state_budget(fiscal_year)")
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.state_funding(
        fiscal_year integer NOT NULL, source text NOT NULL, amount numeric,
        PRIMARY KEY(fiscal_year, source))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.state_federal_funds(
        fiscal_year integer NOT NULL, category text, department text NOT NULL,
        amount numeric, PRIMARY KEY(fiscal_year, department))""")
    try:
        budg = rows("nh_state_budget.csv")
        fund = rows("nh_state_funding.csv")
    except FileNotFoundError:
        print("  (state budget CSVs absent -- run 'nhbot state-fiscal'; skipping)")
        return None
    try:
        fed = rows("nh_state_federal_funds.csv")
    except FileNotFoundError:
        fed = []
    cur.execute("TRUNCATE nh.state_budget, nh.state_funding, nh.state_federal_funds")
    for r in budg:
        cur.execute("""INSERT INTO nh.state_budget(fiscal_year,category,department,amount)
                       VALUES(%s,%s,%s,%s)""",
                    (int(r["fiscal_year"]), r["category"] or None, r["department"], float(r["amount"] or 0)))
    for r in fund:
        cur.execute("INSERT INTO nh.state_funding(fiscal_year,source,amount) VALUES(%s,%s,%s)",
                    (int(r["fiscal_year"]), r["source"], float(r["amount"] or 0)))
    for r in fed:
        cur.execute("""INSERT INTO nh.state_federal_funds(fiscal_year,category,department,amount)
                       VALUES(%s,%s,%s,%s)""",
                    (int(r["fiscal_year"]), r["category"] or None, r["department"], float(r["amount"] or 0)))
    return {"budget_rows": len(budg), "funding_rows": len(fund), "federal_rows": len(fed)}


def load_state_revenue(cur):
    """State tax & fee revenue by source (General & Education funds, $ millions).
    Self-creates its table, truncates and reloads. Skips if the CSV isn't built."""
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.state_revenue(
        fiscal_year integer NOT NULL, source text NOT NULL,
        actual_musd numeric, plan_musd numeric,
        PRIMARY KEY(fiscal_year, source))""")
    try:
        data = rows("nh_state_revenue.csv")
    except FileNotFoundError:
        print("  (state revenue CSV absent -- run 'nhbot state-fiscal'; skipping)")
        return None
    cur.execute("TRUNCATE nh.state_revenue")
    for r in data:
        cur.execute("""INSERT INTO nh.state_revenue(fiscal_year,source,actual_musd,plan_musd)
                       VALUES(%s,%s,%s,%s)""",
                    (int(r["fiscal_year"]), r["source"],
                     float(r["actual_musd"]) if r["actual_musd"] not in (None, "") else None,
                     float(r["plan_musd"]) if r["plan_musd"] not in (None, "") else None))
    return {"rows": len(data)}


def load_state_comparison(cur):
    """National state-by-state tax comparison. Self-creates its table, truncates
    and reloads. Skips if the CSV isn't built."""
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.state_tax_comparison(
        state text PRIMARY KEY, burden_pct numeric, burden_rank integer,
        collections_percap integer, collections_rank integer,
        prop_pct numeric, sales_pct numeric, individual_income_pct numeric,
        corporate_income_pct numeric, other_pct numeric,
        eff_property_rate numeric, eff_property_rank integer,
        hh_property_pc integer, hh_income_pc integer, hh_sales_pc integer,
        hh_excise_pc integer, hh_income_percap integer, hh_persons_per_household numeric,
        hh_burden_pct numeric, hh_burden_rank integer)""")
    # migrate existing installs (CREATE TABLE IF NOT EXISTS won't add the household columns)
    for col, typ in [("hh_property_pc", "integer"), ("hh_income_pc", "integer"),
                     ("hh_sales_pc", "integer"), ("hh_excise_pc", "integer"),
                     ("hh_income_percap", "integer"), ("hh_persons_per_household", "numeric"),
                     ("hh_burden_pct", "numeric"), ("hh_burden_rank", "integer")]:
        cur.execute(f"ALTER TABLE nh.state_tax_comparison ADD COLUMN IF NOT EXISTS {col} {typ}")
    try:
        data = rows("nh_state_comparison.csv")
    except FileNotFoundError:
        print("  (state comparison CSV absent -- run 'nhbot tax-comparison'; skipping)")
        return None
    def num(v):  return float(v) if v not in (None, "") else None
    def ival(v): return int(v) if v not in (None, "") else None
    cur.execute("TRUNCATE nh.state_tax_comparison")
    for r in data:
        cur.execute("""INSERT INTO nh.state_tax_comparison(state,burden_pct,burden_rank,
            collections_percap,collections_rank,prop_pct,sales_pct,individual_income_pct,
            corporate_income_pct,other_pct,eff_property_rate,eff_property_rank,
            hh_property_pc,hh_income_pc,hh_sales_pc,hh_excise_pc,hh_income_percap,
            hh_persons_per_household,hh_burden_pct,hh_burden_rank)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (r["state"], num(r["burden_pct"]), ival(r["burden_rank"]),
             ival(r["collections_percap"]), ival(r["collections_rank"]),
             num(r["prop_pct"]), num(r["sales_pct"]), num(r["individual_income_pct"]),
             num(r["corporate_income_pct"]), num(r["other_pct"]),
             num(r["eff_property_rate"]), ival(r["eff_property_rank"]),
             ival(r["hh_property_pc"]), ival(r["hh_income_pc"]), ival(r["hh_sales_pc"]),
             ival(r["hh_excise_pc"]), ival(r["hh_income_percap"]),
             num(r["hh_persons_per_household"]), num(r["hh_burden_pct"]), ival(r["hh_burden_rank"])))
    return {"rows": len(data)}


def load_valuation(cur):
    """Town tax-base composition (valuation by property class). Self-creates its
    table, truncates and reloads. Skips if the CSV isn't built."""
    cur.execute("""CREATE TABLE IF NOT EXISTS nh.valuation_class(
        geoid char(10) PRIMARY KEY, year integer, residential numeric,
        commercial_industrial numeric, utilities numeric, other numeric, gross numeric)""")
    try:
        data = rows("nh_valuation_class.csv")
    except FileNotFoundError:
        print("  (valuation CSV absent -- run 'nhbot valuation'; skipping)")
        return None
    cur.execute("SELECT geoid FROM nh.municipality")
    valid = {r[0] for r in cur.fetchall()}
    cur.execute("TRUNCATE nh.valuation_class")
    n = skip = 0
    for r in data:
        if r["geoid"] not in valid:
            skip += 1; continue
        cur.execute("""INSERT INTO nh.valuation_class
            (geoid,year,residential,commercial_industrial,utilities,other,gross)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (geoid) DO UPDATE SET
            year=EXCLUDED.year, residential=EXCLUDED.residential,
            commercial_industrial=EXCLUDED.commercial_industrial,
            utilities=EXCLUDED.utilities, other=EXCLUDED.other, gross=EXCLUDED.gross""",
            (r["geoid"], int(r["year"]) if r["year"] else None,
             float(r["residential"]), float(r["commercial_industrial"]),
             float(r["utilities"]), float(r["other"]), float(r["gross"])))
        n += 1
    return {"rows": n, "skip": skip}


def main():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path = nh, public")
            n_muni = load_geography(cur)
            g = geoid_by_name(cur)
            off_eq, off_miss = load_official(cur, g)
            est_rate, est_eq, est_skip = load_estimate(cur, g)
            schools = load_schools(cur)
            finance = load_finance(cur)
            municipal = load_municipal(cur)
            websites = load_municipality_websites(cur)
            profile = load_municipality_profile(cur)
            history = load_town_history(cur)
            coverage = load_municipal_coverage(cur)
            legislature = load_legislature(cur)
            select_board = load_select_board(cur)
            state_budget = load_state_budget(cur)
            state_revenue = load_state_revenue(cur)
            state_comparison = load_state_comparison(cur)
            valuation = load_valuation(cur)
        conn.commit()
        print(f"municipalities loaded:        {n_muni}")
        print(f"official equalized rows:      {off_eq}  (unmatched names skipped: {off_miss})")
        print(f"2025 estimate: tax_rate={est_rate}  equalized={est_eq}  (non-muni skipped: {est_skip})")
        if schools:
            print(f"schools: {schools['districts']} districts, {schools['links']} town links, "
                  f"finance={schools['finance']} (skip {schools['fin_skip']}), "
                  f"enrollment={schools['enrollment']} (skip {schools['enr_skip']})")
        if finance:
            print(f"DOE-25 finance: expenditure={finance['exp']} (skip {finance['exp_skip']}), "
                  f"revenue={finance['rev']} (skip {finance['rev_skip']}), "
                  f"cpp/year={finance['cpp']} (skip {finance['cpp_skip']})")
        if municipal:
            print(f"municipal: {municipal['rows']} rows, {municipal['towns']} town(s) (skip {municipal['skip']})")
        if websites:
            print(f"municipal websites: {websites['rows']} set (skip {websites['skip']})")
        if profile:
            print(f"municipal profile: {profile['rows']} set (skip {profile['skip']})")
        if history:
            print(f"town history: {history['rows']} set (skip {history['skip']}, "
                  f"sb2 upgraded {history['sb2_upgraded']})")
        if coverage:
            print(f"municipal coverage: {coverage['rows']} set (skip {coverage['skip']}) "
                  f"{coverage['by_status']}")
        if legislature:
            print(f"legislature: {legislature['legislators']} legislators, "
                  f"{legislature['house_rows']} house + {legislature['senate_rows']} senate town-links")
        if select_board:
            print(f"select boards: {select_board['rows']} members set (skip {select_board['skip']})")
        if state_budget:
            print(f"state budget: {state_budget['budget_rows']} dept-year rows, "
                  f"{state_budget['funding_rows']} funding-source rows")
        if state_revenue:
            print(f"state revenue: {state_revenue['rows']} source-year rows")
        if state_comparison:
            print(f"state comparison: {state_comparison['rows']} states")
        if valuation:
            print(f"valuation (tax base): {valuation['rows']} towns set (skip {valuation['skip']})")
        print("load committed.")
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
