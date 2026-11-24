import io
import os
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from tests import live_chat_smoke


class LiveChatSmokeTests(TestCase):
    def run_smoke(self, saved_reply):
        class ChatServer(BaseHTTPRequestHandler):
            def respond(self, body, **headers):
                self.send_response(200)
                for name, value in headers.items():
                    self.send_header(name.replace("_", "-"), value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/login/":
                    self.respond(b"Sign in", Set_Cookie="csrftoken=test; Path=/")
                elif self.path == "/chat/1/":
                    self.respond(b"<p>Reply only with the word OK.</p>" + saved_reply)
                else:
                    self.send_error(404)

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/login/":
                    self.respond(b"Model: openrouter/google/gemma-4-26b-a4b-it:free")
                elif self.path == "/chat/send/":
                    self.respond(
                        b'{"text":"Saved <"}\n{"text":"reply>"}\n{"done":true}\n',
                        Location="/chat/1/",
                    )
                else:
                    self.send_error(404)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), ChatServer) as server:
            thread = Thread(target=server.serve_forever)
            thread.start()
            try:
                with TemporaryDirectory() as directory:
                    password = Path(directory) / "recovery_password"
                    password.write_text("fixture-password", encoding="utf-8")
                    output = io.StringIO()
                    with (
                        patch.dict(os.environ, {"RECOVERY_PASSWORD_FILE": str(password)}),
                        patch.object(live_chat_smoke, "BASE_URL", f"http://127.0.0.1:{server.server_port}"),
                        redirect_stdout(output),
                    ):
                        live_chat_smoke.main()
                    return output.getvalue()
            finally:
                server.shutdown()
                thread.join()

    def test_rejects_streamed_reply_missing_from_saved_assistant_message(self):
        with self.assertRaisesRegex(SystemExit, "Saved conversation does not show the streamed assistant reply"):
            self.run_smoke(b"<p>Saved &lt;reply&gt;</p><strong>Assistant</strong><p></p>")

    def test_accepts_streamed_reply_rendered_in_saved_assistant_message(self):
        output = self.run_smoke(b"<strong>Assistant</strong>\n<p>Saved &lt;reply&gt;</p>")
        self.assertIn("Live Django chat passed", output)
