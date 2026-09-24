#!/usr/bin/env python3
"""
Simulated internal network service for the SSRF training lab.

Run this ALONGSIDE the Django app (separate terminal, separate process),
listening only on 127.0.0.1:9000. In a real cloud environment this maps
to the EC2/GCP/Azure instance metadata service (normally at
169.254.169.254) and an internal admin panel that trusts requests
originating from localhost.

You (the "attacker") should NEVER be able to reach this directly from
your browser/curl over the network in a realistic setup — the whole
point is that it's only reachable FROM the Django server process itself,
which is exactly the trust boundary SSRF breaks.

    python3 internal_service.py
"""

from http.server import BaseHTTPRequestHandler, HTTPServer

FAKE_IAM_CREDENTIALS = """{
  "Code": "Success",
  "AccessKeyId": "ASIAFAKEACCESSKEY123",
  "SecretAccessKey": "fakeSecretKeyThatWouldBeCatastrophicIfLeaked/xyz",
  "Token": "FAKE.SESSION.TOKEN.abc123",
  "Expiration": "2099-01-01T00:00:00Z"
}"""

ADMIN_PANEL_HTML = """<html><body>
<h1>Internal Admin Panel</h1>
<p>Welcome, root. This panel is normally only reachable from inside the
VPC / from localhost, and should NEVER be reachable from the public
internet directly.</p>
<p>FLAG: SSRF{internal_admin_panel_reached_via_ssrf_pivot}</p>
</body></html>"""


class InternalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/latest/meta-data/iam/security-credentials"):
            self._respond(200, FAKE_IAM_CREDENTIALS, "application/json")
        elif self.path.startswith("/latest/meta-data"):
            self._respond(200, "iam/\nhostname\ninstance-id\n", "text/plain")
        elif self.path.startswith("/admin"):
            self._respond(200, ADMIN_PANEL_HTML, "text/html")
        else:
            self._respond(200, "Internal service root. Try /latest/meta-data/ or /admin", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode(errors="replace")
        print(f"[internal_service] Received POST to {self.path}: {body}")
        self._respond(200, "received", "text/plain")

    def _respond(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, fmt, *args):
        print(f"[internal_service] {self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 9000), InternalHandler)
    print("Simulated internal service listening on http://127.0.0.1:9000")
    print("Endpoints: /latest/meta-data/iam/security-credentials  |  /admin")
    server.serve_forever()
