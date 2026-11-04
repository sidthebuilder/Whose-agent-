# Whose agent is this?

**A Switch room where a website's operations team and two AI agents work out, as requests arrive, whose AI agents
are visiting the site, and draft the report when one misbehaves. A person approves every report and sends it
from their own mail. No agent can.**

Built for Flip the Switch (WeMakeDevs × DevTools Academy × Switch, 4 to 7 November 2026), Track 01: Best
Business Workflow.

## The problem

Every request names its sender in the user agent, and anyone can type `GPTBot` there. In August 2026 GreyNoise
reported scanners on 824 addresses forging 13 AI crawler names while asking web servers for `/.env` and cloud
key files. None of those addresses was in the IP lists the operators publish
([ppc.land summary](https://ppc.land/scanners-posing-as-claudebot-hunt-credential-files-from-824-addresses/)).

The reliable check is the address: is it in a list the operator publishes for that bot? Doing that by hand is
slow. Nine operators publish 21 lists in different formats, one of them an HTML page. 11 of the 21 are undated
or more than 30 days old. They change without notice: between 31 October and 4 November 2026 OpenAI added 806
prefixes to its Codex cloud list and Anthropic added `153.61.208.0/20` to its API range (`wa.py refresh`).
Once you know whose agent it was, you still need to know whom to tell. Most of these addresses are rented cloud
space, so it's often the network's abuse desk, not the operator.

## What happens in the room

```mermaid
sequenceDiagram
    autonumber
    participant Site as Website log
    participant W as wa.py watch
    participant Room as Switch room (site-whose-agent)
    participant EC as site-egress-check (agent)
    participant AW as site-abuse-writer (agent)
    participant P as A person on the team
    Site->>W: a new line in access.log
    W->>W: check the address against 21 published lists and the registry (RDAP)
    W->>Room: post as site-egress-check: verdict, evidence, registry holder
    Note over W,Room: when a report is needed the post ends with "@site-abuse-writer please draft a report for line N"
    Room->>AW: the mention wakes the writer
    AW->>AW: wa.py draft logs/access.log --line N
    AW->>Room: subject, To and Cc with reasons, quoted evidence; .eml attached
    P->>EC: "@site-egress-check is 20.171.206.10 really GPTBot?"
    EC->>Room: answer from wa.py whois, quoting the list and the registry
    P->>AW: "approve 20261104-154558-L2"
    AW->>AW: wa.py approve ... --by "the person"
    AW->>Room: approved .eml; nothing sent
    P->>P: opens the .eml in their own mail, checks it, sends it
```

What the watcher posted in the local end-to-end run (`scripts/smoke_e2e.py`). The lists and registry answers are
real. The requests were sent by the script from this machine, with the source address set the way the tunnel
sets it: `20.171.206.10` is in OpenAI's GPTBot list, and `49.36.10.1` is an arbitrary address in Reliance Jio's
network standing in for a home connection. No request came from either address.

> **Verified** · claims GPTBot (OpenAI)
> `GET /t/openai-range` from `20.171.206.10` at 15:45:58 UTC · line 1 of `logs/smoke/access.log`
> Claims GPTBot (OpenAI); the address is in OpenAI GPTBot (training crawler).
> Registry: Microsoft Corporation (MSFT). OpenAI rents this address; it does not hold it.

> **Claimed, not declared** · claims GPTBot (OpenAI)
> `GET /t/spoof` from `49.36.10.1` at 15:45:58 UTC · line 2 of `logs/smoke/access.log`
> Claims GPTBot (OpenAI), but the address is in none of OpenAI's 6 published lists.
> Registry: Reliance Jio Infocomm Limited (RELIANCEJIO-IN) · abuse contact: ip.abuse@ril.com
> Next: Treat as spoofed until OpenAI says otherwise [...]
> @site-abuse-writer please draft a report for line 2 of `logs/smoke/access.log`.

The draft the writer then posts goes to `ip.abuse@ril.com`, the abuse address the Asian registry (APNIC)
publishes for that network. It quotes the log line exactly, with its SHA-256, and lists all six OpenAI files it
was checked against, with their dates. OpenAI is not copied, because its bots page lists no contact address.

## How Switch is used

| | |
|---|---|
| **Agents** | Two agents registered in Switch Console, each a Claude Code, Codex or OpenCode session in its own folder of this repository: [`agents/egress-check`](agents/egress-check/AGENTS.md) and [`agents/abuse-writer`](agents/abuse-writer/AGENTS.md). The `AGENTS.md` in each folder (and `CLAUDE.md`, which imports it) lists the commands the agent runs and what it must not do. |
| **Room** | One private room, `site-whose-agent`, created with both agents and you in it by [`switch/whose-agent.template.yaml`](switch/whose-agent.template.yaml). It carries room instructions (who does what, and the rules every agent follows), a room document `verdicts`, and a kickoff that makes both agents introduce themselves. |
| **Who may address whom** | Both agents use `addressing: anyone`, so anyone in this private room can ask them, including each other. The writer's instructions limit approval further: it accepts `approve <id>` only from a person, checking with `list_participants` when it can't tell, and `wa.py approve` records who approved. This rule lives in the agent's instructions, not in code; what stops a bad report going out is that no agent can send mail. |
| **Live feed** | `wa.py watch` follows the log and posts into the room as `site-egress-check` through the agent protocol (`POST /agents/{id}/message`), using the credentials Switch Console wrote in `agents/egress-check/.switch/agents/`. |
| **Hand-off** | A finding that needs a report ends with a mention of the writer, which wakes it in the same thread. People can also ask either agent directly. |
| **Shared context** | The thread keeps the evidence, the draft (also saved as a room document `report <id>` and an attached `.eml`) and the approval, so anyone who joins later sees what happened and who signed off. |
| **Human gate** | The writer drafts and records approvals. Sending stays with a person, from their own mail. |

Without Switch the commands still run in a terminal, but the team loses the parts that make it a workflow: the
live feed in the channel they already use, the hand-off between the two agents, and the record of who approved
which report.

## The data is real

| What | Where it comes from |
|---|---|
| Published IP lists | The 21 lists from 9 operators (OpenAI, Anthropic, Google, Microsoft, Apple, Perplexity, Mistral, Common Crawl, DuckDuckGo), fetched from the operators' own URLs. `data/` holds the 31 October 2026 snapshot with a SHA-256 manifest ([from my Registry Scorecard](https://github.com/sidthebuilder/declared-egress)); `data/live/` holds the copy `wa.py refresh` fetched on 4 November 2026. |
| Who holds an address, and its abuse contact | RDAP answers from the regional internet registries (ARIN, RIPE NCC, APNIC, LACNIC, AFRINIC) through rdap.org, fetched live and cached in `cache/rdap.json`. |
| Operator contacts | Only addresses the operators publish themselves: Apple's Applebot page and Anthropic's ClaudeBot user agent ([`data/operator_contacts.json`](data/operator_contacts.json), with sources). Anyone else is reached through the network's abuse contact. |
| Agent traffic | `wa.py serve` runs a small test page that logs every request. Put it behind a tunnel and ask real assistants (ChatGPT, Claude, Perplexity, Gemini and others) to open it: each visit is a real request from that operator's infrastructure, checked live in the room. The page says plainly that it logs requests. |

## Run it

You need Python 3.11 or later (standard library only), [Switch Console](https://github.com/sandbox-quantum/switch/releases)
with Docker, one of Claude Code, Codex or OpenCode, and for real traffic
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).

1. **Clone** this repository, for example to `C:/code/whose-agent`, and run `python wa.py lists` to check it works.
2. **Set up Switch.** Install Switch Console, add a local server (it includes Mattermost), and set up your agent
   provider ([getting started](https://docs.switchagents.ai/flintai/switch/getting-started)).
3. **Create the room.** In Switch Console open Templates, use `switch/whose-agent.template.yaml`, and enter your
   team name (default `site`), your website, and the path of your clone. Switch creates `site-egress-check`,
   `site-abuse-writer` and the private room `site-whose-agent`, and both agents introduce themselves.
4. **Start the test page and the tunnel**, each in its own terminal:
   ```
   python wa.py serve
   cloudflared tunnel --url http://127.0.0.1:8787
   ```
   cloudflared prints a `https://<random>.trycloudflare.com` address.
5. **Start the live feed:**
   ```
   python wa.py watch logs/access.log --room site-whose-agent --agent-dir agents/egress-check --handoff site-abuse-writer
   ```
   Add `--dry-run` to print the messages instead of posting them.
6. **Send agents to the page.** Ask each assistant to open `https://<random>.trycloudflare.com/t/<its-name>`
   (for example `/t/chatgpt`) and tell you the verification word. Each visit appears in the room within seconds.
   To see the spoof case, send the GPTBot name yourself:
   `curl -A "Mozilla/5.0 (compatible; GPTBot/1.3; +https://openai.com/gptbot)" https://<random>.trycloudflare.com/t/spoof`.
   That draft goes to your own internet provider's abuse desk, so don't send it.
7. **Work in the room.** Ask `@site-egress-check` about any address or log, reply `approve <id>` to a draft you
   agree with, then open the approved `.eml` from `outbox/approved/` and send it yourself.

To publish a log you captured, first run `python wa.py redact logs/access.log --out examples/access.log`. It
replaces every address that no AI operator publishes with the first address of its network, so the registry
answer stays the same and the host is removed.

### Commands

| Command | What it does |
|---|---|
| `wa.py lists` | the lists in use, their dates and ages |
| `wa.py refresh` | fetch every list again from the operator's URL; a failed download keeps the previous copy |
| `wa.py whois IP` | the lists that contain an address, and its registry holder and abuse contact |
| `wa.py check LOG` | every AI-related request in a log, with verdicts; `--all` for every request |
| `wa.py watch LOG` | follow a log and post to a Switch room (or `--dry-run`) |
| `wa.py summary` | what the watcher saw in the last `--minutes` |
| `wa.py draft LOG --line N` | the report for one request, to `outbox/` as Markdown, JSON and an unsent `.eml` |
| `wa.py drafts`, `wa.py approve ID --by NAME` | list drafts; record a person's approval |
| `wa.py serve` | the test page |
| `wa.py redact LOG --out FILE` | hide undeclared hosts before publishing a log |
| `wa.py rooms`, `wa.py post TEXT` | check the connection to Switch |

Agents add `--json` for exact fields. `--offline` uses only cached registry answers; `--snapshot` checks against
the 31 October lists instead of the newest copy.

## Verdicts

| Verdict | Meaning |
|---|---|
| Verified | Names operator X, and the address is in one of X's published lists |
| Claimed, not declared | Names X, but the address is in none of X's lists: spoofed, or X's lists are incomplete |
| Declared by another operator | Names X, but operator Y publishes the address |
| Declared address, no bot name | No bot name, but an operator publishes the address (agents that drive a normal browser look like this) |
| Undeclared | No bot name, and no AI operator publishes the address: a person, or an agent whose operator publishes nothing |

Flags: `WRITE`, `WRITE-FROM-CRAWLER` (crawlers never need to write), `RESEARCH-RANGE`, `SECRET-PATH` (a request
for `/.env`, `/.aws/credentials` or `/.git/config`), and `SIGNED` (a Web Bot Auth `Signature-Agent` header,
recorded but not verified). `Google-Extended` and `Applebot-Extended` are robots.txt tokens that Google and Apple
say no crawler sends, so a request using either name is forged wherever it comes from.

## Checks

```
python -m pytest -q          # 71 tests, about 5 s, no network
python scripts/smoke_e2e.py  # the test page, the watcher and a draft together, with live registry lookups
```

The tests run against the real 31 October snapshot and real RDAP answers saved in `tests/fixtures/`. They cover
each verdict and flag with addresses taken from the published lists, the registry parsing for ARIN and APNIC,
the recipient rules, the unsent `.eml`, the watcher's repeat handling and log rotation, the test page, the
Switch client against a fake bridge, and the template itself: every placeholder is declared, every agent folder
exists, and every command the agents are told to run exists.

## Limits

- An address check tells you whose network sent a request, not who wrote the software. A request from an
  operator's published range came from that operator's infrastructure; a request outside it is not proof of bad
  intent, because lists lag (two changed in four days) and some agents publish nothing.
- "Claimed, not declared" can mean the operator's own list is incomplete. The draft says so and asks the
  operator to add the address.
- Web Bot Auth signatures are recorded, not verified.
- Only Apple and Anthropic publish a bot contact address we could verify. Everyone else is reached through the
  network's abuse desk, which for rented cloud space is the cloud provider.
- The test page only sees agents that someone sends to it. On a real site, point `watch` at the server's access
  log (common or combined format, as Apache and nginx write by default).
- The watcher posts the full address of requests that name an AI agent or come from a published range. Use
  `--redact` to show undeclared addresses as their /24 or /48 in the room.

## Credits and disclosure

- The list data and the range-file parser come from my earlier project, the
  [Registry Scorecard](https://github.com/sidthebuilder/declared-egress) (MIT). The Switch room, the agents, the
  watcher, the classifier, the report drafts, the test page and the tests were written for this hackathon.
- I used Kiro (Claude) as a coding assistant. I chose the workflow and the data sources, and checked the
  results.
- Licence: MIT. The published lists belong to their operators; registry data comes from the RIRs' public RDAP
  services.
