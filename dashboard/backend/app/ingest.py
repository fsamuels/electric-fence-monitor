"""MQTT ingest subscriber entrypoint -- its own container, not a FastAPI
background task, so the API and ingest can be restarted independently.

D0 scope: connect to the broker and keep ingest_state's heartbeat fresh so
/healthz can report on it. Phase D1 adds the fence/+/state subscription,
contract validation, and the readings/node_state writes.
"""

import asyncio
import logging

import asyncpg
import paho.mqtt.client as mqtt

from app.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ingest")

_connected = asyncio.Event()


def _on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    if reason_code == 0:
        log.info("connected to broker %s:%s", settings.mqtt_host, settings.mqtt_port)
        _connected.set()
    else:
        log.warning("broker connect failed: %s", reason_code)
        _connected.clear()


def _on_disconnect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    log.warning("disconnected from broker: %s", reason_code)
    _connected.clear()


async def _heartbeat_loop(pool: asyncpg.Pool, interval_s: int = 5) -> None:
    while True:
        connected = _connected.is_set()
        await pool.execute(
            "UPDATE ingest_state SET connected = $1, last_heartbeat = now() WHERE id = 1",
            connected,
        )
        await asyncio.sleep(interval_s)


def _asyncpg_dsn() -> str:
    # asyncpg's own DSN form, distinct from the SQLAlchemy URL in config.py.
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.connect_async(settings.mqtt_host, settings.mqtt_port)
    client.loop_start()

    pool = await asyncpg.create_pool(_asyncpg_dsn())
    try:
        await _heartbeat_loop(pool)
    finally:
        await pool.close()
        client.loop_stop()


if __name__ == "__main__":
    asyncio.run(main())
