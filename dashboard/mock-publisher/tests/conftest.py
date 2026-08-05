"""DB-backed tests need a real Postgres+Timescale instance -- see
dashboard/backend/tests/conftest.py for the same rationale (exclusion
constraints have no sqlite equivalent). Skipped rather than failed when
unreachable.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

DATABASE_URL = os.environ.get(
    "FENCE_DATABASE_URL", "postgresql+asyncpg://fence:fence@localhost:5432/fence"
)

TABLES = (
    "readings",
    "node_state",
    "calibrations",
    "fence_events",
    "node_assignments",
    "nodes",
    "locations",
)


@pytest.fixture
async def db_engine():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- any connect failure means "skip", not "fail"
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        await session.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()

    try:
        yield engine
    finally:
        await engine.dispose()
