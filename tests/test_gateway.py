"""The dashboard API client used to set up the room by script (whoseagent/gateway.py), against a stand-in."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from whoseagent.gateway import Gateway, GatewayError


class FakeGateway(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def _send(self, status, obj, cookie=""):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null")

    def do_POST(self):
        body = self._body()
        FakeGateway.calls.append(("POST", self.path, self.headers.get("Cookie"), body))
        if self.path == "/gateway/auth/login":
            if body != {"email": "admin@switch.local", "password": "right"}:
                return self._send(401, {"detail": "Invalid credentials"})
            return self._send(200, {"name": "Admin"}, cookie="switch_auth=s3ss10n; Path=/; HttpOnly; SameSite=Lax")
        if self.headers.get("Cookie") != "switch_auth=s3ss10n":
            return self._send(401, {"detail": "not signed in"})
        if self.path == "/gateway/agents/register-other":
            return self._send(200, {"id": "A1", "api_key": "k"})
        if self.path == "/gateway/rooms/from-yaml":
            return self._send(200, {"room_id": "R1", "created_document_ids": ["D1"]})
        self._send(404, {"detail": "unknown"})

    def do_GET(self):
        FakeGateway.calls.append(("GET", self.path, self.headers.get("Cookie"), None))
        if self.headers.get("Cookie") != "switch_auth=s3ss10n":
            return self._send(401, {"detail": "not signed in"})
        if self.path == "/gateway/agents":
            return self._send(200, [{"id": "A1", "name": "site-egress-check"}])
        if self.path == "/gateway/rooms":
            return self._send(200, {"rooms": [{"id": "R1", "name": "site-whose-agent"}]})
        self._send(404, {"detail": "unknown"})


@pytest.fixture
def gateway():
    FakeGateway.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeGateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_sign_in_keeps_the_session_cookie_for_later_calls(gateway):
    g = Gateway(gateway)
    assert g.login("admin@switch.local", "right") == {"name": "Admin"}
    assert g.agents() == [{"id": "A1", "name": "site-egress-check"}]
    assert g.rooms() == [{"id": "R1", "name": "site-whose-agent"}]
    assert g.register_other("site-egress-check", "checks addresses") == {"id": "A1", "api_key": "k"}
    assert FakeGateway.calls[-1][3] == {"name": "site-egress-check", "description": "checks addresses",
                                        "overwrite": True}
    assert g.room_from_yaml("version: 1", {"team": "site"})["room_id"] == "R1"
    assert FakeGateway.calls[-1][3] == {"yaml": "version: 1", "inputs": {"team": "site"}}


def test_errors_name_the_call_and_the_status(gateway):
    with pytest.raises(GatewayError, match=r"POST /gateway/auth/login: HTTP 401 .*Invalid credentials"):
        Gateway(gateway).login("admin@switch.local", "wrong")
    with pytest.raises(GatewayError, match="HTTP 401"):
        Gateway(gateway).agents()  # not signed in
    with pytest.raises(GatewayError, match="cannot reach"):
        Gateway("http://127.0.0.1:9", timeout=2).agents()
