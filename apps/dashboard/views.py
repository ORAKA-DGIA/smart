from datetime import date, timedelta
from django.utils import timezone
from django.utils.timezone import localtime
from django.db.models import Avg, Sum
from django.db.models.functions import TruncHour
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.devices.models import Device, DeviceSensorReading
from apps.alerts.models import Alert
from apps.analytics.models import SensorReading


@api_view(['GET'])
def dashboard_summary(request):
    active_alerts = Alert.objects.filter(severity='High', dismissed=False).count()
    total = Device.objects.count()
    online = Device.objects.filter(status='Online').count()
    status = 'System Operational' if online == total else f'{online}/{total} Devices Online'
    return Response({
        'readable_date': timezone.localtime().strftime('%A, %B %d, %Y'),
        'station': 'Main Entrance Station',
        'status': status,
        'active_alerts': active_alerts,
    })


@api_view(['GET'])
def kpi_list(request):
    today = date.today()
    yesterday = today - timedelta(days=1)

    def reading(d):
        return SensorReading.objects.filter(date=d).aggregate(
            soap=Sum('soap_usage'), water=Sum('water_usage'),
            washed=Sum('handwashes'), unwashed=Sum('unwashed'),
        )

    t = reading(today)
    y = reading(yesterday)

    def pct_change(now, prev):
        if not prev:
            return '+0%'
        diff = ((now or 0) - (prev or 0)) / (prev or 1) * 100
        return f"{'+' if diff >= 0 else ''}{diff:.0f}%"

    # Latest reading per device — subquery approach, works on all DBs
    from django.db.models import Max
    latest_ids = (
        DeviceSensorReading.objects
        .values('device')
        .annotate(latest=Max('id'))
        .values_list('latest', flat=True)
    )
    latest_qs = DeviceSensorReading.objects.filter(id__in=latest_ids)
    avg_soap  = latest_qs.aggregate(v=Avg('soap_level'))['v'] or 0
    avg_water = latest_qs.aggregate(v=Avg('water_level'))['v'] or 0

    handwashes = t['washed'] or 0
    unwashed   = t['unwashed'] or 0
    water_used = t['water'] or 0

    return Response([
        {'label': 'Handwashes Today', 'value': str(handwashes),      'change': pct_change(handwashes, y['washed']),  'up': handwashes >= (y['washed'] or 0), 'color': '#10b981'},
        {'label': 'Soap Remaining',   'value': f"{avg_soap:.0f}%",   'change': '-',                                  'up': avg_soap > 30,                    'color': '#6366f1'},
        {'label': 'Water Used (mL)',   'value': f"{water_used:.0f}",  'change': pct_change(water_used, y['water']),   'up': True,                             'color': '#0ea5e9'},
        {'label': 'Left Unwashed',    'value': str(unwashed),         'change': pct_change(unwashed, y['unwashed']),  'up': unwashed <= (y['unwashed'] or 0), 'color': '#ef4444'},
    ])


@api_view(['GET'])
def sensor_list(request):
    from django.db.models import Max
    latest_ids = (
        DeviceSensorReading.objects
        .values('device')
        .annotate(latest=Max('id'))
        .values_list('latest', flat=True)
    )
    latest_qs = DeviceSensorReading.objects.filter(id__in=latest_ids)
    agg       = latest_qs.aggregate(
        w=Avg('water_level'),
        s=Avg('soap_level'),
        t=Avg('temperature'),
    )
    avg_water = agg['w']
    avg_soap  = agg['s']
    avg_temp  = agg['t']
    handwashes = SensorReading.objects.filter(date=date.today()).aggregate(v=Sum('handwashes'))['v'] or 0
    max_washes = 500

    def fmt_level(v):
        return f"{v:.0f}%" if v is not None else 'N/A'

    return Response([
        {'label': 'Water Level',    'value': fmt_level(avg_water),                    'pct': int(avg_water or 0),                               'color': '#0ea5e9'},
        {'label': 'Soap Level',     'value': fmt_level(avg_soap),                     'pct': int(avg_soap  or 0),                               'color': '#6366f1'},
        {'label': 'Temperature',    'value': f"{avg_temp:.1f}\u00b0C" if avg_temp is not None else 'N/A', 'pct': int((avg_temp or 0) / 50 * 100), 'color': '#f59e0b'},
        {'label': 'Handwash Count', 'value': str(handwashes),                         'pct': min(int(handwashes / max_washes * 100), 100),      'color': '#10b981'},
    ])


@api_view(['GET'])
def device_list(request):
    devices = Device.objects.prefetch_related('readings').all()[:6]
    result = []
    for d in devices:
        r = d.readings.first()
        result.append({
            'id':       d.id,
            'name':     d.name,
            'status':   d.status,
            'battery':  d.battery,
            'color':    d.color,
            'location': d.location,
            'icon':     d.icon,
            'latest_reading': {
                'water_level': r.water_level if r else None,
                'soap_level':  r.soap_level  if r else None,
            },
        })
    return Response(result)


@api_view(['GET'])
def alert_list(request):
    alerts = Alert.objects.filter(dismissed=False).order_by('-time')[:4]
    return Response([
        {
            'id':       a.id,
            'title':    a.title,
            'device':   a.device,
            'time':     localtime(a.time).strftime('%I:%M %p'),
            'severity': a.severity,
        }
        for a in alerts
    ])


@api_view(['GET'])
def activity_waveform(request):
    now   = timezone.localtime()
    today = now.date()
    # Aggregate handwashes per hour from SensorReading
    hourly_data = (
        SensorReading.objects
        .filter(timestamp__date=today)
        .annotate(hour=TruncHour('timestamp'))
        .values('hour')
        .annotate(total_handwashes=Sum('handwashes'))
        .order_by('hour')
    )

    # Create buckets for all hours up to current
    current_hour = now.hour
    buckets = [0] * (current_hour + 1)
    for data in hourly_data:
        hour = localtime(data['hour']).hour
        if hour <= current_hour:
            buckets[hour] = data['total_handwashes'] or 0

    hours  = [f"{h % 12 or 12}{'am' if h < 12 else 'pm'}" for h in range(current_hour + 1)]
    values = buckets
    return Response({'hours': hours, 'values': values})

