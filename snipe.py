#!/usr/bin/env python3
"""cal-seat-sniper — a tiny, dependency-free UC Berkeley class-seat watcher.

It watches a class section on classes.berkeley.edu and notifies you the moment
a seat becomes snipeable — so you can fire a pre-staged enroll in CalCentral
during the "window" between a drop and the next waitlist batch run.

Three-tier polling (all three verified against the live site):

1. Change detector — the associated-sections HTML fragment at
       /sections/associated/<node_id>
   is served UNCACHED (``cache-control: no-cache``, ``x-drupal-cache:
   UNCACHEABLE``, Fastly ``x-cache: MISS`` on every hit), so its per-section
   Open Seats / Enrolled / Enrollment Limit / Waitlisted / Waitlist limit
   numbers are real-time. Seat/waitlist detection latency = your poll interval, not the
   ~15-min page cache. Gotcha: the fragment lists every section of the course
   EXCEPT the node you asked for, so each watched section is read through a
   sibling "probe" node discovered automatically on the first poll (and
   persisted in the state file, so restarts skip discovery).

2. Detail source — the section's content page embeds a JSON blob at
       drupalSettings.ucb.enrollment.available.enrollmentStatus
   with status.code (O=Open / W=Waitlist / C=Closed), enrolledCount,
   maxEnroll, waitlistedCount, maxWaitlist, reservedCount, openReserved, and
   the per-group seatReservations[] breakdown. It sits behind ~15-min caches,
   so it is fetched only for the O/W/C status + requirement-group codes on a
   slow periodic refresh.

3. Real-time reserved breakdown — the SAME content rendered UNCACHED via
       /content/<slug>?_wrapper_format=drupal_ajax
   (verified UNCACHEABLE, age:0 every hit). On a detected change this gives the
   live per-group open-reserved counts, so "seats reserved for YOUR group"
   detection isn't gated by the ~15-min page cache. (It carries descriptions
   but no requirement-group codes, so codes are enriched from tier 2.)

No CalNet, no gated API, no third-party server. Reads public pages only.
Stdlib only — needs Python 3.8+. Native notifications on macOS (osascript) and
Linux (notify-send).

Usage:
    python3 snipe.py                       # loop using configs/config.json
    python3 snipe.py --config configs/config-me.json   # a per-person config
                                           # (state auto-pairs: states/state-me.json)
    python3 snipe.py --state states/mine.json          # override the auto-paired state path
    python3 snipe.py --url "<content-url>" # watch one class, default settings
    python3 snipe.py --once                # single poll of all classes, then exit
    python3 snipe.py --list                # print the configured classes and exit
    python3 snipe.py --show-reserved       # print each class's reserved-seat groups
    python3 snipe.py --interval 90         # override the poll cadence (seconds)
    python3 snipe.py --no-fast-poll        # legacy mode: full-page polls only
    python3 snipe.py --bust-cache          # bust the CDN cache on full-page fetches
    python3 snipe.py --verbose             # show retries/debug detail
    python3 snipe.py --quiet               # only alerts and errors
    python3 snipe.py --logfile snipe.log   # also append logs to a file
    python3 snipe.py --test-notify         # send a test notification and exit
    python3 snipe.py --version             # print the version and exit
"""

from __future__ import annotations

import argparse
import gzip
import html as html_lib
import http.client
import json
import logging
import math
import os
import platform
import random
import re
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

    _PACIFIC: Optional["ZoneInfo"] = ZoneInfo("America/Los_Angeles")
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

VERSION = "0.3.0"
USER_AGENT = (
    f"cal-seat-sniper/{VERSION} (personal class-seat watcher; polite polling; "
    "contact: your-email@berkeley.edu)"
)

DEFAULT_POLL_INTERVAL = 60            # seconds between polls (fast mode: ~detection latency)
MIN_POLITE_INTERVAL = 60              # warn below this in legacy full-page mode
MIN_POLITE_INTERVAL_FAST = 30         # warn below this in fast (fragment) mode
REQUEST_SPACING = 0.75                # min seconds between our HTTP requests (no bursts)
CONTENT_REFRESH_SECONDS = 450         # full-page refresh cadence in fast mode (for the
                                      # O/W/C status label + requirement-group codes)
DISCOVERY_RETRY_SECONDS = 900         # re-try the fragment tier for a legacy-mode class this often
FRAGMENT_FAIL_LEGACY_THRESHOLD = 5    # consecutive fragment failures before legacy fallback
HTTP_TIMEOUT = 25                     # seconds per request
FETCH_RETRIES = 3                     # attempts per poll on transient failures
FETCH_BACKOFF_BASE = 2.0             # seconds; grows as base * 2**attempt
FETCH_BACKOFF_JITTER = 1.0           # extra random seconds added to each backoff
JITTER_CAP = 15                      # max extra seconds of poll jitter
JITTER_FRACTION = 0.15               # poll jitter as a fraction of the interval
PERSISTENT_404_THRESHOLD = 3         # consecutive 404s before a loud one-time warning
DEFAULT_SOUND = "Glass"
NOTIFY_TIMEOUT = 10                  # seconds for the notify subprocess
SMTP_TIMEOUT = 20
TELEGRAM_TIMEOUT = 15

KNOWN_ALERT_KINDS = ("*", "capacity", "reserved", "unreserved", "eligible", "status", "waitlist")
STATUS_TAGS = {"O": "OPEN", "W": "WAITLIST", "C": "CLOSED"}

# Seats "reserved for Students with Enrollment Permission" are held for specific
# students by SID — they are never snipeable just by being in a group, so text
# tokens in "reserved_groups" deliberately cannot match this block. It's detected
# by its requirement-group code (from the plain page) OR its description text
# (the uncached ajax breakdown carries no code — see fetch_reserved_ajax).
ENROLLMENT_PERMISSION_CODE = "000055"
ENROLLMENT_PERMISSION_TEXT = "enrollment permission"

BLOB_RE = re.compile(
    r'<script type="application/json" data-drupal-selector="drupal-settings-json">'
    r"(.*?)</script>",
    re.S,
)

# One associated-sections fragment "row" (one section) starts with this wrapper.
FRAGMENT_ROW_SPLIT = '<div class="detail-class-associated-sections-flex">'
FRAGMENT_HREF_RE = re.compile(r'<a href="[^"]*/content/([^"?#]+)"')
FRAGMENT_NUM_RE = re.compile(
    r'<span class="detail-label">'
    r"(Open Seats|Enrolled|Enrollment Limit|Waitlisted|Waitlist limit)"
    r":</span>\s*([0-9][0-9,]{0,9})"
)

Snapshot = dict            # a normalized enrollment reading (see parse_enrollment)
log = logging.getLogger("cal-seat-sniper")


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def now_pacific() -> datetime:
    """Return an aware datetime in UC Berkeley's timezone (Pacific)."""
    if _PACIFIC is not None:
        return datetime.now(_PACIFIC)
    return datetime.now().astimezone()


def timestamp() -> str:
    """A concise Pacific-time timestamp for snapshots and state."""
    return now_pacific().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Fetching + parsing the public class page
