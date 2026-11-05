import http.client
import json
import threading

import pytest

from whoseagent.honeypage import Recorder, client_ip, serve
from whoseagent.logparse import parse_line


@pytest.fixture
def server(tmp_path):
    srv = serve("127.0.0.1", 0, tmp_path)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, path, method="GET", headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def last(path):
    return path.read_text(encoding="utf-8").splitlines()[-1]


def test_the_page_logs_who_fetched_it(server):
    ua = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot"
    status, body = call(server, "/t/chatgpt", headers={
        "User-Agent": ua, "CF-Connecting-IP": "20.171.206.10", "Signature-Agent": '"https://chatgpt.com"'})
    rec = server.recorder
    assert status == 200
    assert rec.word("chatgpt").encode() in body and b"logged" in body
    r = parse_line(last(rec.access))
    assert (r.ip, r.method, r.path, r.status) == ("20.171.206.10", "GET", "/t/chatgpt", 200)
    assert r.ua == ua and "chatgpt.com" in r.signature_agent
    j = json.loads(last(rec.jsonl))
    assert (j["label"], j["word"], j["peer"]) == ("chatgpt", rec.word("chatgpt"), "127.0.0.1")
    assert j["headers"]["user-agent"] == ua


def test_the_form_keeps_only_the_length_of_a_note(server):
    status, _ = call(server, "/t/claude/note", method="POST", body=b"note=hello+there",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 200
    assert parse_line(last(server.recorder.access)).is_write
    j = json.loads(last(server.recorder.jsonl))
    assert j["body_len"] == 16 and "hello" not in server.recorder.jsonl.read_text(encoding="utf-8")


def test_other_paths(server):
    assert call(server, "/robots.txt")[0] == 200
    assert call(server, "/favicon.ico")[0] == 204
    assert call(server, "/etc/passwd")[0] == 404
    assert call(server, '/t/x"y')[0] == 404
    status, body = call(server, "/t/perplexity", method="HEAD")
    assert status == 200 and body == b""
    assert parse_line(last(server.recorder.access)).method == "HEAD"
    assert all(parse_line(x) for x in server.recorder.access.read_text(encoding="utf-8").splitlines())


def test_proxy_headers_are_trusted_only_from_this_machine():
    assert client_ip("203.0.113.9", {"CF-Connecting-IP": "20.171.206.10"}) == "203.0.113.9"
    assert client_ip("127.0.0.1", {"X-Forwarded-For": "20.171.206.10, 10.0.0.1"}) == "20.171.206.10"
    assert client_ip("127.0.0.1", {"CF-Connecting-IP": "not-an-ip"}) == "127.0.0.1"
    assert client_ip("::1", {"CF-Connecting-IP": "2001:db8::5"}) == "2001:db8::5"


def test_tunnel_headers_with_ports_and_the_forwarded_header():
    assert client_ip("127.0.0.1", {"X-Forwarded-For": "34.229.10.5:50412"}) == "34.229.10.5"
    assert client_ip("127.0.0.1", {"X-Forwarded-For": "[2001:db8::7]:50412"}) == "2001:db8::7"
    assert client_ip("127.0.0.1", {"Forwarded": 'for="[2001:db8::7]:4711";proto=https'}) == "2001:db8::7"
    assert client_ip("127.0.0.1", {"Forwarded": "for=34.229.10.5;proto=https, for=10.0.0.1"}) == "34.229.10.5"
    assert client_ip("203.0.113.9", {"Forwarded": "for=34.229.10.5"}) == "203.0.113.9"


def test_verification_words_are_stable_per_label(tmp_path):
    a, b = Recorder(tmp_path), Recorder(tmp_path)
    assert a.word("chatgpt") == b.word("chatgpt")
    assert "-" in a.word("chatgpt")
