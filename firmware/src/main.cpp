// Fence voltage monitor node.
//
// Wakes every SAMPLE_INTERVAL_S to sample the fence ADC (radio off, cheap).
// Only brings up Wi-Fi/MQTT — the expensive ~4x part — every
// REPORT_INTERVAL_S, or immediately when a sample crosses a fault threshold.
// See docs/dashboard-plan.md#reporting-cadence-and-alert-latency for why:
// sampling often and talking rarely gives the same fault-detection latency
// as reporting every minute at roughly a third of the energy.
//
// The charger pulse is held by the hardware peak detector as a slowly
// decaying quasi-DC level; sampling continuously for SAMPLE_WINDOW_MS and
// taking the max is deliberately tolerant of RC tuning imprecision and
// sampling phase relative to the pulse cycle.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_sleep.h>

#include <cstdio>
#include <ctime>

#include "config.h"

#define FW_VERSION "0.3.0"

// Bounded window to catch a retained fence/<node_id>/calib/set command
// during a report wake, on top of the connection already paid for.
#define CALIB_SET_CHECK_MS 300

enum FenceStatus : uint8_t { STATUS_NORMAL = 0, STATUS_LOW = 1, STATUS_DOWN = 2 };

// Survives deep sleep (not power loss) — lets the backend spot resets and
// gives a cheap local record of publish failures between successful reports.
RTC_DATA_ATTR uint32_t bootCount = 0;
RTC_DATA_ATTR uint32_t failedPublishes = 0;

// Time accumulated since the last successful report, in sample-interval
// steps — the wake granularity is now SAMPLE_INTERVAL_S, not
// REPORT_INTERVAL_S, so this is how a report-due wake is recognized.
RTC_DATA_ATTR uint32_t secondsSinceReport = REPORT_INTERVAL_S;
// The fault status as of the last *reported* reading. Reporting is
// edge-triggered off this: a sample-only wake that finds the status
// unchanged stays silent, but any transition forces an immediate report.
RTC_DATA_ATTR uint8_t lastReportedStatus = STATUS_NORMAL;
// Monotonic per-node reading counter, reserved in the contract for future
// QoS-1 dedup; increments once per publish attempt (gaps from a lost publish
// are expected under QoS 0), resets with boot.
RTC_DATA_ATTR uint32_t seqCounter = 0;

static WiFiClient wifiClient;
static PubSubClient mqtt(wifiClient);
static Preferences calibPrefs;

// Runtime calibration constant: kv = adc_mv * calGainKvPerMv + calOffsetKv.
// Loaded from NVS at boot (loadCalibration()); falls back to the
// compile-time CAL_KV_PER_MV/CAL_KV_OFFSET defaults only on first-ever boot.
// This is on-node only — purely for the local fault-threshold check added
// in Phase 2. The backend computes its own authoritative kV from raw
// adc_mv via a separate, versioned calibrations table; this constant can be
// coarse. See docs/dashboard-plan.md#calibration.
static float calGainKvPerMv = CAL_KV_PER_MV;
static float calOffsetKv = CAL_KV_OFFSET;

// Set by mqttCallback() when a fence/<node_id>/calib/set command arrives
// during the bounded check window in checkCalibrationUpdate().
static bool calibUpdateReceived = false;
static float calibUpdateGain = 0.0f;
static float calibUpdateOffset = 0.0f;

