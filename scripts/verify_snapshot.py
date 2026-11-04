"""Check that every raw file in the 31 October 2026 snapshot still has the SHA-256 recorded when it was fetched.

  python scripts/verify_snapshot.py

Exit code 0 when all files match, 1 otherwise. Git keeps these files byte for byte (see .gitattributes), so a
fresh clone on any machine should pass.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SNAP = Path(__file__).resolve().parent.parent / "data" / "snapshot-2026-10-31"


def main() -> int:
    manifest = json.loads((SNAP / "manifest.json").read_text(encoding="utf-8"))
    bad = [m["file"] for m in manifest
           if hashlib.sha256((SNAP / m["file"]).read_bytes()).hexdigest() != m["sha256"]]
    print(f"{len(manifest)} snapshot files, {len(bad)} with a different SHA-256{': ' + ', '.join(bad) if bad else ''}.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
