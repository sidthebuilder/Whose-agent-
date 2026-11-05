"""Send requests to the test page from a cloud machine on Modal, as the scanners in the news do: rented cloud
servers that call themselves AI crawlers. The requests come from a real cloud address that is not yours.

  python -m modal run scripts/modal_spoof.py --url https://<tunnel>/                 one GPTBot request
  python -m modal run scripts/modal_spoof.py --url https://<tunnel>/ --case scanner  ClaudeBot asking for /.env
  python -m modal run scripts/modal_spoof.py --url https://<tunnel>/ --case all      every case below, in order

It runs on a small CPU container (no GPU) for a few seconds. Only send it to a page you run yourself.
"""
import modal

GPTBOT = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.3; +https://openai.com/gptbot"
CLAUDEBOT = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"
GOOGLE_EXTENDED = "Mozilla/5.0 (compatible; Google-Extended)"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
CASES = {  # name: (method, path, user agent)
    "gptbot": ("GET", "/t/spoof", GPTBOT),
    "scanner": ("GET", "/.env", CLAUDEBOT),
    "google-extended": ("GET", "/t/extended", GOOGLE_EXTENDED),
    "writer": ("POST", "/t/spoof/note", GPTBOT),
    "browser": ("GET", "/t/person", BROWSER),
}
app = modal.App("whose-agent-spoof")


@app.function(image=modal.Image.debian_slim(python_version="3.12"), timeout=120, cpu=0.25, memory=256)
def fetch(base: str, cases: list[str]) -> list[dict]:
    import time
    import urllib.error
    import urllib.request

    out = []
    for name in cases:
        method, path, ua = CASES[name]
        # X-Pinggy-No-Screen only tells a free Pinggy tunnel not to show browsers its one-time warning page
        # (https://pinggy.io/docs/http_tunnels/screening/); the test page does not record it
        headers = {"User-Agent": ua, "Accept": "text/html", "X-Pinggy-No-Screen": "1"}
        data = b"note=hello" if method == "POST" else None
        if data:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out.append({"case": name, "status": r.status, "bytes": len(r.read())})
        except urllib.error.HTTPError as e:
            out.append({"case": name, "status": e.code, "bytes": 0})
        time.sleep(1.5)
    return out


@app.local_entrypoint()
def main(url: str, case: str = "gptbot") -> None:
    if not url.startswith("https://"):
        raise SystemExit("--url must be the https address of your test page")
    cases = list(CASES) if case == "all" else case.split(",")
    unknown = [c for c in cases if c not in CASES]
    if unknown:
        raise SystemExit(f"unknown case {unknown}; choose from {', '.join(CASES)} or all")
    print(f"sending {', '.join(cases)} to the test page from a Modal cloud machine")
    for row in fetch.remote(url, cases):
        print(f"  {row['case']:<16} HTTP {row['status']} ({row['bytes']} bytes)")
