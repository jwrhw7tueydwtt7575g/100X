"""Nearby-facility search: water, toilets, medical posts, langars, shelters.

Postgres prefilters candidates with a bounding box on the (lat, lon) index and
the exact haversine distance is computed in Python — accurate to metres over
the temple precinct without requiring PostGIS.

Distances and availability are returned as **rendered strings** (`"0.8 km"`,
`"Open · Volunteer staffed"`) because the frontend prints them verbatim; the
numeric distance rides along in excluded fields for internal callers.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.i18n import t
from app.data.reference import FACILITIES, localized_name
from app.models.db_models import Facility
from app.models.schemas import FacilityOut
from app.redis_client import get_redis
from app.services.geo import bounding_box, haversine_m, walk_minutes
from app.utils import is_open_now

log = structlog.get_logger(__name__)

# Types a pilgrim should still be routed to even when marked closed.
_ALWAYS_RELEVANT = {"medical", "police"}

# Categories a community member can offer. Police posts and lost & found desks
# are official-only.
SEVA_CATEGORIES = ("food", "accommodation", "water", "medical", "rest")

MAPBOX_CATEGORY_MAP = {
    "medical": "hospital,pharmacy,clinic,doctor",
    "police": "police",
    "food": "restaurant,food,fast_food",
    "accommodation": "lodging,hotel",
    "toilet": "restroom,public_toilet",
    "water": "drinking_water",
}


async def check_mapbox_rate_limit(
    key: str = "wv:mapbox:ratelimit", max_requests: int = 20, window_seconds: int = 60
) -> bool:
    client = get_redis()
    if client is None:
        return True
    try:
        current = await client.incr(key)
        if current == 1:
            await client.expire(key, window_seconds)
        return current <= max_requests
    except Exception as exc:
        log.warning("mapbox_ratelimit_check_failed", error=str(exc))
        return True


import math

def get_bearing_direction(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Calculates compass heading from (lat1, lon1) to (lat2, lon2)."""
    dlon = math.radians(lon2 - lon1)
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(x, y)) + 360) % 360
    compass = ["North", "North-East", "East", "South-East", "South", "South-West", "West", "North-West"]
    return compass[int((bearing + 22.5) // 45) % 8]


def format_distance(metres: float, user_lat: float | None = None, user_lon: float | None = None, fac_lat: float | None = None, fac_lon: float | None = None) -> str:
    """`0.8 km`, or metres when that would round to `0.0 km`."""
    dist_str = f"{round(metres)} m" if metres < 100 else f"{metres / 1000:.1f} km"
    if user_lat is not None and user_lon is not None and fac_lat is not None and fac_lon is not None:
        direction = get_bearing_direction(user_lat, user_lon, fac_lat, fac_lon)
        mins = max(1, int(round(metres / 80.0)))
        return f"{dist_str} ({direction} • {mins} min walk)"
    return dist_str


def format_availability(row: dict[str, Any], is_open: bool, language: str) -> str:
    """`Open · Volunteer staffed` — status first, then who runs it."""
    status = t("facility_open" if is_open else "facility_closed", language)
    staffing = (row.get("details") or {}).get("staffing")
    return f"{status} · {staffing}" if staffing else status


class FacilityService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def nearby(
        self,
        lat: float,
        lon: float,
        radius_m: int | None = None,
        facility_types: list[str] | None = None,
        limit: int = 20,
        language: str = "en",
        open_only: bool = False,
        congestion_factor: float = 1.0,
    ) -> list[FacilityOut]:
        radius = min(radius_m or settings.facility_default_radius_m, settings.facility_max_radius_m)

        # Retrieve DB / reference candidates
        rows = await self._candidates(lat, lon, radius, facility_types)

        # Retrieve Mapbox live POI candidates if token available and rate limit ok
        mapbox_rows = await self._fetch_mapbox_pois(lat, lon, facility_types)
        all_rows = mapbox_rows + rows

        results: list[FacilityOut] = []
        seen_names = set()

        for row in all_rows:
            distance = haversine_m(lat, lon, row["lat"], row["lon"])
            if distance > radius:
                continue

            name = localized_name(row, language) if "name_en" in row or "name" in row else row.get("name", "")
            dedup_key = (row["facility_type"], name.lower())
            if dedup_key in seen_names:
                continue
            seen_names.add(dedup_key)

            is_open = is_open_now(
                row.get("opens_at"), row.get("closes_at"), row.get("is_24x7", False)
            )
            if open_only and not is_open and row["facility_type"] not in _ALWAYS_RELEVANT:
                continue

            contact = row.get("contact_phone") or row.get("phone")

            results.append(
                FacilityOut(
                    id=str(row.get("external_id") or row.get("id") or f"mb-{len(results)}"),
                    category=row["facility_type"],
                    name=name,
                    distance=format_distance(distance, lat, lon, row["lat"], row["lon"]),
                    latitude=row["lat"],
                    longitude=row["lon"],
                    availability=format_availability(row, is_open, language),
                    contact=contact,
                    phone=contact,
                    distance_m=int(round(distance)),
                    walk_minutes=walk_minutes(
                        distance, settings.walking_speed_kmph, congestion_factor
                    ),
                    is_open=is_open,
                )
            )

        results.extend(
            await self._community(lat, lon, radius, facility_types, language)
        )
        results.sort(key=lambda f: f.distance_m)
        return results[:limit]

    async def _community(
        self,
        lat: float,
        lon: float,
        radius_m: int,
        facility_types: list[str] | None,
        language: str,
    ) -> list[FacilityOut]:
        """Live seva offerings, as facilities the caller can treat uniformly.

        Merged here rather than in the router so that every consumer — the
        `/nearby` endpoint, the LLM's `get_nearby_facility` tool, and SOS
        responder lookup — sees them without extra wiring.
        """
        from app.services.community_service import CommunityServiceRepo

        # Only the six pilgrim-facing categories exist as seva; asking for a
        # police post should not run this query at all.
        wanted = [c for c in (facility_types or SEVA_CATEGORIES) if c in SEVA_CATEGORIES]
        if not wanted:
            return []

        offerings = await CommunityServiceRepo(self.db).active(
            lat=lat, lon=lon, radius_m=radius_m, categories=wanted
        )

        out: list[FacilityOut] = []
        for offering in offerings:
            distance = haversine_m(lat, lon, offering.latitude, offering.longitude)
            out.append(
                FacilityOut(
                    id=offering.id,
                    category=offering.category,  # type: ignore[arg-type]
                    name=offering.title,
                    distance=format_distance(distance),
                    latitude=offering.latitude,
                    longitude=offering.longitude,
                    availability=t(
                        "facility_seva_open", language, provider=offering.provider_name
                    ),
                    contact=offering.contact_phone,
                    is_seva=True,
                    provider_name=offering.provider_name,
                    available_until=offering.available_until,
                    distance_m=int(round(distance)),
                    walk_minutes=walk_minutes(distance, settings.walking_speed_kmph),
                    is_open=True,  # `active()` already filtered to the live window
                )
            )
        return out

    async def nearest(
        self,
        lat: float,
        lon: float,
        facility_types: list[str],
        radius_m: int = 5000,
        language: str = "en",
    ) -> FacilityOut | None:
        found = await self.nearby(
            lat, lon, radius_m=radius_m, facility_types=facility_types, limit=1,
            language=language,
        )
        return found[0] if found else None

    # --- internals ---------------------------------------------------------

    async def _fetch_mapbox_pois(
        self, lat: float, lon: float, facility_types: list[str] | None
    ) -> list[dict[str, Any]]:
        token = settings.mapbox_access_token
        if not token:
            return []

        if not await check_mapbox_rate_limit():
            log.warning("mapbox_facility_rate_limited")
            return []

        categories_to_query = []
        if facility_types:
            for ft in facility_types:
                if ft in MAPBOX_CATEGORY_MAP:
                    categories_to_query.append((ft, MAPBOX_CATEGORY_MAP[ft]))
        else:
            categories_to_query = [(k, v) for k, v in MAPBOX_CATEGORY_MAP.items()]

        if not categories_to_query:
            return []

        results = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            for category_type, mapbox_cat in categories_to_query:
                cat_results = []
                try:
                    # 1. Primary: Category Search Box API
                    url = f"https://api.mapbox.com/search/searchbox/v1/category/{mapbox_cat}"
                    params = {
                        "access_token": token,
                        "proximity": f"{lon},{lat}",
                        "radius": 10000,
                        "limit": 5,
                    }
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        for feat in data.get("features", []):
                            props = feat.get("properties", {})
                            geom = feat.get("geometry", {})
                            coords = geom.get("coordinates", [lon, lat])
                            f_lon, f_lat = coords[0], coords[1]
                            meta = props.get("metadata") or {}
                            phone = meta.get("phone") or meta.get("telephone") or props.get("phone") or props.get("telephone")
                            mb_id = props.get("mapbox_id") or props.get("id") or len(results)
                            cat_results.append(
                                {
                                    "id": f"fac-mb-{mb_id}",
                                    "external_id": f"fac-mb-{mb_id}",
                                    "name": props.get("name") or "Nearby Facility",
                                    "name_en": props.get("name") or "Nearby Facility",
                                    "facility_type": category_type,
                                    "lat": f_lat,
                                    "lon": f_lon,
                                    "address": props.get("full_address") or props.get("address"),
                                    "contact_phone": phone,
                                    "phone": phone,
                                    "is_24x7": True,
                                    "is_operational": True,
                                    "details": {"staffing": "Mapbox Verified POI"},
                                }
                            )

                    # 2. Fallback: Forward Search API if Category search yields 0 POIs
                    if not cat_results:
                        f_url = "https://api.mapbox.com/search/searchbox/v1/forward"
                        f_params = {
                            "q": category_type,
                            "access_token": token,
                            "proximity": f"{lon},{lat}",
                            "limit": 5,
                        }
                        f_resp = await client.get(f_url, params=f_params)
                        if f_resp.status_code == 200:
                            f_data = f_resp.json()
                            for feat in f_data.get("features", []):
                                props = feat.get("properties", {})
                                geom = feat.get("geometry", {})
                                coords = geom.get("coordinates", [lon, lat])
                                f_lon, f_lat = coords[0], coords[1]
                                meta = props.get("metadata") or {}
                                phone = meta.get("phone") or meta.get("telephone") or props.get("phone") or props.get("telephone")
                                mb_id = props.get("mapbox_id") or props.get("id") or len(results)
                                cat_results.append(
                                    {
                                        "id": f"fac-mb-{mb_id}",
                                        "external_id": f"fac-mb-{mb_id}",
                                        "name": props.get("name") or "Nearby Facility",
                                        "name_en": props.get("name") or "Nearby Facility",
                                        "facility_type": category_type,
                                        "lat": f_lat,
                                        "lon": f_lon,
                                        "address": props.get("full_address") or props.get("address"),
                                        "contact_phone": phone,
                                        "phone": phone,
                                        "is_24x7": True,
                                        "is_operational": True,
                                        "details": {"staffing": "Mapbox Verified POI"},
                                    }
                                )
                    results.extend(cat_results)
                except Exception as exc:  # noqa: BLE001
                    log.warning("mapbox_fetch_error", category=category_type, error=str(exc))

        return results

    async def _candidates(
        self, lat: float, lon: float, radius_m: int, facility_types: list[str] | None
    ) -> list[dict[str, Any]]:
        rows = await self._from_db(lat, lon, radius_m, facility_types)
        if rows:
            return rows

        # Empty database (fresh deploy, or Postgres unreachable): fall back to
        # the bundled reference dataset so safety lookups still answer.
        fallback = [dict(f) for f in FACILITIES if f.get("is_operational", True)]
        if facility_types:
            wanted = set(facility_types)
            fallback = [f for f in fallback if f["facility_type"] in wanted]
        return fallback

    async def _from_db(
        self, lat: float, lon: float, radius_m: int, facility_types: list[str] | None
    ) -> list[dict[str, Any]]:
        if self.db is None:
            return []

        min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_m)
        stmt = select(Facility).where(
            Facility.is_operational.is_(True),
            Facility.lat.between(min_lat, max_lat),
            Facility.lon.between(min_lon, max_lon),
        )
        if facility_types:
            stmt = stmt.where(Facility.facility_type.in_(facility_types))

        try:
            facilities = (await self.db.execute(stmt.limit(200))).scalars().all()
        except Exception as exc:  # noqa: BLE001
            log.warning("facility_db_read_failed", error=str(exc))
            return []

        return [self._to_dict(f) for f in facilities]

    @staticmethod
    def _to_dict(facility: Facility) -> dict[str, Any]:
        return {
            "id": facility.id,
            "external_id": facility.external_id,
            "name_en": facility.name_en,
            "name_mr": facility.name_mr,
            "name_hi": facility.name_hi,
            "facility_type": facility.facility_type,
            "lat": facility.lat,
            "lon": facility.lon,
            "address": facility.address,
            "contact_phone": facility.contact_phone,
            "opens_at": facility.opens_at,
            "closes_at": facility.closes_at,
            "is_24x7": facility.is_24x7,
            "capacity": facility.capacity,
            "wheelchair_accessible": facility.wheelchair_accessible,
            "details": facility.details,
        }

