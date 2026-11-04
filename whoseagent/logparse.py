"""Read web server access logs in the common or combined log format (Apache, nginx, and our honeypage).

The honeypage appends two more quoted fields after the user agent, the way nginx custom formats do:
the Signature-Agent header (set by agents that sign their requests with Web Bot Auth) and a request id.
Standard parsers ignore extra fields, and so does this one when they are missing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_Q = r'(?:[^"\\]|\\.)*'  # the inside of a quoted field; Apache writes an embedded quote as \"
LOG_LINE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)(?: [^"]*)?" '
    r'(?P<status>\d{3}) (?P<size>\S+)'
    rf'(?: "(?P<referer>{_Q})" "(?P<ua>{_Q})")?'
    rf'(?: "(?P<sig>{_Q})")?(?: "(?P<rid>{_Q})")?')
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WRITE_GET = re.compile(r"[?&](action=(edit|save|submit)|save=|edit=)", re.I)


@dataclass(frozen=True)
class Request:
    ip: str
    time: str        # as written in the log, e.g. 04/Nov/2026:18:02:11 +0000
    method: str
    path: str
    status: int
    ua: str
    referer: str = ""
    signature_agent: str = ""
    request_id: str = ""
    line_no: int = 0
    raw: str = ""

    @property
    def is_write(self) -> bool:
        return self.method in WRITE_METHODS or (self.method == "GET" and bool(WRITE_GET.search(self.path)))

    @property
    def when(self) -> datetime | None:
        try:
            return datetime.strptime(self.time, "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            return None


def _clean(v: str | None) -> str:
    return "" if v in (None, "-") else v


def parse_line(line: str, line_no: int = 0) -> Request | None:
    m = LOG_LINE.match(line.strip())
    if not m:
        return None
    g = m.groupdict()
    return Request(ip=g["ip"], time=g["time"], method=g["method"].upper(), path=g["path"],
                   status=int(g["status"]), ua=_clean(g["ua"]), referer=_clean(g["referer"]),
                   signature_agent=_clean(g["sig"]), request_id=_clean(g["rid"]), line_no=line_no,
                   raw=line.rstrip("\r\n"))


def read_log(path, start_line: int = 1) -> list[Request]:
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if i < start_line:
                continue
            r = parse_line(line, i)
            if r:
                out.append(r)
    return out


def format_line(ip: str, when: datetime, method: str, path: str, proto: str, status: int, size: int,
                referer: str, ua: str, signature_agent: str = "", request_id: str = "") -> str:
    """One line in the combined format, plus the two honeypage fields."""
    def q(v: str) -> str:  # quotes and line breaks are replaced, so every parser can split the line
        v = (v or "-").replace('"', "'").replace("\r", " ").replace("\n", " ")
        return f'"{v[:1000]}"'
    stamp = when.strftime("%d/%b/%Y:%H:%M:%S %z")
    return (f'{ip} - - [{stamp}] "{method} {path} {proto}" {status} {size} {q(referer)} {q(ua)} '
            f'{q(signature_agent)} {q(request_id)}')
