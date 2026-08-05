"""Fence-condition scenario generators and the fence-state payload builder --
D2. See docs/dashboard-plan.md's "Mock publisher" section for the six
scenarios and the payload-shape rationale below.

Each Scenario is a pure generator keyed on a reading index `i` (not wall
time), so the same curve can be driven by live mode's real-time ticks or
backfill mode's synthetic historical timestamps identically.
"""

import math
import random

KV_PER_MV = 0.003704  # theoretical divider ratio -- matches firmware's CAL_KV_PER_MV default
NORMAL_KV = 7.0
LOW_THRESHOLD_KV = 5.0
DOWN_FLOOR_KV = 1.0


def kv_to_adc_mv(kv: float) -> int:
    return max(0, min(3300, round(kv / KV_PER_MV)))


def _temp_c(i: int, interval_s: int) -> float:
    elapsed_hours = i * interval_s / 3600
    return round(15 + 8 * math.sin(2 * math.pi * elapsed_hours / 24) + random.uniform(-0.5, 0.5), 2)


class Scenario:
    name = "scenario"

    def kv(self, i: int) -> float:
        raise NotImplementedError

    def batt_v(self, i: int) -> float:
        return 3.98

    def is_silent(self, i: int) -> bool:
        return False


class Normal(Scenario):
    name = "normal"

    def kv(self, i: int) -> float:
        return NORMAL_KV + random.uniform(-0.15, 0.15)


class SlowDecline(Scenario):
    """Gradual drift toward the low threshold -- stands in for a
    vegetation-load trend that plays out over days in the real system.
    """

    name = "slow-decline"

    def __init__(self, span: int = 200):
        self.span = max(span, 1)

    def kv(self, i: int) -> float:
        frac = min(1.0, i / self.span)
        base = NORMAL_KV - frac * (NORMAL_KV - (LOW_THRESHOLD_KV - 0.5))
        return base + random.uniform(-0.1, 0.1)


class LowVoltage(Scenario):
    name = "low-voltage"

    def kv(self, i: int) -> float:
        return random.uniform(3.5, 4.5)


class FenceDown(Scenario):
    name = "fence-down"

    def kv(self, i: int) -> float:
        return random.uniform(0.0, 0.3)


class NodeSilent(Scenario):
    name = "node-silent"

    def __init__(self, silent_after: int = 3):
        self.silent_after = silent_after

    def kv(self, i: int) -> float:
        return NORMAL_KV + random.uniform(-0.15, 0.15)

    def is_silent(self, i: int) -> bool:
        return i >= self.silent_after


class BatteryDrain(Scenario):
    name = "battery-drain"

    def __init__(self, span: int = 200):
        self.span = max(span, 1)

    def kv(self, i: int) -> float:
        return NORMAL_KV + random.uniform(-0.15, 0.15)

    def batt_v(self, i: int) -> float:
        frac = min(1.0, i / self.span)
        return round(4.2 - frac * (4.2 - 3.0), 3)


SCENARIOS = {
    cls.name: cls
    for cls in (Normal, SlowDecline, LowVoltage, FenceDown, NodeSilent, BatteryDrain)
}


def make_scenario(name: str, **kwargs) -> Scenario:
    try:
        cls = SCENARIOS[name]
    except KeyError:
        raise ValueError(f"unknown scenario {name!r}; choices: {', '.join(SCENARIOS)}") from None
    return cls(**kwargs)


def build_payload(
    node_id: str,
    scenario: Scenario,
    i: int,
    kv: float | None = None,
    boot: int | None = None,
    fw: str = "0.1.0",
    report_interval_s: int | None = None,
    sample_interval_s: int | None = None,
) -> dict:
    """Builds one fence/<node_id>/state payload, contract-shaped (see
    contract/fence-state.schema.json).

    **Latest-only shape** -- this is the mock's explicit answer to the
    split-cadence payload question docs/dashboard-plan.md's Reserved
    optional fields section leaves open (latest-only vs. summary vs.
    timestamped batch): one reading per message, matching what current
    firmware sends and what the contract already defines. Revisit this
    function the day firmware actually buffers multiple samples per report.
    """
    if kv is None:
        kv = scenario.kv(i)
    adc_mv = kv_to_adc_mv(kv)
    interval_for_temp = sample_interval_s or report_interval_s or 600
    payload = {
        "node_id": node_id,
        "fw": fw,
        "kv": round(kv, 3),
        "adc_mv": adc_mv,
        "batt_v": round(scenario.batt_v(i), 3),
        "rssi": random.randint(-80, -40),
        "boot": boot if boot is not None else i + 1,
        "failed_pub": 0,
        "wifi_ms": random.randint(800, 4000),
        "temp_c": _temp_c(i, interval_for_temp),
    }
    if report_interval_s is not None:
        payload["report_interval_s"] = report_interval_s
    if sample_interval_s is not None:
        payload["sample_interval_s"] = sample_interval_s
    return payload
