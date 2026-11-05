"""Set up the room on the local Switch server (started with scripts/switch_stack.py), by script:

  python scripts/switch_room.py setup [SITE]   register the two agents (keys saved the way Switch Console saves
                                               them) as always-on agents, then create the room from
                                               switch/whose-agent.template.yaml. Start both agents first
                                               (python wa.py agent ...) so they answer the kickoff.
  python scripts/switch_room.py agents         register the agents only (nothing else is changed)
  python scripts/switch_room.py info           the room id and its Mattermost address
  python scripts/switch_room.py reset          delete the room (the agents stay registered)

The template's `agents:` block is for Switch Console, which starts Claude Code, Codex or OpenCode for each agent.
Here the agents are this repository's own (`wa.py agent`), registered directly, so only the room part of the
template is sent to the server (POST /gateway/rooms/from-yaml). Writes switch/stack/room.json.

Each agent is registered as "always on": it keeps its own connection to Switch open and answers as soon as it
is mentioned. (An agent registered with register-other starts as "session passive", and Switch then answers
mentions for it with "I read room messages asynchronously...".)
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from whoseagent.gateway import Gateway, GatewayError  # noqa: E402

STACK = ROOT / "switch" / "stack"
TEMPLATE = ROOT / "switch" / "whose-agent.template.yaml"
STATE = STACK / "room.json"
API = "http://127.0.0.1:8000"
TEAM = "site"
AGENTS = {  # folder -> (description shown to people in the room)
    "egress-check": "Works out whose AI agent sent a request, from the IP lists that 9 AI operators publish "
                    "and the regional internet registries. Answers questions about addresses and logs.",
    "abuse-writer": "Drafts the report to the operator or network behind a misbehaving agent. Never sends; "
                    "a person approves and sends.",
}
ALWAYS_ON = {"connection_model": "always_on", "message_exchange": True, "pre_invocation_mediation": [],
             "post_invocation_mediation": [], "event_reporting": []}


def stack_env() -> dict[str, str]:
    p = STACK / ".env"
    if not p.exists():
        sys.exit("No switch/stack/.env: start the server first with python scripts/switch_stack.py up")
    return dict(ln.split("=", 1) for ln in p.read_text(encoding="utf-8").splitlines() if "=" in ln)


def room_document(text: str) -> str:
    """The template without its Console-only parts: the agents block and the params only Console reads."""
    out, skipping = [], False
    console_params = ("repo_dir:", "provider:", "location:")
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        top = ln[:1] not in ("", " ", "#")
        if top:
            skipping = ln.startswith("agents:")
        if not skipping:
            stripped = ln.strip()
            if ln.startswith("  ") and not ln.startswith("    ") and stripped.startswith(console_params):
                i += 1  # drop this param and its indented body
                while i < len(lines) and lines[i].startswith("    "):
                    i += 1
                continue
            out.append(ln)
        i += 1
    return "\n".join(out) + "\n"


def credentials_path(folder: str, name: str) -> Path:
    return ROOT / "agents" / folder / ".switch" / "agents" / f"{name}.json"


def save_credentials(folder: str, name: str, agent_id: str, key: str) -> None:
    path = credentials_path(folder, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent.parent / ".gitignore").write_text("*\n", encoding="utf-8")
    path.write_text(json.dumps({"env": {"SWITCH_API_ENDPOINT": API, "SWITCH_API_TOKEN": key,
                                        "SWITCH_AGENT_ID": agent_id}}, indent=1), encoding="utf-8")


def as_agent(folder: str, name: str, method: str, path: str, body: object) -> tuple[int, object]:
    """Call the agent API with the agent's own key (PATCH /agents/{id} is there, not under /gateway)."""
    env = json.loads(credentials_path(folder, name).read_text(encoding="utf-8"))["env"]
    req = urllib.request.Request(f"{API}{path.format(id=env['SWITCH_AGENT_ID'])}", method=method,
                                 data=json.dumps(body).encode("utf-8"), headers={
                                     "Authorization": f"Bearer {env['SWITCH_API_TOKEN']}",
                                     "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]


def register_agents(g: Gateway) -> dict[str, str]:
    """Register each agent unless it is registered and its key is on this machine; make it always-on."""
    have = {a.get("name"): a for a in g.agents()}
    ids = {}
    for folder, desc in AGENTS.items():
        name = f"{TEAM}-{folder}"
        if name in have and credentials_path(folder, name).exists():
            ids[folder] = have[name]["id"]
            print(f"{name} is registered ({ids[folder]}); its key is in agents/{folder}/.switch/")
        else:
            reg = g.register_other(name, desc, display_name=name, overwrite=True)
            save_credentials(folder, name, reg["id"], reg["api_key"])
            ids[folder] = reg["id"]
            print(f"registered {name} ({reg['id']}); key saved in agents/{folder}/.switch/agents/{name}.json")
        status, out = as_agent(folder, name, "PATCH", "/agents/{id}", {"integration_profile": ALWAYS_ON})
        if status != 200:
            sys.exit(f"could not make {name} always-on: HTTP {status} {out}")
        model = g.agent(ids[folder]).get("connection_model")
        print(f"  connection model: {model}")
    return ids


def setup(site: str) -> None:
    env = stack_env()
    g = Gateway()
    g.login(env["GATEWAY_ADMIN_EMAIL"], env["GATEWAY_ADMIN_PASSWORD"])
    ids = register_agents(g)
    doc = room_document(TEMPLATE.read_text(encoding="utf-8"))
    check = g.validate_template(doc)
    for kind in ("errors", "warnings"):
        for item in check.get(kind, []):
            print(f"template {kind[:-1]}: {item.get('code')}: {item.get('message')}")
    existing = [r for r in g.rooms() if r.get("name") == f"{TEAM}-whose-agent"]
    if existing:
        room = existing[0]
        print(f"the room already exists: {room['id']} (python scripts/switch_room.py reset deletes it)")
    else:
        made = g.room_from_yaml(doc, {"team": TEAM, "site": site})
        room = g.room(made["room_id"])
        print(f"created room {room['name']} ({room['id']}); documents: {made.get('created_document_ids')}")
    # the Mattermost channel can be named differently from the room (a name freed by deleting a room stays
    # taken by the archived channel), so the browser address comes from the channel Switch reports
    team = env.get("MATTERMOST_TEAM_NAME", "switch")
    channel = (room.get("external_channel_url") or "").rstrip("/").rsplit("/", 1)[-1] or room["name"]
    state = {"room_id": room["id"], "room_name": room["name"],
             "browser_url": f"http://localhost:{env.get('MATTERMOST_HOST_PORT', '8065')}/{team}/channels/{channel}",
             "channel_url": room.get("external_channel_url"), "bridge": room.get("bridge_display_name"),
             "agents": ids, "users": room.get("connected_user_names")}
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(json.dumps(state, indent=1))


def reset() -> None:
    env = stack_env()
    g = Gateway()
    g.login(env["GATEWAY_ADMIN_EMAIL"], env["GATEWAY_ADMIN_PASSWORD"])
    gone = 0
    for r in g.rooms():
        if r.get("name") == f"{TEAM}-whose-agent":
            g.call("DELETE", f"/rooms/{r['id']}")
            print(f"deleted room {r['name']} ({r['id']})")
            gone += 1
    if not gone:
        print("no room to delete")
    STATE.unlink(missing_ok=True)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    try:
        if cmd == "setup":
            setup(sys.argv[2] if len(sys.argv) > 2 else "our website")
        elif cmd == "agents":
            env = stack_env()
            g = Gateway()
            g.login(env["GATEWAY_ADMIN_EMAIL"], env["GATEWAY_ADMIN_PASSWORD"])
            register_agents(g)
        elif cmd == "reset":
            reset()
        elif cmd == "info":
            print(STATE.read_text(encoding="utf-8") if STATE.exists() else "no room yet: run setup")
        else:
            sys.exit(__doc__)
    except GatewayError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
