# cal-seat-sniper

A tiny, dependency-free watcher for UC Berkeley class seats. It reads a section's
**real-time** enrollment numbers from a public, *uncached* endpoint on
`classes.berkeley.edu` and **notifies you the instant a seat becomes snipeable** —
your cue to fire a pre-staged enroll in CalCentral during the "window" between
someone dropping and the next waitlist batch run. Detection latency ≈ your poll
interval (default 1 min), not the site's ~15-minute page cache.

This does, locally and for free, the core of what a paid service like
`waitlistwarrior.net` does. See `berkeley-waitlist-window.md` for the full
mechanism (why the window exists, §1.1 and §4) and the exact data source (§2.4).

## How it works

The watcher combines **three public data sources** (all three verified against the live site):

1. **Real-time change detector — the associated-sections fragment.** Every
   section page loads its "Associated Sections" table from
   `/sections/associated/<node_id>`, and that fragment is served **uncached**
   (`cache-control: no-cache`, `x-drupal-cache: UNCACHEABLE`, a Fastly `MISS`
   with `age: 0` on every hit). Each row carries live **Open Seats, Enrolled,
   Enrollment Limit, Waitlisted, Waitlist limit** for one section of the
   course. One gotcha: the fragment lists every section of the course *except*
   the node you asked for — so the watcher reads your section through a
   sibling **"probe"** section's fragment, discovered automatically on the
   first poll (and shared, so several watched sections of one course cost a
   single request per poll).

2. **Detail source — the section content page.** Its HTML embeds a JSON blob at
   `drupalSettings.ucb.enrollment.available.enrollmentStatus` with
   `status.code` (**`O` = Open**, `W` = Waitlist, `C` = Closed),
   `enrolledCount`, `maxEnroll` (capacity), `waitlistedCount`, `maxWaitlist`,
   `reservedCount`, `openReserved`, and the per-group `seatReservations[]`
   breakdown the fragment lacks. This page sits behind ~15-minute caches, so
   it's polled only for the `O`/`W`/`C` status and requirement-group codes on a
   slow refresh (`content_refresh_seconds`, default 450).

3. **Real-time reserved breakdown — the uncached ajax variant.** The same
   content rendered via `…/content/<slug>?_wrapper_format=drupal_ajax` is served
   **uncached** (`x-drupal-dynamic-cache: UNCACHEABLE`, `age: 0` every hit). On a
   detected change the watcher reads the live per-group open-reserved counts from
   it, so *"a seat reserved for **my** major just opened"* is detected in real
   time, not gated by the ~15-minute page cache. (It carries group descriptions
   but no codes, so codes are enriched from tier 2.)

**Open seats = `capacity − enrolled`.** The live open/waitlist counts come
from the fragment; on any change the reserved breakdown is refreshed live from
the ajax variant. Only the `O`/`W`/`C` status label and the requirement-group codes ride the
slow page refresh.

Alerts are based on which triggers you list in `"alert_on"`:

Each trigger watches one count: **`eligible`** (the default) plus **`*`**,
**`unreserved`**, **`reserved`** fire when that category of open seat goes up,
**`waitlist`** when a waitlist spot opens, **`capacity`** when the course's max size
grows, and **`status`** when the section's status flips to Open.

