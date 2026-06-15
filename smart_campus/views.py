import json
from datetime import datetime, timedelta, time
from django.db.models import Avg, Max, Min, Q, F, DateTimeField
from django.db.models.functions import TruncHour, TruncMinute
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.utils.timezone import now, make_aware
import csv

from .models import SensorReading, VenueEvent, Alert


# --- Reasonable bounds for sensor data ---
REASONABLE_BOUNDS = {
    "temp": (0, 60),
    "hum": (0, 100),
    "light": (0, 100),
    "snd": (0, 200),
}

def clean_readings(qs):
    """Filter out obviously corrupted sensor readings."""
    return qs.filter(
        temp__gte=0, temp__lte=60,
        hum__gte=0, hum__lte=100,
        light__gte=0, light__lte=100,
        snd__gte=-100, snd__lte=200,
    )


# --- Shared filter helpers ---

def parse_shared_filters(request):
    loc = request.GET.get("loc", "")
    node_id = request.GET.get("node_id", "")
    start = request.GET.get("start", "")
    end = request.GET.get("end", "")
    metric = request.GET.get("metric", "temp")
    return {
        "loc": loc,
        "node_id": node_id,
        "start": start,
        "end": end,
        "metric": metric,
    }


def apply_reading_filters(qs, filters):
    if filters["node_id"]:
        qs = qs.filter(node_id=filters["node_id"])
    if filters["loc"]:
        qs = qs.filter(loc=filters["loc"])
    if filters["start"]:
        try:
            qs = qs.filter(timestamp__gte=make_aware(datetime.fromisoformat(filters["start"])))
        except ValueError:
            pass
    if filters["end"]:
        try:
            qs = qs.filter(timestamp__lte=make_aware(datetime.fromisoformat(filters["end"])))
        except ValueError:
            pass
    return qs


def build_filter_query_string(filters, **overrides):
    params = {**filters}
    params.update(overrides)
    parts = []
    for k, v in params.items():
        if v:
            parts.append(f"{k}={v}")
    return "&".join(parts)


# --- Views ---

def overview_view(request):
    filters = parse_shared_filters(request)

    # Latest reading per venue from the last 15 minutes
    cutoff = now() - timedelta(minutes=15)
    latest_per_room = {}
    for code, _ in SensorReading.VENUE_CHOICES:
        latest = (
            clean_readings(SensorReading.objects.filter(loc=code, timestamp__gte=cutoff))
            .order_by("-timestamp")
            .first()
        )
        if latest:
            latest_per_room[code] = latest

    latest_all = clean_readings(SensorReading.objects.filter(timestamp__gte=cutoff))
    metrics_summary = {}
    metrics_delta = {}
    if latest_all.exists():
        current_avgs = latest_all.aggregate(
            temp=Avg("temp"), hum=Avg("hum"), light=Avg("light"), snd=Avg("snd")
        )
        metrics_summary = {
            "temp": round(current_avgs["temp"] or 0, 1),
            "hum": round(current_avgs["hum"] or 0, 1),
            "light": round(current_avgs["light"] or 0, 1),
            "snd": round(current_avgs["snd"] or 0, 1),
        }
        # Compare latest vs second-latest reading for arrows
        ordered = latest_all.order_by("-timestamp")
        all_list = list(ordered[:2])
        if len(all_list) == 2:
            for key in ("temp", "hum", "light", "snd"):
                cur = getattr(all_list[0], key) or 0
                prev = getattr(all_list[1], key) or 0
                if cur > prev:
                    metrics_delta[key] = "up"
                elif cur < prev:
                    metrics_delta[key] = "down"
                else:
                    metrics_delta[key] = ""

    # Cross-room temperature averages for bar chart
    cross_room_temp = []
    for code, label in SensorReading.VENUE_CHOICES:
        room_readings = clean_readings(SensorReading.objects.filter(
            loc=code, timestamp__gte=cutoff
        ))
        avg = room_readings.aggregate(temp=Avg("temp"))
        if avg["temp"] is not None:
            cross_room_temp.append({
                "loc": code,
                "label": label.replace("Computer Room", "").strip(),
                "temp": round(avg["temp"], 1),
            })

    # Venue data distribution (percentage of total readings per venue)
    venue_counts = {}
    total_readings = 0
    for code, label in SensorReading.VENUE_CHOICES:
        cnt = clean_readings(SensorReading.objects.filter(
            loc=code, timestamp__gte=cutoff
        )).count()
        venue_counts[code] = cnt
        total_readings += cnt
    venue_data_pct = []
    for code, label in SensorReading.VENUE_CHOICES:
        cnt = venue_counts.get(code, 0)
        if cnt > 0:
            venue_data_pct.append({
                "loc": code,
                "label": label.replace("Computer Room", "").strip(),
                "count": cnt,
                "pct": round(cnt / total_readings * 100, 1) if total_readings > 0 else 0,
            })

    # Active alerts

    active_alerts_count = Alert.objects.filter(status="active").count()
    recent_alerts = Alert.objects.filter(status="active").order_by("-triggered_at")[:5]

    # Last update time
    last_reading = clean_readings(SensorReading.objects.all()).order_by("-timestamp").first()
    last_update = last_reading.timestamp if last_reading else None

    context = {
        "filters": filters,
        "latest_per_room": latest_per_room,
        "metrics_summary": metrics_summary,
        "metrics_delta": metrics_delta,
        "active_alerts_count": active_alerts_count,
        "recent_alerts": recent_alerts,
        "last_update": last_update,
        "cross_room_temp": json.dumps(cross_room_temp),
        "venue_data_pct": json.dumps(venue_data_pct),
        "current_range": request.GET.get("range", "24"),
    }
    return render(request, "overview.html", context)