# --------------------------------------------------------------------------- #
class FetchError(Exception):
    """Raised when a page can't be fetched or its enrollment data can't be read.

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

    classes.berkeley.edu rate-limits bursts (observed around ~220 rapid
    requests); spacing our fetches keeps every poll round far below that and
    is generally kinder to the origin, since the fragment endpoint is uncached.
    """
    global _last_request_at
    wait = REQUEST_SPACING - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def http_get(url: str) -> str:
    """GET a URL with a polite UA, retrying transient network/server errors.

    Permanent HTTP errors (e.g. 404) fail immediately; transient ones (timeouts,
    connection resets, 429, 500/502/503/504) are retried with exponential backoff.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            # ~96% smaller transfers (a large course's fragment: ~130 KB -> ~5 KB)
            "Accept-Encoding": "gzip",
        })
    except ValueError as e:   # e.g. schemeless/malformed URL
        raise FetchError(f"invalid URL: {e}")
    last_err: Optional[Exception] = None
    for attempt in range(FETCH_RETRIES):
        _pace_requests()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)   # BadGzipFile -> OSError -> retried
                return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {e.code} fetching page", status=e.code)
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


def parse_drupal_settings(html: str) -> dict:
    """Extract the drupalSettings JSON blob a content page embeds."""
    m = BLOB_RE.search(html)
    if not m:
        raise FetchError("could not find the drupal-settings-json blob (page format changed?)")
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        # ValueError (superset of JSONDecodeError) also covers a JSON integer
        # literal exceeding Python 3.11+'s 4300-digit int-conversion limit.
        raise FetchError(f"could not parse the drupal-settings JSON: {e}")


def node_id_from_settings(settings: dict) -> str:
    """The section's Drupal node id, from drupalSettings.path.currentPath ("node/NNN")."""
    path = ""
    try:
        path = settings["path"]["currentPath"]
    except (KeyError, TypeError):
        pass
    m = re.fullmatch(r"node/(\d+)", str(path))
    if not m:
        raise FetchError(f"could not read the node id from currentPath {path!r}")
    return m.group(1)


def parse_enrollment(html: str) -> Snapshot:
    """Parse a section content page's HTML into a normalized enrollment snapshot.

    Pure and offline-testable: give it saved HTML and it returns the reading.
    """
    return enrollment_from_settings(parse_drupal_settings(html))


def enrollment_from_settings(settings: dict) -> Snapshot:
    """Build a normalized enrollment snapshot from a page's drupalSettings."""
    try:
        es = settings["ucb"]["enrollment"]["available"]["enrollmentStatus"]
        return _snapshot_from_status(es)
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as e:
        # Covers missing keys AND server-controlled nulls/Infinity anywhere in the blob
        # (e.g. "enrollmentStatus": null, "enrolledCount": null, "status": null).
        raise FetchError(f"could not parse enrollment JSON: {e}")


def _snapshot_from_status(es: dict) -> Snapshot:
    """Build the normalized snapshot from an enrollmentStatus dict."""
    enrolled = int(es.get("enrolledCount") or 0)
    capacity = int(es.get("maxEnroll") or 0)
    open_capacity = max(0, capacity - enrolled)          # total open seats
    open_reserved = int(es.get("openReserved") or 0)  # open seats held for a group or specific student (SID)
    # Per-group reservation breakdown (seatReservations[]): each block holds
    # maxEnroll seats for requirementGroup, of which enrolledCount are taken —
    # so "open" is what that group can still snipe. The per-group opens sum to
    # openReserved (verified across live classes, 2026-07-31).
    reservations = [
        {
            "code": str((r.get("requirementGroup") or {}).get("code") or ""),
            "description": str((r.get("requirementGroup") or {}).get("description") or ""),
            "open": max(0, int(r.get("maxEnroll") or 0) - int(r.get("enrolledCount") or 0)),
        }
        for r in es.get("seatReservations") or []
    ]
    return {
        "reservations": reservations,
        "status_code": (es.get("status") or {}).get("code", "?"),   # O / W / C
        "status_desc": (es.get("status") or {}).get("description", "?"),
        "enrolled": enrolled,
        "capacity": capacity,
        "waitlisted": int(es.get("waitlistedCount") or 0),
        "waitlist": es.get("maxWaitlist"),
        "reserved": int(es.get("reservedCount") or 0),
        "open_reserved": open_reserved,
        "open_capacity": open_capacity,
        # the seats anyone can snipe without a major/permission restriction:
        "open_unreserved": max(0, open_capacity - open_reserved),
        "ts": timestamp(),
    }


def cache_bust(url: str) -> str:
    """Append a unique query param to skip the CDN edge cache.

    classes.berkeley.edu content pages are served through a Fastly CDN + Drupal
    caches (``max-age=900``). A unique query string misses the CDN and Drupal's
    internal page cache, removing one ~15-min layer. It does NOT reach real-time:
    a deeper Drupal *dynamic render* cache at the origin (also ~15 min, keyed by
    route not query) still returns a HIT. Net effect: page staleness roughly
    halves (worst ~30 -> ~15 min), not zero — which is the detection latency in
    legacy (--no-fast-poll) mode, and just the status/code detail's lag in fast
    mode (live counts and the reserved breakdown come from uncached endpoints). Uses ``&`` if the URL already has a query.

    The nonce combines a nanosecond timestamp with random bits so repeat calls in
    the same second (or same loop iteration) still produce distinct values.
    """
    nonce = f"{time.time_ns()}{random.randint(1000, 9999)}"
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_cb={nonce}"


def fetch_enrollment(url: str, bust_cache: bool = False) -> Snapshot:
    """Fetch a section content page and return a normalized enrollment snapshot.

    With ``bust_cache`` set, a unique query param skips the CDN/edge cache to
    roughly halve the page's staleness. It does NOT reach real-time (a ~15-min
    origin render cache remains) and it adds origin load, so keep polls polite.
    """
    if "/content/" not in url:
        raise FetchError(
            "URL doesn't look like a section content page. Open your class on "
            "classes.berkeley.edu, click the specific section, and copy that URL "
            "(it looks like https://classes.berkeley.edu/content/2026-fall-...)."
        )
    return parse_enrollment(http_get(cache_bust(url) if bust_cache else url))


# One rendered reserved-seat row in the ?_wrapper_format=drupal_ajax response:
#   <span class="detail-numeral">66</span> reserved for <description> </div>
RESERVED_AJAX_RE = re.compile(
    r'detail-numeral">\s*([0-9][0-9,]{0,9})\s*</span>\s*reserved for\s*(.*?)\s*</div>',
    re.S,
)


