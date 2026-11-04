from datetime import datetime, timezone

from whoseagent.logparse import format_line, parse_line


def test_combined_format():
    r = parse_line('66.249.66.1 - - [31/Oct/2026:08:02:40 +0000] "GET /wiki/Startseite HTTP/1.1" 200 5123 "-" '
                   '"Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"', 7)
    assert (r.ip, r.method, r.path, r.status, r.line_no) == ("66.249.66.1", "GET", "/wiki/Startseite", 200, 7)
    assert "Googlebot/2.1" in r.ua
    assert r.referer == ""
    assert not r.is_write
    assert r.when == datetime(2026, 10, 31, 8, 2, 40, tzinfo=timezone.utc)


def test_common_format_without_user_agent():
    r = parse_line('127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326')
    assert r.ua == ""
    assert r.when.year == 2000


def test_apache_escaped_quote_in_user_agent():
    r = parse_line(r'1.2.3.4 - - [04/Nov/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 1 "-" "Bot \"quoted\" 1.0"')
    assert r.ua == r'Bot \"quoted\" 1.0'


def test_writes():
    base = '1.2.3.4 - - [04/Nov/2026:10:00:00 +0000] "{} {} HTTP/1.1" 200 1 "-" "x"'
    assert parse_line(base.format("POST", "/wiki/Talk")).is_write
    assert parse_line(base.format("GET", "/wiki.cgi?action=save&page=X")).is_write
    assert not parse_line(base.format("GET", "/wiki.cgi?page=X")).is_write


def test_honeypage_format_round_trip():
    when = datetime(2026, 11, 4, 18, 2, 11, tzinfo=timezone.utc)
    text = format_line("20.171.206.10", when, "GET", "/t/chatgpt", "HTTP/1.1", 200, 812, "",
                       'Agent with "quotes"\nand a newline', 'sig1="https://chatgpt.com"', "abc123")
    assert "\n" not in text
    r = parse_line(text)
    assert (r.ip, r.path, r.status, r.request_id) == ("20.171.206.10", "/t/chatgpt", 200, "abc123")
    assert r.ua == "Agent with 'quotes' and a newline"
    assert r.signature_agent == "sig1='https://chatgpt.com'"
    assert r.when == when


def test_not_a_log_line():
    assert parse_line("hello world") is None
    assert parse_line("") is None
