"""A small client for the local Mattermost's REST API (v4), signed in with an account from switch/stack/.env.
Used by switch_room.py (to archive a deleted room's channel) and record_demo.py. Standard library only.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def stack_env() -> dict[str, str]:
    return dict(ln.split("=", 1) for ln in (ROOT / "switch" / "stack" / ".env").read_text(encoding="utf-8").splitlines()
                if "=" in ln)


class MattermostError(RuntimeError):
    pass


class Mattermost:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.env = env or stack_env()
        self.base = f"http://127.0.0.1:{self.env.get('MATTERMOST_HOST_PORT', '8065')}/api/v4"
        self.token = ""

    def call(self, method: str, path: str, body: object | None = None):
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
            raise MattermostError(f"{method} {path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}") from None
        except urllib.error.URLError as e:
            raise MattermostError(f"Mattermost is not reachable at {self.base} ({e.reason})") from None

    def login_admin(self) -> dict:
        return self.call("POST", "/users/login", {"login_id": self.env["MATTERMOST_ADMIN_USER"],
                                                  "password": self.env["MATTERMOST_ADMIN_PASSWORD"]})

    def login_person(self) -> dict:
        return self.call("POST", "/users/login", {"login_id": self.env["MATTERMOST_USER"],
                                                  "password": self.env["MATTERMOST_USER_PASSWORD"]})

    def team(self) -> dict:
        return self.call("GET", f"/teams/name/{self.env.get('MATTERMOST_TEAM_NAME', 'switch')}")

    def archive_channels_named(self, display_name: str) -> list[str]:
        """Archive every open channel of the team shown as display_name (needs the admin). Returns their names."""
        team_id, gone, page = self.team()["id"], [], 0
        while True:
            rows = self.call("GET", f"/channels?per_page=200&page={page}&exclude_default_channels=true") or []
            for c in rows:
                if c.get("team_id") == team_id and c.get("display_name") == display_name and not c.get("delete_at"):
                    self.call("DELETE", f"/channels/{c['id']}")
                    gone.append(c["name"])
            if len(rows) < 200:
                return gone
            page += 1
