#!/usr/bin/env python3
"""Offline unit tests for cal-seat-sniper.

Run from the app dir:  python3 -m unittest discover -s tests
                  or:  python3 -m pytest tests/test_snipe.py -q

No network is used: enrollment reads go through BerkeleyTime's GraphQL endpoint,
and every test that would touch it monkeypatches ``snipe.http_post_json`` (or
``snipe.fetch_berkeleytime_batch``) to return canned JSON. Snapshots are built
either from an inline dict of the Snapshot shape or via ``snipe._snapshot_from_bt``
on a canned BerkeleyTime ``latest`` object.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snipe  # noqa: E402

# A section is self-contained: each class entry carries year/semester/subject/
# number/section (class-schedule-slug order), not a URL or a shared term. These
# fixtures spread into class dicts / configs and give the derived coords + state key.
SECTION = {"year": 2026, "semester": "Fall", "subject": "COMPSCI",
           "number": "61A", "section": "001"}
COORDS = {"year": 2026, "semester": "Fall", "subject": "COMPSCI",
          "courseNumber": "61A", "sectionNumber": "001"}
KEY = "2026-fall-compsci-61a-001"

# A real BerkeleyTime ``latest`` enrollment object (CS 161-style: 41 open, all
# reserved). Used to build both canned API responses and Snapshots.
BT_LATEST = {
    "endTime": "2026-08-02T17:30:11.507Z",
    "granularitySeconds": 900,
    "status": "O",
    "enrolledCount": 479,
    "maxEnroll": 520,
    "waitlistedCount": 289,
    "maxWaitlist": 300,
    "reservedCount": 41,
    "openReserved": 41,
    "seatReservationCount": [
        {"enrolledCount": 244, "maxEnroll": 244,
         "requirementGroup": {"code": "001600", "description": (
             "Undergraduate Students: Electrical Engineering & Computer Science, "
             "Computer Science, and Electrical & Computer Engineering Majors")}},
        {"enrolledCount": 0, "maxEnroll": 41,
         "requirementGroup": {"code": "000055",
                              "description": "Students with Enrollment Permission"}},
        {"enrolledCount": 187, "maxEnroll": 187,
         "requirementGroup": {"code": "001088",
                              "description": "Undergraduate Data Science Majors"}},
    ],
}


def bt_batch_response(latests):
    """Wrap ``latest`` objects as an aliased batch response (s0..sN), as
    fetch_berkeleytime_batch expects. A None entry becomes a not-found section."""
    data = {f"s{i}": ({"latest": lt} if lt is not None else None)
            for i, lt in enumerate(latests)}
    return json.dumps({"data": data})


GOOD_BATCH_RESPONSE = bt_batch_response([BT_LATEST])


def snap(**over):
    """A minimal snapshot with sensible defaults, overridable per test."""
    base = {
        "status_code": "W", "reservations": [],
        "enrolled": 100, "capacity": 100, "waitlisted": 20, "waitlist": 100,
        "open_reserved": 0, "open_capacity": 0, "open_unreserved": 0,
        "source_ts": None,
    }
    base.update(over)
    return base


def _matches(reservation, groups):
    """Test shim for the reserved-matching semantics: production splits the
    group tokens once in open_eligible, so compose the two helpers here."""
    return snipe._reservation_hits(reservation, *snipe._split_group_tokens(groups))


# --------------------------------------------------------------------------- #
# BerkeleyTime data source
# --------------------------------------------------------------------------- #
class ClassCoordsTest(unittest.TestCase):
    """class_coords builds BerkeleyTime query coords from a self-contained class entry."""

    def test_happy_path_uppercases(self):
        # subject/number/section are upper-cased; the year may be int or str.
        cls = {"year": 2026, "semester": "fall", "subject": "compsci",
               "number": "61a", "section": "001"}
        self.assertEqual(snipe.class_coords(cls), COORDS)
        self.assertEqual(snipe.class_coords({**cls, "year": "2026"}), COORDS)

    def test_spaced_subject_uppercased(self):
        # BerkeleyTime normalizes subject spacing/case: "mec eng" -> "MEC ENG".
        cls = {"year": 2026, "semester": "Fall", "subject": "mec eng",
               "number": "c85", "section": "203"}
        self.assertEqual(
            snipe.class_coords(cls),
            {"year": 2026, "semester": "Fall", "subject": "MEC ENG",
             "courseNumber": "C85", "sectionNumber": "203"})

    def test_year_and_semester_are_per_class(self):
        cls = {"year": 2025, "semester": "Spring", "subject": "COMPSCI",
               "number": "61A", "section": "001"}
        self.assertEqual(
            snipe.class_coords(cls),
            {"year": 2025, "semester": "Spring", "subject": "COMPSCI",
             "courseNumber": "61A", "sectionNumber": "001"})

    def test_none_when_field_missing_or_blank(self):
        for cls in (
            {k: v for k, v in SECTION.items() if k != "year"},       # no year
            {k: v for k, v in SECTION.items() if k != "semester"},   # no semester
            {k: v for k, v in SECTION.items() if k != "subject"},    # no subject
            {k: v for k, v in SECTION.items() if k != "number"},     # no number
            {k: v for k, v in SECTION.items() if k != "section"},    # no section
            {**SECTION, "subject": "  "},                            # blank subject
        ):
            self.assertIsNone(snipe.class_coords(cls), cls)

    def test_none_when_year_not_four_digits(self):
        self.assertIsNone(snipe.class_coords({**SECTION, "year": "20x6"}))
        self.assertIsNone(snipe.class_coords({**SECTION, "year": 26}))
        self.assertIsNone(snipe.class_coords({**SECTION, "year": "26"}))
        # Unicode "digits" pass isdigit() but must not crash int(): superscripts
        # (int() rejects) and non-ASCII decimals (int() would accept) are invalid.
        self.assertIsNone(snipe.class_coords({**SECTION, "year": "²⁰²⁶"}))
        self.assertIsNone(snipe.class_coords({**SECTION, "year": "٢٠٢٦"}))

    def test_none_when_semester_invalid(self):
        self.assertIsNone(snipe.class_coords({**SECTION, "semester": "Autumn"}))


class ClassKeyTest(unittest.TestCase):
    """class_key is the lowercased year-sem-subject-course-section state key."""

    def test_format(self):
        self.assertEqual(snipe.class_key(COORDS), KEY)
        self.assertEqual(snipe.class_key(COORDS), "2026-fall-compsci-61a-001")

    def test_spaced_subject_kept(self):
        coords = {"year": 2026, "semester": "Fall", "subject": "MEC ENG",
                  "courseNumber": "C85", "sectionNumber": "203"}
        self.assertEqual(snipe.class_key(coords), "2026-fall-mec eng-c85-203")


class ClassLabelTest(unittest.TestCase):
    """class_label derives "SUBJECT NUMBER SECTION" from the entry's fields."""

    def test_derived_from_fields(self):
        self.assertEqual(snipe.class_label(dict(SECTION)), "COMPSCI 61A 001")

    def test_skips_missing_and_null_fields(self):
        self.assertEqual(snipe.class_label({"subject": "COMPSCI", "number": None,
                                            "section": "001"}), "COMPSCI 001")

    def test_unnamed_fallback(self):
        self.assertEqual(snipe.class_label({}), "(unnamed)")


