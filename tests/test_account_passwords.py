from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from superharry.models import AccountAudit, UserProfile


class AccountPasswordWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.manager = users.create_user(
            username="password-manager",
            password="synthetic-manager-password",
            is_staff=True,
        )
        administrators, _ = Group.objects.get_or_create(name="Administrators")
        cls.manager.groups.add(administrators)
        cls.target = users.create_user(
            username="password-target",
            password="synthetic-old-target-password",
        )

    def test_create_and_reset_forms_default_to_required_change_and_can_opt_out(self):
        self.client.force_login(self.manager)
        create_response = self.client.get(reverse("account-create"))
        self.assertContains(create_response, 'name="must_change_password"')
        self.assertRegex(create_response.content.decode(), r'name="must_change_password"[^>]*checked')

        created_response = self.client.post(
            reverse("account-create"),
            {
                "username": "password-created-default",
                "password": "A-created-account-password-5127",
                "must_change_password": "on",
            },
        )
        self.assertEqual(created_response.status_code, 302)
        created = get_user_model().objects.get(username="password-created-default")
        self.assertTrue(created.profile.must_change_password)

        optional_response = self.client.post(
            reverse("account-create"),
            {
                "username": "password-created-optional",
                "password": "A-optional-account-password-5127",
            },
        )
        self.assertEqual(optional_response.status_code, 302)
        optional = get_user_model().objects.get(username="password-created-optional")
        self.assertFalse(optional.profile.must_change_password)

        reset_url = reverse("account-password-reset", args=(self.target.pk,))
        reset_response = self.client.get(reset_url)
        self.assertContains(reset_response, 'name="must_change_password"')
        self.assertRegex(reset_response.content.decode(), r'name="must_change_password"[^>]*checked')
        self.assertNotContains(reset_response, "synthetic-old-target-password")

        self.client.post(
            reset_url,
            {
                "new_password1": "A-reset-password-without-required-change-6239",
                "new_password2": "A-reset-password-without-required-change-6239",
            },
        )
        self.target.refresh_from_db()
        self.assertFalse(self.target.profile.must_change_password)

    def test_pending_user_cannot_access_app_or_admin_until_password_changes(self):
        UserProfile.objects.create(user=self.target, must_change_password=True)
        client = Client()
        client.force_login(self.target)
        protected_requests = (
            ("GET", "/"),
            ("GET", "/settings/"),
            ("GET", "/admin/"),
            ("GET", "/admin/auth/user/"),
            ("POST", "/chat/send/"),
        )
        for method, path in protected_requests:
            with self.subTest(method=method, path=path):
                response = getattr(client, method.lower())(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], reverse("required-password-change"))

        self.assertEqual(client.get(reverse("required-password-change")).status_code, 200)
        static_response = client.get("/static/superharry/chat.css")
        self.assertNotEqual(static_response.status_code, 302)

        logout_response = client.post(reverse("logout"))
        self.assertEqual(logout_response.status_code, 302)
        self.assertFalse("_auth_user_id" in client.session)

    def test_successful_required_change_clears_gate_and_keeps_current_session(self):
        UserProfile.objects.create(user=self.target, must_change_password=True)
        client = Client()
        client.force_login(self.target)
        response = client.post(
            reverse("required-password-change"),
            {
                "old_password": "synthetic-old-target-password",
                "new_password1": "A-user-selected-password-after-temp-9642",
                "new_password2": "A-user-selected-password-after-temp-9642",
            },
        )
        self.assertRedirects(response, reverse("chat"))
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("A-user-selected-password-after-temp-9642"))
        self.assertFalse(self.target.profile.must_change_password)
        self.assertEqual(client.get("/").status_code, 200)

    def test_admin_reset_invalidates_existing_sessions_and_audits_no_credentials(self):
        target_client = Client()
        self.assertTrue(target_client.login(username="password-target", password="synthetic-old-target-password"))
        manager_client = Client()
        manager_client.force_login(self.manager)

        reset_password = "A-new-reset-password-with-required-change-3198"
        response = manager_client.post(
            reverse("account-password-reset", args=(self.target.pk,)),
            {
                "new_password1": reset_password,
                "new_password2": reset_password,
                "must_change_password": "on",
            },
        )
        self.assertRedirects(response, reverse("account-detail", args=(self.target.pk,)))
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(reset_password))
        self.assertTrue(self.target.profile.must_change_password)

        expired_session = target_client.get("/")
        self.assertEqual(expired_session.status_code, 302)
        self.assertTrue(expired_session["Location"].startswith("/login/"))

        audit = AccountAudit.objects.get(target=self.target, action="password_reset")
        self.assertEqual(audit.actor, self.manager)
        self.assertEqual(audit.target_username, self.target.username)
        self.assertNotIn(reset_password, str(audit.__dict__))

        self.assertTrue(target_client.login(username="password-target", password=reset_password))
        gated = target_client.get("/settings/")
        self.assertEqual(gated.status_code, 302)
        self.assertEqual(gated["Location"], reverse("required-password-change"))

    def test_regular_user_cannot_reset_password_and_invalid_reset_is_not_audited(self):
        self.client.force_login(self.target)
        reset_url = reverse("account-password-reset", args=(self.target.pk,))
        self.assertEqual(self.client.get(reset_url).status_code, 403)

        manager_client = Client()
        manager_client.force_login(self.manager)
        response = manager_client.post(
            reset_url,
            {
                "new_password1": "123",
                "new_password2": "123",
                "must_change_password": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "too short")
        self.assertFalse(AccountAudit.objects.filter(target=self.target, action="password_reset").exists())

    def test_superuser_can_open_integrated_reset_for_administrator_but_delegate_cannot(self):
        owner = get_user_model().objects.create_superuser(
            username="password-owner", password="CHANGE_ME"
        )
        administrator = get_user_model().objects.create_user(
            username="password-admin-target", password="CHANGE_ME", is_staff=True
        )
        group, _ = Group.objects.get_or_create(name="Administrators")
        administrator.groups.add(group)
        reset_url = reverse("account-password-reset", args=(administrator.pk,))

        owner_client = Client()
        owner_client.force_login(owner)
        response = owner_client.get(reset_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="new_password1"')
        self.assertNotContains(response, "CHANGE_ME")

        delegate_client = Client()
        delegate_client.force_login(self.manager)
        self.assertEqual(delegate_client.get(reset_url).status_code, 404)
        self.assertEqual(
            delegate_client.post(
                reset_url,
                {"new_password1": "CHANGE_ME", "new_password2": "CHANGE_ME"},
            ).status_code,
            404,
        )
