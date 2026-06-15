from django.db import models


class SensorReading(models.Model):
    VENUE_CHOICES = [
        ("W311A", "W311A"),
        ("W311D-Z1", "W311D-Z1"),
        ("W311D-Z2", "W311D-Z2"),
        ("W311-H1", "W311-H1"),
        ("W311-H2", "W311-H2"),
        ("W311-H3", "W311-H3"),
    ]

    node_id = models.CharField(max_length=10, db_index=True)
    loc = models.CharField(max_length=10, choices=VENUE_CHOICES, db_index=True)
    temp = models.FloatField()
    hum = models.FloatField()
    light = models.FloatField()
    snd = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["loc", "-timestamp"]),
            models.Index(fields=["node_id", "-timestamp"]),
        ]

    def __str__(self):
        return f"{self.node_id} @ {self.loc} [{self.timestamp:%Y-%m-%d %H:%M}]"


class VenueEvent(models.Model):
    VENUE_CHOICES = [
        ("W311A", "W311A Computer Room"),
        ("W311D-Z1", "W311D Z1"),
        ("W311D-Z2", "W311D Z2"),
        ("W311-H1", "W311 H1"),
        ("W311-H2", "W311 H2"),
        ("W311-H3", "W311 H3"),
    ]

    venue = models.CharField(max_length=10, choices=VENUE_CHOICES, db_index=True)
    event_date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    event_title = models.CharField(max_length=200)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["event_date", "start_time"]
        indexes = [
            models.Index(fields=["venue", "event_date"]),
        ]

    def __str__(self):
        return f"{self.event_title} @ {self.venue} [{self.event_date}]"


class Alert(models.Model):
    SEVERITY_CHOICES = [
        ("warning", "Warning"),
        ("critical", "Critical"),
    ]
    STATUS_CHOICES = [
        ("active", "Active"),
        ("acknowledged", "Acknowledged"),
    ]

    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="warning")
    alert_type = models.CharField(max_length=50)
    room = models.CharField(max_length=10, db_index=True)
    message = models.TextField()
    triggered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="active")
    reading = models.ForeignKey(
        SensorReading, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-triggered_at"]

    def __str__(self):
        return f"[{self.severity}] {self.alert_type} @ {self.room}"