class BtFieldTest(unittest.TestCase):
    """_bt_field: unquoted semester enum, JSON-quoted strings, required fields."""

    def test_field_shape(self):
        coords = {"year": 2026, "semester": "Fall", "subject": "COMPSCI",
                  "courseNumber": "161", "sectionNumber": "001"}
        q = snipe._bt_field(coords)
        self.assertIn("semester:Fall", q)          # enum, no quotes
        self.assertNotIn('semester:"Fall"', q)
        self.assertIn('subject:"COMPSCI"', q)       # string, quoted
        self.assertIn('courseNumber:"161"', q)
        self.assertIn('sectionNumber:"001"', q)
        self.assertIn("year:2026", q)
        for field in ("seatReservationCount", "openReserved", "endTime"):
            self.assertIn(field, q)

    def test_string_args_cannot_break_out(self):
        # A quote in a value stays JSON-escaped inside the string literal.
        coords = {"year": 2026, "semester": "Fall", "subject": 'A"B',
                  "courseNumber": "1", "sectionNumber": "1"}
        self.assertIn(r'subject:"A\"B"', snipe._bt_field(coords))


class BtStatusTest(unittest.TestCase):
    """_bt_status derives O/W/C from the open/waitlist counts, then BT's flag."""

    def test_open_when_seats_free(self):
        self.assertEqual(snipe._bt_status({"status": "C"}, 5, 0, 100), "O")

    def test_waitlist_when_full_but_line_has_room(self):
        self.assertEqual(snipe._bt_status({"status": "C"}, 0, 10, 20), "W")

    def test_falls_back_to_open_flag_when_waitlist_full(self):
        self.assertEqual(snipe._bt_status({"status": "O"}, 0, 20, 20), "O")

    def test_closed_otherwise(self):
        self.assertEqual(snipe._bt_status({"status": "C"}, 0, 20, 20), "C")
        # no waitlist max and not flagged open -> closed
        self.assertEqual(snipe._bt_status({}, 0, 5, None), "C")


class SnapshotFromBtTest(unittest.TestCase):
    """_snapshot_from_bt normalizes a BerkeleyTime ``latest`` into a Snapshot."""

    def test_maps_all_fields(self):
        s = snipe._snapshot_from_bt(BT_LATEST)
        self.assertEqual(s["enrolled"], 479)
        self.assertEqual(s["capacity"], 520)
        self.assertEqual(s["open_capacity"], 41)
        self.assertEqual(s["open_reserved"], 41)
        self.assertEqual(s["open_unreserved"], 0)   # 41 open, all reserved
        self.assertEqual(s["waitlisted"], 289)
        self.assertEqual(s["waitlist"], 300)
        self.assertEqual(s["status_code"], "O")     # 41 open -> Open
        self.assertEqual(s["source_ts"], "2026-08-02T17:30:11.507Z")

    def test_per_group_reservations(self):
        s = snipe._snapshot_from_bt(BT_LATEST)
        self.assertEqual([r["open"] for r in s["reservations"]], [0, 41, 0])
        self.assertEqual([r["code"] for r in s["reservations"]],
                         ["001600", "000055", "001088"])
        self.assertIn("Data Science", s["reservations"][2]["description"])

    def test_null_and_missing_fields_yield_zeros_without_crashing(self):
        latest = {"endTime": None, "status": None, "enrolledCount": None,
                  "maxEnroll": None, "waitlistedCount": None, "maxWaitlist": None,
                  "reservedCount": None, "openReserved": None,
                  "seatReservationCount": None}
        s = snipe._snapshot_from_bt(latest)
        self.assertEqual(s["enrolled"], 0)
        self.assertEqual(s["capacity"], 0)
        self.assertEqual(s["open_capacity"], 0)
        self.assertEqual(s["open_reserved"], 0)
        self.assertEqual(s["open_unreserved"], 0)
        self.assertEqual(s["reservations"], [])
        self.assertIsNone(s["waitlist"])
        self.assertIsNone(s["source_ts"])
        self.assertEqual(s["status_code"], "C")     # no seats, no waitlist -> Closed
        # An entirely empty object must also be tolerated.
        self.assertEqual(snipe._snapshot_from_bt({})["open_capacity"], 0)

    def test_malformed_section_becomes_fetcherror_not_crash(self):
        # a schema-violating (but JSON-valid) section degrades to a per-section
        # FetchError, never raising to sink the whole batch.
        for enrollment in ("not-an-object",
                           {"latest": {"enrolledCount": "N/A"}},   # non-numeric count
                           {"latest": {"enrolledCount": "42.0"}},  # float-string count
                           {"latest": {"seatReservationCount": 7}}):  # non-list reservations
            self.assertIsInstance(snipe._snapshot_or_error(enrollment), snipe.FetchError)
        # a well-formed section still normalizes to a Snapshot
        self.assertIsInstance(snipe._snapshot_or_error({"latest": BT_LATEST}), dict)


class PollClassTest(unittest.TestCase):
    """_poll_class fetches one section (via the batch path) and raises on failure;
    never hits network."""

    def setUp(self):
        self._orig = snipe.http_post_json
        self.calls = []   # (url, payload, extra_headers) per call

    def tearDown(self):
        snipe.http_post_json = self._orig

    def _patch(self, response):
        def fake(url, payload, extra_headers=None):
            self.calls.append((url, payload, extra_headers))
            return response
        snipe.http_post_json = fake

    def test_good_response_yields_snapshot(self):
        self._patch(GOOD_BATCH_RESPONSE)
        s = snipe._poll_class(COORDS)
        self.assertEqual(s["enrolled"], 479)
        self.assertEqual(s["open_capacity"], 41)
        self.assertEqual(s["status_code"], "O")
        # posted to the BT endpoint with a query payload
        url, payload, _ = self.calls[0]
        self.assertEqual(url, snipe.BT_ENDPOINT)
        self.assertIn("enrollment(", payload["query"])

    def test_unique_session_id_header_per_call(self):
        self._patch(GOOD_BATCH_RESPONSE)
        snipe._poll_class(COORDS)
        snipe._poll_class(COORDS)
        h1, h2 = self.calls[0][2], self.calls[1][2]
        self.assertIn("sessionId", h1)
        self.assertIn("sessionId", h2)
        self.assertTrue(h1["sessionId"])
        self.assertNotEqual(h1["sessionId"], h2["sessionId"])

    def test_graphql_errors_raise(self):
        self._patch(json.dumps({"errors": [{"message": "boom"}]}))
        with self.assertRaises(snipe.FetchError) as ctx:
            snipe._poll_class(COORDS)
        self.assertIn("boom", str(ctx.exception))

    def test_null_enrollment_is_404(self):
        self._patch(bt_batch_response([None]))
        with self.assertRaises(snipe.FetchError) as ctx:
            snipe._poll_class(COORDS)
        self.assertEqual(ctx.exception.status, 404)

    def test_null_latest_raises(self):
        self._patch(json.dumps({"data": {"s0": {"latest": None}}}))
        with self.assertRaises(snipe.FetchError) as ctx:
            snipe._poll_class(COORDS)
        self.assertIsNone(ctx.exception.status)     # not a 404 (section exists)

    def test_invalid_json_raises(self):
        self._patch("not json at all")
        with self.assertRaises(snipe.FetchError):
            snipe._poll_class(COORDS)


