"""End-to-end check on this machine, without Switch: the test page, the watcher and the report draft together.

  python scripts/smoke_e2e.py

It starts the test page on a free local port, follows its log with the watcher (messages printed instead of
posted), and sends three requests as the tunnel would deliver them (with CF-Connecting-IP): the GPTBot name
from an address in OpenAI's GPTBot list, the GPTBot name from a home connection, and a browser. Registry
lookups are live. This checks the plumbing; it is not a capture of real agent traffic (see README, "Capture").
Output goes to the terminal and to logs/smoke_e2e.txt; its own log goes to logs/smoke/.
"""
from __future__ import annotations

import http.client
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whoseagent import paths  # noqa: E402
from whoseagent.classify import classify  # noqa: E402
from whoseagent.honeypage import serve  # noqa: E402
from whoseagent.logparse import read_log  # noqa: E402
from whoseagent.rdap import Rdap  # noqa: E402
from whoseagent.registry import load  # noqa: E402
from whoseagent.report import draft, load_contacts, to_markdown  # noqa: E402
from whoseagent.watch import Watcher, follow  # noqa: E402

GPTBOT = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.3; +https://openai.com/gptbot"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
CASES = [("20.171.206.10", GPTBOT, "/t/openai-range"), ("49.36.10.1", GPTBOT, "/t/spoof"),
         ("49.36.10.1", BROWSER, "/t/person")]


def main() -> None:
    folder = paths.LOGS / "smoke"
    shutil.rmtree(folder, ignore_errors=True)
    out: list[str] = []
    say = lambda s: (out.append(s), print(s, flush=True))  # noqa: E731
    reg = load()
    srv = serve("127.0.0.1", 0, folder)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    say(f"test page on 127.0.0.1:{port}; lists: {len(reg.lists)} ({reg.source}), {reg.n_ranges} ranges")

    posted: list[str] = []
    stop = threading.Event()
    w = Watcher(reg, Rdap(), posted.append, "logs/smoke/access.log", window=60, handoff="site-abuse-writer",
                findings_path=folder / "findings.jsonl")
    t = threading.Thread(target=lambda: [w.handle(n, ln) for n, ln in follow(folder / "access.log", poll=0.1,
                                                                             stop=stop.is_set)], daemon=True)
    t.start()
    for ip, ua, path in CASES:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers={"User-Agent": ua, "CF-Connecting-IP": ip})
        r = c.getresponse()
        r.read()
        c.close()
        say(f"sent {path} as {ip}: HTTP {r.status}")
    deadline = time.time() + 60
    while len(posted) < 2 and time.time() < deadline:
        time.sleep(0.2)
    stop.set()
    srv.shutdown()
    say("")
    say(f"--- the watcher posted {len(posted)} messages (the browser visit is recorded, not posted) ---")
    for p in posted:
        say(p)
        say("")
    reqs = read_log(folder / "access.log")
    spoof = next(r for r in reqs if r.path == "/t/spoof")
    f = classify(spoof, reg)
    d = draft(f, Rdap().lookup(spoof.ip), reg, load_contacts(), "logs/smoke/access.log", site="wiki.example.org",
              sender="Site operations")
    say("--- the draft the abuse-writer would post (not saved, not sent) ---")
    say(to_markdown(d))
    (paths.LOGS / "smoke_e2e.txt").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
