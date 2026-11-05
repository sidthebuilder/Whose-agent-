"""Run the whole room on this machine in the background, with one command (after scripts/switch_stack.py up):

  python scripts/room.py up --site "wiki.example.org"     start everything (see below)
  python scripts/room.py status                           what is running, and the addresses
  python scripts/room.py spoof [--case all]               send requests that call themselves AI crawlers, from
                                                          a cloud machine on Modal, to the test page
  python scripts/room.py down                             stop everything this script started

`up` does, in order:
  1. registers the two agents with the local Switch server, if they are not registered yet
  2. starts both agents (python wa.py agent ...) and waits until each holds its connection to Switch
  3. creates the room from the template if it does not exist yet; the agents answer its kickoff
  4. starts the test page (python wa.py serve) and a public tunnel to it: Pinggy over ssh by default (free,
     no account; a key for it is made in tools/), or --tunnel cloudflare, localhost.run or none
  5. starts the log watcher, which posts each AI request to the room and hands reports to the writer
Each program's output goes to logs/run/<name>.log. Their process ids and the public address of the test page
are in logs/run/state.json. Nothing secret is printed.

Which tunnel: the test page must learn the real address of each client, and the tunnel must let a request
that calls itself GPTBot through. Pinggy does both (X-Forwarded-For). In our tests on 5 November 2026 a
Cloudflare quick tunnel answered 403 to a GPTBot request from a cloud machine, before it reached the page, and
localhost.run's free tunnel let it through but passed no client address (every request was 127.0.0.1).
Two things about Pinggy's free tunnels: their addresses contain your own public IP address (don't show them
in a recording), and they show a browser a one-time "this is a tunnel" page first, so an agent that drives a
real browser stops there unless it clicks through. `spoof` sends the header that skips it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "logs" / "run"
STATE = RUN / "state.json"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

WINDOWS = os.name == "nt"
TUNNEL_URL = {"pinggy": re.compile(r"https://(?!dashboard\.)[a-z0-9-]+(?:\.[a-z0-9-]+)*\.pinggy(?:-free)?\.(?:link|net|online)\b"),
              "localhost.run": re.compile(r"https://[a-z0-9-]+\.lhr\.life"),
              "cloudflare": re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")}
KEY = ROOT / "tools" / "tunnel_key"  # an SSH key made for the tunnel only (tools/ is ignored by git)


def tunnel_key() -> Path:
    """Pinggy's free tunnels take any SSH key; make one for this purpose, readable only by this user."""
    if not KEY.exists():
        KEY.parent.mkdir(exist_ok=True)
        keygen = shutil.which("ssh-keygen")
        if not keygen:
            sys.exit("No ssh-keygen found. Install OpenSSH, or use --tunnel cloudflare or --tunnel none.")
        subprocess.run([keygen, "-t", "ed25519", "-N", "", "-C", "whose-agent tunnel", "-f", str(KEY), "-q"],
                       check=True)
        if WINDOWS:  # Windows OpenSSH refuses a private key that other accounts can read
            subprocess.run(["icacls", str(KEY), "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:F"],
                           capture_output=True, check=True)
    return KEY


def tunnel_command(kind: str, port: int) -> list[str]:
    if kind == "cloudflare":
        exe = ROOT / "tools" / ("cloudflared.exe" if WINDOWS else "cloudflared")
        if not exe.exists():
            sys.exit("No cloudflared: python scripts/get_cloudflared.py")
        return [str(exe), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    ssh = shutil.which("ssh")
    if not ssh:
        sys.exit("No ssh client found. Install OpenSSH, or use --tunnel cloudflare or --tunnel none.")
    common = [ssh, "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
              "-o", "ExitOnForwardFailure=yes"]
    if kind == "pinggy":  # x:xff: Pinggy passes the client's address in X-Forwarded-For
        return [*common, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", str(tunnel_key()), "-p", "443",
                "-R", f"0:127.0.0.1:{port}", "x:xff@free.pinggy.io"]
    return [*common, "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-R", f"80:127.0.0.1:{port}", "nokey@localhost.run"]


def log_path(name: str) -> Path:
    return RUN / f"{name}.log"


def spawn(name: str, args: list[str]) -> int:
    """Start a program detached from this terminal, its output in logs/run/<name>.log. Returns its pid."""
    RUN.mkdir(parents=True, exist_ok=True)
    out = open(log_path(name), "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    kw = {"creationflags": 0x00000008 | 0x00000200} if WINDOWS else {"start_new_session": True}  # detached
    p = subprocess.Popen(args, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, **kw)
    return p.pid


def wa(*args: str) -> list[str]:
    return [sys.executable, str(ROOT / "wa.py"), *args]


def alive(pid: int) -> bool:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True,
                             text=True, errors="replace").stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill(pid: int) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, 15)
        except OSError:
            pass


def wait_for(name: str, pattern: re.Pattern | str, seconds: float, pid: int) -> re.Match | None:
    rx = re.compile(pattern) if isinstance(pattern, str) else pattern
    deadline = time.time() + seconds
    while time.time() < deadline:
        text = log_path(name).read_text(encoding="utf-8", errors="replace") if log_path(name).exists() else ""
        m = rx.search(text)
        if m:
            return m
        if not alive(pid):
            return None
        time.sleep(0.5)
    return None


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"pids": {}}


def save_state(state: dict) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def tail(name: str, n: int = 6) -> str:
    p = log_path(name)
    return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]) if p.exists() else ""