class BtBatchTest(unittest.TestCase):
    """fetch_berkeleytime_batch: many sections per request, per-section errors,
    and chunking to BerkeleyTime's alias cap. Never hits the network."""

    COORDS = {"year": 2026, "semester": "Fall", "subject": "COMPSCI",
              "courseNumber": "161", "sectionNumber": "001"}

    def setUp(self):
        self._orig = snipe.http_post_json
        self.posted = []

    def tearDown(self):
        snipe.http_post_json = self._orig

    def test_batch_query_aliases_each_section(self):
        q = snipe._bt_batch_query([self.COORDS, self.COORDS])
        self.assertTrue(q.startswith("query{"))
        self.assertIn("s0: enrollment(", q)
        self.assertIn("s1: enrollment(", q)

    def test_one_request_isolates_missing_sections(self):
        def fake(url, payload, extra_headers=None):
            self.posted.append(payload)
            return bt_batch_response([BT_LATEST, None, BT_LATEST])  # middle not found
        snipe.http_post_json = fake
        out = snipe.fetch_berkeleytime_batch([self.COORDS] * 3)
        self.assertEqual(len(self.posted), 1)                 # ONE request for all three
        self.assertEqual(out[0]["open_capacity"], 41)
        self.assertIsInstance(out[1], snipe.FetchError)
        self.assertEqual(out[1].status, 404)
        self.assertEqual(out[2]["open_capacity"], 41)

    def test_chunks_above_the_alias_cap(self):
        n = snipe.BT_MAX_BATCH + 2

        def fake(url, payload, extra_headers=None):
            count = payload["query"].count(": enrollment(")
            self.posted.append(count)
            return bt_batch_response([BT_LATEST] * count)
        snipe.http_post_json = fake
        out = snipe.fetch_berkeleytime_batch([self.COORDS] * n)
        self.assertEqual(len(out), n)
        self.assertTrue(all(isinstance(o, dict) for o in out))
        # two requests, neither over the cap
        self.assertEqual(self.posted, [snipe.BT_MAX_BATCH, 2])

    def test_whole_chunk_failure_marks_every_section(self):
        def boom(url, payload, extra_headers=None):
            raise snipe.FetchError("network down")
        snipe.http_post_json = boom
        out = snipe.fetch_berkeleytime_batch([self.COORDS, self.COORDS])
        self.assertTrue(all(isinstance(o, snipe.FetchError) for o in out))

    def test_non_dict_data_fails_chunk_not_crashes(self):
        # a malformed response whose "data" isn't an object must fail the chunk
        # gracefully (every section -> FetchError), not raise AttributeError.
        for raw in ('{"data": []}', '{"data": "oops"}', '{"data": null}'):
            snipe.http_post_json = lambda *a, _raw=raw, **k: _raw
            out = snipe.fetch_berkeleytime_batch([self.COORDS, self.COORDS])
            self.assertTrue(all(isinstance(o, snipe.FetchError) for o in out), raw)

    def test_empty_list_makes_no_request(self):
        snipe.http_post_json = lambda *a, **k: self.fail("should not POST")
        self.assertEqual(snipe.fetch_berkeleytime_batch([]), [])


class SnapshotAgeTest(unittest.TestCase):
    """snapshot_age_seconds parses an ISO8601 source_ts (trailing Z tolerated)."""

    SRC = "2026-08-02T17:30:11.507Z"

    def test_known_delta(self):
        epoch = datetime.fromisoformat(self.SRC.replace("Z", "+00:00")).timestamp()
        self.assertAlmostEqual(
            snipe.snapshot_age_seconds({"source_ts": self.SRC}, epoch + 123.0),
            123.0, places=3)

    def test_missing_source_ts_is_none(self):
        self.assertIsNone(snipe.snapshot_age_seconds({}, 0.0))
        self.assertIsNone(snipe.snapshot_age_seconds({"source_ts": None}, 0.0))

    def test_unparseable_source_ts_is_none(self):
        self.assertIsNone(snipe.snapshot_age_seconds({"source_ts": "yesterday"}, 0.0))


class WarnIfStaleTest(unittest.TestCase):
    """_warn_if_stale logs only when the snapshot is older than BT_STALE_SECONDS."""

    def test_stale_snapshot_warns(self):
        with self.assertLogs(snipe.log, level="WARNING") as cm:
            snipe._warn_if_stale("X", {"source_ts": "2020-01-01T00:00:00Z"},
                                 time.time())
        self.assertTrue(any("old" in line for line in cm.output))

    def test_fresh_snapshot_is_quiet(self):
        now = time.time()
        fresh = datetime.fromtimestamp(now).astimezone().isoformat()
        with self.assertRaises(AssertionError):   # assertLogs fails: nothing logged
            with self.assertLogs(snipe.log, level="WARNING"):
                snipe._warn_if_stale("X", {"source_ts": fresh}, now)

    def test_missing_source_ts_is_quiet(self):
        with self.assertRaises(AssertionError):
            with self.assertLogs(snipe.log, level="WARNING"):
                snipe._warn_if_stale("X", {"source_ts": None}, time.time())


