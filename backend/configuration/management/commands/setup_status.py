"""What this copy already holds, as one line of JSON - for the launcher's
first-run setup, which skips steps that are already done.

    python manage.py setup_status
"""

import json

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connections
from django.db.migrations.executor import MigrationExecutor

from accounts.models import AdminProfile
from logs.models import EntryLog
from users.models import Person


class Command(BaseCommand):
    help = "Prints what this copy already holds (people, records, accounts) as JSON."

    def handle(self, *args, **options):
        executor = MigrationExecutor(connections["default"])
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        status = {"database_ready": not pending, "people": 0, "entry_logs": 0, "accounts": 0, "active_admins": 0}
        try:
            status.update(
                people=Person.objects.count(),
                entry_logs=EntryLog.objects.count(),
                accounts=AdminProfile.objects.count(),
                active_admins=AdminProfile.objects.filter(
                    role=AdminProfile.Role.ADMIN, user__is_active=True
                ).count(),
            )
        except DatabaseError:
            pass  # tables not created yet - all zero
        self.stdout.write(json.dumps(status))
