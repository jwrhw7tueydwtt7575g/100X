"""Crowd density lookups for monitored zones.

Read path: Redis cache → latest Postgres snapshot → deterministic estimate.
The `source` field on the response tells the client which one it got, and
`estimated` readings are explicitly labelled so the app can show them as
approximations rather than as sensor data.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime

import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.i18n import t
from app.data.reference import ZONES, ZONES_BY_ID, localized_name
from app.models.db_models import CrowdSnapshot, Zone
from app.models.schemas import AlternateZone, CrowdResponse
from app.redis_client import cache_get_json, cache_set_json
from app.utils import now_ist, now_utc

log = structlog.get_logger(__name__)

CACHE_PREFIX = "wv:crowd:"
CACHE_TTL_SECONDS = 30  # crowd data goes stale fast

# Multiplier applied to walking times when moving through a crowd.
CONGESTION_FACTOR: dict[str, float] = {
    "low": 1.0,
    "moderate": 1.3,
    "high": 1.8,
    "critical": 2.6,
}

# People cleared per minute, by zone type. Used to turn a headcount into a wait.
_THROUGHPUT_PER_MIN: dict[str, int] = {
    "queue": 220,
    "temple": 180,
    "ghat": 600,
    "corridor": 800,
    "transit": 300,
}

# Hours (IST) when each zone type fills up: aarti times and bathing hours.
_PEAK_HOURS: dict[str, set[int]] = {
    "temple": {4, 5, 6, 11, 18, 19, 22, 23},
    "queue": {4, 5, 6, 7, 8, 17, 18, 19, 20},
    "ghat": {4, 5, 6, 7, 16, 17, 18},
    "camp": {12, 13, 20, 21, 22},
    "transit": {6, 7, 8, 18, 19, 20},
}


class ZoneNotFoundError(LookupError):
    """Raised when the requested zone_id is not registered."""


@dataclass(slots=True)
class ZoneReading:
    zone_id: str
    zone_name: str
    zone_type: str
    density_level: str
    people_estimate: int
    capacity: int
    occupancy_ratio: float
    wait_minutes: int | None
    trend: str
    source: str
    updated_at: datetime
    alternate_zone_ids: list[str]


class CrowdService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    # --- public API --------------------------------------------------------

    async def get_zone(self, zone_id: str, language: str = "mr") -> CrowdResponse:
        reading = await self.read_zone(zone_id)
        alternates = await self._alternates(reading, language)
        return CrowdResponse(
            zone_id=reading.zone_id,
            zone_name=reading.zone_name if language == "en" else self._name(zone_id, language),
            zone_type=reading.zone_type,
            density_level=reading.density_level,  # type: ignore[arg-type]
            people_estimate=reading.people_estimate,
            capacity=reading.capacity,
            occupancy_ratio=round(reading.occupancy_ratio, 3),
            wait_minutes=reading.wait_minutes,
            trend=reading.trend,  # type: ignore[arg-type]
            advice=self._advice(reading, alternates, language),
            language=language,  # type: ignore[arg-type]
            alternate_zones=alternates,
            source=reading.source,  # type: ignore[arg-type]
            updated_at=reading.updated_at,
        )

    async def read_zone(self, zone_id: str) -> ZoneReading:
        """Raw reading without localisation — used internally by other services."""
        cached = await cache_get_json(f"{CACHE_PREFIX}{zone_id}")
        if cached:
            try:
                reading = ZoneReading(
                    **{**cached, "updated_at": datetime.fromisoformat(cached["updated_at"])},
                )
                # A sensor reading served from cache is reported as `cache`; an
                # estimate stays `estimated` however it was fetched, because
                # what the client must not confuse is a guess with a measurement.
                if reading.source == "live":
                    reading.source = "cache"
                return reading
            except (TypeError, ValueError, KeyError):
                log.warning("crowd_cache_malformed", zone_id=zone_id)

        reading = await self._read_from_db(zone_id)
        if reading is None:
            reading = self._estimate(zone_id)

        await cache_set_json(
            f"{CACHE_PREFIX}{zone_id}",
            {**asdict(reading), "updated_at": reading.updated_at.isoformat()},
            ttl_seconds=CACHE_TTL_SECONDS,
        )
        return reading

    async def nearest_zone_id(self, lat: float, lon: float) -> str | None:
        """Zone whose centre is closest to a point, within 5 km."""
        from app.services.geo import haversine_m

        best: tuple[str, float] | None = None
        for zone in ZONES:
            distance = haversine_m(lat, lon, zone["lat"], zone["lon"])
            if best is None or distance < best[1]:
                best = (zone["zone_id"], distance)
        if best and best[1] <= 5000:
            return best[0]
        return None

    def congestion_factor(self, density_level: str) -> float:
        return CONGESTION_FACTOR.get(density_level, 1.0)

    # --- internals ---------------------------------------------------------

    async def _read_from_db(self, zone_id: str) -> ZoneReading | None:
        if self.db is None:
            return None
        try:
            zone = (
                await self.db.execute(select(Zone).where(Zone.zone_id == zone_id))
            ).scalar_one_or_none()
            if zone is None:
                return None

            snapshots = (
                (
                    await self.db.execute(
                        select(CrowdSnapshot)
                        .where(CrowdSnapshot.zone_id == zone.id)
                        .order_by(desc(CrowdSnapshot.recorded_at))
                        .limit(2)
                    )
                )
                .scalars()
                .all()
            )
        except Exception as exc:  # noqa: BLE001 — never fail a safety lookup on DB errors
            log.warning("crowd_db_read_failed", zone_id=zone_id, error=str(exc))
            return None

        if not snapshots:
            return None

        latest = snapshots[0]
        trend = latest.trend
        if len(snapshots) > 1:
            delta = latest.people_estimate - snapshots[1].people_estimate
            threshold = max(int(zone.capacity * 0.02), 50)
            trend = "rising" if delta > threshold else "falling" if delta < -threshold else "steady"

        occupancy = latest.people_estimate / max(zone.capacity, 1)
        return ZoneReading(
            zone_id=zone.zone_id,
            zone_name=zone.name_en,
            zone_type=zone.zone_type,
            density_level=latest.density_level,
            people_estimate=latest.people_estimate,
            capacity=zone.capacity,
            occupancy_ratio=occupancy,
            wait_minutes=latest.wait_minutes
            or self._wait_minutes(zone.zone_type, latest.people_estimate),
            trend=trend,
            source="live",
            updated_at=latest.recorded_at,
            alternate_zone_ids=list(zone.alternate_zone_ids or []),
        )

    def _estimate(self, zone_id: str) -> ZoneReading:
        """Deterministic stand-in used before the sensor feed is wired up.

        Occupancy is a stable per-zone base modulated by the time of day, so
        repeated calls within a minute agree with each other and the app does
        not flicker between contradictory numbers.
        """
        zone = ZONES_BY_ID.get(zone_id)
        if zone is None:
            raise ZoneNotFoundError(zone_id)

        now = now_ist()
        seed = int(hashlib.sha256(f"{zone_id}:{now:%Y-%m-%d-%H}".encode()).hexdigest()[:8], 16)
        base = 0.25 + (seed % 1000) / 1000 * 0.35  # 0.25 – 0.60

        if now.hour in _PEAK_HOURS.get(zone["zone_type"], set()):
            base += 0.28
        if now.hour in (1, 2, 3):
            base -= 0.15

        occupancy = max(0.02, min(base, 1.25))
        people = int(zone["capacity"] * occupancy)
        level = self.density_level(occupancy)

        return ZoneReading(
            zone_id=zone_id,
            zone_name=zone["name_en"],
            zone_type=zone["zone_type"],
            density_level=level,
            people_estimate=people,
            capacity=zone["capacity"],
            occupancy_ratio=occupancy,
            wait_minutes=self._wait_minutes(zone["zone_type"], people),
            trend="rising" if now.minute % 3 == 0 else "steady",
            source="estimated",
            updated_at=now_utc(),
            alternate_zone_ids=list(zone.get("alternate_zone_ids") or []),
        )

    @staticmethod
    def density_level(occupancy_ratio: float) -> str:
        if occupancy_ratio < 0.35:
            return "low"
        if occupancy_ratio < 0.65:
            return "moderate"
        if occupancy_ratio < 0.88:
            return "high"
        return "critical"

    @staticmethod
    def _wait_minutes(zone_type: str, people: int) -> int | None:
        throughput = _THROUGHPUT_PER_MIN.get(zone_type)
        if not throughput:
            return None
        return max(1, round(people / throughput))

    @staticmethod
    def _name(zone_id: str, language: str) -> str:
        zone = ZONES_BY_ID.get(zone_id)
        return localized_name(zone, language) if zone else zone_id

    async def _alternates(self, reading: ZoneReading, language: str) -> list[AlternateZone]:
        """Suggest quieter neighbours, but only when the zone is actually busy."""
        if reading.density_level in ("low", "moderate"):
            return []

        from app.services.geo import haversine_m

        origin = ZONES_BY_ID.get(reading.zone_id)
        out: list[AlternateZone] = []
        for alt_id in reading.alternate_zone_ids:
            if alt_id not in ZONES_BY_ID:
                continue
            try:
                alt = await self.read_zone(alt_id)
            except ZoneNotFoundError:
                continue
            if CONGESTION_FACTOR.get(alt.density_level, 1.0) >= CONGESTION_FACTOR.get(
                reading.density_level, 1.0
            ):
                continue
            alt_zone = ZONES_BY_ID[alt_id]
            distance = (
                int(haversine_m(origin["lat"], origin["lon"], alt_zone["lat"], alt_zone["lon"]))
                if origin
                else None
            )
            out.append(
                AlternateZone(
                    zone_id=alt_id,
                    name=localized_name(alt_zone, language),
                    density_level=alt.density_level,  # type: ignore[arg-type]
                    distance_m=distance,
                )
            )
        return out

    def _advice(
        self, reading: ZoneReading, alternates: list[AlternateZone], language: str
    ) -> str:
        zone_name = self._name(reading.zone_id, language)
        advice = t(
            f"crowd_{reading.density_level}",
            language,
            zone=zone_name,
            wait=reading.wait_minutes or "—",
        )
        if alternates:
            names = ", ".join(a.name for a in alternates)
            advice = f"{advice} {t('crowd_alternates', language, zones=names)}"
        return advice
