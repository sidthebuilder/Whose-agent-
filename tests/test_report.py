import email
import hashlib
from email import policy

from whoseagent.classify import classify
from whoseagent.report import draft, load_contacts, recipients, save, to_markdown

from helpers import (ANTHROPIC, ANTHROPIC_API_IP, GPTBOT_UA, JIO, JIO_IP, MICROSOFT, OPENAI_GPTBOT_IP, rdap_fixture,
                     req)


def test_spoofed_gptbot_goes_to_the_network_that_sent_it(reg):
    f = classify(req(JIO_IP, GPTBOT_UA, line_no=31), reg)
    d = draft(f, rdap_fixture(JIO), reg, load_contacts(), "logs/access.log", site="wiki.example.org", sender="Ops")
    assert [a for a, _ in d["to"]] == ["ip.abuse@ril.com"]
    assert d["cc"] == []
    assert any("OpenAI publishes no contact address" in n for n in d["notes"])
    assert "GPTBot" in d["subject"] and JIO_IP in d["subject"]
    assert "    " + f.request.raw in d["body"]  # quoted exactly, indented
    for url in ("https://openai.com/gptbot.json", "https://openai.com/chatgpt-user.json"):
        assert url in d["body"]  # every OpenAI list it is not in is cited
    assert "Reliance Jio Infocomm Limited" in d["body"]
    assert d["sha256"] == hashlib.sha256(f.request.raw.encode()).hexdigest()
    assert d["id"].endswith("-L31")
    assert not d["approved_by"]


def test_anthropic_traffic_goes_to_anthropics_published_contact(reg):
    f = classify(req(ANTHROPIC_API_IP, "Claude-User/1.0"), reg)
    to, cc, notes = recipients(f, rdap_fixture(ANTHROPIC), load_contacts())
    assert [a for a, _ in to] == ["claudebot@anthropic.com"]
    assert cc == []  # Anthropic holds this address itself
    assert any("domain you are reporting about" in n for n in notes)


def test_operator_without_a_contact_goes_to_the_networks_abuse_desk(reg):
    f = classify(req(OPENAI_GPTBOT_IP, GPTBOT_UA, method="POST"), reg)
    to, cc, notes = recipients(f, rdap_fixture(MICROSOFT), load_contacts())
    assert [a for a, _ in to] == ["abuse@microsoft.com"]
    assert any("registered to Microsoft Corporation" in n for n in notes)


def test_a_rented_address_copies_the_network(reg):
    contacts = {"OpenAI": {"contact": "bots@example.org", "source": "test"}}
    f = classify(req(OPENAI_GPTBOT_IP, GPTBOT_UA, method="POST"), reg)
    to, cc, notes = recipients(f, rdap_fixture(MICROSOFT), contacts)
    assert [a for a, _ in to] == ["bots@example.org"]
    assert [a for a, _ in cc] == ["abuse@microsoft.com"]
    assert any("OpenAI rents it" in n for n in notes)


def test_no_recipient_is_invented(reg):
    f = classify(req(JIO_IP, GPTBOT_UA), reg)
    to, cc, notes = recipients(f, {"holders": [], "abuse": []}, {})
    assert to == [] and cc == []
    assert any("No recipient found" in n for n in notes)


def test_scanner_and_forged_token_reports_ask_the_right_thing(reg):
    f = classify(req(JIO_IP, "Mozilla/5.0", path="/.env"), reg)
    d = draft(f, rdap_fixture(JIO), reg, {}, "x.log")
    assert d["subject"] == f"Requests for credentials files from {JIO_IP}"
    assert "asked our server for credentials files" in d["body"]
    f = classify(req(JIO_IP, "Google-Extended"), reg)
    d = draft(f, rdap_fixture(JIO), reg, {}, "x.log")
    assert "which no crawler sends" in d["subject"]
    assert "a name Google says none of its crawlers uses" in d["body"]


def test_a_persons_note_is_added(reg):
    f = classify(req(JIO_IP, GPTBOT_UA), reg)
    d = draft(f, rdap_fixture(JIO), reg, {}, "x.log", note="We saw 40 more requests like this today.")
    assert "We saw 40 more requests like this today." in d["body"]


def test_saved_files_and_an_unsent_eml(reg, tmp_path):
    f = classify(req(JIO_IP, GPTBOT_UA), reg)
    d = draft(f, rdap_fixture(JIO), reg, load_contacts(), "logs/access.log")
    md, eml = save(d, tmp_path)
    assert md.exists() and eml.exists() and (tmp_path / f"{d['id']}.json").exists()
    assert "draft, not sent" in md.read_text(encoding="utf-8")
    msg = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert msg["X-Unsent"] == "1"
    assert msg["To"] == "ip.abuse@ril.com"
    assert f.request.raw in msg.get_content()
    assert "approved by" in to_markdown({**d, "approved_by": "A person", "approved_at": "now"})
