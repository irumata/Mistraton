import http.client
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from server import APIHandler


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), APIHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, origin=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        self.addCleanup(connection.close)
        headers = {"Origin": origin} if origin else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response, json.loads(response.read())

    def test_health(self):
        response, data = self.request("/api/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(data, {"status": "ok"})
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(
            response.getheader("Content-Type"), "application/json; charset=utf-8"
        )

    def test_message_with_query_string(self):
        response, data = self.request("/api/message?check=1")
        self.assertEqual(response.status, 200)
        self.assertEqual(data, {"message": "Hello from your Oracle Cloud VM!"})

    def test_unknown_path(self):
        response, data = self.request("/not-found")
        self.assertEqual(response.status, 404)
        self.assertEqual(data, {"error": "Not found"})

    @patch.dict(os.environ, {"ALLOWED_ORIGIN": "https://example.pages.dev"})
    def test_cors(self):
        for origin in (
            "https://example.pages.dev",
            "https://untrusted.example",
            "https://example.pages.dev.untrusted.example",
            None,
        ):
            with self.subTest(origin=origin):
                response, _ = self.request("/api/message", origin)
                expected = origin if origin == "https://example.pages.dev" else None
                self.assertEqual(
                    response.getheader("Access-Control-Allow-Origin"), expected
                )
                self.assertEqual(response.getheader("Vary"), "Origin")


if __name__ == "__main__":
    unittest.main()
