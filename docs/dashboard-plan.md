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
  committed transport (retained MQTT state messages; the topic key changes
  from a config'd name to the node's `node_id` — see
  [Identity](#identity-nodes-locations-and-assignments)).

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

One constraint to keep visible while deferring it: the project's whole point is
checking status *remotely*, so any on-property deployment eventually needs a
remote-access answer such as Tailscale/WireGuard. That is intentionally tracked
as a later operational decision in [Security](#security), not as a blocker for
the mock-backed dashboard or the first real-node cutover.

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
                                                     │  nodes / readings  │
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

Topic: **`fence/<node_id>/state`**, retained, QoS 0 — keyed on the node's own
stable identifier, not on a human-assigned fence name. See
[Identity](#identity-nodes-locations-and-assignments) for why. Payload (see
[firmware/README.md](../firmware/README.md) for the producer's side):

```json
{
  "node_id": "a4c1385f2b10",
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
| `node_id` | string | — | `^[a-z0-9][a-z0-9-]{1,62}$` (topic-safe, opaque) | The node itself, self-assigned and stable for its lifetime. **How** it's derived is a firmware detail the backend does not depend on — see below | Primary key of the *node*. Also the topic segment it arrived on (the two must agree). The fence location it maps to is resolved in the backend, never sent by the node |
| `fw` | string | — | semver, e.g. `0.1.0` | `FW_VERSION` compile-time constant | Shown on the node detail; lets you spot a node that missed an OTA rollout. Stored per reading, so a bad release is attributable after the fact |
| `kv` | float | kV | 0–12 | `adc_mv × CAL_KV_PER_MV + CAL_KV_OFFSET`, computed on-node | **Advisory only.** The node uses it to decide whether a reading warrants an immediate transmit; the backend recomputes kV from `adc_mv` and is authoritative for display and alerting — see [Calibration](#calibration) |
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

#### Identity: nodes, locations, and assignments

Worth settling before nodes 2–5 exist, because it is painful to change once
history has accumulated under the wrong scheme.

There are two distinct identities, and the original design conflated them:

- **Node** — *which physical ESP32 is this?*
- **Location** — *which point on the fence line is this?*

They have different lifecycles, and no single identifier can serve both:

- Swap a failed board at the north gate → node changes, location doesn't.
  That location's history must continue uninterrupted.
- Move a working board from the north gate to the creek crossing → node is
  the same, location changed. Its history must **not** continue as though it
  were the same measurement point.

Pick either one as *the* id and one of those two routine operations silently
corrupts the record. So the model carries all three: two identities plus the
time-varying relationship between them.

**The node names itself; the backend names the fence.**

| | Value | Assigned by | Lives in |
|---|---|---|---|
| `node_id` | Opaque, topic-safe string, stable for the node's lifetime | The node itself, with no configuration step | The node |
| `location_id` | Human-readable slug: `north-gate`, `creek-crossing` | You | The database |
| assignment | `(node_id → location_id)` over a validity window | You, via the dashboard | The database |

A node knows only its own `node_id`, which it determines at runtime.
**No fence name appears in `config.h` and none appears in the payload** — a
node reports who *it* is, never where it thinks it is. Moving hardware between
fence points is a UI action against `node_assignments` — no reflash, no config
edit, no site visit beyond physically moving the box.

This is the same versioned-mapping pattern used for
[calibration](#calibration): readings store the immutable fact (*this node
measured this value at this time*), and the interpretation (*which fence that
was*) resolves at query time from the assignment valid at the reading's
timestamp. Both directions stay correct — the location's timeline continues
across a board swap, and a relocated board's old readings stay attributed to
where they were actually taken.

**What this buys, beyond what a name-in-config scheme could:**

1. **Relocation without reflashing.** The point of the exercise.
2. **ACLs pre-provision at flash time.** A node's id is readable before it's
   deployed and never changes — so the per-node Mosquitto credential and its
   topic ACL are written once and survive every move. With location-based
   topics, every relocation is also an ACL edit.
3. **Identity cannot be mistyped or duplicated.** Because it is self-assigned
   rather than typed into a per-node config file, the duplicate-config failure
   mode disappears rather than needing detection.
4. **The MQTT client-id collision disappears too** — client id derives from
   `node_id`, unique by construction.
5. **Zero-config provisioning.** Flash identical firmware to every board.
   A new node publishing under an unrecognized `node_id` appears in the
   dashboard as **unassigned**, and you bind it to a location in the UI. That
   is the whole per-node identity setup.

**The cost, stated honestly:** topics are opaque. `fence/a4c1385f2b10/state`
is worse than `fence/north-gate/state` at a `mosquitto_sub` prompt or in a
hand-read ACL file. Mitigations: the firmware prints its `node_id` on the
serial console at boot, `esptool.py read_mac` reads it over USB without
flashing, and the ACL file is generated rather than hand-written. For a
five-node fleet you look it up once. It remains a real daily-ergonomics tax
and is the reason this decision deserved to be made explicitly.

##### How the firmware populates `node_id` today — an implementation detail

Everything above holds for any node that can name itself stably. The backend
treats `node_id` as **opaque**: a topic-safe string it stores, joins on, and
never parses. That's deliberate. A LoRa-bridged node, a different MCU, or a
second-source board would all slot in without a contract change, and nothing
downstream has to care.

What the current ESP32 firmware actually does, recorded here so the two
documents agree — not as something the API may assume:

- It derives the id from the **factory-burned 48-bit eFuse base MAC**,
  formatted as 12 lowercase hex characters. The classic ESP32 (WROOM-32) has
  no 128-bit unique ID — that's an ESP32-S2/S3/C3 feature — so the MAC is the
  available hardware identity.
- It's readable **before Wi-Fi comes up**, which matters: a wake cycle that
  never associates still knows who it is.
- All 48 bits are used. Espressif OUI prefixes repeat across a production
  batch, so truncating would discard exactly the bytes carrying the entropy.

> **Implementation trap.** Arduino's `ESP.getEfuseMac()` returns a `uint64_t`
> with bytes in **reverse order** relative to the string `WiFi.macAddress()`
> prints. Formatted naively, the resulting id won't match the MAC in the
> router's DHCP table or on `esptool.py read_mac`. `firmware/src/main.cpp`
> sidesteps this by calling `esp_read_mac()` and formatting the bytes
> directly. Pin the expected format in `contract/fence-state.schema.json` with
> a worked example and verify it on first hardware — cheap to get right once,
> expensive to discover after history has accumulated under two formats.

The **validation** the backend performs is therefore on shape, not source: the
topic-safe pattern in the field table, plus agreement between the payload
`node_id` and the topic segment. It does not check length-12, does not check
hex, and does not attempt to recognise a MAC.

**Ingest verifies payload `node_id` against the topic segment.** They come
from the same source on the node, so they can't disagree by misconfiguration,
but
a broken bridge or a misrouting gateway can make them disagree, and that's
worth catching loudly rather than storing.

**Calibration keys on the assignment, not on either id alone.** The constant
is a physical property of a specific board's ADC *and* the specific divider
and peak detector it's wired to — and the diode dominates the error budget.
The hand-wired HV chain stays at the fence while the board is swappable, so a
board that moves is paired with a *different* chain and must be recalibrated.
`(node_id, location_id, valid_from)` is therefore the key, which is exactly
the assignment. See [Calibration](#calibration).

**What `node_id` cannot tell you.** It identifies the node's compute module
only.
Because the PCB plan sockets the DevKit, the module is swappable independently
of the peak detector, the divider, and the enclosure — so a diode replacement
or a rewired divider invalidates calibration with no detectable identity
change. Automatic detection covers one of the three things that invalidate a
calibration; the other two must be logged manually as `fence_events`. That's
a limitation of any hardware id, not an argument against having one.

#### Reserved optional fields — add to the contract now

The firmware doesn't send these yet and doesn't have to. Reserving them costs
nothing today and avoids a breaking change to a contract that ingest, dedup,
status derivation, and the charts all sit on:

| Field | Type | Meaning | Ingest behavior when absent |
|---|---|---|---|
| `ts` | int (epoch seconds, UTC) | When the node *took* the reading | Fall back to receipt time |
| `seq` | int | Monotonic per-node reading counter | Best-effort dedup only until sent; `boot` helps detect resets but is not unique |
| `report_interval_s` | int | How often this node sends a routine heartbeat | Fall back to configured per-node default |
| `sample_interval_s` | int | How often this node reads the fence | Assume equal to `report_interval_s` |
| `temp_c` | float | Enclosure temperature | No temperature compensation of the peak-detector diode drift; see [Calibration](#calibration) |

(`node_id` is **required**, not reserved — it's the topic key and the node's
primary identity. See [Identity](#identity-nodes-locations-and-assignments).)

`ts` matters more than it looks. Software plan Phase 4 already commits to
buffering readings in RTC memory across failed transmits and flushing on
reconnect — the moment that lands, receipt time is simply wrong for every
buffered row, and a whole flush arrives stamped within the same second.
Reserving the field now means that work becomes additive. (Sending it requires
NTP on wake, which costs awake-time against the hardware Phase 3 energy
budget — a real reason for the firmware to defer it, and a good reason for the
backend not to depend on it.)

`seq` should become the actual dedup key once firmware can send it. Until then,
`boot` is useful telemetry, not a safe uniqueness guarantee: it resets on power
loss, so `(node_id, boot)` can collide with an older row from the same node.
Before `seq` exists, duplicate suppression should be conservative and
best-effort rather than silently dropping legitimate post-reset readings.

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

**The split cadence eventually needs a richer payload shape.** If a node
samples every minute but reports every 15 minutes, a heartbeat cannot remain
ambiguous about what it represents: the latest sample only, min/max over the
window, or a batch of timestamped samples. The dashboard can start with today's
single-reading payload, but the contract should record this as the next
breaking semantic question before buffered history or report-by-exception
firmware lands.

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
`msg.retain`). A message with that flag set updates durable `node_state`
last-known display state and **never inserts a `readings` row.** Live messages
arrive with the flag clear and are inserted normally. Retained receipt time
must also never clear `silent`; the original reading time, if known, is what
matters.

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

Three tables carry identity, and the split follows directly from
[Identity](#identity-nodes-locations-and-assignments): store immutable facts,
resolve interpretation at query time.

`nodes` table: `node_id` (PK), `first_seen`, `fw_version`,
`report_interval_s`, `sample_interval_s`. One row per physical node, created
automatically the first time an unrecognized `node_id` publishes.

`node_state` table: `node_id` (PK), `last_payload`, `payload_ts`,
`received_at`, `was_retained`, `ingest_seen_at`. Durable last-known state for
display and health checks. This is separate from `readings` so retained MQTT
replays can refresh "what did the broker last know?" without fabricating a new
historical reading or clearing a `silent` status.

`locations` table: `location_id` (PK — human-readable slug,
`^[a-z0-9][a-z0-9-]{1,30}$`), `label`, `notes`, `created_at`. One row per
monitored point on the fence line. **Created by a human in the dashboard, never
by ingest** — a location is a deliberate decision about the property, not
something a stray MQTT message should be able to invent.

`node_assignments` table: `node_id`, `location_id`, `valid_from`, `valid_to`
(null = current). The time-varying mapping between the two. Constraints worth
enforcing in the schema rather than hoping for: **no overlapping windows for a
given `node_id`** (one board can't be in two places) and **none for a given
`location_id`** (two boards at one point would silently interleave readings).
Moving hardware closes one row and opens another; that's the entire operation.

`calibrations` table: `node_id`, `location_id`, `valid_from`, `valid_to`,
`kv_per_mv`, `kv_offset`, `method`, `points` (JSON — the raw measurement pairs,
kept so the fit can be redone), `notes`. Keyed on the *assignment*, because the
constant depends on both the board's ADC and the divider chain it's wired to.
Versioned rather than mutated, so a reading is always converted with the
constants valid when it was taken. This is what makes `kv` a **query-time
computation over stored `adc_mv`** rather than a value frozen on the node —
see [Calibration](#calibration). `docs/calibration.md` stays the
human-readable record; this table is what the API reads.

`fence_events` table: `ts`, `location_id` (nullable — some events are
property-wide), `node_id` (nullable — some are node-specific), `kind`,
`note`. Operator-annotated timeline of deliberate physical changes: wire added
or removed, charger changed, vegetation cleared, grounding modified, board
swapped, recalibrated. Without it, every intentional change is
indistinguishable from a developing fault for the rest of that location's life.

`readings` hypertable (Timescale): **`node_id`**, `ts`, `adc_mv`, `batt_v`,
`rssi`, `boot`, `failed_pub`, `wifi_ms`, `fw`, `seq`, `temp_c`, plus the
advisory `kv` the node computed. One row per received *live* message (retained messages
excluded — see the data contract above).

**Readings key on the node, not the location**, which is the crux of the
whole scheme. "This board measured 1872 mV at this instant" is an immutable
fact. "That was the north gate" and "that means 6.93 kV" are both
interpretations, resolved at read time by joining to the assignment and the
calibration whose windows cover the reading's `ts`. Get this backwards —
stamping `location_id` onto rows at ingest — and relocating a board either
rewrites history or forks it.

**No `last_seen` column.** It would be a denormalized copy of
`max(readings.ts)` needing an update on every insert, and the two will diverge
the first time a write path is missed. At 263k rows/year, derive it. Same
reasoning applies to `fw_version`: it arrives on every message and changes
whenever OTA (software plan Phase 4) ships, so it's an upsert-on-change
convenience field, not authoritative history — the authoritative record is the
`fw` value on each reading.

**Two states that look alike and aren't:**

- A **node with no current assignment** is *unassigned* — freshly flashed,
  or pulled from service. It's reporting fine; it just isn't attributed to a
  fence yet. This is the provisioning inbox, not a fault.
- A **location with no current assignment** is *unmonitored* — a fence point
  nobody is watching. Distinct from `silent`, which means an assigned node
  has stopped reporting. Both deserve surfacing; only one is an alert.

Two specifics worth writing down before they bite:

- **`ts` is `timestamptz`, not `timestamp`.** Naive timestamps work perfectly
  until the deployment box and the development machine disagree about
  timezone, at which point every historical chart silently shifts. Store UTC,
  convert at the edge.
- **`create_hypertable()` is a raw-SQL Alembic operation**, and it must run in
  the same migration that creates the table, before any rows exist —
  converting a populated table is a separate and more annoying path.

Unknown-node policy: a message from an unrecognized `node_id` auto-creates a
`nodes` row in the **unassigned** state. That is the provisioning inbox, not
an error — flash a board, power it on, and it shows up waiting to be bound to
a location. Because `node_id` is self-assigned rather than configured, the old
failure mode of a typo'd id creating a phantom node is gone entirely.
Locations are never auto-created.

#### Retention and rollups

Software plan Phase 5 requires **at least a season of readings** so
vegetation-growth trends are visible. That requirement has to be implemented
somewhere, and this is the somewhere — otherwise Timescale is being run for no
reason (see the Decision section's honest accounting of why it's here):

- **Continuous aggregate** bucketing raw `readings` to hourly and daily
  min/max/avg of `adc_mv` and `batt_v`, per node. Calibrated kV is then
  resolved at query time from the assignment/calibration windows covering the
  bucket. Do not materialize calibrated `kv` into a long-lived aggregate unless
  the plan also defines how a backdated calibration invalidates and refreshes
  those buckets; otherwise the "recalibration repairs history" promise stops
  being true.
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
| `silent` | assigned node has sent no reading for > 2–3× **its own** `report_interval_s` (never the sample interval, never a global constant — see data contract) |
| `unmonitored` | location has no currently assigned node — not a fault, but not being watched either |

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

So D5.5 adds a scheduler and a `status_transitions` table (`location_id`, `ts`,
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
a "flaky link" indicator on the location card — rising `failed_pub`, `wifi_ms`
trending up, `rssi` weak — turns a real engineering decision into an
observation instead of a field trip. This is separate from fence status and
shouldn't be folded into the status badge.

### API surface (initial)

- `GET /locations` — all monitored fence points, each with its currently assigned node, latest reading + derived status
- `GET /locations/{id}` — location detail, current and past assignments, latest reading
- `GET /locations/{id}/readings?since=&bucket=` — time series for the chart, stitched across whatever nodes were assigned during the range.
- `GET /nodes` — every known board, including **unassigned** ones awaiting provisioning
- `POST /assignments` / `PATCH /assignments/{id}` — bind a node to a location, or close an assignment when hardware moves. This is how hardware gets relocated; there is no firmware step
  **`bucket` (server-side downsampling) is part of the initial design, not an
  optimization to add later.** A season of 5-node history is ~1.3M points and
  no chart library should receive that; `time_bucket` collapses it to whatever
  the range needs, which is the concrete thing Timescale is here for.
- `GET /healthz` — **three-way**, not a boolean: `{api, broker, db}` plus
  ingest heartbeat age. "API is up" is close to worthless on its own; what
  matters operationally is whether ingest still holds a broker connection and
  when it last wrote state. Because ingest runs as its own container, it must
  publish a heartbeat somewhere the API can read — simplest is an `ingest_state`
  row in Postgres updated on broker connect/disconnect and each successful
  message. Without that shared heartbeat, the API cannot honestly report ingest
  health.
- Live updates: polling to start (simple, adequate for a 10–15 min firmware
  duty cycle); a `/ws/live` WebSocket is a stretch goal, not required for MVP.

**Frontend types are generated from OpenAPI, not hand-mirrored.** FastAPI
emits an OpenAPI schema for free; `openapi-typescript` (or similar) turns it
into `src/api/schema.ts` as a build step. Hand-maintained duplicates of
backend models drift silently and the drift shows up as a runtime `undefined`
in the browser. This is a five-minute setup in D0 that removes an entire
class of bug.

### Frontend

Per-location card: status badge (color-coded per table above), current kV as a
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

The firmware uses `node_id` as its MQTT **client id**. Two clients presenting
the same id make the broker evict one on each connect, producing an endless
reconnect loop that is genuinely confusing to diagnose. Deriving the client id
from `node_id` removes that failure mode for real hardware, but mock publishers
still have to pick ids that can't collide with it. Since D6 has mock and real
nodes live at the same time:

- Mock nodes use a reserved `node_id` prefix — `mock-0001` through
  `mock-0005`. Real hardware is then unambiguously identifiable and mock rows
  stay trivially separable in the database afterward. Treating `node_id` as an
  opaque string rather than a MAC is what makes this a one-line convention
  instead of hunting for an address range real silicon never uses.
- Mock client ids derive from those node ids exactly as real firmware derives
  its own, so the collision behavior under test is the real one.

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
query time from a versioned per-assignment calibration record in the database**, rather than
being baked in on the node. The consequences are large:

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
on-node constant can be coarse. **The backend's value is authoritative for
display and alerting**; the payload's `kv` is advisory.

Storage: a `calibrations` table — `node_id`, `location_id`, `valid_from`,
`valid_to`, `kv_per_mv`, `kv_offset`, `method`, `points` (the raw measurement
pairs), `notes`. `docs/calibration.md` remains the human-readable record;
this table is what the API actually reads.

The node's local constant should still be updatable without a reflash — NVS plus
a retained `fence/<node_id>/config` topic — because a node whose local threshold
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
table: `ts`, `location_id` (nullable for property-wide events), `node_id`
(nullable), `kind`, `note`.
Kinds: wire added or removed, charger changed or serviced, vegetation cleared,
grounding modified, board swapped, recalibrated.

Cheap to build, and without it every deliberate physical change looks like an
anomaly forever afterward — the trend tier has no way to distinguish "someone
extended the fence on 12 March" from "something has been slowly going wrong
since 12 March." It also gives the charts annotation markers, which is the
single most useful thing you can overlay on a long time series.

This connects to the board-swap detection via `node_id`: a swap can raise a
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

Security is intentionally **not** a blocker for the dashboard build or the
first real-node cutover. The initial implementation can run on a trusted LAN
with anonymous Mosquitto and no dashboard auth while the hardware and data path
are being proven. The items below are still worth documenting now because they
shape the eventual hardening path, but they are "resolve before depending on
this unattended over the long term," not D0-D6 prerequisites.

### Broker authentication and ACLs — resolve later

`config.example.h` currently ships `MQTT_USER ""` with "leave empty for
anonymous," and an unauthenticated broker means anyone on the ranch network
can publish to a node's state topic claiming a healthy 6.9 kV. For a system
whose entire purpose is reporting that the fence is *not* fine, a spoofable
"everything's fine" is the worst available failure mode — strictly worse than
the dashboard being down, which is at least visibly broken.

Later hardening path:

- Per-node MQTT credentials, not one shared account.
- Mosquitto ACL restricting each node to publishing only
  `fence/<its-node_id>/state`; ingest gets a separate read-only account
  subscribed to `fence/+/state`.
- `allow_anonymous false` once credentials exist.

**Chip-keyed topics make this materially easier**, which is a real secondary
benefit of the [identity decision](#identity-nodes-locations-and-assignments).
The ACL subject is the node's factory-fixed MAC, so:

- Credentials and ACL entries are **generated at flash time from a known,
  permanent id** — before the board ever leaves the bench.
- They **never change when hardware moves.** Under location-keyed topics,
  every relocation would also be an ACL edit and a broker reload; here the
  assignment change is purely a database row.
- The ACL file is generated from the `nodes` table rather than hand-written,
  which also removes the readability objection to opaque topic strings.

One implementation wrinkle to resolve when hardening: unknown-node
auto-creation and locked-down ACLs do not coexist by magic. A node cannot
first-publish to a broker that already rejects unknown credentials. The
provisioning flow should become: read `node_id` on the bench, pre-create the
`nodes` row, generate credentials/ACLs, flash secrets, then deploy. Until
that flow exists, anonymous LAN MQTT keeps bring-up simple.

### API and frontend

The stated point of the project is checking status *remotely*, which means
this may be exposed beyond the LAN eventually. Auth is deferred with
deployment, but the preferred posture is recorded now:

- **Local/LAN development:** no auth, as it stands today.
- **Remote access, first choice:** no public exposure at all — reach the
  dashboard over Tailscale/WireGuard and let the VPN be the authentication
  boundary.
- **If ever publicly exposed:** TLS terminated at a reverse proxy, plus a
  single-user session/token auth on the API. Not built now; noted so the API
  isn't designed in a way that makes it painful.

MQTT over TLS on the node side is explicitly *not* planned for the initial
system — it's real overhead on a battery-powered ESP32 for a LAN-local hop, and
broker ACLs are the later hardening step that addresses the practical spoofing
risk.

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

The dead-man's switch has one dependency security does not: outbound internet.
If a ranch-network outage is part of the failure being detected, the external
check will fire, which is good. If the stack is deployed somewhere without
reliable outbound access, this needs a different observer before anyone treats
"no alert" as proof that all is well.

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
  fields), wrong types, duplicate `seq` when present, reused `boot` after a
  power reset, a `node_id` failing the 12-hex-char pattern, and a payload
  `node_id` disagreeing with the topic segment.
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
    aclfile                  Later hardening: per-node publish restrictions
                             (see Security)
  backend/
    app/
      main.py                FastAPI app + router mounts
      ingest.py              MQTT subscriber — its OWN container entrypoint,
                             not a background task inside the API process
      status.py              derive_status(): pure function, no I/O
      models.py              SQLAlchemy models (Timescale hypertable)
      db.py
      routers/locations.py
      routers/nodes.py       Includes the unassigned-node inbox
      routers/assignments.py   Bind / move / retire hardware
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
        LocationCard.tsx    One monitored fence point
        NodeInbox.tsx     Unassigned boards awaiting a location
        AssignmentDialog.tsx Bind or move hardware
        VoltageChart.tsx
        StatusBadge.tsx
        LinkQuality.tsx      failed_pub / wifi_ms / rssi — feeds the
                             antenna-vs-LoRa decision
        EventAnnotations.tsx fence_events markers overlaid on charts
        LogChangeDialog.tsx  Operator records a physical change
      pages/Dashboard.tsx    Location grid from D4 onward (one card, then many)
    Dockerfile
    package.json
```

`contract/` sits at the repo root, not under `dashboard/`, because the
firmware is the other party to it.

---

## Phased plan

### Phase D0 — Scaffolding
- [x] `contract/fence-state.schema.json` + example payloads: `node_id` plus the eight non-identity fields the firmware sends today as required (`fw`, `kv`, `adc_mv`, `batt_v`, `rssi`, `boot`, `failed_pub`, `wifi_ms`); reserved optional fields (`ts`, `seq`, `report_interval_s`, `sample_interval_s`, `temp_c`) permitted-but-absent. `node_id` is specified as an **opaque topic-safe string**, not as a MAC or a fixed-width hex value
- [x] `docker-compose.yml` wiring Mosquitto, Postgres+Timescale, empty FastAPI app, empty ingest container, empty React app
- [x] Networking between services confirmed
- [x] `GET /healthz` returning api/broker/db plus ingest heartbeat status
- [x] OpenAPI → TypeScript client generation wired as a build step
- [x] CI workflow: `firmware/lint.sh`, backend lint/test, frontend typecheck/test, contract validation

> **Mosquitto 2.x will not work out of the box, and this is where the
> afternoon goes.** It defaults to `allow_anonymous false` with a
> localhost-only listener, so a minimal `mosquitto.conf` in Docker silently
> refuses every connection from other containers. The dev config needs an
> explicit `listener 1883 0.0.0.0`. Anonymous access is acceptable for initial
> LAN development and real-node bring-up; per-node credentials are tracked in
> Security as later hardening, not a D0 or D6 gate.

**Exit:** `docker compose up` brings up all six services; `/healthz` reports api/db/broker plus fresh ingest heartbeat green; React dev server reachable; CI green on an empty stack.

### Phase D1 — Data contract & storage
- [x] `nodes` / `node_state` / `readings` schema; `create_hypertable` in the initial migration; `ts` as `timestamptz`
- [x] `calibrations` table — versioned with `valid_from`/`valid_to`, storing the raw fit points, never mutated in place
- [x] `fence_events` table — operator-annotated timeline of deliberate physical changes
- [x] `ingest_state` heartbeat row so `/healthz` can report whether the subscriber is connected and recently active
- [x] MQTT ingest subscriber (`fence/+/state`) in its own container, validating against the contract schema
- [x] **Retained-message handling: retained → update durable `node_state`, never insert a reading, never clear `silent` from retained receipt time**
- [x] Optional `ts` / `seq` / `report_interval_s` / `sample_interval_s` / `temp_c` honored when present, sensible fallbacks when absent
- [x] `nodes` / `locations` / `node_assignments` tables, with **non-overlapping validity windows enforced in the schema** for both `node_id` and `location_id`
- [x] **Identity checks at ingest**: `node_id` matches the topic-safe opaque pattern `^[a-z0-9][a-z0-9-]{1,62}$` and agrees with the topic segment; unrecognized nodes auto-create as *unassigned*; locations never auto-create. Validate **shape only** — no length-12 check, no hex check, no attempt to recognise a MAC
- [x] Assignment change raises a `fence_events` row automatically — board swaps and relocations both land on the timeline
- [x] Continuous aggregate (hourly + daily) over raw `adc_mv`/`batt_v`, compression policy, retention policy
- [x] Ingest edge-case tests: malformed, missing field, extra field, retained replay, malformed `node_id`, topic/payload mismatch, reading from an unassigned node, overlapping assignment windows rejected, post-reset `boot` reuse not treated as a hard duplicate

**Exit:** manually publishing one MQTT message produces exactly one row; restarting the ingest container ten times produces **zero** additional rows; a message from an unknown `node_id` lands in the unassigned inbox rather than erroring or inventing a location.

### Phase D2 — Mock publisher
- [ ] Normal-operation scenario for one simulated node, live mode at accelerated cadence
- [ ] Remaining scenarios: slow-decline, low-voltage, fence-down, node-silent, battery-drain
- [ ] `--cadence realtime` option, covering both the current 600 s single-interval model and the recommended 60 s sample / 900 s report split
- [ ] **Report-by-exception simulation**: routine heartbeats *plus* immediate off-cadence transmits on threshold crossings, so irregular arrival spacing is exercised before real firmware produces it
- [ ] Explicit mock behavior for the future split-cadence payload question: latest-only, summary, or timestamped batch. Pick one before firmware buffers multiple samples per report
- [ ] **Backfill mode**: N days of history at real spacing, written directly to the DB, including plausible `fence_events` rows to annotate against
- [ ] Emit `temp_c`; a **board-swap scenario** (assignment closed and reopened at one location with a different `node_id`), a **relocation scenario** (one `node_id` moved between locations), and an **uncalibrated scenario** (no `calibrations` row covering the readings)
- [ ] Reserved mock `node_id` prefix (`mock-0001`…) so mock and real hardware never collide and mock rows stay separable — trivially available now that `node_id` is an opaque string rather than a MAC
- [ ] Contract test: every scenario's payload validates against `contract/fence-state.schema.json`

**Exit:** DB fills with plausible time series across every scenario on demand; `--backfill 90d` produces a season of realistically-spaced history in seconds; a fault transmit arriving between heartbeats is stored and charted correctly rather than treated as a gap.

### Phase D3 — API
- [ ] `GET /locations`, `GET /locations/{id}`, `GET /locations/{id}/readings?since=&bucket=`, `GET /nodes`, assignment write endpoints
- [ ] **kV and location both resolved at query time** by joining each reading to the `node_assignments` and `calibrations` rows whose validity windows cover its `ts` — never read from the payload's advisory `kv`, never from a location stamped at ingest
- [ ] Readings with no covering calibration returned as **provisional**, carrying `adc_mv` and an explicit flag rather than a plausible-looking number
- [ ] `GET`/`POST /fence-events` — read for chart annotation, write for the "log a change" affordance
- [ ] `derive_status()` as a pure, I/O-free function per the thresholds table above
- [ ] Windows expressed as multiples of each node's `report_interval_s`, never absolute seconds or the sample interval
- [ ] Table-driven status tests across every scenario and boundary condition, including irregular arrival from off-cadence fault transmits
- [ ] Alert/status tests cover the report-by-exception ownership question: backend debounce still sees enough low samples, or firmware sends an explicit local fault state that changes the backend rule
- [ ] Retroactive-recalibration test: inserting a backdated `calibrations` row changes historical kV **without touching `readings`**

**Exit:** API returns the correct derived status for each mock scenario, and the same status for a node whether it's running at 10 s or 900 s cadence; adding a calibration row retroactively corrects history in one write; relocating a node in the assignment table leaves its prior readings attributed to the prior location.

### Phase D4 — Frontend MVP
- [ ] Location grid shell rendering a single `LocationCard`: status badge, current kV, voltage-over-time chart
- [ ] Chart ranges 24h/7d/30d/season with server-side bucketing, validated against backfilled data
- [ ] Provisional presentation for readings with no covering calibration, with `adc_mv` shown alongside kV
- [ ] `fence_events` rendered as chart annotations — the difference between "something broke here" and "we extended the fence here"

**Exit:** dashboard visibly reflects mock data changes within one polling interval; the 7d chart looks right against 90 days of backfill rather than twenty minutes of live mock; a backfilled fence extension reads as an annotated step, not an anomaly.

### Phase D5 — Multi-node & live updates
- [ ] Grid populated with all monitored locations, one card each
- [ ] **Unassigned-node inbox** and the assignment dialog — the provisioning and relocation flow, and the reason no name lives in firmware
- [ ] Link-quality indicator (`failed_pub`, `wifi_ms`, `rssi`)
- [ ] **"Log a change" affordance** writing `fence_events` — a button beats a markdown file nobody updates
- [ ] Board swaps and calibration changes surfaced on the node detail timeline
- [ ] Polling-based live updates; WebSocket as stretch goal

**Exit:** 2+ monitored locations visible simultaneously; a silent/down location is visually distinct from the rest; an unassigned node appears in the inbox rather than as a broken card; a logged fence change appears on the chart without a deploy.

### Phase D5.5 — Minimal push alerting

Pulled forward from software plan Phase 6 deliberately. Push notification is
the project's *stated primary requirement*, not a nicety, and the custom-stack
decision means it's the one thing that doesn't arrive for free. Left at the
end of a distant phase, the realistic outcome is a good-looking dashboard that
never actually pages anyone — the failure mode this project exists to prevent.
Once D3's status derivation exists, this is small.

- [ ] `status_transitions` table (`location_id`, `ts`, `from_status`, `to_status`, `notified_at`)
- [ ] Scheduler evaluating `derive_status()` per location on an interval — the observer a stateless API doesn't provide
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
- [ ] Record each board's `node_id` at flash time (serial console, or `esptool.py read_mac` over USB) — it's the provisioning key and the future ACL subject
- [ ] Optional later-hardening dry run: pre-create `nodes` rows and generate per-node MQTT credentials/ACLs from `node_id`, but do not block cutover on this while running on a trusted LAN
- [ ] Remote access path noted if needed (Tailscale/WireGuard preferred), but not required for the first local cutover

**Cutover:**
- [ ] Point real firmware's `MQTT_HOST` config at this broker (dev, then wherever it's deployed)
- [ ] Confirm no client-id collision: real hardware uses its eFuse MAC, mock nodes use the reserved `fe00…` range
- [ ] Leave the real node with no `calibrations` row until hardware Phase 4 completes; expect wrong-but-plausible kV and read `adc_mv` in the meantime
- [ ] Retire mock nodes (keep the publisher — it's the test fixture and the CI dependency); mock data is separable by the `mock-` prefix

**Re-tune against reality:**
- [ ] Confirm `report_interval_s` is correct per node and `silent` doesn't false-fire, including around off-cadence fault transmits
- [ ] Expect `silent` to be common, not exceptional — weak mesh Wi-Fi is a top-5 project risk and QoS 0 loses messages silently. Tune the threshold against observed loss rate rather than theory
- [ ] Feed observed `rssi` / `wifi_ms` / `failed_pub` into the antenna-vs-LoRa decision (software plan Connectivity Contingency)
- [ ] Feed measured sleep current and wake duration back into the [cadence decision](#reporting-cadence-and-alert-latency) — the estimates there are unvalidated until hardware Phase 3 closes, and a high sleep current invalidates the conclusion entirely
- [ ] After hardware Phase 4: insert the `calibrations` row (with `valid_from` backdated to the node's first reading, so existing history is corrected retroactively) and record the derivation in `docs/calibration.md`
- [ ] Bind the real node to its location in the dashboard and confirm the assignment writes a `fence_events` row, so the location's timeline starts with a known-good marker
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
- **Public internet exposure** — remote access is deferred; VPN access is the
  preferred later path, while TLS termination and API session auth are
  designed-for but not built (see Security).
- **Broker hardening** — per-node MQTT credentials, generated ACLs, and
  `allow_anonymous false` are documented in Security as later work. Initial
  implementation can use anonymous MQTT on a trusted LAN so this does not slow
  down the hardware/data-path bring-up.
- **MQTT over TLS on the node side** — rejected on power/complexity grounds
  for a LAN-local hop; broker ACLs are the likely later hardening step.
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
5. **How much do opaque topics actually hurt in daily use?** The chip-keyed
   topic decision trades `mosquitto_sub` readability for relocation without
   reflashing. If it grates in practice, a read-only convenience bridge
   republishing to `fence-by-location/<slug>/state` is a backend-only
   addition — the authoritative path stays chip-keyed.
6. **Is an assembly-level id worth having**, distinct from `node_id`? The MAC
   identifies the ESP32 module, not the enclosure, divider, or peak detector.
   For 2–5 nodes an asset-tag sticker plus `fence_events` entries is probably
   enough; a `hardware_units` table would be over-engineering until it isn't.
