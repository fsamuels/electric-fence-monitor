"""Status derivation -- see docs/dashboard-plan.md's "Status derivation"
section. Pure, I/O-free by design: it has to be callable from the API
request path (D3), the D5.5 alerting scheduler, and unit tests alike.
Thresholds are config (app.config.settings), never hardcoded here, since
the plan calls out they may differ per node/season.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    OK = "ok"
    LOW = "low"
    DOWN = "down"
    SILENT = "silent"
    UNMONITORED = "unmonitored"


@dataclass(frozen=True)
class StatusThresholds:
    low_kv: float = 5.0
    down_kv: float = 1.0
    low_consecutive: int = 3
    silent_multiplier: float = 2.5


@dataclass(frozen=True)
class ReadingForStatus:
    ts: datetime
    kv: float | None


def derive_status(
    readings: list[ReadingForStatus],
    *,
    has_assignment: bool,
    report_interval_s: int | None,
    thresholds: StatusThresholds,
    now: datetime,
) -> Status:
    """Pure function -- no DB session, no clock reads. Callers resolve
    `readings` (most-recent-first order not required, sorted here),
    `has_assignment`, and `report_interval_s` themselves; `now` is passed
    in explicitly rather than read internally so this stays testable and
    reusable from a scheduler that evaluates many locations against one
    consistent instant.
    """
    if not has_assignment:
        return Status.UNMONITORED

    if not readings:
        # An assigned node that has never sent a reading is indistinguishable
        # from one that has gone silent -- the fence state is unknown either way.
        return Status.SILENT

    ordered = sorted(readings, key=lambda r: r.ts, reverse=True)
    latest = ordered[0]

    if report_interval_s and report_interval_s > 0:
        silent_window_s = thresholds.silent_multiplier * report_interval_s
        if (now - latest.ts).total_seconds() > silent_window_s:
            return Status.SILENT

    # Provisional readings (no covering calibration, kv is None) can't
    # inform ok/low/down -- they still count toward recency/`silent` above.
    calibrated = [r for r in ordered if r.kv is not None]
    if not calibrated:
        return Status.OK

    if calibrated[0].kv <= 0 or calibrated[0].kv < thresholds.down_kv:
        return Status.DOWN

    recent = calibrated[: thresholds.low_consecutive]
    if len(recent) == thresholds.low_consecutive and all(
        r.kv < thresholds.low_kv for r in recent
    ):
        return Status.LOW

    return Status.OK
