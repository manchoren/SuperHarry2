from django.conf import settings
from django.db import models


class Conversation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)


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
