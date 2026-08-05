import os

from app import models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import backfill

DATABASE_URL = os.environ.get(
    "FENCE_DATABASE_URL", "postgresql+asyncpg://fence:fence@localhost:5432/fence"
)


async def test_backfill_writes_readings_and_seeds_calibration(db_engine):
    await backfill.run_backfill_async(
        days=2, scenario_name="normal", node_count=1, history_mode="none", database_url=DATABASE_URL
    )

    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_maker() as session:
        readings = (await session.execute(select(models.Reading))).scalars().all()
        assert len(readings) == 2 * 86400 // backfill.REPORT_INTERVAL_S

        assert await session.get(models.Node, "mock-0001") is not None

        calibrations = (await session.execute(select(models.Calibration))).scalars().all()
        assert len(calibrations) == 1

        state = await session.get(models.NodeState, "mock-0001")
        assert state is not None
        assert state.was_retained is False


async def test_backfill_uncalibrated_history_seeds_no_calibration(db_engine):
    await backfill.run_backfill_async(
        days=1,
        scenario_name="normal",
        node_count=1,
        history_mode="uncalibrated",
        database_url=DATABASE_URL,
    )

    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_maker() as session:
        calibrations = (await session.execute(select(models.Calibration))).scalars().all()
        assert calibrations == []
        # The node is still assigned -- only calibration is withheld.
        assignments = (await session.execute(select(models.NodeAssignment))).scalars().all()
        assert len(assignments) == 1


async def test_backfill_board_swap_produces_two_node_ids_and_event(db_engine):
    await backfill.run_backfill_async(
        days=4, scenario_name="normal", node_count=1, history_mode="board-swap", database_url=DATABASE_URL
    )

    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_maker() as session:
        nodes = {n.node_id for n in (await session.execute(select(models.Node))).scalars().all()}
        assert nodes == {"mock-0001", "mock-0001-b"}

        events = (await session.execute(select(models.FenceEvent))).scalars().all()
        assert any(e.kind == "board_swap" for e in events)

        locations = {loc.location_id for loc in (await session.execute(select(models.Location))).scalars().all()}
        assert locations == {"mock-loc-0001"}


async def test_backfill_relocation_produces_two_locations_and_event(db_engine):
    await backfill.run_backfill_async(
        days=4, scenario_name="normal", node_count=1, history_mode="relocation", database_url=DATABASE_URL
    )

    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_maker() as session:
        nodes = {n.node_id for n in (await session.execute(select(models.Node))).scalars().all()}
        assert nodes == {"mock-0001"}

        locations = {loc.location_id for loc in (await session.execute(select(models.Location))).scalars().all()}
        assert locations == {"mock-loc-0001", "mock-loc-0001-b"}

        events = (await session.execute(select(models.FenceEvent))).scalars().all()
        assert any(e.kind == "relocated" for e in events)


async def test_backfill_node_silent_scenario_stops_readings_before_now(db_engine):
    await backfill.run_backfill_async(
        days=10,
        scenario_name="node-silent",
        node_count=1,
        history_mode="none",
        database_url=DATABASE_URL,
    )

    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_maker() as session:
        readings = (await session.execute(select(models.Reading))).scalars().all()
        total_possible = 10 * 86400 // backfill.REPORT_INTERVAL_S
        # node-silent stops producing readings ~90% of the way through, so
        # fewer rows than a fully-reporting scenario over the same window.
        assert 0 < len(readings) < total_possible
