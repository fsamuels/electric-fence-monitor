# Software Development Plan

Phased plan for the firmware and backend. Hardware design and build sequence live in the [README](../README.md) and [hardware plan](hardware-plan.md); the two plans intersect at Phase 4 of the hardware plan (integrated prototype) and at the field site survey.

Two decisions are deliberately kept open and handled as explicit milestones rather than assumptions:

1. **Firmware toolchain** — Arduino framework via PlatformIO is the working recommendation; final decision when development begins (see Milestone A).
2. **Backend** — Home Assistant vs. custom stack (see Milestone B).

Everything else is written to be valid under any outcome of those two decisions.

---

## Milestone A — Firmware Toolchain Decision

**Working recommendation: Arduino framework on PlatformIO.** Documented alternatives below; make the final call when firmware development actually starts, informed by whatever the backend decision (Milestone B) lands on.

| Option | Pros | Cons | Choose if… |
|---|---|---|---|
| **Arduino via PlatformIO** *(recommended)* | Mature ESP32 core; rich libraries for Wi-Fi, MQTT (PubSubClient/AsyncMqtt), deep sleep, OTA; reproducible builds committed to this repo; huge community for exactly this class of project | C++; some low-level ADC/power features wrapped less precisely than ESP-IDF | You want the safest default with the least friction |
| **ESP-IDF** | Full control over ADC calibration API, RTC/deep-sleep wake stubs, power management; Espressif's first-class framework | More boilerplate; steeper curve; slower iteration for a one-person project | Power budget turns out tight in hardware Phase 3 and every µA matters |
| **ESPHome** | Almost no code — YAML config; native Home Assistant integration incl. OTA and dashboards for free | Custom multi-sample/max logic and per-node calibration require escape-hatch C++ lambdas anyway; awkward if backend ends up *not* Home Assistant | Milestone B picks Home Assistant **and** the custom ADC logic proves simple |
| **MicroPython** | Fastest iteration; easy remote REPL debugging | Higher sleep/wake overhead; less precise timing; FW updates clunkier | Prototyping speed matters more than power draw (could be used for Phase 1 spikes even if not the final choice) |

**Decision criteria, in order:** (1) works with the backend chosen in Milestone B, (2) supports the multi-sample/max ADC strategy and per-node calibration cleanly, (3) deep-sleep power draw, (4) development speed.

A reasonable path: prototype Phase 1–2 firmware in Arduino/PlatformIO (or even MicroPython for the ADC spike), and only revisit if a blocker appears.

---

## Milestone B — Backend Decision (Home Assistant vs. custom)

Both candidates satisfy the confirmed requirement: **dashboard to check anytime AND active push alerts** on threshold drop or outage.

| Criterion | Home Assistant | Custom (MQTT broker + DB + dashboard) |
|---|---|---|
| Threshold alerting | Built-in (automations, notify integrations → phone push) | Build it (Node-RED, Grafana alerts, or hand-rolled) |
| "Node went silent" detection | Built-in (entity unavailable / last-seen automations) | Build it (last-seen watchdog) |
| Historical logging/graphs | Built-in recorder + history graphs | InfluxDB/SQLite + Grafana, more setup but more control |
| Multi-node dashboard | Free — one card per node | Full layout control |
| Effort | Low (config, not code) | Days-to-weeks of side project |
| Maintenance | HA upgrades occasionally break things | You own every piece |
| Prerequisite | An always-on box on the ranch network running HA | An always-on box running the broker + stack (same box requirement) |

**Decision: custom stack — Mosquitto + Postgres/TimescaleDB + FastAPI + React/TypeScript** (not Home Assistant, not the Grafana/InfluxDB trio). Full rationale and architecture in [docs/dashboard-plan.md](dashboard-plan.md), decided ahead of hardware so the dashboard could be built now against mock MQTT data using the firmware's real payload contract. Summary of why:

