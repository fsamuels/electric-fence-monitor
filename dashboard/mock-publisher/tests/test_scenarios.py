import json
from pathlib import Path

import jsonschema
import pytest

import scenarios

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contract" / "fence-state.schema.json"


@pytest.fixture(scope="module")
def contract_validator():
    schema = json.loads(SCHEMA_PATH.read_text())
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize("name", list(scenarios.SCENARIOS))
def test_scenario_payload_matches_contract(name, contract_validator):
    scenario = scenarios.make_scenario(name)
    for i in (0, 1, 50):
        payload = scenarios.build_payload(
            "mock-0001", scenario, i, report_interval_s=600, sample_interval_s=600
        )
        contract_validator.validate(payload)


def test_node_silent_stops_after_configured_reading():
    scenario = scenarios.make_scenario("node-silent", silent_after=3)
    assert not scenario.is_silent(0)
    assert not scenario.is_silent(2)
    assert scenario.is_silent(3)
    assert scenario.is_silent(10)


def test_slow_decline_trends_toward_low_threshold():
    scenario = scenarios.make_scenario("slow-decline", span=100)
    assert scenario.kv(99) < scenario.kv(0)


def test_fence_down_stays_near_zero():
    scenario = scenarios.make_scenario("fence-down")
    for i in range(20):
        assert 0.0 <= scenario.kv(i) < 1.0


def test_battery_drain_trends_down():
    scenario = scenarios.make_scenario("battery-drain", span=100)
    assert scenario.batt_v(99) < scenario.batt_v(0)


def test_unknown_scenario_raises():
    with pytest.raises(ValueError, match="unknown scenario"):
        scenarios.make_scenario("not-a-real-scenario")


def test_boot_override_is_used_verbatim():
    scenario = scenarios.make_scenario("normal")
    payload = scenarios.build_payload("mock-0001", scenario, i=99, boot=1)
    assert payload["boot"] == 1
