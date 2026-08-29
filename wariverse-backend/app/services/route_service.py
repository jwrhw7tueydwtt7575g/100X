"""Walking guidance to the temple along three precomputed pilgrim routes.

This is deliberately not a general-purpose router. Pilgrims walk a small number
of published approaches through a dense crowd; sending someone down an
arbitrary shortest path would route them through barricades and side lanes in
the dark. Guidance therefore picks between three surveyed corridors and says
which zones to avoid.

Selection prefers a route with no HIGH/VERY_HIGH zone on it, then the shortest.
If every route is congested it takes the least-congested one and still lists
what to avoid — there is always an answer, because "no route" helps nobody.

Walking speed is 2.5 km/h, not the usual 5: during the Wari the crowd sets the
pace.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.i18n import t
from app.data.reference import ZONES_BY_ID, localized_name
from app.models.schemas import LabelledPoint, RouteGuidanceResponse, Waypoint
from app.services.crowd_service import CONGESTION_FACTOR, CrowdService, ZoneNotFoundError
from app.services.geo import haversine_m

log = structlog.get_logger(__name__)

# The Vitthal temple, and the default destination for every request.
TEMPLE_LAT = 17.6775
TEMPLE_LON = 75.3283
TEMPLE_LABEL = "Vitthal Temple"

WALKING_SPEED_KMPH = 2.5

# --- the three precomputed approaches ---------------------------------------
# Each ends at the temple. `zones` are the monitored areas the corridor passes
# through, used both to score congestion and to build `avoid_areas`.
ROUTES: list[dict[str, Any]] = [
    {
        "route_id": "east-gate-1",
        "label": "East approach via Gate 1",
        "zones": ["main-road", "gate-1"],
        "coordinates": [
            (17.6771, 75.3325),
            (17.6776, 75.3316),
            (17.6784, 75.3309),
            (17.6790, 75.3308),
            (17.6786, 75.3296),
            (17.6779, 75.3288),
            (TEMPLE_LAT, TEMPLE_LON),
        ],
    },
    {
        "route_id": "north-gate-2",
        "label": "North approach via Gate 2",
        "zones": ["bhima-ghat", "gate-2"],
        "coordinates": [
            (17.6812, 75.3262),
            (17.6806, 75.3272),
            (17.6800, 75.3283),
            (17.6795, 75.3295),
            (17.6787, 75.3292),
            (TEMPLE_LAT, TEMPLE_LON),
        ],
    },
    {
        "route_id": "south-gate-3",
        "label": "South approach via Gate 3",
        "zones": ["gate-3"],
        "coordinates": [
            (17.6765, 75.3320),
            (17.6768, 75.3310),
            (17.6772, 75.3303),
            (17.6779, 75.3301),
            (17.6777, 75.3292),
            (TEMPLE_LAT, TEMPLE_LON),
        ],
    },
]

ROUTES_BY_ID = {route["route_id"]: route for route in ROUTES}

_BUSY = ("HIGH", "VERY_HIGH")


class DestinationNotFoundError(LookupError):
    """Raised when no route reaches the requested destination."""


def path_length_m(coordinates: list[tuple[float, float]]) -> float:
    return sum(
        haversine_m(a[0], a[1], b[0], b[1])
        for a, b in zip(coordinates, coordinates[1:], strict=False)
    )


def format_distance(metres: float) -> str:
    return f"{metres / 1000:.1f} km"


def format_duration(metres: float, language: str = "en") -> str:
    minutes = max(1, round((metres / 1000) / WALKING_SPEED_KMPH * 60))
    return t("route_walk_minutes", language, minutes=minutes)


class RouteService:
    def __init__(
        self, db: AsyncSession | None = None, crowd_service: CrowdService | None = None
    ) -> None:
        self.db = db
        self.crowd = crowd_service or CrowdService(db)

    async def guidance(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float | None = None,
        dest_lng: float | None = None,
        language: str = "en",
    ) -> RouteGuidanceResponse:
        dest_lat = TEMPLE_LAT if dest_lat is None else dest_lat
        dest_lng = TEMPLE_LON if dest_lng is None else dest_lng

        statuses = await self._zone_statuses()
        chosen, joined, total_m = self._choose(
            origin_lat, origin_lng, dest_lat, dest_lng, statuses
        )

        log.info(
            "route_selected",
            route_id=chosen["route_id"],
            distance_m=round(total_m),
            congested_zones=[z for z in chosen["zones"] if statuses.get(z) in _BUSY],
        )

        return RouteGuidanceResponse(
            origin=LabelledPoint(
                latitude=origin_lat,
                longitude=origin_lng,
                label=t("route_current_location", language),
            ),
            destination=LabelledPoint(
                latitude=dest_lat,
                longitude=dest_lng,
                label=self._destination_label(dest_lat, dest_lng, language),
            ),
            route_coordinates=[
                Waypoint(latitude=lat, longitude=lon) for lat, lon in joined
            ],
            estimated_time=format_duration(total_m, language),
            distance=format_distance(total_m),
            avoid_areas=self._avoid_areas(statuses, language),
            route_id=chosen["route_id"],
        )

    # --- internals ---------------------------------------------------------

    async def _zone_statuses(self) -> dict[str, str]:
        """Current status per zone, straight from the crowd cache."""
        statuses: dict[str, str] = {}
        for zone_id in ZONES_BY_ID:
            try:
                statuses[zone_id] = (await self.crowd.read_zone(zone_id)).status
            except ZoneNotFoundError:  # pragma: no cover — ids come from the registry
                continue
        return statuses

    def _choose(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
        statuses: dict[str, str],
    ) -> tuple[dict[str, Any], list[tuple[float, float]], float]:
        """Pick a corridor, then walk from the origin onto it and off at the end."""
        best: tuple[float, int, dict, list, float] | None = None

        for route in ROUTES:
            coordinates = list(route["coordinates"])

            # Join the corridor at whichever surveyed point is closest, rather
            # than always at its far end — but never at the LAST point, which
            # would reduce the "route" to a straight line to the destination
            # and send a pilgrim across whatever lies between.
            entry = min(
                range(len(coordinates) - 1),
                key=lambda i: haversine_m(origin_lat, origin_lng, *coordinates[i]),
            )
            leg = coordinates[entry:]
            joined = [(origin_lat, origin_lng), *leg]

            # A destination away from the temple gets a final hop off the corridor.
            if (
                haversine_m(dest_lat, dest_lng, TEMPLE_LAT, TEMPLE_LON) > 50
                and (dest_lat, dest_lng) != joined[-1]
            ):
                joined.append((dest_lat, dest_lng))

            distance = path_length_m(joined)

            # The hop from the pilgrim onto the corridor is unsurveyed ground —
            # count it twice so a corridor they are already standing on beats
            # one that is nominally shorter but starts with a 400 m scramble.
            join_distance = haversine_m(origin_lat, origin_lng, *coordinates[entry])

            # A route is as bad as its WORST point. Summing over zones would
            # instead reward routes that pass through fewer zones, which has
            # nothing to do with how crowded they are.
            penalty = max(
                (
                    CONGESTION_FACTOR.get(statuses.get(zone, "LOW"), 1.0)
                    for zone in route["zones"]
                ),
                default=1.0,
            )
            busy_count = sum(1 for zone in route["zones"] if statuses.get(zone) in _BUSY)

            # Congestion first, then effective distance — a clear route slightly
            # longer beats a short one through a crush.
            score = (busy_count, penalty * 500 + distance + join_distance)
            if best is None or score < (best[1], best[0]):
                best = (score[1], score[0], route, joined, distance)

        assert best is not None  # ROUTES is never empty
        return best[2], best[3], best[4]

    @staticmethod
    def _avoid_areas(statuses: dict[str, str], language: str) -> list[str]:
        """Congested zones, phrased for display: `Gate 3 — high congestion`."""
        out: list[str] = []
        for zone_id, status in statuses.items():
            if status not in _BUSY:
                continue
            name = localized_name(ZONES_BY_ID[zone_id], language)
            label = t(
                "congestion_very_high" if status == "VERY_HIGH" else "congestion_high",
                language,
            )
            out.append(f"{name} — {label}")
        return out

    @staticmethod
    def _destination_label(lat: float, lon: float, language: str) -> str:
        """Name the destination if it is a place we know, else say it plainly."""
        if haversine_m(lat, lon, TEMPLE_LAT, TEMPLE_LON) <= 120:
            return TEMPLE_LABEL

        nearest, best = None, float("inf")
        for zone in ZONES_BY_ID.values():
            distance = haversine_m(lat, lon, zone["lat"], zone["lon"])
            if distance < best:
                nearest, best = zone, distance
        if nearest is not None and best <= 200:
            return localized_name(nearest, language)
        return t("route_selected_destination", language)
