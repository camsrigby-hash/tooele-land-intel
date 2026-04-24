"""ArcGIS REST API helper for Tooele County GIS services."""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

log = logging.getLogger(__name__)

TIMEOUT = 30
REQUEST_DELAY = 0.3  # seconds between requests — polite rate limit
CACHE_DIR = Path("data/cache/arcgis")


def _make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "TooeleLandIntel/1.0"})
    return session


# Shared session (lazy-initialized per process — no cache, used for ad-hoc queries)
SESSION = _make_session()


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def _load_cache(path: Path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _save_cache(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        log.warning("Cache write failed: %s", e)


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
    """Spatial query - return features whose geometry contains (lon, lat)."""
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


def query_by_point_radius(url: str, lon: float, lat: float, radius_m: float,
                          out_fields: str = "*") -> list[dict]:
    """Spatial query - return features within radius_m meters of (lon, lat).

    Used as a fallback when a strict point-in-polygon query returns nothing
    but neighboring polygons still carry useful context (e.g. the 2022
    Tooele County General Plan has patchy coverage).
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "distance": radius_m,
        "units": "esriSRUnit_Meter",
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
    """Return (lon, lat) centroid of first matching parcel, or None.

    Results are cached under data/cache/arcgis/ keyed by (url, where) hash
    so repeated geocoding runs don't re-hit UGRC.
    """
    cache_path = _cache_path(f"{url}|{where}")
    cached = _load_cache(cache_path)
    if cached is not None:
        # Cached None stored as False sentinel to distinguish from cache miss
        return None if cached is False else (cached[0], cached[1])

    params = {
        "where": where,
        "outFields": "Parcel_ID",
        "returnGeometry": "true",
        "returnCentroid": "true",
        "outSR": "4326",
        "f": "json",
    }
    try:
        time.sleep(REQUEST_DELAY)
        session = _make_session()
        resp = session.get(f"{url}/query", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.warning("ArcGIS error for %r: %s", where, data["error"])
            _save_cache(cache_path, False)
            return None
        features = data.get("features", [])
        if not features:
            _save_cache(cache_path, False)
            return None
        geom = features[0].get("centroid") or features[0].get("geometry")
        if not geom:
            _save_cache(cache_path, False)
            return None
        if "x" in geom:
            result = (geom["x"], geom["y"])
        elif "rings" in geom:
            rings = geom["rings"][0]
            xs = [p[0] for p in rings]
            ys = [p[1] for p in rings]
            result = (sum(xs) / len(xs), sum(ys) / len(ys))
        else:
            _save_cache(cache_path, False)
            return None
        _save_cache(cache_path, list(result))
        return result
    except Exception as e:
        log.warning("get_parcel_centroid failed for %r: %s", where, e)
        return None
