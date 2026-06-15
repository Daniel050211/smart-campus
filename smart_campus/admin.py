from django.contrib import admin

from .models import SensorReading, VenueEvent, Alert


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "node_id", "loc", "temp", "hum", "light", "snd")
    list_filter = ("loc", "node_id")
    search_fields = ("node_id", "loc")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    readonly_fields = ("node_id", "loc", "temp", "hum", "light", "snd", "timestamp")


@admin.register(VenueEvent)
class VenueEventAdmin(admin.ModelAdmin):
    list_display = ("event_title", "venue", "event_date", "start_time", "end_time")
    list_filter = ("venue", "event_date")
    search_fields = ("event_title", "venue", "notes")
    date_hierarchy = "event_date"
    ordering = ("-event_date", "start_time")
    fieldsets = (
        (None, {
            "fields": ("venue", "event_title", "event_date", "start_time", "end_time", "notes")
        }),
    )


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("triggered_at", "severity", "alert_type", "room", "status")
    list_filter = ("severity", "status", "room")
    search_fields = ("message", "room", "alert_type")
    date_hierarchy = "triggered_at"
    ordering = ("-triggered_at",)
    readonly_fields = ("triggered_at",)
