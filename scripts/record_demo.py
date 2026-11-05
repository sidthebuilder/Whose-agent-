"""The demo video, automatically. Double-click START_DEMO.bat (or run the command below) and watch: it runs
the real room live, speaks the narration aloud with captions, moves a pointer to what it talks about, types my
messages into the chat, and records the screen with the narration.

  .venv\\Scripts\\python scripts/record_demo.py

Before Edge opens it:
  1. makes the narration if a line in demo/demo.json changed (scripts/make_voice.py)
  2. wakes the agents' model, so answers come in seconds (the first wake takes about 4 minutes)
  3. makes a fresh room (scripts/room.py), so the video starts from the kickoff; --keep-room skips this
  4. deploys the small Modal function that sends the test request from a cloud machine
Then Edge opens full screen, signed in to the Switch dashboard and to Mattermost through their APIs (no
password is typed or shown), and the demo runs by itself for about two and a half minutes. Don't touch the
mouse or keyboard until Edge closes, and turn on Do Not Disturb so no notification shows up in the video.

Writes demo/out/demo.mp4 (the screen with the narration), demo/out/demo.srt (captions to upload with it) and
demo/out/demo-timeline.json. The narration also plays aloud, so you can record the screen yourself as well
(Win+Alt+R in Xbox Game Bar records Edge with its sound); --no-record skips the built-in recording.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
DEMO, OUT, VOICE, RUN = ROOT / "demo", ROOT / "demo" / "out", ROOT / "demo" / "voice", ROOT / "logs" / "run"
API, DASHBOARD = "http://localhost:8000", "http://localhost:3001"
LEAD_IN = 1.5  # seconds of the title card kept before the first line

OVERLAY_JS = r"""
(() => {
  if (window.__wa) return;
  const W = window.__wa = {};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const css = `
    #wa-root { position: fixed; inset: 0; pointer-events: none; z-index: 2147483647;
               font-family: 'Segoe UI Variable Text', 'Segoe UI', system-ui, sans-serif; }
    #wa-root * { box-sizing: border-box; }
    .wa-box { position: fixed; border: 3px solid #ff5b2e; border-radius: 8px; background: rgba(255,91,46,.07);
              box-shadow: 0 0 0 6px rgba(255,91,46,.14); opacity: 0; transition: opacity .3s ease; }
    .wa-box.on { opacity: 1; }
    #wa-title { position: fixed; inset: 0; background: #ffffff; display: flex; flex-direction: column;
                justify-content: center; padding: 0 11vw; opacity: 0; transition: opacity .7s ease; }
    #wa-title.on { opacity: 1; }
    #wa-title .bar { width: 84px; height: 9px; border-radius: 5px; background: #ff5b2e; margin-bottom: 30px; }
    #wa-title .main { font-size: 66px; line-height: 1.08; font-weight: 700; color: #101828; letter-spacing: -1.5px; }
    #wa-title .sub { font-size: 27px; line-height: 1.45; color: #475467; margin-top: 20px; max-width: 980px; }
    #wa-title .foot { position: absolute; left: 11vw; right: 11vw; bottom: 9vh; font-size: 17px; color: #667085; }
    #wa-caption { position: fixed; left: 50%; top: 16px; transform: translateX(-50%); max-width: 74%;
                  background: rgba(16,24,40,.9); color: #fff; font-size: 24px; line-height: 1.38; font-weight: 500;
                  padding: 11px 22px; border-radius: 12px; text-align: center; opacity: 0; transition: opacity .2s; }
    #wa-caption.on { opacity: 1; }
    #wa-badge { position: fixed; right: 16px; bottom: 14px; background: rgba(16,24,40,.8); color: #fff;
                font-size: 13px; padding: 5px 11px; border-radius: 7px; opacity: 0; transition: opacity .4s; }
    #wa-badge.on { opacity: 1; }
    #wa-ripple { position: fixed; width: 38px; height: 38px; margin: -19px 0 0 -19px; border-radius: 50%;
                 border: 3px solid #ff5b2e; opacity: 0; }
    #wa-ripple.on { animation: wa-ripple .55s ease-out; }
    @keyframes wa-ripple { from { opacity: .95; transform: scale(.35); } to { opacity: 0; transform: scale(1.7); } }
    #wa-cursor { position: fixed; left: 0; top: 0; width: 30px; height: 30px; transform: translate(-200px, -200px);
                 filter: drop-shadow(0 2px 3px rgba(0,0,0,.35)); }
    html, body, body * { cursor: none !important; }`;
  const build = () => {
    if (document.getElementById('wa-root')) return;
    const st = document.createElement('style');
    st.textContent = css;
    document.documentElement.appendChild(st);
    const root = document.createElement('div');
    root.id = 'wa-root';
    root.innerHTML = '<div id="wa-boxes"></div>' +
      '<div id="wa-title"><div class="bar"></div><div class="main"></div><div class="sub"></div><div class="foot"></div></div>' +
      '<div id="wa-caption"></div><div id="wa-badge"></div><div id="wa-ripple"></div>' +
      '<svg id="wa-cursor" viewBox="0 0 30 30"><path d="M4 2.5 L4 24 L9.6 18.6 L13.6 27.2 L17.3 25.6 L13.4 17.2 L21.2 17.2 Z" ' +
      'fill="#ffffff" stroke="#101828" stroke-width="1.7" stroke-linejoin="round"/></svg>';
    // outside <body>, so the page never sees it and the waits below never match its text
    document.documentElement.appendChild(root);
    W.place(innerWidth * 0.64, innerHeight * 0.58, 0);
  };
  const $ = (id) => document.getElementById(id);
  W.place = (x, y, ms) => {
    const c = $('wa-cursor');
    if (!c) return;
    c.style.transition = 'transform ' + ms + 'ms cubic-bezier(.22,.61,.36,1)';
    c.style.transform = 'translate(' + (x - 4) + 'px,' + (y - 2) + 'px)';
    W.at = {x, y};
  };
  W.moveTo = async (x, y, ms) => { W.place(x, y, ms); await sleep(ms + 40); return true; };
  W.ripple = (x, y) => {
    const e = $('wa-ripple');
    e.style.left = x + 'px'; e.style.top = y + 'px';
    e.classList.remove('on'); void e.offsetWidth; e.classList.add('on');
  };
  W.caption = (t) => { const c = $('wa-caption'); if (!c) return; c.textContent = t || ''; c.classList.toggle('on', !!t); };
  W.badge = (t) => { const b = $('wa-badge'); b.textContent = t || ''; b.classList.toggle('on', !!t); };
  W.title = (main, sub, foot) => {
    const t = $('wa-title');
    t.querySelector('.main').textContent = main || '';
    t.querySelector('.sub').textContent = sub || '';
    t.querySelector('.foot').textContent = foot || '';
    t.classList.toggle('on', !!main);
    $('wa-badge').style.visibility = main ? 'hidden' : 'visible';
  };
  W.clear = () => { const b = $('wa-boxes'); if (b) b.innerHTML = ''; };
  W.box = (r) => {
    const d = document.createElement('div');
    d.className = 'wa-box';
    Object.assign(d.style, {left: (r.left - 6) + 'px', top: (r.top - 4) + 'px',
                            width: (r.width + 12) + 'px', height: (r.height + 8) + 'px'});
    $('wa-boxes').appendChild(d);
    requestAnimationFrame(() => d.classList.add('on'));
  };
  // narration: the line plays aloud and the caption follows the audio, one sentence at a time
  W.say = (url, chunks, seconds) => {
    if (W.audio) { try { W.audio.pause(); } catch (e) {} }
    W.timers = (W.timers || []).filter((t) => clearTimeout(t) && false);
    const a = new Audio(url);
    W.audio = a; W.fakeEnd = 0;
    const show = (t) => { const c = chunks.find((c) => t >= c.start && t < c.end); W.caption(c ? c.text : ''); };
    a.addEventListener('timeupdate', () => show(a.currentTime));
    a.addEventListener('ended', () => W.caption(''));
    W.caption(chunks[0].text);
    return a.play().then(() => 'playing').catch((e) => {
      // no sound in this browser: keep the captions on time anyway
      W.fakeEnd = performance.now() + seconds * 1000;
      chunks.forEach((c) => W.timers.push(setTimeout(() => W.caption(c.text), c.start * 1000)));
      W.timers.push(setTimeout(() => W.caption(''), seconds * 1000));
      return 'silent: ' + e;
    });
  };
  W.done = () => W.fakeEnd ? performance.now() >= W.fakeEnd : (!W.audio || W.audio.ended || W.audio.paused);

  // finding what to point at
  const lineTarget = (msg, text, next) => {
    const w = document.createTreeWalker(msg, NodeFilter.SHOW_TEXT);
    let node = null;
    for (let n = w.nextNode(); n; n = w.nextNode()) if (n.data.includes(text)) { node = n; break; }
    if (!node) return null;
    const pre = node.parentElement.closest('pre');
    if (pre) return {el: pre.closest('.post-code') || pre};
    const block = node.parentElement.closest('p, li, blockquote, h1, h2, h3, h4, h5, h6, td') || node.parentElement;
    if (next && block.nextElementSibling) return {el: block.nextElementSibling};
    const lines = [[]];
    const all = document.createTreeWalker(block, NodeFilter.SHOW_ALL);
    for (let x = all.nextNode(); x; x = all.nextNode()) {
      if (x.nodeName === 'BR') lines.push([]);
      else if (x.nodeType === 3 && x.data.trim()) lines[lines.length - 1].push(x);
    }
    const line = lines.find((l) => l.includes(node)) || [node];
    const r = document.createRange();
    r.setStart(line[0], 0);
    const end = line[line.length - 1];
    r.setEnd(end, end.length);
    return {el: block, range: r};
  };
  const find = (o) => {
    const posts = [...document.querySelectorAll('.post')].filter((p) => !p.classList.contains('post--system'));
    if (posts.length) {  // Mattermost: the newest post (or with o.first the oldest) that matches
      for (const p of (o.first ? posts : posts.reverse())) {
        const msg = p.querySelector('.post-message__text') || p.querySelector('.post-message') || p;
        const t = msg.innerText || msg.textContent || '';
        const head = p.querySelector('.post__header');
        if (o.by && !(head && head.innerText.includes(o.by))) continue;
        if ((o.in && !t.includes(o.in)) || !t.includes(o.text)) continue;
        if (o.whole) return [{el: p.querySelector('.post__body') || p}];
        return [lineTarget(msg, o.text, o.next) || {el: msg}];
      }
      return [];
    }
    const out = [];  // any other page: the table row or element that shows the text
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const el = n.parentElement;
      if (!n.data.includes(o.text) || !el || !el.getClientRects().length) continue;
      out.push({el: el.closest('tr, [role="row"]') || el});
    }
    return o.all ? out : out.slice(-1);
  };
  const rectOf = (t) => {
    let rs = t.range ? [...t.range.getClientRects()] : [t.el.getBoundingClientRect()];
    rs = rs.filter((r) => r.width > 1 && r.height > 1);
    if (!rs.length) return null;
    const l = Math.max(0, Math.min(...rs.map((r) => r.left))), tp = Math.max(0, Math.min(...rs.map((r) => r.top)));
    const rt = Math.min(innerWidth, Math.max(...rs.map((r) => r.right)));
    const b = Math.min(innerHeight, Math.max(...rs.map((r) => r.bottom)));
    return rt > l && b > tp ? {left: l, top: tp, width: rt - l, height: b - tp} : null;
  };
  W.point = async (o) => {
    const ts = find(o);
    if (!ts.length) return {found: false};
    const first = rectOf(ts[0]);
    const inView = first && first.top >= 64 && first.bottom <= innerHeight - 150;
    if (!inView) {
      const el = ts[0].range ? ts[0].range.startContainer.parentElement : ts[0].el;
      el.scrollIntoView({block: 'center', behavior: 'smooth'});
      await sleep(850);
    }
    W.clear();
    const rects = ts.map(rectOf).filter(Boolean);
    if (!rects.length) return {found: false};
    const r = rects[0];
    await W.moveTo(r.left + Math.min(26, r.width / 3), r.top + Math.min(r.height / 2 + 7, r.height - 2), o.ms || 780);
    rects.forEach(W.box);
    return {found: true, boxes: rects.length};
  };
  W.scrollBottom = async () => {
    W.clear();
    const s = document.querySelector('#postListScrollContainer');
    if (!s) return false;
    s.scrollTo({top: s.scrollHeight, behavior: 'smooth'});
    await sleep(900);
    s.scrollTop = s.scrollHeight;
    return true;
  };
  // every piece of text the page adds from now on, for the waits
  const watch = () => {
    window.__waAdded = window.__waAdded || '';
    const add = (t) => { if (t && t.trim()) window.__waAdded += '\n' + t; };
    new MutationObserver((ms) => {
      for (const m of ms) {
        if (m.type === 'characterData') add(m.target.data);
        for (const n of m.addedNodes) add(n.nodeType === 1 ? n.innerText : n.textContent);
      }
    }).observe(document.body, {childList: true, subtree: true, characterData: true});
  };
  const ready = () => { build(); watch(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready); else ready();
})();
"""


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def helper(args: list[str], name: str, timeout: int = 600) -> None:
    """Run a script of this repository. Its output goes to logs/run/<name>.log, not to the screen: it can contain
    the tunnel's address, which for a free tunnel includes this machine's public IP address."""
    RUN.mkdir(parents=True, exist_ok=True)
    with open(RUN / f"{name}.log", "w", encoding="utf-8") as f:
        r = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=timeout,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
    if r.returncode != 0:
        tail = (RUN / f"{name}.log").read_text(encoding="utf-8", errors="replace")[-1200:]
        raise SystemExit(f"{' '.join(args[:2])} failed. logs/run/{name}.log ends:\n{tail}")


def wake_model() -> None:
    from whoseagent.agent import Model, load_dotenv
    load_dotenv()
    t = time.time()
    Model().chat([{"role": "user", "content": "Reply with the word ready."}])
    log(f"   the model answered after {time.time() - t:.0f} s")


def keep_warm(stop: threading.Event) -> None:
    """A tiny request every 90 seconds while the demo runs, so the model server can't scale to zero mid-video."""
    from whoseagent.agent import Model
    while not stop.wait(90):
        try:
            Model().chat([{"role": "user", "content": "ok"}])
        except Exception:  # noqa: BLE001
            pass


