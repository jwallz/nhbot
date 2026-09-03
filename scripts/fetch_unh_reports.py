#!/usr/bin/env python3
"""Download the latest annual report for each NH town from the UNH Scholars
Repository (scholars.unh.edu), for the municipal (MS-535 / town-budget) layer.

WHY THIS RUNS IN YOUR OWN TERMINAL: scholars.unh.edu is blocked from Claude's
automation environment (egress proxy 403). Your real network can reach it, so
run this yourself:

    python3 scripts/fetch_unh_reports.py                # every town without a local PDF yet
    python3 scripts/fetch_unh_reports.py --limit 20     # just the next 20 (a batch)
    python3 scripts/fetch_unh_reports.py --only 3301101300,3300944260   # specific geoids

It is stdlib-only (no pip installs), resumable (skips towns whose PDF already
exists), and polite (a short delay between requests). PDFs land in
data/raw/municipal/{geoid}_{year}.pdf — exactly what `nhbot municipal` expects.
A log is written to data/raw/municipal/_download_log.csv.

After it finishes, back in Claude (or yourself):
    nhbot municipal            # extract budgets from the new PDFs (needs OCR deps)
    nhbot municipal-coverage   # refresh the loaded/needs_review/missing roster
    nhbot load                 # push coverage + budgets to the DB
"""
import argparse, csv, re, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUNI = ROOT / "data" / "raw" / "municipal"
COVERAGE = ROOT / "data" / "processed" / "nh_municipal_coverage.csv"
CROSSWALK = ROOT / "data" / "processed" / "nh_municipality_geoid_crosswalk.csv"
INDEX = "https://scholars.unh.edu/nh_town_reports/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DELAY = 1.0  # seconds between requests, be polite


def norm(s):
    s = re.sub(r",?\s*(nh|new hampshire)\s*$", "", s.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def get(url, binary=False, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "ignore")


def town_roster(args):
    """(geoid, name) list to consider. Default: every incorporated municipality
    (221 towns + 13 cities). Whether each is actually fetched is gated on whether its
    PDF already exists locally (see existing_geoids), so the default run is resumable
    and complete. --only limits to specific geoids."""
    if args.only:
        want = set(args.only.split(","))
        rows = list(csv.DictReader(open(CROSSWALK)))
        return [(r["geoid"], r["municipality"]) for r in rows if r["geoid"] in want]
    rows = list(csv.DictReader(open(CROSSWALK)))
    return [(r["geoid"], r["municipality"]) for r in rows
            if r.get("entity_type") in ("town", "city")]


def collection_slugs():
    """Map normalized-town-name -> UNH collection slug, from the master index."""
    html = get(INDEX)
    slugs = {}
    # match a collection slug wherever it appears (href="/x_nh_reports/", full URL, etc.);
    # excludes the master 'nh_town_reports' (that ends '_town_reports', not '_nh_reports').
    for slug in re.findall(r'\b([a-z0-9_]+)_nh_reports\b', html, re.I):
        slugs[norm(slug.replace("_", " "))] = slug.lower()
    return slugs


def _abs(url):
    url = url.replace("&amp;", "&")
    return url if url.startswith("http") else "https://scholars.unh.edu" + url


def latest_report_pdf(slug):
    """(year, pdf_url) for the newest report in a town's collection, or (None, None).

    A bepress collection page lists reports newest-first and carries BOTH each report's
    page link (/{slug}_nh_reports/N, title has the year) AND its direct PDF download
    (viewcontent.cgi?article=...&context={slug}_nh_reports). So one fetch is enough:
    the first PDF link is the newest report; the first titled listing gives the year.
    (hrefs are absolute; raw HTML encodes & as &amp;.)"""
    html = get(f"https://scholars.unh.edu/{slug}_nh_reports/")
    # newest year: first article-listing title that contains a 4-digit year
    year = None
    for title in re.findall(rf'{slug}_nh_reports/\d+/?"[^>]*>([^<]+)', html):
        ym = re.search(r"\b(19\d\d|20\d\d)\b", title)
        if ym:
            year = int(ym.group(1)); break
    # newest PDF: first viewcontent link for this collection
    pdfs = re.findall(
        rf'((?:https?://scholars\.unh\.edu)?/cgi/viewcontent\.cgi\?article=\d+&(?:amp;)?context={slug}_nh_reports)',
        html)
    if pdfs:
        return year, _abs(pdfs[0])
    # fallback: no direct PDF link listed -> open the newest report page and look there
    rp = re.findall(rf'{slug}_nh_reports/(\d+)/?"', html)
    if not rp:
        return year, None
    page = get(f"https://scholars.unh.edu/{slug}_nh_reports/{rp[0]}/")
    m = re.search(
        rf'((?:https?://scholars\.unh\.edu)?/cgi/viewcontent\.cgi\?article=\d+&(?:amp;)?context={slug}_nh_reports)',
        page)
    return year, (_abs(m.group(1)) if m else None)


def existing_geoids():
    out = set()
    for p in MUNI.glob("*.pdf"):
        m = re.match(r"^(\d{10})", p.name)
        if m:
            out.add(m.group(1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N downloads")
    ap.add_argument("--only", help="comma-separated geoids to fetch")
    args = ap.parse_args()

    MUNI.mkdir(parents=True, exist_ok=True)
    roster = town_roster(args)
    print(f"Resolving UNH collections for {len(roster)} town(s)...")
    slugs = collection_slugs()
    print(f"  {len(slugs)} town collections found on the UNH index.\n")

    have = existing_geoids()
    log, got, fail = [], 0, 0
    for geoid, name in roster:
        if geoid in have and not args.only:
            continue
        slug = slugs.get(norm(name))
        if not slug:
            print(f"  {name:22} NO UNH COLLECTION MATCH"); fail += 1
            log.append((geoid, name, "", "", "no_collection_match")); continue
        try:
            year, url = latest_report_pdf(slug)
            if not url:
                print(f"  {name:22} no PDF link found ({slug})"); fail += 1
                log.append((geoid, name, slug, year or "", "no_pdf_link")); continue
            time.sleep(DELAY)
            pdf = get(url, binary=True)
            fn = MUNI / f"{geoid}_{year or 'na'}.pdf"
            fn.write_bytes(pdf)
            mb = len(pdf) / 1e6
            print(f"  {name:22} {year}  {mb:5.1f} MB  -> {fn.name}")
            got += 1
            log.append((geoid, name, slug, year or "", f"ok {mb:.1f}MB"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  {name:22} DOWNLOAD ERROR: {e}"); fail += 1
            log.append((geoid, name, slug, "", f"error {e}"))
        time.sleep(DELAY)
        if args.limit and got >= args.limit:
            print(f"\nReached --limit {args.limit}."); break

    # append to the log
    newlog = not (MUNI / "_download_log.csv").exists()
    with open(MUNI / "_download_log.csv", "a", newline="") as f:
        w = csv.writer(f)
        if newlog:
            w.writerow(["geoid", "name", "slug", "year", "result"])
        w.writerows(log)
    print(f"\nDone: {got} downloaded, {fail} could not be fetched. "
          f"Log -> data/raw/municipal/_download_log.csv")
    if fail:
        print("Re-run to retry; already-downloaded towns are skipped automatically.")


if __name__ == "__main__":
    main()
