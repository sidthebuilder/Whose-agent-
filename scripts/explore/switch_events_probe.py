"""Look at how the local Switch server delivers messages to an agent and takes replies (writes logs/events_probe.txt).

Reads the kickoff from the agents' notification queues, opens a long-poll connection, binds it to the room,
and posts one threaded test reply. Run once after scripts/switch_room.py setup.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from whoseagent.switch import load_credentials  # noqa: E402

OUT = []
STATE = json.loads((ROOT / "switch" / "stack" / "room.json").read_text(encoding="utf-8"))


def call(creds, method, path, body=None, headers=None, timeout=40):
    req = urllib.request.Request(creds.endpoint.rstrip("/") + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json",
                                          "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def log(title, status, text, n=1800):
    OUT.append(f"== {title}: HTTP {status}\n{text[:n]}\n")


def main() -> None:
    eg = load_credentials(ROOT / "agents" / "egress-check")
    aw = load_credentials(ROOT / "agents" / "abuse-writer")
    room = STATE["room_id"]
    log("writer notifications (kickoff?)", *call(aw, "GET", f"/agents/{aw.agent_id}/notifications?timeout=3"))
    conn = str(uuid.uuid4())
    log("egress events long poll", *call(eg, "GET", f"/agents/{eg.agent_id}/events?timeout=3&connection_id={conn}"
                                                    f"&scope=all&filter=all&start_from=0&client=whoseagent&client_version=0.1.0"))
    log("connect_to_room", *call(eg, "POST", f"/agents/{eg.agent_id}/ops/connect_to_room",
                                 {"room_id": room, "include_general_instructions": False},
                                 {"X-Switch-Connection-Id": conn}), 2500)
    log("list_participants", *call(eg, "POST", f"/agents/{eg.agent_id}/ops/list_participants", {},
                                   {"X-Switch-Connection-Id": conn}))
    log("read_context", *call(eg, "POST", f"/agents/{eg.agent_id}/ops/read_context", {"limit": 5, "room_id": room},
                              {"X-Switch-Connection-Id": conn}), 3000)
    log("typing", *call(eg, "POST", f"/agents/{eg.agent_id}/typing", {"room_id": room, "is_typing": True}))
    (ROOT / "logs" / "events_probe.txt").write_text("\n".join(OUT), encoding="utf-8")
    print("\n".join(OUT))


if __name__ == "__main__":
    main()
