"""Record the demo video in the real Switch room, automatically: requests, typing, captions, cuts and voice.

One time:
  .venv\\Scripts\\python -m pip install -r demo/requirements.txt
  .venv\\Scripts\\python scripts/make_voice.py
  .venv\\Scripts\\python scripts/record_demo.py --login --url ROOM_URL
      Edge opens the room. Sign in to Mattermost, then press Enter in this terminal. The sign-in is kept in
      demo/.profile/ (ignored by git).

Record:
  .venv\\Scripts\\python scripts/record_demo.py --url ROOM_URL
      Starts the test page, a Cloudflare quick tunnel and the watcher; opens the room in Edge; runs the steps in
      demo/demo.json; writes demo/out/demo.mp4 and demo/out/demo.srt (captions to upload with the video).

Rehearsal, to test this script without Switch, on demo/rehearsal/index.html (a canned stand-in page):
  .venv\\Scripts\\python scripts/record_demo.py --rehearsal

ROOM_URL is the address of the site-whose-agent channel in your browser, for example
http://localhost:8065/<team>/channels/site-whose-agent. In the video, the person's messages are typed into the
real chat box by this script, and everything the watcher and the agents post is real. Waits for the agents are
shortened, and the badge in the corner of the video says both.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO, OUT, VOICE, TOOLS = ROOT / "demo", ROOT / "demo" / "out", ROOT / "demo" / "voice", ROOT / "tools"
CLOUDFLARED = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
W, H = 1280, 720
KEEP_BEFORE, KEEP_AFTER = 1.2, 1.8  # seconds of each wait kept in the video

OVERLAY_JS = r"""
(badge) => {
  if (document.getElementById('wa-overlay')) return;
  const style = document.createElement('style');
  style.textContent = `
    #wa-overlay { position: fixed; inset: 0; pointer-events: none; z-index: 2147483647;
                  font-family: 'Segoe UI', Arial, sans-serif; }
    #wa-badge { position: absolute; top: 8px; right: 10px; background: rgba(20,20,20,.74); color: #fff;
                font-size: 12px; padding: 4px 9px; border-radius: 4px; }
    #wa-title { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
                justify-content: center; background: rgba(251,250,246,.97); opacity: 0; transition: opacity .6s; }
    #wa-title.on { opacity: 1; }
    #wa-title-main { font-family: 'EB Garamond', Georgia, serif; font-size: 60px; color: #2b2b2b; }
    #wa-title-sub { font-family: Georgia, serif; font-style: italic; font-size: 25px; color: #555; margin-top: 12px; }
    #wa-caption { position: absolute; left: 50%; top: 64px; transform: translateX(-50%); max-width: 80%;
                  background: rgba(15,15,15,.84); color: #fff; font-size: 21px; line-height: 1.4;
                  padding: 10px 18px; border-radius: 6px; text-align: center; opacity: 0; transition: opacity .25s; }
    #wa-caption.on { opacity: 1; }`;
  const o = document.createElement('div');
  o.id = 'wa-overlay';
  o.innerHTML = '<div id="wa-badge"></div><div id="wa-title"><div id="wa-title-main"></div>' +
                '<div id="wa-title-sub"></div></div><div id="wa-caption"></div>';
  // outside <body>, so it never shows up in the page text the steps wait for
  document.documentElement.appendChild(style);
  document.documentElement.appendChild(o);
  document.getElementById('wa-badge').textContent = badge;
  // collect the text of everything added to the page from now on: the waits search it
  window.__waAdded = '';
  const add = (t) => { if (t && t.trim()) window.__waAdded += '\n' + t; };
  new MutationObserver((ms) => {
    for (const m of ms) {
      if (m.type === 'characterData') add(m.target.data);
      for (const n of m.addedNodes) add(n.nodeType === 1 ? n.innerText : n.textContent);
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
}
"""
SET_CAPTION = "(t) => { const c = document.getElementById('wa-caption'); c.textContent = t; c.classList.toggle('on', !!t); }"
SET_TITLE = """([a, b]) => { const t = document.getElementById('wa-title');
  document.getElementById('wa-title-main').textContent = a; document.getElementById('wa-title-sub').textContent = b;
  t.classList.toggle('on', !!a); }"""


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def media_seconds(path: Path) -> float:
    err = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path), "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not times:
        raise RuntimeError(f"cannot read the length of {path}")
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


# ---------------------------------------------------------------- the helpers the real run starts

class Tunnel:
    """A Cloudflare quick tunnel to the test page. Its address is random and lives until it is stopped."""

    def __init__(self, port: int) -> None:
        exe = TOOLS / "cloudflared.exe"
        if not exe.exists():
            TOOLS.mkdir(exist_ok=True)
            log(f"downloading cloudflared from {CLOUDFLARED}")
            urllib.request.urlretrieve(CLOUDFLARED, exe)
        self.url, self.ready = "", threading.Event()
        self.proc = subprocess.Popen([str(exe), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not self.url:
                self.url = m.group(0)
                self.ready.set()

    def wait(self, timeout: float = 90) -> str:
        if not self.ready.wait(timeout):
            raise SystemExit("cloudflared gave no tunnel address. Is the internet reachable?")
        deadline = time.time() + timeout
        while time.time() < deadline:  # a new tunnel takes a few seconds to answer
            try:
                with urllib.request.urlopen(urllib.request.Request(f"{self.url}/robots.txt",
                                                                   headers={"User-Agent": "whoseagent-demo"}),
                                            timeout=10) as r:
                    if r.status == 200:
                        return self.url
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2)
        raise SystemExit(f"the tunnel {self.url} did not answer")


def start(args_list: list[str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(ROOT / "wa.py"), *args_list], cwd=ROOT)


def send_request(base: str, path: str, ua: str) -> None:
    req = urllib.request.Request(base.rstrip("/") + path, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log(f"request {path} as {ua[:40]}...: HTTP {r.status}")
    except urllib.error.HTTPError as e:
        log(f"request {path}: HTTP {e.code}")


# ---------------------------------------------------------------- the run

def fill(text: str, values: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def run_steps(page, cfg: dict, durations: dict, rehearsal: bool, base_url: str, t0: float, box: str):
    values = {"checker": cfg["checker"], "writer": cfg["writer"]}
    marks: dict[str, int] = {}
    last_mark = None
    says, waits = [], []
    now = lambda: time.monotonic() - t0  # noqa: E731

    def added_since(offset: int) -> str:
        return page.evaluate("(i) => window.__waAdded.slice(i)", offset)

    for i, step in enumerate(cfg["steps"], 1):
        if "title" in step:
            page.evaluate(SET_TITLE, [step["title"], step.get("subtitle", "")])
        if "say" in step:
            key = step["say"]
            secs = durations[key]["seconds"]
            says.append({"key": key, "t": now(), "seconds": secs, "text": cfg["lines"][key]})
            page.evaluate(SET_CAPTION, cfg["lines"][key])
            log(f"[{i}] say {key} ({secs:.1f} s)")
            time.sleep(secs + 0.35)
            page.evaluate(SET_CAPTION, "")
        if "title" in step:
            page.evaluate(SET_TITLE, ["", ""])
            time.sleep(0.6)
        if "request" in step:
            marks[step.get("mark", f"step{i}")] = last_mark = page.evaluate("window.__waAdded.length")
            last_mark = step.get("mark", f"step{i}")
            if rehearsal:
                page.evaluate("window.rehearsalRequest()")
                log(f"[{i}] rehearsal request")
            else:
                send_request(base_url, step["request"]["path"], step["request"]["ua"])
        if "type" in step:
            text = fill(step["type"], values)
            if re.search(r"\{\w+\}", text):
                log(f"[{i}] skipped: nothing to fill in for {text!r}")
                continue
            name = step.get("mark", f"step{i}")
            marks[name], last_mark = page.evaluate("window.__waAdded.length"), name
            page.locator(box).click()
            page.keyboard.type(text, delay=45)
            time.sleep(0.4)
            page.keyboard.press("Enter")
            log(f"[{i}] typed {text!r}")
        if "wait" in step:
            since = marks.get(step.get("since") or last_mark, 0)
            start_t, found = now(), None
            deadline = time.monotonic() + step.get("timeout", 300)
            while time.monotonic() < deadline:
                found = re.search(step["wait"], added_since(since))
                if found:
                    break
                time.sleep(0.5)
            if found and step.get("capture") and found.groups():
                matches = list(re.finditer(step["wait"], added_since(since)))
                values[step["capture"]] = matches[-1].group(1)
            log(f"[{i}] wait {step['wait']!r}: {'found' if found else 'TIMED OUT'} after {now() - start_t:.0f} s"
                + (f", {step['capture']} = {values.get(step['capture'])}" if step.get("capture") else ""))
            time.sleep(1.0)  # let the new message render before anything covers it
            waits.append({"start": start_t, "end": now() - 1.0, "found": bool(found), "pattern": step["wait"]})
        if step.get("hold"):
            time.sleep(step["hold"])
    return says, waits


# ---------------------------------------------------------------- editing

def cuts_from(waits: list[dict]) -> list[tuple[float, float]]:
    out = []
    for w in waits:
        a, b = w["start"] + KEEP_BEFORE, w["end"] - KEEP_AFTER
        if b - a > 1.0:
            out.append((a, b))
    return out


def to_edited(t: float, cuts: list[tuple[float, float]]) -> float:
    return t - sum(min(b, t) - a for a, b in cuts if a < t)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def edit(raw: Path, says: list[dict], cuts: list[tuple[float, float]], out_mp4: Path) -> float:
    total = media_seconds(raw)
    keep, pos = [], 0.0
    for a, b in cuts:
        if a > pos:
            keep.append((pos, min(a, total)))
        pos = max(pos, b)
    if pos < total:
        keep.append((pos, total))
    parts = [f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]" for i, (a, b) in enumerate(keep)]
    parts.append("".join(f"[v{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=0[vout]")
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw)]
    for j, s in enumerate(says):
        cmd += ["-i", str(VOICE / f"{s['key']}.mp3")]
        ms = max(0, int(to_edited(s["t"], cuts) * 1000))
        parts.append(f"[{j + 1}:a]aresample=48000,adelay=delays={ms}:all=1[a{j}]")
    if says:
        parts.append("".join(f"[a{j}]" for j in range(len(says)))
                     + f"amix=inputs={len(says)}:normalize=0:dropout_transition=0[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    if says:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "25",
            "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return sum(b - a for a, b in keep)


def write_srt(says: list[dict], cuts, path: Path) -> None:
    rows = []
    for n, s in enumerate(says, 1):
        a = to_edited(s["t"], cuts)
        rows.append(f"{n}\n{srt_time(a)} --> {srt_time(a + s['seconds'])}\n{s['text']}\n")
    path.write_text("\n".join(rows), encoding="utf-8")


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="the room's address in your browser")
    ap.add_argument("--login", action="store_true", help="open the room to sign in, and keep the sign-in")
    ap.add_argument("--rehearsal", action="store_true", help="test on the canned stand-in page (not Switch)")
    ap.add_argument("--headless", action="store_true", help="no browser window")
    ap.add_argument("--port", type=int, default=8787, help="port for the test page")
    ap.add_argument("--base-url", help="use this public address of the test page instead of starting a tunnel")
    ap.add_argument("--no-helpers", action="store_true", help="don't start the test page, tunnel and watcher")
    ap.add_argument("--box", default="#post_textbox", help="CSS selector of the chat's message box")
    a = ap.parse_args()
    cfg = json.loads((DEMO / "demo.json").read_text(encoding="utf-8"))
    from playwright.sync_api import sync_playwright

    if a.rehearsal:
        url, profile, name = (DEMO / "rehearsal" / "index.html").as_uri(), DEMO / ".profile-rehearsal", "rehearsal"
        badge = "REHEARSAL · canned stand-in page, not Switch"
    else:
        if not a.url:
            sys.exit("--url is needed: the address of the room in your browser")
        url, profile, name = a.url, DEMO / ".profile", "demo"
        badge = "Real Switch room · my messages typed by a script · waits shortened"

    if a.login:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(str(profile), channel="msedge", headless=False,
                                                       viewport={"width": W, "height": H})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url)
            input("Sign in to the chat in the Edge window, open the room, then press Enter here... ")
            ctx.close()
        log(f"sign-in kept in {profile}")
        return

    durations_path = VOICE / "durations.json"
    if not durations_path.exists():
        sys.exit("No narration yet: run .venv\\Scripts\\python scripts/make_voice.py first.")
    durations = json.loads(durations_path.read_text(encoding="utf-8"))
    missing = [s["say"] for s in cfg["steps"] if "say" in s and s["say"] not in durations]
    if missing:
        sys.exit(f"No narration for {missing}: run scripts/make_voice.py again.")

    helpers: list[subprocess.Popen] = []
    base_url = a.base_url or ""
    try:
        if not a.rehearsal and not a.no_helpers:
            helpers.append(start(["serve", "--port", str(a.port)]))
            if not base_url:
                tunnel = Tunnel(a.port)
                helpers.append(tunnel.proc)
                base_url = tunnel.wait()
                log(f"test page is public at {base_url}")
            watcher = start(["watch", "logs/access.log", "--room", cfg["room"], "--agent-dir", "agents/egress-check",
                             "--handoff", cfg["writer"]])
            helpers.append(watcher)
            time.sleep(4)
            if watcher.poll() is not None:
                raise SystemExit("The watcher stopped: check that Switch Console registered site-egress-check with "
                                 "agents/egress-check as its folder (it writes .switch/agents/ there).")
        elif not a.rehearsal and not base_url:
            sys.exit("With --no-helpers, give the test page's public address with --base-url.")

        OUT.mkdir(parents=True, exist_ok=True)
        raw_dir = OUT / f"{name}-raw"
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile), channel="msedge", headless=a.headless, viewport={"width": W, "height": H},
                record_video_dir=str(raw_dir), record_video_size={"width": W, "height": H}, bypass_csp=True)
            t0 = time.monotonic()
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.locator(a.box).wait_for(state="visible", timeout=60000)
            except Exception:
                ctx.close()
                raise SystemExit(f"No message box ({a.box}) on {url}. Not signed in? Run with --login first.")
            time.sleep(2.5)
            page.evaluate(OVERLAY_JS, badge)
            says, waits = run_steps(page, cfg, durations, a.rehearsal, base_url, t0, a.box)
            time.sleep(1.0)
            video = page.video
            ctx.close()
            raw = OUT / f"{name}-raw.webm"
            video.save_as(str(raw))
        cuts = cuts_from(waits)
        mp4, srt = OUT / f"{name}.mp4", OUT / f"{name}.srt"
        length = edit(raw, says, cuts, mp4)
        write_srt(says, cuts, srt)
        (OUT / f"{name}-timeline.json").write_text(json.dumps({"says": says, "waits": waits, "cuts": cuts},
                                                             indent=1), encoding="utf-8")
        timed_out = [w["pattern"] for w in waits if not w["found"]]
        log(f"wrote {mp4.relative_to(ROOT)} ({length:.0f} s, {len(cuts)} waits shortened) and {srt.relative_to(ROOT)}")
        if length > 180:
            log("warning: longer than 3 minutes; shorten lines in demo/demo.json and record again")
        if timed_out:
            log(f"warning: these never appeared, so the video is incomplete: {timed_out}")
    finally:
        for h in helpers:
            if h.poll() is None:
                h.terminate()


if __name__ == "__main__":
    main()
