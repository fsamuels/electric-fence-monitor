"""D0 stub: connects and idles so `docker compose up` brings up all six
services end to end. Phase D2 adds scenarios, --cadence, and --backfill.
"""

import logging
import os
import time

import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mock-publisher")

MQTT_HOST = os.environ.get("FENCE_MQTT_HOST", "broker")
MQTT_PORT = int(os.environ.get("FENCE_MQTT_PORT", "1883"))


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect_async(MQTT_HOST, MQTT_PORT)
    client.loop_start()
    log.info("mock-publisher idling (no scenarios wired yet -- see Phase D2)")
    try:
        while True:
            time.sleep(60)
    finally:
        client.loop_stop()


if __name__ == "__main__":
    main()
