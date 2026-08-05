"""Query-time resolution of location and calibrated kV for readings.

See docs/dashboard-plan.md's Storage section: `readings` stores immutable
facts keyed on `node_id`; which location a reading belongs to and what kV it
converts to are both interpretations, resolved here by joining to whichever
`node_assignments` / `calibrations` row's validity window covers the
reading's `ts`. Never read location or kV from the payload's advisory
values, and never stamp them at ingest -- that's what makes a backdated
calibration correct history in one write and a relocation leave prior
readings attributed to the prior location.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ResolvedReading:
    ts: datetime
    node_id: str
    adc_mv: int
    batt_v: float | None
    rssi: int | None
    failed_pub: int | None
    wifi_ms: int | None
    kv: float | None
    provisional: bool


def calibrate(adc_mv: float, kv_per_mv: float | None, kv_offset: float | None) -> float | None:
    if kv_per_mv is None:
        return None
    return adc_mv * kv_per_mv + kv_offset


# Joins each reading to the node_assignments row for `location_id` whose
# window covers the reading's ts, then to the calibrations row for that
# same (node_id, location_id) pair whose window also covers it. A reading
# with no covering calibration still comes back (LEFT JOIN) with
# kv_per_mv/kv_offset NULL -- callers treat that as provisional.
_READINGS_FOR_LOCATION_SQL = """
SELECT
    r.ts,
    r.node_id,
    r.adc_mv,
    r.batt_v,
    r.rssi,
    r.failed_pub,
    r.wifi_ms,
    c.kv_per_mv,
    c.kv_offset
FROM readings r
JOIN node_assignments a
    ON a.node_id = r.node_id
    AND a.location_id = :location_id
    AND r.ts >= a.valid_from
    AND (a.valid_to IS NULL OR r.ts < a.valid_to)
LEFT JOIN calibrations c
    ON c.node_id = r.node_id
    AND c.location_id = a.location_id
    AND r.ts >= c.valid_from
    AND (c.valid_to IS NULL OR r.ts < c.valid_to)
WHERE r.ts >= :since AND r.ts < :until
ORDER BY r.ts
"""


async def readings_for_location(
    session: AsyncSession,
    location_id: str,
    since: datetime,
    until: datetime,
) -> list[ResolvedReading]:
    """Every reading attributed to `location_id` in [since, until), across
    however many nodes were assigned there over that window -- a relocation
    or board swap mid-window is handled by the join, not by the caller.
    """
    rows = (
        await session.execute(
            text(_READINGS_FOR_LOCATION_SQL),
            {"location_id": location_id, "since": since, "until": until},
        )
    ).all()
    return [
        ResolvedReading(
            ts=row.ts,
            node_id=row.node_id,
            adc_mv=row.adc_mv,
            batt_v=row.batt_v,
            rssi=row.rssi,
            failed_pub=row.failed_pub,
            wifi_ms=row.wifi_ms,
            kv=calibrate(row.adc_mv, row.kv_per_mv, row.kv_offset),
            provisional=row.kv_per_mv is None,
        )
        for row in rows
    ]


_RECENT_READINGS_FOR_NODE_SQL = """
SELECT
    r.ts,
    r.node_id,
    r.adc_mv,
    r.batt_v,
    r.rssi,
    r.failed_pub,
    r.wifi_ms,
    c.kv_per_mv,
    c.kv_offset
FROM readings r
LEFT JOIN calibrations c
    ON c.node_id = r.node_id
    AND c.location_id = :location_id
    AND r.ts >= c.valid_from
    AND (c.valid_to IS NULL OR r.ts < c.valid_to)
WHERE r.node_id = :node_id
ORDER BY r.ts DESC
LIMIT :limit
"""


async def recent_readings_for_node(
    session: AsyncSession, node_id: str, location_id: str, limit: int
) -> list[ResolvedReading]:
    """Most-recent-first readings for a node, calibrated against the
    calibration row(s) for `location_id` (the node's current assignment) --
    used for status derivation, which only ever looks at recent history.
    """
    rows = (
        await session.execute(
            text(_RECENT_READINGS_FOR_NODE_SQL),
            {"node_id": node_id, "location_id": location_id, "limit": limit},
        )
    ).all()
    return [
        ResolvedReading(
            ts=row.ts,
            node_id=row.node_id,
            adc_mv=row.adc_mv,
            batt_v=row.batt_v,
            rssi=row.rssi,
            failed_pub=row.failed_pub,
            wifi_ms=row.wifi_ms,
            kv=calibrate(row.adc_mv, row.kv_per_mv, row.kv_offset),
            provisional=row.kv_per_mv is None,
        )
        for row in rows
    ]


_CURRENT_ASSIGNMENT_SQL = """
SELECT node_id, location_id, valid_from
FROM node_assignments
WHERE location_id = :location_id AND valid_to IS NULL
"""


async def current_assignment_for_location(
    session: AsyncSession, location_id: str
) -> tuple[str, datetime] | None:
    """(node_id, valid_from) of the currently-open assignment for a
    location, or None if the location is unmonitored.
    """
    row = (
        await session.execute(text(_CURRENT_ASSIGNMENT_SQL), {"location_id": location_id})
    ).one_or_none()
    if row is None:
        return None
    return row.node_id, row.valid_from


_NODE_FOR_ASSIGNMENT_SQL = """
SELECT report_interval_s FROM nodes WHERE node_id = :node_id
"""


async def report_interval_for_node(session: AsyncSession, node_id: str) -> int | None:
    row = (
        await session.execute(text(_NODE_FOR_ASSIGNMENT_SQL), {"node_id": node_id})
    ).one_or_none()
    return row.report_interval_s if row else None
