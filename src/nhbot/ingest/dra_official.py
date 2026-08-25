#!/usr/bin/env python3
"""
NHbot -- ingest DRA's OFFICIAL published equalized (full-value) tax rates.

DRA publishes the authoritative equalized rate each year in the
"Comparison of Full Value Tax Rates (Ranking Order)" PDF, which also carries
the total equalized valuation (incl. utilities + equalized railroad). For any
year DRA has published, this is the canonical equalized-rate source -- no
estimation. (For the current, not-yet-published year, use
compute_equalized_rates.py, which emits a clearly-labeled ESTIMATE.)

This scans raw/<year>/ for files matching *comparison-of-full-value*.pdf,
parses each, and writes one long-format table:

    phase0/nh_equalized_rates_official.csv   (one row per municipality per year)

DRA's definition (verbatim from the PDF):
  full_value_rate = gross local property taxes to be raised
      / total equalized valuation (incl. utility values + equalized railroad) * 1000

Dependencies: pdfplumber
"""
import re, csv, os, glob, difflib
import pdfplumber
import openpyxl

from nhbot.config import RAW_DIR, PROCESSED_DIR
RAW  = str(RAW_DIR)
OUT  = str(PROCESSED_DIR)

CITIES = {"Berlin","Claremont","Concord","Dover","Franklin","Keene","Laconia",
          "Lebanon","Manchester","Nashua","Portsmouth","Rochester","Somersworth"}

# The 25 unincorporated places, in post-canon() form. Used to classify
# entity_type deterministically -- these places carry a numeric rank only in
# years they levy a rate, so rank-based classification is unstable.
UNINC = {"Atkinson & Gilmanton","Bean's Grant","Bean's Purchase","Cambridge",
         "Chandler's Purchase","Crawford's Purchase","Cutt's Grant","Dix's Grant",
         "Dixville","Erving's Grant","Green's Grant","Hadley's Purchase",
         "Hale's Location","Kilkenny","Livermore","Low & Burbank's Grant",
         "Martin's Location","Millsfield","Odell","Pinkham's Grant",
         "Sargent's Purchase","Second College Grant","Success",
         "Thompson & Meserve's Purchase","Wentworth's Location"}

# Explicit repairs for PDF text-layer garbles seen across 2019-2024.
_VARIANTS = {"Atkinson & Gilmanton Academy Grant": "Atkinson & Gilmanton",
             "Wentworth Location": "Wentworth's Location",
             "Erving's Location": "Erving's Grant",
             "PHuarlech'sa Lsoecation": "Hale's Location"}

def _load_canonical():
    """234 municipality + 25 unincorporated names (post-canon) from the
    authoritative 2025 tax-rate workbook, for unknown-name detection."""
    f = os.path.join(RAW, "2025", "2025-municipal-and-village-district-tax-rates.xlsx")
    if not os.path.exists(f):
        return None
    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    ws = wb["2025 Municipal Tax Rates"]; names = set()
    for r in ws.iter_rows(min_row=6, values_only=True):
        m = r[0]
        if not m: continue
        m = re.sub(r"\s+", " ", str(m)).strip()
        if m.lower().startswith(("total","source","note","the ","municipal tax",
                                 "new hampshire","department","revenue")): continue
        if r[8] is None and r[4] is None: continue
        if m == "Penacook": continue
        names.add(canon(m))
    wb.close(); return names

_CANON = None  # lazily populated

def canon(n):
    if n is None: return None
    n = re.sub(r"\s+", " ", str(n)).strip()
    n = re.sub(r"\s*\(U\)\s*$", "", n)
    if "meserve" in n.lower():            # Thom(p)so(m/n) & Meserve's [Purchase]
        return "Thompson & Meserve's Purchase"
    n = _VARIANTS.get(n, n)
    global _CANON
    if _CANON and n not in _CANON:        # fuzzy-repair remaining garbles
        m = difflib.get_close_matches(n, _CANON, n=1, cutoff=0.82)
        if m: return m[0]
    return n

def fnum(s):
    if s is None: return None
    s = str(s).replace(",", "").strip()
    if s in ("", "N/A"): return None
    try: return float(s)
    except: return None

def parse_comparison_pdf(path):
    """Return {municipality: {...}} from one year's comparison PDF."""
    out = {}
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for t in page.extract_tables():
                for row in t:
                    if not row or not row[0]:
                        continue
                    name = canon(row[0])
                    low = name.lower()
                    if low.startswith(("municipality", "average", "nh department",
                                       "municipal and")) or re.match(r"^\d{4}", low):
                        continue
                    if len(row) < 7:
                        continue
                    local_rate, fvr = fnum(row[3]), fnum(row[5])
                    if local_rate is None and fvr is None:
                        continue
                    out[name] = dict(
                        modified_local_assessed_value=fnum(row[1]),
                        equalized_valuation_incl_util_rr=fnum(row[2]),
                        local_total_rate=local_rate,
                        equalization_ratio=fnum(row[4]),
                        full_value_rate_official=fvr,
                        rank=str(row[6]).strip())
    return out

def entity_type(name, rank):
    if name in UNINC:
        return "unincorporated"
    return "city" if name in CITIES else "town"

def year_of(path):
    m = re.search(r"(19|20)\d{2}", os.path.basename(path))
    return int(m.group(0)) if m else None

def main():
    global _CANON
    _CANON = _load_canonical()
    pdfs = sorted(glob.glob(os.path.join(RAW, "*", "*comparison-of-full-value*.pdf")))
    if not pdfs:
        print("no comparison PDFs found under raw/*/ -- nothing to ingest")
        return
    rows = []
    unknown = []
    for p in pdfs:
        yr = year_of(p)
        parsed = parse_comparison_pdf(p)
        if _CANON:
            for nm in parsed:
                if nm not in _CANON:
                    unknown.append((yr, nm))
        n_official = sum(1 for v in parsed.values()
                         if v["full_value_rate_official"] not in (None, 0))
        print(f"{os.path.basename(p)}: {len(parsed)} rows ({n_official} with a rate), year={yr}")
        for name, v in parsed.items():
            rows.append(dict(municipality=name, vintage=yr,
                             entity_type=entity_type(name, v["rank"]),
                             **v,
                             source=os.path.basename(p)))
    rows.sort(key=lambda r: (r["municipality"], r["vintage"]))
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "nh_equalized_rates_official.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    years = sorted({r["vintage"] for r in rows})
    print(f"\nwrote {path}: {len(rows)} rows across years {years}")
    if unknown:
        print(f"WARNING: {len(unknown)} unresolved name(s) not in canonical set: {unknown}")
    else:
        print("name validation: all rows map to canonical municipalities/unincorporated places")

if __name__ == "__main__":
    main()
