"""NH municipal (town-side) budget — department-level, from the standardized
DRA chart of accounts, for every town, run once a year.

Source: each town's most recent annual report from the UNH Scholars Repository
(scholars.unh.edu/nh_town_reports) — the one uniform, statewide, current archive.
Every town's report contains a DRA budget form (MS-535 actual expenditures, or
MS-232 / MS-737 appropriations) with 4-digit function codes. This module
auto-detects and extracts that form, whether it's clean text or a scanned image:

  detect_format():
    * text-form   -> a page carries an MS-5xx/2xx/7xx title AND >=3 coded lines
                     (this also rejects detailed budget worksheets, which lack the title)
    * scanned-form-> the report's TOC names the form's printed page; we map that to a
                     PDF page via the page-number footers on the scanned pages, then OCR
    * needs-review-> no standard form found (name-only summaries; the 13 cities' GASB format)

Categorization is by the form's SECTION HEADER (robust to OCR digit errors), with a
4-digit code range as fallback; "Payments to Other Governments" (the school/county
pass-through) is excluded so the figure is the town's own spending.

Output: data/processed/nh_municipal_expenditure.csv
        geoid, year, function_code, department, category, amount, kind, source
"""
import csv, re
from nhbot.config import RAW_DIR, PROCESSED_DIR

# A malformed PDF can hang pdfminer/pdfplumber deep in C, where Python signals never
# arrive — so a soft (SIGALRM) timeout can't interrupt it. Instead each report is parsed
# in a SEPARATE PROCESS that we HARD-kill if it runs past TOWN_TIMEOUT; the batch then
# continues and the offending town is flagged needs_review. Skip-list short-circuits
# known-bad files so we don't even wait on them.
TOWN_TIMEOUT = 120  # seconds
SKIP_GEOIDS = {
    "3301149140",  # Mont Vernon — malformed 2024 report PDF hangs pdfminer (hard-hang)
}

MUNI_DIR = RAW_DIR / "municipal"

SECTION_HEADERS = [
    (r"payments? to other government",  None),                        # exclude (pass-through)
    (r"general government",             "General Government"),
    (r"public safety",                  "Public Safety"),
    (r"airport|aviation",               "Airport / Aviation"),
    (r"highway",                        "Highways & Streets"),
    (r"sanitation",                     "Sanitation"),
    (r"water distribution|water treatment", "Water Distribution"),
    (r"^electric",                      "Electric"),
    (r"^health",                        "Health"),
    (r"^welfare",                       "Welfare"),
    (r"culture (and|&) recreation",     "Culture & Recreation"),
    (r"conservation|development",       "Conservation & Development"),
    (r"debt service",                   "Debt Service"),
    (r"capital outlay",                 "Capital Outlay"),
    (r"operating transfers",            "Operating Transfers"),
]

CODE_RANGES = [
    (4130, 4199, "General Government"), (4210, 4299, "Public Safety"),
    (4301, 4309, "Airport / Aviation"), (4311, 4319, "Highways & Streets"),
    (4321, 4329, "Sanitation"), (4331, 4339, "Water Distribution"),
    (4351, 4359, "Electric"), (4411, 4419, "Health"), (4441, 4449, "Welfare"),
    (4520, 4589, "Culture & Recreation"), (4611, 4659, "Conservation & Development"),
    (4711, 4790, "Debt Service"), (4901, 4909, "Capital Outlay"),
    (4911, 4919, "Operating Transfers"),
]

def category_by_code(code):
    for lo, hi, name in CODE_RANGES:
        if lo <= code <= hi:
            return name
    return "Other"

def money(s):
    try:
        return float(str(s).replace(",", "").replace("$", "").replace(" ", ""))
    except ValueError:
        return None

