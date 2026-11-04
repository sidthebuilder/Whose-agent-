import json

import pytest

from whoseagent import rdap
from whoseagent.rdap import Rdap, describe

from helpers import ANTHROPIC, FIXTURES, JIO, MICROSOFT, rdap_fixture


def test_arin_microsoft():
    r = rdap_fixture(MICROSOFT)
    assert r["holders"][0] == "Microsoft Corporation"
    assert "abuse@microsoft.com" in r["abuse"]
    assert (r["start"], r["end"]) == ("20.160.0.0", "20.175.255.255")


def test_arin_anthropic():
    r = rdap_fixture(ANTHROPIC)
    assert r["holders"][0] == "Anthropic, PBC"
    assert r["abuse"] == ["arin@anthropic.com"]


def test_apnic_jio():
    r = rdap_fixture(JIO)
    assert r["holders"][0] == "Reliance Jio Infocomm Limited"
    assert r["abuse"] == ["ip.abuse@ril.com"]
    assert r["country"] == "IN"


def test_one_cached_answer_serves_the_whole_network(tmp_path):
    ms = rdap_fixture(MICROSOFT)
    path = tmp_path / "rdap.json"
    path.write_text(json.dumps({"ms": ms}), encoding="utf-8")
    cache = Rdap(offline=True, path=path)
    assert cache.lookup("20.171.206.99")["holders"] == ["Microsoft Corporation"]
    assert cache.lookup("8.8.8.8")["error"].startswith("not in cache")


def test_the_most_specific_network_wins(tmp_path):
    wide = {"start": "20.0.0.0", "end": "20.255.255.255", "holders": ["Wide"], "abuse": []}
    narrow = {"start": "20.171.206.0", "end": "20.171.206.255", "holders": ["Narrow"], "abuse": []}
    path = tmp_path / "rdap.json"
    path.write_text(json.dumps({"a": wide, "b": narrow}), encoding="utf-8")
    assert Rdap(offline=True, path=path).lookup("20.171.206.10")["holders"] == ["Narrow"]


def test_private_addresses_are_never_sent_to_the_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(rdap, "get_json", lambda *a, **k: pytest.fail("network call"))
    assert "private" in Rdap(path=tmp_path / "c.json").lookup("127.0.0.1")["error"]
    assert "private" in Rdap(path=tmp_path / "c.json").lookup("192.168.1.10")["error"]


def test_an_online_answer_is_cached(tmp_path, monkeypatch):
    raw = json.loads((FIXTURES / JIO).read_text(encoding="utf-8"))
    calls = []

    def fake(url, timeout=30):
        calls.append(url)
        return 200, raw, "https://rdap.apnic.net/ip/49.36.10.1"

    monkeypatch.setattr(rdap, "get_json", fake)
    path = tmp_path / "rdap.json"
    first = Rdap(path=path).lookup("49.36.10.1")
    again = Rdap(path=path).lookup("49.36.200.200")  # same network, from the cache file
    assert first["holders"] == again["holders"] == ["Reliance Jio Infocomm Limited"]
    assert first["source"] == "https://rdap.apnic.net/ip/49.36.10.1"
    assert len(calls) == 1


def test_describe():
    text = describe(rdap_fixture(MICROSOFT))
    assert "Microsoft Corporation" in text and "abuse@microsoft.com" in text
    assert "failed" in describe({"error": "HTTP 429"})
