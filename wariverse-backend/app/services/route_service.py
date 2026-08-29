"""Turn-by-turn guidance along the palkhi route.

This is deliberately not a general-purpose router: pilgrims walk a fixed,
published corridor of halts (Alandi → … → Wakhari → Pandharpur). Guidance
snaps the user to the nearest waypoint and walks the ordered list towards the
destination, adding crowd warnings from `CrowdService` on the way.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.i18n import t
from app.data.reference import (
    DEFAULT_ROUTE_ID,
    ROUTE_WAYPOINTS,
    ZONES_BY_ID,
    localized_name,
)
from app.models.db_models import RouteWaypoint
from app.models.schemas import (
    GeoPoint,
    RouteGuidanceResponse,
    RouteStep,
    RouteWarning,
)
from app.services.crowd_service import CONGESTION_FACTOR, CrowdService, ZoneNotFoundError
from app.services.geo import bearing_label, haversine_m, walk_minutes
from app.utils import now_utc

log = structlog.get_logger(__name__)

DEFAULT_DESTINATION = "vitthal_temple"

_DIRECTION_WORDS: dict[str, dict[str, str]] = {
    "mr": {
        "north": "उत्तरेकडे", "north-east": "ईशान्येकडे", "east": "पूर्वेकडे",
        "south-east": "आग्नेयेकडे", "south": "दक्षिणेकडे", "south-west": "नैऋत्येकडे",
        "west": "पश्चिमेकडे", "north-west": "वायव्येकडे",
    },
    "hi": {
        "north": "उत्तर", "north-east": "उत्तर-पूर्व", "east": "पूर्व",
        "south-east": "दक्षिण-पूर्व", "south": "दक्षिण", "south-west": "दक्षिण-पश्चिम",
        "west": "पश्चिम", "north-west": "उत्तर-पश्चिम",
    },
}


class DestinationNotFoundError(LookupError):
    """Raised when the requested destination is not on the route."""


class RouteService:
    def __init__(
        self, db: AsyncSession | None = None, crowd_service: CrowdService | None = None
    ) -> None:
        self.db = db
        self.crowd = crowd_service or CrowdService(db)

    async def guidance(
        self,
        lat: float,
        lon: float,
        destination: str | None = None,
        route_id: str = DEFAULT_ROUTE_ID,
        language: str = "mr",
    ) -> RouteGuidanceResponse:
        waypoints = await self._waypoints(route_id)
        if not waypoints:
            raise DestinationNotFoundError(route_id)

        destination_key = (destination or DEFAULT_DESTINATION).strip().lower()
        dest_index = self._find_destination(waypoints, destination_key)
        start_index = self._nearest_index(waypoints, lat, lon)

        # The palkhi walks one way, but a pilgrim may be ahead of their target
        # (e.g. standing at the temple asking for the ghat), so allow both
        # directions along the ordered corridor.
        if start_index <= dest_index:
            leg = waypoints[start_index : dest_index + 1]
        else:
            leg = list(reversed(waypoints[dest_index : start_index + 1]))

        readings = await self._zone_readings(leg)
        steps, total_m = self._build_steps(lat, lon, leg, readings, language)

        worst = self._worst_congestion(readings)
        eta = walk_minutes(
            total_m, settings.walking_speed_kmph, CONGESTION_FACTOR.get(worst, 1.0)
        )
        destination_wp = leg[-1] if leg else waypoints[dest_index]

        return RouteGuidanceResponse(
            route_id=route_id,
            origin=GeoPoint(lat=lat, lon=lon),
            destination_name=localized_name(destination_wp, language),
            destination=GeoPoint(lat=destination_wp["lat"], lon=destination_wp["lon"]),
            distance_km=round(total_m / 1000, 2),
            eta_minutes=eta,
            congestion_level=worst,  # type: ignore[arg-type]
            language=language,  # type: ignore[arg-type]
            steps=steps,
            warnings=self._warnings(readings, language),
            updated_at=now_utc(),
        )

    # --- internals ---------------------------------------------------------

    async def _waypoints(self, route_id: str) -> list[dict[str, Any]]:
        if self.db is not None:
            try:
                rows = (
                    (
                        await self.db.execute(
                            select(RouteWaypoint)
                            .where(RouteWaypoint.route_id == route_id)
                            .order_by(RouteWaypoint.sequence)
                        )
                    )
                    .scalars()
                    .all()
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("route_db_read_failed", route_id=route_id, error=str(exc))
                rows = []

            if rows:
                return [
                    {
                        "sequence": w.sequence,
                        "name_en": w.name_en,
                        "name_mr": w.name_mr,
                        "name_hi": w.name_hi,
                        "lat": w.lat,
                        "lon": w.lon,
                        "zone_ref": w.zone_ref,
                        "is_halt": w.is_halt,
                        "landmark": w.landmark,
                    }
                    for w in rows
                ]

        return [dict(w) for w in ROUTE_WAYPOINTS] if route_id == DEFAULT_ROUTE_ID else []

    @staticmethod
    def _slug(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    def _find_destination(self, waypoints: list[dict[str, Any]], key: str) -> int:
        slug = self._slug(key)
        for index, wp in enumerate(waypoints):
            candidates = {
                self._slug(wp.get("zone_ref") or ""),
                self._slug(wp["name_en"]),
                self._slug(wp.get("name_mr") or ""),
                self._slug(wp.get("name_hi") or ""),
            }
            if slug in candidates - {""}:
                return index

        # A destination that is a zone but not a waypoint (e.g. the bus stand)
        # resolves to the closest waypoint on the corridor.
        zone = ZONES_BY_ID.get(key)
        if zone:
            return self._nearest_index(waypoints, zone["lat"], zone["lon"])

        raise DestinationNotFoundError(key)

    @staticmethod
    def _nearest_index(waypoints: list[dict[str, Any]], lat: float, lon: float) -> int:
        distances = [haversine_m(lat, lon, w["lat"], w["lon"]) for w in waypoints]
        return distances.index(min(distances))

    async def _zone_readings(self, leg: list[dict[str, Any]]) -> dict[str, Any]:
        readings: dict[str, Any] = {}
        for wp in leg:
            zone_ref = wp.get("zone_ref")
            if not zone_ref or zone_ref in readings:
                continue
            try:
                readings[zone_ref] = await self.crowd.read_zone(zone_ref)
            except ZoneNotFoundError:
                continue
        return readings

    def _build_steps(
        self,
        lat: float,
        lon: float,
        leg: list[dict[str, Any]],
        readings: dict[str, Any],
        language: str,
    ) -> tuple[list[RouteStep], float]:
        steps: list[RouteStep] = []
        cursor = (lat, lon)
        cumulative = 0.0

        for index, wp in enumerate(leg):
            distance = haversine_m(cursor[0], cursor[1], wp["lat"], wp["lon"])
            # Skip a zero-length first hop when the pilgrim is already standing
            # on the waypoint, unless it is also the destination.
            if index == 0 and distance < 50 and len(leg) > 1:
                cursor = (wp["lat"], wp["lon"])
                continue

            cumulative += distance
            name = localized_name(wp, language)
            is_last = index == len(leg) - 1

            if is_last:
                instruction = t("route_arrive", language, name=name)
            else:
                direction = bearing_label(cursor[0], cursor[1], wp["lat"], wp["lon"])
                instruction = t(
                    "route_step",
                    language,
                    name=name,
                    direction=_DIRECTION_WORDS.get(language, {}).get(direction, direction),
                    distance=int(round(distance)),
                )

            reading = readings.get(wp.get("zone_ref") or "")
            steps.append(
                RouteStep(
                    sequence=len(steps) + 1,
                    instruction=instruction,
                    name=name,
                    lat=wp["lat"],
                    lon=wp["lon"],
                    distance_m=int(round(distance)),
                    cumulative_distance_m=int(round(cumulative)),
                    is_halt=bool(wp.get("is_halt")),
                    landmark=wp.get("landmark"),
                    congestion=getattr(reading, "density_level", None),
                )
            )
            cursor = (wp["lat"], wp["lon"])

        return steps, cumulative

    @staticmethod
    def _worst_congestion(readings: dict[str, Any]) -> str:
        worst = "low"
        for reading in readings.values():
            if CONGESTION_FACTOR.get(reading.density_level, 1.0) > CONGESTION_FACTOR[worst]:
                worst = reading.density_level
        return worst

    @staticmethod
    def _warnings(readings: dict[str, Any], language: str) -> list[RouteWarning]:
        warnings: list[RouteWarning] = []
        for zone_id, reading in readings.items():
            if reading.density_level not in ("high", "critical"):
                continue
            zone = ZONES_BY_ID.get(zone_id)
            name = localized_name(zone, language) if zone else zone_id
            warnings.append(
                RouteWarning(
                    zone_id=zone_id,
                    severity="avoid" if reading.density_level == "critical" else "caution",
                    message=(
                        t("crowd_critical", language, zone=name)
                        if reading.density_level == "critical"
                        else t("route_congested", language, zone=name)
                    ),
                )
            )
        return warnings
