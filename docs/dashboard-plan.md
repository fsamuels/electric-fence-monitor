# Dashboard Development Plan

Builds the monitoring dashboard **ahead of hardware** using mock data, so
visibility exists the moment a real node ships. This resolves the software
plan's [Milestone B](software-plan.md#milestone-b--backend-decision-home-assistant-vs-custom)
backend decision early — see "Decision" below — and treats the dashboard as a
Phase 5 (Backend Bring-Up) head start, not a throwaway prototype.

**Guiding constraint:** the mock publisher speaks the exact MQTT topic and
JSON payload the firmware already publishes (documented in
[firmware/README.md](../firmware/README.md)). Nothing downstream of the
broker can tell mock data from real data. When hardware exists, swapping in
real nodes is a config change (point firmware at the broker), not a rewrite.

---

## Decision (Milestone B)

**Custom stack: Mosquitto + Postgres/TimescaleDB + FastAPI + React/TypeScript**, not Home Assistant.

Rationale:

- HA gives free alerting/dashboards but constrains custom views (e.g. the
  Phase 7 multi-node fault-localization overlay) to its automation/card model,
  and is a heavier thing to run just for local dev.
- Grafana+InfluxDB (the software plan's other named fallback) was considered
  and passed over: it's a strong observability stack but skills there are
  more self-hosted-ops-specific. A hand-rolled API + relational store +
  frontend is closer to general enterprise software engineering, which is a
  deliberate secondary goal here.
- Postgres + the TimescaleDB extension gives real time-series performance
  without leaving standard SQL/Postgres tooling.
- Backend in Python (FastAPI) — fits the domain (MQTT, data handling) and is
  the faster path for a project this size. Frontend in React **+ TypeScript**
  — deliberately chosen over plain JS for the transferable-skills goal, while
  keeping the backend in the language that's the better functional fit.
- MQTT/Mosquitto is not really a choice — it's already the firmware's
  committed transport (`fence/<node-id>/state`, retained messages).

**Deployment target (Raspberry Pi vs. cloud) is explicitly deferred.**
Docker Compose makes every component portable — the same images run
unchanged on a Pi, a cloud VM, or a NAS. Decide this once there's an
always-on box question to answer for real (same moment Milestone B's
"always-on box" prerequisite gets resolved). One constraint to keep in mind
whichever way it goes: the project's whole point is checking status
*remotely*, so a Pi-only deployment will eventually need something like
Tailscale/WireGuard for off-property access — not a blocker now, just a
footnote so it isn't forgotten later.

---

## Architecture

```
┌─────────────────┐        ┌───────────────┐        ┌──────────────────────┐
│  mock-publisher   │──MQTT──▶│   Mosquitto   │──MQTT──▶│  ingest (FastAPI bg)  │
│  (fake nodes,      │  publish│    broker     │ subscribe│  writes readings to  │
│  1..N scenarios)   │        │  fence/+/state│        │  Postgres/TimescaleDB │
└─────────────────┘        └───────────────┘        └───────────┬──────────┘
                                                                     │
        (later: real ESP32 nodes publish here instead)              │
                                                                     ▼
                                                         ┌──────────────────────┐
                                                         │   Postgres +          │
                                                         │   TimescaleDB          │
                                                         │   nodes / readings     │
                                                         └───────────┬──────────┘
                                                                     │ SQL
                                                                     ▼
                                                         ┌──────────────────────┐
                                                         │  FastAPI REST API      │
                                                         │  /nodes, /readings     │
                                                         │  status derivation     │
                                                         └───────────┬──────────┘
                                                                     │ HTTP(S)
                                                                     ▼
                                                         ┌──────────────────────┐
                                                         │  React + TS frontend   │
                                                         │  status card + graph   │
                                                         └──────────────────────┘
```

All five services (broker, mock-publisher, backend, db, frontend) run via
one `docker-compose.yml` in `dashboard/`.

### Data contract (reused, not invented)

Topic: `fence/<node-id>/state`, retained. Payload — identical to the
firmware's actual message (see [firmware/README.md](../firmware/README.md)):

```json
{
  "node": "fence-01",
  "fw": "0.1.0",
  "kv": 6.93,
  "adc_mv": 1872,
  "batt_v": 3.98,
  "rssi": -71,
  "boot": 123,
  "failed_pub": 2,
  "wifi_ms": 2300
}
```

The firmware payload has no timestamp field; the ingest service stamps each
row with receipt time (`ts`) on write. Fine for now since publish is
near-real-time on wake — worth revisiting only if buffered/delayed
transmission (software plan Phase 4 hardening) becomes real.

### Storage

`nodes` table: `node_id` (PK), `first_seen`, `last_seen`, `fw_version`.
Calibration fields deferred to `docs/calibration.md` per the software plan —
not duplicated here.

`readings` hypertable (Timescale): `node_id`, `ts`, `kv`, `adc_mv`, `batt_v`,
`rssi`, `boot`, `failed_pub`, `wifi_ms`. One row per received message.

### Status derivation

Computed at the API layer, not stored — mirrors the thresholds already
defined in [software-plan.md Phase 6](software-plan.md#phase-6--alert-logic)
(kept as config, not hardcoded, since the plan calls out thresholds may
differ per node/season):

| Status | Condition |
|---|---|
| `ok` | `kv` ≥ low threshold (default 5 kV) |
| `low` | `kv` < low threshold for N consecutive readings (default 2–3) |
| `down` | `kv` < down floor (default 1 kV) or pinned at zero |
| `silent` | no reading for > 2–3× the node's sleep interval |

This first cut computes and *displays* status — it does not page anyone.
Push/active alerting (software plan Phase 6) is a separate concern that can
read the same API/DB later without touching ingestion or the dashboard.

### API surface (initial)

- `GET /nodes` — all nodes, each with latest reading + derived status
- `GET /nodes/{id}` — node detail + latest reading
- `GET /nodes/{id}/readings?since=` — time series for the chart (range-selectable)
- Live updates: polling to start (simple, adequate for a 10–15 min firmware
  duty cycle); a `/ws/live` WebSocket is a stretch goal, not required for MVP.

### Frontend

Per-node card: status badge (color-coded per table above), current kV as a
large number, voltage-over-time line chart (range toggle: 1h/24h/7d) via
Recharts, secondary stats (battery, RSSI, last seen). Multiple cards in a
grid from the start (README already scopes multi-node display in from day
one) — this also lays groundwork for the Phase 7 comparative/overlay view
without a later rewrite.

### Mock publisher

Simulates 1–5 fake nodes, each independently configurable to a scenario, so
every dashboard status state is exercisable on demand instead of waiting on
real fault conditions:

- **normal** — `kv` oscillating around ~7 kV with small noise, matching the operating range in the README
- **slow-decline** — gradual drift toward the low threshold over minutes (stands in for a vegetation-load trend over days)
- **low-voltage** — parked under 5 kV
- **fence-down** — pinned near zero
- **node-silent** — stops publishing entirely
- **battery-drain** — `batt_v` trending down alongside normal `kv`

Publishes on an accelerated, configurable interval (e.g. every 5–15 s) rather
than the firmware's real 10–15 min duty cycle, so the dashboard is visibly
alive during development.

---

## Directory layout

```
dashboard/
  docker-compose.yml
  mosquitto/
    mosquitto.conf
  backend/
    app/
      main.py             FastAPI app + router mounts
      mqtt_ingest.py       MQTT subscriber background task
      models.py            SQLAlchemy models (Timescale hypertable)
      db.py
      routers/nodes.py
      config.py            thresholds, consecutive-reading counts, sleep interval
    alembic/                migrations
    pyproject.toml
    Dockerfile
  mock-publisher/
    publisher.py
    scenarios.py
    Dockerfile
  frontend/
    src/
      api/                 typed client for the FastAPI backend
      components/
        NodeCard.tsx
        VoltageChart.tsx
        StatusBadge.tsx
      pages/Dashboard.tsx
      types.ts              mirrors backend Pydantic schemas
    Dockerfile
    package.json
```

---

## Phased plan

### Phase D0 — Scaffolding
- [ ] `docker-compose.yml` wiring Mosquitto, Postgres+Timescale, empty FastAPI app, empty React app
- [ ] Networking between services confirmed

**Exit:** `docker compose up` brings up all services; FastAPI health check and React dev server both reachable.

### Phase D1 — Data contract & storage
- [ ] `nodes` / `readings` schema, Timescale hypertable on `readings`
- [ ] MQTT ingest subscriber (`fence/+/state`) writing rows on receipt

**Exit:** manually publishing one MQTT message produces a row in the DB.

### Phase D2 — Mock publisher
- [ ] Normal-operation scenario for one simulated node at accelerated cadence
- [ ] Remaining scenarios: slow-decline, low-voltage, fence-down, node-silent, battery-drain

**Exit:** DB fills with plausible time series across every scenario on demand.

### Phase D3 — API
- [ ] `GET /nodes`, `GET /nodes/{id}`, `GET /nodes/{id}/readings`
- [ ] Status derivation logic per the thresholds table above

**Exit:** API returns the correct derived status for each mock scenario.

### Phase D4 — Frontend MVP
- [ ] Single-node view: status badge, current kV, voltage-over-time chart

**Exit:** dashboard visibly reflects mock data changes within one polling interval.

### Phase D5 — Multi-node & live updates
- [ ] Node grid, one card per node
- [ ] Polling-based live updates; WebSocket as stretch goal

**Exit:** 2+ mock nodes visible simultaneously; a silent/down node is visually distinct from the rest.

### Phase D6 — Real hardware cutover (blocked on hardware Phase 4/6 + firmware Phase 2)
- [ ] Stop the mock publisher
- [ ] Point real firmware's `MQTT_BROKER` config at this broker (dev, then wherever it's deployed)

**Exit:** a real node's reading appears in the dashboard, indistinguishable in shape from mock data.

---

## Explicitly out of scope for this pass

- **Push/active alerting** (software plan Phase 6) — the dashboard shows
  status visually; phone/text notifications are a later addition that can
  read the same API/DB.
- **Deployment target** — deferred, see Decision section above.
- **Fault localization / comparative multi-node overlay** (software plan
  Phase 7) — the multi-node grid here is groundwork, not the full feature.
