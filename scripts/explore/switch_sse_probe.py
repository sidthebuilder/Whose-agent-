"""Open an event-stream connection for site-egress-check, keep it alive with the heartbeat, and run the room
operations that need it (connect_to_room, list_participants, read_context). Writes logs/sse_probe.txt.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from whoseagent.switch import load_credentials  # noqa: E402

OUT: list[str] = []
STATE = json.loads((ROOT / "switch" / "stack" / "room.json").read_text(encoding="utf-8"))


def req(creds, method, path, body=None, headers=None, timeout=30):
    r = urllib.request.Request(creds.endpoint.rstrip("/") + path, method=method,
                               data=None if body is None else json.dumps(body).encode(),
                               headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json",
                                        "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main() -> None:
    eg = load_credentials(ROOT / "agents" / "egress-check")
    room = STATE["room_id"]
    conn = str(uuid.uuid4())
    frames: list[str] = []
    stop = threading.Event()

    def stream():
        r = urllib.request.Request(
            f"{eg.endpoint}/agents/{eg.agent_id}/events?connection_id={conn}&scope=all&filter=addressed"
            f"&start_from=head&client=whoseagent&client_version=0.1.0&rooms={room}",
            headers={"Authorization": f"Bearer {eg.token}", "Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                OUT.append(f"stream open: HTTP {resp.status} {resp.headers.get('Content-Type')}")
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    frames.append(line)
                    if stop.is_set():
                        break
        except Exception as e:  # noqa: BLE001
            OUT.append(f"stream ended: {e!r}")

    threading.Thread(target=stream, daemon=True).start()

    def beat():
        while not stop.is_set():
            s, t = req(eg, "POST", f"/agents/{eg.agent_id}/connection/beat", {"connection_id": conn})
            if s != 200:
                OUT.append(f"beat: HTTP {s} {t[:200]}")
            time.sleep(2)

    time.sleep(2)
    threading.Thread(target=beat, daemon=True).start()
    time.sleep(1)
    h = {"X-Switch-Connection-Id": conn}
    for name, body in (("connect_to_room", {"room_id": room, "include_general_instructions": False}),
                       ("list_participants", {}), ("read_context", {"limit": 3})):
        s, t = req(eg, "POST", f"/agents/{eg.agent_id}/ops/{name}", body, h)
        OUT.append(f"== {name}: HTTP {s}\n{t[:2500]}")
    stop.set()
    OUT.append("== first stream frames:\n" + "\n".join(frames[:30]))
    (ROOT / "logs" / "sse_probe.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print("\n".join(OUT))


if __name__ == "__main__":
    main()
