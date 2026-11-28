from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from superharry.models import AccountAudit


class AccountManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.superuser = users.create_superuser(username="account-owner", password="synthetic-owner-password")
        administrators, _ = Group.objects.get_or_create(name="Administrators")
        cls.delegate = users.create_user(username="account-delegate", password="synthetic-delegate-password", is_staff=True)
        cls.delegate.groups.add(administrators)
        cls.regular = users.create_user(
            username="regular-account", email="regular@example.test", password="synthetic-regular-password"
        )
        cls.inactive = users.create_user(
            username="inactive-account", email="inactive@example.test", password="synthetic-inactive-password", is_active=False
        )
        cls.privileged = users.create_user(
            username="privileged-account", email="privileged@example.test", password="synthetic-privileged-password", is_staff=True
        )
        cls.privileged.groups.add(administrators)
        cls.direct_permission = users.create_user(
            username="permission-account", password="synthetic-permission-password"
        )
        cls.direct_permission.user_permissions.add(Permission.objects.get(codename="view_user"))
        cls.unauthorized_staff = users.create_user(
            username="ordinary-staff", password="synthetic-staff-password", is_staff=True
        )

    def login(self, user):
        self.client.force_login(user)

    def test_account_routes_require_an_authorized_manager(self):
        urls = (
            reverse("account-list"),
            reverse("account-create"),
            reverse("account-detail", args=(self.regular.pk,)),
            reverse("account-edit", args=(self.regular.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), f"/login/?next={url}")

        for user in (self.regular, self.unauthorized_staff):
            self.login(user)
            for url in urls:
                with self.subTest(username=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            denied = self.client.post(
                reverse("account-create"),
                {"username": f"created-by-{user.username}", "password": "A-long-synthetic-password-814"},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertFalse(get_user_model().objects.filter(username=f"created-by-{user.username}").exists())
            self.client.logout()

    def test_delegate_can_search_and_filter_only_regular_accounts(self):
        self.login(self.delegate)
        response = self.client.get(reverse("account-list"))
        self.assertContains(response, "regular-account")
        self.assertContains(response, "inactive-account")
        self.assertNotContains(response, "privileged-account")
        self.assertNotContains(response, "account-owner")
        self.assertNotContains(response, "permission-account")
        self.assertNotContains(response, "ordinary-staff")
        self.assertContains(response, 'name="role"')
        self.assertNotContains(response, "Administrator</option>")

        response = self.client.get(reverse("account-list"), {"q": "regular@example.test"})
        self.assertContains(response, "regular-account")
        self.assertNotContains(response, "inactive-account")

        response = self.client.get(reverse("account-list"), {"status": "active", "role": "regular"})
        self.assertContains(response, "regular-account")
        self.assertNotContains(response, "inactive-account")

        response = self.client.get(reverse("account-list"), {"status": "inactive"})
        self.assertContains(response, "inactive-account")
        self.assertNotContains(response, "regular-account")

    def test_delegate_cannot_view_or_edit_privileged_accounts_by_direct_url(self):
        self.login(self.delegate)
        self.assertEqual(self.client.get(reverse("account-detail", args=(self.privileged.pk,))).status_code, 404)
        self.assertEqual(self.client.get(reverse("account-detail", args=(self.superuser.pk,))).status_code, 404)
        self.assertEqual(self.client.get(reverse("account-detail", args=(self.direct_permission.pk,))).status_code, 404)
        self.assertEqual(self.client.get(reverse("account-edit", args=(self.privileged.pk,))).status_code, 404)
        for target in (self.privileged, self.superuser):
            for name, method, data in (
                ("account-edit", "post", {"username": target.username}),
                ("account-password-reset", "post", {"new_password1": "CHANGE_ME", "new_password2": "CHANGE_ME"}),
                ("account-deactivate", "post", {}),
                ("account-reactivate", "post", {}),
                ("account-delete", "get", {}),
                ("account-delete", "post", {"confirmation": target.username}),
            ):
                with self.subTest(target=target.username, route=name, method=method):
                    response = getattr(self.client, method)(reverse(name, args=(target.pk,)), data)
                    self.assertEqual(response.status_code, 404)
        response = self.client.post(
            reverse("account-edit", args=(self.regular.pk,)),
            {
                "username": "regular-account",
                "first_name": "Changed",
                "last_name": "Name",
                "email": "regular@example.test",
                "is_staff": "on",
                "is_superuser": "on",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.regular.refresh_from_db()
        self.assertFalse(self.regular.is_staff)
        self.assertFalse(self.regular.is_superuser)

    def test_create_uses_existing_password_validation_and_records_nonsecret_audit(self):
        self.login(self.delegate)
        response = self.client.post(
            reverse("account-create"),
            {
                "username": "new-learner",
                "first_name": "Taylor",
                "last_name": "Example",
                "email": "taylor@example.test",
                "password": "A-long-synthetic-password-814",
                "must_change_password": "on",
            },
        )
        account = get_user_model().objects.get(username="new-learner")
        self.assertRedirects(response, reverse("account-detail", args=(account.pk,)))
        self.assertTrue(account.is_active)
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        self.assertTrue(account.profile.must_change_password)
        self.assertTrue(account.check_password("A-long-synthetic-password-814"))
        self.assertNotContains(self.client.get(reverse("account-detail", args=(account.pk,))), "A-long-synthetic-password-814")
        event = AccountAudit.objects.get(target=account, action="created")
        self.assertEqual(event.actor, self.delegate)
        self.assertEqual(event.target_username, "new-learner")
        self.assertFalse(event.target_is_privileged)
        self.assertNotIn("A-long-synthetic-password-814", str(event.__dict__))

    def test_create_rejects_invalid_password_without_creating_account_or_audit(self):
        self.login(self.delegate)
        response = self.client.post(
            reverse("account-create"),
            {"username": "short-password-user", "password": "123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "too short", status_code=200)
        self.assertFalse(get_user_model().objects.filter(username="short-password-user").exists())
        self.assertFalse(AccountAudit.objects.filter(target_username="short-password-user").exists())

    def test_create_rejects_duplicate_username(self):
        self.login(self.delegate)
        response = self.client.post(
            reverse("account-create"),
            {"username": "regular-account", "password": "A-long-synthetic-password-814"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertFalse(AccountAudit.objects.filter(target_username="regular-account", actor=self.delegate).exists())

    def test_edit_updates_only_profile_fields_and_records_audit_without_values(self):
        self.login(self.delegate)
        response = self.client.post(
            reverse("account-edit", args=(self.regular.pk,)),
            {
                "username": "renamed-regular",
                "first_name": "Riley",
                "last_name": "Example",
                "email": "riley@example.test",
                "password": "A-long-synthetic-password-814",
            },
        )
        self.assertRedirects(response, reverse("account-detail", args=(self.regular.pk,)))
        self.regular.refresh_from_db()
        self.assertEqual(self.regular.username, "renamed-regular")
        self.assertEqual(self.regular.first_name, "Riley")
        self.assertEqual(self.regular.email, "riley@example.test")
        self.assertTrue(self.regular.check_password("synthetic-regular-password"))
        event = AccountAudit.objects.get(target=self.regular, action="edited")
        self.assertEqual(event.actor, self.delegate)
        self.assertEqual(event.target_username, "renamed-regular")
        self.assertNotIn("riley@example.test", str(event.__dict__))

    def test_superuser_can_view_and_edit_administrator_accounts(self):
        self.login(self.superuser)
        response = self.client.get(reverse("account-list"), {"role": "administrator"})
        self.assertContains(response, "privileged-account")
        detail = self.client.get(reverse("account-detail", args=(self.privileged.pk,)))
        self.assertContains(detail, "Administrator")
        self.assertContains(detail, "Edit account")
        edit_url = reverse("account-edit", args=(self.privileged.pk,))
        self.assertContains(self.client.get(edit_url), 'name="administrators_group"')
        historical = AccountAudit.objects.create(
            target=self.privileged,
            target_id_snapshot=self.privileged.pk,
            target_username=self.privileged.username,
            target_is_privileged=False,
            action="edited",
        )

        demoted = self.client.post(
            edit_url,
            {
                "username": self.privileged.username,
                "first_name": "Regular now",
                "administrators_group": "",
            },
        )
        self.assertRedirects(demoted, reverse("account-detail", args=(self.privileged.pk,)))
        self.privileged.refresh_from_db()
        self.assertFalse(self.privileged.is_staff)
        self.assertFalse(self.privileged.groups.filter(name="Administrators").exists())
        self.assertFalse(
            AccountAudit.objects.filter(target=self.privileged, action="edited")
            .order_by("-pk").first().target_is_privileged
        )

        promoted = self.client.post(
            edit_url,
            {
                "username": self.privileged.username,
                "first_name": "Administrator again",
                "administrators_group": "on",
            },
        )
        self.assertRedirects(promoted, reverse("account-detail", args=(self.privileged.pk,)))
        self.privileged.refresh_from_db()
        self.assertTrue(self.privileged.is_staff)
        self.assertTrue(self.privileged.groups.filter(name="Administrators").exists())
        self.assertTrue(AccountAudit.objects.filter(target=self.privileged, action="edited", target_is_privileged=True).exists())
        historical.refresh_from_db()
        self.assertTrue(historical.target_is_privileged)

    def test_delegated_admin_cannot_post_account_role_fields(self):
        self.login(self.delegate)
        response = self.client.post(
            reverse("account-edit", args=(self.regular.pk,)),
            {"username": self.regular.username, "administrators_group": "on"},
        )
        self.assertEqual(response.status_code, 403)
        self.regular.refresh_from_db()
        self.assertFalse(self.regular.is_staff)
        self.assertFalse(self.regular.groups.exists())

    def test_superuser_cannot_target_superuser_accounts_from_integrated_ui(self):
        self.login(self.superuser)
        self.assertEqual(self.client.get(reverse("account-detail", args=(self.superuser.pk,))).status_code, 404)
        for name, method, data in (
            ("account-edit", "post", {"username": self.superuser.username}),
            ("account-password-reset", "post", {"new_password1": "CHANGE_ME", "new_password2": "CHANGE_ME"}),
            ("account-deactivate", "post", {}),
            ("account-reactivate", "post", {}),
            ("account-delete", "post", {"confirmation": self.superuser.username}),
        ):
            with self.subTest(route=name):
                response = getattr(self.client, method)(reverse(name, args=(self.superuser.pk,)), data)
                self.assertEqual(response.status_code, 404)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_active)
        self.assertTrue(self.superuser.is_superuser)

    def test_superuser_can_create_administrator_through_integrated_account_form(self):
        from unittest.mock import patch

        self.login(self.superuser)
        response = self.client.get(reverse("account-create"))
        self.assertContains(response, 'name="administrators_group"')
        with patch("superharry.accounts.validate_password"):
            response = self.client.post(
                reverse("account-create"),
                {
                    "username": "created-administrator",
                    "password": "CHANGE_ME",
                    "administrators_group": "on",
                    "must_change_password": "on",
                },
            )
        created = get_user_model().objects.get(username="created-administrator")
        self.assertRedirects(response, reverse("account-detail", args=(created.pk,)))
        self.assertTrue(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertTrue(created.groups.filter(name="Administrators").exists())
        self.assertTrue(created.profile.must_change_password)
        self.assertTrue(AccountAudit.objects.get(target=created).target_is_privileged)
        self.assertTrue(created.check_password("CHANGE_ME"))
