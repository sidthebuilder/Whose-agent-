"""Read what the local Switch server offers, so the room setup uses its real API: the OpenAPI schema (paths
and request bodies for agent registration, rooms, addressing policies) and the operation registry.

  python scripts/switch_probe.py            writes logs/switch_openapi.json and a summary to logs/switch_probe.txt
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8000"
KEYWORDS = ("register", "agents", "rooms", "addressing", "polic", "template", "yaml", "collaborations",
            "identities", "auth/login", "message", "events", "ops")


def main() -> None:
    out = ROOT / "logs"
    out.mkdir(exist_ok=True)
    lines = []
    for path in ("/health", "/openapi.json", "/gateway/openapi.json", "/docs"):
        try:
            with urllib.request.urlopen(BASE + path, timeout=20) as r:
                body = r.read()
            lines.append(f"{path}: HTTP 200, {len(body)} bytes")
            if path.endswith("openapi.json"):
                spec = json.loads(body)
                (out / f"switch{path.replace('/', '_')}").write_bytes(body)
                for p, ops in sorted(spec.get("paths", {}).items()):
                    if any(k in p for k in KEYWORDS):
                        for method, op in ops.items():
                            if method in ("get", "post", "put", "patch", "delete"):
                                lines.append(f"  {method.upper():6} {p}  {op.get('summary', '')}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{path}: {e}")
    (out / "switch_probe.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:200]))


if __name__ == "__main__":
    sys.exit(main())
