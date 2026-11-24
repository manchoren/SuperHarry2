from datetime import date, datetime, timezone
from io import StringIO
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from superharry.models import Conversation


@override_settings(OMNIROUTE_URL="http://gateway", OMNIROUTE_API_KEY="key", CHAT_MODEL="test/model")
class SchedulerTests(TestCase):
    def test_restart_retries_reserved_but_not_submitted_occurrence(self):
        from superharry.models import ChatSubmission, Schedule, ScheduleOccurrence

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        prompt = conversation.messages.create(role="user", content="Check the report")
        reply = conversation.messages.create(role="assistant", prompt=prompt, content="", complete=False, in_progress=True)
        schedule = Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report", next_run=None,
            timezone="UTC", repetition="once", preferred_model="test/model")
        occurrence = ScheduleOccurrence.objects.create(schedule=schedule, due_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), status="claimed")
        key = uuid5(NAMESPACE_URL, f"superharry:schedule-occurrence:{occurrence.pk}")
        ChatSubmission.objects.create(owner=owner, key=key, reply=reply, fingerprint="reserved")

        def response(messages, *, route_id, on_submit):
            on_submit()
            yield "Recovered reserved work."

        with patch("superharry.chat.stream_reply", side_effect=response) as gateway:
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        self.assertEqual(gateway.call_count, 1)
        self.client.force_login(owner)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Recovered reserved work.")

    def test_restart_finds_submitted_reply_when_occurrence_was_not_yet_linked(self):
        from superharry.models import ChatSubmission, Schedule, ScheduleOccurrence

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        prompt = conversation.messages.create(role="user", content="Check the report")
        reply = conversation.messages.create(role="assistant", prompt=prompt, content="Partial result", complete=False, in_progress=True)
        schedule = Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report", next_run=None,
            timezone="UTC", repetition="once", preferred_model="test/model")
        occurrence = ScheduleOccurrence.objects.create(schedule=schedule, due_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), status="claimed")
        key = uuid5(NAMESPACE_URL, f"superharry:schedule-occurrence:{occurrence.pk}")
        ChatSubmission.objects.create(owner=owner, key=key, reply=reply, fingerprint="submitted", submitted_day=date(2026, 1, 1))
        with patch("superharry.chat.stream_reply") as gateway:
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        gateway.assert_not_called()
        self.client.force_login(owner)
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, "Partial result")
        self.assertContains(page, "Incomplete reply")
        self.assertContains(page, "Scheduled task #")

    def test_overlapping_scheduler_invocation_does_not_submit_twice(self):
        from superharry.models import Schedule

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/model")

        def response(messages, *, route_id, on_submit):
            on_submit()
            call_command("run_schedules", stdout=StringIO())
            yield "Completed once."

        with patch("superharry.chat.stream_reply", side_effect=response) as gateway:
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        self.assertEqual(gateway.call_count, 1)
        self.client.force_login(owner)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Completed once.")

    def test_django_recovery_does_not_release_an_active_scheduled_response(self):
        from superharry.models import Schedule

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/model")
        self.client.force_login(owner)

        def response(messages, *, route_id, on_submit):
            on_submit()
            call_command("recover_chat_attempts", stdout=StringIO())
            self.assertEqual(self.client.post("/chat/send/", {"message": "Another request"}).status_code, 409)
            yield "Finished."

        with patch("superharry.chat.stream_reply", side_effect=response):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Finished.")

    def test_restart_keeps_partial_submitted_reply_without_resubmitting(self):
        from superharry.models import ChatSubmission, Message, Schedule, ScheduleOccurrence

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        prompt = conversation.messages.create(role="user", content="Check the report")
        reply = conversation.messages.create(role="assistant", prompt=prompt, content="Partial result", complete=False, in_progress=True)
        ChatSubmission.objects.create(owner=owner, reply=reply, fingerprint="submitted", submitted_day=date(2026, 1, 1))
        schedule = Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report", next_run=None,
            timezone="UTC", repetition="once", preferred_model="test/model")
        occurrence = ScheduleOccurrence.objects.create(schedule=schedule, due_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), status="running", reply=reply)
        with patch("superharry.chat.stream_reply") as gateway:
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
                call_command("run_schedules", stdout=StringIO())
        gateway.assert_not_called()
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.status, "interrupted")
        self.client.force_login(owner)
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, "Partial result")
        self.assertContains(page, "Incomplete reply")
        self.assertContains(page, "Retry reply")

    def test_restart_recovers_occurrence_interrupted_before_submission(self):
        from superharry.models import Schedule, ScheduleOccurrence

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        schedule = Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report",
            next_run=None, timezone="UTC", repetition="once", preferred_model="test/model")
        ScheduleOccurrence.objects.create(schedule=schedule, due_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), status="claimed")

        def response(messages, *, route_id, on_submit):
            on_submit()
            yield "Recovered work completed."

        with patch("superharry.chat.stream_reply", side_effect=response):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        self.client.force_login(owner)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Recovered work completed.")

    def test_cancel_after_submission_allows_the_response_to_finish(self):
        from superharry.models import Schedule

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        schedule = Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/model")
        self.client.force_login(owner)

        def response(messages, *, route_id, on_submit):
            on_submit()
            self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Cancel schedule")
            self.assertEqual(self.client.post(f"/schedules/{schedule.pk}/cancel/").status_code, 302)
            yield "Completed after cancellation."

        with patch("superharry.chat.stream_reply", side_effect=response):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, "Completed after cancellation.")
        self.assertContains(page, "Canceled")

    def test_scheduled_reply_obeys_owner_response_cap(self):
        from superharry.models import Schedule, UserChatLimits

        owner = get_user_model().objects.create_user(username="owner")
        UserChatLimits.objects.create(user=owner, max_response_characters=5)
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/model")

        def response(messages, *, route_id, on_submit):
            on_submit()
            yield "123456789"

        with patch("superharry.chat.stream_reply", side_effect=response):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        self.client.force_login(owner)
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, "12345")
        self.assertNotContains(page, "123456")

    def test_schedule_with_exhausted_quota_is_not_submitted(self):
        from superharry.models import ChatSubmission, Schedule, UserChatLimits
        owner = get_user_model().objects.create_user(username="owner")
        UserChatLimits.objects.create(user=owner, daily_messages=1)
        ChatSubmission.objects.create(owner=owner, fingerprint="used", active=False, submitted_day=date(2026, 1, 1))
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        schedule = Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/model")
        with patch("superharry.chat.stream_reply") as gateway:
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        gateway.assert_not_called()
        self.assertEqual(schedule.occurrences.get().status, "skipped")
        self.client.force_login(owner)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Scheduled task skipped: You have reached your daily message limit.")

    def test_only_owner_can_cancel_a_schedule_from_the_conversation(self):
        from superharry.models import Schedule

        owner = get_user_model().objects.create_user(username="owner")
        stranger = get_user_model().objects.create_user(username="stranger")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        schedule = Schedule.objects.create(
            owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc),
            timezone="UTC", repetition="once", preferred_model="test/model",
        )
        other_client = Client()
        other_client.force_login(stranger)
        self.assertEqual(other_client.post(f"/schedules/{schedule.pk}/cancel/").status_code, 404)
        self.client.force_login(owner)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Check the report")
        self.assertEqual(self.client.post(f"/schedules/{schedule.pk}/cancel/").status_code, 302)
        schedule.refresh_from_db()
        self.assertIsNone(schedule.next_run)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Canceled")

    def test_active_response_delays_due_schedule(self):
        from superharry.models import ChatSubmission, Schedule

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        ChatSubmission.objects.create(owner=owner, fingerprint="active")
        schedule = Schedule.objects.create(
            owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            timezone="UTC", repetition="once", preferred_model="test/model",
        )
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run, datetime(2026, 1, 1, 12, tzinfo=timezone.utc))

    def test_delayed_schedule_runs_when_active_response_finishes_within_grace_period(self):
        from superharry.models import ChatSubmission, Schedule

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        active = ChatSubmission.objects.create(owner=owner, fingerprint="active")
        Schedule.objects.create(owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/model")

        def response(messages, *, route_id, on_submit):
            on_submit()
            yield "Delayed work completed."

        with patch("superharry.chat.stream_reply", side_effect=response):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
                active.active = False
                active.save(update_fields=("active",))
                call_command("run_schedules", stdout=StringIO())
        self.client.force_login(owner)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Delayed work completed.")

    def test_schedule_over_one_hour_late_is_marked_missed_without_submission(self):
        from superharry.models import Schedule

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        schedule = Schedule.objects.create(
            owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            timezone="UTC", repetition="once", preferred_model="test/model",
        )
        with patch("superharry.chat.stream_reply") as gateway:
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 13, 1, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        gateway.assert_not_called()
        self.assertEqual(schedule.occurrences.get().status, "missed")
        self.client.force_login(owner)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Scheduled task missed")

    def test_due_one_time_schedule_posts_reply_only_once(self):
        from superharry.models import Schedule

        owner = get_user_model().objects.create_user(username="owner")
        conversation = Conversation.objects.create(owner=owner, title="Follow up")
        Schedule.objects.create(
            owner=owner, conversation=conversation, prompt="Check the report",
            next_run=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            timezone="UTC", repetition="once", preferred_model="test/model",
        )

        def response(messages, *, route_id, on_submit):
            on_submit()
            yield "Report checked."

        with patch("superharry.chat.stream_reply", side_effect=response) as gateway:
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
                call_command("run_schedules", stdout=StringIO())

        self.assertEqual(gateway.call_count, 1)
        self.client.force_login(owner)
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, "Report checked.")
        self.assertContains(page, "Check the report")
        self.assertContains(page, "Scheduled task #")