def reading_list_view(request):
    filters = parse_shared_filters(request)

    readings = clean_readings(SensorReading.objects.all())
    readings = apply_reading_filters(readings, filters)

    # Pagination
    page = int(request.GET.get("page", 1))
    per_page = 50
    total = readings.count()
    readings = readings[(page - 1) * per_page : page * per_page]
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Latest readings per venue (for real-time comfort score)
    latest_readings = []
    now_ts = now()
    cutoff_hr = now_ts - timedelta(hours=1)
    for code, label in SensorReading.VENUE_CHOICES:
        latest = clean_readings(SensorReading.objects.filter(
            loc=code, timestamp__gte=cutoff_hr
        )).order_by("-timestamp").first()
        if latest:
            latest_readings.append({
                "loc": latest.loc,
                "label": label.replace("Computer Room", "").strip(),
                "node_id": latest.node_id,
                "temp": latest.temp,
                "hum": latest.hum,
                "light": latest.light,
                "snd": latest.snd,
            })

    filter_qs = build_filter_query_string(filters)

    # Generate page range for pagination
    page_range = []
    if total_pages <= 7:
        page_range = list(range(1, total_pages + 1))
    else:
        page_range = [1]
        if page > 3:
            page_range.append(0)  # ellipsis marker
        start = max(2, page - 1)
        end = min(total_pages - 1, page + 1)
        for p in range(start, end + 1):
            page_range.append(p)
        if page < total_pages - 2:
            page_range.append(0)  # ellipsis marker
        page_range.append(total_pages)

    context = {
        "filters": filters,
        "readings": readings,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "filter_qs": filter_qs,
        "page_range": page_range,
    }
    return render(request, "sensor_data.html", context)


