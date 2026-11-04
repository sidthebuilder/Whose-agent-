"""Draft the report a site sends when an agent misbehaves: to whom, what was seen, and what is asked.

Every fact in the draft comes from a tool, never from a model: the log lines are quoted as written, the list
URLs and dates come from the registry data, and the recipients come from the operators' published contacts
and the regional registry (RDAP). The draft is written to outbox/ as Markdown and as an unsent .eml that a
person opens, checks and sends from their own mail account. Nothing here sends mail.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from . import paths
from .classify import CONTROL_ONLY, Finding
from .registry import Registry


def load_contacts(path: Path | None = None) -> dict:
    p = path or paths.DATA / "operator_contacts.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def subject(f: Finding) -> str:
    r = f.request
    if f.verdict == "claimed-not-declared":
        op, product = f.claimed
        if product in CONTROL_ONLY:
            return f"Requests from {r.ip} using the {product} name, which no crawler sends"
        return f"Requests using the {product} name from {r.ip}, outside {op}'s published addresses"
    if f.verdict == "declared-other":
        return f"Requests from {r.ip} ({f.declared[0].operator}) using {f.claimed[0]}'s bot name"
    if "RESEARCH-RANGE" in f.flags:
        return f"Requests from your declared research range at {r.ip}"
    if "WRITE-FROM-CRAWLER" in f.flags:
        return f"Write requests from your declared crawler address {r.ip}"
    if "SECRET-PATH" in f.flags:
        return f"Requests for credentials files from {r.ip}"
    return f"Agent traffic from {r.ip} ({f.operator or 'your network'})"


def recipients(f: Finding, rdap: dict, contacts: dict):
    """Who the report goes to, as (To, Cc, notes). To and Cc are lists of (address, why) pairs.

    A bot name used outside the operator's published addresses goes to the network that sent it (its abuse
    contact in the registry), with the operator whose name was used in copy. Traffic from an operator's own
    published range goes to that operator, with the network's abuse contact in copy when the operator only
    rents the address. Only addresses that the operator or the registry publish are used."""
    to, cc, notes = [], [], []
    holder = (rdap.get("holders") or [rdap.get("network") or "unknown"])[0]
    abuse = [(a, f"abuse contact for the network, registered to {holder} (RDAP)") for a in rdap.get("abuse", [])]
    if f.verdict == "claimed-not-declared":
        op = f.claimed[0]
        to += abuse
        if op in contacts:
            cc.append((contacts[op]["contact"],
                       f"{op}'s published contact, so it knows its bot name is used or its lists are incomplete"))
        else:
            notes.append(f"{op} publishes no contact address we could find, so it is not copied.")
    else:
        op = f.operator or (f.claimed[0] if f.claimed else "")
        if op in contacts:
            to.append((contacts[op]["contact"], f"{op}'s published contact ({contacts[op]['source']})"))
            if abuse and op.lower() not in holder.lower():
                cc += abuse
                notes.append(f"The address is registered to {holder}, not {op}: {op} rents it, so the "
                             f"network's abuse contact is copied.")
        else:
            to += abuse
            if op:
                notes.append(f"{op} publishes no contact address we could find, so this goes to the abuse "
                             f"contact of the network, registered to {holder}.")
    if not to and cc:
        to, cc = cc, []
    if not to:
        notes.append("No recipient found: neither the operator nor the registry publishes an address.")
    for c in contacts.values():  # what the operator asks of people who write to it
        if c.get("send_note") and any(addr == c["contact"] for addr, _ in to + cc):
            notes.append(c["send_note"])
    return to, cc, notes


def ask(f: Finding) -> str:
    if f.verdict == "claimed-not-declared":
        op, product = f.claimed
        if product in CONTROL_ONLY:
            return (f"Please identify the customer or system that sent these requests and stop it from presenting "
                    f"itself as {product}, a name {op} says none of its crawlers uses.")
        return (f"Please identify the customer or system that sent these requests and stop it from presenting "
                f"itself as {product}. If this is {op}'s own traffic, please add the address to {op}'s published "
                f"list, so that sites can verify it.")
    if f.verdict == "declared-other":
        return (f"Please tell us why traffic from your published address uses {f.claimed[0]}'s bot name, "
                f"and stop it if it is not yours.")
    if "RESEARCH-RANGE" in f.flags:
        return "Please identify the run behind these requests and stop it, and confirm when it has stopped."
    if "WRITE-FROM-CRAWLER" in f.flags:
        return "A crawler should never write. Please identify the job behind these requests and stop it."
    if "SECRET-PATH" in f.flags:
        return "Please identify the system that asked our server for credentials files, and stop it."
    return "Please tell us which of your products sent these requests, and whether they were expected."


def _utc(f: Finding) -> str:
    w = f.request.when
    return w.astimezone(timezone.utc).strftime("%d %B %Y at %H:%M UTC") if w else f.request.time


def draft(f: Finding, rdap: dict, reg: Registry, contacts: dict, log_name: str,
          related: list[str] | None = None, site: str = "our website", sender: str = "[your name]",
          note: str = "") -> dict:
    r = f.request
    lines = list(related or [])
    if r.raw not in lines:
        lines.insert(0, r.raw)
    to, cc, notes = recipients(f, rdap, contacts)
    now = datetime.now(timezone.utc)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    # the lists that matter: all of the claimed operator's lists when the address is in none of them,
    # otherwise the lists that contain the address
    lists = reg.lists_of(f.claimed[0]) if f.verdict == "claimed-not-declared" else f.declared
    list_lines = []
    for li in lists:
        age = li.age_days()
        dated = f"dated {li.date_in_file[:10]}" if li.date_in_file else "no date in the file"
        list_lines.append(f"- {li.operator} {li.name}: {li.url} ({dated}"
                          f"{f', {age} days old' if age is not None else ''}; our copy fetched {li.fetched_at[:10]})")
    holder = (rdap.get("holders") or ["unknown"])[0]
    n = len(lines)
    body = [
        "Hello,",
        "",
        f"We run {site}. On {_utc(f)} our server received {'this request' if n == 1 else f'these {n} requests'} "
        f"from {r.ip}. The log line{'s are' if n > 1 else ' is'} exactly as our server wrote {'them' if n > 1 else 'it'}:",
        "",
        *[f"    {x}" for x in lines],
        "",
        f"What we checked: {f.explanation}",
    ]
    if list_lines:
        body += ["", "The published lists we checked:", *list_lines]
    body += ["", f"Registry (RDAP): the address is in network {rdap.get('network') or '?'} "
                 f"({rdap.get('start') or '?'} - {rdap.get('end') or '?'}), registered to {holder}"
                 f"{'; source ' + rdap['source'] if rdap.get('source') else ''}."]
    if note.strip():
        body += ["", note.strip()]
    body += [
        "",
        f"What we ask: {ask(f)}",
        "",
        f"The quoted lines are unchanged from our log (SHA-256 {digest}). We can send the full log for the "
        f"period on request.",
        "",
        "Thank you,",
        sender,
    ]
    return {"id": f"{now:%Y%m%d-%H%M%S}-L{r.line_no}", "subject": subject(f), "to": to, "cc": cc,
            "notes": notes, "body": "\n".join(body), "verdict": f.verdict, "flags": f.flags, "ip": r.ip,
            "log": log_name, "line": r.line_no, "quoted_lines": lines, "sha256": digest,
            "created_at": now.isoformat(timespec="seconds"), "approved_by": "", "approved_at": ""}


def to_markdown(d: dict) -> str:
    status = (f"approved by {d['approved_by']} at {d['approved_at']}; a person sends it from their own mail"
              if d.get("approved_by") else "draft, not sent. A person in the room approves it and sends it")
    out = [f"# Report {d['id']}", "", f"**Status:** {status}.", "", f"**Subject:** {d['subject']}", ""]
    for label, rows in (("To", d["to"]), ("Cc", d["cc"])):
        for addr, why in rows:
            out.append(f"**{label}:** {addr} ({why})  ")
    if d["notes"]:
        out += ["", *[f"> {x}" for x in d["notes"]]]
    flags = f", flags {', '.join(d['flags'])}" if d["flags"] else ""
    out += ["", f"Evidence: line {d['line']} of `{d['log']}`, verdict `{d['verdict']}`{flags}.", "", "---", "",
            d["body"], ""]
    return "\n".join(out)


def to_eml(d: dict) -> bytes:
    m = EmailMessage()
    m["Subject"] = d["subject"]
    if d["to"]:
        m["To"] = ", ".join(a for a, _ in d["to"])
    if d["cc"]:
        m["Cc"] = ", ".join(a for a, _ in d["cc"])
    m["X-Unsent"] = "1"  # Outlook and others open it as a draft to check and send
    m.set_content(d["body"])
    return bytes(m)


def save(d: dict, outbox: Path | None = None) -> tuple[Path, Path]:
    outbox = outbox or paths.OUTBOX
    outbox.mkdir(parents=True, exist_ok=True)
    md, eml = outbox / f"{d['id']}.md", outbox / f"{d['id']}.eml"
    md.write_text(to_markdown(d), encoding="utf-8")
    eml.write_bytes(to_eml(d))
    (outbox / f"{d['id']}.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
    return md, eml
