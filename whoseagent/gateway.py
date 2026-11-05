"""A small client for the Switch dashboard API (the Gateway, under /gateway), for setting up the room by script:
sign in as the server's admin, register agents, set who may address them, and create a room from YAML.
Standard library only. The agent-facing side (event stream, operations, posting) is in switch.py.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class GatewayError(RuntimeError):
    pass


class Gateway:
    def __init__(self, base: str = "http://127.0.0.1:8000", timeout: int = 60) -> None:
        self.base, self.timeout, self.cookie = base.rstrip("/"), timeout, ""

    def call(self, method: str, path: str, body: object | None = None) -> object:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(f"{self.base}/gateway{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                for c in r.headers.get_all("Set-Cookie") or []:
                    if c.startswith("switch_auth="):
                        self.cookie = c.split(";", 1)[0]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            raise GatewayError(f"{method} /gateway{path}: HTTP {e.code} {detail}") from None
        except urllib.error.URLError as e:
            raise GatewayError(f"{method} /gateway{path}: cannot reach {self.base} ({e.reason})") from None
        return json.loads(raw) if raw else None

    def login(self, email: str, password: str) -> dict:
        me = self.call("POST", "/auth/login", {"email": email, "password": password})
        if not self.cookie:
            raise GatewayError("the server accepted the login but set no session cookie")
        return me

    def agents(self) -> list[dict]:
        out = self.call("GET", "/agents")
        return out if isinstance(out, list) else (out or {}).get("agents", [])

    def register_other(self, name: str, description: str, display_name: str = "", overwrite: bool = True) -> dict:
        body = {"name": name, "description": description, "overwrite": overwrite}
        if display_name:
            body["display_name"] = display_name
        return self.call("POST", "/agents/register-other", body)

    def agent(self, agent_id: str) -> dict:
        return self.call("GET", f"/agents/{agent_id}")

    def set_policy(self, agent_id: str, policy: dict | None) -> dict:
        return self.call("PUT", f"/agents/{agent_id}/addressing-policy", {"policy": policy})

    def rooms(self) -> list[dict]:
        out = self.call("GET", "/rooms")
        return out if isinstance(out, list) else (out or {}).get("rooms", [])

    def room(self, room_id: str) -> dict:
        return self.call("GET", f"/rooms/{room_id}")

    def room_from_yaml(self, text: str, inputs: dict | None = None) -> dict:
        return self.call("POST", "/rooms/from-yaml", {"yaml": text, "inputs": inputs or {}})

    def validate_template(self, text: str) -> dict:
        return self.call("POST", "/templates/validate", {"content": text})

    def add_users(self, room_id: str, names: list[str]) -> dict:
        return self.call("POST", f"/rooms/{room_id}/users", {"user_names": names})
