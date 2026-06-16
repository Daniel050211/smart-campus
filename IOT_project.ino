/*****************************************************************************
 * ESP8266 IoT Status Tag - TM1118 Smart Campus W311
 *
 * Receives alert data via MQTT, displays on LED matrix + RGB LED.
 * Button press acknowledges/clears the current alert.
 *
 * Hardware: ESP8266 + LED Matrix + RGB LED (NO buzzer)
 *
 * LED & 8x8 Matrix behaviour:
 *   - Startup:  8x8 matrix shows "S", LED blue / light blue
 *   - Normal:   Green LED on, 8x8 matrix blank (nothing displayed)
 *   - Critical (LED blink red, matrix shows "C"):
 *       * temp > 40 AND humidity < 40
 *       * OR after 00:00 AND light > 60 AND sound > 40
 *   - Warning (LED blink yellow, matrix shows "W"):
 *       * temp > 40 OR temp < 15
 *   - Button press: Green LED on, 8x8 matrix blank
 *       (stays normal until next critical/warning alert arrives)
 *
 * Libraries:
 *   - ESP8266WiFi.h
 *   - PubSubClient.h
 *   - ArduinoJson.h
 *   - LedMatrix.h
 *   - ButtonDebounce.h
 ******************************************************************************/

#include <SPI.h>
#include <ButtonDebounce.h>
#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "LedMatrix.h"

// Forward declaration
struct AlertState;

// --- Pin Configuration ---
#define NUMBER_OF_DEVICES 1
#define CS_PIN        D4
#define red_light_pin D0    // Active LOW: LOW = ON
#define green_light_pin D8
#define blue_light_pin  D3
#define TRIG          D2    // Acknowledge button

// --- WiFi & MQTT ---
const char *ssid = "EIA-W311MESH";
const char *password = "42004200";
const char *mqtt_server = "ia.ic.polyu.edu.hk";
const char *mqttTopic_TX = "IC/TM1118/TEAM_A02/PUB";
const char *mqttTopic_RX = "IC/TM1118/TEAM_A02/SUB";
const char *mqttTopic_Alert = "iot/alerts/#";       // Subscribe to all alerts
const char *mqttTopic_Led = "iot/device/led";       // LED control from Django
const char *mqttTopic_Matrix = "iot/device/matrix"; // Matrix control from Django

// --- Objects ---
LedMatrix ledMatrix = LedMatrix(NUMBER_OF_DEVICES, CS_PIN);
WiFiClient espClient;
PubSubClient client(espClient);
ButtonDebounce trigger(TRIG, 100);

// --- State ---
byte reconnect_count = 0;
unsigned long reconnect_delay = 5000;
const unsigned long max_reconnect_delay = 60000;
const byte max_reconnect_attempts = 20;
char msg[256];
String ipAddress;
String macAddr;
String recMsg = "";
volatile bool buttonPressed = false;

// --- Device State ---
enum DeviceMode {
  MODE_STARTUP,    // Blue LED + "S" on matrix
  MODE_NORMAL,     // Green LED on, matrix blank
  MODE_CRITICAL,   // Blink red LED + "C" on matrix
  MODE_WARNING     // Blink yellow LED + "W" on matrix
};
DeviceMode currentMode = MODE_STARTUP;

// --- Alert Type Helpers ---
// Maps alert_type to a single-char for LED Matrix display
char alertTypeChar(String parameter) {
  // Critical alerts -> "C"
  if (parameter == "critical_temp_humidity")  return 'C';
  if (parameter == "critical_night_intrusion") return 'C';
  // Warning alerts -> "W"
  if (parameter == "high_temp")    return 'W';
  if (parameter == "low_temp")     return 'W';
  if (parameter == "night_noise")  return 'W';
  if (parameter == "night_light")  return 'W';
  // Legacy / fallback
  if (parameter == "night_intrusion") return 'C';
  if (parameter == "high_humidity")   return 'W';
  if (parameter == "low_humidity")    return 'W';
  return '!';
}