- HA's automation/card model would constrain later custom views (e.g. the Phase 7 fault-localization overlay). This is a trade, not a free win: the custom stack is six containers to HA's one and has to build the push alerting HA gives away. Chosen for control and transferable skills, accepting more operational surface — not because it's lighter.
- Grafana + InfluxDB (the originally planned custom fallback) was reconsidered in favor of a hand-rolled API + Postgres + React app — deliberately chosen for more generally transferable software engineering skills over self-hosted-ops-specific tooling.
- MQTT/Mosquitto stays the transport either way — it's already the firmware's committed contract.
- Deployment target (Raspberry Pi vs. cloud) is intentionally left open; Docker Compose keeps the stack portable to either.

~~Original evaluation plan (superseded by the above):~~

- ~~Confirm whether Home Assistant is already running (or acceptable to run) on the ranch network~~
- ~~Stand up a throwaway HA + Mosquitto instance; time how long a threshold alert + dashboard card takes~~
- ~~Only if HA proves inadequate: spec the custom stack~~

**Note:** either way, the firmware speaks **MQTT** (see message contract below). Home Assistant consumes MQTT natively via discovery, and any custom stack starts from a broker — so firmware work can begin before Milestone B is settled.

---

## Firmware Phases

### Phase 1 — ADC Spike (bench, fake or real sensing chain)

**Goal:** prove the reading strategy against the peak detector.

- [ ] PlatformIO project skeleton in `firmware/`, committed with pinned platform/library versions
- [ ] Implement multi-sample/max: sample the ADC continuously for a 2–3 s window (covering at least one full pulse interval at 1–1.5 s), report the max
- [ ] Use ADC calibration/attenuation APIs so raw counts map linearly to pin voltage (11 dB attenuation for the 0–3.3 V range; ESP32 ADC is notoriously nonlinear at the extremes — characterize it)
- [ ] Validate against the hardware Phase-2 breadboard: reported max should track the scope-observed peak detector level
- [ ] Tune the window length against the *measured* RC decay from hardware Phase 2 (`docs/rc-tuning-results.md`)

**Exit criteria:** reported reading is stable pulse-to-pulse and tracks known input changes.

### Phase 2 — Duty Cycle & Telemetry

**Goal:** the wake → read → transmit → sleep loop, with everything the backend will need.

