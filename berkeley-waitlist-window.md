# The Berkeley Waitlist Auto-Enrollment "Window"

> **Status:** Living document — continuously refined by an automated agent loop.
> **Last human-seeded:** 2026-07-29. **Refinement:** see `refine-doc-loop` (self-paced agent).
> **Confidence tags:** ✅ confirmed from official UCB sources · 🟡 plausible/partly-official · 🔴 vendor/marketing claim, unverified.

---

## 0. TL;DR

UC Berkeley does **not** enroll the next waitlisted student the instant a seat opens. Automatic
waitlists are drained by a **batch job that runs at fixed intervals** — **~every 6 hours (4×/day)**
through the end of the third week of instruction (three official sources say 6 h; the Registrar's "every
3 hours" is a lone outlier — §4), and only nightly/weekend earlier in the cycle. Between the
moment a student **drops** and the next batch run, the class's enrollment counts change on the public
pages while no reallocation has happened yet. That lag is the "window" the whole
watch-a-class-and-alert-me ecosystem (e.g. `waitlistwarrior.net`) is built around. Crucially, the batch
does **not** lock the freed seat while it waits to run: in that interval a fast, **direct** enrollment
can take the open seat **ahead of the people already on the waitlist** — that is the real "batch-jump,"
and it is **not** limited to empty-waitlist edge cases (see §1.1). The one genuine dead-end is trying to
enroll at an instant when the section is truly full (0 open seats) — then you're forced onto the waitlist.

**The three sites that actually matter:**

| Site | URL | Role |
|---|---|---|
| **Class Schedule / Academic Guide** | `classes.berkeley.edu` | Public, no-login. Shows live enrolled / waitlisted / capacity / open-seat / reserved-seat data per section. **This is the page a watcher scrapes.** |
| **CalCentral** | `calcentral.berkeley.edu` | Authenticated. Where you actually enroll, waitlist, drop, and see your Phase/Adjustment appointment times ("My Academics" → "Class Enrollment"). |
| **Schedule Planner / Builder** | inside CalCentral | Build a conflict-free schedule and push it to your enrollment cart. |

---

## 1. The phenomenon (why a "window" exists at all)

Two facts combine to create the gap:

1. **Drops are instantaneous and self-service.** A student drops in CalCentral, confirms, and their
   seat is released immediately. The public counts on `classes.berkeley.edu` reflect this on the next
   scrape/refresh. ✅
2. **Waitlist *promotion* is not instantaneous — it is batched.** Berkeley runs an **automatic
   waitlist processor on a schedule**, not on every drop event. ✅

So there is a real interval in which "a seat exists" is *true on the public page* but "the next
waitlisted person has been enrolled" is *not yet true*. The length of that interval is exactly the
gap between batch runs (see §4).

### 1.1 Can a non-waitlisted (or lower-ranked) student actually grab that seat? Yes — and not only in edge cases

This is the crux, and an **earlier version of this doc got it wrong** by over-reading one official
table (see the correction at the end of this section). Here is the cross-checked picture.

**Why grabbing works (the mechanism).** Berkeley's student system is Oracle **PeopleSoft / Campus
Solutions**, and its waitlist auto-enroll is a **periodic batch** (every ~6h at peak — §4), *not* a
real-time reservation that fires the instant a seat frees. Whether a freed seat is **held for the
waitlist** between batch runs is a **per-section PeopleSoft setting**, not an automatic law of the
system. Across universities running the *same software*, three distinct behaviors exist — which is
exactly why "does watching help?" has different answers at different schools:

| Model | What happens the moment a seat frees | Can a fast non-#1 student grab it? | Seen at |
|---|---|---|---|
| **A — Notify & reserve** | Seat is reserved for the #1 waitlister, who gets an email + ~24h to claim it | **No** | Colorado State, Denver |
| **B — Hold closed until batch** | "The class remains closed until a process runs that automatically enrolls students from the wait list" | **No** | Northern Arizona |
| **C — Open & grabbable** | Section is **not** set to auto-reserve seats for the list, so "students will be able to bypass the wait list to gain enrollment if a seat is available" | **Yes — first to direct-enroll wins** | *Berkeley, in practice* |

**Berkeley behaves like Model C.** The official UCB pages describe the batch but **never state that a
freed seat is locked between runs**, and the lived reality — the entire reason this doc exists — is
that students *do* catch open seats and enroll ahead of a standing waitlist. That is corroborated by
(a) direct student accounts of getting in this way, (b) the `waitlistwarrior.net` anecdotes
(CS 189 from 200+, Chem 1A), and (c) the plain fact that a whole category of watch-and-alert tools
would be **pointless** if Berkeley used Model A or B. So **being told the instant a seat opens and then
enrolling directly can, and does, beat the batch — regardless of how long the waitlist is.** 🟡
*(Mechanism inferred from PeopleSoft configuration behavior + convergent empirical evidence; UCB does
not publish its per-section seat-hold setting. Cross-university PeopleSoft sources in §9.)*

**What "grab" requires in CalCentral.** In the shopping cart, enroll and let the add go through on the
open seat — do **not** rely on "Add to waitlist if full" as your play: if a real seat is open at that
instant you go straight in; if the section is full at that instant the add simply fails (or drops you
onto the waitlist if you opted in). The move is: watch the count, and the second an open seat appears,
fire the direct-enroll.

**Balance / caveat (for honesty):** *official* Berkeley guidance frames it the other way — for a **full**
class it tells you to **join the waitlist** ("you cannot enroll in an open seat without first joining the
waitlist"), and the auto-enroller then works the queue. That guidance is about the *full-class* state; the
Model-C grab is about the **drop-window** state (a seat momentarily open, `enrolled < capacity`), which
the official pages don't address. So Model-C remains an **empirically-observed / cross-university-inferred**
behavior, **not an officially-endorsed path** — real, but don't expect a Berkeley page to tell you to do
it. 🟡

**⚠️ Correction — what the earlier draft got wrong.** A prior version claimed CalCentral *hard-blocks*
any walk-up on a waitlisted section, citing the Registrar's "Guide to Enrollment Rules" **"Enrollment
Attempt Failed"** outcome. That outcome is real but describes a **different state**: the section is
**actually full (0 open seats)** and you declined the waitlist — i.e. *"you tried to enroll in a full
class,"* **not** *"a seat just opened and you were blocked."* Conflating those two was the error, and it
wrongly implied grabbing only works on an empty waitlist. It does not: a fresh drop opens a real seat,
and in that window direct enrollment goes through. *(A separate table row — "Waitlisted, class waitlist
not processed yet" — does mean an attempt can sometimes land you on the list instead of enrolling, so
grabbing isn't a guaranteed win **every** time; but that is a "sometimes you miss," not a "you can never
jump the batch.")* ✅ *(correction verified against cross-university PeopleSoft behavior — §9.)*

**Moments that make the window especially winnable:**
- **Any fresh drop while the batch is between runs** — the general case above.
- Someone **higher on the waitlist is skipped** by the batch (unit cap, time conflict, hold — §5), so
  the seat falls through to whoever is eligible and fastest.
- **Reserved seats** for an enrollment group (major/minor) **release** to general availability at a set
  time — first-come wins.
- The **waitlist empties or is closed**, converting the section back to plain direct enrollment.

**Bottom line (corrected):** watching converts *information latency* into seats. Because Berkeley's
batch is periodic and does not lock freed seats, a fast direct enrollment in the window genuinely
**jumps the batch ahead of waitlisted students** — that is the real phenomenon, not merely an
empty-waitlist special case. The one thing watching can't beat is a manual-waitlist section, where a
human in the department picks (§4.1).

---

## 2. The sites to watch (and exactly what to watch on them)

### 2.1 `classes.berkeley.edu` — the Berkeley Academic Guide / Class Schedule (public)
- No login required; served publicly, so it is the scrape target for any external watcher.
- Each section's detail exposes the live counters that matter:
  - **Enrolled** (current count)
  - **Waitlisted** (current count)
  - **Capacity / Enrollment Max**
  - **Open Seats**
  - **Reserved seats / restrictions** (e.g. "seats reserved for declared majors") — the guide details
    reserved-seat rules per section. ✅
- **What a change means:** an *enrolled* number going **down** or *open seats* going **up** = a drop
  just happened → you are now inside a window. A *waitlisted* number dropping without enrolled rising
  = someone left the line (your position improved, or a skip happened).

### 2.2 `calcentral.berkeley.edu` — CalCentral (authenticated; where you act)
- The actual enroll / waitlist / drop / swap actions. ✅
- **"My Academics" → "Class Enrollment"** block shows your **Phase 1 / Phase 2 / Adjustment Period**
  appointment windows. ✅
- Monitor your **waitlist position** here. ✅
- Because it's behind CalNet SSO, external watchers can't scrape it server-side — which is why the
  vendor tool ships a **browser extension** to read authenticated CalCentral pages client-side (§6). 🔴

### 2.3 Schedule Planner / Schedule Builder (inside CalCentral)
- Build a conflict-free plan, then load it into the enrollment cart so that when your window hits you
  can enroll in one action rather than searching. ✅

### 2.4 The actual data source — how a watcher reads live counts ⭐ (verified by direct fetch, 2026-07-29)

This is the concrete, reproducible answer to "what does a watcher scrape," established by fetching a
live section page — not inferred.

- **Every section has a public content page**, no login:
  `https://classes.berkeley.edu/content/<term>-<dept>-<coursenum>-<classnum>-<component>-<num>`
  — e.g. `https://classes.berkeley.edu/content/2026-fall-compsci-61a-001-lec-001`. ✅
- **The live enrollment numbers are server-rendered directly into the page HTML** (not loaded by a
  separate authenticated API call), inside a
  `<script type="application/json" data-drupal-selector="drupal-settings-json">` blob, at JSON path
  **`drupalSettings.ucb.enrollment.available.enrollmentStatus`**. Confirmed fields: ✅
  - **`status.code`** — the official section status: **`O` = Open, `W` = Waitlist, `C` = Closed** (plus
    `status.description`).
  - **`enrolledCount`**, **`maxEnroll`** (capacity), **`waitlistedCount`**, **`maxWaitlist`**,
    `reservedCount`, `openReserved`, and a `seatReservations[]` list (reserved-seat groups — §5).
  - **Open seats = `maxEnroll − enrolledCount`.** The "grab now" signal is `status.code` flipping to
    **`O`**, or open seats going **0 → N**.
  - ⚠️ **`openReserved` interpretation (clarified 2026-07-29):** seats that show open **but reserved
    "for Students with Enrollment Permission"** are typically held for **specific students by SID**
    (Student-Specific Enrollment Permission) who simply haven't enrolled yet — so *"'Open Seats' reserved
    for Students with Enrollment Permission … does not mean that open seats are available"* to you.
    Reserved-open seats are grabbable only if the reservation is for a **group you're in** (e.g. your
    major) — not when they're held for a named individual. This is why the app shows the reserved/unreserved
    breakdown, defaults to the personalized `eligible` trigger (§10), and offers an
    `unreserved`-only trigger. ✅ *(SIS/PEACE guidance:
    <https://peace.studentorg.berkeley.edu/2015/08/13/how-do-waitlists-work-for-berkeley-classes1/>.)*
  - **`seatReservations[]` structure (verified live, 2026-07-31):** one entry per reserve-capacity
    block — `requirementGroup: {code, description}`, `maxEnroll` (seats held), `enrolledCount` (taken)
    — so **seats still open to that group = `maxEnroll − enrolledCount`**, and the per-group opens
    **sum exactly to `openReserved`** (checked across live classes; e.g. CS 188: 66 EECS/CS/ECE + 144
    permission + 7 MDes + 4 non-EECS-engineering = 221). Requirement groups are **free-form** — the
    Registrar creates them on department request (any "combination of requirements"; SIS job aid
    <https://sis.berkeley.edu/sites/default/files/how_to_set_up_reserve_capacities.pdf>), so there is
    no closed vocabulary. Shapes observed across ~34 sampled sections: single majors ("Statistics
    Majors"), major lists ("Undergraduate Students: Electrical Engineering & Computer Science,
    Computer Science, and Electrical & Computer Engineering Majors"), exclusions ("Non-EECS Declared
    Engineering Majors"), colleges ("Undeclared Students in the College of Engineering"), transfer
    admits ("New Letters & Sciences Transfer Students", "Computer Science Majors: New Transfer
    Students"), minors ("Students with a Minor in Public Policy"), grad programs ("Master of Design
    Students"), joint majors ("Bioengineering and Joint Bioengineering / Materials Science Engineering
    Majors"), and the SID-held "Students with Enrollment Permission" (code `000055`). ✅
- Because the data sits in the *initial* HTML, a plain server-side `GET` + JSON parse reads it — this is
  literally what "`classes.berkeley.edu` is the page a watcher scrapes" (§2.1) means in practice. For the
  app in **§10** this page supplies the O/W/C status + requirement-group codes; the *change detector*
  is the uncached fragment below, and the live reserved breakdown comes from the ⭐⭐ `drupal_ajax`
  source below. ✅
- **There is no ungated public JSON API.** Berkeley's official SIS Class/Student APIs are gated behind
  CalNet + Data-Owner approval (API Central → developers.api.berkeley.edu), so a personal watcher relies
  on this public page, not the API. ✅
- **Politeness (see §6.1):** the page is ~30 KB; a **1–2 minute** per-class cadence is low-impact.
  High-frequency hammering is exactly what the Acceptable-Use "don't interfere with normal operation"
  clause targets — poll gently. 🟡

**⭐ The UNCACHED real-time source — the associated-sections fragment (verified by direct fetch,
2026-07-31).** Each section page lazy-loads its "Associated Sections" table from

```
https://classes.berkeley.edu/sections/associated/<node_id>
```

where `<node_id>` is the section's Drupal node id, readable from the same drupal-settings blob at
`drupalSettings.path.currentPath` (`"node/517627"` for CS 61A LEC 001). Measured properties: ✅

- **Served uncached, every hit:** `cache-control: must-revalidate, no-cache, private`,
  `x-drupal-cache: UNCACHEABLE (no cacheability)`, `x-drupal-dynamic-cache: UNCACHEABLE`, Fastly
  `x-cache: MISS` with `age: 0` on repeated fetches. **This bypasses the entire §2.5 cache stack** —
  the numbers are rendered live per request.
- **Per-row data** (HTML fragment, one row per section of the course): **Open Seats, Enrolled,
  Enrollment Limit, Waitlisted, Waitlist limit** — everything except the reserved-seat breakdown
  (available uncached via the ⭐⭐ `drupal_ajax` source below) and the O/W/C status code (cached
  content page only). Freshness demonstrated directly: the
  fragment showed CS 61A's waitlist at 182 while the cached content page still served 163.
- **Gotcha — self-exclusion:** the response lists every section of the course **except** the node you
  asked for. A watcher therefore reads its target through a **sibling ("probe") node's** fragment:
  fetch the target page once for its node id, list siblings via its own fragment, fetch one sibling's
  page for *its* node id, then poll that sibling's fragment — the target's row is in it. One probe
  fragment covers **all** watched sections of a course per poll.
- **Politeness still applies:** uncached means every hit is origin work (~130 KB for a large course;
  ~5 KB on the wire with gzip, which the app requests),
  and the server rate-limits bursts (observed around ~220 rapid requests) — space requests and keep a
  ≥ 30–60 s cadence. 🟡

**⭐⭐ A SECOND uncached source — the reserved-seat breakdown via `_wrapper_format=drupal_ajax`
(verified by direct fetch, 2026-07-31).** The associated fragment lacks the per-group reserved
breakdown; the content page has it (`seatReservations[]`) but is dynamic-cached. The gap is closed by
requesting the content page in Drupal's AJAX render mode:

```
https://classes.berkeley.edu/content/<slug>?_wrapper_format=drupal_ajax
```

- **Served UNCACHEABLE, every hit:** `x-drupal-cache: UNCACHEABLE`, **`x-drupal-dynamic-cache:
  UNCACHEABLE`** (contrast the plain page, which returns `dynamic-cache: HIT`), Fastly `x-cache: MISS`,
  `age: 0`. Drupal rebuilds the node render per request instead of serving the ~15-min render cache. ✅
- **Payload:** a JSON array of AJAX commands; the `insert` command's `data` is the rendered section
  HTML, which contains the **"Reserved Seating → Current Enrollment"** block with one
  `<span class="detail-numeral">N</span> reserved for <group description>` row per requirement group.
  N is the group's currently-**open** reserved seats; the rows sum to `openReserved` (verified: CS 188
  = 66 EECS/CS/ECE + 144 Enrollment-Permission + 7 M.Des + 4 non-EECS-eng = 221). ✅
- **Limitation:** the AJAX HTML carries the group **descriptions but not their requirement-group
  codes**, and no `O/W/C` status — so a watcher enriches codes and reads status from the (cached)
  plain page, while taking the live open-reserved counts from here. The block is rendered twice in the
  markup (summary + detail); dedupe by description. ✅
- **Caveat on "real-time":** the UNCACHEABLE headers prove it's rebuilt per request and it is never
  staler than the plain page, but a discriminating *live-change* test (fragment/page disagreeing) was
  not obtainable during a quiet enrollment window on 2026-07-31 — all sources agreed. Treat it as
  proven-uncached and structurally real-time, pending a busy-period confirmation. 🟡
- **App usage (§10):** on a detected seat change the app fetches this to make `eligible`
  detection real-time; the plain page is polled only on the slow `content_refresh` for status + codes.

*(Also examined and rejected, 2026-07-31: the class-search view `/search/class?search=<query>` is
origin-uncached (`dynamic-cache: UNCACHEABLE`, Fastly-cached 900 s but nonce-bustable) and
server-renders result rows — but each row carries only the **unreserved** seat count (blank when 0),
no waitlist/enrolled/reserved data, and its "section closed" text is a screen-reader label for the
collapsed-row toggle, not enrollment status. Strictly less data than the fragment + ajax pair. No
other AJAX endpoints exist in the site's JS — `/sections/associated/` and Drupal-core plumbing only.)*

### 2.5 Data freshness — how stale is what you read? ⭐ (measured 2026-07-29)

**Two separate clocks** govern the race, and conflating them is the #1 source of confusion:
- **The ~6-hour waitlist batch = *when* a freed seat is reallocated** — ~6 h (4×/day); the Registrar's
  "every 3 h" is the lone outlier. Full three-source analysis lives in **§4**. ✅
- **The page cache = *how late you find out*.** For the content *pages* it is the real limiter, and it
  is **layered** — but see the end of this section and §2.4: the **associated-sections fragment
  bypasses all of it** (found 2026-07-31):

| Layer | Header signal | TTL | Bustable from the public side? |
|---|---|---|---|
| Fastly CDN edge | `via: varnish`, `x-served-by: cache-pao/chi`, `age` | `max-age=900` (15 min) — **empirically confirmed**: `age` rode 0 → ~900 then reset | ✅ yes — a unique `?_cb=` query misses it |
| Drupal internal page cache | `x-drupal-cache` | ~15 min | ✅ yes — query string is part of its key |
| **Drupal dynamic render cache** | `x-drupal-dynamic-cache` | ~15 min (or cache-tag invalidation) | ❌ **no** — unique nonces *and* `Cache-Control: no-cache` still return `HIT` |

**So a plain scrape of the content pages sits behind two stacked ~15-min caches** (edge + render): mean
detection **~15 min**, worst **~30 min**. **Cache-busting removes only the CDN/edge layer** — roughly
*halving* latency (worst ~30 → ~15, mean ~15 → ~7.5 min). It does **not** yield real-time: the **origin
dynamic render cache (~15 min) cannot be busted from outside**, so the *pages* cannot be forced to
real-time by scraping — but the **uncached associated-sections fragment can be read instead** (§2.4 and
the correction at the end of this section). ✅ *(An earlier draft called cache-busting "near real-time";
a later one said no real-time path existed at all — both corrected.)*

**Is the underlying data live? `classes.berkeley.edu` vs BerkeleyTime:**
- **`classes.berkeley.edu` is the official, upstream feed.** When Drupal actually rebuilds the page (a
  `dynamic-cache` MISS — observed ~2.2 s, vs ~0.3 s for a cached HIT, consistent with a live backend
  call), it reflects **SIS enrollment at render time**. The data is *live at the source*; the ≤15-min lag
  is purely the cache on top. ✅
- **BerkeleyTime is downstream and no fresher.** Its GraphQL history for CS 61A (pulled directly):
  `granularitySeconds: 900` (samples ~every 15 min), 367 snapshots / 4 months, **median gap 1.35 h, 33%
  of gaps > 3 h**, and its fields are *identical* to the classes.berkeley.edu blob — i.e. it scrapes the
  same source every ~15 min and stores only changes. A historical-trends tool, not a live feed, and it
  **cannot be cache-busted** to go faster. ✅ *(BerkeleyTime is run by the **ASUC Office of the CTO**,
  open-source at <https://github.com/asuc-octo/berkeleytime>; its FAQ claims enrollment is "refreshed
  continuously" (<https://berkeleytime.com/faq>), but the measured API granularity above — 15-min
  sampling — is the real ceiling, so treat "continuously" as marketing, not sub-15-min data.)*
- **Verdict:** for watching, **scrape `classes.berkeley.edu` (§2.4), not BerkeleyTime** — official,
  upstream, and via the **uncached associated-sections fragment definitively sub-15-min: real-time per
  request** (BerkeleyTime's fixed 15-min sampling never can be). The only fresher *authenticated* view
  is **CalCentral/SIS**, which UCB describes as giving **"near real-time"
  updates to rosters and waitlists**. ✅ *(SIS: <https://sis.berkeley.edu/enrollment-faq> and
  <https://sis.berkeley.edu/student>, verified 2026-07-29.)*
- **The one unmeasured link** is SIS → page-render latency (how fast a drop reaches Drupal's render).
  Untestable from the public side; the definitive check is comparing a page fetch against **CalCentral
  during a live drop**. Best evidence suggests it's small, but treat it as unknown. 🟡

**What `waitlistwarrior.net` uses:** it takes a class-page URL and polls it, across 100+ schools — i.e.
it scrapes each school's public schedule page (`classes.berkeley.edu` for Berkeley), **not BerkeleyTime**.
If it polls the pages, it's behind the same Fastly cache and "1-minute checks" only help within the
cache floor; whether it knows about the uncached fragment (§2.4) is unobservable from outside. No
*privileged* source found — the fastest public path is the fragment, which anyone (including this
repo's app) can poll. 🔴/🟡

**Can you beat the ~15-min floor from the public side? YES — via the associated-sections fragment
(corrected 2026-07-31; an earlier draft said no).** For the *content pages* the floor stands, as
tested 2026-07-29: a unique query nonce misses the CDN and Drupal internal page cache, but the
**Drupal dynamic render cache still returns `HIT`**; so do `Cache-Control: no-cache` requests and
session-style cookies (`NO_CACHE`, `SSESS…`) — all served from cache. There is **no public JSON:API**
(`/jsonapi` → 404, `?_format=json` → 406), and the official SIS Class/Student APIs are **gated**
(CalNet + data-owner approval). What that earlier sweep missed is that the pages' own
**`/sections/associated/<node_id>` fragment is served entirely uncached** (§2.4) and carries live
open-seat *and* waitlist counts per section — so **detection latency for the counts is bounded by
your poll interval**, not by any cache. The ~15-min floor now applies only to the O/W/C status
label and the requirement-group codes (the reserved-seat breakdown is uncached too — see the ⭐⭐
`drupal_ajax` source in §2.4). ✅

**The remaining (now mostly moot) unknown:** whether Berkeley invalidates the *content pages'* dynamic
render cache on enrollment change (Drupal cache tags). The fragment sidesteps the question for
seat/waitlist counts and the ajax variant for the reserved breakdown; it still matters only for how
stale the O/W/C status label and requirement-group codes can be (≤15 min). 🟡

---

## 3. Enrollment phases & appointments (the calendar the window lives inside)

- Enrollment happens in **phases**: **Phase 1**, **Phase 2**, and the **Adjustment Period**.
  Continuing students get **two** phases (1 and 2); **new** students get **one**. ✅
- **Unit caps (current, per registrar.berkeley.edu/enrollment):** caps rise each phase, and
  **waitlisted units count** toward them — it is a **hard-cap enforcement**. ✅
  - **Continuing undergraduates:** **Phase 1 = 13.5 units**, **Phase 2 = 17.5 units**. ✅
  - **New/incoming undergraduates** (single phase): **Phase 1 = 17.5 units**. ✅
  - **Graduate students:** **Phase 1 = 12 units**, **Phase 2 = 20.5 units**. ✅
  - **Adjustment Period:** up to your college's maximum unit limit, and **20.5 units is the campus-wide
    standard maximum** ("a maximum of 20.5 units" per Fall/Spring — L&S Advising, phrased campus-wide:
    <https://lsadvising.berkeley.edu/policies/unit-minimum-maximum-semester>). Explicitly confirmed for
    **L&S**, **CDSS** (<https://cdss.berkeley.edu/academics/policies/unit-semester-limits>), and
    **CED / Environmental Design** (<https://ced.berkeley.edu/advising/undergraduate-advising/undergraduate-students/policies>);
    **Haas** publishes a 13-unit minimum and otherwise **follows the standard 20.5** (exceed via major
    adviser). ✅ The one confirmed **exception is the College of Chemistry — *no* unit maximum**
    (<https://chemistry.berkeley.edu/ugrad/current-students/academic-policies>); Engineering states a
    12-unit minimum and no explicit max. **CNR/Rausser** also confirms **20.5** ("Students may enroll in
    up to 20.5 units during the Adjustment Period", per its policy doc). All verified 2026-07-29. ✅ *(The
    per-college question is now settled: 20.5 is the standard, confirmed for L&S/CDSS/CED/CNR and the Haas
    default; **Chemistry is the only no-cap exception**. The doc's earlier "Phase 1 ≈17.5" was the
    new-student figure; continuing undergrads are capped at 13.5 in Phase 1. The earlier "Engineering =
    20.5" is **retracted** — Engineering states no explicit max.)*
- Find your exact appointment times under **CalCentral → My Academics → Class Enrollment**. ✅
- **Add/Drop ("Adjustment") deadline:** classes must be added/dropped/swapped/unit-changed by
  **11:59 p.m. Pacific on Wednesday of week 4** of a Fall/Spring semester. For **Fall 2026 this falls on
  Wednesday, September 16, 2026** (instruction begins Aug 26; Sept 16 is Wed of week 4). ✅
  *(confirmed against registrar.berkeley.edu 2026–27 calendar + UE add/drop deadlines page, 2026-07-29 — see §9.)*
- **Early Drop Deadline (EDD)** courses: droppable only **through Friday of week 2** (Fall 2026:
  **Fri, Sept 4, 2026**). ✅
- **Fees (current per Registrar Fee Definitions):** **$5** late-add per class after the **Friday of week 3**;
  **$10** late-drop per class after the **Friday of week 2** (the "second Friday" of instruction). ✅
- A **department may still add students from a waitlist until the Friday *after* the add/drop
  deadline** (Fall 2026: staff waitlist adds run **Thu Sept 17–Fri Sept 18, 2026**). ✅

---

## 4. THE EXACT MECHANISM — auto-enrollment intervals ⭐

This is the core answer the doc exists to pin down.

**Reconciled per-phase timetable** — the three "conflicting" cadences are not contradictory; each
governs a *different span* of the cycle, tightening as add/drop peaks:

| Cycle span | Auto-processing cadence | Source |
|---|---|---|
| **Phase I and Phase II** (enrollment appointments) | **each weekend** | PoliSci advising ✅ |
| **Adjustment Period** (begins ~the week before classes start) | **nightly** | PoliSci advising ✅ |
| **Through the end of the third week of instruction** (peak add/drop) | **every ~6 h** (4×/day) — the Registrar's "every 3 h" is the lone outlier | SIS FAQ #1056 ✅ + SIS Campus Solutions ✅ + Econ ✅ (Registrar dissents) |
| **After the third week of instruction** | **auto-processing stops** — instructors manually admit from the waitlist if space exists | PoliSci advising + SIS FAQ #1056 ✅ |

> **Peak-cadence conflict — largely RESOLVED (2026-07-29):** **three** official sources say **~6 hours**:
> the student-facing SIS "How Waitlists Work" job aid (Enrollment FAQ #1056) states *"The enrollment
> system checks waitlists **4 times per day** through the 3rd week of instruction. After that, waitlists
> are processed manually"* (<https://sis.berkeley.edu/help/enrollment-faq/how-waitlists-work>); the SIS
> **Campus Solutions** staff job aid says *"Waitlists run automatically **every six hours**"*
> (<https://sis.berkeley.edu/run-waitlist-demand>); and the Econ FAQ says *"four times a day."* Only the
> **Registrar's** "How to Enroll" page says *every three hours* — the **lone outlier**, likely stale.
> **Treat ~6 h (4×/day) as the true batch interval;** 3 h is optimistic. 🟡 *(A third-party statistical
> analysis of BerkeleyTime promotion timing reportedly found ~3-hour clustering, but on 15-min-sampled
> data and not reproduced here — suggestive at most, and outweighed by the three ~6 h primary sources.)*
> 🟡 *(exact firing clock-time still unpublished.)*

- The uncontested per-phase wording: *"Wait Lists are run every weekend starting with Phase I and then
  nightly during the Adjustment Period, which begins the week before classes begin"* (PoliSci advising);
  the Registrar's *"processed every three hours through the end of the third week of instruction"* is the
  peak-cadence quote now treated as the 3 h outlier (blockquote above). ✅ *(independent official UCB
  sources — see §9.)*
- Automatic enrollment off the waitlist can continue **up until the add deadline** for the term/session,
  but the *automatic* peak job ends with week 3; after that, adds from the waitlist are manual. ✅
- **Sequential rule:** students are pulled off an automatic waitlist **in order**, *provided a seat is
  available for their enrollment group*. ✅
- **Summer Sessions differ — and this is the case behind the CS 189 anecdote:**
  - **Per-session add/drop deadlines (confirmed).** Summer runs overlapping sessions of different
    lengths — **A (6-wk), B (10-wk), C (8-wk), D (6-wk), E (3-wk), F (3-wk), and 12W (12-wk)** — and
    the official Session Dates & Deadlines table gives **each session its own "Deadline to Add" and
    "Deadline to Drop"** column. "You may enroll in classes up until the add deadline for each
    session." Do not assume Fall/Spring dates. ✅
  - **Auto-processing runs through *week 2* of the session, not week 3.** "Students on most waitlists
    are automatically enrolled as space becomes available … Students continue to be automatically
    enrolled from the waitlist through the second week of the session" (SS help center), and
    "Automatic enrollment off the waitlist can happen up until the add deadline for each session"
    (summer.berkeley.edu). ✅
  - **The waitlist mechanism itself is the same** — opt-in via the "Waitlist if class is full" box in
    CalCentral, automatic sequential promotion as space opens, email notice to your Berkeley address. ✅
  - **The intra-window frequency (the Fall/Spring ~6 h figure) is *not* published for summer.** No
    official summer source states an hourly/nightly cadence; only the *span* (through week 2) is
    documented. Notably, the SIS "every six hours" schedule is explicitly scoped to **"fall and spring"**
    (<https://sis.berkeley.edu/run-waitlist-demand>), so it does **not** even claim to cover summer —
    treat any specific summer interval as genuinely unconfirmed. 🟡

- **Fixed wall-clock schedule vs. rolling offset — not officially published.** UCB does **not** publish
  the exact clock times the batch fires (no "runs at 00:00 / 03:00 / 06:00 …" statement exists in any
  Registrar, SIS, or departmental source), so whether the peak job is phase-locked to fixed wall-clock
  times or runs on a rolling offset is **unverified from public sources.** 🟡 Two cautions against
  assuming a clean, predictable clock:
  - The *cadence number itself* disagrees across sources (~6 h vs the Registrar's 3 h — see the
    three-source analysis in the blockquote above), so even the interval isn't a literal fixed timetable
    and a run is not guaranteed to land on a predictable clock minute. 🟡
  - No credible crowd-sourced observation (r/berkeley, Daily Cal, student blogs) pinning auto-enrollment
    to specific clock times could be found; anecdotes describe waking up already enrolled ("overnight")
    but do not establish a repeatable fixed hour. 🔴/🟡
  - **Practical takeaway:** plan for a multi-hour window in peak add/drop (**~6 h**, or ~3 h at the
    most optimistic) and do **not** try to time-camp a predicted run minute — the safe model is "a run
    will happen within the interval, at an hour you can't reliably predict," so watch continuously and
    act on the count change, not on a guessed schedule.

**So the practical window length = time until the next scheduled batch run:**
- Deep in add/drop (through week 3): **up to ~6 hours** (~3 h if the more optimistic Registrar cadence
  applies — treat ~6 h as the working figure, per §4).
- Adjustment Period, pre-peak: **overnight** (until the nightly run).
- Phase 1 / Phase 2: **until the weekend run** (can be days).

The earlier in the cycle, the *longer* the window — but also the more competition, since more people
are shopping. The peak-period window is short but frequent. Because the exact firing times are
unpublished (and even the interval count varies by source), the window's *end* is not predictable to
the minute — another reason continuous watching beats trying to guess the next run.

### 4.1 Automatic vs. manual waitlists
- **Automatic:** the batch processor described above pulls in sequential order. ✅
- **Manual:** the **instructor/department** selectively admits from the waitlist and **need not follow
  sequential order** — being #1 guarantees nothing. You can see which type a section uses in the
  Class Schedule's current-enrollment/restrictions info. ✅
- **Implication for watching:** a watcher is most useful on **automatic** waitlists and on **open-seat
  / reserved-seat-release** situations. On manual waitlists, speed doesn't help — the professor
  decides.

---

## 5. What blocks an auto-enroll (why #1 gets skipped)

The batch will **skip** a waitlisted student — passing the seat down (or into the open-seat window) —
when that student:

- Would **exceed the phase unit cap** (remember: **waitlisted units count** toward the cap, and it's a
  **hard cap** — continuing undergrads: 13.5 in Phase 1 / 17.5 in Phase 2; Adjustment Period up to the
  campus-wide standard of 20.5, with Chemistry the no-cap exception — see §3). ✅
- Has a **time conflict** with an already-enrolled section (same error that blocks manual enroll also
  blocks waitlist promotion). ✅
- Has an **enrollment hold / block** on their account — **administrative holds must be cleared before
  any enrollment**, so an unresolved hold stops a waitlist promotion just like a manual add. ✅
  *(Cal Student Central, "Blocks": <https://studentcentral.berkeley.edu/blocks>; SIS Enrollment FAQ:
  <https://sis.berkeley.edu/enrollment-faq> — verified 2026-07-29.)*
- Isn't in the **enrollment group** the open seat is reserved for, or **doesn't meet the section's
  prerequisites / reserved-seat requirements**. ✅
- **Their linked related section (discussion/lab) has no open seat** — for a lecture with a required
  discussion/lab, the waitlist enrolls you **only if that related section also has room**; if the
  discussion is full, you're skipped even at #1. ✅ *(NEW — SIS "How Waitlists Work," Enrollment FAQ
  #1056: <https://sis.berkeley.edu/help/enrollment-faq/how-waitlists-work> — verified 2026-07-29. This is
  a real, commonly-missed skip trigger: watch the **discussion** section's open seats too, not just the
  lecture.)*

Each skip is a moment a lower/faster person can win — a prime "window" trigger.

---

## 6. The watcher-tool pattern (e.g. `waitlistwarrior.net`) 🔴 vendor claims

Documented here because it's the practical application of the mechanism above. **All figures below are
the vendor's own marketing/site copy — treat as unverified claims, not endorsement.**

**What it does:** you submit a class page URL; it polls the enrollment counts at a set interval and
emails/Telegrams you the moment enrolled/waitlisted/open-seat numbers change, with a link to enroll.
It advertises history tracking (every seat count — enrolled / waitlisted / capacity / open seats —
tracked over time) and support for **~80 universities** (re-checked 2026-07-29): the full UC system plus,
among many others, **USC, Stanford, Caltech, the Ivies (Harvard/Yale/Princeton/Columbia/Cornell/Brown/
Dartmouth/UPenn), MIT, Duke, Big Ten (Michigan, Ohio State, Penn State, Wisconsin, …), UT Austin, Texas
A&M, Georgia Tech, NYU**, and a few international (**Oxford, Cambridge, Imperial, UCL, LSE, McGill**) —
**UC Berkeley included.** The headline count has varied over time (site currently implies ~50–80). 🔴
*(vendor site copy, verified 2026-07-29: <https://waitlistwarrior.net>.)*

**Tiers (as advertised, verified against live site copy 2026-07-29):**

| Tier | Price | Classes watched | Check interval | Alerts | Extra |
|---|---|---|---|---|---|
| Free | $0 | 1 | every **30 min** | Email | — |
| Plus | **$12** / semester | up to 3 | every **5 min** | Email + Telegram | — |
| Pro | **$19** / semester | up to 6 | every **1 min** ("30× faster") | Email + Telegram | Browser extension for SSO/private portals (CalCentral) |

- Paid = **one-time payment, ~4 months of access, no auto-renew / no subscription** (framed as
  covering server costs). 🔴 *(live site copy, confirmed 2026-07-29.)*
- The **browser extension** (Pro only) exists specifically because CalCentral is behind CalNet SSO and
  can't be scraped server-side — the extension reads the authenticated page client-side. 🔴
- The recruiting-message paraphrase — *"$12 or $19 … checks every 1 or 5 minutes … more watched
  classes"* — **reconciles exactly** with the site's two paid tiers (Plus $12/5-min/3 classes,
  Pro $19/1-min/6 classes); it was a loose restatement of the same tiers, not a separate offer. 🔴

**Honest assessment vs. the mechanism:**
- **Any section where a seat can momentarily open (the general case):** a 1-minute checker inside a
  multi-hour batch window is genuinely valuable — because Berkeley doesn't lock the freed seat (Model C,
  §1.1), a fast **direct** enrollment can take it **ahead of the waitlist**. This is the tool's core
  value, and it is **not** limited to empty-waitlist situations.
- **Reserved-seat releases and batch-skips** (§1.1, §5) are especially winnable moments for a fast watcher.
- **Manual-waitlist sections** are the real exception — the department admits by hand and need not follow
  order (§4.1), so reaction time helps little; a watcher still tells you the count moved, but winning the
  seat isn't up to your speed.
- The anecdotes ("got in from 200+ on CS 189", "friend into Chem 1A") are exactly what Model-C grabbing
  predicts — a long waitlist doesn't stop a watcher from catching a freed seat first — though as
  individual testimonials they remain **unverifiable.** 🔴
- ⚠️ **Policy caveat (see §6.1):** the two data sources the tool touches carry *different* risk. Scraping
  the **public** class pages is low-risk but not zero (robots + an Acceptable-Use "don't interfere with
  normal operation" clause apply); the **Pro-tier browser extension** that reads **CalCentral behind
  CalNet SSO** is the riskier surface, because you remain responsible for everything done under your
  CalNet ID and the extension routes authenticated-session data to a third party. Read §6.1 before
  relying on the extension.

### 6.1 Legitimacy & policy — is watching/scraping allowed? 🟡

Not legal advice — this is the **relevant policy language**; students should check the **current** terms
themselves before using any tool. UCB has no single page that says "scraping the class schedule is
allowed/forbidden," so this is assembled from the primary sources (see §9).

**(a) Scraping the public `classes.berkeley.edu` pages — lower risk, not zero.**
- The site is public and requires no login, and there is **no site-wide crawl ban**: the live
  `robots.txt` (HTTP 200, a stock Drupal file) has **no `Crawl-delay` and no blanket `Disallow: /`**. ✅
  *(fetched directly — see §9.)*
- **But** it explicitly `Disallow:`s **`/search/`**, which is the path the class-schedule **search
  interface** lives under — so a crawler that hammers the search endpoint is going against the site's
  stated robots preference, even though individual public course/section content is not itself
  disallowed. 🟡 *(robots.txt is advisory, not a contract, but it is the operator's expressed wish.)*
- The **Acceptable Use of Technology Resources policy** prohibits "**knowingly performing an act which
  will interfere with the normal operation of computers, terminals, peripherals, or networks**." So the
  live constraint on scraping public pages is **rate/impact**, not the act of reading public data:
  poll politely, don't hammer. 🟡 *(This is the "public page, generally scrapeable but rate-limits/robots
  may apply" case.)*
- **Takeaway:** watching public counts at a modest interval is broadly consistent with policy; aggressive
  high-frequency polling is where the interference clause could bite.

**(b) A browser extension reading CalCentral behind CalNet SSO — the riskier one. 🟡**
- The **CalNet User Terms of Service** state: "**A CalNet passphrase must not be revealed to any other
  person for any reason,**" and that users "**will be held responsible if inappropriate activities are
  conducted under the authority of your CalNet ID.**" ✅
- **Key distinction:** an extension **you** install that reads a page **in your own already-authenticated
  session** does **not**, by itself, "reveal your passphrase to another person" — so it is *not* the
  same as the classic prohibited credential-sharing. 🟡 That is the narrow sense in which the vendor's
  "we never see your password" framing can be literally true.
- **However**, two real exposures remain: (1) you stay **responsible for everything done under your
  CalNet ID**, so anything the extension does in-session is *your* action for policy purposes; and (2) a
  third-party extension that **transmits authenticated-session data (or scraped private pages) to a
  vendor server** is exactly the kind of unauthorized onward disclosure/data-handling the Acceptable Use
  and Electronic Communications policies are concerned with — and the AUP separately bars
  "**attempting to access, accessing, or exploiting resources one is not authorized to access.**" 🟡
- **No public UCB source explicitly names browser extensions or session automation** as permitted or
  banned, so this is a **judgment call under general policy**, not a settled rule. The conservative read:
  reading your *own* CalCentral page client-side is defensible; **shipping that authenticated data to a
  third party is the part a student should scrutinize** (what it sends, to whom, and whether it exceeds
  what you're authorized to redistribute). 🟡
- **Student-conduct angle:** nothing found elevates polite public-page watching to a conduct violation;
  the credential/authorized-access rules above are the operative ones, and they bite hardest on the
  *authenticated-session + third-party-server* combination, not on reading a public catalog. 🟡

**Bottom line:** public-page watching at a sane rate is low-risk; the CalNet-authenticated extension is
the piece to evaluate carefully against the **current** CalNet ToS and Acceptable Use Policy before use.

### 6.2 Why this stays watch-only — the auto-enroll / credentials question 🟡

The obvious next step is "just auto-enroll me when a seat opens." Here's the honest reason the tooling
deliberately stops at *notify*:

- **Watching needs no credentials** (seat counts are public — §2.4). **Enrolling** requires an
  authenticated CalCentral action — a categorically different, riskier thing.
- **DUO 2FA is a wall.** CalNet requires a Duo push per login, so you *cannot* headlessly auto-enroll
  without a human approving 2FA. Unattended botting fights the security design by construction.
- **The credential-hosting trap.** A *hosted, multi-user* auto-enroller would have to collect students'
  CalNet passphrases — violating the CalNet ToS ("passphrase must not be revealed… you're responsible for
  activity under your CalNet ID") and creating a credential honeypot. This is the "really sketchy"
  scenario; don't build it.
- **The only defensible form is local + single-user + interactive login:** you log in and approve Duo
  yourself, and a script automates just the final enroll click in *your own* session. No stored/shared
  credentials — but it's still ToS-gray (automating enrollment) and an ethical arms-race.
- **The genuinely safe speed-up is human-in-the-loop:** on alert, pre-open the CalCentral enroll page so
  you click "Enroll" in ~1 second. Captures most of the benefit, needs no credentials, stays on the right
  side of the line.

Because the batch window is typically **hours** (~6-h cycles, §4) and fast polling detects the drop
within **minutes** (§2.5), you keep nearly the whole window to click — so unattended auto-enroll buys
little over a pre-staged one-click — at much higher risk. **This tool therefore notifies; it does not
enroll, and never handles CalNet credentials.** 🟡

---

## 7. A student's practical playbook (derived from the mechanism)

1. **Watch the public page** (`classes.berkeley.edu`) for the target section's counts.
2. **Pre-stage** the enroll action in CalCentral (Schedule Planner cart) so acting is one click.
3. **Know your window length** from where you are in the calendar (weekend → nightly → ~6-hourly, §4). Do
   **not** try to predict the exact minute a batch fires — the clock times are unpublished and even the
   interval count varies by source (§4); watch continuously and act on the count change instead.
4. **Distinguish the cases:** empty/short waitlist or reserved-release = *race to enroll*; live
   automatic waitlist = *get on it and let the batch work*; manual waitlist = *contact the
   instructor/department, speed won't help*.
5. **Mind the unit cap** — waitlisted units count; drop dead weight so a promotion isn't skipped.
6. **Respect deadlines** — Wed 11:59pm PT of week 4 (Fall/Spring); Early-Drop courses by Fri week 2;
   department waitlist adds until the Friday after add/drop. **Summer:** deadlines are **per-session**
   (A–F, 12W), and auto-enrollment off the waitlist runs only **through week 2** of that session — check
   the Session Dates & Deadlines table for your specific session. ✅

---

## 8. Open questions / to verify in future refinements

- [x] **Can detection beat the ~15-min cache floor?** (2026-07-31) RESOLVED — **yes**: the
      `/sections/associated/<node_id>` fragment is served **uncached** (headers verified, §2.4) with
      live per-section open-seat and waitlist counts; detection is bounded by the poll interval. The
      app (§10) polls it as its primary tier.
- [ ] **Does an enrollment change invalidate the content pages' Drupal dynamic render cache, or is it
      TTL-only (≤15 min)?** (§2.5) Now only affects how stale the O/W/C status label and
      requirement-group codes can be — seat counts come from the uncached fragment and the
      reserved breakdown from the uncached `drupal_ajax` variant (§2.4). A query nonce, `no-cache`, and cookies
      could not resolve it (all return `dynamic: HIT`); test by comparing a cache-busted page fetch
      against **CalCentral during a live drop** in add/drop. 🟡
- [ ] **SIS → Academic-Guide render latency** — the one unmeasured hop (how fast a drop reaches Drupal's
      render). Untestable from the public side; same CalCentral comparison would bound it. (§2.5) 🟡
- [x] **Is the true peak waitlist interval 3 h or 6 h?** (2026-07-29) RESOLVED to **~6 h (4×/day)** —
      three official sources agree (SIS FAQ #1056, SIS Campus Solutions job aid, Econ) against the
      Registrar's "every 3 h" **lone outlier** (likely stale); full analysis and citations in §4.
      Residual 🟡: the exact wall-clock firing time is still unpublished.
- [x] **Per-college Adjustment-Period max units.** (2026-07-29) **FULLY RESOLVED:** **20.5 is the
      campus-wide standard maximum** — confirmed for **L&S, CDSS, CED, and CNR** (+ Haas default). The one
      confirmed exception is **Chemistry (no cap)**; Engineering publishes a 12-unit minimum only. (§3) ✅
- [x] **Reconcile the peak vs "nightly" vs "each weekend" cadences into one per-phase timetable.**
      DONE (§4): each cadence governs a different span — **weekend** in Phases I/II, **nightly** in the
      Adjustment Period, **~every 6 h** through the end of week 3 (the Registrar's 3 h is the outlier),
      then **manual**. Confirmed by SIS/Econ (~6 h) + PoliSci advising (weekend/nightly). ✅
      *(Still open: the parallel Summer Sessions cadence — tracked as its own item below.)*
- [x] Confirm whether CalCentral **hard-blocks** a walk-up from direct-enrolling into a
      full-with-waitlist section, or whether a freed seat is grabbable. **RE-RESOLVED (§1.1) — earlier
      answer CORRECTED.** The prior "it hard-blocks" conclusion over-read one table. The Registrar's
      "Enrollment Attempt Failed" outcome applies only when the section is **actually full (0 open
      seats)** and you declined the waitlist — it does **not** describe a seat that just opened from a
      drop. Berkeley's auto-enroll is a **periodic batch that does not lock a freed seat between runs**
      (PeopleSoft "Model C" — cross-university sources in §9), so a fast **direct** enrollment **can and
      does grab an open seat ahead of the waitlist**, not only when the list is empty. 🟡 *(mechanism
      inferred from PeopleSoft config + convergent student/vendor evidence; UCB's per-section seat-hold
      setting is unpublished.)* Residual nuance: a "waitlist not processed yet" state can still route an
      attempt onto the list, so a grab isn't guaranteed every single time — but that is "sometimes you
      miss," not "you can never jump the batch."
- [x] Confirm current-year **unit caps** and whether they vary by college. RESOLVED (§3): continuing
      undergrads **13.5 (Phase 1) / 17.5 (Phase 2)**; new undergrads **17.5 (single phase)**; grads
      **12 / 20.5**; **Adjustment Period per the item above** (20.5 campus-wide standard, Chemistry the
      no-cap exception). Waitlisted units count; **hard-cap enforcement**. The old "Phase 1 ≈17.5" was the
      new-student number; the earlier "Engineering = 20.5" is **retracted** (Engineering publishes a
      12-unit minimum, no explicit max). ✅
- [x] Nail down **Summer Sessions** per-session add deadlines and processing cadence (differs from
      Fall/Spring; relevant to the CS 189 summer anecdote). RESOLVED (§4): (a) **per-session add/drop
      deadlines confirmed** — sessions A(6wk)/B(10wk)/C(8wk)/D(6wk)/E(3wk)/F(3wk)/12W each get their own
      add & drop dates in the official Session Dates & Deadlines table ✅; (b) **auto-processing span is
      through the second week of the session** (SS help center), up to each session's add deadline
      (summer.berkeley.edu) — note this is **week 2, vs week 3 in Fall/Spring** ✅; (c) the **waitlist
      runs the same way** (opt-in checkbox, automatic sequential promotion, email notice) ✅. *(Still
      open: the intra-window **frequency** in summer — no official source gives an hourly/nightly
      number, so the Fall/Spring ~6 h cadence is unconfirmed for summer. 🟡)*
- [x] Whether the peak job runs on a fixed wall-clock schedule (predictable) or a rolling offset.
      ADDRESSED (§4) — **unknowable from public sources.** UCB publishes **no** exact clock times for the
      batch (nothing like "runs at 00:00/03:00/06:00…" in any Registrar/SIS/departmental page), so
      fixed-vs-rolling cannot be confirmed. 🟡 Two findings argue *against* a clean predictable clock:
      (a) the cadence number itself disagrees across sources (~6 h vs the Registrar's 3 h — §4), so it
      cannot be a literal fixed timetable; and (b) no credible crowd-sourced observation
      (r/berkeley/Daily Cal/blogs) ties auto-enrollment to a repeatable clock hour. Practical resolution:
      treat **~6 h** as the working interval (~3 h optimistic), don't time-camp a predicted run, watch
      continuously. ✅ *(exact firing minute still unpublished 🟡)*
- [x] Legitimacy/ToS: does scraping `classes.berkeley.edu` or extension-reading CalCentral violate any
      UCB acceptable-use policy? RESOLVED (§6.1) — **no single UCB page rules on it; assembled from
      primary sources.** (a) **Public scraping is lower-risk**: live `robots.txt` has no site-wide crawl
      ban / no `Crawl-delay` (✅ fetched directly) but does `Disallow: /search/` (the class-search path),
      and the **Acceptable Use Policy** bars "knowingly … interfer[ing] with the normal operation of …
      networks" — so the real limit is *polling rate*, not reading public data. 🟡 (b) **The
      CalCentral/CalNet extension is the riskier surface**: the **CalNet ToS** ("passphrase must not be
      revealed … held responsible for activities under your CalNet ID") isn't literally broken by an
      extension reading your *own* logged-in session, **but** you own everything done under your ID and
      **shipping authenticated-session data to a third-party vendor** implicates the AUP's
      unauthorized-access/interference clauses and the UC ECP. No source explicitly names browser
      extensions, so it's a judgment call — students should check the **current** terms. 🟡 *(framed as
      policy language, not legal advice.)*

---

## 9. Sources

- Office of the Registrar — How to Enroll in Classes: <https://registrar.berkeley.edu/enrollment/how-to-enroll-in-classes/>
- Office of the Registrar — Guide to Enrollment Rules (PeopleSoft enrollment-outcomes table: hard-block vs. waitlist behavior): <https://registrar.berkeley.edu/wp-content/uploads/Guide-to-Enrollment-Rules-v1.h-8-31-18.pdf>
- Student Information Systems (SIS) — Waitlists: <https://sis.berkeley.edu/topics/waitlists>
- Office of the Registrar — Enrollment (per-phase unit caps 13.5/17.5, grad 12/20.5, Adjustment = college max, waitlisted units count as hard cap): <https://registrar.berkeley.edu/enrollment/>
- Office of the Registrar — Fee Definitions (current late-add **$5**/course after Friday of week 3; late-drop **$10**/course after Friday of week 2 — confirmed 2026-07-29): <https://registrar.berkeley.edu/tuition-fees/fee-definitions/>
- Office of the Registrar — 2026–27 Academic Calendar & Calendars index (Fall 2026 add/drop "Adjustment" deadline **Wed Sept 16, 2026, 11:59 p.m. PT**; instruction begins Aug 26): <https://registrar.berkeley.edu/calendars/>
- Undergraduate Education — Add/Drop Deadlines and Policies (Fall 2026: student add/drop deadline **Wed Sept 16**; staff waitlist adds **Thu Sept 17–Fri Sept 18**; EDD courses drop by Fri of week 2): <https://ue.berkeley.edu/faculty-staff/campus-communications/adddrop-deadlines-and-policies>
- Student Information Systems (SIS) — Unit Limits: <https://sis.berkeley.edu/help/enrollment-faq/unit-limits>
- L&S Advising — Enrollment (Add or Drop a Course): <https://lsadvising.berkeley.edu/progress-planning/schedule-planning-and-enrollment/enrollment-add-or-drop-course>
- Political Science Dept. — Course Enrollment Strategies (per-phase waitlist cadence): <https://polisci.berkeley.edu/undergraduate-program/requirements-major/course-enrollment-strategies>
- Berkeley Summer Sessions — Adding Courses and Waitlisted Courses ("Automatic enrollment off the waitlist can happen up until the add deadline for each session"): <https://summer.berkeley.edu/enrollment-changes/adding-courses-and-waitlisted-courses>
- Berkeley Summer Sessions — Enrollment Changes (per-session add/drop deadlines): <https://summer.berkeley.edu/enrollment-changes>
- Berkeley Summer Sessions — Session Dates & Deadlines (per-session add/drop table; sessions A/B/C/D/E/F/12W with 3/6/8/10/12-wk lengths): <https://summer.berkeley.edu/courses/session-dates-deadlines>
- Berkeley Summer Sessions — Waitlisted Courses (Zendesk; "Students continue to be automatically enrolled from the waitlist through the second week of the session"): <https://ssall.zendesk.com/hc/en-us/articles/360041411274-Waitlisted-Courses>
- Economics Dept. — Waitlist and Enrollment FAQs (states the waitlist is "processed automatically **four times a day**" — conflicts with the Registrar's "every three hours," evidence there is no single published wall-clock timetable): <https://econ.berkeley.edu/sites/default/files/Waitlist%20and%20Enrollment%20FAQs%20(1).pdf>
- **SIS — "Run a Waitlist on Demand" (system-of-record job aid; "Waitlists run automatically every six hours in Campus Solutions (until end of third week of fall and spring)"; staff can trigger a run manually — verified 2026-07-29):** <https://sis.berkeley.edu/run-waitlist-demand>
- **SIS — "How Waitlists Work" (Enrollment FAQ #1056; the authoritative student-facing mechanism — verified 2026-07-29 via text export). Key quotes: "The enrollment system checks waitlists **4 times per day** through the 3rd week of instruction. After that, waitlists are processed manually"; the system "begins at the top of the waitlist" and **skips** ineligible students (reserved-seat/prereq fail, **linked discussion/lab has no open seat**, a hold, or a time conflict); "each waitlist will count towards your unit limit":** <https://sis.berkeley.edu/help/enrollment-faq/how-waitlists-work>
- SIS — Enrollment FAQ (CalCentral gives "near real-time" roster/waitlist updates; holds must clear before enrolling): <https://sis.berkeley.edu/enrollment-faq>
- Cal Student Central — Blocks/Holds (administrative holds must be cleared before enrollment): <https://studentcentral.berkeley.edu/blocks>
- L&S Advising — Unit Minimum & Maximum in a Semester (L&S max = 20.5 units Fall/Spring — verified 2026-07-29): <https://lsadvising.berkeley.edu/policies/unit-minimum-maximum-semester>
- College of Chemistry — Academic Policies (no unit maximum): <https://chemistry.berkeley.edu/ugrad/current-students/academic-policies>
- CDSS — Unit/Semester Limits ("up to 20.5 units in a Fall or Spring semester"): <https://cdss.berkeley.edu/academics/policies/unit-semester-limits>
- College of Environmental Design — Undergraduate Policies (max 20.5 units without adviser permission): <https://ced.berkeley.edu/advising/undergraduate-advising/undergraduate-students/policies>
- Berkeley Engineering — Scholarship & Progress (12-unit minimum; no explicit maximum published): <https://engineering.berkeley.edu/students/undergraduate-guide/policies-procedures/scholarship-progress/>
- PEACE @ UC Berkeley — How Do Waitlists Work: <https://peace.studentorg.berkeley.edu/2015/08/13/how-do-waitlists-work-for-berkeley-classes1/>
- **Cross-university PeopleSoft / Campus Solutions behavior (the basis for the §1.1 seat-grab correction — Berkeley runs the same software):**
- Oracle — PeopleSoft Student Records PeopleBook, Managing Wait Lists (the **"Auto Enroll from Wait List"** control on the class's Enrollment Cntrl tab; when it is **not** selected, students can **bypass the wait list to enroll if a seat is available** — the Model-C mechanism): <https://docs.oracle.com/cd/E29376_01/hrcs90r5/eng/psbooks/lssr/htm/lssr35.htm>
- University of Iowa Registrar — Seat Reservation & Waitlist Integration (how open seats interact with waitlist auto-enroll in PeopleSoft; the config gap that lets direct enrollment beat the batch): <https://registrar.uiowa.edu/seat-reservation-and-waitlist-integration>
- Northern Arizona University — Class Wait List FAQ (PeopleSoft **Model B**: "the class **remains closed** until a process runs that automatically enrolls students from the wait list"; batch runs every two hours; being waitlisted does not guarantee a seat): <https://in.nau.edu/registrar/class-wait-list-faq/>
- Colorado State University — Registration Waitlist FAQs (PeopleSoft **Model A**: the freed seat is reserved for the notified student, who has **24 hours** to register — no grab window): <https://thehub.colostate.edu/registration-records/registration-waitlist-faqs/>
- University of Denver — Closed Seats & Waitlists (Model-A notify-and-reserve; "waitlisting does not guarantee a seat"): <https://www.du.edu/registrar/registration/how-register/waitlists>
- UNC / Baylor / UT-Austin registrar waitlist FAQs (independent confirmation that a waitlist **does not guarantee** a seat and that students are urged to enroll in open sections first — consistent with seats being claimable outside strict list order): <https://registrar.unc.edu/waitlist-faq/>
- **Legitimacy/policy (§6.1):**
- `classes.berkeley.edu/robots.txt` (fetched directly; HTTP 200; stock Drupal file — no `Crawl-delay`, no site-wide `Disallow: /`, but `Disallow: /search/`): <https://classes.berkeley.edu/robots.txt>
- Office of Ethics, Risk & Compliance — Acceptable Use of Technology Resources ("Acceptable Use Policy"; prohibits interfering with normal operation of networks, using accounts you're not authorized to use, and accessing resources you're not authorized to access): <https://oercs.berkeley.edu/policies/campus-policy-library/acceptable-use-technology-resources-acceptable-use-policy>
- CalNet — User Terms of Service ("A CalNet passphrase must not be revealed to any other person for any reason"; responsibility for activity under your CalNet ID): <https://calnet.berkeley.edu/calnet-me/calnet-user-terms-service>
- Information Security Office — Campus Online Activities Policy (CalNet identity mgmt, privacy, allowable-use context): <https://security.berkeley.edu/policy/campus-online-activities-policy>
- Information Security Office — UC Electronic Communications Policy (allowable use / privacy of electronic communications): <https://security.berkeley.edu/policy/electronic-communications-policy>
- Class Schedule / Academic Guide: <https://classes.berkeley.edu>
- CalCentral: <https://calcentral.berkeley.edu>
- Vendor (claims only): <https://waitlistwarrior.net>
- Berkeley API program (SIS APIs gated behind CalNet + Data-Owner approval; no ungated public enrollment API): <https://developers.api.berkeley.edu> / <https://api-central.berkeley.edu/apis>
- **Live data source verified by direct fetch (§2.4):** section content page, e.g. <https://classes.berkeley.edu/content/2026-fall-compsci-61a-001-lec-001> — enrollment JSON embedded at `drupalSettings.ucb.enrollment.available.enrollmentStatus`.
- **BerkeleyTime GraphQL API (student/ASUC; §2.5):** <https://berkeleytime.com/api/graphql> — pulled CS 61A `enrollment(){ latest history }` directly: `granularitySeconds: 900`, 367 snapshots / 4 months, median gap ~1.35 h (a ~15-min sampler, not a live feed).
- **Caching layers verified by direct fetch (§2.5, 2026-07-29):** `classes.berkeley.edu` served via **Fastly** (`via: varnish`, `x-served-by: cache-pao/chi`, `cache-control: max-age=900`); a unique `?_cb=` nonce, `Cache-Control: no-cache`, and session-style cookies all still return `x-drupal-dynamic-cache: HIT`; no public JSON:API (`/jsonapi` → 404, `?_format=json` → 406).

---

## 10. The companion app — `cal-seat-sniper` (does what the service does, locally)

A small, dependency-free local watcher built from this doc's mechanism. It reproduces the *core* of
`waitlistwarrior.net` (§6) for your own classes, with no account, no payment, and no third-party server.

- **What it does:** polls each class's course via the **§2.4 uncached associated-sections fragment**
  (through an auto-discovered sibling "probe" node; one request per course per poll), reads the live
  open-seat/waitlist counts, and on any movement fetches the **§2.4 uncached `drupal_ajax` variant**
  for the live per-group reserved breakdown (the plain content page is polled only on a slow refresh
  for O/W/C status + requirement-group codes). It **notifies you when a seat opens** — by default via the
  personalized `eligible` trigger: the user lists their majors/programs (or requirement-group
  codes) in `"reserved_groups"` and the app pings when **unreserved seats plus seats open in matching
  `seatReservations[]` blocks** (§2.4) increase; with no groups configured that gracefully means
  unreserved seats only. `--show-reserved` prints each class's groups verbatim to copy from (SID-held
  "Enrollment Permission" blocks never match by text). Further triggers: `*` (ANY open-seat
  increase, reserved included — a reserved seat can still be open *to you*, §5),
  `unreserved`, `reserved`, `waitlist` (a spot on the waitlist opens up), `capacity`
  (the course was expanded), `status`. Multiple triggers in one
  poll coalesce into **one** alert; optional cooldown + repeat-while-open control pacing. That alert is
  your cue to fire a pre-staged direct enroll in CalCentral inside the batch **window** (§1.1, §4).
- **Data sources:** the public fragment + uncached `drupal_ajax` reserved breakdown + content-page
  JSON (§2.4) — no CalNet, no gated API.
- **Freshness (§2.5):** seat/waitlist **and reserved-group detection ≈ the poll interval** (default
  60 s) — both the fragment and the reserved breakdown are uncached. Only the `O/W/C` status label
  and the requirement-group codes ride the ≤15-min page cache. `--no-fast-poll` reverts to page-only polling with the old
  ~15-min-mean behavior; `bust_cache` then ~halves that.
- **Notifications:** native macOS desktop alert + sound by default; optional email (SMTP) and Telegram,
  mirroring the service's channels. Secrets come from an env var **or** an inline config field
  (which must stay gitignored).
- **Politeness by design:** default 60 s cadence with jitter, one shared fragment request per course,
  ~0.75 s spacing between its own requests (far under the observed ~220-request burst limit), honest
  User-Agent, retry/backoff, persistent state so you're not re-alerted on restart — consistent
  with §6.1.
- **What it deliberately does *not* do:** touch CalCentral behind CalNet, handle credentials, or enroll
  for you (§6.2) — it only reads public pages and tells you *when* to act.
- **Location:** the repo root (`snipe.py`; see `README.md` for setup — `python3 snipe.py`). 106 offline
  unit tests in `tests/`, including the fast-poll flow against a faked server.

---

*This document is machine-refined. Each refinement pass should: verify one 🟡/🔴 claim against a
primary UCB source and upgrade or correct its tag, resolve one item in §8, and preserve the confidence
tagging discipline. Do not remove the accuracy caveats in §1.1 and §6.*
