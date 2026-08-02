#!/usr/bin/env python3
"""cal-seat-sniper — a tiny, dependency-free UC Berkeley class-seat watcher.

It watches a UC Berkeley class section and notifies you the moment a seat becomes
snipeable — so you can fire a pre-staged enroll in CalCentral during the "window"
between a drop and the next waitlist batch run.

It reads a section's live enrollment straight from BerkeleyTime's public GraphQL
API, which pulls from Berkeley's official SIS Class API every 15 minutes — the
freshest source available without a CalNet login. classes.berkeley.edu is NOT
used: its SIS -> Drupal feed was observed lagging hours (and arriving in delayed
bursts) on real drops, so a fresh-looking pull there can't be trusted as current.

Data source — BerkeleyTime GraphQL (https://berkeleytime.com/api/graphql):
  The enrollment(year, semester, subject, courseNumber, sectionNumber) query
  returns ``latest`` — the most recent 15-min snapshot — with status,
  enrolledCount, maxEnroll, waitlistedCount, maxWaitlist, openReserved, and the
  full per-group ``seatReservationCount[]`` breakdown
  (requirement-group code + description), i.e. everything this watcher needs.
  Each poll sends a unique ``sessionId`` header, which forces a cache miss past
  BerkeleyTime's shared response cache (up to ~1 h) so every read reflects the
  freshest snapshot. Each watched section is given directly in the config in the
  same order as a class-schedule slug — ``year`` / ``semester`` / ``subject`` /
  ``number`` / ``section`` (e.g. 2026, Fall, COMPSCI, 161, 001) — the five
  coordinates the query needs. (The section number alone distinguishes a lecture
  from its discussion/lab, so no LEC/DIS/LAB type is required.)

Freshness ceiling: BerkeleyTime's datapuller runs every 15 min and only while a
term is in its self-service enrollment window (undergraduate sections). Detection
latency is therefore ~15 min + your poll interval — not real-time, but reliable,
unlike the classes.berkeley.edu feed. A stale-data warning fires when the latest
snapshot is older than ~30 min (puller idle, or a non-UGRD section).

No CalNet, no gated API key, no third-party server beyond BerkeleyTime's public
endpoint. Stdlib only — needs Python 3.8+. Native notifications on macOS
(osascript) and Linux (notify-send).

Usage:
    python3 snipe.py                       # loop using configs/config.json
    python3 snipe.py --config configs/config-me.json   # a per-person config
                                           # (state auto-pairs: states/state-me.json)
    python3 snipe.py --state states/mine.json          # override the auto-paired state path
    python3 snipe.py --class "2026:Fall:COMPSCI:161:001"   # watch one section, no config
    python3 snipe.py --once                # single poll of all classes, then exit
    python3 snipe.py --list                # print the configured classes and exit
    python3 snipe.py --show-reserved       # print each class's reserved-seat groups
    python3 snipe.py --interval 180        # override the poll cadence (seconds)
    python3 snipe.py --verbose             # show retries/debug detail
    python3 snipe.py --quiet               # only alerts and errors
    python3 snipe.py --logfile snipe.log   # also append logs to a file
    python3 snipe.py --test-notify         # send a test notification and exit
    python3 snipe.py --version             # print the version and exit
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import json
import logging
import math
import os
import platform
import random
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Optional

try:  # Pacific-time timestamps when the tz database is available (Python 3.9+).
    from zoneinfo import ZoneInfo

    _PACIFIC: Optional[ZoneInfo] = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - falls back to the host's local time.
    _PACIFIC = None

# --------------------------------------------------------------------------- #
# Constants (no magic numbers scattered through the code)
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(HERE, "configs")     # all config files live here
STATE_DIR = os.path.join(HERE, "states")       # all state files live here
DEFAULT_CONFIG = os.path.join(CONFIG_DIR, "config.json")
EXAMPLE_CONFIG = os.path.join(CONFIG_DIR, "config.example.json")

APP_NAME = "cal-seat-sniper"       # logger name, User-Agent, and notification title
VERSION = "0.4.0"
USER_AGENT = (
    f"{APP_NAME}/{VERSION} (personal class-seat watcher; polite polling; "
    "contact: your-email@berkeley.edu)"
)

# BerkeleyTime's public GraphQL API — the sole data source. Its datapuller
# refreshes enrollment from SIS every 15 min, so polling faster than that only
# adds load without fresher data; the default cadence is a gentle multiple.
BT_ENDPOINT = "https://berkeleytime.com/api/graphql"
BT_STALE_SECONDS = 30 * 60           # warn if the newest snapshot is older than this
                                     # (datapuller idle / non-UGRD section)
BT_MAX_BATCH = 15                    # sections per request — BerkeleyTime (graphql-armor)
                                     # rejects a query with more than 15 aliases
SEMESTERS = {"spring": "Spring", "summer": "Summer", "fall": "Fall"}
# A class entry's required fields, in class-schedule-slug order (also the --class
# spec order). The single source of truth for that schema.
CLASS_FIELDS = ("year", "semester", "subject", "number", "section")

DEFAULT_POLL_INTERVAL = 300          # seconds between polls (~15-min data; be gentle)
MIN_POLITE_INTERVAL = 60             # warn below this — BerkeleyTime is a volunteer service
REQUEST_SPACING = 0.75               # min seconds between our HTTP requests (no bursts)
HTTP_TIMEOUT = 25                    # seconds per request
FETCH_RETRIES = 3                    # attempts per poll on transient failures
FETCH_BACKOFF_BASE = 2.0             # seconds; grows as base * 2**attempt
FETCH_BACKOFF_JITTER = 1.0           # extra random seconds added to each backoff
JITTER_CAP = 15                      # max extra seconds of poll jitter
JITTER_FRACTION = 0.15               # poll jitter as a fraction of the interval
PERSISTENT_404_THRESHOLD = 3         # consecutive 404s before a loud one-time warning
DEFAULT_SOUND = "Glass"
NOTIFY_TIMEOUT = 10                  # seconds for the notify subprocess
SMTP_TIMEOUT = 20
DEFAULT_SMTP_PORT = 587              # SMTP submission port used when none is configured
TELEGRAM_TIMEOUT = 15

KNOWN_ALERT_KINDS = ("*", "capacity", "reserved", "unreserved", "eligible", "status", "waitlist")
STATUS_TAGS = {"O": "OPEN", "W": "WAITLIST", "C": "CLOSED"}

# Seats "reserved for Students with Enrollment Permission" are held for specific
# students by SID — they are never snipeable just by being in a group, so text
# tokens in "reserved_groups" deliberately cannot match this block. It's detected
# by its requirement-group code (000055) or its description text; BerkeleyTime's
# seatReservationCount[] carries both.
ENROLLMENT_PERMISSION_CODE = "000055"
ENROLLMENT_PERMISSION_TEXT = "enrollment permission"

Snapshot = dict            # a normalized enrollment reading (see _snapshot_from_bt)
log = logging.getLogger(APP_NAME)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class FetchError(Exception):
    """Raised when a request can't be completed or its enrollment data can't be read.

    ``status`` carries the HTTP status code for permanent HTTP errors (e.g. 404),
    so callers can treat a persistent 404 (moved/ended section) specially.
    """

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class ConfigError(Exception):
    """Raised when the config file is missing required values or is not well-formed."""


_last_request_at = 0.0  # monotonic time of our last outbound request (politeness)


def _pace_requests() -> None:
    """Keep at least REQUEST_SPACING seconds between outbound requests.

    Every poll is a cache-busted request to BerkeleyTime's origin (a volunteer-run
    service), so spacing our fetches avoids bursts and is kinder to it.
    """
    global _last_request_at
    wait = REQUEST_SPACING - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _http_send(req: urllib.request.Request) -> bytes:
    """Send a prepared request, retrying transient network/server errors.

    Permanent HTTP errors (e.g. 404) fail immediately; transient ones (timeouts,
    connection resets, 429, 500/502/503/504) are retried with exponential backoff.
    Returns the (gzip-decoded) response body as bytes.
    """
    last_err: Optional[Exception] = None
    for attempt in range(FETCH_RETRIES):
        _pace_requests()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)   # BadGzipFile -> OSError -> retried
                return body
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {e.code} from the server", status=e.code)
            last_err = e
        except ValueError as e:
            # UnicodeEncodeError/UnicodeError from a non-ASCII char or overlong
            # label in the URL — permanent, recurs every attempt: fail now.
            raise FetchError(f"invalid URL or encoding: {e}")
        except (urllib.error.URLError, TimeoutError, OSError, EOFError,
                zlib.error, http.client.HTTPException) as e:
            # EOFError: truncated gzip body; zlib.error: corrupted gzip stream;
            # HTTPException (e.g. IncompleteRead): connection dropped mid-body —
            # all transient, all retried.
            last_err = e
        if attempt < FETCH_RETRIES - 1:
            backoff = FETCH_BACKOFF_BASE * (2 ** attempt) + random.uniform(
                0, FETCH_BACKOFF_JITTER
            )
            log.debug("fetch attempt %d failed (%s); retrying in %.1fs",
                      attempt + 1, last_err, backoff)
            time.sleep(backoff)
    raise FetchError(f"network error after {FETCH_RETRIES} attempts: {last_err}")


def http_post_json(url: str, payload: dict, extra_headers: Optional[dict] = None) -> str:
    """POST a JSON body with a polite UA and return the response text.

    Used for BerkeleyTime's GraphQL endpoint. ``extra_headers`` carries the
    per-request ``sessionId`` used to bust the shared response cache.
    """
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    except ValueError as e:
        raise FetchError(f"invalid URL: {e}")
    return _http_send(req).decode("utf-8", "replace")


def _field(cls: dict, key: str) -> str:
    """Read a class-entry field as a stripped string ("" if missing or null).

    The single way every context reads a class field, so config parsing,
    validation, and labelling agree (e.g. a null value never leaks as "None").
    """
    return str(cls.get(key) or "").strip()


def class_coords(cls: dict) -> Optional[dict]:
    """Build BerkeleyTime query coords from a self-contained class entry.

    Each entry names its section in the same order as a class-schedule slug:
    ``year``, ``semester``, ``subject``, ``number``, ``section`` (e.g. 2026, Fall,
    COMPSCI, 161, 001). BerkeleyTime normalizes subject case/spacing, so values are
    upper-cased ("compsci" -> "COMPSCI", "mec eng" -> "MEC ENG"). Returns None if a
    field is missing/blank, the year isn't 4 digits, or the semester isn't
    Spring/Summer/Fall.
    """
    year = _field(cls, "year")
    semester = SEMESTERS.get(_field(cls, "semester").lower())
    subject = _field(cls, "subject")
    number = _field(cls, "number")
    section = _field(cls, "section")
    # isascii guards against Unicode "digits" (e.g. superscripts) that pass
    # isdigit() but make int() raise, or non-ASCII decimals that int() would
    # silently accept — a year must be four plain ASCII digits.
    if not (year.isascii() and year.isdigit() and len(year) == 4 and semester
            and subject and number and section):
        return None
    return {
        "year": int(year),
        "semester": semester,
        "subject": subject.upper(),
        "courseNumber": number.upper(),
        "sectionNumber": section.upper(),
    }


def class_key(coords: dict) -> str:
    """A stable per-section state key from its coords (year-sem-subject-course-section)."""
    return "-".join(str(coords[k]) for k in
                    ("year", "semester", "subject", "courseNumber", "sectionNumber")).lower()


def class_label(cls: dict) -> str:
    """A human label for a class: "SUBJECT NUMBER SECTION" from its fields."""
    parts = [_field(cls, k) for k in ("subject", "number", "section")]
    return " ".join(p for p in parts if p) or "(unnamed)"


def _bt_field(coords: dict) -> str:
    """One ``enrollment(...){latest{...}}`` selection for a section.

    ``semester`` is a GraphQL enum (unquoted); the string args are JSON-quoted,
    so a subject/course/section value can't break out of the query. Aliasable, so
    several of these fetch many sections in a single query (see _bt_batch_query).
    """
    return (
        "enrollment("
        f"year:{coords['year']},"
        f"semester:{coords['semester']},"
        f"subject:{json.dumps(coords['subject'])},"
        f"courseNumber:{json.dumps(coords['courseNumber'])},"
        f"sectionNumber:{json.dumps(coords['sectionNumber'])}"
        "){latest{endTime status "
        "enrolledCount maxEnroll waitlistedCount maxWaitlist openReserved "
        "seatReservationCount{enrolledCount maxEnroll "
        "requirementGroup{code description}}}}"
    )


def _bt_batch_query(coords_list: list) -> str:
    """A query fetching several sections at once, aliased ``s0``..``sN``."""
    fields = " ".join(f"s{i}: {_bt_field(c)}" for i, c in enumerate(coords_list))
    return "query{" + fields + "}"


def _bt_status(latest: dict, open_capacity: int, waitlisted: int,
               max_waitlist: Optional[int]) -> str:
    """Reconstruct SIS's O/W/C status code from BerkeleyTime's O/C flag + counts.

    BerkeleyTime reports only Open/Closed. The rest of the app expects the
    O(pen)/W(aitlist)/C(losed) label, so derive it: open seats -> O; else full
    but the waitlist has room -> W; else C. Falls back to BerkeleyTime's own flag
    when the counts are inconclusive.
    """
    if open_capacity > 0:
        return "O"
    if max_waitlist is not None and waitlisted < max_waitlist:
        return "W"
    if str(latest.get("status") or "").upper() == "O":
        return "O"
    return "C"


def _snapshot_from_bt(latest: dict) -> Snapshot:
    """Normalize a BerkeleyTime ``latest`` enrollment object into a Snapshot.

    Mirrors the field set the rest of the app consumes. BerkeleyTime's
    ``seatReservationCount`` gives the per-group breakdown — requirement-group
    code + description, with ``open`` = maxEnroll - enrolledCount — so the
    reserved/eligible triggers work off it directly, including the "Students with
    Enrollment Permission" block (code 000055). ``source_ts`` is BerkeleyTime's
    snapshot time (endTime, UTC), used for the staleness check.
    """
    enrolled = int(latest.get("enrolledCount") or 0)
    capacity = int(latest.get("maxEnroll") or 0)
    open_capacity = max(0, capacity - enrolled)
    open_reserved = int(latest.get("openReserved") or 0)
    waitlisted = int(latest.get("waitlistedCount") or 0)
    raw_max_waitlist = latest.get("maxWaitlist")
    max_waitlist = int(raw_max_waitlist) if raw_max_waitlist is not None else None
    reservations = []
    for r in latest.get("seatReservationCount") or []:
        rg = r.get("requirementGroup") or {}
        reservations.append({
            "code": str(rg.get("code") or ""),
            "description": str(rg.get("description") or ""),
            "open": max(0, int(r.get("maxEnroll") or 0) - int(r.get("enrolledCount") or 0)),
        })
    return {
        "reservations": reservations,
        "status_code": _bt_status(latest, open_capacity, waitlisted, max_waitlist),
        "enrolled": enrolled,
        "capacity": capacity,
        "waitlisted": waitlisted,
        "waitlist": max_waitlist,
        "open_reserved": open_reserved,
        "open_capacity": open_capacity,
        # the seats anyone can snipe without a major/permission restriction:
        "open_unreserved": max(0, open_capacity - open_reserved),
        "source_ts": latest.get("endTime"),
    }


def _snapshot_or_error(enrollment: Optional[dict]) -> Any:
    """Turn one query's ``enrollment`` value into a Snapshot or a FetchError.

    Returned (not raised) so batch callers can keep one bad section from sinking
    the others; the single-section caller re-raises it. Malformed section data
    (a non-object value, or wrong-typed counts) becomes a FetchError too, so a
    schema-violating response degrades that one section instead of crashing.
    """
    if not enrollment:
        return FetchError(
            "section not found on BerkeleyTime — check its subject/number/section, "
            "or the term may be one BerkeleyTime doesn't pull (it covers "
            "undergraduate sections during enrollment windows).",
            status=404,
        )
    try:
        latest = enrollment.get("latest")
        if not latest:
            return FetchError("BerkeleyTime has no enrollment snapshot for this section yet")
        return _snapshot_from_bt(latest)
    except (ValueError, TypeError, AttributeError) as e:
        return FetchError(f"malformed enrollment data from BerkeleyTime: {e}")


def _bt_post(query: str) -> dict:
    """POST a GraphQL query (unique sessionId per call) and return the parsed doc.

    The unique ``sessionId`` header is folded into BerkeleyTime's response-cache
    key, forcing a cache miss past its shared cache (annotated up to ~1 h) so the
    resolver returns the freshest 15-min datapuller snapshot.
    """
    session = f"{time.time_ns()}{random.randint(1000, 9999)}"
    raw = http_post_json(BT_ENDPOINT, {"query": query}, {"sessionId": session})
    try:
        return json.loads(raw)
    except ValueError as e:
        raise FetchError(f"BerkeleyTime returned invalid JSON: {e}")


def fetch_berkeleytime_batch(coords_list: list) -> list:
    """Fetch several sections in as few requests as possible (chunked to the alias
    cap), returning a list parallel to ``coords_list``.

    Each item is a Snapshot or a FetchError — one bad or missing section never
    sinks the rest. A whole-chunk failure (network, or a rejected query) is
    recorded against every section in that chunk.
    """
    results: list = [None] * len(coords_list)
    for start in range(0, len(coords_list), BT_MAX_BATCH):
        chunk = coords_list[start:start + BT_MAX_BATCH]
        try:
            doc = _bt_post(_bt_batch_query(chunk))
            data = doc.get("data") if isinstance(doc, dict) else None
            if not isinstance(data, dict):
                # No usable data object (null, or a malformed non-object) -> the
                # query was rejected (e.g. armor) or the response is malformed;
                # fail the whole chunk rather than crash on data.get below.
                msg = ((doc.get("errors") or [{}])[0] or {}).get("message", "unknown error") \
                    if isinstance(doc, dict) else "no data returned"
                raise FetchError(f"BerkeleyTime API error: {msg}")
        except FetchError as e:
            for j in range(len(chunk)):
                results[start + j] = e
            continue
        for j in range(len(chunk)):
            results[start + j] = _snapshot_or_error(data.get(f"s{j}"))
    return results


def snapshot_age_seconds(snap: Snapshot, now_ts: float) -> Optional[float]:
    """Seconds between a snapshot's BerkeleyTime source time and ``now_ts``.

    Returns None when the snapshot carries no parseable source timestamp.
    """
    src = snap.get("source_ts")
    if not src:
        return None
    try:
        dt = datetime.fromisoformat(str(src).replace("Z", "+00:00"))
    except ValueError:
        return None
    return now_ts - dt.timestamp()


def _enrolled_ratio(s: Snapshot) -> str:
    """The "enrolled/capacity" fragment shared by the status line and alerts."""
    return f"{s['enrolled']}/{s['capacity']}"


def _reserved_suffix(open_reserved: int) -> str:
    """", N reserved" when some open seats are reserved, else ""."""
    return f", {open_reserved} reserved" if open_reserved else ""


def _waitlist_suffix(waitlist: Optional[int]) -> str:
    """"/max" when the waitlist limit is known, else "" (unknown)."""
    return f"/{waitlist}" if waitlist is not None else ""


def status_line(name: str, s: Snapshot, eligible: int) -> str:
    """A one-line human-readable summary of a snapshot (no timestamp; the log adds it).

    ``eligible`` is the snapshot's open_eligible count, computed once per poll by
    the caller and passed in.
    """
    tag = STATUS_TAGS.get(s["status_code"], s["status_code"])
    return (
        f"{name}: {tag} | "
        f"enrolled {_enrolled_ratio(s)} | "
        f"open {s['open_capacity']} ({eligible} eligible, "
        f"{s['open_unreserved']} unreserved{_reserved_suffix(s['open_reserved'])}) | "
        f"waitlist {s['waitlisted']}{_waitlist_suffix(s['waitlist'])}"
    )


# --------------------------------------------------------------------------- #
# Alert detection (diff between the previous and current snapshot)
# --------------------------------------------------------------------------- #
def _split_group_tokens(groups: list) -> tuple:
    """Partition reserved_group tokens into (includes, excludes), stripped & non-empty.

    A leading "!" marks an exclusion; its remaining text goes to ``excludes``.
    Doing this once (rather than per reservation) is why reservation matching
    takes the pre-split tokens below.
    """
    includes, excludes = [], []
    for g in groups:
        t = g.strip()
        if t.startswith("!"):
            e = t[1:].strip()
            if e:
                excludes.append(e)
        elif t:
            includes.append(t)
    return includes, excludes


def _is_permission_block(reservation: dict) -> bool:
    """True for the SID-held "Students with Enrollment Permission" block.

    Detected by its requirement-group code (000055) or its description text —
    the single definition shared by reservation matching and the reserved-seat
    listing, so both agree on what counts as this never-group-snipeable hold.
    """
    return (reservation["code"] == ENROLLMENT_PERMISSION_CODE
            or ENROLLMENT_PERMISSION_TEXT in reservation["description"].lower())


def _reservation_hits(reservation: dict, includes: list, excludes: list) -> bool:
    """Match one reservations[] entry against already-split include/exclude tokens.

    A digit token matches the requirement-group CODE (leading zeros ignored); any
    other token is a case-insensitive SUBSTRING match on the description. A block
    held for specific students by SID ("Students with Enrollment Permission") is
    matchable only by its explicit code, never by text. An exclusion that hits
    vetoes the reservation even if an include also hits.
    """
    code = reservation["code"]
    desc = reservation["description"].lower()
    is_permission = _is_permission_block(reservation)

    def hits(token: str) -> bool:
        if token.isdigit():
            return code == token or (code.lstrip("0") == token.lstrip("0") != "")
        return not is_permission and token.lower() in desc

    if any(hits(t) for t in excludes):
        return False
    return any(hits(t) for t in includes)


def open_eligible(snap: Snapshot, groups: list) -> int:
    """Seats snipeable by *this* user: unreserved + reserved for their groups.

    With no configured groups this is just the unreserved count. The reserved
    portion comes from the snapshot's reservations (BerkeleyTime's per-group
    seatReservationCount breakdown) and is clamped to the snapshot's
    open_reserved as a guard against inconsistent reads.
    """
    open_unreserved = snap["open_unreserved"]
    includes, excludes = _split_group_tokens(groups or [])
    if not (includes or excludes):
        return open_unreserved
    open_eligible_reserved = sum(r["open"] for r in snap["reservations"]
                                 if _reservation_hits(r, includes, excludes))
    return open_unreserved + min(open_eligible_reserved, snap["open_reserved"])


def open_waitlist(snap: Snapshot) -> Optional[int]:
    """Open spots on the waitlist: waitlist (max) minus currently waitlisted.

    Returns None when the section exposes no waitlist max, since we then can't tell
    how many spots — if any — are open to get in line.
    """
    waitlist = snap["waitlist"]
    if waitlist is None:
        return None
    return max(0, waitlist - snap["waitlisted"])


def detect_alerts(
    prev: Optional[Snapshot], curr: Snapshot, alert_on: list,
    groups: list = (), curr_eligible: Optional[int] = None,
) -> list:
    """Return a list of (kind, body) alerts triggered by this change.

    Each ``body`` carries no class-name prefix — ``coalesce_alerts`` adds the
    single ``"{name}: "`` once, so it isn't repeated per message here.

    Alert kinds (configure via "alert_on"):
      "*"          -> the TOTAL number of open seats went up (a spot just freed).
                      Includes reserved seats, since a reserved seat may still be
                      open to you (your major / permission). Fires even while the
                      section still shows Waitlist — the drop window.
      "capacity"   -> the section's max capacity went up: the course was expanded
                      (more total seats), independent of whether any are open yet.
      "reserved"   -> only the RESERVED open count went up: a seat opened that is
                      held for some group — snipeable only if that group is one of
                      yours (see "eligible"), otherwise not for you.
      "unreserved" -> only the UNRESERVED open count went up (a seat anyone can
                      snipe). Identical to the default when no "reserved_groups" are
                      configured.
      "eligible"   -> the number of open seats snipeable by YOU went up: unreserved
                      plus reserved for a group listed in your "reserved_groups"
                      config (majors, minors, etc.). THE DEFAULT. With no
                      reserved_groups configured it simply counts unreserved.
      "status"     -> the section's status flipped to Open (informational).
      "waitlist"   -> a spot on the waitlist opened up: the waitlist was full and now
                      has room to get in line — NOT merely that the line advanced.
    """
    alerts: list = []
    first_seen = prev is None
    if curr_eligible is None:
        curr_eligible = open_eligible(curr, groups)

    def on_rise(kind, prev_val, curr_val, make_msg):
        """Fire ``kind`` when its open-count rose above the prior value (and is > 0).

        ``make_msg`` is a thunk so a kind's (distinct) message is built only when
        it actually fires — not for every disabled or unchanged trigger.
        """
        if kind in alert_on and curr_val > prev_val and curr_val > 0:
            alerts.append((kind, make_msg()))

    # 1) TOTAL open seats increased — a spot just freed. Reserved seats count too,
    #    because a reserved seat can still be open to you. Fires even while status is
    #    still "Waitlist": that IS the drop window. Opt-in.
    on_rise(
        "*", 0 if first_seen else prev["open_capacity"], curr["open_capacity"],
        lambda: f"{curr['open_capacity']} open seat(s) "
                f"({_enrolled_ratio(curr)}{_reserved_suffix(curr['open_reserved'])}) "
                f"— a spot just freed up, check CalCentral now!")

    # 2) Max capacity increased — the course was expanded. Opt-in. Skipped on first
    #    sight, which is a baseline reading, not an expansion event.
    if "capacity" in alert_on and not first_seen and curr["capacity"] > prev["capacity"]:
        alerts.append((
            "capacity",
            f"capacity grew {prev['capacity']} -> {curr['capacity']} "
            f"({curr['enrolled']} enrolled) — the course was expanded.",
        ))

    # 3) RESERVED open seats increased — a seat opened that's held for some group.
    #    Snipeable only if that group is one of yours (see "eligible"). Opt-in.
    on_rise(
        "reserved", 0 if first_seen else prev["open_reserved"], curr["open_reserved"],
        lambda: f"{curr['open_reserved']} reserved open seat(s) "
                f"({_enrolled_ratio(curr)}) — held for a group; snipeable "
                f"only if it's yours.")

    # 4) UNRESERVED open seats increased — a seat snipeable by anyone. Opt-in, for
    #    when you're not in the reserved group and want to ignore reserved seats.
    on_rise(
        "unreserved", 0 if first_seen else prev["open_unreserved"], curr["open_unreserved"],
        lambda: f"{curr['open_unreserved']} unreserved open seat(s) "
                f"({_enrolled_ratio(curr)}) — snipeable by anyone, enroll now!")

    # 5) Open seats snipeable by THIS user increased — unreserved plus seats reserved
    #    for a group in "reserved_groups". THE DEFAULT. (curr_eligible is computed
    #    once by the caller and threaded through; prev's is computed only when the
    #    eligible trigger is actually enabled, since it's the one costly baseline.)
    prev_eligible = (open_eligible(prev, groups)
                     if "eligible" in alert_on and not first_seen else 0)
    on_rise(
        "eligible", prev_eligible, curr_eligible,
        lambda: f"{curr_eligible} seat(s) YOU can snipe "
                f"({curr['open_unreserved']} unreserved"
                + (f" + {curr_eligible - curr['open_unreserved']} reserved for your group(s)"
                   if curr_eligible - curr['open_unreserved'] > 0 else "")
                + f"; {_enrolled_ratio(curr)}) — enroll now!")

    # 6) Section status flipped to Open. Informational — say whether it's really
    #    snipeable or Open-but-all-reserved.
    if "status" in alert_on:
        became_open = (first_seen and curr["status_code"] == "O") or (
            not first_seen and prev["status_code"] != "O" and curr["status_code"] == "O"
        )
        if became_open:
            if curr["open_unreserved"] > 0:
                msg = (f"is now OPEN with {curr['open_unreserved']} snipeable "
                       f"seat(s) — go!")
            elif curr["open_reserved"] > 0:
                msg = (f"shows OPEN but all {curr['open_capacity']} open seat(s) are "
                       f"RESERVED (need major/permission) — may not be snipeable.")
            else:
                msg = "is now OPEN."
            alerts.append(("status", msg))

    # 7) A spot on the waitlist opened — the waitlist had no room and now does, so you
    #    can get in line. The waitlist max comes from BerkeleyTime (maxWaitlist), so
    #    open_waitlist is known; distinct from the line merely advancing (which the
    #    shrinking waitlisted count would show). Opt-in.
    if "waitlist" in alert_on and not first_seen:
        prev_open_waitlist = open_waitlist(prev)
        curr_open_waitlist = open_waitlist(curr)
        if prev_open_waitlist == 0 and curr_open_waitlist and curr_open_waitlist > 0:
            alerts.append((
                "waitlist",
                f"a waitlist spot opened — {curr_open_waitlist} now free "
                f"(waitlisted {curr['waitlisted']}{_waitlist_suffix(curr['waitlist'])}) "
                f"— get in line.",
            ))

    return alerts


def evaluate_alerts(
    prev: Optional[Snapshot],
    curr: Snapshot,
    alert_on: list,
    name: str,
    alert_times: dict,
    now_ts: float,
    cooldown: float,
    repeat_seconds: float,
    groups: list = (),
    curr_eligible: Optional[int] = None,
) -> tuple:
    """Decide which alerts to actually fire, honoring cooldown and repeat-while-open.

    Returns (fired_alerts, updated_alert_times). ``alert_times`` maps an alert kind
    to the epoch time it last fired for this class.

    - cooldown: suppress a repeat of the same kind within this many seconds (anti-spam).
    - repeat_seconds: if > 0 and seats stay open, re-alert every this many seconds so
      the notification doesn't go silent while a seat is still open.
    - curr_eligible: open_eligible(curr) precomputed by the caller, threaded through
      so it's computed once per poll rather than here and in detect_alerts.
    """
    if curr_eligible is None:
        curr_eligible = open_eligible(curr, groups)
    candidates = detect_alerts(prev, curr, alert_on, groups, curr_eligible)

    if repeat_seconds > 0:
        already = {kind for kind, _ in candidates}
        still_open = (
            ("*", curr["open_capacity"], "open seat(s)"),
            ("reserved", curr["open_reserved"], "reserved open seat(s)"),
            ("unreserved", curr["open_unreserved"], "unreserved open seat(s)"),
            ("eligible", curr_eligible, "seat(s) YOU can snipe"),
        )
        for kind, count, noun in still_open:
            if kind not in alert_on or kind in already:
                continue
            if count <= 0:
                continue
            last = alert_times.get(kind)
            if last is not None and (now_ts - last) >= repeat_seconds:
                candidates.append((
                    kind,
                    f"still {count} {noun} "
                    f"({_enrolled_ratio(curr)}) — reminder, still open.",
                ))

    fired: list = []
    new_times = dict(alert_times)
    for kind, body in candidates:
        last = alert_times.get(kind)
        if cooldown > 0 and last is not None and (now_ts - last) < cooldown:
            log.debug("suppressed %s alert for %s (cooldown)", kind, name)
            continue
        fired.append((kind, body))
        new_times[kind] = now_ts
    return fired, new_times


def coalesce_alerts(name: str, fired: list) -> Optional[str]:
    """Merge one poll's fired ``(kind, body)`` alerts into one ``"{name}: ..."`` line.

    Returns ``None`` when nothing fired. The bodies (from detect_alerts /
    evaluate_alerts) carry no name prefix, so the class name is added exactly
    once here: a lone alert reads ``"{name}: body"``, and several kinds firing in
    one poll are de-duplicated and joined into ONE ping instead of one per kind.
    """
    if not fired:
        return None
    bodies: list = []
    for _kind, body in fired:
        if body not in bodies:
            bodies.append(body)
    return f"{name}: " + " | ".join(bodies)


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
def notify_desktop(title: str, message: str, sound: Optional[str] = DEFAULT_SOUND) -> None:
    """Native notification. macOS via osascript; Linux via notify-send if present."""
    system = platform.system()
    try:
        if system == "Darwin":
            def esc(s: str) -> str:
                return s.replace("\\", "\\\\").replace('"', '\\"')

            script = f'display notification "{esc(message)}" with title "{esc(title)}"'
            if sound:
                script += f' sound name "{esc(sound)}"'
            subprocess.run(["osascript", "-e", script], check=False,
                           capture_output=True, timeout=NOTIFY_TIMEOUT)
        elif system == "Linux":
            subprocess.run(["notify-send", title, message], check=False,
                           capture_output=True, timeout=NOTIFY_TIMEOUT)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass  # notification is best-effort; console output always happens


def resolve_secret(cfg: dict, literal_key: str, env_key: str) -> str:
    """Resolve a secret from an inline config value or a named environment variable.

    Prefers an inline literal (``literal_key``); otherwise reads the environment
    variable named by ``env_key``. The inline literal has its whitespace stripped
    (Gmail App Passwords are displayed in spaced groups of four but used as 16
    contiguous characters); env-var values are used exactly as provided.

    Inline secrets are convenient but sit in the config file in plain text — keep
    that file out of version control (this project's ``.gitignore`` already does).
    """
    literal = cfg.get(literal_key)
    if literal:
        return "".join(str(literal).split())
    env_name = cfg.get(env_key)
    # An env-var name must be a string to look up; a non-string (config mistake)
    # means "no usable env var" rather than a crash in os.environ.get.
    if isinstance(env_name, str) and env_name:
        return os.environ.get(env_name, "")
    return ""


def _channel_secret(channel: str, cfg: Optional[dict],
                    literal_key: str, env_key: str) -> str:
    """Resolve an optional notify channel's secret, else log a one-line skip.

    Returns "" when the channel block is a non-dict/empty (disabled) or neither the
    inline secret nor its env var is set — the caller then skips that channel. This
    is the shared "guard + resolve + skip-warn" preamble for every notify channel.
    """
    if not isinstance(cfg, dict) or not cfg:
        return ""
    secret = resolve_secret(cfg, literal_key, env_key)
    if not secret:
        log.warning("%s skipped: set notify.%s.%s or the %r env var",
                    channel, channel, literal_key, cfg.get(env_key))
    return secret


def notify_email(cfg: Optional[dict], subject: str, body: str) -> None:
    """Optional SMTP email. Password comes from ``password`` (inline) or ``password_env``."""
    pw = _channel_secret("email", cfg, "password", "password_env")
    if not pw:
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg["from"]
        msg["To"] = cfg["to"]
        msg.set_content(body)
        port = int(cfg.get("port", DEFAULT_SMTP_PORT))
        with smtplib.SMTP(cfg["host"], port, timeout=SMTP_TIMEOUT) as server:
            if cfg.get("use_tls", True):
                server.starttls()
            server.login(cfg["username"], pw)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError, KeyError, ValueError, TypeError,
            OverflowError) as e:
        # Channel failures must never kill the watch loop — including a
        # malformed "port" reaching int() (also caught up front by validation).
        log.warning("email failed: %s", e)


def notify_telegram(cfg: Optional[dict], text: str) -> None:
    """Optional Telegram alert. Token comes from ``bot_token`` (inline) or ``bot_token_env``."""
    token = _channel_secret("telegram", cfg, "bot_token", "bot_token_env")
    if not token:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": cfg["chat_id"], "text": text}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, KeyError, ValueError,
            http.client.HTTPException) as e:
        # HTTPException (e.g. IncompleteRead/BadStatusLine): response truncated
        # mid-read — a channel failure must never kill the watch loop.
        log.warning("telegram failed: %s", e)


def fire_notifications(notify_cfg: dict, message: str) -> None:
    """Send an alert across every enabled channel, plus the console/log.

    The notification title/subject is always the app name (APP_NAME).
    """
    log.warning(">>> ALERT: %s", message)
    if notify_cfg.get("desktop", True):
        notify_desktop(APP_NAME, message, sound=notify_cfg.get("sound_name", DEFAULT_SOUND))
    notify_email(notify_cfg.get("email"), APP_NAME, message)
    notify_telegram(notify_cfg.get("telegram"), f"{APP_NAME}\n{message}")


# --------------------------------------------------------------------------- #
# State persistence (so a restart doesn't re-alert on unchanged classes)
# --------------------------------------------------------------------------- #
def load_state(path: str) -> dict:
    """Load the saved per-class state, tolerating a missing or corrupt file."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # missing file, unreadable path (e.g. a directory), invalid UTF-8, or
        # corrupt JSON — all mean "no usable prior state"
        return {}


def default_state_path(config_path: str) -> str:
    """The state file that pairs with a config file, inside states/.

    configs/config.json       -> states/state.json
    configs/config-alice.json -> states/state-alice.json   (per-person configs
    get per-person state, so two watchers never clobber each other's baselines)
    anything-else.json        -> states/anything-else.state.json
    """
    stem = os.path.splitext(os.path.basename(config_path))[0]
    if stem == "config":
        name = "state.json"
    elif stem.startswith("config-"):
        name = f"state-{stem[len('config-'):]}.json"
    else:
        name = f"{stem}.state.json"
    return os.path.join(STATE_DIR, name)


def save_state(path: str, state: dict) -> None:
    """Atomically persist state via a temp file + rename.

    Best-effort: a failed write (disk full, permissions, states/ shadowed by a
    file) is logged, never raised — losing one round's persistence must not
    kill a long-running watcher; alerts still fired and memory state is intact.
    """
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as e:
        log.warning("could not save state to %s: %s", path, e)


def _working_entry(state: dict, key: str) -> dict:
    """A mutable copy of a key's state entry (or a fresh dict if absent/not a dict)."""
    entry = state.get(key)
    return dict(entry) if isinstance(entry, dict) else {}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path: str) -> dict:
    """Load, default, and validate a config file. Exits with guidance on error."""
    if not os.path.exists(path):
        sys.exit(
            f"No config found at {path}\n"
            f"Copy the example and edit it:\n"
            f"    cp {EXAMPLE_CONFIG} {path}\n"
        )
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError as e:
        sys.exit(f"Config at {path} could not be read: {e}")
    except ValueError as e:  # invalid JSON, or a file that isn't valid UTF-8
        sys.exit(f"Config at {path} is not valid JSON: {e}")
    if not isinstance(cfg, dict):
        sys.exit(f"Config at {path} must be a JSON object.")
    return _finalize_config(cfg, f"Config error in {path}")


