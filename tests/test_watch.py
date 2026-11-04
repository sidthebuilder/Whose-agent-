import json
import threading
import time

from whoseagent.classify import classify
from whoseagent.watch import Watcher, follow, message, redact_ip

from helpers import BROWSER_UA, GPTBOT_UA, JIO, JIO_IP, MICROSOFT, OPENAI_GPTBOT_IP, line, rdap_fixture, req


class FakeRdap:
    def __init__(self, name):
        self.rec, self.calls = rdap_fixture(name), 0

    def lookup(self, ip):
        self.calls += 1
        return self.rec


def collect(path, n, **kw):
    got, stop = [], threading.Event()

    def run():
        for item in follow(path, poll=0.02, stop=stop.is_set, **kw):
            got.append(item)
            if len(got) >= n:
                stop.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return got, stop, t


def test_follow_reads_only_new_lines(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("old line\n", encoding="utf-8")
    got, stop, t = collect(p, 2)
    time.sleep(0.2)
    with open(p, "a", encoding="utf-8") as f:
        f.write("new 1\nnew ")
        f.flush()
        time.sleep(0.1)
        f.write("2\n")  # a line written in two parts arrives once, whole
    t.join(5)
    stop.set()
    assert got == [(2, "new 1"), (3, "new 2")]


def test_a_log_that_appears_later_is_read_from_its_first_line(tmp_path):
    p = tmp_path / "access.log"
    got, stop, t = collect(p, 2)
    time.sleep(0.2)
    p.write_text("first\nsecond\n", encoding="utf-8")
    t.join(5)
    stop.set()
    assert got == [(1, "first"), (2, "second")]


def test_follow_from_start_and_after_truncation(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("a\nb\n", encoding="utf-8")
    got, stop, t = collect(p, 3, from_start=True)
    time.sleep(0.2)
    p.write_text("c\n", encoding="utf-8")  # rotated: shorter than before
    t.join(5)
    stop.set()
    assert got == [(1, "a"), (2, "b"), (1, "c")]


def test_watcher_posts_once_hands_off_and_records_everything(reg, tmp_path):
    posted, rd = [], FakeRdap(JIO)
    w = Watcher(reg, rd, posted.append, "logs/access.log", window=60, handoff="site-abuse-writer",
                findings_path=tmp_path / "findings.jsonl")
    spoof = line(JIO_IP, GPTBOT_UA)
    w.handle(1, spoof)
    w.handle(2, spoof)  # the same again within the window: counted, not posted
    w.handle(3, line(JIO_IP, BROWSER_UA))  # a person: recorded, not posted, no registry lookup
    w.handle(4, "not a log line")
    assert len(posted) == 1
    assert posted[0].startswith("**Claimed, not declared** · claims GPTBot (OpenAI)")
    assert "Reliance Jio Infocomm Limited" in posted[0] and "ip.abuse@ril.com" in posted[0]
    assert "@site-abuse-writer please draft a report for line 1 of `logs/access.log`." in posted[0]
    assert rd.calls == 2
    rows = [json.loads(x) for x in (tmp_path / "findings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["verdict"] for r in rows] == ["claimed-not-declared", "claimed-not-declared", "undeclared"]
    assert rows[0]["needs_report"] and rows[0]["claimed"] == ["OpenAI", "GPTBot"]


def test_verified_message_says_who_holds_the_address(reg):
    text = message(classify(req(OPENAI_GPTBOT_IP, GPTBOT_UA), reg), rdap_fixture(MICROSOFT), "logs/access.log",
                   handoff="site-abuse-writer")
    assert text.startswith("**Verified** · claims GPTBot (OpenAI)")
    assert "Registry: Microsoft Corporation (MSFT). OpenAI rents this address; it does not hold it." in text
    assert "abuse contact" not in text and "@site-abuse-writer" not in text


def test_redact_shows_only_the_network_of_undeclared_addresses(reg):
    text = message(classify(req(JIO_IP, GPTBOT_UA), reg), rdap_fixture(JIO), "x.log", redact=True)
    assert "49.36.10.0/24" in text and JIO_IP not in text
    assert redact_ip("2001:db8:1234:5678::1") == "2001:db8:1234::/48"
