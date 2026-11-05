"""Deploy the agents' model server on Modal (scripts/modal_llm.py) and record how to reach it in .env.

  python scripts/modal_llm_setup.py            create the key if needed, store it in the Modal secret, deploy
  python scripts/modal_llm_setup.py --check    call the deployed server once (wakes it, about 2-4 minutes cold)

Needs a Modal account (`python -m modal setup` once). Writes WHOSEAGENT_LLM_BASE_URL, WHOSEAGENT_LLM_MODEL and
WHOSEAGENT_LLM_API_KEY to .env in the repository (git-ignored). The key is generated here and never printed;
command output is saved to logs/modal_llm_setup.txt with the key redacted.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
LOG = ROOT / "logs" / "modal_llm_setup.txt"
SECRET = "whose-agent-llm"
SERVED = os.environ.get("WA_SERVED", "whoseagent-qwen72b")


def read_env() -> dict[str, str]:
    if not ENV.exists():
        return {}
    return dict(ln.split("=", 1) for ln in ENV.read_text(encoding="utf-8").splitlines() if "=" in ln)


def write_env(values: dict[str, str]) -> None:
    current = read_env()
    current.update(values)
    ENV.write_text("".join(f"{k}={v}\n" for k, v in current.items()), encoding="utf-8")


def run(args: list[str], key: str) -> tuple[int, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "COLUMNS": "250"}
    r = subprocess.run([sys.executable, "-m", "modal", *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), env=env)
    return r.returncode, (r.stdout + r.stderr).replace(key, "<redacted>")


def check(env: dict[str, str]) -> str:
    base, key, model = env.get("WHOSEAGENT_LLM_BASE_URL", ""), env.get("WHOSEAGENT_LLM_API_KEY", ""), \
        env.get("WHOSEAGENT_LLM_MODEL", SERVED)
    if not base:
        return "no WHOSEAGENT_LLM_BASE_URL in .env: deploy first"
    health = base[:-3] + "/health" if base.endswith("/v1") else base + "/health"
    t0 = time.time()
    for _ in range(90):  # a cold start loads about 40 GB of weights
        try:
            with urllib.request.urlopen(health, timeout=30) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(5)
    body = json.dumps({"model": model, "max_tokens": 20, "temperature": 0,
                       "messages": [{"role": "user", "content": "Reply with the word ready."}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        reply = json.loads(r.read())["choices"][0]["message"]["content"]
    return f"model {model} answered {reply!r} after {time.time() - t0:.0f} s"


def main() -> None:
    LOG.parent.mkdir(exist_ok=True)
    env = read_env()
    if "--check" in sys.argv:
        out = check(env)
        LOG.write_text(out + "\n", encoding="utf-8")
        print(out)
        return
    key = env.get("WHOSEAGENT_LLM_API_KEY") or "wa-" + secrets.token_urlsafe(32)
    write_env({"WHOSEAGENT_LLM_API_KEY": key, "WHOSEAGENT_LLM_MODEL": SERVED})
    code, out1 = run(["secret", "create", "--force", SECRET, f"VLLM_API_KEY={key}"], key)
    code2, out2 = run(["deploy", str(ROOT / "scripts" / "modal_llm.py")], key)
    urls = re.findall(r"https://[\w.-]+\.modal\.(?:run|direct)[\w/.-]*", out2)
    url = urls[-1].rstrip("/") if urls else ""
    if url:
        write_env({"WHOSEAGENT_LLM_BASE_URL": url + "/v1"})
    LOG.write_text(f"secret exit={code}\n{out1}\n\ndeploy exit={code2}\nurl={url}\n\n{out2[-6000:]}", encoding="utf-8")
    print(f"secret: exit {code}; deploy: exit {code2}; url: {url or 'not found, see logs/modal_llm_setup.txt'}")


if __name__ == "__main__":
    main()
