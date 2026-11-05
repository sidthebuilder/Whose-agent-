"""Screenshot a local page with Microsoft Edge (Playwright), to check how it looks.

  .venv\\Scripts\\python scripts/screenshot.py docs/index.html logs/site.png [width] [height] [--full]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    page_path, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    nums = [int(a) for a in sys.argv[3:] if a.isdigit()]
    width, height = (nums + [1280, 800])[:2]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(page_path.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(500)
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page="--full" in sys.argv)
        browser.close()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
