"""Async Redis client used for crowd caching, OTP storage and chat sessions.

Redis is treated as a cache, never as the only copy of anything that matters.
`get_redis()` returns None when the server is unreachable so callers can fall
back to Postgres or to an in-process store.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings

log = structlog.get_logger(__name__)

_client: Redis | None = None
_healthy: bool = False


async def init_redis() -> bool:
    global _client, _healthy

    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )

    try:
        await _client.ping()
        _healthy = True
        log.info("redis_connected", url=_redacted_url())
    except (RedisError, OSError) as exc:
        _healthy = False
        log.error("redis_connect_failed", error=str(exc))

    return _healthy


async def close_redis() -> None:
    global _client, _healthy
    if _client is not None:
        await _client.aclose()
        log.info("redis_closed")
    _client = None
    _healthy = False


def get_redis() -> Redis | None:
    """Return the shared client, or None when Redis was never reachable."""
    return _client if _healthy else None


async def ping_redis() -> bool:
    global _healthy
    if _client is None:
        return False
    try:
        await _client.ping()
        _healthy = True
    except (RedisError, OSError):
        _healthy = False
    return _healthy


async def cache_get_json(key: str) -> Any | None:
    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except (RedisError, OSError) as exc:
        log.warning("redis_get_failed", key=key, error=str(exc))
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("redis_value_not_json", key=key)
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int | None = None) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        await client.set(
            key,
            json.dumps(value, default=str),
            ex=ttl_seconds or settings.redis_ttl_seconds,
        )
        return True
    except (RedisError, OSError) as exc:
        log.warning("redis_set_failed", key=key, error=str(exc))
        return False


async def cache_delete(*keys: str) -> None:
    client = get_redis()
    if client is None or not keys:
        return
    try:
        await client.delete(*keys)
    except (RedisError, OSError) as exc:
        log.warning("redis_delete_failed", keys=keys, error=str(exc))


def _redacted_url() -> str:
    url = settings.redis_url
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.rpartition('@')[2]}"
    return url
