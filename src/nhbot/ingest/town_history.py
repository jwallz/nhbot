"""Short town-history snippet per municipality, for the town-page About section.

Source: each town's Wikipedia article (en.wikipedia.org/wiki/<Town>,_New_Hampshire).
A neutral 2-3 sentence history (founding/incorporation, namesake, a notable fact)
was extracted per town and captured to data/raw/wikipedia/town_history.json
(list of {geoid, name, history, source_url}). This module validates and writes
the processed CSV the loader consumes. Attribution: each row keeps its Wikipedia
source_url, shown on the page.

Annual refresh: re-run the extraction (WebFetch each town's Wikipedia article),
overwrite the raw JSON, then run `nhbot town-history`.

Output: data/processed/nh_town_history.csv
        geoid, name, history, source_url, sb2
(`sb2` is carried for the loader's optional SB2 upgrade; left blank here because
Wikipedia articles do not reliably state SB2 status.)
"""
import csv, json
from nhbot.config import RAW_DIR, PROCESSED_DIR

RAW = RAW_DIR / "wikipedia" / "town_history.json"


def build():
    data = json.load(open(RAW))
    rows, empty = [], 0
    seen = set()
    for r in data:
        geoid = r["geoid"]
        if geoid in seen:
            continue
        seen.add(geoid)
        hist = (r.get("history") or "").strip()
        if not hist:
            empty += 1
        rows.append({"geoid": geoid, "name": r.get("name", ""),
                     "history": hist, "source_url": r.get("source_url", ""),
                     "sb2": ""})

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outp = PROCESSED_DIR / "nh_town_history.csv"
    with open(outp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["geoid", "name", "history", "source_url", "sb2"])
        w.writeheader(); w.writerows(rows)

    print("=== town history ===")
    print(f"  {len(rows)} snippets ({empty} empty) -> {outp.name}")
    return rows


def main():
    build()


if __name__ == "__main__":
    main()
