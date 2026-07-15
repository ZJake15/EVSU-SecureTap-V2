from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSecurityOrAbove
from logs.models import EntryLog


class SummaryView(APIView):
    permission_classes = [IsSecurityOrAbove]

    def get(self, request):
        now = timezone.localtime()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        today_logs = EntryLog.objects.filter(timestamp__gte=today_start, timestamp__lt=tomorrow_start)

        entries_today = today_logs.filter(
            direction=EntryLog.Direction.ENTRY, status=EntryLog.Status.SUCCESS
        ).count()
        failed_today = today_logs.filter(status=EntryLog.Status.FAILED).count()

        hourly = (
            today_logs.filter(status=EntryLog.Status.SUCCESS)
            .annotate(hour=TruncHour("timestamp"))
            .values("hour")
            .annotate(count=Count("id"))
            .order_by("hour")
        )
        entries_by_hour = [
            {"hour": timezone.localtime(entry["hour"]).strftime("%H:00"), "count": entry["count"]}
            for entry in hourly
        ]
        peak_hour = max(entries_by_hour, key=lambda item: item["count"])["hour"] if entries_by_hour else None

        week_start = today_start - timedelta(days=6)
        daily = (
            EntryLog.objects.filter(
                timestamp__gte=week_start, timestamp__lt=tomorrow_start, status=EntryLog.Status.SUCCESS
            )
            .annotate(date=TruncDate("timestamp"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )
        entries_by_day = [
            {"date": entry["date"].strftime("%Y-%m-%d"), "count": entry["count"]} for entry in daily
        ]

        return Response(
            {
                "entries_today": entries_today,
                "failed_today": failed_today,
                "peak_hour": peak_hour,
                "entries_by_hour": entries_by_hour,
                "entries_by_day": entries_by_day,
            }
        )
