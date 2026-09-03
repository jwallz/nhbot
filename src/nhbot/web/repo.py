"""Query layer — plain functions returning dicts. Read-only views over the
canonical `nh` schema. Everything joins on GEOID.

Two rates matter and both are surfaced:
  * advertised  — the rate on the tax bill (tax_rate.total_rate)
  * equalized   — DRA full-value rate for cross-town comparison (equalized_rate)
"""
from nhbot.web.db import query, query_one
from nhbot.web.slug import slugify

METRICS = {"advertised", "equalized"}

# --- slug <-> geoid (RESTful town URLs: /amherst, /new-boston) -----------------
_SLUG_TO_GEOID = None
_GEOID_TO_SLUG = None

def _build_slug_maps():
    global _SLUG_TO_GEOID, _GEOID_TO_SLUG
    rows = query("SELECT geoid, name FROM municipality")
    _SLUG_TO_GEOID = {slugify(r["name"]): r["geoid"] for r in rows}
    _GEOID_TO_SLUG = {r["geoid"]: slugify(r["name"]) for r in rows}

def geoid_for_slug(slug):
    if _SLUG_TO_GEOID is None:
        _build_slug_maps()
    return _SLUG_TO_GEOID.get(slug)

def slug_for(geoid):
    if _GEOID_TO_SLUG is None:
        _build_slug_maps()
    return _GEOID_TO_SLUG.get(geoid)

def all_towns():
    """[{name, slug, county, entity_type}] for the town-list page and the
    index search box. Real municipalities only (towns + cities), alphabetical."""
    rows = query("""
        SELECT m.name, m.geoid, m.entity_type, c.name AS county
        FROM municipality m JOIN county c USING (county_fips)
        WHERE m.entity_type IN ('town','city')
        ORDER BY m.name
    """)
    return [{"name": r["name"], "slug": slugify(r["name"]),
             "county": r["county"], "entity_type": r["entity_type"]} for r in rows]

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
               m.aland_sqmi, m.website,
               m.form_of_government, m.governing_body, m.sb2,
               m.year_incorporated, m.population_2020, m.history, m.history_source,
               m.ms535_status, m.ms535_year, m.ms535_kind,
               c.name AS county
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

# Municipal category -> fixed display slug (own color) or fold into "other".
MUNI_SLUG = {
    "General Government":   "gengov",
    "Public Safety":        "safety",
    "Highways & Streets":   "highways",
    "Culture & Recreation": "culture",
    "Capital Outlay":       "capital",
}

def total_budget(geoid):
    """Combined public operating budget serving the town: the town (municipal)
    budget plus each school district's spending. Cooperative districts serve more
    than one town, so they're flagged as shared. Colors: town orange, schools blue/aqua."""
    parts = []
    muni = query_one("""SELECT sum(amount) AS t, max(year) AS y, min(kind) AS kind
                        FROM municipal_expenditure
                        WHERE geoid=%s AND kind = (
                            SELECT kind FROM municipal_expenditure WHERE geoid=%s
                            ORDER BY (kind='actual') DESC, year DESC LIMIT 1)""", (geoid, geoid))
    if muni and muni["t"]:
        parts.append({"slug": "municipal", "label": "Town (municipal)", "amount": float(muni["t"]),
                      "year": muni["y"], "shared": False, "grade_span": None})
    else:
        return None   # no honest "combined" figure without the town's own budget
    dists = query("""
        SELECT sd.district_id, sd.name, td.grade_span,
               (SELECT count(*) FROM town_district t2 WHERE t2.district_id = sd.district_id) AS n_towns
        FROM town_district td JOIN school_district sd ON sd.district_id = td.district_id
        WHERE td.geoid = %s
        ORDER BY (CASE WHEN td.grade_span ~ '^[0-9]'
                       THEN split_part(td.grade_span,'-',1)::int ELSE 0 END), sd.name
    """, (geoid,))
    school_slugs = ["education", "county", "culture"]  # blue, aqua, yellow for up to 3 districts
    for i, d in enumerate(dists):
        y = query_one("SELECT max(year) AS y FROM district_expenditure WHERE district_id=%s", (d["district_id"],))
        if not y or not y["y"]:
            continue
        tot = query_one("""SELECT sum(amount) AS t FROM district_expenditure
                           WHERE district_id=%s AND year=%s AND pct IS NOT NULL""",
                        (d["district_id"], y["y"]))
        if not tot or not tot["t"]:
            continue
        parts.append({"slug": school_slugs[i % len(school_slugs)],
                      "label": d["name"], "amount": float(tot["t"]), "year": y["y"],
                      "shared": d["n_towns"] > 1, "grade_span": d["grade_span"]})
    if len(parts) < 2:
        return None
    total = sum(p["amount"] for p in parts)
    for p in parts:
        p["pct"] = p["amount"] / total * 100
    school_total = sum(p["amount"] for p in parts if p["slug"] != "municipal")
    town_total = next((p["amount"] for p in parts if p["slug"] == "municipal"), 0)
    return {"parts": parts, "total": total, "school_total": school_total,
            "town_total": town_total, "has_shared": any(p["shared"] for p in parts)}

