"""Copy the real 31 October 2026 snapshot from the Registry Scorecard into data/.

  python scripts/import_scorecard.py "path/to/declared-egress"

Writes data/lists.csv (one row per published list, with the date in the file and the fetch time),
data/prefixes.csv (one row per declared prefix, with the registry holder found for it), data/sources.csv
(the 21 list URLs) and data/snapshot-2026-10-31/ (the raw files and their manifest with SHA-256 hashes).
Nothing is edited: rows are copied, and exact duplicate prefixes within one list are dropped.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "../middle power/scorecard").resolve()
    snap = src / "snapshots" / "2026-10-31"
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    fetched = {(m["operator"], m["product"]): m["fetched_at"] for m in manifest}
    DATA.mkdir(exist_ok=True)

    with open(src / "results" / "scorecard.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(DATA / "lists.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["operator", "list", "purpose", "url", "date_in_file", "fetched_at", "R1_current",
                    "R2_separate", "R3_dedicated", "R4_signed", "passes"])
        for r in rows:
            w.writerow([r["operator"], r["list"], r["purpose"], r["url"], r["date_in_file"],
                        fetched.get((r["operator"], r["list"]), ""), r["R1_current"], r["R2_separate"],
                        r["R3_dedicated"], r["R4_signed"], r["passes"]])

    seen, kept = set(), 0
    with open(src / "results" / "prefixes.csv", newline="", encoding="utf-8") as f, \
            open(DATA / "prefixes.csv", "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g)
        w.writerow(["operator", "list", "prefix", "holder", "rdap_registrant", "rdap_network"])
        for p in csv.DictReader(f):
            key = (p["operator"], p["list"], p["prefix"])
            if key in seen:
                continue
            seen.add(key)
            kept += 1
            w.writerow([p["operator"], p["list"], p["prefix"], p["holder"], p["rdap_registrant"], p["rdap_network"]])

    shutil.copy(src / "sources.csv", DATA / "sources.csv")
    dest = DATA / "snapshot-2026-10-31"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(snap, dest)
    print(f"{len(rows)} lists, {kept} prefixes, {len(manifest)} raw files copied from {src}")


if __name__ == "__main__":
    main()
