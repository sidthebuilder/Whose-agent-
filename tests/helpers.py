"""Shared test helpers. Addresses come from the real published lists (31 October 2026 snapshot) and the real
registry answers in fixtures/, so the tests check the code against what operators and registries publish."""
from __future__ import annotations

import csv
import ipaddress
import json
from datetime import datetime, timezone
from pathlib import Path

from whoseagent import paths
from whoseagent.logparse import format_line, parse_line
from whoseagent.rdap import parse_rdap

FIXTURES = Path(__file__).parent / "fixtures"
GPTBOT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.3; +https://openai.com/gptbot"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
WHEN = datetime(2026, 11, 4, 12, 30, 5, tzinfo=timezone.utc)
OPENAI_GPTBOT_IP = "20.171.206.10"    # in OpenAI's GPTBot list; registered to Microsoft
ANTHROPIC_API_IP = "160.79.104.10"    # in Anthropic's API outbound range; registered to Anthropic
JIO_IP = "49.36.10.1"                 # an Indian consumer ISP; in no AI operator's list


def line(ip: str, ua: str, method: str = "GET", path: str = "/t/test", sig: str = "", when: datetime = WHEN) -> str:
    return format_line(ip, when, method, path, "HTTP/1.1", 200, 512, "", ua, sig, "test")


def req(ip: str, ua: str, method: str = "GET", path: str = "/t/test", sig: str = "", line_no: int = 1):
    return parse_line(line(ip, ua, method, path, sig), line_no)


def rdap_fixture(name: str) -> dict:
    return parse_rdap(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


MICROSOFT = "rdap_arin_microsoft.json"
ANTHROPIC = "rdap_arin_anthropic.json"
JIO = "rdap_apnic_jio.json"


def first_address(operator: str, list_name: str) -> str:
    """The first IPv4 address of the first prefix an operator publishes in one list (real snapshot)."""
    with open(paths.DATA / "prefixes.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["operator"] == operator and r["list"] == list_name and ":" not in r["prefix"]:
                return str(ipaddress.ip_network(r["prefix"], strict=False).network_address)
    raise LookupError(f"{operator} {list_name} has no IPv4 prefix")


def first_ipv6_prefix(operator: str, list_name: str) -> str:
    with open(paths.DATA / "prefixes.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["operator"] == operator and r["list"] == list_name and ":" in r["prefix"]:
                return r["prefix"]
    raise LookupError(f"{operator} {list_name} has no IPv6 prefix")
