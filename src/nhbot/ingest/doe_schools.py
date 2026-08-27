"""NH DOE schools layer — structure + per-pupil cost + enrollment.

Inputs (data/raw/doe/, captured from NH DOE, all keyed on DOE district id):
  district-town.tsv            town -> district -> SAU (+ grade span), many-to-many
  cost-per-pupil-fy2025.csv    DOE-25 cost per pupil by district (elem/mid/high/total)
  stud-ratio21-22.csv          fall enrollment, teacher FTE, student-teacher ratio

Outputs (data/processed/):
  nh_school_structure.csv      geoid, town, district_id, district, grade_span, sau_id, sau
  nh_school_sau.csv            sau_id, sau
  nh_school_district.csv       district_id, district
  nh_district_finance.csv      district_id, year, cpp_elementary, cpp_middle, cpp_high, cpp_total
  nh_district_enrollment.csv   district_id, year, enrollment, teacher_fte, student_teacher_ratio

Everything joins town -> GEOID via the canonical crosswalk (by normalized name);
district facts join to towns through the structure table on district_id.
"""
import csv, re, os
from nhbot.config import RAW_DIR, PROCESSED_DIR

DOE = RAW_DIR / "doe"
CPP_YEAR = 2025          # FY (2024-2025 school year)
ENROLL_YEAR = 2022       # stud-ratio 2021-22

def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def key(name):
    n = norm(name).lower().replace("&", "and").replace("'", "").replace(".", "")
    return re.sub(r"\s+", " ", n).strip()

def money(v):
    v = norm(v).replace("$", "").replace(",", "")
    if v in ("", "-"):
        return None
    try: return float(v)
    except ValueError: return None

def num(v):
    v = norm(v)
    if v in ("", "-"): return None
    try: return float(v)
    except ValueError: return None

# ---------- crosswalk: municipality name -> geoid ----------
def load_town_geoid():
    out = {}
    with open(PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv") as f:
        for r in csv.DictReader(f):
            out[key(r["municipality"])] = (r["geoid"], r["municipality"])
    return out

# ---------- structure ----------
def parse_structure():
    rows = []
    with open(DOE / "district-town.tsv") as f:
        rd = csv.reader(f, delimiter="\t")
        header = next(rd)
        for r in rd:
            if len(r) < 7 or not r[0].strip().isdigit():
                continue
            rows.append({
                "district_id": int(r[0]), "district": norm(r[1]),
                "town": norm(r[3]), "grade_span": norm(r[4]),
                "sau_id": r[5].strip(), "sau": norm(r[6]),
            })
    return rows

# ---------- finance (cost per pupil) ----------
def parse_cpp():
    out = {}
    with open(DOE / "cost-per-pupil-fy2025.csv") as f:
        for r in csv.reader(f):
            if len(r) < 8 or not r[0].strip().isdigit():
                continue
            did = int(r[0].strip())
            out[did] = {
                "cpp_elementary": money(r[4]), "cpp_middle": money(r[5]),
                "cpp_high": money(r[6]), "cpp_total": money(r[7]),
            }
    return out

# ---------- enrollment / staffing ----------
def parse_enrollment():
    out = {}
    with open(DOE / "stud-ratio21-22.csv") as f:
        for r in csv.reader(f):
            if len(r) < 5 or not r[0].strip().isdigit():
                continue
            did = int(r[0].strip())
            out[did] = {
                "enrollment": num(r[2]), "teacher_fte": num(r[3]),
                "student_teacher_ratio": num(r[4]) or None,
            }
    return out

def main():
    town_geoid = load_town_geoid()
    structure = parse_structure()
    cpp = parse_cpp()
    enroll = parse_enrollment()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # dimensions
    saus = {}; districts = {}
    for s in structure:
        if s["sau_id"]:
            saus.setdefault(s["sau_id"], s["sau"])
        districts.setdefault(s["district_id"], s["district"])

    # structure rows -> geoid; report unmatched towns (academies, Penacook, facilities)
    struct_rows = []; unmatched = []
    for s in structure:
        m = town_geoid.get(key(s["town"]))
        if not m:
            unmatched.append(s["town"]); continue
        struct_rows.append({
            "geoid": m[0], "town": m[1], "district_id": s["district_id"],
            "district": s["district"], "grade_span": s["grade_span"],
            "sau_id": s["sau_id"], "sau": s["sau"],
        })

    def write(name, rows, cols):
        with open(PROCESSED_DIR / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

    write("nh_school_structure.csv", sorted(struct_rows, key=lambda x: (x["town"], x["district_id"])),
          ["geoid","town","district_id","district","grade_span","sau_id","sau"])
    write("nh_school_sau.csv", [{"sau_id":k,"sau":v} for k,v in sorted(saus.items(), key=lambda x:int(x[0]))],
          ["sau_id","sau"])
    write("nh_school_district.csv", [{"district_id":k,"district":v} for k,v in sorted(districts.items())],
          ["district_id","district"])
    write("nh_district_finance.csv",
          [{"district_id":k,"year":CPP_YEAR,**v} for k,v in sorted(cpp.items())],
          ["district_id","year","cpp_elementary","cpp_middle","cpp_high","cpp_total"])
    write("nh_district_enrollment.csv",
          [{"district_id":k,"year":ENROLL_YEAR,**v} for k,v in sorted(enroll.items())],
          ["district_id","year","enrollment","teacher_fte","student_teacher_ratio"])

    munis = {r["geoid"] for r in struct_rows}
    print(f"structure: {len(struct_rows)} town-district links, {len(munis)} municipalities matched")
    print(f"  unmatched 'towns' (expected: academies/village/facilities): {sorted(set(unmatched))}")
    print(f"districts: {len(districts)}  saus: {len(saus)}")
    print(f"finance: {len(cpp)} districts with cost-per-pupil")
    print(f"enrollment: {len(enroll)} districts")

if __name__ == "__main__":
    main()
