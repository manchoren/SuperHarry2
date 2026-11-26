import json
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts.run_skill_jobs import validate_runtime


class RuntimePolicyTests(SimpleTestCase):
    def check_versions(self, runc, containerd):
        components = json.dumps([
            {"Name": "runc", "Version": runc},
            {"Name": "containerd", "Version": containerd},
        ])
        with patch("scripts.run_skill_jobs.subprocess.check_output", return_value=components):
            validate_runtime()

    def test_supported_project_baseline_and_current_release(self):
        self.check_versions("1.4.3", "v2.3.6")
        self.check_versions("1.5.3", "v2.4.2")

    def test_advisory_fixed_older_branches_remain_outside_project_baseline(self):
        with self.assertRaisesRegex(RuntimeError, "runc 1.3.6"):
            self.check_versions("1.3.6", "2.3.6")
        with self.assertRaisesRegex(RuntimeError, "containerd 2.2.9"):
            self.check_versions("1.5.3", "2.2.9")

    def test_containerd_240_is_affected_despite_exceeding_numeric_floor(self):
        for version in ("2.4.0", "v2.4.0+vendor.1"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(RuntimeError, "containerd .*2.4.0"):
                    self.check_versions("1.5.3", version)

    def test_unparseable_prerelease_and_unavailable_runtime_fail_closed(self):
        with self.assertRaises(RuntimeError):
            self.check_versions("1.5.0-rc.3", "2.4.2")
        with patch("scripts.run_skill_jobs.subprocess.check_output", side_effect=OSError("unavailable")):
            with self.assertRaises(RuntimeError):
                validate_runtime()
