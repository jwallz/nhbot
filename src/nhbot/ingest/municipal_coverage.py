"""MS-535 / town-budget coverage roster — which of the 234 municipalities we
have a parsed town budget for, which were attempted but need review, and which
are still missing. This is the "what's left to gather" tracker for the municipal
(town-side) budget layer, whose source is each town's annual report in the UNH
Scholars Repository (see municipal.py).

Status per town:
  * loaded        — has line items in nh_municipal_expenditure.csv (we extracted a
                    DRA budget form). `kind` records actual (MS-535) vs appropriation;
                    `year` and `source` come from the extracted rows.
  * needs_review  — a report was found but no standard form parsed (name-only
                    summaries, the cities' GASB format, OCR failures). Recorded in the
                    manifest so we don't re-chase it blindly.
  * missing       — no report gathered yet (the default for everything else).

Inputs:
  data/processed/nh_municipality_geoid_crosswalk.csv   (all 234 geoids + names)
  data/processed/nh_municipal_expenditure.csv          (parsed budgets -> 'loaded')
  data/raw/municipal/coverage_manifest.csv  (OPTIONAL, hand/agent-maintained:
        geoid,status,kind,year,source,note — for needs_review / attempted towns)

Output: data/processed/nh_municipal_coverage.csv
        geoid, name, ms535_status, ms535_kind, ms535_year, ms535_source
"""
import csv
from collections import Counter
from nhbot.config import RAW_DIR, PROCESSED_DIR

CROSSWALK  = PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"
EXPEND     = PROCESSED_DIR / "nh_municipal_expenditure.csv"
STATUS     = PROCESSED_DIR / "nh_municipal_status.csv"          # per-run extractor outcomes
MANIFEST   = RAW_DIR / "municipal" / "coverage_manifest.csv"    # optional hand overrides


def _load_expenditure():
    """geoid -> (kind, year, source) from parsed budget rows. Prefer 'actual'
    (a true MS-535) over 'appropriation' when a town has both; newest year wins."""
    out = {}
    try:
        rows = list(csv.DictReader(open(EXPEND)))
    except FileNotFoundError:
        return out
    for r in rows:
        g = r["geoid"]
        year = int(r["year"]) if r.get("year") else None
        kind = "actual" if (r.get("kind") == "actual") else (r.get("kind") or None)
        cand = (kind, year, r.get("source"))
        cur = out.get(g)
        if cur is None:
            out[g] = cand; continue
        # prefer actual over appropriation, then newer year
        rank = lambda k: 1 if k == "actual" else 0
        if (rank(kind), year or 0) > (rank(cur[0]), cur[1] or 0):
            out[g] = cand
    return out


def _load_status():
    """Non-loaded outcomes (needs_review/error) from the extractor's last run."""
    out = {}
    try:
        rows = list(csv.DictReader(open(STATUS)))
    except FileNotFoundError:
        return out
    for r in rows:
        if (r.get("status") or "") == "loaded":
            continue   # 'loaded' is taken authoritatively from the expenditure CSV
        out[r["geoid"]] = {
            "status": (r.get("status") or "").strip() or "needs_review",
            "kind":   (r.get("kind") or "").strip() or None,
            "year":   int(r["year"]) if str(r.get("year", "")).strip().isdigit() else None,
            "source": (r.get("note") or "").strip() or None,
        }
    return out


def _load_manifest():
    out = {}
    try:
        rows = list(csv.DictReader(open(MANIFEST)))
    except FileNotFoundError:
        return out
    for r in rows:
        out[r["geoid"]] = {
            "status": (r.get("status") or "").strip() or "needs_review",
            "kind":   (r.get("kind") or "").strip() or None,
            "year":   int(r["year"]) if r.get("year", "").strip().isdigit() else None,
            "source": (r.get("source") or "").strip() or None,
        }
    return out


def build():
    # only the 234 incorporated municipalities (221 towns + 13 cities) file a
    # town budget; unincorporated places/grants have no town government.
    towns = [t for t in csv.DictReader(open(CROSSWALK))
             if t.get("entity_type") in ("town", "city")]
    parsed = _load_expenditure()
    status = _load_status()
    manifest = _load_manifest()
    # precedence for non-loaded: hand manifest overrides the extractor's status file
    nonloaded = {**status, **manifest}

    rows = []
    for t in towns:
        g, name = t["geoid"], t["municipality"]
        if g in parsed:
            kind, year, source = parsed[g]
            rows.append({"geoid": g, "name": name, "ms535_status": "loaded",
                         "ms535_kind": kind or "", "ms535_year": year or "",
                         "ms535_source": source or ""})
        elif g in nonloaded:
            m = nonloaded[g]
            rows.append({"geoid": g, "name": name, "ms535_status": m["status"],
                         "ms535_kind": m["kind"] or "", "ms535_year": m["year"] or "",
                         "ms535_source": m["source"] or ""})
        else:
            rows.append({"geoid": g, "name": name, "ms535_status": "missing",
                         "ms535_kind": "", "ms535_year": "", "ms535_source": ""})

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outp = PROCESSED_DIR / "nh_municipal_coverage.csv"
    with open(outp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["geoid", "name", "ms535_status",
              "ms535_kind", "ms535_year", "ms535_source"])
        w.writeheader(); w.writerows(rows)

    status = Counter(r["ms535_status"] for r in rows)
    kinds = Counter(r["ms535_kind"] for r in rows if r["ms535_status"] == "loaded")
    print("=== municipal (MS-535) coverage ===")
    print(f"  {len(rows)} towns: " + ", ".join(f"{k} {v}" for k, v in status.most_common()))
    if kinds:
        print(f"  loaded by kind: " + ", ".join(f"{k or '?'} {v}" for k, v in kinds.most_common()))
    missing = [r["name"] for r in rows if r["ms535_status"] == "missing"]
    if missing:
        print(f"  still to gather ({len(missing)}): {', '.join(missing[:12])}"
              + (" ..." if len(missing) > 12 else ""))
    print(f"  -> {outp.name}")
    return rows


def main():
    build()


if __name__ == "__main__":
    main()