# --------------------------------------------------------------------------- #
# Alert detection (source-agnostic; unchanged)
# --------------------------------------------------------------------------- #
class DetectAlertsTest(unittest.TestCase):
    def test_any_open_seat_fires(self):
        prev = snap(open_capacity=0)
        cur = snap(open_capacity=2, enrolled=98)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["*"])]
        self.assertEqual(kinds, ["*"])

    def test_no_change_no_alert(self):
        prev = snap(open_capacity=2, enrolled=98)
        cur = snap(open_capacity=2, enrolled=98)
        self.assertEqual(snipe.detect_alerts(prev, cur, ["*"]), [])

    def test_first_seen_with_open_capacity_fires(self):
        cur = snap(open_capacity=3, enrolled=97)
        kinds = [k for k, _ in snipe.detect_alerts(None, cur, ["*"])]
        self.assertEqual(kinds, ["*"])

    def test_unreserved_only(self):
        prev = snap(open_capacity=1, open_unreserved=0, open_reserved=1)
        cur = snap(open_capacity=2, open_unreserved=1, open_reserved=1, enrolled=98)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["unreserved"])]
        self.assertEqual(kinds, ["unreserved"])
        prev2 = snap(open_capacity=1, open_unreserved=0)
        cur2 = snap(open_capacity=2, open_unreserved=0, open_reserved=2, enrolled=98)
        self.assertEqual(snipe.detect_alerts(prev2, cur2, ["unreserved"]), [])

    def test_reserved_only(self):
        prev = snap(open_capacity=1, open_unreserved=1, open_reserved=0)
        cur = snap(open_capacity=2, open_unreserved=1, open_reserved=1, enrolled=98)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["reserved"])]
        self.assertEqual(kinds, ["reserved"])
        prev2 = snap(open_capacity=1, open_unreserved=0, open_reserved=0)
        cur2 = snap(open_capacity=2, open_unreserved=2, open_reserved=0, enrolled=98)
        self.assertEqual(snipe.detect_alerts(prev2, cur2, ["reserved"]), [])

    def test_became_open(self):
        prev = snap(status_code="W")
        cur = snap(status_code="O", open_capacity=1, open_unreserved=1, enrolled=99)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["status"])]
        self.assertEqual(kinds, ["status"])

    def test_waitlist_spot_opens(self):
        prev = snap(waitlisted=100, waitlist=100)   # full
        cur = snap(waitlisted=99, waitlist=100)     # one spot opened
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["waitlist"])]
        self.assertEqual(kinds, ["waitlist"])
        prev2 = snap(waitlisted=20, waitlist=100)
        cur2 = snap(waitlisted=18, waitlist=100)
        self.assertEqual(snipe.detect_alerts(prev2, cur2, ["waitlist"]), [])
        prev3 = snap(waitlisted=100, waitlist=None)
        cur3 = snap(waitlisted=99, waitlist=None)
        self.assertEqual(snipe.detect_alerts(prev3, cur3, ["waitlist"]), [])

    def test_capacity_expands(self):
        prev = snap(capacity=100)
        cur = snap(capacity=110)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["capacity"])]
        self.assertEqual(kinds, ["capacity"])
        self.assertEqual(snipe.detect_alerts(None, cur, ["capacity"]), [])
        self.assertEqual(
            snipe.detect_alerts(snap(capacity=110), snap(capacity=110), ["capacity"]),
            [])


class CooldownTest(unittest.TestCase):
    def test_cooldown_suppresses_repeat(self):
        prev = snap(open_capacity=0)
        cur = snap(open_capacity=2, enrolled=98)
        fired, times = snipe.evaluate_alerts(
            prev, cur, ["*"], "X", {}, now_ts=0.0, cooldown=300, repeat_seconds=0)
        self.assertEqual(len(fired), 1)
        self.assertIn("*", times)
        cur2 = snap(open_capacity=4, enrolled=96)
        fired2, _ = snipe.evaluate_alerts(
            prev, cur2, ["*"], "X", times, now_ts=10.0, cooldown=300, repeat_seconds=0)
        self.assertEqual(fired2, [])
        fired3, _ = snipe.evaluate_alerts(
            prev, cur2, ["*"], "X", times, now_ts=400.0, cooldown=300, repeat_seconds=0)
        self.assertEqual(len(fired3), 1)

    def test_repeat_while_open(self):
        steady = snap(open_capacity=2, enrolled=98)
        times = {"*": 0.0}
        fired, _ = snipe.evaluate_alerts(
            steady, steady, ["*"], "X", times, now_ts=100.0,
            cooldown=0, repeat_seconds=600)
        self.assertEqual(fired, [])
        fired2, times2 = snipe.evaluate_alerts(
            steady, steady, ["*"], "X", times, now_ts=700.0,
            cooldown=0, repeat_seconds=600)
        self.assertEqual([k for k, _ in fired2], ["*"])
        self.assertEqual(times2["*"], 700.0)


