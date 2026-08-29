import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import close_db, get_engine, init_db
from app.services.facility_service import FacilityService


async def main() -> None:
    await init_db()
    factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        rows = await FacilityService(db).nearby(17.6795, 75.3295, radius_m=4000, limit=50)
    for row in rows[:16]:
        print(f"m={row.distance_m:6} distance={row.distance!r:14} parts={len(row.distance.split())}")
    await close_db()


asyncio.run(main())