def fetch_reserved_ajax(url: str) -> list:
    """Fetch the UNCACHED reserved-seat breakdown from the drupal_ajax variant.

    The plain content page carries the structured ``seatReservations`` JSON but is
    served from Drupal's ~15-min dynamic render cache. Appending
    ``?_wrapper_format=drupal_ajax`` renders the SAME content **uncached**
    (verified: ``x-drupal-dynamic-cache: UNCACHEABLE``, ``age: 0`` every hit), so
    the per-group open-reserved counts are real-time. The trade-off: the ajax
    response gives each block's description text and open count but NOT its
    requirement-group code, so callers enrich codes from the last plain-page read
    (see ``_with_realtime_reservations``).

    Returns a list of ``{"code": "", "description": str, "open": int}`` (``open``
    is the block's currently-open reserved seats). Raises FetchError on a bad
    response.
    """
    ajax_url = cache_bust(url) + "&_wrapper_format=drupal_ajax"
    raw = http_get(ajax_url)
    try:
        commands = json.loads(raw)
        article = next(
            c["data"] for c in commands
            if isinstance(c, dict) and c.get("command") == "insert"
            and isinstance(c.get("data"), str) and c.get("data")
        )
    except (json.JSONDecodeError, StopIteration, TypeError) as e:
        raise FetchError(f"could not read the drupal_ajax reserved response: {e}")
    # The article renders the breakdown twice (a summary + the "Current
    # Enrollment" block); requirement groups are distinct, so dedupe by
    # description keeping the first occurrence.
    rows, seen = [], set()
    for count, desc_html in RESERVED_AJAX_RE.findall(article):
        desc = html_lib.unescape(re.sub(r"\s+", " ", desc_html)).strip()
        if desc and desc not in seen:
            seen.add(desc)
            rows.append({"code": "", "description": desc,
                         "open": int(count.replace(",", ""))})
    return rows


def _with_realtime_reservations(content_snap: Optional[Snapshot], rt_rows: list) -> Snapshot:
    """Overlay real-time ajax reserved rows onto a cached-page snapshot.

    Keeps the page's status/counts but replaces the reserved breakdown with the
    uncached per-group opens, enriching each row with the requirement-group code
    from the cached page (matched by description) so numeric code tokens in
    ``reserved_groups`` keep working. ``open_reserved`` is refreshed to the live
    sum so ``open_eligible``/``merge_row_snapshot`` compute against current data.
    """
    base = dict(content_snap or {})
    code_by_desc = {
        str(r.get("description") or ""): str(r.get("code") or "")
        for r in (base.get("reservations") or [])
    }
    merged, open_reserved = [], 0
    for r in rt_rows:
        desc = r["description"]
        merged.append({"code": code_by_desc.get(desc, ""),
                       "description": desc, "open": int(r["open"])})
        open_reserved += int(r["open"])
    base["reservations"] = merged
    base["open_reserved"] = open_reserved
    return base


# --------------------------------------------------------------------------- #
# Fast polling: the uncached associated-sections fragment
# --------------------------------------------------------------------------- #
def content_slug(url: str) -> Optional[str]:
    """The section slug from a content URL ("2026-fall-compsci-61a-001-lec-001")."""
    m = re.search(r"/content/([^/?#]+)", url)
    return m.group(1) if m else None


def parse_associated_sections(html: str) -> dict:
    """Parse an associated-sections fragment into {slug: fragment row}.

    Each row carries the real-time numbers the fragment renders per section:
    open_capacity, enrolled, capacity, waitlisted, waitlist. Rows without the three
    core counts (e.g. a section rendered without enrollment data) are skipped.
    Pure and offline-testable, like parse_enrollment.
    """
    rows: dict = {}
    for chunk in html.split(FRAGMENT_ROW_SPLIT)[1:]:
        href = FRAGMENT_HREF_RE.search(chunk)
        if not href:
            continue
        nums = {
            label: int(value.replace(",", ""))
            for label, value in FRAGMENT_NUM_RE.findall(chunk)
        }
        if not all(k in nums for k in ("Open Seats", "Enrolled", "Enrollment Limit")):
            continue
        rows[href.group(1)] = {
            "open_capacity": nums["Open Seats"],
            "enrolled": nums["Enrolled"],
            "capacity": nums["Enrollment Limit"],
            "waitlisted": nums.get("Waitlisted", 0),
            "waitlist": nums.get("Waitlist limit"),
        }
    return rows


def fetch_fragment(base_url: str, node_id: str) -> dict:
    """Fetch /sections/associated/<node_id> and return its parsed rows.

    The fragment is served uncached (verified: ``x-drupal-cache: UNCACHEABLE``,
    Fastly MISS with ``age: 0`` on every hit), so no cache-busting is needed —
    every read is real-time. Remember: the response lists every section of the
    course EXCEPT ``node_id``'s own.
    """
    url = urllib.parse.urljoin(base_url, f"/sections/associated/{node_id}")
    return parse_associated_sections(http_get(url))


def merge_row_snapshot(row: dict, content_snap: Optional[Snapshot]) -> Snapshot:
    """Combine a real-time fragment row with the last full-page snapshot.

    The fragment is authoritative for the live counts (open/enrolled/capacity/
    waitlist). ``content_snap`` contributes what the fragment lacks — the status
    code, requirement-group codes, and the reserved breakdown (refreshed live
    from the uncached ajax variant on every change; see _poll_class). As a
    guard against any staleness, ``open_reserved`` is clamped into
    [0, open_capacity] before ``open_unreserved`` is derived.
    """
    open_capacity = int(row["open_capacity"])
    content_snap = content_snap or {}
    open_reserved = min(max(0, int(content_snap.get("open_reserved", 0))), open_capacity)
    return {
        "status_code": content_snap.get("status_code", "?"),
        "status_desc": content_snap.get("status_desc", "?"),
        "enrolled": int(row["enrolled"]),
        "capacity": int(row["capacity"]),
        "waitlisted": int(row["waitlisted"]),
        "waitlist": row.get("waitlist"),
        "reserved": int(content_snap.get("reserved", 0)),
        "reservations": list(content_snap.get("reservations") or []),
        "open_reserved": open_reserved,
        "open_capacity": open_capacity,
        "open_unreserved": max(0, open_capacity - open_reserved),
        "ts": timestamp(),
    }


def discover_probe(
    url: str, slug: str, watched_slugs: set, bust_cache: bool, now_ts: float
) -> tuple:
    """Find the sibling "probe" node whose fragment carries this section's row.

    Because /sections/associated/<node> omits the requested node's own section,
    we read our target through a sibling: fetch the target's content page (node
    id + a baseline snapshot), list its siblings via its own fragment, pick one
    deterministically — the first sibling NOT itself being watched, so every
    watched section of a course converges on the same probe and shares one
    fragment request per poll — and resolve that sibling's node id.

    Returns (baseline_snapshot, discovery). ``discovery`` is either
    {"node_id", "probe_node", "probe_slug", "checked_ts"} or, when the course
    has no usable sibling, {"node_id", "mode": "legacy", "checked_ts"} — the
    class then falls back to full-page polling and re-tries the fragment
    every ~15 min.
    """
    settings = parse_drupal_settings(http_get(cache_bust(url) if bust_cache else url))
    snap = enrollment_from_settings(settings)
    node_id = node_id_from_settings(settings)
    discovery = {"node_id": node_id, "checked_ts": now_ts}

    try:
        rows = fetch_fragment(url, node_id)
    except FetchError as e:
        # The fragment tier itself is unavailable (endpoint moved/blocked/erroring).
        # The content page is healthy, so degrade to full-page polling and retry
        # discovery every ~15 min — never let this look like a dead section URL.
        log.warning("associated-sections fragment unavailable for %s (%s); using "
                    "full-page polling for now", slug, e)
        discovery["mode"] = "legacy"
        return snap, discovery

    probe_slug = next((s for s in rows if s not in watched_slugs), None)
    if probe_slug is None:
        probe_slug = next(iter(rows), None)
    if probe_slug is None:
        discovery["mode"] = "legacy"   # single-section course: no sibling to probe
        return snap, discovery

    try:
        probe_url = urllib.parse.urljoin(url, f"/content/{probe_slug}")
        probe_settings = parse_drupal_settings(http_get(probe_url))
        discovery["probe_node"] = node_id_from_settings(probe_settings)
        discovery["probe_slug"] = probe_slug
    except FetchError as e:
        log.warning("probe discovery via %s failed (%s); using full-page polling "
                    "for now", probe_slug, e)
        discovery["mode"] = "legacy"
    return snap, discovery


