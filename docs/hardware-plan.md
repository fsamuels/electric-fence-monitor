# Hardware Development Plan

Phased build plan for the fence monitor hardware. Each phase has an explicit goal and exit criteria so it's clear when to move on. The overall design (divider values, peak detector topology, power system) is described in the [README](../README.md); this document covers *how to get from parts to a field-deployed unit*.

Guiding principle: **retire the highest-risk unknowns first.** The peak detector RC tuning and the HV divider behavior are the riskiest parts of the whole project, so they come before any firmware, power, or enclosure work.

---

## Phase 0 — Parts Sourcing & Bench Setup

**Goal:** everything on the bench needed to prototype the sensing chain.

- [ ] Order ESP32 dev boards — at least one standard board and one with a u.FL external antenna connector (so the weak-signal mitigation can be tested without a second order cycle)
- [ ] Order divider components: 10× 100 MΩ 1% HV resistors, 270 kΩ 1% Rsense, spares of each
- [ ] Order peak detector components: 1N4148 diodes, assortment of non-electrolytic capacitors (0.01–0.1 µF), assortment of bleed resistors (1–3 MΩ range plus values above and below for tuning), 3.3 V and 3.6 V Zeners
- [ ] Order power components: 18650 cells + holder, TP4056 module, 5–6 W solar panel, buck/boost regulator
- [ ] Order enclosure candidates: IP65/67 box (~4×4×2 in), cable glands, desiccant packets
- [ ] Confirm access to the handheld fence tester (needed for calibration in Phase 4)
- [ ] Decide the bench HV test source: variable HV supply if available, otherwise plan controlled tests against the actual charger (see Phase 1 safety notes)

**Exit criteria:** all parts on hand; a bench test plan for HV exists that doesn't involve improvising around a live fence.

---

## Phase 1 — Breadboard the Sensing Chain (highest risk first)

**Goal:** a working divider + peak detector that produces a stable, ADC-readable voltage proportional to pulse peak.

- [ ] Build the divider on a spaced-out prototyping layout — **not** a compact breadboard; maintain arc-safe spacing between the 100 MΩ resistors from the very first test
- [ ] Install the first 3.3 V Zener clamp at the Rsense node before any HV is applied
- [ ] Verify divider ratio at low voltage first (e.g., a few hundred volts if a variable supply is available) before going anywhere near 7–10 kV
- [ ] Build the peak detector stage (1N4148 → cap → bleed resistor → second Zener clamp)
- [ ] Apply pulsed HV (charger under controlled conditions, or pulse generator + supply) and observe the peak detector output on a scope or multimeter
- [ ] Confirm the output is a readable quasi-DC level tracking pulse peaks, and that it decays enough between pulses that a voltage *drop* on the fence shows up within a few pulse cycles

**Safety for this phase:** de-energize before touching anything; single-hand technique when probing; the dedicated ground rod arrangement should be replicated at the bench (isolated return, not shared with other bench equipment grounds).

**Exit criteria:** peak detector output visibly tracks pulse peak; both Zener clamps confirmed limiting correctly; no arcing or leakage observed across the divider at full charger voltage.

---

## Phase 2 — Peak Detector RC Tuning (empirical)

**Goal:** an RC time constant that gives fresh readings every pulse cycle without droop distorting the reading. This is flagged as the project's highest technical risk — budget real time here.

- [ ] Starting point: C = 0.1 µF, R in the 1–3 MΩ range (RC ≈ 100–300 ms)
- [ ] Sweep bleed resistor values; for each, record: peak level held, droop between pulses, and how many pulse cycles a simulated fence-voltage drop takes to show at the output
- [ ] Verify behavior at the *low* end of the range (~5 kV equivalent) — this is where alert accuracy matters most
- [ ] Check temperature sensitivity informally (component behavior on a cold vs. warm bench) since deployment is outdoors year-round
- [ ] **Document actual measured values vs. calculated** in this repo (add `docs/rc-tuning-results.md`) — these numbers feed directly into the firmware sampling window

**Exit criteria:** a chosen R/C pair with recorded justification; measured settling/decay behavior documented so the firmware team (i.e., future you) knows the sampling window that makes sense.

---

## Phase 3 — Power System

**Goal:** the ESP32 runs indefinitely on battery + solar with the planned duty cycle.

