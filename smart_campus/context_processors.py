# Available for custom template context processors
from .models import Alert

def nav_context(request):
    active_alerts_count = Alert.objects.filter(status="active").count()
    return {
        "venues": [
            ("W311A", "W311A Computer Room"),
            ("W311-H1", "W311 H1"),
            ("W311-H2", "W311 H2"),
            ("W311-H3", "W311 H3"),
            ("W311D-Z1", "W311D Z1"),
            ("W311D-Z2", "W311D Z2"),
        ],
        "nav_pages": [
            ("overview", "Overview", "/"),
            ("readings", "Sensor Data", "/readings/"),
            ("dashboard", "Dashboard", "/dashboard/"),
            ("events", "Venue Events", "/events/"),
            ("time_event", "Time-Event Analysis", "/analysis/time-event/"),
            ("alerts", "Alerts", "/alerts/"),
        ],
        "active_alerts_count": active_alerts_count,
    }
