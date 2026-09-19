/*
 * Smart Shelf reference firmware (ESP32 + DHT22 + MQ-135 + relay-controlled fan)
 *
 * Generic reference sketch: sends raw sensor readings to the FastAPI backend and
 * drives the exhaust fan relay from the fan_command in the response. All SRI,
 * alerting, and hysteresis logic is computed server-side -- this firmware never
 * makes a spoilage decision on its own.
 *
 * Libraries: Arduino core for ESP32, ArduinoJson v7, DHT sensor library by Adafruit.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>

// ==================== Configuration ====================
const char* WIFI_SSID = "your-ssid";
const char* WIFI_PASSWORD = "your-password";

// Plain HTTP for a LAN/dev deployment (see HTTPClient usage below). For a
// production deployment over the public internet, use WiFiClientSecure and an
// "https://" URL instead, and validate the backend's TLS certificate.
const char* BACKEND_BASE_URL = "http://192.168.1.50:8000";
const char* DEVICE_ID = "shelf-01";

#define DHT_PIN 4
#define DHT_TYPE DHT22

// ADC1 only (GPIO32-39). ADC2 pins share hardware with the WiFi radio and
// cannot be read reliably while WiFi is connected. GPIO34 is ADC1 and input-only.
#define MQ135_PIN 34
const uint8_t MQ135_SAMPLES = 10;             // averaged per send to denoise the ADC
const unsigned long MQ135_WARMUP_MS = 180000; // MQ-135 needs to warm up before its output is meaningful
// The device_calibration baseline (mq135_baseline) must be measured AFTER this
// warm-up period, in clean air, on this same MQ135_PIN, averaged over the same
// MQ135_SAMPLES -- otherwise gas_raw and the baseline are not on the same scale.

#define RELAY_PIN 26
const bool RELAY_ACTIVE_LOW = true; // most opto-isolated relay boards energize on LOW

const unsigned long SEND_INTERVAL_MS = 15000;
const unsigned long HTTP_TIMEOUT_MS = 5000;
const uint8_t MAX_CONSECUTIVE_FAILURES = 3;
// =========================================================

DHT dht(DHT_PIN, DHT_TYPE);

unsigned long lastSendMs = 0;
unsigned long deviceSeq = 0;
uint8_t consecutiveFailures = 0;
bool fanOn = false;

void setFan(bool on) {
  fanOn = on;
  bool level = RELAY_ACTIVE_LOW ? !on : on;
  digitalWrite(RELAY_PIN, level ? HIGH : LOW);
}

// Shared by every failure path (DHT read, HTTP request, unparseable response):
// holds the last fan state for MAX_CONSECUTIVE_FAILURES, then forces it off.
void handleFailure() {
  consecutiveFailures++;
  if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
    Serial.println("[SAFETY] max consecutive failures reached - forcing fan OFF");
    setFan(false);
  } else {
    Serial.println("[SAFETY] holding last fan state until failures clear");
  }
}

void ensureWifiConnected() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  Serial.println("[WiFi] not connected, (re)connecting...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] connected, IP=");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("[WiFi] connection attempt failed, will retry next send");
  }
}

int readMq135Average() {
  long total = 0;
  for (uint8_t i = 0; i < MQ135_SAMPLES; i++) {
    total += analogRead(MQ135_PIN);
    delay(5);
  }
  return total / MQ135_SAMPLES;
}

// The backend server-stamps device_timestamp when it is omitted, so an ESP32
// with no RTC/NTP sync should never send one (see README "Firmware contract").
String buildRequestJson(float tempC, float humidityPct, int gasRaw) {
  JsonDocument doc;
  doc["device_seq"] = deviceSeq;
  doc["temp_c"] = tempC;
  doc["humidity_pct"] = humidityPct;
  doc["gas_raw"] = gasRaw;
  doc["sensor_status"] = "ok";
  String output;
  serializeJson(doc, output);
  return output;
}

// Returns true only if fan_command was parsed and applied to the relay.
bool applyFanCommand(const String &responseBody) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, responseBody);
  if (err) {
    Serial.print("[JSON] could not parse response: ");
    Serial.println(err.c_str());
    return false;
  }
  const char* fanCommand = doc["fan_command"];
  if (fanCommand == nullptr) {
    Serial.println("[JSON] response missing fan_command");
    return false;
  }
  if (strcmp(fanCommand, "on") != 0 && strcmp(fanCommand, "off") != 0) {
    Serial.print("[JSON] unexpected fan_command value: ");
    Serial.println(fanCommand);
    return false;
  }
  setFan(strcmp(fanCommand, "on") == 0);
  Serial.print("[FAN] fan_command=");
  Serial.println(fanCommand);
  return true;
}

void setup() {
  Serial.begin(115200);
  // Latch the OFF level into the pin's output register BEFORE switching it to
  // OUTPUT mode, so an active-low relay can't glitch energized during the
  // window between reset and the first digitalWrite.
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);
  pinMode(RELAY_PIN, OUTPUT);
  fanOn = false;
  dht.begin();
}

void loop() {
  if (millis() - lastSendMs < SEND_INTERVAL_MS) {
    return;
  }
  lastSendMs = millis();

  if (millis() < MQ135_WARMUP_MS) {
    Serial.println("[MQ135] warming up");
    setFan(false);
    return;
  }

  float tempC = dht.readTemperature();
  float humidityPct = dht.readHumidity();

  if (isnan(tempC) || isnan(humidityPct)) {
    Serial.println("[DHT22] read failed (NaN) - skipping this send");
    handleFailure();
    return;
  }

  int gasRaw = readMq135Average();

  ensureWifiConnected();

  deviceSeq++;
  String requestBody = buildRequestJson(tempC, humidityPct, gasRaw);
  String url = String(BACKEND_BASE_URL) + "/devices/" + DEVICE_ID + "/readings";

  Serial.print("[HTTP] POST ");
  Serial.print(url);
  Serial.print(" body=");
  Serial.println(requestBody);

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(HTTP_TIMEOUT_MS);
  int statusCode = http.POST(requestBody);
  String responseBody = (statusCode > 0) ? http.getString() : "";

  if (statusCode == 200) {
    Serial.print("[HTTP] 200 response=");
    Serial.println(responseBody);
    if (applyFanCommand(responseBody)) {
      consecutiveFailures = 0;
    } else {
      handleFailure();
    }
  } else {
    Serial.printf("[HTTP] request failed, status=%d body=%s\n", statusCode, responseBody.c_str());
    handleFailure();
  }
  http.end();
}
