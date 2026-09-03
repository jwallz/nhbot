"""Per-municipality profile basics — form of government, governing body, year
incorporated, 2020 population — for the town-page header.

Source: the "List of municipalities in New Hampshire" table on Wikipedia
(captured to data/raw/wikipedia/nh_municipalities_wiki.json: name, type, county,
gov, pop2020, land, year). Wikipedia's government column reliably gives the
structural form (Mayor-council / Council-manager / Town council / Town meeting)
and flags "town manager" / "ballot initiative", but it does NOT mark every SB2
(official-ballot) town — so SB2 is set True only where Wikipedia says so here,
and the town-history pass (town_history.py, same Wikipedia articles) upgrades
more town-meeting towns to SB2 where their article states it. A town-meeting
town is never labelled "traditional"; absent a positive SB2 signal it stays a
plain "Town meeting".

Land area is NOT taken from here — the Census gazetteer value (municipality.aland_sqmi)
stays canonical.

Output: data/processed/nh_municipality_profile.csv
        geoid, name, form_of_government, governing_body, sb2, year_incorporated, population_2020
"""
import csv, json, re, unicodedata
from nhbot.config import RAW_DIR, PROCESSED_DIR

WIKI = RAW_DIR / "wikipedia" / "nh_municipalities_wiki.json"
CROSSWALK = PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify(gov, is_city):
    """(form_of_government, governing_body, sb2) from Wikipedia's gov string."""
    g = gov.lower()
    if "mayor" in g:
        return "Mayor–council", "Mayor & city council", False
    if "council-manager" in g or "council manager" in g:
        body = "City council & manager" if is_city else "Town council & manager"
        return "Council–manager", body, False
    if g.strip() == "town council":
        return "Town council", "Town council", False
    # Town-meeting variants. NOTE: the SB2 / official-ballot vs. traditional split
    # is intentionally NOT derived here — neither the Wikipedia list column nor the
    # town articles mark it reliably (e.g. Rye is SB2 but unflagged). Every
    # town-meeting town is shown simply as "Town meeting" until an authoritative
    # SB2 roster (DRA/NHMA) is sourced; sb2 stays False so nothing is mislabelled.
    if "town manager" in g:
        return "Town meeting", "Select board & town manager", False
    return "Town meeting", "Select board", False


def load_crosswalk():
    by_norm = {}
    with open(CROSSWALK) as f:
        for r in csv.DictReader(f):
            by_norm[norm(r["municipality"])] = (r["geoid"], r["municipality"])
    return by_norm


def build():
    by_norm = load_crosswalk()
    wiki = json.load(open(WIKI))
    rows, unmatched = [], []
    for w in wiki:
        hit = by_norm.get(norm(w["name"]))
        if not hit:
            unmatched.append(w["name"]); continue
        geoid, canon = hit
        is_city = w["type"].lower().startswith("city")
        form, body, sb2 = classify(w["gov"], is_city)
        year = int(w["year"]) if w["year"].isdigit() else None
        pop = int(w["pop2020"]) if w["pop2020"].isdigit() else None
        rows.append({"geoid": geoid, "name": canon, "form_of_government": form,
                     "governing_body": body, "sb2": "true" if sb2 else "false",
                     "year_incorporated": year or "", "population_2020": pop or ""})

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outp = PROCESSED_DIR / "nh_municipality_profile.csv"
    with open(outp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["geoid", "name", "form_of_government",
              "governing_body", "sb2", "year_incorporated", "population_2020"])
        w.writeheader(); w.writerows(rows)

    from collections import Counter
    forms = Counter(r["form_of_government"] for r in rows)
    sb2n = sum(1 for r in rows if r["sb2"] == "true")
    print("=== municipal profile ===")
    print(f"  matched: {len(rows)} / {len(wiki)} wiki rows")
    print(f"  forms: {dict(forms)}  | SB2 flagged (wiki only): {sb2n}")
    if unmatched:
        print(f"  unmatched names: {unmatched}")
    print(f"  {len(rows)} rows -> {outp.name}")
    return rows


def main():
    build()


if __name__ == "__main__":
    main()
