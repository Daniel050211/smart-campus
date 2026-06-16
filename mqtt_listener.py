"""
MQTT listener daemon for collecting sensor data published by Raspberry Pi nodes.
Subscribes to iot/sensor-A (shared topic) and stores readings in the Django database.

Usage:
    python mqtt_listener.py

This script is designed to run as a long-lived background process alongside
the Django web server. It requires paho-mqtt (pip install paho-mqtt).
"""
import os
import sys
import json
import time
from datetime import timedelta

# Set up Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smart_campus.settings")

import django
django.setup()

import paho.mqtt.client as mqtt
from django.conf import settings
from django.utils import timezone
from smart_campus.models import SensorReading, Alert
from django.db.utils import OperationalError

LOCATION_MAP = {
    "W311A": "W311A",
    "W311D-Z1": "W311D-Z1",
    "W311D-Z2": "W311D-Z2",
    "W311-H1": "W311-H1",
    "W311-H2": "W311-H2",
    "W311-H3": "W311-H3",
}
VALID_LOCS = set(LOCATION_MAP.values())
VALID_NODES = set(settings.MQTT_TEAM_NODES)


def publish_alert_to_mqtt(client, alert):
    """Publish alert JSON to iot/alerts/{type} for hardware IoT tags."""
    try:
        payload = json.dumps({
            "node_id": alert.reading.node_id if alert.reading else "",
            "location": alert.room,
            "parameter": alert.alert_type,
            "severity": alert.severity,
            "value": alert.reading.temp if alert.reading and "temp" in alert.alert_type else 0,
            "message": alert.message,
        })
        client.publish("iot/alerts/" + alert.alert_type, payload, qos=1)
        print(f"  Published alert to MQTT: iot/alerts/{alert.alert_type}")
    except Exception as e:
        print(f"  Failed to publish alert to MQTT: {e}")