- [ ] Deep sleep cycle with **two** configurable intervals, not one: `SAMPLE_INTERVAL_S` (how often to read the fence, start at 60) and `REPORT_INTERVAL_S` (how often to send a routine heartbeat, start at 900), plus immediate out-of-band transmit when a reading crosses a fault threshold. Bringing the radio up costs ~4× what sampling costs, so sampling often and talking rarely gives ~1 min fault detection at roughly a third the energy of simply reporting every minute — full analysis and numbers in [dashboard-plan.md](dashboard-plan.md#reporting-cadence-and-alert-latency). Requires threshold state held in RTC memory across sleep
  - Note this makes the `ts` payload field effectively mandatory: once sampling and reporting decouple, a heartbeat carries readings taken minutes earlier and receipt time is wrong for all of them
  - **Measure deep-sleep current before tuning any of this.** A stock ESP32 dev board draws 5–20 mA asleep, which exceeds the wake-cost of every cadence option and caps runtime near 10 days regardless. Cadence is only a real decision once the custom Logic & Power board brings that to tens of µA
- [ ] Wi-Fi connect with a bounded timeout — a node in a weak-signal spot must not burn its battery retrying; on failure, log locally (RTC memory counter) and go back to sleep
- [ ] Telemetry payload: `chip_id` (eFuse MAC), firmware version, raw ADC max, computed kV, battery voltage, Wi-Fi RSSI, boot/wake counter
  - Battery voltage and RSSI are not optional extras: RSSI feeds the antenna-vs-LoRa decision, battery feeds the "dead node vs. dead fence" distinction
- [ ] Per-device config (Wi-Fi credentials, coarse local calibration constant, sample/report intervals) separated from code — build flags or NVS. **Device identity is deliberately *not* in this list:** it comes from the ESP32 MAC-derived `chip_id`, so onboarding devices 2–5 needs no per-device identity config at all
- [ ] **Identity comes from the hardware, the name comes from the backend.** The node derives `chip_id` from the ESP32 MAC at runtime, publishes to `fence/<chip_id>/state`, and uses `chip_id` for its MQTT client id. Which *fence* a board is watching is a versioned assignment held in the database, so relocating hardware is a dashboard action with no reflash — full rationale in [dashboard-plan.md](dashboard-plan.md#identity-devices-locations-and-assignments)
  - The classic ESP32 has no 128-bit unique id (that's S2/S3/C3), so the factory 48-bit base MAC *is* the hardware identity. It's readable before Wi-Fi comes up, so a wake that never associates still knows who it is. Store all 48 bits — Espressif OUI prefixes repeat within a batch, so truncation discards the bytes that carry the entropy
  - **Watch the byte order.** `ESP.getEfuseMac()` returns a `uint64_t` byte-reversed relative to what `WiFi.macAddress()` prints; formatted naively it won't match `esptool.py read_mac` or the router's DHCP table. Pin the format in `contract/fence-state.schema.json` and verify on first hardware
  - Print `chip_id` on the serial console at boot — it's the provisioning key and eventual ACL subject
- [ ] Publish via MQTT: `fence/<chip_id>/state` as a JSON document, with MQTT retain so the dashboard shows the last reading immediately

**Exit criteria:** bench unit runs the full cycle unattended for 24 h; measured awake-time matches the hardware energy budget assumptions.

### Phase 3 — Calibration Support

**Goal:** readings in real kV, per node.

**Calibration is applied in the backend, not on the node.** `adc_mv` is stored raw on every reading, so kV is computed at query time from a versioned per-node calibration record — which means recalibration is a database update that fixes all history retroactively, with no site visit and no reflash. The on-device constant survives only so the node can decide locally whether a reading is bad enough to warrant an immediate out-of-band transmit; it can be coarse. Full rationale and error budget in [dashboard-plan.md](dashboard-plan.md#calibration).

- [ ] Calibration mode (e.g., held pin at boot, or MQTT command): rapid readings streamed while someone at the fence compares against the handheld tester
- [ ] **Multi-point fit, not single-point.** `kv = adc_mv × gain + offset` has two unknowns, and the peak-detector diode drop (0.3–0.5 V, i.e. 15–25% at 7 kV) makes the offset term dominant — far larger than resistor tolerance. Collect 3–5 points across 5–10 kV to solve both and to confirm the response is linear rather than assume it. Sources of distinct levels, in preference order: charger power settings, natural voltage variation measured at several points along the line, or bench characterization done first in hardware Phase 1–2
- [ ] Store the on-device constant in **NVS, updatable over MQTT** — not a compile-time `#define`. A constant that requires a reflash to change contradicts Phase 4's premise of a node you never walk to
- [ ] **Log enclosure temperature (`temp_c` in the payload).** Diode Vf drifts about −2 mV/°C, so a −10 °C to +40 °C seasonal swing is ~0.37 kV of apparent shift — roughly 7% of the 5 kV alert threshold, with no physical change to the fence. Backend-side compensation is cheap once the data exists and impossible to reconstruct retroactively, so log it from the start even if compensation comes later
- [ ] Document each calibration in `docs/calibration.md` keyed on **`(chip_id, location_id, date)`** — i.e. on the *assignment*, since the constant depends on both the board's ADC and the divider chain it's wired to. A board swap **and** a relocation each invalidate it: a moved board is paired with a different chain. Record the raw points as well as the derived constants, and treat readings as provisional until re-verified

**Exit criteria:** hardware Phase-4 exit criteria met (agreement with handheld tester within tolerance across 5–10 kV).

### Phase 4 — Hardening & OTA

**Goal:** a node you never have to walk to.

- [ ] OTA firmware updates (ArduinoOTA or HTTP-pull on wake) — walking the fence line to reflash defeats the whole purpose
- [ ] Watchdog + safe failure modes: bad reading (clamped/zero when fence known-on) flagged rather than silently reported; brown-out handled quietly
- [ ] Backoff strategy for extended Wi-Fi outages (lengthen sleep, don't drain the battery fighting a dead AP)
- [ ] Optional: buffer a few readings in RTC memory across failed transmits, flush on reconnect, so short outages don't leave gaps in history

**Exit criteria:** node survives simulated AP outage, brown-out, and an OTA update cycle on the bench.

---

## Backend & Alerting Phases

### Phase 5 — Backend Bring-Up

Underway ahead of hardware, against mock MQTT data — see
[docs/dashboard-plan.md](dashboard-plan.md) for the detailed phased plan
(D0–D6). Summary:

- [ ] Stand up Mosquitto + Postgres/TimescaleDB + FastAPI + React/TypeScript via Docker Compose (dashboard-plan Phases D0–D1)
- [ ] Mock publisher exercises every node scenario (normal, low-voltage, fence-down, silent, battery-drain, board-swap, uncalibrated) plus report-by-exception arrival, so the dashboard is fully testable before real hardware exists (Phase D2)
- [ ] Ingest `fence/<chip_id>/state`; dashboard showing per-location: current kV, voltage-over-time chart, battery, RSSI, last-seen, link quality, derived status (Phases D3–D5)
- [ ] **Calibration applied at query time from a versioned `calibrations` table** (Phases D1/D3) — since `adc_mv` is stored raw, recalibration is a database update that repairs all history retroactively, with no site visit and no reflash. This is what removes calibration from the list of reasons to walk the fence line
- [ ] **`fence_events` timeline** (Phases D1/D4/D5): operator-annotated record of deliberate physical changes — wire added, charger serviced, vegetation cleared, board swapped, recalibrated — rendered as chart annotations. Without it the trend tier cannot distinguish an intentional change from a developing fault, and every fence extension reads as an anomaly for the rest of the node's life
- [ ] **Device/location/assignment model** (Phase D1): readings key on `chip_id`; which fence that was, and what constants apply, both resolve at query time from versioned assignment and calibration windows. Relocating hardware is a database write, not a reflash — and an unrecognized board arrives in an unassigned inbox rather than inventing a location
- [ ] Historical retention target: at least a season of readings, so vegetation-growth trends are visible — implemented as Timescale continuous aggregates + retention/compression policies (Phase D1)
- [ ] Security hardening is documented but deferred: per-device MQTT credentials, broker ACLs, and remote-access auth are later work, not implementation blockers while the stack runs on a trusted LAN (dashboard-plan Security section)
- [ ] Minimal push alerting + external dead-man's switch (Phase D5.5) — pulled ahead of Phase 6 because it's the primary requirement, not a nicety
- [ ] Cut over from mock publisher to real firmware once hardware Phase 4/6 and firmware Phase 2 land (Phase D6) — deploy target (Pi vs. cloud) decided at that point, see dashboard-plan.md. Note the cutover is a re-tuning exercise, not a config change: real cadence is ~60× slower than mock, so every time-based threshold and chart range is re-validated there

### Phase 6 — Alert Logic

Built on the established operating range (typical ~7 kV, minimum acceptable ~5 kV, peak 10 kV). Thresholds should be config, not code — they may differ per node or season.

- [ ] **Low-voltage alert:** kV below ~5 kV. Require N consecutive low readings (e.g., 2–3) before alerting, to avoid one-off noise paging anyone at 6 a.m.
- [ ] **Fence-down alert:** kV below a floor (e.g., <1 kV) or reading pinned at zero — distinct, higher-urgency alert
- [ ] **Node-silent alert:** no report for > 2–3× the sleep interval. Distinguish causes where possible: last known battery voltage low → probably node power; battery was healthy → probably Wi-Fi or node failure. Either way the fence state is *unknown*, which is itself alert-worthy
- [ ] **Trend/warning tier (secondary goal):** slow decline over days (vegetation load growing) as a low-urgency notification before it ever crosses the hard threshold. Must be baselined against the `fence_events` timeline — a deliberate change (wire added, charger serviced, vegetation cleared) produces a step that otherwise reads as a developing fault forever afterward
- [ ] Push delivery: ntfy/Pushover from the custom stack. **A minimal single-channel version of this ships in dashboard-plan Phase D5.5**, ahead of this phase — the custom-stack decision means push alerting is the one thing that doesn't arrive for free, and deferring all of it here risks ending up with a good dashboard that never pages anyone. What remains for Phase 6 is the richer behavior below
- [ ] Alert acknowledgment/quiet hours as needed once real alerts start flowing
- [ ] **Dead-man's switch** (also D5.5): a dead broker, dead ingest, or dead host is indistinguishable from a quiet healthy fence — nothing inside the stack can detect its own total failure, so an external service must alert when the stack stops checking in. Hard prerequisite before the alert drills below are meaningful

### Phase 7 — Multi-Node & Fault Localization (after node 1 proves out)

- [ ] Onboard nodes 2+ via config only (validates the Phase-2 config separation)
- [ ] Comparative view: voltage at each point along the fence line on one chart — a drop that appears at node N but not node N-1 brackets the fault location between them
- [ ] Revisit dashboard layout for a fleet rather than a single gauge

---

## Connectivity Contingency (weak Wi-Fi)

Sequenced mitigation, cheapest first — triggered by the site survey in hardware Phase 6:

1. **Measure first:** RSSI at actual deployment points, logged by the node itself (it's in the telemetry payload).
2. **External antenna:** u.FL ESP32 variant + directional or higher-gain antenna through a cable gland.
3. **Mesh extension:** an additional mesh node/repeater closer to the fence point may be cheaper than redesigning the device.
4. **LoRa fallback:** ESP32+LoRa module per node plus a LoRa gateway on the ranch network. Note this changes the firmware transport layer and adds a gateway — a real scope expansion, hence last resort. The MQTT message contract survives unchanged (gateway bridges LoRa → MQTT).

## Testing Strategy

- **Static analysis:** `firmware/lint.sh` (cppcheck + cpplint) runs without the ESP32 toolchain and should stay clean on every change; clang-tidy / `pio check` are available on toolchain-equipped machines for deeper passes. Wired into CI in dashboard-plan Phase D0 — it currently runs only by hand.
- **Contract test:** the MQTT payload schema is shared between firmware and backend and currently exists as hand-copied JSON in several files. `contract/fence-state.schema.json` becomes the single source of truth, validated on both sides in CI (dashboard-plan Testing section).
- **Bench rig:** a second ESP32 (or signal generator) producing fake "peak detector" voltages lets firmware development proceed without HV on the desk.
- **Soak tests:** every firmware phase ends with a ≥24 h unattended bench run before moving on.
- **Alert drills:** before trusting the system, deliberately induce each alert condition (drop the divider input, kill the node's power, kill its Wi-Fi) and confirm the right alert fires with the right urgency.
- **Field shakedown:** shared with hardware Phase 6 — ≥2 weeks unattended at the weakest-signal site.

## Repo Structure (as software work begins)

```
contract/            Authoritative MQTT payload schema shared by firmware and backend
firmware/            PlatformIO project (src/, platformio.ini)
  src/config.example.h  Tracked template — copy to config.h per node
  src/config.h       Real per-device config with credentials (gitignored)
dashboard/           Mosquitto + FastAPI + Postgres/TimescaleDB + React/TS, per Milestone B decision — see dashboard-plan.md
docs/
  software-plan.md   This file
  dashboard-plan.md  Dashboard architecture + phased plan (mock data, ahead of hardware)
  calibration.md     Per-node calibration records
  rc-tuning-results.md  Measured RC values from hardware Phase 2
```
