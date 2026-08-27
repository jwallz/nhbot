"""Query layer — plain functions returning dicts. Read-only views over the
canonical `nh` schema. Everything joins on GEOID.

Two rates matter and both are surfaced:
  * advertised  — the rate on the tax bill (tax_rate.total_rate)
  * equalized   — DRA full-value rate for cross-town comparison (equalized_rate)
"""
from nhbot.web.db import query, query_one

METRICS = {"advertised", "equalized"}

# NH statewide average cost per pupil, DOE FY2025 (operating). Town-page benchmark.
STATE_CPP_TOTAL = 22699.85
STATE_CPP_YEAR = 2025

# Sort columns whitelisted to keep the compare endpoint injection-safe.
COMPARE_SORTS = {
    "name":       "m.name",
    "county":     "c.name",
    "equalized":  "e.full_value_rate",
    "advertised": "t.total_rate",
    "ratio":      "r.ratio_pct",
    "cpp":        "cpp.cpp_total",
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

def get_schools(geoid):
    """The school districts a town belongs to (many-to-many), each with its
    latest cost-per-pupil and enrollment. Ordered by grade span so K-8 shows
    before the 9-12 cooperative. Returns [] when the DOE layer isn't loaded."""
    return query("""
        SELECT td.district_id, sd.name AS district, td.grade_span,
               s.sau_id, s.name AS sau,
               f.cpp_total, f.cpp_elementary, f.cpp_middle, f.cpp_high, f.year AS cpp_year,
               en.enrollment, en.teacher_fte, en.student_teacher_ratio, en.year AS enroll_year
        FROM town_district td
        JOIN school_district sd ON sd.district_id = td.district_id
        LEFT JOIN sau s ON s.sau_id = sd.sau_id
        LEFT JOIN LATERAL (
            SELECT cpp_total, cpp_elementary, cpp_middle, cpp_high, year
            FROM district_finance df WHERE df.district_id = td.district_id
            ORDER BY year DESC LIMIT 1
        ) f ON true
        LEFT JOIN LATERAL (
            SELECT enrollment, teacher_fte, student_teacher_ratio, year
            FROM district_enrollment de WHERE de.district_id = td.district_id
            ORDER BY year DESC LIMIT 1
        ) en ON true
        WHERE td.geoid = %s
        ORDER BY (CASE
                    WHEN td.grade_span IS NULL THEN 99
                    WHEN td.grade_span ~ '^[0-9]' THEN split_part(td.grade_span,'-',1)::int
                    ELSE 0 END), sd.name
    """, (geoid,))

# Sortable columns for the school-spending grid (injection-safe whitelist).
SCHOOL_SORTS = {
    "town":       "m.name",
    "county":     "c.name",
    "district":   "sd.name",
    "cpp":        "f.cpp_total",
    "enrollment": "en.enrollment",
    "ratio":      "en.student_teacher_ratio",
}

def school_rows(county_fips=None, sort="cpp", direction="desc"):
    """One row per town-district link (a coop town appears once per district),
    each with its latest cost-per-pupil, enrollment, and student-teacher ratio.
    This is the same shape as the town-page Schools box, for every town."""
    col = SCHOOL_SORTS.get(sort, "f.cpp_total")
    direction = "ASC" if str(direction).lower() == "asc" else "DESC"
    where = ["m.entity_type IN ('city','town')"]
    params = {}
    if county_fips:
        where.append("m.county_fips = %(county)s"); params["county"] = county_fips
    sql = f"""
        SELECT m.geoid, m.name AS town, c.name AS county,
               sd.district_id, sd.name AS district, td.grade_span,
               s.sau_id, s.name AS sau,
               f.cpp_total, en.enrollment, en.student_teacher_ratio
        FROM town_district td
        JOIN municipality m ON m.geoid = td.geoid
        JOIN county c USING (county_fips)
        JOIN school_district sd ON sd.district_id = td.district_id
        LEFT JOIN sau s ON s.sau_id = sd.sau_id
        LEFT JOIN LATERAL (
            SELECT cpp_total FROM district_finance df
            WHERE df.district_id = td.district_id AND df.cpp_total IS NOT NULL
            ORDER BY df.year DESC LIMIT 1
        ) f ON true
        LEFT JOIN LATERAL (
            SELECT enrollment, student_teacher_ratio FROM district_enrollment de
            WHERE de.district_id = td.district_id
            ORDER BY de.year DESC LIMIT 1
        ) en ON true
        WHERE {" AND ".join(where)}
        ORDER BY {col} {direction} NULLS LAST, m.name ASC, sd.name ASC
    """
    return query(sql, params)

# --- DOE-25 finance: group the function lines into display buckets ---------
# Fixed categorical order (dataviz reference palette). Colors set in CSS by class.
EXP_GROUPS = [
    ("instruction",    "Instruction",       {"1100","1200","1300","1400","1500"}),
    ("support",        "Student & staff support", {"2100","2200","2900"}),
    ("administration", "Administration",     {"2300&2800","2400","2500"}),
    ("operations",     "Operations & maintenance", {"2600"}),
    ("transportation", "Transportation",     {"2700"}),
    ("other",          "Other",             None),   # residual bucket
]
REV_GROUPS = [
    ("proptax", "Local property tax", {"1100"}),
    ("localother", "Other local (tuition, fees)", None),   # code-less local row
    ("state_adequacy", "State adequacy aid", {"3111&3112&3119"}),
    ("state_other", "Other state aid", {"3120-3900"}),
    ("federal", "Federal aid", {"4000"}),
]

def _bucket(rows, groups, key_code, key_name):
    """Sum DOE lines into fixed display groups; keep only recurring (pct not null)."""
    named = {code for _, _, codes in groups if codes for code in codes}
    out = []
    total = sum(float(r["amount"]) for r in rows if r["pct"] is not None)
    for slug, label, codes in groups:
        if codes is None:
            members = [r for r in rows if r["pct"] is not None and r[key_code] not in named]
        else:
            members = [r for r in rows if r[key_code] in codes]
        amt = sum(float(r["amount"] or 0) for r in members)
        if amt <= 0:
            continue
        out.append({"slug": slug, "label": label, "amount": amt,
                    "pct": (amt / total * 100) if total else 0})
    return out, total

def get_finance(geoid):
    """Per-district 'where the money goes': spending buckets + revenue buckets,
    with the administration and property-tax shares called out. One entry per
    district the town belongs to (ordered like the Schools section)."""
    dists = query("""
        SELECT td.district_id, sd.name AS district, td.grade_span
        FROM town_district td JOIN school_district sd ON sd.district_id = td.district_id
        WHERE td.geoid = %s
        ORDER BY (CASE WHEN td.grade_span ~ '^[0-9]'
                       THEN split_part(td.grade_span,'-',1)::int ELSE 0 END), sd.name
    """, (geoid,))
    out = []
    for d in dists:
        did = d["district_id"]
        # latest loaded finance year for this district
        yr = query_one("""SELECT max(year) AS y FROM district_expenditure WHERE district_id=%s""", (did,))
        yr = yr["y"] if yr else None
        if yr is None:
            continue
        exp = query("""SELECT function_code, function_name, amount, pct
                       FROM district_expenditure WHERE district_id=%s AND year=%s""", (did, yr))
        rev = query("""SELECT source_code, source_name, amount, pct
                       FROM district_revenue WHERE district_id=%s AND year=%s""", (did, yr))
        if not exp and not rev:
            continue
        spend, spend_total = _bucket(exp, EXP_GROUPS, "function_code", "function_name")
        revb,  rev_total   = _bucket(rev, REV_GROUPS, "source_code", "source_name")
        admin = next((g["pct"] for g in spend if g["slug"] == "administration"), None)
        ptax  = next((g["pct"] for g in revb if g["slug"] == "proptax"), None)
        out.append({
            "district_id": did, "district": d["district"], "grade_span": d["grade_span"],
            "year": yr, "spend": spend, "spend_total": spend_total,
            "revenue": revb, "rev_total": rev_total,
            "admin_pct": admin, "proptax_pct": ptax,
        })
    return out

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
               r.ratio_pct AS ratio,
               cpp.cpp_total AS cpp_total
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
        LEFT JOIN LATERAL (
            SELECT df.cpp_total
            FROM town_district td
            JOIN district_finance df ON df.district_id = td.district_id
            WHERE td.geoid = m.geoid AND df.cpp_total IS NOT NULL
            ORDER BY (CASE
                        WHEN td.grade_span ~ '^[0-9]' THEN split_part(td.grade_span,'-',1)::int
                        ELSE 0 END), df.year DESC
            LIMIT 1
        ) cpp ON true
        WHERE {" AND ".join(where)}
        ORDER BY {col} {direction} NULLS LAST, m.name ASC
    """
    return query(sql, params)
