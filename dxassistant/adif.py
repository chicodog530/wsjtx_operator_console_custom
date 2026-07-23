from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

TAG = re.compile(r"<([^:>]+)(?::(\d+)(?::[^>]+)?)?>", re.IGNORECASE)


def parse_adif(text: str) -> Iterable[dict[str, str]]:
    record: dict[str, str] = {}
    pos = 0

    while True:
        match = TAG.search(text, pos)
        if not match:
            break

        name = match.group(1).upper()
        length = int(match.group(2) or 0)
        value_start = match.end()

        if name == "EOH":
            record.clear()
            pos = value_start
            continue

        if name == "EOR":
            if record:
                yield record
            record = {}
            pos = value_start
            continue

        value = text[value_start : value_start + length] if length else ""
        record[name] = value.strip()
        pos = value_start + length

    if record:
        yield record


def read_adif(path: Path) -> list[dict[str, str]]:
    return list(parse_adif(path.read_text(encoding="utf-8", errors="ignore")))
