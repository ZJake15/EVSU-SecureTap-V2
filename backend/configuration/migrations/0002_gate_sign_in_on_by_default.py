"""Guard sign-in at the gate monitor is now on by default (registry.py).

A copy that already ran has the old default, off, stored - settings are
saved the first time they're needed (store._load). Turn that stored value
on too, but only where nobody ever changed it: a row an Admin saved from
the Settings page has changed_by/changed_at, and their choice stays.
"""

from django.db import migrations


def turn_on_unless_chosen(apps, schema_editor):
    SystemSetting = apps.get_model("configuration", "SystemSetting")
    SystemSetting.objects.filter(
        key="gate_sign_in_enabled", changed_by__isnull=True, changed_at__isnull=True,
    ).update(bool_value=True)


class Migration(migrations.Migration):

    dependencies = [
        ("configuration", "0001_initial"),
    ]

    # Going back leaves the value as it is - it's still a valid setting.
    operations = [migrations.RunPython(turn_on_unless_chosen, migrations.RunPython.noop)]