def status_line(name: str, s: Snapshot, groups: list = ()) -> str:
    """A one-line human-readable summary of a snapshot (no timestamp; the log adds it)."""
    tag = STATUS_TAGS.get(s["status_code"], s["status_code"])
    return (
        f"{name}: {tag} | "
        f"enrolled {s['enrolled']}/{s['capacity']} | "
        f"open {s['open_capacity']} ({open_eligible(s, groups)} eligible, "
        f"{s['open_unreserved']} unreserved"
        f"{', ' + str(s['open_reserved']) + ' reserved' if s['open_reserved'] else ''}) | "
        f"waitlist {s['waitlisted']}"
        f"{'/' + str(s['waitlist']) if s['waitlist'] is not None else ''}"
    )


# --------------------------------------------------------------------------- #
# Alert detection (diff between the previous and current snapshot)
# --------------------------------------------------------------------------- #
def reservation_matches(reservation: dict, groups: list) -> bool:
    """Does one seatReservations entry match the user's group tokens?

    A token that is all digits is compared against the requirement-group CODE
    (exactly, ignoring leading zeros). Any other token is a case-insensitive
    SUBSTRING match against the group description — e.g. "Statistics Majors"
    is matched by "statistics", and the long EECS/CS/ECE description by
    "Computer Science". "Students with Enrollment Permission" seats (held for
    specific students by SID) never match by text, only by their explicit code.

    A token starting with "!" is an EXCLUSION: a reservation it hits never
    counts, even if an include token also hits. This narrows broad tokens —
    e.g. ["Computer Science", "!Transfer"] counts CS-major blocks but not
    "Computer Science Majors: New Transfer Students".
    """
    code = str(reservation.get("code") or "")
    desc = str(reservation.get("description") or "").lower()
    # A block held for specific students by SID — matchable only by explicit code,
    # never by text. Detected by code (plain page) or description (ajax breakdown).
    is_permission = code == ENROLLMENT_PERMISSION_CODE or ENROLLMENT_PERMISSION_TEXT in desc

    def hits(token: str) -> bool:
        if token.isdigit():
            return code == token or (code.lstrip("0") == token.lstrip("0") != "")
        return not is_permission and token.lower() in desc

    tokens = [str(g).strip() for g in groups]
    if any(hits(t[1:].strip()) for t in tokens if t.startswith("!") and t[1:].strip()):
        return False
    return any(hits(t) for t in tokens if t and not t.startswith("!"))


def open_eligible(snap: Snapshot, groups: list) -> int:
    """Seats snipeable by *this* user: unreserved + reserved for their groups.

    With no configured groups this is just the unreserved count. The reserved
    portion comes from the snapshot's reservations (refreshed live from the
    uncached ajax variant whenever the counts move) and is clamped to the
    snapshot's open_reserved as a guard against inconsistent reads.
    """
    open_unreserved = int(snap.get("open_unreserved", 0))
    groups = [g for g in (groups or []) if str(g).strip()]
    if not groups:
        return open_unreserved
    open_eligible_reserved = sum(int(r.get("open") or 0)
                                 for r in snap.get("reservations") or []
                                 if reservation_matches(r, groups))
    return open_unreserved + min(open_eligible_reserved, int(snap.get("open_reserved", 0)))


def open_waitlist(snap: Snapshot) -> Optional[int]:
    """Open spots on the waitlist: waitlist (max) minus currently waitlisted.

    Returns None when the section exposes no waitlist max, since we then can't tell
    how many spots — if any — are open to get in line.
    """
    waitlist = snap.get("waitlist")
    if waitlist is None:
        return None
    return max(0, int(waitlist) - int(snap.get("waitlisted", 0)))


