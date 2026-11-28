from django.db import IntegrityError, transaction
from django.utils import timezone

from .account_state import lock_active_account
from .models import ChatLimits, ChatSubmission, UserChatLimits


class AdmissionDenied(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def admit(user, key, fingerprint, create_reply):
    # Start with a write so concurrent SQLite transactions wait for the
    # reservation before checking quota or creating any conversation records.
    try:
        with transaction.atomic():
            if not lock_active_account(user.pk):
                raise AdmissionDenied("This account is inactive.", 403)
            submission = ChatSubmission.objects.create(owner=user, key=key, fingerprint=fingerprint)
            limits = ChatLimits.objects.get(pk=1)
            override = UserChatLimits.objects.filter(user=user).first()
            daily_limit = limits.daily_messages
            response_limit = limits.max_response_characters
            if override is not None:
                if override.daily_messages is not None:
                    daily_limit = override.daily_messages
                if override.max_response_characters is not None:
                    response_limit = override.max_response_characters
            used = ChatSubmission.objects.filter(owner=user, submitted_day=timezone.localdate())
            if used.count() >= daily_limit:
                raise AdmissionDenied("You have reached your daily message limit.", 429)
            submission.reply = create_reply()
            submission.save(update_fields=("reply",))
    except IntegrityError:
        previous = ChatSubmission.objects.select_related("reply").filter(owner=user, key=key).first()
        if previous is not None:
            if previous.fingerprint != fingerprint:
                raise AdmissionDenied("This submission ID was used for a different request.", 409) from None
            return previous, None
        raise AdmissionDenied("A reply is already in progress or has been completed.", 409) from None
    return submission, response_limit
