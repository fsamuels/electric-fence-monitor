"""D1 initial schema: nodes, node_state, locations, node_assignments,
calibrations, fence_events, readings hypertable, ingest_state, continuous
aggregates, compression/retention policies.

See docs/dashboard-plan.md's Storage section for the reasoning behind the
table split. Replaces dashboard/db/init.sql, which only ever covered
ingest_state for D0.

Revision ID: d1_0001
Revises:
Create Date: 2026-08-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "d1_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NODE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}$"
LOCATION_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,30}$"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    # Needed for the equality clause in the node_assignments exclusion
    # constraints below -- plain GiST doesn't support `=` on text/int.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "nodes",
        sa.Column("node_id", sa.String(), primary_key=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fw_version", sa.String(), nullable=True),
        sa.Column("report_interval_s", sa.Integer(), nullable=True),
        sa.Column("sample_interval_s", sa.Integer(), nullable=True),
        sa.CheckConstraint(f"node_id ~ '{NODE_ID_PATTERN}'", name="ck_nodes_node_id_shape"),
    )

    op.create_table(
        "node_state",
        sa.Column("node_id", sa.String(), sa.ForeignKey("nodes.node_id"), primary_key=True),
        sa.Column("last_payload", JSONB, nullable=False),
        sa.Column("payload_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("was_retained", sa.Boolean(), nullable=False),
        sa.Column("ingest_seen_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "locations",
        sa.Column("location_id", sa.String(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"location_id ~ '{LOCATION_ID_PATTERN}'", name="ck_locations_location_id_shape"
        ),
    )

    op.create_table(
        "node_assignments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.String(), sa.ForeignKey("nodes.node_id"), nullable=False),
        sa.Column(
            "location_id", sa.String(), sa.ForeignKey("locations.location_id"), nullable=False
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_node_assignments_window_order",
        ),
    )
    # No overlapping windows for a given node_id (one board can't be in two
    # places) or a given location_id (two boards at one point would
    # silently interleave readings) -- see docs/dashboard-plan.md Storage.
    op.execute(
        """
        ALTER TABLE node_assignments
        ADD CONSTRAINT excl_node_assignments_node_no_overlap
        EXCLUDE USING gist (
            node_id WITH =,
            tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz), '[)') WITH &&
        )
        """
    )
    op.execute(
        """
        ALTER TABLE node_assignments
        ADD CONSTRAINT excl_node_assignments_location_no_overlap
        EXCLUDE USING gist (
            location_id WITH =,
            tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz), '[)') WITH &&
        )
        """
    )

    op.execute(
        """
        CREATE TABLE fence_events (
            id BIGSERIAL PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL,
            location_id VARCHAR REFERENCES locations(location_id),
            node_id VARCHAR REFERENCES nodes(node_id),
            kind VARCHAR NOT NULL,
            note TEXT
        )
        """
    )

    # Assignment changes must land on the fence_events timeline regardless
    # of which future API writes node_assignments -- enforced here rather
    # than in application code, so it can't be forgotten (see
    # docs/dashboard-plan.md D1 checklist).
    op.execute(
        """
        CREATE FUNCTION log_node_assignment_change() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO fence_events (ts, location_id, node_id, kind, note)
                VALUES (
                    NEW.valid_from, NEW.location_id, NEW.node_id, 'assigned',
                    'node ' || NEW.node_id || ' assigned to ' || NEW.location_id
                );
            ELSIF TG_OP = 'UPDATE' AND OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL THEN
                INSERT INTO fence_events (ts, location_id, node_id, kind, note)
                VALUES (
                    NEW.valid_to, NEW.location_id, NEW.node_id, 'unassigned',
                    'node ' || NEW.node_id || ' unassigned from ' || NEW.location_id
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_node_assignments_fence_event
        AFTER INSERT OR UPDATE ON node_assignments
        FOR EACH ROW EXECUTE FUNCTION log_node_assignment_change()
        """
    )

    op.create_table(
        "calibrations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.String(), sa.ForeignKey("nodes.node_id"), nullable=False),
        sa.Column(
            "location_id", sa.String(), sa.ForeignKey("locations.location_id"), nullable=False
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kv_per_mv", sa.Double(), nullable=False),
        sa.Column("kv_offset", sa.Double(), nullable=False),
        sa.Column("method", sa.String(), nullable=True),
        sa.Column("points", JSONB, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_calibrations_window_order",
        ),
    )

    op.create_table(
        "readings",
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column(
            "node_id", sa.String(), sa.ForeignKey("nodes.node_id"), primary_key=True
        ),
        sa.Column("adc_mv", sa.Integer(), nullable=False),
        sa.Column("batt_v", sa.Double(), nullable=True),
        sa.Column("rssi", sa.Integer(), nullable=True),
        sa.Column("boot", sa.Integer(), nullable=True),
        sa.Column("failed_pub", sa.Integer(), nullable=True),
        sa.Column("wifi_ms", sa.Integer(), nullable=True),
        sa.Column("fw", sa.String(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("temp_c", sa.Double(), nullable=True),
        sa.Column("kv", sa.Double(), nullable=True),
    )
    # Must run in the same migration that creates the table, before any
    # rows exist -- converting a populated table is a separate, more
    # annoying path (see docs/dashboard-plan.md Storage).
    op.execute("SELECT create_hypertable('readings', 'ts')")

    op.create_table(
        "ingest_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("connected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_ingest_state_singleton"),
    )
    op.execute(
        "INSERT INTO ingest_state (id, connected, last_heartbeat) VALUES (1, false, NULL)"
    )

    # Continuous aggregates over raw adc_mv/batt_v -- calibrated kV stays a
    # query-time computation (see Calibration), so it is deliberately not
    # materialized here.
    op.execute(
        """
        CREATE MATERIALIZED VIEW readings_hourly
        WITH (timescaledb.continuous) AS
        SELECT
            node_id,
            time_bucket('1 hour', ts) AS bucket,
            min(adc_mv) AS adc_mv_min,
            max(adc_mv) AS adc_mv_max,
            avg(adc_mv) AS adc_mv_avg,
            min(batt_v) AS batt_v_min,
            max(batt_v) AS batt_v_max,
            avg(batt_v) AS batt_v_avg,
            count(*) AS reading_count
        FROM readings
        GROUP BY node_id, bucket
        WITH NO DATA
        """
    )
    op.execute(
        """
        CREATE MATERIALIZED VIEW readings_daily
        WITH (timescaledb.continuous) AS
        SELECT
            node_id,
            time_bucket('1 day', ts) AS bucket,
            min(adc_mv) AS adc_mv_min,
            max(adc_mv) AS adc_mv_max,
            avg(adc_mv) AS adc_mv_avg,
            min(batt_v) AS batt_v_min,
            max(batt_v) AS batt_v_max,
            avg(batt_v) AS batt_v_avg,
            count(*) AS reading_count
        FROM readings
        GROUP BY node_id, bucket
        WITH NO DATA
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy('readings_hourly',
            start_offset => INTERVAL '3 hours',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour')
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy('readings_daily',
            start_offset => INTERVAL '3 days',
            end_offset => INTERVAL '1 day',
            schedule_interval => INTERVAL '1 day')
        """
    )

    # Compress raw chunks older than ~30 days.
    op.execute(
        """
        ALTER TABLE readings SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'node_id',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    op.execute("SELECT add_compression_policy('readings', INTERVAL '30 days')")

    # Keep raw readings for 1 year (well past "a season"); the daily
    # aggregate has no retention policy and is kept indefinitely -- it's
    # tiny and is what year-over-year vegetation comparison needs.
    op.execute("SELECT add_retention_policy('readings', INTERVAL '1 year')")


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('readings', if_exists => true)")
    op.execute("SELECT remove_compression_policy('readings', if_exists => true)")
    op.execute(
        "SELECT remove_continuous_aggregate_policy('readings_daily', if_exists => true)"
    )
    op.execute(
        "SELECT remove_continuous_aggregate_policy('readings_hourly', if_exists => true)"
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS readings_daily")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS readings_hourly")

    op.drop_table("ingest_state")
    op.drop_table("readings")
    op.drop_table("calibrations")
    op.execute("DROP TRIGGER trg_node_assignments_fence_event ON node_assignments")
    op.execute("DROP FUNCTION log_node_assignment_change()")
    op.drop_table("fence_events")
    op.execute(
        "ALTER TABLE node_assignments DROP CONSTRAINT excl_node_assignments_location_no_overlap"
    )
    op.execute(
        "ALTER TABLE node_assignments DROP CONSTRAINT excl_node_assignments_node_no_overlap"
    )
    op.drop_table("node_assignments")
    op.drop_table("locations")
    op.drop_table("node_state")
    op.drop_table("nodes")
