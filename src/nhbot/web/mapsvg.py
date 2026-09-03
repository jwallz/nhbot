"""Server-rendered town map of New Hampshire.

A single, generic (non-thematic) map: every municipality is one light-filled
region that links to its town page (/{slug}) and shows its name as a native
hover tooltip. Where a name fits inside its region without colliding with a
neighbour's, it's also drawn as an in-region label — so most of the state reads
directly off the map, and the crowded southeast still names every town on hover.

Zero JavaScript: navigation is plain <a>, the tooltip is <title>. The whole SVG
is identical on every request, so it's built once and cached.

Label placement uses the precomputed anchors in nh_map_labels.json
(`nhbot map-labels`); no geometry library is needed at request time.
"""
import json
from html import escape
from nhbot.config import PROCESSED_DIR

REGION_FILL   = "#e9eef4"
REGION_STROKE = "#b7c2cf"
LABEL_FILL    = "#3a4653"

_GEOJSON = None
_LABELS = None
_CACHE = {}

def _geojson():
    global _GEOJSON
    if _GEOJSON is None:
        _GEOJSON = json.load(open(PROCESSED_DIR / "nh_municipalities.geojson"))
    return _GEOJSON

def _labels():
    global _LABELS
    if _LABELS is None:
        _LABELS = json.load(open(PROCESSED_DIR / "nh_map_labels.json"))
    return _LABELS

def _polys(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return list(geom["coordinates"])
    return []

def _bounds():
    xs, ys = [], []
    for f in _geojson()["features"]:
        for poly in _polys(f["geometry"]):
            for ring in poly:
                for lon, lat in ring:
                    xs.append(lon); ys.append(lat)
    return min(xs), max(xs), min(ys), max(ys)


def town_map(width=1000, font=7, pad=10, glyph=0.5):
    """Generic clickable town map. `values`-free: same for everyone, cached.
    Returns the SVG string."""
    import math
    key = (width, font, pad, glyph)
    if key in _CACHE:
        return _CACHE[key]

    minx, maxx, miny, maxy = _bounds()
    k = math.cos(math.radians((miny + maxy) / 2))
    scale = (width - 2 * pad) / ((maxx - minx) * k)
    height = (maxy - miny) * scale + 2 * pad
    def pt(lon, lat):
        return (pad + (lon - minx) * k * scale, pad + (maxy - lat) * scale)

    # regions (links + hover), keyed for slug lookup
    slug = {l["geoid"]: l["slug"] for l in _labels()}
    regions = []
    for f in _geojson()["features"]:
        p = f["properties"]; geoid = p["geoid"]
        d = "".join(
            "M" + " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(lon, lat) for lon, lat in ring)) + "Z"
            for poly in _polys(f["geometry"]) for ring in poly)
        href = "/" + slug.get(geoid, "") if slug.get(geoid) else "#"
        regions.append(
            f'<a href="{href}"><path d="{d}" fill="{REGION_FILL}" stroke="{REGION_STROKE}" '
            f'stroke-width="0.5"><title>{escape(p["name"])}</title></path></a>')

    # labels: greedy, largest region first, drop on overflow/collision
    placed, boxes = [], []
    for l in sorted(_labels(), key=lambda r: -r["area"]):
        name = l["name"]
        cx, cy = pt(l["lon"], l["lat"])
        avail = l["chord_deg"] * k * scale
        tw = len(name) * font * glyph
        if tw > avail * 0.95:
            continue
        th = font * 1.15
        x0, y0, x1, y1 = cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2
        if any(not (x1 <= b[0] or x0 >= b[2] or y1 <= b[1] or y0 >= b[3]) for b in boxes):
            continue
        boxes.append((x0, y0, x1, y1))
        placed.append(
            f'<text x="{cx:.1f}" y="{cy + font * 0.35:.1f}" font-size="{font}" '
            f'text-anchor="middle">{escape(name)}</text>')

    svg = (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" class="townmap" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Clickable map of New Hampshire municipalities">'
        f'<g class="regions">{"".join(regions)}</g>'
        f'<g class="labels" fill="{LABEL_FILL}" font-family="system-ui,Segoe UI,Arial,sans-serif" '
        f'pointer-events="none">{"".join(placed)}</g>'
        f'</svg>')
    _CACHE[key] = svg
    return svg


def _lean_color(lean):
    """lean -1..+1  ->  blue (all D) .. purple (even) .. red (all R)."""
    D, P, R = (0x1c, 0x5c, 0xab), (0x6b, 0x4c, 0x9a), (0xc0, 0x39, 0x2b)
    if lean <= 0:
        a, b, f = D, P, lean + 1        # -1 -> blue, 0 -> purple
    else:
        a, b, f = P, R, lean            #  0 -> purple, +1 -> red
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3))


