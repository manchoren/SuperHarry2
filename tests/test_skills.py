import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings


class SkillCatalogTests(TestCase):
    def post_job(self, url, data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, data)

    def test_worker_rejects_unsupported_or_unverifiable_docker_runtimes(self):
        from scripts.run_skill_jobs import validate_runtime

        obsolete = json.dumps([
            {"Name": "runc", "Version": "1.3.6"},
            {"Name": "containerd", "Version": "2.2.5"},
        ])
        supported = json.dumps([
            {"Name": "runc", "Version": "1.4.3"},
            {"Name": "containerd", "Version": "2.3.6"},
        ])
        with patch("scripts.run_skill_jobs.subprocess.check_output", return_value=obsolete):
            with self.assertRaisesRegex(RuntimeError, r"runc 1\.3\.6.*containerd 2\.2\.5"):
                validate_runtime()
        with patch("scripts.run_skill_jobs.subprocess.check_output", return_value="not json"):
            with self.assertRaises(RuntimeError):
                validate_runtime()
        with patch("scripts.run_skill_jobs.subprocess.check_output", return_value=supported):
            validate_runtime()

    def test_catalog_requires_authentication(self):
        self.assertEqual(self.client.get("/skills/").status_code, 302)

    def test_signed_in_user_can_browse_repository_skills(self):
        self.client.force_login(get_user_model().objects.create_user(username="reader"))
        response = self.client.get("/skills/")
        self.assertContains(response, "summarize")
        self.assertContains(response, "Summarize pasted text in chat.")
        self.assertContains(response, "Repository")

    def test_user_can_inspect_repository_skill_as_read_only(self):
        self.client.force_login(get_user_model().objects.create_user(username="reader"))
        response = self.client.get("/skills/repository/summarize/")
        self.assertContains(response, "supplied text in its original language")
        self.assertContains(response, "read-only")
        self.assertEqual(self.client.post("/skills/repository/summarize/").status_code, 405)

    def test_repository_skill_is_discovered_after_adding_its_definition(self):
        self.client.force_login(get_user_model().objects.create_user(username="reader"))
        with TemporaryDirectory() as directory, patch("superharry.skill_views.SKILLS_DIRECTORY", Path(directory)):
            self.assertNotContains(self.client.get("/skills/"), "new-skill")
            (Path(directory) / "new-skill.md").write_text(
                '+++\npurpose = "New purpose"\nallowed_tools = []\n+++\nInstructions',
                encoding="utf-8",
            )
            self.assertContains(self.client.get("/skills/"), "new-skill")

    def test_user_can_create_a_vm_skill_visible_to_other_users(self):
        owner = get_user_model().objects.create_user(username="owner")
        reader = get_user_model().objects.create_user(username="other")
        self.client.force_login(owner)
        response = self.client.post("/skills/new/", {
            "name": "draft-report",
            "purpose": "Prepare a report",
            "instructions": "Prepare the report.",
        })
        self.assertEqual(response.status_code, 302)
        self.client.force_login(reader)
        catalog = self.client.get("/skills/")
        self.assertContains(catalog, "draft-report")
        self.assertContains(catalog, "Prepare a report")

    def test_owner_can_edit_skill_but_another_user_cannot(self):
        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(owner)
        self.client.post("/skills/new/", {
            "name": "draft-report", "purpose": "First", "instructions": "Draft",
        })
        self.client.force_login(other)
        self.assertEqual(self.client.post("/skills/draft-report/edit/", {
            "name": "draft-report", "purpose": "Changed by other", "instructions": "Draft",
        }).status_code, 404)
        self.client.force_login(owner)
        self.assertEqual(self.client.post("/skills/draft-report/edit/", {
            "name": "draft-report", "purpose": "Updated", "instructions": "Draft",
        }).status_code, 302)
        self.assertContains(self.client.get("/skills/"), "Updated")

    def test_repository_name_cannot_be_claimed_by_vm_skill(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        response = self.client.post("/skills/new/", {
            "name": "summarize", "purpose": "Conflict", "instructions": "Draft",
        })
        self.assertContains(response, "This name belongs to a repository skill.")

    def test_owner_can_delete_skill_but_other_user_cannot(self):
        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(owner)
        self.client.post("/skills/new/", {
            "name": "draft-report", "purpose": "First", "instructions": "Draft",
        })
        self.client.force_login(other)
        self.assertEqual(self.client.post("/skills/draft-report/delete/").status_code, 404)
        self.client.force_login(owner)
        self.assertEqual(self.client.post("/skills/draft-report/delete/").status_code, 302)
        self.assertNotContains(self.client.get("/skills/"), "draft-report")

    def test_other_user_can_inspect_skill_instructions(self):
        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(owner)
        self.client.post("/skills/new/", {
            "name": "draft-report", "purpose": "First", "instructions": "Write a report",
        })
        self.client.force_login(other)
        self.assertContains(self.client.get("/skills/draft-report/"), "Write a report")

    def test_owner_can_save_scripts_and_dependencies_for_other_users_to_inspect(self):
        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(owner)
        response = self.client.post("/skills/new/", {
            "name": "pdf-report", "purpose": "Create a PDF", "instructions": "Use the script",
            "files": '{"render.py": "print(123)"}', "dependencies": '["reportlab==4.0.0"]',
        })
        self.assertEqual(response.status_code, 302)
        self.client.force_login(other)
        detail = self.client.get("/skills/pdf-report/")
        self.assertContains(detail, "render.py")
        self.assertContains(detail, "print(123)")
        self.assertContains(detail, "reportlab==4.0.0")

    def test_administrator_can_edit_another_users_skill(self):
        owner = get_user_model().objects.create_user(username="owner")
        admin = get_user_model().objects.create_user(username="admin")
        admin.groups.add(Group.objects.get(name="Administrators"))
        self.client.force_login(owner)
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Original", "instructions": "Original",
        })
        self.client.force_login(admin)
        response = self.client.post("/skills/report/edit/", {
            "name": "report", "purpose": "Admin updated", "instructions": "Updated",
            "files": '{"main.py": "print(1)"}', "dependencies": '["reportlab"]',
        })
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get("/skills/report/"), "Admin updated")

    def test_owner_can_update_files_and_dependencies(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Original",
        })
        response = self.client.post("/skills/report/edit/", {
            "name": "report", "purpose": "Report", "instructions": "Updated",
            "files": '{"render.py": "updated script"}', "dependencies": '["reportlab"]',
        })
        self.assertEqual(response.status_code, 302)
        detail = self.client.get("/skills/report/")
        self.assertContains(detail, "updated script")
        self.assertContains(detail, "reportlab")

    def test_administrator_can_delete_another_users_skill(self):
        owner = get_user_model().objects.create_user(username="owner")
        admin = get_user_model().objects.create_user(username="admin")
        admin.groups.add(Group.objects.get(name="Administrators"))
        self.client.force_login(owner)
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
        })
        self.client.force_login(admin)
        self.assertEqual(self.client.post("/skills/report/delete/").status_code, 302)
        self.assertEqual(self.client.get("/skills/report/").status_code, 404)

    def test_skill_files_reject_parent_directory_paths(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        response = self.client.post("/skills/new/", {
            "name": "unsafe", "purpose": "Test", "instructions": "Test",
            "files": '{"../outside.py": "content"}', "dependencies": "[]",
        })
        self.assertContains(response, "Files must use safe relative names")
        self.assertNotContains(self.client.get("/skills/"), "unsafe")

    def test_skill_files_reject_ambiguous_names_and_oversized_payloads(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        for files in (
            '{"a//script.py": "content"}',
            '{"C:/script.py": "content"}',
            '{"dir/": "content"}',
        ):
            with self.subTest(files=files):
                response = self.client.post("/skills/new/", {
                    "name": "unsafe", "purpose": "Test", "instructions": "Test",
                    "files": files, "dependencies": "[]",
                })
                self.assertContains(response, "Files must use safe relative names")
        response = self.client.post("/skills/new/", {
            "name": "large", "purpose": "Test", "instructions": "Test",
            "files": json.dumps({"large.py": "x" * (256 * 1024)}), "dependencies": "[]",
        })
        self.assertContains(response, "Skill files exceed the 256 KiB limit.")

    def test_empty_json_values_must_have_the_correct_types(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        response = self.client.post("/skills/new/", {
            "name": "invalid", "purpose": "Test", "instructions": "Test",
            "files": "[]", "dependencies": "{}",
        })
        self.assertContains(response, "Enter a JSON object of file names and text.")
        self.assertContains(response, "Enter a JSON array of dependency specifications.")

    def test_owner_can_prepare_and_retry_a_skill_through_the_worker_queue(self):
        from scripts.run_skill_jobs import process_queue

        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(owner)
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.client.force_login(other)
            self.assertEqual(self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"}).status_code, 404)
            self.client.force_login(owner)
            response = self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            self.assertEqual(response.status_code, 302)
            self.assertContains(self.client.get("/skills/report/"), "Preparation pending")
            with patch("scripts.run_skill_jobs.run_skill", return_value={"status": "failed", "stage": "preparation", "reason": "dependency_preparation_failed"}):
                process_queue(Path(directory))
            self.assertContains(self.client.get("/skills/report/"), "Dependency preparation failed")
            self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            with patch("scripts.run_skill_jobs.run_skill", return_value={"status": "complete", "output": ""}):
                process_queue(Path(directory))
            self.assertContains(self.client.get("/skills/report/"), "Prepared")
            self.client.force_login(other)
            self.assertContains(self.client.get("/skills/"), "Prepared")
            self.client.force_login(owner)
            self.client.post("/skills/report/edit/", {
                "name": "report", "purpose": "Report", "instructions": "Changed",
                "files": '{"main.py": "print(2)"}', "dependencies": "[]",
            })
            self.assertContains(self.client.get("/skills/report/"), "Preparation required")

    def test_any_user_can_run_prepared_skill_but_only_owner_can_read_result(self):
        from scripts.run_skill_jobs import process_queue

        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(owner)
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(input())"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.assertEqual(self.post_job("/skills/report/run/", {"input": "hello"}).status_code, 409)
            self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            with patch("scripts.run_skill_jobs.run_skill", return_value={"status": "complete", "output": ""}):
                process_queue(Path(directory))
            self.client.force_login(other)
            with TemporaryDirectory() as repository, patch("superharry.skill_views.SKILLS_DIRECTORY", Path(repository)):
                (Path(repository) / "report.md").write_text("invalid", encoding="utf-8")
                self.assertEqual(self.post_job("/skills/report/run/", {"input": "hello"}).status_code, 409)
            response = self.post_job("/skills/report/run/", {"input": "hello"})
            self.assertEqual(response.status_code, 302)
            location = response["Location"]
            self.client.force_login(owner)
            self.assertEqual(self.client.post("/skills/report/delete/").status_code, 409)
            self.client.force_login(other)
            with patch("scripts.run_skill_jobs.run_skill", return_value={"status": "complete", "output": "hello\n"}):
                process_queue(Path(directory))
            self.assertContains(self.client.get(location), "hello")
            self.client.force_login(owner)
            self.assertEqual(self.client.get(location).status_code, 404)
            self.assertEqual(self.client.post("/skills/report/delete/").status_code, 302)
            self.client.force_login(other)
            self.assertContains(self.client.get(location), "hello")

    def test_unverified_container_cleanup_preserves_the_job_for_recovery(self):
        from scripts.run_skill_jobs import process_queue, recover_interrupted

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            with patch("scripts.run_skill_jobs.run_skill", return_value={"status": "failed", "reason": "cleanup_unverified"}):
                with self.assertRaises(RuntimeError):
                    process_queue(Path(directory))
            self.assertEqual(len(list(Path(directory).glob("*.skill-running"))), 1)
            self.assertContains(self.client.get("/skills/report/"), "Worker cleanup requires recovery")
            self.assertEqual(self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"}).status_code, 409)
            with patch("scripts.run_skill_jobs.remove_container", return_value=False):
                with self.assertRaises(RuntimeError):
                    recover_interrupted(Path(directory))
            self.assertEqual(len(list(Path(directory).glob("*.skill-running"))), 1)
            with patch("scripts.run_skill_jobs.remove_container", return_value=True):
                recover_interrupted(Path(directory))
            self.assertEqual(len(list(Path(directory).glob("*.skill-running"))), 0)
            self.assertContains(self.client.get("/skills/report/"), "Preparation failed")
            self.assertEqual(self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"}).status_code, 302)

    def test_worker_leaves_job_queued_if_runtime_becomes_unsupported(self):
        from scripts.run_skill_jobs import process_queue

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            def unsupported_runtime():
                raise RuntimeError("runtime unavailable")
            with patch("scripts.run_skill_jobs.run_skill") as run_skill:
                with self.assertRaises(RuntimeError):
                    process_queue(Path(directory), runtime_check=unsupported_runtime)
            run_skill.assert_not_called()
            self.assertEqual(len(list(Path(directory).glob("*.skill-input"))), 1)

    def test_missing_preparation_job_is_reported_as_failed_and_can_be_retried(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            next(Path(directory).glob("*.skill-input")).unlink()
            self.assertContains(self.client.get("/skills/report/"), "Preparation failed")
            self.assertEqual(self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"}).status_code, 302)

    def test_rolled_back_preparation_never_reaches_the_worker(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            with transaction.atomic():
                self.assertEqual(self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"}).status_code, 302)
                transaction.set_rollback(True)
            self.assertEqual(list(Path(directory).glob("*.skill-input")), [])

    def test_rolled_back_execution_never_reaches_the_worker(self):
        from scripts.run_skill_jobs import process_queue

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            with patch("scripts.run_skill_jobs.run_skill", return_value={"status": "complete", "output": ""}):
                process_queue(Path(directory))
            with transaction.atomic():
                self.assertEqual(self.client.post("/skills/report/run/", {"input": "hello"}).status_code, 302)
                transaction.set_rollback(True)
            self.assertEqual(list(Path(directory).glob("*.skill-input")), [])

    def test_active_job_prevents_skill_edit_or_deletion(self):
        from scripts.run_skill_jobs import process_queue

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.post_job("/skills/report/prepare/", {"entrypoint": "main.py"})
            self.assertEqual(self.client.post("/skills/report/edit/", {
                "name": "report", "purpose": "Changed", "instructions": "Draft",
            }).status_code, 409)
            self.assertEqual(self.client.post("/skills/report/delete/").status_code, 409)
            with patch("scripts.run_skill_jobs.run_skill", return_value={"status": "complete", "output": ""}):
                process_queue(Path(directory))
            self.assertEqual(self.client.post("/skills/report/delete/").status_code, 302)

    def test_catalog_marks_a_collision_introduced_by_a_repository_update(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, patch("superharry.skill_views.SKILLS_DIRECTORY", Path(directory)):
            self.client.post("/skills/new/", {
                "name": "collision", "purpose": "VM", "instructions": "Draft",
            })
            (Path(directory) / "collision.md").write_text(
                '+++\npurpose = "Repository"\nallowed_tools = []\n+++\nInstructions',
                encoding="utf-8",
            )
            self.assertContains(self.client.get("/skills/"), "Name conflict")

    @override_settings(OMNIROUTE_URL="http://gateway", OMNIROUTE_API_KEY="test", CHAT_MODEL="test/model")
    def test_later_repository_collision_cannot_be_loaded_in_chat(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("superharry.skill_views.SKILLS_DIRECTORY", root), patch("superharry.text_skills.SKILLS_DIRECTORY", root):
                self.client.post("/skills/new/", {
                    "name": "collision", "purpose": "VM", "instructions": "Draft",
                })
                (root / "collision.md").write_text(
                    '+++\npurpose = "Repository"\nallowed_tools = []\n+++\nInstructions',
                    encoding="utf-8",
                )
                response = self.client.post("/chat/send/", {"message": "/collision example"})
                self.assertEqual(response.status_code, 400)
                self.assertIn("conflicts", response.json()["error"])

    def test_invalid_repository_definition_still_conflicts_with_vm_name(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, patch("superharry.skill_views.SKILLS_DIRECTORY", Path(directory)):
            self.client.post("/skills/new/", {
                "name": "collision", "purpose": "VM", "instructions": "Draft",
            })
            (Path(directory) / "collision.md").write_text("invalid", encoding="utf-8")
            self.assertContains(self.client.get("/skills/"), "Name conflict")


class SkillCommitTests(TransactionTestCase):
    def test_unavailable_spool_does_not_leave_a_false_pending_preparation(self):
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        self.client.post("/skills/new/", {
            "name": "report", "purpose": "Report", "instructions": "Draft",
            "files": '{"main.py": "print(1)"}', "dependencies": "[]",
        })
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=str(Path(directory) / "missing")):
            response = self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            self.assertEqual(response.status_code, 503)
            self.assertContains(self.client.get("/skills/report/"), "Preparation failed")