- [ ] Assemble 18650 + TP4056 + solar panel + buck/boost chain on the bench
- [ ] Measure actual ESP32 current draw in each state: deep sleep, wake + ADC read, Wi-Fi transmit (numbers vary a lot by board — measure, don't trust datasheets)
- [ ] Compute the energy budget: reads-per-hour × (wake+read+transmit energy) + sleep floor, vs. worst-case winter solar harvest
- [ ] Decide single vs. parallel 18650 based on the measured budget and desired dark-day reserve (target: multi-week runtime with zero solar)
- [ ] Verify TP4056 charging behavior from the actual panel (panel voltage sag under load, charge cutoff)
- [ ] Brown-out behavior: confirm what the ESP32 does as the battery dies — it should fail silent, not spam garbage readings (coordinates with the software plan's battery telemetry)

**Exit criteria:** measured energy budget closes with margin; battery configuration chosen; charge and brown-out behavior verified.

---

## Phase 4 — Integrated Prototype & Calibration

**Goal:** one full unit — sensing + ESP32 + power — reading real fence voltage accurately.

- [ ] Move the tuned sensing chain from breadboard to protoboard/perfboard with proper HV spacing preserved
- [ ] Integrate with the ESP32 running the Phase-2 firmware from the [software plan](software-plan.md) (raw ADC readings are enough at this stage)
- [ ] Connect to the actual fence with the dedicated ground rod
- [ ] **Calibrate against the handheld tester:** record ADC raw values against tester kV readings at multiple points (ideally by varying fence load — e.g., known leak to ground — to get readings across the 5–10 kV range, not just one operating point)
- [ ] Derive the per-node calibration constant/curve; record it in `docs/calibration.md` keyed by node ID
- [ ] Sanity-check repeatability: same conditions on different days should give the same kV

**Exit criteria:** monitor's reported kV agrees with the handheld tester within an accepted tolerance (suggest ±5%) across the operating range; calibration procedure written down so it's repeatable for future nodes.

---

## Phase 5 — Enclosure & Weatherproofing

**Goal:** the unit survives outdoors.

- [ ] Lay out components in the IP65/67 box; keep the HV divider section physically separated from logic/power, and treat the divider tap as permanent exposed HV wiring in the internal layout
- [ ] Install cable glands: fence lead, ground rod lead, solar panel lead, external antenna (if used)
- [ ] Add desiccant; decide whether a pressure-equalization vent (e.g., Gore vent) is needed to fight condensation cycling
- [ ] Mount the solar panel (angle, sun exposure, cable strain relief)
- [ ] Water test: hose spray-down and a rain cycle before trusting it in the field
- [ ] Check enclosure internal temperature range in direct sun — Li-ion charging has temperature limits

**Exit criteria:** unit passes spray-down with no ingress; battery charges within safe temperature bounds in realistic sun.

---

## Phase 6 — Field Deployment & Horse-Proofing

**Goal:** the unit lives on the fence line, and reports over real-world Wi-Fi.

- [ ] Site survey: measure actual Wi-Fi RSSI at each candidate deployment point (this feeds the connectivity decision in the software plan — external antenna vs. LoRa fallback)
- [ ] First deployment at the *weakest* known Wi-Fi point — if it works there, it works everywhere
- [ ] Design and install horse-proof mounting: out of reach or armored; no dangling leads a horse can catch; fence lead and ground lead protected (conduit or routing along the post)
- [ ] Drive the dedicated ground rod at the deployment site
- [ ] Observe for a multi-week shakedown period: reading stability, battery/solar behavior through weather, enclosure condition
- [ ] Revisit and finalize the BOM with actual sourced part numbers (`hardware/BOM.md`)

**Exit criteria:** ≥2 weeks unattended operation with continuous reporting, stable calibration, healthy battery, and no physical damage.

---

## Scaling Notes (nodes 2–5)

Decisions above that exist specifically so later nodes are cheap to add:

- Per-node calibration is a documented, repeatable procedure (Phase 4), not a one-off.
- The BOM is finalized with real part numbers (Phase 6) so subsequent builds are order-and-assemble.
- Node ID lives in firmware config (see software plan), so hardware builds are identical across nodes.
- If the fleet grows beyond ~5 nodes, revisit the build-vs-buy decision — multi-node commercial systems (Tru-Test) amortize differently at scale.

## Risk Register (hardware)

| Risk | Phase where retired | Mitigation |
|---|---|---|
| Peak detector RC tuning wrong | Phase 2 | Empirical sweep, documented results; firmware multi-sample/max is tolerant of imprecision |
| Divider arcing/leakage at 10 kV | Phase 1 | Spaced layout from first test; low-voltage ratio verification first |
| Winter power budget doesn't close | Phase 3 | Measure real draw; parallel 18650 option; longer sleep interval as firmware fallback |
| Condensation/ingress | Phase 5 | Desiccant, gland selection, spray test, optional vent |
| Horse damage | Phase 6 | Mounting design treated as a deliverable, not an afterthought |
| Calibration drift over time | Phase 6 shakedown | Repeatability checks; periodic re-check against handheld tester |
