from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-tm1118-smart-campus-change-in-production"

DEBUG = True

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "smart_campus",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "smart_campus.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "smart_campus" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "smart_campus.context_processors.nav_context",
            ],
        },
    },
]

WSGI_APPLICATION = "smart_campus.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db2.sqlite3",
        "OPTIONS": {
            "timeout": 30,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-hk"
TIME_ZONE = "Asia/Hong_Kong"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "smart_campus" / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# MQTT configuration
MQTT_BROKER = "ia.ic.polyu.edu.hk"
MQTT_PORT = 1883
MQTT_TOPIC_PREFIX = "iot/sensor-"
MQTT_TEAM_NODES = ["A01", "A02", "A03", "A04", "A05", "A06"]

ALERT_NIGHT_START = 22
ALERT_NIGHT_END = 6

# == IoT Workshop Alert Thresholds ==

# --- Nighttime Intrusion (Critical) ---
# R1: A01, A02, A04, A06: sound >= 28dB AND light >= 70%
# R2: A03:                   sound >= 28dB AND light >= 75%
# R3: A05:                   sound >= 30dB (no light condition)
ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD = 28       # dB - R1, R2
ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD_A05 = 30    # dB - R3
ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_DEFAULT = 70  # % - R1 (A01, A02, A04, A06)
ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_A03 = 75       # % - R2 (A03 only)

# --- Temperature Alerts (Warning sustained only) ---
# R4: A01-A03, A05, A06: temp >= 28C sustained 5 min  => Warning
# R5 - removed
# R6: A04 only:          temp >= 29C sustained 5 min  => Warning
# R7 - removed
ALERT_TEMP_WARNING_DEFAULT = 28.0    # C - R4
# ALERT_TEMP_CRITICAL_DEFAULT = 30.0  - removed
ALERT_TEMP_WARNING_A04 = 29.0        # C - R6
# ALERT_TEMP_CRITICAL_A04 = 31.0     - removed
ALERT_TEMP_LOW = 10.0               # C - Low temp warning (below 10C)

# Sustained condition window (minutes)
ALERT_SUSTAINED_MINUTES = 5

# --- Sound Spike Alert (Critical) ---
# Any time: sound >= 80dB (glass break / shout / alarm)
ALERT_SOUND_SPIKE_THRESHOLD = 80
 
# === SQLite WAL mode for concurrent access ===
# WAL allows concurrent reads during writes, eliminating most
# "database is locked" errors when the MQTT listener and web
# server access the same db.sqlite3 file simultaneously.
from django.db.backends.signals import connection_created

def _activate_sqlite_wal(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")

connection_created.connect(_activate_sqlite_wal)
