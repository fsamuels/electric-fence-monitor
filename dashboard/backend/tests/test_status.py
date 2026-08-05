"""Table-driven tests for app.status.derive_status -- see
docs/dashboard-plan.md Phase D3 checklist: every mock scenario and boundary
condition, including irregular arrival from off-cadence fault transmits.
derive_status is pure, so these need no DB.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.status import ReadingForStatus, Status, StatusThresholds, derive_status

NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)
THRESHOLDS = StatusThresholds(low_kv=5.0, down_kv=1.0, low_consecutive=3, silent_multiplier=2.5)


def reading(seconds_ago: float, kv: float | None) -> ReadingForStatus:
    return ReadingForStatus(ts=NOW - timedelta(seconds=seconds_ago), kv=kv)


def test_unmonitored_when_no_assignment() -> None:
    status = derive_status(
        [reading(10, 7.0)],
        has_assignment=False,
        report_interval_s=600,
        thresholds=THRESHOLDS,
        now=NOW,
    )
    assert status == Status.UNMONITORED


def test_silent_when_no_readings_ever() -> None:
    status = derive_status(
        [], has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.SILENT


def test_ok_normal_operation() -> None:
    # normal-operation scenario: healthy kV every heartbeat.
    readings = [reading(s, 7.0) for s in (0, 600, 1200)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.OK


def test_low_after_n_consecutive_low_readings() -> None:
    # slow-decline / low-voltage scenario.
    readings = [reading(s, 4.5) for s in (0, 600, 1200)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.LOW


def test_ok_when_only_two_of_three_are_low() -> None:
    # One-off dip shouldn't page anyone -- needs N consecutive.
    readings = [reading(0, 7.0), reading(600, 4.5), reading(1200, 4.5)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.OK


def test_down_below_floor() -> None:
    # fence-down scenario.
    readings = [reading(0, 0.5), reading(600, 7.0)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.DOWN


def test_down_pinned_at_zero() -> None:
    readings = [reading(0, 0.0)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.DOWN


def test_down_takes_precedence_over_low_history() -> None:
    readings = [reading(0, 0.5), reading(600, 4.5), reading(1200, 4.5)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.DOWN


def test_silent_after_multiple_missed_heartbeats() -> None:
    # node-silent scenario: last reading well past 2.5x report_interval_s.
    readings = [reading(2000, 7.0)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.SILENT


def test_not_silent_within_threshold_multiple() -> None:
    readings = [reading(1400, 7.0)]  # < 2.5 * 600 = 1500
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.OK


def test_silent_boundary_is_exclusive() -> None:
    readings = [reading(1500, 7.0)]  # exactly 2.5x -- not yet over
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.OK

    readings = [reading(1500.001, 7.0)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.SILENT


def test_silent_measured_against_own_report_interval_not_global() -> None:
    # A mock node reporting every 10s and a real node every 900s must not
    # share one global silent threshold -- see the data contract.
    mock_readings = [reading(30, 7.0)]  # 3x a 10s cadence
    assert (
        derive_status(
            mock_readings,
            has_assignment=True,
            report_interval_s=10,
            thresholds=THRESHOLDS,
            now=NOW,
        )
        == Status.SILENT
    )

    real_readings = [reading(30, 7.0)]  # trivially fine for a 900s cadence
    assert (
        derive_status(
            real_readings,
            has_assignment=True,
            report_interval_s=900,
            thresholds=THRESHOLDS,
            now=NOW,
        )
        == Status.OK
    )


def test_irregular_off_cadence_fault_transmit_does_not_confuse_status() -> None:
    # report-by-exception: a fault reading arrives between heartbeats.
    # Irregular spacing must be read as normal arrival, not as noise/gap.
    readings = [
        reading(5, 4.0),  # off-cadence fault transmit
        reading(300, 7.0),  # mid-cycle heartbeat
        reading(600, 7.0),  # routine heartbeat
    ]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    # Only the most recent reading is low -- one off-cadence transmit alone
    # should not trip the N-consecutive low-voltage alert.
    assert status == Status.OK


def test_provisional_readings_excluded_from_thresholds_but_count_for_recency() -> None:
    readings = [reading(0, None), reading(600, None)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    # No calibrated readings to judge ok/low/down -- but recent enough not
    # to be silent, so it falls back to ok rather than a false alert.
    assert status == Status.OK


def test_provisional_most_recent_falls_back_to_last_calibrated() -> None:
    readings = [reading(0, None), reading(600, 4.5), reading(1200, 4.5), reading(1800, 4.5)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.LOW


def test_missing_report_interval_never_marks_silent() -> None:
    # Fallback per the data contract: no report_interval_s known yet.
    readings = [reading(999999, 7.0)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=None, thresholds=THRESHOLDS, now=NOW
    )
    assert status == Status.OK


@pytest.mark.parametrize(
    "kv_sequence,expected",
    [
        ([7.0, 7.0, 7.0], Status.OK),
        ([4.9, 4.9, 4.9], Status.LOW),
        ([0.9], Status.DOWN),
        ([5.0, 5.0, 5.0], Status.OK),  # boundary: low_kv is exclusive (< not <=)
        ([1.0], Status.OK),  # boundary: down_kv is exclusive (< not <=)
    ],
)
def test_threshold_boundaries(kv_sequence: list[float], expected: Status) -> None:
    readings = [reading(i * 600, kv) for i, kv in enumerate(kv_sequence)]
    status = derive_status(
        readings, has_assignment=True, report_interval_s=600, thresholds=THRESHOLDS, now=NOW
    )
    assert status == expected
