# tickets.json format

The GP Passport app downloads `tickets.json` from GitHub Pages. Keep it valid JSON —
one stray comma breaks it for everyone (the publish step blocks invalid files).

## Top level

| Field | Type | Notes |
| --- | --- | --- |
| `version` | number | Format version (currently 4). |
| `updated` | `"2026-11-13T15:04:05Z"` | When the file last changed, as a **UTC** timestamp (the `Z`). The robot sets it automatically. **When you edit by hand, update it too** — the current UTC time, or at least today's date (`"2026-11-13"`). The app ignores files older than its own copy. |
| `events` | array | One entry per Grand Prix per season. |
| `guides` | array | "Which ticket?" picks per circuit (shared across seasons). |

## Event

| Field | Type | Notes |
| --- | --- | --- |
| `season`, `round` | number | Identify the race. **Don't change** without checking the official calendar. |
| `circuitId` | string | e.g. `"great-britain"`. `season` + `circuitId` is unique. |
| `grandPrixName`, `locality`, `countryFlag` | string | Display info. |
| `raceStart`, `raceEnd` | `"YYYY-MM-DD"` | First day of the weekend, race day. |
| `ticketURL` | URL | Official ticket page (the app's "Buy on Official Site" button). |
| `soldOut` | bool | Whole event sold out. |
| `priceFrom` | `{"amount", "currency"}` or `null` | Headline "from" price; `null` falls back to the cheapest available price in `prices`. |
| `windows` | array | Sale phases — see below. |
| `pricesAsOf` | `"YYYY-MM-DD"` | When `prices` were last checked. |
| `pricesNote` | string | Short caveat, e.g. dynamic pricing. |
| `prices` | array | `{"ticket", "type", "amount", "currency", "days", "soldOut"?}` — adult face value, local currency. |
| `note` | string or `null` | "Good to know" text shown to fans. |
| `sources` | array of URLs | Where the information came from (not shown in the app). |

### Sale windows (`windows`)

`{"kind", "title", "start", "end", "soldOut"?}` — `start`/`end` are `"YYYY-MM-DD"` or `null`
(`start: null` = already open, `end: null` = open-ended; `end` is the last day, inclusive).

| `kind` | Status shown while open | Use for |
| --- | --- | --- |
| `generalSale` | ON SALE (OPENS 13 NOV before `start`) | The public sale |
| `presale` | PRESALE | Members / card-holder presales |
| `earlyBird`, `discount` | ON SALE + listed under Deals | Time-limited offers — set `end` |
| `waitlist` | WAITLIST | Sign-up only, no sale date yet (dates ignored) |

Ticket tiers (`type` in prices and picks): `generalAdmission`, `grandstand`, `hospitality`, `paddockClub`.

## Rules

- Only publish what an **official** source states (circuit/promoter site, F1's ticket store,
  official press releases). Never guess dates or prices; never use resale prices.
- Check your edit locally with `python3 scripts/validate.py tickets.json`.
