#!/usr/bin/env python3
"""Check upcoming races' official pages for ticket changes.

Fetches the URLs in sources.json, keeps only ticket-related lines (prices, sale
dates, "sold out", ...) and compares a fingerprint with state/page-hashes.json.
Pages that changed are saved to .ticket-watch/pages/ and listed in
.ticket-watch/changes.md for Claude. A URL seen for the first time only records
a baseline. Uses only the Python standard library.

Checking speed adjusts to each race: "hot" races (a sale or deal starting or
ending within 7 days, a presale running, the race within 30 days, or no sale
date yet during on-sale season) are checked every run (hourly); quiet races
every 6 hours, spread across the day. Sold-out and finished races are skipped.

It also follows each site's robots.txt and backs off from a site for a while
when it refuses access (HTTP 403/429/503), instead of retrying every hour.

    python3 scripts/watch.py [--all] [--max-races 6]
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
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "page-hashes.json"
REPORT = ROOT / "state" / "check-report.json"
BACKOFF = ROOT / "state" / "backoff.json"
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
# The token sites' robots.txt rules are matched against.
ROBOTS_AGENT = "GPPassportTicketWatch"
# Quiet races are checked once every this many hours.
QUIET_EVERY_HOURS = 6
HOT_WINDOW_DAYS = 7
HOT_RACE_DAYS = 30
ON_SALE_SEASON_MONTHS = {9, 10, 11, 12}
# How long to leave a site alone after it refuses access.
BACKOFF_HOURS = 24
REFUSED = {403, 429, 503}


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


# MARK: - Checking speed

def hot_reason(event, today):
    """Why a race should be checked every hour right now, or None if it's quiet."""
    day = dt.date.fromisoformat(today)
    soon = (day + dt.timedelta(days=HOT_WINDOW_DAYS)).isoformat()
    for window in event.get("windows", []):
        if window.get("kind") == "waitlist" or window.get("soldOut"):
            continue
        start, end = window.get("start"), window.get("end")
        if start and today <= start <= soon:
            return f"{window['title']} starts {start}"
        if end and today <= end <= soon:
            return f"{window['title']} ends {end}"
        if window.get("kind") == "presale" and (not start or start <= today) and (not end or end >= today):
            return "presale running"
    if event["raceStart"] <= (day + dt.timedelta(days=HOT_RACE_DAYS)).isoformat():
        return "race within 30 days"
    sale_known = any(
        w.get("kind") in {"generalSale", "earlyBird", "discount"} and not (w.get("end") and w["end"] < today)
        for w in event.get("windows", [])
    )
    if not sale_known and day.month in ON_SALE_SEASON_MONTHS:
        return "no sale date yet (on-sale season)"
    return None


def quiet_turn(event_id, hour):
    """Spreads quiet races evenly over the cycle: each gets a fixed hour slot."""
    return zlib.crc32(event_id.encode()) % QUIET_EVERY_HOURS == hour % QUIET_EVERY_HOURS


# MARK: - Politeness: robots.txt and back-off

class Politeness:
    def __init__(self, backoff, now):
        self.backoff = backoff
        self.now = now
        self.robots = {}

    def blocked_until(self, url):
        until = self.backoff.get(urllib.parse.urlparse(url).hostname or "")
        if until and dt.datetime.fromisoformat(until) > self.now:
            return until
        return None

    def allowed_by_robots(self, url):
        """True if robots.txt lets us fetch the URL (no robots.txt = allowed)."""
        parts = urllib.parse.urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self.robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                request = urllib.request.Request(f"{root}/robots.txt", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=15) as response:
                    parser.parse(response.read(500_000).decode("utf-8", errors="replace").splitlines())
            except urllib.error.HTTPError as error:
                # Like the major search engines: a missing/forbidden robots.txt (4xx) means no rules;
                # a server error means "try later".
                parser = None if error.code >= 500 else "allow"
            except (urllib.error.URLError, TimeoutError, OSError):
                parser = None
            self.robots[root] = parser
        parser = self.robots[root]
        if parser is None:
            return None  # robots.txt unreachable — skip the site this run
        if parser == "allow":
            return True
        return parser.can_fetch(ROBOTS_AGENT, url)

    def refused(self, url, error):
        """Back off from a site that refused access, honouring Retry-After when given."""
        hours = BACKOFF_HOURS
        retry_after = error.headers.get("Retry-After") if getattr(error, "headers", None) else None
        if retry_after and retry_after.isdigit():
            hours = min(max(int(retry_after) / 3600, 1), BACKOFF_HOURS)
        until = (self.now + dt.timedelta(hours=hours)).replace(microsecond=0)
        host = urllib.parse.urlparse(url).hostname or ""
        self.backoff[host] = until.isoformat()
        return until.strftime("%Y-%m-%d %H:%MZ")

    def tidy(self):
        """Drops back-offs that have expired."""
        for host, until in list(self.backoff.items()):
            if dt.datetime.fromisoformat(until) <= self.now:
                del self.backoff[host]