// The node's own identifier: stable for the life of the board and assigned
// without any per-node config step. Downstream (topic, payload, backend) this
// is an opaque string — that it happens to come from the ESP32's MAC is an
// implementation detail here, not something the contract may assume.
//
// esp_read_mac() is used rather than ESP.getEfuseMac(), which returns a
// uint64_t with the bytes reversed relative to the printed MAC — formatting
// that naively yields an id matching neither `esptool.py read_mac` nor the
// router's DHCP table.
static void formatNodeId(char *buffer, size_t size) {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  snprintf(buffer, size, "%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

// Continuously sample the peak detector output and keep the max.
// analogReadMilliVolts applies the factory ADC calibration, which matters:
// the raw ESP32 ADC is nonlinear at the extremes. windowMs is a parameter
// rather than always SAMPLE_WINDOW_MS so calibration mode can use a shorter,
// more responsive window (CALIB_SAMPLE_WINDOW_MS) while attended.
static uint32_t sampleFencePeakMv(uint32_t windowMs) {
  uint32_t maxMv = 0;
  const uint32_t start = millis();
  while (millis() - start < windowMs) {
    const uint32_t mv = analogReadMilliVolts(PIN_FENCE_ADC);
    if (mv > maxMv) {
      maxMv = mv;
    }
  }
  return maxMv;
}

// Load the on-node calibration constant from NVS. Absent on first-ever boot
// (or after a full erase), in which case the compile-time defaults are
// seeded in so there's a single consistent source of truth from then on.
static void loadCalibration() {
  calibPrefs.begin("fence-cal", true);
  const bool hasGain = calibPrefs.isKey("gain");
  if (hasGain) {
    calGainKvPerMv = calibPrefs.getFloat("gain", CAL_KV_PER_MV);
    calOffsetKv = calibPrefs.getFloat("offset", CAL_KV_OFFSET);
  }
  calibPrefs.end();
  if (!hasGain) {
    calibPrefs.begin("fence-cal", false);
    calibPrefs.putFloat("gain", calGainKvPerMv);
    calibPrefs.putFloat("offset", calOffsetKv);
    calibPrefs.end();
  }
}

// Persist a new calibration constant and switch to it immediately. Skips
// the NVS write if the values already match, since the source command is a
// retained MQTT message that redelivers on every reconnect and flash write
// endurance is finite.
static void applyCalibrationUpdate(float gain, float offset) {
  if (gain == calGainKvPerMv && offset == calOffsetKv) {
    return;
  }
  calGainKvPerMv = gain;
  calOffsetKv = offset;
  calibPrefs.begin("fence-cal", false);
  calibPrefs.putFloat("gain", gain);
  calibPrefs.putFloat("offset", offset);
  calibPrefs.end();
  Serial.printf("calibration updated: gain=%.6f offset=%.4f\n", gain, offset);
}

// PubSubClient's callback for the bounded fence/<node_id>/calib/set check
// in checkCalibrationUpdate(). Global rather than a lambda/capture because
// PubSubClient's callback signature carries no user context pointer.
static void mqttCallback(char *topic, uint8_t *payload, unsigned int length) {
  (void)topic;
  JsonDocument doc;
  if (deserializeJson(doc, payload, length) != DeserializationError::Ok) {
    return;
  }
  if (!doc["kv_per_mv"].is<float>() || !doc["kv_offset"].is<float>()) {
    return;
  }
  calibUpdateGain = doc["kv_per_mv"].as<float>();
  calibUpdateOffset = doc["kv_offset"].as<float>();
  calibUpdateReceived = true;
}

static FenceStatus classifyStatus(float fenceKv) {
  if (fenceKv < DOWN_KV_THRESHOLD) {
    return STATUS_DOWN;
  }
  if (fenceKv < LOW_KV_THRESHOLD) {
    return STATUS_LOW;
  }
  return STATUS_NORMAL;
}

static float readBatteryVolts() {
  uint32_t sumMv = 0;
  for (int i = 0; i < BATT_SAMPLES; ++i) {
    sumMv += analogReadMilliVolts(PIN_BATT_ADC);
    delay(2);
  }
  const float avgMv = sumMv / static_cast<float>(BATT_SAMPLES);
  return avgMv * BATT_DIVIDER_RATIO / 1000.0f;
}

static bool connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  const uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start >= WIFI_TIMEOUT_MS) {
      return false;
    }
    delay(100);
  }
  return true;
}

// Best-effort NTP sync so the reading can carry a real `ts`. Bounded so a
// slow/unreachable NTP server doesn't eat into the battery budget on top of
// the Wi-Fi timeout already spent. Returns false (no ts sent) on timeout —
// the backend falls back to receive time, same as it does for mock data
// that omits ts today.
static bool syncTimeGetEpoch(time_t *outEpoch) {
  configTime(0, 0, NTP_SERVER);
  const uint32_t start = millis();
  time_t now = time(nullptr);
  // Before sync, time() reads near the 1970 epoch; a plausible 2020+ value
  // is the signal that SNTP has actually landed a reply.
  while (now < 1600000000) {
    if (millis() - start >= NTP_SYNC_TIMEOUT_MS) {
      return false;
    }
    delay(100);
    now = time(nullptr);
  }
  *outEpoch = now;
  return true;
}

