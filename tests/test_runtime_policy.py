import json
import subprocess
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from scripts.run_skill_jobs import validate_runtime
from scripts.run_csv_jobs import run_job as run_csv_job
from tests.csv_runner_smoke import check_runtime as check_csv_runtime


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

    def test_csv_smoke_rejects_containerd_240(self):
        components = json.dumps([
            {"Name": "runc", "Version": "1.5.3"},
            {"Name": "containerd", "Version": "2.4.0"},
        ])
        with patch("tests.csv_runner_smoke.subprocess.check_output", return_value=components):
            with self.assertRaisesRegex(SystemExit, "containerd 2.4.0"):
                check_csv_runtime()

    def test_csv_smoke_accepts_the_same_verified_baseline(self):
        components = json.dumps([
            {"Name": "runc", "Version": "1.4.3"},
            {"Name": "containerd", "Version": "2.3.6"},
        ])
        with patch("tests.csv_runner_smoke.subprocess.check_output", return_value=components):
            check_csv_runtime()

    def test_failed_csv_container_does_not_require_host_sigalrm(self):
        with patch("scripts.run_csv_jobs.subprocess.run", side_effect=subprocess.CalledProcessError(1, "docker")):
            self.assertEqual(run_csv_job(uuid4(), b"invalid"), {"status": "failed"})
        with patch("scripts.run_csv_jobs.subprocess.run", side_effect=subprocess.CalledProcessError(142, "docker")):
            self.assertEqual(run_csv_job(uuid4(), b"slow"), {"status": "timeout"})
