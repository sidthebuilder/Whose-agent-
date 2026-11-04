"""The whoseagent command line.

  lists                    the published lists in use, with their age
  refresh                  fetch every list again from the operator's own URL
  whois IP                 which published lists contain an address, and who holds it (RDAP)
  check LOG                whose agents are in a web server log
  watch LOG --room NAME    follow a log and post each agent to a Switch room as it arrives
  summary                  what the watcher saw in the last N minutes
  draft LOG --line N       draft the report for one request (written to outbox/, never sent)
  drafts                   the drafts, and whether a person approved them
  approve ID --by NAME     record a person's approval; the person sends the mail
  serve                    run the test page that records real agent traffic
  redact LOG --out FILE    replace undeclared addresses with their network before publishing a log
  rooms, post TEXT         check the connection to Switch

Run it as `python wa.py <command>` from the repository, or as `python ../../wa.py <command>` from an agent's
folder. Log paths are tried as given, then relative to the repository. Add --json for machine-readable output,
--offline to use only cached registry answers, --snapshot to use the 31 October 2026 lists.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__, paths
from .classify import LABELS, Finding, classify
from .logparse import Request, parse_line, read_log
from .rdap import Rdap, describe
from .registry import load, refresh


def _print(obj: object, as_json: bool, text: str) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str) if as_json else text)


def _resolve(path_text: str) -> Path:
    """A path as given, or relative to the repository root, so agents can name files from their own folder."""
    p = Path(path_text)
    if p.exists() or p.is_absolute():
        return p
    q = paths.ROOT / path_text
    return q if q.exists() else p


def _display(p: Path) -> str:
    try:
        return p.resolve().relative_to(paths.ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def _utc(r: Request) -> str:
    w = r.when
    return w.astimezone(timezone.utc).strftime("%d %b %H:%M:%S UTC") if w else r.time


def _finding_dict(f: Finding, rec: dict | None = None) -> dict:
    r = f.request
    out = {"line": r.line_no, "time": r.time, "ip": r.ip, "method": r.method, "path": r.path, "ua": r.ua,
           "verdict": f.verdict, "label": f.label(), "flags": f.flags, "needs_report": f.needs_report,
           "claimed": {"operator": f.claimed[0], "product": f.claimed[1]} if f.claimed else None,
           "declared": [{"operator": x.operator, "list": x.name, "purpose": x.purpose, "url": x.url}
                        for x in f.declared],
           "explanation": f.explanation, "advice": f.advice}
    if rec is not None:
        out["registry"] = rec
    return out


# ---------------------------------------------------------------- the lists

def cmd_lists(a) -> None:
    reg = load()
    rows = [{"operator": op, "list": name, "purpose": li.purpose, "url": li.url, "date_in_file": li.date_in_file,
             "age_days": li.age_days(), "fetched_at": li.fetched_at} for (op, name), li in sorted(reg.lists.items())]
    dated = [r["age_days"] for r in rows if r["age_days"] is not None]
    lines = [f"{len(rows)} lists from {len(reg.operators())} operators, {reg.n_ranges} ranges ({reg.source}). "
             f"{sum(1 for r in rows if r['age_days'] is None or r['age_days'] > 30)} are undated or over 30 days old"
             f"{f'; the oldest is {max(dated)} days old' if dated else ''}.", ""]
    for r in rows:
        days = r["age_days"]
        age = "no date" if days is None else f"{days} day{'' if days == 1 else 's'} old"
        lines.append(f"  {r['operator']:<13} {r['list'][:44]:<44} {age:<15} copy fetched {r['fetched_at'][:10]}")
    _print({"source": reg.source, "ranges": reg.n_ranges, "lists": rows}, a.json, "\n".join(lines))


def cmd_refresh(a) -> None:
    s = refresh(log=(lambda *_: None) if a.json else print)
    text = [f"Fetched {s['fetched']} of {s['lists']} lists from the operators' own URLs ({s['prefixes']} prefixes)."]
    text.append(f"Changed since the {s['base']}: {', '.join(s['changed'])}." if s["changed"]
                else f"No list changed since the {s['base']}.")
    if s["kept_old"]:
        text.append(f"Download failed, previous copy kept: {', '.join(s['kept_old'])}.")
    _print(s, a.json, "\n".join(text))


def cmd_whois(a) -> None:
    try:
        ipaddress.ip_address(a.ip)
    except ValueError:
        sys.exit(f"Not an IP address: {a.ip}")
    reg = load()
    found = reg.lookup(a.ip)
    rec = Rdap(offline=a.offline).lookup(a.ip)
    obj = {"ip": a.ip, "source": reg.source,
           "declared": [{"operator": x.operator, "list": x.name, "purpose": x.purpose, "url": x.url} for x in found],
           "registry": rec}
    if found:
        lines = [f"{a.ip} is in {len(found)} published list{'s' if len(found) != 1 else ''}:"]
        lines += [f"  {x.operator} {x.name} ({x.purpose}): {x.url}" for x in found]
    else:
        lines = [f"{a.ip} is in none of the {len(reg.lists)} published lists from {len(reg.operators())} operators."]
    lines.append(f"Registry: {describe(rec)}")
    _print(obj, a.json, "\n".join(lines))


# ---------------------------------------------------------------- logs

def cmd_check(a) -> None:
    log = _resolve(a.log)
    if not log.exists():
        sys.exit(f"No such file: {a.log}")
    reg = load()
    rd = Rdap(offline=a.offline)
    reqs = read_log(log, start_line=a.since_line)
    findings = [classify(r, reg) for r in reqs]
    shown = [f for f in findings if a.all or f.interesting]
    recs: dict[str, dict] = {}
    if not a.no_registry:
        for f in shown[:a.limit]:
            if f.request.ip not in recs:
                recs[f.request.ip] = rd.lookup(f.request.ip)
    counts = Counter(f.label() for f in findings)
    report_lines = [f.request.line_no for f in findings if f.needs_report]
    if a.json:
        _print({"log": _display(log), "requests": len(findings), "source": reg.source, "ranges": reg.n_ranges,
                "counts": dict(counts), "needs_report": report_lines, "shown": min(len(shown), a.limit),
                "findings": [_finding_dict(f, recs.get(f.request.ip)) for f in shown[:a.limit]]}, True, "")
        return
    out = [f"Checked {len(findings)} requests in {_display(log)} against {len(reg.lists)} published lists "
           f"({reg.n_ranges} ranges, {reg.source}).",
           "Verdicts: " + (", ".join(f"{k} {v}" for k, v in counts.most_common()) or "none") + ".",
           (f"Needs a report: line{'s' if len(report_lines) != 1 else ''} {', '.join(map(str, report_lines))}."
            if report_lines else "No request needs a report."), ""]
    for f in shown[:a.limit]:
        r = f.request
        out.append(f"line {r.line_no}  {_utc(r)}  {r.ip}  {r.method} {r.path[:70]}")
        out.append(f"  {f.label()}{' [' + ', '.join(f.flags) + ']' if f.flags else ''}: {f.explanation}")
        if r.ip in recs:
            out.append(f"  Registry: {describe(recs[r.ip])}")
        if f.advice and f.verdict != "verified":
            out.append(f"  Next: {f.advice}")
    if len(shown) > a.limit:
        out.append(f"... {len(shown) - a.limit} more not shown (use --limit).")
    print("\n".join(out))


def cmd_watch(a) -> None:
    from .switch import SwitchClient, SwitchError, load_credentials
    from .watch import Watcher, follow

    log = _resolve(a.log)
    reg = load()
    if a.dry_run:
        def post(text: str) -> None:
            print(text + "\n" + "-" * 72, flush=True)
        where = "this terminal (dry run)"
    else:
        try:
            client = SwitchClient(load_credentials(a.agent_dir, a.agent_name))
            room_id = a.room_id or client.find_room(a.room)
        except SwitchError as e:
            sys.exit(str(e))

        def post(text: str) -> None:
            try:
                client.post(room_id, text)
            except SwitchError as e:
                print(f"post failed: {e}", file=sys.stderr, flush=True)
        where = f"room {a.room or room_id}"
    name = _display(log)
    w = Watcher(reg, Rdap(offline=a.offline), post, name, window=a.window, redact=a.redact,
                handoff=a.handoff, everything=a.everything)
    newest = max((li.fetched_at for li in reg.lists.values()), default="")[:10]
    print(f"Watching {name}, posting to {where}. Checking against {len(reg.lists)} lists, {reg.n_ranges} ranges "
          f"({reg.source}). Ctrl+C to stop.", flush=True)
    if not a.quiet:
        post(f"Watching `{name}`. Each request that names an AI agent, comes from a published AI address or "
             f"raises a flag is checked against {len(reg.lists)} lists from {len(reg.operators())} operators "
             f"({reg.n_ranges} ranges, newest copy fetched {newest}) and the regional registries.")
    try:
        for n, line in follow(log, from_start=a.from_start):
            w.handle(n, line)
    except KeyboardInterrupt:
        print(f"Stopped. Posted {w.posted} messages.")


def cmd_summary(a) -> None:
    p = Path(a.findings) if a.findings else paths.LOGS / "findings.jsonl"
    if not p.exists():
        sys.exit(f"No findings yet: {p} does not exist. Start the watcher first.")
    since = datetime.now(timezone.utc) - timedelta(minutes=a.minutes)
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            x = json.loads(line)
            if datetime.fromisoformat(x["seen_at"]) >= since:
                rows.append(x)
        except (ValueError, KeyError):
            continue
    ai = [x for x in rows if x.get("claimed") or x.get("declared") or x.get("flags")]
    verdicts = Counter(LABELS.get(x["verdict"], x["verdict"]) for x in ai)
    operators = Counter((x["claimed"][0] if x.get("claimed") else x["declared"][0][0]) for x in ai
                        if x.get("claimed") or x.get("declared"))
    todo = [x for x in ai if x.get("needs_report")]
    obj = {"minutes": a.minutes, "requests": len(rows), "agent_requests": len(ai), "verdicts": dict(verdicts),
           "operators": dict(operators),
           "needs_report": [{"log": x["log"], "line": x["line"], "ip": x["ip"], "verdict": x["verdict"],
                             "claimed": x.get("claimed"), "holder": x.get("holder", "")} for x in todo]}
    lines = [f"In the last {a.minutes} minutes: {len(rows)} requests, {len(ai)} from AI agents or flagged."]
    if ai:
        lines.append("Verdicts: " + ", ".join(f"{k} {v}" for k, v in verdicts.most_common()) + ".")
        lines.append("By operator: " + (", ".join(f"{k} {v}" for k, v in operators.most_common()) or "none") + ".")
    for x in todo:
        who = f"claims {x['claimed'][1]}" if x.get("claimed") else ""
        lines.append(f"Needs a report: line {x['line']} of {x['log']}: {x['ip']} {who}, "
                     f"network {x.get('holder') or 'unknown'}.")
    _print(obj, a.json, "\n".join(lines))


# ---------------------------------------------------------------- reports

def cmd_draft(a) -> None:
    from .report import draft, load_contacts, save, to_markdown

    log = _resolve(a.log)
    if not log.exists():
        sys.exit(f"No such file: {a.log}")
    reg = load()
    reqs = read_log(log)
    target = next((r for r in reqs if r.line_no == a.line), None)
    if target is None:
        sys.exit(f"Line {a.line} of {_display(log)} is not a request in the common or combined log format.")
    f = classify(target, reg)
    rec = Rdap(offline=a.offline).lookup(target.ip)
    same = [r for r in reqs if r.ip == target.ip and r.ua == target.ua]
    i = same.index(target)
    lo = max(0, i - a.max_lines // 2)
    related = [r.raw for r in same[lo:lo + a.max_lines]]
    d = draft(f, rec, reg, load_contacts(), _display(log), related, site=a.site, sender=a.sender, note=a.note)
    md, eml = save(d)
    d["files"] = {"markdown": _display(md), "eml": _display(eml)}
    _print(d, a.json, to_markdown(d) + f"\nSaved {_display(md)} and {_display(eml)} (not sent).")


def cmd_drafts(a) -> None:
    rows = []
    for p in sorted(paths.OUTBOX.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rows.append({"id": d["id"], "created_at": d["created_at"], "subject": d["subject"],
                     "to": [x for x, _ in d["to"]], "approved_by": d.get("approved_by", ""),
                     "approved_at": d.get("approved_at", "")})
    lines = [f"{len(rows)} draft{'s' if len(rows) != 1 else ''} in {_display(paths.OUTBOX)}."]
    for r in rows:
        status = f"approved by {r['approved_by']}" if r["approved_by"] else "waiting for a person"
        lines.append(f"  {r['id']}  {status}  to {', '.join(r['to']) or 'nobody'}  {r['subject']}")
    _print(rows, a.json, "\n".join(lines))


def cmd_approve(a) -> None:
    from .report import to_markdown

    p = paths.OUTBOX / f"{a.id}.json"
    eml = paths.OUTBOX / f"{a.id}.eml"
    if not (p.exists() and eml.exists()):
        sys.exit(f"No draft {a.id} in {_display(paths.OUTBOX)}.")
    if not a.by.strip():
        sys.exit("--by needs the name of the person who approved it.")
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("approved_by"):
        sys.exit(f"{a.id} was already approved by {d['approved_by']} at {d['approved_at']}.")
    d["approved_by"], d["approved_at"] = a.by.strip(), datetime.now(timezone.utc).isoformat(timespec="seconds")
    p.write_text(json.dumps(d, indent=1), encoding="utf-8")
    (paths.OUTBOX / f"{a.id}.md").write_text(to_markdown(d), encoding="utf-8")
    approved = paths.OUTBOX / "approved"
    approved.mkdir(exist_ok=True)
    shutil.copy(eml, approved / eml.name)
    _print({"id": a.id, "approved_by": d["approved_by"], "approved_at": d["approved_at"],
            "eml": _display(approved / eml.name), "sent": False}, a.json,
           f"Approved by {d['approved_by']}. Nothing was sent: open {_display(approved / eml.name)} in your mail "
           f"client, check it, and send it from your own account.")


# ---------------------------------------------------------------- the test page and publishing

def cmd_serve(a) -> None:
    from .honeypage import serve

    srv = serve(a.host, a.port)
    host, port = srv.server_address[:2]
    print(f"Test page on http://{host}:{port}/t/<label> (for example /t/chatgpt). Requests are logged to "
          f"{_display(srv.recorder.access)}.\nPublish it with:  cloudflared tunnel --url http://127.0.0.1:{port}\n"
          f"Ctrl+C to stop.", flush=True)
    if not ipaddress.ip_address(host).is_loopback:
        print("Warning: this listens on every network interface, not only this machine.", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def _network_address(ip: str, reg) -> str:
    """The first address of the /24 (IPv4) or /48 (IPv6) that holds ip, so the registry answer stays the
    same while the host is removed. Falls back to the last address, then to a documentation address."""
    a = ipaddress.ip_address(ip)
    net = ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 48}", strict=False)
    for candidate in (net.network_address, net.broadcast_address):
        if not reg.lookup(str(candidate)):
            return str(candidate)
    return "192.0.2.0" if a.version == 4 else "2001:db8::"


def cmd_redact(a) -> None:
    reg = load()
    total = changed = 0
    log = _resolve(a.log)
    with open(log, encoding="utf-8", errors="replace") as f, open(a.out, "w", encoding="utf-8", newline="\n") as g:
        for raw in f:
            line = raw.lstrip().rstrip("\r\n")
            r = parse_line(line)
            if r:
                total += 1
                if not reg.lookup(r.ip):
                    try:
                        net = _network_address(r.ip, reg)
                    except ValueError:
                        net = r.ip
                    if net != r.ip:
                        line = net + line[len(r.ip):]
                        changed += 1
            g.write(line + "\n")
    print(f"{total} requests; {changed} undeclared addresses replaced by their network address; "
          f"addresses in published AI lists kept. Written to {a.out}.")


def cmd_rooms(a) -> None:
    from .switch import SwitchClient, SwitchError, load_credentials
    try:
        client = SwitchClient(load_credentials(a.agent_dir, a.agent_name))
        rooms = client.rooms()
    except SwitchError as e:
        sys.exit(str(e))
    _print(rooms, a.json, "\n".join(f"{r['id']}  {r['name']}" for r in rooms) or "This agent is in no room.")


def cmd_post(a) -> None:
    from .switch import SwitchClient, SwitchError, load_credentials
    try:
        client = SwitchClient(load_credentials(a.agent_dir, a.agent_name))
        client.post(a.room_id or client.find_room(a.room), a.text)
    except SwitchError as e:
        sys.exit(str(e))
    print("Posted.")


# ---------------------------------------------------------------- argument parsing

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--offline", action="store_true", help="registry answers from the cache only")
    common.add_argument("--snapshot", action="store_true", help="use the 31 October 2026 lists, not the newest copy")
    switch = argparse.ArgumentParser(add_help=False)
    switch.add_argument("--agent-dir", help="the agent's working directory, where Switch Console wrote .switch/")
    switch.add_argument("--agent-name", help="which agent, when that directory holds several")
    room = switch.add_mutually_exclusive_group()
    room.add_argument("--room", help="room name")
    room.add_argument("--room-id", help="room id")

    p = argparse.ArgumentParser(prog="whoseagent", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"whoseagent {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    s = sub.add_parser("lists", parents=[common], help="the published lists in use")
    s.set_defaults(func=cmd_lists)
    s = sub.add_parser("refresh", parents=[common], help="fetch every list again from the operator")
    s.set_defaults(func=cmd_refresh)
    s = sub.add_parser("whois", parents=[common], help="who publishes and who holds an address")
    s.add_argument("ip")
    s.set_defaults(func=cmd_whois)
    s = sub.add_parser("check", parents=[common], help="whose agents are in a log")
    s.add_argument("log")
    s.add_argument("--all", action="store_true", help="show every request, not only AI-related ones")
    s.add_argument("--since-line", type=int, default=1)
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--no-registry", action="store_true", help="skip the registry lookups")
    s.set_defaults(func=cmd_check)
    s = sub.add_parser("watch", parents=[common, switch], help="follow a log and post to a Switch room")
    s.add_argument("log")
    s.add_argument("--dry-run", action="store_true", help="print the messages instead of posting them")
    s.add_argument("--handoff", default="", help="agent to mention when a request needs a report")
    s.add_argument("--window", type=int, default=120, help="seconds before a repeat is posted again")
    s.add_argument("--from-start", action="store_true", help="also post the lines already in the log")
    s.add_argument("--redact", action="store_true", help="show undeclared addresses as their /24 or /48")
    s.add_argument("--everything", action="store_true", help="post every request, people included")
    s.add_argument("--quiet", action="store_true", help="no start message in the room")
    s.set_defaults(func=cmd_watch)
    s = sub.add_parser("summary", parents=[common], help="what the watcher saw recently")
    s.add_argument("--minutes", type=int, default=60)
    s.add_argument("--findings", help="findings file (default logs/findings.jsonl)")
    s.set_defaults(func=cmd_summary)
    s = sub.add_parser("draft", parents=[common], help="draft the report for one request")
    s.add_argument("log")
    s.add_argument("--line", type=int, required=True)
    s.add_argument("--site", default="our website", help="the site the report speaks for")
    s.add_argument("--sender", default="[your name]", help="the name the report is signed with")
    s.add_argument("--note", default="", help="a paragraph a person asked to add")
    s.add_argument("--max-lines", type=int, default=10, help="most log lines to quote")
    s.set_defaults(func=cmd_draft)
    s = sub.add_parser("drafts", parents=[common], help="list the drafts")
    s.set_defaults(func=cmd_drafts)
    s = sub.add_parser("approve", parents=[common], help="record a person's approval of a draft")
    s.add_argument("id")
    s.add_argument("--by", required=True, help="the person who approved it")
    s.set_defaults(func=cmd_approve)
    s = sub.add_parser("serve", parents=[common], help="run the test page")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(func=cmd_serve)
    s = sub.add_parser("redact", parents=[common], help="redact undeclared addresses in a log")
    s.add_argument("log")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_redact)
    s = sub.add_parser("rooms", parents=[common, switch], help="the Switch rooms an agent is in")
    s.set_defaults(func=cmd_rooms)
    s = sub.add_parser("post", parents=[common, switch], help="post one message to a Switch room")
    s.add_argument("text")
    s.set_defaults(func=cmd_post)
    return p


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    if getattr(a, "snapshot", False):
        os.environ["WHOSEAGENT_USE_SNAPSHOT"] = "1"
    a.func(a)
