"""National tax comparison — how New Hampshire's tax burden and tax mix compare
to other states. Source: Tax Foundation "Facts & Figures: How Does Your State
Compare?" (annual PDF; figures ultimately from U.S. Census / BEA).

Drop the PDF in data/raw/national/ and run `nhbot tax-comparison`. Writes
nh_state_comparison.csv (one row per state + U.S. + D.C.) with:
  burden_pct / burden_rank        Table 2  state-local tax burden, % of income
  collections_percap / _rank      Table 5  state & local tax collections per capita
  prop/sales/indinc/corpinc/other Table 7  sources of collections, % from each
  eff_property_rate / _rank       Table 33 property tax as % of home value

Tables 2 & 7 are one-up (one state per line); 5 & 33 are two-up (two per line).
The parser locates state tokens via the Tax Foundation abbreviation set, so it
survives the differing column counts (e.g. the U.S. summary row has no rank).
"""
import csv, re, glob, os
from pathlib import Path
from nhbot.config import RAW_DIR, PROCESSED_DIR

SRC = RAW_DIR / "national"

ABBR = {'U.S.': 'United States', 'US': 'United States', 'Ala.': 'Alabama', 'Alaska': 'Alaska', 'Ariz.': 'Arizona',
'Ark.': 'Arkansas', 'Calif.': 'California', 'Colo.': 'Colorado', 'Conn.': 'Connecticut',
'Del.': 'Delaware', 'D.C.': 'District of Columbia', 'Fla.': 'Florida', 'Ga.': 'Georgia',
'Hawaii': 'Hawaii', 'Idaho': 'Idaho', 'Ill.': 'Illinois', 'Ind.': 'Indiana', 'Iowa': 'Iowa',
'Kans.': 'Kansas', 'Ky.': 'Kentucky', 'La.': 'Louisiana', 'Maine': 'Maine', 'Md.': 'Maryland',
'Mass.': 'Massachusetts', 'Mich.': 'Michigan', 'Minn.': 'Minnesota', 'Miss.': 'Mississippi',
'Mo.': 'Missouri', 'Mont.': 'Montana', 'Nebr.': 'Nebraska', 'Nev.': 'Nevada',
'N.H.': 'New Hampshire', 'N.J.': 'New Jersey', 'N.M.': 'New Mexico', 'N.Y.': 'New York',
'N.C.': 'North Carolina', 'N.D.': 'North Dakota', 'Ohio': 'Ohio', 'Okla.': 'Oklahoma',
'Ore.': 'Oregon', 'Pa.': 'Pennsylvania', 'R.I.': 'Rhode Island', 'S.C.': 'South Carolina',
'S.D.': 'South Dakota', 'Tenn.': 'Tennessee', 'Tex.': 'Texas', 'Utah': 'Utah', 'Vt.': 'Vermont',
'Va.': 'Virginia', 'Wash.': 'Washington', 'W.Va.': 'West Virginia', 'Wis.': 'Wisconsin',
'Wyo.': 'Wyoming'}

def _num(t):
    t = t.replace('$', '').replace(',', '').replace('%', '')
    try:
        return float(t)
    except ValueError:
        return None

def _lines(pdf, pages):
    out = []
    for pi in pages:
        out += (pdf.pages[pi].extract_text() or "").splitlines()
    return out

def _oneup(pdf, pages, nfields):
    """One state per line: STATE v1 v2 ... . Keeps up to nfields numbers."""
    res = {}
    for l in _lines(pdf, pages):
        toks = l.split()
        if toks and toks[0] in ABBR:
            vals = [v for v in (_num(x) for x in toks[1:]) if v is not None]
            if vals:
                res[ABBR[toks[0]]] = (vals + [None] * nfields)[:nfields]
    return res

def _twoup(pdf, pages, nfields):
    """Two states per line: split at the second state token."""
    res = {}
    for l in _lines(pdf, pages):
        toks = l.split()
        st = [i for i, t in enumerate(toks) if t in ABBR]
        if not st:
            continue
        blocks = ([toks[st[0]:st[1]], toks[st[1]:]] if len(st) >= 2 else [toks[st[0]:]])
        for b in blocks:
            if b and b[0] in ABBR:
                vals = [v for v in (_num(x) for x in b[1:]) if v is not None]
                if vals:
                    res[ABBR[b[0]]] = (vals + [None] * nfields)[:nfields]
    return res


