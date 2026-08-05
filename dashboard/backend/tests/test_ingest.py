"""Ingest edge-case tests -- see docs/dashboard-plan.md Phase D1 checklist
and Testing section. Calls app.ingest.handle_message directly rather than
going through a real broker: the MQTT wiring itself (retained replay on
subscribe, restart producing zero duplicate rows) is exercised manually via
docker compose -- see the D1 exit criteria -- and isn't worth a broker
dependency in the test suite.
"""

import copy
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app import ingest, models

BASE_PAYLOAD = {
    "node_id": "mock-0001",
    "fw": "0.1.0",
    "kv": 7.01,
    "adc_mv": 1895,
    "batt_v": 4.05,
    "rssi": -58,
    "boot": 42,
    "failed_pub": 0,
    "wifi_ms": 1200,
}


def payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode()


async def _reading_count(session) -> int:
    result = await session.execute(select(models.Reading))
    return len(result.all())


async def test_live_message_creates_node_and_reading(db_session) -> None:
    await ingest.handle_message("fence/mock-0001/state", payload_bytes(BASE_PAYLOAD), False)

    node = await db_session.get(models.Node, "mock-0001")
    assert node is not None

    assert await _reading_count(db_session) == 1


async def test_malformed_json_is_dropped(db_session) -> None:
    await ingest.handle_message("fence/mock-0001/state", b"{not json", False)

    assert await _reading_count(db_session) == 0
    assert await db_session.get(models.Node, "mock-0001") is None


async def test_missing_required_field_is_dropped(db_session) -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    del payload["adc_mv"]

    await ingest.handle_message("fence/mock-0001/state", payload_bytes(payload), False)

    assert await _reading_count(db_session) == 0
    assert await db_session.get(models.Node, "mock-0001") is None


async def test_extra_field_is_accepted_and_ignored(db_session) -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["some_future_field"] = "unrecognized"

    await ingest.handle_message("fence/mock-0001/state", payload_bytes(payload), False)

    assert await _reading_count(db_session) == 1


async def test_malformed_node_id_is_dropped(db_session) -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["node_id"] = "Not_Valid!"

    await ingest.handle_message("fence/Not_Valid!/state", payload_bytes(payload), False)

    assert await _reading_count(db_session) == 0
    assert await db_session.get(models.Node, "Not_Valid!") is None


async def test_topic_payload_node_id_mismatch_is_dropped(db_session) -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["node_id"] = "mock-0001"

    await ingest.handle_message("fence/mock-0002/state", payload_bytes(payload), False)

    assert await _reading_count(db_session) == 0
    assert await db_session.get(models.Node, "mock-0001") is None
    assert await db_session.get(models.Node, "mock-0002") is None


async def test_retained_message_updates_state_but_not_readings(db_session) -> None:
    await ingest.handle_message("fence/mock-0001/state", payload_bytes(BASE_PAYLOAD), True)

    assert await _reading_count(db_session) == 0
    state = await db_session.get(models.NodeState, "mock-0001")
    assert state is not None
    assert state.was_retained is True
    # Node itself is still created -- the "unassigned inbox" applies
    # regardless of retain, only the readings insert is skipped.
    assert await db_session.get(models.Node, "mock-0001") is not None


async def test_restart_replay_of_retained_message_creates_zero_new_readings(db_session) -> None:
    # Simulates the D1 exit criterion: ingest restarting repeatedly while a
    # retained message sits on the broker must not fabricate history.
    for _ in range(5):
        await ingest.handle_message("fence/mock-0001/state", payload_bytes(BASE_PAYLOAD), True)

    assert await _reading_count(db_session) == 0


async def test_reading_from_unassigned_node_is_accepted(db_session) -> None:
    # No node_assignments row is ever created by ingest -- a node with no
    # assignment is simply "unassigned", not an error.
    await ingest.handle_message("fence/mock-0001/state", payload_bytes(BASE_PAYLOAD), False)

    assert await _reading_count(db_session) == 1
    result = await db_session.execute(select(models.NodeAssignment))
    assert result.all() == []


async def test_post_reset_boot_reuse_is_not_treated_as_duplicate(db_session) -> None:
    first = copy.deepcopy(BASE_PAYLOAD)
    first["boot"] = 50

    reset = copy.deepcopy(BASE_PAYLOAD)
    reset["boot"] = 0  # power-loss reset, reused wake count

    await ingest.handle_message("fence/mock-0001/state", payload_bytes(first), False)
    await ingest.handle_message("fence/mock-0001/state", payload_bytes(reset), False)

    # Two distinct receipt timestamps -> two rows. Nothing keys dedup on
    # `boot`, so the reused count after a reset doesn't collide with the
    # earlier reading.
    assert await _reading_count(db_session) == 2


async def test_overlapping_assignment_windows_for_same_node_are_rejected(db_session) -> None:
    jan_1 = datetime(2026, 1, 1, tzinfo=UTC)
    jan_15 = datetime(2026, 1, 15, tzinfo=UTC)
    feb_1 = datetime(2026, 2, 1, tzinfo=UTC)

    await db_session.execute(pg_insert(models.Node).values(node_id="mock-0001", first_seen=jan_1))
    await db_session.execute(
        pg_insert(models.Location).values(
            location_id="north-gate", label="North Gate", created_at=jan_1
        )
    )
    await db_session.execute(
        pg_insert(models.NodeAssignment).values(
            node_id="mock-0001",
            location_id="north-gate",
            valid_from=jan_1,
            valid_to=feb_1,
        )
    )
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            pg_insert(models.NodeAssignment).values(
                node_id="mock-0001",
                location_id="north-gate",
                valid_from=jan_15,
                valid_to=None,
            )
        )
        await db_session.commit()
