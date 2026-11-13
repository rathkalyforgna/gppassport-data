#!/usr/bin/env python3
"""Set tickets.json's "updated" to today if its content changed but the date didn't.

    python3 scripts/bump_updated.py BEFORE.json tickets.json YYYY-MM-DD

Edits only the "updated" line, so the file's hand-friendly formatting is kept.
"""

import json
import re
import sys


def main():
    before_path, path, today = sys.argv[1:4]
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    current = json.loads(text)

    strip = lambda data: {k: v for k, v in data.items() if k != "updated"}
    if strip(current) == strip(before) or current.get("updated", "") > before.get("updated", ""):
        return
    text, count = re.subn(r'("updated"\s*:\s*)"\d{4}-\d{2}-\d{2}"', rf'\g<1>"{today}"', text, count=1)
    if count:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"Bumped 'updated' to {today}")


if __name__ == "__main__":
    main()
