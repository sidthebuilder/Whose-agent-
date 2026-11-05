"""List the published tags of Switch's server images on GHCR (anonymous, public packages).

  python scripts/ghcr_tags.py [image ...]     default: switch-core gateway setup
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

NAMESPACE = "sandbox-quantum"


def tags(image: str) -> list[str]:
    repo = f"{NAMESPACE}/{image}"
    with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{repo}:pull", timeout=30) as r:
        token = json.loads(r.read())["token"]
    out, url = [], f"https://ghcr.io/v2/{repo}/tags/list?n=1000"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            out += json.loads(r.read()).get("tags") or []
            link = r.headers.get("Link", "")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = ("https://ghcr.io" + m.group(1)) if m else ""
    return out


def version_key(t: str):
    return tuple(int(x) for x in re.findall(r"\d+", t))


def main() -> None:
    for image in sys.argv[1:] or ["switch-core", "gateway", "setup"]:
        ts = tags(image)
        releases = sorted((t for t in ts if re.fullmatch(r"v?\d+\.\d+\.\d+", t)), key=version_key)
        print(f"{image}: {len(ts)} tags; newest releases: {', '.join(releases[-6:]) or 'none'}")


if __name__ == "__main__":
    main()
