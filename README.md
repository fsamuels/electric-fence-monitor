# Wi-Fi Electric Fence Voltage Monitor

Remote, battery/solar-powered electric fence voltage monitor for Reign Cloud Ranch. An ESP32-based node reads fence voltage through a high-voltage resistive divider and peak detector, reports over the property's existing mesh Wi-Fi, and raises alerts when voltage drops below a safe threshold or the fence goes down entirely — so faults, shorts, and vegetation contact can be caught without walking the fence line daily.

**Status:** design phase. No hardware built or firmware written yet. See the development plans:

- [Hardware development plan](docs/hardware-plan.md)
- [Software development plan](docs/software-plan.md)

---

## Problem Statement

- No electrical power is available at fence testing/monitoring locations — the only energy source at those points is the fence itself.
- Existing mesh Wi-Fi covers most of the property but is weak at some locations.
- **Primary goal:** know fence voltage remotely and get alerted when it drops below a safe threshold or drops out entirely, without manual walk-and-check.
- **Secondary goal:** use voltage drop as an indicator of *where* a short or fault likely is (e.g., vegetation load, break in the line).

## Operating Parameters

| Parameter | Value |
|---|---|
| Charger rated peak (worst case) | up to 10,000 V |
| Typical operating voltage | ~7,000 V |
| Minimum acceptable ("still fine") | ~5,000 V |
| Pulse shape | ~µs rise, decaying over ~1–10 ms |
| Pulse interval | roughly every 1–1.5 s |

## Deployment Scale

Start with **one prototype unit**, but design for a small number of additional nodes (2–5) to follow without rework: node identification, per-node calibration constants, and a dashboard that can show multiple monitoring points are in scope from the start. Multiple nodes along the fence line also serve the secondary goal — comparing voltage across points helps localize a fault.

---

## Hardware

Full design rationale and build sequence in [docs/hardware-plan.md](docs/hardware-plan.md).

### Compute Platform: ESP32

Chosen over Raspberry Pi — built-in Wi-Fi, much lower power draw, deep sleep between readings, and no OS overhead for what is a periodic read-and-transmit workload. If weak mesh signal is confirmed at deployment sites, a variant with a u.FL external antenna connector is the cheap first mitigation before considering LoRa.

### Power: Battery + Solar

**Decision: do not attempt to power the device from the fence itself.** Fence chargers deliver pulsed (not continuous) power, and harvesting would require a second HV energy-extraction circuit stacked on top of the sensing circuit — added complexity and risk for a source that is weakest exactly when the device most needs to work (during a fault/low-voltage condition).

Design:

- 18650 Li-ion cell (or two in parallel)
- Small 5–6 W solar panel
- TP4056 charge controller
- Buck/boost regulator to 3.3 V
- ESP32 deep-sleep duty cycle (wake → read → transmit → sleep) targeted for multi-week to indefinite runtime on solar top-up

### High-Voltage Sensing: Resistive Divider

**Divider chain:**

- High side: 10× 100 MΩ resistors in series = 1 GΩ total. Splitting the voltage stress puts ~1,000 V across each resistor at 10 kV peak, within typical resistor voltage ratings — no single resistor ever blocks the full 10 kV.
- Low side (Rsense): 270 kΩ, 1% tolerance (tolerance directly affects calibration accuracy).
- Resulting ADC-side output: ~2.70 V at 10 kV, ~1.89 V at 7 kV, ~1.35 V at 5 kV — good spread and resolution within the ESP32's 0–3.3 V ADC range.

**Protection:** 3.3 V Zener diode clamp at the Rsense/ADC-side node (cathode to node, anode to ground) — a hard backstop against calculation error, component drift, or moisture-driven resistance changes upstream.

**Grounding:** the divider circuit uses its own dedicated ground rod — **not** tied to the fence charger's ground system — to avoid ground loop interference and keep the sensing circuit electrically isolated from the HV return path.

**Physical/safety notes:**

- Resistors need real air-gap spacing to prevent arcing at these voltages — not suitable for a tightly packed breadboard layout in the final build.
- Treat the divider tap as permanent exposed HV wiring in enclosure and connector design, not as a low-voltage signal wire.

### Peak Detector (pulse capture stage)

The fence pulse is far too fast for direct ESP32 ADC sampling, so a peak detector holds the pulse peak as a stable level for the ADC to read:

```
Divider output → [Diode] → [Capacitor to GND] → ESP32 ADC pin
                              |
                         [Bleed resistor to GND]
```

- **Diode:** fast-switching type (e.g., 1N4148), not a slow standard rectifier — must respond in the microsecond range.
- **Capacitor:** small value, ~0.01–0.1 µF (non-electrolytic) — charges near-instantly on the pulse, avoids smearing multiple pulses together.
- **Bleed resistor:** sets the RC discharge time constant. Target RC ≈ 100–300 ms (a fraction of the ~1–1.5 s pulse interval) so each reading reflects the current pulse without carryover from stale readings. With C = 0.1 µF, R ≈ 1–3 MΩ as a starting point — **requires empirical tuning on a breadboard**, not just calculated values.
- **Additional Zener clamp** (3.3–3.6 V) downstream of the peak detector, immediately before the ADC pin, as the final hard protection layer.

**Firmware implication:** because sampling timing relative to the pulse cycle matters, the firmware multi-samples over a 2–3 second window and takes the max reading rather than trusting a single ADC read — simpler and more robust than interrupt-based edge-triggered sampling, and more forgiving of RC tuning imprecision.

### Enclosure

