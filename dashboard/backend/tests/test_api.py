"""Integration tests for the Phase D3 API -- see docs/dashboard-plan.md's
Phase D3 exit criteria: correct derived status per scenario, retroactive
recalibration corrects history without touching `readings`, and relocating
a node leaves its prior readings attributed to the prior location.
"""

import json
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import app

NOW = datetime.now(UTC).replace(microsecond=0)


async def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _insert_node(session, node_id: str, report_interval_s: int = 600) -> None:
    await session.execute(
        text(
            "INSERT INTO nodes (node_id, first_seen, fw_version, report_interval_s, "
            "sample_interval_s) VALUES (:node_id, :now, '0.1.0', :interval, :interval)"
        ),
        {"node_id": node_id, "now": NOW, "interval": report_interval_s},
    )
    await session.commit()


async def _insert_reading(session, node_id: str, ts: datetime, adc_mv: int) -> None:
    await session.execute(
        text(
            "INSERT INTO readings (ts, node_id, adc_mv, batt_v) "
            "VALUES (:ts, :node_id, :adc_mv, 4.0)"
        ),
        {"ts": ts, "node_id": node_id, "adc_mv": adc_mv},
    )
    await session.commit()


async def _insert_calibration(
    session,
    node_id: str,
    location_id: str,
    valid_from: datetime,
    valid_to: datetime | None,
    kv_per_mv: float,
    kv_offset: float,
) -> None:
    await session.execute(
        text(
            "INSERT INTO calibrations (node_id, location_id, valid_from, valid_to, "
            "kv_per_mv, kv_offset) VALUES (:node_id, :location_id, :valid_from, :valid_to, "
            ":kv_per_mv, :kv_offset)"
        ),
        {
            "node_id": node_id,
            "location_id": location_id,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "kv_per_mv": kv_per_mv,
            "kv_offset": kv_offset,
        },
    )
    await session.commit()


