"""NH state legislators (House + Senate) and the town→district mappings that place
each town's representatives and senator on its page.

Sources (downloaded to data/raw/legislature/ — gc.nh.gov is not reachable from the
cloud sandbox, so John pulls them in his own terminal):
  * members.txt              — the General Court roster: BOTH senators (LegislativeBody=S)
                               and representatives (H), each with County + District, name,
                               party, town of residence, title, email, phone, elected status.
  * house_662-5.htm          — RSA 662:5, State Representative Districts: per county, each
                               "District No. N <towns/wards> <#reps>". Includes BASE and
                               FLOTERIAL districts (a town is in a base district AND, often,
                               a larger floterial one — so it has several reps).
  * senate_662-3.htm         — RSA 662:3, the 24 Senate districts and their towns/wards.

The House list is space-separated with no delimiter between multi-word town names, so
towns are segmented by greedy longest-match against the known NH place names (county-scoped),
with "<City> Ward <n>" handled explicitly and directional abbreviations (E./W./N./S.)
expanded. Cities appear by ward, so a city correctly maps to many House districts and,
when split, several Senate districts.

Join model (done at query time): a town's REPS = legislators (body=house) whose
(county, district) is any of the town's House districts (base + floterial); a town's
SENATOR(S) = legislators (body=senate) whose district is any of the town's Senate districts.

Outputs (data/processed/):
  nh_legislators.csv            id, body, county, district, first_name, last_name, party,
                                town_residence, title, email, phone, elected_status
  nh_town_house_district.csv    geoid, county, district
  nh_town_senate_district.csv   geoid, senate_district
"""
import csv, re, html
from nhbot.config import RAW_DIR, PROCESSED_DIR

LEG = RAW_DIR / "legislature"
CROSSWALK = PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"


def _norm(s):
    s = s.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _places():
    """Return (place_geoid, by_county, cities) dictionaries from the crosswalk.
    place_geoid: normalized place name -> geoid (None for unincorporated grants).
    by_county: normalized county -> set of its normalized place names.
    cities: normalized city name -> geoid."""
    rows = list(csv.DictReader(open(CROSSWALK)))
    place_geoid, by_county, cities = {}, {}, {}
    for r in rows:
        nm = _norm(r["municipality"])
        g = r["geoid"] if r["entity_type"] != "unincorporated" else None
        place_geoid[nm] = g
        by_county.setdefault(_norm(r["county_name"]), set()).add(nm)
        if r["entity_type"] == "city":
            cities[nm] = r["geoid"]
    # statute spellings for a couple of unincorporated grants (consume as units; no geoid)
    place_geoid.setdefault("atkinson and gilmanton academy grant", None)
    place_geoid.setdefault("erving s location", None)
    return place_geoid, by_county, cities


_DIR = {r"\bE\. ": "East ", r"\bW\. ": "West ", r"\bN\. ": "North ", r"\bS\. ": "South "}


def _expand_dir(s):
    for k, v in _DIR.items():
        s = re.sub(k, v, s)
    return s


def _segment(tokens, county_norm, place_geoid, by_county, cities):
    """Greedy longest-match segmentation of a space-separated town/ward token list into
    geoids. County-scoped names win first, then any NH place. '<City> Ward <n>' -> city."""
    scope = by_county.get(county_norm, set())
    out, i, n = [], 0, len(tokens)
    while i < n:
        if i + 2 < n and tokens[i + 1].lower() == "ward" and re.match(r"\d+$", tokens[i + 2]):
            c = _norm(tokens[i])
            if c in cities:
                out.append(cities[c]); i += 3; continue
        hit = None
        for L in (5, 4, 3, 2, 1):
            if i + L <= n:
                cand = _norm(" ".join(tokens[i:i + L]))
                if cand in scope or cand in place_geoid:
                    hit = (cand, L); break
        if hit:
            g = place_geoid.get(hit[0])
            if g:
                out.append(g)
            i += hit[1]
        else:
            i += 1   # unmatched stray token (grant fragment) — skip
    return out