def political_map(makeup, width=1000, pad=10):
    """Statewide choropleth of partisan representation. `makeup` is
    repo.political_makeup(): {geoid: {d, r, i, n, lean, has_major}}. Each town is
    filled on the blue↔purple↔red lean gradient, links to its page, and names its
    exact tally on hover so the colour is a summary, never the only signal. Data-
    driven, so not cached (the underlying legislator roster is what changes)."""
    import math
    minx, maxx, miny, maxy = _bounds()
    k = math.cos(math.radians((miny + maxy) / 2))
    scale = (width - 2 * pad) / ((maxx - minx) * k)
    height = (maxy - miny) * scale + 2 * pad
    def pt(lon, lat):
        return (pad + (lon - minx) * k * scale, pad + (maxy - lat) * scale)

    slug = {l["geoid"]: l["slug"] for l in _labels()}
    regions = []
    for f in _geojson()["features"]:
        p = f["properties"]; geoid = p["geoid"]
        m = makeup.get(geoid)
        if m and m["has_major"]:
            fill = _lean_color(m["lean"])
        elif m:
            fill = "#9a9a95"                 # seated, but no major-party member
        else:
            fill = "#d9d9d4"                 # no representation matched
        # exact tally for the hover tooltip
        if m:
            bits = []
            if m["r"]: bits.append(f'{m["r"]} R')
            if m["d"]: bits.append(f'{m["d"]} D')
            if m["i"]: bits.append(f'{m["i"]} I')
            tip = f'{p["name"]} — ' + (" · ".join(bits) if bits else "no seated members")
        else:
            tip = f'{p["name"]} — unincorporated (no local representation)'
        d = "".join(
            "M" + " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(lon, lat) for lon, lat in ring)) + "Z"
            for poly in _polys(f["geometry"]) for ring in poly)
        href = "/" + slug.get(geoid, "") if slug.get(geoid) else "#"
        regions.append(
            f'<a href="{href}"><path d="{d}" fill="{fill}" stroke="#ffffff" '
            f'stroke-width="0.5"><title>{escape(tip)}</title></path></a>')

    return (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" class="polmap" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Map of New Hampshire municipalities coloured by the partisan makeup '
        f'of their state legislative representation">'
        f'<g class="regions">{"".join(regions)}</g></svg>')


_LOC_CACHE = {}

def locator_map(geoid, width=230, pad=6):
    """Tiny 'you are here' state map for a town page.

    Draws every municipality as a faint grey silhouette of New Hampshire with
    the target town filled in the accent colour and a marker dot at its centre —
    a quick visual answer to "where in the state is this town?". No labels, no
    links; colours are class-driven so the map follows the site's light/dark
    theme (see .locator in app.css). Same for every request, so it's cached.
    """
    import math
    key = (geoid, width)
    if key in _LOC_CACHE:
        return _LOC_CACHE[key]

    minx, maxx, miny, maxy = _bounds()
    k = math.cos(math.radians((miny + maxy) / 2))
    scale = (width - 2 * pad) / ((maxx - minx) * k)
    height = (maxy - miny) * scale + 2 * pad
    def pt(lon, lat):
        return (pad + (lon - minx) * k * scale, pad + (maxy - lat) * scale)

    ctx, here, name = [], "", ""
    for f in _geojson()["features"]:
        p = f["properties"]
        d = "".join(
            "M" + " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(lon, lat) for lon, lat in ring)) + "Z"
            for poly in _polys(f["geometry"]) for ring in poly)
        if p["geoid"] == geoid:
            here = f'<path d="{d}" class="here"/>'
            name = p["name"]
        else:
            ctx.append(f'<path d="{d}" class="ctx"/>')

    # marker dot at the town's label anchor (a point that sits inside the town)
    anchor = {l["geoid"]: (l["lon"], l["lat"]) for l in _labels()}.get(geoid)
    dot = ""
    if anchor:
        cx, cy = pt(*anchor)
        dot = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.2" class="dot"/>'

    label = escape(name) if name else "this town"
    # Colours are inlined in the SVG (not just app.css) so the map renders
    # correctly even if a browser is holding a cached, pre-locator stylesheet.
    # They reference the theme vars (with hard fallbacks) so light/dark still work.
    style = (
        '<style>'
        '.locator .ctx{fill:var(--line,#e1e0d9);stroke:var(--panel,#fff);stroke-width:.4}'
        '.locator .here{fill:var(--est,#eb6834);stroke:var(--est,#eb6834);stroke-width:.4}'
        '.locator .dot{fill:var(--est,#eb6834);stroke:var(--panel,#fff);stroke-width:1.3}'
        '</style>')
    svg = (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="{width}" class="locator" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Location of {label} within New Hampshire">'
        f'{style}<g class="ctx-g">{"".join(ctx)}</g>{here}{dot}</svg>')
    _LOC_CACHE[key] = svg
    return svg
