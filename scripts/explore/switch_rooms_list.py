"""List the rooms on the local Switch server with their agents and users (writes logs/rooms_list.txt)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from whoseagent.gateway import Gateway  # noqa: E402

ENV = dict(ln.split("=", 1) for ln in (ROOT / "switch" / "stack" / ".env").read_text(encoding="utf-8").splitlines()
           if "=" in ln)


def main() -> None:
    g = Gateway()
    g.login(ENV["GATEWAY_ADMIN_EMAIL"], ENV["GATEWAY_ADMIN_PASSWORD"])
    lines = []
    for r in g.rooms():
        lines.append(json.dumps({k: r.get(k) for k in ("id", "name", "channel_type", "bridge_display_name",
                                                       "connected_user_names", "agent_ids", "external_channel_url",
                                                       "created_at")}))
    for a in g.agents():
        lines.append(json.dumps({k: a.get(k) for k in ("id", "name", "connection_model", "connector_type")}))
    (ROOT / "logs" / "rooms_list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
