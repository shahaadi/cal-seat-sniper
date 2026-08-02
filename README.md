# cal-seat-sniper

A tiny, dependency-free watcher for UC Berkeley class seats. It reads a section's
enrollment numbers from **BerkeleyTime's public GraphQL API** — which pulls
Berkeley's **official SIS** data directly every ~15 minutes — and **notifies you
when a seat becomes snipeable**, your cue to fire a pre-staged enroll in CalCentral
during the "window" between someone dropping and the next waitlist batch run.

It used to scrape `classes.berkeley.edu`. That was dropped: those pages have
*uncached HTTP* but are fed from SIS by a separate import job that was observed
lagging **hours** on real enrollment changes, so a fresh-looking pull couldn't be
trusted as current (see *Why not classes.berkeley.edu?* below). BerkeleyTime is an
independent consumer of the same SIS Class API, so it tracks enrollment reliably —
at the cost of a hard ~15-minute freshness floor. Detection latency is therefore
**~15 min + your poll interval**: not real-time, but trustworthy, which is the
whole point of the switch.

This does, locally and for free, the core of what a paid service like
`waitlistwarrior.net` does. See `berkeley-waitlist-window.md` for the full
mechanism (why the window exists, §1.1 and §4).

## How it works

The watcher's **sole data source is BerkeleyTime's public GraphQL API**
(`https://berkeleytime.com/api/graphql`, unauthenticated). For each watched
section it runs one query:

```
enrollment(year, semester, subject, courseNumber, sectionNumber) { latest { … } }
```

`latest` is the most recent 15-minute snapshot and carries **everything this
watcher needs**: the status flag, `enrolledCount`, `maxEnroll` (capacity),
`waitlistedCount`, `maxWaitlist`, `openReserved`, and the full
per-group `seatReservationCount[]` breakdown (each with a requirement-group **code
+ description** and its own enrolled/max counts). Because that single object has
the open counts *and* the reserved breakdown, there's nothing else to fetch —
`classes.berkeley.edu` is never contacted.

