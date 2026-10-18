""""Blur sensitivity" (blur_min) defaults to 10 instead of 60: measured at the
gate in the capstone pilot, a still face 2-5 m from the camera scores as low
as about 20, so 60 skipped nearly every frame there ("Hold steady"). Moves a
value nobody has changed by hand to the new default; a value an Admin chose
is left alone."""

from django.db import migrations

NEW_DEFAULT = 10.0
OLD_DEFAULT = 60.0


def to_new_default(apps, schema_editor):
    SystemSetting = apps.get_model("configuration", "SystemSetting")
    SystemSetting.objects.filter(key="blur_min", changed_by__isnull=True, changed_at__isnull=True).update(
        number_value=NEW_DEFAULT
    )


def to_old_default(apps, schema_editor):
    SystemSetting = apps.get_model("configuration", "SystemSetting")
    SystemSetting.objects.filter(key="blur_min", changed_by__isnull=True, changed_at__isnull=True).update(
        number_value=OLD_DEFAULT
    )


class Migration(migrations.Migration):
    dependencies = [("configuration", "0004_blur_min_size_independent")]

    operations = [migrations.RunPython(to_new_default, to_old_default)]
