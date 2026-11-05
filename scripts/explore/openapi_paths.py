"""List the paths and methods in a saved OpenAPI file that contain a substring.

  python scripts/openapi_paths.py logs/switch_gateway_openapi.json agents/{agent_id}
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    want = sys.argv[2] if len(sys.argv) > 2 else ""
    for path, ops in spec["paths"].items():
        if want in path:
            for method, op in ops.items():
                if method in ("get", "post", "put", "patch", "delete"):
                    print(f"{method.upper()} {path} - {op.get('summary', '')}")


if __name__ == "__main__":
    main()
