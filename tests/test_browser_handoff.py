import time
import unittest
from uuid import uuid4

from superharry import browser_handoff


class BrowserHandoffTests(unittest.TestCase):
    def setUp(self):
        self.job_id = uuid4()
        self.owner_id = 41
        self.token = "task-capability-012345678901234567890123456789"
        browser_handoff.clear(self.job_id)
        browser_handoff.register(self.job_id, self.owner_id, self.token, time.time() + 20)

    def tearDown(self):
        browser_handoff.clear(self.job_id)

    def test_owner_control_and_frame_are_task_scoped_and_volatile(self):
        frame = b"\xff\xd8\xff\xd9"
        self.assertIsNone(browser_handoff.authenticate(self.job_id, self.owner_id + 1, self.token))
        self.assertIsNone(browser_handoff.authenticate(self.job_id, self.owner_id, self.token + "wrong"))
        self.assertFalse(browser_handoff.take_control(self.job_id, self.owner_id + 1))
        self.assertTrue(browser_handoff.take_control(self.job_id, self.owner_id))
        self.assertEqual(browser_handoff.worker_poll(self.job_id, self.owner_id, self.token, timeout=0)["type"], "take_control")
        self.assertTrue(browser_handoff.publish_owner_frame(self.job_id, self.owner_id, self.token, frame))
        self.assertEqual(browser_handoff.owner_frame(self.job_id, self.owner_id), frame)
        self.assertTrue(browser_handoff.return_control(self.job_id, self.owner_id))
        self.assertFalse(browser_handoff.take_control(self.job_id, self.owner_id))
        self.assertIsNone(browser_handoff.owner_frame(self.job_id, self.owner_id))
        self.assertEqual(browser_handoff.worker_poll(self.job_id, self.owner_id, self.token, timeout=0)["type"], "return_control")

    def test_input_allowlist_and_queue_overflow_fail_closed(self):
        self.assertIsNone(browser_handoff.validate_owner_event({"type": "click", "x": 1280, "y": 2}))
        self.assertIsNone(browser_handoff.validate_owner_event({"type": "key", "key": "F5"}))
        self.assertIsNone(browser_handoff.validate_owner_event({"type": "type", "text": "x" * 1025}))
        self.assertTrue(browser_handoff.take_control(self.job_id, self.owner_id))
        browser_handoff.worker_poll(self.job_id, self.owner_id, self.token, timeout=0)
        for index in range(browser_handoff.MAX_QUEUED_EVENTS):
            self.assertTrue(browser_handoff.enqueue_owner_event(self.job_id, self.owner_id, {
                "type": "key", "key": "Tab", "test_sequence": index,
            }))
        self.assertFalse(browser_handoff.enqueue_owner_event(self.job_id, self.owner_id, {"type": "key", "key": "Tab"}))
        self.assertTrue(browser_handoff.return_control(self.job_id, self.owner_id))

    def test_expiry_and_clear_discard_queued_text_and_frames(self):
        self.assertTrue(browser_handoff.take_control(self.job_id, self.owner_id))
        browser_handoff.worker_poll(self.job_id, self.owner_id, self.token, timeout=0)
        secret = "synthetic-secret-sentinel-4862"
        self.assertTrue(browser_handoff.enqueue_owner_event(self.job_id, self.owner_id, {"type": "type", "text": secret}))
        state = browser_handoff.owner_state(self.job_id, self.owner_id)
        self.assertTrue(state.active)
        browser_handoff.clear(self.job_id)
        self.assertIsNone(browser_handoff.owner_state(self.job_id, self.owner_id))
        self.assertEqual(state.token, "")
        self.assertFalse(state.owner_control)
        self.assertTrue(state.events.empty())

        expired_id = uuid4()
        browser_handoff.register(expired_id, self.owner_id, self.token, time.time() - 1)
        self.assertIsNone(browser_handoff.authenticate(expired_id, self.owner_id, self.token))
        browser_handoff.clear(expired_id)

    def test_external_action_requires_one_owner_decision_bound_to_exact_proposal(self):
        self.assertTrue(browser_handoff.take_control(self.job_id, self.owner_id))
        proposal = browser_handoff.propose_action(
            self.job_id, self.owner_id, self.token,
            digest="a" * 64, method="POST", url="https://fixture.example/profile",
            summary="Save profile", form_text="Update profile",
            fields=[{"name": "display_name", "value": "Ada"}],
            expires_at=time.time() + 10,
        )
        self.assertIsNotNone(proposal)
        self.assertEqual(browser_handoff.action_proposal(self.job_id, self.owner_id), proposal)
        self.assertIsNone(browser_handoff.action_proposal(self.job_id, self.owner_id + 1))
        self.assertFalse(browser_handoff.decide_action(
            self.job_id, self.owner_id, proposal["id"], "b" * 64, "approve",
        ))
        self.assertFalse(browser_handoff.decide_action(
            self.job_id, self.owner_id + 1, proposal["id"], "a" * 64, "approve",
        ))
        self.assertTrue(browser_handoff.decide_action(
            self.job_id, self.owner_id, proposal["id"], "a" * 64, "approve",
        ))
        self.assertEqual(browser_handoff.wait_action_decision(
            self.job_id, self.owner_id, self.token, proposal["id"], "a" * 64, timeout=0.1,
        ), "approve")
        self.assertFalse(browser_handoff.decide_action(
            self.job_id, self.owner_id, proposal["id"], "a" * 64, "approve",
        ))
        self.assertIsNone(browser_handoff.action_proposal(self.job_id, self.owner_id))

    def test_denial_expiry_and_task_cleanup_never_release_an_action(self):
        self.assertTrue(browser_handoff.take_control(self.job_id, self.owner_id))
        denied = browser_handoff.propose_action(
            self.job_id, self.owner_id, self.token,
            digest="c" * 64, method="POST", url="https://fixture.example/message",
            summary="Send message", form_text="Compose message", fields=[], expires_at=time.time() + 10,
        )
        self.assertTrue(browser_handoff.decide_action(
            self.job_id, self.owner_id, denied["id"], denied["digest"], "deny",
        ))
        self.assertEqual(browser_handoff.wait_action_decision(
            self.job_id, self.owner_id, self.token, denied["id"], denied["digest"], timeout=0.1,
        ), "deny")

        expired = browser_handoff.propose_action(
            self.job_id, self.owner_id, self.token,
            digest="d" * 64, method="POST", url="https://fixture.example/update",
            summary="Update", form_text="Edit profile", fields=[], expires_at=time.time() - 1,
        )
        self.assertIsNone(expired)
        pending = browser_handoff.propose_action(
            self.job_id, self.owner_id, self.token,
            digest="e" * 64, method="POST", url="https://fixture.example/update",
            summary="Update", form_text="Edit profile", fields=[], expires_at=time.time() + 10,
        )
        self.assertIsNotNone(pending)
        browser_handoff.clear(self.job_id)
        self.assertIsNone(browser_handoff.wait_action_decision(
            self.job_id, self.owner_id, self.token, pending["id"], pending["digest"], timeout=0.1,
        ))


if __name__ == "__main__":
    unittest.main()
