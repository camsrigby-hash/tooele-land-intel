"""
Convert herriman_gp.geojson → herriman_gp.kmz for Google Earth Pro eye-test.
Phase 18b-2d Herriman extraction output.
"""
import json
import os
import sys
from collections import defaultdict
import simplekml

GEOJSON_PATH = "data/zoning/future/herriman_gp.geojson"
LEGEND_PATH  = "data/zoning/future/legends/herriman_legend.json"
KMZ_PATH     = "data/zoning/future/herriman_gp.kmz"

EXTRACTION_DATE = "2026-05-18"
SOURCE_PDF      = "Herriman City General Plan (Future Land Use Map)"
KML_NAME        = "Herriman GP FLU (Phase 18b-2d extraction, 2026-05-18)"


def rgb_to_kml_color(r, g, b, alpha_hex="80"):
    """Convert RGB to KML AABBGGRR format."""
    return f"{alpha_hex}{b:02x}{g:02x}{r:02x}"


def build_legend_map(legend_path):
    with open(legend_path) as f:
        entries = json.load(f)
    return {
        e["label"]: rgb_to_kml_color(*e["rgb"])
        for e in entries
    }


def polygon_coords(geometry):
    """Yield (outer_ring, [hole_rings]) tuples from Polygon or MultiPolygon."""
    gtype = geometry["type"]
    if gtype == "Polygon":
        rings = geometry["coordinates"]
        yield rings[0], rings[1:]
    elif gtype == "MultiPolygon":
        for poly in geometry["coordinates"]:
            yield poly[0], poly[1:]


def ring_to_kml(ring):
    """Convert GeoJSON ring [[lon,lat],...] to simplekml tuple list."""
    return [(c[0], c[1]) for c in ring]


def build_description(props):
    fields = [
        ("parcel_id",              props.get("parcel_id", "")),
        ("sampled_zone",           props.get("sampled_zone", "")),
        ("extraction_method",      props.get("extraction_method", "")),
        ("color_match_confidence", props.get("color_match_confidence", "")),
        ("lab_distance",           props.get("lab_distance", "")),
        ("extraction_date",        props.get("extraction_date", "")),
        ("source_pdf_filename",    props.get("source_pdf_filename", "")),
        ("source_pdf_page",        props.get("source_pdf_page", "")),
        ("flu_plan_vintage",       props.get("flu_plan_vintage", "")),
    ]
    rows = "".join(
        f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"
        for k, v in fields if v not in ("", None)
    )
    return f"<table>{rows}</table>"


def make_poly_style(fill_color, line_color="60000000"):
    """Return a simplekml Style with fill and line."""
    style = simplekml.Style()
    style.polystyle.color     = fill_color
    style.polystyle.fill      = 1
    style.polystyle.outline   = 1
    style.linestyle.color     = line_color
    style.linestyle.width     = 1
    return style


def main():
    print(f"Loading {GEOJSON_PATH}...")
    with open(GEOJSON_PATH) as f:
        gj = json.load(f)
    features = gj["features"]
    print(f"  {len(features)} features loaded")

    print(f"Loading legend {LEGEND_PATH}...")
    legend = build_legend_map(LEGEND_PATH)
    print(f"  {len(legend)} legend entries")

    # Build KML
    kml = simplekml.Kml(name=KML_NAME)
    kml.document.description = (
        f"Extraction date: {EXTRACTION_DATE}\n"
        f"Source PDF: {SOURCE_PDF}\n"
        f"Total polygons: {len(features)}\n"
        f"Pipeline: Phase 18b-2d raster-sample\n"
        f"Vintage metadata: see flu_plan_vintage field per parcel"
    )

    # Group features by zone
    zone_features = defaultdict(list)
    for feat in features:
        zone = feat["properties"].get("sampled_zone") or "Unclassified"
        zone_features[zone].append(feat)

    # Cache styles per zone to avoid creating duplicates
    UNCLASSIFIED_COLOR = "80808080"
    style_cache = {}

    def get_style(zone):
        if zone in style_cache:
            return style_cache[zone]
        fill = legend.get(zone, UNCLASSIFIED_COLOR)
        s = make_poly_style(fill_color=fill, line_color="60000000")
        style_cache[zone] = s
        return s

    total_placemarks = 0
    skipped = 0

    for zone in sorted(zone_features.keys()):
        folder = kml.newfolder(name=zone)
        feats  = zone_features[zone]
        style  = get_style(zone)

        for feat in feats:
            geom = feat.get("geometry")
            if not geom:
                skipped += 1
                continue

            props = feat["properties"]
            desc  = build_description(props)
            name  = zone

            try:
                for outer, holes in polygon_coords(geom):
                    pol = folder.newpolygon(name=name, description=desc)
                    pol.outerboundaryis = ring_to_kml(outer)
                    if holes:
                        pol.innerboundaryis = [ring_to_kml(h) for h in holes]
                    pol.style = style
                    total_placemarks += 1
            except Exception as e:
                print(f"  WARN geometry error on parcel {props.get('parcel_id','?')}: {e}")
                skipped += 1

    print(f"Saving KMZ to {KMZ_PATH}...")
    kml.savekmz(KMZ_PATH)

    size_mb = os.path.getsize(KMZ_PATH) / 1_048_576
    print(f"\n--- Done ---")
    print(f"KMZ path:        {os.path.abspath(KMZ_PATH)}")
    print(f"File size:       {size_mb:.2f} MB")
    print(f"Total placemarks:{total_placemarks}")
    print(f"Folders:         {len(zone_features)}")
    print(f"Skipped:         {skipped}")

    if size_mb > 30:
        print("WARNING: file > 30 MB — consider geometry simplification")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
