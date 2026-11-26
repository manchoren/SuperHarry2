import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    memory_enabled = models.BooleanField(default=False)


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
    max_active_schedules = models.PositiveIntegerField(default=10)
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


class VmSkill(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.SlugField(max_length=48, unique=True)
    purpose = models.CharField(max_length=255)
    instructions = models.TextField()
    files = models.JSONField(default=dict, blank=True)
    dependencies = models.JSONField(default=list, blank=True)


class SkillPreparation(models.Model):
    skill = models.OneToOneField(VmSkill, on_delete=models.CASCADE, related_name="preparation")
    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    fingerprint = models.CharField(max_length=64)
    payload = models.JSONField()


class SkillRun(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    skill = models.ForeignKey(VmSkill, null=True, on_delete=models.SET_NULL)
    skill_name = models.CharField(max_length=48)
    repository_skill = models.BooleanField(default=False)
    reply = models.ForeignKey("Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="skill_runs")
    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    lifecycle = models.CharField(max_length=16, blank=True, default="")
    lifecycle_generation = models.PositiveIntegerField(default=0)
    lifecycle_uncertain = models.BooleanField(default=False)
    execution_payload = models.JSONField(default=dict)


class SkillOutbox(models.Model):
    job_id = models.UUIDField(primary_key=True, editable=False)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    payload = models.JSONField()
    state = models.CharField(max_length=16, default="pending")
    preparation = models.OneToOneField(SkillPreparation, null=True, on_delete=models.CASCADE, related_name="outbox")
    execution = models.OneToOneField(SkillRun, null=True, on_delete=models.CASCADE, related_name="outbox")

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(preparation__isnull=False, execution__isnull=True) |
                       models.Q(preparation__isnull=True, execution__isnull=False)),
            name="skill_outbox_exactly_one_owner",
        )]


class CsvFile(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    payload = models.BinaryField()


class CsvSummary(models.Model):
    file = models.OneToOneField(CsvFile, on_delete=models.CASCADE, related_name="summary")
    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)


class ChatInput(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    message = models.ForeignKey("Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="attachments")
    name = models.CharField(max_length=255)
    media_type = models.CharField(max_length=32)
    payload = models.BinaryField()


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
        if self.submitted_day is None:
            self.submitted_day = timezone.localdate()
            self.save(update_fields=("submitted_day",))


class Schedule(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    prompt = models.TextField()
    next_run = models.DateTimeField(null=True)
    timezone = models.CharField(max_length=64)
    repetition = models.CharField(max_length=8)
    wall_time = models.TimeField(null=True, blank=True)
    weekday = models.PositiveSmallIntegerField(null=True, blank=True)
    input_ids = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey("ScheduleOccurrence", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    preferred_model = models.CharField(max_length=255, blank=True)
    canceled = models.BooleanField(default=False)

    @property
    def local_next_run(self):
        from zoneinfo import ZoneInfo
        return self.next_run.astimezone(ZoneInfo(self.timezone)) if self.next_run else None


class ScheduleOccurrence(models.Model):
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name="occurrences")
    due_at = models.DateTimeField()
    status = models.CharField(max_length=24, default="claimed")
    reply = models.ForeignKey(Message, null=True, on_delete=models.SET_NULL)
    approval_file = models.ForeignKey(CsvFile, null=True, blank=True, on_delete=models.SET_NULL)
    user_request = models.TextField(blank=True)
    failure_reason = models.TextField(blank=True)
    skill_run = models.ForeignKey(SkillRun, null=True, blank=True, on_delete=models.SET_NULL)
    preparation_job = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("schedule", "due_at"), name="unique_schedule_occurrence")]