def chunks(text: str, seconds: float) -> list[dict]:
    """The caption for a line: one sentence at a time, timed by its share of the line's length."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    total, t, out = sum(len(p) for p in parts), 0.0, []
    for p in parts:
        d = seconds * len(p) / total
        out.append({"text": p, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def fill(text: str, values: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


class ScreenRecorder:
    """The whole screen at its real resolution, with ffmpeg (gdigrab), without the system pointer."""

    def __init__(self, path: Path, fps: int = 30) -> None:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        self.size, self.path, self.t0, self.err = (w - w % 2, h - h % 2), path, None, []
        self.proc = subprocess.Popen(
            [ffmpeg(), "-y", "-hide_banner", "-stats_period", "0.2", "-f", "gdigrab", "-framerate", str(fps),
             "-draw_mouse", "0", "-offset_x", "0", "-offset_y", "0", "-video_size", f"{self.size[0]}x{self.size[1]}",
             "-i", "desktop", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "15", "-pix_fmt", "yuv420p", str(path)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        buf = b""
        for chunk in iter(lambda: self.proc.stderr.read(200), b""):
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for ln in lines:
                text = ln.decode("utf-8", "replace")
                self.err.append(text)
                m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", text)
                if m and self.t0 is None:  # when ffmpeg's clock started, on ours
                    h, mi, s = m.groups()
                    self.t0 = time.monotonic() - (int(h) * 3600 + int(mi) * 60 + float(s))

    def started(self, timeout: float = 15) -> bool:
        deadline = time.monotonic() + timeout
        while self.t0 is None and time.monotonic() < deadline and self.proc.poll() is None:
            time.sleep(0.1)
        return self.t0 is not None

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
            except OSError:
                pass
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def mix(raw: Path, says: list[dict], cut: float, length: float, out_mp4: Path) -> None:
    """The final video: the recording from `cut` for `length` seconds, with each narration line placed where it
    was spoken."""
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{cut:.3f}", "-i", str(raw)]
    parts = []
    for j, s in enumerate(says):
        cmd += ["-i", str(VOICE / f"{s['key']}.mp3")]
        ms = max(0, int(round((s["t"] - cut) * 1000)))
        parts.append(f"[{j + 1}:a]aresample=48000,adelay=delays={ms}:all=1[a{j}]")
    parts.append("".join(f"[a{j}]" for j in range(len(says)))
                 + f"amix=inputs={len(says)}:normalize=0:dropout_transition=0[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]", "-t", f"{length:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True)


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def write_srt(says: list[dict], cut: float, path: Path) -> None:
    rows, n = [], 0
    for s in says:
        for c in s["chunks"]:
            n += 1
            a = s["t"] - cut + c["start"]
            rows.append(f"{n}\n{srt_time(a)} --> {srt_time(s['t'] - cut + c['end'])}\n{c['text']}\n")
    path.write_text("\n".join(rows), encoding="utf-8")


class Demo:
    def __init__(self, cfg: dict, durations: dict, ctx, pages: dict, room_id: str, spoof, public_url: str) -> None:
        self.cfg, self.durations, self.ctx, self.pages = cfg, durations, ctx, pages
        self.room_id, self.spoof, self.public_url = room_id, spoof, public_url
        self.page = pages["dashboard"]
        self.values = {"checker": cfg["checker"], "writer": cfg["writer"]}
        self.marks: dict[str, int] = {}
        self.says: list[dict] = []
        self.warnings: list[str] = []
        self.clock = time.monotonic  # replaced by the recorder's clock when recording

    # the page
    def js(self, expr: str, arg=None):
        return self.page.evaluate(expr, arg)

    def added(self) -> int:
        return self.pages["room"].evaluate("() => (window.__waAdded || '').length")

    def tab(self, name: str) -> None:
        self.page = self.pages[name]
        self.page.bring_to_front()
        self.js("(t) => window.__wa && __wa.badge(t)", self.cfg.get("badge", ""))
        time.sleep(0.6)

    # narration
    def say(self, key: str) -> None:
        secs = self.durations[key]["seconds"]
        url = "data:audio/mpeg;base64," + base64.b64encode((VOICE / f"{key}.mp3").read_bytes()).decode()
        parts = chunks(self.cfg["lines"][key], secs)
        t = self.clock()
        result = self.js("([u, c, s]) => __wa.say(u, c, s)", [url, parts, secs])
        self.says.append({"key": key, "t": t, "seconds": secs, "chunks": parts})
        log(f"   say {key} ({secs:.1f} s){'' if result == 'playing' else ' ' + str(result)}")

    def finish_say(self) -> None:
        while not self.js("() => __wa.done()"):
            time.sleep(0.15)
        time.sleep(0.35)

    # actions
    def point(self, a: dict) -> None:
        o = {"text": fill(a["point"], self.values), "in": a.get("in"), "whole": a.get("whole", False),
             "all": a.get("all", False), "next": a.get("next", False), "by": a.get("by"),
             "first": a.get("first", False)}
        r = self.js("(o) => __wa.point(o)", o)
        if not r.get("found"):
            self.warn(f"nothing to point at: {o['text']!r}")

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        log(f"   warning: {text}")

    def click_link(self, name: str) -> None:
        loc = self.page.get_by_role("link", name=name, exact=True).first
        b = loc.bounding_box()
        if not b:
            return self.warn(f"no link {name!r}")
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        self.js("([x, y]) => __wa.moveTo(x, y, 750)", [x, y])
        self.js("([x, y]) => __wa.ripple(x, y)", [x, y])
        self.js("() => __wa.clear()")
        loc.click()

    def type_message(self, text: str) -> None:
        box = self.page.locator("#post_textbox")
        b = box.bounding_box()
        x, y = b["x"] + 46, b["y"] + b["height"] / 2
        self.js("([x, y]) => __wa.moveTo(x, y, 700)", [x, y])
        self.js("([x, y]) => __wa.ripple(x, y)", [x, y])
        box.click()
        time.sleep(0.25)
        self.page.keyboard.type(text, delay=42)
        time.sleep(0.45)
        self.page.keyboard.press("Enter")
        log(f"   typed {text!r}")

    def agent_post(self, text: str) -> None:
        r = subprocess.run([sys.executable, str(ROOT / "wa.py"), "post", "--agent-dir", "agents/egress-check",
                            "--room-id", self.room_id, text], cwd=ROOT, capture_output=True, text=True, timeout=60)
        log(f"   posted as {self.cfg['checker']}: {text!r} ({'ok' if r.returncode == 0 else r.stderr.strip()[-200:]})")

    def start_spoof(self, case: str) -> None:
        def go() -> None:
            try:
                res = self.spoof.remote(self.public_url, [case])
                log(f"   the cloud machine sent '{case}': {res}")
            except Exception as e:  # noqa: BLE001
                log(f"   the Modal request failed: {e!r}")
        threading.Thread(target=go, daemon=True).start()

    def wait(self, a: dict) -> None:
        pattern = fill(a["wait"], self.values)
        since = self.marks.get(a.get("since", ""), 0)
        deadline, rx = time.monotonic() + a.get("timeout", 60), re.compile(pattern)
        page, start = self.pages["room"], time.monotonic()
        while time.monotonic() < deadline:
            m = rx.search(page.evaluate("(i) => (window.__waAdded || '').slice(i)", since))
            if m:
                if a.get("capture") and m.groups():
                    self.values[a["capture"]] = m.group(1)
                log(f"   found {pattern!r} after {time.monotonic() - start:.1f} s"
                    + (f": {a['capture']} = {self.values[a['capture']]}" if a.get("capture") else ""))
                time.sleep(0.8)  # let it render before anything points at it
                return
            time.sleep(0.3)
        self.warn(f"{pattern!r} did not appear within {a.get('timeout', 60)} s")

    def action(self, a: dict) -> None:
        if "mark" in a:
            self.marks[a["mark"]] = self.added()
        if "point" in a:
            self.point(a)
        elif "pause" in a:
            time.sleep(a["pause"])
        elif "click_link" in a:
            self.click_link(a["click_link"])
        elif "agent_post" in a:
            self.agent_post(fill(a["agent_post"], self.values))
        elif "wait" in a:
            self.wait(a)

    def scene(self, s: dict) -> None:
        if "tab" in s:
            self.tab(s["tab"])
        self.js("() => __wa.clear()")
        if "title" in s:
            main, sub, foot = (s["title"] + ["", ""])[:3]
            self.js("([a, b, c]) => __wa.title(a, b, c)", [main, sub, foot])
        if s.get("scroll") == "bottom":
            self.js("() => __wa.scrollBottom()")
        if "mark" in s:
            self.marks[s["mark"]] = self.added()
        if "spoof" in s:
            self.start_spoof(s["spoof"])
        if "say" in s:
            self.say(s["say"])
        for a in s.get("do", []):
            self.action(a)
        if "say" in s:
            self.finish_say()
        if s.get("untitle"):
            self.js("() => __wa.title('')")
            time.sleep(0.9)
        if "type" in s:
            self.type_message(fill(s["type"], self.values))
        if "wait" in s:
            self.wait(s)
        if s.get("hold"):
            time.sleep(s["hold"])

    def run(self) -> None:
        for i, s in enumerate(self.cfg["scenes"], 1):
            log(f"scene {i}/{len(self.cfg['scenes'])}")
            self.scene(s)


def set_fullscreen(ctx, page) -> None:
    try:
        s = ctx.new_cdp_session(page)
        win = s.send("Browser.getWindowForTarget")
        s.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "fullscreen"}})
    except Exception as e:  # noqa: BLE001
        log(f"   could not go full screen ({e!r}); the demo runs in a normal window")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-room", action="store_true", help="use the room as it is instead of a fresh one")
    ap.add_argument("--no-record", action="store_true", help="don't record the screen (record it yourself)")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    cfg = json.loads((DEMO / "demo.json").read_text(encoding="utf-8"))
    from mm import Mattermost, stack_env  # scripts/mm.py
    from playwright.sync_api import sync_playwright

    log("1/5 narration")
    helper(["scripts/make_voice.py"], "demo-voice", timeout=300)
    durations = json.loads((VOICE / "durations.json").read_text(encoding="utf-8"))

    log("2/5 waking the agents' model (the first wake takes about 4 minutes)")
    wake_model()

    if a.keep_room:
        log("3/5 the room as it is")
        helper(["scripts/room.py", "status"], "demo-status")
    else:
        log("3/5 a fresh room: the agents, the room and its kickoff, the test page, the tunnel and the watcher")
        helper(["scripts/room.py", "down"], "demo-down")
        helper(["scripts/switch_room.py", "reset"], "demo-reset")
        helper(["scripts/room.py", "up", "--site", cfg["site"]], "demo-up", timeout=400)
    state = json.loads((RUN / "state.json").read_text(encoding="utf-8"))
    room = json.loads((ROOT / "switch" / "stack" / "room.json").read_text(encoding="utf-8"))
    env = stack_env()
    m = Mattermost(env)
    m.login_person()
    channel_name = (room.get("channel_url") or "").rstrip("/").rsplit("/", 1)[-1]
    channel = m.call("GET", f"/teams/name/{env.get('MATTERMOST_TEAM_NAME', 'switch')}/channels/name/{channel_name}")
    deadline = time.time() + 240
    while time.time() < deadline:  # both agents answer the kickoff
        posts = m.call("GET", f"/channels/{channel['id']}/posts?per_page=60")["posts"].values()
        kick = next((p for p in posts if "this is the agent-traffic room" in p["message"] and not p.get("root_id")), None)
        if kick and sum(1 for p in posts if p.get("root_id") == kick["id"]) >= 2:
            break
        time.sleep(2)
    else:
        log("   warning: the kickoff has fewer than two answers")
    m.call("POST", "/channels/members/me/view", {"channel_id": channel["id"]})

    log("4/5 the Modal function that sends the test request")
    helper(["-m", "modal", "deploy", "scripts/modal_spoof.py"], "demo-modal-deploy", timeout=300)
    import modal
    spoof = modal.Function.from_name("whose-agent-spoof", "fetch")
    spoof.remote(state["public_url"], [])  # starts a container now, so the request in the video goes out at once
    wake_model()  # so the model is warm when the question comes

    log("5/5 the demo: Edge opens full screen in 5 seconds. Don't touch the mouse or keyboard until it closes.")
    time.sleep(5)
    OUT.mkdir(parents=True, exist_ok=True)
    raw = OUT / "demo-raw.mp4"
    stop = threading.Event()
    threading.Thread(target=keep_warm, args=(stop,), daemon=True).start()
    rec = None
    if sys.platform == "win32":
        # Modal's client leaves asyncio on selector event loops, which can't start a browser on Windows
        # (NotImplementedError); Playwright needs the default proactor loop. Modal keeps its own running loop.
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False, ignore_default_args=["--enable-automation"],
                                    args=["--autoplay-policy=no-user-gesture-required", "--start-fullscreen",
                                          "--no-default-browser-check", "--disable-features=Translate"])
        ctx = browser.new_context(no_viewport=True)
        ctx.add_init_script(OVERLAY_JS)
        r1 = ctx.request.post(f"{API}/gateway/auth/login", headers={"Content-Type": "application/json"},
                              data=json.dumps({"email": env["GATEWAY_ADMIN_EMAIL"],
                                               "password": env["GATEWAY_ADMIN_PASSWORD"]}))
        r2 = ctx.request.post("http://localhost:8065/api/v4/users/login",
                              headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
                              data=json.dumps({"login_id": env["MATTERMOST_USER"],
                                               "password": env["MATTERMOST_USER_PASSWORD"]}))
        if r1.status != 200 or r2.status != 200:
            raise SystemExit(f"sign-in failed: dashboard HTTP {r1.status}, Mattermost HTTP {r2.status}")
        room_page = ctx.new_page()
        set_fullscreen(ctx, room_page)
        room_page.goto(state["browser_url"], wait_until="domcontentloaded")
        room_page.locator("#post_textbox").wait_for(state="visible", timeout=90000)
        time.sleep(2)
        room_page.evaluate("() => __wa.scrollBottom()")
        dash = ctx.new_page()
        set_fullscreen(ctx, dash)
        dash.goto(f"{DASHBOARD}/rooms", wait_until="domcontentloaded")
        dash.get_by_text(room["room_name"], exact=True).first.wait_for(timeout=60000)
        demo = Demo(cfg, durations, ctx, {"dashboard": dash, "room": room_page}, room["room_id"], spoof,
                    state["public_url"])
        first = cfg["scenes"][0]
        dash.bring_to_front()
        dash.evaluate("([a, b, c]) => __wa.title(a, b, c)", (first.get("title", []) + ["", "", ""])[:3])
        time.sleep(3.5)  # the browser's "press F11 to exit full screen" note goes away
        if not a.no_record:
            rec = ScreenRecorder(raw)
            if rec.started():
                demo.clock = lambda: time.monotonic() - rec.t0
                log(f"   recording the screen at {rec.size[0]}x{rec.size[1]}")
            else:
                log("   warning: the screen recorder did not start; record the screen yourself")
                rec.stop()
                rec = None
        time.sleep(1.5)
        try:
            demo.run()
        finally:
            time.sleep(0.5)
            end = demo.clock()
            if rec:
                rec.stop()
            stop.set()
            browser.close()
    (OUT / "demo-timeline.json").write_text(json.dumps({"says": demo.says, "warnings": demo.warnings,
                                                       "values": demo.values}, indent=1), encoding="utf-8")
    if not rec:
        log("done (not recorded here)")
        return
    cut = max(0.0, demo.says[0]["t"] - LEAD_IN) if demo.says else 0.0
    length = end - cut
    mp4, srt = OUT / "demo.mp4", OUT / "demo.srt"
    log("mixing the narration into the recording")
    mix(raw, demo.says, cut, length, mp4)
    write_srt(demo.says, cut, srt)
    log(f"wrote {mp4.relative_to(ROOT)} ({length:.0f} s) and {srt.relative_to(ROOT)}")
    if length > 180:
        log("warning: longer than 3 minutes; shorten lines or pauses in demo/demo.json and run it again")
    if demo.warnings:
        log(f"check these before uploading: {demo.warnings}")


if __name__ == "__main__":
    main()