**You name each section directly in the config**, in the same order as a
class-schedule link (`2026-fall-compsci-61a-001`): `year`, `semester`, `subject`,
`number`, `section` — the five coordinates the query needs. Use the exact SIS
subject code (`COMPSCI`, `MEC ENG`, `PUB POL` — not `CS`/`ME`/`PP`; case and
internal spaces don't matter); `semester` is `Fall`/`Spring`/`Summer`. The section
**number** alone identifies a section, so a discussion/lab is just its own number
(e.g. `101`) — no `LEC`/`DIS`/`LAB` type needed. Year+semester are per-class, so
you can watch different terms at once.

**Open seats = `capacity − enrolled`.** Per-group reserved opens come from
`seatReservationCount[]` (`open = maxEnroll − enrolledCount` per block). The
`O`/`W`/`C` status label is reconstructed from BerkeleyTime's Open/Closed flag plus
the live counts (open seats → Open; else full but waitlist has room → Waitlist;
else Closed).

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
| **`waitlist`** | A **spot on the waitlist opens** — the waitlist was full and now has room to get in line. (The waitlist limit `maxWaitlist` comes from the same snapshot as the seat counts.) This is *not* the same as the line merely advancing. | off |
| **`capacity`** | The section's **max capacity** goes up — the department *expanded* the course (more total seats), independent of whether any are open yet. | off |
| **`status`** | The section's status flips to **Open**. Informational: the alert says whether it's snipeable or "Open but all seats reserved." | off |

**How the default works:** a section can read `open 36` where all 36 are reserved
for CS/EECS majors — meaningless to a non-major, fully snipeable if you *are* the
major. The default `eligible` trigger therefore counts **unreserved plus
reserved for any group you list in `"reserved_groups"`** (below), and pings
only when *that* number goes up. Haven't set any groups? It gracefully counts just
the unreserved. If instead you want a ping on **every** open seat — reserved
for anyone or not — set `"alert_on": ["*"]`.

## Why not classes.berkeley.edu?

Earlier versions read the Berkeley Class Schedule's *uncached* endpoints — the
associated-sections fragment and the `?_wrapper_format=drupal_ajax` variant. Those
endpoints really do bypass the CDN/Drupal HTTP caches (`age: 0`, `UNCACHEABLE` on
every hit) — that part was correct.

**The flaw: "uncached HTTP" is not the same as "fresh data."**
`classes.berkeley.edu` is a Drupal site that renders those endpoints from its
**own database**, which is fed from SIS by a **separate import job**. The uncached
headers only prove the HTTP response was rebuilt per request — not that the
underlying database reflects current SIS enrollment.

**In practice that feed was unreliable.** The SIS → `classes.berkeley.edu` import
was observed lagging **hours** on real enrollment changes (one drop took ~5–7 hours
to appear), was inconsistent in *both* directions (enrolls sometimes showed in 2–3
min, sometimes not), and delivered changes **late and in abrupt batches** — so a
fresh-looking pull could not be trusted as current. That is fatal for
time-sensitive seat sniping.

**BerkeleyTime avoids this because it is not downstream of the Class Schedule.**
It's an independent, sibling consumer that pulls the **official SIS Class API**
(`gateway.api.berkeley.edu/sis`) directly, on its own schedule — so it tracks SIS
far more reliably than the laggy Class Schedule feed. The trade-off is a fixed
15-minute refresh cadence (see *Data freshness*), which is a price worth paying for
data you can trust.

## Reserved seats & `reserved_groups` — only alert on seats YOU can snipe

Departments reserve blocks of seats for specific student populations (SIS "reserve
capacities"). Each block appears in BerkeleyTime's `seatReservationCount[]` as an
entry with a **requirement group** — a code plus a human-readable description — and
its own enrolled/max counts, so **seats still open to that group = that block's open
count** (these per-group opens sum to the section's total `open_reserved`).

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
COMPSCI 188 001 — open 221 (0 unreserved, 221 reserved)
     66 open | code 001600 | "Undergraduate Students: Electrical Engineering & Computer Science, ..."
    144 open | code 000055 | "Students with Enrollment Permission"  <- held for specific students by SID; NOT snipeable via a group
      7 open | code 001232 | "Master of Design Students"
      4 open | code 001373 | "Non-EECS Declared Engineering Majors"
```

(The same info is on the class's page on classes.berkeley.edu under "Reserved
Seats".) The per-group breakdown — codes *and* descriptions — comes from the same
BerkeleyTime snapshot as the seat counts, so `eligible` reflects the reserved
counts as of the newest 15-minute snapshot, matchable by either text or code.

Set triggers in your config, e.g. `"alert_on": ["*", "waitlist"]`. You can also
set a **per-class** `"alert_on"` inside any `classes[]` entry to override the global
list for just that section.

**Alert pacing (optional).** Two config keys control how often alerts fire (both
default `0` = off, preserving the fire-on-every-change behavior):

- `"alert_cooldown_seconds"` — suppress a repeat of the *same* alert kind for a class
  within this many seconds. Stops spam when a seat flickers open/closed.
- `"repeat_while_open_seconds"` — if seats stay open, re-ping every this many seconds so
  a still-snipeable seat doesn't go silent after the first alert.

No CalNet, no login, no gated API key. It only reads BerkeleyTime's public API —
it never touches CalCentral. It can't enroll for you; it tells you *when*.

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

1. **Name each section** in class-schedule order — `year`, `semester`, `subject`,
   `number`, `section` — one entry per section under `"classes"`, e.g.
   `{ "year": 2026, "semester": "Fall", "subject": "COMPSCI", "number": "61A", "section": "001" }`.
   Use the exact SIS subject code (`COMPSCI`, `MEC ENG`, `PUB POL`); the section
   number identifies the section (a discussion is just its own number, e.g. `101`).
   Alerts and logs label each section as `SUBJECT NUMBER SECTION`.
2. Add both the **lecture** and your **discussion/lab** as separate entries (see
   *Notes & caveats*). Year+semester are per-class, so different terms can coexist.
3. Optionally list your majors/programs in `"reserved_groups"` (see the section
   above) — run `--show-reserved` to see the exact group names on your classes.
4. Every other key is optional; the defaults (5-minute polls) are already tuned for
   polite detection. Keep `"poll_interval_seconds"` ≥ 60 — BerkeleyTime's data only
   moves every ~15 min, so faster polling just loads a volunteer-run service without
   getting fresher data (see *Be polite* below).

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
python3 snipe.py --interval 600  # override poll_interval_seconds for this run
python3 snipe.py --class "2026:Fall:COMPSCI:161:001"  # watch one section, no config file
python3 snipe.py --state states/other.json  # override the auto-paired state file
python3 snipe.py --test-notify   # send a test notification and exit
python3 snipe.py -v              # verbose (show retries/debug); -q for alerts-only
python3 snipe.py --logfile snipe.log   # also append logs to a file
python3 snipe.py --version       # print the version and exit
```

### All config keys

| Key | Default | Meaning |
|---|---|---|
| `classes` | *(required)* | one entry per section, in slug order: `{"year", "semester", "subject", "number", "section"}` (e.g. `2026`, `"Fall"`, `"COMPSCI"`, `"161"`, `"001"`); optional per-class `"alert_on"` / `"reserved_groups"` override the globals |
| `reserved_groups` | `[]` | who you are: description substrings, digit codes, `!`-prefixed exclusions |
| `alert_on` | `["eligible"]` | any of `eligible`, `*`, `unreserved`, `reserved`, `waitlist`, `capacity`, `status` |
| `poll_interval_seconds` | `300` | seconds between polls; detection latency ≈ 15 min + this; keep ≥ 60 (below it the app warns) |
| `alert_cooldown_seconds` | `0` | suppress same-kind repeats within N s (anti-flicker) |
| `repeat_while_open_seconds` | `0` | re-ping every N s while seats stay snipeable |
| `notify.desktop` / `notify.sound_name` | `true` / `"Glass"` | native notification + macOS sound |
| `notify.email` | `null` | SMTP block (`host`, `port` 587, `use_tls` true, `username`, `password` or `password_env`, `from`, `to` — multiple recipients comma-separated) |
| `notify.telegram` | `null` | `bot_token` or `bot_token_env`, plus `chat_id` |

### Reliability details (what it does for you automatically)

- **One coalesced ping per poll.** If several triggers fire for the same class at
  once (e.g. a seat opened *and* a waitlist spot opened), you get one notification
  listing everything.
- **Stale-data warnings.** BerkeleyTime's datapuller only runs while a term is in
  its self-service enrollment window (and covers undergraduate sections). When the
  newest snapshot is older than ~30 min the watcher logs a staleness warning, so a
  frozen reading never looks live.
- **A missing section gets one loud warning.** A section BerkeleyTime can't
  resolve (wrong subject/number/section, or a term it doesn't pull) keeps failing;
  after a few consecutive misses the watcher fires a single "fix this class"
  notification instead of silent per-poll skips.
- **Transient network errors are retried** (3 attempts, exponential backoff),
  every outbound request is spaced ~0.75 s from the last with per-poll jitter, and
  responses are gzip-compressed, so the watcher never bursts the API.
- **State survives restarts.** Baselines and alert cooldowns are persisted after
  every poll — restarting never re-alerts on unchanged classes.

Example output (timestamps are in Pacific time; here the watcher has
`"reserved_groups": ["Non-EECS Declared Engineering Majors"]`, so the freed
reserved seat counts as snipeable):

```
2026-07-29 14:03:11 PDT  COMPSCI 161 001: OPEN | enrolled 145/180 | open 35 (35 eligible, 0 unreserved, 35 reserved) | waitlist 60/300
2026-07-29 14:05:20 PDT  COMPSCI 161 001: OPEN | enrolled 144/180 | open 36 (36 eligible, 0 unreserved, 36 reserved) | waitlist 60/300
2026-07-29 14:05:20 PDT  >>> ALERT: COMPSCI 161 001: 36 seat(s) YOU can snipe (0 unreserved + 36 reserved for your group(s); 144/180) — enroll now!
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

**The 15-minute floor.** BerkeleyTime's datapuller refreshes enrollment from SIS
**every 15 minutes** — a hard floor. There is no live-SIS passthrough; every read
returns the most recent 15-minute snapshot. So detection latency is
**~15 min + your poll interval**. That is *not* real-time — but it is **reliable**,
which the old `classes.berkeley.edu` feed was not. Trustworthy-but-15-min beats
fresh-looking-but-hours-stale, and that reliability is the whole point of the
switch.

**Why polling faster is pointless.** BerkeleyTime fronts its API with a shared
response cache (annotated up to ~1 hour). The watcher defeats it by sending a
**unique `sessionId` header on every request** — BerkeleyTime folds that value into
its GraphQL cache key, so each poll misses the shared cache and gets the freshest
15-minute snapshot. But the *snapshot itself* still only updates every 15 min, so
polling faster than that just loads the origin without ever seeing fresher data.

**All sections in one request.** Every poll fetches all watched sections in a
single GraphQL query using field aliases (chunked at 15, BerkeleyTime's alias cap),
so a whole watchlist costs one origin hit per poll, not one per class. A section
that's missing or errors is isolated to its own slot and never sinks the rest.

**When the data is frozen.** The datapuller only runs while a term is in its
**self-service enrollment window**, and it covers **undergraduate (UGRD)** sections.
Outside those, the data doesn't move — so the watcher logs a **staleness warning**
whenever the newest snapshot is older than ~30 min (puller idle, or a non-UGRD
section).

**The only live view** is **CalCentral/SIS** (authenticated), which this tool
deliberately never touches.

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
- **Be polite (this matters).** **BerkeleyTime is a volunteer-run ASUC service.**
  Because every poll is cache-busted, each hit reaches its origin — and since the
  data only moves every 15 min, polling fast buys nothing but load. The default
  poll is 5 min and the tool warns below 60 s; do **not** crank it down. The watcher
  also spaces its own requests ~0.75 s apart and gzips responses, so it stays gentle
  on the API. Reads public data only, with an honest User-Agent.
- Not affiliated with UC Berkeley or with waitlistwarrior.net.

## Development

Offline unit tests (class-entry → query-coords parsing, the GraphQL query shape,
snapshot normalization against faked BerkeleyTime responses, status derivation,
staleness detection, alert transitions, cooldown, coalescing, and config
validation) live in `tests/`:

```bash
python3 -m unittest discover -s tests
```

## License

MIT — see [`LICENSE`](LICENSE).
