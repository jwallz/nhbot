"""NH municipal website URLs — one official government website per municipality,
keyed to the canonical Census GEOID for display at the top of each town page.

Primary source: GRANIT's "NH Municipal Sites (Towns)" index
(https://granit.unh.edu/pages/nh-municipal-sites-towns), a UNH-maintained,
politically-neutral listing of each town's municipal website. The page is a
JavaScript (Ember) app whose rendered list is captured to
data/raw/municipal_websites/granit_towns.json (name, county, url per town).

That rendered list caps out and omits ~17 municipalities (Portsmouth is a city,
so it isn't on the "Towns" page at all; the rest are towns the widget didn't
render). Those are filled from GAPFILL below — each town's official website
confirmed by direct search — so coverage is complete. Every output row carries a
`source` of 'granit' or 'verified' so the provenance stays transparent.

Non-website GRANIT values are dropped: "No" (unincorporated grants/purchases with
no municipal site), state ELMI community-profile PDFs (nhes.nh.gov), and the
NH Municipal Association org page (nhmunicipal.org) — none of these is a town's
own website.

Output: data/processed/nh_municipality_website.csv
        geoid, name, website, source
"""
import csv, json, re, unicodedata
from nhbot.config import RAW_DIR, PROCESSED_DIR

GRANIT_JSON = RAW_DIR / "municipal_websites" / "granit_towns.json"
CROSSWALK   = PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"

# Municipalities the GRANIT widget omits, with the official website confirmed by
# direct search (2026-08). Keyed by canonical (crosswalk) name.
GAPFILL = {
    "Auburn":        "https://www.auburnnh.gov/",
    "Brookline":     "https://www.brooklinenh.gov/",
    "Durham":        "https://www.durhamnh.gov/",
    "Exeter":        "https://www.exeternh.gov/",
    "Fremont":       "https://www.fremont.nh.gov/",
    "Groton":        "https://grotonnh.gov/",
    "Hudson":        "https://www.hudsonnh.gov/",
    "Jefferson":     "https://jeffersonnh.org/",
    "Kingston":      "https://www.kingstonnh.gov/",
    "Lyndeborough":  "https://lyndeborough.nh.us/",
    "Moultonborough":"https://www.moultonboroughnh.gov/",
    "Nottingham":    "https://www.nottingham-nh.gov/",
    "Ossipee":       "https://www.ossipee.org/",
    "Portsmouth":    "https://www.portsmouthnh.gov/",
    "Rye":           "https://www.ryenh.gov/",
    "Swanzey":       "https://www.swanzeynh.gov/",
    "Tuftonboro":    "https://www.tuftonboronh.gov/",
}


def norm(s):
    """Normalize a municipality name for matching across sources (drop case,
    punctuation, apostrophes, and the town/city/of/nh boilerplate)."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(town|city|of|nh)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_url(u):
    """Return a usable municipal website, or None. Drops 'No', state ELMI
    profile PDFs, the NHMA org page, and strips 'Online Mapping' text that the
    GRANIT list sometimes glues onto the URL when a space is missing."""
    if not u or u.strip().lower() == "no":
        return None
    u = re.sub(r"Online.*$", "", u.strip())        # "...gov/Online Mapping:" glue
    if "nhes.nh.gov" in u or "nhmunicipal.org" in u:
        return None                                 # not the town's own website
    return u or None


def load_crosswalk():
    """canonical name -> (geoid, canonical_name); also norm-key -> canonical."""
    by_geoid, by_norm = {}, {}
    with open(CROSSWALK) as f:
        for r in csv.DictReader(f):
            by_geoid[r["geoid"]] = r["municipality"]
            by_norm[norm(r["municipality"])] = (r["geoid"], r["municipality"])
    return by_geoid, by_norm


def build():
    by_geoid, by_norm = load_crosswalk()
    granit = json.load(open(GRANIT_JSON))

    out = {}            # geoid -> (name, website, source)
    unmatched = []      # GRANIT names that don't resolve to a GEOID (info only)

    # 1) GRANIT rows -> geoid
    for g in granit:
        url = clean_url(g["url"])
        hit = by_norm.get(norm(g["name"]))
        if not hit:
            unmatched.append((g["name"], url))
            continue
        geoid, canon = hit
        if url:                                     # only keep rows with a real site
            out[geoid] = (canon, url, "granit")

    # 2) fill widget-omitted municipalities from the verified set
    n_gap = 0
    for name, url in GAPFILL.items():
        hit = by_norm.get(norm(name))
        if not hit:
            continue
        geoid, canon = hit
        if geoid not in out:
            out[geoid] = (canon, url, "verified")
            n_gap += 1

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outp = PROCESSED_DIR / "nh_municipality_website.csv"
    with open(outp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["geoid", "name", "website", "source"])
        for geoid in sorted(out, key=lambda k: out[k][0]):
            name, url, src = out[geoid]
            w.writerow([geoid, name, url, src])

    total_muni = len(by_geoid)
    with_site = len(out)
    print(f"=== municipal websites ===")
    print(f"  GRANIT rows read:        {len(granit)}")
    print(f"  matched with a website:  {with_site - n_gap} (granit)")
    print(f"  gap-filled (verified):   {n_gap}")
    print(f"  total geoids with site:  {with_site} / {total_muni} municipalities")
    if unmatched:
        named = [n for n, _ in unmatched]
        print(f"  GRANIT names not matched (unincorporated/no site): {len(unmatched)}")
    print(f"  {with_site} rows -> {outp.name}")
    return out


def main():
    build()


if __name__ == "__main__":
    main()