def parse(pdf_path):
    import pdfplumber
    pdf = pdfplumber.open(pdf_path)
    burden = _oneup(pdf, [5, 6], 3)      # Table 2: total burden%, rank, per-capita
    sources = _oneup(pdf, [12, 13], 5)   # Table 7: prop, sales, indinc, corpinc, other
    percap = _twoup(pdf, [10], 2)        # Table 5: total collections per capita, rank
    propeff = _twoup(pdf, [47], 2)       # Table 33: effective property-tax rate, rank
    # --- household (individual-facing) components, per capita, for the household burden ---
    hh_prop  = _twoup(pdf, [48], 1)      # Table 34: property tax per capita
    hh_inc   = _twoup(pdf, [21], 1)      # Table 13: individual income tax per capita
    hh_sales = _twoup(pdf, [30], 1)      # Table 20: general sales tax per capita
    hh_exc   = _twoup(pdf, [46], 1)      # Table 32: excise tax per capita
    income   = _twoup(pdf, [55], 1)      # Table 41: income per capita (denominator)
    hhsize   = _twoup(pdf, [56], 2)      # Table 42: people per household (2023, 2024)

    def rank(v):                         # valid state ranks are 1..51; the U.S. row has none
        return int(v) if v and 1 <= v <= 51 else None
    def val(d, st):
        v = d.get(st, [None])[0]
        return v

    rows = []
    for st in sorted(set(ABBR.values())):        # set(): 'U.S.' and 'US' both map to United States
        b = burden.get(st, [None, None, None]); s = sources.get(st, [None] * 5)
        pc = percap.get(st, [None, None]); pe = propeff.get(st, [None, None])
        p, i, sa, e, inc = (val(hh_prop, st), val(hh_inc, st), val(hh_sales, st),
                            val(hh_exc, st), val(income, st))
        pph = hhsize.get(st, [None, None])[1]     # latest (2024) people-per-household
        basket = None if None in (p, i, sa, e) else (p + i + sa + e)
        hh_pct = round(100 * basket / inc, 2) if (basket is not None and inc) else None
        rows.append({
            "state": st,
            "burden_pct": b[0], "burden_rank": rank(b[1]),
            "collections_percap": int(pc[0]) if pc[0] else None,
            "collections_rank": rank(pc[1]),
            "prop_pct": s[0], "sales_pct": s[1], "individual_income_pct": s[2],
            "corporate_income_pct": s[3], "other_pct": s[4],
            "eff_property_rate": pe[0], "eff_property_rank": rank(pe[1]),
            "hh_property_pc": int(p) if p is not None else None,
            "hh_income_pc": int(i) if i is not None else None,
            "hh_sales_pc": int(sa) if sa is not None else None,
            "hh_excise_pc": int(e) if e is not None else None,
            "hh_income_percap": int(inc) if inc else None,
            "hh_persons_per_household": pph,
            "hh_burden_pct": hh_pct, "hh_burden_rank": None,
        })
    # assign household-burden ranks (1 = lowest) across the 50 states only
    ranked = sorted([r for r in rows if r["hh_burden_pct"] is not None
                     and r["state"] not in ("United States", "District of Columbia")],
                    key=lambda r: r["hh_burden_pct"])
    for n, r in enumerate(ranked, 1):
        r["hh_burden_rank"] = n
    return rows


def main():
    SRC.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    cands = glob.glob(str(SRC / "*FactsFigures*.pdf")) or glob.glob(str(SRC / "*.pdf"))
    if not cands:
        print(f"  (no Facts & Figures PDF in {SRC}; skipping)")
        return
    pdfs = sorted(cands, key=os.path.getmtime, reverse=True)   # newest edition wins
    rows = parse(pdfs[0])
    cols = ["state", "burden_pct", "burden_rank", "collections_percap", "collections_rank",
            "prop_pct", "sales_pct", "individual_income_pct", "corporate_income_pct",
            "other_pct", "eff_property_rate", "eff_property_rank",
            "hh_property_pc", "hh_income_pc", "hh_sales_pc", "hh_excise_pc",
            "hh_income_percap", "hh_persons_per_household", "hh_burden_pct", "hh_burden_rank"]
    with open(PROCESSED_DIR / "nh_state_comparison.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    have = sum(1 for r in rows if r["burden_pct"] is not None)
    print(f"  tax-comparison: {len(rows)} rows ({have} with burden data) from {Path(pdfs[0]).name}")
    print("  Run 'nhbot load' to ingest.")


if __name__ == "__main__":
    main()
