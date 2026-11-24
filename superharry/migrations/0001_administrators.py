from django.db import migrations


def create_administrators(apps, schema_editor):
    database = schema_editor.connection.alias
    groups = apps.get_model("auth", "Group").objects.using(database)
    permissions = apps.get_model("auth", "Permission").objects.using(database)
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(database)
    user_type, _ = content_types.get_or_create(app_label="auth", model="user")
    administrators, _ = groups.get_or_create(name="Administrators")
    for action in ("add", "change", "delete", "view"):
        permission, _ = permissions.get_or_create(
            content_type=user_type,
            codename=f"{action}_user",
            defaults={"name": f"Can {action} user"},
        )
        administrators.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("auth", "0012_alter_user_first_name_max_length")]
    operations = [
        migrations.RunPython(create_administrators, migrations.RunPython.noop),
    ]