class CoalesceTest(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(snipe.coalesce_alerts("X", []))

    def test_single_alert_gets_one_name_prefix(self):
        # bodies are prefix-free; coalesce adds the class name exactly once
        fired = [("*", "2 open seat(s) — check CalCentral now!")]
        self.assertEqual(snipe.coalesce_alerts("X", fired),
                         "X: 2 open seat(s) — check CalCentral now!")

    def test_multiple_kinds_merge_into_one(self):
        fired = [
            ("*", "2 open seat(s) — a spot just freed up!"),
            ("waitlist", "a waitlist spot opened — 1 now free — get in line."),
        ]
        msg = snipe.coalesce_alerts("X", fired)
        self.assertEqual(msg.count("X:"), 1)
        self.assertIn("2 open seat(s)", msg)
        self.assertIn("a waitlist spot opened", msg)

    def test_full_poll_coalesces_open_and_waitlist(self):
        prev = snap(open_capacity=0, waitlisted=100, waitlist=100)
        cur = snap(open_capacity=2, enrolled=98, waitlisted=99, waitlist=100)
        fired, _ = snipe.evaluate_alerts(
            prev, cur, ["*", "waitlist"], "X", {}, now_ts=0.0,
            cooldown=0, repeat_seconds=0)
        self.assertEqual({k for k, _ in fired}, {"*", "waitlist"})
        msg = snipe.coalesce_alerts("X", fired)
        self.assertEqual(msg.count("X:"), 1)


class PersistentMissingTest(unittest.TestCase):
    def test_persistent_404_fires_once_and_success_resets(self):
        state, sent = {}, []
        notify = {"desktop": False}
        err = snipe.FetchError("section not found on BerkeleyTime", status=404)
        orig = snipe.fire_notifications
        snipe.fire_notifications = lambda cfg, message: sent.append(message)
        try:
            for _ in range(snipe.PERSISTENT_404_THRESHOLD - 1):
                snipe._handle_fetch_error(state, "u", "X", err, notify)
            self.assertEqual(sent, [])
            snipe._handle_fetch_error(state, "u", "X", err, notify)
            self.assertEqual(len(sent), 1)
            self.assertIn("times in a row", sent[0])
            snipe._handle_fetch_error(state, "u", "X", err, notify)
            self.assertEqual(len(sent), 1)
        finally:
            snipe.fire_notifications = orig
        state2 = {}
        snipe._handle_fetch_error(
            state2, "u", "X", snipe.FetchError("network error"), notify)
        self.assertNotIn("u", state2)


class ResolveSecretTest(unittest.TestCase):
    """resolve_secret: inline wins, whitespace stripped, env fallback, else ''."""

    ENV = "CAL_SEAT_TEST_SECRET"

    def tearDown(self):
        os.environ.pop(self.ENV, None)

    def test_inline_preferred_over_env(self):
        os.environ[self.ENV] = "from-env"
        cfg = {"password": "inline-secret", "password_env": self.ENV}
        self.assertEqual(
            snipe.resolve_secret(cfg, "password", "password_env"), "inline-secret")

    def test_inline_whitespace_stripped(self):
        cfg = {"password": "abcd efgh"}
        self.assertEqual(
            snipe.resolve_secret(cfg, "password", "password_env"), "abcdefgh")

    def test_falls_back_to_env(self):
        os.environ[self.ENV] = "env-token"
        cfg = {"bot_token_env": self.ENV}
        self.assertEqual(
            snipe.resolve_secret(cfg, "bot_token", "bot_token_env"), "env-token")

    def test_empty_when_neither_set(self):
        self.assertEqual(snipe.resolve_secret({}, "password", "password_env"), "")

    def test_empty_when_env_name_given_but_unset(self):
        cfg = {"password_env": "CAL_SEAT_DEFINITELY_UNSET_VAR"}
        self.assertEqual(snipe.resolve_secret(cfg, "password", "password_env"), "")

    def test_non_string_env_name_is_skipped_not_crashed(self):
        # a non-string env-var name (config mistake) must not reach os.environ.get
        # and raise TypeError — treat it as "no usable env var".
        self.assertEqual(
            snipe.resolve_secret({"password_env": 12345}, "password", "password_env"), "")


class HttpSendGzipTest(unittest.TestCase):
    """_http_send (via http_post_json) requests gzip and transparently decompresses."""

    def test_gzip_response_decompressed(self):
        import gzip as gz, io, unittest.mock as mock

        class FakeResp(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        body = gz.compress("hello Ω".encode("utf-8"))
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["accept"] = req.headers.get("Accept-encoding")
            return FakeResp(body)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            out = snipe.http_post_json("https://classes.berkeley.edu/x", {})
        self.assertEqual(out, "hello Ω")
        self.assertEqual(captured["accept"], "gzip")

    def test_plain_response_passthrough(self):
        import io, unittest.mock as mock

        class FakeResp(io.BytesIO):
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: FakeResp(b"plain text")):
            self.assertEqual(snipe.http_post_json("https://x.example/y", {}), "plain text")

    def test_truncated_gzip_retried_not_crashed(self):
        import gzip as gz, io, unittest.mock as mock

        class FakeResp(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        truncated = gz.compress(b"x" * 500)[:20]
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return FakeResp(truncated)

        with mock.patch("urllib.request.urlopen", fake_urlopen), \
             mock.patch("time.sleep", lambda s: None):
            with self.assertRaises(snipe.FetchError):
                snipe.http_post_json("https://x.example/y", {})
        self.assertEqual(len(calls), snipe.FETCH_RETRIES)

    def test_corrupted_gzip_retried_not_crashed(self):
        import gzip as gz, io, unittest.mock as mock

        class FakeResp(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        payload = bytes(i % 251 for i in range(4000))
        blob = bytearray(gz.compress(payload))
        for i in range(12, min(len(blob) - 9, 60)):
            blob[i] ^= 0xFF
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: FakeResp(bytes(blob))), \
             mock.patch("time.sleep", lambda s: None):
            with self.assertRaises(snipe.FetchError):
                snipe.http_post_json("https://x.example/y", {})


class HttpPostJsonTest(unittest.TestCase):
    """http_post_json posts a JSON body and threads extra headers through."""

    def test_posts_json_with_session_header(self):
        import io, unittest.mock as mock

        class FakeResp(io.BytesIO):
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["method"] = req.get_method()
            captured["body"] = req.data
            captured["session"] = req.headers.get("Sessionid")
            captured["ctype"] = req.headers.get("Content-type")
            return FakeResp(b'{"ok": true}')

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            out = snipe.http_post_json("https://berkeleytime.com/api/graphql",
                                       {"query": "q"}, {"sessionId": "abc123"})
        self.assertEqual(out, '{"ok": true}')
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(json.loads(captured["body"]), {"query": "q"})
        self.assertEqual(captured["session"], "abc123")
        self.assertEqual(captured["ctype"], "application/json")


class ConfigTest(unittest.TestCase):
    def test_valid_config_passes(self):
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [{**SECTION}],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {"desktop": True},
        }
        snipe.validate_config(cfg)  # should not raise

    def test_alert_kind_reported(self):
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [{**SECTION}],
            "alert_on": ["bogus"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {},
        }
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        self.assertIn("bogus", str(ctx.exception))

    def test_class_missing_required_fields_rejected(self):
        # Each class needs a non-empty year/semester/subject/number/section, and
        # the error names the offending field.
        for drop in ("year", "semester", "subject", "number", "section"):
            section = dict(SECTION)
            del section[drop]
            cfg = {
                "poll_interval_seconds": 120,
                "classes": [section],
                "alert_on": ["*"], "alert_cooldown_seconds": 0,
                "repeat_while_open_seconds": 0, "notify": {},
            }
            with self.assertRaises(snipe.ConfigError) as ctx:
                snipe.validate_config(cfg)
            msg = str(ctx.exception)
            self.assertIn("classes[0]", msg)
            self.assertIn(drop, msg)

    def test_bad_year_rejected(self):
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [{**SECTION, "year": "20x6"}],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {},
        }
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        self.assertIn("year", str(ctx.exception))

    def test_bad_semester_rejected(self):
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [{**SECTION, "semester": "Autumn"}],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {},
        }
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        self.assertIn("semester", str(ctx.exception))

    def test_structured_class_passes(self):
        # A self-contained class (year/semester/subject/number/section) is valid.
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [dict(SECTION)],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {},
        }
        snipe.validate_config(cfg)  # should not raise

    def test_per_class_alert_on_override_validated(self):
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [{**SECTION, "alert_on": ["nope"]}],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {},
        }
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        self.assertIn("nope", str(ctx.exception))

    def _base_cfg(self, notify):
        return {
            "poll_interval_seconds": 120,
            "classes": [dict(SECTION)],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": notify,
        }

    def test_enabled_email_needs_fields_and_a_secret(self):
        cfg = self._base_cfg({"desktop": True, "email": {"host": "smtp.example.com"}})
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        msg = str(ctx.exception)
        self.assertIn("notify.email.username", msg)
        self.assertIn("password", msg)

    def test_non_dict_channels_rejected(self):
        for channel, bad in (("email", "you@example.com"), ("email", True),
                             ("email", ["a"]), ("telegram", True),
                             ("telegram", "123:ABC")):
            cfg = self._base_cfg({"desktop": True, channel: bad})
            with self.assertRaises(snipe.ConfigError) as ctx:
                snipe.validate_config(cfg)
            self.assertIn(f"notify.{channel} must be an object", str(ctx.exception))

    def test_non_string_sound_rejected(self):
        cfg = self._base_cfg({"desktop": True, "sound_name": 5})
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        self.assertIn("sound_name", str(ctx.exception))

    def test_boolean_numerics_rejected(self):
        for key in ("poll_interval_seconds", "alert_cooldown_seconds",
                    "repeat_while_open_seconds"):
            cfg = self._base_cfg({"desktop": True})
            cfg[key] = True
            with self.assertRaises(snipe.ConfigError) as ctx:
                snipe.validate_config(cfg)
            self.assertIn(key, str(ctx.exception))

    def test_non_list_classes_gets_friendly_error(self):
        for bad in (None, 42):
            f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
            json.dump({"classes": bad}, f)
            f.close()
            try:
                with self.assertRaises(SystemExit) as ctx:
                    snipe.load_config(f.name)
                self.assertIn("classes must be a list", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_unreadable_config_path_gets_friendly_error(self):
        # exists (so it passes the missing-file check) but can't be opened as a
        # file — a directory. Should exit friendly, not raise a raw OSError.
        d = tempfile.mkdtemp(suffix=".json")
        try:
            with self.assertRaises(SystemExit) as ctx:
                snipe.load_config(d)
            self.assertIn("could not be read", str(ctx.exception))
        finally:
            os.rmdir(d)

    def test_nan_and_infinity_numerics_rejected(self):
        for bad in (float("nan"), float("inf")):
            cfg = self._base_cfg({"desktop": True})
            cfg["poll_interval_seconds"] = bad
            with self.assertRaises(snipe.ConfigError) as ctx:
                snipe.validate_config(cfg)
            self.assertIn("poll_interval_seconds", str(ctx.exception))

    def test_non_dict_channels_nonfatal_at_send_time(self):
        snipe.notify_email("you@example.com", "s", "b")
        snipe.notify_telegram(True, "text")

    def test_telegram_truncated_response_nonfatal(self):
        import http.client, unittest.mock as mock

        class Boom:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                raise http.client.IncompleteRead(b"partial")

        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: Boom()):
            snipe.notify_telegram({"bot_token": "1:AA", "chat_id": "9"}, "text")

    def test_bad_email_port_rejected(self):
        for bad in (None, "587", 0, 70000, True):
            cfg = self._base_cfg({"email": {
                "host": "smtp.example.com", "username": "u", "from": "u@x",
                "to": "v@x", "password": "pw", "port": bad,
            }})
            with self.assertRaises(snipe.ConfigError) as ctx:
                snipe.validate_config(cfg)
            self.assertIn("port", str(ctx.exception))

    def test_bad_port_at_send_time_is_nonfatal(self):
        snipe.notify_email({"host": "h", "username": "u", "from": "u@x",
                            "to": "v@x", "password": "pw", "port": None},
                           "subj", "body")

    def test_enabled_email_with_inline_password_passes(self):
        cfg = self._base_cfg({"email": {
            "host": "smtp.example.com", "username": "u", "from": "u@x", "to": "v@x",
            "password": "dummy-app-pw",
        }})
        snipe.validate_config(cfg)  # should not raise

    def test_enabled_telegram_needs_token_and_chat_id(self):
        cfg = self._base_cfg({"telegram": {}})
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        msg = str(ctx.exception)
        self.assertIn("chat_id", msg)
        self.assertIn("bot_token", msg)

    def test_null_channels_are_fine(self):
        cfg = self._base_cfg({"desktop": True, "email": None, "telegram": None})
        snipe.validate_config(cfg)

    def test_non_dict_notify_gives_friendly_error_not_crash(self):
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump({"classes": [dict(SECTION)], "notify": None}, f)
        f.close()
        try:
            with self.assertRaises(SystemExit) as ctx:
                snipe.load_config(f.name)
            self.assertIn("notify must be an object", str(ctx.exception))
        finally:
            os.unlink(f.name)


