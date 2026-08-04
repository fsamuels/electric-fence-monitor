-- D0 scaffolding only: enables the Timescale extension and creates the
-- single table /healthz needs to report ingest status. Phase D1 replaces
-- this with alembic-managed migrations for the full schema (nodes,
-- node_state, readings hypertable, calibrations, fence_events) and may
-- reshape ingest_state along the way -- this file is not meant to survive
-- past D1.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS ingest_state (
    id SMALLINT PRIMARY KEY DEFAULT 1,
    connected BOOLEAN NOT NULL DEFAULT false,
    last_heartbeat TIMESTAMPTZ,
    CONSTRAINT ingest_state_singleton CHECK (id = 1)
);

INSERT INTO ingest_state (id, connected, last_heartbeat)
VALUES (1, false, NULL)
ON CONFLICT (id) DO NOTHING;
