"""Copy the requests the Modal cloud machine sent on 5 November 2026 (python scripts/room.py spoof --case all)
from the test page's log into examples/access.log, byte for byte, for the README's quick test.

  python scripts/make_example_log.py 32.192.72.106

Only lines from the given address are copied, so no other visitor's address can end up in the repository.
"""
import ipaddress
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ip = str(ipaddress.ip_address(sys.argv[1]))
lines = [ln for ln in (ROOT / "logs" / "access.log").read_bytes().split(b"\n") if ln.startswith(ip.encode() + b" ")]
out = ROOT / "examples" / "access.log"
out.parent.mkdir(exist_ok=True)
out.write_bytes(b"\n".join(lines) + b"\n")
print(f"{len(lines)} lines from {ip} written to {out.relative_to(ROOT)}")
