from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
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
        self.assertContains(self.client.get('/'), 'avatar-initial')
        response = self.client.post('/settings/', {'action': 'avatar', 'avatar': self.image()})
        self.assertContains(response, 'Profile image updated')
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
