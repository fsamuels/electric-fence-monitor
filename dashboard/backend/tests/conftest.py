"""Ingest/storage tests need a real Postgres+Timescale instance -- exclusion
constraints and the hypertable itself have no meaningful sqlite/mock
equivalent. Skip these tests rather than fail hard when no DB is reachable
(e.g. a plain `pytest` run with no `docker compose up -d db` first).

Each test gets its own event loop (pytest-asyncio's default), but
app.db.engine's pool is created once at import time and caches asyncpg
connections bound to whichever loop first used it. Reusing that engine
across tests errors on "attached to a different loop". So each test builds
its own NullPool engine (never holds a connection between checkouts, so
nothing outlives the loop it was born in) and points app.db / app.ingest at
it for the test's duration.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import db as db_module
from app import ingest as ingest_module
from app.config import settings

TABLES = (
    "readings",
    "node_state",
    "calibrations",
    "fence_events",
    "node_assignments",
    "nodes",
    "locations",
    "ingest_state",
)


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)

    try:
        async with test_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- any connect failure means "skip", not "fail"
        await test_engine.dispose()
        pytest.skip(f"database not reachable: {exc}")

    test_session_local = async_sessionmaker(test_engine, expire_on_commit=False)

    original_db_session_local = db_module.SessionLocal
    original_ingest_session_local = ingest_module.SessionLocal
    db_module.SessionLocal = test_session_local
    ingest_module.SessionLocal = test_session_local

    async with test_session_local() as session:
        await session.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
        try:
            yield session
        finally:
            db_module.SessionLocal = original_db_session_local
            ingest_module.SessionLocal = original_ingest_session_local
            await test_engine.dispose()
