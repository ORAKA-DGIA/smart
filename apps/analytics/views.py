from datetime import datetime, date, time, timedelta
from django.db.models import Sum, Q, DateTimeField
from django.db.models.functions import Cast, Coalesce, TruncDay, TruncHour, TruncMinute, TruncMonth
from django.utils import timezone
from django.utils.timezone import localtime
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .models import SensorReading
from .serializers import SensorReadingSerializer
from apps.alerts.utils import create_alert


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, '%Y-%m-%d')


def _coalesced_timestamp(qs):
    return qs.annotate(ts=Coalesce('timestamp', Cast('date', DateTimeField())))


def _aggregate(qs, resolution='day'):
    """Aggregate sensor values for the requested time bucket."""
    qs = _coalesced_timestamp(qs)
    if resolution == 'minute':
        rows = (
            qs.annotate(period=TruncMinute('ts'))
              .values('period')
              .annotate(
                  soap_usage=Sum('soap_usage'),
                  water_usage=Sum('water_usage'),
                  handwashes=Sum('handwashes'),
                  unwashed=Sum('unwashed'),
              )
              .order_by('period')
        )
        labels = [localtime(r['period']).strftime('%H:%M') for r in rows]
    elif resolution == 'hour':
        rows = (
            qs.annotate(period=TruncHour('ts'))
              .values('period')
              .annotate(
                  soap_usage=Sum('soap_usage'),
                  water_usage=Sum('water_usage'),
                  handwashes=Sum('handwashes'),
                  unwashed=Sum('unwashed'),
              )
              .order_by('period')
        )
        labels = [localtime(r['period']).strftime('%H:%M') for r in rows]
    elif resolution == 'month':
        rows = (
            qs.annotate(period=TruncMonth('ts'))
              .values('period')
              .annotate(
                  soap_usage=Sum('soap_usage'),
                  water_usage=Sum('water_usage'),
                  handwashes=Sum('handwashes'),
                  unwashed=Sum('unwashed'),
              )
              .order_by('period')
        )
        labels = [localtime(r['period']).strftime('%b') for r in rows]
    else:
        rows = (
            qs.annotate(period=TruncDay('ts'))
              .values('period')
              .annotate(
                  soap_usage=Sum('soap_usage'),
                  water_usage=Sum('water_usage'),
                  handwashes=Sum('handwashes'),
                  unwashed=Sum('unwashed'),
              )
              .order_by('period')
        )
        labels = [localtime(r['period']).strftime('%b %d').replace(' 0', ' ') for r in rows]

    soap, water, washed, unwashed = [], [], [], []
    for r in rows:
        soap.append(round(r['soap_usage'] or 0, 2))
        water.append(round(r['water_usage'] or 0, 2))
        washed.append(r['handwashes'] or 0)
        unwashed.append(r['unwashed'] or 0)

    return {
        'labels': labels,
        'soapUsage': soap,
        'waterUsage': water,
        'handwashes': washed,
        'unwashed': unwashed,
    }


def _resolve_resolution(qs, from_dt, to_dt, requested='auto'):
    if requested == 'daily':
        return 'day'
    if requested == 'hourly':
        return 'hour'
    if requested == 'minute':
        return 'minute'
    if requested == 'month':
        return 'month'
    if requested != 'auto':
        return 'day'

    if qs.filter(timestamp__isnull=False).exists():
        if (to_dt - from_dt) <= timedelta(hours=1):
            return 'minute'
        if (to_dt - from_dt) <= timedelta(days=2):
            return 'hour'
    return 'day'


def _build_range_response(from_dt, to_dt, resolution='auto'):
    if isinstance(from_dt, date) and not isinstance(from_dt, datetime):
        from_dt = datetime.combine(from_dt, time.min)
    if isinstance(to_dt, date) and not isinstance(to_dt, datetime):
        to_dt = datetime.combine(to_dt, time.max)

    qs = SensorReading.objects.filter(
        Q(timestamp__range=(from_dt, to_dt)) |
        Q(timestamp__isnull=True, date__range=(from_dt.date(), to_dt.date()))
    )

    bucket = _resolve_resolution(qs, from_dt, to_dt, resolution)
    response = _aggregate(qs, bucket)
    response['resolution'] = bucket
    response['range'] = f"{from_dt.strftime('%b %d').replace(' 0', ' ')} – {to_dt.strftime('%b %d').replace(' 0', ' ')}"
    return response