def tax_dollar(geoid, year):
    """The four-way split of the tax bill as shares of the total rate — the macro
    'where your property tax goes' view. Uses the advertised rate components."""
    r = query_one("""SELECT municipal_rate, county_rate, local_ed_rate, state_ed_rate, total_rate
                     FROM tax_rate WHERE geoid=%s AND tax_year=%s AND municipal_rate IS NOT NULL""",
                  (geoid, year))
    if not r or not r["total_rate"]:
        return None
    tot = float(r["total_rate"])
    parts = [("education", "Education (schools)",
              float(r["local_ed_rate"] or 0) + float(r["state_ed_rate"] or 0)),
             ("municipal", "Town / municipal", float(r["municipal_rate"] or 0)),
             ("county", "County", float(r["county_rate"] or 0))]
    return {"total_rate": tot,
            "parts": [{"slug": s, "label": l, "rate": v, "pct": v / tot * 100}
                      for s, l, v in parts if v > 0]}

def get_municipal(geoid):
    """Town-side budget by department (appropriations). Category stacked bar
    (top categories + Other) plus the biggest individual departments."""
    rows = query("""SELECT category, department, function_code, amount, kind, source, year
                    FROM municipal_expenditure
                    WHERE geoid=%s AND kind = (
                        SELECT kind FROM municipal_expenditure WHERE geoid=%s
                        ORDER BY (kind='actual') DESC, year DESC LIMIT 1)
                    ORDER BY amount DESC""", (geoid, geoid))
    if not rows:
        return None
    total = sum(float(r["amount"]) for r in rows)
    cats = {}
    for r in rows:
        slug = MUNI_SLUG.get(r["category"], "other")
        label = r["category"] if slug != "other" else "Other"
        c = cats.setdefault(slug, {"slug": slug, "label": label, "amount": 0.0})
        c["amount"] += float(r["amount"])
    # order: named categories by amount, then Other last
    named = sorted([c for c in cats.values() if c["slug"] != "other"], key=lambda x: -x["amount"])
    other = [c for c in cats.values() if c["slug"] == "other"]
    catlist = named + other
    for c in catlist:
        c["pct"] = c["amount"] / total * 100
    depts = [{"department": r["department"], "category": r["category"],
              "amount": float(r["amount"]), "pct": float(r["amount"]) / total * 100}
             for r in rows][:8]
    return {"year": rows[0]["year"], "kind": rows[0]["kind"], "source": rows[0]["source"],
            "total": total, "categories": catlist, "departments": depts}

_BOARD_LABEL = {"Selectman": "Select Board", "Town Councilor": "Town Council",
                "Alderman": "Board of Aldermen"}


