"""Who holds an address, and where abuse reports for it go, from the regional internet registries (RDAP).

rdap.org sends each query to the registry responsible (ARIN, RIPE NCC, APNIC, LACNIC or AFRINIC). Answers
are cached by network range in cache/rdap.json, so one answer serves every address in the same network.
"""
from __future__ import annotations

import ipaddress
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .net import get_json

RDAP_URL = "https://rdap.org/ip/{ip}"


def _vcard(entity: dict) -> dict:
    out = {"fn": "", "emails": []}
    v = entity.get("vcardArray")
    if isinstance(v, list) and len(v) > 1 and isinstance(v[1], list):
        for item in v[1]:
            if not isinstance(item, list) or len(item) < 4:
                continue
            if item[0] == "fn":
                out["fn"] = str(item[3])
            elif item[0] == "email":
                out["emails"].append(str(item[3]).strip())
    return out


def parse_rdap(j: dict) -> dict:
    """Keep what says who holds the network and where to report abuse."""
    holders, abuse, other_emails = [], [], []

    def walk(ents):
        for e in ents or []:
            roles = [r.lower() for r in e.get("roles", [])]
            vc = _vcard(e)
            if "registrant" in roles and vc["fn"]:
                holders.append(vc["fn"])
            if "abuse" in roles:
                abuse.extend(vc["emails"])
            else:
                other_emails.extend(vc["emails"])
            walk(e.get("entities"))

    walk(j.get("entities"))
    if not holders:  # some registries name the holder without a registrant role
        for e in j.get("entities") or []:
            fn = _vcard(e)["fn"]
            if fn:
                holders.append(fn)
                break
    # RIPE and APNIC often put the abuse address in remarks instead of an abuse entity
    if not abuse:
        for r in j.get("remarks") or []:
            for d in r.get("description") or []:
                for word in str(d).replace(",", " ").split():
                    if "@" in word and "abuse" in word.lower():
                        abuse.append(word.strip(".;:<>()'\""))
    dedupe = lambda xs: list(dict.fromkeys(x for x in xs if x))  # noqa: E731
    return {"network": j.get("name", ""), "handle": j.get("handle", ""), "start": j.get("startAddress", ""),
            "end": j.get("endAddress", ""), "country": j.get("country", ""), "holders": dedupe(holders)[:3],
            "abuse": dedupe(abuse)[:3], "other_emails": dedupe(other_emails)[:3],
            "port43": j.get("port43", "")}


class Rdap:
    def __init__(self, offline: bool = False, path: Path | None = None) -> None:
        self.offline = offline
        self.path = path or paths.CACHE / "rdap.json"
        self.cache: dict[str, dict] = (json.loads(self.path.read_text(encoding="utf-8"))
                                       if self.path.exists() else {})

    def _covering(self, ip) -> dict | None:
        best = None
        for rec in self.cache.values():
            try:
                lo, hi = ipaddress.ip_address(rec["start"]), ipaddress.ip_address(rec["end"])
            except (KeyError, ValueError):
                continue
            if lo.version == ip.version and lo <= ip <= hi:
                if best is None or int(hi) - int(lo) < int(ipaddress.ip_address(best["end"])) - int(
                        ipaddress.ip_address(best["start"])):
                    best = rec  # the most specific network wins
        return best

    def lookup(self, ip_text: str) -> dict:
        """RDAP record for the network holding this address, or {"error": ...}. Never raises."""
        try:
            ip = ipaddress.ip_address(ip_text.strip())
        except ValueError:
            return {"error": "not an IP address"}
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            return {"error": "private or reserved address", "holders": [], "abuse": []}
        hit = self._covering(ip)
        if hit or self.offline:
            return hit or {"error": "not in cache (offline)", "holders": [], "abuse": []}
        rec = {"error": "no answer"}
        for attempt in range(3):
            status, data, final = get_json(RDAP_URL.format(ip=ip), timeout=30)
            if status == 200 and isinstance(data, dict):
                rec = parse_rdap(data)
                rec["source"] = final
                break
            rec = {"error": f"HTTP {status}", "source": final}
            if status not in (0, 429, 500, 502, 503, 504):
                break
            time.sleep(2 * (attempt + 1))
        rec["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not rec.get("error"):
            self.cache[f"{rec['start']}-{rec['end']}"] = rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
        return rec


def describe(rec: dict) -> str:
    """One line for a chat message."""
    if rec.get("error"):
        return f"registry lookup failed ({rec['error']})"
    holder = rec["holders"][0] if rec.get("holders") else rec.get("network") or "unknown holder"
    abuse = ", ".join(rec.get("abuse") or []) or "no abuse address published"
    return f"registered to {holder} ({rec.get('network', '')}, {rec.get('start')} - {rec.get('end')}); abuse: {abuse}"
