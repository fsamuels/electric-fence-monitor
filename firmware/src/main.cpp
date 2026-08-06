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
#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_sleep.h>

#include <cstdio>
#include <ctime>

#include "config.h"

#define FW_VERSION "0.2.0"

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
// the raw ESP32 ADC is nonlinear at the extremes.
static uint32_t sampleFencePeakMv() {
  uint32_t maxMv = 0;
  const uint32_t start = millis();
  while (millis() - start < SAMPLE_WINDOW_MS) {
    const uint32_t mv = analogReadMilliVolts(PIN_FENCE_ADC);
    if (mv > maxMv) {
      maxMv = mv;
    }
  }
  return maxMv;
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

static bool publishState(const char *nodeId, uint32_t fenceMv, float fenceKv,
                         float battV, int32_t rssi, uint32_t wifiMs) {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);

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
  // Give the TCP stack a moment to flush before we tear everything down.
  mqtt.loop();
  delay(50);
  mqtt.disconnect();
  return ok;
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
  ++bootCount;
  Serial.begin(115200);
  char nodeId[13];
  formatNodeId(nodeId, sizeof(nodeId));
  Serial.printf("\n%s fw %s boot %u\n", nodeId, FW_VERSION, bootCount);

  analogSetPinAttenuation(PIN_FENCE_ADC, ADC_11db);  // full 0-3.3 V range
  analogSetPinAttenuation(PIN_BATT_ADC, ADC_11db);

  // Sample before bringing the radio up: less supply noise on the ADC and
  // no radio drawing current during the multi-second window.
  const uint32_t fenceMv = sampleFencePeakMv();
  const float fenceKv = fenceMv * CAL_KV_PER_MV + CAL_KV_OFFSET;
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