def _finalize_config(cfg: dict, where: str) -> dict:
    """Apply defaults, validate, and sys.exit (prefixed with ``where``) on error.

    The shared tail for both config sources — a file and a ``--class`` spec — so
    they default and validate through exactly the same path.
    """
    _apply_config_defaults(cfg)
    try:
        validate_config(cfg)
    except ConfigError as e:
        sys.exit(f"{where}:\n{e}")
    return cfg


def _apply_config_defaults(cfg: dict) -> None:
    """Fill in the optional-key defaults, in place (shared by file and --class configs).

    Default alert: the open seats THIS user can snipe — unreserved plus, if
    "reserved_groups" is set, reserved for their groups. An explicit "alert_on"
    always wins (e.g. ["*"] to ping on ANY open seat).
    """
    cfg.setdefault("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    cfg.setdefault("classes", [])
    cfg.setdefault("reserved_groups", [])
    cfg.setdefault("alert_on", ["eligible"])
    cfg.setdefault("alert_cooldown_seconds", 0)
    cfg.setdefault("repeat_while_open_seconds", 0)
    cfg.setdefault("notify", {})
    # Guard the setdefault so a non-dict "notify" (e.g. null) reaches
    # validate_config's friendly "notify must be an object" error instead of
    # crashing here with an AttributeError.
    if isinstance(cfg["notify"], dict):
        cfg["notify"].setdefault("desktop", True)


def _validate_reserved_groups(value: Any, where: str, errors: list) -> None:
    """Require a list of non-empty group tokens (major/program text, a numeric
    requirement-group code, or a bare ``"!"`` exclusion is rejected)."""
    if not isinstance(value, list) or any(
        not isinstance(g, str) or not g.strip() or g.strip() == "!"
        for g in value
    ):
        errors.append(
            f"{where} must be a list of non-empty strings (major/minor/program "
            f"text to substring-match, a numeric requirement-group code, or a "
            f"\"!\"-prefixed exclusion like \"!Transfer\")."
        )


def _validate_alert_on(value: Any, where: str, errors: list) -> None:
    """Require a non-empty list whose every entry is a known alert kind."""
    if not isinstance(value, list) or not value:
        errors.append(f"{where} must be a non-empty list, e.g. [\"eligible\"].")
        return
    for kind in value:
        if kind not in KNOWN_ALERT_KINDS:
            errors.append(
                f"{where} has unknown alert {kind!r}; valid: {list(KNOWN_ALERT_KINDS)}."
            )


REQUIRED_EMAIL_FIELDS = ("host", "username", "from", "to")


def _validate_secret(block: dict, channel: str, literal_key: str, env_key: str,
                     errors: list) -> None:
    """Require a channel secret via an inline value OR a named env var (either works)."""
    if not (block.get(literal_key) or block.get(env_key)):
        errors.append(f'notify.{channel} needs a "{literal_key}" (inline) or a '
                      f'"{env_key}" (env-var name).')


def _validate_notify(notify: dict, errors: list) -> None:
    """Sanity-check the email/telegram blocks when they're present (non-null).

    Each channel is optional; a ``null``/absent block is fine. But once enabled,
    it needs its required fields and a way to reach its secret (inline value or an
    env-var name) — otherwise it would silently fail to send at alert time.
    """
    for channel in ("email", "telegram"):
        value = notify.get(channel)
        if value is not None and not isinstance(value, dict):
            errors.append(
                f"notify.{channel} must be an object (copy the _{channel}_example "
                f"block) or null to disable it."
            )
    sound = notify.get("sound_name")
    if sound is not None and not isinstance(sound, str):
        errors.append("notify.sound_name must be a string (a macOS sound name).")

    email = notify.get("email")
    if isinstance(email, dict):
        for field in REQUIRED_EMAIL_FIELDS:
            if not email.get(field):
                errors.append(f"notify.email.{field} is required when email is enabled.")
        _validate_secret(email, "email", "password", "password_env", errors)
        port = email.get("port", DEFAULT_SMTP_PORT)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            errors.append(
                f"notify.email.port must be an integer port number (e.g. {DEFAULT_SMTP_PORT}); "
                "omit it to use the default."
            )

    telegram = notify.get("telegram")
    if isinstance(telegram, dict):
        if not telegram.get("chat_id"):
            errors.append("notify.telegram.chat_id is required when telegram is enabled.")
        _validate_secret(telegram, "telegram", "bot_token", "bot_token_env", errors)


def validate_config(cfg: dict) -> None:
    """Raise ConfigError (with all problems joined) if the config is unusable."""
    errors: list = []

    def _bad_number(value, minimum_exclusive):
        return (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= minimum_exclusive)

    interval = cfg.get("poll_interval_seconds")
    if _bad_number(interval, 0):
        errors.append("poll_interval_seconds must be a positive number.")

    for key in ("alert_cooldown_seconds", "repeat_while_open_seconds"):
        if _bad_number(cfg.get(key), -1e-9):
            errors.append(f"{key} must be a number >= 0.")

    _validate_alert_on(cfg.get("alert_on"), "alert_on", errors)
    _validate_reserved_groups(cfg.get("reserved_groups", []), "reserved_groups", errors)

    classes = cfg.get("classes")
    if not isinstance(classes, list):
        errors.append("classes must be a list.")
    else:
        for i, cls in enumerate(classes):
            where = f"classes[{i}]"
            if not isinstance(cls, dict):
                errors.append(f"{where} must be an object with "
                              "year/semester/subject/number/section.")
                continue
            missing = [k for k in CLASS_FIELDS if not _field(cls, k)]
            if missing:
                errors.append(f"{where} is missing required field(s): "
                              f"{', '.join(missing)}.")
            elif class_coords(cls) is None:
                errors.append(f"{where} has an invalid year (need 4 digits) or "
                              "semester (Spring/Summer/Fall).")
            if "alert_on" in cls:
                _validate_alert_on(cls["alert_on"], f"{where}.alert_on", errors)
            if "reserved_groups" in cls:
                _validate_reserved_groups(
                    cls["reserved_groups"], f"{where}.reserved_groups", errors)

    notify = cfg.get("notify")
    if not isinstance(notify, dict):
        errors.append("notify must be an object.")
    else:
        _validate_notify(notify, errors)

    if errors:
        raise ConfigError("\n".join(f"  - {e}" for e in errors))


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
class _PacificFormatter(logging.Formatter):
    """Formatter that stamps log records in Pacific time."""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if _PACIFIC is not None:
            dt = dt.astimezone(_PACIFIC)
        else:
            dt = dt.astimezone()
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S %Z")


def setup_logging(verbose: bool, quiet: bool, logfile: Optional[str]) -> None:
    """Configure the module logger. verbose->DEBUG, quiet->WARNING, else INFO."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    log.setLevel(level)
    log.handlers.clear()
    fmt = _PacificFormatter("%(asctime)s  %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    log.addHandler(console)

    if logfile:
        try:
            fileh = logging.FileHandler(logfile, encoding="utf-8")
        except OSError as e:
            # Best-effort, like save_state / the notify channels: a bad --logfile
            # path warns (to the console handler above) instead of killing startup.
            log.warning("could not open logfile %s: %s — continuing without file logging",
                        logfile, e)
        else:
            fileh.setFormatter(fmt)
            log.addHandler(fileh)


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def _handle_fetch_error(
    state: dict, key: str, name: str, err: FetchError, notify_cfg: dict
) -> None:
    """Log a fetch failure; escalate a *persistent* not-found to a one-time alert.

    A single not-found can be a transient hiccup, but a section BerkeleyTime keeps
    failing to find usually means the subject/course/section is wrong or the
    section was removed — a quiet per-poll skip would hide that. We count
    consecutive not-founds in state and, once past the threshold, fire a single
    notification per class telling the user to fix the entry.
    """
    if err.status != 404:
        log.warning("%s: ! %s", name, err)
        return
    entry = _working_entry(state, key)
    missing = int(entry.get("missing_count", 0)) + 1
    entry["missing_count"] = missing
    if missing >= PERSISTENT_404_THRESHOLD and not entry.get("missing_alerted"):
        entry["missing_alerted"] = True
        fire_notifications(
            notify_cfg,
            f"{name}: BerkeleyTime hasn't found this section {missing} times in a "
            f"row — its subject/number/section may be wrong, or the section was "
            f"removed. Fix or remove this class in your config.",
        )
    else:
        log.warning("%s: ! %s (404 x%d)", name, err, missing)
    state[key] = entry


_BAD_CLASS_MSG = (
    "incomplete class entry — needs \"year\", \"semester\", \"subject\", \"number\", "
    "and \"section\" (e.g. 2026, Fall, COMPSCI, 161, 001)."
)


def _poll_class(coords: dict) -> Snapshot:
    """Fetch one section's snapshot from BerkeleyTime (single-section convenience).

    Uses the same batch path the poll loop uses. Raises FetchError on a
    failed/empty BerkeleyTime response.
    """
    result = fetch_berkeleytime_batch([coords])[0]
    if isinstance(result, FetchError):
        raise result
    return result


def _warn_if_stale(name: str, snap: Snapshot, now_ts: float) -> None:
    """Warn when BerkeleyTime's newest snapshot is older than BT_STALE_SECONDS.

    An old snapshot means the datapuller is idle — the term isn't in a
    self-service enrollment window, or the section isn't one BerkeleyTime pulls
    (it covers undergraduate sections) — so the reading may not reflect current
    enrollment.
    """
    age = snapshot_age_seconds(snap, now_ts)
    if age is not None and age > BT_STALE_SECONDS:
        log.warning("%s: BerkeleyTime data is ~%d min old (snapshot %s) — the "
                    "datapuller looks idle (term not in an enrollment window, or "
                    "a non-undergraduate section); readings may be stale.",
                    name, int(age // 60), snap.get("source_ts"))


def poll_once(
    classes: list,
    notify_cfg: dict,
    default_alert_on: list,
    state: dict,
    cooldown: float = 0,
    repeat_seconds: float = 0,
    default_groups: list = (),
) -> None:
    """Poll every class once via BerkeleyTime, log its status, and fire alerts.

    All sections are fetched together — one cache-busted request per BT_MAX_BATCH
    classes — then each is processed with its own alert/state bookkeeping. Each
    class fully specifies its section (year, semester, subject, number, section);
    state is keyed by the resulting canonical section id.
    """
    now_ts = time.time()
    # Resolve every class to coords + a result up front: a Snapshot, or a
    # FetchError (a bad/incomplete class entry, or what the batch returned).
    coords_list = [class_coords(cls) for cls in classes]
    fetched = iter(fetch_berkeleytime_batch([c for c in coords_list if c is not None]))
    results = [next(fetched) if c is not None else FetchError(_BAD_CLASS_MSG)
               for c in coords_list]

    for cls, coords, result in zip(classes, coords_list, results):
        name = class_label(cls)
        key = class_key(coords) if coords else name   # stable per-section state key
        alert_on = cls.get("alert_on", default_alert_on)
        groups = cls.get("reserved_groups", default_groups)
        try:
            if isinstance(result, FetchError):
                _handle_fetch_error(state, key, name, result, notify_cfg)
                continue
            snap = result
            _warn_if_stale(name, snap, now_ts)
            # Compute the (only) open_eligible for this snapshot once, then thread it
            # through the log line and the alert logic instead of recomputing it.
            curr_eligible = open_eligible(snap, groups)
            # Guard the eager status_line build so --quiet polls skip it entirely.
            if log.isEnabledFor(logging.INFO):
                log.info(status_line(name, snap, curr_eligible))
            # Carry forward this class's state entry (a mutable copy we update and
            # write back below). The diff baseline comes from it: a bookkeeping-only
            # entry (a 404 count, no snapshot) yields a None baseline — i.e. this
            # poll is treated as first sight.
            entry = _working_entry(state, key)
            prev_snap = entry.get("snapshot")
            alert_times = dict(entry.get("alert_times") or {})
            fired, new_times = evaluate_alerts(
                prev_snap, snap, alert_on, name, alert_times, now_ts, cooldown,
                repeat_seconds, groups, curr_eligible
            )
            message = coalesce_alerts(name, fired)
            if message is not None:
                fire_notifications(notify_cfg, message)
            # A successful poll clears any not-found bookkeeping.
            entry.pop("missing_count", None)
            entry.pop("missing_alerted", None)
            entry["snapshot"] = snap
            entry["alert_times"] = new_times
            state[key] = entry
        except Exception:
            # Boundary guard: nothing — not even a hand-corrupted state entry —
            # may kill the watch loop. Drop this class's saved state (it
            # re-baselines cleanly next poll) and keep watching everything else.
            log.exception("%s: unexpected error this poll — resetting this "
                          "class's saved state and continuing", name)
            state.pop(key, None)


def _poll_cfg(cfg: dict, state: dict) -> None:
    """Run one poll_once round with arguments marshalled from ``cfg`` (shared by the
    loop and the one-shot ``--once`` path so they can't drift)."""
    poll_once(cfg["classes"], cfg["notify"], cfg["alert_on"], state,
              cfg["alert_cooldown_seconds"],
              cfg["repeat_while_open_seconds"],
              cfg["reserved_groups"])


def run_loop(cfg: dict, state_path: str) -> None:
    """Poll on a loop until interrupted, persisting state after every round."""
    state = load_state(state_path)
    interval = cfg["poll_interval_seconds"]
    classes = cfg["classes"]
    if not classes:
        sys.exit("No classes configured. Add some to your config's \"classes\" list.")
    if interval < MIN_POLITE_INTERVAL:
        log.warning("poll interval %ss is below the polite minimum of %ss — "
                    "BerkeleyTime is a volunteer-run service and its data only "
                    "moves every ~15 min, so please poll gently.",
                    interval, MIN_POLITE_INTERVAL)
    log.info("Watching %d class(es) every ~%ss via BerkeleyTime. Ctrl-C to stop.",
             len(classes), interval)
    log.info("Source: BerkeleyTime GraphQL (SIS-direct, cache-busted per poll). "
             "It refreshes every ~15 min during enrollment windows, so detection "
             "latency ≈ 15 min + the poll interval; a warning fires if data goes stale.")
    try:
        while True:
            _poll_cfg(cfg, state)
            save_state(state_path, state)
            # jitter so we don't hammer on a fixed cadence
            time.sleep(interval + random.uniform(0, min(JITTER_CAP, interval * JITTER_FRACTION)))
    except KeyboardInterrupt:
        log.info("Stopped.")
        save_state(state_path, state)


def show_reserved(classes: list) -> None:
    """Fetch each class once and print its reserved-seat groups verbatim.

    This is how users find out what to put in "reserved_groups": the exact
    description text (any substring of it, case-insensitive) or the numeric
    requirement-group code, straight from BerkeleyTime's live breakdown.
    """
    if not classes:
        log.info("No classes configured.")
        return
    for cls in classes:
        name = class_label(cls)
        coords = class_coords(cls)
        if coords is None:
            log.warning("%s: ! %s", name, _BAD_CLASS_MSG)
            continue
        try:
            snap = _poll_class(coords)
        except FetchError as e:
            log.warning("%s: ! %s", name, e)
            continue
        log.info("%s — open %d (%d unreserved, %d reserved)",
                 name, snap["open_capacity"], snap["open_unreserved"],
                 snap["open_reserved"])
        if not snap["reservations"]:
            log.info("    no reserved-seat groups on this section")
        for r in snap["reservations"]:
            note = ("  <- held for specific students by SID; NOT snipeable via "
                    "a group" if _is_permission_block(r) else "")
            log.info('    %3d open | code %s | "%s"%s',
                     r["open"], r["code"], r["description"], note)


def config_from_class(spec: str) -> dict:
    """Build an in-memory config watching one section, from a --class spec.

    ``spec`` is ``YEAR:SEMESTER:SUBJECT:NUMBER:SECTION`` — the same five fields, in
    the same order, as a config-file class entry (colon-delimited so a spaced
    subject like "MEC ENG" stays intact). Defaults and validation go through the
    same path as a config file. Exits with guidance on a malformed spec.
    """
    parts = [p.strip() for p in (spec or "").split(":")]
    if len(parts) != len(CLASS_FIELDS) or not all(parts):
        sys.exit('--class must be "YEAR:SEMESTER:SUBJECT:NUMBER:SECTION", '
                 'e.g. "2026:Fall:COMPSCI:161:001".')
    # No "name": class_label derives "SUBJECT NUMBER SECTION" from these fields.
    cfg = {"classes": [dict(zip(CLASS_FIELDS, parts))]}
    return _finalize_config(cfg, "--class error")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="UC Berkeley class-seat watcher")
    ap.add_argument("--version", action="version",
                    version=f"{APP_NAME} {VERSION}")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="path to a config file (default configs/config.json)")
    ap.add_argument("--state", default=None,
                    help="path to the state file (default: the states/ file "
                         "paired with the config name, e.g. configs/config-x.json "
                         "-> states/state-x.json)")
    ap.add_argument("--class", dest="klass", metavar="YEAR:SEMESTER:SUBJECT:NUMBER:SECTION",
                    help='watch a single section with default settings, e.g. '
                         '--class "2026:Fall:COMPSCI:161:001"')
    ap.add_argument("--interval", type=int, metavar="SECONDS",
                    help="override poll_interval_seconds")
    ap.add_argument("--once", action="store_true", help="poll once and exit")
    ap.add_argument("--list", action="store_true",
                    help="print the configured classes and exit")
    ap.add_argument("--show-reserved", action="store_true",
                    help="fetch each configured class once and print its "
                         "reserved-seat groups verbatim (copy the text or code "
                         "into \"reserved_groups\"), then exit")
    ap.add_argument("--test-notify", action="store_true",
                    help="send a test notification across every configured "
                         "channel (desktop/email/telegram) and exit")
    ap.add_argument("--logfile", help="also append logs to this file")
    verbosity = ap.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true",
                           help="show retries and debug detail")
    verbosity.add_argument("-q", "--quiet", action="store_true",
                           help="only show alerts and errors")
    return ap


def main(argv: Optional[list] = None) -> None:
    """Parse CLI args and run the requested mode."""
    args = build_arg_parser().parse_args(argv)
    setup_logging(args.verbose, args.quiet, args.logfile)
    if args.state is None:
        args.state = default_state_path(args.config)

    cfg = config_from_class(args.klass) if args.klass else load_config(args.config)

    if args.test_notify:
        # cfg["notify"] is always a dict here (defaulted + validated on load).
        fire_notifications(cfg["notify"], "Test notification — it works!")
        return

    if args.interval is not None:
        if args.interval <= 0:
            sys.exit("--interval must be a positive number of seconds.")
        cfg["poll_interval_seconds"] = args.interval

    if args.list:
        classes = cfg["classes"]
        if not classes:
            log.info("No classes configured.")
        for cls in classes:
            coords = class_coords(cls)
            log.info("%s  ->  %s", class_label(cls),
                     class_key(coords) if coords else "(incomplete entry)")
        return

    if args.show_reserved:
        show_reserved(cfg["classes"])
        return

    if args.once:
        state = load_state(args.state)
        _poll_cfg(cfg, state)
        save_state(args.state, state)
    else:
        run_loop(cfg, args.state)


if __name__ == "__main__":
    main()