// Leaves the MQTT connection open on success so checkCalibrationUpdate()
// can reuse it without paying for a second TCP handshake; the caller is
// responsible for the final mqtt.disconnect().
static bool publishState(const char *nodeId, uint32_t fenceMv, float fenceKv,
                         float battV, int32_t rssi, uint32_t wifiMs) {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(mqttCallback);

  bool connected;
  if (strlen(MQTT_USER) > 0) {
    connected = mqtt.connect(nodeId, MQTT_USER, MQTT_PASSWORD);
  } else {
    connected = mqtt.connect(nodeId);
  }
  if (!connected) {
    Serial.printf("MQTT connect failed, state=%d\n", mqtt.state());
    return false;
  }

  time_t epoch;
  const bool haveTime = syncTimeGetEpoch(&epoch);

  JsonDocument doc;
  doc["node_id"] = nodeId;
  doc["fw"] = FW_VERSION;
  doc["kv"] = roundf(fenceKv * 100.0f) / 100.0f;
  doc["adc_mv"] = fenceMv;
  doc["batt_v"] = roundf(battV * 100.0f) / 100.0f;
  doc["rssi"] = rssi;
  doc["boot"] = bootCount;
  doc["failed_pub"] = failedPublishes;
  doc["wifi_ms"] = wifiMs;
  doc["seq"] = ++seqCounter;
  doc["sample_interval_s"] = SAMPLE_INTERVAL_S;
  doc["report_interval_s"] = REPORT_INTERVAL_S;
  if (haveTime) {
    doc["ts"] = static_cast<uint32_t>(epoch);
  }

  char topic[64];
  snprintf(topic, sizeof(topic), "fence/%s/state", nodeId);
  char payload[MQTT_MAX_PACKET_SIZE];
  const size_t len = serializeJson(doc, payload, sizeof(payload));

  // Retained, so the dashboard always shows the last known reading.
  const bool ok = mqtt.publish(topic, reinterpret_cast<const uint8_t *>(payload), len, true);
  if (ok) {
    Serial.printf("published %s %s\n", topic, payload);
  }
  // Give the TCP stack a moment to flush. Connection is left open — see
  // the function comment above.
  mqtt.loop();
  delay(50);
  return ok;
}

// Bounded check for a retained calibration update, reusing the still-open
// MQTT connection from a just-completed publishState(). One report-cycle
// lag to apply is an acceptable tradeoff for not reordering the Phase 2
// sample-before-radio flow.
static void checkCalibrationUpdate(const char *nodeId) {
  char topic[64];
  snprintf(topic, sizeof(topic), "fence/%s/calib/set", nodeId);
  calibUpdateReceived = false;
  mqtt.subscribe(topic);
  const uint32_t start = millis();
  while (millis() - start < CALIB_SET_CHECK_MS && !calibUpdateReceived) {
    mqtt.loop();
    delay(20);
  }
  mqtt.unsubscribe(topic);
  if (calibUpdateReceived) {
    applyCalibrationUpdate(calibUpdateGain, calibUpdateOffset);
  }
}

// Calibration mode: entered instead of the normal sample/report/sleep loop
// when PIN_CALIB_MODE is held LOW at boot. Streams fast readings (serial +
// MQTT, unretained) while someone at the fence compares against the
// handheld tester; exits by releasing the pin. See firmware/README.md's
// Calibration section for the full workflow, including the offline
// multi-point fit tool and how the result gets back onto the node.
static void runCalibrationMode(const char *nodeId) {
  Serial.println("=== calibration mode ===");
  Serial.printf("current gain=%.6f offset=%.4f\n", calGainKvPerMv, calOffsetKv);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  const uint32_t wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < WIFI_TIMEOUT_MS) {
    delay(100);
  }
  const bool wifiOk = WiFi.status() == WL_CONNECTED;
  if (wifiOk) {
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    bool mqttOk;
    if (strlen(MQTT_USER) > 0) {
      mqttOk = mqtt.connect(nodeId, MQTT_USER, MQTT_PASSWORD);
    } else {
      mqttOk = mqtt.connect(nodeId);
    }
    Serial.printf("wifi up; mqtt %s\n", mqttOk ? "connected" : "failed, streaming to serial only");
  } else {
    Serial.println("wifi failed; streaming to serial only");
  }

  char topic[64];
  snprintf(topic, sizeof(topic), "fence/%s/calib", nodeId);
  uint32_t n = 0;
  while (digitalRead(PIN_CALIB_MODE) == LOW) {
    const uint32_t fenceMv = sampleFencePeakMv(CALIB_SAMPLE_WINDOW_MS);
    const float fenceKv = fenceMv * calGainKvPerMv + calOffsetKv;
    ++n;
    Serial.printf("calib #%u: adc_mv=%u kv=%.3f\n", n, fenceMv, fenceKv);

    if (wifiOk && mqtt.connected()) {
      JsonDocument doc;
      doc["adc_mv"] = fenceMv;
      doc["kv"] = roundf(fenceKv * 100.0f) / 100.0f;
      doc["n"] = n;
      char payload[128];
      const size_t len = serializeJson(doc, payload, sizeof(payload));
      mqtt.publish(topic, reinterpret_cast<const uint8_t *>(payload), len, false);
      mqtt.loop();
    }
    delay(CALIB_PUBLISH_INTERVAL_MS);
  }

  Serial.println("calibration mode exited, rebooting into normal operation");
  Serial.flush();
  mqtt.disconnect();
  WiFi.disconnect(true);
  esp_restart();
}

