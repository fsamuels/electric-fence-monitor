"""Node listing and assignment endpoints -- see docs/dashboard-plan.md Phase
D3. Moving a node is "close one row, open another" (Storage section): both
the node's prior open assignment and the target location's prior open
assignment (a board swap) are closed in the same transaction as the new
assignment is opened, so `node_assignments` never has to be patched up by
hand. The DB trigger from D1 (`log_node_assignment_change`) turns each of
those inserts/closes into a `fence_events` row automatically.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter()


class NodeSummary(BaseModel):
    node_id: str
    fw_version: str | None
    report_interval_s: int | None
    sample_interval_s: int | None
    first_seen: datetime
    location_id: str | None
    last_payload_ts: datetime | None
    last_received_at: datetime | None
    rssi: int | None
    wifi_ms: int | None
    failed_pub: int | None


class AssignmentCreate(BaseModel):
    location_id: str
    valid_from: datetime | None = None


class Assignment(BaseModel):
    id: int
    node_id: str
    location_id: str
    valid_from: datetime
    valid_to: datetime | None


_NODES_SQL = """
SELECT
    n.node_id,
    n.fw_version,
    n.report_interval_s,
    n.sample_interval_s,
    n.first_seen,
    a.location_id,
    s.payload_ts AS last_payload_ts,
    s.received_at AS last_received_at,
    (s.last_payload ->> 'rssi')::int AS rssi,
    (s.last_payload ->> 'wifi_ms')::int AS wifi_ms,
    (s.last_payload ->> 'failed_pub')::int AS failed_pub
FROM nodes n
LEFT JOIN node_assignments a ON a.node_id = n.node_id AND a.valid_to IS NULL
LEFT JOIN node_state s ON s.node_id = n.node_id
ORDER BY n.node_id
"""


@router.get("/nodes", response_model=list[NodeSummary])
async def list_nodes(session: AsyncSession = Depends(get_session)) -> list[NodeSummary]:
    rows = (await session.execute(text(_NODES_SQL))).all()
    return [NodeSummary(**row._mapping) for row in rows]


async def _node_exists(session: AsyncSession, node_id: str) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM nodes WHERE node_id = :node_id"), {"node_id": node_id}
        )
    ).one_or_none()
    return row is not None


async def _location_exists(session: AsyncSession, location_id: str) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM locations WHERE location_id = :location_id"),
            {"location_id": location_id},
        )
    ).one_or_none()
    return row is not None


@router.post("/nodes/{node_id}/assignment", response_model=Assignment, status_code=201)
async def assign_node(
    node_id: str, body: AssignmentCreate, session: AsyncSession = Depends(get_session)
) -> Assignment:
    if not await _node_exists(session, node_id):
        raise HTTPException(status_code=404, detail="node not found")
    if not await _location_exists(session, body.location_id):
        raise HTTPException(status_code=404, detail="location not found")

    valid_from = body.valid_from or datetime.now(UTC)

    try:
        # Close this node's current assignment, if any (relocation).
        await session.execute(
            text(
                "UPDATE node_assignments SET valid_to = :valid_from "
                "WHERE node_id = :node_id AND valid_to IS NULL"
            ),
            {"node_id": node_id, "valid_from": valid_from},
        )
        # Close the target location's current assignment, if any (board swap).
        await session.execute(
            text(
                "UPDATE node_assignments SET valid_to = :valid_from "
                "WHERE location_id = :location_id AND valid_to IS NULL"
            ),
            {"location_id": body.location_id, "valid_from": valid_from},
        )
        row = (
            await session.execute(
                text(
                    "INSERT INTO node_assignments (node_id, location_id, valid_from, valid_to) "
                    "VALUES (:node_id, :location_id, :valid_from, NULL) "
                    "RETURNING id, node_id, location_id, valid_from, valid_to"
                ),
                {"node_id": node_id, "location_id": body.location_id, "valid_from": valid_from},
            )
        ).one()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="assignment window conflicts with an existing assignment",
        ) from exc

    return Assignment(**row._mapping)


@router.delete("/nodes/{node_id}/assignment", status_code=204)
async def unassign_node(node_id: str, session: AsyncSession = Depends(get_session)) -> None:
    now = datetime.now(UTC)
    result = await session.execute(
        text(
            "UPDATE node_assignments SET valid_to = :now "
            "WHERE node_id = :node_id AND valid_to IS NULL"
        ),
        {"node_id": node_id, "now": now},
    )
    if result.rowcount == 0:
        await session.rollback()
        raise HTTPException(status_code=404, detail="node has no current assignment")
    await session.commit()
