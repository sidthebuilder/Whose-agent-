#!/usr/bin/env python3
"""Run whoseagent from any folder: `python path/to/wa.py <command>`. Python 3.11+, standard library only.
The agents in the Switch room run it from their own folders as `python ../../wa.py <command>`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from whoseagent.cli import main  # noqa: E402

main()
