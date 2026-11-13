#!/usr/bin/env python3
"""Check each upcoming race's official pages for ticket changes.

Fetches the URLs in sources.json, keeps only ticket-related lines (prices, sale
dates, "sold out", ...) and compares a fingerprint with state/page-hashes.json.
Pages that changed are saved to .ticket-watch/pages/ and listed in
.ticket-watch/changes.md for Claude. A URL seen for the first time only records
a baseline. Uses only the Python standard library.

    python3 scripts/watch.py [--max-races 6]
"""

import argparse
import datetime as dt
import hashlib
import html.parser
import json
import os
import pathlib
import re
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "page-hashes.json"
REPORT = ROOT / "state" / "check-report.json"
WORK = ROOT / ".ticket-watch"
USER_AGENT = "Mozilla/5.0 (compatible; GPPassportTicketWatch/1.0; +https://rathkalyforgna.github.io/gppassport-data/)"
# Lines worth watching: ticket words, money and dates. Everything else is page chrome.
RELEVANT = re.compile(
    r"ticket|sale|presale|pre-sale|sold.?out|early.?bird|waitlist|wait list|grandstand|general admission|"
    r"hospitality|paddock|price|discount|offer|release|phase|€|£|\$|¥|%|"
    r"\b(AED|QAR|SAR|BHD|MXN|BRL|SGD|AUD|CAD|JPY|CNY|HUF|EUR|GBP|USD|AZN)\b|"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)
MAX_PAGE_CHARS = 60_000


class TextExtractor(html.parser.HTMLParser):
    """Collects visible text, one line per block, skipping scripts and styles."""

    SKIP = {"script", "style", "noscript", "svg", "template"}
    BLOCKS = {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "article", "span", "a", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)

    def lines(self):
        text = "".join(self.parts)
        lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
        return [line for line in lines if line]


def fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=25) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read(3_000_000).decode(charset, errors="replace")
    extractor = TextExtractor()
    extractor.feed(body)
    return extractor.lines()


# Per-visit noise that would otherwise look like a change: session/CSRF tokens,
# UUIDs and other long random strings, and leaked script/markup lines.
NOISE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{16,}\b|\b[A-Za-z0-9_\-+/=]{32,}\b",
    re.IGNORECASE,
)
CODE_LINE = re.compile(r"^\s*<|window\.|function\s*\(|=>|\{\s*\"|;\s*$")


def fingerprint(lines):
    relevant = [
        NOISE.sub("#", line)[:300]
        for line in lines
        if RELEVANT.search(line) and not CODE_LINE.search(line)
    ]
    return hashlib.sha256("\n".join(relevant).encode()).hexdigest(), len(relevant)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-races", type=int, default=6, help="max races sent to Claude per run")
    args = parser.parse_args()

    today = dt.date.today().isoformat()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    tickets = json.loads((ROOT / "tickets.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    hashes = load(STATE, {})
    report = load(REPORT, {})

    # Soonest races first, so a busy run handles the most urgent ones.
    events = sorted(
        (e for e in tickets["events"] if e["raceEnd"] >= today and not e.get("soldOut")),
        key=lambda e: e["raceStart"],
    )
    changed = []
    for event in events:
        event_id = f"{event['season']}-{event['circuitId']}"
        urls = sources.get(event_id, [])
        statuses = {}
        event_changed = []
        for url in urls:
            try:
                lines = fetch_text(url)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                reason = getattr(error, "code", None) or getattr(error, "reason", None) or error
                statuses[url] = f"error: {reason}"
                continue
            digest, relevant_count = fingerprint(lines)
            if relevant_count == 0:
                # Likely a JavaScript-built page: nothing readable to compare.
                statuses[url] = "no readable ticket text"
                continue
            statuses[url] = "ok"
            previous = hashes.get(url, {}).get("hash")
            if previous is None:
                hashes[url] = {"hash": digest, "since": now}
            elif previous != digest:
                event_changed.append((url, digest, lines))

        if event_changed and len(changed) < args.max_races:
            changed.append((event, event_changed))
            for url, digest, _ in event_changed:
                hashes[url] = {"hash": digest, "since": now}
        # (Races over the per-run cap keep their old fingerprint and are picked up next run.)
        report[event_id] = {
            # Date only, so the report changes (and is committed) at most once a day.
            "checked": today,
            "ok": sum(1 for s in statuses.values() if s == "ok"),
            "total": len(urls),
            "problems": {url: s for url, s in statuses.items() if s != "ok"},
        }

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    if changed:
        pages = WORK / "pages"
        pages.mkdir(parents=True, exist_ok=True)
        listing = [f"# Changed official pages ({today})", ""]
        for event, items in changed:
            listing.append(f"## {event['grandPrixName']} — season {event['season']}, circuitId `{event['circuitId']}`")
            for index, (url, _, lines) in enumerate(items):
                file = pages / f"{event['season']}-{event['circuitId']}-{index}.txt"
                file.write_text(f"URL: {url}\nFetched: {now}\n\n" + "\n".join(lines)[:MAX_PAGE_CHARS], encoding="utf-8")
                listing.append(f"- {url} → `{file.relative_to(ROOT)}`")
            listing.append("")
        (WORK / "changes.md").write_text("\n".join(listing), encoding="utf-8")

    unreadable = sorted(k for k, v in report.items() if v.get("total") and v.get("ok") == 0)
    print(f"Checked {len(events)} races; {len(changed)} with ticket changes.")
    for event, items in changed:
        print(f"  changed: {event['season']} {event['circuitId']} ({len(items)} page(s))")
    if unreadable:
        print(f"  couldn't read any source for: {', '.join(unreadable)}")

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")
            handle.write(f"today={today}\n")


if __name__ == "__main__":
    main()
