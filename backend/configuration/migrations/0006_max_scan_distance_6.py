""""Ignore faces farther than" (max_scan_distance) defaults to 6 m instead of
5 m: measured at the gate in the capstone test, a face standing on the 5 m
mark is estimated at 4.3-5.0 m, so with the limit at 5 m many of its frames
were dropped ("Not detected", slow decisions). Moves a value nobody has
changed by hand to the new default; a value an Admin chose is left alone."""

from django.db import migrations

NEW_DEFAULT = 6.0
OLD_DEFAULT = 5.0


def to_new_default(apps, schema_editor):
    SystemSetting = apps.get_model("configuration", "SystemSetting")
    SystemSetting.objects.filter(key="max_scan_distance", changed_by__isnull=True, changed_at__isnull=True,
                                 number_value=OLD_DEFAULT).update(number_value=NEW_DEFAULT)


def to_old_default(apps, schema_editor):
    SystemSetting = apps.get_model("configuration", "SystemSetting")
    SystemSetting.objects.filter(key="max_scan_distance", changed_by__isnull=True, changed_at__isnull=True,
                                 number_value=NEW_DEFAULT).update(number_value=OLD_DEFAULT)


class Migration(migrations.Migration):
    dependencies = [("configuration", "0005_blur_min_default_10")]

    operations = [migrations.RunPython(to_new_default, to_old_default)]
