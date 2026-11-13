#!/usr/bin/env python3
"""Stamp tickets.json's "updated" with the current UTC time if its content changed.

    python3 scripts/bump_updated.py BEFORE.json tickets.json

Writes an ISO 8601 timestamp such as "2026-11-13T15:04:05Z", so updates made on
the same day can be told apart. Edits only the "updated" line, so the file's
hand-friendly formatting is kept.
"""

import datetime as dt
import json
import re
import sys


def main():
    before_path, path = sys.argv[1:3]
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    current = json.loads(text)

    strip = lambda data: {k: v for k, v in data.items() if k != "updated"}
    if strip(current) == strip(before):
        return
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text, count = re.subn(r'("updated"\s*:\s*)"[^"]*"', rf'\g<1>"{now}"', text, count=1)
    if count:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"Stamped 'updated' as {now}")


if __name__ == "__main__":
    main()
