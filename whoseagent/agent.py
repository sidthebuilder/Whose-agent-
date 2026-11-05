"""The room's two agents, run from this repository:

  python wa.py agent egress-check --site "wiki.example.org"
  python wa.py agent abuse-writer --site "wiki.example.org"

Each agent holds one connection to Switch over the agent protocol: an event stream down (Server-Sent Events)
with a heartbeat up every 2 seconds, and HTTP operations bound to that connection. When a message mentions it,
it answers in the thread of that message. Register the agents as "always on" (scripts/switch_room.py does).

What each agent can do is fixed here, not left to the model:
  site-egress-check   whois, check_log, summary, lists      (read-only lookups)
  site-abuse-writer   draft_report, list_drafts
Neither has a tool that sends mail. Approving a report is not a model tool either: the writer approves only
when a person (a participant of type "user" in the room, not an agent) writes "approve <id>".

A language model reads each question and chooses the tools, through any OpenAI-compatible endpoint
(WHOSEAGENT_LLM_BASE_URL, WHOSEAGENT_LLM_MODEL, WHOSEAGENT_LLM_API_KEY, from the environment or .env). Each tool
runs a `wa.py` command and gives the model what that command prints for a person, so an agent can only state
what the command says. Before a reply is posted it is checked: every IP address, address range, email address
and number of two or more digits in it must appear in a tool result or in the message it answers. If one does
not, the model is asked once to correct the reply; if it still does not, the agent posts the command's own
output instead. The commands an answer used are listed under it.

The watcher's hand-off ("please draft a report for line N of LOG") and approvals are handled without a model,
so drafting and approving work the same with any model, or none.
"""
from __future__ import annotations

import ipaddress
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, paths
from .report import to_markdown
from .switch import Credentials, load_credentials

ROOT = paths.ROOT
WA = ROOT / "wa.py"
# "please draft a report for line 31 of logs/access.log" (the watcher), or "reports for lines 3, 4 and 5 of ..."
HANDOFF = re.compile(r"draft (?:a )?reports? for lines? (\d+(?:(?:,\s*|,?\s+and\s+)\d+)*) of `?([\w./\\-]+?)`?[.!]?\s*$",
                     re.I | re.M)
MAX_DRAFTS = 5  # most reports drafted for one hand-off
APPROVE = re.compile(r"\bapprove\s+`?(\d{8}-\d{6}-L\d+)`?", re.I)
GROUNDED = re.compile(r"(?<![\w.])(?:\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?|[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,7}(?:/\d{1,3})?"
                      r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\d[\d,]*\d(?:\.\d+)?)(?![\w])", re.I)
SENDER = "Site operations"  # how a report drafted from the watcher's hand-off is signed


def load_dotenv(path: Path | None = None) -> None:
    p = path or ROOT / ".env"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- tools: each one is a wa.py command

def _inside(path_text: str, *roots: Path) -> Path:
    """A log path an agent may read: inside logs/ or examples/ of this repository, never anywhere else.
    Relative paths are tried from the repository and from an agent's folder (agents/<name>/)."""
    p = Path(path_text.strip().strip("`'\"").replace("\\", "/"))
    tries = [p.resolve()] if p.is_absolute() else [(ROOT / p).resolve(), (ROOT / "agents" / "_" / p).resolve()]
    for q in tries:
        for r in roots:
            try:
                q.relative_to(r.resolve())
                return q
            except ValueError:
                continue
    raise ValueError(f"{path_text} is not a log this room may read (only logs/ and examples/)")


def _log_arg(path_text: str) -> str:
    """The log as the command gets it: checked with _inside, then written relative to the repository (wa.py
    resolves it from there), so the room sees logs/access.log rather than a path on this machine."""
    p = _inside(path_text, paths.LOGS, ROOT / "examples")
    try:
        return p.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def run_wa(args: list[str], timeout: int = 120) -> tuple[str, str]:
    """Run one wa.py command. Returns (the command as a person would type it, its output)."""
    shown = "python wa.py " + " ".join(a if " " not in a else f'"{a}"' for a in args)
    r = subprocess.run([sys.executable, str(WA), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), timeout=timeout)
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        out = (out + "\n" + (r.stderr or "").strip()).strip() or f"exit code {r.returncode}"
    return shown, out


