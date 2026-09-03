"""Precompute in-region label anchors for the town map, so the web app can draw
labels with pure arithmetic (no shapely at request time).

For each municipality we find the visual centre (polygon "pole of inaccessibility"
— the interior point farthest from any edge, which is where a label sits best),
and the width of the polygon along the horizontal line through that point
(`chord_deg`, in longitude degrees). At render the web layer projects these to
pixels, then greedily places labels largest-area-first, dropping any that would
overflow their chord or collide with an already-placed label.

Output: data/processed/nh_map_labels.json
        [{geoid, name, slug, lon, lat, chord_deg, area}]
Run:    nhbot map-labels   (needs the [geo] extra: shapely)
"""
import json
from nhbot.config import PROCESSED_DIR
from nhbot.web.slug import slugify

GEOJSON = PROCESSED_DIR / "nh_municipalities.geojson"
OUT     = PROCESSED_DIR / "nh_map_labels.json"


def build():
    from shapely.geometry import shape, LineString
    from shapely.algorithms.polylabel import polylabel

    gj = json.load(open(GEOJSON))
    out = []
    for f in gj["features"]:
        p = f["properties"]
        g = shape(f["geometry"])
        g0 = max(g.geoms, key=lambda q: q.area) if g.geom_type == "MultiPolygon" else g
        try:
            pl = polylabel(g0, tolerance=0.0008)
        except Exception:
            pl = g0.representative_point()
        # width of the polygon along the horizontal through the label point
        chord = LineString([(g0.bounds[0] - 1, pl.y), (g0.bounds[2] + 1, pl.y)])
        inter = g0.intersection(chord)
        chord_deg = 0.0
        if not inter.is_empty:
            segs = list(inter.geoms) if inter.geom_type == "MultiLineString" else [inter]
            for s in segs:
                x0, x1 = s.coords[0][0], s.coords[-1][0]
                if min(x0, x1) <= pl.x <= max(x0, x1):
                    chord_deg = abs(x1 - x0)
        out.append({"geoid": p["geoid"], "name": p["name"], "slug": slugify(p["name"]),
                    "lon": round(pl.x, 6), "lat": round(pl.y, 6),
                    "chord_deg": round(chord_deg, 6), "area": g0.area})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT, "w"))
    print(f"=== map labels ===")
    print(f"  {len(out)} municipality label anchors -> {OUT.name}")
    return out


def main():
    build()


if __name__ == "__main__":
    main()
