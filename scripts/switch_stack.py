"""Run Switch's own all-in-one server on this machine with Docker: the Switch server, its database, a
Mattermost for the team to chat in (seeded with a team, an admin and one person), and the operator dashboard.

  python scripts/switch_stack.py up        download the compose file, create secrets, start, wait until ready
  python scripts/switch_stack.py status    what is running, and the addresses
  python scripts/switch_stack.py logs      the last lines of every service's log
  python scripts/switch_stack.py down      stop it (the data stays in Docker volumes)
  python scripts/switch_stack.py reset     stop it and delete its data

The compose file is Switch's published one for the pinned version (the OCI artifact
ghcr.io/sandbox-quantum/standalone-compose), downloaded into switch/stack/. Secrets are generated here into
switch/stack/.env (git-ignored) and never printed. Everything binds to 127.0.0.1.
See https://docs.switchagents.ai/flintai/switch/deploy/self-host
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "switch" / "stack"
ENV = STACK / ".env"
COMPOSE = STACK / "docker-compose.yml"
VERSION = os.environ.get("SWITCH_VERSION", "0.31.0")
PROJECT = "whoseagent-switch"
PERSON = os.environ.get("WA_PERSON", "person")  # the Mattermost account the person chats from


def ghcr_compose(version: str) -> bytes:
    repo = "sandbox-quantum/standalone-compose"
    with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{repo}:pull", timeout=30) as r:
        token = json.loads(r.read())["token"]
    req = urllib.request.Request(f"https://ghcr.io/v2/{repo}/manifests/{version}", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.oci.artifact.manifest.v1+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        manifest = json.loads(r.read())
    layer = (manifest.get("layers") or manifest.get("blobs"))[0]

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(f"https://ghcr.io/v2/{repo}/blobs/{layer['digest']}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with opener.open(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:  # the blob lives behind a signed redirect, fetched without the token
        if e.code in (301, 302, 303, 307, 308):
            with urllib.request.urlopen(e.headers["Location"], timeout=60) as r:
                return r.read()
        raise


def set_env(**values: str) -> None:
    """Change settings in an existing .env without touching its secrets."""
    lines = ENV.read_text(encoding="utf-8").splitlines()
    keys = {ln.split("=", 1)[0] for ln in lines if "=" in ln}
    out = [f"{ln.split('=', 1)[0]}={values[ln.split('=', 1)[0]]}" if "=" in ln and ln.split("=", 1)[0] in values
           else ln for ln in lines]
    out += [f"{k}={v}" for k, v in values.items() if k not in keys]
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def write_env() -> None:
    if ENV.exists():
        return
    h = lambda n=24: secrets.token_hex(n)  # noqa: E731
    values = {
        "SWITCH_REGISTRY": "ghcr.io", "SWITCH_IMAGE_NAMESPACE": "sandbox-quantum", "SWITCH_VERSION": VERSION,
        # 3000 and 5432 are common on a developer's machine (FalkorDB, a local Postgres), so the dashboard
        # and the database use the next ports up; the API and Mattermost keep Switch's defaults
        "SWITCH_BIND_ADDR": "127.0.0.1", "API_HOST_PORT": "8000", "GATEWAY_HOST_PORT": "3001",
        "MATTERMOST_HOST_PORT": "8065", "POSTGRES_HOST_PORT": "5433",
        "DB_USER": "switch_app", "DB_PASSWORD": h(), "DB_OWNER_USER": "postgres", "DB_OWNER_PASSWORD": h(),
        "DB_NAME": "switch", "AGENT_REGISTRATION_TOKEN": h(), "SECRET_KEYS": f"local:{h(32)}",
        "GATEWAY_ADMIN_EMAIL": "admin@switch.local", "GATEWAY_ADMIN_PASSWORD": h(),
        "FRONTEND_BASE_URL": "http://localhost:3001",
        "MATTERMOST_ADMIN_USER": "admin", "MATTERMOST_ADMIN_PASSWORD": h(), "MATTERMOST_TEAM_NAME": "switch",
        "MATTERMOST_USER": PERSON, "MATTERMOST_USER_PASSWORD": h(),
        "OUTBOUND_ALLOWED_PRIVATE_HOSTS": "localhost", "TELEMETRY_ENABLED": "false",
        "THIRD_PARTY_AVATARS_ENABLED": "false",
    }
    ENV.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    print(f"wrote {ENV.relative_to(ROOT)} with new random secrets (not printed)")


def env_values() -> dict[str, str]:
    return dict(ln.split("=", 1) for ln in ENV.read_text(encoding="utf-8").splitlines() if "=" in ln)


def compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "--env-file", str(ENV), "-f", str(COMPOSE),
           "--profile", "collab", "--profile", "gateway", *args]
    return subprocess.run(cmd, cwd=STACK, capture_output=capture, text=True, encoding="utf-8", errors="replace")


def wait_http(url: str, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status < 500:
                    return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(3)
    return False


def up() -> None:
    STACK.mkdir(parents=True, exist_ok=True)
    if not COMPOSE.exists():
        COMPOSE.write_bytes(ghcr_compose(VERSION))
        print(f"downloaded Switch's compose file for {VERSION} to {COMPOSE.relative_to(ROOT)}")
    write_env()
    r = compose("up", "-d")
    if r.returncode != 0:
        sys.exit("docker compose up failed")
    ok_api = wait_http("http://127.0.0.1:8000/health", 300)
    ok_mm = wait_http("http://127.0.0.1:8065/api/v4/system/ping", 300)
    print(f"Switch API {'ready' if ok_api else 'NOT ready'} at http://localhost:8000")
    print(f"Mattermost {'ready' if ok_mm else 'NOT ready'} at http://localhost:8065")
    deadline = time.time() + 300
    while time.time() < deadline:  # the seeder links the person's Mattermost account to the admin
        logs = compose("logs", "--no-color", "setup", capture=True).stdout
        if "Setup complete!" in logs:
            print("Seeder finished: Mattermost team, admin and the person's account exist, bridge registered.")
            break
        time.sleep(5)
    else:
        print("The seeder did not report completion; see: python scripts/switch_stack.py logs")
    status()


def status() -> None:
    compose("ps")
    v = env_values() if ENV.exists() else {}
    print("\nAddresses (this machine only):\n  Switch API and agent bridge  http://localhost:8000\n"
          f"  Switch dashboard             http://localhost:{v.get('GATEWAY_HOST_PORT', '3001')}\n"
          "  Mattermost                   http://localhost:8065\n"
          f"Mattermost team '{v.get('MATTERMOST_TEAM_NAME', 'switch')}', person '{v.get('MATTERMOST_USER', PERSON)}'. "
          f"Passwords are in {ENV.relative_to(ROOT)}.")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "up":
        up()
    elif cmd == "status":
        status()
    elif cmd == "logs":
        print(compose("logs", "--no-color", "--tail", "60", capture=True).stdout[-20000:])
    elif cmd == "down":
        compose("down")
    elif cmd == "reset":
        compose("down", "-v")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
