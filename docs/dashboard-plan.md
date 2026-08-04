# Dashboard Development Plan

Builds the monitoring dashboard **ahead of hardware** using mock data, so
visibility exists the moment a real node ships. This resolves the software
plan's [Milestone B](software-plan.md#milestone-b--backend-decision-home-assistant-vs-custom)
backend decision early — see "Decision" below — and treats the dashboard as a
Phase 5 (Backend Bring-Up) head start, not a throwaway prototype.

**Guiding constraint:** the mock publisher speaks the exact MQTT topic and
JSON payload the firmware already publishes (documented in
[firmware/README.md](../firmware/README.md)), so no downstream component
needs to know whether a reading came from a simulated node or a real one.

**The one axis where mock deliberately differs from real is time**, and it
differs by a lot: the mock publisher runs at an accelerated cadence (seconds)
while a real node sleeps 10–15 minutes between reports. Everything
time-dependent — chart ranges, consecutive-reading counts, silence timeouts,
row volume — behaves differently under the two. That gap is managed
explicitly rather than ignored: see [Time scale](#time-scale-mock-vs-real)
below. Every other part of the pipeline is contract-identical, so the
hardware cutover is a configuration and re-tuning exercise (see
[Phase D6](#phase-d6--real-hardware-cutover-blocked-on-hardware-phase-46--firmware-phase-2)),
not a rewrite.

---

## Decision (Milestone B)

**Custom stack: Mosquitto + Postgres/TimescaleDB + FastAPI + React/TypeScript**, not Home Assistant.

Rationale:

- HA gives free alerting/dashboards but constrains custom views (e.g. the
  Phase 7 multi-node fault-localization overlay) to its automation/card model.
  **This is a trade, not a free win:** the stack below is six containers to
  HA's one, and it does not ship the push alerting HA gives away — that has
  to be built (see [Phase D5.5](#phase-d55--minimal-push-alerting)). The
  custom stack is chosen for control over the end state and for transferable
  skills, accepting more operational surface and more work to reach feature
  parity. It is not the lighter-weight option and shouldn't be defended as one.
- Grafana+InfluxDB (the software plan's other named fallback) was considered
  and passed over: it's a strong observability stack but skills there are
  more self-hosted-ops-specific. A hand-rolled API + relational store +
  frontend is closer to general enterprise software engineering, which is a
  deliberate secondary goal here.
- Postgres + the TimescaleDB extension: **chosen for the tooling, not because
  the data volume demands it.** Be honest about the scale — 5 nodes at a
  10-minute duty cycle is 144 readings/node/day, about **263k rows/year
  total**. Plain Postgres would not notice that. What Timescale actually buys
  here is `time_bucket` for server-side chart downsampling, plus continuous
  aggregates and retention/compression policies that make the "at least a
  season of history" requirement a config line instead of a cron job. The
  cost to keep in view: Timescale's Docker images trail upstream Postgres
  releases, which matters if deployment lands on a Pi (ARM) later. If it ever
  becomes an obstacle, dropping back to stock Postgres is a viable retreat at
  this volume.
- Backend in Python (FastAPI) — fits the domain (MQTT, data handling) and is
  the faster path for a project this size. Frontend in React **+ TypeScript**
  — deliberately chosen over plain JS for the transferable-skills goal, while
  keeping the backend in the language that's the better functional fit.
- MQTT/Mosquitto is not really a choice — it's already the firmware's
  committed transport (`fence/<node-id>/state`, retained messages).

**Whole-property Home Assistant hub: deferred, not rejected.** The house/farm
already run a mix of Google-ecosystem devices, smart bulbs/outlets, a Sense
energy monitor, a Droplet water monitor, Blink cameras, and a Roomba —
enough that a property-wide HA hub is a real, independently valuable project,
just a bigger scope than "monitor the fence." Rather than merge that decision
into this one, the two are kept compatible: Mosquitto is already the shared
transport, so a future HA instance can subscribe to `fence/+/state` on the
same broker and surface fence status alongside everything else, with zero
change to this dashboard, the ingest service, or the firmware. This app
doesn't need to wait on that decision, and that decision doesn't need to
route through this app.

**Deployment target (Raspberry Pi vs. cloud) is explicitly deferred.**
Docker Compose makes every component portable — the same images run
unchanged on a Pi, a cloud VM, or a NAS. Decide this once there's an
always-on box question to answer for real (same moment Milestone B's
"always-on box" prerequisite gets resolved).

One constraint that is *not* deferred with it: the project's whole point is
checking status *remotely*, so any on-property deployment needs
Tailscale/WireGuard for off-property access. That's not a footnote — it's the
authentication boundary the [Security](#security) section relies on instead of
exposing the API publicly, and it's a listed prerequisite for
[Phase D6](#phase-d6--real-hardware-cutover-blocked-on-hardware-phase-46--firmware-phase-2).
Deferring *where* the stack runs is fine; deferring *how it's reached* is how
you end up port-forwarding an unauthenticated dashboard at the last minute.

---

## Architecture

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
                                                     │  /nodes, /readings   │
                                                     │  status derivation   │
                                                     └──────────┬───────────┘
                                                                │ HTTP(S)
                                                                ▼
                                                     ┌──────────────────────┐
                                                     │  React + TS frontend │
                                                     │  status card + graph │
                                                     └──────────────────────┘
```

All six services (broker, mock-publisher, ingest, api, db, frontend) run via
one `docker-compose.yml` in `dashboard/`.

**Ingest is its own container, not a FastAPI background task.** Co-locating
them looks simpler and isn't: if the subscriber thread dies — malformed
payload, broker disconnect, DB unavailable — the API keeps serving stale rows
and reports itself healthy, so a dead ingest is indistinguishable from a quiet
fence. That is exactly the ambiguity the `silent` status exists to resolve, so
it must not be reintroduced by the process topology. Separate containers also
mean `uvicorn --reload` in development can't double-subscribe and
double-insert, and either side can be restarted alone. The API and ingest
still share the `models.py` / `db.py` modules; only the process boundary
differs.

### Data contract (reused, not invented)

Topic: `fence/<node-id>/state`, retained, QoS 0. Payload — the firmware's
actual message today (see [firmware/README.md](../firmware/README.md)):

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

**Single source of truth:** this payload currently exists as hand-copied JSON
in `firmware/README.md`, in this document, and (once written) in the mock
publisher. Three copies with nothing enforcing agreement will drift the first
time a field is added. D0 creates `contract/fence-state.schema.json` at the
repo root as the authoritative definition; the mock publisher generates
against it, the ingest parser validates against it, and both READMEs point at
it instead of restating it.

#### Reserved optional fields — add to the contract now

The firmware doesn't send these yet and doesn't have to. Reserving them costs
nothing today and avoids a breaking change to a contract that ingest, dedup,
status derivation, and the charts all sit on:

| Field | Type | Meaning | Ingest behavior when absent |
|---|---|---|---|
| `ts` | int (epoch seconds, UTC) | When the node *took* the reading | Fall back to receipt time |
| `seq` | int | Monotonic per-node reading counter | Fall back to `(node, boot)` |
| `sleep_interval_s` | int | This node's configured duty cycle | Fall back to configured per-node default |

`ts` matters more than it looks. Software plan Phase 4 already commits to
buffering readings in RTC memory across failed transmits and flushing on
reconnect — the moment that lands, receipt time is simply wrong for every
buffered row, and a whole flush arrives stamped within the same second.
Reserving the field now means that work becomes additive. (Sending it requires
NTP on wake, which costs awake-time against the hardware Phase 3 energy
budget — a real reason for the firmware to defer it, and a good reason for the
backend not to depend on it.)

`sleep_interval_s` is load-bearing for the `silent` status, which is defined
relative to *this node's* cadence. It cannot be one global constant: mock
nodes report every 5–15 s and real nodes every 600 s, so a single value makes
every real node permanently silent or every mock node permanently fine — and
during the D6 cutover both are live at once. Self-describing in the payload is
the preferred fix; a per-node DB column is the fallback until firmware sends it.

#### Retained messages must not create readings

The firmware publishes with `retain=true` so the dashboard shows a last-known
value immediately. The consequence for ingest is easy to miss and corrupts
data silently: **the broker re-delivers the retained message to every new
subscriber at subscribe time.** With naive "one row per received message"
ingestion, every restart of the ingest container — rebuild, crash loop, reload
— reinserts the last reading for every node stamped with a *fresh, wrong*
timestamp. Restart ten times while developing and you have ten fabricated
readings that look like a healthy node reporting normally. Worse, it silently
resurrects a dead node: one that stopped reporting an hour ago gets a
brand-new row the instant ingest restarts, clearing its `silent` status.

**Rule:** MQTT exposes a retain flag on received messages (paho:
`msg.retain`). A message with that flag set updates the in-memory/`nodes`
last-known state for display and **never inserts a `readings` row.** Live
messages arrive with the flag clear and are inserted normally.

#### Delivery is at-most-once — gaps are normal

The firmware publishes at QoS 0 and disconnects ~50 ms later before deep
sleep. A publish can be lost with no error surfaced, and `failed_pub` only
counts *connection* failures, not lost messages. So a missing reading is an
expected steady-state event, not evidence of a fault — particularly over the
weak mesh Wi-Fi that is already a named project risk. Two implications:

- The `silent` threshold is really "N consecutive losses," so it must be set
  with false positives in mind (hence 2–3× cadence, not 1×).
- If false silents prove annoying in the field, the fix is QoS 1 on both the
  firmware publish and the ingest subscription — deferred, but this is where
  it would go, and it's why `seq` is reserved above.

### Storage

`nodes` table: `node_id` (PK), `first_seen`, `fw_version`, `sleep_interval_s`,
`calibrated` (bool, see [Frontend](#frontend)). Calibration *constants* stay
deferred to `docs/calibration.md` per the software plan — not duplicated here.

**No `last_seen` column.** It would be a denormalized copy of
`max(readings.ts)` needing an update on every insert, and the two will diverge
the first time a write path is missed. At 263k rows/year, derive it. Same
reasoning applies to `fw_version`: it arrives on every message and changes
whenever OTA (software plan Phase 4) ships, so it's an upsert-on-change
convenience field, not authoritative history — the authoritative record is the
`fw` value on each reading.

`readings` hypertable (Timescale): `node_id`, `ts`, `kv`, `adc_mv`, `batt_v`,
`rssi`, `boot`, `failed_pub`, `wifi_ms`, `fw`. One row per received *live*
message (retained messages excluded — see the data contract above).

Two specifics worth writing down before they bite:

- **`ts` is `timestamptz`, not `timestamp`.** Naive timestamps work perfectly
  until the deployment box and the development machine disagree about
  timezone, at which point every historical chart silently shifts. Store UTC,
  convert at the edge.
- **`create_hypertable()` is a raw-SQL Alembic operation**, and it must run in
  the same migration that creates the table, before any rows exist —
  converting a populated table is a separate and more annoying path.

Unknown-node policy: a message from an unrecognized `node` id auto-creates a
`nodes` row. This is what makes onboarding nodes 2–5 a pure config change, but
it also means a typo'd `NODE_ID` silently creates a phantom node rather than
erroring — an accepted trade, noted so it isn't a surprise during D6.

#### Retention and rollups

Software plan Phase 5 requires **at least a season of readings** so
vegetation-growth trends are visible. That requirement has to be implemented
somewhere, and this is the somewhere — otherwise Timescale is being run for no
reason (see the Decision section's honest accounting of why it's here):

- **Continuous aggregate** bucketing `readings` to hourly and daily min/max/avg
  of `kv` and `batt_v`, per node. The long chart ranges and the slow-decline
  trend tier read the aggregate, not raw rows.
- **Compression policy** on raw chunks older than ~30 days.
- **Retention policy:** keep raw readings for 1 year (well past "a season"),
  keep the daily aggregate indefinitely — it's tiny and it's what the
  year-over-year vegetation comparison actually needs.

Set these in D1 rather than "later." They're a few lines each while the schema
is being written and a data-migration exercise afterward.

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
| `silent` | no reading for > 2–3× **that node's** sleep interval (from `sleep_interval_s`, not a global constant — see data contract) |

**Implement this as a pure function** — `derive_status(readings, thresholds,
node) -> Status` — with no I/O and no framework coupling. It has to be
callable from three places: the API request path (now), the alerting scheduler
(D5.5), and unit tests (D3). Burying it in a route handler means rewriting it
the first time something other than an HTTP request needs an answer.

#### Stateless-on-read is right for display and insufficient for alerting

Computing status on read is the correct choice for a dashboard, but the claim
that push alerting can "just read the same API later" is only half true, and
the difference is worth understanding before D5.5 rather than during it:

- Display is **level-triggered** — "what is the status right now?" — and a
  stateless query answers it perfectly.
- Alerting is **edge-triggered** — "did this node *transition* ok→low?" — and
  needs both an observer (nothing polls on its own; a stateless API only
  computes status when a human loads the page, so a fence that goes down at
  2 a.m. is noticed at 8 a.m. when someone opens the tab) and a memory of what
  was already announced, so it doesn't re-page every evaluation cycle.

So D5.5 adds a scheduler and a `status_transitions` table (`node_id`, `ts`,
`from_status`, `to_status`, `notified_at`). That's an addition, not a
rewrite — provided the status logic is the pure function described above.
Noted here so the D3 design leaves room for it.

The `silent`/battery split from software plan Phase 6 is deliberately
preserved: last known `batt_v` low → probably node power; `batt_v` healthy →
probably Wi-Fi or node failure. Either way the fence state is *unknown*, which
is itself the alert.

#### Link quality is a first-class signal, not debug data

`failed_pub` and `wifi_ms` are in the payload specifically to inform the
antenna-vs-LoRa decision (software plan Connectivity Contingency), and
`rssi` feeds the same call. Surfacing them is nearly free once they're stored:
a "flaky link" indicator on the node card — rising `failed_pub`, `wifi_ms`
trending up, `rssi` weak — turns a real engineering decision into an
observation instead of a field trip. This is separate from fence status and
shouldn't be folded into the status badge.

### API surface (initial)

- `GET /nodes` — all nodes, each with latest reading + derived status
- `GET /nodes/{id}` — node detail + latest reading
- `GET /nodes/{id}/readings?since=&bucket=` — time series for the chart.
  **`bucket` (server-side downsampling) is part of the initial design, not an
  optimization to add later.** A season of 5-node history is ~1.3M points and
  no chart library should receive that; `time_bucket` collapses it to whatever
  the range needs, which is the concrete thing Timescale is here for.
- `GET /healthz` — **three-way**, not a boolean: `{api, broker, db}`. "API is
  up" is close to worthless on its own; what matters operationally is whether
  ingest still holds a broker connection and when it last wrote a row. Given
  ingest runs as its own container, this is the only thing that distinguishes
  "the fence is quiet" from "the pipeline is dead."
- Live updates: polling to start (simple, adequate for a 10–15 min firmware
  duty cycle); a `/ws/live` WebSocket is a stretch goal, not required for MVP.

**Frontend types are generated from OpenAPI, not hand-mirrored.** FastAPI
emits an OpenAPI schema for free; `openapi-typescript` (or similar) turns it
into `src/api/schema.ts` as a build step. Hand-maintained duplicates of
backend models drift silently and the drift shows up as a runtime `undefined`
in the browser. This is a five-minute setup in D0 that removes an entire
class of bug.

### Frontend

Per-node card: status badge (color-coded per table above), current kV as a
large number, voltage-over-time line chart via Recharts, secondary stats
(battery, RSSI, last seen, link quality).

The card component and the grid that holds it are built together from the
start — the README scopes multi-node display in from day one, and it's also
groundwork for the Phase 7 comparative/overlay view. D4 renders that grid with
a single node in it and D5 adds the rest; there is no single-node layout that
later gets thrown away.

**Chart ranges are set by the real duty cycle, not the mock cadence.** This is
the most likely thing to get built wrong. At the firmware's 600 s sleep
interval a node produces **6 points per hour**, so the conventional
`1h / 24h / 7d` toggle gives a six-point line for the first option — fine
against a mock publisher emitting every 10 s, useless in production. Real
point counts per node:

| Range | Points at 600 s cadence | Verdict |
|---|---|---|
| 1 h | 6 | Useless — omit |
| 24 h | 144 | Good default |
| 7 d | 1,008 | Good, bucket to hourly |
| 30 d | 4,320 | Bucket to hourly |
| Season (~90 d) | ~13,000 | Bucket to daily min/max/avg |

So: **24h / 7d / 30d / season**, with `bucket` set per range. Develop against
backfilled data at real spacing (see Mock publisher) so the chart is tuned
against the density it will actually receive.

**Uncalibrated nodes are marked as such.** Until hardware Phase 4 calibration
completes, `CAL_KV_PER_MV` is theoretical divider math and the reported `kV`
is wrong-but-plausible — the worst kind of wrong, because it invites debugging
the fence when the problem is a constant in `config.h`. A node with
`calibrated = false` shows its kV visibly provisional and displays `adc_mv`
alongside it. This is what makes the D6 window (real hardware reporting,
calibration not yet done) survivable.

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

#### Two modes, and the second one is the important one

- **Live mode** — publishes over MQTT on an accelerated, configurable interval
  (default 5–15 s) so the dashboard is visibly alive while developing. Also
  supports `--cadence realtime` (600 s), which is what the pre-cutover
  rehearsal in D6 runs against.
- **Backfill mode** — generates *N days of history at the real 600 s spacing,
  ending now*, written directly to the database rather than published. This is
  what actually de-risks the chart ranges, the bucketing, the retention
  policy, query performance, and the slow-decline trend tier, none of which
  can be exercised meaningfully by a publisher that has only been running for
  twenty minutes. `--backfill 90d --scenario slow-decline` should produce a
  season of plausible history in seconds.

Backfill writes straight to Postgres deliberately: pushing 90 days of history
through the broker would be both slow and a lie, since real history never
arrives that way.

#### Coexistence with real nodes

The firmware uses `NODE_ID` as its MQTT **client id**
(`mqtt.connect(NODE_ID)`). Two clients presenting the same id make the broker
evict one on each connect, producing an endless reconnect loop that is
genuinely confusing to diagnose. Since D6 has mock and real nodes live at the
same time:

- Mock publisher uses distinct client ids (`mock-pub-<n>`), never a bare node id.
- Mock nodes are namespaced `mock-01`…`mock-05`, so `fence-01` is
  unambiguously the real hardware and mock data is trivially separable in the
  database afterward.

---

## Time scale (mock vs. real)

The single largest source of "it worked in development" risk in this plan, and
the reason it gets its own section. A real node reports every 600 s; the mock
publisher defaults to every 10 s. That's a 60× difference, and it inverts in
different directions depending on what's being tested:

| | Mock (10 s) | Real (600 s) |
|---|---|---|
| Readings/node/day | 8,640 | 144 |
| 5 nodes, per day | 43,200 | 720 |
| 5 nodes, per year | — | ~263,000 |
| Points in a 1 h chart | 360 | 6 |
| Points in a 7 d chart | 60,480 | 1,008 |

Six days of mock data exceeds a full year of production volume, while a mock
chart is 60× denser than the real one. So mock is simultaneously
*over*-representative of load and *under*-representative of sparseness —
tuning anything against it naively gets both wrong.

Handling, applied throughout the plan above:

1. Cadence is an explicit config knob with a `realtime` setting, not a constant.
2. Chart ranges and bucket sizes are chosen from the **real** cadence table in
   [Frontend](#frontend).
3. Anything window-based — `low` consecutive counts, `silent` timeout, trend
   detection — is expressed in **multiples of the node's `sleep_interval_s`**,
   never in absolute seconds. This is what makes the same config correct for
   both a 10 s mock node and a 600 s real one.
4. Backfill at real spacing is the default way to develop chart, aggregate,
   and trend behavior.
5. D6 includes a realtime-cadence rehearsal before real hardware arrives.

---

## Security

Absent from the first draft of this plan, and one half of it must be settled
*before* nodes are deployed rather than after.

### Broker authentication and ACLs — decide before D6

`config.example.h` currently ships `MQTT_USER ""` with "leave empty for
anonymous," and an unauthenticated broker means anyone on the ranch network
can publish `fence/fence-01/state` claiming a healthy 6.9 kV. For a system
whose entire purpose is reporting that the fence is *not* fine, a spoofable
"everything's fine" is the worst available failure mode — strictly worse than
the dashboard being down, which is at least visibly broken.

- Per-node MQTT credentials, not one shared account.
- Mosquitto ACL restricting each node to publishing only its own
  `fence/<its-id>/state`; ingest gets a separate read-only account subscribed
  to `fence/+/state`.
- `allow_anonymous false` once credentials exist.

The timing matters: this is a `config.h` change plus a reflash. Cheap while
one bench node exists, a walk to every fence post afterward.

### API and frontend

The stated point of the project is checking status *remotely*, which means
this gets exposed beyond the LAN eventually. Auth isn't purely a deployment
concern — it changes the API surface and the frontend, so the posture is
recorded now even though the deployment target isn't:

- **Local/LAN development:** no auth, as it stands today.
- **Remote access, first choice:** no public exposure at all — reach the
  dashboard over Tailscale/WireGuard and let the VPN be the authentication
  boundary. This is the lowest-effort option that isn't negligent, and it
  matches the deferred-deployment posture.
- **If ever publicly exposed:** TLS terminated at a reverse proxy, plus a
  single-user session/token auth on the API. Not built now; noted so the API
  isn't designed in a way that makes it painful.

MQTT over TLS on the node side is explicitly *not* planned — it's real
overhead on a battery-powered ESP32 for a LAN-local hop, and the ACL work
above addresses the actual threat.

---

## Availability — who watches the watcher

Neither this plan nor the software plan states an availability expectation for
the monitoring system itself, and it needs one before anybody trusts it: this
is the escalation path for a real containment function, and horses are on the
other side of it.

The failure that matters: **a dead Pi, dead broker, or dead ingest looks
exactly like a healthy quiet fence.** Silence is the normal state of a system
that only speaks up when something is wrong, so the absence of alerts proves
nothing. Nothing inside the stack can detect its own total failure.

The standard answer, and the one adopted here: a **dead-man's switch**. The
stack pings an external service (healthchecks.io or equivalent) on a schedule;
that service alerts when the pings *stop*. The check lives outside every
component it's watching, which is the whole point.

- Scoped into D5.5, alongside the first real alerting.
- The ping should be emitted by the alerting scheduler and conditioned on
  ingest health (`/healthz`), so it stops on a dead ingest, not just a dead
  host.
- Hard prerequisite before the system is trusted for unattended operation —
  i.e. before the software plan's Phase 6 alert drills and the hardware plan's
  Phase 6 field deployment.

---

## Testing

The software plan has a Testing Strategy section; the first draft of this one
had nothing, for the component with the most moving parts.

### Contract test (highest value, do it first)

The payload schema is currently guaranteed by a JSON blob copied by hand into
multiple files. Once `contract/fence-state.schema.json` exists (D0), a test
asserts that every mock scenario emits payloads validating against it, and
that the ingest parser accepts every documented field and rejects malformed
input predictably. This is the test that keeps "mock and real are
indistinguishable" true as fields get added.

### Unit

- **Status derivation** — the pure function, table-driven across every
  scenario and boundary: exactly at threshold, N-1 vs. N consecutive low
  readings, silent at 2× vs. 3× cadence, low battery vs. healthy battery on a
  silent node. This is the logic alerting will depend on; it's also trivially
  testable, so there's no excuse.
- **Ingest edge cases**, each of which is a real thing the broker will hand
  it: retained flag set (must not insert), malformed JSON, missing required
  field, unexpected extra field (must not crash — the firmware will add
  fields), wrong types, unknown node id, duplicate `(node, boot)`.

### Integration

Compose-based: publish a known message to the real broker, assert a row lands
in the real database with the right values. Slower, few in number, but it's
the only thing that actually tests the wiring the unit tests mock out.

### Resilience drills

Mirrors the software plan's alert drills — deliberately induce each failure
and confirm the system reports it correctly rather than silently coping:

- Kill the DB while the broker is up; confirm ingest doesn't wedge and
  `/healthz` reports the failure.
- Kill the broker; confirm ingest reconnects and `/healthz` reports the gap.
- Restart ingest repeatedly with retained messages present; **confirm no
  phantom readings are created** — this is the specific regression test for
  the retained-message rule above.
- Stop a mock node; confirm `silent` fires at the expected multiple of its
  cadence, and clears correctly when it resumes.

### CI

There is no CI in this repo at all today, and `firmware/lint.sh` already
exists and is run by hand. One GitHub Actions workflow, added in D0:

- `firmware/lint.sh` (cppcheck + cpplint — no ESP32 toolchain needed, which is
  exactly why it's CI-able)
- backend: ruff + pytest
- frontend: tsc + eslint + vitest
- contract schema validation

---

## Directory layout

```
contract/
  fence-state.schema.json   Authoritative MQTT payload schema — firmware,
                            mock publisher, and ingest all defer to this
  examples/                 Golden payloads used by tests on both sides
.github/workflows/ci.yml    firmware lint + backend + frontend + contract

dashboard/
  docker-compose.yml
  mosquitto/
    mosquitto.conf
    aclfile                  Per-node publish restrictions (see Security)
  backend/
    app/
      main.py                FastAPI app + router mounts
      ingest.py              MQTT subscriber — its OWN container entrypoint,
                             not a background task inside the API process
      status.py              derive_status(): pure function, no I/O
      models.py              SQLAlchemy models (Timescale hypertable)
      db.py
      routers/nodes.py
      routers/health.py      /healthz — api / broker / db, three-way
      config.py              thresholds, consecutive-reading counts,
                             cadence multiples (never absolute seconds)
    alembic/                 migrations (incl. create_hypertable, policies)
    tests/
    pyproject.toml
    Dockerfile               One image, two entrypoints (api | ingest)
  mock-publisher/
    publisher.py             live mode (MQTT) + backfill mode (direct DB)
    scenarios.py
    Dockerfile
  frontend/
    src/
      api/
        schema.ts            GENERATED from OpenAPI — do not hand-edit
        client.ts
      components/
        NodeCard.tsx
        VoltageChart.tsx
        StatusBadge.tsx
        LinkQuality.tsx      failed_pub / wifi_ms / rssi — feeds the
                             antenna-vs-LoRa decision
      pages/Dashboard.tsx    Node grid from D4 onward (one card, then many)
    Dockerfile
    package.json
```

`contract/` sits at the repo root, not under `dashboard/`, because the
firmware is the other party to it.

---

## Phased plan

### Phase D0 — Scaffolding
- [ ] `contract/fence-state.schema.json` + example payloads, derived from the firmware's current message
- [ ] `docker-compose.yml` wiring Mosquitto, Postgres+Timescale, empty FastAPI app, empty ingest container, empty React app
- [ ] Networking between services confirmed
- [ ] `GET /healthz` returning three-way api/broker/db status
- [ ] OpenAPI → TypeScript client generation wired as a build step
- [ ] CI workflow: `firmware/lint.sh`, backend lint/test, frontend typecheck/test, contract validation

> **Mosquitto 2.x will not work out of the box, and this is where the
> afternoon goes.** It defaults to `allow_anonymous false` with a
> localhost-only listener, so a minimal `mosquitto.conf` in Docker silently
> refuses every connection from other containers. The dev config needs an
> explicit `listener 1883 0.0.0.0`; anonymous access is acceptable *only*
> until the Security section's per-node credentials land, which is a
> prerequisite for D6, not for D0.

**Exit:** `docker compose up` brings up all six services; `/healthz` reports all three subsystems green; React dev server reachable; CI green on an empty stack.

### Phase D1 — Data contract & storage
- [ ] `nodes` / `readings` schema; `create_hypertable` in the initial migration; `ts` as `timestamptz`
- [ ] MQTT ingest subscriber (`fence/+/state`) in its own container, validating against the contract schema
- [ ] **Retained-message handling: retained → update last-known state, never insert a reading**
- [ ] Optional `ts` / `seq` / `sleep_interval_s` honored when present, sensible fallbacks when absent
- [ ] Continuous aggregate (hourly + daily), compression policy, retention policy
- [ ] Ingest edge-case tests: malformed, missing field, extra field, unknown node, retained replay

**Exit:** manually publishing one MQTT message produces exactly one row; restarting the ingest container ten times produces **zero** additional rows.

### Phase D2 — Mock publisher
- [ ] Normal-operation scenario for one simulated node, live mode at accelerated cadence
- [ ] Remaining scenarios: slow-decline, low-voltage, fence-down, node-silent, battery-drain
- [ ] `--cadence realtime` (600 s) option
- [ ] **Backfill mode**: N days of history at real 600 s spacing, written directly to the DB
- [ ] Distinct client ids (`mock-pub-<n>`) and namespaced node ids (`mock-01`…)
- [ ] Contract test: every scenario's payload validates against `contract/fence-state.schema.json`

**Exit:** DB fills with plausible time series across every scenario on demand; `--backfill 90d` produces a season of realistically-spaced history in seconds.

### Phase D3 — API
- [ ] `GET /nodes`, `GET /nodes/{id}`, `GET /nodes/{id}/readings?since=&bucket=`
- [ ] `derive_status()` as a pure, I/O-free function per the thresholds table above
- [ ] Windows expressed as multiples of each node's `sleep_interval_s`, never absolute seconds
- [ ] Table-driven status tests across every scenario and boundary condition

**Exit:** API returns the correct derived status for each mock scenario, and the same status for a node whether it's running at 10 s or 600 s cadence.

### Phase D4 — Frontend MVP
- [ ] Node grid shell rendering a single `NodeCard`: status badge, current kV, voltage-over-time chart
- [ ] Chart ranges 24h/7d/30d/season with server-side bucketing, validated against backfilled data
- [ ] Provisional/uncalibrated presentation, with `adc_mv` shown alongside kV

**Exit:** dashboard visibly reflects mock data changes within one polling interval, and the 7d chart looks right against 90 days of backfill — not just against twenty minutes of live mock.

### Phase D5 — Multi-node & live updates
- [ ] Grid populated with all mock nodes, one card each
- [ ] Link-quality indicator (`failed_pub`, `wifi_ms`, `rssi`)
- [ ] Polling-based live updates; WebSocket as stretch goal

**Exit:** 2+ mock nodes visible simultaneously; a silent/down node is visually distinct from the rest.

### Phase D5.5 — Minimal push alerting

Pulled forward from software plan Phase 6 deliberately. Push notification is
the project's *stated primary requirement*, not a nicety, and the custom-stack
decision means it's the one thing that doesn't arrive for free. Left at the
end of a distant phase, the realistic outcome is a good-looking dashboard that
never actually pages anyone — the failure mode this project exists to prevent.
Once D3's status derivation exists, this is small.

- [ ] `status_transitions` table (`node_id`, `ts`, `from_status`, `to_status`, `notified_at`)
- [ ] Scheduler evaluating `derive_status()` per node on an interval — the observer a stateless API doesn't provide
- [ ] Edge-triggered dispatch with de-duplication: notify on transition, not on every evaluation
- [ ] One delivery channel (ntfy or Pushover), severity split: `low` vs. `down` vs. `silent`
- [ ] **Dead-man's switch**: scheduler pings an external service, conditioned on ingest health
- [ ] Drill each condition against mock scenarios and confirm exactly one notification per transition

**Exit:** driving a mock node into each fault state produces exactly one correctly-prioritized push notification; stopping the whole stack causes the external dead-man's-switch to fire.

### Phase D6 — Real hardware cutover (blocked on hardware Phase 4/6 + firmware Phase 2)

Not a config change. The contract is identical, but the *operating conditions*
are not, and this is where the plan's mock-vs-real assumptions get audited.

**Before hardware arrives:**
- [ ] Run the full stack against `--cadence realtime` mock nodes for ≥24 h; confirm charts, `silent` timeouts, and alerting all behave at 600 s spacing
- [ ] Per-node MQTT credentials + Mosquitto ACLs in place (Security section) — before nodes are flashed and deployed, not after
- [ ] Remote access path decided and working (Tailscale/WireGuard), since off-property visibility is the point of the project

**Cutover:**
- [ ] Point real firmware's `MQTT_HOST` config at this broker (dev, then wherever it's deployed)
- [ ] Confirm no client-id collision: real node is `fence-01`, mock publishers are `mock-pub-<n>`
- [ ] Mark the real node `calibrated = false` until hardware Phase 4 completes; expect wrong-but-plausible kV and read `adc_mv` in the meantime
- [ ] Retire mock nodes (keep the publisher — it's the test fixture and the CI dependency); mock data is separable by the `mock-` prefix

**Re-tune against reality:**
- [ ] Confirm `sleep_interval_s` is correct per node and `silent` doesn't false-fire
- [ ] Expect `silent` to be common, not exceptional — weak mesh Wi-Fi is a top-5 project risk and QoS 0 loses messages silently. Tune the threshold against observed loss rate rather than theory
- [ ] Feed observed `rssi` / `wifi_ms` / `failed_pub` into the antenna-vs-LoRa decision (software plan Connectivity Contingency)
- [ ] Set `calibrated = true` and record constants in `docs/calibration.md` after hardware Phase 4

**Exit:** a real node's reading appears in the dashboard, indistinguishable in shape from mock data; alerting fires correctly at real cadence; a deliberately powered-down node produces a `silent` alert within the expected window.

---

## Explicitly out of scope for this pass

- **Full alert management** — a *minimal* push path is now in scope
  ([Phase D5.5](#phase-d55--minimal-push-alerting)) because it's the project's
  primary requirement. What stays out: alert acknowledgment, quiet hours,
  escalation chains, multiple delivery channels, and the slow-decline trend
  tier (software plan Phase 6). One channel, one notification per transition.
- **Deployment target** — deferred, see Decision section above.
- **Public internet exposure** — remote access is via VPN; TLS termination and
  API session auth are designed-for but not built (see Security).
- **MQTT over TLS on the node side** — rejected on power/complexity grounds
  for a LAN-local hop; broker ACLs address the actual threat.
- **QoS 1 delivery** — deferred unless observed message loss in the field
  justifies it; `seq` is reserved in the contract so it can be added later
  without a breaking change.
- **Fault localization / comparative multi-node overlay** (software plan
  Phase 7) — the multi-node grid here is groundwork, not the full feature.

---

## Open questions

Recorded rather than silently assumed:

1. **Does `ts` ever get sent?** It costs NTP-on-wake against the hardware
   Phase 3 energy budget. Reserved in the contract either way; the decision
   only becomes forcing when Phase 4 buffering lands.
2. **What loss rate does QoS 0 actually produce** over the ranch mesh? Sets
   the `silent` threshold and determines whether QoS 1 is worth it. Only
   answerable with real hardware at a real deployment point.
3. **Is a `low` reading ever legitimately transient?** The N-consecutive rule
   assumes yes. Field data from hardware Phase 6 confirms or refutes.
4. **Does Timescale earn its place** once real volume is visible? At 263k
   rows/year, stock Postgres remains a viable retreat.
