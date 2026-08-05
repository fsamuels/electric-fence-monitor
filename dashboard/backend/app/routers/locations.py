"""Location, reading, and fence-event endpoints -- see docs/dashboard-plan.md
Phase D3. kV and location are both resolved at query time (app.resolve);
this router never reads the payload's advisory `kv` or a location stamped
at ingest.
"""

from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import resolve
from app.config import settings
from app.db import get_session
from app.status import ReadingForStatus, Status, StatusThresholds, derive_status

router = APIRouter()


def _thresholds() -> StatusThresholds:
    return StatusThresholds(
        low_kv=settings.status_low_kv,
        down_kv=settings.status_down_kv,
        low_consecutive=settings.status_low_consecutive,
        silent_multiplier=settings.status_silent_multiplier,
    )


class LocationSummary(BaseModel):
    location_id: str
    label: str
    status: Status
    node_id: str | None
    current_kv: float | None
    provisional: bool
    updated_at: datetime | None


class LocationDetail(LocationSummary):
    notes: str | None
    created_at: datetime
    assignment_valid_from: datetime | None


class LocationCreate(BaseModel):
    location_id: str
    label: str
    notes: str | None = None


class ReadingPoint(BaseModel):
    ts: datetime
    kv_avg: float | None
    kv_min: float | None
    kv_max: float | None
    adc_mv_avg: float
    provisional: bool
    reading_count: int


class FenceEvent(BaseModel):
    id: int
    ts: datetime
    location_id: str | None
    node_id: str | None
    kind: str
    note: str | None


class FenceEventCreate(BaseModel):
    ts: datetime | None = None
    location_id: str | None = None
    node_id: str | None = None
    kind: str
    note: str | None = None


async def _location_row(session: AsyncSession, location_id: str):
    row = (
        await session.execute(
            text(
                "SELECT location_id, label, notes, created_at FROM locations "
                "WHERE location_id = :location_id"
            ),
            {"location_id": location_id},
        )
    ).one_or_none()
    return row


async def _summary_for_location(session: AsyncSession, location_id: str, label: str) -> LocationSummary:
    assignment = await resolve.current_assignment_for_location(session, location_id)
    if assignment is None:
        return LocationSummary(
            location_id=location_id,
            label=label,
            status=Status.UNMONITORED,
            node_id=None,
            current_kv=None,
            provisional=False,
            updated_at=None,
        )

    node_id, _valid_from = assignment
    report_interval_s = await resolve.report_interval_for_node(session, node_id)
    recent = await resolve.recent_readings_for_node(
        session, node_id, location_id, limit=settings.status_low_consecutive
    )
    status = derive_status(
        [ReadingForStatus(ts=r.ts, kv=r.kv) for r in recent],
        has_assignment=True,
        report_interval_s=report_interval_s,
        thresholds=_thresholds(),
        now=datetime.now(UTC),
    )
    latest = recent[0] if recent else None
    return LocationSummary(
        location_id=location_id,
        label=label,
        status=status,
        node_id=node_id,
        current_kv=latest.kv if latest else None,
        provisional=latest.provisional if latest else False,
        updated_at=latest.ts if latest else None,
    )


@router.get("/locations", response_model=list[LocationSummary])
async def list_locations(session: AsyncSession = Depends(get_session)) -> list[LocationSummary]:
    rows = (await session.execute(text("SELECT location_id, label FROM locations"))).all()
    return [await _summary_for_location(session, row.location_id, row.label) for row in rows]


@router.post("/locations", response_model=LocationDetail, status_code=201)
async def create_location(
    body: LocationCreate, session: AsyncSession = Depends(get_session)
) -> LocationDetail:
    existing = await _location_row(session, body.location_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="location already exists")

    now = datetime.now(UTC)
    await session.execute(
        text(
            "INSERT INTO locations (location_id, label, notes, created_at) "
            "VALUES (:location_id, :label, :notes, :created_at)"
        ),
        {
            "location_id": body.location_id,
            "label": body.label,
            "notes": body.notes,
            "created_at": now,
        },
    )
    await session.commit()
    return await get_location(body.location_id, session)


