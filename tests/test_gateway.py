import io
import json
import unittest
from unittest.mock import patch

from django.test import override_settings


class GatewayStreamingTests(unittest.TestCase):
    @override_settings(OMNIROUTE_URL="http://gateway.invalid", OMNIROUTE_API_KEY="test-key")
    @patch("superharry.gateway.build_opener")
    def test_finished_sse_without_done_marker_is_a_complete_reply(self, build_opener):
        event = {"choices": [{"delta": {"content": "Local reply"}, "finish_reason": "stop"}]}
        body = b"data: " + json.dumps(event).encode() + b"\n\n"
        response = io.BytesIO(body)
        response.__enter__ = lambda: response
        response.__exit__ = lambda *args: None
        build_opener.return_value.open.return_value = response

        from superharry.gateway import stream_reply
        self.assertEqual(list(stream_reply([], route_id="local/model")), ["Local reply"])
