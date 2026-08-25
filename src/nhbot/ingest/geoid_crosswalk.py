#!/usr/bin/env python3
"""
NHbot -- build the canonical municipality -> Census GEOID crosswalk.

NH municipalities are Census County Subdivisions (MCDs). The 10-digit GEOID
(state 33 + county FIPS + cousub FIPS) is the durable join key that replaces
name-based joins across DRA / DOE / SOS / ACS.

Inputs:
  raw/geo/2023_Gaz_cousubs_national.txt         Census 2023 Gazetteer (national)
  raw/2025/2025-municipal-and-village-district-tax-rates.xlsx   canonical name list

Output:
  phase0/nh_municipality_geoid_crosswalk.csv    one row per municipality (234) +
                                                unincorporated place (25) = 259

Name reconciliation notes:
  - Census appends a type token: "Amherst town", "Berlin city",
    "Cambridge township", "Beans grant", "Hale's location".
  - Census drops apostrophes and uses "and" for "&".
  - Hart's Location is an incorporated TOWN whose name ends in "Location".
  - Livermore is a Census "town" (pop 0) but a DRA unincorporated place.
  Entity_type here follows OUR canonical classification, not Census's.

Dependencies: openpyxl
"""
import openpyxl, re, csv, os

from nhbot.config import RAW_DIR, PROCESSED_DIR
RAW  = str(RAW_DIR)
OUT  = str(PROCESSED_DIR)

CITIES = {"Berlin","Claremont","Concord","Dover","Franklin","Keene","Laconia",
          "Lebanon","Manchester","Nashua","Portsmouth","Rochester","Somersworth"}

NH_COUNTIES = {"001":"Belknap","003":"Carroll","005":"Cheshire","007":"Coos",
               "009":"Grafton","011":"Hillsborough","013":"Merrimack",
               "015":"Rockingham","017":"Strafford","019":"Sullivan"}

# Explicit GEOID overrides for the handful Census names can't be normalized to.
GEOID_OVERRIDE = {
    "Atkinson & Gilmanton": "3300702420",   # "Atkinson and Gilmanton Academy grant"
    "Wentworth's Location": "3300780740",    # "Wentworth location" (no apostrophe-s)
    "Erving's Grant": "3300725180",          # Census "Ervings location" (grant vs location)
}

INCORP_SUFFIX = ("town", "city")            # only these are Census type tokens to strip
def norm(s): return re.sub(r"\s+"," ",str(s)).strip() if s is not None else None
def canon(n):
    if n is None: return None
    n = re.sub(r"\s*\(U\)\s*$","",norm(n))
    return {"Atkinson & Gilmanton Academy Grant":"Atkinson & Gilmanton",
            "Wentworth Location":"Wentworth's Location"}.get(n,n)
def key(name):
    n = name.lower().replace("&","and").replace("'","").replace(".","")
    return re.sub(r"\s+"," ",n).strip()

def load_canonical():
    wb = openpyxl.load_workbook(f"{RAW}/2025/2025-municipal-and-village-district-tax-rates.xlsx",
                               read_only=True, data_only=True)
    ws = wb["2025 Municipal Tax Rates"]; munis=set(); uninc=set()
    for r in ws.iter_rows(min_row=6, values_only=True):
        m = norm(r[0])
        if not m: continue
        if m.lower().startswith(("total","source","note","the ","municipal tax",
                                 "new hampshire","department","revenue")): continue
        if r[8] is None and r[4] is None: continue
        if m.endswith("(U)"): uninc.add(canon(m))
        elif m == "Penacook": pass
        else: munis.add(m)
    wb.close(); return munis, uninc

def load_census():
    rows=[]
    with open(f"{RAW}/geo/2023_Gaz_cousubs_national.txt", encoding="latin-1") as f:
        hdr=[h.strip() for h in f.readline().split("\t")]
        for line in f:
            d=dict(zip(hdr,[p.strip() for p in line.rstrip("\n").split("\t")]))
            if d.get("USPS")=="NH" and d["NAME"]!="County subdivisions not defined":
                rows.append(d)
    return rows

def census_base(name):
    toks=name.split()
    if toks[-1].lower() in INCORP_SUFFIX:      # strip only town/city
        return " ".join(toks[:-1])
    return name

def main():
    munis, uninc = load_canonical()
    cen = load_census()
    by_geoid = {d["GEOID"]: d for d in cen}

    # lookup: normalized census name -> census row
    cen_by_key = {}
    for d in cen:
        cen_by_key.setdefault(key(census_base(d["NAME"])), d)
        cen_by_key.setdefault(key(d["NAME"]), d)          # also full form (grants/purchases)

    rows=[]; unmatched=[]
    for name in sorted(munis | uninc):
        etype = ("unincorporated" if name in uninc
                 else "city" if name in CITIES else "town")
        d = None; method=""
        if name in GEOID_OVERRIDE:
            d = by_geoid.get(GEOID_OVERRIDE[name]); method="override"
        if d is None:
            d = cen_by_key.get(key(name)); method="name"
        if d is None:
            # unincorporated stored bare ("Cambridge") vs census "Cambridge township"
            cand=[c for c in cen if key(census_base_all(c["NAME"]))==key(name)]
            if len(cand)==1: d=cand[0]; method="base"
        if d is None:
            unmatched.append(name); continue
        g=d["GEOID"]
        rows.append(dict(
            municipality=name, entity_type=etype, geoid=g,
            state_fips=g[:2], county_fips=g[2:5],
            county_name=NH_COUNTIES.get(g[2:5],""), cousub_fips=g[5:],
            census_name=d["NAME"], ansicode=d["ANSICODE"],
            aland_sqmi=d["ALAND_SQMI"], awater_sqmi=d["AWATER_SQMI"],
            intptlat=d["INTPTLAT"], intptlon=d["INTPTLONG"],
            match_method=method, source="2023_Gaz_cousubs_national.txt"))

    os.makedirs(OUT, exist_ok=True)
    path=os.path.join(OUT,"nh_municipality_geoid_crosswalk.csv")
    with open(path,"w",newline="") as f:
        w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    geoids=[r["geoid"] for r in rows]
    print(f"wrote {path}")
    print(f"rows={len(rows)}  cities={sum(1 for r in rows if r['entity_type']=='city')}  "
          f"towns={sum(1 for r in rows if r['entity_type']=='town')}  "
          f"unincorporated={sum(1 for r in rows if r['entity_type']=='unincorporated')}")
    print(f"unique GEOIDs: {len(set(geoids))}  (dupes: {len(geoids)-len(set(geoids))})")
    print(f"match methods: " + ", ".join(f"{m}={sum(1 for r in rows if r['match_method']==m)}"
                                          for m in ('name','base','override')))
    if unmatched: print("UNMATCHED (need manual GEOID):", unmatched)
    else: print("all canonical municipalities + unincorporated places mapped to a GEOID")

# helper that strips ANY trailing type token (for unincorporated base compare)
_ALLTYPES=("town","city","grant","purchase","township","location","gore","plantation")
def census_base_all(name):
    toks=name.split()
    return " ".join(toks[:-1]) if toks[-1].lower() in _ALLTYPES else name

if __name__=="__main__":
    main()
