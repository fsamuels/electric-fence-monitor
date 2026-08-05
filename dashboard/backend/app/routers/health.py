import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session

router = APIRouter()


class IngestStatus(BaseModel):
    connected: bool
    last_heartbeat: datetime | None
    healthy: bool


class HealthResponse(BaseModel):
    api: bool
    broker: bool
    db: bool
    ingest: IngestStatus
    healthy: bool


async def _check_broker() -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.mqtt_host, settings.mqtt_port),
            timeout=2,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, TimeoutError):
        return False


async def _check_ingest(session: AsyncSession) -> tuple[bool, IngestStatus]:
    try:
        row = (
            await session.execute(
                text("SELECT connected, last_heartbeat FROM ingest_state WHERE id = 1")
            )
        ).one_or_none()
    except Exception:  # noqa: BLE001 -- any DB error means db is unhealthy, not a crash
        return False, IngestStatus(connected=False, last_heartbeat=None, healthy=False)

    if row is None:
        return True, IngestStatus(connected=False, last_heartbeat=None, healthy=False)

    connected, last_heartbeat = row
    fresh = (
        last_heartbeat is not None
        and (datetime.now(UTC) - last_heartbeat).total_seconds()
        < settings.ingest_heartbeat_stale_s
    )
    ingest_healthy = bool(connected) and fresh
    return True, IngestStatus(
        connected=bool(connected), last_heartbeat=last_heartbeat, healthy=ingest_healthy
    )


@router.get("/healthz", response_model=HealthResponse)
async def healthz(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    broker_ok = await _check_broker()
    db_ok, ingest = await _check_ingest(session)

    return HealthResponse(
        api=True,
        broker=broker_ok,
        db=db_ok,
        ingest=ingest,
        healthy=broker_ok and db_ok and ingest.healthy,
    )
