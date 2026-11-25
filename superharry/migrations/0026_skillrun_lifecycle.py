from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("superharry", "0025_scheduleoccurrence_skill_run")]

    operations = [
        migrations.AddField(
            model_name="skillrun", name="lifecycle",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(model_name="skillrun", name="lifecycle_generation", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="skillrun", name="lifecycle_uncertain", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="skillrun", name="execution_payload", field=models.JSONField(default=dict)),
    ]
