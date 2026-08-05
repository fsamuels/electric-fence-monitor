# Architecture

This document covers the **software architecture** of the dashboard stack —
the part of the project that is actually running code today. For hardware
design (sensing chain, power system, enclosure) see
[hardware-plan.md](hardware-plan.md) and the [hardware/](../hardware/)
schematics; for firmware see [firmware/README.md](../firmware/README.md).
For the full phased build-out and every design decision's rationale, see
[dashboard-plan.md](dashboard-plan.md) — this document is a shorter,
current-state-oriented companion to it, not a replacement.

## High-level overview

```
┌──────────────────┐       ┌────────────────┐       ┌──────────────────────┐
│  mock-publisher  │──MQTT─▶│   Mosquitto    │──MQTT─▶│  ingest service      │
│  fake nodes,     │publish │    broker      │  sub   │  (own container)     │
│  live + backfill │        │ fence/+/state  │        │  validate → insert   │
└──────────────────┘       └────────────────┘        └──────────┬───────────┘
                                                                │
   (later: real ESP32 nodes publish here instead)               │
                                                                ▼
                                                     ┌──────────────────────┐
                                                     │  Postgres +          │
                                                     │  TimescaleDB         │
                                                     │  nodes / readings    │
                                                     └──────────┬───────────┘
                                                                │ SQL
                                                                ▼
                                                     ┌──────────────────────┐
                                                     │  FastAPI REST API    │
                                                     │  /locations, /nodes  │
                                                     │  status derivation   │
                                                     └──────────┬───────────┘
                                                                │ HTTP (polling)
                                                                ▼
                                                     ┌──────────────────────┐
                                                     │  React + TS frontend │
                                                     │  grid + node inbox   │
                                                     └──────────────────────┘
```

The mock publisher and real ESP32 nodes are interchangeable inputs — both
speak the same MQTT topic and JSON payload, defined once in
[contract/fence-state.schema.json](../contract/fence-state.schema.json).
Nothing downstream of Mosquitto can tell the difference, which is the whole
point of building the dashboard ahead of hardware.

## Components

| Component | Responsibility | Path |
|---|---|---|
| **Contract** | Single source of truth for the MQTT payload shape; validated in CI against example payloads | [contract/](../contract/) |
| **Firmware** | ESP32 node: read sensing chain, publish retained state, deep sleep | [firmware/](../firmware/) |
| **Mock publisher** | Simulates 1–5 fake nodes across every fault scenario, in live (MQTT) or backfill (direct-to-Postgres) mode | [dashboard/mock-publisher/](../dashboard/mock-publisher/) |
| **Mosquitto broker** | MQTT transport, `fence/<node_id>/state`, retained QoS 0 | `dashboard/docker-compose.yml` (`broker` service) |
| **Ingest service** | Subscribes to `fence/+/state`, validates against the contract schema, writes to Postgres. Runs as its own container (not a FastAPI background task) so a dead subscriber can't masquerade as a healthy fence | `dashboard/backend/app/ingest.py` |
| **Postgres + TimescaleDB** | Durable storage: immutable `readings`, versioned `node_assignments`/`calibrations`, operator-logged `fence_events` | `dashboard/backend/app/models.py`, `dashboard/backend/alembic/` |
| **FastAPI API** | REST surface; resolves location/kV/status at query time rather than storing them | `dashboard/backend/app/routers/` |
| **React + TypeScript frontend** | Location grid, unassigned-node inbox, charts, assignment/log-change UI | `dashboard/frontend/src/` |

`migrate` is a one-shot container running Alembic migrations before `api`/`ingest` start; it has no long-running process of its own.

## Data flow

1. A node (mock or real) publishes a JSON reading to `fence/<node_id>/state`, retained, QoS 0.
2. The ingest service receives it. A **retained** message (broker replay on subscribe, e.g. after a restart) updates `node_state` only — it never inserts a `readings` row and never clears a `silent` status. A **live** message inserts a `readings` row and also updates `node_state`.
3. First-seen `node_id`s auto-create a `nodes` row in the **unassigned** state; `locations` are only ever created by a human via the API/UI, never by ingest.
4. The API resolves, at query time rather than at ingest time:
   - which `location_id` a `node_id`'s readings belong to, from the `node_assignments` validity window covering the reading's timestamp;
   - calibrated kV from the `calibrations` row covering the same window (falls back to a `provisional` flag + raw `adc_mv` if none covers it);
   - `ok`/`low`/`down`/`silent`/`unmonitored` status via the pure function `derive_status()` (`app/status.py`), using each node's own `report_interval_s` for the `silent` threshold rather than a global constant.
5. The frontend polls `GET /locations` and `GET /nodes` on a fixed interval (`Dashboard.tsx`), rendering one card per location plus an inbox of unassigned nodes. Each card independently polls its own readings/events on the same cadence so charts live-update, not just the status grid.
6. Assignment writes (`POST`/`DELETE /nodes/{node_id}/assignment`) and fence-event writes (`POST /fence-events`) go through the API; a DB trigger turns assignment changes into `fence_events` rows automatically, so board swaps and relocations always appear on the timeline without extra application code.

