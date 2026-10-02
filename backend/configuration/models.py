from django.conf import settings
from django.db import models


class SystemSetting(models.Model):
    """One row per system setting the dashboard's Settings page can change.

    Which settings exist, their type, limits and wording all live in
    registry.py, not here - this table only stores each one's current value.
    The value goes in the column that matches its type (a number, an on/off
    switch, or a choice), so the database itself refuses a value of the
    wrong kind; store.py additionally checks every new value against the
    registry's limits before writing it. That's the "typed, validated" part
    - not one free-form blob that will accept anything.

    A row is created automatically the first time the backend needs a
    setting that has none yet, filled with the value the system used before
    this page existed (settings.py/.env). So switching to the Settings page
    changes nothing on day one.
    """

    key = models.CharField(max_length=64, unique=True)
    # Whole-number settings are stored here too (and read back as int).
    number_value = models.FloatField(null=True, blank=True)
    bool_value = models.BooleanField(null=True, blank=True)
    text_value = models.CharField(max_length=64, blank=True)
    # Set only when an admin changes the setting from the dashboard - the
    # "Last changed by ___ on ___" line. Empty for a value still at its
    # day-one default.
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key