def detect_alerts(
    prev: Optional[Snapshot], curr: Snapshot, alert_on: list, name: str,
    groups: list = (),
) -> list:
    """Return a list of (kind, message) alerts triggered by this change.

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

    # 1) TOTAL open seats increased — a spot just freed. Reserved seats count too,
    #    because a reserved seat can still be open to you. Opt-in.
    #    Fires even while status is still "Waitlist": that IS the drop window.
    if "*" in alert_on:
        prev_open_capacity = 0 if first_seen else prev.get("open_capacity", 0)
        if curr["open_capacity"] > prev_open_capacity and curr["open_capacity"] > 0:
            res = f", {curr['open_reserved']} reserved" if curr["open_reserved"] else ""
            alerts.append((
                "*",
                f"{name}: {curr['open_capacity']} open seat(s) "
                f"({curr['enrolled']}/{curr['capacity']}{res}) — a spot just freed up, "
                f"check CalCentral now!",
            ))

    # 2) Max capacity increased — the course was expanded. Opt-in. Skipped on first
    #     sight, which is a baseline reading, not an expansion event.
    if "capacity" in alert_on and not first_seen:
        if curr["capacity"] > prev.get("capacity", 0):
            alerts.append((
                "capacity",
                f"{name}: capacity grew {prev['capacity']} -> {curr['capacity']} "
                f"({curr['enrolled']} enrolled) — the course was expanded.",
            ))

    # 3) RESERVED open seats increased — a seat opened that's held for some group.
    #     Snipeable only if that group is one of yours; otherwise not for you. Opt-in.
    if "reserved" in alert_on:
        prev_open_reserved = 0 if first_seen else prev.get("open_reserved", 0)
        if curr["open_reserved"] > prev_open_reserved and curr["open_reserved"] > 0:
            alerts.append((
                "reserved",
                f"{name}: {curr['open_reserved']} reserved open seat(s) "
                f"({curr['enrolled']}/{curr['capacity']}) — held for a group; snipeable "
                f"only if it's yours.",
            ))

    # 4) UNRESERVED open seats increased — a seat snipeable by anyone. Opt-in, for
    #    when you're not in the reserved group and want to ignore reserved seats.
    if "unreserved" in alert_on:
        prev_open_unreserved = 0 if first_seen else prev.get("open_unreserved", 0)
        if curr["open_unreserved"] > prev_open_unreserved and curr["open_unreserved"] > 0:
            alerts.append((
                "unreserved",
                f"{name}: {curr['open_unreserved']} unreserved open seat(s) "
                f"({curr['enrolled']}/{curr['capacity']}) — snipeable by anyone, enroll now!",
            ))

    # 5) The count of open seats snipeable by THIS user increased — unreserved plus
    #     seats reserved for a group in their "reserved_groups". The default.
    if "eligible" in alert_on:
        prev_open_eligible = 0 if first_seen else open_eligible(prev, groups)
        curr_open_eligible = open_eligible(curr, groups)
        if curr_open_eligible > prev_open_eligible and curr_open_eligible > 0:
            open_eligible_reserved = curr_open_eligible - curr["open_unreserved"]
            detail = f"{curr['open_unreserved']} unreserved"
            if open_eligible_reserved > 0:
                detail += f" + {open_eligible_reserved} reserved for your group(s)"
            alerts.append((
                "eligible",
                f"{name}: {curr_open_eligible} seat(s) YOU can snipe ({detail}; "
                f"{curr['enrolled']}/{curr['capacity']}) — enroll now!",
            ))

    # 6) Section status flipped to Open. Informational — say whether it's really
    #    snipeable or Open-but-all-reserved.
    if "status" in alert_on:
        became_open = (first_seen and curr["status_code"] == "O") or (
            not first_seen and prev["status_code"] != "O" and curr["status_code"] == "O"
        )
        if became_open:
            if curr["open_unreserved"] > 0:
                msg = (f"{name} is now OPEN with {curr['open_unreserved']} snipeable "
                       f"seat(s) — go!")
            elif curr["open_reserved"] > 0:
                msg = (f"{name} shows OPEN but all {curr['open_capacity']} open seat(s) are "
                       f"RESERVED (need major/permission) — may not be snipeable.")
            else:
                msg = f"{name} is now OPEN."
            alerts.append(("status", msg))

    # 7) A spot on the waitlist opened — the waitlist had no room and now does, so you
    #    can get in line. The waitlist max is live from the uncached fragment
    #    ("Waitlist limit"), so open_waitlist is known; distinct from the line merely
    #    advancing (which the shrinking waitlisted count would show). Opt-in.
    if "waitlist" in alert_on and not first_seen:
        prev_open_waitlist = open_waitlist(prev)
        curr_open_waitlist = open_waitlist(curr)
        if prev_open_waitlist == 0 and curr_open_waitlist and curr_open_waitlist > 0:
            of_max = f"/{curr['waitlist']}" if curr["waitlist"] is not None else ""
            alerts.append((
                "waitlist",
                f"{name}: a waitlist spot opened — {curr_open_waitlist} now free "
                f"(waitlisted {curr['waitlisted']}{of_max}) — get in line.",
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
) -> tuple:
    """Decide which alerts to actually fire, honoring cooldown and repeat-while-open.

    Returns (fired_alerts, updated_alert_times). ``alert_times`` maps an alert kind
    to the epoch time it last fired for this class.

    - cooldown: suppress a repeat of the same kind within this many seconds (anti-spam).
    - repeat_seconds: if > 0 and seats stay open, re-alert every this many seconds so
      the notification doesn't go silent while a seat is still open.
    """
    candidates = detect_alerts(prev, curr, alert_on, name, groups)

    if repeat_seconds > 0:
        already = {kind for kind, _ in candidates}
        still_open = (
            ("*", curr["open_capacity"], "open seat(s)"),
            ("reserved", curr["open_reserved"], "reserved open seat(s)"),
            ("unreserved", curr["open_unreserved"], "unreserved open seat(s)"),
            ("eligible", open_eligible(curr, groups), "seat(s) you can snipe"),
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
                    f"{name}: still {count} {noun} "
                    f"({curr['enrolled']}/{curr['capacity']}) — reminder, still open.",
                ))

    fired: list = []
    new_times = dict(alert_times)
    for kind, message in candidates:
        last = alert_times.get(kind)
        if cooldown > 0 and last is not None and (now_ts - last) < cooldown:
            log.debug("suppressed %s alert for %s (cooldown)", kind, name)
            continue
        fired.append((kind, message))
        new_times[kind] = now_ts
    return fired, new_times


def _strip_name_prefix(name: str, message: str) -> str:
    """Drop a leading ``"{name}: "`` / ``"{name} "`` so bodies can be merged."""
    if message.startswith(name):
        return message[len(name):].lstrip(": ")
    return message


def coalesce_alerts(name: str, fired: list) -> Optional[str]:
    """Merge one poll's fired ``(kind, message)`` alerts into a single message.

    Returns ``None`` when nothing fired. A lone alert is returned verbatim; when
    several kinds fire for the same class in one poll they're stripped of their
    duplicated name prefix and joined, so the user gets ONE clear ping listing
    everything that changed instead of one notification per kind.
    """
    if not fired:
        return None
    if len(fired) == 1:
        return fired[0][1]
    parts: list = []
    for _kind, message in fired:
        body = _strip_name_prefix(name, message)
        if body not in parts:
            parts.append(body)
    return f"{name}: " + " | ".join(parts)


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
                script += f' sound name "{esc(str(sound))}"'
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
    if env_name:
        return os.environ.get(env_name, "")
    return ""


def notify_email(cfg: Optional[dict], subject: str, body: str) -> None:
    """Optional SMTP email. Password comes from ``password`` (inline) or ``password_env``."""
    if not cfg or not isinstance(cfg, dict):   # non-dict shapes are rejected by
        return                                 # validation; never crash here

    pw = resolve_secret(cfg, "password", "password_env")
    if not pw:
        log.warning("email skipped: set notify.email.password or the %r env var",
                    cfg.get("password_env"))
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg["from"]
        msg["To"] = cfg["to"]
        msg.set_content(body)
        port = int(cfg.get("port", 587))
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
    if not cfg or not isinstance(cfg, dict):   # see notify_email
        return
    token = resolve_secret(cfg, "bot_token", "bot_token_env")
    if not token:
        log.warning("telegram skipped: set notify.telegram.bot_token or the %r env var",
                    cfg.get("bot_token_env"))
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": cfg["chat_id"], "text": text}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"User-Agent": USER_AGENT})
        urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT).read()
    except (urllib.error.URLError, OSError, KeyError, ValueError,
            http.client.HTTPException) as e:
        # HTTPException (e.g. IncompleteRead/BadStatusLine): response truncated
        # mid-read — a channel failure must never kill the watch loop.
        log.warning("telegram failed: %s", e)


def fire_notifications(notify_cfg: dict, title: str, message: str) -> None:
    """Send an alert across every enabled channel, plus the console/log."""
    log.warning(">>> ALERT: %s", message)
    if notify_cfg.get("desktop", True):
        notify_desktop(title, message, sound=notify_cfg.get("sound_name", DEFAULT_SOUND))
    notify_email(notify_cfg.get("email"), title, message)
    notify_telegram(notify_cfg.get("telegram"), f"{title}\n{message}")


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


def state_entry(state: dict, url: str) -> tuple:
    """Return (prev_snapshot, alert_times) for a URL, migrating older flat entries."""
    entry = state.get(url)
    if isinstance(entry, dict) and "snapshot" in entry:
        return entry.get("snapshot"), dict(entry.get("alert_times") or {})
    if isinstance(entry, dict) and "status_code" in entry:  # pre-0.2 flat snapshot
        return entry, {}
    return None, {}


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
    except json.JSONDecodeError as e:
        sys.exit(f"Config at {path} is not valid JSON: {e}")
    if not isinstance(cfg, dict):
        sys.exit(f"Config at {path} must be a JSON object.")

    cfg.setdefault("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    cfg.setdefault("classes", [])
    # Normalize class URLs: strip in-page #anchors (urllib drops everything
    # after '#' from the request, which would silently turn the ?_cb= and
    # &_wrapper_format= suffixes into no-ops) and stray whitespace.
    for cls in cfg["classes"] if isinstance(cfg["classes"], list) else []:
        if isinstance(cls, dict) and isinstance(cls.get("url"), str):
            cls["url"] = cls["url"].split("#", 1)[0].strip()
    cfg.setdefault("reserved_groups", [])
    # Default: alert on the open seats THIS user can snipe — unreserved plus, if
    # "reserved_groups" is set, reserved for their groups. With no groups configured
    # that gracefully means unreserved only. An explicit "alert_on" always wins
    # (e.g. ["*"] to ping on ANY open seat).
    cfg.setdefault("alert_on", ["eligible"])
    cfg.setdefault("alert_cooldown_seconds", 0)
    cfg.setdefault("repeat_while_open_seconds", 0)
    cfg.setdefault("bust_cache", True)
    cfg.setdefault("fast_poll", True)
    cfg.setdefault("content_refresh_seconds", CONTENT_REFRESH_SECONDS)
    cfg.setdefault("notify", {})
    # Guard the setdefault so a non-dict "notify" (e.g. null) reaches
    # validate_config's friendly "notify must be an object" error instead of
    # crashing here with an AttributeError.
    if isinstance(cfg["notify"], dict):
        cfg["notify"].setdefault("desktop", True)

    try:
        validate_config(cfg)
    except ConfigError as e:
        sys.exit(f"Config error in {path}:\n{e}")
    return cfg


def _validate_reserved_groups(value: Any, where: str, errors: list) -> None:
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
    if not isinstance(value, list) or not value:
        errors.append(f"{where} must be a non-empty list, e.g. [\"eligible\"].")
        return
    for kind in value:
        if kind not in KNOWN_ALERT_KINDS:
            errors.append(
                f"{where} has unknown alert {kind!r}; valid: {list(KNOWN_ALERT_KINDS)}."
            )


REQUIRED_EMAIL_FIELDS = ("host", "username", "from", "to")


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
        if not (email.get("password") or email.get("password_env")):
            errors.append(
                "notify.email needs a \"password\" (inline) or a \"password_env\" (env-var name)."
            )
        port = email.get("port", 587)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            errors.append(
                "notify.email.port must be an integer port number (e.g. 587); "
                "omit it to use the default."
            )

    telegram = notify.get("telegram")
    if isinstance(telegram, dict):
        if not telegram.get("chat_id"):
            errors.append("notify.telegram.chat_id is required when telegram is enabled.")
        if not (telegram.get("bot_token") or telegram.get("bot_token_env")):
            errors.append(
                "notify.telegram needs a \"bot_token\" (inline) or a \"bot_token_env\" "
                "(env-var name)."
            )


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

    if not isinstance(cfg.get("bust_cache", False), bool):
        errors.append("bust_cache must be true or false.")

    if not isinstance(cfg.get("fast_poll", True), bool):
        errors.append("fast_poll must be true or false.")

    if _bad_number(cfg.get("content_refresh_seconds", CONTENT_REFRESH_SECONDS), 0):
        errors.append("content_refresh_seconds must be a positive number.")

    _validate_alert_on(cfg.get("alert_on"), "alert_on", errors)
    _validate_reserved_groups(cfg.get("reserved_groups", []), "reserved_groups", errors)

    classes = cfg.get("classes")
    if not isinstance(classes, list):
        errors.append("classes must be a list.")
    else:
        for i, cls in enumerate(classes):
            where = f"classes[{i}]"
            if not isinstance(cls, dict):
                errors.append(f"{where} must be an object with a \"url\".")
                continue
            url = cls.get("url")
            if not isinstance(url, str) or "/content/" not in url:
                errors.append(
                    f"{where}.url must be a section content URL containing '/content/'."
                )
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
        fileh = logging.FileHandler(logfile, encoding="utf-8")
        fileh.setFormatter(fmt)
        log.addHandler(fileh)


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def _handle_fetch_error(
    state: dict, url: str, name: str, err: FetchError, notify_cfg: dict
) -> None:
    """Log a fetch failure; escalate a *persistent* 404 to a loud one-time alert.

    A single 404 can be a transient hiccup, but a section page that keeps 404ing
    usually means the section id changed or the course ended — a quiet per-poll
    skip would hide that. We count consecutive 404s in state and, once past the
    threshold, fire a single notification per class telling the user to fix the URL.
    """
    if err.status != 404:
        log.warning("%s: ! %s", name, err)
        return
    entry = state.get(url)
    if not isinstance(entry, dict):
        entry = {}
    missing = int(entry.get("missing_count", 0)) + 1
    entry["missing_count"] = missing
    if missing >= PERSISTENT_404_THRESHOLD and not entry.get("missing_alerted"):
        entry["missing_alerted"] = True
        fire_notifications(
            notify_cfg, "cal-seat-sniper",
            f"{name}: section page has returned 404 {missing} times in a row — "
            f"the section id may have changed or the course ended. Update or "
            f"remove this URL in your config.\n{url}",
        )
    else:
        log.warning("%s: ! %s (404 x%d)", name, err, missing)
    state[url] = entry


def _degraded_round(
    url: str, name: str, entry: dict, bust_cache: bool, now_ts: float
) -> Snapshot:
    """One poll's snapshot when the fragment tier failed for this class.

    Alert baselines in fast mode are built from live fragment numbers; letting a
    ~15-min-stale page reading replace one can both fire spurious alerts and
    swallow a real seat-drop when the fragment recovers. So a degraded round
    repeats the last live reading when one exists, and only classes that never
    got fragment data fall back to a page fetch. After
    FRAGMENT_FAIL_LEGACY_THRESHOLD consecutive degraded rounds the class
    switches to consistent full-page (legacy) polling and re-tries the fragment
    tier every ~15 min via the normal discovery retry.
    """
    fails = int(entry.get("frag_fail_count", 0)) + 1
    entry["frag_fail_count"] = fails
    if fails >= FRAGMENT_FAIL_LEGACY_THRESHOLD:
        discovery = entry.get("discovery") or {}
        entry["discovery"] = {"node_id": discovery.get("node_id"),
                              "mode": "legacy", "checked_ts": now_ts}
        entry.pop("frag_fail_count", None)
        log.warning("%s: fragment tier failed %d polls in a row — switching to "
                    "full-page polling (will re-try the fragment every ~15 min)",
                    name, fails)
        return fetch_enrollment(url, bust_cache=bust_cache)
    prev_row = entry.get("fragment_row")
    if prev_row is not None:
        log.warning("%s: degraded poll — repeating the last live reading", name)
        return merge_row_snapshot(prev_row, (entry.get("content") or {}).get("snapshot"))
    return fetch_enrollment(url, bust_cache=bust_cache)


def _poll_class(
    url: str,
    name: str,
    entry: dict,
    watched_slugs: set,
    frag_cache: dict,
    bust_cache: bool,
    fast_poll: bool,
    content_refresh: float,
    now_ts: float,
) -> Snapshot:
    """Produce this poll's snapshot for one class, updating ``entry`` in place.

    Fast path: read the class's real-time numbers out of its probe's uncached
    fragment; when those numbers move, fetch the live reserved breakdown from
    the uncached ajax variant. The full (cached) content page is fetched only
    for the O/W/C status + requirement-group codes — on first sight or when
    older than ``content_refresh``. Falls back to a plain full-page fetch (legacy
    behavior) whenever the fragment tier is unavailable.

    ``frag_cache`` memoizes fragment fetches per poll round keyed by probe
    node (None = the fetch failed this round), so several watched sections of
    one course cost a single fragment request.
    """
    slug = content_slug(url)
    if not (fast_poll and slug):
        return fetch_enrollment(url, bust_cache=bust_cache)

    # Ensure we know how to reach this class's row (probe discovery).
    discovery = entry.get("discovery") or {}
    needs_discovery = not discovery.get("probe_node") if "mode" not in discovery else (
        now_ts - discovery.get("checked_ts", 0) >= DISCOVERY_RETRY_SECONDS
    )
    if needs_discovery:
        snap, discovery = discover_probe(url, slug, watched_slugs, bust_cache, now_ts)
        entry["discovery"] = discovery
        entry["content"] = {"snapshot": snap, "ts": now_ts}
        entry["fragment_row"] = None
        entry.pop("frag_fail_count", None)
        if discovery.get("probe_node"):
            log.debug("%s: probing via sibling %s (node %s)", name,
                      discovery["probe_slug"], discovery["probe_node"])
            # Read the live row through the new probe right away, so even the
            # discovery round alerts off fragment numbers and the baseline
            # snapshot is never a possibly-stale page reading.
            try:
                rows = frag_cache.get(discovery["probe_node"])
                if rows is None:
                    rows = fetch_fragment(url, discovery["probe_node"])
                    frag_cache[discovery["probe_node"]] = rows
                row = rows.get(slug)
                if row is not None:
                    entry["fragment_row"] = row
                    return merge_row_snapshot(row, snap)
            except FetchError as e:
                log.warning("%s: fragment read after discovery failed (%s); "
                            "using the page baseline this round", name, e)
        return snap

    if discovery.get("mode") == "legacy":
        return fetch_enrollment(url, bust_cache=bust_cache)

    # Read our row from the probe's uncached fragment (shared per course/round).
    probe_node = discovery["probe_node"]
    if probe_node not in frag_cache:
        try:
            frag_cache[probe_node] = fetch_fragment(url, probe_node)
        except FetchError as e:
            frag_cache[probe_node] = None
            if e.status == 404:   # probe section vanished — re-discover next poll
                entry.pop("discovery", None)
            log.warning("%s: fragment fetch failed (%s)", name, e)
    rows = frag_cache[probe_node]
    if rows is None:
        return _degraded_round(url, name, entry, bust_cache, now_ts)
    row = rows.get(slug)
    if row is None:
        # Our section is missing from the probe's fragment (section list changed
        # or the page format shifted) — re-discover next poll.
        entry.pop("discovery", None)
        log.warning("%s: section not found in the probe fragment; re-discovering",
                    name)
        return _degraded_round(url, name, entry, bust_cache, now_ts)

    prev_row = entry.get("fragment_row")
    entry["fragment_row"] = row
    entry.pop("frag_fail_count", None)
    changed = prev_row is not None and row != prev_row

    content = entry.get("content") or {}
    content_snap = content.get("snapshot")
    stale = now_ts - content.get("ts", 0) >= content_refresh
    # Refresh the cached-page detail (O/W/C status + requirement-group codes) on
    # first sight and on the slow cadence — status changes are rare.
    if content_snap is None or stale:
        try:
            content_snap = fetch_enrollment(url, bust_cache=bust_cache)
            entry["content"] = {"snapshot": content_snap, "ts": now_ts}
        except FetchError as e:
            if content_snap is None:
                raise
            log.warning("%s: full-page refresh failed (%s); merging fragment with "
                        "the last page data", name, e)
    # On a detected change, pull the reserved breakdown from the UNCACHED ajax
    # variant so eligible-seat detection is real-time, not gated by the ~15-min
    # page cache. Falls back to the cached breakdown if the ajax fetch fails.
    detail_snap = content_snap
    if changed:
        try:
            detail_snap = _with_realtime_reservations(
                content_snap, fetch_reserved_ajax(url))
            # Keep the CURRENT stored page timestamp (which the refresh block
            # above may have just set to now_ts) — re-reading the pre-refresh
            # capture would pin the ts in the past and make every churny poll
            # re-trigger a full-page fetch.
            entry["content"] = {"snapshot": detail_snap,
                                "ts": (entry.get("content") or {}).get("ts", now_ts)}
        except FetchError as e:
            log.warning("%s: real-time reserved fetch failed (%s); using the "
                        "cached breakdown", name, e)
    return merge_row_snapshot(row, detail_snap)


def poll_once(
    classes: list,
    notify_cfg: dict,
    default_alert_on: list,
    state: dict,
    cooldown: float = 0,
    repeat_seconds: float = 0,
    bust_cache: bool = False,
    fast_poll: bool = False,
    content_refresh: float = CONTENT_REFRESH_SECONDS,
    default_groups: list = (),
) -> None:
    """Poll every class once, log its status, and fire any triggered alerts.

    ``fast_poll`` defaults to False here so direct callers get the simple
    legacy path; run_loop/main pass the config value, whose default is True.
    """
    now_ts = time.time()
    watched_slugs = {content_slug(cls["url"]) for cls in classes} - {None}
    frag_cache: dict = {}
    for cls in classes:
        url = cls["url"]
        name = cls.get("name", url)
        alert_on = cls.get("alert_on", default_alert_on)
        groups = cls.get("reserved_groups", default_groups)
        old = state.get(url)
        # Carry forward an existing nested entry; a bare flat snapshot starts fresh
        # (its previous reading is still honored below via state_entry).
        entry = dict(old) if isinstance(old, dict) and "status_code" not in old else {}
        try:
            try:
                snap = _poll_class(url, name, entry, watched_slugs, frag_cache,
                                   bust_cache, fast_poll, content_refresh, now_ts)
            except FetchError as e:
                _handle_fetch_error(state, url, name, e, notify_cfg)
                continue
            log.info(status_line(name, snap, groups))
            prev_snap, alert_times = state_entry(state, url)
            fired, new_times = evaluate_alerts(
                prev_snap, snap, alert_on, name, alert_times, now_ts, cooldown,
                repeat_seconds, groups
            )
            message = coalesce_alerts(name, fired)
            if message is not None:
                fire_notifications(notify_cfg, "cal-seat-sniper", message)
            # A successful poll clears any 404 bookkeeping but keeps discovery data.
            entry.pop("missing_count", None)
            entry.pop("missing_alerted", None)
            entry["snapshot"] = snap
            entry["alert_times"] = new_times
            state[url] = entry
        except Exception:
            # Boundary guard: nothing — not even a hand-corrupted state entry —
            # may kill the watch loop. Drop this class's saved state (it will
            # re-discover cleanly next poll) and keep watching everything else.
            log.exception("%s: unexpected error this poll — resetting this "
                          "class's saved state and continuing", name)
            state.pop(url, None)


def run_loop(cfg: dict, state_path: str) -> None:
    """Poll on a loop until interrupted, persisting state after every round."""
    state = load_state(state_path)
    interval = cfg["poll_interval_seconds"]
    classes = cfg["classes"]
    if not classes:
        sys.exit("No classes configured. Add some to your config's \"classes\" list.")
    fast_poll = cfg.get("fast_poll", True)
    polite_min = MIN_POLITE_INTERVAL_FAST if fast_poll else MIN_POLITE_INTERVAL
    if interval < polite_min:
        log.warning("poll interval %ss is below the polite minimum of %ss — "
                    "please be gentle with the server.", interval, polite_min)
    log.info("Watching %d class(es) every ~%ss. Ctrl-C to stop.",
             len(classes), interval)
    cooldown = cfg.get("alert_cooldown_seconds", 0)
    repeat_seconds = cfg.get("repeat_while_open_seconds", 0)
    bust_cache = cfg.get("bust_cache", False)
    content_refresh = cfg.get("content_refresh_seconds", CONTENT_REFRESH_SECONDS)
    if fast_poll:
        log.info("fast polling ON — seat/waitlist counts (uncached fragment) and "
                 "the reserved breakdown (uncached ajax, on changes) are "
                 "real-time; detection ≈ the poll interval. Only O/W/C status "
                 "flips + requirement-group codes ride the full-page refresh "
                 "(every ~%ss; pages are ~15-min cached).", int(content_refresh))
    elif bust_cache:
        log.info("cache-busting ON — skips the CDN edge cache (~halves detection "
                 "lag; not real-time — a ~15-min origin cache remains). Keep polls polite.")
    try:
        while True:
            poll_once(classes, cfg["notify"], cfg["alert_on"], state,
                      cooldown, repeat_seconds, bust_cache,
                      fast_poll, content_refresh,
                      cfg.get("reserved_groups", []))
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
    requirement-group code, straight from the live page.
    """
    if not classes:
        log.info("No classes configured.")
        return
    for cls in classes:
        name = cls.get("name", cls["url"])
        try:
            snap = fetch_enrollment(cls["url"], bust_cache=True)
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
                    "a group" if r["code"] == ENROLLMENT_PERMISSION_CODE else "")
            log.info('    %3d open | code %s | "%s"%s',
                     r["open"], r["code"], r["description"], note)


