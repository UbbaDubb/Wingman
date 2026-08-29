"""
Append-only JSONL cycle log, one file per calendar day.

JSONL (one JSON object per line) so a crash mid-run can at worst lose the
line being written — every previously logged cycle stays valid, and the file
is greppable / pandas-readable without any parsing ceremony.
"""

import json
from datetime import date
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def log_cycle(record: dict) -> None:
    """
    Serialize `record` as one line of logs/<YYYY-MM-DD>.jsonl (file per day,
    created on first write). The record is appended as-is — structuring it is
    the caller's job; this function must stay dumb so it can never be the
    stage that breaks a cycle.
    """
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"{date.today().isoformat()}.jsonl"
    # default=str catches stray datetimes/Decimals so logging never raises.
    line = json.dumps(record, default=str)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