async def test_create_and_get_location_unmonitored(db_session) -> None:
    async with await _client() as client:
        resp = await client.post(
            "/locations", json={"location_id": "north-gate", "label": "North Gate"}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "unmonitored"
        assert body["node_id"] is None

        resp = await client.get("/locations/north-gate")
        assert resp.status_code == 200
        assert resp.json()["status"] == "unmonitored"

        resp = await client.get("/locations")
        assert any(loc["location_id"] == "north-gate" for loc in resp.json())


async def test_create_location_conflict(db_session) -> None:
    async with await _client() as client:
        await client.post("/locations", json={"location_id": "south-gate", "label": "South"})
        resp = await client.post(
            "/locations", json={"location_id": "south-gate", "label": "South Again"}
        )
        assert resp.status_code == 409


async def test_get_missing_location_404(db_session) -> None:
    async with await _client() as client:
        resp = await client.get("/locations/does-not-exist")
        assert resp.status_code == 404


async def test_assign_node_makes_location_monitored(db_session) -> None:
    await _insert_node(db_session, "node-a")

    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-a", "label": "Loc A"})
        resp = await client.post("/nodes/node-a/assignment", json={"location_id": "loc-a"})
        assert resp.status_code == 201

        resp = await client.get("/locations/loc-a")
        body = resp.json()
        assert body["node_id"] == "node-a"
        # No readings yet -> silent, per derive_status(readings=[]) -> SILENT.
        assert body["status"] == "silent"


async def test_status_ok_with_calibrated_reading(db_session) -> None:
    await _insert_node(db_session, "node-b")
    await _insert_reading(db_session, "node-b", NOW - timedelta(seconds=10), adc_mv=2000)

    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-b", "label": "Loc B"})
        await client.post("/nodes/node-b/assignment", json={"location_id": "loc-b"})

    await _insert_calibration(
        db_session,
        "node-b",
        "loc-b",
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        kv_per_mv=0.0035,
        kv_offset=0.0,
    )

    async with await _client() as client:
        resp = await client.get("/locations/loc-b")
        body = resp.json()
        assert body["status"] == "ok"
        assert body["provisional"] is False
        assert body["current_kv"] == 7.0


async def test_reading_without_calibration_is_provisional(db_session) -> None:
    await _insert_node(db_session, "node-c")
    await _insert_reading(db_session, "node-c", NOW - timedelta(seconds=5), adc_mv=1900)

    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-c", "label": "Loc C"})
        await client.post(
            "/nodes/node-c/assignment",
            json={"location_id": "loc-c", "valid_from": (NOW - timedelta(hours=1)).isoformat()},
        )

        resp = await client.get(
            "/locations/loc-c/readings",
            params={"since": (NOW - timedelta(hours=1)).isoformat()},
        )
        points = resp.json()
        assert len(points) == 1
        assert points[0]["provisional"] is True
        assert points[0]["kv_avg"] is None
        assert points[0]["adc_mv_avg"] == 1900


async def test_retroactive_recalibration_corrects_history_without_touching_readings(
    db_session,
) -> None:
    await _insert_node(db_session, "node-d")
    reading_ts = NOW - timedelta(days=2)
    await _insert_reading(db_session, "node-d", reading_ts, adc_mv=2000)

    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-d", "label": "Loc D"})
        await client.post(
            "/nodes/node-d/assignment",
            json={"location_id": "loc-d", "valid_from": (NOW - timedelta(days=30)).isoformat()},
        )

        resp = await client.get(
            "/locations/loc-d/readings",
            params={"since": (NOW - timedelta(days=3)).isoformat()},
        )
        before = resp.json()
        assert len(before) == 1
        assert before[0]["provisional"] is True
        assert before[0]["kv_avg"] is None

    reading_count_before = (
        await db_session.execute(text("SELECT count(*) FROM readings"))
    ).scalar_one()

    # Backdated calibration, inserted after the fact.
    await _insert_calibration(
        db_session,
        "node-d",
        "loc-d",
        valid_from=NOW - timedelta(days=30),
        valid_to=None,
        kv_per_mv=0.0035,
        kv_offset=0.0,
    )

    reading_count_after = (
        await db_session.execute(text("SELECT count(*) FROM readings"))
    ).scalar_one()
    assert reading_count_after == reading_count_before

    async with await _client() as client:
        resp = await client.get(
            "/locations/loc-d/readings",
            params={"since": (NOW - timedelta(days=3)).isoformat()},
        )
        after = resp.json()
        assert len(after) == 1
        assert after[0]["provisional"] is False
        assert after[0]["kv_avg"] == 7.0


async def test_relocation_leaves_prior_readings_attributed_to_prior_location(
    db_session,
) -> None:
    await _insert_node(db_session, "node-e")

    t0 = NOW - timedelta(hours=2)
    t1 = NOW - timedelta(hours=1)
    await _insert_reading(db_session, "node-e", t0 + timedelta(minutes=1), adc_mv=1900)

    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-e1", "label": "Loc E1"})
        await client.post("/locations", json={"location_id": "loc-e2", "label": "Loc E2"})
        resp = await client.post(
            "/nodes/node-e/assignment",
            json={"location_id": "loc-e1", "valid_from": t0.isoformat()},
        )
        assert resp.status_code == 201

        # Relocate at t1 -- closes the loc-e1 assignment, opens loc-e2.
        resp = await client.post(
            "/nodes/node-e/assignment",
            json={"location_id": "loc-e2", "valid_from": t1.isoformat()},
        )
        assert resp.status_code == 201

    await _insert_reading(db_session, "node-e", t1 + timedelta(minutes=1), adc_mv=1950)

    async with await _client() as client:
        resp = await client.get(
            "/locations/loc-e1/readings", params={"since": t0.isoformat()}
        )
        e1_points = resp.json()
        assert len(e1_points) == 1
        assert e1_points[0]["adc_mv_avg"] == 1900

        resp = await client.get(
            "/locations/loc-e2/readings", params={"since": t0.isoformat()}
        )
        e2_points = resp.json()
        assert len(e2_points) == 1
        assert e2_points[0]["adc_mv_avg"] == 1950

        resp = await client.get("/locations/loc-e2")
        assert resp.json()["node_id"] == "node-e"
        resp = await client.get("/locations/loc-e1")
        assert resp.json()["node_id"] is None
        assert resp.json()["status"] == "unmonitored"


async def test_board_swap_closes_target_locations_prior_assignment(db_session) -> None:
    await _insert_node(db_session, "node-f")
    await _insert_node(db_session, "node-g")

    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-f", "label": "Loc F"})
        await client.post("/nodes/node-f/assignment", json={"location_id": "loc-f"})

        # Board swap: node-g takes over loc-f.
        resp = await client.post("/nodes/node-g/assignment", json={"location_id": "loc-f"})
        assert resp.status_code == 201

        resp = await client.get("/locations/loc-f")
        assert resp.json()["node_id"] == "node-g"

    # The swap must be visible on the fence_events timeline (D1 trigger).
    events = (
        await db_session.execute(
            text("SELECT kind, node_id FROM fence_events WHERE location_id = 'loc-f' ORDER BY ts")
        )
    ).all()
    kinds = [(e.kind, e.node_id) for e in events]
    assert ("assigned", "node-f") in kinds
    assert ("unassigned", "node-f") in kinds
    assert ("assigned", "node-g") in kinds


async def test_assign_unknown_node_404(db_session) -> None:
    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-h", "label": "Loc H"})
        resp = await client.post("/nodes/no-such-node/assignment", json={"location_id": "loc-h"})
        assert resp.status_code == 404


async def test_assign_unknown_location_404(db_session) -> None:
    await _insert_node(db_session, "node-i")
    async with await _client() as client:
        resp = await client.post(
            "/nodes/node-i/assignment", json={"location_id": "no-such-location"}
        )
        assert resp.status_code == 404


async def test_unassign_node(db_session) -> None:
    await _insert_node(db_session, "node-j")
    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-j", "label": "Loc J"})
        await client.post("/nodes/node-j/assignment", json={"location_id": "loc-j"})

        resp = await client.delete("/nodes/node-j/assignment")
        assert resp.status_code == 204

        resp = await client.get("/locations/loc-j")
        assert resp.json()["status"] == "unmonitored"

        resp = await client.delete("/nodes/node-j/assignment")
        assert resp.status_code == 404


async def test_fence_events_create_and_list(db_session) -> None:
    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-k", "label": "Loc K"})
        resp = await client.post(
            "/fence-events",
            json={"location_id": "loc-k", "kind": "vegetation_cleared", "note": "trimmed"},
        )
        assert resp.status_code == 201
        assert resp.json()["kind"] == "vegetation_cleared"

        resp = await client.get("/fence-events", params={"location_id": "loc-k"})
        events = resp.json()
        assert len(events) == 1
        assert events[0]["note"] == "trimmed"


async def test_readings_hourly_bucketing(db_session) -> None:
    await _insert_node(db_session, "node-l")
    base = (NOW - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    for minutes in (5, 20, 65, 80):
        await _insert_reading(db_session, "node-l", base + timedelta(minutes=minutes), adc_mv=1900)

    async with await _client() as client:
        await client.post("/locations", json={"location_id": "loc-l", "label": "Loc L"})
        await client.post(
            "/nodes/node-l/assignment",
            json={"location_id": "loc-l", "valid_from": (base - timedelta(days=1)).isoformat()},
        )
        resp = await client.get(
            "/locations/loc-l/readings",
            params={"since": (base - timedelta(minutes=1)).isoformat(), "bucket": "hour"},
        )
        points = resp.json()
        assert len(points) == 2
        assert points[0]["reading_count"] == 2
        assert points[1]["reading_count"] == 2


async def test_list_nodes_reports_link_quality(db_session) -> None:
    await _insert_node(db_session, "node-m")
    await db_session.execute(
        text(
            "INSERT INTO node_state (node_id, last_payload, payload_ts, received_at, "
            "was_retained, ingest_seen_at) "
            "VALUES (:node_id, CAST(:payload AS JSONB), :now, :now, false, :now)"
        ),
        {
            "node_id": "node-m",
            "payload": json.dumps({"rssi": -60, "wifi_ms": 900, "failed_pub": 1}),
            "now": NOW,
        },
    )
    await db_session.commit()

    async with await _client() as client:
        resp = await client.get("/nodes")
        node = next(n for n in resp.json() if n["node_id"] == "node-m")
        assert node["rssi"] == -60
        assert node["wifi_ms"] == 900
        assert node["failed_pub"] == 1
        assert node["location_id"] is None
