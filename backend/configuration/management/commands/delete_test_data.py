"""Deletes everything the capstone test mode left behind:

    python manage.py delete_test_data

Every test entry (and its photo), and the scan's short-lived face data from
the test sessions - including an unregistered volunteer's. Real entries are
never touched. The same as the gate monitor's "Delete all test data" button;
see logs/test_mode.py.
"""

from django.core.management.base import BaseCommand

from logs import test_mode


class Command(BaseCommand):
    help = "Deletes all capstone test-mode data (test entries and the scan's face data from test sessions)."

    def handle(self, *args, **options):
        counts = test_mode.delete_test_data()
        self.stdout.write("Deleted: " + ", ".join(f"{count} {key.replace('_', ' ')}" for key, count in counts.items()))
