import json
from datetime import datetime, timedelta, timezone

import pytest

from whoseagent import paths
from whoseagent.cli import main

from helpers import BROWSER_UA, GPTBOT_UA, JIO_IP, OPENAI_GPTBOT_IP, line


def run(capsys, *argv):
    main(list(argv))
    return capsys.readouterr().out


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("\n".join([
        line(OPENAI_GPTBOT_IP, GPTBOT_UA, path="/t/openai"),  # 1: the real GPTBot address
        line(JIO_IP, GPTBOT_UA, path="/t/spoof"),             # 2: the GPTBot name from a home connection
        line(JIO_IP, BROWSER_UA, path="/t/person"),           # 3: a person
    ]) + "\n", encoding="utf-8")
    return p


def test_lists(capsys):
    assert run(capsys, "lists").startswith("21 lists from 9 operators")


def test_whois(capsys, rdap_cache):
    out = json.loads(run(capsys, "whois", OPENAI_GPTBOT_IP, "--json", "--offline"))
    assert [d["list"] for d in out["declared"]] == ["GPTBot"]
    assert out["registry"]["holders"] == ["Microsoft Corporation"]
    assert "in none of the 21 published lists" in run(capsys, "whois", JIO_IP, "--offline")


def test_check_draft_approve(capsys, log, rdap_cache):
    out = json.loads(run(capsys, "check", str(log), "--json", "--offline"))
    assert out["requests"] == 3
    assert out["needs_report"] == [2]
    assert [f["verdict"] for f in out["findings"]] == ["verified", "claimed-not-declared"]
    assert out["findings"][1]["registry"]["abuse"] == ["ip.abuse@ril.com"]

    d = json.loads(run(capsys, "draft", str(log), "--line", "2", "--json", "--offline", "--site", "wiki.example.org"))
    assert d["to"][0][0] == "ip.abuse@ril.com"
    assert "We run wiki.example.org." in d["body"]
    listed = run(capsys, "drafts")
    assert d["id"] in listed and "waiting for a person" in listed

    out = run(capsys, "approve", d["id"], "--by", "Shashank")
    assert "Nothing was sent" in out
    assert (paths.OUTBOX / "approved" / f"{d['id']}.eml").exists()
    assert "approved by Shashank" in run(capsys, "drafts")
    with pytest.raises(SystemExit, match="already approved"):
        main(["approve", d["id"], "--by", "Someone else"])


def test_draft_rejects_a_line_that_is_not_a_request(log):
    with pytest.raises(SystemExit, match="Line 9"):
        main(["draft", str(log), "--line", "9", "--offline"])


def test_summary_counts_only_the_window(capsys):
    paths.LOGS.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    rows = [
        {"seen_at": now.isoformat(), "log": "logs/access.log", "line": 5, "ip": JIO_IP, "verdict": "claimed-not-declared",
         "flags": [], "needs_report": True, "claimed": ["OpenAI", "GPTBot"], "declared": [], "holder": "Reliance Jio"},
        {"seen_at": (now - timedelta(hours=3)).isoformat(), "log": "logs/access.log", "line": 1, "ip": OPENAI_GPTBOT_IP,
         "verdict": "verified", "flags": [], "needs_report": False, "claimed": ["OpenAI", "GPTBot"],
         "declared": [["OpenAI", "GPTBot"]], "holder": "Microsoft Corporation"},
    ]
    (paths.LOGS / "findings.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = json.loads(run(capsys, "summary", "--minutes", "60", "--json"))
    assert out["requests"] == 1
    assert out["operators"] == {"OpenAI": 1}
    assert out["needs_report"][0]["line"] == 5


def test_redact_keeps_published_addresses(capsys, log, tmp_path):
    out_file = tmp_path / "public.log"
    run(capsys, "redact", str(log), "--out", str(out_file))
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith(OPENAI_GPTBOT_IP + " ")
    assert lines[1].startswith("49.36.10.0 ") and lines[2].startswith("49.36.10.0 ")
    assert JIO_IP not in out_file.read_text(encoding="utf-8")