def get_select_board(geoid):
    """The town's governing board — select board, town council, or aldermen — chair first."""
    rows = query("""SELECT role, name, is_chair, phone, email
                    FROM select_board WHERE geoid=%s
                    ORDER BY (NOT is_chair), seq""", (geoid,))
    if not rows:
        return None
    from collections import Counter
    role = Counter(r["role"] for r in rows).most_common(1)[0][0]
    members = [{"name": r["name"], "is_chair": r["is_chair"], "role": r["role"],
                "phone": r["phone"] or None, "email": r["email"] or None} for r in rows]
    return {"label": _BOARD_LABEL.get(role, "Governing Board"), "members": members}


_PARTY = {"R": "Republican", "D": "Democrat", "I": "Independent"}


def get_legislators(geoid):
    """The town's state representatives (from its base + floterial House districts) and
    its senator(s) (a split city has several). 'Former' roster entries are hidden."""
    reps = query("""SELECT l.first_name, l.last_name, l.party, l.email, l.town_residence,
                           t.county, t.district
                    FROM town_house_district t
                    JOIN legislator l ON l.body='house' AND l.county=t.county AND l.district=t.district
                    WHERE t.geoid=%s AND l.elected_status <> 'Former'
                    ORDER BY t.district, l.last_name""", (geoid,))
    sens = query("""SELECT l.first_name, l.last_name, l.party, l.email, l.town_residence,
                           t.senate_district AS district
                    FROM town_senate_district t
                    JOIN legislator l ON l.body='senate' AND l.district=t.senate_district
                    WHERE t.geoid=%s AND l.elected_status <> 'Former'
                    ORDER BY t.senate_district, l.last_name""", (geoid,))
    if not reps and not sens:
        return None

    def fmt(r, house):
        district = (f"{r['county']} District {r['district']}" if house
                    else f"Senate District {r['district']}")
        return {"name": f"{r['first_name']} {r['last_name']}".strip(),
                "party": (r["party"] or "").upper(),
                "party_label": _PARTY.get((r["party"] or "").upper(), r["party"]),
                "district": district, "email": r["email"] or None,
                "town_residence": r["town_residence"] or None}

    return {"reps": [fmt(r, True) for r in reps],
            "senators": [fmt(r, False) for r in sens]}


def political_makeup():
    """Per-town partisan composition of state representation, for the statewide
    map. Counts every seated legislator equally — each House rep from the town's
    base + floterial districts, plus its senator(s) — as one unit. 'lean' runs
    -1 (all Democratic) → 0 (evenly split) → +1 (all Republican), computed over
    the two major parties only; independents show in the tally but not the axis.
    Returns {geoid: {d, r, i, n, lean, has_major}}."""
    rows = query("""
        WITH tl AS (
            SELECT t.geoid, upper(l.party) AS party
            FROM town_house_district t
            JOIN legislator l ON l.body='house' AND l.county=t.county AND l.district=t.district
            WHERE l.elected_status <> 'Former'
            UNION ALL
            SELECT t.geoid, upper(l.party) AS party
            FROM town_senate_district t
            JOIN legislator l ON l.body='senate' AND l.district=t.senate_district
            WHERE l.elected_status <> 'Former'
        )
        SELECT geoid,
               count(*) FILTER (WHERE party = 'D')                  AS d,
               count(*) FILTER (WHERE party = 'R')                  AS r,
               count(*) FILTER (WHERE party NOT IN ('D', 'R'))      AS i,
               count(*)                                             AS n
        FROM tl GROUP BY geoid""")
    out = {}
    for row in rows:
        d, r = row["d"], row["r"]
        dr = d + r
        out[row["geoid"]] = {
            "d": d, "r": r, "i": row["i"], "n": row["n"],
            "lean": (r - d) / dr if dr else 0.0,
            "has_major": dr > 0,
        }
    return out


