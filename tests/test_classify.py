from whoseagent.classify import claimed_operator, classify
from whoseagent.registry import ListInfo, Registry

from helpers import (ANTHROPIC_API_IP, BROWSER_UA, GPTBOT_UA, JIO_IP, OPENAI_GPTBOT_IP, first_address, req)


def test_gptbot_from_its_published_address_is_verified(reg):
    f = classify(req(OPENAI_GPTBOT_IP, GPTBOT_UA), reg)
    assert f.verdict == "verified"
    assert f.operator == "OpenAI"
    assert not f.needs_report


def test_gptbot_name_from_a_home_connection_is_claimed_not_declared(reg):
    f = classify(req(JIO_IP, GPTBOT_UA), reg)
    assert f.verdict == "claimed-not-declared"
    assert f.claimed == ("OpenAI", "GPTBot")
    assert "none of OpenAI's 6 published lists" in f.explanation
    assert f.needs_report
    assert f.operator == ""  # the report goes to the network that sent it, not to OpenAI


def test_bot_name_of_one_operator_from_another_operators_address(reg):
    f = classify(req(OPENAI_GPTBOT_IP, "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"), reg)
    assert f.verdict == "declared-other"
    assert f.operator == "OpenAI"
    assert f.needs_report


def test_published_address_without_a_bot_name(reg):
    ip = first_address("OpenAI", "ChatGPT agents (Codex cloud)")
    f = classify(req(ip, BROWSER_UA), reg)
    assert f.verdict == "declared-no-name"
    assert f.operator == "OpenAI"
    assert f.interesting and not f.needs_report


def test_a_person_is_undeclared_and_not_posted(reg):
    f = classify(req(JIO_IP, BROWSER_UA), reg)
    assert f.verdict == "undeclared"
    assert not f.interesting


def test_a_crawler_that_writes_needs_a_report(reg):
    f = classify(req(OPENAI_GPTBOT_IP, GPTBOT_UA, method="POST"), reg)
    assert f.flags == ["WRITE", "WRITE-FROM-CRAWLER"]
    assert f.needs_report


def test_a_user_directed_agent_that_writes_is_flagged_but_not_reported(reg):
    ip = first_address("OpenAI", "ChatGPT-User")
    f = classify(req(ip, "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)", method="POST"), reg)
    assert f.verdict == "verified"
    assert f.flags == ["WRITE"]
    assert not f.needs_report


def test_anthropic_api_address(reg):
    f = classify(req(ANTHROPIC_API_IP, "Claude-User/1.0"), reg)
    assert f.verdict == "verified"
    assert f.operator == "Anthropic"


def test_research_range_is_always_reported():
    # No operator publishes a research range today, so this one is hypothetical, on a documentation prefix.
    r = Registry()
    r.add_list(ListInfo("ExampleLab", "research runs", "research and evaluation runs", "", "", ""))
    r.add_prefix("192.0.2.0/24", "ExampleLab", "research runs")
    f = classify(req("192.0.2.7", BROWSER_UA, method="GET", path="/wiki.cgi?action=save&page=A"), r.finish())
    assert f.verdict == "declared-no-name"
    assert "RESEARCH-RANGE" in f.flags and "WRITE" in f.flags
    assert f.needs_report


def test_signed_requests_are_marked(reg):
    f = classify(req(first_address("OpenAI", "ChatGPT-User"), BROWSER_UA, sig='sig1="https://chatgpt.com"'), reg)
    assert "SIGNED" in f.flags


def test_a_robots_token_used_as_a_user_agent_is_forged_from_anywhere(reg):
    # Google and Apple say no crawler of theirs sends these names (see CONTROL_ONLY in classify.py)
    f = classify(req("66.249.66.1", "Mozilla/5.0 (compatible; Google-Extended)"), reg)
    assert f.verdict == "claimed-not-declared"
    assert "robots.txt token" in f.explanation
    assert classify(req(JIO_IP, "Applebot-Extended/1.0"), reg).needs_report


def test_a_request_for_a_credentials_file_needs_a_report(reg):
    f = classify(req(JIO_IP, BROWSER_UA, path="/backend/.env"), reg)
    assert f.verdict == "undeclared"
    assert f.flags == ["SECRET-PATH"]
    assert f.interesting and f.needs_report
    assert classify(req(JIO_IP, BROWSER_UA, path="/.aws/credentials"), reg).needs_report
    assert not classify(req(JIO_IP, BROWSER_UA, path="/docs/env-setup"), reg).flags


def test_bot_tokens():
    assert claimed_operator("Mozilla/5.0 (compatible; PerplexityBot/1.0)") == ("Perplexity", "PerplexityBot")
    assert claimed_operator("Perplexity-User/1.0") == ("Perplexity", "Perplexity-User")
    assert claimed_operator("Googlebot-Image/1.0") == ("Google", "Googlebot")
    assert claimed_operator("Mozilla/5.0 (compatible; Claude-SearchBot/1.0)") == ("Anthropic", "Claude-SearchBot")
    assert claimed_operator("curl/8.9.1") is None
    assert claimed_operator("") is None
