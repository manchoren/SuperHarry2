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

    def test_owned_file_requires_approval_and_summary_is_private(self):
        self.assertContains(self.client.get("/"), "Summarize a CSV")
        payload = b"Name,Amount\nAlpha,4\nBeta,\n"
        uploaded = self.client.post("/csv/", {
            "file": SimpleUploadedFile("sales.csv", payload, content_type="text/csv"),
        })
        detail = uploaded["Location"]
        self.assertContains(self.client.get(detail), "Approve summary for this file")
        self.assertEqual(self.client.post(f"{detail}approve/").status_code, 400)
        self.assertEqual(self.client.get(f"{detail}download/").status_code, 404)
        self.assertEqual(list(self.spool.iterdir()), [])

        other = Client()
        other.force_login(get_user_model().objects.create_user(username="other-csv-user"))
        self.assertEqual(other.post(f"{detail}approve/", {"confirm": "yes"}).status_code, 404)
        self.assertEqual(other.get(detail).status_code, 404)
        self.assertRedirects(self.client.post(f"{detail}approve/", {"confirm": "yes"}), detail)
        jobs = list(self.spool.glob("*.input"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].read_bytes(), payload)

        summary = {"rows": 2, "columns": [{"name": "Amount", "empty": 1}]}
        jobs[0].with_suffix(".result").write_text(json.dumps({
            "status": "complete", "summary": summary,
        }), encoding="utf-8")
        jobs[0].unlink()
        self.assertContains(self.client.get(detail), "Amount (1 empty cells)")
        self.assertEqual(self.client.get(f"{detail}download/").json(), summary)
        self.assertEqual(other.get(f"{detail}download/").status_code, 404)
