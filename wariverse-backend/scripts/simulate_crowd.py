"""Run the crowd simulator on its own.

The API runs with `--workers 4`, and the simulator must run in exactly one
process — four copies would overwrite each other's readings every few minutes.
So the API disables it and this entrypoint owns it, as a single-replica service.

Delete this service and set `CROWD_SIMULATOR_ENABLED=false` everywhere once the
real CCTV ingestion is writing `crowd_density_readings`.
"""

from __future__ import annotations

import asyncio

import structlog

from app.config import settings
from app.db import close_db, init_db
from app.middleware.logging import configure_logging
from app.redis_client import close_redis, init_redis
from app.services.crowd_simulator import tick

log = structlog.get_logger("crowd-simulator")


async def main() -> None:
    configure_logging()
    await init_db()
    await init_redis()

    interval = settings.crowd_simulator_interval_seconds
    log.info("crowd_simulator_service_started", interval_seconds=interval)
    try:
        while True:
            try:
                await tick()
            except Exception:  # noqa: BLE001 — one bad pass must not end the loop
                log.exception("crowd_tick_failed")
            await asyncio.sleep(interval)
    finally:
        await close_redis()
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
