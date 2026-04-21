"""ArcGIS REST API helper for Tooele County GIS services."""

import requests
from typing import Optional

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TooeleLandIntel/1.0"})
TIMEOUT = 30


def _base(cfg: dict) -> str:
    return cfg["arcgis_base"]


def query_layer(url: str, where: str, out_fields: str = "*", geometry: Optional[dict] = None) -> list[dict]:
    """Run a WHERE query against a feature layer, return list of attribute dicts."""
    params = {
        "where": where,
        "outFields": out_fields,
        "f": "json",
        "returnGeometry": "true" if geometry is None else "false",
    }
    if geometry:
        params.update({
            "geometry": f"{geometry['x']},{geometry['y']}",
            "geometryType": "esriGeometryPoint",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "returnGeometry": "false",
        })
    resp = SESSION.get(f"{url}/query", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def query_by_point(url: str, lon: float, lat: float, out_fields: str = "*") -> list[dict]:
    """Spatial query — return features whose geometry contains (lon, lat)."""
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
    }
    resp = SESSION.get(f"{url}/query", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def get_parcel_centroid(url: str, where: str) -> Optional[tuple[float, float]]:
    """Return (lon, lat) centroid of first matching parcel, or None."""
    params = {
        "where": where,
        "outFields": "Parcel_ID",
        "returnGeometry": "true",
        "returnCentroid": "true",
        "outSR": "4326",
        "f": "json",
    }
    resp = SESSION.get(f"{url}/query", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features", [])
    if not features:
        return None
    geom = features[0].get("centroid") or features[0].get("geometry")
    if not geom:
        return None
    # centroid returns {x, y}; geometry returns rings — get bbox center
    if "x" in geom:
        return geom["x"], geom["y"]
    if "rings" in geom:
        rings = geom["rings"][0]
        xs = [p[0] for p in rings]
        ys = [p[1] for p in rings]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return None
