import django_filters

from .models import EntryLog


class EntryLogFilter(django_filters.FilterSet):
    timestamp__date = django_filters.DateFilter(field_name="timestamp", lookup_expr="date")
    person__full_name = django_filters.CharFilter(field_name="person__full_name", lookup_expr="icontains")
    gate_location = django_filters.CharFilter(field_name="gate_location", lookup_expr="icontains")

    class Meta:
        model = EntryLog
        fields = ["status", "direction", "verification_method"]
