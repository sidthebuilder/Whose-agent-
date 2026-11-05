"""One HTTP helper for everything that fetches: the operators' range files and the registries (RDAP).
Standard library only. Adapted from the Registry Scorecard (github.com/sidthebuilder/declared-egress)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (compatible; whoseagent/0.1; +https://github.com/sidthebuilder/Whose-agent-)"


def http_get(url: str, timeout: int = 30, accept: str = "application/json, */*") -> dict:
    """GET a URL, following redirects. Returns status, final URL, content type and body. Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "final_url": r.geturl(),
                    "type": r.headers.get("Content-Type", ""), "body": r.read()}
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return {"status": e.code, "final_url": url,
                "type": e.headers.get("Content-Type", "") if e.headers else "", "body": body}
    except Exception as e:  # DNS failure, timeout, TLS error
        result = {"status": 0, "final_url": url, "type": "", "body": b"", "error": repr(e)}
        if "CERTIFICATE_VERIFY_FAILED" in result["error"] and os.name == "nt":
            return _powershell_get(url) or result
        return result


def _powershell_get(url: str) -> dict | None:
    """Windows fallback for servers whose certificate chain Python cannot complete but Windows can."""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "body"
        ps = (f"$ProgressPreference='SilentlyContinue'; (Invoke-WebRequest -UseBasicParsing -Uri '{url}' "
              f"-UserAgent '{UA}' -OutFile '{dest}' -PassThru).StatusCode")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=90)
            status = int((out.stdout.strip().splitlines() or ["0"])[-1])
        except Exception:
            return None
        body = dest.read_bytes() if dest.exists() else b""
    return {"status": status, "final_url": url, "type": "", "body": body, "via": "powershell"}


def get_json(url: str, timeout: int = 30) -> tuple[int, object | None, str]:
    """GET and decode JSON. Returns (status, data or None, final URL)."""
    r = http_get(url, timeout=timeout)
    if r["status"] != 200:
        return r["status"], None, r["final_url"]
    try:
        return 200, json.loads(r["body"]), r["final_url"]
    except ValueError:
        return 200, None, r["final_url"]
