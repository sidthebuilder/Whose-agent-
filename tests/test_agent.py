"""The room agents (whoseagent/agent.py): what they may run, who they answer, the approval rule, the check that
a reply states only what a tool printed, and the agent-protocol connection against a stand-in server."""
import contextlib
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from whoseagent import agent, cli, paths
from whoseagent.agent import APPROVE, HANDOFF, Connection, RoomAgent, run_tool, tool_specs, ungrounded
from whoseagent.classify import classify
from whoseagent.switch import Credentials
from whoseagent.watch import message

from helpers import GPTBOT_UA, JIO, JIO_IP, OPENAI_GPTBOT_IP, line, rdap_fixture, req

PEOPLE = [{"name": "site-egress-check", "type": "agent"}, {"name": "site-abuse-writer", "type": "agent"},
          {"name": "shashank", "type": "user"}]


class FakeConn:
    """Stands in for the Switch connection: the room's participants, and what the agent posts."""

    def __init__(self, participants=PEOPLE):
        self.participants, self.posts = participants, []

    def enter(self, room_id):
        return {"name": "site-whose-agent", "instructions": "", "participants": self.participants}

    def typing_while(self, room_id):
        return threading.Event()

    def post(self, room_id, body, thread_id=None):
        self.posts.append((room_id, body, thread_id))


class FakeModel:
    def __init__(self, replies):
        self.replies, self.asked = list(replies), []

    def chat(self, messages, tools=None):
        self.asked.append(messages[-1]["content"])
        return self.replies.pop(0)


def make(role, conn=None, model=None):
    a = RoomAgent.__new__(RoomAgent)  # no credentials file or server needed
    a.role, a.site, a.name, a.brief = role, "wiki.example.org", f"site-{role}", ""
    a.creds, a.log_path = Credentials("http://127.0.0.1:9", "tok", "A1"), paths.LOGS / f"agent-{role}.jsonl"
    a.conn, a.model, a.room_info = conn or FakeConn(), model, {}
    return a


def frame(body, sender, kind=None, on_behalf_of=None, thread=None):
    return {"type": "message", "room_id": "R1", "payload": {
        "addressed": True, "sender_name": sender, "sender_kind": kind, "on_behalf_of": on_behalf_of,
        "message_id": "sw_1", "body": body, "thread_id": thread}}


def in_process(args, timeout=120):
    """run_wa without a subprocess, with registry answers from the cache only (no network in tests)."""
    shown = "python wa.py " + " ".join(a if " " not in a else f'"{a}"' for a in args)  # as run_wa shows it
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            cli.main([*args, "--offline"])
        except SystemExit as e:
            if e.code not in (0, None):
                return shown, f"{out.getvalue()}\n{e.code}".strip()
    return shown, out.getvalue().strip()