def tool_specs(role: str) -> list[dict]:
    def fn(name, desc, props, required=()):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": props, "required": list(required)}}}
    if role == "egress-check":
        return [
            fn("whois", "Which published AI lists contain an IP address, and who holds it in the registry.",
               {"ip": {"type": "string", "description": "an IPv4 or IPv6 address"}}, ["ip"]),
            fn("check_log", "Whose agents are in a web server access log: a verdict for each AI-related request, "
               "and which lines need a report.",
               {"path": {"type": "string", "description": "log path, for example logs/access.log"},
                "since_line": {"type": "integer", "description": "first line to read, default 1"}}),
            fn("summary", "What the log watcher saw recently: counts by verdict and operator, and what needs a report.",
               {"minutes": {"type": "integer", "description": "how far back, default 60"}}),
            fn("lists", "The published IP lists this room checks against: how many, from how many operators, "
               "and how old each one is.", {}),
        ]
    return [
        fn("draft_report", "Draft the report for one request in a log (never sends anything).",
           {"log": {"type": "string", "description": "log path, for example logs/access.log"},
            "line": {"type": "integer", "description": "the line number of the request"},
            "note": {"type": "string", "description": "a paragraph a person asked to add, if any"}}, ["log", "line"]),
        fn("list_drafts", "The drafts written so far, and whether a person approved them.", {}),
    ]


def run_tool(role: str, name: str, args: dict, site: str, sender: str) -> tuple[str, str]:
    """Run one tool for one agent. The model sees the output a person would see (draft_report gives JSON,
    which the writer turns into the report's Markdown)."""
    if role == "egress-check":
        if name == "whois":
            ip = str(args.get("ip", "")).strip()
            ipaddress.ip_address(ip)  # refuses anything that is not an address
            return run_wa(["whois", ip])
        if name == "check_log":
            return run_wa(["check", _log_arg(str(args.get("path") or "logs/access.log")), "--limit", "15",
                           "--since-line", str(max(1, int(args.get("since_line") or 1)))])
        if name == "summary":
            return run_wa(["summary", "--minutes", str(min(1440, max(1, int(args.get("minutes") or 60))))])
        if name == "lists":
            return run_wa(["lists"])
    if role == "abuse-writer":
        if name == "draft_report":
            cmd = ["draft", _log_arg(str(args.get("log") or "logs/access.log")), "--line", str(int(args["line"])),
                   "--json", "--site", site, "--sender", sender]
            if str(args.get("note") or "").strip():
                cmd += ["--note", str(args["note"])[:1000]]
            return run_wa(cmd)
        if name == "list_drafts":
            return run_wa(["drafts"])
    raise ValueError(f"{role} has no tool {name}")


# ---------------------------------------------------------------- the model