class ReservationsTest(unittest.TestCase):
    """seatReservationCount parsing + group matching + eligible-seat math.

    Snapshots are built via _snapshot_from_bt from a canned ``latest`` object,
    exercising the same path the live app uses.
    """

    # Per-group shapes (enrolledCount, maxEnroll) with the resulting open count.
    EECS = ("001600", "Undergraduate Students: Electrical Engineering & Computer "
                      "Science, Computer Science, and Electrical & Computer "
                      "Engineering Majors", 149, 83)     # 66 open
    PERM = ("000055", "Students with Enrollment Permission", 144, 0)  # 144 open
    MDES = ("001232", "Master of Design Students", 7, 0)              # 7 open
    NONEECS = ("001373", "Non-EECS Declared Engineering Majors", 10, 6)  # 4 open

    def _snap(self):
        groups = [self.EECS, self.PERM, self.MDES, self.NONEECS]
        latest = {
            "endTime": "2026-08-02T00:00:00Z", "status": "O",
            "enrolledCount": 1179, "maxEnroll": 1400,
            "waitlistedCount": 0, "maxWaitlist": 500,
            "reservedCount": 221, "openReserved": 221,
            "seatReservationCount": [
                {"requirementGroup": {"code": c, "description": d},
                 "maxEnroll": mx, "enrolledCount": en}
                for (c, d, mx, en) in groups
            ],
        }
        return snipe._snapshot_from_bt(latest)

    def test_parse_includes_per_group_open(self):
        s = self._snap()
        self.assertEqual([r["open"] for r in s["reservations"]], [66, 144, 7, 4])
        self.assertEqual(s["reservations"][0]["code"], "001600")

    def test_substring_match_case_insensitive(self):
        s = self._snap()
        self.assertEqual(snipe.open_eligible(s, ["computer science"]), 66)
        self.assertEqual(snipe.open_eligible(s, ["Master of Design"]), 7)
        self.assertEqual(
            snipe.open_eligible(s, ["computer science", "master of design"]), 73)

    def test_code_match(self):
        s = self._snap()
        self.assertEqual(snipe.open_eligible(s, ["001600"]), 66)
        self.assertEqual(snipe.open_eligible(s, ["1600"]), 66)   # leading zeros

    def test_permission_blocks_never_text_match(self):
        s = self._snap()
        self.assertEqual(snipe.open_eligible(s, ["students"]), 73)
        self.assertEqual(snipe.open_eligible(s, ["000055"]), 144)

    def test_exclusion_tokens_veto_matches(self):
        transfer = {"code": "001475",
                    "description": "Computer Science Majors: New Transfer Students",
                    "open": 10}
        majors = {"code": "001600",
                  "description": "Undergraduate Students: Electrical Engineering & "
                                 "Computer Science, Computer Science, and Electrical "
                                 "& Computer Engineering Majors", "open": 5}
        groups = ["Computer Science", "!Transfer"]
        self.assertFalse(_matches(transfer, groups))
        self.assertTrue(_matches(majors, groups))
        self.assertFalse(_matches(transfer,
                                                   ["Computer Science", "!1475"]))
        self.assertFalse(_matches(majors, ["!Transfer"]))

    def test_no_groups_is_unreserved_only(self):
        s = self._snap()
        self.assertEqual(snipe.open_eligible(s, []), s["open_unreserved"])

    def test_eligible_clamped_to_open_reserved(self):
        s = snap(open_capacity=3, open_unreserved=1, open_reserved=2,
                 reservations=[{"code": "000705", "description":
                                "Statistics Majors", "open": 10}])
        self.assertEqual(snipe.open_eligible(s, ["statistics"]), 3)  # 1 + min(10,2)

    def test_eligible_alert_fires_only_for_my_groups(self):
        prev = snap(open_capacity=0, open_unreserved=0, open_reserved=0,
                    reservations=[])
        cur = snap(open_capacity=1, open_unreserved=0, open_reserved=1, enrolled=99,
                   reservations=[{"code": "000705",
                                  "description": "Statistics Majors", "open": 1}])
        fired = snipe.detect_alerts(prev, cur, ["eligible"], ["Statistics"])
        self.assertEqual([k for k, _ in fired], ["eligible"])
        self.assertIn("reserved for your group", fired[0][1])
        self.assertEqual(
            snipe.detect_alerts(prev, cur, ["eligible"], ["History"]), [])


