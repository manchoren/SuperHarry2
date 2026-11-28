from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("superharry", "0036_chatinput_created_at_skillartifact_updated_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="selected_skills",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