def coded_count(t):
    """Count '4xxx <Name>' records anywhere in the text (a form page has many).
    Splits each line at every 4-digit code so two-up (side-by-side) form layouts
    are counted fully, not just the left column."""
    n = 0
    for line in t.split("\n"):
        for rec in re.split(r"(?=\b4\d{3}\s+[A-Za-z])", _norm_amounts(line)):
            if re.match(r"\s*4\d{3}\s+[A-Za-z]", rec):
                n += 1
    return n

def _norm_amounts(line):
    # collapse spaces inside numbers/codes, an artifact of some PDFs — e.g.
    # "380,49 8.00", "$ 29 ,404.00", "86, 500", code "47 9 0" -> clean digits.
    line = re.sub(r"(?<=\d)[ \t]+(?=[\d,.])", "", line)   # digit | space | digit/comma/dot
    line = re.sub(r"(?<=[,.])[ \t]+(?=\d)", "", line)     # comma/dot | space | digit
    return line

# County/school/precinct pass-throughs — excluded so the figure is the town's own spending.
def _is_passthrough(code):
    return 4931 <= code <= 4939

def parse_lines(texts):
    """Yield (category, code, name, amount) for each town-budget line, robust across
    the DRA form variants seen in NH annual reports:
      * forms with NO "MS-535/636" label (detected purely by 4xxx function codes)
      * two-up page layouts (two form columns side by side) — each line is split at
        every code so both are captured
      * multi-amount rows (Adopted / Increases / One-Time / Default) — the FIRST
        dollar figure is taken (the adopted/appropriated amount)
      * intra-number spacing artifacts
    Category comes from the 4-digit code (category_by_code), so it needs no section
    headers. County/school pass-through codes (4931-4939) are excluded."""
    seen = set()
    for t in texts:
        for raw in t.split("\n"):
            line = _norm_amounts(raw)
            for rec in re.split(r"(?=\b4\d{3}\s+[A-Za-z])", line):
                m = re.match(r"\s*(4\d{3})\s+([A-Za-z][^$]*?)\s*\$", rec)
                if not m:
                    continue
                code = int(m.group(1))
                if _is_passthrough(code):
                    continue
                amts = re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", rec)
                val = money(amts[0]) if amts else None      # first = adopted/appropriated
                if val is None or val <= 0:
                    continue
                if code in seen:      # a code can repeat across summary+detail pages; keep first
                    continue
                seen.add(code)
                name = re.sub(r"\s{2,}.*$", "", m.group(2).strip())   # drop trailing columns
                name = re.sub(r"[\s,]+\d[\d,]*$", "", name).strip()    # drop trailing stray digits
                yield category_by_code(code), code, name, val

# ---------- text acquisition ----------
def pdf_page_texts(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]

def ocr_texts(path, lo, hi, dpi=350):
    import pymupdf, pytesseract, io
    from PIL import Image
    doc = pymupdf.open(str(path))
    out = []
    for idx in range(lo, min(hi + 1, len(doc))):
        pix = doc[idx].get_pixmap(dpi=dpi)
        out.append(pytesseract.image_to_string(
            Image.open(io.BytesIO(pix.tobytes("png"))), config="--psm 6"))
    return out

# ---------- form location ----------
def _toc_form(texts):
    """Best DRA form referenced in the TOC as (preference, form, printed_page).
    Prefers MS-535 (actuals) > MS-737 > MS-232."""
    best = None
    for i in range(min(8, len(texts))):
        for l in texts[i].split("\n"):
            u = l.replace("-", "").upper()
            for form, pref in (("MS-535", 0), ("MS-737", 1), ("MS-232", 2)):
                if form.replace("-", "") in u:
                    nums = re.findall(r"\d+", l)
                    if nums:
                        cand = (pref, form, int(nums[-1]))
                        if best is None or cand < best:
                            best = cand
    return best

def _printed_map(texts):
    """printed page number -> PDF index, using the short (scanned) pages whose
    only text is their page-number footer."""
    m = {}
    for i, t in enumerate(texts):
        if len(t) < 80:
            nums = re.findall(r"\b(\d{1,3})\b", t)
            if len(nums) == 1:
                m.setdefault(int(nums[0]), i)
    return m