// Severity priority: critical=2, warning=1, info/other=0
int severityPriority(String severity) {
  if (severity == "critical") return 2;
  if (severity == "warning")  return 1;
  return 0;
}

// --- Alert State (Queue: up to 3 alerts) ---
#define MAX_ALERTS 3

struct AlertState {
  String nodeId;
  String location;
  String parameter;
  String severity;
  float value;
  unsigned long timestamp;
  bool active;
};

AlertState alertQueue[MAX_ALERTS];
int activeAlertCount = 0;

// Get the highest-priority active alert, or NULL if none
AlertState* getTopAlert() {
  AlertState* best = nullptr;
  int bestPri = -1;
  for (int i = 0; i < MAX_ALERTS; i++) {
    if (alertQueue[i].active) {
      int pri = severityPriority(alertQueue[i].severity);
      if (pri > bestPri) {
        bestPri = pri;
        best = &alertQueue[i];
      }
    }
  }
  return best;
}

// Find a slot: reuse an inactive slot, or the lowest-priority active one
int findAlertSlot(String parameter, String location) {
  // First, check if same type+location already exists -> update it
  for (int i = 0; i < MAX_ALERTS; i++) {
    if (alertQueue[i].active &&
        alertQueue[i].parameter == parameter &&
        alertQueue[i].location == location) {
      return i;
    }
  }
  // Find an inactive slot
  for (int i = 0; i < MAX_ALERTS; i++) {
    if (!alertQueue[i].active) return i;
  }
  // All full -> replace the lowest-priority one
  int lowestIdx = 0;
  int lowestPri = severityPriority(alertQueue[0].severity);
  unsigned long oldestTime = alertQueue[0].timestamp;
  for (int i = 1; i < MAX_ALERTS; i++) {
    int pri = severityPriority(alertQueue[i].severity);
    if (pri < lowestPri || (pri == lowestPri && alertQueue[i].timestamp > oldestTime)) {
      lowestPri = pri;
      lowestIdx = i;
      oldestTime = alertQueue[i].timestamp;
    }
  }
  return lowestIdx;
}

// Deactivate an alert by index
void deactivateAlert(int idx) {
  if (idx >= 0 && idx < MAX_ALERTS && alertQueue[idx].active) {
    alertQueue[idx].active = false;
    alertQueue[idx].severity = "";
    activeAlertCount--;
  }
}

// Deactivate the top-priority alert (button press)
void deactivateTopAlert() {
  AlertState* top = getTopAlert();
  if (top) {
    int idx = top - alertQueue;
    deactivateAlert(idx);
  }
}

// Clear all alerts
void clearAllAlerts() {
  for (int i = 0; i < MAX_ALERTS; i++) {
    alertQueue[i].active = false;
    alertQueue[i].severity = "";
  }
  activeAlertCount = 0;
}

StaticJsonDocument<512> jsonBuffer;

unsigned long lastBlink = 0;
bool ledState = false;

// ===== RGB LED Helpers (Active LOW) =====
void setLED(int r, int g, int b) {
  digitalWrite(red_light_pin, r);
  digitalWrite(green_light_pin, g);
  digitalWrite(blue_light_pin, b);
}

void setLEDOff() {
  setLED(HIGH, HIGH, HIGH);  // All off
}

// Blue / light blue LED (startup)
void setBlueLED() {
  setLED(HIGH, HIGH, LOW);   // Blue ON (active LOW)
}

// Green LED (normal / acknowledged)
void setGreenLED() {
  setLED(HIGH, LOW, HIGH);   // Green ON (active LOW)
}

// Red LED (critical blink ON phase)
void setRedLED() {
  setLED(LOW, HIGH, HIGH);   // Red ON (active LOW)
}

// Yellow / Amber LED (warning blink ON phase: red+green)
void setYellowLED() {
  setLED(LOW, LOW, HIGH);    // Amber (Red+Green) ON (active LOW)
}

// ===== LED Matrix Display =====
void displayChar(char c) {
  ledMatrix.setText(String(c));
  ledMatrix.clear();
  ledMatrix.drawText();
  ledMatrix.commit();
}

