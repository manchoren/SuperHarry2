from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase


class AccountAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_superuser(username="owner")
        cls.delegate = users.create_user(username="delegate", is_staff=True)
        cls.delegate.groups.add(Group.objects.get(name="Administrators"))
        cls.regular = users.create_user(username="regular", password="initial-password")

    def test_administrator_can_create_a_regular_account(self):
        self.client.force_login(self.delegate)
        response = self.client.post(
            "/admin/auth/user/add/",
            {
                "username": "new-user",
                "usable_password": "true",
                "password1": "new-example-password-349",
                "password2": "new-example-password-349",
            },
            follow=True,
        )
        self.assertContains(response, "was added successfully")
        self.assertContains(self.client.get("/admin/auth/user/"), "new-user")

    def test_administrator_cannot_grant_privileges_to_a_regular_account(self):
        self.client.force_login(self.delegate)
        url = f"/admin/auth/user/{self.regular.pk}/change/"
        response = self.client.post(
            url,
            {
                "username": "regular",
                "first_name": "Updated",
                "is_active": "on",
                "is_staff": "on",
                "is_superuser": "on",
                "groups": self.delegate.groups.get().pk,
                "date_joined_0": "2026-01-01",
                "date_joined_1": "12:00:00",
            },
            follow=True,
        )
        self.assertContains(response, "was changed successfully")
        regular_browser = Client()
        login = regular_browser.post(
            "/admin/login/",
            {"username": "regular", "password": "initial-password", "next": "/admin/"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertRedirects(
            regular_browser.get("/admin/auth/user/"),
            "/admin/login/?next=/admin/auth/user/",
        )

    def test_administrator_cannot_manage_other_privileged_accounts(self):
        self.client.force_login(self.delegate)
        for account in (self.owner, self.delegate):
            for action in ("change", "password", "delete"):
                with self.subTest(account=account.username, action=action):
                    response = self.client.get(f"/admin/auth/user/{account.pk}/{action}/")
                    if action == "password":
                        self.assertEqual(response.status_code, 404)
                    else:
                        self.assertRedirects(response, "/admin/")

    def test_superuser_can_delegate_account_management(self):
        self.client.force_login(self.owner)
        url = f"/admin/auth/user/{self.regular.pk}/change/"
        response = self.client.post(
            url,
            {
                "username": "regular",
                "is_active": "on",
                "is_staff": "on",
                "groups": self.delegate.groups.get().pk,
                "date_joined_0": "2026-01-01",
                "date_joined_1": "12:00:00",
            },
            follow=True,
        )
        self.assertContains(response, "was changed successfully")
        delegate_browser = Client()
        self.assertRedirects(
            delegate_browser.post(
                "/admin/login/",
                {"username": "regular", "password": "initial-password", "next": "/admin/"},
            ),
            "/admin/",
        )
        self.assertEqual(delegate_browser.get("/admin/auth/user/").status_code, 200)
        self.assertEqual(delegate_browser.get("/admin/auth/group/").status_code, 403)

    def test_staff_without_account_permissions_cannot_manage_accounts(self):
        staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin/auth/user/").status_code, 403)
