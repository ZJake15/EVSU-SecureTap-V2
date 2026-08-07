# Hand-written (no working Django environment was available to run
# `makemigrations` when this was authored - run
# `python manage.py makemigrations logs --check` before relying on this, and
# let Django regenerate it if it disagrees with logs/models.py).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logs', '0006_unmatchedattempt'),
    ]

    operations = [
        migrations.AddField(
            model_name='entrylog',
            name='liveness_score',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='entrylog',
            name='status',
            field=models.CharField(
                choices=[
                    ('success', 'Success'),
                    ('failed', 'Failed'),
                    ('spoof_suspected', 'Spoof suspected'),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='SpoofAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gate_location', models.CharField(max_length=100)),
                ('embedding', models.JSONField()),
                ('liveness_score', models.FloatField()),
                ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
        ),
    ]