def up(a) -> None:
    import switch_room  # scripts/switch_room.py
    from whoseagent.gateway import Gateway

    state = load_state()
    running = {k: v for k, v in state.get("pids", {}).items() if alive(v)}
    if running:
        sys.exit(f"Already running: {', '.join(running)}. Stop them first: python scripts/room.py down")
    state = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "site": a.site, "pids": {}}
    env = switch_room.stack_env()
    g = Gateway()
    g.login(env["GATEWAY_ADMIN_EMAIL"], env["GATEWAY_ADMIN_PASSWORD"])

    print("1. agents: registering with Switch if needed")
    switch_room.register_agents(g)

    print("2. starting both agents")
    for role in ("egress-check", "abuse-writer"):
        pid = spawn(f"agent-{role}", wa("agent", role, "--site", a.site))
        state["pids"][f"agent-{role}"] = pid
        save_state(state)
        if not wait_for(f"agent-{role}", r"connected to ", 40, pid):
            sys.exit(f"site-{role} did not connect. Its log, logs/run/agent-{role}.log, ends:\n{tail(f'agent-{role}')}")
        print(f"   site-{role} is connected (pid {pid}); log: logs/run/agent-{role}.log")

    print("3. the room")
    rooms = {r.get("name"): r for r in g.rooms()}
    if f"{switch_room.TEAM}-whose-agent" in rooms and switch_room.STATE.exists():
        print(f"   {switch_room.TEAM}-whose-agent exists; using it")
    else:
        switch_room.setup(a.site)
        print("   created; the agents answer its kickoff in the room")
    room = json.loads(switch_room.STATE.read_text(encoding="utf-8"))
    state["room_id"], state["browser_url"] = room["room_id"], room.get("browser_url", "")

    print("4. test page and tunnel")
    state["pids"]["serve"] = spawn("serve", wa("serve", "--port", str(a.port)))
    if a.tunnel != "none":
        state["pids"]["tunnel"] = spawn("tunnel", tunnel_command(a.tunnel, a.port))
    save_state(state)
    if a.tunnel != "none":
        m = wait_for("tunnel", TUNNEL_URL[a.tunnel], 60, state["pids"]["tunnel"])
        if not m:
            sys.exit(f"The tunnel gave no address. logs/run/tunnel.log ends:\n{tail('tunnel')}")
        time.sleep(1.5)  # Pinggy prints two addresses for the same tunnel; either works
        found = TUNNEL_URL[a.tunnel].findall(log_path("tunnel").read_text(encoding="utf-8", errors="replace"))
        state["public_url"] = next((u for u in found if "pinggy-free.link" not in u), m.group(0))
        print(f"   the test page is public at {state['public_url']}/t/<label>")
    else:
        print(f"   the test page is at http://127.0.0.1:{a.port}/t/<label> (this machine only)")

    print("5. log watcher")
    watch = ["watch", "logs/access.log", "--room-id", state["room_id"], "--agent-dir", "agents/egress-check",
             "--handoff", f"{switch_room.TEAM}-abuse-writer"]
    if a.redact:
        watch.append("--redact")
    state["pids"]["watcher"] = spawn("watcher", wa(*watch))
    save_state(state)
    if not wait_for("watcher", r"Watching ", 30, state["pids"]["watcher"]):
        sys.exit(f"The watcher stopped. logs/run/watcher.log ends:\n{tail('watcher')}")
    print(f"\nReady. Open the room: {state['browser_url']}\n"
          f"Send a test request: python scripts/room.py spoof   (or any client: curl -A \"GPTBot/1.3\" "
          f"{state.get('public_url', 'http://127.0.0.1:%d' % a.port)}/t/test)\nStop: python scripts/room.py down")


def status(_a) -> None:
    state = load_state()
    if not state.get("pids"):
        print("Nothing started by this script.")
        return
    for name, pid in state["pids"].items():
        print(f"  {name:<20} pid {pid:<7} {'running' if alive(pid) else 'stopped'}   logs/run/{name}.log")
    for k in ("browser_url", "public_url", "room_id", "site", "started_at"):
        if state.get(k):
            print(f"  {k:<20} {state[k]}")


def down(_a) -> None:
    state = load_state()
    for name, pid in reversed(list(state.get("pids", {}).items())):
        if alive(pid):
            kill(pid)
            print(f"stopped {name} (pid {pid})")
    state["pids"] = {}
    save_state(state)


def spoof(a) -> None:
    url = load_state().get("public_url")
    if not url:
        sys.exit("No public address: start the room with a tunnel first (python scripts/room.py up).")
    cmd = [sys.executable, "-m", "modal", "run", str(ROOT / "scripts" / "modal_spoof.py"), "--url", url,
           "--case", a.case]
    print(f"Sending '{a.case}' to the test page from a Modal cloud machine (needs a Modal account)", flush=True)
    sys.exit(subprocess.run(cmd, cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"}).returncode)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("up", help="start the agents, the room, the test page, a tunnel and the watcher")
    s.add_argument("--site", default="our website", help="the site the reports speak for")
    s.add_argument("--port", type=int, default=8787, help="port for the test page")
    s.add_argument("--tunnel", choices=["pinggy", "localhost.run", "cloudflare", "none"], default="pinggy",
                   help="how the test page gets a public address (default pinggy: free, no account, and it "
                        "passes on the client's address)")
    s.add_argument("--redact", action="store_true", help="the watcher shows undeclared addresses as /24 or /48")
    s.set_defaults(func=up)
    sub.add_parser("status", help="what is running").set_defaults(func=status)
    sub.add_parser("down", help="stop everything").set_defaults(func=down)
    s = sub.add_parser("spoof", help="requests that call themselves AI crawlers, from a cloud machine on Modal")
    s.add_argument("--case", default="gptbot",
                   help="gptbot (default), scanner (ClaudeBot asking for /.env), google-extended, writer (a POST "
                        "as GPTBot), browser, a comma-separated list, or all")
    s.set_defaults(func=spoof)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
