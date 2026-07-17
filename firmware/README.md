# Firmware

First-pass firmware for the fence monitor node (software plan Phases 1–2): wake, multi-sample the peak detector output and take the max, read battery voltage, publish a retained JSON state message over MQTT, deep sleep.

## Setup

1. Install [PlatformIO](https://platformio.org/) (`pip install platformio` or the VS Code extension).
2. Copy the config template and edit it for the node being built:

   ```sh
   cp src/config.example.h src/config.h
   ```

   `src/config.h` is gitignored — it holds Wi-Fi credentials and the per-node calibration constant.
3. Build / flash / watch:

   ```sh
   pio run                 # build
   pio run -t upload       # flash over USB
   pio device monitor      # serial output at 115200
   ```

## What it publishes

One retained message per wake cycle to `fence/<NODE_ID>/state`:

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

| Field | Meaning |
|---|---|
| `kv` | Calibrated fence voltage (`adc_mv * CAL_KV_PER_MV + CAL_KV_OFFSET`) |
| `adc_mv` | Raw max millivolts seen at the ADC over the sample window — kept in the payload so calibration can be redone from history |
| `batt_v` | Battery voltage via divider on `PIN_BATT_ADC` |
| `rssi` | Wi-Fi signal at this wake — feeds the antenna-vs-LoRa decision |
| `boot` | Wake counter (RTC memory; resets on power loss) |
| `failed_pub` | Wake cycles that failed to publish since last power loss |
| `wifi_ms` | Time to associate — another weak-signal indicator |

If Wi-Fi doesn't come up within `WIFI_TIMEOUT_MS`, the node increments `failed_pub` and goes back to sleep rather than draining the battery retrying. "Node went silent" detection is the backend's job (software plan Phase 6).

## Bench testing without high voltage

Feed a known DC level (0–3 V, e.g. from a bench supply or a potentiometer across 3.3 V) into `PIN_FENCE_ADC` and check that `adc_mv` tracks it. The multi-sample/max logic can be exercised with a function generator producing slow pulses. No fence or HV divider is needed to develop against the MQTT/backend side.

## Calibration

`CAL_KV_PER_MV` defaults to the theoretical divider ratio (0.003704 kV/mV). After the hardware Phase 4 calibration against the handheld tester, replace it per node and record the derivation in [`docs/calibration.md`](../docs/calibration.md).

## Not yet implemented (later phases)

- Calibration mode with rapid streamed readings (Phase 3)
- OTA updates, watchdog/brown-out handling, Wi-Fi outage backoff, buffered readings across failed transmits (Phase 4)
