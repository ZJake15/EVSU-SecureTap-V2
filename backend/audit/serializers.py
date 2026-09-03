from rest_framework import serializers

from .models import AuditLogEntry


class AuditLogEntrySerializer(serializers.ModelSerializer):
    actor_username = serializers.SerializerMethodField()

    class Meta:
        model = AuditLogEntry
        fields = ["id", "actor", "actor_username", "action", "target_description", "detail", "timestamp"]

    def get_actor_username(self, obj):
        return obj.actor.username if obj.actor else None
