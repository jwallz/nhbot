"""Town tax-base composition — how a municipality's assessed valuation splits
across residential, commercial/industrial, utilities, and other property.

Source: NH DRA "Tables by County" (from the annual Equalization reports) — one
born-digital PDF per county, listing every municipality's valuation by class
(the same figures towns file on the MS-1). Drop the ten county PDFs in
data/raw/valuation/ and run `nhbot valuation`.

Why it matters: NH applies one tax rate to all property in a town, so a bigger
commercial/industrial base means businesses shoulder more of the levy and the
rate on homes is lower. This surfaces that share on each town page.

Writes nh_valuation_class.csv: geoid, name, county, year, residential,
commercial_industrial, utilities, other, gross. Each town reconciles to DRA's
own gross-valuation total.
"""
import csv, re, glob
from pathlib import Path
from nhbot.config import RAW_DIR, PROCESSED_DIR

SRC = RAW_DIR / "valuation"
CROSSWALK = PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"

def _norm(s):
    s = (s or "").lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

def _geoids():
    return {_norm(r["municipality"]): (r["geoid"], r["county_name"])
            for r in csv.DictReader(open(CROSSWALK))
            if r["entity_type"] in ("town", "city")}

def _parse_page(text):
    """{municipality: [numbers]} for one page of a Tables-by-County report."""
    rows = {}
    for l in text.splitlines():
        l = l.strip()
        if (not l or "DEPARTMENT OF REVENUE" in l or l.startswith("Municipality")
                or "MUNICIPAL AND PROPERTY" in l or "Tables by County" in l
                or "County Totals" in l or re.search(r"COUNTY\s*$", l)):
            continue
        m = re.match(r"^(.*?)\s+([\d,].*)$", l)     # name = leading text before the first number
        if not m:
            continue
        name = m.group(1).strip()
        if not re.match(r"^[A-Za-z]", name) or "Total" in name:
            continue
        nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]+", m.group(2))
                if x.replace(",", "").isdigit()]
        rows[name] = nums
    return rows

def parse_county(path):
    """{municipality: dict(residential, ci, utilities, other, gross)} for a county."""
    import pdfplumber
    pdf = pdfplumber.open(path)
    p1 = _parse_page(pdf.pages[0].extract_text() or "")   # ...RES LAND, CI LAND, RES BLDG, MFG
    p2 = _parse_page(pdf.pages[1].extract_text() or "")   # CI BLDG, ...WATER, GAS, ELEC, OTHER, ...GROSS
    out = {}
    for name, a in p1.items():
        b = p2.get(name)
        if not b or len(a) < 9 or len(b) < 9:
            continue
        res = a[5] + a[7] + a[8]              # residential land + buildings + manufactured housing
        ci = a[6] + b[0]                      # commercial/industrial land + buildings
        util = b[3] + b[4] + b[5] + b[6]      # water + gas/oil + electric + other utilities
        gross = b[8]
        other = gross - res - ci - util       # current use, conservation, easements, timber, etc.
        out[name] = dict(residential=res, commercial_industrial=ci,
                         utilities=util, other=other, gross=gross)
    return out

def main():
    SRC.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(glob.glob(str(SRC / "*.pdf")))
    if not files:
        print(f"  (no county valuation PDFs in {SRC}; skipping)")
        return
    xw = _geoids()
    rows, unmatched = [], []
    for f in files:
        year_m = re.search(r"(20\d{2})", Path(f).name)
        year = int(year_m.group(1)) if year_m else None
        county = parse_county(f)
        for name, v in county.items():
            hit = xw.get(_norm(name))
            if not hit:
                unmatched.append(name); continue
            geoid, cty = hit
            rows.append({"geoid": geoid, "name": name, "county": cty, "year": year, **v})
    cols = ["geoid", "name", "county", "year", "residential",
            "commercial_industrial", "utilities", "other", "gross"]
    with open(PROCESSED_DIR / "nh_valuation_class.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(f"  valuation: {len(rows)} municipalities from {len(files)} county files")
    if unmatched:
        print(f"  unmatched (unincorporated/other, skipped): {len(unmatched)} -> {unmatched[:8]}")
    print("  Run 'nhbot load' to ingest.")

if __name__ == "__main__":
    main()
