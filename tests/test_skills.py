from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


class SkillCatalogTests(TestCase):
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
