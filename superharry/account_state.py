from django.contrib.auth import get_user_model
from django.db.models import F


def account_is_active(user_id):
    return get_user_model()._default_manager.filter(pk=user_id, is_active=True).exists()


def lock_active_account(user_id):
    """Serialize a new work admission against an account suspension."""
    return bool(
        get_user_model()._default_manager.filter(pk=user_id, is_active=True).update(
            is_active=F("is_active")
        )
    )


def lock_account(user_id):
    """Take the account row write lock before an admin lifecycle transition."""
    return bool(
        get_user_model()._default_manager.filter(pk=user_id).update(is_active=F("is_active"))
    )