// Blank the matrix (nothing displayed) - normal / acknowledged state
void displayBlank() {
  ledMatrix.clear();
  ledMatrix.commit();
}

// Refresh the display and LEDs based on current top alert
void refreshOutput() {
  AlertState* top = getTopAlert();
  if (top) {
    if (top->severity == "critical") {
      currentMode = MODE_CRITICAL;
      setRedLED();             // Will blink in loop()
      displayChar('C');
    } else if (top->severity == "warning") {
      currentMode = MODE_WARNING;
      setYellowLED();          // Will blink in loop()
      displayChar('W');
    } else {
      currentMode = MODE_NORMAL;
      setGreenLED();
      displayBlank();
    }
  } else {
    // No active alerts -> normal state: green LED, blank matrix
    currentMode = MODE_NORMAL;
    setGreenLED();
    displayBlank();
  }
}

// Set device to normal (green LED, blank matrix) - called on acknowledge
// Clears the alert queue so loop() blink logic doesn't override
void setNormalState() {
  clearAllAlerts();
  currentMode = MODE_NORMAL;
  setGreenLED();
  displayBlank();
  Serial.println("-> Normal mode (green LED, blank matrix)");
}

// ===== WiFi Setup =====
void setup_wifi() {
  WiFi.disconnect();
  delay(100);

  Serial.printf("\nConnecting to %s\n", ssid);
  WiFi.begin(ssid, password);

  unsigned long currentTime = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    // Blink blue during WiFi connect
    digitalWrite(blue_light_pin, digitalRead(blue_light_pin) ^ 1);
    if (millis() - currentTime > 30000UL) {
      ESP.restart();
    }
  }

  Serial.printf("\nWiFi connected\n");

  ipAddress = WiFi.localIP().toString();
  Serial.printf("IP address: %s\n", ipAddress.c_str());
  macAddr = WiFi.macAddress();
  Serial.printf("MAC address: %s\n", macAddr.c_str());
}

// ===== Handle iot/device/led MQTT message =====
// Expected: {"color": "green|red|yellow|blue", "blink": true|false, "node_id": "...", "room": "..."}
void handleDeviceLedMessage(const char* payload, unsigned int length) {
  recMsg = "";
  for (unsigned int i = 0; i < length; i++) {
    recMsg += (char)payload[i];
  }

  DeserializationError error = deserializeJson(jsonBuffer, recMsg);
  if (error) {
    Serial.print(F("LED JSON parse failed: "));
    Serial.println(error.c_str());
    return;
  }

  String color = jsonBuffer["color"].as<String>();
  bool blink = jsonBuffer["blink"].as<bool>();

  Serial.printf("DEVICE LED: color=%s blink=%s\n", color.c_str(), blink ? "true" : "false");

  if (color == "green") {
    // Green = normal / acknowledged
    setNormalState();
  } else if (color == "red") {
    currentMode = MODE_CRITICAL;
    setRedLED();
    displayChar('C');
  } else if (color == "yellow") {
    currentMode = MODE_WARNING;
    setYellowLED();
    displayChar('W');
  } else if (color == "blue") {
    currentMode = MODE_STARTUP;
    setBlueLED();
  }

  jsonBuffer.clear();
}

// ===== Handle iot/device/matrix MQTT message =====
// Expected: {"letter": "S|C|W|", "node_id": "...", "room": "..."}
void handleDeviceMatrixMessage(const char* payload, unsigned int length) {
  recMsg = "";
  for (unsigned int i = 0; i < length; i++) {
    recMsg += (char)payload[i];
  }

  DeserializationError error = deserializeJson(jsonBuffer, recMsg);
  if (error) {
    Serial.print(F("Matrix JSON parse failed: "));
    Serial.println(error.c_str());
    return;
  }

  String letter = jsonBuffer["letter"].as<String>();

  Serial.printf("DEVICE MATRIX: letter='%s'\n", letter.c_str());

  if (letter.length() == 0) {
    displayBlank();
  } else if (letter == "S") {
    displayChar('S');
  } else if (letter == "C") {
    displayChar('C');
  } else if (letter == "W") {
    displayChar('W');
  } else {
    displayChar(letter.charAt(0));
  }

  jsonBuffer.clear();
}

