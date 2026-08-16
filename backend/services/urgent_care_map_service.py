"""Live, urgent-care-only discovery and routing integrations."""

from __future__ import annotations

import math
import os
from typing import Any

import requests


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
ORS_DIRECTIONS_URL = "https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson"
DEFAULT_RADIUS_METERS = 5_000
MAX_RADIUS_METERS = 15_000
MAX_FACILITIES = 12
REQUEST_TIMEOUT_SECONDS = 12


class UrgentCareMapError(Exception):
    """Safe error for an external map-service failure."""

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_coordinates(latitude: Any, longitude: Any) -> tuple[float, float]:
    """Convert and validate a geographic point supplied by the client."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        raise ValueError("Valid latitude and longitude are required.") from None
    if not math.isfinite(lat) or not -90 <= lat <= 90:
        raise ValueError("Latitude must be between -90 and 90.")
    if not math.isfinite(lon) or not -180 <= lon <= 180:
        raise ValueError("Longitude must be between -180 and 180.")
    return lat, lon


def validated_radius(value: Any) -> tuple[int, bool]:
    """Return a bounded discovery radius without permitting unbounded queries."""
    if value is None:
        return DEFAULT_RADIUS_METERS, False
    try:
        radius = int(float(value))
    except (TypeError, ValueError):
        raise ValueError("radiusMeters must be a positive number.") from None
    if radius <= 0:
        raise ValueError("radiusMeters must be a positive number.")
    return min(radius, MAX_RADIUS_METERS), radius > MAX_RADIUS_METERS


def _haversine_meters(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> int:
    earth_radius_meters = 6_371_000
    latitude_delta = math.radians(dest_lat - origin_lat)
    longitude_delta = math.radians(dest_lon - origin_lon)
    a = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(origin_lat))
        * math.cos(math.radians(dest_lat))
        * math.sin(longitude_delta / 2) ** 2
    )
    return round(2 * earth_radius_meters * math.asin(math.sqrt(a)))


def _urgent_care_query(latitude: float, longitude: float, radius_meters: int) -> str:
    """Match only OSM elements explicitly identified as urgent care."""
    around = f"(around:{radius_meters},{latitude},{longitude})"
    return (
        "[out:json][timeout:10];\n(\n"
        f'  nwr["name"~"urgent[ _-]?care",i]{around};\n'
        f'  nwr["description"~"urgent[ _-]?care",i]{around};\n'
        f'  nwr["healthcare:speciality"~"urgent[ _-]?care",i]{around};\n'
        ");\nout center tags;"
    )


def _is_urgent_care(tags: dict[str, str]) -> bool:
    """Require a clear urgent-care signal; never treat all clinics as urgent care."""
    searchable = " ".join(
        str(tags.get(key, ""))
        for key in ("name", "description", "healthcare:speciality", "healthcare")
    ).lower()
    return any(signal in searchable for signal in ("urgent care", "urgent-care", "urgent_care"))


def _element_coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return validate_coordinates(element["lat"], element["lon"])
    center = element.get("center")
    if isinstance(center, dict) and "lat" in center and "lon" in center:
        return validate_coordinates(center["lat"], center["lon"])
    return None


def _address(tags: dict[str, str]) -> str | None:
    street = " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")]))
    locality = ", ".join(filter(None, [tags.get("addr:city"), tags.get("addr:state")]))
    return ", ".join(filter(None, [street, locality, tags.get("addr:postcode")])) or None


def discover_urgent_care_facilities(
    latitude: Any, longitude: Any, radius_meters: Any = None
) -> dict[str, Any]:
    """Retrieve and normalize nearby OSM places explicitly marked urgent care."""
    origin_latitude, origin_longitude = validate_coordinates(latitude, longitude)
    radius, radius_capped = validated_radius(radius_meters)
    try:
        response = requests.post(
            OVERPASS_URL,
            data={"data": _urgent_care_query(origin_latitude, origin_longitude, radius)},
            headers={"User-Agent": "avoidable-ed-utilization-navigator/1.0"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        elements = response.json().get("elements", [])
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise UrgentCareMapError("Urgent-care search is temporarily unavailable.") from exc

    facilities: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags")
        if not isinstance(tags, dict) or not _is_urgent_care(tags):
            continue
        try:
            coordinates = _element_coordinates(element)
        except ValueError:
            continue
        if coordinates is None:
            continue
        facility_id = f"{element.get('type', 'element')}/{element.get('id')}"
        if facility_id in seen_ids:
            continue
        seen_ids.add(facility_id)
        facility_latitude, facility_longitude = coordinates
        facility: dict[str, Any] = {
            "id": facility_id,
            "name": tags.get("name", "Urgent Care"),
            "type": "urgent_care",
            "latitude": facility_latitude,
            "longitude": facility_longitude,
            "distanceMeters": _haversine_meters(
                origin_latitude, origin_longitude, facility_latitude, facility_longitude
            ),
        }
        address = _address(tags)
        if address:
            facility["address"] = address
        if tags.get("opening_hours"):
            facility["openingHours"] = tags["opening_hours"]
        facilities.append(facility)

    facilities.sort(key=lambda facility: facility["distanceMeters"])
    return {
        "origin": {"latitude": origin_latitude, "longitude": origin_longitude},
        "radiusMeters": radius,
        "radiusCapped": radius_capped,
        "facilities": facilities[:MAX_FACILITIES],
    }


def calculate_urgent_care_route(origin: dict[str, Any], destination: dict[str, Any]) -> dict[str, Any]:
    """Return a real ORS driving route suitable for Leaflet GeoJSON rendering."""
    origin_latitude, origin_longitude = validate_coordinates(
        origin.get("latitude"), origin.get("longitude")
    )
    destination_latitude, destination_longitude = validate_coordinates(
        destination.get("latitude"), destination.get("longitude")
    )
    api_key = os.getenv("OPENROUTESERVICE_API_KEY")
    if not api_key:
        raise UrgentCareMapError("Routing service is temporarily unavailable.")
    try:
        response = requests.post(
            ORS_DIRECTIONS_URL,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={
                "coordinates": [
                    [origin_longitude, origin_latitude],
                    [destination_longitude, destination_latitude],
                ],
                "instructions": False,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise UrgentCareMapError("Routing service is temporarily unavailable.") from exc
    if response.status_code == 429:
        raise UrgentCareMapError("Routing service is busy. Please try again shortly.", 429)
    if response.status_code >= 400:
        raise UrgentCareMapError("Routing service is temporarily unavailable.")
    try:
        feature = response.json()["features"][0]
        summary = feature["properties"]["summary"]
        geometry = feature["geometry"]
        distance_meters = round(float(summary["distance"]))
        duration_seconds = round(float(summary["duration"]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise UrgentCareMapError("Routing service returned an invalid route.") from exc
    return {
        "distanceMeters": distance_meters,
        "durationSeconds": duration_seconds,
        "geometry": geometry,
    }
