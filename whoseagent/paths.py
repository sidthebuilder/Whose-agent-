"""Where the package keeps its data, caches, logs and drafts. Every path can be moved with an environment
variable, so an agent working in its own directory can point at a shared log or outbox."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("WHOSEAGENT_DATA", ROOT / "data"))
CACHE = Path(os.environ.get("WHOSEAGENT_CACHE", ROOT / "cache"))
LOGS = Path(os.environ.get("WHOSEAGENT_LOGS", ROOT / "logs"))
OUTBOX = Path(os.environ.get("WHOSEAGENT_OUTBOX", ROOT / "outbox"))
