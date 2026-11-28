import io
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from scripts.skill_sandbox import _valid_browser_headers, run_skill


class SkillSandboxCleanupTests(SimpleTestCase):
    def test_set_cookie_list_is_allowed_only_for_owner_handoff(self):
        headers = {"set-cookie": ["session=synthetic; Path=/", "csrf=synthetic; Path=/"]}
        self.assertTrue(_valid_browser_headers(headers, browser_handoff=True))
        self.assertFalse(_valid_browser_headers(headers, browser_handoff=False))

    def test_cookie_lists_are_rejected_for_other_headers_and_when_unbounded(self):
        self.assertFalse(_valid_browser_headers({"location": ["/private"]}, browser_handoff=True))
        self.assertFalse(_valid_browser_headers({"set-cookie": ["x=y"] * 17}, browser_handoff=True))
        self.assertFalse(_valid_browser_headers({"set-cookie": ["x=" + "a" * 4097]}, browser_handoff=True))

    def process(self):
        process = MagicMock()
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(b'{"status":"complete","output":"ok"}')
        process.wait.return_value = 0
        process.poll.return_value = 0
        return process

    def test_unverified_cleanup_overrides_a_successful_result(self):
        process = self.process()
        with patch("scripts.skill_sandbox.subprocess.Popen", return_value=process):
            with patch("scripts.skill_sandbox.remove_container", return_value=False):
                self.assertEqual(run_skill({}), {"status": "failed", "reason": "cleanup_unverified"})

    def test_keyboard_interrupt_propagates_when_cleanup_cannot_be_verified(self):
        process = self.process()
        process.wait.side_effect = KeyboardInterrupt
        process.poll.return_value = None
        with patch("scripts.skill_sandbox.subprocess.Popen", return_value=process):
            with patch("scripts.skill_sandbox.remove_container", return_value=False) as cleanup:
                with self.assertRaises(KeyboardInterrupt):
                    run_skill({})
        process.kill.assert_called_once()
        cleanup.assert_called_once()

    def test_timeout_is_preserved_when_cleanup_succeeds(self):
        process = self.process()
        with patch("scripts.skill_sandbox.subprocess.Popen", return_value=process):
            with patch("scripts.skill_sandbox.remove_container", return_value=True):
                self.assertEqual(run_skill({}, timeout=0), {"status": "timeout"})