@api_view(['GET'])
@authentication_classes([])
@permission_classes([])
def analytics_auto(request):
    now = timezone.now()
    start = now - timedelta(minutes=3)
    recent_qs = SensorReading.objects.filter(timestamp__range=(start, now))

    # Generate data for each of the last 3 minutes, even if no data
    minutes_data = []
    current_minute = start.replace(second=0, microsecond=0)
    end_minute = now.replace(second=0, microsecond=0)

    while current_minute <= end_minute:
        min_start = current_minute
        min_end = current_minute + timedelta(minutes=1)
        qs_min = recent_qs.filter(timestamp__range=(min_start, min_end))

        soap = qs_min.aggregate(s=Sum('soap_usage'))['s'] or 0
        water = qs_min.aggregate(w=Sum('water_usage'))['w'] or 0
        handwashes = qs_min.aggregate(h=Sum('handwashes'))['h'] or 0
        unwashed = qs_min.aggregate(u=Sum('unwashed'))['u'] or 0

        minutes_data.append({
            'period': current_minute,
            'soap_usage': soap,
            'water_usage': water,
            'handwashes': handwashes,
            'unwashed': unwashed,
        })
        current_minute += timedelta(minutes=1)

    # Build response
    labels = [localtime(m['period']).strftime('%H:%M') for m in minutes_data]
    soap_usage = [round(m['soap_usage'], 2) for m in minutes_data]
    water_usage = [round(m['water_usage'], 2) for m in minutes_data]
    handwashes = [m['handwashes'] for m in minutes_data]
    unwashed = [m['unwashed'] for m in minutes_data]

    response = {
        'labels': labels,
        'soapUsage': soap_usage,
        'waterUsage': water_usage,
        'handwashes': handwashes,
        'unwashed': unwashed,
        'resolution': 'minute',
        'range': 'Last 3 minutes',
    }
    return Response(response)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([])
def analytics_week(request):
    today = timezone.localdate()
    start = today - timedelta(days=6)
    response = _build_range_response(start, today, request.query_params.get('resolution', 'auto'))
    return Response(response)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([])
def analytics_month(request):
    today = timezone.localdate()
    start = date(today.year, 1, 1)
    response = _build_range_response(start, today, request.query_params.get('resolution', 'auto'))
    return Response(response)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([])
def analytics_range(request):
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')
    if not from_date or not to_date:
        return Response({'error': 'from and to query params required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from_dt = _parse_iso(from_date)
        to_dt = _parse_iso(to_date)
    except ValueError:
        return Response({'error': 'Invalid from/to format.'}, status=status.HTTP_400_BAD_REQUEST)

    if from_dt > to_dt:
        from_dt, to_dt = to_dt, from_dt

    response = _build_range_response(from_dt, to_dt, request.query_params.get('resolution', 'auto'))
    return Response(response)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([])
def iot_ingest(request):
    """
    IoT devices POST readings here.
    Expected payload:
    {
        "date": "2025-07-20",
        "device": "IoT-Station-01",
        "soap_usage": 1800.0,
        "water_usage": 145000.0,
        "handwashes": 62,
        "unwashed": 8
    }
    """
    serializer = SensorReadingSerializer(data=request.data)
    if serializer.is_valid():
        timestamp = serializer.validated_data.get('timestamp') or timezone.now()
        date_value = serializer.validated_data.get('date') or timestamp.date()
        device_name = serializer.validated_data['device']

        if not date_value:
            return Response({'error': 'date or timestamp required.'}, status=status.HTTP_400_BAD_REQUEST)

        obj, created = SensorReading.objects.update_or_create(
            date=date_value,
            device=device_name,
            defaults={
                'timestamp': timestamp,
                'soap_usage': serializer.validated_data['soap_usage'],
                'water_usage': serializer.validated_data['water_usage'],
                'handwashes': serializer.validated_data['handwashes'],
                'unwashed': serializer.validated_data['unwashed'],
            }
        )

        soap = serializer.validated_data['soap_usage']
        water = serializer.validated_data['water_usage']
        unwashed = serializer.validated_data['unwashed']

        if soap < 300:
            create_alert(
                title    = 'Critical Soap Supply',
                device   = device_name,
                message  = f'Daily soap usage dropped to {soap} mL — dispenser may be empty.',
                severity = 'High',
            )
        if water < 500:   # fewer than ~4 handwashes worth
            create_alert(
                title    = 'Low Water Usage Detected',
                device   = device_name,
                message  = f'Water usage is only {water} mL today — possible supply issue.',
                severity = 'Medium',
            )
        if unwashed > 50:
            create_alert(
                title    = 'High Non-Compliance Rate',
                device   = device_name,
                message  = f'{unwashed} people left without washing hands today.',
                severity = 'High',
            )

        return Response(SensorReadingSerializer(obj).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