def call(name, args):
    return {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def repo(tmp_path, monkeypatch, rdap_cache):
    """A repository of its own: logs/access.log with a GPTBot claim from an Indian ISP on line 1."""
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent, "run_wa", in_process)
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    (paths.LOGS / "access.log").write_text(line(JIO_IP, GPTBOT_UA) + "\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- what the agents may do

def test_no_agent_has_a_tool_that_sends_or_approves():
    names = {t["function"]["name"] for role in ("egress-check", "abuse-writer") for t in tool_specs(role)}
    assert names == {"whois", "check_log", "summary", "lists", "draft_report", "list_drafts"}
    with pytest.raises(ValueError, match="has no tool"):
        run_tool("egress-check", "draft_report", {"log": "logs/access.log", "line": 1}, "x", "y")
    with pytest.raises(ValueError, match="has no tool"):
        run_tool("abuse-writer", "send_mail", {}, "x", "y")


@pytest.mark.parametrize("path", ["../../.env", "data/prefixes.csv", "C:/Windows/win.ini", "/etc/passwd",
                                  "logs/../.env", "switch/stack/.env"])
def test_tools_read_only_logs_and_examples(repo, path):
    with pytest.raises(ValueError, match="not a log this room may read"):
        run_tool("egress-check", "check_log", {"path": path}, "x", "y")


def test_whois_takes_only_an_address(repo):
    with pytest.raises(ValueError):
        run_tool("egress-check", "whois", {"ip": "20.171.206.10; del *"}, "x", "y")
    cmd, out = run_tool("egress-check", "whois", {"ip": OPENAI_GPTBOT_IP}, "x", "y")
    assert cmd == f"python wa.py whois {OPENAI_GPTBOT_IP}"
    assert "OpenAI GPTBot" in out and "Microsoft Corporation" in out


# ---------------------------------------------------------------- the grounding check

def test_ungrounded_finds_what_no_tool_printed():
    src = "21 lists from 9 operators, 5927 ranges. Registry: Microsoft (20.160.0.0 - 20.175.255.255); abuse@microsoft.com"
    assert ungrounded("21 lists, 5,927 ranges; abuse@microsoft.com; 20.160.0.0", src) == []
    assert ungrounded("22 lists; write to gptbot@openai.com about 20.171.206.11", src) == \
        ["20.171.206.11", "22", "gptbot@openai.com"]
    assert ungrounded("9 operators", src) == []  # one digit is not checked


# ---------------------------------------------------------------- who the agents answer

def test_agents_answer_people_and_leave_other_agents_alone():
    conn = FakeConn()
    checker = make("egress-check", conn, FakeModel([{"content": "Hello."}]))
    checker.handle(frame("@site-egress-check what do you make of this?", "site-abuse-writer"))
    assert conn.posts == []
    checker.handle(frame("@site-egress-check hello", "site-egress-check"))  # its own message
    assert conn.posts == []
    checker.handle(frame("@site-egress-check hello", "shashank"))
    assert conn.posts == [("R1", "Hello.", "sw_1")]


def test_the_kickoff_counts_as_the_person_who_started_the_room():
    conn = FakeConn()
    checker = make("egress-check", conn, FakeModel([{"content": "Ready."}]))
    checker.handle(frame("@site-egress-check say hello", "shashank", kind="platform", on_behalf_of="shashank"))
    assert conn.posts[-1][1] == "Ready."


@pytest.mark.parametrize("text, lines", [
    ("@site-abuse-writer please draft a report for line 5 of `logs/access.log`.", ["5"]),
    ("@site-abuse-writer please draft a report for lines 3, 4, and 5 of `logs/access.log`.", ["3", "4", "5"]),
    ("@site-abuse-writer draft reports for lines 3 and 4 of logs/access.log", ["3", "4"]),
])
def test_handoff_wording(text, lines):
    m = HANDOFF.search(text)
    assert m and __import__("re").findall(r"\d+", m.group(1)) == lines and m.group(2) == "logs/access.log"


def test_handoff_from_the_watcher_drafts_the_report_without_a_model(repo, reg):
    handoff = message(classify(req(JIO_IP, GPTBOT_UA), reg), rdap_fixture(JIO), "logs/access.log",
                      handoff="site-abuse-writer")
    m = HANDOFF.search(handoff)
    assert m and m.groups() == ("1", "logs/access.log")
    conn = FakeConn()
    writer = make("abuse-writer", conn)  # no model at all
    writer.handle(frame(handoff, "site-egress-check"))
    body = conn.posts[-1][1]
    draft_id = APPROVE.search(body).group(1)
    assert draft_id.endswith("-L1") and "**To:** ip.abuse@ril.com" in body
    assert "Nothing is sent" in body and '--site wiki.example.org --sender "Site operations"`' in body
    assert (paths.OUTBOX / f"{draft_id}.eml").exists() and writer.model is None


def test_only_a_person_can_approve(repo, reg):
    _, out = in_process(["draft", "logs/access.log", "--line", "1", "--json"])
    draft_id = json.loads(out)["id"]
    conn = FakeConn()
    writer = make("abuse-writer", conn)
    writer.handle(frame(f"@site-abuse-writer approve {draft_id}", "site-egress-check"))
    assert "only a person in this room can approve" in conn.posts[-1][1]
    # an agent naming a person does not make it one: on_behalf_of counts only when the platform posted
    writer.handle(frame(f"@site-abuse-writer approve {draft_id}", "site-egress-check", on_behalf_of="shashank"))
    assert "only a person in this room can approve" in conn.posts[-1][1]
    assert not (paths.OUTBOX / "approved").exists()
    writer.handle(frame(f"@site-abuse-writer approve `{draft_id}`", "shashank"))
    assert conn.posts[-1][1].startswith("Approved by shashank at ") and "Nothing was sent" in conn.posts[-1][1]
    assert (paths.OUTBOX / "approved" / f"{draft_id}.eml").exists()
    assert json.loads((paths.OUTBOX / f"{draft_id}.json").read_text(encoding="utf-8"))["approved_by"] == "shashank"


# ---------------------------------------------------------------- answers through the model

def test_an_answer_lists_the_commands_it_used(repo):
    conn = FakeConn()
    model = FakeModel([call("whois", {"ip": OPENAI_GPTBOT_IP}),
                       {"content": f"Yes: {OPENAI_GPTBOT_IP} is in OpenAI's GPTBot list. Microsoft Corporation holds "
                                   "it, so OpenAI rents it."}])
    checker = make("egress-check", conn, model)
    checker.handle(frame(f"@site-egress-check is {OPENAI_GPTBOT_IP} really GPTBot?", "shashank"))
    body = conn.posts[-1][1]
    assert body.startswith("Yes: 20.171.206.10 is in OpenAI's GPTBot list.")
    assert body.endswith(f"`python wa.py whois {OPENAI_GPTBOT_IP}`")


def test_an_invented_fact_is_corrected_once_then_replaced_by_the_tool_output(repo):
    conn = FakeConn()
    model = FakeModel([call("whois", {"ip": OPENAI_GPTBOT_IP}),
                       {"content": "It is GPTBot. Write to gptbot@openai.com."},
                       {"content": "It is GPTBot. Write to gptbot@openai.com."}])
    checker = make("egress-check", conn, model)
    checker.handle(frame(f"@site-egress-check is {OPENAI_GPTBOT_IP} GPTBot?", "shashank"))
    assert "gptbot@openai.com" in model.asked[-1]  # the model was told what no tool printed
    body = conn.posts[-1][1]
    assert "gptbot@openai.com" not in body and "without my summary" in body and "Microsoft Corporation" in body


def test_a_draft_the_model_asks_for_is_posted_as_the_command_made_it(repo):
    conn = FakeConn()
    model = FakeModel([call("draft_report", {"log": "logs/access.log", "line": 1}),
                       {"content": "Here is a report to abuse@example.com."}])
    writer = make("abuse-writer", conn, model)
    writer.handle(frame("@site-abuse-writer can you write up line 1 of logs/access.log?", "shashank"))
    body = conn.posts[-1][1]
    assert "abuse@example.com" not in body and "**To:** ip.abuse@ril.com" in body and APPROVE.search(body)


class FakeModelServer(BaseHTTPRequestHandler):
    """An OpenAI-compatible endpoint that answers 503 a few times first, as a GPU server does while it starts."""
    busy, calls = 0, []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        FakeModelServer.calls.append((self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != "Bearer k":
            status, out = 401, {"error": "bad key"}
        elif FakeModelServer.busy > 0:
            FakeModelServer.busy -= 1
            status, out = 503, {"error": "no upstreams available"}
        else:
            status, out = 200, {"choices": [{"message": {"role": "assistant", "content": "ready"}}]}
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def model_server(monkeypatch):
    FakeModelServer.busy, FakeModelServer.calls = 2, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeModelServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("WHOSEAGENT_LLM_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
    monkeypatch.setenv("WHOSEAGENT_LLM_MODEL", "m")
    monkeypatch.setenv("WHOSEAGENT_LLM_API_KEY", "k")
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    yield srv
    srv.shutdown()
    srv.server_close()


def test_the_model_is_asked_again_while_its_server_starts(model_server):
    msg = agent.Model().chat([{"role": "user", "content": "hi"}], tool_specs("egress-check"))
    assert msg["content"] == "ready" and len(FakeModelServer.calls) == 3
    path, auth, body = FakeModelServer.calls[-1]
    assert path == "/v1/chat/completions" and body["model"] == "m" and body["tool_choice"] == "auto"


def test_the_model_gives_up_on_a_refusal_or_after_the_wait(model_server, monkeypatch):
    monkeypatch.setenv("WHOSEAGENT_LLM_API_KEY", "wrong")
    with pytest.raises(RuntimeError, match="HTTP 401"):
        agent.Model().chat([{"role": "user", "content": "hi"}])
    assert len(FakeModelServer.calls) == 1  # a 4xx is not retried
    monkeypatch.setenv("WHOSEAGENT_LLM_API_KEY", "k")
    monkeypatch.setenv("WHOSEAGENT_LLM_WAIT", "0")
    FakeModelServer.busy = 5
    with pytest.raises(RuntimeError, match="HTTP 503"):
        agent.Model().chat([{"role": "user", "content": "hi"}])


def test_without_a_model_the_agent_says_so_but_still_drafts(repo, monkeypatch):
    for k in ("WHOSEAGENT_LLM_BASE_URL", "WHOSEAGENT_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    conn = FakeConn()
    checker = make("egress-check", conn)
    checker.handle(frame("@site-egress-check hello", "shashank"))
    assert "no model" in conn.posts[-1][1]
    writer = make("abuse-writer", conn)
    writer.handle(frame("@site-abuse-writer please draft a report for line 1 of `logs/access.log`.",
                        "site-egress-check"))
    assert APPROVE.search(conn.posts[-1][1])


def test_a_failure_is_said_in_the_room():
    conn = FakeConn()
    checker = make("egress-check", conn, None)
    checker.model = type("Broken", (), {"chat": lambda self, m, t=None: (_ for _ in ()).throw(RuntimeError("down"))})()
    checker.handle(frame("@site-egress-check hello", "shashank"))
    assert "down" in conn.posts[-1][1]


# ---------------------------------------------------------------- the agent-protocol connection

class FakeSwitch(BaseHTTPRequestHandler):
    """The agent side of a Switch server, as far as the agents use it: the event stream (Server-Sent Events),
    the heartbeat, and operations bound to the connection by the X-Switch-Connection-Id header."""
    calls: list = []
    stop = threading.Event()

    def log_message(self, *a):
        pass

    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.path.startswith("/agents/A1/events?") or self.headers.get("Accept") != "text/event-stream":
            return self._json(404, {"detail": "no"})
        conn_id = self.path.split("connection_id=")[1].split("&")[0]
        FakeSwitch.calls.append(("stream", conn_id))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        msg = {"type": "message", "room_id": "R1", "payload": {"addressed": True, "sender_name": "shashank",
                                                               "message_id": "sw_9", "body": "@site-egress-check hi"}}
        self.wfile.write(("event: connection_state\ndata: " + json.dumps({"connection_id": conn_id, "rooms": ["R1"]})
                          + "\n\n: keepalive\n\nid: 7\nevent: message\ndata: " + json.dumps(msg) + "\n\n").encode())
        self.wfile.flush()
        while not FakeSwitch.stop.wait(0.2):
            try:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
            except OSError:
                return

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        FakeSwitch.calls.append((self.path, self.headers.get("X-Switch-Connection-Id"), body))
        if self.path == "/agents/A1/connection/beat":
            return self._json(200, {"ok": True})
        if self.path.startswith("/agents/A1/ops/"):
            streams = [c[1] for c in FakeSwitch.calls if c[0] == "stream"]
            if self.headers.get("X-Switch-Connection-Id") not in streams:
                return self._json(409, {"detail": "connection is not open"})
            if self.path.endswith("/connect_to_room"):
                return self._json(200, {"result": {"room_id": body["room_id"], "participants": PEOPLE}})
            return self._json(200, {"result": {"ok": True}})
        self._json(404, {"detail": "unknown"})


def test_connection_reads_the_stream_and_posts_in_the_thread():
    FakeSwitch.calls, FakeSwitch.stop = [], threading.Event()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSwitch)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    conn = Connection(Credentials(f"http://127.0.0.1:{srv.server_address[1]}", "tok", "A1"), log=lambda *_: None)
    try:
        conn.start()
        got = conn.events.get(timeout=5)
        assert got["payload"]["body"] == "@site-egress-check hi" and conn.cursor == "7"
        assert conn.state["rooms"] == ["R1"]
        conn.post("R1", "hello", "sw_9")
        time.sleep(2.5)  # one heartbeat
        ops = [c for c in FakeSwitch.calls if c[0].startswith("/agents/A1/ops/")]
        assert [c[0].rsplit("/", 1)[1] for c in ops] == ["connect_to_room", "post_message"]
        assert all(c[1] == conn.id for c in ops)  # every operation names this connection
        assert ops[-1][2] == {"body": "hello", "thread_id": "sw_9"}
        assert ("/agents/A1/connection/beat", None, {"connection_id": conn.id}) in FakeSwitch.calls
    finally:
        conn.stop.set()
        FakeSwitch.stop.set()
        srv.shutdown()
        srv.server_close()