class PermissionGuardTest(unittest.TestCase):
    """The SID-held permission block is never text-matchable, by code or by text."""

    def test_blocked_by_code(self):
        r = {"code": "000055", "description": "Students with Enrollment Permission",
             "open": 5}
        self.assertFalse(_matches(r, ["students", "permission"]))
        self.assertTrue(_matches(r, ["000055"]))

    def test_blocked_by_description_when_code_missing(self):
        r = {"code": "", "description": "Students with Enrollment Permission",
             "open": 5}
        self.assertFalse(_matches(r, ["students", "enrollment"]))

    def test_is_permission_block_by_code_or_text(self):
        # the shared helper (used by both matching AND the reserved-seat listing)
        # flags the SID-held block by code OR by description text, consistently
        self.assertTrue(snipe._is_permission_block({"code": "000055", "description": "x"}))
        self.assertTrue(snipe._is_permission_block(
            {"code": "009999", "description": "Some Enrollment Permission hold"}))
        self.assertFalse(snipe._is_permission_block({"code": "001600", "description": "EECS"}))


# --------------------------------------------------------------------------- #
# poll_once flow (BerkeleyTime source, no network)
# --------------------------------------------------------------------------- #
class PollOnceFlowTest(unittest.TestCase):
    """poll_once fetches via BerkeleyTime, updates state, and fires alerts."""

    def setUp(self):
        self._orig_post = snipe.http_post_json
        self._orig_fire = snipe.fire_notifications
        self.alerts = []
        snipe.fire_notifications = lambda cfg, msg: self.alerts.append(msg)

    def tearDown(self):
        snipe.http_post_json = self._orig_post
        snipe.fire_notifications = self._orig_fire

    def _serve(self, response):
        self.posted = []

        def fake(url, payload, extra_headers=None):
            self.posted.append((url, payload, extra_headers))
            return response
        snipe.http_post_json = fake

    def test_first_poll_records_snapshot_and_alerts(self):
        self._serve(GOOD_BATCH_RESPONSE)
        state = {}
        snipe.poll_once([{**SECTION}],
                        {"desktop": False}, ["*"], state)
        self.assertEqual(state[KEY]["snapshot"]["open_capacity"], 41)
        self.assertEqual(len(self.alerts), 1)             # first-seen 41 open
        self.assertIn("41 open seat", self.alerts[0])
        # a unique sessionId was sent, and no other network path was touched
        self.assertEqual(len(self.posted), 1)
        self.assertIn("sessionId", self.posted[0][2])

    def test_second_unchanged_poll_is_quiet(self):
        self._serve(GOOD_BATCH_RESPONSE)
        state = {}
        snipe.poll_once([{**SECTION}],
                        {"desktop": False}, ["*"], state)
        snipe.poll_once([{**SECTION}],
                        {"desktop": False}, ["*"], state)
        self.assertEqual(len(self.alerts), 1)             # no change, no new alert

    def test_two_classes_fetched_in_one_request(self):
        self._serve(bt_batch_response([BT_LATEST, BT_LATEST]))
        # Two DIFFERENT sections so they get distinct state keys.
        section2 = {**SECTION, "section": "101"}
        key2 = "2026-fall-compsci-61a-101"
        state = {}
        snipe.poll_once([{**SECTION},
                         {**section2}],
                        {"desktop": False}, ["*"], state)
        self.assertEqual(len(self.posted), 1)             # both sections, ONE request
        self.assertIn("snapshot", state[KEY])
        self.assertIn("snapshot", state[key2])

    def test_404_response_does_not_baseline(self):
        self._serve(bt_batch_response([None]))
        state = {}
        snipe.poll_once([{**SECTION}],
                        {"desktop": False}, ["*"], state)
        self.assertEqual(self.alerts, [])
        self.assertEqual(state[KEY].get("missing_count"), 1)
        self.assertNotIn("snapshot", state[KEY])


class CliOnceTest(unittest.TestCase):
    """main(--class ... --once) drives one poll via BerkeleyTime; no network."""

    def setUp(self):
        self._orig_post = snipe.http_post_json
        self._orig_fire = snipe.fire_notifications
        self.posted = []
        snipe.fire_notifications = lambda *a, **k: None

        def fake(url, payload, extra_headers=None):
            self.posted.append((url, payload, extra_headers))
            return GOOD_BATCH_RESPONSE
        snipe.http_post_json = fake
        self._state = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        os.unlink(self._state)

    def tearDown(self):
        snipe.http_post_json = self._orig_post
        snipe.fire_notifications = self._orig_fire
        if os.path.exists(self._state):
            os.unlink(self._state)

    def test_once_posts_to_berkeleytime_and_saves_state(self):
        snipe.main(["--class", "2026:Fall:COMPSCI:61A:001",
                    "--once", "--state", self._state, "--quiet"])
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0][0], snipe.BT_ENDPOINT)
        self.assertIn("sessionId", self.posted[0][2])
        saved = snipe.load_state(self._state)
        self.assertEqual(len(saved), 1)
        self.assertIn("snapshot", next(iter(saved.values())))

    def test_nonpositive_interval_rejected(self):
        for bad in ("-5", "0"):
            with self.assertRaises(SystemExit):
                snipe.main(["--class", "2026:Fall:COMPSCI:61A:001",
                            "--once", "--interval", bad,
                            "--state", self._state, "--quiet"])


