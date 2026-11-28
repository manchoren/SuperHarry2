from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from superharry.models import AccountAudit, Conversation, Schedule


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
        form_response = self.client.get("/admin/auth/user/add/")
        self.assertContains(form_response, 'name="must_change_password"')
        self.assertRegex(
            form_response.content.decode(),
            r'name="must_change_password"[^>]*checked',
        )
        response = self.client.post(
            "/admin/auth/user/add/",
            {
                "username": "new-user",
                "usable_password": "true",
                "password1": "new-example-password-349",
                "password2": "new-example-password-349",
                "must_change_password": "on",
            },
            follow=True,
        )
        self.assertContains(response, "was added successfully")
        self.assertContains(self.client.get("/admin/auth/user/"), "new-user")
        created = get_user_model().objects.get(username="new-user")
        self.assertTrue(created.profile.must_change_password)
        audit = AccountAudit.objects.get(target=created)
        self.assertEqual(audit.actor, self.delegate)
        self.assertEqual(audit.action, "created")
        self.assertFalse(audit.target_is_privileged)
        audit_fields = {field.name for field in AccountAudit._meta.fields}
        self.assertFalse(any("password" in name or "content" in name for name in audit_fields))

    def test_superuser_creation_form_defaults_to_required_password_change(self):
        self.client.force_login(self.owner)
        response = self.client.get("/admin/auth/user/add/")
        self.assertContains(response, 'name="must_change_password"')
        self.assertRegex(
            response.content.decode(),
            r'name="must_change_password"[^>]*checked',
        )

    def test_admin_creation_can_opt_out_but_delegate_cannot_grant_roles(self):
        self.client.force_login(self.delegate)
        response = self.client.post(
            "/admin/auth/user/add/",
            {
                "username": "new-optional-user",
                "usable_password": "true",
                "password1": "optional-example-password-349",
                "password2": "optional-example-password-349",
                "is_staff": "on",
                "is_superuser": "on",
                "groups": str(self.delegate.groups.get().pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        created = get_user_model().objects.get(username="new-optional-user")
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertFalse(created.groups.exists())
        self.assertFalse(created.profile.must_change_password)
        audit = AccountAudit.objects.get(target=created)
        self.assertEqual(audit.actor, self.delegate)
        self.assertEqual(audit.action, "created")
        self.assertFalse(audit.target_is_privileged)

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
        audit = AccountAudit.objects.get(target=self.regular, action="edited")
        self.assertEqual(audit.actor, self.delegate)
        self.assertFalse(audit.target_is_privileged)
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
                    elif action == "delete":
                        self.assertEqual(response.status_code, 403)
                    else:
                        self.assertRedirects(response, "/admin/")

    def test_admin_password_route_redirects_regular_resets_to_integrated_workflow(self):
        for actor in (self.delegate, self.owner):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                integrated_url = reverse(
                    "account-password-reset", args=[self.regular.pk]
                )
                self.assertRedirects(
                    self.client.get(
                        f"/admin/auth/user/{self.regular.pk}/password/"
                    ),
                    integrated_url,
                    fetch_redirect_response=False,
                )
                response = self.client.post(
                    f"/admin/auth/user/{self.regular.pk}/password/",
                    {
                        "password1": "A-raw-admin-password-349",
                        "password2": "A-raw-admin-password-349",
                    },
                )
                self.assertRedirects(
                    response,
                    integrated_url,
                    fetch_redirect_response=False,
                )
                self.regular.refresh_from_db()
                self.assertTrue(self.regular.check_password("initial-password"))
                self.assertFalse(
                    AccountAudit.objects.filter(action="password_reset").exists()
                )

    def test_superuser_can_reset_superuser_password_through_native_admin_policy(self):
        target = get_user_model().objects.create_superuser(
            username="other-owner", password="CHANGE_ME"
        )
        target_browser = Client()
        self.assertTrue(target_browser.login(username=target.username, password="CHANGE_ME"))
        self.client.force_login(self.owner)
        admin_url = f"/admin/auth/user/{target.pk}/password/"
        response = self.client.get(admin_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("must_change_password", response.context["form"].fields)
        self.assertRegex(
            response.content.decode(),
            r'name="must_change_password"[^>]*checked',
        )
        self.assertNotContains(response, "CHANGE_ME")

        reset_password = "Fresh-Unrelated-Reset-Password-932!"
        response = self.client.post(
            admin_url,
            {
                "password1": reset_password,
                "password2": reset_password,
                "usable_password": "true",
                "must_change_password": "on",
            },
        )
        if response.status_code == 200:
            self.fail(f"Admin superuser password form errors: {response.context['form'].errors}")
        self.assertRedirects(response, f"/admin/auth/user/{target.pk}/change/")
        target.refresh_from_db()
        self.assertTrue(target.check_password(reset_password))
        self.assertTrue(target.profile.must_change_password)
        stale_session = target_browser.get("/")
        self.assertEqual(stale_session.status_code, 302)
        self.assertTrue(stale_session["Location"].startswith("/login/"))

        audit = AccountAudit.objects.get(target=target, action="password_reset")
        self.assertEqual(audit.actor, self.owner)
        self.assertNotIn(reset_password, str(audit.__dict__))

    def test_superuser_admin_password_route_redirects_administrator_to_integrated_reset(self):
        self.client.force_login(self.owner)
        integrated_url = reverse("account-password-reset", args=[self.delegate.pk])
        admin_url = f"/admin/auth/user/{self.delegate.pk}/password/"
        self.assertRedirects(
            self.client.get(admin_url), integrated_url, fetch_redirect_response=False
        )
        old_hash = self.delegate.password
        response = self.client.post(
            admin_url,
            {"password1": "CHANGE_ME", "password2": "CHANGE_ME"},
        )
        self.assertRedirects(response, integrated_url, fetch_redirect_response=False)
        self.delegate.refresh_from_db()
        self.assertEqual(self.delegate.password, old_hash)
        self.assertFalse(AccountAudit.objects.filter(target=self.delegate, action="password_reset").exists())

    def test_last_active_superuser_cannot_be_demoted_by_direct_admin_post(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/admin/auth/user/{self.owner.pk}/change/",
            {
                "username": self.owner.username,
                "is_staff": "on",
                "is_active": "on",
                "is_superuser": "",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "12:00:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Keep at least one active superuser for account recovery")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_superuser)

    def test_last_active_superuser_cannot_be_deactivated_by_direct_admin_post(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/admin/auth/user/{self.owner.pk}/change/",
            {
                "username": self.owner.username,
                "is_staff": "on",
                "is_active": "",
                "is_superuser": "on",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "12:00:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Keep at least one active superuser for account recovery")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_last_admin_capable_superuser_cannot_be_stripped_of_staff_access(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/admin/auth/user/{self.owner.pk}/change/",
            {
                "username": self.owner.username,
                "is_staff": "",
                "is_active": "on",
                "is_superuser": "on",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "12:00:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Keep at least one active superuser for account recovery")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_staff)
        self.assertTrue(self.owner.is_superuser)
        self.assertTrue(self.owner.is_active)

    def test_superuser_can_be_demoted_when_another_active_superuser_remains(self):
        backup = get_user_model().objects.create_superuser(
            username="backup-owner", password="CHANGE_ME"
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/admin/auth/user/{self.owner.pk}/change/",
            {
                "username": self.owner.username,
                "is_staff": "on",
                "is_active": "on",
                "is_superuser": "",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "12:00:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        backup.refresh_from_db()
        self.assertFalse(self.owner.is_superuser)
        self.assertTrue(backup.is_superuser)

    def test_delegate_cannot_use_admin_password_route_for_privileged_target(self):
        self.client.force_login(self.delegate)
        url = f"/admin/auth/user/{self.owner.pk}/password/"
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(url, {"password1": "CHANGE_ME", "password2": "CHANGE_ME"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            AccountAudit.objects.filter(action="password_reset").exists()
        )

    def test_superuser_can_delegate_account_management(self):
        self.client.force_login(self.owner)
        historical = AccountAudit.objects.create(
            target=self.regular,
            target_id_snapshot=self.regular.pk,
            target_username=self.regular.username,
            target_is_privileged=False,
            action="edited",
        )
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
        audit = AccountAudit.objects.filter(target=self.regular, action="edited").order_by("-pk").first()
        self.assertEqual(audit.actor, self.owner)
        self.assertTrue(audit.target_is_privileged)
        historical.refresh_from_db()
        self.assertTrue(historical.target_is_privileged)
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

    def test_admin_post_cannot_bypass_lifecycle_to_change_active_state(self):
        from django.utils import timezone

        for actor in (self.delegate, self.owner):
            with self.subTest(actor=actor.username):
                account = get_user_model().objects.create_user(username=f"active-state-{actor.username}")
                conversation = Conversation.objects.create(owner=account, title="Preserved schedule")
                schedule = Schedule.objects.create(
                    owner=account,
                    conversation=conversation,
                    prompt="Keep running after direct admin attempt",
                    next_run=timezone.now() + timedelta(days=1),
                    timezone="UTC",
                    repetition="once",
                )
                self.client.force_login(actor)
                response = self.client.post(
                    f"/admin/auth/user/{account.pk}/change/",
                    {
                        "username": account.username,
                        "is_active": "",
                        "date_joined_0": "2026-01-01",
                        "date_joined_1": "12:00:00",
                    },
                    follow=True,
                )
                self.assertContains(response, "was changed successfully")
                account.refresh_from_db()
                schedule.refresh_from_db()
                self.assertTrue(account.is_active)
                self.assertIsNone(schedule.paused_at)
                self.assertIsNone(schedule.paused_next_run)
                self.assertFalse(AccountAudit.objects.filter(target=account, action="deactivated").exists())

    def test_staff_without_account_permissions_cannot_manage_accounts(self):
        staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin/auth/user/").status_code, 403)

    def test_django_admin_delete_is_disabled_for_superuser(self):
        self.client.force_login(self.owner)
        url = f"/admin/auth/user/{self.regular.pk}/delete/"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=self.regular.pk).exists())
        self.assertFalse(AccountAudit.objects.filter(action="deleted").exists())
