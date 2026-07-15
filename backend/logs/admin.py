from django.contrib import admin

from .models import EntryLog


@admin.register(EntryLog)
class EntryLogAdmin(admin.ModelAdmin):
    list_display = ("person", "direction", "verification_method", "status", "gate_location", "timestamp")
    list_filter = ("status", "direction", "verification_method", "gate_location")
    search_fields = ("person__full_name", "gate_location")
    date_hierarchy = "timestamp"