// ===== MQTT Callback - receive alerts + device control =====
void callback(char* topic, byte* payload, unsigned int length) {
  String topicStr = String(topic);

  // -- Handle device LED control messages --
  if (topicStr == "iot/device/led") {
    handleDeviceLedMessage((const char*)payload, length);
    return;
  }

  // -- Handle device matrix control messages --
  if (topicStr == "iot/device/matrix") {
    handleDeviceMatrixMessage((const char*)payload, length);
    return;
  }

  recMsg = "";
  for (unsigned int i = 0; i < length; i++) {
    recMsg += (char)payload[i];
  }

  // -- Handle clear messages (iot/alerts/clear or iot/alerts/clear/{location}) --
  if (topicStr.startsWith("iot/alerts/clear")) {
    String clearLoc = topicStr.substring(17);  // after "iot/alerts/clear"
    if (clearLoc.startsWith("/")) clearLoc = clearLoc.substring(1);

    if (clearLoc.length() == 0) {
      Serial.println("All alerts cleared via MQTT");
      clearAllAlerts();
    } else {
      for (int i = 0; i < MAX_ALERTS; i++) {
        if (alertQueue[i].active && alertQueue[i].location == clearLoc) {
          Serial.printf("Alert cleared for %s via MQTT\n", clearLoc.c_str());
          deactivateAlert(i);
        }
      }
    }
    refreshOutput();
    return;
  }

  // -- Check if this is an alert topic --
  if (!topicStr.startsWith("iot/alerts")) {
    DeserializationError error = deserializeJson(jsonBuffer, recMsg);
    if (error) return;
    jsonBuffer.clear();
    return;
  }

  // -- Parse alert JSON --
  DeserializationError error = deserializeJson(jsonBuffer, recMsg);
  if (error) {
    Serial.print(F("JSON parse failed: "));
    Serial.println(error.c_str());
    return;
  }

  String nodeId    = jsonBuffer["node_id"].as<String>();
  String location  = jsonBuffer["location"].as<String>();
  String parameter = jsonBuffer["parameter"].as<String>();
  String severity  = jsonBuffer["severity"].as<String>();
  float value      = jsonBuffer["value"].as<float>();

  Serial.printf("ALERT: [%s] %s @ %s | %s=%.1f\n",
    severity.c_str(), nodeId.c_str(), location.c_str(),
    parameter.c_str(), value);

  // Store alert in queue
  int slot = findAlertSlot(parameter, location);
  alertQueue[slot].nodeId = nodeId;
  alertQueue[slot].location = location;
  alertQueue[slot].parameter = parameter;
  alertQueue[slot].severity = severity;
  alertQueue[slot].value = value;
  alertQueue[slot].timestamp = millis();
  if (!alertQueue[slot].active) {
    alertQueue[slot].active = true;
    activeAlertCount++;
  }

  // Refresh display with top-priority alert
  refreshOutput();

  // Publish acknowledgement back
  StaticJsonDocument<256> ack;
  ack["tag"] = macAddr;
  ack["action"] = "alert_received";
  ack["severity"] = severity;
  ack["node_id"] = nodeId;
  ack["parameter"] = parameter;
  ack["location"] = location;
  serializeJson(ack, msg);
  client.publish(mqttTopic_TX, msg);

  jsonBuffer.clear();
}

// ===== MQTT Reconnect with Exponential Backoff =====
void reconnect() {
  while (!client.connected()) {
    Serial.printf("Attempting MQTT connection (attempt %d, delay %lus)...",
      reconnect_count + 1, reconnect_delay / 1000);
    if (client.connect(macAddr.c_str())) {
      Serial.println("Connected");
      snprintf(msg, 75, "IoT Tag (%s) is READY", ipAddress.c_str());
      Serial.println(msg);
      client.subscribe(mqttTopic_RX);
      client.subscribe(mqttTopic_Alert);
      client.subscribe(mqttTopic_Led);      // Subscribe to LED control
      client.subscribe(mqttTopic_Matrix);   // Subscribe to matrix control
      client.publish(mqttTopic_TX, msg);
      reconnect_count = 0;
      reconnect_delay = 5000;
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again...");
      reconnect_count++;
      if (reconnect_count >= max_reconnect_attempts) {
        Serial.println("Max reconnect attempts reached, restarting...");
        ESP.restart();
      }
      delay(reconnect_delay);
      reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay);
    }
  }
}