# Party-lean gradient stops, shared by the map fills and the page legend so they
# always match: all-D (blue) → even (purple) → all-R (red).
LEAN_STOPS = {"d": "#1c5cab", "mid": "#6b4c9a", "r": "#c0392b", "none": "#9a9a95"}


def _points(series, w, h, pad, ymin, ymax):
    """(x,y) pixel points for a list of (year,value); value None -> skipped.
    Returns (points_str, [(x,y,year,value)] for markers)."""
    pts = [(yr, v) for yr, v in series if v is not None]
    if len(pts) < 2:
        return "", []
    xs = [p[0] for p in pts]
    xmin, xmax = min(xs), max(xs)
    span = (xmax - xmin) or 1
    yr_span = (ymax - ymin) or 1
    out, markers = [], []
    for yr, v in pts:
        x = pad + (yr - xmin) / span * (w - 2 * pad)
        y = h - pad - (v - ymin) / yr_span * (h - 2 * pad)
        out.append(f"{x:.1f},{y:.1f}")
        markers.append((round(x, 1), round(y, 1), yr, v))
    return " ".join(out), markers

def get_finance_trend(geoid):
    """Per district the town belongs to: cost-per-pupil and the administration /
    special-education budget shares across every loaded year. Pre-scales the
    series to SVG coordinates so the template just draws polylines."""
    dists = query("""
        SELECT td.district_id, sd.name AS district, td.grade_span
        FROM town_district td JOIN school_district sd ON sd.district_id = td.district_id
        WHERE td.geoid = %s
        ORDER BY (CASE WHEN td.grade_span ~ '^[0-9]'
                       THEN split_part(td.grade_span,'-',1)::int ELSE 0 END), sd.name
    """, (geoid,))
    W, H, PAD = 320, 120, 16
    out = []
    for d in dists:
        did = d["district_id"]
        rowsy = query("""
            SELECT de.year,
              sum(de.amount) FILTER (WHERE de.function_code IN ('2300&2800','2400','2500')) AS admin,
              sum(de.amount) FILTER (WHERE de.function_code = '1200') AS sped,
              sum(de.amount) FILTER (WHERE de.pct IS NOT NULL) AS total
            FROM district_expenditure de WHERE de.district_id=%s GROUP BY de.year ORDER BY de.year
        """, (did,))
        cppy = {r["year"]: r["cpp_total"] for r in query(
            "SELECT year, cpp_total FROM district_finance WHERE district_id=%s", (did,))}
        if len(rowsy) < 2:
            continue
        years = [r["year"] for r in rowsy]
        cpp = [(y, float(cppy[y])) for y in years if cppy.get(y)]
        admin = [(r["year"], float(r["admin"]) / float(r["total"]) * 100)
                 if r["total"] else (r["year"], None) for r in rowsy]
        sped = [(r["year"], float(r["sped"]) / float(r["total"]) * 100)
                if r["total"] and r["sped"] is not None else (r["year"], None) for r in rowsy]
        if len(cpp) < 2:
            continue
        cvals = [v for _, v in cpp]
        cmin, cmax = min(cvals), max(cvals)
        cpad = (cmax - cmin) * 0.12 or cmax * 0.05
        cpp_pts, cpp_mk = _points(cpp, W, H, PAD, cmin - cpad, cmax + cpad)
        pvals = [v for _, v in admin + sped if v is not None]
        pmax = (max(pvals) if pvals else 30) * 1.15
        admin_pts, _ = _points(admin, W, H, PAD, 0, pmax)
        sped_pts, _ = _points(sped, W, H, PAD, 0, pmax)
        first_v, last_v = cpp[0][1], cpp[-1][1]
        out.append({
            "district_id": did, "district": d["district"], "grade_span": d["grade_span"],
            "w": W, "h": H,
            "year_first": years[0], "year_last": years[-1],
            "cpp_first": first_v, "cpp_last": last_v,
            "cpp_change": (last_v - first_v) / first_v * 100 if first_v else None,
            "cpp_pts": cpp_pts, "cpp_end": cpp_mk[-1] if cpp_mk else None,
            "admin_pts": admin_pts, "sped_pts": sped_pts,
            "admin_last": next((v for _, v in reversed(admin) if v is not None), None),
            "sped_last": next((v for _, v in reversed(sped) if v is not None), None),
            "pmax": pmax,
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


# ---------------------------------------------------------------------------
# State of New Hampshire fiscal data (statewide budget & revenue)
# ---------------------------------------------------------------------------
def state_budget_years():
    return [r["fiscal_year"] for r in
            query("SELECT DISTINCT fiscal_year FROM state_budget ORDER BY fiscal_year")]

def state_budget(year):
    """Appropriations by department for one fiscal year, largest first."""
    return query("""SELECT category, department, amount FROM state_budget
                    WHERE fiscal_year=%s ORDER BY amount DESC""", (year,))

def state_budget_by_category(year):
    return query("""SELECT category, sum(amount) AS amount FROM state_budget
                    WHERE fiscal_year=%s GROUP BY category ORDER BY amount DESC""", (year,))

def state_budget_total(year):
    r = query_one("SELECT sum(amount) AS t FROM state_budget WHERE fiscal_year=%s", (year,))
    return float(r["t"]) if r and r["t"] is not None else 0.0

def state_funding(year):
    return query("""SELECT source, amount FROM state_funding
                    WHERE fiscal_year=%s ORDER BY amount DESC""", (year,))

def state_federal_funds(year):
    """The 'Federal Funds' source broken down by receiving agency, largest first.
    Returns [] if the table hasn't been created yet (older load)."""
    reg = query_one("SELECT to_regclass('nh.state_federal_funds') AS t")
    if not reg or reg["t"] is None:
        return []
    return query("""SELECT category, department, amount FROM state_federal_funds
                    WHERE fiscal_year=%s ORDER BY amount DESC""", (year,))

def state_revenue_years():
    return [r["fiscal_year"] for r in
            query("SELECT DISTINCT fiscal_year FROM state_revenue ORDER BY fiscal_year")]

def state_revenue(year):
    """Revenue by source ($M) for one fiscal year, largest actual first, with the
    prior year's actual attached for a simple year-over-year read."""
    cur = query("""SELECT source, actual_musd, plan_musd FROM state_revenue
                   WHERE fiscal_year=%s ORDER BY actual_musd DESC NULLS LAST""", (year,))
    prior = {r["source"]: r["actual_musd"] for r in
             query("SELECT source, actual_musd FROM state_revenue WHERE fiscal_year=%s", (year - 1,))}
    for r in cur:
        r["prior_musd"] = prior.get(r["source"])
    return cur

def state_revenue_total(year):
    r = query_one("SELECT sum(actual_musd) AS t FROM state_revenue WHERE fiscal_year=%s", (year,))
    return float(r["t"]) if r and r["t"] is not None else 0.0


# ---------------------------------------------------------------------------
# National tax comparison (how NH compares to other states)
# ---------------------------------------------------------------------------
def state_comparison_rows():
    """Every state's row from the Tax Foundation comparison table."""
    return query("SELECT * FROM state_tax_comparison")

def state_comparison_map():
    return {r["state"]: r for r in state_comparison_rows()}


def get_valuation(geoid):
    """A town's assessed-valuation composition by property class, with each
    class's share of gross valuation precomputed. None if not loaded."""
    r = query_one("""SELECT year, residential, commercial_industrial, utilities,
                            other, gross FROM valuation_class WHERE geoid = %s""", (geoid,))
    if not r or not r["gross"]:
        return None
    g = float(r["gross"])
    out = dict(r); out["gross"] = g
    for k in ("residential", "commercial_industrial", "utilities", "other"):
        v = float(r[k] or 0)
        out[k] = v
        out[k + "_pct"] = round(100 * v / g, 1)
    return out
