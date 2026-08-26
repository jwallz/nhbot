"""Server-rendered choropleth SVG.

Each town is an <a href="/town/{geoid}"> wrapping its <path>, with a native
<title> tooltip — so navigation and hover both work with zero JavaScript.
"""
import csv, json, math
from html import escape
from nhbot.config import PROCESSED_DIR

RAMP = ["#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
NODATA = "#d8d7d0"

def _polys(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return [p for p in geom["coordinates"]]
    return []

def _quantile_breaks(values, k):
    s = sorted(values)
    return [s[max(0, min(len(s) - 1, round(i * len(s) / k)))] for i in range(1, k)]

def _bin(v, breaks):
    for i, b in enumerate(breaks):
        if v <= b:
            return i
    return len(breaks)

def _load_geojson():
    return json.load(open(PROCESSED_DIR / "nh_municipalities.geojson"))

def choropleth(values, width=560, unit=""):
    """values: {geoid: number}. Returns (svg_str, legend_rows)."""
    gj = _load_geojson()
    breaks = _quantile_breaks([v for v in values.values() if v], len(RAMP)) if values else []

    xs, ys = [], []
    for f in gj["features"]:
        for poly in _polys(f["geometry"]):
            for ring in poly:
                for lon, lat in ring:
                    xs.append(lon); ys.append(lat)
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    k = math.cos(math.radians((miny + maxy) / 2))
    PAD = 8
    w = (maxx - minx) * k
    scale = (width - 2 * PAD) / w
    height = (maxy - miny) * scale + 2 * PAD
    def pt(lon, lat):
        return (PAD + (lon - minx) * k * scale, PAD + (maxy - lat) * scale)

    parts = []
    for f in gj["features"]:
        p = f["properties"]; geoid = p["geoid"]; v = values.get(geoid)
        fill = RAMP[_bin(v, breaks)] if v else NODATA
        d = "".join(
            "M" + " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(lon, lat) for lon, lat in ring)) + "Z"
            for poly in _polys(f["geometry"]) for ring in poly)
        label = f"{p['name']} — {v:.2f}{unit}" if v else f"{p['name']} — n/a"
        parts.append(
            f'<a href="/town/{geoid}"><path d="{d}" fill="{fill}">'
            f'<title>{escape(label)}</title></path></a>')

    svg = (f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" '
           f'class="choropleth" xmlns="http://www.w3.org/2000/svg" '
           f'role="img" aria-label="Choropleth map of New Hampshire municipalities">'
           f'{"".join(parts)}</svg>')

    legend = []
    if breaks:
        edges = [min(v for v in values.values() if v)] + breaks + [max(values.values())]
        legend = [{"color": RAMP[i], "lo": edges[i], "hi": edges[i + 1]} for i in range(len(RAMP))]
    return svg, legend
