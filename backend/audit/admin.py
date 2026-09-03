from django.contrib import admin

from .models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ("actor", "action", "target_description", "timestamp")
    list_filter = ("action",)
    search_fields = ("actor__username", "target_description")
    date_hierarchy = "timestamp"