def _year(text):
    m = re.search(r"\b(20\d\d)\b", text)
    return int(m.group(1)) if m else None

def _build(depts, kind, year, fmt):
    seen = {}
    for cat, code, name, val in depts:
        if val <= 0:
            continue
        if cat == "Other":
            cat = category_by_code(code)
        seen[(cat, code)] = (name, val)
    rows = [(cat, code, name, val) for (cat, code), (name, val) in seen.items()]
    total = sum(v for _, _, _, v in rows)
    return {"status": "ok" if rows else "empty", "format": fmt, "kind": kind,
            "year": year, "total": total, "rows": rows}

def auto_extract(path, year=None, hint=None):
    """Extract the town's DRA budget form. If `hint` is given (a one-time per-town
    config: {pages:(lo,hi) 0-indexed, ocr:bool, kind:'actual'|'appropriation'}),
    extract those pages directly — reliable when auto-location can't find the form.
    Otherwise auto-detect (text-form or scanned-form). Returns status/format/kind/
    year/total/rows."""
    if hint and hint.get("pages"):
        lo, hi = hint["pages"]
        texts = (ocr_texts(path, lo, hi) if hint.get("ocr")
                 else pdf_page_texts(path)[lo:hi + 1])
        kind = hint.get("kind", "appropriation")
        yr = year or _year(" ".join(texts[:3]))
        fmt = "hint-ocr" if hint.get("ocr") else "hint-text"
        return _build(parse_lines(texts), kind, yr, fmt)
    texts = pdf_page_texts(path)
    # A) text form: pages dense with 4xxx function-code lines. No MS-label required —
    # many NH reports print the DRA appropriations form under just "APPROPRIATIONS",
    # and the modern (MS-636/MS-DTB software) form has no visible MS number either.
    formp = [i for i, t in enumerate(texts) if coded_count(t) >= 8]
    if formp:
        ftext = " ".join(texts[i] for i in formp)
        is_actual = bool(re.search(r"MS-?535|actual expenditures?", ftext, re.I)) and not \
            re.search(r"appropriat|adopted budget|default budget|MS-?(636|232|737)", ftext, re.I)
        kind = "actual" if is_actual else "appropriation"
        depts = parse_lines([texts[i] for i in formp])
        return _build(depts, kind, year or _year(ftext), "text-form")
    # B) scanned form: TOC printed page -> PDF page -> OCR
    tf, pm = _toc_form(texts), _printed_map(texts)
    if tf and tf[2] in pm:
        start = pm[tf[2]]
        otexts = ocr_texts(path, start, start + 7)
        kind = "actual" if tf[1] == "MS-535" else "appropriation"
        y = year or _year(" ".join(otexts[:2])) or (_year(texts[3]) if len(texts) > 3 else None)
        return _build(parse_lines(otexts), kind, y, "scanned-ocr")
    return {"status": "needs_review", "format": "unknown", "kind": None,
            "year": year, "total": 0, "rows": [],
            "note": "no standard DRA form found (name-only summary or city GASB format)"}


def extract_with_timeout(path, year=None, hint=None, timeout=TOWN_TIMEOUT):
    """Run auto_extract in a separate PYTHON PROCESS, hard-killed (SIGKILL) if it runs
    past `timeout`. This is the only reliable way to abort a PDF that hangs pdfminer deep
    in C — signals can't reach it, but SIGKILL from the OS always can. Raises TimeoutError
    on hang. The child is `python -m nhbot.ingest.municipal --one <pdf> [year] [--hint j]`
    which prints the result dict as one JSON line."""
    import subprocess, sys, json
    cmd = [sys.executable, "-m", "nhbot.ingest.municipal", "--one", str(path), str(year or "")]
    if hint:
        cmd += ["--hint", json.dumps(hint)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f">{timeout}s")   # subprocess.run already SIGKILLed the child
    for line in out.stdout.splitlines():
        if line.startswith("NHBOT_RESULT:"):
            return json.loads(line[len("NHBOT_RESULT:"):])
    raise RuntimeError((out.stderr or "no result").strip().splitlines()[-1][:200]
                       if (out.stderr or "").strip() else "no result")


