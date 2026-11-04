"""Save real RDAP answers as test fixtures, so the parser is tested on what the registries actually send.

  python scripts/fetch_rdap_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whoseagent.net import get_json  # noqa: E402
from whoseagent.rdap import parse_rdap  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CASES = [
    ("20.171.206.10", "rdap_arin_microsoft.json"),   # in OpenAI's GPTBot list
    ("160.79.104.10", "rdap_arin_anthropic.json"),   # in Anthropic's API outbound range
    ("49.36.10.1", "rdap_apnic_jio.json"),           # an Indian consumer ISP (APNIC)
]


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for ip, name in CASES:
        status, data, url = get_json(f"https://rdap.org/ip/{ip}")
        print(f"{ip:<15} HTTP {status}  {url}")
        if status == 200 and isinstance(data, dict):
            (FIXTURES / name).write_text(json.dumps(data, indent=1), encoding="utf-8")
            print("   ", json.dumps(parse_rdap(data)))


if __name__ == "__main__":
    main()
