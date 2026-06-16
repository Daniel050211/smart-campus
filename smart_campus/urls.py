from django.contrib import admin
from django.urls import path

from . import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.overview_view, name="overview"),
    path("readings/", views.reading_list_view, name="readings"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("events/", views.event_list_view, name="events"),
    path("analysis/time-event/", views.time_event_analysis_view, name="time_event"),
    path("alerts/", views.alert_list_view, name="alerts"),
    # API endpoints
    path("api/readings/", views.api_readings_json, name="api_readings"),
    path("api/latest-summary/", views.api_latest_summary, name="api_latest_summary"),
    path("api/alerts/<int:alert_id>/acknowledge/", views.api_alert_acknowledge, name="api_alert_acknowledge"),
    path("api/alert-stats/", views.api_alert_stats, name="api_alert_stats"),
    path("api/historical-summary/", views.api_historical_summary, name="api_historical_summary"),
    path("api/chat/", views.api_chat, name="api_chat"),
    path("export/readings/csv/", views.export_readings_csv, name="export_readings_csv"),
]