- IP65/67-rated waterproof project box (~4×4×2 in starting size estimate)
- Cable glands for fence lead passthrough and antenna (if external antenna used)
- Desiccant packets for condensation control
- Horse-proofing / physical mounting still to be determined (open risk area alongside weatherproofing)

### Estimated Cost (single unit)

| Category | Item | Est. Cost |
|---|---|---|
| Compute | ESP32 dev board | $8–12 |
| Power | 18650 Li-ion cell(s) | $6–10 |
| Power | TP4056 solar charge controller | $2–4 |
| Power | Solar panel (5–6 W) | $8–15 |
| Power | Buck/boost regulator | $2–3 |
| Sensing | Divider resistors (10× 100 MΩ, 1%) | $5–10 |
| Sensing | Peak detector components | $2–3 |
| Sensing | Misc (protoboard, wire, fence clip/probe) | $5–10 |
| Enclosure | Waterproof box | $8–15 |
| Enclosure | Cable glands | $3–6 |
| Enclosure | Desiccant | $2 |
| **Total** | | **~$50–90/unit** |

Roughly 1/3 to 1/2 the per-unit cost of the closest commercial match (AKO Smart Satellite); the cost advantage improves further at multi-unit scale (bulk component buys, reused design and firmware).

---

## Software & Connectivity

Full plan, including the firmware toolchain and backend decisions, in [docs/software-plan.md](docs/software-plan.md).

**Decided:**

- Wi-Fi (ESP32 built-in) as primary connectivity, leveraging existing mesh network coverage.
- Alerts delivered both ways: a dashboard to check anytime, **and** push/active alerts on threshold drop or full outage.
- Firmware ADC strategy: multi-sample over a 2–3 s window and take the max (see peak detector section above).
- Voltage conversion: a firmware/backend calibration constant maps raw ADC readings to actual kV, calibrated against a known handheld fence tester rather than trusting divider math alone — resistor tolerance stacking across 10 series resistors introduces cumulative error.

**Open — resolved as milestones in the software plan:**

- **Firmware toolchain:** Arduino framework via PlatformIO is the working recommendation; ESP-IDF, ESPHome, and MicroPython are documented as alternatives with a final decision to be made when development begins.
- **Backend:** Home Assistant (built-in threshold alerting, free dashboard, no custom backend) vs. custom MQTT/HTTP endpoint + self-built dashboard — planned as an explicit evaluation milestone.
- **Alert logic:** specific low-voltage and no-signal (dead node vs. dead fence) alert rules, built on the 5 kV / 7 kV / 10 kV operating range.
- **Weak-signal fallback:** external-antenna ESP32 variant tested first; LoRa (ESP32 + LoRa module + gateway) as fallback if Wi-Fi proves insufficient.

---

## Commercial Implementations Evaluated

Researched as alternatives and benchmarks before deciding to build custom:

| Product | Connectivity | Power | Notes |
|---|---|---|---|
| **JVA IP Monitor** | Home Wi-Fi | — | Monitors voltage/current, threshold alerts; relay version can remotely switch the fence on/off. Works with any charger brand. |
| **AKO/Kerbl Smart Satellite** | Cellular | Battery | Preset thresholds (3 kV / 5 kV / 7 kV), historical voltage logging, no subscription fee. **Closest match to the desired functionality.** ~$150–200/unit. |
| **Tru-Test Fence Monitoring** | Radio mesh (node-to-gateway, up to 1,640 ft/node) | Solar | Multi-node system, good for monitoring multiple points/pastures and general fault location. Rated to 12,000 V. |
| **Zareba Intelligizer** | Cellular (SIM included) | — | Text-based alerts and control, works with any AC charger, free first-year cellular service. |

**Conclusion:** the AKO Smart Satellite most closely matches the desired feature set (battery power, threshold alerts, no subscription, historical logging) but at ~3–4× the per-unit cost of a DIY build. Multi-node commercial systems (Tru-Test) are worth reconsidering if the project later expands to many fence-line points, since DIY per-node engineering effort compounds at scale.

**Decision: proceed with the DIY build**, primarily for cost at potential multi-unit scale and for customization — arbitrary thresholds, self-hosted dashboard, no subscription.

---

## Key Open Risks

1. **Peak detector RC tuning** — highest technical risk; requires breadboard prototyping and empirical adjustment, not just calculated values.
2. **Weatherproofing** — enclosure sealing, cable gland selection, condensation management over long-term outdoor deployment.
3. **Horse-proofing** — physical protection/mounting so animals can't damage or dislodge the device; not yet addressed in detail.
4. **Wi-Fi signal strength at actual deployment points** — needs on-site verification before finalizing the connectivity approach (Wi-Fi vs. antenna upgrade vs. LoRa).
5. **Calibration accuracy** — needs validation against the existing handheld tester once a prototype is built.

## Safety

This project involves measuring circuits carrying up to 10,000 V pulses. Even though fence energizers are current-limited by design, treat every part of the divider chain up to the Rsense node as live HV wiring:

- Never work on the sensing chain while it is connected to an energized fence.
- Maintain arc-safe spacing between divider resistors; no compact breadboard layouts in the final build.
- Keep the sensing ground rod separate from the charger ground system.
- Both Zener clamps (at the Rsense node and at the ADC pin) are mandatory, not optional.

## Repository Layout

```
README.md                 Project overview (this file)
docs/
  hardware-plan.md        Phased hardware development plan
  software-plan.md        Phased software development plan (firmware + backend)
firmware/                 ESP32 node firmware (PlatformIO/Arduino) — see firmware/README.md
```

Planned as the project progresses: `hardware/` (schematics, BOM with sourced part numbers) and `docs/calibration.md` (per-node calibration records).
