# Firmware

Firmware for the fence monitor node (software plan Phases 1–3): wake every `SAMPLE_INTERVAL_S`, multi-sample the peak detector output and take the max, read battery voltage. Most wakes go straight back to sleep; only every `REPORT_INTERVAL_S`, or immediately when a reading crosses a fault threshold, does it bring up Wi-Fi and publish a retained JSON state message over MQTT. See [Duty cycle](#duty-cycle) below. A held pin at boot instead enters [calibration mode](#calibration).

## Setup

1. Install [PlatformIO](https://platformio.org/) (`pip install platformio` or the VS Code extension).
2. Copy the config template and edit it for the node being built:

   ```sh
   cp src/config.example.h src/config.h
   ```

   `src/config.h` is gitignored — it holds Wi-Fi credentials and the coarse per-node calibration constant. Identity is not configured; firmware derives `node_id` from the ESP32 MAC at runtime.
3. Build / flash / watch:

   ```sh
   pio run                 # build
   pio run -t upload       # flash over USB
   pio device monitor      # serial output at 115200
   ```

## Duty cycle

The node wakes every `SAMPLE_INTERVAL_S` (default 60) and always takes a
reading, radio off. It only brings up Wi-Fi/MQTT — the expensive part, ~4x
the cost of sampling — every `REPORT_INTERVAL_S` (default 900), or
immediately, out of band, when the reading's status (`normal`/`low`/`down`
against `LOW_KV_THRESHOLD`/`DOWN_KV_THRESHOLD`) differs from the last
*reported* status. This is Option D from
[dashboard-plan.md#reporting-cadence-and-alert-latency](../docs/dashboard-plan.md#reporting-cadence-and-alert-latency):
same ~1 min fault-detection latency as reporting every minute, at roughly a
third of the energy. Threshold state and the time-since-last-report counter
live in RTC memory, so they survive deep sleep (not power loss). A failed
publish doesn't reset the counters, so the next sample-cycle wake retries the
report rather than waiting a full `REPORT_INTERVAL_S`.

The on-node thresholds are intentionally coarse — they only decide whether
*this* node interrupts its own schedule, and don't have to match the
backend's configurable alert thresholds (software plan Phase 6).

## What it publishes

One retained message per **report** (not per wake — see
[Duty cycle](#duty-cycle)) to `fence/<node_id>/state`, keyed on the ESP32's
MAC-derived hardware identity. Which fence a board is watching becomes a
versioned assignment in the backend, so relocating hardware needs no reflash.
See
[docs/dashboard-plan.md](../docs/dashboard-plan.md#identity-nodes-locations-and-assignments).

```json
{
  "node_id": "a4c1385f2b10",
  "fw": "0.3.0",
  "kv": 6.93,
  "adc_mv": 1872,
  "batt_v": 3.98,
  "rssi": -71,
  "boot": 123,
  "failed_pub": 2,
  "wifi_ms": 2300,
  "seq": 47,
  "sample_interval_s": 60,
  "report_interval_s": 900,
  "ts": 1751328000
}
```

| Field | Meaning |
|---|---|
| `node_id` | ESP32 MAC-derived hardware identity; also the MQTT topic segment |
| `kv` | On-node fence voltage estimate (`adc_mv * gain + offset`, from NVS — see [Calibration](#calibration)). Advisory only; the backend recomputes its own authoritative kV from `adc_mv` |
| `adc_mv` | Raw max millivolts seen at the ADC over the sample window — kept in the payload so calibration can be redone from history |
| `batt_v` | Battery voltage via divider on `PIN_BATT_ADC` |
| `rssi` | Wi-Fi signal at this wake — feeds the antenna-vs-LoRa decision |
| `boot` | Wake counter (RTC memory; resets on power loss) |
| `failed_pub` | Publish attempts that failed to connect since last power loss |
| `wifi_ms` | Time to associate — another weak-signal indicator |
| `seq` | Monotonic per-node counter, one per publish attempt (RTC memory; resets on power loss) — reserved for future QoS-1 dedup |
| `sample_interval_s` | Current `SAMPLE_INTERVAL_S`, so the backend's silent-window math can key off it if sampling cadence ever varies |
| `report_interval_s` | Current `REPORT_INTERVAL_S`; already used backend-side to size the silent-node detection window |
| `ts` | UTC epoch seconds the reading was taken, from a best-effort NTP sync at report time. Omitted if NTP doesn't land within `NTP_SYNC_TIMEOUT_MS` — the backend falls back to receive time in that case |

If Wi-Fi doesn't come up within `WIFI_TIMEOUT_MS`, the node increments `failed_pub` and goes back to sleep rather than draining the battery retrying. "Node went silent" detection is the backend's job (software plan Phase 6).

## Linting

`./lint.sh` runs two static analyzers, neither of which needs the ESP32 toolchain installed:

- **cppcheck** (`apt install cppcheck`) — bug-oriented static analysis: `--enable=warning,style,performance,portability`, with system-header lookups suppressed since Arduino/ESP-IDF headers aren't present off-target.
- **cpplint** (`pip install cpplint`) — Google C++ style checks, with the copyright-header and include-subdir rules disabled and a 100-column line limit.

Deeper analysis (**clang-tidy**, or PlatformIO's `pio check`) requires the full Arduino/ESP-IDF include tree, so run those on a machine with the toolchain installed. Keep `lint.sh` passing before pushing.

## Bench testing without high voltage

Feed a known DC level (0–3 V, e.g. from a bench supply or a potentiometer across 3.3 V) into `PIN_FENCE_ADC` and check that `adc_mv` tracks it. The multi-sample/max logic can be exercised with a function generator producing slow pulses. No fence or HV divider is needed to develop against the MQTT/backend side.

## Calibration

`CAL_KV_PER_MV`/`CAL_KV_OFFSET` in `config.h` (0.003704 kV/mV, the
theoretical divider ratio) are **cold-start defaults only**. The real
per-node `gain`/`offset` lives in NVS once a calibration has been derived
and pushed — editing `config.h` and reflashing has no effect on a node
that's already been calibrated.

This on-node value is not the backend's authoritative kV — the dashboard
recomputes that separately, at query time, from a versioned `calibrations`
table (`docs/dashboard-plan.md#calibration`). The on-node constant only
needs to be good enough for this node's own fault-threshold check
(`LOW_KV_THRESHOLD`/`DOWN_KV_THRESHOLD`, [Duty cycle](#duty-cycle)), so it
can be coarse.

**Entering calibration mode:** hold `PIN_CALIB_MODE` (default GPIO0, the
devkit's BOOT button) LOW at boot/reset. The node skips the normal
sample/report/sleep loop entirely, connects Wi-Fi/MQTT once, and while the
pin stays low streams a fast reading (`CALIB_SAMPLE_WINDOW_MS` window, once
every `CALIB_PUBLISH_INTERVAL_MS`) to both serial and an **unretained**
`fence/<node_id>/calib` topic (`{"adc_mv", "kv", "n"}`) — enough to work
from just a laptop's serial monitor, no MQTT client required. Releasing the
pin reboots the node back into normal operation. (An MQTT-triggered
alternative — publishing a command to start calibration remotely — was
considered but not built: it would need the node to keep a persistent MQTT
subscription alive well beyond a normal report wake, cutting against the
whole sample-cheap/report-rarely design from Phase 2. Calibration is an
attended, at-the-fence activity anyway.)

**Deriving and applying the fit:** collect 3–5 `(adc_mv, handheld_tester_kv)`
pairs from the stream above, spread across 5–10 kV, then run
[`tools/fit_calibration.py`](tools/fit_calibration.py) (stdlib-only linear
least-squares — no numpy dependency for a bench tool):

```sh
python3 tools/fit_calibration.py 1872:6.93 2010:7.40 1350:5.10 --node-id a4c1385f2b10
```

It prints the fitted `gain`/`offset`, residuals against each point (to
sanity-check linearity), and the `mosquitto_pub` command line to publish
them **retained** to `fence/<node_id>/calib/set`. The node picks up a
retained command on its next report wake (checked in a bounded ~300 ms
window right after publishing state, reusing that connection rather than
paying for a second one), writes it to NVS, and uses it from then on.

Record every derived calibration in [`docs/calibration.md`](../docs/calibration.md), keyed on `(node_id, location_id, date)` — a board swap or relocation each invalidate a prior calibration.

## Not yet implemented (later phases)

- **Enclosure temperature (`temp_c`) logging (Phase 3).** Blocked on
  hardware: no temperature sensor exists yet, and the ESP32's internal one
  is explicitly ruled out (self-heated, too poor). Sensor choice (TMP102 vs
  DS18B20) is still an open PCB-layout decision — see
  `hardware/pcb-design-plan.md`'s "Temperature sensor footprint" note.
- OTA updates, watchdog/brown-out handling, Wi-Fi outage backoff, buffered readings across failed transmits (Phase 4)
