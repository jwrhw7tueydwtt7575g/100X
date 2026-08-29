"""Palkhi Live Tracking Service.

Tracks and simulates the real-time position of the Palkhi (saint's procession)
along the Wari pilgrimage route. Positions are cached in Redis (`palkhi:live`,
TTL 5 min) and persisted in PostgreSQL (`palkhi_live_position`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import PalkhiLivePosition, PalkhiRoutePoint
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

REDIS_PALKHI_KEY = "palkhi:live"
REDIS_PALKHI_INDEX_KEY = "palkhi:sim_index"
PALKHI_TTL_SECONDS = 300

INITIAL_ROUTE_WAYPOINTS = [
  {"sequence": 1, "lat": 18.5204, "lon": 73.8567, "place_name": "Pune (Sangamwadi Halt)", "scheduled_time": "06:00 AM"},
  {"sequence": 2, "lat": 18.5089, "lon": 73.9259, "place_name": "Hadapsar Palkhi Sthal", "scheduled_time": "09:30 AM"},
  {"sequence": 3, "lat": 18.4871, "lon": 74.0234, "place_name": "Loni Kalbhor", "scheduled_time": "01:00 PM"},
  {"sequence": 4, "lat": 18.4682, "lon": 74.2154, "place_name": "Yawat Halt", "scheduled_time": "06:00 PM"},
  {"sequence": 5, "lat": 18.3912, "lon": 74.3412, "place_name": "Varvand", "scheduled_time": "07:30 AM"},
  {"sequence": 6, "lat": 18.1517, "lon": 74.5772, "place_name": "Baramati", "scheduled_time": "12:00 PM"},
  {"sequence": 7, "lat": 18.1167, "lon": 75.0333, "place_name": "Indapur", "scheduled_time": "05:00 PM"},
  {"sequence": 8, "lat": 17.7210, "lon": 75.2890, "place_name": "Wakhari Ringan Sthal", "scheduled_time": "08:00 AM"},
  {"sequence": 9, "lat": 17.6778, "lon": 75.3283, "place_name": "Pandharpur Vitthal Mandir", "scheduled_time": "02:00 PM"},
]


class PalkhiService:
  def __init__(self, db: AsyncSession | None = None) -> None:
    self.db = db

  async def get_live_position(self) -> dict[str, Any]:
    """Read the current Palkhi position from Redis cache or database."""
    # 1. Try Redis
    redis_client = get_redis()
    if redis_client is not None:
      try:
        raw = await redis_client.get(REDIS_PALKHI_KEY)
        if raw:
          return json.loads(raw)
      except (RedisError, Exception) as exc:
        log.warning("palkhi_redis_read_failed", error=str(exc))

    # 2. Try DB
    if self.db is not None:
      try:
        stmt = select(PalkhiLivePosition).where(PalkhiLivePosition.id == "palkhi-current")
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row:
          pos_data = {
            "latitude": row.lat,
            "longitude": row.lon,
            "currentPlace": row.current_place_name,
            "nextPlace": row.next_place_name,
            "etaMinutes": row.eta_to_next,
            "updatedAt": row.updated_at.isoformat(),
            "isSimulated": True,
          }
          await self._cache_in_redis(pos_data)
          return pos_data
      except Exception as exc:
        log.warning("palkhi_db_read_failed", error=str(exc))

    # 3. Fallback default position (Pune)
    fallback = {
      "latitude": INITIAL_ROUTE_WAYPOINTS[0]["lat"],
      "longitude": INITIAL_ROUTE_WAYPOINTS[0]["lon"],
      "currentPlace": INITIAL_ROUTE_WAYPOINTS[0]["place_name"],
      "nextPlace": INITIAL_ROUTE_WAYPOINTS[1]["place_name"],
      "etaMinutes": 25,
      "updatedAt": datetime.now(timezone.utc).isoformat(),
      "isSimulated": True,
    }
    await self._cache_in_redis(fallback)
    return fallback

  async def advance_simulated_position(self) -> dict[str, Any]:
    """Advance the Palkhi simulator to the next route waypoint."""
    current_idx = 0
    redis_client = get_redis()
    if redis_client is not None:
      try:
        raw_idx = await redis_client.get(REDIS_PALKHI_INDEX_KEY)
        if raw_idx is not None:
          current_idx = (int(raw_idx) + 1) % len(INITIAL_ROUTE_WAYPOINTS)
        await redis_client.set(REDIS_PALKHI_INDEX_KEY, str(current_idx))
      except Exception:
        current_idx = 0

    cur = INITIAL_ROUTE_WAYPOINTS[current_idx]
    nxt = INITIAL_ROUTE_WAYPOINTS[(current_idx + 1) % len(INITIAL_ROUTE_WAYPOINTS)]

    pos_data = {
      "latitude": cur["lat"],
      "longitude": cur["lon"],
      "currentPlace": cur["place_name"],
      "nextPlace": nxt["place_name"],
      "etaMinutes": 20 + (current_idx * 5) % 40,
      "updatedAt": datetime.now(timezone.utc).isoformat(),
      "isSimulated": True,
    }

    # Save to Redis
    await self._cache_in_redis(pos_data)

    # Save to DB if available
    if self.db is not None:
      try:
        stmt = select(PalkhiLivePosition).where(PalkhiLivePosition.id == "palkhi-current")
        existing = (await self.db.execute(stmt)).scalar_one_or_none()
        if existing:
          existing.lat = cur["lat"]
          existing.lon = cur["lon"]
          existing.current_place_name = cur["place_name"]
          existing.next_place_name = nxt["place_name"]
          existing.eta_to_next = pos_data["etaMinutes"]
        else:
          new_row = PalkhiLivePosition(
            id="palkhi-current",
            lat=cur["lat"],
            lon=cur["lon"],
            current_place_name=cur["place_name"],
            next_place_name=nxt["place_name"],
            eta_to_next=pos_data["etaMinutes"],
          )
          self.db.add(new_row)
        await self.db.commit()
      except Exception as exc:
        log.warning("palkhi_db_write_failed", error=str(exc))

    return pos_data

  async def _cache_in_redis(self, data: dict[str, Any]) -> None:
    client = get_redis()
    if client is not None:
      try:
        await client.setex(REDIS_PALKHI_KEY, PALKHI_TTL_SECONDS, json.dumps(data))
      except Exception:
        pass
