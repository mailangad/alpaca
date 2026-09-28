"""Replays ES trades from a CSV file (ts_event,price,size).

`ts_event` is either nanoseconds since the epoch (Databento's native format)
or an ISO-8601 timestamp. scripts/download_es_ticks.py writes this format.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from ..models import Tick


def _parse_ts(value: str) -> datetime:
    if value.isdigit():
        return datetime.fromtimestamp(int(value) / 1e9, tz=timezone.utc)
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def read_ticks(path: str | Path) -> Iterator[Tick]:
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            yield Tick(_parse_ts(row["ts_event"]), float(row["price"]), int(row["size"]))