## External integrations

- **MQTT (Mosquitto)** — the only integration point with real hardware; anonymous access on a trusted LAN today, per-node credentials/ACLs tracked as later hardening (see [dashboard-plan.md Security](dashboard-plan.md#security)).
- **OpenAPI → TypeScript** — the frontend's types are generated from the FastAPI OpenAPI schema (`npm run generate:api`), not hand-mirrored. CI's `api-schema-drift` job fails the build if the committed `src/api/schema.ts` doesn't match what the current backend would generate.
- No external cloud services, push-notification provider, or third-party API is integrated yet — that's Phase D5.5 (see [roadmap.md](roadmap.md)).

## Deployment architecture

Everything runs as seven Docker Compose services (`dashboard/docker-compose.yml`): `broker` (1883), `db` (5432), `migrate` (one-shot), `api` (8000), `ingest`, `mock-publisher`, `frontend` (5173, Vite dev server). All images are portable — the same containers run unchanged on a developer laptop, a Raspberry Pi, or a cloud VM. **Deployment target (Pi vs. cloud) is explicitly deferred** until there's a real always-on-box decision to make (see dashboard-plan.md's Decision section). Remote access (Tailscale/WireGuard) is tracked as a pre-cutover requirement, not solved yet.

## Key design decisions

- **Custom stack over Home Assistant**: Mosquitto + Postgres/TimescaleDB + FastAPI + React/TS, chosen for control over custom views (multi-node fault localization) and for transferable general-purpose engineering skills, accepting more operational surface and having to build push alerting from scratch. Full rationale in [dashboard-plan.md#decision-milestone-b](dashboard-plan.md#decision-milestone-b).
- **Node identity vs. location are separate, versioned concepts.** A node knows only its own self-derived `node_id`; which fence location it's watching is a `(node_id, location_id, valid_from, valid_to)` assignment row. This lets hardware move or get swapped without rewriting history. See [dashboard-plan.md#identity](dashboard-plan.md#identity-nodes-locations-and-assignments).
- **Calibration is resolved at query time, never baked into a reading.** `adc_mv` is stored raw; `kv_per_mv`/`kv_offset` come from a versioned `calibrations` table. A backdated calibration fix retroactively repairs history in one write.
- **Status derivation is a pure function** (`derive_status()`), with no I/O, so it can be called from the API today and from the D5.5 alerting scheduler later without rewriting it.
- **Ingest is a separate container from the API**, specifically so a dead MQTT subscriber can't hide behind a healthy-looking `/healthz` — ingest publishes its own heartbeat row that `/healthz` reports on.
- **Retained MQTT messages never create a reading.** Without this rule, every ingest container restart would fabricate a fresh "reading" from the broker's replay of the last retained message per node, silently resurrecting dead nodes.

## Technical constraints

- **Mock time vs. real time diverge by ~60×** — mock nodes report every 5–15 s, real nodes will report every 10–15 minutes. Every time-based threshold (chart ranges, `silent` timeout, consecutive-reading debounce) has to be validated against `--cadence realtime` mock data, not just default fast-mock data, before the real cutover.
- **MQTT delivery is at-most-once (QoS 0)** — a missing reading is expected steady-state noise, not proof of a fault; `silent` is defined as *N consecutive* misses, not one.
- **No push alerting exists yet.** The dashboard is currently a level-triggered display (correct status when a human loads the page); nothing today notices a 2 a.m. fault by itself. That's Phase D5.5.

## Known architectural debt

- **Phase D5.5 (push alerting) not built.** No `status_transitions` table, no scheduler, no notification channel, no dead-man's switch. Until it exists, a fault at 2 a.m. is invisible until someone opens the dashboard.
- **Security is deferred by design, not by accident**, but is real debt against the "check remotely" goal: anonymous MQTT, no per-node broker ACLs, no API auth, no TLS, no remote-access story (Tailscale/WireGuard) yet. Fine on a trusted LAN; blocking for any off-property access.
- **`docs/software-plan.md`'s own phase checkboxes are stale** relative to actual progress — it points at `dashboard-plan.md` for Phase 5 status (which is current), but its Firmware Phase 1–2 checkboxes are unchecked despite working firmware code existing. Treat `dashboard-plan.md` as the authoritative phase tracker for dashboard work and `firmware/README.md` for firmware status; `software-plan.md`/`hardware-plan.md` are the higher-level plans, not up-to-date trackers.
- **No frontend routing** — the dashboard is a single page (`Dashboard.tsx`); node detail and dialogs are modal overlays with local component state rather than routed views. Fine at the current scale (a handful of locations); would need revisiting if the UI grows more sections.
