"""nhbot command-line entry point.

    nhbot crosswalk       build municipality -> GEOID crosswalk
    nhbot dra-official    ingest DRA official equalized rates (all years in data/raw)
    nhbot dra-estimate    compute current-year equalized-rate ESTIMATE
    nhbot load            load processed CSVs into Postgres (NHBOT_DSN)
    nhbot all             the core pipeline: crosswalk -> dra-official -> dra-estimate -> load
    nhbot boundaries      build map geometry from the Census cb shapefile (needs '.[geo]')
    nhbot municipal-websites  build the geoid->official-website CSV (from data/raw/municipal_websites)
    nhbot map-labels      precompute in-region town-map label anchors (needs '.[geo]')
    nhbot municipal-profile   build form-of-government + basics CSV (from Wikipedia capture)
    nhbot town-history        build town-history snippet CSV (from Wikipedia capture)
    nhbot municipal-coverage  build MS-535 / town-budget coverage roster (what's loaded/missing)
    nhbot legislature         build legislators + town→house/senate district maps (gc.nh.gov)
    nhbot select-board        build municipal governing boards (NH DOT officials directory PDF)
    nhbot state-fiscal        build state budget + revenue CSVs (LBA HB1 Excel + DAS revenue PDFs in data/raw/state)
"""
import argparse
from nhbot.ingest import geoid_crosswalk, dra_official, dra_estimate
from nhbot.db import load as db_load

# core pipeline (no optional deps)
CORE = {
    "crosswalk":    geoid_crosswalk.main,
    "dra-official": dra_official.main,
    "dra-estimate": dra_estimate.main,
    "load":         db_load.main,
}

def _boundaries():
    from nhbot.ingest import boundaries   # lazy import: requires the [geo] extra
    boundaries.main()

def _doe_schools():
    from nhbot.ingest import doe_schools
    doe_schools.main()

def _doe_finance():
    from nhbot.ingest import doe_finance
    doe_finance.main()

def _municipal():
    from nhbot.ingest import municipal
    municipal.main()

def _municipal_websites():
    from nhbot.ingest import municipal_websites
    municipal_websites.main()

def _map_labels():
    from nhbot.ingest import map_labels   # needs the [geo] extra: shapely
    map_labels.main()

def _municipal_profile():
    from nhbot.ingest import municipal_profile
    municipal_profile.main()

def _town_history():
    from nhbot.ingest import town_history
    town_history.main()

def _municipal_coverage():
    from nhbot.ingest import municipal_coverage
    municipal_coverage.main()

def _legislature():
    from nhbot.ingest import legislature
    legislature.main()

def _select_board():
    from nhbot.ingest import select_board
    select_board.main()

def _state_fiscal():
    from nhbot.ingest import state_fiscal
    state_fiscal.main()

def _tax_comparison():
    from nhbot.ingest import tax_comparison
    tax_comparison.main()

def _valuation():
    from nhbot.ingest import valuation
    valuation.main()

EXTRA = {"boundaries": _boundaries, "doe-schools": _doe_schools,
         "doe-finance": _doe_finance, "municipal": _municipal,
         "municipal-websites": _municipal_websites, "map-labels": _map_labels,
         "municipal-profile": _municipal_profile, "town-history": _town_history,
         "municipal-coverage": _municipal_coverage, "legislature": _legislature,
         "select-board": _select_board, "state-fiscal": _state_fiscal,
         "tax-comparison": _tax_comparison, "valuation": _valuation}
STEPS = {**CORE, **EXTRA}

def main(argv=None):
    p = argparse.ArgumentParser(prog="nhbot", description="NH civic municipal dataset pipeline")
    p.add_argument("command", choices=list(STEPS) + ["all"])
    args = p.parse_args(argv)
    order = list(CORE) if args.command == "all" else [args.command]
    for name in order:
        print(f"\n=== nhbot {name} ===")
        STEPS[name]()

if __name__ == "__main__":
    main()
