"""Query layer — plain functions returning dicts. Read-only views over the
canonical `nh` schema. Everything joins on GEOID.

Two rates matter and both are surfaced:
  * advertised  — the rate on the tax bill (tax_rate.total_rate)
  * equalized   — DRA full-value rate for cross-town comparison (equalized_rate)
"""
from nhbot.web.db import query, query_one

METRICS = {"advertised", "equalized"}

# Sort columns whitelisted to keep the compare endpoint injection-safe.
COMPARE_SORTS = {
    "name":       "m.name",
    "county":     "c.name",
    "equalized":  "e.full_value_rate",
    "advertised": "t.total_rate",
    "ratio":      "r.ratio_pct",
}

def latest_official_year():
    row = query_one("SELECT max(tax_year) AS y FROM equalized_rate WHERE is_official")
    return row["y"] if row and row["y"] else None

def latest_year():
    row = query_one("SELECT max(tax_year) AS y FROM tax_rate")
    return row["y"] if row and row["y"] else None

def available_years():
    return [r["tax_year"] for r in query(
        "SELECT DISTINCT tax_year FROM equalized_rate ORDER BY tax_year DESC")]

def counties():
    return query("SELECT county_fips, name FROM county ORDER BY name")

def map_values(year, metric="advertised"):
    """geoid -> chosen rate for a year."""
    if metric == "advertised":
        rows = query("""SELECT geoid, total_rate AS v FROM tax_rate
                        WHERE tax_year = %s AND total_rate > 0""", (year,))
    else:
        rows = query("""SELECT DISTINCT ON (geoid) geoid, full_value_rate AS v
                        FROM equalized_rate
                        WHERE tax_year = %s AND full_value_rate > 0
                        ORDER BY geoid, is_official DESC""", (year,))
    return {r["geoid"]: float(r["v"]) for r in rows}

def get_municipality(geoid):
    return query_one("""
        SELECT m.geoid, m.name, m.entity_type, m.census_name,
               m.aland_sqmi, c.name AS county
        FROM municipality m JOIN county c USING (county_fips)
        WHERE m.geoid = %s
    """, (geoid,))

def rate_history(geoid):
    """One row per year with advertised, equalized, ratio, official flag."""
    return query("""
        SELECT y.tax_year,
               t.total_rate      AS advertised,
               e.full_value_rate AS equalized,
               e.is_official, e.dra_rank,
               r.ratio_pct
        FROM (
            SELECT tax_year FROM equalized_rate WHERE geoid = %(g)s
            UNION SELECT tax_year FROM tax_rate WHERE geoid = %(g)s
        ) y
        LEFT JOIN equalized_rate e ON e.geoid = %(g)s AND e.tax_year = y.tax_year
        LEFT JOIN tax_rate t       ON t.geoid = %(g)s AND t.tax_year = y.tax_year
        LEFT JOIN equalization_ratio r ON r.geoid = %(g)s AND r.tax_year = y.tax_year
        ORDER BY y.tax_year DESC
    """, {"g": geoid})

def tax_split(geoid, year):
    """The 4-way advertised split for a year (available for 2025)."""
    return query_one("""
        SELECT tax_year, municipal_rate, county_rate, local_ed_rate,
               state_ed_rate, total_rate
        FROM tax_rate
        WHERE geoid = %s AND tax_year = %s AND municipal_rate IS NOT NULL
    """, (geoid, year))

def compare_rows(year, county_fips=None, entity_type=None,
                 sort="advertised", direction="desc"):
    col = COMPARE_SORTS.get(sort, "t.total_rate")
    direction = "ASC" if str(direction).lower() == "asc" else "DESC"
    where = ["m.entity_type IN ('city','town')"]
    params = {"year": year}
    if county_fips:
        where.append("m.county_fips = %(county)s"); params["county"] = county_fips
    if entity_type in ("city", "town"):
        where.append("m.entity_type = %(entity)s"); params["entity"] = entity_type
    sql = f"""
        SELECT m.geoid, m.name, m.entity_type, c.name AS county,
               t.total_rate AS advertised,
               e.full_value_rate AS equalized, e.is_official, e.dra_rank,
               r.ratio_pct AS ratio
        FROM municipality m
        JOIN county c USING (county_fips)
        LEFT JOIN LATERAL (
            SELECT full_value_rate, is_official, dra_rank
            FROM equalized_rate er
            WHERE er.geoid = m.geoid AND er.tax_year = %(year)s
            ORDER BY is_official DESC LIMIT 1
        ) e ON true
        LEFT JOIN tax_rate t ON t.geoid = m.geoid AND t.tax_year = %(year)s
        LEFT JOIN equalization_ratio r ON r.geoid = m.geoid AND r.tax_year = %(year)s
        WHERE {" AND ".join(where)}
        ORDER BY {col} {direction} NULLS LAST, m.name ASC
    """
    return query(sql, params)
