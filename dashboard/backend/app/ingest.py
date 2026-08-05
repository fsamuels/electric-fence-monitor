"""MQTT ingest subscriber entrypoint -- its own container, not a FastAPI
background task, so the API and ingest can be restarted independently.

Subscribes to `fence/+/state`, validates against the contract schema, and
writes `nodes` / `node_state` / `readings`. See docs/dashboard-plan.md's data
contract section for the retained-message and identity rules this enforces.
"""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime

import jsonschema
import paho.mqtt.client as mqtt
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import models
from app.config import settings
from app.db import SessionLocal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ingest")

_connected = asyncio.Event()

TOPIC_RE = re.compile(r"^fence/([^/]+)/state$")

with open(settings.contract_schema_path) as f:
    CONTRACT_SCHEMA = json.load(f)


def parse_topic(topic: str) -> str | None:
    match = TOPIC_RE.match(topic)
    return match.group(1) if match else None


async def handle_message(topic: str, payload_bytes: bytes, retained: bool) -> None:
    topic_node_id = parse_topic(topic)
    if topic_node_id is None:
        log.warning("ignoring message on unexpected topic %r", topic)
        return

    try:
        payload = json.loads(payload_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("ignoring malformed JSON on topic %r", topic)
        return

    try:
        jsonschema.validate(payload, CONTRACT_SCHEMA)
    except jsonschema.ValidationError as exc:
        log.warning("payload on %r failed contract validation: %s", topic, exc.message)
        return

    # They come from the same source on a well-behaved node, so this can
    # only disagree via a broken bridge or misrouting gateway -- worth
    # catching loudly rather than storing (see the data contract).
    if payload["node_id"] != topic_node_id:
        log.warning(
            "payload node_id %r disagrees with topic segment %r on %r",
            payload["node_id"],
            topic_node_id,
            topic,
        )
        return

    node_id = payload["node_id"]
    now = datetime.now(UTC)
    has_ts = "ts" in payload
    reading_ts = datetime.fromtimestamp(payload["ts"], UTC) if has_ts else now

    async with SessionLocal() as session:
        await _upsert_node(session, node_id, payload, first_seen=now)
        await _upsert_node_state(
            session,
            node_id,
            payload,
            payload_ts=reading_ts if has_ts else None,
            received_at=now,
            was_retained=retained,
            ingest_seen_at=now,
        )
        # Retained messages refresh node_state only -- inserting a reading
        # here would fabricate history on every ingest restart and could
        # resurrect a node that has actually gone silent. See the data
        # contract's "Retained messages must not create readings" rule.
        if not retained:
            await _insert_reading(session, node_id, payload, reading_ts)
        await session.commit()


async def _upsert_node(session, node_id: str, payload: dict, first_seen: datetime) -> None:
    stmt = pg_insert(models.Node).values(
        node_id=node_id,
        first_seen=first_seen,
        fw_version=payload.get("fw"),
        report_interval_s=payload.get("report_interval_s"),
        sample_interval_s=payload.get("sample_interval_s"),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[models.Node.node_id],
        set_={
            # fw arrives on every message -- always refresh (see Storage:
            # it's an upsert-on-change convenience field, not authoritative
            # history; the reading's own `fw` is authoritative).
            "fw_version": stmt.excluded.fw_version,
            # Reserved fields: only overwrite when the payload actually
            # sent them, otherwise keep whatever was configured before.
            "report_interval_s": func.coalesce(
                stmt.excluded.report_interval_s, models.Node.report_interval_s
            ),
            "sample_interval_s": func.coalesce(
                stmt.excluded.sample_interval_s, models.Node.sample_interval_s
            ),
        },
    )
    await session.execute(stmt)


async def _upsert_node_state(
    session,
    node_id: str,
    payload: dict,
    payload_ts: datetime | None,
    received_at: datetime,
    was_retained: bool,
    ingest_seen_at: datetime,
) -> None:
    stmt = pg_insert(models.NodeState).values(
        node_id=node_id,
        last_payload=payload,
        payload_ts=payload_ts,
        received_at=received_at,
        was_retained=was_retained,
        ingest_seen_at=ingest_seen_at,
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


async def _insert_reading(session, node_id: str, payload: dict, ts: datetime) -> None:
    stmt = pg_insert(models.Reading).values(
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
    # Best-effort dedup on (node_id, ts) -- not a hard guarantee until `seq`
    # is sent. A reused `boot` after a power reset is NOT treated as a
    # duplicate here: nothing keys on `boot`, only on `ts`, so a legitimate
    # post-reset reading is never dropped for reusing an old wake count.
    stmt = stmt.on_conflict_do_nothing(index_elements=[models.Reading.ts, models.Reading.node_id])
    await session.execute(stmt)


def _on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    if reason_code == 0:
        log.info("connected to broker %s:%s", settings.mqtt_host, settings.mqtt_port)
        client.subscribe("fence/+/state", qos=0)
        _connected.set()
    else:
        log.warning("broker connect failed: %s", reason_code)
        _connected.clear()


def _on_disconnect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    log.warning("disconnected from broker: %s", reason_code)
    _connected.clear()


def _make_on_message(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
    def _on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        # Runs on paho's network thread -- hand off to the asyncio loop
        # rather than doing async DB work here.
        loop.call_soon_threadsafe(
            queue.put_nowait, (msg.topic, msg.payload, bool(msg.retain))
        )

    return _on_message


async def _message_loop(queue: asyncio.Queue) -> None:
    while True:
        topic, payload_bytes, retained = await queue.get()
        try:
            await handle_message(topic, payload_bytes, retained)
        except Exception:
            log.exception("failed to process message on topic %r", topic)


async def _heartbeat_loop(interval_s: int = 5) -> None:
    while True:
        connected = _connected.is_set()
        async with SessionLocal() as session:
            stmt = (
                pg_insert(models.IngestState)
                .values(id=1, connected=connected, last_heartbeat=datetime.now(UTC))
                .on_conflict_do_update(
                    index_elements=[models.IngestState.id],
                    set_={"connected": connected, "last_heartbeat": datetime.now(UTC)},
                )
            )
            await session.execute(stmt)
            await session.commit()
        await asyncio.sleep(interval_s)


async def main() -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _make_on_message(loop, queue)
    client.connect_async(settings.mqtt_host, settings.mqtt_port)
    client.loop_start()

    try:
        await asyncio.gather(_heartbeat_loop(), _message_loop(queue))
    finally:
        client.loop_stop()


if __name__ == "__main__":
    asyncio.run(main())
