"""Render a static choropleth preview of the equalized tax rate.

Validation + first-look artifact (NOT the production map): joins the boundary
GeoJSON to the DRA official equalized rate for a given year, bins it into a
sequential blue ramp, and writes a self-contained, theme-aware HTML page with an
inline SVG, legend, and hover tooltip.

    python scripts/make_map_preview.py [year]     # default 2024
"""
import csv, json, math, sys
from nhbot.config import PROCESSED_DIR

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
# sequential blue ramp (dataviz reference palette), low -> high
RAMP = ["#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
NODATA = "#d8d7d0"

def load():
    gj = json.load(open(PROCESSED_DIR / "nh_municipalities.geojson"))
    name2geoid = {r["municipality"]: r["geoid"]
                  for r in csv.DictReader(open(PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv"))}
    rate = {}
    with open(PROCESSED_DIR / "nh_equalized_rates_official.csv") as f:
        for r in csv.DictReader(f):
            if int(r["vintage"]) == YEAR and r["full_value_rate_official"]:
                v = float(r["full_value_rate_official"])
                g = name2geoid.get(r["municipality"])
                if v > 0 and g:
                    rate[g] = v
    return gj, rate

def quantile_breaks(values, k):
    s = sorted(values)
    return [s[max(0, min(len(s) - 1, round(i * len(s) / k)))] for i in range(1, k)]

def bin_index(v, breaks):
    for i, b in enumerate(breaks):
        if v <= b:
            return i
    return len(breaks)

def project(gj):
    xs, ys = [], []
    for f in gj["features"]:
        for poly in _polys(f["geometry"]):
            for ring in poly:
                for lon, lat in ring:
                    xs.append(lon); ys.append(lat)
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    k = math.cos(math.radians((miny + maxy) / 2))
    W, PAD = 640, 16
    sx = lambda lon: (lon - minx) * k
    sy = lambda lat: (maxy - lat)
    w = (maxx - minx) * k
    h = (maxy - miny)
    scale = (W - 2 * PAD) / w
    H = h * scale + 2 * PAD
    def pt(lon, lat):
        return (PAD + sx(lon) * scale, PAD + sy(lat) * scale)
    return pt, W, H

def _polys(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return [p for p in geom["coordinates"]]
    return []

def path_d(geom, pt):
    d = []
    for poly in _polys(geom):
        for ring in poly:
            pts = [pt(lon, lat) for lon, lat in ring]
            d.append("M" + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    return "".join(d)

def main():
    gj, rate = load()
    breaks = quantile_breaks(list(rate.values()), len(RAMP))
    pt, W, H = project(gj)

    paths = []
    for f in gj["features"]:
        g = f["geometry"]; p = f["properties"]
        v = rate.get(p["geoid"])
        fill = RAMP[bin_index(v, breaks)] if v else NODATA
        nm = p['name'].replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
        label = f"{nm} — {v:.2f}" if v else f"{nm} — n/a"
        paths.append(
            f'<path d="{path_d(g, pt)}" fill="{fill}" data-label="{label}"/>')

    # legend swatches
    edges = [min(rate.values())] + breaks + [max(rate.values())]
    legend = "".join(
        f'<span class="sw"><i style="background:{RAMP[i]}"></i>'
        f'{edges[i]:.1f}–{edges[i+1]:.1f}</span>'
        for i in range(len(RAMP)))

    html = f"""<title>NH Equalized Tax Rate {YEAR}</title>
<style>
  .wrap{{--surface:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--stroke:#fcfcfb;
        background:var(--surface);color:var(--ink);
        font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
        padding:20px;max-width:720px;margin:0 auto}}
  @media (prefers-color-scheme:dark){{.wrap{{--surface:#1a1a19;--ink:#fff;--muted:#c3c2b7;--stroke:#1a1a19}}}}
  .wrap h1{{font-size:17px;margin:0 0 2px}}
  .wrap p.sub{{color:var(--muted);margin:0 0 14px}}
  .wrap svg path{{stroke:var(--stroke);stroke-width:.5;cursor:default}}
  .wrap svg path:hover{{stroke:var(--ink);stroke-width:1.2}}
  .legend{{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px;color:var(--muted);font-size:12px}}
  .sw{{display:inline-flex;align-items:center;gap:5px}}
  .sw i{{width:14px;height:14px;border-radius:3px;display:inline-block}}
  #tip{{position:fixed;pointer-events:none;opacity:0;background:var(--ink);color:var(--surface);
       padding:4px 8px;border-radius:5px;font-size:12px;transform:translate(-50%,-140%);white-space:nowrap}}
</style>
<div class="wrap">
  <h1>New Hampshire — Equalized Property Tax Rate, {YEAR}</h1>
  <p class="sub">DRA full-value rate per $1,000. Darker = higher. Sequential quantile bins; unincorporated places shown as n/a.</p>
  <svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg" aria-label="Choropleth of NH equalized tax rate">
    {''.join(paths)}
  </svg>
  <div class="legend">{legend}<span class="sw"><i style="background:{NODATA}"></i>n/a</span></div>
</div>
<div id="tip"></div>
<script>
  const tip=document.getElementById('tip');
  for(const p of document.querySelectorAll('.wrap svg path')){{
    p.addEventListener('mousemove',e=>{{tip.textContent=p.dataset.label;tip.style.left=e.clientX+'px';tip.style.top=e.clientY+'px';tip.style.opacity=1;}});
    p.addEventListener('mouseleave',()=>tip.style.opacity=0);
  }}
</script>"""
    out = PROCESSED_DIR / f"nh_equalized_rate_{YEAR}_map.html"
    out.write_text(html)
    print(f"wrote {out}  ({out.stat().st_size//1024} KB)  breaks={[round(b,1) for b in breaks]}")

if __name__ == "__main__":
    main()
