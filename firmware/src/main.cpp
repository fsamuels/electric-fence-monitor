// Fence voltage monitor node — first pass.
//
// Wake → sample fence ADC (multi-sample/max over a window covering at
// least one charger pulse) → read battery → connect Wi-Fi (bounded) →
// publish retained JSON state over MQTT → deep sleep.
//
// The charger pulse is held by the hardware peak detector as a slowly
// decaying quasi-DC level; sampling continuously for SAMPLE_WINDOW_MS and
// taking the max is deliberately tolerant of RC tuning imprecision and
// sampling phase relative to the pulse cycle.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_sleep.h>

#include "config.h"

#define FW_VERSION "0.1.0"

// Survives deep sleep (not power loss) — lets the backend spot resets and
// gives a cheap local record of publish failures between successful reports.
RTC_DATA_ATTR uint32_t bootCount = 0;
RTC_DATA_ATTR uint32_t failedPublishes = 0;

static WiFiClient wifiClient;
static PubSubClient mqtt(wifiClient);

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

static float readBatteryVolts() {
  uint32_t sumMv = 0;
  for (int i = 0; i < BATT_SAMPLES; ++i) {
    sumMv += analogReadMilliVolts(PIN_BATT_ADC);
    delay(2);
  }
  const float avgMv = sumMv / (float)BATT_SAMPLES;
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

static bool publishState(uint32_t fenceMv, float fenceKv, float battV,
                         long rssi, uint32_t wifiMs) {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);

  bool connected;
  if (strlen(MQTT_USER) > 0) {
    connected = mqtt.connect(NODE_ID, MQTT_USER, MQTT_PASSWORD);
  } else {
    connected = mqtt.connect(NODE_ID);
  }
  if (!connected) {
    Serial.printf("MQTT connect failed, state=%d\n", mqtt.state());
    return false;
  }

  JsonDocument doc;
  doc["node"] = NODE_ID;
  doc["fw"] = FW_VERSION;
  doc["kv"] = roundf(fenceKv * 100.0f) / 100.0f;
  doc["adc_mv"] = fenceMv;
  doc["batt_v"] = roundf(battV * 100.0f) / 100.0f;
  doc["rssi"] = rssi;
  doc["boot"] = bootCount;
  doc["failed_pub"] = failedPublishes;
  doc["wifi_ms"] = wifiMs;

  char topic[64];
  snprintf(topic, sizeof(topic), "fence/%s/state", NODE_ID);
  char payload[MQTT_MAX_PACKET_SIZE];
  const size_t len = serializeJson(doc, payload, sizeof(payload));

  // Retained, so the dashboard always shows the last known reading.
  const bool ok = mqtt.publish(topic, (const uint8_t *)payload, len, true);
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
  Serial.printf("sleeping for %d s\n", SLEEP_INTERVAL_S);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)SLEEP_INTERVAL_S * 1000000ULL);
  esp_deep_sleep_start();
}

void setup() {
  ++bootCount;
  Serial.begin(115200);
  Serial.printf("\n%s fw %s boot %u\n", NODE_ID, FW_VERSION, bootCount);

  analogSetPinAttenuation(PIN_FENCE_ADC, ADC_11db);  // full 0-3.3 V range
  analogSetPinAttenuation(PIN_BATT_ADC, ADC_11db);

  // Sample before bringing the radio up: less supply noise on the ADC and
  // no radio drawing current during the multi-second window.
  const uint32_t fenceMv = sampleFencePeakMv();
  const float fenceKv = fenceMv * CAL_KV_PER_MV + CAL_KV_OFFSET;
  const float battV = readBatteryVolts();
  Serial.printf("fence %u mV (%.2f kV), batt %.2f V\n", fenceMv, fenceKv,
                battV);

  bool published = false;
  const uint32_t wifiStart = millis();
  if (connectWifi()) {
    const uint32_t wifiMs = millis() - wifiStart;
    const long rssi = WiFi.RSSI();
    Serial.printf("wifi up in %u ms, rssi %ld dBm\n", wifiMs, rssi);
    published = publishState(fenceMv, fenceKv, battV, rssi, wifiMs);
  } else {
    Serial.println("wifi connect timed out");
  }

  if (!published) {
    ++failedPublishes;
  }
  goToSleep();
}

// Never reached: setup() ends in deep sleep and wake-up restarts the sketch.
void loop() {}
