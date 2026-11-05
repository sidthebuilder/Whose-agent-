"""Talk to the room from a terminal: post as the person (the Mattermost account in switch/stack/.env) and print
what the agents reply. For testing the room without a browser.

  python scripts/room_say.py "@site-egress-check is 20.171.206.10 really GPTBot?"
  python scripts/room_say.py --replies 2 --wait 180 "@site-abuse-writer approve 20261105-101500-L3"
  python scripts/room_say.py --read 8        the last 8 messages in the room

Replies are printed as they arrive, until --replies messages from others have come or --wait seconds pass.
The room is the one scripts/switch_room.py created (switch/stack/room.json). Passwords are never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "switch" / "stack"


def load() -> tuple[dict, dict]:
    if not (STACK / ".env").exists() or not (STACK / "room.json").exists():
        sys.exit("No room yet: run python scripts/switch_stack.py up, then python scripts/switch_room.py setup")
    env = dict(ln.split("=", 1) for ln in (STACK / ".env").read_text(encoding="utf-8").splitlines() if "=" in ln)
    return env, json.loads((STACK / "room.json").read_text(encoding="utf-8"))


class Mattermost:
    def __init__(self, port: str) -> None:
        self.base, self.token = f"http://127.0.0.1:{port}/api/v4", ""
        self.names: dict[str, str] = {}

    def call(self, method: str, path: str, body=None):
        headers = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode("utf-8"))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if path == "/users/login":
                    self.token = r.headers.get("Token", "")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            sys.exit(f"Mattermost {method} {path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")
        except urllib.error.URLError as e:
            sys.exit(f"Mattermost is not reachable at {self.base} ({e.reason}). Is the stack up?")

    def name(self, user_id: str) -> str:
        if user_id not in self.names:
            u = self.call("GET", f"/users/{user_id}")
            full = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
            self.names[user_id] = full or u.get("username", user_id)
        return self.names[user_id]


def show(mm: Mattermost, post: dict) -> None:
    when = time.strftime("%H:%M:%S", time.localtime(post["create_at"] / 1000))
    reply = " (in a thread)" if post.get("root_id") else ""
    print(f"--- {when} {mm.name(post['user_id'])}{reply}\n{post['message']}\n", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="the message to post")
    ap.add_argument("--wait", type=int, default=90, help="seconds to wait for replies")
    ap.add_argument("--replies", type=int, default=1, help="stop after this many messages from others")
    ap.add_argument("--read", type=int, default=0, help="print the last N messages and stop")
    ap.add_argument("--thread", help="post in the thread of this Mattermost post id")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    env, room = load()
    mm = Mattermost(env.get("MATTERMOST_HOST_PORT", "8065"))
    me = mm.call("POST", "/users/login", {"login_id": env["MATTERMOST_USER"], "password": env["MATTERMOST_USER_PASSWORD"]})
    channel_name = (room.get("channel_url") or "").rstrip("/").rsplit("/", 1)[-1] or room["room_name"]
    team = env.get("MATTERMOST_TEAM_NAME", "switch")
    channel = mm.call("GET", f"/teams/name/{team}/channels/name/{channel_name}")
    if a.read:
        posts = mm.call("GET", f"/channels/{channel['id']}/posts?per_page={a.read}")
        for pid in reversed(posts["order"]):
            show(mm, posts["posts"][pid])
        return
    if not a.text:
        sys.exit("Give the message to post, or --read N.")
    body = {"channel_id": channel["id"], "message": a.text}
    if a.thread:
        body["root_id"] = a.thread
    sent = mm.call("POST", "/posts", body)
    print(f"--- posted as {me['username']} in {channel_name} (post {sent['id']})\n{a.text}\n", flush=True)
    seen, got, deadline = {sent["id"]}, 0, time.time() + a.wait
    while time.time() < deadline and got < a.replies:
        time.sleep(2)
        posts = mm.call("GET", f"/channels/{channel['id']}/posts?since={sent['create_at']}")
        for p in sorted(posts["posts"].values(), key=lambda p: p["create_at"]):
            if p["id"] in seen or p["user_id"] == me["id"] or p.get("type"):
                continue
            seen.add(p["id"])
            show(mm, p)
            got += 1
    if got < a.replies:
        print(f"({got} of {a.replies} replies within {a.wait} s)")


if __name__ == "__main__":
    main()
