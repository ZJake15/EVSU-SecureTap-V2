""""Blur sensitivity" (blur_min) is now measured on the face resized to a
fixed size (users.insightface_utils.face_sharpness) instead of the raw crop,
whose score mostly reflected how big the face was - so the old default of 25
means something else on the new scale. Moves a value nobody has changed by
hand to the new default, 60; a value an Admin chose is left alone."""

from django.db import migrations

NEW_DEFAULT = 60.0
OLD_DEFAULT = 25.0


def to_new_scale(apps, schema_editor):
    SystemSetting = apps.get_model("configuration", "SystemSetting")
    SystemSetting.objects.filter(key="blur_min", changed_by__isnull=True, changed_at__isnull=True).update(
        number_value=NEW_DEFAULT
    )


def to_old_scale(apps, schema_editor):
    SystemSetting = apps.get_model("configuration", "SystemSetting")
    SystemSetting.objects.filter(key="blur_min", changed_by__isnull=True, changed_at__isnull=True).update(
        number_value=OLD_DEFAULT
    )


class Migration(migrations.Migration):
    dependencies = [("configuration", "0003_scheduledjob")]

    operations = [migrations.RunPython(to_new_scale, to_old_scale)]
