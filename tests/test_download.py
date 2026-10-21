from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch

from proxywrap.errors import ProxyWrapError
from proxywrap.update import download


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agents = []
        agents = cls.agents

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                agents.append(self.headers.get("User-Agent"))
                if self.path == "/error?token=private":
                    self.send_error(403)
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://example.com/private-token")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'rules: ["MATCH,DIRECT"]\n')

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_actual_http_download_and_user_agent(self):
        self.assertIn(b"MATCH,DIRECT", download(self.base, "CustomClash"))
        self.assertEqual(self.agents[-1], "CustomClash")

    def test_http_error_redacts_url_and_token(self):
        with self.assertRaises(ProxyWrapError) as result:
            download(self.base + "/error?token=private")
        self.assertNotIn("token", str(result.exception))
        self.assertNotIn(self.base, str(result.exception))

    def test_insecure_redirect_rejected(self):
        with self.assertRaises(ProxyWrapError):
            download(self.base + "/redirect")

    def test_size_limit(self):
        with patch("proxywrap.update.MAX_BYTES", 4), self.assertRaises(ProxyWrapError):
            download(self.base)

    def test_control_character_never_reaches_http_client(self):
        with self.assertRaises(ProxyWrapError) as result:
            download(self.base + "/\x00private-token")
        self.assertNotIn("private-token", str(result.exception))