# Per-town HINTS (optional), keyed by geoid. Most towns need none — the form is
# auto-located. Add an entry only when auto-location can't find the form (some reports
# have it scanned with no reliable text index):
#     "<geoid>": {"pages": (lo, hi), "ocr": True/False, "kind": "actual"/"appropriation"}
# — 0-indexed PDF pages of the MS-535 (actuals) or MS-232/737 (appropriations). Page
# ranges are stable year to year, so build the hint once and it's reused each annual run.
# The hint augments the town's auto-discovered {geoid}[_{year}].pdf; it never replaces it.
HINTS = {
    # "3301101300": {"pages": (120, 127), "ocr": False, "kind": "appropriation"},
}

_GEOID_RE = re.compile(r"^(\d{10})(?:_(\d{4}))?\.pdf$", re.I)


def _geoid_names():
    """geoid -> canonical town name, from the crosswalk."""
    import csv as _csv
    p = PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"
    out = {}
    try:
        for r in _csv.DictReader(open(p)):
            out[r["geoid"]] = r["municipality"]
    except FileNotFoundError:
        pass
    return out


def _worklist():
    """Every data/raw/municipal/{geoid}[_{year}].pdf, one dict per town: geoid, town,
    pdf, year, hint. Purely file-driven so it scales to all 234 towns; a HINTS entry
    (looked up by geoid) is attached when present."""
    names = _geoid_names()
    items, seen = [], set()
    for p in sorted(MUNI_DIR.glob("*.pdf")):
        m = _GEOID_RE.match(p.name)
        if not m or m.group(1) in seen:
            continue
        geoid, yr = m.group(1), m.group(2)
        seen.add(geoid)
        items.append({"geoid": geoid, "town": names.get(geoid, geoid), "pdf": p.name,
                      "year": int(yr) if yr else None, "hint": HINTS.get(geoid)})
    return items


EXP_FIELDS = ["geoid", "year", "function_code", "department", "category", "amount", "kind", "source"]
ST_FIELDS  = ["geoid", "name", "status", "kind", "year", "source", "note"]


def _done_geoids():
    """geoids already recorded in nh_municipal_status.csv (from a prior/partial run)."""
    p = PROCESSED_DIR / "nh_municipal_status.csv"
    if not p.exists():
        return set()
    try:
        return {r["geoid"] for r in csv.DictReader(open(p)) if r.get("geoid")}
    except Exception:
        return set()


