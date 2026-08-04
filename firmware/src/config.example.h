// Per-node configuration.
// Copy this file to config.h (gitignored) and edit for the node being built.
// Everything node-specific lives here so nodes 2+ are a config change,
// not a code fork.
#pragma once

// --- Node identity ---
#define NODE_ID "fence-01"

// --- Wi-Fi ---
#define WIFI_SSID "your-ssid"
#define WIFI_PASSWORD "your-password"
// A node in a weak-signal spot must not burn its battery retrying;
// after this long it gives up and goes back to sleep.
#define WIFI_TIMEOUT_MS 15000

// --- MQTT ---
#define MQTT_HOST "192.168.1.10"
#define MQTT_PORT 1883
#define MQTT_USER ""      // leave empty for anonymous
#define MQTT_PASSWORD ""
// State is published retained to: fence/<NODE_ID>/state

// --- Pins ---
// GPIO 34/35 are input-only ADC1 pins: no pull-ups to fight, and ADC1
// keeps working while Wi-Fi is active (ADC2 does not).
#define PIN_FENCE_ADC 34  // peak detector output
#define PIN_BATT_ADC 35   // battery divider tap

// --- Sampling ---
// Window must cover at least one full charger pulse interval (~1-1.5 s).
// Revisit against measured RC decay from hardware Phase 2
// (docs/rc-tuning-results.md).
#define SAMPLE_WINDOW_MS 2500
#define BATT_SAMPLES 8

// --- Calibration (per node) ---
// kv = adc_mv * CAL_KV_PER_MV + CAL_KV_OFFSET
// Theoretical divider math: 10 kV across 1 GΩ + 270 kΩ gives ~2.70 V at
// the ADC, i.e. 0.003704 kV/mV. Resistor tolerance stacking makes this
// a starting point only — replace with the constant derived against the
// handheld tester and record it in docs/calibration.md.
#define CAL_KV_PER_MV 0.003704f
#define CAL_KV_OFFSET 0.0f

// Battery sense divider ratio (two equal resistors = 2.0).
#define BATT_DIVIDER_RATIO 2.0f

// --- Duty cycle ---
#define SLEEP_INTERVAL_S 600  // 10 min; alert latency vs. power trade-off
