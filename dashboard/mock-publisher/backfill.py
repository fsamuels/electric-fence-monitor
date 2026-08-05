"""Backfill mode -- D2. Writes N days of history at today's real 600s
spacing directly to Postgres, ending now, instead of publishing over MQTT
(see docs/dashboard-plan.md's Mock publisher section: "Backfill writes
straight to Postgres deliberately").

Shares `app/models.py` with the API/ingest service (see Dockerfile) so the
schema has one source of truth; the insert/upsert helpers below are a small,
backfill-specific subset of what ingest.py does for live messages -- no MQTT,
no contract-schema validation, since these payloads are generated in-process
by scenarios.build_payload and are already contract-shaped.

`--history` additionally seeds a board-swap, relocation, or uncalibrated
scenario alongside the base fence-condition scenario, so those exercise the
identity/calibration machinery (D2 checklist) without needing real hardware.
Re-running backfill for the same node/location without clearing the DB will
hit the node_assignments exclusion constraint (overlapping windows) -- this
is a dev tool, not an idempotent seeder.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app import models
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scenarios import KV_PER_MV, build_payload, make_scenario

log = logging.getLogger("mock-publisher")

REPORT_INTERVAL_S = 600  # today's single-interval firmware model


@dataclass
class Segment:
    node_id: str
    location_id: str
    valid_from: datetime
    valid_to: datetime | None
    calibrated: bool
    reading_start: int
    reading_end: int  # exclusive


def run_backfill(
    days: int, scenario_name: str, node_count: int, history_mode: str, database_url: str
) -> None:
    asyncio.run(run_backfill_async(days, scenario_name, node_count, history_mode, database_url))


async def run_backfill_async(
    days: int, scenario_name: str, node_count: int, history_mode: str, database_url: str
) -> None:
    engine = create_async_engine(database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    total_readings = max(1, (days * 86400) // REPORT_INTERVAL_S)
    start = now - timedelta(seconds=total_readings * REPORT_INTERVAL_S)

    async with session_maker() as session:
        for n in range(1, node_count + 1):
            await _backfill_one_node(session, n, scenario_name, history_mode, start, now, total_readings)
        await session.commit()

    await engine.dispose()
    log.info(
        "backfill complete: %d node(s), %d readings each, history=%s, %s -> %s",
        node_count,
        total_readings,
        history_mode,
        start.isoformat(),
        now.isoformat(),
    )


async def _backfill_one_node(session, index, scenario_name, history_mode, start, now, total_readings) -> None:
    primary_node_id = f"mock-{index:04d}"
    location_id = f"mock-loc-{index:04d}"

    scenario_kwargs = {}
    if scenario_name in ("slow-decline", "battery-drain"):
        scenario_kwargs = {"span": total_readings}
    elif scenario_name == "node-silent":
        scenario_kwargs = {"silent_after": max(1, int(total_readings * 0.9))}
    scenario = make_scenario(scenario_name, **scenario_kwargs)

    segments = _build_segments(primary_node_id, location_id, history_mode, start, total_readings)

    for seg in segments:
        await _upsert_location(session, seg.location_id, now)
        await _upsert_node(session, seg.node_id, seg.valid_from)
        await _insert_assignment(session, seg.node_id, seg.location_id, seg.valid_from, seg.valid_to)
        if seg.calibrated:
            await _insert_calibration(session, seg.node_id, seg.location_id, seg.valid_from, seg.valid_to)

    last_payload, last_ts, last_node_id = None, None, None
    for i in range(total_readings):
        if scenario.is_silent(i):
            # node-silent: history simply stops here and stays stopped up to
            # `now`, matching what a real dead node's row set looks like.
            break

        ts = start + timedelta(seconds=i * REPORT_INTERVAL_S)
        seg = _segment_for(segments, i)
        payload = build_payload(
            seg.node_id,
            scenario,
            i,
            boot=i - seg.reading_start + 1,
            report_interval_s=REPORT_INTERVAL_S,
            sample_interval_s=REPORT_INTERVAL_S,
        )
        await _insert_reading(session, seg.node_id, payload, ts)
        last_payload, last_ts, last_node_id = payload, ts, seg.node_id

    await _seed_fence_event(session, history_mode, primary_node_id, location_id, segments, start)

    if last_payload is not None:
        await _upsert_node_state(session, last_node_id, last_payload, last_ts, now)


def _build_segments(primary_node_id, location_id, history_mode, start, total_readings) -> list[Segment]:
    if history_mode == "uncalibrated":
        return [Segment(primary_node_id, location_id, start, None, False, 0, total_readings)]
    if history_mode == "none":
        return [Segment(primary_node_id, location_id, start, None, True, 0, total_readings)]

    mid_i = max(1, min(total_readings - 1, total_readings // 2))
    mid_ts = start + timedelta(seconds=mid_i * REPORT_INTERVAL_S)

    if history_mode == "board-swap":
        swapped_node_id = f"{primary_node_id}-b"
        return [
            Segment(primary_node_id, location_id, start, mid_ts, True, 0, mid_i),
            Segment(swapped_node_id, location_id, mid_ts, None, True, mid_i, total_readings),
        ]
    if history_mode == "relocation":
        location_b = f"{location_id}-b"
        return [
            Segment(primary_node_id, location_id, start, mid_ts, True, 0, mid_i),
            Segment(primary_node_id, location_b, mid_ts, None, True, mid_i, total_readings),
        ]
    raise ValueError(f"unknown history mode {history_mode!r}")


def _segment_for(segments: list[Segment], i: int) -> Segment:
    for seg in segments:
        if seg.reading_start <= i < seg.reading_end:
            return seg
    return segments[-1]


async def _upsert_location(session, location_id, created_at) -> None:
    stmt = (
        pg_insert(models.Location)
        .values(location_id=location_id, label=location_id, created_at=created_at)
        .on_conflict_do_nothing(index_elements=[models.Location.location_id])
    )
    await session.execute(stmt)


async def _upsert_node(session, node_id, first_seen) -> None:
    stmt = (
        pg_insert(models.Node)
        .values(
            node_id=node_id,
            first_seen=first_seen,
            fw_version="0.1.0",
            report_interval_s=REPORT_INTERVAL_S,
            sample_interval_s=REPORT_INTERVAL_S,
        )
        .on_conflict_do_nothing(index_elements=[models.Node.node_id])
    )
    await session.execute(stmt)


async def _insert_assignment(session, node_id, location_id, valid_from, valid_to) -> None:
    await session.execute(
        pg_insert(models.NodeAssignment).values(
            node_id=node_id, location_id=location_id, valid_from=valid_from, valid_to=valid_to
        )
    )


async def _insert_calibration(session, node_id, location_id, valid_from, valid_to) -> None:
    await session.execute(
        pg_insert(models.Calibration).values(
            node_id=node_id,
            location_id=location_id,
            valid_from=valid_from,
            valid_to=valid_to,
            kv_per_mv=KV_PER_MV,
            kv_offset=0.0,
            method="mock-backfill-identity",
            notes="synthetic calibration seeded by mock publisher backfill",
        )
    )


async def _insert_reading(session, node_id, payload, ts) -> None:
    stmt = (
        pg_insert(models.Reading)
        .values(
            ts=ts,
            node_id=node_id,
            adc_mv=payload["adc_mv"],
            batt_v=payload.get("batt_v"),
            rssi=payload.get("rssi"),
            boot=payload.get("boot"),
            failed_pub=payload.get("failed_pub"),
            wifi_ms=payload.get("wifi_ms"),
            fw=payload.get("fw"),
            seq=payload.get("seq"),
            temp_c=payload.get("temp_c"),
            kv=payload.get("kv"),
        )
        .on_conflict_do_nothing(index_elements=[models.Reading.ts, models.Reading.node_id])
    )
    await session.execute(stmt)


async def _upsert_node_state(session, node_id, payload, payload_ts, received_at) -> None:
    stmt = pg_insert(models.NodeState).values(
        node_id=node_id,
        last_payload=payload,
        payload_ts=payload_ts,
        received_at=received_at,
        was_retained=False,
        ingest_seen_at=received_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[models.NodeState.node_id],
        set_={
            "last_payload": stmt.excluded.last_payload,
            "payload_ts": stmt.excluded.payload_ts,
            "received_at": stmt.excluded.received_at,
            "was_retained": stmt.excluded.was_retained,
            "ingest_seen_at": stmt.excluded.ingest_seen_at,
        },
    )
    await session.execute(stmt)


async def _seed_fence_event(session, history_mode, primary_node_id, location_id, segments, start) -> None:
    if history_mode == "board-swap":
        seg = segments[1]
        await _insert_fence_event(
            session,
            ts=seg.valid_from,
            location_id=location_id,
            node_id=seg.node_id,
            kind="board_swap",
            note=f"mock backfill: {segments[0].node_id} replaced by {seg.node_id}",
        )
    elif history_mode == "relocation":
        seg = segments[1]
        await _insert_fence_event(
            session,
            ts=seg.valid_from,
            location_id=seg.location_id,
            node_id=primary_node_id,
            kind="relocated",
            note=f"mock backfill: moved from {segments[0].location_id} to {seg.location_id}",
        )
    else:
        await _insert_fence_event(
            session,
            ts=start,
            location_id=location_id,
            node_id=primary_node_id,
            kind="backfill_seeded",
            note=f"mock backfill: history={history_mode} seeded",
        )


async def _insert_fence_event(session, ts, location_id, node_id, kind, note) -> None:
    await session.execute(
        pg_insert(models.FenceEvent).values(
            ts=ts, location_id=location_id, node_id=node_id, kind=kind, note=note
        )
    )