def config_from_url(url: str) -> dict:
    """Build an in-memory config that watches a single URL with default settings."""
    url = url.split("#", 1)[0].strip()   # same normalization as load_config
    return {
        "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
        "classes": [{"name": url.rsplit("/", 1)[-1], "url": url}],
        "notify": {"desktop": True},
        "alert_on": ["eligible"],
        "reserved_groups": [],
        "alert_cooldown_seconds": 0,
        "repeat_while_open_seconds": 0,
        "bust_cache": True,
        "fast_poll": True,
        "content_refresh_seconds": CONTENT_REFRESH_SECONDS,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="UC Berkeley class-seat watcher")
    ap.add_argument("--version", action="version",
                    version=f"cal-seat-sniper {VERSION}")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="path to a config file (default configs/config.json)")
    ap.add_argument("--state", default=None,
                    help="path to the state file (default: the states/ file "
                         "paired with the config name, e.g. configs/config-x.json "
                         "-> states/state-x.json)")
    ap.add_argument("--url", help="watch a single class URL with default settings")
    ap.add_argument("--interval", type=int, metavar="SECONDS",
                    help="override poll_interval_seconds")
    ap.add_argument("--no-fast-poll", dest="fast_poll", action="store_false",
                    default=None,
                    help="disable the real-time fragment tier and poll full "
                         "pages only (v0.2 behavior)")
    ap.add_argument("--bust-cache", dest="bust_cache", action="store_true",
                    default=None,
                    help="bust the CDN edge cache on full-page fetches (~halves "
                         "their staleness; pages remain ~15-min cached at the "
                         "origin); mostly useful with --no-fast-poll")
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
        # One-time migration: an older version kept state files in the repo root;
        # move a straggler into states/ so a restart doesn't re-alert everything.
        legacy = os.path.join(HERE, os.path.basename(args.state))
        if not os.path.exists(args.state) and os.path.isfile(legacy):
            try:
                os.makedirs(os.path.dirname(args.state), exist_ok=True)
                os.replace(legacy, args.state)
                log.info("migrated state %s -> %s", legacy, args.state)
            except OSError as e:
                log.warning("could not migrate legacy state %s: %s", legacy, e)

    cfg = config_from_url(args.url) if args.url else load_config(args.config)

    if args.test_notify:
        notify = cfg.get("notify")
        if not isinstance(notify, dict):
            notify = {"desktop": True}
        fire_notifications(notify, "cal-seat-sniper",
                           "Test notification — it works!")
        return


    if args.interval is not None:
        if args.interval <= 0:
            sys.exit("--interval must be a positive number of seconds.")
        cfg["poll_interval_seconds"] = args.interval
    if args.bust_cache:
        cfg["bust_cache"] = True
    if args.fast_poll is False:
        cfg["fast_poll"] = False

    if args.list:
        classes = cfg["classes"]
        if not classes:
            log.info("No classes configured.")
        for cls in classes:
            log.info("%s  ->  %s", cls.get("name", "(unnamed)"), cls["url"])
        return

    if args.show_reserved:
        show_reserved(cfg["classes"])
        return

    if args.once:
        state = load_state(args.state)
        poll_once(cfg["classes"], cfg["notify"], cfg["alert_on"], state,
                  cfg.get("alert_cooldown_seconds", 0),
                  cfg.get("repeat_while_open_seconds", 0),
                  cfg.get("bust_cache", False),
                  cfg.get("fast_poll", True),
                  cfg.get("content_refresh_seconds", CONTENT_REFRESH_SECONDS),
                  cfg.get("reserved_groups", []))
        save_state(args.state, state)
    else:
        run_loop(cfg, args.state)


if __name__ == "__main__":
    main()