def _db_retry(fn, max_retries=3, delay=1):
    """Retry a DB operation on OperationalError with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return fn()
        except OperationalError as e:
            if attempt < max_retries - 1:
                print(f"  DB retry {attempt + 1}/{max_retries}: {e}")
                time.sleep(delay * (2 ** attempt))
            else:
                raise

def check_and_create_alerts(reading, node_id, mqtt_client=None):
    """Create alerts based on IoT Workshop Alert System rules (R1-R7).

    R1 (Critical): Night + sound>=28dB + light>=70%   -> A01, A02, A04, A06
    R2 (Critical): Night + sound>=28dB + light>=75%   -> A03
    R3 (Critical): Night + sound>=30dB (no light)     -> A05
    R4 (Warning):  Temp >=35C sustained 5 min         -> A01-A03, A05, A06
    R5 (Critical): Temp >=30C immediate               -> A01-A03, A05, A06
    R6 (Warning):  Temp >=36C sustained 5 min         -> A04
    R7 (Critical): Temp >=31C immediate               -> A04
    """
    now_local = timezone.localtime()
    is_night = now_local.hour >= settings.ALERT_NIGHT_START or now_local.hour < settings.ALERT_NIGHT_END

    def _sustained(threshold):
        # If temp is extremely high (>= threshold + 5C), alert immediately
        if reading.temp >= threshold + 5:
            return True
        cutoff = reading.timestamp - timedelta(minutes=settings.ALERT_SUSTAINED_MINUTES)
        recent = SensorReading.objects.filter(
            node_id=node_id, loc=reading.loc,
            timestamp__gte=cutoff, timestamp__lte=reading.timestamp,
        )
        cnt = recent.count()
        if cnt < 2:
            return False
        return recent.filter(temp__gte=threshold).count() == cnt

    alerts_created = 0

    # ===== NIGHTTIME INTRUSION (Critical) - R1, R2, R3 =====
    if is_night:
        intrusion = False
        msg = ""

        # R1: A01, A02, A04, A06 (sound>=28dB AND light>=70%)
        if node_id in ("A01", "A02", "A04", "A06"):
            intrusion = (reading.snd >= settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD
                         and reading.light >= settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_DEFAULT)
            if intrusion:
                msg = (f"Night intrusion at {reading.loc}: sound={reading.snd}dB "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD}dB), "
                       f"light={reading.light}% "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_DEFAULT}%)")

        # R2: A03 (sound>=28dB AND light>=75%)
        elif node_id == "A03":
            intrusion = (reading.snd >= settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD
                         and reading.light >= settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_A03)
            if intrusion:
                msg = (f"Night intrusion at {reading.loc}: sound={reading.snd}dB "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD}dB), "
                       f"light={reading.light}% "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_A03}%)")

        # R3: A05 (sound>=30dB, no light condition)
        elif node_id == "A05":
            intrusion = reading.snd >= settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD_A05
            if intrusion:
                msg = (f"Night intrusion at {reading.loc}: sound={reading.snd}dB "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD_A05}dB)")

        if intrusion:
            if not Alert.objects.filter(
                room=reading.loc, alert_type="Night Intrusion", status="active"
            ).exists():
                a = Alert.objects.create(
                    severity="critical",
                    alert_type="Night Intrusion",
                    room=reading.loc,
                    message=msg,
                    reading=reading,
                )
                print(f"  ALERT CRITICAL: {msg}")
                if mqtt_client:
                    publish_alert_to_mqtt(mqtt_client, a)
                alerts_created += 1

    # ===== TEMPERATURE ALERTS - R4, R6 (Warning sustained) =====
    if node_id == "A04":
        warn_t = settings.ALERT_TEMP_WARNING_A04        # R6: 36C sustained
    else:
        warn_t = settings.ALERT_TEMP_WARNING_DEFAULT    # R4: 35C sustained

    if reading.temp >= warn_t:
        existing = Alert.objects.filter(
            room=reading.loc, alert_type="High Temperature", severity="warning", status="active"
        ).exists()
        if not existing:
            a = Alert.objects.create(
                severity="warning",
                alert_type="High Temperature",
                room=reading.loc,
                message=(f"Warning: temperature at {reading.loc} sustained at {reading.temp}\u00b0C "
                         f"(>= {warn_t}\u00b0C for {settings.ALERT_SUSTAINED_MINUTES} min)"),
                reading=reading,
            )
            print(f"  ALERT WARNING: Sustained high temp at {reading.loc} ({reading.temp}\u00b0C)")
            if mqtt_client:
                publish_alert_to_mqtt(mqtt_client, a)
            alerts_created += 1

    # ===== LOW TEMPERATURE (Warning) =====
    if reading.temp < settings.ALERT_TEMP_LOW:
        if not Alert.objects.filter(
            room=reading.loc, alert_type="Low Temperature", severity="warning", status="active"
        ).exists():
            a = Alert.objects.create(
                severity="warning",
                alert_type="Low Temperature",
                room=reading.loc,
                message=(f"Warning: low temperature at {reading.loc} ({reading.temp}\u00b0C "
                         f"< {settings.ALERT_TEMP_LOW}\u00b0C)"),
                reading=reading,
            )
            print(f"  ALERT WARNING: Low temp at {reading.loc} ({reading.temp}\u00b0C)")
            if mqtt_client:
                publish_alert_to_mqtt(mqtt_client, a)
            alerts_created += 1

    # ===== SOUND SPIKE (Critical) - R8 =====
    if reading.snd >= settings.ALERT_SOUND_SPIKE_THRESHOLD:
        if not Alert.objects.filter(
            room=reading.loc, alert_type="Sound Spike", severity="critical", status="active"
        ).exists():
            a = Alert.objects.create(
                severity="critical",
                alert_type="Sound Spike",
                room=reading.loc,
                message=(f"Critical: sound spike at {reading.loc} ({reading.snd}dB "
                         f">= {settings.ALERT_SOUND_SPIKE_THRESHOLD}dB)"),
                reading=reading,
            )
            print(f"  ALERT CRITICAL: Sound spike at {reading.loc} ({reading.snd}dB)")
            if mqtt_client:
                publish_alert_to_mqtt(mqtt_client, a)
            alerts_created += 1

    return alerts_created


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[{timezone.now():%Y-%m-%d %H:%M:%S}] Connected to MQTT broker")
        client.subscribe("iot/sensor-A")
        print("Subscribed to: iot/sensor-A (all teams)")
    else:
        print(f"Connection failed with code {rc}")


def on_message(client, userdata, msg):
    topic = msg.topic

    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Invalid JSON: {e}")
        return

    # Get node_id from payload first, then from topic as fallback
    node_id = payload.get("node_id", "").strip()
    if not node_id:
        node_id = topic.replace(settings.MQTT_TOPIC_PREFIX, "")

    if node_id not in VALID_NODES:
        print(f"Ignoring unknown node: {node_id} (topic: {topic})")
        return

    loc = payload.get("loc", "")
    if loc not in VALID_LOCS:
        print(f"Unknown location from {node_id}: {loc}")
        return

    try:
        reading = _db_retry(lambda: SensorReading.objects.create(
            node_id=node_id,
            loc=loc,
            temp=float(payload.get("temp", 0)),
            hum=float(payload.get("hum", 0)),
            light=float(payload.get("light", 0)),
            snd=float(payload.get("snd", 0)),
        ))
        print(f"[{reading.timestamp:%Y-%m-%d %H:%M:%S}] Stored: {node_id} @ {loc} "
              f"T:{reading.temp}\u00b0C H:{reading.hum}% L:{reading.light}% S:{reading.snd}dB")
        _db_retry(lambda: check_and_create_alerts(reading, node_id, client))
        # Publish reading via MQTT for ESP display (no firewall needed)
        try:
            reading_msg = json.dumps({
                "node_id": node_id, "loc": loc,
                "temp": reading.temp, "hum": reading.hum,
                "light": reading.light, "snd": reading.snd,
            })
            client.publish("iot/readings", reading_msg)
            print(f"  Published to iot/readings: {node_id} @ {loc}")
        except Exception as e:
            print(f"  Failed to publish readings MQTT: {e}")
    except (ValueError, TypeError) as e:
        print(f"Invalid sensor values from {node_id}: {e}")
    except OperationalError as e:
        print(f"Database error saving reading from {node_id}: {e}")


def main():
    import socket
    client = mqtt.Client(client_id=f"A02-{socket.gethostname()}")
    client.on_connect = on_connect
    client.on_message = on_message

    broker = settings.MQTT_BROKER
    port = settings.MQTT_PORT

    print(f"TM1118 Smart Campus MQTT Listener")
    print(f"Connecting to {broker}:{port}...")

    while True:
        try:
            client.connect(broker, port, keepalive=60)
            client.loop_forever()
        except Exception as e:
            print(f"MQTT error: {e}. Retrying in 30 seconds...")
            time.sleep(30)


if __name__ == "__main__":
    main()
