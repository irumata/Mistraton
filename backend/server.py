"""Small read-only API, intended to run behind an HTTPS reverse proxy."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self.respond(200, {"status": "ok"})
        elif path == "/api/message":
            self.respond(200, {"message": "Hello from your Oracle Cloud VM!"})
        else:
            self.respond(404, {"error": "Not found"})

    def respond(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        origin = self.headers.get("Origin")
        allowed_origin = os.environ.get("ALLOWED_ORIGIN", "http://localhost:8080")
        if origin and origin == allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("127.0.0.1", int(os.environ.get("PORT", "8000"))), APIHandler
    )
    print(f"API listening on http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
