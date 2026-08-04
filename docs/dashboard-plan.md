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
while a real node sleeps 10–15 minutes between reports (a default that is
itself challenged in
[Reporting cadence and alert latency](#reporting-cadence-and-alert-latency) —
the recommendation there is to sample every minute and report every 15).
Everything
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

#### Field by field

`firmware/README.md` documents these from the producer's side. This table is
the *consumer's* view: what each field is, what values are plausible, and what
the dashboard actually does with it. "Plausible range" is what the mock
publisher generates and what the ingest validator should accept — a value
outside it is a bug signal, not a fence event.

| Field | Type | Unit | Plausible range | Where it comes from | What the dashboard does with it |
|---|---|---|---|---|---|
| `node` | string | — | `^[a-z0-9][a-z0-9-]{1,30}$` | `NODE_ID` in per-node `config.h` | Primary key. Routes the reading to a card; also the topic segment it arrived on (the two must agree — see below) |
| `fw` | string | — | semver, e.g. `0.1.0` | `FW_VERSION` compile-time constant | Shown on the node detail; lets you spot a node that missed an OTA rollout. Stored per reading, so a bad release is attributable after the fact |
| `kv` | float | kV | 0–12 | `adc_mv × CAL_KV_PER_MV + CAL_KV_OFFSET`, computed on-device | **Advisory only.** The node uses it to decide whether a reading warrants an immediate transmit; the backend recomputes kV from `adc_mv` and is authoritative for display and alerting — see [Calibration](#calibration) |
| `adc_mv` | int | mV | 0–3300 | Max of a 2.5 s multi-sample burst on the peak-detector output | **The measurement of record.** Everything downstream derives kV from this at query time, so recalibration repairs all history retroactively instead of leaving a discontinuity. Also what's displayed when no calibration covers the reading |
| `batt_v` | float | V | 2.8–4.3 | Battery divider on `PIN_BATT_ADC`, 8-sample average | Battery gauge, and the **"dead node vs. dead fence" discriminator**: a silent node whose last `batt_v` was sagging is a power problem; one that was healthy is a Wi-Fi or hardware problem. Different diagnosis, different response |
| `rssi` | int | dBm | −90 to −30 | `WiFi.RSSI()` at the moment of association | Link-quality indicator. Feeds the antenna-vs-LoRa decision with measured data instead of guesswork |
| `boot` | int | count | monotonic, resets to 0 on power loss | RTC memory counter, survives deep sleep | Wake counter. A **reset to 0 means the node lost power entirely** — brown-out, battery disconnect, or a swap. That's a distinct event from a reboot and worth surfacing |
| `failed_pub` | int | count | monotonic, resets with `boot` | Incremented on any wake that fails to publish | Cumulative missed transmissions. Rising = the link is degrading. Note it counts *connection* failures only — a QoS 0 message lost in flight increments nothing |
| `wifi_ms` | int | ms | 500–15000 | Time from `WiFi.begin()` to association | How hard the node had to work to get online. Trending up is an early weak-signal warning; at the `WIFI_TIMEOUT_MS` ceiling (15000) the node gave up |

Three of these — `rssi`, `failed_pub`, `wifi_ms` — are deliberately *not*
fence data. They describe the health of the reporting path, and they're in the
payload specifically so the antenna-vs-LoRa decision (software plan
Connectivity Contingency) can be made from field measurements. They drive the
link-quality indicator, kept visually separate from fence status.

A note on `boot` and `failed_pub`: both are **cumulative counters, not
per-message deltas**, and both reset on power loss. Chart them as rates
(difference between consecutive readings) rather than raw values, and treat a
decrease as a reset event rather than clamping it to zero.

#### Node identity — `node` is a *location*, not a board

Worth settling before nodes 2–5 exist, because it's painful to change once
history has accumulated under the wrong scheme.

There are two different identities here and the current design conflates them:

- **Deployment identity** — *which point on the fence line is this?*
- **Device identity** — *which physical ESP32 is this?*

They have different lifecycles, and one identifier can't serve both:

- Swap a failed board at the north gate → device changes, location doesn't.
  You want that location's history to continue uninterrupted.
- Move a working board from the north gate to the creek crossing → device is
  the same, location changed. You emphatically do *not* want the history to
  continue as though it's the same measurement point.

Whichever one you pick as the single id, one of those two routine operations
silently corrupts the record. And the secondary project goal —
fault localization by comparing voltage across points — is inherently about
*locations*, so location has to be the thing the data is keyed on.

**Scheme:**

| | Value | Assigned by | Stable across |
|---|---|---|---|
| `node` (topic segment, DB key, display) | Human-readable location slug: `north-gate`, `creek-crossing`, or `fence-01` if you prefer numbering | Human, in `config.h` | Board swaps |
| `chip_id` (new payload field) | Low 6 bytes of `ESP.getEfuseMac()`, hex | Factory eFuse — no config, cannot collide | Everything; it *is* the board |
| MQTT **client id** | `<node>-<chip_id suffix>` | Derived at runtime | — |

Human-readable wins for `node` because it's the thing you'll read in topic
strings while debugging (`mosquitto_sub -t 'fence/north-gate/state'`), in
Mosquitto ACL files, in the dashboard, and in `docs/calibration.md`. A UUID or
bare MAC is collision-proof but turns every one of those into a lookup.

Adding `chip_id` costs one field and buys three things:

1. **Detects a duplicate `node`** — two boards flashed with the same
   `config.h` produce interleaved readings under one id, which otherwise looks
   like an erratic fence rather than an inventory mistake.
2. **Records board swaps** — same `node`, new `chip_id` is a real event: the
   location's history continues, and the swap is visible on the timeline where
   it belongs.
3. **Fixes the client-id collision structurally.** The firmware currently uses
   `NODE_ID` as its MQTT client id, so two nodes sharing an id evict each
   other in a reconnect loop. Client id derived from the chip is unique by
   construction, while the topic stays location-stable. Both problems, one
   change.

**Constraints on the `node` string**, since it's simultaneously a topic
segment, part of a client id, and a database key:

- No `/`, `+`, `#`, whitespace, or non-ASCII — `/` and the wildcards would
  break topic routing outright.
- Lowercase kebab-case, 2–31 chars: `^[a-z0-9][a-z0-9-]{1,30}$`. Enforced in
  the contract schema and rejected at ingest rather than auto-creating a
  phantom node.
- Keep it short. The MQTT spec only guarantees 23-character client ids;
  Mosquitto is more permissive, but there's no reason to test it.
- The firmware builds the topic into a fixed `char topic[64]` via `snprintf`,
  which truncates *silently* — a long id would publish to a subtly wrong
  topic. The 31-char cap leaves ample headroom, and a compile-time
  `static_assert` on `sizeof(NODE_ID)` makes it impossible to get wrong.

**Ingest must verify `node` matches the topic it arrived on.** They're
independent inputs — the firmware could publish `{"node": "north-gate"}` to
`fence/creek-crossing/state` after a partial config edit. Mismatch is a
misconfiguration, not a reading; reject it loudly.

**Calibration keys on the pair, not on `node` alone.** The calibration
constant is a physical property of a specific divider chain *and* a specific
board's ADC. The hand-wired HV chain stays at the fence while the board is
swappable, so `docs/calibration.md` records `(node, chip_id, date)` and any
board swap invalidates the constant until re-verified against the handheld
tester. Recording it against `node` alone would silently carry a stale
constant onto new hardware — producing wrong-but-plausible kV, the exact
failure mode the provisional-reading treatment exists to prevent.

#### Reserved optional fields — add to the contract now

The firmware doesn't send these yet and doesn't have to. Reserving them costs
nothing today and avoids a breaking change to a contract that ingest, dedup,
status derivation, and the charts all sit on:

| Field | Type | Meaning | Ingest behavior when absent |
|---|---|---|---|
| `ts` | int (epoch seconds, UTC) | When the node *took* the reading | Fall back to receipt time |
| `seq` | int | Monotonic per-node reading counter | Fall back to `(node, boot)` |
| `report_interval_s` | int | How often this node sends a routine heartbeat | Fall back to configured per-node default |
| `sample_interval_s` | int | How often this node reads the fence | Assume equal to `report_interval_s` |
| `chip_id` | string (hex) | Device identity from the ESP32 eFuse MAC | Board swaps and duplicate-`node` detection unavailable; see [Node identity](#node-identity--node-is-a-location-not-a-board) |
| `temp_c` | float | Enclosure temperature | No temperature compensation of the peak-detector diode drift; see [Calibration](#calibration) |

`ts` matters more than it looks. Software plan Phase 4 already commits to
buffering readings in RTC memory across failed transmits and flushing on
reconnect — the moment that lands, receipt time is simply wrong for every
buffered row, and a whole flush arrives stamped within the same second.
Reserving the field now means that work becomes additive. (Sending it requires
NTP on wake, which costs awake-time against the hardware Phase 3 energy
budget — a real reason for the firmware to defer it, and a good reason for the
backend not to depend on it.)

The interval fields are load-bearing for the `silent` status, which is defined
relative to *this node's* cadence. It cannot be one global constant: mock
nodes report every 5–15 s and real nodes every 600–900 s, so a single value
makes every real node permanently silent or every mock node permanently fine —
and during the D6 cutover both are live at once. Self-describing in the payload
is the preferred fix; a per-node DB column is the fallback until firmware
sends it.

**Two intervals, because they stop being the same number.** Once the
[cadence recommendation](#reporting-cadence-and-alert-latency) lands, a node
samples every 60 s but only transmits a heartbeat every 900 s — plus an
immediate, off-cadence transmit whenever a reading crosses a fault threshold.
That distinction matters downstream:

- **`silent` is measured against `report_interval_s`**, never the sample
  interval. Measuring against sampling would fire an alert every heartbeat gap.
- **Arrival is no longer uniform.** A fault message arrives between
  heartbeats, so ingest, the charts, and any inter-arrival logic must treat
  irregular spacing as normal rather than as a gap or a duplicate.
- Before the split ships, firmware sends only `report_interval_s` and the
  sample interval is assumed equal — which is exactly today's behavior.

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

`nodes` table: `node_id` (PK), `first_seen`, `fw_version`, `chip_id`,
`report_interval_s`, `sample_interval_s`. Note there is no `calibrated` boolean — whether a node is
calibrated is derived from whether a `calibrations` row covers the reading's
timestamp, which is strictly more informative.

`calibrations` table: `node_id`, `chip_id`, `valid_from`, `valid_to`,
`kv_per_mv`, `kv_offset`, `method`, `points` (JSON — the raw measurement pairs,
kept so the fit can be redone), `notes`. Versioned rather than mutated, so a
reading is always converted with the constants that were valid when it was
taken. This is what makes `kv` a **query-time computation over stored
`adc_mv`** rather than a value frozen on the device — see
[Calibration](#calibration) for why that's the whole point.
`docs/calibration.md` stays the human-readable record; this table is what the
API reads.

`fence_events` table: `ts`, `node_id` (nullable — some events are
property-wide), `kind`, `note`. Operator-annotated timeline of deliberate
physical changes: wire added or removed, charger changed, vegetation cleared,
grounding modified, board swapped, recalibrated. Without it, every intentional
change is indistinguishable from a developing fault for the rest of the
node's life.

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
| `silent` | no reading for > 2–3× **that node's** `report_interval_s` (never the sample interval, and never a global constant — see data contract) |

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

**Readings with no calibration behind them are marked as such.** Until
hardware Phase 4 calibration completes, the constants are theoretical divider
math and the derived kV is wrong-but-plausible — the worst kind of wrong,
because it invites debugging the fence when the problem is a number in a
config. A reading with no `calibrations` row covering its timestamp shows its
kV visibly provisional and displays `adc_mv` alongside it. Because calibration
is versioned and applied at query time, this resolves itself retroactively the
moment a calibration row is added — the D6 window (real hardware reporting,
calibration not yet done) becomes survivable rather than a data gap.

**Charts annotate `fence_events`.** Deliberate physical changes — wire added,
charger serviced, vegetation cleared, board swapped, recalibrated — render as
markers on the time series. This is the difference between a step change
reading as "something broke here" versus "we extended the fence here."

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

## Reporting cadence and alert latency

The firmware currently sleeps 600 s between reports, which means a downed
fence can go unnoticed for ten minutes — and, as shown below, considerably
longer than that once alert debouncing is accounted for. That is worth
challenging. This section works the trade rather than assuming the default.

**All numbers here are estimates.** The energy budget falls out of hardware
Phase 3, which hasn't been built. They're good enough to rank the options and
to show that the obvious answer isn't the best one; they are not good enough
to commit to a value.

### The precondition that dominates everything else

Before cadence matters at all: **a stock ESP32 dev board draws 5–20 mA in
deep sleep**, because of its LDO regulator and USB-serial chip. At 10 mA
that's 240 mAh/day doing nothing — which exceeds the wake-cycle cost of
*every* option below, including reporting four times a minute. On a stock dev
board the runtime is ~10 days regardless of cadence, and this entire analysis
is noise.

The custom Logic & Power board (`hardware/logic-power-board-schematic.md`)
should reach 20–50 µA. **Cadence only becomes a real decision once that board
exists.** Until then, measure sleep current first — it's the whole budget.

### Where the energy actually goes

Per wake cycle, from `firmware/src/main.cpp` and `config.example.h`:

| Phase | Duration | Est. current | Energy |
|---|---|---|---|
| ADC sampling (`SAMPLE_WINDOW_MS`, radio off) | 2.5 s | ~45 mA | 0.031 mAh |
| Battery read (8 samples) | ~20 ms | ~45 mA | negligible |
| Wi-Fi associate + MQTT publish | ~3 s typical | ~120 mA | 0.100 mAh |
| **Full report cycle** | **~5.5 s** | | **~0.131 mAh** |
| **Sample-only cycle (radio never comes up)** | **2.5 s** | | **~0.031 mAh** |

The important structural fact: **bringing up the radio costs ~4× what
sampling costs.** Sampling is cheap; talking is expensive. Every option below
follows from that.

Assumes 2500 mAh usable from an 18650 (derated) and 20 µA sleep
(0.5 mAh/day).

### Options

| | Strategy | Cycles/day | mAh/day | Battery-only reserve | Fence-down latency |
|---|---|---|---|---|---|
| **A** | Report every 10 min *(current)* | 144 | 19.4 | ~129 days | ≤ 10 min |
| **B** | Report every 1 min | 1,440 | 189 | ~13 days | ≤ 1 min |
| **C** | Report every 15 s | 5,760 | 755 | ~3.3 days | ≤ 15 s |
| **D** | **Sample every 1 min, report every 15 min, transmit immediately on fault** | 1,440 sample / 96 report | 54.8 | **~45 days** | **≤ 1 min** |

**D dominates B.** Same one-minute detection latency, roughly a third of the
energy, and 3.5× the battery reserve — because the fence is fine almost all
the time, and transmitting "still fine" once a minute is the expensive part.
Sample often, talk rarely, and interrupt immediately when something is
actually wrong.

**C is the one to be wary of**, and not primarily for battery-life reasons.
At a 15 s interval the node is awake ~37% of the time, so it isn't a
duty-cycled sensor any more. More importantly the reserve drops to ~3 days:
a four-day winter overcast blinds the node, and a blind node means the fence
state is *unknown* — which this plan already treats as alert-worthy. That
trades a known, bounded latency for an increased chance of total blindness.
Bad trade.

Solar is not the binding constraint for A, B, or D. A 5 W panel yields
~1.5–7.5 Wh/day depending on season, against 0.07 Wh/day (A), 0.70 (B), and
0.20 (D). What matters is **reserve for consecutive dark days**, which is the
column above.

### The latency number is not the sleep interval

This is the part most likely to be mis-estimated. End-to-end time from
"fence goes down" to "phone buzzes":

```
sleep interval (≤ 600 s)
  + sample window (2.5 s)
  + Wi-Fi associate + publish (~3 s, or 15 s on timeout)
  + N consecutive readings required to confirm      ← the multiplier
  + alerting scheduler interval
  + any QoS 0 message lost in flight (adds a full interval)
```

The debounce is the dominant term, because it *multiplies* the interval.
The plan requires 2–3 consecutive low readings before alerting, precisely so
one noisy sample doesn't page anyone at 6 a.m.:

| Cadence | N=3 confirmation | Realistic worst case with one lost message |
|---|---|---|
| 10 min | **30 min** | ~40 min |
| 1 min | **3 min** | ~4 min |

So the current design's real low-voltage alert latency is closer to **30–40
minutes** than to ten. That reframes the question: faster cadence doesn't just
reduce latency linearly, it makes the debounce *affordable*. At one-minute
sampling you can require three confirmations and still alert in three minutes.
That's better fence detection **and** fewer false alarms — the two normally
trade against each other, and here they don't.

A `down` reading (< 1 kV or pinned at zero) should use a shorter debounce than
`low` — N=2 — since it's higher urgency and less likely to be noise.

### Second-order cost: the mesh

At option C, five nodes generate ~28,800 Wi-Fi associations/day on a mesh
that is *already known to be weak* at these locations. Failures aren't free:
a failed association burns the full `WIFI_TIMEOUT_MS` (15 s at ~100 mA
≈ 0.42 mAh), **3× the cost of a successful cycle**. So failures are
disproportionately expensive and cluster exactly at the weakest nodes —
the degradation is nonlinear, and the worst-placed node drains fastest.

### Recommendation

**Adopt D**, with sample and report intervals as separate config values:

- `SAMPLE_INTERVAL_S` — how often to read the fence (start at 60)
- `REPORT_INTERVAL_S` — how often to transmit a routine heartbeat (start at 900)
- Transmit immediately, out of band, when a reading crosses a fault threshold
- Optionally adapt: after one marginal reading, drop to fast reporting until
  the state resolves

Two consequences worth naming:

1. **This is a firmware change**, in software plan Phase 2/4 territory — a
   single `SLEEP_INTERVAL_S` becomes two intervals plus threshold state held
   in RTC memory. Not a dashboard change.
2. **It makes `ts` mandatory rather than optional.** Once sampling and
   reporting decouple, a heartbeat carries readings taken minutes before it
   was sent, and receipt time is simply wrong for all of them. This is the
   same buffering problem Phase 4 already anticipated, arriving sooner — and
   it's exactly why `ts` and `seq` are reserved in the contract now. The
   reservation pays for itself here.

Backend-side, nothing structural changes: `report_interval_s` in the payload
already carries the cadence per node, and every window is defined as a
multiple of it rather than in absolute seconds.

**Open until hardware Phase 3:** the actual measured sleep current, wake
duration, and association time. If measured sleep current lands near the dev
board's 5–20 mA rather than the custom board's 20–50 µA, revisit this whole
section — the conclusion changes completely.

---

## Calibration

### What the sensor actually measures — and what it doesn't

The divider measures **the peak potential between the fence wire at the tap
point and the local sensing ground rod.** That's it. Calibration's only job is
mapping `adc_mv → volts at that tap point`.

This matters because most site-to-site variation is **signal, not calibration
error**, and conflating the two leads to trying to calibrate away the very
thing the system exists to detect:

| Varies by site | Is it a calibration problem? |
|---|---|
| Charger model / energy rating | **No.** Different input voltage — measure it |
| Fence length, wire gauge, number of strands | **No.** More load sags the charger; the sag *is* the reading |
| Vegetation contact, wet insulators, leakage | **No.** This is the fault the project exists to catch |
| Distance from charger | **No.** Voltage drop along the line is the Phase 7 localization signal |
| Divider resistor tolerance | **Yes** — dominant gain error |
| Peak-detector diode forward drop | **Yes** — dominant *offset* error, and temperature-dependent |
| RC droop between pulses | **Yes** — systematic underestimate |
| ESP32 ADC nonlinearity | **Yes** — ±2–3% even with factory calibration |
| Sensing ground rod soil resistance | **Barely.** See below |

A longer fence that sags to 5.5 kV should *report* 5.5 kV. If calibration were
re-tuned at each site to make every fence read ~7 kV, the system would be
tuned to hide exactly what it's supposed to surface.

**Soil resistance is a non-issue, contrary to intuition.** The ground rod's
resistance to earth (10 Ω–5 kΩ depending on soil) sits in series with a
1 GΩ + 270 kΩ divider. Even a terrible 5 kΩ rod is ~0.0005% of the chain —
lost in the noise of a 1% resistor. What *is* real, though second-order, is
earth potential gradient: during a pulse, return current through the soil
raises local earth potential, so a sensing rod near the charger's ground reads
against a shifted reference. Expect ~1–5% at typical separations, and prefer
siting the sensing rod well away from the charger ground — which the design
already requires for isolation reasons.

### The error budget is dominated by the diode, not the resistors

| Source | Type | Rough magnitude at 7 kV (~1.89 V at the ADC) |
|---|---|---|
| **Peak-detector diode Vf** | Offset | **0.3–0.5 V → 15–25%** |
| RC droop between pulses | Gain | 2–10%, depends on tuned RC |
| ADC nonlinearity | Both | 2–3% |
| Rsense tolerance (1%) | Gain | 1% |
| Divider stack (10× 1%) | Gain | 0.3–1% |

The diode drop dwarfs everything else, which has two consequences:

1. **Single-point calibration is not enough.** `kv = adc_mv × CAL_KV_PER_MV +
   CAL_KV_OFFSET` is the right *shape* — gain plus offset — but two unknowns
   need at least two points, and realistically 3–5 across 5–10 kV to confirm
   the response is actually linear rather than assumed to be.
2. **Calibration drifts with temperature.** Silicon Vf moves about
   −2 mV/°C. A −10 °C to +40 °C swing is ~100 mV at the ADC ≈ **0.37 kV
   apparent shift** — roughly 7% of the 5 kV alert threshold, in a system
   deployed outdoors year-round. A node calibrated in July will read
   differently in January with no physical change to the fence.

**Reserve `temp_c` in the contract now** (see the optional-fields table).
Backend-side temperature compensation is cheap once the data exists and
impossible to reconstruct retroactively — the same argument as `ts`. Note the
ESP32's internal sensor is self-heated and poor; a small external sensor in
the enclosure is the useful version.

### Where calibration is applied: the backend, not the node

This is the important architectural call, and the plan already accidentally
set it up correctly.

`adc_mv` is stored raw on every reading. That means **kV can be computed at
query time from a per-node calibration record in the database**, rather than
being baked in on-device. The consequences are large:

- **Recalibration is a database update.** No site visit, no reflash, no OTA.
- **It applies retroactively.** Fixing a bad constant repairs the entire
  history rather than leaving a discontinuity at the moment of the fix.
- **Calibration becomes versioned data**, with a validity window — so a
  reading from March uses March's constants and one from October uses
  October's, which is what makes seasonal recalibration coherent instead of
  destructive.

The node still computes its own `kv`, but only for one purpose: deciding
locally whether a reading is bad enough to justify an immediate out-of-band
transmit (see [Reporting cadence](#reporting-cadence-and-alert-latency)). The
on-device constant can be coarse. **The backend's value is authoritative for
display and alerting**; the payload's `kv` is advisory.

Storage: a `calibrations` table — `node_id`, `chip_id`, `valid_from`,
`valid_to`, `kv_per_mv`, `kv_offset`, `method`, `points` (the raw measurement
pairs), `notes`. `docs/calibration.md` remains the human-readable record;
this table is what the API actually reads.

The on-device constant should still be updatable without a reflash — NVS plus
a retained `fence/<node>/config` topic — because a node whose local threshold
is badly wrong will either spam immediate-transmits or fail to send them. But
that's a coarse safety setting, not the measurement path.

### Field procedure

The awkward part: you can't easily *dial* a fence to a known voltage. Three
practical sources of multi-point data, in preference order:

1. **Charger power settings**, if the energizer has them — cleanest way to get
   distinct levels at a fixed tap point.
2. **Natural variation along the line** — measure at 3–5 points with the
   handheld tester, pairing each reading with the node's `adc_mv`. Works
   without any charger control.
3. **Bench characterization first** (hardware Phase 1–2), so the sensing chain
   is understood before deployment and field work reduces to an offset trim.

Fit gain and offset, record the points (not just the derived constants — so
the fit can be redone later), and open a new `calibrations` row rather than
editing the old one.

**Re-verify calibration after:** a board swap, a divider or peak-detector
component change, cable length changes between tap and enclosure (it adds to
the peak-detector capacitance and shifts RC), and at least once across a
seasonal temperature swing until drift is characterized.

### Adding fence wire to an existing monitored fence

The direct answer: **nothing happens to calibration.** The ADC-to-volts
mapping is a property of the sensing chain, not of the fence. Adding 500 m of
wire changes the load, the charger sags, and the node correctly reports a
lower voltage. That's the system working.

What *does* need to happen is **re-baselining**, and it's easy to overlook:

- **Steady-state moves.** Say the fence drops 7.2 kV → 6.4 kV. Still above the
  5 kV threshold, so nothing alerts — but the margin just shrank by 40% and
  nobody was told.
- **The trend detector misfires.** The slow-decline tier watches for gradual
  drops indicating vegetation load. A step change from an intentional
  extension looks like a fault unless the baseline is reset.
- **Per-node thresholds may need revisiting.** They're already specified as
  per-node config; this is the moment that flexibility earns its place.
- **Multi-node relationships shift.** Extending the line *between* the charger
  and a node increases upstream load for every node beyond it, changing the
  comparative pattern the Phase 7 fault localization depends on.

So the plan needs an operator-annotated event timeline — a `fence_events`
table: `ts`, `node_id` (nullable for property-wide events), `kind`, `note`.
Kinds: wire added or removed, charger changed or serviced, vegetation cleared,
grounding modified, board swapped, recalibrated.

Cheap to build, and without it every deliberate physical change looks like an
anomaly forever afterward — the trend tier has no way to distinguish "someone
extended the fence on 12 March" from "something has been slowly going wrong
since 12 March." It also gives the charts annotation markers, which is the
single most useful thing you can overlay on a long time series.

This connects to the board-swap detection via `chip_id`: a swap can raise a
`fence_events` row automatically. Most other events need a human to record
them, which is a UI affordance worth having — a "log a change" button beats a
markdown file nobody updates.

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
   detection — is expressed in **multiples of the node's `report_interval_s`**,
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
  fields), wrong types, unknown node id, duplicate `(node, boot)`, node id
  failing the slug pattern, payload `node` disagreeing with the topic, and a
  changed `chip_id` on a known node.
- **Calibration application**, which is where a subtle error would silently
  corrupt every displayed number: a reading converts using the constants valid
  at its own `ts` rather than the newest ones; a backdated calibration row
  retroactively changes historical kV without mutating `readings`; a reading
  with no covering calibration comes back flagged provisional rather than
  guessing; overlapping validity windows are rejected at write time.

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
      routers/calibrations.py  Versioned constants; kV is computed here,
                               not read from the payload
      routers/events.py        fence_events read + write
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
        EventAnnotations.tsx fence_events markers overlaid on charts
        LogChangeDialog.tsx  Operator records a physical change
      pages/Dashboard.tsx    Node grid from D4 onward (one card, then many)
    Dockerfile
    package.json
```

`contract/` sits at the repo root, not under `dashboard/`, because the
firmware is the other party to it.

---

## Phased plan

### Phase D0 — Scaffolding
- [ ] `contract/fence-state.schema.json` + example payloads: the nine fields the firmware sends today as required, the reserved optional fields (`ts`, `seq`, `report_interval_s`, `sample_interval_s`, `chip_id`, `temp_c`) as permitted-but-absent, and the `node` slug pattern enforced
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
- [ ] `calibrations` table — versioned with `valid_from`/`valid_to`, storing the raw fit points, never mutated in place
- [ ] `fence_events` table — operator-annotated timeline of deliberate physical changes
- [ ] MQTT ingest subscriber (`fence/+/state`) in its own container, validating against the contract schema
- [ ] **Retained-message handling: retained → update last-known state, never insert a reading**
- [ ] Optional `ts` / `seq` / `report_interval_s` / `sample_interval_s` / `chip_id` / `temp_c` honored when present, sensible fallbacks when absent
- [ ] **Node identity checks**: `node` matches `^[a-z0-9][a-z0-9-]{1,30}$`; payload `node` agrees with the topic it arrived on (mismatch is a misconfiguration — reject loudly, don't store)
- [ ] **`chip_id` change on a known node raises a board-swap `fence_events` row automatically**; two chip ids interleaving under one `node` is a duplicate-config error, not an erratic fence — surface it as such
- [ ] Continuous aggregate (hourly + daily), compression policy, retention policy
- [ ] Ingest edge-case tests: malformed, missing field, extra field, unknown node, retained replay, bad node id, topic/payload mismatch, chip id change

**Exit:** manually publishing one MQTT message produces exactly one row; restarting the ingest container ten times produces **zero** additional rows; publishing under a changed `chip_id` produces a board-swap event rather than a silent overwrite.

### Phase D2 — Mock publisher
- [ ] Normal-operation scenario for one simulated node, live mode at accelerated cadence
- [ ] Remaining scenarios: slow-decline, low-voltage, fence-down, node-silent, battery-drain
- [ ] `--cadence realtime` option, covering both the current 600 s single-interval model and the recommended 60 s sample / 900 s report split
- [ ] **Report-by-exception simulation**: routine heartbeats *plus* immediate off-cadence transmits on threshold crossings, so irregular arrival spacing is exercised before real firmware produces it
- [ ] **Backfill mode**: N days of history at real spacing, written directly to the DB, including plausible `fence_events` rows to annotate against
- [ ] Emit `chip_id` and `temp_c`; a **board-swap scenario** (same `node`, new `chip_id`) and an **uncalibrated scenario** (no `calibrations` row covering the readings)
- [ ] Distinct client ids (`mock-pub-<n>`) and namespaced node ids (`mock-01`…)
- [ ] Contract test: every scenario's payload validates against `contract/fence-state.schema.json`

**Exit:** DB fills with plausible time series across every scenario on demand; `--backfill 90d` produces a season of realistically-spaced history in seconds; a fault transmit arriving between heartbeats is stored and charted correctly rather than treated as a gap.

### Phase D3 — API
- [ ] `GET /nodes`, `GET /nodes/{id}`, `GET /nodes/{id}/readings?since=&bucket=`
- [ ] **kV computed at query time** by joining each reading to the `calibrations` row whose validity window covers its `ts` — never read from the payload's advisory `kv`
- [ ] Readings with no covering calibration returned as **provisional**, carrying `adc_mv` and an explicit flag rather than a plausible-looking number
- [ ] `GET`/`POST /fence-events` — read for chart annotation, write for the "log a change" affordance
- [ ] `derive_status()` as a pure, I/O-free function per the thresholds table above
- [ ] Windows expressed as multiples of each node's `report_interval_s`, never absolute seconds or the sample interval
- [ ] Table-driven status tests across every scenario and boundary condition, including irregular arrival from off-cadence fault transmits
- [ ] Retroactive-recalibration test: inserting a backdated `calibrations` row changes historical kV **without touching `readings`**

**Exit:** API returns the correct derived status for each mock scenario, and the same status for a node whether it's running at 10 s or 900 s cadence; adding a calibration row retroactively corrects a node's entire history in one write.

### Phase D4 — Frontend MVP
- [ ] Node grid shell rendering a single `NodeCard`: status badge, current kV, voltage-over-time chart
- [ ] Chart ranges 24h/7d/30d/season with server-side bucketing, validated against backfilled data
- [ ] Provisional presentation for readings with no covering calibration, with `adc_mv` shown alongside kV
- [ ] `fence_events` rendered as chart annotations — the difference between "something broke here" and "we extended the fence here"

**Exit:** dashboard visibly reflects mock data changes within one polling interval; the 7d chart looks right against 90 days of backfill rather than twenty minutes of live mock; a backfilled fence extension reads as an annotated step, not an anomaly.

### Phase D5 — Multi-node & live updates
- [ ] Grid populated with all mock nodes, one card each
- [ ] Link-quality indicator (`failed_pub`, `wifi_ms`, `rssi`)
- [ ] **"Log a change" affordance** writing `fence_events` — a button beats a markdown file nobody updates
- [ ] Board swaps and calibration changes surfaced on the node detail timeline
- [ ] Polling-based live updates; WebSocket as stretch goal

**Exit:** 2+ mock nodes visible simultaneously; a silent/down node is visually distinct from the rest; a logged fence change appears on the chart without a deploy.

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
- [ ] Run the full stack against `--cadence realtime` mock nodes for ≥24 h; confirm charts, `silent` timeouts, and alerting all behave at real spacing — under both the single-interval and split sample/report models
- [ ] Per-node MQTT credentials + Mosquitto ACLs in place (Security section) — before nodes are flashed and deployed, not after. ACLs are written per `node`, so the location-slug naming has to be settled first
- [ ] Remote access path decided and working (Tailscale/WireGuard), since off-property visibility is the point of the project
- [ ] Agree the `node` slug per deployment point and record it — it becomes the topic, the ACL subject, the DB key, and the calibration key, so renaming later is expensive

**Cutover:**
- [ ] Point real firmware's `MQTT_HOST` config at this broker (dev, then wherever it's deployed)
- [ ] Confirm no client-id collision: real node is `fence-01`, mock publishers are `mock-pub-<n>`
- [ ] Leave the real node with no `calibrations` row until hardware Phase 4 completes; expect wrong-but-plausible kV and read `adc_mv` in the meantime
- [ ] Retire mock nodes (keep the publisher — it's the test fixture and the CI dependency); mock data is separable by the `mock-` prefix

**Re-tune against reality:**
- [ ] Confirm `report_interval_s` is correct per node and `silent` doesn't false-fire, including around off-cadence fault transmits
- [ ] Expect `silent` to be common, not exceptional — weak mesh Wi-Fi is a top-5 project risk and QoS 0 loses messages silently. Tune the threshold against observed loss rate rather than theory
- [ ] Feed observed `rssi` / `wifi_ms` / `failed_pub` into the antenna-vs-LoRa decision (software plan Connectivity Contingency)
- [ ] Feed measured sleep current and wake duration back into the [cadence decision](#reporting-cadence-and-alert-latency) — the estimates there are unvalidated until hardware Phase 3 closes, and a high sleep current invalidates the conclusion entirely
- [ ] After hardware Phase 4: insert the `calibrations` row (with `valid_from` backdated to the node's first reading, so existing history is corrected retroactively) and record the derivation in `docs/calibration.md`
- [ ] Log a `fence_events` row for the deployment itself, so the node's timeline starts with a known-good marker rather than an unexplained beginning
- [ ] Begin characterizing seasonal `temp_c` drift against calibration, so compensation can be added later from real data rather than the −2 mV/°C rule of thumb

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