# MARK: - Main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-races", type=int, default=6, help="max races sent to Claude per run")
    parser.add_argument("--all", action="store_true", help="check every race now (manual runs)")
    args = parser.parse_args()

    clock = dt.datetime.now(dt.timezone.utc)
    today = clock.date().isoformat()
    now = clock.strftime("%Y-%m-%dT%H:%MZ")
    tickets = json.loads((ROOT / "tickets.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    hashes = load(STATE, {})
    report = load(REPORT, {})
    polite = Politeness(load(BACKOFF, {}), clock)

    # Soonest races first, so a busy run handles the most urgent ones.
    upcoming = sorted(
        (e for e in tickets["events"] if e["raceEnd"] >= today and not e.get("soldOut")),
        key=lambda e: e["raceStart"],
    )
    plan = []
    for event in upcoming:
        event_id = f"{event['season']}-{event['circuitId']}"
        reason = hot_reason(event, today)
        if args.all or reason or quiet_turn(event_id, clock.hour):
            plan.append((event, event_id, reason or ("manual run" if args.all else "quiet — 6-hourly turn")))

    changed = []
    for event, event_id, reason in plan:
        urls = sources.get(event_id, [])
        statuses = {}
        event_changed = []
        for url in urls:
            until = polite.blocked_until(url)
            if until:
                statuses[url] = f"backing off until {until[:16]}Z"
                continue
            robots = polite.allowed_by_robots(url)
            if robots is None:
                statuses[url] = "skipped: robots.txt unreachable"
                continue
            if not robots:
                statuses[url] = "skipped: disallowed by robots.txt"
                continue
            try:
                lines = fetch_text(url)
            except urllib.error.HTTPError as error:
                if error.code in REFUSED:
                    statuses[url] = f"error: {error.code} — backing off until {polite.refused(url, error)}"
                else:
                    statuses[url] = f"error: {error.code}"
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                statuses[url] = f"error: {getattr(error, 'reason', None) or error}"
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
            "speed": "hourly" if hot_reason(event, today) else "6-hourly",
            "ok": sum(1 for s in statuses.values() if s == "ok"),
            "total": len(urls),
            "problems": {url: s for url, s in statuses.items() if s != "ok"},
        }

    polite.tidy()
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    BACKOFF.write_text(json.dumps(polite.backoff, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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

    hot = sum(1 for _, _, r in plan if not r.startswith(("quiet", "manual")))
    print(f"Checked {len(plan)} of {len(upcoming)} upcoming races ({hot} hot); {len(changed)} with ticket changes.")
    for event, event_id, reason in plan:
        print(f"  {event_id}: {reason}")
    for event, items in changed:
        print(f"  changed: {event['season']} {event['circuitId']} ({len(items)} page(s))")
    unreadable = sorted(k for k, v in report.items() if v.get("total") and v.get("ok") == 0)
    if unreadable:
        print(f"  couldn't read any source for: {', '.join(unreadable)}")

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")
            handle.write(f"today={today}\n")


if __name__ == "__main__":
    main()
