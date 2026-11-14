"""TomTom traffic incidents mapped to approximate district-level impacts."""

import logging
import math
import threading
import time
from numbers import Real

import requests

import config
from external_factors import haversine_km

LOGGER = logging.getLogger(__name__)

API_URL = "https://api.tomtom.com/maps/orbis/traffic/incidents/details"
MOSCOW_BBOX = "36.8,55.15,38.1,56.1"
API_TIMEOUT = 8
CACHE_TTL_SECONDS = 20 * 60
DISTRICT_INFLUENCE_RADIUS_KM = 3.5
MAX_DISTRICT_PENALTY = 0.12
ATTRIBUTES = (
    "incidents(geometry(type,coordinates),"
    "properties(iconCategory,magnitudeOfDelay))"
)

CATEGORY_IMPACTS = {
    "accident": 0.10,
    "jam": 0.08,
    "roadclosed": 0.12,
    "laneclosed": 0.08,
    "roadworks": 0.04,
    "brokendownvehicle": 0.06,
    "dangerousconditions": 0.06,
    "flooding": 0.08,
    "ice": 0.06,
    "fog": 0.04,
    "rain": 0.03,
    "wind": 0.03,
}
DELAY_MULTIPLIERS = {
    "minor": 0.5,
    "moderate": 0.75,
    "major": 1.0,
    "undefined": 1.0,
    "unknown": 0.5,
}

_lock = threading.Lock()
_cache: dict = {"value": None, "at": 0.0}


def _coordinates(geometry: dict) -> list[tuple[float, float]]:
    """Return valid GeoJSON positions as (latitude, longitude) pairs."""
    found = []

    def visit(value):
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and isinstance(value[0], Real)
            and isinstance(value[1], Real)
        ):
            lon, lat = float(value[0]), float(value[1])
            if (
                math.isfinite(lat)
                and math.isfinite(lon)
                and -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                found.append((lat, lon))
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(geometry.get("coordinates"))
    return found


def normalize_incidents(payload: dict) -> list[dict]:
    """Keep only the fields needed to place current traffic incidents."""
    incidents = payload.get("incidents") if isinstance(payload, dict) else None
    if not isinstance(incidents, list):
        return []

    normalized = []
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        properties = incident.get("properties") or {}
        geometry = incident.get("geometry") or {}
        category = str(properties.get("iconCategory") or "").replace("_", "").lower()
        impact = CATEGORY_IMPACTS.get(category)
        coordinates = _coordinates(geometry) if isinstance(geometry, dict) else []
        if impact is None or not coordinates:
            continue
        delay = str(properties.get("magnitudeOfDelay") or "unknown").lower()
        normalized.append({
            "impact": impact * DELAY_MULTIPLIERS.get(delay, 1.0),
            "coordinates": coordinates,
        })
    return normalized


def fetch_incidents(force: bool = False) -> list[dict] | None:
    """Fetch current incidents, using a 20-minute cache to stay within quota."""
    api_key = config.TOMTOM_TRAFFIC_API_KEY
    if not api_key:
        return None

    with _lock:
        cached = _cache["value"]
        if (
            cached is not None
            and not force
            and time.monotonic() - _cache["at"] < CACHE_TTL_SECONDS
        ):
            return list(cached)

        try:
            response = requests.get(
                API_URL,
                params={
                    "apiVersion": 2,
                    "bbox": MOSCOW_BBOX,
                    "timeValidity": "present",
                },
                headers={
                    "TomTom-Api-Key": api_key,
                    "Attributes": ATTRIBUTES,
                    "Accept": "application/json",
                },
                timeout=API_TIMEOUT,
            )
            response.raise_for_status()
            incidents = normalize_incidents(response.json())
        except (requests.RequestException, ValueError, TypeError) as exc:
            LOGGER.warning("TomTom traffic feed unavailable: %s", exc)
            return None

        _cache.update({"value": incidents, "at": time.monotonic()})
        return list(incidents)


def district_impacts(incidents: list[dict], centers: dict) -> dict[str, float]:
    """Estimate a capped road-access penalty for districts near incidents."""
    impacts: dict[str, float] = {}
    for incident in incidents:
        coordinates = incident.get("coordinates") or []
        try:
            incident_impact = max(0.0, float(incident.get("impact") or 0))
        except (TypeError, ValueError):
            continue
        if not coordinates or incident_impact <= 0:
            continue

        for district, center in centers.items():
            try:
                distance = min(
                    haversine_km(
                        float(center["lat"]), float(center["lon"]), lat, lon
                    )
                    for lat, lon in coordinates
                )
            except (KeyError, TypeError, ValueError):
                continue
            if distance >= DISTRICT_INFLUENCE_RADIUS_KM:
                continue
            decay = 1.0 - distance / DISTRICT_INFLUENCE_RADIUS_KM
            impacts[district] = min(
                MAX_DISTRICT_PENALTY,
                impacts.get(district, 0.0) + incident_impact * decay,
            )
    return {district: round(value, 4) for district, value in impacts.items()}


def load_traffic_impacts(centers: dict) -> dict[str, float]:
    """Load current incidents and map their road-access impact to districts."""
    incidents = fetch_incidents()
    if incidents is None or not centers:
        return {}
    return district_impacts(incidents, centers)
