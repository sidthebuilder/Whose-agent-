"""The declared egress ranges: every IP range that AI operators publish for their bots and agents.

Two sources, both real:
  data/            the snapshot of 31 October 2026 (21 lists from 9 operators), with fetch times and hashes,
                   copied from the Registry Scorecard (github.com/sidthebuilder/declared-egress, commit a797713)
  data/live/       the same lists fetched again from the operators' own URLs by `whoseagent refresh`

`load()` uses the live copy when there is one, so the room always checks against the newest lists it has.
Set WHOSEAGENT_USE_SNAPSHOT=1 (or pass --snapshot) to check against the 31 October snapshot instead.
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .net import http_get


@dataclass(frozen=True)
class ListInfo:
    """One published list: who publishes it, what it is for, where it lives and how old it is."""
    operator: str
    name: str
    purpose: str
    url: str
    date_in_file: str  # the date the operator wrote into the file, "" when it gives none
    fetched_at: str    # when this copy was fetched (UTC, ISO 8601)

    def age_days(self, now: datetime | None = None) -> int | None:
        if not self.date_in_file:
            return None
        try:
            t = datetime.fromisoformat(self.date_in_file.replace("Z", "+00:00"))
        except ValueError:
            return None
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - t).days


class Registry:
    """Every declared range, searchable by address. Ranges may overlap, so a lookup returns every list hit."""

    def __init__(self, source: str = "") -> None:
        self.source = source
        self.lists: dict[tuple[str, str], ListInfo] = {}
        self._ranges: dict[int, list[tuple[int, int, tuple[str, str]]]] = {4: [], 6: []}
        self._index: dict[int, tuple[list[int], int]] = {}

    def add_list(self, info: ListInfo) -> None:
        self.lists[(info.operator, info.name)] = info

    def add_prefix(self, prefix: str, operator: str, name: str) -> None:
        n = ipaddress.ip_network(prefix.strip(), strict=False)
        self._ranges[n.version].append((int(n.network_address), int(n.broadcast_address), (operator, name)))

    def finish(self) -> "Registry":
        for v, rs in self._ranges.items():
            rs.sort()
            uniq = [r for i, r in enumerate(rs) if i == 0 or r != rs[i - 1]]  # one list may repeat a prefix
            self._ranges[v] = uniq
            self._index[v] = ([r[0] for r in uniq], max((r[1] - r[0] for r in uniq), default=0))
        return self

    @property
    def n_ranges(self) -> int:
        return sum(len(v) for v in self._ranges.values())

    def operators(self) -> list[str]:
        return sorted({op for op, _ in self.lists})

    def lists_of(self, operator: str) -> list[ListInfo]:
        return [i for (op, _), i in sorted(self.lists.items()) if op == operator]

    def lookup(self, ip: str) -> list[ListInfo]:
        """Every published list that contains this address (empty when no operator declares it)."""
        try:
            a = ipaddress.ip_address(ip.strip())
        except ValueError:
            return []
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        if a.version not in self._index:
            return []
        starts, span = self._index[a.version]
        rs, x, keys = self._ranges[a.version], int(a), []
        j = bisect.bisect_right(starts, x) - 1
        while j >= 0 and starts[j] >= x - span:
            lo, hi, key = rs[j]
            if lo <= x <= hi and key not in keys:
                keys.append(key)
            j -= 1
        return [self.lists.get(k) or ListInfo(k[0], k[1], "", "", "", "") for k in sorted(keys)]


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load(prefer_live: bool = True, data_dir: Path | None = None) -> Registry:
    """Load the newest copy of the lists: data/live/ when `refresh` has run, else the 31 October snapshot."""
    data_dir = data_dir or paths.DATA
    if os.environ.get("WHOSEAGENT_USE_SNAPSHOT") == "1":
        prefer_live = False
    live = data_dir / "live"
    base = live if prefer_live and (live / "prefixes.csv").exists() else data_dir
    reg = Registry(source="live copy" if base == live else "snapshot of 31 Oct 2026")
    for r in _read_csv(base / "lists.csv"):
        reg.add_list(ListInfo(r["operator"], r["list"], r["purpose"], r["url"], r.get("date_in_file", ""),
                              r.get("fetched_at", "")))
    for p in _read_csv(base / "prefixes.csv"):
        reg.add_prefix(p["prefix"], p["operator"], p["list"])
    return reg.finish()


# ---------------------------------------------------------------- refresh from the operators' own URLs

DATE_KEYS = ("creationTime", "creation_time", "lastUpdated", "last_updated", "updated", "generated_at")


def parse_ranges(body: bytes, section_start: str = "", section_end: str = "") -> tuple[str, list[str]]:
    """Read one range file: the date it gives (or "") and its prefixes. For documentation pages,
    section_start/section_end cut out the part that lists egress addresses. Same rules as the scorecard."""
    text = body.decode("utf-8", "replace").strip()
    if section_start and section_start in text:
        text = text[text.index(section_start):]
        if section_end and section_end in text:
            text = text[:text.index(section_end)]
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    strings: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            strings.append(node)

    created = ""
    if data is None:
        strings = re.findall(r"[0-9A-Fa-f:.]+/\d{1,3}", text)
    else:
        walk(data)
        if isinstance(data, dict):
            created = next((data[k] for k in DATE_KEYS if isinstance(data.get(k), str)), "")
    nets = set()
    for s in strings:
        s = s.strip()
        if len(s) > 50 or not any(c in s for c in ".:"):
            continue
        try:
            nets.add(ipaddress.ip_network(s, strict=False))
        except ValueError:
            pass
    return created, [str(n) for n in sorted(nets, key=lambda n: (n.version, n))]


def refresh(data_dir: Path | None = None, log=print) -> dict:
    """Fetch every list again from the URL in data/sources.csv and write data/live/. A list that fails to
    download keeps its previous copy, and the summary says so."""
    data_dir = data_dir or paths.DATA
    live = data_dir / "live"
    raw = live / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    base = live if (live / "prefixes.csv").exists() else data_dir
    old = {(r["operator"], r["list"]): r for r in _read_csv(base / "lists.csv")}
    old_prefixes: dict[tuple[str, str], list[str]] = {}
    for p in _read_csv(base / "prefixes.csv"):
        old_prefixes.setdefault((p["operator"], p["list"]), []).append(p["prefix"])
    lists, prefixes = [], []
    summary = {"fetched": 0, "kept_old": [], "changed": [], "base": "live copy" if base == live else "snapshot"}
    for s in _read_csv(data_dir / "sources.csv"):
        key = (s["operator"], s["product"])
        r = http_get(s["url"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        created, pfx = ("", [])
        if r["status"] == 200 and r["body"]:
            created, pfx = parse_ranges(r["body"], s.get("section_start", ""), s.get("section_end", ""))
        if not pfx:  # failed or empty: keep what we had, and say so
            summary["kept_old"].append(f"{key[0]} {key[1]} (HTTP {r['status']})")
            if key in old:
                o = old[key]
                lists.append([key[0], key[1], o["purpose"], o["url"], o.get("date_in_file", ""), o.get("fetched_at", "")])
                prefixes += [[key[0], key[1], p] for p in old_prefixes.get(key, [])]
            log(f"  kept old copy  {key[0]:<13} {key[1]}  (HTTP {r['status']})")
            continue
        name = re.sub(r"[^a-z0-9]+", "_", f"{key[0]} {key[1]}".lower()).strip("_")
        (raw / f"{name}.txt").write_bytes(r["body"])
        if sorted(pfx) != sorted(old_prefixes.get(key, [])):
            summary["changed"].append(f"{key[0]} {key[1]}")
        summary["fetched"] += 1
        lists.append([key[0], key[1], s["purpose"], s["url"], created, now])
        prefixes += [[key[0], key[1], p] for p in pfx]
        log(f"  HTTP 200       {key[0]:<13} {key[1]}  {len(pfx)} prefixes, dated {created or 'no date'}"
            f"  sha256 {hashlib.sha256(r['body']).hexdigest()[:12]}")
    with open(live / "lists.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["operator", "list", "purpose", "url", "date_in_file", "fetched_at"])
        w.writerows(lists)
    with open(live / "prefixes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["operator", "list", "prefix"])
        w.writerows(prefixes)
    summary["lists"], summary["prefixes"] = len(lists), len(prefixes)
    return summary