static void goToSleep() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  Serial.printf("sleeping for %d s\n", SAMPLE_INTERVAL_S);
  Serial.flush();
  esp_sleep_enable_timer_wakeup(static_cast<uint64_t>(SAMPLE_INTERVAL_S) * 1000000ULL);
  esp_deep_sleep_start();
}

void setup() {
  Serial.begin(115200);
  char nodeId[13];
  formatNodeId(nodeId, sizeof(nodeId));

  analogSetPinAttenuation(PIN_FENCE_ADC, ADC_11db);  // full 0-3.3 V range
  analogSetPinAttenuation(PIN_BATT_ADC, ADC_11db);
  loadCalibration();

  // Held LOW at boot (e.g. the devkit's BOOT button): enter calibration
  // mode instead of the normal cycle. Checked before bootCount increments
  // and before deep sleep is ever considered — this is a deliberately
  // separate, attended path, not a wake cycle.
  pinMode(PIN_CALIB_MODE, INPUT_PULLUP);
  if (digitalRead(PIN_CALIB_MODE) == LOW) {
    runCalibrationMode(nodeId);  // never returns: reboots on exit
  }

  ++bootCount;
  Serial.printf("\n%s fw %s boot %u\n", nodeId, FW_VERSION, bootCount);

  // Sample before bringing the radio up: less supply noise on the ADC and
  // no radio drawing current during the multi-second window.
  const uint32_t fenceMv = sampleFencePeakMv(SAMPLE_WINDOW_MS);
  const float fenceKv = fenceMv * calGainKvPerMv + calOffsetKv;
  const float battV = readBatteryVolts();
  Serial.printf("fence %u mV (%.2f kV), batt %.2f V\n", fenceMv, fenceKv,
                battV);

  // This wake represents SAMPLE_INTERVAL_S of elapsed time since the last
  // one, whether or not it ends up reporting.
  secondsSinceReport += SAMPLE_INTERVAL_S;
  const FenceStatus status = classifyStatus(fenceKv);
  const bool reportDue = secondsSinceReport >= REPORT_INTERVAL_S;
  const bool faultEdge = status != static_cast<FenceStatus>(lastReportedStatus);

  if (!reportDue && !faultEdge) {
    // Sample-only wake: radio never comes up. This is the cheap ~4x path
    // that makes frequent sampling affordable.
    Serial.println("sample-only wake, no report due");
    goToSleep();
    return;
  }

  Serial.printf("reporting (due=%d, fault_edge=%d)\n", reportDue, faultEdge);
  bool published = false;
  const uint32_t wifiStart = millis();
  if (connectWifi()) {
    const uint32_t wifiMs = millis() - wifiStart;
    const int32_t rssi = WiFi.RSSI();
    Serial.printf("wifi up in %u ms, rssi %d dBm\n", wifiMs,
                  static_cast<int>(rssi));
    published = publishState(nodeId, fenceMv, fenceKv, battV, rssi, wifiMs);
    if (published) {
      // Reuses the still-open connection from publishState() above.
      checkCalibrationUpdate(nodeId);
    }
    mqtt.disconnect();
  } else {
    Serial.println("wifi connect timed out");
  }

  if (published) {
    secondsSinceReport = 0;
    lastReportedStatus = status;
  } else {
    // Leave secondsSinceReport/lastReportedStatus alone: reportDue or
    // faultEdge will still hold next wake, so the next sample retries the
    // report rather than silently waiting a full REPORT_INTERVAL_S.
    ++failedPublishes;
  }
  goToSleep();
}

// Never reached: setup() ends in deep sleep and wake-up restarts the sketch.
void loop() {}
