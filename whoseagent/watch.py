"""Follow a web server log and tell the room, as it happens, about every request that names an AI agent,
comes from a declared AI address, or raises a flag. This is the live half of the workflow: the people in the
room see each agent arrive, with its verdict and its registry record, without opening a terminal.

Messages go to a Switch room through the agent protocol (see switch.py), or to the terminal with --dry-run.
Repeats of the same address, user agent and verdict within --window seconds are counted, not re-posted.
Every finding is also appended to logs/findings.jsonl, which the egress-check agent reads to answer
questions such as "what happened in the last hour?".
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .classify import Finding, classify
from .logparse import parse_line
from .rdap import Rdap
from .registry import Registry


def follow(path: Path, from_start: bool = False, poll: float = 0.5, stop: Callable[[], bool] = lambda: False
           ) -> Iterator[tuple[int, str]]:
    """Yield (line number, line) for lines appended to the file, like `tail -F`. Survives truncation and
    rotation; waits for the file to appear."""
    pos, line_no, ino = 0, 0, None
    existed = path.exists()  # a file that appears later is new from its first line
    while not path.exists():
        if stop():
            return
        time.sleep(poll)
    if existed and not from_start:
        with open(path, "rb") as f:
            for _ in f:
                line_no += 1
            pos = f.tell()
    buf = b""
    while not stop():
        try:
            st = os.stat(path)
        except FileNotFoundError:
            time.sleep(poll)
            continue
        if ino is None:
            ino = (st.st_dev, st.st_ino)
        if st.st_size < pos or (st.st_dev, st.st_ino) != ino:  # truncated or rotated
            pos, line_no, buf, ino = 0, 0, b"", (st.st_dev, st.st_ino)
        if st.st_size > pos:
            with open(path, "rb") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line_no += 1
                yield line_no, raw.decode("utf-8", "replace").rstrip("\r")
        else:
            time.sleep(poll)


def redact_ip(ip: str) -> str:
    """The network part only: /24 for IPv4, /48 for IPv6. Used for addresses no operator declares."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    n = ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 48}", strict=False)
    return f"{n.network_address}/{n.prefixlen}"


def message(f: Finding, rdap: dict, log_name: str, redact: bool = False, handoff: str = "") -> str:
    r = f.request
    when = r.when.strftime("%H:%M:%S UTC") if r.when else r.time
    shown_ip = redact_ip(r.ip) if redact and not f.declared else r.ip
    head = f"**{f.label()}**"
    if f.claimed:
        head += f" · claims {f.claimed[1]} ({f.claimed[0]})"
    elif f.declared:
        head += f" · {f.declared[0].operator} {f.declared[0].name}"
    if f.flags:
        head += " · " + " ".join(f"`{x}`" for x in f.flags)
    out = [head, f"`{r.method} {r.path[:80]}` from `{shown_ip}` at {when} · line {r.line_no} of `{log_name}`",
           f.explanation]
    if rdap.get("error"):
        out.append(f"Registry: lookup failed ({rdap['error']}).")
    else:
        holder = (rdap.get("holders") or [rdap.get("network") or "unknown"])[0]
        reg = f"Registry: {holder} ({rdap.get('network', '')})"
        op = f.operator
        if op and op.lower() not in holder.lower():
            reg += f". {op} rents this address; it does not hold it."
        if f.needs_report and rdap.get("abuse"):
            reg += f" · abuse contact: {', '.join(rdap['abuse'])}"
        out.append(reg)
    if f.request.signature_agent:
        out.append(f"Signed request: Signature-Agent `{f.request.signature_agent[:80]}` (recorded, not verified).")
    if f.advice and f.verdict != "verified":
        out.append(f"Next: {f.advice}")
    if handoff and f.needs_report:
        out.append(f"@{handoff} please draft a report for line {r.line_no} of `{log_name}`.")
    return "\n".join(out)


class Watcher:
    def __init__(self, reg: Registry, rdap: Rdap, post: Callable[[str], None], log_name: str,
                 window: int = 120, redact: bool = False, handoff: str = "", everything: bool = False,
                 findings_path: Path | None = None) -> None:
        self.reg, self.rdap, self.post, self.log_name = reg, rdap, post, log_name
        self.window, self.redact, self.handoff, self.everything = window, redact, handoff, everything
        self.findings_path = findings_path or paths.LOGS / "findings.jsonl"
        self.seen: dict[tuple, list] = {}  # key -> [last posted time, suppressed count]
        self.posted = 0

    def handle(self, line_no: int, line: str) -> Finding | None:
        req = parse_line(line, line_no)
        if not req:
            return None
        f = classify(req, self.reg)
        rec = self.rdap.lookup(req.ip) if (f.interesting or self.everything) else {}
        self._record(f, rec)
        if not (f.interesting or self.everything):
            return f
        key = (req.ip, req.ua, f.verdict, tuple(f.flags))
        now = time.monotonic()
        last = self.seen.get(key)
        if last and now - last[0] < self.window:
            last[1] += 1
            return f
        text = message(f, rec, self.log_name, self.redact, self.handoff)
        if last and last[1]:
            text += f"\n({last[1]} more like this in the last {self.window} s were not posted.)"
        self.seen[key] = [now, 0]
        self.post(text)
        self.posted += 1
        return f

    def _record(self, f: Finding, rec: dict) -> None:
        r = f.request
        self.findings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.findings_path, "a", encoding="utf-8") as out:
            out.write(json.dumps({
                "seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "log": self.log_name,
                "line": r.line_no, "time": r.time, "ip": r.ip, "method": r.method, "path": r.path, "ua": r.ua,
                "verdict": f.verdict, "flags": f.flags, "needs_report": f.needs_report,
                "claimed": list(f.claimed) if f.claimed else None,
                "declared": [[d.operator, d.name] for d in f.declared],
                "holder": (rec.get("holders") or [""])[0] if rec else "",
                "network": rec.get("network", "") if rec else "",
                "abuse": rec.get("abuse", []) if rec else []}) + "\n")
