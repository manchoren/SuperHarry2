from django.test import TestCase, override_settings
from django.urls import reverse


class ComponentCatalogTests(TestCase):
    def test_catalog_is_disabled_by_default(self):
        with override_settings(UI_COMPONENT_CATALOG_ENABLED=False):
            response = self.client.get(reverse("ui-component-catalog"))
        self.assertEqual(response.status_code, 404)

    def test_enabled_catalog_uses_local_styles_and_synthetic_examples(self):
        with override_settings(UI_COMPONENT_CATALOG_ENABLED=True):
            response = self.client.get(reverse("ui-component-catalog"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/superharry/components.css")
        self.assertContains(response, "jordan@example.com")
        self.assertContains(response, "catalog-code")
        self.assertEqual(response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")
