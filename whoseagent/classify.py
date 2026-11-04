"""Decide whose agent sent a request, from two independent facts:

  1. the name it gives (a bot token in the user agent, such as GPTBot), which anyone can type, and
  2. the address it came from, checked against every range the operators publish.

Verdicts
  verified               claims operator X, and the address is in one of X's published lists
  claimed-not-declared   claims operator X, but the address is in none of X's lists: spoofed, or X's lists
                         are incomplete (only X can say which)
  declared-other         claims operator X, but the address is published by a different operator Y
  declared-no-name       names no known bot, but the address is in an operator's list (agents that drive a
                         normal browser, such as ChatGPT agent, look like this)
  undeclared             names no known bot and no operator declares the address: a person, or an agent
                         whose operator publishes nothing

A name that is only a robots.txt control token (Google-Extended, Applebot-Extended) is forged wherever it
comes from: Google and Apple say no crawler of theirs sends it as a user agent.

Flags (added to any verdict)
  WRITE                  POST, PUT, PATCH, DELETE, or a GET that edits (?action=edit|save|submit)
  WRITE-FROM-CRAWLER     a write from a range declared only for crawlers: crawlers never need to write
  RESEARCH-RANGE         the address is in a range declared for research, training or evaluation runs
  SECRET-PATH            a request for a credentials file (/.env, /.aws/credentials, /.git/config), the paths
                         that scanners forging AI crawler names asked for in GreyNoise's August 2026 report
  SIGNED                 the request carried a Signature-Agent header (Web Bot Auth); recorded, not verified
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .logparse import Request
from .registry import ListInfo, Registry

# user-agent token (lower case) -> (operator, product). Longer tokens are tried first.
BOT_TOKENS: dict[str, tuple[str, str]] = {
    "gptbot": ("OpenAI", "GPTBot"),
    "oai-searchbot": ("OpenAI", "OAI-SearchBot"),
    "chatgpt-user": ("OpenAI", "ChatGPT-User"),
    "oai-adsbot": ("OpenAI", "OAI-AdsBot"),
    "claudebot": ("Anthropic", "ClaudeBot"),
    "claude-user": ("Anthropic", "Claude-User"),
    "claude-searchbot": ("Anthropic", "Claude-SearchBot"),
    "googlebot": ("Google", "Googlebot"),
    "google-agent": ("Google", "Google-Agent"),
    "googleother": ("Google", "GoogleOther"),
    "google-inspectiontool": ("Google", "Google-InspectionTool"),
    "google-cloudvertexbot": ("Google", "Google-CloudVertexBot"),
    "bingbot": ("Microsoft", "Bingbot"),
    "applebot": ("Apple", "Applebot"),
    "perplexitybot": ("Perplexity", "PerplexityBot"),
    "perplexity-user": ("Perplexity", "Perplexity-User"),
    "mistralai-user": ("Mistral", "MistralAI-User"),
    "mistralai-index": ("Mistral", "MistralAI-Index"),
    "ccbot": ("Common Crawl", "CCBot"),
    "duckassistbot": ("DuckDuckGo", "DuckAssistBot"),
    "google-extended": ("Google", "Google-Extended"),
    "applebot-extended": ("Apple", "Applebot-Extended"),
}
# robots.txt control tokens that no crawler sends as a user agent, according to the operators' own pages:
# https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers (Google-Extended) and
# https://support.apple.com/en-us/119829 ("Applebot-Extended does not crawl webpages")
CONTROL_ONLY = {"Google-Extended", "Applebot-Extended"}
_TOKEN_RE = re.compile("|".join(re.escape(t) for t in sorted(BOT_TOKENS, key=len, reverse=True)), re.I)
RESEARCH = re.compile(r"research|evaluation", re.I)
CRAWLER = re.compile(r"crawler", re.I)
SECRET_PATH = re.compile(r"(?:^|/)(?:\.env(?:\.[a-z0-9_-]+)?|\.aws/credentials|\.git/config)$", re.I)

LABELS = {
    "verified": "Verified",
    "claimed-not-declared": "Claimed, not declared",
    "declared-other": "Declared by another operator",
    "declared-no-name": "Declared address, no bot name",
    "undeclared": "Undeclared",
}


@dataclass
class Finding:
    request: Request
    verdict: str
    claimed: tuple[str, str] | None          # (operator, product) from the user agent
    declared: list[ListInfo]                 # lists that contain the address
    flags: list[str] = field(default_factory=list)
    explanation: str = ""
    advice: str = ""

    @property
    def operator(self) -> str:
        """The operator this request should be raised with, when there is one."""
        if self.verdict in ("verified", "declared-no-name", "declared-other"):
            return self.declared[0].operator
        return ""

    @property
    def interesting(self) -> bool:
        """Worth a message in the room: an AI name, a declared address, or a flag."""
        return bool(self.claimed or self.declared or self.flags)

    @property
    def needs_report(self) -> bool:
        return self.verdict in ("claimed-not-declared", "declared-other") or any(
            f in self.flags for f in ("WRITE-FROM-CRAWLER", "RESEARCH-RANGE", "SECRET-PATH"))

    def label(self) -> str:
        return LABELS[self.verdict]


def claimed_operator(ua: str) -> tuple[str, str] | None:
    m = _TOKEN_RE.search(ua or "")
    return BOT_TOKENS[m.group(0).lower()] if m else None


def _lists(infos: list[ListInfo]) -> str:
    return "; ".join(f"{i.operator} {i.name} ({i.purpose})" if i.purpose else f"{i.operator} {i.name}"
                     for i in infos)


def classify(req: Request, reg: Registry) -> Finding:
    declared = reg.lookup(req.ip)
    ops = {d.operator for d in declared}
    claimed = claimed_operator(req.ua)
    if claimed:
        op, product = claimed
        mine = [d for d in declared if d.operator == op]
        if product in CONTROL_ONLY:
            f = Finding(req, "claimed-not-declared", claimed, declared,
                        explanation=f"Claims {product}, which is a robots.txt token, not a crawler: {op} says no "
                                    f"crawler of its sends it as a user agent, so the name is forged.",
                        advice="Report to the network's abuse contact. The name alone proves the request is not "
                               f"from {op}.")
        elif mine:
            f = Finding(req, "verified", claimed, mine,
                        explanation=f"Claims {product} ({op}); the address is in {_lists(mine)}.")
        elif declared:
            f = Finding(req, "declared-other", claimed, declared,
                        explanation=f"Claims {product} ({op}), but the address is published by "
                                    f"{', '.join(sorted(ops))}: {_lists(declared)}.",
                        advice=f"Ask {', '.join(sorted(ops))} why its address uses {op}'s bot name.")
        else:
            n = len(reg.lists_of(op))
            f = Finding(req, "claimed-not-declared", claimed, [],
                        explanation=f"Claims {product} ({op}), but the address is in none of {op}'s "
                                    f"{n} published list{'s' if n != 1 else ''}.",
                        advice=f"Treat as spoofed until {op} says otherwise: either someone is using its bot "
                               f"name, or its published lists are incomplete. Report to the network's abuse "
                               f"contact, and tell {op}.")
    elif declared:
        f = Finding(req, "declared-no-name", None, declared,
                    explanation=f"No bot name in the user agent, but the address is in {_lists(declared)}.",
                    advice="An operator's agent using a normal browser user agent: attribute it to the operator.")
    else:
        f = Finding(req, "undeclared", None, [],
                    explanation="No bot name, and no AI operator declares this address.",
                    advice="A person, or an agent whose operator publishes nothing. The address alone cannot say.")

    if req.is_write:
        f.flags.append("WRITE")
        if declared and all(CRAWLER.search(d.purpose) for d in declared):
            f.flags.append("WRITE-FROM-CRAWLER")
            f.advice = "A crawler never needs to write: report to the operator. " + f.advice
    if any(RESEARCH.search(d.purpose) for d in declared):
        f.flags.append("RESEARCH-RANGE")
        f.advice = ("A range declared for research or evaluation runs: report to the operator within 24 hours. "
                    + f.advice)
    if SECRET_PATH.search(req.path.split("?")[0]):
        f.flags.append("SECRET-PATH")
        f.advice = ("It asked for a credentials file: check the file is not reachable, and report the "
                    "request. " + f.advice)
    if req.signature_agent:
        f.flags.append("SIGNED")
    return f
