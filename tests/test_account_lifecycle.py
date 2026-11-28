import json
import threading
import tempfile
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from superharry.models import (
    AccountAudit,
    ChatSubmission,
    Conversation,
    Message,
    Schedule,
    ScheduleOccurrence,
    SkillOutbox,
    SkillPreparation,
    SkillRun,
    VmSkill,
)


class AccountLifecycleTests(TransactionTestCase):
    def setUp(self):
        users = get_user_model().objects
        self.manager = users.create_user(username="lifecycle-manager", password="synthetic-manager-password", is_staff=True)
        administrators, _ = Group.objects.get_or_create(name="Administrators")
        self.manager.groups.add(administrators)
        self.account = users.create_user(username="lifecycle-user", password="synthetic-user-password")
        self.conversation = Conversation.objects.create(owner=self.account, title="Keep this conversation")
        self.client.force_login(self.manager)
        self.now = datetime(2026, 11, 27, 12, tzinfo=datetime_timezone.utc)

    def deactivate(self):
        return self.client.post(reverse("account-deactivate", args=(self.account.pk,)))

    def reactivate(self):
        return self.client.post(reverse("account-reactivate", args=(self.account.pk,)))

    def test_deactivation_blocks_existing_session_and_new_work_and_preserves_account_data(self):
        memory_url = reverse("memories")
        user_client = self.client_class()
        user_client.force_login(self.account)
        self.assertEqual(user_client.get(memory_url).status_code, 200)

        with patch("superharry.account_lifecycle.timezone.now", return_value=self.now):
            response = self.deactivate()
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        self.assertFalse(self.account.is_staff)
        self.assertFalse(self.account.is_superuser)
        self.assertFalse(self.account.groups.exists())
        self.assertFalse(self.account.user_permissions.exists())
        self.assertTrue(Conversation.objects.filter(pk=self.conversation.pk, owner=self.account).exists())
        event = AccountAudit.objects.get(target=self.account, action="deactivated")
        self.assertEqual(event.actor, self.manager)
        self.assertEqual(event.target_username, "lifecycle-user")

        blocked = user_client.post("/chat/send/", {"message": "No work while inactive"})
        self.assertEqual(blocked.status_code, 302)
        self.assertIn("/login/", blocked["Location"])

        with patch("superharry.account_lifecycle.timezone.now", return_value=self.now + timedelta(days=1)):
            activated = self.reactivate()
        self.assertEqual(activated.status_code, 200)
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertFalse(self.account.is_staff)
        self.assertFalse(self.account.is_superuser)
        self.assertTrue(Conversation.objects.filter(pk=self.conversation.pk, owner=self.account).exists())
        self.assertTrue(AccountAudit.objects.filter(target=self.account, action="reactivated").exists())

    def test_deactivation_pauses_future_schedule_and_scheduler_does_not_claim_it(self):
        due = self.now + timedelta(hours=1)
        schedule = Schedule.objects.create(
            owner=self.account,
            conversation=self.conversation,
            prompt="Run later",
            next_run=due,
            timezone="UTC",
            repetition="once",
        )
        with patch("superharry.account_lifecycle.timezone.now", return_value=self.now):
            response = self.deactivate()
        self.assertEqual(response.status_code, 200)
        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.paused_at)
        self.assertEqual(schedule.paused_next_run, due)
        self.assertIsNone(schedule.next_run)
        self.assertFalse(schedule.canceled)

        from superharry.scheduler import _run_due

        with patch("superharry.scheduler.available_routes", side_effect=AssertionError("paused work reached the gateway")):
            _run_due()
        self.assertFalse(ScheduleOccurrence.objects.filter(schedule=schedule).exists())

        with patch("superharry.account_lifecycle.timezone.now", return_value=self.now + timedelta(minutes=30)):
            response = self.reactivate()
        self.assertEqual(response.status_code, 200)
        schedule.refresh_from_db()
        self.assertIsNone(schedule.paused_at)
        self.assertIsNone(schedule.paused_next_run)
        self.assertEqual(schedule.next_run, due)
        self.assertFalse(schedule.canceled)

    def test_reactivation_skips_missed_one_time_and_recurring_occurrences(self):
        daily_due = self.now + timedelta(hours=1)
        daily = Schedule.objects.create(
            owner=self.account,
            conversation=self.conversation,
            prompt="Daily work",
            next_run=daily_due,
            timezone="UTC",
            repetition="daily",
            wall_time=time(13),
        )
        once_due = self.now + timedelta(hours=2)
        once = Schedule.objects.create(
            owner=self.account,
            conversation=self.conversation,
            prompt="One time work",
            next_run=once_due,
            timezone="UTC",
            repetition="once",
        )
        canceled_due = self.now + timedelta(hours=3)
        canceled = Schedule.objects.create(
            owner=self.account,
            conversation=self.conversation,
            prompt="Previously canceled work",
            next_run=canceled_due,
            timezone="UTC",
            repetition="once",
            canceled=True,
        )
        with patch("superharry.account_lifecycle.timezone.now", return_value=self.now):
            self.assertEqual(self.deactivate().status_code, 200)
        resumed_at = self.now + timedelta(days=3)
        with patch("superharry.account_lifecycle.timezone.now", return_value=resumed_at):
            self.assertEqual(self.reactivate().status_code, 200)

        daily.refresh_from_db()
        self.assertEqual(daily.next_run, self.now + timedelta(days=3, hours=1))
        self.assertIsNone(daily.paused_at)
        skipped_daily = list(ScheduleOccurrence.objects.filter(schedule=daily).order_by("due_at"))
        self.assertEqual(len(skipped_daily), 3)
        self.assertTrue(all(item.status == "skipped" for item in skipped_daily))
        self.assertTrue(all("inactive" in item.failure_reason for item in skipped_daily))

        once.refresh_from_db()
        self.assertIsNone(once.next_run)
        self.assertIsNone(once.paused_at)
        skipped_once = ScheduleOccurrence.objects.get(schedule=once)
        self.assertEqual(skipped_once.status, "skipped")
        self.assertIn("inactive", skipped_once.failure_reason)

        canceled.refresh_from_db()
        self.assertTrue(canceled.canceled)
        self.assertEqual(canceled.next_run, canceled_due)
        self.assertIsNone(canceled.paused_at)
        self.assertFalse(ScheduleOccurrence.objects.filter(schedule=canceled).exists())

    def test_inflight_chat_response_is_interrupted_and_confirmation_is_persisted(self):
        from superharry.chat import ChatOutcome, execute_message

        prompt = self.conversation.messages.create(role=Message.Role.USER, content="Wait for a reply")
        reply = self.conversation.messages.create(
            prompt=prompt, role=Message.Role.ASSISTANT, content="partial", complete=False, in_progress=True,
        )
        submission = ChatSubmission.objects.create(owner=self.account, reply=reply, fingerprint="f" * 64)
        provider_headers_sent = threading.Event()
        release_provider = threading.Event()

        class BlockingGateway(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                provider_headers_sent.set()
                release_provider.wait(10)
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingGateway)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join)
        self.addCleanup(server.shutdown)
        execution = execute_message(submission, "test/model", 200, None)
        self.assertIsNone(next(execution))
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.extend(execution))
        with override_settings(
            OMNIROUTE_URL=f"http://127.0.0.1:{server.server_port}",
            OMNIROUTE_API_KEY="synthetic-lifecycle-test",
        ):
            worker.start()
            self.assertTrue(provider_headers_sent.wait(5), "provider did not enter the blocked stream")
            worker.join(timeout=0.4)
            self.assertTrue(worker.is_alive(), "an idle provider stream should wait for its next chunk")
            response = self.deactivate()
            self.assertIn(response.status_code, (200, 202))
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive(), "the parent stream reader did not terminate after deactivation")
            release_provider.set()
        if response.status_code == 202:
            self.assertContains(response, "only after each worker or response confirms it", status_code=202)
        else:
            self.assertContains(response, "All tracked ongoing work has confirmed it stopped.")
        result = outcomes[-1]
        self.assertIsInstance(result, ChatOutcome)
        self.assertEqual(result.status, "interrupted")
        submission.refresh_from_db()
        self.assertIsNotNone(submission.cancel_requested_at)
        self.assertIsNotNone(submission.cancellation_confirmed_at)
        self.assertFalse(submission.active)
        reply.refresh_from_db()
        self.assertFalse(reply.in_progress)
        self.assertFalse(reply.complete)
        self.assertEqual(reply.content, "partial")

    def test_create_skill_admission_locks_active_account_before_persisting(self):
        from superharry.chat import execute_message

        prompt = self.conversation.messages.create(role=Message.Role.USER, content="Create a skill")
        reply = self.conversation.messages.create(prompt=prompt, role=Message.Role.ASSISTANT, content="", in_progress=True)
        submission = ChatSubmission.objects.create(owner=self.account, reply=reply, fingerprint="c" * 64)
        arguments = json.dumps({
            "name": "must-not-persist",
            "purpose": "Test",
            "instructions": "Synthetic",
            "files": {"main.py": "print('hello')"},
            "dependencies": [],
        })
        call = {"tool_call": {"id": "create", "name": "create_skill", "arguments": arguments}}
        with (
            patch("superharry.chat.stream_reply", side_effect=[(value for value in [call]), (value for value in ["done"])]),
            patch("superharry.account_state.lock_active_account", return_value=False),
        ):
            outcome = list(execute_message(submission, "test/model", 2000, None))[-1]
        self.assertEqual(outcome.status, "complete")
        self.assertFalse(VmSkill.objects.filter(owner=self.account, name="must-not-persist").exists())
        reply.refresh_from_db()
        self.assertIn("could not be created", reply.content)

    def test_update_skill_admission_locks_active_account_before_saving(self):
        from superharry.chat import execute_message

        skill = VmSkill.objects.create(
            owner=self.account,
            name="must-not-change",
            purpose="Original purpose",
            instructions="Original instructions",
            files={"main.py": "print('original')"},
        )
        prompt = self.conversation.messages.create(role=Message.Role.USER, content="Update a skill")
        reply = self.conversation.messages.create(prompt=prompt, role=Message.Role.ASSISTANT, content="", in_progress=True)
        submission = ChatSubmission.objects.create(owner=self.account, reply=reply, fingerprint="u" * 64)
        arguments = json.dumps({
            "name": skill.name,
            "purpose": "Changed purpose",
            "instructions": "Changed instructions",
            "files": {"main.py": "print('changed')"},
            "dependencies": [],
        })
        call = {"tool_call": {"id": "update", "name": "update_skill", "arguments": arguments}}
        with (
            patch("superharry.chat.stream_reply", side_effect=[(value for value in [call]), (value for value in ["done"])]),
            patch("superharry.account_state.lock_active_account", return_value=False),
        ):
            outcome = list(execute_message(submission, "test/model", 2000, None))[-1]
        self.assertEqual(outcome.status, "complete")
        skill.refresh_from_db()
        self.assertEqual(skill.purpose, "Original purpose")
        self.assertEqual(skill.instructions, "Original instructions")
        self.assertEqual(skill.files["main.py"], "print('original')")
        reply.refresh_from_db()
        self.assertIn("cannot update", reply.content)

    def test_stream_reader_start_failure_closes_pipes_without_querying_process_state(self):
        from unittest.mock import MagicMock
        from superharry.chat import stream_reply
        from superharry.gateway import GatewayFailure

        context = MagicMock()
        receiver, sender = MagicMock(), MagicMock()
        process = context.Process.return_value
        process.start.side_effect = RuntimeError("synthetic process start failure")
        context.Pipe.return_value = (receiver, sender)
        with patch("superharry.chat.multiprocessing.get_context", return_value=context):
            with self.assertRaises(GatewayFailure):
                list(stream_reply([], route_id="test/model"))
        process.start.assert_called_once()
        process.is_alive.assert_not_called()
        sender.close.assert_called_once()
        receiver.close.assert_called_once()

    def test_skill_cancellation_waits_for_worker_confirmation_and_keeps_external_uncertainty(self):
        from scripts.run_skill_jobs import process_queue
        from superharry.skill_jobs import result

        with tempfile.TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            run = SkillRun.objects.create(
                owner=self.account, skill_name="long-skill", repository_skill=True,
            )
            SkillOutbox.objects.create(
                job_id=run.job_id, requested_by=self.account, execution=run, payload={"operation": "run"},
            )
            spool = Path(directory)
            (spool / f"{run.job_id}.skill-running").write_text(
                json.dumps({"_lifecycle_generation": 0}), encoding="utf-8",
            )
            (spool / f"{run.job_id}.skill-external").write_text("requested", encoding="utf-8")

            response = self.deactivate()
            self.assertEqual(response.status_code, 202)
            self.assertContains(response, "only after each worker or response confirms it", status_code=202)
            run.refresh_from_db()
            self.assertEqual(run.lifecycle, "cancelled")
            self.assertEqual(run.lifecycle_generation, 1)
            self.assertTrue(run.lifecycle_uncertain)
            control = json.loads((spool / f"{run.job_id}.skill-control").read_text(encoding="utf-8"))
            self.assertEqual(control["action"], "cancelled")
            self.assertTrue(control["uncertain"])
            self.assertFalse(SkillOutbox.objects.filter(job_id=run.job_id).exists())
            self.assertIsNone(result(spool, run.job_id))

            # Simulate the sandbox reporting that its container is gone; only
            # then may the lifecycle control publish a generation-matched stop.
            (spool / f"{run.job_id}.skill-running").unlink()
            process_queue(spool)
            outcome = result(spool, run.job_id)
            self.assertEqual(outcome["reason"], "account_deactivated")
            self.assertEqual(outcome["generation"], 1)
            self.assertTrue(outcome["external_action_uncertain"])
            self.assertEqual(self.client.get(reverse("account-detail", args=(self.account.pk,))).context["pending_cancellations"]["skills"], 0)

    def test_running_skill_preparation_is_cancelled_but_user_skill_is_preserved(self):
        from scripts.run_skill_jobs import process_queue
        from superharry.skill_jobs import result

        with tempfile.TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            skill = VmSkill.objects.create(
                owner=self.account,
                name="lifecycle-skill",
                purpose="Keep this skill",
                instructions="Synthetic instructions",
                files={"main.py": "print(1)"},
            )
            preparation = SkillPreparation.objects.create(
                skill=skill, fingerprint="a" * 64, payload={"operation": "prepare"},
            )
            SkillOutbox.objects.create(
                job_id=preparation.job_id, requested_by=self.account, preparation=preparation, payload={"operation": "prepare"},
            )
            spool = Path(directory)
            (spool / f"{preparation.job_id}.skill-input").write_text(
                json.dumps({"operation": "prepare"}), encoding="utf-8",
            )

            response = self.deactivate()
            self.assertEqual(response.status_code, 202)
            preparation.refresh_from_db()
            self.assertEqual(preparation.lifecycle, "cancelled")
            self.assertFalse(SkillOutbox.objects.filter(job_id=preparation.job_id).exists())
            process_queue(spool)
            outcome = result(spool, preparation.job_id)
            self.assertEqual(outcome["reason"], "account_deactivated")
            self.assertTrue(VmSkill.objects.filter(pk=skill.pk, owner=self.account).exists())

    def test_database_busy_transition_fails_closed_without_audit(self):
        from django.db import OperationalError

        with patch("superharry.account_lifecycle.lock_account", side_effect=OperationalError("database is locked")):
            response = self.deactivate()
        self.assertEqual(response.status_code, 503)
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertFalse(AccountAudit.objects.filter(target=self.account, action="deactivated").exists())


class PrivilegedAccountLifecycleTests(TransactionTestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_superuser(username="lifecycle-owner", password="CHANGE_ME")
        self.admin_account = users.create_user(
            username="lifecycle-admin", password="CHANGE_ME", is_staff=True
        )
        group, _ = Group.objects.get_or_create(name="Administrators")
        self.admin_account.groups.add(group)
        self.client.force_login(self.owner)

    def test_superuser_can_deactivate_and_reactivate_administrator(self):
        deactivate = self.client.post(
            reverse("account-deactivate", args=(self.admin_account.pk,))
        )
        self.assertEqual(deactivate.status_code, 200)
        self.admin_account.refresh_from_db()
        self.assertFalse(self.admin_account.is_active)
        self.assertTrue(
            AccountAudit.objects.filter(target=self.admin_account, action="deactivated").exists()
        )

        reactivate = self.client.post(
            reverse("account-reactivate", args=(self.admin_account.pk,))
        )
        self.assertEqual(reactivate.status_code, 200)
        self.admin_account.refresh_from_db()
        self.assertTrue(self.admin_account.is_active)
        self.assertTrue(
            AccountAudit.objects.filter(target=self.admin_account, action="reactivated").exists()
        )
