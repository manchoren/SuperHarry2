import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("superharry", "0026_skillrun_lifecycle")]

    operations = [
        migrations.AddField(
            model_name="chatinput", name="message",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="attachments", to="superharry.message"),
        ),
        migrations.AddField(
            model_name="skillrun", name="repository_skill",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="skillrun", name="reply",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="skill_runs", to="superharry.message"),
        ),
    ]
