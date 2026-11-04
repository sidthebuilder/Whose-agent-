import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from whoseagent.switch import Credentials, SwitchClient, SwitchError, load_credentials


class FakeBridge(BaseHTTPRequestHandler):
    """The two agent-protocol calls whoseagent makes: the list_rooms operation and the plain message post."""
    calls: list = []

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"detail": "no"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null")
        FakeBridge.calls.append((self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != "Bearer tok":
            return self._send(401, {"detail": "bad token"})
        if self.path == "/agents/A1/ops/list_rooms":
            return self._send(200, {"result": {"rooms": [{"room_id": "r-9", "name": "site-whose-agent"},
                                                         {"room_id": "r-2", "name": "general"}]}})
        if self.path == "/agents/A1/message":
            return self._send(200, {"ok": True})
        self._send(404, {"detail": "unknown"})


@pytest.fixture
def bridge():
    FakeBridge.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeBridge)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_find_a_room_and_post_to_it(bridge):
    c = SwitchClient(Credentials(bridge, "tok", "A1"))
    assert c.health()
    assert c.find_room("Site-Whose-Agent") == "r-9"
    c.post("r-9", "**Verified** · claims GPTBot (OpenAI)")
    assert FakeBridge.calls[-1] == ("/agents/A1/message", "Bearer tok",
                                    {"room_id": "r-9", "content": "**Verified** · claims GPTBot (OpenAI)"})


def test_unknown_room_and_bad_token(bridge):
    with pytest.raises(SwitchError, match="Its rooms: general, site-whose-agent"):
        SwitchClient(Credentials(bridge, "tok", "A1")).find_room("missing")
    with pytest.raises(SwitchError, match="HTTP 401"):
        SwitchClient(Credentials(bridge, "wrong", "A1")).post("r-9", "x")


def test_unreachable_server():
    with pytest.raises(SwitchError, match="cannot reach"):
        SwitchClient(Credentials("http://127.0.0.1:9", "tok", "A1"), timeout=2).post("r", "x")


def test_credentials_from_the_file_switch_console_writes(tmp_path, monkeypatch):
    folder = tmp_path / ".switch" / "agents"
    folder.mkdir(parents=True)
    (folder / "site-egress-check.json").write_text(json.dumps({"env": {
        "SWITCH_API_ENDPOINT": "http://localhost:8080", "SWITCH_API_TOKEN": "tok", "SWITCH_AGENT_ID": "A1"}}))
    c = load_credentials(tmp_path)
    assert (c.endpoint, c.token, c.agent_id) == ("http://localhost:8080", "tok", "A1")
    monkeypatch.setenv("SWITCH_API_ENDPOINT", "http://other")
    monkeypatch.setenv("SWITCH_API_TOKEN", "t2")
    monkeypatch.setenv("SWITCH_AGENT_ID", "A2")
    assert load_credentials(tmp_path).agent_id == "A2"  # the environment wins


def test_missing_credentials(tmp_path):
    with pytest.raises(SwitchError, match="No Switch credentials"):
        load_credentials(None)
    with pytest.raises(SwitchError, match="found 0"):
        load_credentials(tmp_path)
