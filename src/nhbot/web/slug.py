"""Canonical town-name <-> URL-slug conversion, used by both the query layer
(repo) and the offline map-label build, so the site's RESTful URLs (/amherst,
/new-boston, /harts-location) resolve consistently.

Slugs are derived from the canonical municipality name: apostrophes/periods are
dropped, every other run of non-alphanumerics becomes a single hyphen. All 259
NH municipality names are distinct under this rule (verified in the map-label
build), so a slug maps to exactly one GEOID.
"""
import re

def slugify(name: str) -> str:
    s = name.lower().replace("'", "").replace("’", "").replace(".", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s
