import io
import json
import unittest
from unittest.mock import patch


class OllamaProvisioningTests(unittest.TestCase):
    @patch("scripts.provision.urllib.request.urlopen")
    @patch("scripts.provision.subprocess.run")
    def test_named_model_is_pulled_and_readiness_is_verified(self, run, urlopen):
        from scripts.provision import ensure_ollama_model

        urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps({
            "models": [{"name": "qwen3.5:4b"}],
        }).encode())
        self.assertEqual(ensure_ollama_model("qwen3.5:4b"), "ready")
        run.assert_called_once()
        self.assertIn("qwen3.5:4b", run.call_args.args[0])
        self.assertEqual(urlopen.call_args.args[0].full_url, "http://127.0.0.1:11434/api/tags")
