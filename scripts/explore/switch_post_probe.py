"""Check what the log watcher can do without an open agent connection: list its rooms, post, read history.
Uses site-egress-check's key from agents/egress-check/.switch/ (never printed). Writes logs/post_probe.txt.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREDS = json.loads((ROOT / "agents/egress-check/.switch/agents/site-egress-check.json").read_text("utf-8"))["env"]
ROOM = json.loads((ROOT / "switch/stack/room.json").read_text("utf-8"))["room_id"]
BASE, AID, KEY = CREDS["SWITCH_API_ENDPOINT"], CREDS["SWITCH_AGENT_ID"], CREDS["SWITCH_API_TOKEN"]


def call(method: str, path: str, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")[:1500]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:600]


out = []
for label, method, path, body in (
        ("moderation rooms", "GET", f"/agents/{AID}/moderation/rooms", None),
        ("post message", "POST", f"/agents/{AID}/message",
         {"room_id": ROOM, "content": "Connection check from the log watcher (this room is about to be recreated)."}),
        ("history", "GET", f"/agents/{AID}/rooms/{ROOM}/history?limit=3", None)):
    status, text = call(method, path, body)
    out.append(f"== {label}: HTTP {status}\n{text}\n")
(ROOT / "logs" / "post_probe.txt").write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
sys.exit(0)
