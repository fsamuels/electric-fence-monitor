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

**Evaluation plan:**

- [ ] Confirm whether Home Assistant is already running (or acceptable to run) on the ranch network — this is the dominant factor
- [ ] Stand up a throwaway HA + Mosquitto instance; point a bench ESP32 publishing fake voltage at it; build one threshold alert + one dashboard card; time how long it takes
- [ ] Only if HA proves inadequate or unacceptable: spec the custom stack (Mosquitto + InfluxDB + Grafana is the conventional trio)
- [ ] Record the decision and rationale in this file

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

- [ ] Deep sleep cycle with configurable interval (starting point: every 10–15 min; alert latency vs. power trade-off to be tuned against the hardware Phase-3 energy budget)
- [ ] Wi-Fi connect with a bounded timeout — a node in a weak-signal spot must not burn its battery retrying; on failure, log locally (RTC memory counter) and go back to sleep
- [ ] Telemetry payload: node ID, firmware version, raw ADC max, computed kV, battery voltage, Wi-Fi RSSI, boot/wake counter
  - Battery voltage and RSSI are not optional extras: RSSI feeds the antenna-vs-LoRa decision, battery feeds the "dead node vs. dead fence" distinction
- [ ] Per-node config (node ID, Wi-Fi credentials, calibration constant, sleep interval) separated from code — build flags or NVS — so nodes 2–5 are a config change, not a code fork
- [ ] Publish via MQTT: `fence/<node-id>/state` as a JSON document, with MQTT retain so the dashboard shows the last reading immediately

**Exit criteria:** bench unit runs the full cycle unattended for 24 h; measured awake-time matches the hardware energy budget assumptions.

### Phase 3 — Calibration Support

**Goal:** readings in real kV, per node.

- [ ] Calibration mode (e.g., held pin at boot, or MQTT command): rapid readings streamed while someone at the fence compares against the handheld tester
- [ ] Store the derived constant/curve per node (NVS or config); apply it to compute kV on-device so the payload carries both raw and calibrated values
- [ ] Document each node's calibration in `docs/calibration.md` (node ID, date, points measured, constant derived)

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

- [ ] Execute Milestone B evaluation; stand up the chosen stack on an always-on box on the ranch network
- [ ] Ingest `fence/<node-id>/state`; dashboard showing per-node: current kV, sparkline/history, battery, RSSI, last-seen
- [ ] Historical retention target: at least a season of readings, so vegetation-growth trends are visible

### Phase 6 — Alert Logic

Built on the established operating range (typical ~7 kV, minimum acceptable ~5 kV, peak 10 kV). Thresholds should be config, not code — they may differ per node or season.

- [ ] **Low-voltage alert:** kV below ~5 kV. Require N consecutive low readings (e.g., 2–3) before alerting, to avoid one-off noise paging anyone at 6 a.m.
- [ ] **Fence-down alert:** kV below a floor (e.g., <1 kV) or reading pinned at zero — distinct, higher-urgency alert
- [ ] **Node-silent alert:** no report for > 2–3× the sleep interval. Distinguish causes where possible: last known battery voltage low → probably node power; battery was healthy → probably Wi-Fi or node failure. Either way the fence state is *unknown*, which is itself alert-worthy
- [ ] **Trend/warning tier (secondary goal):** slow decline over days (vegetation load growing) as a low-urgency notification before it ever crosses the hard threshold
- [ ] Push delivery: HA companion app notifications if Milestone B → Home Assistant; otherwise ntfy/Pushover/Telegram from the custom stack
- [ ] Alert acknowledgment/quiet hours as needed once real alerts start flowing

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

- **Bench rig:** a second ESP32 (or signal generator) producing fake "peak detector" voltages lets firmware development proceed without HV on the desk.
- **Soak tests:** every firmware phase ends with a ≥24 h unattended bench run before moving on.
- **Alert drills:** before trusting the system, deliberately induce each alert condition (drop the divider input, kill the node's power, kill its Wi-Fi) and confirm the right alert fires with the right urgency.
- **Field shakedown:** shared with hardware Phase 6 — ≥2 weeks unattended at the weakest-signal site.

## Repo Structure (as software work begins)

```
firmware/            PlatformIO project (src/, platformio.ini, per-node config)
backend/             HA config snippets or custom-stack compose files, per Milestone B
docs/
  software-plan.md   This file
  calibration.md     Per-node calibration records
  rc-tuning-results.md  Measured RC values from hardware Phase 2
```
