#!/usr/bin/env python3
"""Offline unit tests for cal-seat-sniper.

Run from the app dir:  python3 -m unittest discover -s tests
No network is used: the parsers run against saved section-page, associated-
fragment, and reserved-ajax fixtures, and the fast-poll flow runs against a
faked server (see FastPollFlowTest).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snipe  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "section_page.html")
FRAGMENT_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "associated_fragment.html")
RESERVED_AJAX_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "reserved_ajax.json")


def snap(**over):
    """A minimal snapshot with sensible defaults, overridable per test."""
    base = {
        "status_code": "W", "status_desc": "Waitlist",
        "enrolled": 100, "capacity": 100, "waitlisted": 20, "waitlist": 100,
        "reserved": 0, "open_reserved": 0, "open_capacity": 0, "open_unreserved": 0,
        "ts": "2026-07-29T00:00:00",
    }
    base.update(over)
    return base


class ParserTest(unittest.TestCase):
    def test_parses_real_fixture(self):
        with open(FIXTURE, encoding="utf-8") as f:
            s = snipe.parse_enrollment(f.read())
        self.assertEqual(s["status_code"], "O")
        self.assertEqual(s["enrolled"], 1358)
        self.assertEqual(s["capacity"], 1400)
        self.assertEqual(s["open_capacity"], 42)
        self.assertEqual(s["open_reserved"], 42)
        self.assertEqual(s["open_unreserved"], 0)      # 42 open, all reserved
        self.assertEqual(s["waitlisted"], 163)
        self.assertEqual(s["waitlist"], 500)

    def test_missing_blob_raises(self):
        with self.assertRaises(snipe.FetchError):
            snipe.parse_enrollment("<html>no blob here</html>")


class DetectAlertsTest(unittest.TestCase):
    def test_any_open_seat_fires(self):
        prev = snap(open_capacity=0)
        cur = snap(open_capacity=2, enrolled=98)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["*"], "X")]
        self.assertEqual(kinds, ["*"])

    def test_no_change_no_alert(self):
        prev = snap(open_capacity=2, enrolled=98)
        cur = snap(open_capacity=2, enrolled=98)
        self.assertEqual(snipe.detect_alerts(prev, cur, ["*"], "X"), [])

    def test_first_seen_with_open_capacity_fires(self):
        cur = snap(open_capacity=3, enrolled=97)
        kinds = [k for k, _ in snipe.detect_alerts(None, cur, ["*"], "X")]
        self.assertEqual(kinds, ["*"])

    def test_unreserved_only(self):
        prev = snap(open_capacity=1, open_unreserved=0, open_reserved=1)
        cur = snap(open_capacity=2, open_unreserved=1, open_reserved=1, enrolled=98)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["unreserved"], "X")]
        self.assertEqual(kinds, ["unreserved"])
        # ...and the reserved-only bump would NOT fire under unreserved:
        prev2 = snap(open_capacity=1, open_unreserved=0)
        cur2 = snap(open_capacity=2, open_unreserved=0, open_reserved=2, enrolled=98)
        self.assertEqual(snipe.detect_alerts(prev2, cur2, ["unreserved"], "X"), [])

    def test_reserved_only(self):
        # A reserved-only bump fires "reserved" but not "unreserved".
        prev = snap(open_capacity=1, open_unreserved=1, open_reserved=0)
        cur = snap(open_capacity=2, open_unreserved=1, open_reserved=1, enrolled=98)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["reserved"], "X")]
        self.assertEqual(kinds, ["reserved"])
        # ...and an unreserved-only bump would NOT fire under reserved:
        prev2 = snap(open_capacity=1, open_unreserved=0, open_reserved=0)
        cur2 = snap(open_capacity=2, open_unreserved=2, open_reserved=0, enrolled=98)
        self.assertEqual(snipe.detect_alerts(prev2, cur2, ["reserved"], "X"), [])

    def test_became_open(self):
        prev = snap(status_code="W")
        cur = snap(status_code="O", open_capacity=1, open_unreserved=1, enrolled=99)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["status"], "X")]
        self.assertEqual(kinds, ["status"])

    def test_waitlist_spot_opens(self):
        # A full waitlist (waitlisted == waitlist max) gaining room fires "waitlist".
        prev = snap(waitlisted=100, waitlist=100)   # full
        cur = snap(waitlisted=99, waitlist=100)     # one spot opened
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["waitlist"], "X")]
        self.assertEqual(kinds, ["waitlist"])
        # The line merely advancing on a NOT-full waitlist is not a spot opening.
        prev2 = snap(waitlisted=20, waitlist=100)
        cur2 = snap(waitlisted=18, waitlist=100)
        self.assertEqual(snipe.detect_alerts(prev2, cur2, ["waitlist"], "X"), [])
        # With no waitlist max we can't tell, so it stays quiet.
        prev3 = snap(waitlisted=100, waitlist=None)
        cur3 = snap(waitlisted=99, waitlist=None)
        self.assertEqual(snipe.detect_alerts(prev3, cur3, ["waitlist"], "X"), [])

    def test_capacity_expands(self):
        prev = snap(capacity=100)
        cur = snap(capacity=110)
        kinds = [k for k, _ in snipe.detect_alerts(prev, cur, ["capacity"], "X")]
        self.assertEqual(kinds, ["capacity"])
        # First sight is a baseline, not an expansion.
        self.assertEqual(snipe.detect_alerts(None, cur, ["capacity"], "X"), [])
        # A shrinking/steady capacity does not fire.
        self.assertEqual(
            snipe.detect_alerts(snap(capacity=110), snap(capacity=110), ["capacity"], "X"),
            [])


class CooldownTest(unittest.TestCase):
    def test_cooldown_suppresses_repeat(self):
        prev = snap(open_capacity=0)
        cur = snap(open_capacity=2, enrolled=98)
        # first fire at t=0
        fired, times = snipe.evaluate_alerts(
            prev, cur, ["*"], "X", {}, now_ts=0.0, cooldown=300, repeat_seconds=0)
        self.assertEqual(len(fired), 1)
        self.assertIn("*", times)
        # a further increase 10s later is within cooldown -> suppressed
        cur2 = snap(open_capacity=4, enrolled=96)
        fired2, _ = snipe.evaluate_alerts(
            prev, cur2, ["*"], "X", times, now_ts=10.0, cooldown=300, repeat_seconds=0)
        self.assertEqual(fired2, [])
        # past cooldown -> fires again
        fired3, _ = snipe.evaluate_alerts(
            prev, cur2, ["*"], "X", times, now_ts=400.0, cooldown=300, repeat_seconds=0)
        self.assertEqual(len(fired3), 1)

    def test_repeat_while_open(self):
        # steady state (no change) but seats remain open and repeat is enabled
        steady = snap(open_capacity=2, enrolled=98)
        times = {"*": 0.0}  # last alerted at t=0
        # before repeat window: nothing
        fired, _ = snipe.evaluate_alerts(
            steady, steady, ["*"], "X", times, now_ts=100.0,
            cooldown=0, repeat_seconds=600)
        self.assertEqual(fired, [])
        # after repeat window: a reminder fires
        fired2, times2 = snipe.evaluate_alerts(
            steady, steady, ["*"], "X", times, now_ts=700.0,
            cooldown=0, repeat_seconds=600)
        self.assertEqual([k for k, _ in fired2], ["*"])
        self.assertEqual(times2["*"], 700.0)


class CoalesceTest(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(snipe.coalesce_alerts("X", []))

    def test_single_alert_verbatim(self):
        fired = [("*", "X: 2 open seat(s) — check CalCentral now!")]
        self.assertEqual(snipe.coalesce_alerts("X", fired), fired[0][1])

    def test_multiple_kinds_merge_into_one(self):
        fired = [
            ("*", "X: 2 open seat(s) — a spot just freed up!"),
            ("waitlist", "X: a waitlist spot opened — 1 now free — get in line."),
        ]
        msg = snipe.coalesce_alerts("X", fired)
        # One message, the class name appears exactly once (prefix stripped).
        self.assertEqual(msg.count("X:"), 1)
        self.assertIn("2 open seat(s)", msg)
        self.assertIn("a waitlist spot opened", msg)

    def test_full_poll_coalesces_open_and_waitlist(self):
        # A real diff that trips both "*" and "waitlist" in one poll: a seat frees
        # while a full waitlist gains room.
        prev = snap(open_capacity=0, waitlisted=100, waitlist=100)
        cur = snap(open_capacity=2, enrolled=98, waitlisted=99, waitlist=100)
        fired, _ = snipe.evaluate_alerts(
            prev, cur, ["*", "waitlist"], "X", {}, now_ts=0.0,
            cooldown=0, repeat_seconds=0)
        self.assertEqual({k for k, _ in fired}, {"*", "waitlist"})
        msg = snipe.coalesce_alerts("X", fired)
        self.assertEqual(msg.count("X:"), 1)  # single coalesced ping


class PersistentMissingTest(unittest.TestCase):
    def test_persistent_404_fires_once_and_success_resets(self):
        state, sent = {}, []
        notify = {"desktop": False}
        err = snipe.FetchError("HTTP 404 fetching page", status=404)
        orig = snipe.fire_notifications
        snipe.fire_notifications = lambda cfg, title, message: sent.append(message)
        try:
            # Below threshold: quiet, no notification.
            for _ in range(snipe.PERSISTENT_404_THRESHOLD - 1):
                snipe._handle_fetch_error(state, "u", "X", err, notify)
            self.assertEqual(sent, [])
            # Threshold hit: exactly one loud alert.
            snipe._handle_fetch_error(state, "u", "X", err, notify)
            self.assertEqual(len(sent), 1)
            self.assertIn("404", sent[0])
            # Still 404ing: no repeat.
            snipe._handle_fetch_error(state, "u", "X", err, notify)
            self.assertEqual(len(sent), 1)
        finally:
            snipe.fire_notifications = orig
        # A non-404 error never escalates.
        state2 = {}
        snipe._handle_fetch_error(
            state2, "u", "X", snipe.FetchError("network error"), notify)
        self.assertNotIn("u", state2)


class ResolveSecretTest(unittest.TestCase):
    """resolve_secret: inline wins, whitespace stripped, env fallback, else ''."""

    ENV = "CAL_SEAT_TEST_SECRET"  # dummy env var, cleaned up after each test

    def tearDown(self):
        os.environ.pop(self.ENV, None)

    def test_inline_preferred_over_env(self):
        os.environ[self.ENV] = "from-env"
        cfg = {"password": "inline-secret", "password_env": self.ENV}
        self.assertEqual(
            snipe.resolve_secret(cfg, "password", "password_env"), "inline-secret")

    def test_inline_whitespace_stripped(self):
        # Gmail App Passwords display in spaced groups of four.
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


class HttpGetGzipTest(unittest.TestCase):
    """http_get requests gzip and transparently decompresses the response."""

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
            out = snipe.http_get("https://classes.berkeley.edu/x")
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
            self.assertEqual(snipe.http_get("https://x.example/y"), "plain text")

    def test_truncated_gzip_retried_not_crashed(self):
        # A dropped connection mid-body yields partial gzip -> EOFError from
        # decompress; must surface as FetchError after retries, never escape.
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
                snipe.http_get("https://x.example/y")
        self.assertEqual(len(calls), snipe.FETCH_RETRIES)   # it retried

    def test_corrupted_gzip_retried_not_crashed(self):
        # valid gzip header + corrupt deflate stream -> zlib.error; must be
        # treated as transient like truncation, never escape http_get
        import gzip as gz, io, unittest.mock as mock

        class FakeResp(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        payload = bytes(i % 251 for i in range(4000))   # poorly compressible
        blob = bytearray(gz.compress(payload))
        for i in range(12, min(len(blob) - 9, 60)):
            blob[i] ^= 0xFF                     # mangle past the 10-byte header
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: FakeResp(bytes(blob))), \
             mock.patch("time.sleep", lambda s: None):
            with self.assertRaises(snipe.FetchError):
                snipe.http_get("https://x.example/y")


class CacheBustTest(unittest.TestCase):
    """cache_bust: appends a distinct _cb nonce, correct separator."""

    URL = "https://classes.berkeley.edu/content/x"

    def test_appends_cb_param(self):
        busted = snipe.cache_bust(self.URL)
        self.assertIn("_cb=", busted)
        self.assertTrue(busted.startswith(self.URL + "?_cb="))

    def test_distinct_nonce_on_repeat(self):
        # Two back-to-back calls must not collide (nanosecond ts + random bits).
        self.assertNotEqual(snipe.cache_bust(self.URL), snipe.cache_bust(self.URL))

    def test_uses_ampersand_when_query_present(self):
        busted = snipe.cache_bust(self.URL + "?foo=bar")
        self.assertIn("&_cb=", busted)
        self.assertNotIn("?_cb=", busted)


class FetchEnrollmentBustTest(unittest.TestCase):
    """fetch_enrollment threads bust_cache through to the fetched URL (no network)."""

    URL = "https://classes.berkeley.edu/content/2026-fall-x-001-lec-001"

    def setUp(self):
        with open(FIXTURE, encoding="utf-8") as f:
            self._html = f.read()
        self._orig_get = snipe.http_get
        self._seen = []

        def fake_get(url):
            self._seen.append(url)
            return self._html

        snipe.http_get = fake_get

    def tearDown(self):
        snipe.http_get = self._orig_get

    def test_bust_cache_appends_cb(self):
        snipe.fetch_enrollment(self.URL, bust_cache=True)
        self.assertEqual(len(self._seen), 1)
        self.assertIn("_cb=", self._seen[0])
        self.assertTrue(self._seen[0].startswith(self.URL))

    def test_no_bust_leaves_url_untouched(self):
        snipe.fetch_enrollment(self.URL, bust_cache=False)
        self.assertEqual(self._seen, [self.URL])
        self.assertNotIn("_cb=", self._seen[0])


class CliBustCacheTest(unittest.TestCase):
    """--bust-cache overrides config through main() to the fetched URL (no network).

    Runs with --no-fast-poll: these tests pin the legacy full-page path; the
    fast-poll flow has its own tests below.
    """

    URL = "https://classes.berkeley.edu/content/2026-fall-x-001-lec-001"

    def setUp(self):
        with open(FIXTURE, encoding="utf-8") as f:
            self._html = f.read()
        self._orig_get = snipe.http_get
        self._orig_fire = snipe.fire_notifications
        self._seen = []
        snipe.http_get = lambda url: (self._seen.append(url), self._html)[1]
        snipe.fire_notifications = lambda *a, **k: None  # no real notifications
        self._state = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False).name
        os.unlink(self._state)  # let the app create it fresh

    def tearDown(self):
        snipe.http_get = self._orig_get
        snipe.fire_notifications = self._orig_fire
        if os.path.exists(self._state):
            os.unlink(self._state)

    def test_flag_busts_cache(self):
        snipe.main(["--url", self.URL, "--once", "--bust-cache", "--no-fast-poll",
                    "--state", self._state, "--quiet"])
        self.assertEqual(len(self._seen), 1)
        self.assertIn("_cb=", self._seen[0])
        self.assertTrue(self._seen[0].startswith(self.URL))

    def test_default_busts_cache(self):
        # bust_cache defaults to true since v0.3 (freshest defaults)
        snipe.main(["--url", self.URL, "--once", "--no-fast-poll",
                    "--state", self._state, "--quiet"])
        self.assertEqual(len(self._seen), 1)
        self.assertIn("_cb=", self._seen[0])

    def test_config_can_disable_bust(self):
        import json
        cfg = {"classes": [{"name": "A", "url": self.URL}], "bust_cache": False}
        path = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w")
        json.dump(cfg, path)
        path.close()
        try:
            snipe.main(["--config", path.name, "--once", "--no-fast-poll",
                        "--state", self._state, "--quiet"])
        finally:
            os.unlink(path.name)
        self.assertEqual(self._seen, [self.URL])
        self.assertNotIn("_cb=", self._seen[0])

    def test_nonpositive_interval_rejected(self):
        for bad in ("-5", "0"):
            with self.assertRaises(SystemExit):
                snipe.main(["--url", self.URL, "--once", "--no-fast-poll",
                            "--interval", bad, "--state", self._state, "--quiet"])


class ConfigTest(unittest.TestCase):
    def test_valid_config_passes(self):
        cfg = {
            "poll_interval_seconds": 120, "classes": [
                {"name": "A", "url": "https://classes.berkeley.edu/content/x"}],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {"desktop": True},
        }
        snipe.validate_config(cfg)  # should not raise

    def test_bad_url_and_alert_kind_reported(self):
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [{"name": "A", "url": "https://example.com/search"}],
            "alert_on": ["bogus"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {},
        }
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        msg = str(ctx.exception)
        self.assertIn("/content/", msg)
        self.assertIn("bogus", msg)

    def test_per_class_alert_on_override_validated(self):
        cfg = {
            "poll_interval_seconds": 120,
            "classes": [{"url": "https://classes.berkeley.edu/content/x",
                         "alert_on": ["nope"]}],
            "alert_on": ["*"], "alert_cooldown_seconds": 0,
            "repeat_while_open_seconds": 0, "notify": {},
        }
        with self.assertRaises(snipe.ConfigError):
            snipe.validate_config(cfg)

    def _base_cfg(self, notify):
        return {
            "poll_interval_seconds": 120,
            "classes": [{"url": "https://classes.berkeley.edu/content/x"}],
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
        # "email": "you@x" or "telegram": true would crash at alert time
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
                    "repeat_while_open_seconds", "content_refresh_seconds"):
            cfg = self._base_cfg({"desktop": True})
            cfg[key] = True
            with self.assertRaises(snipe.ConfigError) as ctx:
                snipe.validate_config(cfg)
            self.assertIn(key, str(ctx.exception))

    def test_non_list_classes_gets_friendly_error(self):
        import json
        for bad in (None, 42):
            f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
            json.dump({"classes": bad}, f)
            f.close()
            try:
                with self.assertRaises(SystemExit) as ctx:
                    snipe.load_config(f.name)    # must not TypeError
                self.assertIn("classes must be a list", str(ctx.exception))
            finally:
                os.unlink(f.name)

    def test_nan_and_infinity_numerics_rejected(self):
        for bad in (float("nan"), float("inf")):
            cfg = self._base_cfg({"desktop": True})
            cfg["poll_interval_seconds"] = bad
            with self.assertRaises(snipe.ConfigError) as ctx:
                snipe.validate_config(cfg)
            self.assertIn("poll_interval_seconds", str(ctx.exception))

    def test_non_dict_channels_nonfatal_at_send_time(self):
        # belt and braces: even unvalidated shapes must never raise mid-loop
        snipe.notify_email("you@example.com", "s", "b")
        snipe.notify_telegram(True, "text")

    def test_telegram_truncated_response_nonfatal(self):
        import http.client, unittest.mock as mock

        class Boom:
            def read(self):
                raise http.client.IncompleteRead(b"partial")

        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: Boom()):
            snipe.notify_telegram(
                {"bot_token": "1:AA", "chat_id": "9"}, "text")   # must not raise

    def test_url_anchor_stripped_on_load(self):
        import json
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump({"classes": [{"url":
                   "https://classes.berkeley.edu/content/x-1#section-times "}]}, f)
        f.close()
        try:
            cfg = snipe.load_config(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(cfg["classes"][0]["url"],
                         "https://classes.berkeley.edu/content/x-1")
        # --url mode gets the same normalization
        c2 = snipe.config_from_url(
            "https://classes.berkeley.edu/content/x-1#anchor")
        self.assertEqual(c2["classes"][0]["url"],
                         "https://classes.berkeley.edu/content/x-1")

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
        # even if a bad port sneaks past validation, the channel must only
        # log a warning — never kill the watch loop
        snipe.notify_email({"host": "h", "username": "u", "from": "u@x",
                            "to": "v@x", "password": "pw", "port": None},
                           "subj", "body")   # must not raise

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
        snipe.validate_config(cfg)  # disabled channels don't trigger validation

    def test_non_dict_notify_gives_friendly_error_not_crash(self):
        import json
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump({"classes": [{"url": "https://classes.berkeley.edu/content/x"}],
                   "notify": None}, f)
        f.close()
        try:
            with self.assertRaises(SystemExit) as ctx:
                snipe.load_config(f.name)   # must not raise AttributeError
            self.assertIn("notify must be an object", str(ctx.exception))
        finally:
            os.unlink(f.name)

    def test_non_bool_bust_cache_rejected(self):
        cfg = self._base_cfg({"desktop": True})
        cfg["bust_cache"] = "yes"  # must be a JSON bool, not a string
        with self.assertRaises(snipe.ConfigError) as ctx:
            snipe.validate_config(cfg)
        self.assertIn("bust_cache", str(ctx.exception))

    def test_bool_bust_cache_passes(self):
        cfg = self._base_cfg({"desktop": True})
        cfg["bust_cache"] = True
        snipe.validate_config(cfg)  # should not raise


class FragmentParseTest(unittest.TestCase):
    """parse_associated_sections against markup saved from the live endpoint."""

    def setUp(self):
        with open(FRAGMENT_FIXTURE, encoding="utf-8") as f:
            self.rows = snipe.parse_associated_sections(f.read())

    def test_parses_rows_keyed_by_slug(self):
        lec = self.rows["2026-fall-compsci-61a-001-lec-001"]
        self.assertEqual(lec, {"open_capacity": 42, "enrolled": 1358,
                               "capacity": 1400, "waitlisted": 182, "waitlist": 500})
        lab = self.rows["2026-fall-compsci-61a-101l-lab-101l"]
        self.assertEqual((lab["open_capacity"], lab["capacity"]), (1, 1))

    def test_comma_formatted_numbers(self):
        row = self.rows["2026-fall-compsci-61a-888-dis-888"]
        self.assertEqual(row, {"open_capacity": 1042, "enrolled": 1358,
                               "capacity": 2400, "waitlisted": 1001, "waitlist": 1500})

    def test_plain_html_yields_no_rows(self):
        self.assertEqual(snipe.parse_associated_sections("<html>nothing</html>"), {})


class ReservedAjaxTest(unittest.TestCase):
    """fetch_reserved_ajax + _with_realtime_reservations against a saved fixture."""

    def setUp(self):
        with open(RESERVED_AJAX_FIXTURE, encoding="utf-8") as f:
            self._raw = f.read()
        self._orig = snipe.http_get
        self._seen = []
        snipe.http_get = lambda url: (self._seen.append(url), self._raw)[1]

    def tearDown(self):
        snipe.http_get = self._orig

    def test_parses_and_dedupes(self):
        rows = snipe.fetch_reserved_ajax(
            "https://classes.berkeley.edu/content/2026-fall-compsci-188-001-lec-001")
        # fixture renders the breakdown twice; distinct groups -> 4 rows, sum 221
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(r["open"] for r in rows), 221)
        self.assertTrue(all(r["code"] == "" for r in rows))  # ajax carries no codes

    def test_requests_uncached_ajax_variant(self):
        snipe.fetch_reserved_ajax("https://classes.berkeley.edu/content/x")
        self.assertIn("_wrapper_format=drupal_ajax", self._seen[0])
        self.assertIn("_cb=", self._seen[0])

    def test_enrichment_supplies_codes_and_live_reserved(self):
        rows = snipe.fetch_reserved_ajax("https://classes.berkeley.edu/content/x")
        cached = snap(open_reserved=999, reservations=[  # stale numbers, real codes
            {"code": "001600", "description": "Undergraduate Students: Electrical "
             "Engineering & Computer Science, Computer Science, and Electrical & "
             "Computer Engineering Majors", "open": 0},
            {"code": "000055", "description": "Students with Enrollment Permission",
             "open": 0},
        ])
        merged = snipe._with_realtime_reservations(cached, rows)
        self.assertEqual(merged["open_reserved"], 221)   # live sum, not 999
        eecs = next(r for r in merged["reservations"] if r["open"] == 66)
        self.assertEqual(eecs["code"], "001600")         # code carried from page
        # a CS major (text) gets 66; a text token can't reach the permission block
        snapd = dict(cached); snapd.update(merged)
        snapd["open_capacity"] = 221; snapd["open_unreserved"] = 0
        self.assertEqual(snipe.open_eligible(snapd, ["Computer Science"]), 66)
        self.assertEqual(snipe.open_eligible(snapd, ["students"]), 73)   # 66+7, not permission
        self.assertEqual(snipe.open_eligible(snapd, ["000055"]), 144)    # code still works

    def test_bad_response_raises(self):
        snipe.http_get = lambda url: "not json"
        with self.assertRaises(snipe.FetchError):
            snipe.fetch_reserved_ajax("https://classes.berkeley.edu/content/x")


class PermissionGuardTest(unittest.TestCase):
    """The SID-held permission block is never text-matchable, by code or by text."""

    def test_blocked_by_code(self):
        r = {"code": "000055", "description": "Students with Enrollment Permission",
             "open": 5}
        self.assertFalse(snipe.reservation_matches(r, ["students", "permission"]))
        self.assertTrue(snipe.reservation_matches(r, ["000055"]))

    def test_blocked_by_description_when_code_missing(self):
        # ajax-sourced rows have no code — the text guard must still apply
        r = {"code": "", "description": "Students with Enrollment Permission",
             "open": 5}
        self.assertFalse(snipe.reservation_matches(r, ["students", "enrollment"]))


class ExceptionHardeningTest(unittest.TestCase):
    """No config value or server response may crash the watch loop (round F)."""

    def test_invalid_urls_become_fetcherror(self):
        for bad in ("classes.berkeley.edu/content/x",        # schemeless
                    "https://classes.berkeley.edu/content/x‑y"):  # non-ASCII
            with self.assertRaises(snipe.FetchError):
                snipe.http_get(bad)

    def test_null_enrollment_shapes_become_fetcherror(self):
        shapes = [
            {"ucb": {"enrollment": {"available": {"enrollmentStatus": None}}}},
            {"ucb": {"enrollment": {"available": {"enrollmentStatus":
                {"enrolledCount": None, "maxEnroll": 100}}}}},
            {"ucb": {"enrollment": {"available": {"enrollmentStatus":
                {"status": None, "enrolledCount": 1, "maxEnroll": "x"}}}}},
        ]
        for settings in shapes[:2]:
            # null status is tolerated (-> "?"); null counts coerce to 0 — only
            # verify none of these CRASH with a non-FetchError
            try:
                snipe.enrollment_from_settings(settings)
            except snipe.FetchError:
                pass
        with self.assertRaises(snipe.FetchError):
            snipe.enrollment_from_settings(shapes[2])   # int("x")

    def test_non_string_ajax_data_becomes_fetcherror(self):
        orig = snipe.http_get
        snipe.http_get = lambda url: '[{"command":"insert","data":{"not":"str"}}]'
        try:
            with self.assertRaises(snipe.FetchError):
                snipe.fetch_reserved_ajax("https://classes.berkeley.edu/content/x")
        finally:
            snipe.http_get = orig

    def test_telegram_unicode_token_nonfatal(self):
        import unittest.mock as mock

        def boom(req, timeout=None):
            raise UnicodeEncodeError("ascii", "tokén", 3, 4, "ordinal")

        with mock.patch("urllib.request.urlopen", boom):
            snipe.notify_telegram({"bot_token": "tokén", "chat_id": "1"},
                                  "text")   # must not raise

    def test_desktop_permission_error_nonfatal(self):
        import unittest.mock as mock
        with mock.patch("subprocess.run",
                        side_effect=PermissionError("denied")), \
             mock.patch("platform.system", return_value="Darwin"):
            snipe.notify_desktop("t", "m")   # must not raise

    def test_load_state_tolerates_directory_and_bad_bytes(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(snipe.load_state(d), {})       # a directory
            p = os.path.join(d, "bad.json")
            with open(p, "wb") as f:
                f.write(b"\xff\xfe\x00garbage")
            self.assertEqual(snipe.load_state(p), {})       # invalid UTF-8
        finally:
            import shutil; shutil.rmtree(d)

    def test_save_state_failure_nonfatal(self):
        d = tempfile.mkdtemp()
        try:
            shadow = os.path.join(d, "states-file")
            with open(shadow, "w") as f:
                f.write("x")                                # file shadows the dir
            snipe.save_state(os.path.join(shadow, "s.json"), {"a": 1})  # no raise
        finally:
            import shutil; shutil.rmtree(d)

    def test_email_missing_fields_nonfatal(self):
        snipe.notify_email({"host": "h", "username": "u", "password": "pw"},
                           "s", "b")   # no "from"/"to": must not raise

    def test_telegram_missing_chat_id_nonfatal(self):
        snipe.notify_telegram({"bot_token": "1:AA"}, "text")   # must not raise

    def test_infinity_enrollment_becomes_fetcherror(self):
        settings = {"ucb": {"enrollment": {"available": {"enrollmentStatus":
                    {"enrolledCount": float("inf"), "maxEnroll": 100}}}}}
        with self.assertRaises(snipe.FetchError):
            snipe.enrollment_from_settings(settings)

    def test_corrupt_state_entries_never_crash_poll(self):
        """No hand-corrupted state entry (any inner shape) may escape poll_once."""
        corrupt_entries = [
            {"discovery": "legacy"},                                  # non-dict
            {"discovery": {"mode": "legacy", "checked_ts": "x"}},     # bad ts
            {"snapshot": "oops", "alert_times": "x"},
            {"snapshot": snap(), "alert_times": {"*": "5"},
             "content": "junk"},
            {"missing_count": "abc"},                                 # bad 404 count
            {"discovery": {"probe_node": "9", "node_id": "1"},
             "fragment_row": "not-a-dict"},
        ]
        url = "https://classes.berkeley.edu/content/2026-fall-x-001-lec-001"
        orig_get, orig_fire = snipe.http_get, snipe.fire_notifications
        snipe.fire_notifications = lambda *a, **k: None
        try:
            for status in (None, 404):
                snipe.http_get = lambda u, _st=status: (_ for _ in ()).throw(
                    snipe.FetchError("net", status=_st))
                for entry in corrupt_entries:
                    snipe.poll_once([{"name": "X", "url": url}],
                                    {"desktop": False}, ["*"], {url: entry},
                                    cooldown=300, repeat_seconds=60,
                                    fast_poll=True)   # must not raise
        finally:
            snipe.http_get, snipe.fire_notifications = orig_get, orig_fire

    def test_boundary_guard_resets_entry_and_continues(self):
        """When a poll genuinely crashes past the FetchError path, that class's
        state is dropped (so it re-discovers) and other classes still poll."""
        u1 = "https://classes.berkeley.edu/content/a-1"
        u2 = "https://classes.berkeley.edu/content/b-1"
        orig_pc, orig_ev, orig_fire = (snipe._poll_class, snipe.evaluate_alerts,
                                       snipe.fire_notifications)
        snipe.fire_notifications = lambda *a, **k: None
        snipe._poll_class = lambda *a, **k: snap(open_capacity=1, enrolled=1)
        polled = []

        def ev(prev, cur, alert_on, name, *a, **k):
            polled.append(name)
            if name == "A":
                raise RuntimeError("corrupt inner state")
            return [], {}

        try:
            snipe.evaluate_alerts = ev
            state = {u1: {"snapshot": snap()}, u2: {"snapshot": snap()}}
            snipe.poll_once([{"name": "A", "url": u1}, {"name": "B", "url": u2}],
                            {"desktop": False}, ["*"], state, fast_poll=True)
            self.assertNotIn(u1, state)        # crashed class reset
            self.assertIn(u2, state)           # sibling still polled + saved
            self.assertEqual(polled, ["A", "B"])
        finally:
            (snipe._poll_class, snipe.evaluate_alerts,
             snipe.fire_notifications) = orig_pc, orig_ev, orig_fire

    def test_giant_json_integer_becomes_fetcherror(self):
        # a 4301+-digit int literal in the blob makes json.loads raise plain
        # ValueError (not JSONDecodeError) on 3.11+; must surface as FetchError
        blob = ('<script type="application/json" '
                'data-drupal-selector="drupal-settings-json">'
                '{"ucb": {"enrollment": {"available": {"enrollmentStatus": '
                '{"enrolledCount": ' + "9" * 4400 + '}}}}}</script>')
        with self.assertRaises(snipe.FetchError):
            snipe.parse_drupal_settings(blob)
        with self.assertRaises(snipe.FetchError):
            snipe.parse_enrollment(blob)

    def test_pathological_digit_counts_skipped(self):
        # Python 3.11+ int() raises on >4300 digits; bounded regexes skip such rows
        big = "9" * 5000
        html = ('<div class="detail-class-associated-sections-flex">'
                '<h4 class="section-header"><a href="/content/x-1"></a></h4>'
                f'<span class="detail-label">Open Seats:</span> {big}'
                '</div>')
        self.assertEqual(snipe.parse_associated_sections(html), {})

    def test_load_state_non_dict_json_returns_empty(self):
        for content in ("null", "[]", '"x"', "42"):
            f = tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                            mode="w")
            f.write(content)
            f.close()
            try:
                self.assertEqual(snipe.load_state(f.name), {})
            finally:
                os.unlink(f.name)


class NodeIdTest(unittest.TestCase):
    def test_node_id_from_content_fixture(self):
        with open(FIXTURE, encoding="utf-8") as f:
            settings = snipe.parse_drupal_settings(f.read())
        self.assertEqual(snipe.node_id_from_settings(settings), "517627")

    def test_missing_path_raises(self):
        with self.assertRaises(snipe.FetchError):
            snipe.node_id_from_settings({"path": {"currentPath": "front"}})
        with self.assertRaises(snipe.FetchError):
            snipe.node_id_from_settings({})


class ContentSlugTest(unittest.TestCase):
    def test_extracts_slug(self):
        self.assertEqual(
            snipe.content_slug("https://classes.berkeley.edu/content/2026-fall-x-001-lec-001"),
            "2026-fall-x-001-lec-001")

    def test_strips_query(self):
        self.assertEqual(
            snipe.content_slug("https://classes.berkeley.edu/content/x-1?_cb=99"), "x-1")

    def test_non_content_url_is_none(self):
        self.assertIsNone(snipe.content_slug("https://classes.berkeley.edu/search"))


class MergeRowSnapshotTest(unittest.TestCase):
    ROW = {"open_capacity": 3, "enrolled": 97, "capacity": 100,
           "waitlisted": 5, "waitlist": 50}

    def test_fragment_counts_win_content_supplements(self):
        content = snap(status_code="W", status_desc="Waitlist",
                       reserved=10, open_reserved=2,
                       open_capacity=0, enrolled=100)  # stale page numbers lose
        s = snipe.merge_row_snapshot(self.ROW, content)
        self.assertEqual(s["open_capacity"], 3)
        self.assertEqual(s["enrolled"], 97)
        self.assertEqual(s["waitlisted"], 5)
        self.assertEqual(s["status_code"], "W")
        self.assertEqual(s["open_reserved"], 2)
        self.assertEqual(s["open_unreserved"], 1)

    def test_open_reserved_clamped_to_open_capacity(self):
        content = snap(open_reserved=42)   # stale: more reserved-open than open
        s = snipe.merge_row_snapshot(self.ROW, content)
        self.assertEqual(s["open_reserved"], 3)
        self.assertEqual(s["open_unreserved"], 0)

    def test_no_content_snapshot_yet(self):
        s = snipe.merge_row_snapshot(self.ROW, None)
        self.assertEqual(s["status_code"], "?")
        self.assertEqual(s["open_reserved"], 0)
        self.assertEqual(s["open_unreserved"], 3)


def _content_html(node, enrolled=1358, capacity=1400, waitlisted=182, waitlist=500,
                  reserved=42, open_reserved=42, code="O", desc="Open",
                  reservations=None):
    import json
    settings = {
        "path": {"currentPath": f"node/{node}"},
        "ucb": {"enrollment": {"available": {"enrollmentStatus": {
            "status": {"code": code, "description": desc},
            "enrolledCount": enrolled, "maxEnroll": capacity,
            "waitlistedCount": waitlisted, "maxWaitlist": waitlist,
            "reservedCount": reserved, "openReserved": open_reserved,
            "seatReservations": [
                {"number": i + 1,
                 "requirementGroup": {"code": c, "description": d},
                 "maxEnroll": mx, "enrolledCount": en}
                for i, (c, d, mx, en) in enumerate(reservations or [])
            ],
        }}}},
    }
    return ('<script type="application/json" '
            'data-drupal-selector="drupal-settings-json">'
            + json.dumps(settings) + "</script>")


class ReservationsTest(unittest.TestCase):
    """seatReservations parsing + group matching + eligible-seat math."""

    # Shapes observed live on classes.berkeley.edu (2026-07-31).
    EECS = ("001600", "Undergraduate Students: Electrical Engineering & Computer "
                      "Science, Computer Science, and Electrical & Computer "
                      "Engineering Majors", 149, 83)     # 66 open
    PERM = ("000055", "Students with Enrollment Permission", 144, 0)  # 144 open
    MDES = ("001232", "Master of Design Students", 7, 0)              # 7 open

    def _snap(self):
        html = _content_html(1, enrolled=1179, capacity=1400, open_reserved=221,
                             reservations=[self.EECS, self.PERM, self.MDES,
                                           ("001373", "Non-EECS Declared "
                                            "Engineering Majors", 10, 6)])
        return snipe.parse_enrollment(html)

    def test_parse_includes_per_group_open(self):
        s = self._snap()
        self.assertEqual(
            [r["open"] for r in s["reservations"]], [66, 144, 7, 4])
        self.assertEqual(s["reservations"][0]["code"], "001600")

    def test_substring_match_case_insensitive(self):
        s = self._snap()
        # CS 188-style: 221 open, 0 unreserved, 66 snipeable as an EECS major
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
        # "students" appears in the permission description but must not match it
        # (also matches "Undergraduate Students..." and "...Design Students" -> 73)
        self.assertEqual(snipe.open_eligible(s, ["students"]), 73)
        # explicit code still works for the rare user who truly has permission
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
        self.assertFalse(snipe.reservation_matches(transfer, groups))
        self.assertTrue(snipe.reservation_matches(majors, groups))
        # exclusion by code works too
        self.assertFalse(snipe.reservation_matches(transfer,
                                                   ["Computer Science", "!1475"]))
        # an exclusion alone never makes anything match
        self.assertFalse(snipe.reservation_matches(majors, ["!Transfer"]))

    def test_no_groups_is_unreserved_only(self):
        s = self._snap()
        self.assertEqual(snipe.open_eligible(s, []), s["open_unreserved"])

    def test_eligible_clamped_to_open_reserved(self):
        # stale page data could sum group opens above open_reserved
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
        fired = snipe.detect_alerts(prev, cur, ["eligible"], "X",
                                    ["Statistics"])
        self.assertEqual([k for k, _ in fired], ["eligible"])
        self.assertIn("reserved for your group", fired[0][1])
        # a different major sees nothing
        self.assertEqual(
            snipe.detect_alerts(prev, cur, ["eligible"], "X", ["History"]),
            [])


class FastPollFlowTest(unittest.TestCase):
    """End-to-end fast-poll rounds against a faked classes.berkeley.edu."""

    LEC = "https://classes.berkeley.edu/content/2026-fall-compsci-61a-001-lec-001"
    LAB = "https://classes.berkeley.edu/content/2026-fall-compsci-61a-101l-lab-101l"

    def setUp(self):
        with open(FRAGMENT_FIXTURE, encoding="utf-8") as f:
            self.fragment = f.read()
        self.pages = {
            "/content/2026-fall-compsci-61a-001-lec-001": _content_html(111),
            "/content/2026-fall-compsci-61a-101l-lab-101l": _content_html(
                333, enrolled=0, capacity=1, waitlisted=0, waitlist=0, reserved=0, open_reserved=0),
            "/content/2026-fall-compsci-61a-999-dis-999": _content_html(222),
        }
        self.seen = []
        self._orig_get = snipe.http_get
        self._orig_fire = snipe.fire_notifications
        self.alerts = []
        snipe.http_get = self._fake_get
        snipe.fire_notifications = lambda cfg, title, msg: self.alerts.append(msg)
        self.state = {}

    def tearDown(self):
        snipe.http_get = self._orig_get
        snipe.fire_notifications = self._orig_fire

    def _fake_get(self, url):
        self.seen.append(url)
        if "/sections/associated/" in url:
            if self.fragment is None:
                raise snipe.FetchError("network error after 3 attempts")
            return self.fragment
        if "_wrapper_format=drupal_ajax" in url:
            # minimal uncached reserved-breakdown response (no reserved blocks)
            return '[{"command":"insert","data":"<article>no reserved</article>"}]'
        for path, html in self.pages.items():
            if path in url:
                return html
        raise snipe.FetchError("HTTP 404 fetching page", status=404)

    def _poll(self, classes):
        self.seen.clear()
        snipe.poll_once(classes, {"desktop": False}, ["*"], self.state,
                        fast_poll=True, content_refresh=10**9)

    def test_discovery_then_fragment_only_then_change(self):
        classes = [{"name": "LEC", "url": self.LEC}]

        # Round 1 — discovery: content(target), fragment(target), content(probe),
        # then an immediate probe-fragment read so the baseline is live.
        self._poll(classes)
        self.assertEqual(len(self.seen), 4)
        self.assertIn("/sections/associated/111", self.seen[1])
        self.assertIn("/sections/associated/222", self.seen[3])
        disc = self.state[self.LEC]["discovery"]
        self.assertEqual(disc["probe_node"], "222")
        self.assertEqual(disc["probe_slug"], "2026-fall-compsci-61a-999-dis-999")
        self.assertEqual(self.state[self.LEC]["fragment_row"]["open_capacity"], 42)
        # first sight of 42 open seats alerts (existing first-seen behavior)
        self.assertEqual(len(self.alerts), 1)

        # Round 2 — steady state: ONE uncached fragment request, nothing else.
        self._poll(classes)
        self.assertEqual(len(self.seen), 1)
        self.assertIn("/sections/associated/222", self.seen[0])
        self.assertEqual(self.state[self.LEC]["snapshot"]["open_capacity"], 42)
        self.assertEqual(len(self.alerts), 1)  # no change, no new alert

        # Round 3 — a seat frees in the fragment while the page stays stale:
        # alert fires from the fragment numbers; the change triggers one fetch
        # of the uncached ajax variant for the live reserved breakdown.
        self.fragment = self.fragment.replace(
            "Open Seats:</span> 42", "Open Seats:</span> 43", 1)
        self._poll(classes)
        self.assertEqual(len(self.seen), 2)
        self.assertIn("/sections/associated/222", self.seen[0])
        self.assertIn("_wrapper_format=drupal_ajax", self.seen[1])
        self.assertIn("_cb=", self.seen[1])          # ajax fetch is nonce-busted
        self.assertEqual(self.state[self.LEC]["snapshot"]["open_capacity"], 43)
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("43 open seat", self.alerts[1])

    def test_same_course_shares_one_fragment_request(self):
        classes = [{"name": "LEC", "url": self.LEC},
                   {"name": "LAB", "url": self.LAB}]
        self._poll(classes)          # discovery round for both
        self._poll(classes)          # steady state
        frag = [u for u in self.seen if "/sections/associated/" in u]
        self.assertEqual(len(frag), 1)   # both classes read the same probe fetch
        self.assertEqual(len(self.seen), 1)

    def test_transient_fragment_failure_keeps_live_baseline(self):
        classes = [{"name": "LEC", "url": self.LEC}]
        self._poll(classes)                      # discovery, baseline open=42
        # fragment 503s this round; the cached page (still 42 open in
        # _content_html) must NOT poison the baseline or fire an alert
        real_fragment = self.fragment
        self.fragment = None                     # _fake_get raises on fragment
        self._poll(classes)
        self.assertEqual(self.state[self.LEC]["snapshot"]["open_capacity"], 42)
        self.assertEqual(len(self.alerts), 1)    # no spurious alert
        self.assertEqual(self.state[self.LEC]["frag_fail_count"], 1)
        # fragment recovers with a real +1 — alert fires off the live diff
        self.fragment = real_fragment.replace(
            "Open Seats:</span> 42", "Open Seats:</span> 43", 1)
        self._poll(classes)
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("43", self.alerts[1])
        self.assertNotIn("frag_fail_count", self.state[self.LEC])

    def test_stale_plus_changed_round_advances_content_ts(self):
        """A round that is both stale and changed must keep the refreshed page
        timestamp — pinning the old ts would re-trigger a full-page fetch on
        every subsequent churny poll (politeness regression)."""
        entry = {
            "discovery": {"node_id": "111", "probe_node": "222",
                          "probe_slug": "2026-fall-compsci-61a-999-dis-999",
                          "checked_ts": 0.0},
            "fragment_row": {"open_capacity": 41, "enrolled": 1359,
                             "capacity": 1400, "waitlisted": 182, "waitlist": 500},
            "content": {"snapshot": snap(), "ts": 100.0},   # very stale
        }
        now = 10_000.0
        snipe._poll_class(self.LEC, "LEC", entry,
                          {"2026-fall-compsci-61a-001-lec-001"}, {},
                          bust_cache=False, fast_poll=True,
                          content_refresh=450, now_ts=now)
        # both the stale page refresh AND the change-triggered ajax ran...
        self.assertTrue(any("_wrapper_format=drupal_ajax" in u for u in self.seen))
        page_fetches = [u for u in self.seen
                        if "/content/2026-fall-compsci-61a-001-lec-001" in u
                        and "_wrapper_format" not in u]
        self.assertEqual(len(page_fetches), 1)
        # ...and the stored ts is the fresh one, not the pinned 100.0
        self.assertEqual(entry["content"]["ts"], now)

    def test_missing_row_falls_back_and_rediscovers(self):
        classes = [{"name": "LEC", "url": self.LEC}]
        self._poll(classes)
        # fragment suddenly stops listing our section (renumbered section)
        self.fragment = self.fragment.replace("61a-001-lec-001", "61a-777-lec-777")
        self.pages["/content/2026-fall-compsci-61a-777-lec-777"] = _content_html(777)
        self._poll(classes)
        self.assertNotIn("discovery", self.state[self.LEC])   # queued re-discovery
        # the degraded round repeats the last live reading — no page fetch, no
        # stale-page baseline, snapshot unchanged
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.state[self.LEC]["snapshot"]["open_capacity"], 42)
        # ...and the next round re-discovers a working probe
        self._poll(classes)
        self.assertEqual(
            self.state[self.LEC]["discovery"].get("probe_slug"),
            "2026-fall-compsci-61a-777-lec-777")   # first non-watched sibling


class AlertDefaultTest(unittest.TestCase):
    """load_config: alert_on defaults to eligible (groups or not)."""

    URL = "https://classes.berkeley.edu/content/x"

    def _load(self, cfg):
        import json
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump(cfg, f)
        f.close()
        try:
            return snipe.load_config(f.name)
        finally:
            os.unlink(f.name)

    def test_default_is_eligible(self):
        cfg = self._load({"classes": [{"url": self.URL}]})
        self.assertEqual(cfg["alert_on"], ["eligible"])
        self.assertEqual(cfg["reserved_groups"], [])
        # ...and with no groups the eligible count degrades to unreserved-only
        s = snap(open_capacity=2, open_unreserved=1, open_reserved=1)
        self.assertEqual(snipe.open_eligible(s, cfg["reserved_groups"]), 1)

    def test_groups_keep_eligible_default(self):
        cfg = self._load({"classes": [{"url": self.URL}],
                          "reserved_groups": ["Statistics"]})
        self.assertEqual(cfg["alert_on"], ["eligible"])

    def test_explicit_alert_on_wins(self):
        cfg = self._load({"classes": [{"url": self.URL}],
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


class StateMigrationTest(unittest.TestCase):
    def test_flat_legacy_entry_migrates(self):
        legacy = snap(open_capacity=2, enrolled=98)
        state = {"u": legacy}  # pre-0.2 flat snapshot
        prev, times = snipe.state_entry(state, "u")
        self.assertEqual(prev["open_capacity"], 2)
        self.assertEqual(times, {})

    def test_new_entry_shape(self):
        state = {"u": {"snapshot": snap(open_capacity=1), "alert_times": {"*": 5.0}}}
        prev, times = snipe.state_entry(state, "u")
        self.assertEqual(prev["open_capacity"], 1)
        self.assertEqual(times, {"*": 5.0})


if __name__ == "__main__":
    unittest.main()
