from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("superharry", "0038_account_audit"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="must_change_password",
            field=models.BooleanField(default=False),
        ),
    ]
