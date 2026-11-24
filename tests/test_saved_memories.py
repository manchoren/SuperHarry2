import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase


class SavedMemoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="memory-owner")
        self.browser = Client(enforce_csrf_checks=True)
        self.browser.force_login(self.user)

    def post_form(self, url, **data):
        page = self.browser.get(url)
        self.assertEqual(page.status_code, 200)
        return self.browser.post(
            url,
            {"csrfmiddlewaretoken": self.browser.cookies["csrftoken"].value, **data},
        )

    def test_user_can_save_and_inspect_a_memory(self):
        response = self.post_form("/memories/new/", content="I prefer concise summaries.")
        self.assertRedirects(response, "/memories/")
        self.assertContains(self.browser.get("/memories/"), "I prefer concise summaries.")

    def saved_memory_link(self, action):
        page = self.browser.get("/memories/")
        match = re.search(rf'href="(/memories/[0-9]+/{action}/)"', page.content.decode())
        self.assertIsNotNone(match)
        return match.group(1)

    def test_user_can_edit_a_saved_memory(self):
        self.post_form("/memories/new/", content="Short summaries")
        edit_url = self.saved_memory_link("edit")
        self.assertContains(self.browser.get(edit_url), "Short summaries")
        response = self.post_form(edit_url, content="Detailed summaries")
        self.assertRedirects(response, "/memories/")
        listing = self.browser.get("/memories/")
        self.assertContains(listing, "Detailed summaries")
        self.assertNotContains(listing, "Short summaries")

    def test_user_can_delete_a_saved_memory_after_confirming(self):
        self.post_form("/memories/new/", content="Memory to delete")
        delete_url = self.saved_memory_link("delete")
        self.assertContains(self.browser.get(delete_url), "Memory to delete")
        self.assertContains(self.browser.get("/memories/"), "Memory to delete")
        self.assertRedirects(self.post_form(delete_url), "/memories/")
        self.assertNotContains(self.browser.get("/memories/"), "Memory to delete")

    def test_other_accounts_cannot_view_edit_or_delete_a_memory(self):
        self.post_form("/memories/new/", content="Unshared preference")
        edit_url = self.saved_memory_link("edit")
        delete_url = self.saved_memory_link("delete")
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="other-memory-user"))

        self.assertNotContains(other.get("/memories/"), "Unshared preference")
        for url in (edit_url, delete_url):
            with self.subTest(url=url):
                self.assertEqual(other.get(url).status_code, 404)
                self.assertEqual(other.post(url, {"content": "Changed"}).status_code, 404)
        self.assertContains(self.browser.get("/memories/"), "Unshared preference")

    def test_signed_out_visitors_cannot_list_saved_memories(self):
        self.assertRedirects(Client().get("/memories/"), "/login/?next=/memories/")
