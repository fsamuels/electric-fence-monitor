# HV Voltage Divider — Schematic

Schematic for the fence-voltage sensing chain: the high-voltage resistive divider,
the peak detector, and the two Zener clamps that feed the ESP32 ADC. This is the
canonical wiring reference for the Phase 1 breadboard build (see
[hardware-plan.md](../docs/hardware-plan.md)) and the divider math behind the
firmware calibration constant (see [firmware/src/config.h](../firmware/src/config.h)).

> **Safety:** Every node from the fence tap down to the Rsense node is live HV
> wiring — up to 10 kV pulses. Both Zener clamps are mandatory. The sensing ground
> rod is **dedicated** and must not be tied to the charger's ground system. See the
> [README safety section](../README.md#safety).

---

## Sensing chain (fence → ADC)

```
   FENCE (HV, up to 10 kV pulses)
     │
     ├── HIGH SIDE: 10 × 100 MΩ, 1%  in series = 1 GΩ  (Rtop)
     │   ~1,000 V dropped per resistor at 10 kV peak → within each resistor's rating
     │
    R1  [100 MΩ]
     │
    R2  [100 MΩ]
     │
    R3  [100 MΩ]
     │
    R4  [100 MΩ]
     │
    R5  [100 MΩ]
     │
    R6  [100 MΩ]
     │
    R7  [100 MΩ]
     │
    R8  [100 MΩ]
     │
    R9  [100 MΩ]
     │
    R10 [100 MΩ]
     │
     ├──────────────┬──────────────────┐   ← Rsense node (divider tap, still exposed HV)
     │              │                   │
   Rsense         D_clamp1            (to peak detector, below)
  [270 kΩ, 1%]   ZENER 3.3 V
     │            cathode → node
     │            anode   → GND
     │              │
    GND            GND      ← dedicated sensing ground rod
                            (NOT the charger ground)

   ── Peak detector (from Rsense node) ─────────────────────────────

   Rsense node ──►│──────┬───────────┬────────────►  ESP32 ADC  (GPIO 34, PIN_FENCE_ADC)
                 D_peak  │           │
                1N4148   Cpk        Rbleed          D_clamp2
              (fast)   0.1 µF     1–3 MΩ*          ZENER 3.3–3.6 V
                        │           │              cathode → ADC node
                       GND         GND             anode   → GND
                                                     │
                                                    GND

   * Rbleed sets RC discharge (target RC ≈ 100–300 ms with Cpk = 0.1 µF).
     Value is EMPIRICAL — tune on the breadboard in Phase 2 and record the
     result in docs/rc-tuning-results.md before trusting SAMPLE_WINDOW_MS.
```

Signal flow: the 1 GΩ / 270 kΩ divider scales the fence pulse into the ADC range;
the 1N4148 rectifies each fast pulse into `Cpk`, which holds the peak while `Rbleed`
discharges it slowly enough to read but fast enough that a real voltage drop shows
within a few pulse cycles. Both Zeners are hard backstops, not the primary scaling.

---

## Components

| Ref       | Part                    | Value / spec            | Notes |
|-----------|-------------------------|-------------------------|-------|
| R1–R10    | HV resistor             | 100 MΩ, 1%              | Series = 1 GΩ high side; needs arc-safe air spacing |
| Rsense    | Resistor                | 270 kΩ, 1%              | Low side; tolerance drives calibration accuracy |
| D_clamp1  | Zener diode             | 3.3 V                   | At Rsense node; cathode→node, anode→GND |
| D_peak    | Switching diode         | 1N4148 (fast)           | Peak-detect rectifier; µs response, not a slow rectifier |
| Cpk       | Capacitor (non-electro) | 0.01–0.1 µF (0.1 µF start) | Holds pulse peak |
| Rbleed    | Resistor                | 1–3 MΩ (tune)           | Sets RC decay; **empirical**, Phase 2 |
| D_clamp2  | Zener diode             | 3.3–3.6 V               | At ADC pin; cathode→ADC node, anode→GND — final protection |

---

## Divider math

Ratio (ignoring peak-detector diode drop and ADC loading):

```
Vadc = Vfence × Rsense / (Rtop + Rsense)
     = Vfence × 270 kΩ / (1,000,000 kΩ + 270 kΩ)
     ≈ Vfence × 2.6999e-4
```

| Fence peak | ADC-side (ideal) |
|-----------:|-----------------:|
| 10 kV      | ~2.70 V |
|  7 kV      | ~1.89 V |
|  5 kV      | ~1.35 V |

Good spread within the ESP32 0–3.3 V ADC range. This is a **starting point only** —
tolerance stacking across 10 series resistors plus the diode drop makes the real
transfer function node-specific. The firmware constant `CAL_KV_PER_MV` (0.003704
kV/mV) is derived from this ideal ratio and must be replaced with the value
calibrated against the handheld tester (Phase 4, `docs/calibration.md`).

---

## Grounding

`GND` throughout this schematic is the **dedicated sensing ground rod**, driven at
the deployment site and kept electrically separate from the fence charger's ground
system. Tying them together reintroduces the ground-loop interference this design
exists to avoid.
