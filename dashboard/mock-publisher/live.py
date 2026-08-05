"""Live-mode MQTT publishing -- D2. One thread per simulated node, each
connecting/publishing/disconnecting on its own cadence like real firmware's
wake-publish-sleep cycle, so client-id-derived-from-node_id collision
behavior (docs/dashboard-plan.md's Mock publisher / Coexistence section) is
exercised the same way it will be against real hardware.

Two cadence models, both selectable via --cadence (see resolve_cadence):
today's single-interval firmware (what D6's pre-cutover rehearsal runs
against) and the recommended 60s-sample/900s-report split from the
Reporting cadence section. Under the split model, every sample tick is
checked for a threshold crossing and published immediately off-cycle if one
occurs -- "report-by-exception" -- in addition to the routine report tick.
"""

import json
import logging
import random
import threading
import time

import paho.mqtt.client as mqtt

from scenarios import DOWN_FLOOR_KV, LOW_THRESHOLD_KV, build_payload, make_scenario

log = logging.getLogger("mock-publisher")

CADENCE_PRESETS = {
    "realtime": (600, 600),
    "realtime-split": (60, 900),
}


def resolve_cadence(cadence_arg) -> tuple[int, int]:
    """Returns (sample_interval_s, report_interval_s)."""
    if cadence_arg in CADENCE_PRESETS:
        return CADENCE_PRESETS[cadence_arg]
    return cadence_arg, cadence_arg


def _publish(node_id: str, payload: dict, mqtt_host: str, mqtt_port: int) -> None:
    topic = f"fence/{node_id}/state"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=node_id)
    try:
        client.connect(mqtt_host, mqtt_port)
        client.loop_start()
        client.publish(topic, json.dumps(payload), qos=0, retain=True)
        time.sleep(0.2)  # let paho flush the publish before disconnecting
    except OSError as exc:
        log.warning("%s: publish failed: %s", node_id, exc)
    finally:
        client.loop_stop()
        client.disconnect()


def _node_loop(
    node_id: str,
    scenario_name: str,
    sample_interval_s: int,
    report_interval_s: int,
    mqtt_host: str,
    mqtt_port: int,
    stop_event: threading.Event,
) -> None:
    scenario = make_scenario(scenario_name)
    ticks_per_report = max(1, report_interval_s // sample_interval_s)
    prev_kv = None
    i = 0

    while not stop_event.is_set():
        if scenario.is_silent(i):
            log.info("%s (%s): going silent", node_id, scenario_name)
            stop_event.wait()
            break

        kv = scenario.kv(i)
        is_routine = i % ticks_per_report == 0
        crossed_low = prev_kv is not None and prev_kv >= LOW_THRESHOLD_KV > kv
        crossed_down = prev_kv is not None and prev_kv >= DOWN_FLOOR_KV > kv

        if is_routine or crossed_low or crossed_down:
            payload = build_payload(
                node_id,
                scenario,
                i,
                kv=kv,
                report_interval_s=report_interval_s,
                sample_interval_s=sample_interval_s,
            )
            _publish(node_id, payload, mqtt_host, mqtt_port)
            reason = "routine" if is_routine and not (crossed_low or crossed_down) else "fault-exception"
            log.info(
                "%s (%s): %s kv=%.2f adc_mv=%d batt_v=%.2f",
                node_id,
                scenario_name,
                reason,
                payload["kv"],
                payload["adc_mv"],
                payload["batt_v"],
            )

        prev_kv = kv
        i += 1
        jitter = sample_interval_s * random.uniform(-0.05, 0.05)
        stop_event.wait(max(0.5, sample_interval_s + jitter))


def run_live(node_scenarios: list[str], cadence_arg, mqtt_host: str, mqtt_port: int) -> None:
    sample_interval_s, report_interval_s = resolve_cadence(cadence_arg)
    stop_event = threading.Event()
    threads = []

    for idx, scenario_name in enumerate(node_scenarios, start=1):
        node_id = f"mock-{idx:04d}"
        t = threading.Thread(
            target=_node_loop,
            args=(node_id, scenario_name, sample_interval_s, report_interval_s, mqtt_host, mqtt_port, stop_event),
            daemon=True,
        )
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=2)