@router.get("/locations/{location_id}", response_model=LocationDetail)
async def get_location(
    location_id: str, session: AsyncSession = Depends(get_session)
) -> LocationDetail:
    row = await _location_row(session, location_id)
    if row is None:
        raise HTTPException(status_code=404, detail="location not found")

    summary = await _summary_for_location(session, location_id, row.label)
    assignment = await resolve.current_assignment_for_location(session, location_id)
    return LocationDetail(
        **summary.model_dump(),
        notes=row.notes,
        created_at=row.created_at,
        assignment_valid_from=assignment[1] if assignment else None,
    )


def _bucket_readings(
    readings: list[resolve.ResolvedReading], bucket: str
) -> list[ReadingPoint]:
    if bucket == "raw":
        return [
            ReadingPoint(
                ts=r.ts,
                kv_avg=r.kv,
                kv_min=r.kv,
                kv_max=r.kv,
                adc_mv_avg=r.adc_mv,
                provisional=r.provisional,
                reading_count=1,
            )
            for r in readings
        ]

    grouped: dict[datetime, list[resolve.ResolvedReading]] = defaultdict(list)
    for r in readings:
        key = (
            r.ts.replace(minute=0, second=0, microsecond=0)
            if bucket == "hour"
            else r.ts.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        grouped[key].append(r)

    points = []
    for key in sorted(grouped):
        rows = grouped[key]
        kvs = [r.kv for r in rows if r.kv is not None]
        points.append(
            ReadingPoint(
                ts=key,
                kv_avg=sum(kvs) / len(kvs) if kvs else None,
                kv_min=min(kvs) if kvs else None,
                kv_max=max(kvs) if kvs else None,
                adc_mv_avg=sum(r.adc_mv for r in rows) / len(rows),
                provisional=any(r.provisional for r in rows),
                reading_count=len(rows),
            )
        )
    return points


@router.get("/locations/{location_id}/readings", response_model=list[ReadingPoint])
async def get_location_readings(
    location_id: str,
    since: datetime,
    until: datetime | None = None,
    bucket: str = Query(default="raw", pattern="^(raw|hour|day)$"),
    session: AsyncSession = Depends(get_session),
) -> list[ReadingPoint]:
    if await _location_row(session, location_id) is None:
        raise HTTPException(status_code=404, detail="location not found")

    until_dt = until or datetime.now(UTC)
    readings = await resolve.readings_for_location(session, location_id, since, until_dt)
    return _bucket_readings(readings, bucket)


@router.get("/fence-events", response_model=list[FenceEvent])
async def list_fence_events(
    location_id: str | None = None,
    node_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[FenceEvent]:
    clauses = []
    params: dict = {}
    if location_id is not None:
        clauses.append("location_id = :location_id")
        params["location_id"] = location_id
    if node_id is not None:
        clauses.append("node_id = :node_id")
        params["node_id"] = node_id
    if since is not None:
        clauses.append("ts >= :since")
        params["since"] = since
    if until is not None:
        clauses.append("ts < :until")
        params["until"] = until

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = (
        await session.execute(
            text(
                f"SELECT id, ts, location_id, node_id, kind, note FROM fence_events "
                f"{where} ORDER BY ts DESC"
            ),
            params,
        )
    ).all()
    return [FenceEvent(**row._mapping) for row in rows]


@router.post("/fence-events", response_model=FenceEvent, status_code=201)
async def create_fence_event(
    body: FenceEventCreate, session: AsyncSession = Depends(get_session)
) -> FenceEvent:
    ts = body.ts or datetime.now(UTC)
    row = (
        await session.execute(
            text(
                "INSERT INTO fence_events (ts, location_id, node_id, kind, note) "
                "VALUES (:ts, :location_id, :node_id, :kind, :note) "
                "RETURNING id, ts, location_id, node_id, kind, note"
            ),
            {
                "ts": ts,
                "location_id": body.location_id,
                "node_id": body.node_id,
                "kind": body.kind,
                "note": body.note,
            },
        )
    ).one()
    await session.commit()
    return FenceEvent(**row._mapping)
