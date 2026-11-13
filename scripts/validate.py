#!/usr/bin/env python3
"""Validate tickets.json and (optionally) classify the risk of a change.

    python3 scripts/validate.py tickets.json
    python3 scripts/validate.py tickets.json --base old.json [--report out.md] [--github-output]

Exit code 1 if the file is invalid. With --base, prints the list of changes and a
risk level: "none", "low" (safe to publish automatically) or "high" (needs review).
Uses only the Python standard library.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
KINDS = {"generalSale", "presale", "earlyBird", "discount", "waitlist"}
TIERS = {"generalAdmission", "grandstand", "hospitality", "paddockClub"}
GOALS = {"budget", "firstTimer", "bestView", "atmosphere", "family", "premium"}
EVENT_REQUIRED = {
    "season": int, "circuitId": str, "grandPrixName": str, "locality": str,
    "countryFlag": str, "round": int, "raceStart": str, "raceEnd": str,
    "ticketURL": str, "soldOut": bool, "windows": list,
}
# A price move bigger than this (either way) needs a human look.
PRICE_CHANGE_LIMIT = 0.30


def is_date(value):
    if not isinstance(value, str) or not DATE.match(value):
        return False
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def is_timestamp(value):
    """A UTC ISO 8601 timestamp such as 2026-11-13T15:04:05Z."""
    if not isinstance(value, str) or not TIMESTAMP.match(value):
        return False
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate(data):
    """Returns a list of error strings (empty when valid)."""
    errors = []
    if not isinstance(data, dict):
        return ["top level must be an object"]
    if not isinstance(data.get("version"), int):
        errors.append("'version' must be a number")
    if not (is_timestamp(data.get("updated")) or is_date(data.get("updated"))):
        errors.append("'updated' must be a UTC timestamp (2026-11-13T15:04:05Z) or a YYYY-MM-DD date")
    events = data.get("events")
    if not isinstance(events, list) or not events:
        return errors + ["'events' must be a non-empty list"]

    seen = set()
    for index, event in enumerate(events):
        where = f"events[{index}]"
        if not isinstance(event, dict):
            errors.append(f"{where} must be an object")
            continue
        where = f"{event.get('season')} {event.get('circuitId', where)}"
        for field, kind in EVENT_REQUIRED.items():
            if not isinstance(event.get(field), kind) or (kind is int and isinstance(event.get(field), bool)):
                errors.append(f"{where}: '{field}' missing or wrong type")
        key = (event.get("season"), event.get("circuitId"))
        if key in seen:
            errors.append(f"{where}: duplicate season + circuitId")
        seen.add(key)
        for field in ("raceStart", "raceEnd", "pricesAsOf"):
            if field in event and event[field] is not None and not is_date(event[field]):
                errors.append(f"{where}: '{field}' must be YYYY-MM-DD")
        if is_date(event.get("raceStart")) and is_date(event.get("raceEnd")) and event["raceStart"] > event["raceEnd"]:
            errors.append(f"{where}: raceStart is after raceEnd")
        if not str(event.get("ticketURL", "")).startswith("https://"):
            errors.append(f"{where}: ticketURL must be an https:// link")
        price_from = event.get("priceFrom")
        if price_from is not None and not valid_money(price_from):
            errors.append(f"{where}: priceFrom must be null or {{amount, currency}}")
        for w_index, window in enumerate(event.get("windows") or []):
            w_where = f"{where} windows[{w_index}]"
            if not isinstance(window, dict):
                errors.append(f"{w_where} must be an object")
                continue
            if window.get("kind") not in KINDS:
                errors.append(f"{w_where}: kind must be one of {sorted(KINDS)}")
            if not isinstance(window.get("title"), str) or not window["title"].strip():
                errors.append(f"{w_where}: title is required")
            for field in ("start", "end"):
                if window.get(field) is not None and not is_date(window[field]):
                    errors.append(f"{w_where}: '{field}' must be null or YYYY-MM-DD")
            if is_date(window.get("start")) and is_date(window.get("end")) and window["start"] > window["end"]:
                errors.append(f"{w_where}: start is after end")
            if "soldOut" in window and not isinstance(window["soldOut"], bool):
                errors.append(f"{w_where}: soldOut must be true or false")
        prices = event.get("prices")
        if prices is not None:
            if not isinstance(prices, list):
                errors.append(f"{where}: prices must be a list")
            else:
                for p_index, price in enumerate(prices):
                    p_where = f"{where} prices[{p_index}]"
                    if not isinstance(price, dict) or not valid_money(price):
                        errors.append(f"{p_where}: needs amount (> 0) and a 3-letter currency")
                        continue
                    if not isinstance(price.get("ticket"), str) or not price["ticket"].strip():
                        errors.append(f"{p_where}: ticket name is required")
                    if price.get("type") not in TIERS:
                        errors.append(f"{p_where}: type must be one of {sorted(TIERS)}")
        for field in ("note", "pricesNote"):
            if event.get(field) is not None and not isinstance(event[field], str):
                errors.append(f"{where}: '{field}' must be text or null")

    for g_index, guide in enumerate(data.get("guides") or []):
        g_where = f"guides[{g_index}]"
        if not isinstance(guide, dict) or not isinstance(guide.get("circuitId"), str):
            errors.append(f"{g_where}: needs a circuitId")
            continue
        for pick in guide.get("picks") or []:
            if pick.get("goal") not in GOALS or pick.get("type") not in TIERS:
                errors.append(f"{g_where} ({guide['circuitId']}): pick has an invalid goal or type")
    return errors


def valid_money(value):
    return (isinstance(value, dict)
            and isinstance(value.get("amount"), (int, float)) and not isinstance(value.get("amount"), bool)
            and value["amount"] > 0
            and isinstance(value.get("currency"), str) and re.fullmatch(r"[A-Z]{3}", value["currency"]) is not None)


# MARK: - Change classification

def event_key(event):
    return f"{event['season']} {event['circuitId']}"


def price_moved(old, new):
    return old > 0 and abs(new - old) / old > PRICE_CHANGE_LIMIT


def classify(base, new):
    """Returns (risk, changes) where changes is a list of (risk, description)."""
    changes = []
    old_events = {event_key(e): e for e in base["events"]}
    new_events = {event_key(e): e for e in new["events"]}

    for key in sorted(old_events.keys() - new_events.keys()):
        changes.append(("high", f"{key}: race removed"))
    for key in sorted(new_events.keys() - old_events.keys()):
        changes.append(("high", f"{key}: race added"))

    for key in sorted(old_events.keys() & new_events.keys()):
        old, cur = old_events[key], new_events[key]
        name = cur.get("grandPrixName", key)
        label = f"{name} {cur['season']}"
        for field in ("round", "raceStart", "raceEnd", "grandPrixName", "locality", "ticketURL"):
            if old.get(field) != cur.get(field):
                changes.append(("high", f"{label}: {field} changed {old.get(field)!r} → {cur.get(field)!r}"))
        if old.get("soldOut") != cur.get("soldOut"):
            changes.append(("low", f"{label}: sold out → {cur.get('soldOut')}"))

        old_windows = {json.dumps(w, sort_keys=True) for w in old.get("windows", [])}
        new_windows = {json.dumps(w, sort_keys=True) for w in cur.get("windows", [])}
        for raw in sorted(new_windows - old_windows):
            w = json.loads(raw)
            changes.append(("low", f"{label}: sale window {w['kind']} “{w['title']}” {w.get('start') or 'open'} → {w.get('end') or 'open-ended'}"))
        for raw in sorted(old_windows - new_windows):
            w = json.loads(raw)
            # Replacing a waitlist once a sale is announced is routine; dropping a real sale phase isn't.
            risk = "low" if w["kind"] == "waitlist" else "high"
            changes.append((risk, f"{label}: removed window {w['kind']} “{w['title']}”"))

        old_from, new_from = old.get("priceFrom"), cur.get("priceFrom")
        if old_from != new_from:
            risk = "low"
            if old_from and new_from and old_from["currency"] == new_from["currency"] and price_moved(old_from["amount"], new_from["amount"]):
                risk = "high"
            if old_from and new_from and old_from["currency"] != new_from["currency"]:
                risk = "high"
            changes.append((risk, f"{label}: from-price {fmt(old_from)} → {fmt(new_from)}"))

        old_prices = {p["ticket"]: p for p in old.get("prices") or []}
        new_prices = {p["ticket"]: p for p in cur.get("prices") or []}
        for ticket in sorted(new_prices.keys() - old_prices.keys()):
            changes.append(("low", f"{label}: new price {ticket} {fmt(new_prices[ticket])}"))
        for ticket in sorted(old_prices.keys() - new_prices.keys()):
            changes.append(("high", f"{label}: price removed for {ticket}"))
        for ticket in sorted(old_prices.keys() & new_prices.keys()):
            o, n = old_prices[ticket], new_prices[ticket]
            if o.get("soldOut", False) != n.get("soldOut", False):
                changes.append(("low", f"{label}: {ticket} sold out → {n.get('soldOut', False)}"))
            if (o["amount"], o["currency"]) != (n["amount"], n["currency"]):
                risky = o["currency"] != n["currency"] or price_moved(o["amount"], n["amount"])
                changes.append(("high" if risky else "low", f"{label}: {ticket} {fmt(o)} → {fmt(n)}"))
        for field in ("note", "pricesNote", "pricesAsOf", "sources"):
            if old.get(field) != cur.get(field):
                changes.append(("low", f"{label}: {field} updated"))

    if base.get("guides") != new.get("guides"):
        changes.append(("high", "Which Ticket? guides changed"))
    if base.get("version") != new.get("version"):
        changes.append(("high", f"version {base.get('version')} → {new.get('version')}"))

    if not changes:
        return "none", []
    return ("high" if any(r == "high" for r, _ in changes) else "low"), changes


def fmt(money):
    return "none" if not money else f"{money['amount']:g} {money['currency']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file")
    parser.add_argument("--base", help="previous version, to classify the change")
    parser.add_argument("--report", help="write a markdown change report here")
    parser.add_argument("--github-output", action="store_true", help="write risk=... to $GITHUB_OUTPUT")
    args = parser.parse_args()

    try:
        with open(args.file, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as error:
        print(f"❌ {args.file} is not valid JSON: {error}")
        return 1

    errors = validate(data)
    if errors:
        print(f"❌ {args.file} has {len(errors)} problem(s):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"✅ {args.file} is valid ({len(data['events'])} races, updated {data['updated']})")

    if args.base:
        with open(args.base, encoding="utf-8") as handle:
            base = json.load(handle)
        risk, changes = classify(base, data)
        if changes and data.get("updated", "") <= base.get("updated", ""):
            changes.append(("low", f"note: 'updated' is still {data.get('updated')} — bump it"))
        print(f"Risk: {risk}")
        for level, text in changes:
            print(f"  [{level}] {text}")
        if args.report:
            with open(args.report, "w", encoding="utf-8") as handle:
                handle.write(f"**Risk: {risk}**\n\n")
                handle.writelines(f"- {'🔍' if level == 'high' else '✅'} {text}\n" for level, text in changes)
        if args.github_output and os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
                handle.write(f"risk={risk}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
