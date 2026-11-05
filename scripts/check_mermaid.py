"""Check that every Mermaid diagram in a Markdown file parses, with Mermaid's own parser in a browser.

  .venv\\Scripts\\python scripts/check_mermaid.py README.md

GitHub shows "Unable to render rich display" for a diagram that doesn't parse; this finds that before a push.
Needs Playwright with Edge (demo/requirements.txt) and internet access for mermaid from jsdelivr.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

text = Path(sys.argv[1] if len(sys.argv) > 1 else "README.md").read_text(encoding="utf-8")
blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
html = """<!doctype html><script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
mermaid.initialize({startOnLoad: false});
window.check = async (src) => { try { await mermaid.parse(src); return 'ok'; } catch (e) { return String(e.message || e); } };
window.ready = true;
</script>"""
bad = 0
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    page = b.new_page()
    page.set_content(html)
    page.wait_for_function("window.ready === true", timeout=60000)
    for i, src in enumerate(blocks, 1):
        result = page.evaluate("(s) => window.check(s)", src)
        print(f"diagram {i}: {result if result == 'ok' else 'ERROR ' + result}")
        bad += result != "ok"
    b.close()
print(f"{len(blocks)} diagrams, {bad} with errors")
sys.exit(1 if bad else 0)