def parse_members():
    rows = list(csv.DictReader(open(LEG / "members.txt", encoding="cp1252"), delimiter="\t"))
    out = []
    for i, r in enumerate(rows, 1):
        body = "senate" if r["LegislativeBody"].strip() == "S" else "house"
        dist = r["District"].strip()
        out.append({
            "id": i, "body": body,
            "county": r["County"].strip(),
            "district": int(dist) if dist.isdigit() else None,
            "first_name": r["FirstName"].strip(), "last_name": r["LastName"].strip(),
            "party": r["party"].strip().upper()[:1] or "",
            "town_residence": r["city"].strip(),
            "title": r["PersonTitle"].strip(),
            "email": r["WorkEmail"].strip(),
            "phone": r["Phone"].strip(),
            "elected_status": r["electedStatus"].strip(),
        })
    return out


def parse_house(place_geoid, by_county, cities):
    t = html.unescape(re.sub(r"<[^>]+>", " ",
        open(LEG / "house_662-5.htm", encoding="cp1252", errors="replace").read()))
    t = re.sub(r"\s+", " ", t)
    t = t[t.find("as follows:") + 11:]
    out = []
    for roman, county, body in re.findall(
            r"([IVX]+)\.\s+([A-Za-zö]+)\s+County\s+(.*?)(?=[IVX]+\.\s+[A-Za-zö]+\s+County|$)", t):
        cn = _norm(county)
        chunks = re.split(r"District No\.\s*(\d+)\s*", body)
        for k in range(1, len(chunks), 2):
            dno = int(chunks[k])
            m = re.search(r"(.*?)(\d+)\s*$", chunks[k + 1].strip())   # towns ... <#reps>
            if not m:
                continue
            for g in _segment(_expand_dir(m.group(1)).split(), cn, place_geoid, by_county, cities):
                out.append((g, county, dno))
    return sorted(set(out))


def parse_senate(place_geoid, cities):
    t = html.unescape(re.sub(r"<[^>]+>", " ",
        open(LEG / "senate_662-3.htm", encoding="cp1252", errors="replace").read()))
    t = re.sub(r"\s+", " ", t)
    out = []
    for dno, lst in re.findall(
            r"[Ss]enatorial district number (\d+) is constituted of (.*?)(?=[Ss]enatorial district number|\Z)", t):
        dno = int(dno)
        lst = lst.split(".")[0]   # cut terminal period (drops trailing roman numeral / amendment text)
        for wm in re.finditer(r"wards?\s+[\d,\s]*?(?:and\s+\d+\s+)?in\s+([A-Z][a-z]+)", lst):
            c = _norm(wm.group(1))
            if c in cities:
                out.append((cities[c], dno))
        lst = re.sub(r"wards?\s+[\d,\s]*?(?:and\s+\d+\s+)?in\s+[A-Z][a-z]+", "", lst)
        for tok in re.split(r",\s*", lst):
            tok = re.sub(r"^\s*and\s+", "", tok.strip())
            g = place_geoid.get(_norm(tok)) if tok else None
            if g:
                out.append((g, dno))
    return sorted(set(out))


def build():
    place_geoid, by_county, cities = _places()
    legislators = parse_members()
    house = parse_house(place_geoid, by_county, cities)
    senate = parse_senate(place_geoid, cities)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    with open(PROCESSED_DIR / "nh_legislators.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "body", "county", "district", "first_name",
              "last_name", "party", "town_residence", "title", "email", "phone", "elected_status"])
        w.writeheader(); w.writerows(legislators)
    with open(PROCESSED_DIR / "nh_town_house_district.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["geoid", "county", "district"]); w.writerows(house)
    with open(PROCESSED_DIR / "nh_town_senate_district.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["geoid", "senate_district"]); w.writerows(senate)

    from collections import Counter
    bod = Counter(l["body"] for l in legislators)
    active = [l for l in legislators if l["elected_status"] != "Former"]
    htowns = len(set(g for g, _, _ in house)); stowns = len(set(g for g, _ in senate))
    print("=== legislature ===")
    print(f"  legislators: {len(legislators)} ({bod['house']} house, {bod['senate']} senate; "
          f"{len(legislators)-len(active)} 'Former' will be hidden on town pages)")
    print(f"  house town→district rows: {len(house)}  covering {htowns} towns/cities")
    print(f"  senate town→district rows: {len(senate)}  covering {stowns} towns/cities")
    return legislators, house, senate


def main():
    build()


if __name__ == "__main__":
    main()