class Model:
    def __init__(self) -> None:
        self.base = os.environ.get("WHOSEAGENT_LLM_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("WHOSEAGENT_LLM_MODEL", "")
        self.key = os.environ.get("WHOSEAGENT_LLM_API_KEY", "")
        self.wait = float(os.environ.get("WHOSEAGENT_LLM_WAIT", "600"))  # seconds to wait for a cold start
        if not (self.base and self.model):
            raise RuntimeError("no model: set WHOSEAGENT_LLM_BASE_URL and WHOSEAGENT_LLM_MODEL (and "
                               "WHOSEAGENT_LLM_API_KEY) in the environment or in .env. Any OpenAI-compatible "
                               "endpoint works.")

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0.1, "max_tokens": 700}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        data = json.dumps(body).encode("utf-8")
        last, deadline = "", time.time() + self.wait
        # A serverless GPU that has scaled to zero answers 503 until a machine has started and loaded the
        # model (about 4 minutes for the Modal server in scripts/modal_llm.py), so keep asking until then
        while True:
            req = urllib.request.Request(self.base + "/chat/completions", data=data, headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read())["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}"
                if e.code < 500 and e.code != 429:
                    break
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last = repr(e)
            if time.time() > deadline:
                break
            time.sleep(10)
        raise RuntimeError(f"the model did not answer: {last}")


def draft_reply(cmd: str, out: str) -> str:
    """What the writer posts for a draft: the report exactly as the draft command made it, and how to approve."""
    try:
        d = json.loads(out)
        d["id"]
    except (ValueError, KeyError, TypeError):
        return f"I could not draft that report. `{cmd}` said:\n```\n{out[:1500]}\n```"
    md = re.sub(r"^# ", "#### ", to_markdown(d), flags=re.M)  # a chat-sized heading
    return (md + f"\nReply `approve {d['id']}` to approve it, or tell me what to change. "
            "Nothing is sent: after approval a person sends it from their own mail.\n\n`" + cmd + "`")


def ungrounded(reply: str, sources: str) -> list[str]:
    """The addresses, ranges, email addresses and numbers (two or more digits) in a reply that appear in none of
    the sources. Thousands separators are ignored, so 5,927 matches 5927."""
    flat = sources.replace(",", "")
    out = []
    for tok in GROUNDED.findall(reply):
        t = tok.rstrip(".,")
        if t and t not in sources and t.replace(",", "") not in flat:
            out.append(t)
    return sorted(set(out))


# ---------------------------------------------------------------- the connection to Switch

class Connection:
    """One agent-protocol connection: an SSE stream for events, a heartbeat, and operations bound to it."""

    def __init__(self, creds: Credentials, log=print) -> None:
        self.creds, self.log = creds, log
        self.base = creds.endpoint.rstrip("/")
        self.id = str(uuid.uuid4())
        self.events: queue.Queue = queue.Queue()
        self.cursor: str = ""
        self.state: dict = {}
        self.seen: set[str] = set()
        self.alive = threading.Event()
        self.stop = threading.Event()
        self.room = ""

    def _req(self, method: str, path: str, body: object | None = None, timeout: int = 30, headers=None):
        h = {"Authorization": f"Bearer {self.creds.token}", "Content-Type": "application/json",
             "Accept": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=None if body is None else json.dumps(body).encode("utf-8"))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:500]
        except (urllib.error.URLError, OSError) as e:
            return 0, repr(e)

    def start(self) -> None:
        threading.Thread(target=self._stream_loop, daemon=True).start()
        threading.Thread(target=self._beat_loop, daemon=True).start()
        if not self.alive.wait(30):
            raise SystemExit(f"Could not open the event stream at {self.base}. Is the Switch server running?")

    def _stream_loop(self) -> None:
        while not self.stop.is_set():
            url = (f"{self.base}/agents/{self.creds.agent_id}/events?connection_id={self.id}&scope=all"
                   f"&filter=addressed&start_from=head&client=whoseagent&client_version={__version__}")
            headers = {"Authorization": f"Bearer {self.creds.token}", "Accept": "text/event-stream"}
            if self.cursor:
                headers["Last-Event-ID"] = self.cursor
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as r:
                    self.alive.set()
                    event, data = "", []
                    for raw in r:
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith(":"):
                            continue  # keepalive comment
                        if not line:
                            if data:
                                self._dispatch(event, "\n".join(data))
                            event, data = "", []
                        elif line.startswith("id:"):
                            self.cursor = line[3:].strip()
                        elif line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        if self.stop.is_set():
                            return
            except Exception as e:  # noqa: BLE001  dropped stream: reconnect with the same connection id
                if not self.stop.is_set():
                    self.log(f"event stream dropped ({e!r}); reconnecting")
            self.alive.clear()
            time.sleep(2)

    def _dispatch(self, event: str, data: str) -> None:
        try:
            frame = json.loads(data)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        if isinstance(frame.get("events"), list):  # a batch
            for f in frame["events"]:
                self._dispatch(event, json.dumps(f))
            return
        kind = frame.get("type") or event
        if kind and kind not in self.seen:
            self.seen.add(kind)
            self.log(f"first '{kind}' event on the stream")
        if kind == "connection_state":
            self.state = frame
        elif kind == "evicted":
            self.log(f"evicted by the server: {frame.get('reason')}")
        elif kind == "message":
            if "payload" not in frame and "body" in frame:
                frame = {"type": "message", "room_id": frame.get("room_id", ""), "payload": frame}
            self.events.put(frame)

    def _beat_loop(self) -> None:
        last = 200
        while not self.stop.is_set():
            if self.alive.is_set():
                status, out = self._req("POST", f"/agents/{self.creds.agent_id}/connection/beat",
                                        {"connection_id": self.id}, timeout=10)
                if status != last:  # say so once when the heartbeat starts or stops being accepted
                    self.log("heartbeat accepted again" if status == 200 else f"heartbeat refused: HTTP {status} {out}")
                    last = status
            time.sleep(2)

    def op(self, name: str, args: dict | None = None):
        status, out = self._req("POST", f"/agents/{self.creds.agent_id}/ops/{name}", args or {},
                                timeout=60, headers={"X-Switch-Connection-Id": self.id})
        if status != 200:
            raise RuntimeError(f"{name}: HTTP {status} {out}")
        return out.get("result") if isinstance(out, dict) else out

    def enter(self, room_id: str) -> dict:
        info = self.op("connect_to_room", {"room_id": room_id, "include_general_instructions": False})
        self.room = room_id
        return info if isinstance(info, dict) else {}

    def typing(self, room_id: str, on: bool) -> None:
        self._req("POST", f"/agents/{self.creds.agent_id}/typing", {"room_id": room_id, "is_typing": on}, timeout=10)

    def typing_while(self, room_id: str) -> threading.Event:
        """Show "is typing" in the room until the returned event is set (the chat drops it after a few seconds,
        so it is sent again every 4)."""
        done = threading.Event()

        def keep() -> None:
            while not done.is_set():
                self.typing(room_id, True)
                done.wait(4)
            self.typing(room_id, False)
        threading.Thread(target=keep, daemon=True).start()
        return done

    def post(self, room_id: str, body: str, thread_id: str | None = None) -> None:
        args = {"body": body}
        if thread_id:
            args["thread_id"] = thread_id
        if self.room != room_id:
            self.enter(room_id)
        try:
            self.op("post_message", args)
        except RuntimeError:  # the server may forget the room when the stream reconnects: enter it again, once
            self.enter(room_id)
            self.op("post_message", args)


