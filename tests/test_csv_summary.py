import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase


class CsvSummaryTests(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.spool = Path(directory)
        self.enterContext(self.settings(CSV_SPOOL=directory))
        self.client.force_login(get_user_model().objects.create_user(username="csv-owner"))

    def test_chat_does_not_offer_the_standalone_csv_flow(self):
        self.assertNotContains(self.client.get("/"), "Summarize a CSV")

    def test_chat_offers_csv_attachment(self):
        self.assertContains(self.client.get("/"), 'name="attachment"')

    def test_legacy_upload_is_no_longer_available(self):
        response = self.client.post("/csv/", {"file": SimpleUploadedFile("old.csv", b"a\n1\n")})
        self.assertEqual(response.status_code, 405)

    def test_previously_delivered_result_remains_private_and_discoverable(self):
        from superharry.models import CsvFile, CsvSummary
        owner = get_user_model().objects.get(username="csv-owner")
        record = CsvFile.objects.create(owner=owner, name="sales.csv", payload=b"a\n1\n")
        job = CsvSummary.objects.create(file=record)
        detail = f"/csv/{record.pk}/"
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="other-csv-user"))
        summary = {"rows": 2, "columns": [{"name": "Amount", "empty": 1}]}
        (self.spool / f"{job.job_id}.result").write_text(json.dumps({
            "status": "complete", "summary": summary,
        }), encoding="utf-8")
        self.assertContains(self.client.get("/"), "Previous CSV results")
        self.assertContains(self.client.get("/memories/"), "Previous CSV results")
        self.assertNotContains(other.get("/memories/"), "Previous CSV results")
        self.assertContains(self.client.get("/csv/"), "sales.csv")
        self.assertContains(self.client.get(detail), "Amount (1 empty cells)")
        self.assertEqual(self.client.get(f"{detail}download/").json(), summary)
        self.assertEqual(other.get(detail).status_code, 404)
        self.assertEqual(other.get(f"{detail}download/").status_code, 404)
