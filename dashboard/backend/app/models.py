"""SQLAlchemy models for the D1 schema.

Shared by the API and the ingest service -- see docs/dashboard-plan.md's
Identity and Storage sections for why the schema is split this way:
`readings` stores immutable facts keyed on the node; `node_assignments` and
`calibrations` are versioned interpretations resolved at query time.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


NODE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}$"
LOCATION_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,30}$"


class Node(Base):
    """One row per physical node -- auto-created on first unrecognized publish."""

    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String, primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fw_version: Mapped[str | None] = mapped_column(String, nullable=True)
    report_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(f"node_id ~ '{NODE_ID_PATTERN}'", name="ck_nodes_node_id_shape"),
    )


class NodeState(Base):
    """Durable last-known display state. Updated by both retained and live
    messages; never a source of `readings` rows -- see the data contract's
    retained-message rule.
    """

    __tablename__ = "node_state"

    node_id: Mapped[str] = mapped_column(
        String, ForeignKey("nodes.node_id"), primary_key=True
    )
    last_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    was_retained: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ingest_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Location(Base):
    """One row per monitored fence point. Created only by a human via the
    dashboard -- ingest never invents one.
    """

    __tablename__ = "locations"

    location_id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"location_id ~ '{LOCATION_ID_PATTERN}'", name="ck_locations_location_id_shape"
        ),
    )


class NodeAssignment(Base):
    """Time-varying (node_id -> location_id) mapping. Non-overlapping windows
    per node_id and per location_id are enforced at the DB level via
    exclusion constraints in the migration (SQLAlchemy has no first-class
    EXCLUDE construct).
    """

    __tablename__ = "node_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.node_id"), nullable=False)
    location_id: Mapped[str] = mapped_column(
        String, ForeignKey("locations.location_id"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_node_assignments_window_order"
        ),
    )


class Calibration(Base):
    """Versioned per-assignment calibration fit. Never mutated in place --
    a new row is opened when the constant changes.
    """

    __tablename__ = "calibrations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.node_id"), nullable=False)
    location_id: Mapped[str] = mapped_column(
        String, ForeignKey("locations.location_id"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kv_per_mv: Mapped[float] = mapped_column(Double, nullable=False)
    kv_offset: Mapped[float] = mapped_column(Double, nullable=False)
    method: Mapped[str | None] = mapped_column(String, nullable=True)
    points: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_calibrations_window_order"
        ),
    )


class FenceEvent(Base):
    """Operator-annotated timeline of deliberate physical changes. Both
    location_id and node_id are nullable -- some events are property-wide,
    some node-specific.
    """

    __tablename__ = "fence_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("locations.location_id"), nullable=True
    )
    node_id: Mapped[str | None] = mapped_column(String, ForeignKey("nodes.node_id"), nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Reading(Base):
    """Timescale hypertable, partitioned on `ts`. One row per received
    *live* message -- retained messages update NodeState only, never this
    table. `kv` here is the node's advisory estimate; the authoritative
    value is computed at query time from `calibrations`.
    """

    __tablename__ = "readings"

    # (node_id, ts) as the best-effort dedup key until `seq` exists in the
    # payload -- see the data contract's note on dedup. Timescale also
    # requires the hypertable's partitioning column (ts) in any unique
    # constraint, which this satisfies.
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        String, ForeignKey("nodes.node_id"), primary_key=True
    )
    adc_mv: Mapped[int] = mapped_column(Integer, nullable=False)
    batt_v: Mapped[float | None] = mapped_column(Double, nullable=True)
    rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    boot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_pub: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wifi_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fw: Mapped[str | None] = mapped_column(String, nullable=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_c: Mapped[float | None] = mapped_column(Double, nullable=True)
    kv: Mapped[float | None] = mapped_column(Double, nullable=True)



class IngestState(Base):
    """Singleton heartbeat row -- see D0. Kept as-is in D1, just migrated
    into alembic instead of db/init.sql.
    """

    __tablename__ = "ingest_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (CheckConstraint("id = 1", name="ck_ingest_state_singleton"),)
