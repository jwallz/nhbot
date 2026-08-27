"""nhbot command-line entry point.

    nhbot crosswalk       build municipality -> GEOID crosswalk
    nhbot dra-official    ingest DRA official equalized rates (all years in data/raw)
    nhbot dra-estimate    compute current-year equalized-rate ESTIMATE
    nhbot load            load processed CSVs into Postgres (NHBOT_DSN)
    nhbot all             the core pipeline: crosswalk -> dra-official -> dra-estimate -> load
    nhbot boundaries      build map geometry from the Census cb shapefile (needs '.[geo]')
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

EXTRA = {"boundaries": _boundaries, "doe-schools": _doe_schools}
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
