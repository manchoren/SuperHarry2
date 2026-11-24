import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)


class ChatRoute(models.Model):
    route_id = models.CharField(max_length=255)

    @classmethod
    def active_id(cls):
        return (
            cls.objects.filter(pk=1).values_list("route_id", flat=True).first()
            or settings.CHAT_MODEL
        )

    @classmethod
    def select(cls, route_id):
        cls.objects.update_or_create(pk=1, defaults={"route_id": route_id})


class ChatLimits(models.Model):
    daily_messages = models.PositiveIntegerField(default=50)
    max_response_characters = models.PositiveIntegerField(
        default=12000, validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name_plural = "chat limits"


class UserChatLimits(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    daily_messages = models.PositiveIntegerField(null=True, blank=True, help_text="Blank inherits the global limit.")
    max_response_characters = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        help_text="Blank inherits the global limit.",
    )

    class Meta:
        verbose_name_plural = "user chat limits"


class SavedMemory(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.TextField()

    class Meta:
        ordering = ("-pk",)


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    prompt = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    role = models.CharField(max_length=9, choices=Role)
    content = models.TextField()
    complete = models.BooleanField(default=True)
    in_progress = models.BooleanField(default=False)

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(
                fields=("prompt",),
                condition=models.Q(complete=True) | models.Q(in_progress=True),
                name="one_active_or_complete_reply_per_prompt",
            ),
        ]


class ChatSubmission(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reply = models.OneToOneField(Message, null=True, on_delete=models.CASCADE)
    key = models.UUIDField(default=uuid.uuid4)
    fingerprint = models.CharField(max_length=64)
    active = models.BooleanField(default=True)
    submitted_day = models.DateField(null=True)

    class Meta:
        indexes = [models.Index(fields=("owner", "submitted_day"))]
        constraints = [
            models.UniqueConstraint(
                fields=("owner",), condition=models.Q(active=True), name="one_active_chat_per_user"
            ),
            models.UniqueConstraint(fields=("owner", "key"), name="unique_chat_submission_key"),
        ]

    def mark_submitted(self):
        self.submitted_day = timezone.localdate()
        self.save(update_fields=("submitted_day",))
