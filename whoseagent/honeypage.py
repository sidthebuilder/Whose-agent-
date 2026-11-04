"""A test page that records real agent traffic: who fetched it, from which address, with which name.

Run it on this machine and publish it through a tunnel (cloudflared), then ask AI assistants to open
/t/<label> (one label per assistant, for example /t/chatgpt). Every request is written to
logs/access.log in the combined log format, which `whoseagent check` and `watch` read like any web
server's log, and to logs/requests.jsonl with the headers that matter for attribution.

The page says plainly that it logs requests. It shows a verification word derived from the label, so you
can check that the assistant really read the copy this server sent, and it has a small form for testing
agents that write. Form contents are not stored, only their length and hash.

Security: it binds to 127.0.0.1 by default, serves no files, reads at most 64 KB of any body, and trusts the
CF-Connecting-IP / X-Forwarded-For headers only when the connection comes from this machine (the tunnel).
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import secrets
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths
from .logparse import format_line

MAX_BODY = 64 * 1024
LABEL = re.compile(r"^/t/([a-z0-9][a-z0-9_-]{0,39})(/note)?/?$")
KEEP_HEADERS = ("user-agent", "accept", "accept-language", "from", "referer", "signature", "signature-input",
                "signature-agent", "cf-connecting-ip", "cf-ipcountry", "cf-ray", "x-forwarded-for",
                "x-forwarded-proto", "via", "sec-ch-ua", "sec-fetch-mode", "sec-fetch-site")
WORDS = ("amber", "basalt", "cedar", "delta", "ember", "fjord", "granite", "harbor", "indigo", "juniper",
         "kestrel", "lagoon", "meadow", "nimbus", "obsidian", "pebble", "quartz", "rowan", "saffron", "tundra",
         "umber", "violet", "willow", "xenon", "yarrow", "zephyr")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>whose-agent test page</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; line-height: 1.5">
<h1>whose-agent test page</h1>
<p>This page is part of an open experiment that checks whether AI assistants fetch web pages from the
addresses their operators publish: <a href="https://github.com/sidthebuilder/whose-agent">github.com/sidthebuilder/whose-agent</a>.</p>
<p>Every request to this page is logged: the time, the IP address, the user agent and a few request headers.
Nothing else is collected.</p>
<p>The verification word for this page is <strong>{word}</strong>.</p>
<form method="post" action="/t/{label}/note">
<p><label>Leave a note (only its length is kept): <input name="note" maxlength="200"></label>
<button type="submit">Send</button></p></form>
</body></html>
"""
NOTE_PAGE = "<!doctype html><title>Thank you</title><p>Thank you. Your note was received.</p>"


class Recorder:
    """Appends one line per request to the access log and one JSON record to requests.jsonl."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.access, self.jsonl = folder / "access.log", folder / "requests.jsonl"
        salt_file = folder / "honeypage_salt.txt"
        if not salt_file.exists():
            salt_file.write_text(secrets.token_hex(16), encoding="utf-8")
        self.salt = salt_file.read_text(encoding="utf-8").strip()
        self.lock = threading.Lock()

    def word(self, label: str) -> str:
        h = hashlib.sha256(f"{self.salt}:{label}".encode()).digest()
        return WORDS[h[0] % len(WORDS)] + "-" + WORDS[h[1] % len(WORDS)]

    def write(self, line: str, record: dict) -> None:
        with self.lock:
            with open(self.access, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            with open(self.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")


def client_ip(peer: str, headers) -> str:
    """The real client address. Proxy headers are trusted only from this machine (the tunnel)."""
    try:
        local = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local = False
    if local:
        for name in ("CF-Connecting-IP", "X-Forwarded-For"):
            value = (headers.get(name) or "").split(",")[0].strip()
            try:
                return str(ipaddress.ip_address(value))
            except ValueError:
                continue
    return peer


def make_handler(rec: Recorder):
    class Handler(BaseHTTPRequestHandler):
        server_version = "whoseagent-honeypage/0.1"
        sys_version = ""
        timeout = 15

        def log_message(self, fmt, *args):  # the Recorder is the log
            pass

        def _handle(self) -> None:
            path = self.path.split("#")[0][:500]
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            body = self.rfile.read(length) if length > 0 else b""
            bare = path.split("?")[0]
            m = LABEL.match(bare)
            label = m.group(1) if m else ""
            served_word = ""
            reading = self.command in ("GET", "HEAD")
            if reading and bare == "/robots.txt":
                status, ctype, out = 200, "text/plain", b"User-agent: *\nAllow: /\n"
            elif reading and bare == "/favicon.ico":
                status, ctype, out = 204, "image/x-icon", b""
            elif reading and (bare == "/" or (m and not m.group(2))):
                label = label or "home"
                served_word = rec.word(label)
                status, ctype = 200, "text/html; charset=utf-8"
                out = PAGE.format(word=served_word, label=html.escape(label)).encode()
            elif m and m.group(2) and self.command == "POST":
                status, ctype, out = 200, "text/html; charset=utf-8", NOTE_PAGE.encode()
            else:
                status, ctype, out = 404, "text/plain", b"not found\n"
            ip = client_ip(self.client_address[0], self.headers)
            rid = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc)
            ua = self.headers.get("User-Agent", "")
            sig_agent = self.headers.get("Signature-Agent", "")
            safe_path = path.replace(" ", "%20").replace('"', "%22") or "/"
            line = format_line(ip, now, self.command, safe_path, self.request_version, status,
                               len(out), self.headers.get("Referer", ""), ua, sig_agent, rid)
            rec.write(line, {
                "ts": now.isoformat(timespec="seconds"), "rid": rid, "ip": ip, "peer": self.client_address[0],
                "method": self.command, "path": path, "label": label, "status": status, "word": served_word,
                "headers": {k: (self.headers.get(k) or "")[:500] for k in KEEP_HEADERS if self.headers.get(k)},
                "body_len": len(body), "body_sha256": hashlib.sha256(body).hexdigest() if body else ""})
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Robots-Tag", "noindex")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(out)

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _handle

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8787, folder: Path | None = None) -> ThreadingHTTPServer:
    rec = Recorder(folder or paths.LOGS)
    srv = ThreadingHTTPServer((host, port), make_handler(rec))
    srv.daemon_threads = True
    srv.recorder = rec  # type: ignore[attr-defined]
    return srv
