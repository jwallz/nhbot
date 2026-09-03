"""Each municipality's governing board — Select Board members (towns), Town Councilors
(council-manager towns), or Aldermen (some cities) — for the town page.

Source: NH DOT "City and Town Officials of the State of New Hampshire" directory PDF
(data/raw/nh_officials_directory_2025.pdf; published at nh.gov/government/cities-towns).
It's a grid table, one block per municipality: col 0 = Municipality (only on the block's
first row — carried forward), col 6 = Position, col 7 = Name, col 8 = Phone, col 9 = Email.

Governing-board rows are detected by Position: "Board of Selectman[, Chair]" (Selectman),
"Town Counsilor/Councilor" (Town Councilor — the directory misspells it "Counsilor"), and
"Alderman" (Alderman). Chairs are flagged (Vice-Chair is not a chair). Other officials
(Town Administrator, Road Agent, Clerk, Public Works, Mayor, Manager) are skipped.

Output: data/processed/nh_select_board.csv
        geoid, seq, role, name, is_chair, phone, email
"""
import csv, re
from nhbot.config import RAW_DIR, PROCESSED_DIR

PDF = RAW_DIR / "nh_officials_directory_2025.pdf"
CROSSWALK = PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"

_COUNCIL = re.compile(r"coun[sc][ei]l+or", re.I)     # councilor / counsilor / counselor / councillor
_ALDER   = re.compile(r"alder(man|men)", re.I)
_SELECT  = re.compile(r"select(man|men|board)", re.I)
_SKIP_MUNI = {"municipality", "town info", "municipal officials in new hampshire 2025", ""}


def _norm(s):
    s = (s or "").lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _geoids():
    return {_norm(r["municipality"]): r["geoid"]
            for r in csv.DictReader(open(CROSSWALK))
            if r["entity_type"] in ("town", "city")}


def build():
    import pdfplumber
    xw = _geoids()
    rows, cur, seq = [], None, {}
    with pdfplumber.open(PDF) as pdf:
        for pg in pdf.pages[1:]:                      # page 1 is the cover
            for tbl in pg.extract_tables():
                for r in tbl:
                    c = [(x or "").replace("\n", " ").strip() for x in r]
                    if len(c) < 8:
                        continue
                    if c[0].strip() and _norm(c[0]) not in _SKIP_MUNI and c[6].strip() != "Position":
                        cur = c[0].strip()
                    pos, name = c[6].strip(), c[7].strip()
                    if not name or pos == "Position":
                        continue
                    if _COUNCIL.search(pos):   role = "Town Councilor"
                    elif _ALDER.search(pos):   role = "Alderman"
                    elif _SELECT.search(pos):  role = "Selectman"
                    else:                      continue
                    g = xw.get(_norm(cur))
                    if not g:
                        continue
                    is_chair = bool(re.search(r"chair", pos, re.I)) and not re.search(r"vice", pos, re.I)
                    seq[g] = seq.get(g, 0) + 1
                    rows.append({"geoid": g, "seq": seq[g], "role": role,
                                 "name": name, "is_chair": "true" if is_chair else "false",
                                 "phone": c[8].strip(),
                                 "email": (c[9].strip() if len(c) > 9 else "")})

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outp = PROCESSED_DIR / "nh_select_board.csv"
    with open(outp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["geoid", "seq", "role", "name", "is_chair", "phone", "email"])
        w.writeheader(); w.writerows(rows)

    from collections import Counter
    roles = Counter(r["role"] for r in rows)
    print("=== select board / governing bodies ===")
    print(f"  {len(rows)} members across {len({r['geoid'] for r in rows})}/234 municipalities")
    print(f"  roles: {dict(roles)}")
    print(f"  -> {outp.name}")
    return rows


def main():
    build()


if __name__ == "__main__":
    main()