# ---------------------------------------------------------------- the agent

class RoomAgent:
    def __init__(self, role: str, agent_dir: Path, site: str, log_path: Path | None = None) -> None:
        if role not in ("egress-check", "abuse-writer"):
            raise SystemExit("role must be egress-check or abuse-writer")
        self.role, self.site = role, site
        self.creds = load_credentials(agent_dir)
        self.name = Path(self.creds.source).stem if self.creds.source.endswith(".json") else f"site-{role}"
        self.brief = (agent_dir / "AGENTS.md").read_text(encoding="utf-8") if (agent_dir / "AGENTS.md").exists() else ""
        self.log_path = log_path or paths.LOGS / f"agent-{role}.jsonl"
        self.conn = Connection(self.creds, log=self.say)
        self.model: Model | None = None
        self.room_info: dict[str, dict] = {}

    def say(self, text: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} [{self.name}] {text}", flush=True)

    def record(self, **fields) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now(), "agent": self.name, **fields}, ensure_ascii=False) + "\n")

    def run(self) -> None:
        self.conn.start()
        self.say(f"connected to {self.creds.endpoint} as {self.creds.agent_id}; waiting for mentions")
        while True:
            frame = self.conn.events.get()
            try:
                self.handle(frame)
            except Exception as e:  # noqa: BLE001  one bad message must not stop the agent
                self.say(f"error: {e!r}")
                self.record(error=repr(e))

    def participants(self, room_id: str, fresh: bool = False) -> list[dict]:
        info = self.room_info.get(room_id)
        if fresh or not info or time.time() - info.get("_at", 0) > 60:
            info = self.conn.enter(room_id)
            info["_at"] = time.time()
            self.room_info[room_id] = info
        return info.get("participants", [])

    def sender_is_person(self, room_id: str, payload: dict) -> tuple[bool, str]:
        """Whether a message comes from a person: a participant of type "user" in the room's own list (the
        ids in events are opaque by design). on_behalf_of is trusted only when the platform posted the message
        (a kickoff posted for the person who started the room); an agent's message is an agent's."""
        who = payload.get("sender_name") or ""
        if payload.get("sender_kind") == "platform" and payload.get("on_behalf_of"):
            who = payload["on_behalf_of"]
        for fresh in (False, True):
            for p in self.participants(room_id, fresh):
                if p.get("name") == who:
                    return p.get("type") == "user", who
        return False, who

    def handle(self, frame: dict) -> None:
        p, room = frame.get("payload", {}), frame.get("room_id", "")
        if not p.get("addressed") or p.get("sender_name") == self.name:
            return
        text, thread = p.get("body", ""), p.get("thread_id") or p.get("message_id")
        person, sender = self.sender_is_person(room, p)
        sender = sender or "someone"
        # Agents answer people. From another agent (the log watcher posts as site-egress-check) the writer takes
        # only a hand-off, and says no to an approval; anything else from an agent is left alone, so two agents
        # replying in one thread can't keep answering each other.
        if not person and not (self.role == "abuse-writer" and (HANDOFF.search(text) or APPROVE.search(text))):
            self.say(f"left alone: a message from {sender}, an agent: {text[:80]!r}")
            self.record(kind="ignored", sender=sender, message=text[:500])
            return
        self.say(f"message from {sender}: {text[:120]!r}")
        typing = self.conn.typing_while(room)
        try:
            reply, how = self.answer(room, text, sender, p)
        except Exception as e:  # noqa: BLE001  say so in the room rather than go quiet
            self.record(kind="error", message=text[:500], sender=sender, error=repr(e))
            reply, how = f"Something failed while I worked on that: `{type(e).__name__}: {str(e)[:300]}`", "error"
        finally:
            typing.set()
        for part in (reply if isinstance(reply, list) else [reply]):  # several drafts go out one per message
            if part:
                self.conn.post(room, part, thread)
                self.say(f"replied ({how}, {len(part)} characters)")

    def answer(self, room: str, text: str, sender: str, payload: dict) -> tuple[str, str]:
        if self.role == "abuse-writer":
            m = APPROVE.search(text)
            if m:
                return self.approve(room, m.group(1), payload), "approval"
            m = HANDOFF.search(text)
            if m:
                lines = list(dict.fromkeys(int(n) for n in re.findall(r"\d+", m.group(1))))
                drafts = [self.draft(n, m.group(2)) for n in lines[:MAX_DRAFTS]]
                if len(lines) > MAX_DRAFTS:
                    drafts.append(f"I drafted the first {MAX_DRAFTS} of {len(lines)} lines; ask me for the rest.")
                return (drafts[0] if len(drafts) == 1 else drafts), "hand-off"
        try:
            return self.with_model(room, text, sender)
        except RuntimeError as e:  # no model configured, or it did not answer
            self.record(kind="model-error", message=text, error=str(e))
            return f"I can't answer questions right now: {e}", "error"

    def draft(self, line: int, log: str, note: str = "") -> str:
        try:
            cmd, out = run_tool("abuse-writer", "draft_report", {"log": log, "line": line, "note": note},
                                self.site, SENDER)
        except ValueError as e:
            return f"I can't draft that: {e}"
        self.record(kind="draft", command=cmd, output=out[:4000])
        return draft_reply(cmd, out)

    def approve(self, room: str, draft_id: str, payload: dict) -> str:
        person, who = self.sender_is_person(room, payload)
        if not person:
            self.record(kind="approval-refused", draft=draft_id, sender=who)
            return (f"I can't take an approval from {who}: only a person in this room can approve a report. "
                    "Agents, me included, can't.")
        cmd, out = run_wa(["approve", draft_id, "--by", who, "--json"])
        self.record(kind="approval", command=cmd, output=out[:2000])
        try:
            d = json.loads(out)
        except ValueError:
            return f"That approval did not go through. `{cmd}` said:\n```\n{out[:800]}\n```"
        return (f"Approved by {d['approved_by']} at {d['approved_at'][11:16]} UTC. Nothing was sent. The approved "
                f"draft is `{d['eml']}`: open it in your own mail client, check it, and send it yourself.")

    def system_prompt(self, room_name: str, instructions: str, sender: str) -> str:
        tools = ", ".join(t["function"]["name"] for t in tool_specs(self.role))
        return (f"You are {self.name}, an agent in the Switch room '{room_name}' for {self.site}. {sender} wrote "
                "to you. Answer in at most 6 short lines of Markdown, in plain words. If the message speaks to "
                "several agents, answer only the part meant for you.\n"
                f"Your tools ({tools}) run the `wa.py` commands your brief describes and return what they print. "
                "Call a tool for every fact about addresses, logs, lists, reports and counts. State only what a "
                "tool result says. Copy addresses, list names, email addresses and numbers exactly as printed; "
                "never work a number out yourself. What you do and what you may not do is in your brief. If no "
                "tool answers a question, say so in one line. Don't show the commands: they are listed under "
                "your reply.\n\n"
                f"# Your brief\n{self.brief}\n\n# Room instructions\n{instructions}")

    def with_model(self, room: str, text: str, sender: str) -> tuple[str, str]:
        if self.model is None:
            self.model = Model()
        info = self.room_info.get(room) or self.conn.enter(room)
        self.room_info[room] = info
        system = self.system_prompt(info.get("name", ""), info.get("instructions", ""), sender)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": f"{sender}: {text}"}]
        tools, outputs, calls = tool_specs(self.role), [], []
        reply = ""
        for _ in range(4):
            msg = self.model.chat(messages, tools)
            tcs = msg.get("tool_calls") or []
            if not tcs:
                reply = (msg.get("content") or "").strip()
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
            for tc in tcs:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                    cmd, out = run_tool(self.role, name, args, self.site, sender)
                except Exception as e:  # noqa: BLE001  a refused or failed call is shown to the model as such
                    cmd, out = f"{name}({tc['function'].get('arguments')})", f"error: {e}"
                calls.append(cmd)
                outputs.append(out)
                messages.append({"role": "tool", "tool_call_id": tc.get("id", name), "content": out[:7000]})
        drafted = [(c, o) for c, o in zip(calls, outputs) if c.startswith("python wa.py draft ")]
        if drafted:  # a draft is posted exactly as the command made it, never in the model's words
            reply = draft_reply(*drafted[-1])
            self.record(kind="draft", message=text, sender=sender, calls=calls, how="model chose draft")
            return reply, "draft"
        sources = text + "\n" + "\n".join(outputs)
        bad = ungrounded(reply, sources) if reply else []
        first_bad = bad
        if reply and bad:
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": f"These are not in any tool result: {', '.join(bad)}. Rewrite the "
                                                     "reply using only facts from the tool results."}]
            reply = (self.model.chat(messages).get("content") or "").strip()
            bad = ungrounded(reply, sources)
        how = "model" if not first_bad else "model, corrected once"
        if not reply or bad:
            how = "tool output"
            reply = ("I could not answer that from my tools without guessing." if not calls else
                     "Here is what my tools returned, without my summary:\n```\n" + outputs[-1][:2500] + "\n```")
        if calls:
            reply += "\n\n" + " · ".join(f"`{c}`" for c in calls)
        self.record(kind="answer", message=text, sender=sender, calls=calls, ungrounded=first_bad, still=bad,
                    how=how, reply=reply)
        return reply, how


def main(role: str, agent_dir: str | None, site: str) -> None:
    load_dotenv()
    folder = Path(agent_dir) if agent_dir else ROOT / "agents" / role
    RoomAgent(role, folder if folder.is_absolute() else ROOT / folder, site).run()
