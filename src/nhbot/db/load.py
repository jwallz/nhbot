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
        conn.commit()
        print(f"municipalities loaded:        {n_muni}")
        print(f"official equalized rows:      {off_eq}  (unmatched names skipped: {off_miss})")
        print(f"2025 estimate: tax_rate={est_rate}  equalized={est_eq}  (non-muni skipped: {est_skip})")
        print("load committed.")
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
