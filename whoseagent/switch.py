"""A small client for the Switch agent protocol: enough to post into a room and find a room by name.

Credentials are the ones Switch Console writes when it registers an agent, in
<agent working directory>/.switch/agents/<agent name>.json:

    {"env": {"SWITCH_API_ENDPOINT": "...", "SWITCH_API_TOKEN": "...", "SWITCH_AGENT_ID": "..."}}

or the same three names as environment variables. Posting uses `POST /agents/{id}/message`, which needs
no event stream, so the log watcher can post in the agent's name while the agent's own session runs.
See https://docs.switchagents.ai/flintai/switch/internals/agent-protocol
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class SwitchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    endpoint: str
    token: str
    agent_id: str
    source: str = ""


def load_credentials(agent_dir: str | Path | None = None, agent_name: str | None = None) -> Credentials:
    env = os.environ
    if env.get("SWITCH_API_ENDPOINT") and env.get("SWITCH_API_TOKEN") and env.get("SWITCH_AGENT_ID"):
        return Credentials(env["SWITCH_API_ENDPOINT"], env["SWITCH_API_TOKEN"], env["SWITCH_AGENT_ID"], "environment")
    if agent_dir is None:
        raise SwitchError("No Switch credentials: pass --agent-dir (the agent's working directory) "
                          "or set SWITCH_API_ENDPOINT, SWITCH_API_TOKEN and SWITCH_AGENT_ID.")
    folder = Path(agent_dir) / ".switch" / "agents"
    files = [folder / f"{agent_name}.json"] if agent_name else sorted(folder.glob("*.json"))
    files = [f for f in files if f.exists()]
    if len(files) != 1:
        raise SwitchError(f"Expected one credentials file in {folder}, found {len(files)}. "
                          f"Name the agent with --agent-name.")
    e = json.loads(files[0].read_text(encoding="utf-8")).get("env", {})
    try:
        return Credentials(e["SWITCH_API_ENDPOINT"], e["SWITCH_API_TOKEN"], e["SWITCH_AGENT_ID"], str(files[0]))
    except KeyError as k:
        raise SwitchError(f"{files[0]} has no {k}") from None


class SwitchClient:
    def __init__(self, creds: Credentials, timeout: int = 20) -> None:
        self.creds, self.timeout = creds, timeout
        self.base = creds.endpoint.rstrip("/")

    def _call(self, method: str, path: str, body: object | None = None) -> object:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.creds.token}", "Content-Type": "application/json",
            "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else ""
            raise SwitchError(f"{method} {path}: HTTP {e.code} {detail}") from None
        except urllib.error.URLError as e:
            raise SwitchError(f"{method} {path}: cannot reach {self.base} ({e.reason})") from None
        return json.loads(raw) if raw else None

    def health(self) -> bool:
        try:
            self._call("GET", "/health")
            return True
        except SwitchError:
            return False

    def op(self, name: str, args: dict | None = None) -> object:
        """Run one operation. The arguments object goes in the body unwrapped; the result is unwrapped."""
        out = self._call("POST", f"/agents/{self.creds.agent_id}/ops/{name}", args or {})
        return out.get("result") if isinstance(out, dict) and "result" in out else out

    def rooms(self) -> list[dict]:
        """The rooms this agent belongs to, as dicts with at least an id and a name when the server gives them."""
        out = self.op("list_rooms")
        found: list[dict] = []

        def walk(node):
            if isinstance(node, dict):
                rid = node.get("room_id") or node.get("id")
                if rid and ("name" in node or "room_name" in node):
                    found.append({"id": rid, "name": node.get("name") or node.get("room_name")})
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(out)
        return found

    def find_room(self, name: str) -> str:
        rooms = self.rooms()
        exact = [r for r in rooms if str(r["name"]).strip().lower() == name.strip().lower()]
        if len(exact) == 1:
            return exact[0]["id"]
        names = ", ".join(sorted({str(r["name"]) for r in rooms})) or "none"
        raise SwitchError(f"No single room named {name!r} for this agent. Its rooms: {names}")

    def post(self, room_id: str, content: str) -> object:
        return self._call("POST", f"/agents/{self.creds.agent_id}/message", {"room_id": room_id, "content": content})