| Trigger | Fires when | Default? |
|---|---|---|
| **`eligible`** | The number of open seats **you personally** can snipe goes up: unreserved **plus** reserved for a group listed in your `"reserved_groups"` (your major, minor, program…). With no groups configured it simply counts unreserved. Like the other open-count triggers, it fires *even while the status still shows Waitlist* (the drop-to-batch window). See *Reserved seats & `reserved_groups`* below. | ✅ **on** |
| **`*`** | The **total** number of open seats goes up (`capacity − enrolled` increases) — a spot just freed, whether or not it's reserved. Use this to see *everything*, e.g. if you might have an enrollment permission coming. | off |
| **`unreserved`** | Only the **unreserved** open count goes up (`capacity − enrolled − open_reserved`). Same as the default when you have no `"reserved_groups"`. | off |
| **`reserved`** | Only the **reserved** open count goes up — a seat opened that's held for some group. Snipeable only if that group is one of yours; if it is, use `eligible` instead, which already folds your groups in. | off |
| **`waitlist`** | A **spot on the waitlist opens** — the waitlist was full and now has room to get in line. (The waitlist limit is read live from the same uncached fragment as the seat counts.) This is *not* the same as the line merely advancing. | off |
| **`capacity`** | The section's **max capacity** goes up — the department *expanded* the course (more total seats), independent of whether any are open yet. | off |
| **`status`** | The section's status flips to **Open**. Informational: the alert says whether it's snipeable or "Open but all seats reserved." (The `O`/`W`/`C` status label — which, along with the requirement-group codes, is one of the only values riding the cached page — can lag a pure status flip by the page-refresh cadence in fast mode; every open-count trigger above is real-time.) | off |

**How the default works:** a section can read `open 36` where all 36 are reserved
for CS/EECS majors — meaningless to a non-major, fully snipeable if you *are* the
major. The default `eligible` trigger therefore counts **unreserved plus
reserved for any group you list in `"reserved_groups"`** (below), and pings
only when *that* number goes up. Haven't set any groups? It gracefully counts just
the unreserved. If instead you want a ping on **every** open seat — reserved
for anyone or not — set `"alert_on": ["*"]`.

## Reserved seats & `reserved_groups` — only alert on seats YOU can snipe