def dashboard_view(request):
    filters = parse_shared_filters(request)

    # Default to last 24 hours
    if not filters["start"]:
        start_dt = now() - timedelta(hours=24)
    else:
        try:
            start_dt = make_aware(datetime.fromisoformat(filters["start"]))
        except ValueError:
            start_dt = now() - timedelta(hours=24)

    if not filters["end"]:
        end_dt = now()
    else:
        try:
            end_dt = make_aware(datetime.fromisoformat(filters["end"]))
        except ValueError:
            end_dt = now()

    aggregation = request.GET.get("aggregation", "avg")
    # Trend data - single venue or all venues (aggregated)
    trend_data = []
    agg_func = Avg if aggregation == "avg" else Max if aggregation == "max" else Min
    if filters["loc"]:
        base = clean_readings(SensorReading.objects.filter(
            loc=filters["loc"], timestamp__gte=start_dt, timestamp__lte=end_dt
        ))
        if aggregation == "avg":
            # Raw readings sampled for performance
            readings = base.order_by("timestamp")
            total = readings.count()
            lst = list(readings)
            if total > 200:
                step = max(1, total // 200)
                lst = lst[::step]
            trend_data = [{"timestamp": r.timestamp.isoformat(), "temp": r.temp, "hum": r.hum, "light": r.light, "snd": r.snd} for r in lst]
        else:
            # Max/Min bucketed
            buckets = (base.annotate(bucket=TruncMinute("timestamp")).values("bucket").annotate(temp=agg_func("temp"), hum=agg_func("hum"), light=agg_func("light"), snd=agg_func("snd")).order_by("bucket"))
            trend_data = [{"timestamp": b["bucket"].isoformat(), "temp": round(b["temp"] or 0, 1), "hum": round(b["hum"] or 0, 1), "light": round(b["light"] or 0, 1), "snd": round(b["snd"] or 0, 1)} for b in buckets]
    else:
        # All venues: bucketed aggregation
        qs = clean_readings(SensorReading.objects.filter(
            timestamp__gte=start_dt, timestamp__lte=end_dt
        ))
        buckets = (qs.annotate(bucket=TruncMinute("timestamp")).values("bucket").annotate(temp=agg_func("temp"), hum=agg_func("hum"), light=agg_func("light"), snd=agg_func("snd")).order_by("bucket"))
        trend_data = [{"timestamp": b["bucket"].isoformat(), "temp": round(b["temp"] or 0, 1), "hum": round(b["hum"] or 0, 1), "light": round(b["light"] or 0, 1), "snd": round(b["snd"] or 0, 1)} for b in buckets]

    # Cross-room comparison (averages in the period)
    cross_room = []
    aggregation = request.GET.get("aggregation", "avg")
    agg_func = Avg if aggregation == "avg" else Max if aggregation == "max" else Min
    for code, label in SensorReading.VENUE_CHOICES:
        room_readings = clean_readings(SensorReading.objects.filter(
            loc=code, timestamp__gte=start_dt, timestamp__lte=end_dt
        ))
        if room_readings.exists():
            aggs = room_readings.aggregate(
                temp=agg_func("temp"),
                hum=agg_func("hum"),
                light=agg_func("light"),
                snd=agg_func("snd"),
            )
            cross_room.append({
                "loc": code,
                "label": label,
                "temp": round(aggs["temp"] or 0, 1),
                "hum": round(aggs["hum"] or 0, 1),
                "light": round(aggs["light"] or 0, 1),
                "snd": round(aggs["snd"] or 0, 1),
            })

    # Latest readings per venue (for real-time comfort score)
    latest_readings = []
    now_ts = now()
    cutoff_hr = now_ts - timedelta(hours=1)
    for code, label in SensorReading.VENUE_CHOICES:
        latest = clean_readings(SensorReading.objects.filter(
            loc=code, timestamp__gte=cutoff_hr
        )).order_by("-timestamp").first()
        if latest:
            latest_readings.append({
                "loc": latest.loc,
                "label": label.replace("Computer Room", "").strip(),
                "node_id": latest.node_id,
                "temp": latest.temp,
                "hum": latest.hum,
                "light": latest.light,
                "snd": latest.snd,
            })

    filter_qs = build_filter_query_string(filters)

    context = {
        "filters": filters,
        "trend_data": json.dumps(trend_data),
        "cross_room": json.dumps(cross_room),
        "filter_qs": filter_qs,
        "aggregation": aggregation,
        "latest_readings": json.dumps(latest_readings),
        "selected_venue": next((v for v in cross_room if v["loc"] == filters["loc"]), None) if filters["loc"] else None,
    }
    return render(request, "dashboard.html", context)


def event_list_view(request):
    venue = request.GET.get("venue", "")
    start = request.GET.get("start", "")      # CHANGED: was "event_date"
    end = request.GET.get("end", "")           # NEW

    events = VenueEvent.objects.all()
    if venue:
        events = events.filter(venue=venue)

    # CHANGED: was just events.filter(event_date=event_date)
    # NOW: overlap-based time filtering
    # An event overlaps with [filter_start, filter_end] if:
    #   event_start < filter_end AND event_end > filter_start
    if start and end:
        try:
            start_dt = make_aware(datetime.fromisoformat(start))
            end_dt = make_aware(datetime.fromisoformat(end))
            # First narrow by date range at DB level for performance
            events = events.filter(
                event_date__gte=start_dt.date(),
                event_date__lte=end_dt.date(),
            )
            # Then check actual time overlap
            filtered = []
            for ev in events:
                ev_start = make_aware(datetime.combine(ev.event_date, ev.start_time))
                ev_end = make_aware(datetime.combine(ev.event_date, ev.end_time))
                if ev_start < end_dt and ev_end > start_dt:
                    filtered.append(ev)
            events = filtered
        except ValueError:
            pass
    elif start:
        try:
            start_dt = make_aware(datetime.fromisoformat(start))
            events = events.filter(event_date__gte=start_dt.date())
        except ValueError:
            pass
    elif end:
        try:
            end_dt = make_aware(datetime.fromisoformat(end))
            events = events.filter(event_date__lte=end_dt.date())
        except ValueError:
            pass

    context = {
        "events": events,
        "venue_filter": venue,
        "start_filter": start,    # CHANGED: was "event_date_filter": event_date
        "end_filter": end,        # NEW
    }
    return render(request, "venue_events.html", context)


def time_event_analysis_view(request):
    filters = parse_shared_filters(request)

    venue = filters["loc"]
    start = filters["start"]
    end = filters["end"]

    matching_events = []
    avg_readings = {}
    readings_for_period = []

    if venue:
        try:
            start_dt = make_aware(datetime.fromisoformat(start)) if start else now() - timedelta(hours=24)
            end_dt = make_aware(datetime.fromisoformat(end)) if end else now()
        except ValueError:
            start_dt = now() - timedelta(hours=24)
            end_dt = now()

        # Find matching events (time overlap check)
        from django.db.models import Q
        ev_start = start_dt.time()
        ev_end = end_dt.time()
        if start_dt.date() == end_dt.date():
            # Same day: exact time overlap
            matching_events = VenueEvent.objects.filter(
                venue=venue, event_date=start_dt.date(),
                start_time__lt=ev_end, end_time__gt=ev_start
            )
        else:
            # Multi-day: events on first/last day with time overlap, all events on middle days
            matching_events = VenueEvent.objects.filter(venue=venue).filter(
                Q(event_date=start_dt.date(), end_time__gt=ev_start) |
                Q(event_date=end_dt.date(), start_time__lt=ev_end) |
                Q(event_date__gt=start_dt.date(), event_date__lt=end_dt.date())
            )

        # Average environmental data
        readings = clean_readings(SensorReading.objects.filter(
            loc=venue, timestamp__gte=start_dt, timestamp__lte=end_dt
        ))
        if readings.exists():
            aggs = readings.aggregate(
                temp_avg=Avg("temp"), hum_avg=Avg("hum"),
                light_avg=Avg("light"), snd_avg=Avg("snd"),
                temp_max=Max("temp"), temp_min=Min("temp"),
            )
            avg_readings = {
                "temp_avg": round(aggs["temp_avg"] or 0, 1),
                "hum_avg": round(aggs["hum_avg"] or 0, 1),
                "light_avg": round(aggs["light_avg"] or 0, 1),
                "snd_avg": round(aggs["snd_avg"] or 0, 1),
                "temp_max": round(aggs["temp_max"] or 0, 1),
                "temp_min": round(aggs["temp_min"] or 0, 1),
                "count": readings.count(),
            }
            readings_for_period = list(readings.order_by("timestamp"))

    # Latest readings per venue (for real-time comfort score)
    latest_readings = []
    now_ts = now()
    cutoff_hr = now_ts - timedelta(hours=1)
    for code, label in SensorReading.VENUE_CHOICES:
        latest = clean_readings(SensorReading.objects.filter(
            loc=code, timestamp__gte=cutoff_hr
        )).order_by("-timestamp").first()
        if latest:
            latest_readings.append({
                "loc": latest.loc,
                "label": label.replace("Computer Room", "").strip(),
                "node_id": latest.node_id,
                "temp": latest.temp,
                "hum": latest.hum,
                "light": latest.light,
                "snd": latest.snd,
            })

    filter_qs = build_filter_query_string(filters)

    context = {
        "filters": filters,
        "matching_events": matching_events,
        "avg_readings": avg_readings,
        "readings_for_period": readings_for_period,
        "filter_qs": filter_qs,
    }
    return render(request, "time_event_analysis.html", context)


def alert_list_view(request):
    status = request.GET.get("status", "")
    severity = request.GET.get("severity", "")
    room = request.GET.get("room", "")

    alerts = Alert.objects.all()
    if status:
        alerts = alerts.filter(status=status)
    if severity:
        alerts = alerts.filter(severity=severity)
    if room:
        alerts = alerts.filter(room=room)

    context = {
        "alerts": alerts,
        "status_filter": status,
        "severity_filter": severity,
        "room_filter": room,
    }
    return render(request, "alerts.html", context)


# --- API endpoints for AJAX ---

def api_readings_json(request):
    filters = parse_shared_filters(request)
    readings = clean_readings(SensorReading.objects.all())
    readings = apply_reading_filters(readings, filters)
    readings = readings[:200]
    data = [
        {
            "id": r.id,
            "node_id": r.node_id,
            "loc": r.loc,
            "temp": r.temp,
            "hum": r.hum,
            "light": r.light,
            "snd": r.snd,
            "timestamp": r.timestamp.isoformat(),
        }
        for r in readings
    ]
    return JsonResponse({"readings": data})


def api_latest_summary(request):
    cutoff = now() - timedelta(minutes=15)
    latest_all = clean_readings(SensorReading.objects.filter(timestamp__gte=cutoff))
    metrics = {}
    delta = {}
    all_in_window = latest_all.order_by("-timestamp")
    if all_in_window.exists():
        cur = all_in_window.aggregate(temp=Avg("temp"), hum=Avg("hum"), light=Avg("light"), snd=Avg("snd"))
        metrics = {k: round(v or 0, 1) for k, v in cur.items()}
        # Compare single latest reading vs single previous reading
        all_list = list(all_in_window[:2])
        if len(all_list) == 2:
            for key in ("temp", "hum", "light", "snd"):
                c = getattr(all_list[0], key) or 0
                p = getattr(all_list[1], key) or 0
                if c > p: delta[key] = "up"
                elif c < p: delta[key] = "down"
                else: delta[key] = ""
    data = {}
    for code, label in SensorReading.VENUE_CHOICES:
        latest = (
            clean_readings(SensorReading.objects.filter(loc=code, timestamp__gte=cutoff))
            .order_by("-timestamp")
            .first()
        )
        if latest:
            data[code] = {
                "label": label,
                "node_id": latest.node_id,
                "temp": latest.temp,
                "hum": latest.hum,
                "light": latest.light,
                "snd": latest.snd,
            }
    return JsonResponse({"latest": data, "metrics": metrics, "delta": delta})


def api_historical_summary(request):
    try:
        hours = int(request.GET.get("hours", "24"))
    except (ValueError, TypeError):
        hours = 24
    hours = max(1, min(hours, 168))
    cutoff = now() - timedelta(hours=hours)

    base_qs = clean_readings(SensorReading.objects.filter(timestamp__gte=cutoff))

    # Use 5-minute intervals for short ranges (so lines show), hourly for longer
    if hours <= 6:
        readings = (
            base_qs
            .annotate(period=TruncMinute("timestamp"))
            .values("period")
            .annotate(temp=Avg("temp"), hum=Avg("hum"), light=Avg("light"), snd=Avg("snd"))
            .order_by("period")
        )
        fmt = "%H:%M"
    else:
        readings = (
            base_qs
            .annotate(period=TruncHour("timestamp"))
            .values("period")
            .annotate(temp=Avg("temp"), hum=Avg("hum"), light=Avg("light"), snd=Avg("snd"))
            .order_by("period")
        )
        fmt = "%m/%d %H:00"

    result = []
    for r in readings:
        result.append({
            "hour": r["period"].strftime(fmt),
            "temp": round(r["temp"] or 0, 1),
            "hum": round(r["hum"] or 0, 1),
            "light": round(r["light"] or 0, 1),
            "snd": round(r["snd"] or 0, 1),
        })
    return JsonResponse({"hours": result})


@csrf_exempt
def api_alert_acknowledge(request, alert_id):
    if request.method == "POST":
        try:
            alert = Alert.objects.get(id=alert_id)
            alert.status = "acknowledged"
            alert.save()
            return JsonResponse({"ok": True, "active_count": Alert.objects.filter(status="active").count()})
        except Alert.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Not found"}, status=404)
    return JsonResponse({"ok": False, "error": "POST only"}, status=405)


def api_alert_stats(request):
    """Return alert statistics (counts by status, severity, and room)."""
    total = Alert.objects.count()
    by_status = {}
    for s, _ in Alert.STATUS_CHOICES:
        by_status[s] = Alert.objects.filter(status=s).count()
    by_severity = {}
    for s, _ in Alert.SEVERITY_CHOICES:
        by_severity[s] = Alert.objects.filter(severity=s).count()
    by_room = {}
    for code, _ in SensorReading.VENUE_CHOICES:
        cnt = Alert.objects.filter(room=code).count()
        if cnt:
            by_room[code] = cnt
    return JsonResponse({
        "total": total,
        "by_status": by_status,
        "by_severity": by_severity,
        "by_room": by_room,
    })


def export_readings_csv(request):
    filters = parse_shared_filters(request)
    readings = clean_readings(SensorReading.objects.all())
    readings = apply_reading_filters(readings, filters)
    readings = readings[:5000]

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sensor_readings.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Node ID', 'Venue', 'Temperature (C)', 'Humidity (%)', 'Light (%)', 'Sound (dB)', 'Timestamp'])
    for r in readings:
        writer.writerow([r.id, r.node_id, r.loc, r.temp, r.hum, r.light, r.snd, r.timestamp.strftime('%Y-%m-%d %H:%M:%S')])
    return response


