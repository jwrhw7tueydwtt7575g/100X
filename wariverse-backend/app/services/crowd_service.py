"""Crowd density for the six monitored Pandharpur zones.

Read path: Redis (`crowd:{zone_id}`, 5 min TTL) → latest
`crowd_density_readings` row → the time-of-day curve below. Every reading
carries a `source` so a modelled number is never mistaken for a measurement.

`density` is an integer 0-100 percentage of zone capacity. `status` buckets it,
and the two are always written together so no consumer has to re-derive the
thresholds.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.i18n import t
from app.data.reference import ZONES, ZONES_BY_ID, localized_name
from app.models.db_models import CrowdDensityReading
from app.models.schemas import AlternateZone
from app.redis_client import get_redis
from app.utils import now_ist, now_utc

log = structlog.get_logger(__name__)

CACHE_PREFIX = "crowd:"
CACHE_TTL_SECONDS = 300  # 5 minutes

# --- status thresholds ------------------------------------------------------
# LOW <30, MODERATE 30-60, HIGH 60-85, VERY_HIGH >85.
DENSITY_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (30, "LOW"),
    (61, "MODERATE"),
    (86, "HIGH"),
)
VERY_HIGH = "VERY_HIGH"


def density_to_status(density: int) -> str:
    """Bucket a 0-100 density into the stored status vocabulary."""
    for ceiling, status in DENSITY_THRESHOLDS:
        if density < ceiling:
            return status
    return VERY_HIGH


# Multiplier applied to walking times when moving through a crowd.
CONGESTION_FACTOR: dict[str, float] = {
    "LOW": 1.0,
    "MODERATE": 1.3,
    "HIGH": 1.8,
    "VERY_HIGH": 2.6,
}

# --- the daily curve --------------------------------------------------------
# Baseline density by hour (IST) on a Wari peak day. The main peak is 10:00-14:00
# — the mid-morning darshan rush, when heat also slows movement — with a second,
# smaller evening rise around the aartis. The 03:00-05:00 climb is the kakad
# aarti crowd arriving before dawn.
#
# Replace this with the fitted curve once `crowd_density_readings` has real
# history; it is deliberately a single hardcoded shape, not a trained model.
HOURLY_CURVE: dict[int, int] = {
    0: 12, 1: 9, 2: 8, 3: 14, 4: 26, 5: 38,
    6: 46, 7: 52, 8: 58, 9: 68, 10: 80, 11: 88,
    12: 90, 13: 86, 14: 76, 15: 64, 16: 58, 17: 60,
    18: 68, 19: 71, 20: 62, 21: 47, 22: 31, 23: 19,
}

# How busy each zone runs relative to that curve. The temple courtyard is the
# bottleneck; the approach road disperses people along its length.
ZONE_INTENSITY: dict[str, float] = {
    "gate-1": 0.95,
    "gate-2": 1.05,
    "gate-3": 0.85,
    "temple-main": 1.12,
    "bhima-ghat": 0.90,
    "main-road": 0.75,
    "mukhdarshan-queue": 0.80,
    "darshan-mandap-token": 0.92,
    "padsparsha-queue": 1.18,
    "chandrabhaga-riverbank": 0.95,
}

# Bathing happens at dawn, so the ghat peaks hours before the temple does.
# A POSITIVE shift reads the curve ahead of the clock, which moves the peak
# EARLIER: at 08:00 the ghat sees the curve's 12:00 value.
_ZONE_HOUR_SHIFT: dict[str, int] = {"bhima-ghat": 4}


def curve_density(zone_id: str, at: datetime | None = None) -> int:
    """Modelled density for a zone at a moment, from the hardcoded curve.

    Interpolates between hours so the number moves smoothly rather than
    jumping on the hour.
    """
    moment = at or now_ist()
    shift = _ZONE_HOUR_SHIFT.get(zone_id, 0)

    hour = (moment.hour + shift) % 24
    next_hour = (hour + 1) % 24
    fraction = moment.minute / 60

    base = HOURLY_CURVE[hour] * (1 - fraction) + HOURLY_CURVE[next_hour] * fraction
    scaled = base * ZONE_INTENSITY.get(zone_id, 1.0)
    return max(0, min(round(scaled), 100))


# People cleared per minute, by zone type. Turns a headcount into a wait.
_THROUGHPUT_PER_MIN: dict[str, int] = {
    "queue": 220,
    "temple": 180,
    "ghat": 600,
    "corridor": 800,
    "transit": 300,
}


class ZoneNotFoundError(LookupError):
    """Raised when the requested zone_id is not registered."""


@dataclass(slots=True)
class ZoneReading:
    zone_id: str
    zone_name: str
    zone_type: str
    density: int
    status: str
    latitude: float
    longitude: float
    people_estimate: int
    capacity: int
    wait_minutes: int | None
    trend: str
    source: str
    recorded_at: datetime

    @property
    def density_level(self) -> str:
        """Alias used by the route and orchestration services."""
        return self.status


def build_reading(
    zone_id: str, density: int, source: str, recorded_at: datetime | None = None,
    trend: str = "steady", language: str = "en",
) -> ZoneReading:
    """Assemble a reading from a density value plus static zone metadata."""
    zone = ZONES_BY_ID.get(zone_id)
    if zone is None:
        raise ZoneNotFoundError(zone_id)

    density = max(0, min(int(density), 100))
    people = round(zone["capacity"] * density / 100)
    return ZoneReading(
        zone_id=zone_id,
        zone_name=localized_name(zone, language),
        zone_type=zone["zone_type"],
        density=density,
        status=density_to_status(density),
        latitude=zone["lat"],
        longitude=zone["lon"],
        people_estimate=people,
        capacity=zone["capacity"],
        wait_minutes=_wait_minutes(zone["zone_type"], people),
        trend=trend,
        source=source,
        recorded_at=recorded_at or now_utc(),
    )


def _wait_minutes(zone_type: str, people: int) -> int | None:
    throughput = _THROUGHPUT_PER_MIN.get(zone_type)
    if not throughput:
        return None
    return max(1, round(people / throughput))


class CrowdService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    # --- reads -------------------------------------------------------------

    async def read_zone(self, zone_id: str, language: str = "en") -> ZoneReading:
        """Current reading: Redis → Postgres → the curve."""
        if zone_id not in ZONES_BY_ID:
            raise ZoneNotFoundError(zone_id)

        if cached := await self._read_cache(zone_id, language):
            return cached
        if stored := await self._read_db(zone_id, language):
            await self._write_cache(stored)
            return stored

        # Nothing recorded yet: the curve is the honest answer, labelled as a
        # model rather than a measurement.
        return build_reading(
            zone_id, curve_density(zone_id), source="model", language=language
        )

    async def read_all(self, language: str = "en") -> list[ZoneReading]:
        return [await self.read_zone(zone["zone_id"], language) for zone in ZONES]

    async def forecast(
        self, zone_id: str, hours: int = 12, language: str = "en"
    ) -> list[dict[str, Any]]:
        """Hourly projection, anchored on the current reading.

        The first point is now; later points converge on the curve, so a
        forecast never contradicts the density the pilgrim is looking at.
        """
        if zone_id not in ZONES_BY_ID:
            raise ZoneNotFoundError(zone_id)

        current = await self.read_zone(zone_id, language)
        start = now_ist()

        points: list[dict[str, Any]] = []
        for offset in range(hours):
            at = start + timedelta(hours=offset)
            modelled = curve_density(zone_id, at)

            # Blend away from the live reading over the first few hours.
            weight = min(offset / 3, 1.0)
            value = round(current.density * (1 - weight) + modelled * weight)
            value = max(0, min(value, 100))

            points.append(
                {
                    "time": format_hour(at),
                    "hour": at.hour,
                    "value": value,
                    "status": density_to_status(value),
                }
            )
        return points

    def recommendation(
        self, zone_id: str, points: list[dict[str, Any]], language: str = "en"
    ) -> str:
        """One sentence naming the best time to go."""
        zone_name = localized_name(ZONES_BY_ID[zone_id], language)
        if not points:
            return ""

        busy_threshold = 60
        first_busy = next((p for p in points if p["value"] >= busy_threshold), None)

        # Quiet now, busy later → go before it fills up.
        if points[0]["value"] < busy_threshold and first_busy is not None:
            return t(
                "forecast_before", language, zone=zone_name, time=first_busy["time"]
            )

        # Busy now → name the FIRST hour that eases, not the quietest one.
        # The global minimum is always the small hours, and "come back at 1 AM"
        # is useless advice to someone standing in a queue at 4 PM.
        if points[0]["value"] >= busy_threshold:
            relief = next(
                (p for p in points[1:] if p["value"] < busy_threshold), None
            )
            if relief is not None:
                return t(
                    "forecast_after", language, zone=zone_name, time=relief["time"]
                )

        if first_busy is None:
            return t("forecast_all_clear", language, zone=zone_name)

        quiet = min(points, key=lambda p: p["value"])
        return t("forecast_quietest", language, zone=zone_name, time=quiet["time"])

    # --- writes ------------------------------------------------------------

    async def record(
        self, zone_id: str, density: int, source: str = "model", persist: bool = True
    ) -> ZoneReading:
        """Store a new reading in Redis and (optionally) Postgres."""
        previous = await self._read_cache(zone_id) or await self._read_db(zone_id)
        trend = "steady"
        if previous is not None:
            delta = density - previous.density
            trend = "rising" if delta > 2 else "falling" if delta < -2 else "steady"

        reading = build_reading(zone_id, density, source=source, trend=trend)
        await self._write_cache(reading)

        if persist and self.db is not None:
            try:
                self.db.add(
                    CrowdDensityReading(
                        zone_id=reading.zone_id,
                        zone_name=ZONES_BY_ID[zone_id]["name_en"],
                        density=reading.density,
                        status=reading.status,
                        latitude=reading.latitude,
                        longitude=reading.longitude,
                        source=source,
                    )
                )
                await self.db.commit()
            except Exception as exc:  # noqa: BLE001 — a cached reading still serves
                log.warning("crowd_persist_failed", zone_id=zone_id, error=str(exc))
                await self.db.rollback()

        return reading

    # --- helpers used by other services ------------------------------------

    async def alternates(self, reading: ZoneReading, language: str) -> list[AlternateZone]:
        """Quieter neighbours, offered only when the zone is actually busy."""
        if reading.status in ("LOW", "MODERATE"):
            return []

        from app.services.geo import haversine_m

        origin = ZONES_BY_ID[reading.zone_id]
        out: list[AlternateZone] = []
        for alt_id in origin.get("alternate_zone_ids") or []:
            if alt_id not in ZONES_BY_ID:
                continue
            alt = await self.read_zone(alt_id, language)
            if CONGESTION_FACTOR[alt.status] >= CONGESTION_FACTOR[reading.status]:
                continue
            out.append(
                AlternateZone(
                    zone_id=alt_id,
                    name=alt.zone_name,
                    density_level=alt.status,  # type: ignore[arg-type]
                    distance_m=int(
                        haversine_m(
                            origin["lat"], origin["lon"],
                            ZONES_BY_ID[alt_id]["lat"], ZONES_BY_ID[alt_id]["lon"],
                        )
                    ),
                )
            )
        return out

    async def advice(self, reading: ZoneReading, language: str) -> str:
        """Localized guidance for a reading, plus quieter alternatives."""
        text = t(
            f"crowd_{reading.status.lower()}",
            language,
            zone=reading.zone_name,
            wait=reading.wait_minutes or "—",
        )
        alternates = await self.alternates(reading, language)
        if alternates:
            names = ", ".join(a.name for a in alternates)
            text = f"{text} {t('crowd_alternates', language, zones=names)}"
        return text

    async def nearest_zone_id(self, lat: float, lon: float) -> str | None:
        """Zone whose centre is closest to a point, within 5 km."""
        from app.services.geo import haversine_m

        best: tuple[str, float] | None = None
        for zone in ZONES:
            distance = haversine_m(lat, lon, zone["lat"], zone["lon"])
            if best is None or distance < best[1]:
                best = (zone["zone_id"], distance)
        return best[0] if best and best[1] <= 5000 else None

    def congestion_factor(self, status: str) -> float:
        return CONGESTION_FACTOR.get(status, 1.0)

    @staticmethod
    def density_level(density: int) -> str:
        return density_to_status(density)

    # --- storage -----------------------------------------------------------

    async def _read_cache(self, zone_id: str, language: str = "en") -> ZoneReading | None:
        client = get_redis()
        if client is None:
            return None
        try:
            raw = await client.get(f"{CACHE_PREFIX}{zone_id}")
        except (RedisError, OSError) as exc:
            import app.redis_client as rc
            rc._healthy = False
            log.debug("crowd_cache_read_failed", zone_id=zone_id, error=str(exc))
            return None
        if not raw:
            return None

        try:
            payload = json.loads(raw)
            return build_reading(
                zone_id,
                payload["density"],
                source=payload.get("source", "model"),
                recorded_at=datetime.fromisoformat(payload["recorded_at"]),
                trend=payload.get("trend", "steady"),
                language=language,
            )
        except (json.JSONDecodeError, KeyError, ValueError, ZoneNotFoundError):
            log.warning("crowd_cache_malformed", zone_id=zone_id)
            return None

    async def _write_cache(self, reading: ZoneReading) -> None:
        client = get_redis()
        if client is None:
            return
        try:
            await client.set(
                f"{CACHE_PREFIX}{reading.zone_id}",
                json.dumps(
                    {
                        "density": reading.density,
                        "status": reading.status,
                        "source": reading.source,
                        "trend": reading.trend,
                        "recorded_at": reading.recorded_at.isoformat(),
                    }
                ),
                ex=CACHE_TTL_SECONDS,
            )
        except (RedisError, OSError) as exc:
            import app.redis_client as rc
            rc._healthy = False
            log.debug("crowd_cache_write_failed", zone_id=reading.zone_id, error=str(exc))

    async def _read_db(self, zone_id: str, language: str = "en") -> ZoneReading | None:
        if self.db is None:
            return None
        try:
            rows = (
                (
                    await self.db.execute(
                        select(CrowdDensityReading)
                        .where(CrowdDensityReading.zone_id == zone_id)
                        .order_by(desc(CrowdDensityReading.recorded_at))
                        .limit(2)
                    )
                )
                .scalars()
                .all()
            )
        except Exception as exc:  # noqa: BLE001 — never fail a safety lookup on DB errors
            log.warning("crowd_db_read_failed", zone_id=zone_id, error=str(exc))
            return None

        if not rows:
            return None

        latest = rows[0]
        trend = "steady"
        if len(rows) > 1:
            delta = latest.density - rows[1].density
            trend = "rising" if delta > 2 else "falling" if delta < -2 else "steady"

        return build_reading(
            zone_id,
            latest.density,
            source=latest.source,
            recorded_at=latest.recorded_at,
            trend=trend,
            language=language,
        )


def format_hour(moment: datetime) -> str:
    """`8 AM`, `12 PM`, `3 PM` — no leading zero, which strftime cannot do portably."""
    hour = moment.hour % 12 or 12
    return f"{hour} {'AM' if moment.hour < 12 else 'PM'}"


# Kept for the dataclass-dict round trip used by callers that cache readings.
def reading_to_dict(reading: ZoneReading) -> dict[str, Any]:
    payload = asdict(reading)
    payload["recorded_at"] = reading.recorded_at.isoformat()
    return payload