Departments reserve blocks of seats for specific student populations (SIS "reserve
capacities"). Each block appears in the section page's JSON as a `seatReservations[]`
entry with a **requirement group** — a code plus a human-readable description — and
its own held/taken counts, so **seats still open to that group = that block's open
count** (these per-group opens sum to the section's total `open_reserved`; verified
live 2026-07-31).

List what you are in `"reserved_groups"` (globally, or per class inside a `classes[]`
entry to override) — the default `eligible` trigger then counts those reserved
seats as yours too:

```json
"reserved_groups": ["Electrical Engineering & Computer Science", "001600"]
```

Matching rules (each list entry is one token):

- **Text token** → case-insensitive **substring** match against the group
  description. `"statistics"` matches `Statistics Majors`; `"Computer Science"`
  matches the long EECS/CS/ECE description.
- **All-digit token** → exact match against the requirement-group **code**
  (leading zeros optional): `"001600"` or `"1600"`.
- **`!`-prefixed token** → **exclusion**: a block it hits never counts, even if an
  include token also hits. Use it to narrow broad tokens — e.g.
  `["Computer Science", "!Transfer"]` counts CS-major blocks but not
  `Computer Science Majors: New Transfer Students`. Recommended excludes for most
  people: `"!Transfer"`, `"!first term"`, and `"!Minor"` unless you're the minor.
- Seats reserved for **"Students with Enrollment Permission"** (code `000055`) are
  held for *specific students by SID* — being in a "group" can't get you one, so text
  tokens never match that block (only the explicit code does, for the rare student
  who actually holds a permission).

**What do the groups look like?** They're free-form — the Registrar creates
requirement groups on request (any "combination of requirements", per the SIS
job aid), so there is no fixed list. Patterns observed live across ~34 sections
(2026-07-31): single majors (`Statistics Majors`), major lists (`Undergraduate
Students: Electrical Engineering & Computer Science, Computer Science, and
Electrical & Computer Engineering Majors`), exclusions (`Non-EECS Declared
Engineering Majors`), colleges (`Undeclared Students in the College of
Engineering`), transfer admits (`New Letters & Sciences Transfer Students`,
`Computer Science Majors: New Transfer Students`), minors (`Students with a Minor
in Public Policy`), graduate programs (`Master of Design Students`), joint majors
(`Bioengineering and Joint Bioengineering / Materials Science Engineering
Majors`), and the SID-specific `Students with Enrollment Permission`.

**To see the exact groups on *your* classes** (copy the text or code straight into
your config):

```bash
python3 snipe.py --show-reserved
```

```
CS 188 (LEC 001) — open 221 (0 unreserved, 221 reserved)
     66 open | code 001600 | "Undergraduate Students: Electrical Engineering & Computer Science, ..."
    144 open | code 000055 | "Students with Enrollment Permission"  <- held for specific students by SID; NOT snipeable via a group
      7 open | code 001232 | "Master of Design Students"
      4 open | code 001373 | "Non-EECS Declared Engineering Majors"
```

(The same info is on the class's page on classes.berkeley.edu under "Reserved
Seats".) The per-group breakdown is refreshed from the **uncached** ajax variant
on every detected change, so `eligible` reflects the live reserved counts
in real time — not the ~15-minute-cached page. (Requirement-group *codes* still
come from the slow page refresh, so a reservation block added mid-term is
matchable by its code only after the next page refresh; its description text
matches immediately.)

Set triggers in your config, e.g. `"alert_on": ["*", "waitlist"]`. You can also
set a **per-class** `"alert_on"` inside any `classes[]` entry to override the global
list for just that section.

**Alert pacing (optional).** Two config keys control how often alerts fire (both
default `0` = off, preserving the fire-on-every-change behavior):

- `"alert_cooldown_seconds"` — suppress a repeat of the *same* alert kind for a class
  within this many seconds. Stops spam when a seat flickers open/closed.
- `"repeat_while_open_seconds"` — if seats stay open, re-ping every this many seconds so
  a still-snipeable seat doesn't go silent after the first alert.

No CalNet, no login, no gated API, no third-party server. It only reads public
pages — it never touches CalCentral. It can't enroll for you; it tells you *when*.

## Requirements

- **Python 3.8+** (standard library only — nothing to `pip install`).
- macOS for native desktop alerts (Linux uses `notify-send` if installed).
  Email and Telegram work anywhere.

## Setup

All configs live in `configs/`, all state files in `states/` (both gitignored
except the tracked example, so secrets and runtime data never get committed):

```bash
cp configs/config.example.json configs/config.json
```

Then edit `configs/config.json`:

1. **Get each class URL.** On `classes.berkeley.edu`, search your course, click the
   **specific section** you want, and copy the URL from the address bar. It looks
   like `https://classes.berkeley.edu/content/2026-fall-compsci-61a-001-lec-001`.
   (You need the section's *content* page — not a search results URL.)
2. Add one `{ "name": ..., "url": ... }` entry per section under `"classes"`.
3. Optionally list your majors/programs in `"reserved_groups"` (see the section
   above) — run `--show-reserved` to see the exact group names on your classes.
4. Every other key is optional; the defaults (60 s polls, fast polling,
   cache-busting on) are already tuned for the fastest polite detection. Keep
   `"poll_interval_seconds"` ≥ 60, or ≥ 30 at the very lowest; see *Be polite*
   below. With fast polling this interval ≈ your detection latency.

**Watching for several people?** Make one config each — `configs/config-alice.json`,
`configs/config-bob.json` — with their own classes, `reserved_groups`, and
notification targets. Each config automatically pairs with its own state file
(`configs/config-alice.json` → `states/state-alice.json`), so runs never clobber
each other. Run one process per person:

```bash
python3 snipe.py --config configs/config-alice.json
```

## Run

```bash
python3 snipe.py                 # watch everything in configs/config.json (loops)
python3 snipe.py --config configs/config-alice.json   # use another config file
python3 snipe.py --once          # one poll of all classes, then exit (good for testing)
python3 snipe.py --list          # print the configured classes and exit
python3 snipe.py --show-reserved # print each class's reserved-seat groups and exit
python3 snipe.py --interval 90   # override poll_interval_seconds for this run
python3 snipe.py --url "https://classes.berkeley.edu/content/2026-fall-compsci-61a-001-lec-001"
python3 snipe.py --state states/other.json  # override the auto-paired state file
python3 snipe.py --test-notify   # send a test notification and exit
python3 snipe.py --no-fast-poll  # legacy mode: full-page polls only (v0.2 behavior)
python3 snipe.py --bust-cache    # force cache-busting on (it's already the default)
python3 snipe.py -v              # verbose (show retries/debug); -q for alerts-only
python3 snipe.py --logfile snipe.log   # also append logs to a file
python3 snipe.py --version       # print the version and exit
```

### All config keys

| Key | Default | Meaning |
|---|---|---|
| `classes` | *(required)* | `{"name", "url"}` per watched section; optional per-class `"alert_on"` and `"reserved_groups"` override the globals for that section |
| `reserved_groups` | `[]` | who you are: description substrings, digit codes, `!`-prefixed exclusions |
| `alert_on` | `["eligible"]` | any of `eligible`, `*`, `unreserved`, `reserved`, `waitlist`, `capacity`, `status` |
| `poll_interval_seconds` | `60` | ≈ detection latency in fast mode; keep ≥ 60 (below 30 the app warns loudly) |
| `fast_poll` | `true` | real-time fragment tier; `false` = v0.2 full-page polling |
| `content_refresh_seconds` | `450` | fast mode: page-refresh cadence for O/W/C status + group codes (reserved counts are live from the ajax tier) |
| `bust_cache` | `true` | cache-bust full-page fetches (edge cache only) |
| `alert_cooldown_seconds` | `0` | suppress same-kind repeats within N s (anti-flicker) |
| `repeat_while_open_seconds` | `0` | re-ping every N s while seats stay snipeable |
| `notify.desktop` / `notify.sound_name` | `true` / `"Glass"` | native notification + macOS sound |
| `notify.email` | `null` | SMTP block (`host`, `port` 587, `use_tls` true, `username`, `password` or `password_env`, `from`, `to` — multiple recipients comma-separated) |
| `notify.telegram` | `null` | `bot_token` or `bot_token_env`, plus `chat_id` |

### Reliability details (what it does for you automatically)

- **One coalesced ping per poll.** If several triggers fire for the same class at
  once (e.g. a seat opened *and* a waitlist spot opened), you get one notification
  listing everything.
- **Probe discovery is persistent and self-healing.** The sibling probe found on
  the first poll is saved to the state file (restarts skip discovery); if your
  section vanishes from the probe's fragment, the watcher re-discovers a new probe
  on the next poll.
- **Fragment outages can't corrupt alerts.** If the fragment tier fails
  transiently, the watcher repeats the last live reading instead of adopting
  ~15-min-stale page counts (which could fire phantom alerts or swallow a real
  drop on recovery). After 5 consecutive failures it degrades to full-page
  polling and re-tries the fragment tier every ~15 minutes.
- **Real-time reserved fetch degrades gracefully.** If the uncached ajax fetch
  fails on a change, the watcher falls back to the last cached reserved
  breakdown for that poll rather than erroring.
- **Persistent 404s get one loud warning.** A section page that keeps 404ing
  (renumbered section / ended course) triggers a single "fix this URL"
  notification instead of silent per-poll skips.
- **Transient network errors are retried** (3 attempts, exponential backoff),
  every outbound request is spaced ~0.75 s from the last with per-poll jitter, and
  responses are gzip-compressed (~96% less transfer), so the watcher never bursts
  the server.
- **State survives restarts.** Baselines, probe discovery, and alert cooldowns are
  persisted after every poll — restarting never re-alerts on unchanged classes.

Example output (timestamps are in Pacific time; here the watcher has
`"reserved_groups": ["Non-EECS Declared Engineering Majors"]`, so the freed
reserved seat counts as snipeable):

```
2026-07-29 14:03:11 PDT  CS 161 (LEC 001): OPEN | enrolled 145/180 | open 35 (35 eligible, 0 unreserved, 35 reserved) | waitlist 60/300
2026-07-29 14:05:20 PDT  CS 161 (LEC 001): OPEN | enrolled 144/180 | open 36 (36 eligible, 0 unreserved, 36 reserved) | waitlist 60/300
2026-07-29 14:05:20 PDT  >>> ALERT: CS 161 (LEC 001): 36 seat(s) YOU can snipe (0 unreserved + 36 reserved for your group(s); 144/180) — enroll now!
```

(The status line breaks the open count down — e.g. `open 36 (36 eligible,
0 unreserved, 36 reserved)`, all counts of *open* seats (the `reserved` part appears
only when some open seats are reserved) — where **eligible** is how
many you can actually snipe (unreserved plus any reserved for your
`"reserved_groups"`), **unreserved** is snipeable by anyone, and **reserved** is
held for a group you may or may not be in. With no `"reserved_groups"`
configured the default only pings for unreserved seats; use `"alert_on": ["*"]`
to get pinged on every opening instead.)

### Run it in the background

```bash
# simplest: keep it running after you close the terminal
nohup python3 snipe.py > snipe.log 2>&1 &

# stop it later
pkill -f "snipe.py"
```

## Data freshness

**Fast polling (default, `"fast_poll": true`).** The associated-sections fragment
is uncached, so seat/waitlist changes are visible the moment they happen —
**detection latency ≈ your poll interval**. When the numbers move, the watcher
reads the live per-group reserved breakdown from the **uncached ajax variant**,
so both the total open count *and* which group a freed seat belongs to are
real-time — `eligible` fires the moment a seat you can snipe opens. The only
values that ride the ~15-minute page cache are the `O`/`W`/`C` status label
(used by the opt-in `status` trigger) and the requirement-group codes.

Steady-state request cost: **one fragment request per course per poll** (shared
across watched sections of the same course); on a change, one uncached ajax fetch
for the reserved breakdown; and a plain-page fetch only every
`content_refresh_seconds` (default 450) to refresh status + codes.

**Legacy mode (`--no-fast-poll`).** Polls only the section pages, which sit
behind two stacked ~15-minute caches (measured): a **Fastly CDN** edge cache
(`max-age=900`) and, at the origin, a **Drupal dynamic render cache**. A plain
scrape detects a change in **~15 min on average, ~30 min worst case**.
`bust_cache` (config key, or the `--bust-cache` flag) appends a unique query
param that misses the CDN/edge cache — roughly **halving** that latency (worst
~30 → ~15 min); the origin render cache can't be bypassed from the public side.
In fast mode `bust_cache` only affects the periodic full-page refreshes (the
change-triggered reserved fetch uses the always-uncached ajax variant). It
defaults to **true** since v0.3, so there's nothing to enable — set
`"bust_cache": false` to opt out.

**Why not use BerkeleyTime instead?** BerkeleyTime samples the same data only
~every 15 min and stores change-history (median ~1.3h between recorded snapshots),
so it's a historical-trends tool, not a live feed — far staler than the fragment
feed this watcher reads, and it can't be sped up. `classes.berkeley.edu` is the official, upstream source. The only
truly live enrollment view is **CalCentral/SIS** (authenticated), which this tool
deliberately never touches. See `berkeley-waitlist-window.md` §2.5 for the full
measurement.

## Optional alert channels

Native desktop notifications need no setup. To also get **email** or **Telegram**
(so alerts reach your phone), fill the corresponding block in your config file.

Each secret can be supplied **two ways** — pick one:

- **Env var (recommended):** set `password_env` / `bot_token_env` to the *name* of an
  environment variable that holds the secret. Nothing sensitive touches the file.
- **Inline:** put the secret directly in the block as `password` / `bot_token`. This is
  handy, but the secret then lives in the config file in **plaintext** — so keep that file
  out of version control (the bundled `.gitignore` already ignores it). Inline
  `password` values have their whitespace stripped, so you can paste a Gmail App
  Password in its spaced `abcd efgh ijkl mnop` form. If both are set, the inline value
  wins.

**Email (SMTP):** copy `_email_example` to `"email"`, then either export the env var:

```bash
export CAL_SEAT_SMTP_PASSWORD="your-app-password"   # Gmail: use an App Password
python3 snipe.py
```

…or drop the App Password straight into the block as `"password": "abcd efgh ijkl mnop"`
and skip the export.

**Get alerts as a text message (no extra code).** The email channel can send to
**multiple recipients** — comma-separate the `"to"` field — and every US carrier runs
a free **email-to-SMS gateway** that turns an email to `<number>@<gateway>` into a text
on your phone. So add your gateway address alongside your normal email and each alert
also arrives as a text:

```json
"to": "you@berkeley.edu, 5105551234@tmomail.net"
```

Replace `5105551234` with your 10-digit number and pick your carrier's gateway:

| Carrier | SMS gateway | MMS gateway (prefer this) |
|---|---|---|
| **T-Mobile** (incl. Mint, Metro, Ultra) | `<number>@tmomail.net` | `<number>@tmomail.net` |
| **Verizon** | `<number>@vtext.com` | `<number>@vzwpix.com` |
| **AT&T** | `<number>@txt.att.net` | `<number>@mms.att.net` |
| **Google Fi** | `<number>@msg.fi.google.com` | `<number>@msg.fi.google.com` |
| **Cricket** (AT&T) | `<number>@sms.cricketwireless.net` | `<number>@mms.cricketwireless.net` |

Prefer the **MMS** gateway when the carrier has a distinct one — plain SMS gateways cap
around 160 characters and can truncate or mangle the message; MMS carries the full alert.
Notes: the text shows up from an email-style sender (save it as a contact to spot it
fast), gateways can add a few seconds to a minute of delay and may rate-limit, and a
ported or MVNO number may use a different gateway than its original carrier — if texts
don't arrive, double-check the gateway for your specific plan.

**Telegram:** create a bot with @BotFather, get your `chat_id`, copy
`_telegram_example` to `"telegram"`, then either export the env var:

```bash
export CAL_SEAT_TELEGRAM_TOKEN="123456:ABC-your-bot-token"
python3 snipe.py
```

…or set `"bot_token": "123456:ABC-your-bot-token"` inline instead.

## Notes & caveats

- **Also watch the discussion/lab section.** For a lecture with a required
  discussion or lab, Berkeley's waitlist enrolls you **only if the linked
  discussion/lab also has an open seat** — being #1 on the lecture waitlist does
  nothing if the discussion is full (SIS "How Waitlists Work," FAQ #1056). So add
  **both** the lecture *and* your discussion section as watched classes, and pick a
  discussion that isn't packed. (Doc `berkeley-waitlist-window.md` §5.)
- **A reserved-seat warning.** If open seats show as `reserved` (e.g. `open 36
  (0 eligible, 0 unreserved, 36 reserved)`), they may be held for a major/enrollment group you're
  not in — **or for a *specific* student by SID** ("Students with Enrollment
  Permission"), which does *not* mean the seat is available to you. Run
  `--show-reserved` to see who they're held for and put your groups in
  `"reserved_groups"` — the default trigger then counts those seats as yours.
  With no groups set, the default already skips all reserved seats.
- **An alert ≠ a guaranteed snipe.** Berkeley's system can occasionally route an
  enroll attempt onto the waitlist instead ("waitlist not processed yet"), and the
  reallocation batch itself runs only **~every 6 hours** — so you typically have a
  long runway to click, not a millisecond race. Watching wins you *speed and
  information*, not certainty. See `berkeley-waitlist-window.md` §1.1 / §4.
- **Manual-waitlist sections** are picked by the department by hand — speed won't
  help there (doc §4.1).
- **Be polite (this matters).** Berkeley's Acceptable Use policy prohibits
  interfering with normal operation of its systems. A 1–2 minute cadence per class
  is low-impact and fine; do **not** crank the interval down to hammer the server —
  the fragment endpoint is uncached, so every hit reaches the origin. The watcher
  also spaces its own requests ~0.75s apart, well under the server's burst rate
  limit (observed around ~220 rapid requests). This tool reads public pages only,
  at a gentle rate, with an honest User-Agent.
- Not affiliated with UC Berkeley or with waitlistwarrior.net.

## Development

Offline unit tests (parsers against saved page/fragment fixtures, the fast-poll
flow against a faked server, alert transitions, cooldown, config validation) live
in `tests/`:

```bash
python3 -m unittest discover -s tests
```

## License

MIT — see [`LICENSE`](LICENSE).
