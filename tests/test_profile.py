from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils.html import escape
from PIL import Image


class ProfileTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alex", password="old-password-123")
        self.client.force_login(self.user)

    def image(self):
        output = BytesIO()
        Image.new("RGB", (500, 300), "blue").save(output, "JPEG")
        return SimpleUploadedFile("avatar.jpg", output.getvalue(), content_type="image/jpeg")

    def test_upload_persist_display_and_remove(self):
        settings_page = self.client.get('/settings/')
        self.assertContains(settings_page, 'avatar-initial')
        self.assertContains(settings_page, 'accept="image/png,image/jpeg"')
        self.assertContains(settings_page, 'Maximum file size: 2.0 MB')
        self.assertNotContains(settings_page, 'Remove image')
        response = self.client.post('/settings/', {'action': 'avatar', 'avatar': self.image()})
        self.assertContains(response, 'Profile image updated')
        self.assertContains(response, 'Remove image')
        self.assertContains(response, 'id="selected-avatar-preview"')
        image = self.client.get('/settings/avatar/')
        self.assertEqual(image['Content-Type'], 'image/png')
        with Image.open(BytesIO(image.content)) as result:
            self.assertEqual(result.size, (256, 154))
        self.assertContains(self.client.get('/'), 'class="user-avatar"')
        self.assertContains(self.client.post('/settings/', {'action': 'avatar', 'avatar': self.image()}), 'Profile image updated')
        other = Client()
        other.force_login(get_user_model().objects.create_user(username='other'))
        self.assertEqual(other.get('/settings/avatar/').status_code, 404)
        self.client.post('/settings/', {'action': 'remove-avatar'})
        self.assertEqual(self.client.get('/settings/avatar/').status_code, 404)
        self.assertNotContains(self.client.get('/settings/'), 'Remove image')

    @override_settings(AVATAR_MAX_BYTES=50)
    def test_invalid_and_oversized_images_are_rejected(self):
        response = self.client.post('/settings/', {'action': 'avatar', 'avatar': self.image()})
        self.assertContains(response, 'size limit')
        response = self.client.post('/settings/', {'action': 'avatar', 'avatar': SimpleUploadedFile('fake.png', b'not an image')})
        self.assertContains(response, 'valid PNG or JPEG')
        self.assertEqual(self.client.get('/settings/avatar/').status_code, 404)

    def test_password_change_and_access_control(self):
        self.assertEqual(Client().get('/settings/avatar/').status_code, 302)
        response = self.client.post('/settings/', {'action': 'password', 'old_password': 'old-password-123', 'new_password1': 'new-password-456', 'new_password2': 'new-password-456'})
        self.assertContains(response, 'Password updated')
        self.assertTrue(self.client.get('/').status_code == 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('new-password-456'))

    def test_settings_has_three_cards_and_complete_read_only_guidance(self):
        response = self.client.get('/settings/')
        self.assertContains(response, 'aria-labelledby="profile-heading"')
        self.assertContains(response, 'aria-labelledby="password-heading"')
        self.assertContains(response, 'aria-labelledby="agent-heading"')
        self.assertContains(response, 'Current repository-managed SOUL.md (read only)')
        self.assertContains(response, 'readonly')
        self.assertContains(response, 'SOUL.md contains general agent guidance')
        self.assertContains(response, 'Application permissions and safeguards are enforced separately')
        from superharry.soul import guidance
        self.assertContains(response, escape(guidance()))
        self.assertNotContains(response, 'Save guidance')

    def test_password_errors_are_field_specific_and_password_values_are_not_rendered(self):
        response = self.client.post('/settings/', {
            'action': 'password',
            'old_password': 'wrong-old-password',
            'new_password1': 'short',
            'new_password2': 'different',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["password_form"].errors)
        self.assertContains(response, 'id="id_old_password-errors"')
        self.assertContains(response, 'id="id_new_password2-errors"')
        self.assertContains(response, 'name="old_password"')
        self.assertNotContains(response, 'value="wrong-old-password"')
        self.assertNotContains(response, 'value="short"')
        self.assertNotContains(response, 'value="different"')
