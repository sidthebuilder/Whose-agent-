"""Make the local Mattermost (from scripts/switch_stack.py) easy to follow, for a demo or a screen recording:

  python scripts/mattermost_setup.py ["Your Name"]

- turns off collapsed reply threads, so the agents' replies (posted in threads) show in the channel itself
- turns off the first-run tips for the person's account
- sets the person's full name, if you give one (Mattermost here shows full names)

Signs in with the admin account from switch/stack/.env; nothing secret is printed.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(ln.split("=", 1) for ln in (ROOT / "switch" / "stack" / ".env").read_text(encoding="utf-8").splitlines()
           if "=" in ln)
MM = f"http://127.0.0.1:{ENV.get('MATTERMOST_HOST_PORT', '8065')}/api/v4"


def call(method: str, path: str, body=None, token: str = "") -> tuple[object, dict]:
    headers = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(MM + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return (json.loads(raw) if raw else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")


def main() -> None:
    full = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    first, _, last = full.partition(" ")
    _, headers = call("POST", "/users/login", {"login_id": ENV["MATTERMOST_ADMIN_USER"],
                                                "password": ENV["MATTERMOST_ADMIN_PASSWORD"]})
    token = headers.get("Token") or headers.get("token")
    if not token:
        sys.exit("Mattermost accepted the admin login but returned no session token")
    cfg, _ = call("PUT", "/config/patch", {"ServiceSettings": {"CollapsedThreads": "disabled",
                                                               "EnableTutorial": False}}, token)
    print(f"collapsed reply threads: {cfg['ServiceSettings']['CollapsedThreads']}; "
          f"tutorial: {cfg['ServiceSettings'].get('EnableTutorial')}")
    person = ENV.get("MATTERMOST_USER", "shashank")
    user, _ = call("GET", f"/users/username/{person}", token=token)
    if full:
        call("PUT", f"/users/{user['id']}/patch", {"first_name": first, "last_name": last}, token)
    prefs = [{"user_id": user["id"], "category": "tutorial_step", "name": user["id"], "value": "999"},
             {"user_id": user["id"], "category": "display_settings", "name": "collapsed_reply_threads",
              "value": "off"}]
    call("PUT", f"/users/{user['id']}/preferences", prefs, token)
    print(f"{person}: first-run tips off" + (f"; shown as {full!r}" if full else ""))


if __name__ == "__main__":
    main()