// ===== Button Handler =====
void buttonChanged(int state) {
  if (state == LOW) {  // Button pressed (active LOW with pull-up)
    buttonPressed = true;
  }
}

// ===== SETUP =====
void setup() {
  pinMode(TRIG, INPUT_PULLUP);
  pinMode(red_light_pin, OUTPUT);
  pinMode(green_light_pin, OUTPUT);
  pinMode(blue_light_pin, OUTPUT);

  // All LEDs OFF (active LOW)
  setLEDOff();

  // Initialize alert queue
  clearAllAlerts();

  // Set startup mode
  currentMode = MODE_STARTUP;

  Serial.begin(115200);
  Serial.println("TM1118 Smart Campus IoT Status Tag");

  // LED Matrix init
  ledMatrix.init();
  ledMatrix.setIntensity(4);
  ledMatrix.setTextAlignment(TEXT_ALIGN_LEFT);

  // -- Startup: show "S" on matrix with blue LED --
  displayChar('S');
  setBlueLED();

  client.setCallback(callback);
  trigger.setCallback(buttonChanged);

  // Connect WiFi (blue LED stays on during this)
  setup_wifi();

  // -- After WiFi connected: transition to normal state --
  // Green LED on, matrix blank (nothing displayed)
  setNormalState();
  Serial.println("Startup complete -> Normal mode (green LED, blank matrix)");

  client.setServer(mqtt_server, 1883);
}

// ===== LOOP =====
void loop() {
  trigger.update();

  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  // -- Button press: acknowledge/clear top alert --
  if (buttonPressed) {
    buttonPressed = false;

    AlertState* top = getTopAlert();
    if (top) {
      Serial.printf("Alert acknowledged: [%s] %s @ %s\n",
        top->severity.c_str(), top->parameter.c_str(), top->location.c_str());

      // Publish acknowledgement back to server
      StaticJsonDocument<256> ack;
      ack["tag"] = macAddr;
      ack["action"] = "acknowledged";
      ack["node_id"] = top->nodeId;
      ack["parameter"] = top->parameter;
      ack["location"] = top->location;
      ack["severity"] = top->severity;
      serializeJson(ack, msg);
      client.publish(mqttTopic_TX, msg);

      // Deactivate this alert
      int idx = top - alertQueue;
      deactivateAlert(idx);

      // After acknowledge: green LED on, matrix blank
      setNormalState();
      Serial.println("Acknowledge -> Normal mode (green LED, blank matrix)");
    } else {
      // No alert -> just show normal state briefly
      setNormalState();
    }
    delay(100);
  }

  // -- Blink LED for critical (red) and warning (yellow) alerts --
  AlertState* top = getTopAlert();
  if (top) {
    unsigned long now = millis();
    if (now - lastBlink > 500UL) {
      ledState = !ledState;
      if (ledState) {
        if (top->severity == "critical") {
          setRedLED();
        } else if (top->severity == "warning") {
          setYellowLED();
        }
      } else {
        setLEDOff();
      }
      lastBlink = now;
    }
  }

  // -- Auto-clear alerts after 5 minutes --
  for (int i = 0; i < MAX_ALERTS; i++) {
    if (alertQueue[i].active && (millis() - alertQueue[i].timestamp > 300000UL)) {
      Serial.printf("Alert auto-expired: %s @ %s\n",
        alertQueue[i].parameter.c_str(), alertQueue[i].location.c_str());
      deactivateAlert(i);
      refreshOutput();
    }
  }

  delay(10);
}
