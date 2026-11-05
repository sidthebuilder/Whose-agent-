"""One-off look at the local Switch server, to build the room setup on its real behaviour (writes logs/explore_*).

Registers the two agents (or updates them), saves their credentials the way Switch Console does, reads one
agent's default addressing policy, the agent-side OpenAPI (fetched with that agent's key), the operation
registry and the room-template schema.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from whoseagent.gateway import Gateway  # noqa: E402

LOGS = ROOT / "logs"
ENV = dict(ln.split("=", 1) for ln in (ROOT / "switch" / "stack" / ".env").read_text(encoding="utf-8").splitlines()
           if "=" in ln)
AGENTS = {
    "egress-check": ("site-egress-check", "Works out whose AI agent sent a request, from the IP lists that 9 AI "
                     "operators publish and the regional internet registries."),
    "abuse-writer": ("site-abuse-writer", "Drafts the report to the operator or network behind a misbehaving "
                     "agent. Never sends; a person approves and sends."),
}


def save_credentials(folder: str, name: str, agent_id: str, key: str) -> Path:
    path = ROOT / "agents" / folder / ".switch" / "agents" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent.parent / ".gitignore").write_text("*\n", encoding="utf-8")
    path.write_text(json.dumps({"env": {"SWITCH_API_ENDPOINT": "http://127.0.0.1:8000", "SWITCH_API_TOKEN": key,
                                        "SWITCH_AGENT_ID": agent_id}}, indent=1), encoding="utf-8")
    return path


def bearer_get(path: str, key: str) -> bytes:
    req = urllib.request.Request("http://127.0.0.1:8000" + path, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main() -> None:
    g = Gateway()
    me = g.login(ENV["GATEWAY_ADMIN_EMAIL"], ENV["GATEWAY_ADMIN_PASSWORD"])
    notes = [f"signed in as {me.get('name')} ({me.get('role')}), server {me.get('server', {}).get('version')}"]
    keys = {}
    for folder, (name, desc) in AGENTS.items():
        reg = g.register_other(name, desc, display_name=name)
        keys[folder] = (reg["id"], reg["api_key"])
        save_credentials(folder, name, reg["id"], reg["api_key"])
        notes.append(f"registered {name}: id {reg['id']} (key saved under agents/{folder}/.switch/, not printed)")
    agent_id, key = keys["egress-check"]
    detail = g.agent(agent_id)
    (LOGS / "explore_agent_detail.json").write_text(json.dumps(detail, indent=1), encoding="utf-8")
    notes.append(f"default addressing policy: {json.dumps(detail.get('addressing_policy'))}")
    for path, out in (("/openapi.json", "explore_agent_openapi.json"), (f"/agents/{agent_id}/ops", "explore_ops.json")):
        try:
            (LOGS / out).write_bytes(bearer_get(path, key))
            notes.append(f"saved {path} -> logs/{out}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"{path}: {e}")
    try:
        schema = g.call("GET", "/rooms/template-schema")
        (LOGS / "explore_template_schema.json").write_text(json.dumps(schema, indent=1), encoding="utf-8")
        notes.append("saved the room template schema -> logs/explore_template_schema.json")
    except Exception as e:  # noqa: BLE001
        notes.append(f"template schema: {e}")
    bridges = g.call("GET", "/collaborations")
    notes.append("bridges: " + json.dumps([{k: b.get(k) for k in ("bridge_id", "bridge_type", "display_name",
                                                                   "is_default")} for b in bridges]))
    (LOGS / "explore_notes.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    print("\n".join(notes))


if __name__ == "__main__":
    main()