class ExceptionHardeningTest(unittest.TestCase):
    """No config value or server response may crash the watch loop."""

    def test_invalid_urls_become_fetcherror(self):
        for bad in ("berkeleytime.com/api/graphql",              # schemeless
                    "https://berkeleytime.com/api/graphql?q=x‑y"):  # non-ASCII
            with self.assertRaises(snipe.FetchError):
                snipe.http_post_json(bad, {})

    def test_incomplete_class_entry_is_skipped_not_raised(self):
        # An entry missing a required field has no coords, so poll_once yields a
        # non-404 FetchError (_BAD_CLASS_MSG). poll_once must just warn — never
        # raise, never fetch, and never baseline a snapshot for it.
        incomplete = {"subject": "COMPSCI", "number": "61A"}  # no year/sem/section
        self.assertIsNone(snipe.class_coords(incomplete))
        orig_fire = snipe.fire_notifications
        orig_post = snipe.http_post_json
        snipe.fire_notifications = lambda *a, **k: None
        # If poll_once tried to fetch, this would fail the test.
        snipe.http_post_json = lambda *a, **k: self.fail("must not fetch")
        try:
            state = {}
            snipe.poll_once([incomplete], {"desktop": False}, ["*"], state)  # must not raise
            key = snipe.class_label(incomplete)  # falls back to the label
            # A non-404 bad-class error only warns: no snapshot baselined.
            self.assertNotIn("snapshot", state.get(key, {}))
        finally:
            snipe.fire_notifications = orig_fire
            snipe.http_post_json = orig_post

    def test_telegram_unicode_token_nonfatal(self):
        import unittest.mock as mock

        def boom(req, timeout=None):
            raise UnicodeEncodeError("ascii", "tokén", 3, 4, "ordinal")

        with mock.patch("urllib.request.urlopen", boom):
            snipe.notify_telegram({"bot_token": "tokén", "chat_id": "1"}, "text")

    def test_desktop_permission_error_nonfatal(self):
        import unittest.mock as mock
        with mock.patch("subprocess.run",
                        side_effect=PermissionError("denied")), \
             mock.patch("platform.system", return_value="Darwin"):
            snipe.notify_desktop("t", "m")

    def test_load_state_tolerates_directory_and_bad_bytes(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(snipe.load_state(d), {})
            p = os.path.join(d, "bad.json")
            with open(p, "wb") as f:
                f.write(b"\xff\xfe\x00garbage")
            self.assertEqual(snipe.load_state(p), {})
        finally:
            import shutil; shutil.rmtree(d)

    def test_save_state_failure_nonfatal(self):
        d = tempfile.mkdtemp()
        try:
            shadow = os.path.join(d, "states-file")
            with open(shadow, "w") as f:
                f.write("x")
            snipe.save_state(os.path.join(shadow, "s.json"), {"a": 1})
        finally:
            import shutil; shutil.rmtree(d)

    def test_email_missing_fields_nonfatal(self):
        snipe.notify_email({"host": "h", "username": "u", "password": "pw"}, "s", "b")

    def test_telegram_missing_chat_id_nonfatal(self):
        snipe.notify_telegram({"bot_token": "1:AA"}, "text")

    def test_load_state_non_dict_json_returns_empty(self):
        for content in ("null", "[]", '"x"', "42"):
            f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
            f.write(content)
            f.close()
            try:
                self.assertEqual(snipe.load_state(f.name), {})
            finally:
                os.unlink(f.name)

    def test_corrupt_state_entries_never_crash_poll(self):
        """No hand-corrupted state entry may escape poll_once, whether the fetch
        fails or succeeds."""
        corrupt_entries = [
            "not-a-dict-entry",                              # entry itself not a dict
            {"snapshot": "oops", "alert_times": "x"},       # non-dict alert_times
            {"snapshot": snap(), "alert_times": {"*": "5"}},  # non-numeric time
            {"missing_count": "abc"},                       # bad 404 counter
            {"snapshot": snap(open_capacity=1)},            # plausible prior
        ]
        cls = {**SECTION}
        orig_post, orig_fire = snipe.http_post_json, snipe.fire_notifications
        snipe.fire_notifications = lambda *a, **k: None
        try:
            # (a) fetch fails (both a plain error and a 404 escalation path)
            for status in (None, 404):
                def raiser(*a, _st=status, **k):
                    raise snipe.FetchError("net", status=_st)
                snipe.http_post_json = raiser
                for entry in corrupt_entries:
                    snipe.poll_once([cls],
                                    {"desktop": False}, ["*"], {KEY: entry},
                                    cooldown=300, repeat_seconds=60)   # no raise
            # (b) fetch succeeds but the prior entry is corrupt
            snipe.http_post_json = lambda *a, **k: GOOD_BATCH_RESPONSE
            for entry in corrupt_entries:
                snipe.poll_once([cls],
                                {"desktop": False}, ["*"], {KEY: entry},
                                cooldown=300, repeat_seconds=60)       # no raise
        finally:
            snipe.http_post_json, snipe.fire_notifications = orig_post, orig_fire

    def test_boundary_guard_resets_entry_and_continues(self):
        """When a poll genuinely crashes past the FetchError path, that class's
        state is dropped and other classes still poll."""
        section2 = {**SECTION, "section": "002"}
        key2 = "2026-fall-compsci-61a-002"
        orig_batch, orig_ev, orig_fire = (snipe.fetch_berkeleytime_batch,
                                          snipe.evaluate_alerts,
                                          snipe.fire_notifications)
        snipe.fire_notifications = lambda *a, **k: None
        snipe.fetch_berkeleytime_batch = \
            lambda coords_list: [snap(open_capacity=1, enrolled=1) for _ in coords_list]
        polled = []

        def ev(prev, cur, alert_on, name, *a, **k):
            polled.append(name)
            if name == "COMPSCI 61A 001":     # the first section's derived label
                raise RuntimeError("corrupt inner state")
            return [], {}

        try:
            snipe.evaluate_alerts = ev
            state = {KEY: {"snapshot": snap()}, key2: {"snapshot": snap()}}
            snipe.poll_once([dict(SECTION), section2],
                            {"desktop": False}, ["*"], state)
            self.assertNotIn(KEY, state)       # crashed class reset
            self.assertIn(key2, state)         # sibling still polled + saved
            self.assertEqual(polled, ["COMPSCI 61A 001", "COMPSCI 61A 002"])
        finally:
            (snipe.fetch_berkeleytime_batch, snipe.evaluate_alerts,
             snipe.fire_notifications) = orig_batch, orig_ev, orig_fire


class AlertDefaultTest(unittest.TestCase):
    """load_config: alert_on defaults to eligible (groups or not)."""

    def _load(self, cfg):
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump(cfg, f)
        f.close()
        try:
            return snipe.load_config(f.name)
        finally:
            os.unlink(f.name)

    def test_default_is_eligible(self):
        cfg = self._load({"classes": [dict(SECTION)]})
        self.assertEqual(cfg["alert_on"], ["eligible"])
        self.assertEqual(cfg["reserved_groups"], [])
        s = snap(open_capacity=2, open_unreserved=1, open_reserved=1)
        self.assertEqual(snipe.open_eligible(s, cfg["reserved_groups"]), 1)

    def test_groups_keep_eligible_default(self):
        cfg = self._load({"classes": [dict(SECTION)],
                          "reserved_groups": ["Statistics"]})
        self.assertEqual(cfg["alert_on"], ["eligible"])

    def test_explicit_alert_on_wins(self):
        cfg = self._load({"classes": [dict(SECTION)],
                          "reserved_groups": ["Statistics"],
                          "alert_on": ["*", "waitlist"]})
        self.assertEqual(cfg["alert_on"], ["*", "waitlist"])


class DefaultStatePathTest(unittest.TestCase):
    """Each config file pairs with its own state file under states/."""

    def test_default_config(self):
        p = snipe.default_state_path("/x/configs/config.json")
        self.assertEqual(os.path.basename(p), "state.json")
        self.assertEqual(os.path.basename(os.path.dirname(p)), "states")

    def test_per_person_config(self):
        p = snipe.default_state_path("configs/config-alice.json")
        self.assertEqual(os.path.basename(p), "state-alice.json")

    def test_arbitrary_name(self):
        p = snipe.default_state_path("mine.json")
        self.assertEqual(os.path.basename(p), "mine.state.json")


class SetupLoggingTest(unittest.TestCase):
    def test_bad_logfile_path_warns_not_crashes(self):
        # a directory as the --logfile path: FileHandler raises OSError. setup
        # should warn (console handler stays) and continue, not crash startup.
        saved_handlers, saved_level = snipe.log.handlers[:], snipe.log.level
        d = tempfile.mkdtemp()
        try:
            snipe.setup_logging(False, False, d)  # must not raise
            kinds = [type(h).__name__ for h in snipe.log.handlers]
            self.assertIn("StreamHandler", kinds)
            self.assertNotIn("FileHandler", kinds)
        finally:
            snipe.log.handlers[:] = saved_handlers
            snipe.log.setLevel(saved_level)
            os.rmdir(d)


if __name__ == "__main__":
    unittest.main()