def main():
    """Extract the town budget form from every data/raw/municipal/{geoid}[_{year}].pdf.

    RESUMABLE + INCREMENTAL: results are appended per town and flushed immediately, so a
    long OCR run that is interrupted (or stopped early) loses nothing and simply resumes.
    Re-running skips towns already in nh_municipal_status.csv. Set MUNI_FRESH=1 to start
    over (truncate and re-extract everything)."""
    import os
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    exp_path = PROCESSED_DIR / "nh_municipal_expenditure.csv"
    st_path  = PROCESSED_DIR / "nh_municipal_status.csv"

    fresh = os.environ.get("MUNI_FRESH") == "1" or not st_path.exists()
    done = set() if fresh else _done_geoids()
    mode = "w" if fresh else "a"
    exp_f = open(exp_path, mode, newline=""); exp_w = csv.DictWriter(exp_f, fieldnames=EXP_FIELDS)
    st_f  = open(st_path, mode, newline="");  st_w  = csv.DictWriter(st_f, fieldnames=ST_FIELDS)
    if fresh:
        exp_w.writeheader(); st_w.writeheader(); exp_f.flush(); st_f.flush()

    work = [s for s in _worklist() if s["geoid"] not in done]
    n = len(work)
    tally = {"loaded": 0, "needs_review": 0}
    print(f"{'FRESH start' if fresh else 'RESUMING'}: {n} town(s) to process"
          + (f"  ({len(done)} already done)" if done else ""), flush=True)

    def emit_status(**row):
        st_w.writerow(row); st_f.flush()

    for i, s in enumerate(work, 1):
        geoid, town, pdf, yr = s["geoid"], s["town"], s["pdf"], s.get("year")
        path = MUNI_DIR / pdf
        print(f"[{i}/{n}] {town} ({pdf})...", flush=True)
        if not path.exists():
            continue
        if geoid in SKIP_GEOIDS:
            print(f"       -> needs_review: skipped (known-bad PDF)", flush=True)
            emit_status(geoid=geoid, name=town, status="needs_review", kind="",
                        year=yr or "", source="", note="skipped: PDF hangs parser (needs a page hint)")
            tally["needs_review"] += 1
            continue
        try:
            r = extract_with_timeout(path, year=yr, hint=s.get("hint"), timeout=TOWN_TIMEOUT)
        except TimeoutError:
            print(f"       -> needs_review: timeout (>{TOWN_TIMEOUT}s, hard-killed)", flush=True)
            emit_status(geoid=geoid, name=town, status="needs_review", kind="",
                        year=yr or "", source="", note=f"timeout >{TOWN_TIMEOUT}s")
            tally["needs_review"] += 1
            continue
        except Exception as ex:
            print(f"       -> error: {type(ex).__name__}", flush=True)
            emit_status(geoid=geoid, name=town, status="needs_review", kind="",
                        year=yr or "", source="", note=f"error: {ex}")
            tally["needs_review"] += 1
            continue
        if r["status"] != "ok":
            note = r.get("note", r["format"])
            print(f"       -> needs_review: {note}", flush=True)
            emit_status(geoid=geoid, name=town, status="needs_review", kind=r.get("kind") or "",
                        year=r.get("year") or yr or "", source="", note=note)
            tally["needs_review"] += 1
            continue
        print(f"       -> ok/{r['format']}: {len(r['rows'])} depts, {r['kind']}, ${r['total']:,.0f}", flush=True)
        src = f"UNH {town} {r['year']} annual report — {r['kind']} ({r['format']})"
        for cat, code, name, val in r["rows"]:
            exp_w.writerow({"geoid": geoid, "year": r["year"], "function_code": str(code),
                            "department": name, "category": cat, "amount": val,
                            "kind": r["kind"], "source": src})
        exp_f.flush()
        emit_status(geoid=geoid, name=town, status="loaded", kind=r["kind"],
                    year=r["year"] or "", source=src, note=r["format"])
        tally["loaded"] += 1

    exp_f.close(); st_f.close()
    print(f"\n=== municipal batch done: {tally['loaded']} loaded, "
          f"{tally['needs_review']} needs_review (of {n} processed this run) ===")
    print(f"-> nh_municipal_expenditure.csv (append), nh_municipal_status.csv (append)")
    print("Next: `nhbot municipal-coverage` then `nhbot load`.")

def _one(argv):
    """Single-file extract worker for extract_with_timeout's subprocess. Prints the
    result dict as one 'NHBOT_RESULT:{json}' line."""
    import json
    path = argv[argv.index("--one") + 1]
    year = None
    yi = argv.index("--one") + 2
    if yi < len(argv) and argv[yi] and not argv[yi].startswith("--"):
        year = int(argv[yi]) if argv[yi].isdigit() else None
    hint = json.loads(argv[argv.index("--hint") + 1]) if "--hint" in argv else None
    r = auto_extract(path, year=year, hint=hint)
    print("NHBOT_RESULT:" + json.dumps(r))


if __name__ == "__main__":
    import sys
    if "--one" in sys.argv:
        _one(sys.argv)
    else:
        main()
