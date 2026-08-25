"""Municipal boundary geometry for the map.

Reads the Census cartographic boundary file for NH county subdivisions
(cb_YYYY_33_cousub_500k), joins each polygon to the canonical municipality
crosswalk on the 10-digit GEOID (zero name reconciliation), and writes:

    data/processed/nh_municipalities.geojson   full-resolution, EPSG:4326
    data/processed/nh_municipalities.topojson  simplified, topology-preserving

The cb file is already generalized (1:500k) and in EPSG:4269 (NAD83 geographic);
for a choropleth that is coordinate-identical to WGS84/4326 at display scale, so
coordinates are used as lon/lat directly (no reprojection dependency).

Requires the optional geo extra:  pip install -e '.[geo]'
"""
import csv, glob, json, os
import shapefile          # pyshp
import topojson as tp

from nhbot.config import RAW_DIR, PROCESSED_DIR

# simplification tolerance in degrees (~0.0005 deg ≈ 55 m); tune for the map.
SIMPLIFY_TOLERANCE = 0.0005


def _find_shapefile():
    hits = glob.glob(str(RAW_DIR / "geo" / "cb_*_33_cousub_500k.shp"))
    if not hits:
        raise FileNotFoundError(
            "cb_*_33_cousub_500k.shp not found under data/raw/geo/ — "
            "download it per data/SOURCES.md")
    return sorted(hits)[-1]      # newest vintage if several


def _load_crosswalk():
    xw = {}
    with open(PROCESSED_DIR / "nh_municipality_geoid_crosswalk.csv") as f:
        for r in csv.DictReader(f):
            xw[r["geoid"]] = r
    return xw


def main():
    shp_path = _find_shapefile()
    xw = _load_crosswalk()

    reader = shapefile.Reader(shp_path)
    fields = [f[0] for f in reader.fields[1:]]     # drop DeletionFlag
    gi = fields.index("GEOID")

    features, seen, unmatched = [], set(), []
    for sr in reader.iterShapeRecords():
        geoid = sr.record[gi]
        muni = xw.get(geoid)
        if not muni:                # water / "not defined" / non-municipal cousub
            unmatched.append(geoid)
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "geoid": geoid,
                "name": muni["municipality"],
                "entity_type": muni["entity_type"],
                "county": muni["county_name"],
            },
            "geometry": sr.shape.__geo_interface__,
        })
        seen.add(geoid)
    reader.close()

    missing = set(xw) - seen
    fc = {"type": "FeatureCollection", "features": features}

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    geojson_path = PROCESSED_DIR / "nh_municipalities.geojson"
    with open(geojson_path, "w") as f:
        json.dump(fc, f)

    # topology-preserving simplification -> TopoJSON for the web map
    topo = tp.Topology(fc, prequantize=1e6, toposimplify=SIMPLIFY_TOLERANCE)
    topojson_path = PROCESSED_DIR / "nh_municipalities.topojson"
    with open(topojson_path, "w") as f:
        f.write(topo.to_json())

    gj_kb = os.path.getsize(geojson_path) // 1024
    tj_kb = os.path.getsize(topojson_path) // 1024
    print(f"boundaries from {os.path.basename(shp_path)}")
    print(f"  matched polygons: {len(features)} / {len(xw)} crosswalk municipalities")
    if missing:
        print(f"  WARNING missing geometry for {len(missing)} GEOIDs: {sorted(missing)[:10]}")
    else:
        print("  every crosswalk municipality has a polygon")
    print(f"  non-municipal cousubs skipped: {len(unmatched)}")
    print(f"  wrote {geojson_path.name} ({gj_kb} KB) and {topojson_path.name} ({tj_kb} KB)")


if __name__ == "__main__":
    main()
