from django.conf import settings
from django.db import migrations


def _is_privileged(user):
    return bool(
        user.is_staff
        or user.is_superuser
        or user.groups.exists()
        or user.user_permissions.exists()
    )


def backfill_final_target_classification(apps, schema_editor):
    AccountAudit = apps.get_model("superharry", "AccountAudit")
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app_label, user_model_name)
    database = schema_editor.connection.alias

    for target in User.objects.using(database).all().iterator():
        AccountAudit.objects.using(database).filter(
            target_id_snapshot=target.pk
        ).update(target_is_privileged=_is_privileged(target))

    deleted_targets = (
        AccountAudit.objects.using(database)
        .filter(target__isnull=True, action="deleted")
        .order_by("target_id_snapshot", "-occurred_at", "-pk")
    )
    latest_deleted_classification = {}
    for event in deleted_targets.iterator():
        if event.target_id_snapshot is not None:
            latest_deleted_classification.setdefault(
                event.target_id_snapshot, event.target_is_privileged
            )
    for target_id, is_privileged in latest_deleted_classification.items():
        AccountAudit.objects.using(database).filter(
            target_id_snapshot=target_id
        ).update(target_is_privileged=is_privileged)


class Migration(migrations.Migration):
    dependencies = [("superharry", "0041_account_deletion_and_audit_snapshots")]

    operations = [
        migrations.RunPython(
            backfill_final_target_classification,
            migrations.RunPython.noop,
        )
    ]
