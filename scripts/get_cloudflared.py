"""Download cloudflared (Cloudflare's tunnel client) into tools/, for a public address for the test page.

  python scripts/get_cloudflared.py
"""
from __future__ import annotations

import platform
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"


def main() -> None:
    system, machine = platform.system(), platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    name = {"Windows": f"cloudflared-windows-{arch}.exe", "Linux": f"cloudflared-linux-{arch}"}.get(system)
    if not name:
        sys.exit("On macOS: brew install cloudflared")
    out = ROOT / "tools" / ("cloudflared.exe" if system == "Windows" else "cloudflared")
    if out.exists():
        print(f"{out.relative_to(ROOT)} is already there")
        return
    out.parent.mkdir(exist_ok=True)
    print(f"downloading {BASE + name}")
    urllib.request.urlretrieve(BASE + name, out)
    if system != "Windows":
        out.chmod(0o755)
    print(f"saved {out.relative_to(ROOT)} ({out.stat().st_size // 1_000_000} MB)")


if __name__ == "__main__":
    main()
