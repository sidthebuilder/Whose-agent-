import csv
import shutil
from datetime import datetime, timezone

from whoseagent import paths, registry
from whoseagent.registry import load, parse_ranges

from helpers import ANTHROPIC_API_IP, JIO_IP, OPENAI_GPTBOT_IP, first_ipv6_prefix


def names(found):
    return [(x.operator, x.name) for x in found]


def test_snapshot_has_21_lists_from_9_operators(reg):
    with open(paths.DATA / "prefixes.csv", newline="", encoding="utf-8") as f:
        rows = {(r["operator"], r["list"], r["prefix"]) for r in csv.DictReader(f)}
    assert len(reg.lists) == 21
    assert len(reg.operators()) == 9
    assert reg.n_ranges == len(rows)


def test_lookup_finds_the_list_an_address_is_in(reg):
    assert names(reg.lookup(OPENAI_GPTBOT_IP)) == [("OpenAI", "GPTBot")]
    assert names(reg.lookup(ANTHROPIC_API_IP)) == [("Anthropic", "Claude API outbound")]
    assert ("Google", "Common crawlers (Googlebot)") in names(reg.lookup("66.249.66.1"))


def test_lookup_of_unlisted_and_invalid_addresses(reg):
    assert reg.lookup(JIO_IP) == []
    assert reg.lookup("192.0.2.1") == []
    assert reg.lookup("not an address") == []


def test_ipv4_mapped_ipv6_is_the_same_address(reg):
    assert names(reg.lookup("::ffff:" + OPENAI_GPTBOT_IP)) == names(reg.lookup(OPENAI_GPTBOT_IP))


def test_ipv6_ranges_are_searchable(reg):
    prefix = first_ipv6_prefix("Google", "Common crawlers (Googlebot)")
    assert ("Google", "Common crawlers (Googlebot)") in names(reg.lookup(prefix.split("/")[0]))


def test_list_age_matches_the_scorecard(reg):
    bing = reg.lists[("Microsoft", "Bingbot")]
    assert bing.date_in_file.startswith("2024-01-03")
    assert bing.age_days(datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)) == 1032
    assert reg.lists[("Anthropic", "Claude API outbound")].age_days() is None  # the page gives no date


def test_parse_the_real_snapshot_files():
    snap = paths.DATA / "snapshot-2026-10-31"
    created, prefixes = parse_ranges((snap / "openai_gptbot.json").read_bytes())
    assert created.startswith("2026-09-22")
    assert len(prefixes) == 18
    # Anthropic publishes its API range on an HTML page; the addresses are cut out of one section
    created, prefixes = parse_ranges((snap / "anthropic_claude_api_outbound.json").read_bytes(),
                                     "Outbound IP addresses", "Phased out")
    assert (created, prefixes) == ("", ["160.79.104.0/21"])


def _small_data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    for name in ("lists.csv", "prefixes.csv"):
        shutil.copy(paths.DATA / name, d / name)
    with open(paths.DATA / "sources.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    keep = [r for r in rows if r["product"] in ("GPTBot", "PerplexityBot")]
    with open(d / "sources.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(keep)
    return d


def test_refresh_writes_a_live_copy_and_keeps_failed_lists(tmp_path, monkeypatch):
    d = _small_data_dir(tmp_path)
    gptbot = (paths.DATA / "snapshot-2026-10-31" / "openai_gptbot.json").read_bytes()

    def fake_get(url, timeout=30, accept=""):
        if url.endswith("gptbot.json"):
            return {"status": 200, "final_url": url, "type": "application/json", "body": gptbot}
        return {"status": 0, "final_url": url, "type": "", "body": b"", "error": "offline"}

    monkeypatch.setattr(registry, "http_get", fake_get)
    summary = registry.refresh(data_dir=d, log=lambda *_: None)
    assert summary["fetched"] == 1
    assert summary["kept_old"] == ["Perplexity PerplexityBot (HTTP 0)"]
    assert summary["changed"] == []  # the same file as the snapshot
    monkeypatch.delenv("WHOSEAGENT_USE_SNAPSHOT")
    live = load(data_dir=d)
    assert live.source == "live copy"
    assert names(live.lookup(OPENAI_GPTBOT_IP)) == [("OpenAI", "GPTBot")]
    assert ("Perplexity", "PerplexityBot") in live.lists  # kept from the previous copy


def test_snapshot_switch_overrides_the_live_copy(tmp_path, monkeypatch):
    d = _small_data_dir(tmp_path)
    (d / "live").mkdir()
    for name in ("lists.csv", "prefixes.csv"):
        shutil.copy(d / name, d / "live" / name)
    assert load(data_dir=d).source.startswith("snapshot")  # WHOSEAGENT_USE_SNAPSHOT=1 in every test
    monkeypatch.delenv("WHOSEAGENT_USE_SNAPSHOT")
    assert load(data_dir=d).source == "live copy"
