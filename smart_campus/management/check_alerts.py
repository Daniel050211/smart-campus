"""
Django management command to run the alert generation engine.
Evaluates the most recent sensor reading per node against IoT Workshop
    alert rules (R1-R8) and creates Alert records when thresholds are breached.
"""
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from smart_campus.models import SensorReading, Alert


class Command(BaseCommand):
    help = "Run the alert generation engine against recent sensor readings"

    def handle(self, *args, **options):
        now = timezone.now()
        cutoff = now - timedelta(minutes=10)

        recent = SensorReading.objects.filter(
            timestamp__gte=cutoff
        ).order_by("node_id", "loc", "-timestamp")

        # Keep only latest per node_id+loc
        seen = set()
        latest = []
        for r in recent:
            key = (r.node_id, r.loc)
            if key not in seen:
                seen.add(key)
                latest.append(r)

        alerts_created = 0
        for r in latest:
            alerts_created += self._check_intrusion(r)
            alerts_created += self._check_temperature(r)
            alerts_created += self._check_temperature_low(r)
            alerts_created += self._check_sound_spike(r)

        if alerts_created:
            self.stdout.write(f"Created {alerts_created} alert(s)")
        else:
            self.stdout.write("No new alerts generated")

    def _is_sustained(self, reading, threshold):
        cutoff = reading.timestamp - timedelta(minutes=settings.ALERT_SUSTAINED_MINUTES)
        recent = SensorReading.objects.filter(
            node_id=reading.node_id, loc=reading.loc,
            timestamp__gte=cutoff, timestamp__lte=reading.timestamp,
        )
        cnt = recent.count()
        if cnt < 2:
            return False
        return recent.filter(temp__gte=threshold).count() == cnt

    def _check_intrusion(self, reading):
        """Nighttime intrusion R1-R3 (Critical)."""
        now_local = reading.timestamp.astimezone(timezone.get_current_timezone())
        is_night = now_local.hour >= settings.ALERT_NIGHT_START or now_local.hour < settings.ALERT_NIGHT_END
        if not is_night:
            return 0

        node_id = reading.node_id
        intrusion = False
        msg = ""

        if node_id in ("A01", "A02", "A04", "A06"):
            intrusion = (reading.snd >= settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD
                         and reading.light >= settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_DEFAULT)
            if intrusion:
                msg = (f"Night intrusion at {reading.loc}: sound={reading.snd}dB "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD}dB), "
                       f"light={reading.light}% "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_DEFAULT}%)")
        elif node_id == "A03":
            intrusion = (reading.snd >= settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD
                         and reading.light >= settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_A03)
            if intrusion:
                msg = (f"Night intrusion at {reading.loc}: sound={reading.snd}dB "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD}dB), "
                       f"light={reading.light}% "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_LIGHT_THRESHOLD_A03}%)")
        elif node_id == "A05":
            intrusion = reading.snd >= settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD_A05
            if intrusion:
                msg = (f"Night intrusion at {reading.loc}: sound={reading.snd}dB "
                       f"(>= {settings.ALERT_NIGHT_INTRUSION_SOUND_THRESHOLD_A05}dB)")

        if intrusion:
            if not Alert.objects.filter(
                room=reading.loc, alert_type="Night Intrusion", status="active"
            ).exists():
                Alert.objects.create(
                    severity="critical",
                    alert_type="Night Intrusion",
                    room=reading.loc,
                    message=msg,
                    reading=reading,
                )
                return 1
        return 0

    def _check_temperature_low(self, reading):
        """Low temperature warning (below 10C)."""
        if reading.temp < settings.ALERT_TEMP_LOW:
            if not Alert.objects.filter(
                room=reading.loc, alert_type="Low Temperature", severity="warning", status="active"
            ).exists():
                Alert.objects.create(
                    severity="warning",
                    alert_type="Low Temperature",
                    room=reading.loc,
                    message=(f"Warning: low temperature at {reading.loc} ({reading.temp}\u00b0C "
                             f"< {settings.ALERT_TEMP_LOW}\u00b0C)"),
                    reading=reading,
                )
                return 1
        return 0

    def _check_sound_spike(self, reading):
        """Sound spike R8 (Critical) - any time."""
        if reading.snd >= settings.ALERT_SOUND_SPIKE_THRESHOLD:
            if not Alert.objects.filter(
                room=reading.loc, alert_type="Sound Spike", severity="critical", status="active"
            ).exists():
                Alert.objects.create(
                    severity="critical",
                    alert_type="Sound Spike",
                    room=reading.loc,
                    message=(f"Critical: sound spike at {reading.loc} ({reading.snd}dB "
                             f">= {settings.ALERT_SOUND_SPIKE_THRESHOLD}dB)"),
                    reading=reading,
                )
                return 1
        return 0

    def _check_temperature(self, reading):
        """Temperature alerts R4, R6 (Warning sustained only)."""
        node_id = reading.node_id

        if node_id == "A04":
            warn_t = settings.ALERT_TEMP_WARNING_A04
        else:
            warn_t = settings.ALERT_TEMP_WARNING_DEFAULT

        if reading.temp >= warn_t:
            existing = Alert.objects.filter(
                room=reading.loc, alert_type="High Temperature", severity="warning", status="active"
            ).exists()
            if not existing and self._is_sustained(reading, warn_t):
                Alert.objects.create(
                    severity="warning",
                    alert_type="High Temperature",
                    room=reading.loc,
                    message=(f"Warning: temperature at {reading.loc} sustained at {reading.temp}\u00b0C "
                             f"(>= {warn_t}\u00b0C for {settings.ALERT_SUSTAINED_MINUTES} min)"),
                    reading=reading,
                )
                return 1
        return 0
