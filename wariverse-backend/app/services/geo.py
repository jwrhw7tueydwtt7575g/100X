"""Small geodesy helpers shared by the facility, route and SOS services."""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bounding_box(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) box that fully contains the radius.

    Used to let Postgres prefilter on a plain btree index before the exact
    haversine distance is computed in Python.
    """
    lat_delta = math.degrees(radius_m / EARTH_RADIUS_M)
    # Guard against division by zero at the poles (never hit in Maharashtra).
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    lon_delta = math.degrees(radius_m / (EARTH_RADIUS_M * cos_lat))
    return (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta)


def walk_minutes(distance_m: float, speed_kmph: float = 4.0, congestion_factor: float = 1.0) -> int:
    """Walking time in whole minutes, slowed down by crowd congestion."""
    if distance_m <= 0:
        return 0
    effective_kmph = max(speed_kmph / max(congestion_factor, 0.1), 0.5)
    minutes = (distance_m / 1000.0) / effective_kmph * 60.0
    return max(1, round(minutes))


def bearing_label(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Coarse compass direction from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    degrees = (math.degrees(math.atan2(y, x)) + 360) % 360
    points = [
        "north",
        "north-east",
        "east",
        "south-east",
        "south",
        "south-west",
        "west",
        "north-west",
    ]
    return points[round(degrees / 45) % 8]
