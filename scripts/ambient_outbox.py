#!/usr/bin/env python3
"""PL-A1c — the ambient-telemetry OUTBOX: the durable-drain half of the pair
that opens with PL-A1b's digester.

A1b's `_insights_session._spool_append` writes one rollup line per digested
session to `<data_dir>/insights/rollups/<tenancy>.jsonl` and NEVER clears it —
that spool is an AT-LEAST-ONCE queue (a crash between append and the registry
stamp re-digests, hence re-spools, the same session — see
`_insights_session._spool_append`'s own docstring). This module is the reader
that turns that at-least-once queue into EXACTLY-ONCE delivery to the
project-context REST door (PC-A2): it dedups by `session_id`, tracks
delivered/dead-lettered sessions in a small per-tenant state file, and never
drops a still-pending rollup even under a durable row-count cap.

Design, matching the frozen PL-A1c interface contract:

  * `drain(tenancy, transport, ...)` is the whole state machine. `transport`
    is an INJECTABLE callable (`payload: dict -> Response`) so the module is a
    pure state machine over (spool, state) with no baked-in networking — the
    test suite never opens a socket, and `transport=None` ("no endpoint
    configured yet") makes `drain` a byte-for-byte NO-OP.
  * `build_wire_payload` is a PURE function (no clock/fs/env): it is the
    single place that decides what leaves this process, and it carries only
    the opaque `tenancy` id plus the already-privacy-scrubbed rollup — never a
    raw path, branch, user id, company, or auth token (those negative-space
    guarantees hold by construction: this module never even reads `cwd`, it
    only accepts it for interface symmetry with the resolve_tenancy-based
    siblings and never touches its value).
  * The PC-A2 status contract (200/401/403/413/422/429/503/other) is mapped in
    `_classify` and applied entirely inside `drain`; the per-tenant state file
    (`<data_dir>/insights/outbox/<tenancy>.json`) is the only durable record of
    delivered/dead-lettered sessions and of the current backoff/mute window,
    written atomically via the shared `_loop_ledger._atomic_write_lines`
    (mkstemp + os.replace — the same primitive `_insights_session.py` reuses
    for its own registry/notice-marker rewrites).
  * Durable caps: the spool is compacted (terminal rows reclaimed) only once
    its row count exceeds `cap` — never on every ack — and a still-pending row
    is NEVER dropped, even if that leaves the spool over cap (in which case a
    backlog `doctor_hint` is raised instead). The rewrite is IN-PLACE under a
    blocking `fcntl.flock` on the spool's own fd (never mkstemp+`os.replace`,
    which would swap the inode out from under a concurrent
    `_insights_session._spool_append`), re-reading the spool under that same
    lock so a concurrent append is never lost.
  * `drain()` is itself serialized per tenancy by a non-blocking, tenant-wide
    `fcntl.flock` (`_tenant_drain_lock`): a second concurrent drain that loses
    the race returns immediately, before ever loading state — never a
    double-send. Within one drain, state is saved BEFORE the spool is
    compacted, so a crash between the two can never lose a terminal row's
    disposition; a session inside its own `backoff.next_attempt_at` window is
    skipped for SENDS only (disk maintenance still runs).
  * T2-C3 adds a SECOND door and a second, subordinate request per session: the
    opt-in EVENT SKELETON goes to `events_transport` (its own endpoint, its own
    collection) via `build_events_payload` — never merged into the
    session-activity payload, because 25.7% of real sessions would then exceed
    that door's 262144-byte cap and its 413 is a `dead_letter` this module never
    retries (measured 2026-07-27, 68 of 265; see `build_events_payload` for the
    derivation — the same number, date and derivation the server's schema cites). A skeleton is
    attempted only AFTER its session row is acked — in the same drain, or in a
    LATER one, because a skeleton also gets a genuine cross-drain RETRY LANE:
    `_compact_spool` keeps a spool row until its activity row is terminal AND its
    skeleton is resolved (`_retained_rows`), so 401/403/404/429/503 from the
    events door are recoverable instead of destroying the skeleton. Its outcome is
    written to the state file's own `events` compartment (never to `delivered`/
    `dead_letter`/`mute`/`backoff`), the ack is never conditional on the door's
    reported counts, delivery is additionally gated on the resolved state
    `events_consent` — THREE states, because fail-closed is right for capture and
    wrong for destruction: only an explicit `"event_skeleton": false` discards an
    already-captured skeleton, while an unreadable/absent config neither sends it
    nor destroys it (the row is retained and a doctor hint names the scope).
    That state is resolved PER SESSION, via `events_consent_for` (OPEN-1 F4): one
    spool serves every linked worktree of a repo, whose configs can disagree, so a
    single state for the whole spool let one checkout's `false` terminally destroy
    another checkout's granted skeleton. Finally,
    `events_status`/
    `events_status_report` are the readers that keep that compartment from being
    write-only.

stdlib only.
"""

import collections
import contextlib
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _insights_session  # noqa: E402  (data_dir/_spool_path/resolve_tenancy)
import _loop_ledger  # noqa: E402  (shared atomic writer + tolerant ISO parser)
import ambient_digest  # noqa: E402  (the one degrade-graceful JSONL reader)

# POSIX-only advisory file locking — the tenant-wide drain lock (fix 4) and
# the in-place spool-compaction lock (fix 3). Guarded exactly the way
# `_insights_session.py` guards its own `_fcntl` import, so this module still
# IMPORTS on a non-POSIX host; both locks below degrade to best-effort (no
# real cross-process/cross-thread mutual exclusion) when unavailable, rather
# than refusing to run.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

Response = collections.namedtuple("Response", ["status", "body"])

# --------------------------------------------------------------------------- #
# Tunables. None of these are asserted at an exact value by the RED-first
# suite (only "strictly growing" / "exactly 24h" / "non-empty" are pinned);
# picked to be conservative production defaults, documented here rather than
# buried as magic numbers inline.
# --------------------------------------------------------------------------- #

_DEFAULT_CAP = 500  # durable spool row-count cap before compaction kicks in
_BACKOFF_BASE_SECONDS = 60  # attempt 1 -> 60s, attempt 2 -> 120s, ...
_BACKOFF_MAX_SECONDS = 3600  # never back off further than 1h between attempts
_MAX_RESPONSE_BYTES = 65536  # bounded read cap on a PC-A2 response body (an
# ack/error body is just `{"id": ...}` or a short message — never unbounded,
# so a hostile/oversized endpoint response cannot exhaust memory).
_MUTE_401_REASON = "auth_failed"
_MUTE_401_DURATION = timedelta(hours=1)  # shorter than 403: often a refreshable token
_MUTE_403_REASON = "ambient_disabled"
_MUTE_403_DURATION = timedelta(hours=24)  # PC-A2 kill-switch: exact per contract
# A 403 whose body says `Access denied` is the caller's ROLE, not the tenant's
# switch: the door resolved this session's project from the repo binding and
# the key's holder is not an editor there (or, with no project resolved, on any
# project of the company). Muted rather than retried because the backoff cap is
# one hour and a missing role can last days — an hourly re-send that can never
# succeed is not persistence, it is noise. Muted SHORT rather than for the kill
# switch's 24h because the remedy is one admin action away and the row is
# retained to be delivered the moment it lands.
_MUTE_ROLE_REASON = "role_denied"
_MUTE_ROLE_DURATION = timedelta(hours=1)
# The prefix the door's own denial helper stamps on every role refusal
# (`knowledge_authz.ACCESS_DENIED_PREFIX`, project-context). Matched on the
# PREFIX so the specific reason after the colon can say anything.
_ACCESS_DENIED_PREFIX = "Access denied"

# --------------------------------------------------------------------------- #
# T2-C4 — WHAT A 200 FROM THE SESSION-ACTIVITY DOOR HAS TO CARRY BEFORE IT IS
# TREATED AS DELIVERY. The SERVER's key, transcribed, not this client's to
# choose: that door's 200 always carries `id`, so a body without it is not
# that door answering.
#
# WHY THIS EXISTS, AND WHY IT IS THE ORDINARY CASE RATHER THAN THE HOSTILE ONE.
# The endpoint is DERIVED from the url of whatever MCP server the project config
# names. Point that url anywhere that answers 200 — a proxy, a captive portal, a
# load balancer serving its own health page, a typo'd host — and every row was
# marked delivered, `_compact_spool` reclaimed the bytes, and nothing was ever
# re-sent. The client had read `response.status` and never `response.body`.
#
# The events door already worked this way (see `EVENTS_RECEIVED_KEY` above and
# the paragraph beginning "WHY A COUNT IS REQUIRED"), and the reasoning written
# there is about the request schema THIS door declares, not the events door's
# own. The rule was argued for one lane and applied to the other; this is the
# same rule at the site it was argued about.
#
# PRESENCE, NOT TRUTHINESS, and that mirrors the server deliberately: it
# detects success by the PRESENCE of its success keys
# precisely so a legitimate zero-ish value is not read as a rejection. A client
# that demanded more than the server promises would reject a real delivery.
#
# WHAT THIS DOES NOT DO, stated because the notice this feature ships has already
# been corrected four times for claiming protection it did not have: it does not
# stop someone who CONTROLS the config from standing up an endpoint that returns
# `{"id": "..."}`. Nothing a client can check would. It stops a 200 that is not
# this door, which is the failure that happens by accident.
SESSION_ACK_KEYS = ("id",)


def _ack_proves_persistence(body):
    """True iff `body` is the shape the SESSION-ACTIVITY door declares for
    success — a dict carrying every one of `SESSION_ACK_KEYS`, each non-null.

    ONE RULE, TWO DOORS, TWO FUNCTIONS — and the second half of that sentence is
    stated because an earlier revision claimed the question was "asked once" and
    it is not. The rule is: a 200 proves the request was well-formed; only the
    door's own declared success shape in the BODY proves anything was stored.
    The events door asks `_events_counts`, which cannot delegate here: it needs
    its keys' VALUES, because a short `stored` is a legitimate duplicate-`seq`
    collapse it has to render as a diagnostic, and its own docstring argues that
    case at length. Two implementations of one rule is what this repo keeps
    getting bitten by, so the rule is written here, once, and `_events_counts`
    points at it — but anyone hardening "is this body this door's answer" must
    edit both, and this sentence is the reason they will know to.

    `body` may be None (empty or unparseable body), a list, or a string: a
    misdirected endpoint can return any JSON at all, and `str` in particular
    would satisfy a naive `"id" in body` substring test.

    TRUTHY, NOT MERELY PRESENT, AND THAT IS THE SERVER'S OWN CONTRACT RATHER
    THAN A NARROWING OF IT. The server detects success by PRESENCE, which is
    right for a door whose success keys are COUNTS — `{"received": 0,
    "stored": 0}` is a legitimate report. This door's key is a document id,
    which the live service never returns falsy, so `{"id": ""}`, `{"id": 0}`
    and `{"id": false}` — each of which an earlier non-null test accepted, and
    the first of which is an ordinary generic JSON shape — are refused here for
    the same reason the server refuses them there.

    A presence test alone was the gap two independent reviews found: it turned
    "the door proved persistence" back into "some endpoint returned a field of
    the right name", which is the whole failure this function exists to stop."""
    if not isinstance(body, dict):
        return False
    return all(body.get(key) for key in SESSION_ACK_KEYS)


_DOCTOR_HINT_UNPROVEN_ACK = (
    "Ambient outbox: the insights endpoint answered 200 but the reply was not "
    "this door's success shape (no {'id': ...}), so nothing has been proven "
    "stored and no row was settled. The rows are RETAINED and will be retried. "
    "The usual cause is the configured Fairmind MCP url pointing somewhere that "
    "is not the insights service — a proxy, a captive portal, or a typo — since "
    "the endpoint is derived from that url. Check "
    "projects[<repo>].mcpServers.<fairmind>.url in ~/.claude.json."
)

# --------------------------------------------------------------------------- #
# T2-C3 — the EVENT SKELETON's own door. Everything below is about the SECOND
# request a drain can make, never about the session-activity payload.
# --------------------------------------------------------------------------- #

# THE WIRE NAMES, AND WHO OWNS THEM. Every name in this block is the SERVER's,
# transcribed here; not one of them is this client's to choose. That direction of
# convergence is the PL-A2a precedent, re-applied: the client converges on the
# server's schema with no adapter layer, because a translation in the transport
# is a second place the two can drift and the drift is invisible until a release
# of captured sessions has already been dead-lettered.
#
# Measured on 2026-07-26 against the real events door: `contractVersion` is
# what the envelope declares, the server DROPS any other key and substitutes
# its own default, and the client's first draft sent `schema` + `eventCount` — both of
# which were dropped on every request, so every stored row claimed a contract
# version the client never sent and the server's unknown-version warning could
# never fire. `dropped keys = ['eventCount', 'schema']` was the executed proof.
#
# `contractVersion` is the SERVER's own contract literal, transcribed. It is
# deliberately NOT `ambient_digest.EVENT_SCHEMA_VERSION`: that constant stays
# what it already is, a CLIENT-LOCAL stamp in this plugin's own namespace, and
# the two namespaces are different on purpose — the wire version is the store's
# contract and moves when the store's contract moves, the local one moves when
# this plugin's projector does. Anyone tempted to "unify" them would be coupling
# two release cadences that have no reason to agree.
EVENTS_CONTRACT_VERSION = "fm-insights.session-events/1"

# The two keys the events door reports its counts under, and the whole reason
# `drain` reads `response.body` at all (it ignored `.body` entirely until T2-C3).
# Both are the SERVER's names, returned VERBATIM by the events door:
# `{"received": n, "stored": m}`.
#
# WHY A COUNT IS REQUIRED RATHER THAN NICE-TO-HAVE. Proven end-to-end through
# the real REST door on 2026-07-26: on the session-activity door an
# unknown field is ACCEPTED, DROPPED, and 200'd with a body byte-identical to
# the one a fully understood payload gets. A 200 therefore proves the request
# was well-formed, NOT that anything was stored. Without a count in the body the
# client cannot tell delivery from silent discard, which is the failure this
# whole track exists to stop happening a fourth time.
#
# The client's first draft read `eventsPersisted` — a name that appears nowhere
# server-side — so `_events_counts` returned None for EVERY fully-successful
# delivery and `state["events"]["delivered"]` was permanently empty. Executed
# against the real service on 2026-07-26: it answered `{'received': 6,
# 'stored': 6}` with 6 documents in the collection, and the client filed a
# discrepancy. BOTH names are required, and a reply missing either one leaves
# persistence unproven — recorded, never fatal, and retried rather than settled
# (`_EVENTS_RETRYABLE_KINDS` states that boundary and is the only place that
# does).
EVENTS_RECEIVED_KEY = "received"
EVENTS_STORED_KEY = "stored"

# The events door's declared body budget. 2 MiB, mirroring the shape of the
# session-activity door's own body cap
# (262144, enforced BEFORE parsing, on
# both the declared Content-Length and the real length) — the events door
# enforces its own, larger cap the same way, and the two are deliberately
# different numbers because the two payloads are different orders of magnitude.
#
# MEASURED 2026-07-27 over ~/.claude/projects (269 sessions, 265 with a non-empty
# skeleton), building the real `build_events_payload` output for each and taking
# its length under `json.dumps(payload).encode("utf-8")` — the EXACT call
# `make_urllib_transport` makes, default separators included, not the compact
# `separators=(",", ":")` form the design survey measured. See
# `_events_wire_bytes` for why that distinction is load-bearing and what the two
# numbers actually are.
_EVENTS_MAX_BODY_BYTES = 2 * 1024 * 1024

# The events door's declared ROW bound: exceeding it is a 422. Enforced HERE
# too, and that is not redundancy: the byte cap above
# does NOT subsume it, in either direction.
#
# MEASURED 2026-07-27 through `build_events_payload` + `json.dumps` (the
# transport's own call, default separators), on this payload's own EIGHT-key
# envelope — every figure below moved when `projectorVersion` joined it and the
# surrounding prose did not, which is why they are re-derived here rather than
# carried forward:
#
#   a MINIMAL row — `{"seq": n, "kind": "assistant", "actor": "agent"}`, which is
#   all the projector's omit-falsy filter leaves — costs 54.5 wire bytes on
#   average (55 B marginal), so 20,001 of them are 1,089,201 bytes: 51.9% of the
#   2,097,152-byte cap. The byte check passes, the client sends, and the door
#   answers 422 — which `_classify` maps to a dead_letter that is never retried,
#   i.e. permanent loss of a skeleton the client could simply have declined to
#   send. For minimal rows the byte cap alone does not bind until 38,328 rows,
#   nearly twice this bound.
#
# The reverse holds for realistic rows, which is why BOTH bounds exist and neither
# substitutes for the other: a row also carrying a real 36-char `uuid`, a real
# `parentUuid`, a `ts`, a `modelId` and the four token ints costs 314.5 bytes, so
# 20,001 of THOSE are 6,289,461 bytes — 299.9% of the cap — and the byte check
# catches them first, at 6,682 rows.
#
# EVERY FIGURE IN THIS BLOCK IS MEASURED, 2026-07-27, by driving the real
# `build_events_payload` + `_events_wire_bytes` and binary-searching the row count
# at which the cap binds — not by dividing the cap by a mean. An earlier revision
# of this comment did divide, and replaced a correct measured 38,328 with a
# computed ~38,130 while claiming the measured derivation in the same breath; an
# undated number carved into source is worse than no number, and a number that
# contradicts its own stated method is worse again.
#   The corpus maximum is 4,801 rows / 1,375,673 wire bytes, so on today's
# real sessions this ROW bound never fires at all. It fires only for a
# pathologically small-rowed session, which is exactly the case the byte cap
# cannot see. The two also fail DIFFERENTLY (413 vs 422), which is why the client
# declines to send rather than letting either happen.
_EVENTS_MAX_ROWS = 20000

# --------------------------------------------------------------------------- #
# The events dispositions, split by the only distinction that matters to the
# spool: is this skeleton's fate SETTLED, or is it still owed a later attempt?
#
# THIS SPLIT IS THE RETRY LANE. `_compact_spool` keeps a row until its activity
# row is terminal AND its skeleton is RESOLVED, so a retryable kind means the
# skeleton's bytes are still on disk for the next drain to send. Before this
# split there was no lane at all: one 401 from the events door halted the rest of
# the drain, their activity rows acked anyway, and compaction reclaimed the
# skeletons with zero durable record — reproduced on a real drain, 3 of 4
# sessions silently lost.
# --------------------------------------------------------------------------- #

# SETTLED. Nothing further will ever be attempted; the row's bytes are
# reclaimable. `rejected` (413/422) and `unbuildable` were already terminal;
# `over_budget`/`over_rows` are the two declared bounds; `revoked` is an
# EXPLICIT `"event_skeleton": false` discarding a skeleton locally (M3);
# `orphan` is a skeleton whose SESSION row was dead-lettered, so there is no
# server-side record for it to annotate and sending it would create one that
# cannot be joined.
#
# `revoked` was spelled `disabled` until 2026-07-27, and the rename is part of
# the repair rather than cosmetics: this kind is the durable record of WHY bytes
# were destroyed, and "disabled" was equally true of a config that could not be
# READ — which is precisely the state that must never reach this tuple. A
# terminal kind whose name is broader than its trigger is how a destruction path
# acquires entrances the notice never advertised.
_EVENTS_TERMINAL_KINDS = ("over_budget", "over_rows", "rejected", "unbuildable",
                          "revoked", "orphan")

# The terminal kinds that get their OWN doctor hint instead of the generic one:
# the two DECLARED BOUNDS (`_events_omitted_doctor_hint` — we declined to ask)
# and the EXPLICIT REVOCATION (`_events_revoked_doctor_hint` — the one path that
# destroys a captured skeleton). Every OTHER member of the tuple above falls
# through to `_events_terminal_doctor_hint`, and `drain` computes that fallthrough
# BY SUBTRACTION from `_EVENTS_TERMINAL_KINDS` rather than re-listing it.
#
# That subtraction is the point, and it is what makes the tuple above load-bearing
# instead of decorative: nothing read `_EVENTS_TERMINAL_KINDS` at all, while
# `drain` hand-transcribed the same six names into three groups. A seventh
# terminal kind added to the declaration would have been terminal — bytes
# reclaimed — and produced NO hint whatsoever, which is the exact silence every
# other disposition in this compartment exists to break.
_EVENTS_OMITTED_KINDS = ("over_budget", "over_rows")
_EVENTS_REVOKED_KIND = "revoked"
_EVENTS_OWN_HINT_KINDS = _EVENTS_OMITTED_KINDS + (_EVENTS_REVOKED_KIND,)

# RETRYABLE. The skeleton is still in hand and a later drain re-attempts it.
# 401/403 (`refused`), 404/429/503/0/a raising transport (`unreachable`), and
# `no_door` — all recoverable conditions about the DOOR, not about the payload —
# plus `consent_unknown`, which is not about the door at all: the company's
# decision could not be DETERMINED, so the skeleton is neither sent nor
# discarded until it can be;
# plus `uncounted`, a 200 that did not PROVE PERSISTENCE.
#
# ============================ THE BOUNDARY ============================
# A count that is PRESENT AND DISAGREES with what was sent is TERMINAL.
# OTHERWISE — no disagreement having settled it — anything short of BOTH counts
# present and equal to what was sent is persistence UNPROVEN, and unproven
# persistence is RETRIED.
# ======================================================================
#
# This is the one place it is stated; `_attempt_events` implements it and points
# back here rather than restating it, because the two halves drifted apart once
# already and a second copy is how that happens.
#
# WHY DISAGREEMENT SETTLES. A door that answers `{"received": 6, "stored": 4}`
# is SPEAKING this contract and telling us something TRUE — the server's own
# duplicate-`seq` collapse — so it stays a `discrepancy`, terminal, exactly as
# `_events_discrepancy_doctor_hint` argues; retrying it would loop forever
# against a healthy store. That short count is the whole reason a terminal count
# outcome exists at all, and it must not become a retry storm.
#
# WHY SILENCE DOES NOT. A door answering with no counts is not speaking this
# contract: version skew, a proxy that stripped the body, or a rolled-back
# server — the "client deployed before the server" ordering this whole track has
# been careful about everywhere else. Until 2026-07-27 that case resolved the
# session TERMINALLY, so `_retained_rows` kept nothing and compaction reclaimed
# every skeleton it touched, with no retry and no way back.
#
# AND WHY A PARTIAL REPORT IS ON THE SILENT SIDE, which is where this boundary
# moved on 2026-07-27 and where it was previously drawn wrong. The old rule read
# "both counts absent" and therefore filed a 200 carrying only `received` (or
# only `stored`) as a `discrepancy`: settled, spool row reclaimed, skeleton never
# re-attempted. Reproduced through the real `drain` twice — `received` only,
# agreeing with what was sent, gave `lane=discrepancy` and a second drain made
# ZERO calls. The rows were gone, and nothing had ever claimed they were stored.
# Persistence is exactly as unproven with one count as with none: a door that
# reports half its contract has not told you the rows are durable, and the
# argument that licenses a terminal outcome — "the server deliberately collapsed
# a duplicate and said so" — is an argument about a count that DISAGREES, not
# about a count that is missing. So the test is on disagreement, and a partial
# report that agrees contradicts nothing and settles nothing.
#
# `uncounted` NAMES THE MISSING COUNT, NOT A MISSING BODY, and it covers the
# partial case for that reason: a door reporting `received` and not `stored` has
# left the count that proves persistence UNCOUNTED, exactly as a door reporting
# neither has. The name was not changed when the case widened, deliberately —
# `pending` is a DURABLE ring that merges across drains, so a rename would leave
# one state file carrying two names for one condition with no way to tell them
# apart, which costs an operator more than the wider word buys.
#
# WHY RE-SENDING A BATCH THE DOOR MAY ALREADY HAVE STORED IS SAFE, which is the
# precondition the rest of this reasoning rests on and would be a duplication bug
# without: the store keys one document per ROW on `(company, sessionId, seq)` and
# collapses a repeat first-seen-wins. That is the SAME mechanism
# `_events_discrepancy_doctor_hint` cites for the legitimate short `stored` — the
# server performing exactly this collapse — so "retry a 200" costs a wasted
# request, never a duplicated row. If that key or that collapse ever changes,
# this kind must go back to being terminal.
#
# THE DEPENDENCY THIS INHERITS, named the same way `no_door` names it, and it
# GREW when the partial case moved to this side on 2026-07-27: retention is
# unbounded while a skeleton stays pending, so a door that 200s forever without
# proving persistence grows the spool indefinitely (with the backlog hint firing
# every drain). Before the move that needed a door reporting NOTHING — plausibly
# only a proxy or a rollback. After it, a server that ships `received` and never
# `stored` is a permanently-retaining client fleet, and that shape is a one-line
# route change away on the other side. It is still the deliberate direction — a
# retained row is recoverable, a reclaimed one is not, and the alternative here
# is destroying rows nobody ever claimed to have stored — but the cost is real
# and it is now cheaper to trigger by accident.
#
# UNLIKE `_EVENTS_TERMINAL_KINDS`, THIS TUPLE HAS NO PRODUCTION READER, and that
# is stated rather than left for someone to discover: `drain` routes on the
# DISPOSITION `_attempt_events` returns, not on this name, so a kind missing from
# here changes no behaviour and breaks no code. It is a declaration, and what
# keeps it honest is
# `test_a_200_that_does_not_prove_persistence_is_retried_not_destroyed`, which
# drives every retryable shape and asserts the kind it OBSERVES on disk is
# declared here — declaration against artifact, not literal against literal.
#
# ⚠️ ONE MEMBER IS RETAINED WITHOUT BEING RE-ATTEMPTABLE, and it is called out
# here rather than left to read as a sixth ordinary retry: `class_ungranted` (the
# row's FROZEN grant does not cover class C — see
# `_events_class_ungranted_doctor_hint`). It belongs on this side of the split
# because the split's real question is "may the bytes be reclaimed", and the
# answer for it is no. But unlike every other member, nothing a later drain, a
# recovered door or an edited config can do will ever make it sendable: the grant
# it fails is frozen ON THE ROW. It is therefore a PERMANENT retention, and that
# cost is stated in its hint and in this comment rather than discovered from a
# growing spool.
_EVENTS_RETRYABLE_KINDS = ("refused", "unreachable", "no_door",
                           "consent_unknown", "uncounted", "class_ungranted")

# The kind above, named once so the entry builder, the accumulator and the hint
# cannot spell it three ways.
_EVENTS_CLASS_UNGRANTED_KIND = "class_ungranted"

# Bound on each of the three DIAGNOSTIC entry lists in the state file (M2).
# 32, the same bound the orchestrator-token watermark's `last_ids` carry uses
# (`hooks/scripts/capture-orchestrator-tokens.sh`) — chosen for the same reason:
# it is a diagnostic ring, big enough that a real incident's worth of distinct
# sessions is visible in one read and small enough that a file rewritten on every
# drain cannot grow without bound. Before the bound, `undelivered` and
# `discrepancy` were pure-append lists of ~110-char-reason dicts with no cap, no
# prune and no dedup, in a file read and rewritten on every drain.
#
# THE CAP IS SAFE ONLY BECAUSE RETENTION DOES NOT READ THESE LISTS. Retention
# reads `state["events"]["resolved"]` — session ids only, uncapped, the same
# unboundedness `delivered` and `state["delivered"]` already carry. If the capped
# lists were the retention authority, truncating an entry would resurrect a
# settled skeleton and re-send it forever.
_EVENTS_DIAGNOSTIC_CAP = 32

# WHICH lists those are — the names the cap, the `dropped` tally and the reader
# all have to agree on. One writer, because they were transcribed at three sites
# (`_load_state`'s entry filter, `_load_state`'s `dropped` normalization,
# `events_status_report`'s truncation notice) and a fourth diagnostic list added
# to two of the three is a list that is never normalized or never reported.
#
# NOT usable for the `resolved` RECONSTRUCTION in `_load_state`, which walks
# `("undelivered", "discrepancy")` on purpose — see the comment there: the draft
# it migrates had no pending lane, so folding `pending` in would resolve
# skeletons that are still owed a retry.
_EVENTS_DIAGNOSTIC_LISTS = ("undelivered", "discrepancy", "pending")

_DOCTOR_HINT_EVENTS_HALTED = (
    "Ambient event-skeleton delivery was refused by the events door "
    "(401/403); the remaining skeletons in this drain were not attempted. The "
    "SESSION rows themselves were delivered and are unaffected — only the "
    "opt-in event skeleton is delayed. Those skeletons are NOT lost: their "
    "spool rows are retained until the skeleton is resolved, so the next drain "
    "re-attempts them. Check the events door's auth/enablement."
)


def _events_kind_list(entries):
    """`sid: kind (status N)` for each entry, `; `-joined — the per-session detail
    shared VERBATIM by the terminal and the pending hint. Those two say opposite
    things to an operator (gone forever vs queued for retry) but render the same
    evidence, and they had two copies of this expression twenty lines apart."""
    return "; ".join(
        f"{e['session_id']}: {e.get('kind')}"
        + (f" (status {e.get('status')})" if e.get("status") is not None else "")
        for e in entries)


def _events_rows_list(entries):
    """`sid: N rows` for each entry, `; `-joined — the detail shared by the two
    CONSENT hints. They are deliberately separate hints (one destroyed the data,
    one is waiting on an unreadable config) and deliberately the same evidence:
    how much is at stake per session."""
    return "; ".join(f"{e['session_id']}: {e.get('rows')} rows" for e in entries)


def _events_omitted_doctor_hint(entries):
    """T2-C3: one or more skeletons exceeded one of the events door's two
    DECLARED BOUNDS — its 2 MiB body budget (`over_budget`) or its 20000-row
    batch limit (`over_rows`) — and were therefore OMITTED WHOLE, never
    truncated. A cap that silently drops rows drops exactly the rows someone is
    querying (the order of a long episode is the one thing this projection exists
    to carry), so the choice is all-or-nothing and the omission is recorded with
    the row count that WOULD have been sent. Chunking by `seq` range is the named
    follow-up, not a silent truncation.

    These two are the one class of skeleton loss that a retry lane cannot help
    with, and that is why they are terminal rather than pending: the payload is
    over a bound the door declares, so every future attempt fails identically.
    Sending anyway would earn a 413 or a 422, both of which `_classify` maps to a
    dead_letter — the same permanent loss, minus the record."""
    return (
        f"Ambient outbox: {len(entries)} event skeleton(s) exceeded a declared "
        f"bound of the events door (body budget {_EVENTS_MAX_BODY_BYTES} bytes, "
        f"row limit {_EVENTS_MAX_ROWS}) and were OMITTED WHOLE (never "
        f"truncated) — "
        + "; ".join(
            f"{e['session_id']}: {e.get('kind')}, {e.get('rows')} rows, "
            f"{e.get('bytes')} bytes" for e in entries)
        + ". The session rows themselves were delivered. Chunking by seq range "
          "is the follow-up; until then these skeletons are not recoverable — "
          "no retry can fix a payload that is over a declared bound."
    )


def _events_terminal_doctor_hint(entries):
    """T2-C3: skeletons whose fate is SETTLED AGAINST THEM for a reason no retry
    can change — the door rejected the payload permanently (`rejected`, 413/422),
    the payload could not be built at all (`unbuildable`), or the session row it
    annotates was dead-lettered so there is nothing server-side to attach it to
    (`orphan`). Named separately from the omitted-bounds hint because a doctor
    reading these needs to know the door was ASKED and said no (or that the
    session record itself never landed), not that the client declined to ask.

    Stated loudly rather than left implicit: each of these IS a permanently lost
    skeleton. The retry lane makes 401/403/404/429/503 recoverable; it cannot
    make these recoverable, so they are reported rather than quietly resolved."""
    return (
        f"Ambient outbox: {len(entries)} event skeleton(s) are PERMANENTLY "
        f"undelivered and will never be retried — "
        + _events_kind_list(entries)
        + ". The session rows themselves are unaffected. A 'rejected' skeleton "
          "means the door refused the payload permanently (413/422); an "
          "'orphan' means its SESSION row was dead-lettered, so there is no "
          "record for the skeleton to annotate."
    )


def _events_pending_doctor_hint(entries):
    """T2-C3 (M1): skeletons that are NOT YET delivered and ARE still recoverable
    — the retry lane's own signal, and the one disposition that produced no hint
    at all before this. A 404/429/503, a connection error, or a 401/403 halt used
    to ack the session, stop yielding its row, and let compaction reclaim the
    skeleton, so the operator saw nothing and the data was gone. Now the spool row
    is RETAINED until the skeleton resolves, and this hint is how a human learns
    that a backlog is building and which door is at fault.

    Deliberately worded as a delay rather than a loss, because that is what it
    now is — and deliberately still a hint, because a permanently-down events door
    means the spool grows for as long as it stays down."""
    return (
        f"Ambient outbox: {len(entries)} event skeleton(s) were not delivered "
        f"this drain and are QUEUED FOR RETRY (their spool rows are retained "
        f"until the skeleton resolves, so nothing is lost) — "
        + _events_kind_list(entries)
        + ". Investigate the events door (auth/enablement/reachability): while "
          "it stays down the spool keeps growing, because a pending skeleton is "
          "never dropped to make room."
    )


def _events_uncounted_doctor_hint(entries):
    """T2-C3: the events door answered 200 without PROVING PERSISTENCE — it
    reported neither count, or exactly one of the two and that one agreed. Either
    way the contract's proof is missing, which is what this lane is about; the
    per-entry `reason` says which of the two shapes it was.

    Its own hint rather than a share of `_events_pending_doctor_hint`, on the
    same reasoning that split `consent_unknown` out: that hint says "investigate
    the events door (auth/enablement/reachability)", and every one of those three
    is exactly wrong for a door that just answered 200. What is wrong here is the
    CONTRACT on the other end — an older or rolled-back build, a route that only
    half-fills the body, or something in front of it that stripped it.

    Deliberately NOT the discrepancy hint either, which reports a settled fact
    and tells the reader it will never be retried. This one WILL be retried, and
    the skeletons are retained until it is, so the operator's action is
    "reconcile the two deployments", not "accept the loss"."""
    return (
        f"Ambient outbox: {len(entries)} event skeleton(s) were answered 200 by "
        f"the events door WITHOUT both of the {EVENTS_RECEIVED_KEY!r}/"
        f"{EVENTS_STORED_KEY!r} counts the contract requires, so persistence is "
        f"unproven — "
        + _events_rows_list(entries)
        + ". A 200 alone does not prove storage (an unknown field is accepted, "
          "dropped and 200'd), so a door that does not count is "
          "indistinguishable from one silently discarding the batch — and half "
          "a count proves no more than none. Most likely this client is newer "
          "than the door it is talking to, or a proxy stripped the response "
          "body. The skeletons are RETAINED and retried, so nothing is lost "
          "while the two deployments are reconciled — but the spool grows for "
          "as long as the door keeps answering without both counts."
    )


def _events_revoked_doctor_hint(entries):
    """T2-C3 (M3): the decision was EXPLICITLY REVERSED — `"event_skeleton":
    false` in the repo config — and skeletons were already sitting on spool rows
    from when it was on. They are discarded LOCALLY, never sent, never retried,
    and the discard is recorded per session rather than being quietly true:
    "it was turned off" and "the ones already captured were also thrown away"
    are two different facts, and a doc stating only the first claims less than
    the code does.

    The bytes go away with the spool row itself, at the next compaction: nothing
    rewrites a spool row in place, and a resolved skeleton no longer retains its
    row.

    THIS IS THE ONLY PATH THAT DESTROYS A CAPTURED SKELETON, and the hint says so
    out loud so that a reader who sees it can tell it apart from
    `_events_consent_unknown_doctor_hint`, which is the state that USED to arrive
    here and be destroyed by it."""
    return (
        f"Ambient outbox: the event-skeleton decision was explicitly REVERSED "
        f"(\"event_skeleton\": false), so {len(entries)} already-captured "
        f"skeleton(s) were DISCARDED locally without ever being sent — "
        + _events_rows_list(entries)
        + ". Ambient session capture is unaffected. Re-enabling "
          "\"event_skeleton\" does not bring these back: their sessions are "
          "already digested, so there is no re-projection path. This is the "
          "only condition that discards a captured skeleton — an unreadable or "
          "absent config does NOT."
    )


def _events_consent_unknown_doctor_hint(entries, scopes=()):
    """T2-C3 (2026-07-27): the company's decision could not be DETERMINED, so
    nothing was sent and — the part that is the fix — nothing was discarded.

    THE ASYMMETRY THIS HINT EXISTS TO MAKE VISIBLE. Fail-closed is right for
    CAPTURE: an unstated decision must never mean more data leaves the machine.
    It is wrong for DESTRUCTION: a read error is not a decision, and treating it
    as one deleted skeletons captured while the decision was genuinely in force.
    So an absent, unreadable, malformed or wrong-typed config now RETAINS the row
    and raises this — never drop, raise a hint, exactly as this module degrades
    everywhere else.

    Deliberately worded as an unresolved question rather than a fault, and
    deliberately naming the config SCOPE rather than a filesystem path: there is
    only ONE scope since 2026-07-27 (the per-user file stopped being read when
    the decision became the company's — `_insights_session.event_skeleton_
    consent`), so the label is enough to act on, and the repo path and the home
    directory are the strings the opaque tenancy exists so nothing has to carry.

    TWO UNBOUNDED THINGS, stated rather than discovered. (a) While the state
    stays indeterminate the rows are retained, so the spool grows — the same
    unboundedness the pending lane already accepts, and for the same reason (a
    retained row is recoverable; a reclaimed one is not). (b) UNLIKE the other
    events hints, which report only THIS drain's new entries, this one
    re-enumerates every retained session on EVERY drain: for this state nothing
    resolves, so every candidate is "new" each pass and the hint's length tracks
    the retained count. Accepted — the operator's next action is "fix one config
    file", and the session list is the evidence that data is waiting — and it
    costs nothing on disk: the DURABLE lists it feeds are still deduped by
    session and bounded by `_merge_events_entries`/`_EVENTS_DIAGNOSTIC_CAP`."""
    where = (" Could not read: " + "; ".join(scopes) + "." if scopes else
             " No .fairmind-insights.json could be read at the repo root at all.")
    return (
        f"Ambient outbox: the event-skeleton decision could not be DETERMINED, so "
        f"{len(entries)} already-captured skeleton(s) were NEITHER SENT NOR "
        f"DISCARDED — they stay on the spool until it can be — "
        + _events_rows_list(entries)
        + f".{where} Only an explicit \"event_skeleton\": false discards them; "
          "an explicit true resumes delivery on the next drain. Until then the "
          "spool keeps these rows, so nothing is lost while the question is open."
    )


def _events_class_ungranted_doctor_hint(entries):
    """JC5 / 2026-08-14: the skeleton was collected under a grant that does not
    cover CLASS C, so it was NOT sent — and, because a frozen stamp is a label and
    never a switch, not discarded either.

    THE SIBLING OF THE ACTIVITY LANE'S OWN WIDENING BUG, at the door that could not
    express it as an empty field. `build_wire_payload` used to withhold on the LIVE
    class list while its stamp reported `frozen ∩ live`, so a config WIDENED after
    collection shipped class-C fields under a stamp saying C was not applied. The
    events door has the same defect with no partial answer available: the batch IS
    class C in whole, so the only two moves are send it or do not. It used to send
    — `run_drain`'s resolver ANDs the LIVE class-C state with the `event_skeleton`
    switch and never consults the row's frozen grant — which is a grant applied
    BACKWARDS, on the lane carrying the richest per-step data of the two.

    RETAINED, NOT DESTROYED, and that direction is not a judgement call: three
    separate places say a frozen stamp may never discard (`class_consent_state`,
    `_row_grants_skeleton`, `run_drain`'s own resolver comment). Destroying on a
    LABEL would make the stamp a second revocation path, which is precisely what
    those three forbid.

    ⚠️ THE COST, STATED BECAUSE IT IS PERMANENT AND NOT MERELY UNBOUNDED. Every
    other retained disposition waits on something that can change — a door coming
    back, a config becoming readable, two deployments reconciling. This one waits
    on the row's own frozen grant, which never changes, so these rows are retained
    FOREVER and the spool grows by one skeleton per affected session. It is the
    conservative half of a fix whose other half is out of this file: closing it
    properly means `_insights_session.run_drain`'s `events_consent_for` deciding
    the frozen half alongside the live one — either by resolving these rows
    terminally under an explicit rule, or by never capturing a skeleton class C
    does not cover in the first place (`_row_grants_skeleton` deliberately does not
    ask, and says so). Until then this hint is the operator's only sight of it."""
    return (
        f"Ambient outbox: {len(entries)} event skeleton(s) were collected under a "
        f"consent grant that does not include class C (generation_context), which "
        f"is the class the whole skeleton belongs to — so they were NOT SENT, and "
        f"NOT discarded either — "
        + _events_rows_list(entries)
        + ". A grant recorded at collection is never widened afterwards, so "
          "enabling the class now does not make these sendable; the rows stay on "
          "the spool, and the spool keeps them permanently. Nothing is lost, and "
          "nothing further will happen to them without a change to how the "
          "frozen grant is resolved at delivery."
    )


def _events_class_ungranted_entry(sid, rows, applied):
    """The NON-terminal record for a skeleton the row's own frozen grant does not
    cover. No status and no door call — the decision is local, read off the row.

    The APPLIED list is spelled out in the `reason` rather than added as a field
    of its own, and that is not brevity: the applied set is `frozen ∩ live`, so
    `["A"]` says the row was collected narrow while `[]` says the stamp itself
    could not be read — two different investigations behind one refusal, worth
    recording. But `_events_entry_detail` renders `kind`/`status`/`rows`/`bytes`
    and nothing else, so a fifth key would be a durable field with no reader —
    the exact "record that evaporates" `_default_events_state` warns about, one
    level down. It goes where a reader will actually see it."""
    return {
        "session_id": sid, "kind": _EVENTS_CLASS_UNGRANTED_KIND, "status": None,
        "rows": rows,
        "reason": "the classes applied to this row are "
                  f"{sorted(applied)!r}, which does not include class C "
                  "(generation_context) — the class the event skeleton belongs "
                  "to in whole — so it was neither sent nor discarded; it stays "
                  "on the spool",
    }


def _events_discrepancy_doctor_hint(entries):
    """T2-C3: the events door 200'd and a count it REPORTED did not match what
    was sent. A count the door did not report is NOT this hint — neither absent
    is, nor one absent and the other agreeing; both are
    `_events_uncounted_doctor_hint`, and both are retryable. Deliberately NOT fatal
    and deliberately NOT a retry — and that is the SERVER's instruction, not a
    convenience: the events door legitimately returns a short `stored`
    when a batch repeats a `seq` (first-seen wins) and states outright that the
    counts are diagnostic, never the retry signal, so a client that retried on a
    short count would loop forever against a perfectly healthy store. The ack of
    the SESSION row is never conditional on this either."""
    return (
        f"Ambient outbox: {len(entries)} event skeleton(s) were accepted (200) "
        f"but the door's reported {EVENTS_RECEIVED_KEY!r}/{EVENTS_STORED_KEY!r} "
        f"did not match what was sent — "
        + "; ".join(
            f"{e['session_id']}: sent {e.get('sent')}, door reported "
            f"received={e.get('received')!r} stored={e.get('stored')!r}"
            for e in entries)
        + ". A 200 alone does not prove persistence (an unknown field is "
          "accepted, dropped and 200'd), so this is the only signal that the "
          "door on the other end is not the one the contract describes. It is "
          "recorded and NOT retried: the server declares these counts "
          "diagnostic, and a short `stored` can be a legitimate duplicate-seq "
          "collapse."
    )

_DOCTOR_HINT_401 = (
    "Ambient insight delivery is muted after an authentication failure (401) "
    "from the insights endpoint. Check the configured JWT/auth token for "
    "ambient capture; delivery resumes automatically once the mute window "
    "elapses."
)
_DOCTOR_HINT_403 = (
    "Ambient insight delivery was disabled by the insights endpoint (403) "
    "for 24h. Check whether ambient capture was turned off server-side for "
    "this tenant."
)
_DOCTOR_HINT_ROLE = (
    "Ambient insight delivery is paused: the insights endpoint refused this "
    "key's holder (403, access denied) — the session's rows would be filed "
    "under a project where you are not an editor. Nothing is lost; the rows "
    "are retained and delivery resumes on its own once a project admin grants "
    "the editor role. If the key was minted for the wrong project, mint one "
    "from Studio with the right project selected and re-run /fairmind-connect."
)


def _backlog_doctor_hint(pending_count, cap, events_held=0):
    """The spool is over cap and there is nothing left to reclaim.

    `events_held` (T2-C3) is how many of those retained rows are held ONLY
    because their event skeleton is still unresolved — their session rollup is
    already delivered. Naming that share is the difference between "delivery is
    broken" and "the SECOND door is down": both grow the spool identically, and
    the operator's next move is different for each."""
    detail = ""
    if events_held:
        detail = (
            f" {events_held} of them are retained ONLY because their opt-in "
            f"event skeleton is not yet resolved — their session rollups are "
            f"already delivered, so the door to investigate is the EVENTS door, "
            f"not the session one."
        )
    return (
        f"Ambient outbox backlog: {pending_count} rollup(s) are pending "
        f"delivery, above the drain cap of {cap}. Investigate delivery "
        f"(network/auth) — the spool will keep growing (never dropping a "
        f"pending rollup) until it drains." + detail
    )


def _legacy_row_doctor_hint(session_ids):
    """PL-A2a AC8: a legacy (pre-PL-A2a) spool row — no `started_at`/
    `ended_at` at all, so it predates the wire-schema enrichment and can
    never be safely built into a `build_wire_payload` payload — is dead-
    lettered LOCALLY, without ever being sent. The hint NAMES the condition
    (matched by the checker's `legacy|started_at|ended_at|pre-enrich` regex)
    so a doctor/operator reading it knows exactly what happened and why no
    retry will ever resolve it."""
    return (
        f"Ambient outbox: {len(session_ids)} legacy (pre-enrichment) spool "
        f"row(s) — missing started_at/ended_at — were dead-lettered locally "
        f"without being sent. These rows predate the PL-A2a wire-schema "
        f"convergence and will never be retried; they are safe to ignore "
        f"(they carry no data loss beyond the already-superseded local "
        f"rollup)."
    )


def _held_schema_row_doctor_hint(session_ids):
    """PL-A2a round 3 (F3 — AMENDS round 2's D4 disposition, see
    `_unsendable_reason`'s own docstring): a spool row carrying VALID
    `started_at`/`ended_at` but an absent or unrecognized `schema` stamp —
    the case the OLD, purely-structural (missing-timestamps only) sniff
    could never catch — is HELD (kept pending, never sent, never
    dead-lettered) rather than discarded, so a later plugin version that
    understands its schema can still deliver it. Round 2 dead-lettered this
    case unconditionally, which made a genuine future-schema row (or a
    transitional/partial-deploy row with no schema at all) PERMANENTLY
    unrecoverable — `digested_at` is already stamped at spool time, so there
    is no re-digest path. Named separately from `_legacy_row_doctor_hint`
    (worded to match the checker's `schema|version` regex while deliberately
    NOT matching its `legacy|started_at|ended_at|pre-enrich` regex) so a
    doctor merging every hint can tell a genuinely legacy row from a
    schema-mismatched (held) one."""
    return (
        f"Ambient outbox: {len(session_ids)} otherwise well-formed spool "
        f"row(s) carry an unrecognized (absent or unknown) schema version "
        f"— expected {ambient_digest.SCHEMA_VERSION!r} — and are being HELD "
        f"(kept pending, never sent) rather than delivered or discarded. "
        f"Investigate whether the plugin version that produced them "
        f"predates or postdates this build; a future plugin version that "
        f"understands this schema can still deliver them."
    )


def _incomplete_row_doctor_hint(session_ids):
    """PL-A2a round 3 (F4): a spool row carrying the CURRENT schema stamp
    but missing `started_at`/`ended_at` — a LIVE producer defect (a
    registry read failure or a corrupt registry row at digest time, or a
    direct `_digest_one_session` call), not an old, safe-to-ignore leftover
    — is dead-lettered LOCALLY, without ever being sent (unlike the held
    schema-mismatch case above, no schema bump ever recovers genuinely
    absent timestamps). Worded to stay distinguishable from
    `_legacy_row_doctor_hint` and `_held_schema_row_doctor_hint`: never
    claims "safe to ignore", never uses the word "legacy", so an operator
    investigates a live defect rather than dismissing it as old data."""
    return (
        f"Ambient outbox: {len(session_ids)} spool row(s) carry the CURRENT "
        f"schema version ({ambient_digest.SCHEMA_VERSION!r}) but are "
        f"missing started_at/ended_at. This is a live producer defect (a "
        f"registry read failure or corrupt registry row at digest time) "
        f"and needs investigation — these rows were dead-lettered locally "
        f"without being sent."
    )


def _unbuildable_row_doctor_hint(session_ids):
    """PL-A2a round 3 (F1): a row whose payload could not be built by
    `build_wire_payload` (e.g. a hostile/malformed `skills` value) is
    dead-lettered LOCALLY, without ever reaching the transport — this must
    never escape `drain()` as an exception (that would lose every ack
    already obtained this same drain and wedge the tenant's outbox on every
    subsequent drain). Named separately from every other unsendable-row
    hint so a doctor can tell "the row's own shape is broken" apart from
    "this row predates/postdates this build's schema"."""
    return (
        f"Ambient outbox: {len(session_ids)} spool row(s) could not be "
        f"built into a wire payload (a malformed field, e.g. skills) and "
        f"were dead-lettered locally without being sent — investigate the "
        f"producer that spooled them."
    )


# --------------------------------------------------------------------------- #
# Per-tenant outbox state: <data_dir>/insights/outbox/<tenancy>.json.
# --------------------------------------------------------------------------- #

def _outbox_state_path(tenancy):
    return os.path.join(_insights_session.data_dir(), "insights", "outbox", tenancy + ".json")


def _default_events_state():
    """T2-C3's own compartment of the state file, a SIBLING of `dead_letter`
    rather than a widening of it.

    ONE KEY IS THE AUTHORITY AND THE REST ARE DIAGNOSTICS, and that split is what
    makes the retry lane safe:

      * `resolved` — session ids whose skeleton needs NOTHING FURTHER, whatever
                     its outcome was (delivered, terminally undelivered, or a
                     recorded count discrepancy). THIS is what `_compact_spool`
                     consults: a spool row carrying a skeleton is retained until
                     its id appears here. Session ids only, and deliberately
                     UNCAPPED — the same unboundedness `state["delivered"]` has
                     always had, for the same reason (it is the only durable
                     memory of "already dealt with", and forgetting an entry
                     means re-sending it forever).

      * `delivered`  — session ids whose skeleton was accepted AND whose reported
                       counts agreed with what was sent. A subset of `resolved`,
                       kept separate because "landed cleanly" is the number a
                       human wants first.

      * `undelivered` / `discrepancy` / `pending` — BOUNDED DIAGNOSTIC entry
        lists (see `_EVENTS_DIAGNOSTIC_CAP`), each entry carrying an explicit
        `kind` rather than leaving a reader to infer the case from which optional
        fields happen to be set. Same kind+message idiom as `_unsendable_reason`,
        for the same reason: a doctor merging these must be able to tell "too big
        to send" from "the door said no".
          - `undelivered` — TERMINAL non-deliveries (`_EVENTS_TERMINAL_KINDS`).
          - `discrepancy` — the door 200'd and a count it REPORTED did not match
            what was sent. Kept separate from `undelivered` because it is NOT a
            non-delivery: something was very likely stored, just not provably all
            of it. RESOLVED, never retried — the server declares its counts
            diagnostic and a short `stored` can be a legitimate duplicate-seq
            collapse. A 200 that did not PROVE persistence is NOT here — neither
            count, or one absent and the other agreeing — because it claims
            nothing to disagree with; it lands in `pending` as `uncounted` and is
            retried (`_EVENTS_RETRYABLE_KINDS` states the boundary).
          - `pending` — the RETRY LANE's last-attempt record
            (`_EVENTS_RETRYABLE_KINDS`). NOT the retry lane itself: retention is
            structural (a row carrying a skeleton whose id is not in `resolved`),
            so losing a `pending` entry to the cap costs a diagnostic, never the
            data. An entry leaves this list the drain its session resolves.

      * `dropped` — per-list count of diagnostic entries the cap discarded, so a
        truncation is VISIBLE rather than silent. A bounded list with no such
        counter reads as a complete record of a small incident.

    Every one of these is read by `events_status` — a record with no reader is a
    record that evaporates."""
    return {"resolved": [], "delivered": [], "undelivered": [], "discrepancy": [],
            "pending": [],
            "dropped": {"undelivered": 0, "discrepancy": 0, "pending": 0}}


def _default_state():
    return {
        "delivered": [],
        "dead_letter": [],
        "backoff": {"attempts": 0, "next_attempt_at": None},
        "mute": {"until": None, "reason": None},
        "events": _default_events_state(),
    }


def _load_state(tenancy):
    """The persisted outbox state for `tenancy`, normalized so every caller
    can trust the shape (list/dict types, no missing keys). A missing,
    unreadable, or malformed state file degrades to `_default_state()` —
    never raises; a corrupt state file must never wedge a drain."""
    path = _outbox_state_path(tenancy)
    raw = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            raw = None
    if not isinstance(raw, dict):
        return _default_state()

    state = _default_state()
    state["endpoint_refusal"] = raw.get("endpoint_refusal") is True
    delivered = raw.get("delivered")
    if isinstance(delivered, list):
        state["delivered"] = [s for s in delivered if isinstance(s, str)]

    dead_letter = raw.get("dead_letter")
    if isinstance(dead_letter, list):
        state["dead_letter"] = [
            e for e in dead_letter
            if isinstance(e, dict) and isinstance(e.get("session_id"), str)
        ]

    backoff = raw.get("backoff")
    if isinstance(backoff, dict):
        attempts = backoff.get("attempts")
        state["backoff"] = {
            "attempts": attempts if isinstance(attempts, int) else 0,
            "next_attempt_at": backoff.get("next_attempt_at"),
        }

    mute = raw.get("mute")
    if isinstance(mute, dict):
        state["mute"] = {"until": mute.get("until"), "reason": mute.get("reason"),
                         "origin": mute.get("origin"), "identity": mute.get("identity")}

    # T2-C3. A state file written by a pre-T2-C3 build has no `events` key at
    # all, and normalizing it here (rather than at each read site) is what lets
    # `drain` and `events_status` both assume the shape — the same discipline the
    # four keys above already follow. Each list is filtered to entries a reader
    # can actually render: a str for `delivered`, a dict carrying a str
    # `session_id` for the other two.
    events = raw.get("events")
    if isinstance(events, dict):
        for key in ("resolved", "delivered"):
            ids = events.get(key)
            if isinstance(ids, list):
                state["events"][key] = [s for s in ids if isinstance(s, str)]
        for key in _EVENTS_DIAGNOSTIC_LISTS:
            entries = events.get(key)
            if isinstance(entries, list):
                state["events"][key] = [
                    e for e in entries
                    if isinstance(e, dict) and isinstance(e.get("session_id"), str)
                ]
        dropped = events.get("dropped")
        if isinstance(dropped, dict):
            # `ambient_digest._int_or_zero` IS this rule — "a real int (bools are
            # not), else 0" — and it is called rather than restated for the same
            # reason `_read_spool_rows` delegates to `ambient_digest._read_jsonl`:
            # `True is an int` is the trap, and one module owning the guard is what
            # keeps the two copies from disagreeing about it.
            state["events"]["dropped"] = {
                key: ambient_digest._int_or_zero(dropped.get(key))
                for key in _EVENTS_DIAGNOSTIC_LISTS
            }
        # A state file written by the FIRST T2-C3 draft has the three diagnostic
        # lists but no `resolved` key, and that migration cannot be skipped: with
        # `resolved` empty, every skeleton already dealt with would look
        # unresolved, so its spool row would be retained and its skeleton RE-SENT.
        # The three lists are exactly the resolved dispositions of that draft
        # (it had no pending lane at all), so they reconstruct `resolved` — one
        # id per entry, unioned with whatever the file already carried.
        if not isinstance(events.get("resolved"), list):
            recovered = set(state["events"]["delivered"])
            for key in ("undelivered", "discrepancy"):
                recovered.update(e["session_id"] for e in state["events"][key])
            state["events"]["resolved"] = sorted(recovered)

    return state


def _save_state(tenancy, state):
    path = _outbox_state_path(tenancy)
    _insights_session._ensure_private_dir(os.path.dirname(path))
    _loop_ledger._atomic_write_lines(path, [json.dumps(state, sort_keys=True) + "\n"])


# --------------------------------------------------------------------------- #
# Spool access (A1b's durable drain queue — read here, compacted here, never
# elsewhere; `_insights_session._spool_append` is the only writer of NEW rows).
# --------------------------------------------------------------------------- #

def _read_spool_rows(spool_path):
    """Every parseable JSON row in the spool, in file order — the drain queue's
    read side. Delegates to `ambient_digest._read_jsonl`, the single JSONL reader
    carrying the shared degrade-graceful discipline (missing file -> [],
    unparseable line skipped, never raises), so the two can never drift."""
    return ambient_digest._read_jsonl(spool_path)


def _write_spool_rows(spool_path, rows):
    """Atomically rewrite the spool to hold exactly `rows` (used only by the
    compaction step in `drain`, never for a plain append — new rows are always
    appended by `_insights_session._spool_append`)."""
    _insights_session._ensure_private_dir(os.path.dirname(spool_path))
    lines = [json.dumps(row) + "\n" for row in rows]
    _loop_ledger._atomic_write_lines(spool_path, lines)


def _pending_rows(rows, delivered, dead_letter_ids):
    """Yield (session_id, row) for every spool row that is a well-formed,
    still-PENDING entry — a dict with a non-empty str `session_id` that is
    neither delivered nor dead-lettered. The SINGLE definition of "still to be
    SENT on the activity door": the drain loop and the pending count on the
    early-return paths both drive off this one predicate rather than
    transcribing it.

    T2-C3 deliberately did NOT widen this. Spool COMPACTION now drives off
    `_retained_rows` instead — a superset that also keeps a row whose event
    skeleton is unresolved. Widening this predicate to cover that case would make
    the send loop re-send an already-acked session, so the two are separate
    functions with separate names and one call site each."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not isinstance(sid, str) or not sid:
            continue
        if sid in delivered or sid in dead_letter_ids:
            continue
        yield sid, row


def _row_has_skeleton(row):
    """True iff `row` carries a NON-EMPTY event skeleton. One predicate, used by
    retention, by the retry-candidate collapse and by `_attempt_events`'s own
    early skip, so "this row has a skeleton" cannot come to mean three things."""
    events = row.get("events")
    return isinstance(events, list) and bool(events)


def _events_unresolved(row, events_resolved):
    """True when `row` carries a skeleton whose fate is not yet settled — the
    predicate that gives events a genuine cross-drain retry lane.

    DERIVED FROM THE ROW AND THE RESOLVED-ID SET, never from a remembered
    "pending" write, and that is the whole point. The failure this replaces
    (reproduced on a real drain: 3 of 4 sessions lost) was not a missing record,
    it was that compaction did not CONSULT one — a skeleton could vanish because
    its activity row happened to ack. Deriving retention from the row's own
    contents plus the durable resolved set means a forgotten bookkeeping write
    can never make a skeleton reclaimable; at worst it costs a diagnostic entry."""
    if not _row_has_skeleton(row):
        return False
    sid = row.get("session_id")
    return isinstance(sid, str) and bool(sid) and sid not in events_resolved


def _retained_rows(rows, delivered, dead_letter_ids, events_resolved):
    """Yield (session_id, row) for every spool row compaction must KEEP: one
    whose ACTIVITY row is still pending, OR whose event SKELETON is still
    unresolved. A superset of `_pending_rows` — which stays exactly what it was,
    the definition of "still to be SENT on the activity door", because widening it
    would make the send loop re-send an already-acked session."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not isinstance(sid, str) or not sid:
            continue
        activity_pending = sid not in delivered and sid not in dead_letter_ids
        if activity_pending or _events_unresolved(row, events_resolved):
            yield sid, row


def _events_candidate_rows(rows, events_resolved):
    """`{session_id: row}` for every session whose skeleton is UNRESOLVED, the
    row being the LATEST spool line for that session that actually CARRIES a
    non-empty skeleton.

    "Latest that carries one" rather than "latest, then look" is deliberate: A1b's
    spool is at-least-once, so one session can hold several physical lines, and a
    re-digest after the opt-in was switched off appends a line with NO skeleton.
    Taking the latest line unconditionally would then find nothing to send and the
    row would stay retained forever while the skeleton sat one line above it. Same
    shape as the send loop's own latest-SENDABLE collapse, for the same reason."""
    latest = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not isinstance(sid, str) or not sid or sid in events_resolved:
            continue
        if _row_has_skeleton(row):
            latest[sid] = row  # file order, so the LATEST such line wins
    return latest


def _merge_events_entries(existing, new, cap=_EVENTS_DIAGNOSTIC_CAP):
    """Fold `new` diagnostic entries into `existing`, returning
    `(entries, dropped)` — the bounded list and how many entries the cap
    discarded (M2).

    DEDUP BY SESSION, newest last: a session re-attempted across drains must
    occupy ONE slot, not one per attempt, or a single unreachable door fills the
    ring with copies of the same fact and evicts every other session. A superseded
    entry is NOT counted as dropped — nothing about a distinct session was lost.

    TRUNCATION IS FROM THE FRONT (oldest first) and is COUNTED, because a bounded
    list with no counter reads as a complete record of a small incident. The count
    is returned rather than logged so the caller can accumulate it into the state
    file's own `dropped` tally."""
    merged = collections.OrderedDict()
    for entry in list(existing) + list(new):
        if not isinstance(entry, dict):
            continue
        sid = entry.get("session_id")
        if not isinstance(sid, str) or not sid:
            continue
        merged.pop(sid, None)  # re-insert at the END: newest attempt last
        merged[sid] = entry
    entries = list(merged.values())
    dropped = max(0, len(entries) - cap)
    return entries[dropped:], dropped


def _pending_session_ids(tenancy, state):
    """The distinct pending session_ids in the spool — used for the `pending`
    count on the two early-return paths (already-muted, transport=None) where
    the full drain loop never runs."""
    rows = _read_spool_rows(_insights_session._spool_path(tenancy))
    delivered = set(state["delivered"])
    dead_letter_ids = {e["session_id"] for e in state["dead_letter"]}
    return {sid for sid, _ in _pending_rows(rows, delivered, dead_letter_ids)}


# --------------------------------------------------------------------------- #
# The wire payload — PURE, and the single choke point for what ever leaves
# this process. PL-A2a pinned this to the field set the project-context ingest
# endpoint expects; the exact shape is held by a shared conformance fixture
# (`test_pla2a_wire_contract.py` / `test_wire_conformance.py`) rather than
# spelled out here, so client and endpoint can never drift apart silently.
# --------------------------------------------------------------------------- #

def _agents_from_rollups(rollups):
    """One `agents[]` entry per rollup, renaming the snake_case rollup fields
    to the payload's camelCase keys — VALUES pass through unchanged, only the
    keys are renamed.

    `outcome` is still never added (AC4): an ambient session ended, it did not
    succeed or abort, so any outcome here would be invented.

    `agentRole` is different, and T2-C1 split the two apart. It is no longer
    fabricated — it is OBSERVED: `ambient_digest.digest()` now groups on
    `(agent_role, model)`, taking the role from each sidecar's own
    `agent-*.meta.json` `agentType`, so `rollup["agent_role"]` is a reported
    fact. It is read with an unguarded `.get()` like every token field, so a
    rollup that carries no role (a pre-T2-C1 spool row, or a sidecar whose
    metadata could not be read) puts an explicit `null` on the wire rather
    than omitting the key — the consumer's `SessionAgentStats.agentRole` is
    `Optional[str] = None` precisely so absence of a claim is representable.

    The key set stays CLOSED: `agentType` is the ONLY thing taken from a
    sidecar's metadata. `description` (free text) and `toolUseId` (an internal
    handle) never reach this payload."""
    agents = []
    for rollup in rollups:
        if not isinstance(rollup, dict):
            continue
        agents.append({
            "modelId": rollup.get("model"),
            "agentRole": rollup.get("agent_role"),
            "inputTokens": rollup.get("input_tokens"),
            "outputTokens": rollup.get("output_tokens"),
            "cacheReadTokens": rollup.get("cache_read_input_tokens"),
            "cacheCreationTokens": rollup.get("cache_creation_input_tokens"),
        })
    return agents


def _raw_digest(row):
    """A deterministic hash of the SESSION's own content (PL-A2a round 2,
    D6): `row` — session_id, started_at/ended_at, entry_source, schema,
    skills, tool_counts, rollups, all of it — not merely one sub-part of it.
    Round 1 hashed `rollups` alone, so two DIFFERENT sessions sharing the
    same (frequently EMPTY) rollups list collided on the identical digest —
    e.g. a session whose only record carried no usage block produces
    `rollups == []` regardless of its own session_id/timestamps/tool_counts,
    so `sha256:<hash of []>` was reused across every such session. Hashing
    the whole row fixes this while staying PURE and clock-free: `row` never
    carries the three volatile runtime counters (flushLagS/
    pendingBacklogCount/evictedCount) — those arrive as this function's
    caller's OWN separate keyword parameters, never as part of `row` — so
    `rawDigest` can never be perturbed by drain-to-drain variation in them
    (AC6). `sort_keys=True` makes the canonical form independent of a dict's
    insertion order, mirroring the truncated-sha256-hex convention
    `_insights_session._tenancy_from_common` already uses for the opaque
    tenancy id.

    T2-C3 — the ONE exception to "all of it", and it is not an optimization.
    `ambient_digest.EVENT_ROW_KEYS` (the event skeleton, which travels through
    its OWN door and its own collection, never on this payload) is EXCLUDED.
    Without this, attaching a skeleton to the row would change `rawDigest` for
    EVERY session while the wire payload looked byte-identical: `rawDigest`
    ships, an unknown `events` key does not (the server accepts it, DROPS it, and
    200s with an identical body). Measured 2026-07-26: injecting `events=[]` — the
    emptiest possible value — moved the T2-C3 baseline harness sha from
    15874f3c… to 02d1ddce…. So the exclusion is what makes `rawDigest` mean
    "this session's session-activity content", which is the only thing the
    consumer can compare it against. `test_event_skeleton.py` asserts every name
    in that tuple is inert HERE and that the tuple is exactly what
    `digest(events=True)` adds, so a later events key cannot be added without
    one of the two going red."""
    canonical = json.dumps({k: v for k, v in row.items()
                            if k not in ambient_digest.EVENT_ROW_KEYS},
                           sort_keys=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _consent_object(row, granted_classes):
    """JC5 — the `consent` object BOTH ambient payloads carry, built from `row`'s
    four frozen keys (`ambient_digest.CONSENT_ROW_KEYS`) and the LIVE class list
    the drain resolved. Always a dict, never null.

    camelCase inside, matching the ambient payloads' own convention — and note
    the row keys are snake, matching the registry's. The two conventions meet
    here, once, rather than at every field.

    THE TWO LISTS ARE THE WHOLE POINT, and neither is redundant:

      * `classesAtCollection` is FROZEN — resolved at SessionStart against the
        checkout whose transcript this row was built from, and echoed unchanged
        through `meta` and the spool. A config edited between collection and drain
        does NOT retro-label it.
      * `classesApplied` is LIVE — the intersection with what that same checkout
        grants NOW, so a revoke takes effect on rows already on the spool.

    What that buys the server: a field empty under a class in
    `classesAtCollection` but ABSENT from `classesApplied` was WITHHELD; empty
    under a class present in both is GENUINELY EMPTY. Without both lists those two
    are the same bytes.

    `granted_classes is None` means NO LIVE RESOLUTION WAS AVAILABLE (a row whose
    checkout could not be identified — see
    `_insights_session.consent_classes_resolver`), and the applied list is then
    the frozen one unnarrowed. That is the only honest answer when nothing was
    resolved, and it is what keeps a legacy row shipping exactly what it ships
    today.

    ⚠️ PRE-CHANGE SPOOL ROWS. Rows written before this change carry no stamp, and
    `_unsendable_reason` classifies by SCHEMA, which this change deliberately does
    NOT bump — so those rows are drained and sent. Neither naive default is
    acceptable: `[]` labels them as consenting to nothing while sending them
    anyway, and a plain `["A","B","C"]` asserts a grant nobody resolved. They are
    therefore stamped with all three classes under the DISTINCT basis
    `pre_consent`, which is what lets the server tell an inferred stamp from a
    resolved one. Following `_load_state`'s migration precedent: infer at read,
    never rewrite the row.

    ⚠️ AND `ambient_digest.SCHEMA_VERSION` MUST STAY UNBUMPED for exactly that
    reason. Bumping it sends every backlogged row down `_unsendable_reason`
    branch 3 — `"held"`: never sent, never terminal, never reclaimed by
    `_compact_spool` — i.e. stranded forever. The migration and the schema
    constant are one decision, not two.

    🔴 ABSENT AND PRESENT-BUT-UNREADABLE ARE DIFFERENT FACTS, and until 2026-08-14
    this function collapsed them — the SAME hole already closed twice on the loop
    lane (`insights_flush_payload._consent`, at both of its levels), reached here
    through a THIRD reader of one record. The old test was a single conjunction:
    anything it could not read took the pre-change branch and was relabelled
    `["A","B","C"] + pre_consent`, i.e. "collected before this machine existed,
    all three in force". Reproduced on four shapes, every one of which a hand
    edit or a truncated write produces: `consent_classes: ["A"]` with a missing
    or empty `consent_basis`; `["A","Z"]`; `["A", 1]`; and the string `"ABC"`.
    Each shipped a WIDER grant than the record supports, which is the one
    direction a consent stamp must never fail.

      * NOTHING STAMPED AT ALL — none of `ambient_digest.CONSENT_ROW_KEYS` present
        — is the pre-change case: all three, `pre_consent`, as above.
      * ANYTHING ELSE IS PRESENT, and is read by
        `_insights_session.read_frozen_stamp`: an unreadable class list fails
        closed to `[]` + `unreadable` (its own name, because `explicit` claims a
        decision was parsed and `pre_consent` claims all three were in force —
        both are statements the record does not support), a readable one keeps
        the letters this build knows and remaps an unrecognized `basis` to
        `explicit`.

    ABSENCE IS DECIDED FROM THE ARTIFACT, not from `consent_classes` alone.
    `ambient_digest._provenance_fields` writes the four keys as a set or not at
    all, so "no stamp" is "none of the four present"; testing only the class key
    would let a row carrying `consent_basis` and nothing else take the pre-change
    branch and re-open this hole one key over.

    ⚠️ THIS IS THE THIRD READER OF ONE RECORD, and `read_frozen_stamp` exists
    because the first two had already diverged. It is ADOPTED here rather than
    re-implemented — neither difference the two lanes have is a blocker, and both
    are provided for by that function's own contract: it takes the loop lane's
    nested key spelling, so the flat `consent_*` row keys are adapted in the one
    dict literal below (its docstring names that adaptation as the caller's), and
    it refuses to decide ABSENCE at all ("call this only once you have decided the
    record is PRESENT"), which is exactly why the unstamped-row inference stays
    here where the lane-specific answer belongs.

    THE VERSION IS ASSERTED, NEVER COPIED (§F.3, and the same rule `contentMode`
    below already follows). It used to be `row.get("consent_version")` behind a
    non-empty-string test, so any string at all reached the wire — a hand-edited
    row could ship free prose in a field the server reads as a vocabulary id. What
    makes asserting it honest rather than mislabelling: the letters above are
    filtered against THIS build's `ALL_CONSENT_CLASSES` and the basis against THIS
    build's `CONSENT_BASIS_ORDER`, so the stamp emitted here genuinely IS a
    statement in this build's vocabulary, whatever edition the row claimed. The
    loop lane's builder already writes the constant flat, for the same reason."""
    stamped = any(key in row for key in ambient_digest.CONSENT_ROW_KEYS)
    if not stamped:
        classes = list(_insights_session.ALL_CONSENT_CLASSES)
        basis = _insights_session.CONSENT_BASIS_PRE_CONSENT
    else:
        classes, basis = _insights_session.read_frozen_stamp(
            {"classes": row.get("consent_classes"),
             "basis": row.get("consent_basis")})
    version = _insights_session.CONSENT_VERSION
    # 🔴 THE LIVE CONTAINER IS VALIDATED, NOT JUST ITS MEMBERS — the loop lane's
    # own 2026-08-14 fix, which stopped at the site it was reported on and never
    # reached this builder. `set(granted_classes)` iterates whatever it is given:
    # the STRING "ABC" iterates to three letters and GRANTS ALL THREE, and the
    # dict `{"A": False}` iterates to its keys and grants A — a malformed live
    # resolution failing OPEN on the one axis whose whole job is to narrow. An int
    # raises `TypeError`, which inside `drain`'s send loop is caught as an
    # unbuildable payload and dead-letters the whole session. Anything that is not
    # a real collection means no class granted; members are filtered by membership
    # (`in`, not hashing) so an unhashable element cannot raise on the way in.
    if granted_classes is None:
        applied = list(classes)
    elif not isinstance(granted_classes, (list, tuple, set, frozenset)):
        applied = []
    else:
        live = {c for c in granted_classes
                if c in _insights_session.ALL_CONSENT_CLASSES}
        applied = sorted(set(classes) & live)
    # §F.4 — references-only, PERMANENTLY. The row's own value is READ and then
    # NORMALIZED: anything other than the one accepted value (including a future
    # `"snippets"`, and including a row hand-edited or written by a later build)
    # becomes `"references"` here and now, because JC6 is gated and no code path
    # in either repo may emit content bytes before it lands.
    #
    # WRITTEN AS A READ-AND-NORMALIZE RATHER THAN AS THE BARE CONSTANT, even
    # though the two are identical today. The constant alone is the shorter line
    # and it would make the row key `consent_content_mode` decorative — stored by
    # the producer, read by nobody — and it would make the sentence above false as
    # a description of this code: nothing would be normalized, because nothing
    # would be read. This is the line that becomes load-bearing the day JC6 lands,
    # and the day a comment first has to be true is not the day to start writing
    # it that way.
    mode = row.get("consent_content_mode")
    return {
        "classesAtCollection": classes,
        "classesApplied": applied,
        "version": version,
        "basis": basis,
        "contentMode": (mode if mode == _insights_session.CONSENT_CONTENT_MODE
                        else _insights_session.CONSENT_CONTENT_MODE),
    }


def build_wire_payload(row, tenancy, *, flush_lag_s, pending_backlog_count,
                       evicted_count, granted_classes=None):
    """The exact JSON body POSTed to the project-context ingest endpoint (PC-A2)
    — the closed field set it expects. PURE: no clock/fs/env read, so identical
    inputs always yield an identical (deep-equal, byte-identical once
    serialized) output.

    `row` is the PL-A2a ENRICHED spool-row dict (`session_id`/`started_at`/
    `ended_at`/`entry_source`/`schema`/`skills`/`pluginVersion`/
    `parserDegraded`/`tool_counts`/`rollups`, see `ambient_digest.digest`'s
    own return shape) — its own `row.get("tenancy")`, if present, is NEVER
    read; `tenancy` is always the caller's authoritative, pre-resolved opaque
    id. `flush_lag_s`/`pending_backlog_count`/`evicted_count` are the three
    PC-A2 runtime counters, computed and injected by `drain()` — this
    function never reads a clock, the filesystem, or drain's own state to
    derive them itself.

    `skills` (PL-A2a round 2, D1) is read from `row["skills"]` — the ROW
    level `ambient_digest.digest()` now hoists it to — never re-derived from
    `rollups` (a rollup no longer carries its own `skills` copy at all, and
    even when it did, a union over `rollups` is always `[]` whenever
    `rollups` itself is empty, silently dropping a row-level skill name for
    exactly the session this round's D1 fix targets).

    Identity and telemetry fields the endpoint adds downstream are not
    parameters this function accepts. `outcome` is never fabricated at either
    level (AC4), and `agentRole` never appears at the TOP level — a session
    has no single role. Per `agents[]` entry, `agentRole` is now carried
    through from the rollup's observed `agent_role` (T2-C1) — see
    `_agents_from_rollups`.

    `granted_classes` (JC5) is the LIVE consent resolution, passed IN by the
    caller — this function reads no config and no repo, and could not, without
    losing the purity every conformance fixture depends on. `None` means "no live
    resolution available", under which nothing is narrowed; see `_consent_object`.

    WITHHOLDING IS CLASS C ONLY on this payload, because class C is the only class
    that covers anything the ambient lane sends: `agents`, `toolCounts`, `skills`
    (and `events`, which travels through its own door). A and B describe merged
    diffs and rejected proposals — loop-lane facts this payload has never carried,
    so withholding them here would empty nothing.

    A WITHHELD FIELD IS EMPTY, NOT ABSENT, and that is deliberate: the key set
    stays the closed shape the server declares, so withholding cannot be mistaken
    for a schema drift. `consent.classesApplied` is what says an empty list was a
    decision rather than a quiet session.

    WITHHOLDING IS APPLIED AT THE SOURCE, in the hoist block below, and never a
    second time on the payload line. `[]` and `{}` are values the type guards on
    those lines already accept unchanged, so a withheld field needs no second
    spelling down there — which is what keeps each line to ONE concern: a hoisted
    name decides withholding, a payload line decides the type guard, neither does
    both. Written the other way (`[] if withhold_c else (<guard>)`) the withheld
    value appeared twice per field — once as the withheld literal, once as the
    guard's own fallback — two copies free to drift apart with nothing to catch
    it, and `row.get("tool_counts")` was additionally fetched twice inside one
    such expression. The `rollups` list guard MOVED from the hoist to the
    `agents` line in the same change and must never be dropped on the way:
    `_agents_from_rollups` iterates its argument, and `row.get("rollups")` is
    `None` on the ORDINARY row that carries no rollups key at all — not merely on
    a hostile one — so an unguarded call raises `TypeError` there (measured
    2026-08-14: `_agents_from_rollups(None)` and `(5)` both raise; a dict and a
    str return `[]`). Inside `drain`'s send loop that exception is caught as an
    unbuildable payload, so the whole session would be locally dead-lettered
    instead of delivered with an empty `agents` list.

    🔴 WITHHOLDING READS `classesApplied`, NEVER THE LIVE LIST, and that is the
    2026-08-14 fix rather than a restatement. It used to read
    `granted_classes is not None and "C" not in granted_classes` — the LIVE
    resolver's answer — while `_consent_object` reports `classesApplied` as
    `frozen ∩ live`. The two disagree exactly when a config is WIDENED after
    collection: a row frozen at `consent_classes: ["A"]`, drained after `consent`
    granted C, shipped `agents`/`toolCounts`/`skills` IN FULL under a stamp saying
    `classesApplied: ["A"]`. That is a grant applied BACKWARDS — the thing the
    two-list stamp exists to make impossible — and it made the stamp contradict the
    body beside it, which is worse than either alone: a server verifying a
    withholding against `classesApplied` would read the row as proof the stamp
    cannot be trusted. Computing the consent object FIRST and withholding on its
    own applied list is what the loop lane's builder already does
    (`insights_flush_payload.build_loop_payload`: `applied =
    set(consent["classes_applied"])`), so the two lanes now answer one question
    one way.

    A CONSEQUENCE WORTH STATING, because it is an intended behaviour change and
    not a regression: with `granted_classes=None` — no live resolution available —
    `classesApplied` is the FROZEN list, so a row frozen without C now withholds
    where it used to send. That is the same answer the loop lane gives the same
    input, and the honest one: the row says it was collected under a grant that
    does not cover these fields, and no live resolution said otherwise."""
    consent = _consent_object(row, granted_classes)
    withhold_c = "C" not in consent["classesApplied"]
    skills = [] if withhold_c else row.get("skills")
    tool_counts = {} if withhold_c else row.get("tool_counts")
    rollups = [] if withhold_c else row.get("rollups")
    # §F.3 SIBLING, found by applying the `basis`/`version` predicate — "a value
    # copied to the wire without checking it against its closed set" — to every
    # other scalar this payload lifts off the row. `entry_source` is a CLOSED ENUM
    # (`_insights_session._ALLOWED_ENTRY_SOURCES`) whose allowlist was applied at
    # REGISTRATION and nowhere else, so a hand-edited spool row shipped
    # `entry_source` verbatim: `"resume because PROJECT-X said no"` is free human
    # prose on the wire, by exactly the mechanism `basis` was. Normalized here,
    # through the one allowlist, at the boundary that actually emits it.
    #
    # `None` SURVIVES AS `None` rather than becoming `"other"`, and that boundary
    # is load-bearing: a null `entrySource` is a legitimate wire value meaning "the
    # digester never saw one" (pinned by
    # `tests/fixtures/wire/session_activity_degraded_conformance.json`), while
    # `"other"` is a positive claim that the harness reported an entry source this
    # build does not recognize. Absent is not unrecognized, and normalizing it
    # would both fabricate that claim and move a byte-pinned fixture.
    entry_source = row.get("entry_source")
    return {
        "sessionId": row.get("session_id"),
        "repoRef": tenancy,
        "repoRefScheme": "opaque-tenancy",
        "entrySource": (None if entry_source is None
                        else _insights_session._clean_entry_source(entry_source)),
        "pluginVersion": row.get("pluginVersion"),
        "startedAt": row.get("started_at"),
        "endedAt": row.get("ended_at"),
        "skills": sorted(skills) if isinstance(skills, list) else [],
        "toolCounts": tool_counts if isinstance(tool_counts, dict) else {},
        "agents": _agents_from_rollups(rollups if isinstance(rollups, list) else []),
        "consent": consent,
        "decisionsCount": 0,
        "flushLagS": flush_lag_s,
        "pendingBacklogCount": pending_backlog_count,
        "evictedCount": evicted_count,
        "parserDegraded": bool(row.get("parserDegraded")),
        "rawDigest": _raw_digest(row),
    }


def _unsendable_reason(row):
    """PL-A2a round 2 (D4) + round 3 (F3/F4 — Codex+Grok convergent review).
    The ONE function deciding whether `row` can ever be safely built into a
    `build_wire_payload` call and sent. Returns `(kind, message)` — `kind`
    is one of `"legacy"`, `"held"`, or `"incomplete"` (message a non-empty,
    human-readable string distinguishable BY KIND — a doctor merging every
    dead_letter/hint list must be able to tell them apart) — or `None` if
    the row is sendable.

    SCHEMA is checked FIRST, then completeness (round 3, F4 — the reverse
    order round 2 shipped misdiagnoses a CURRENT-schema row with missing
    timestamps as an old, safe-to-ignore leftover instead of the LIVE
    producer defect it actually is):

      1. RECOGNIZED schema (`row["schema"] == ambient_digest.SCHEMA_VERSION`)
         + valid started_at/ended_at -> sendable (`None`).
      2. RECOGNIZED schema + missing started_at/ended_at -> `"incomplete"`
         (F4): a LIVE producer defect (a registry read failure, a corrupt
         registry row at digest time, or a direct `_digest_one_session`
         call) — terminal (dead-lettered LOCALLY): no schema bump ever
         recovers genuinely absent timestamps, so holding it forever would
         accomplish nothing but accumulate dead weight in the spool.
      3. UNRECOGNIZED schema (absent, or present but different from
         `SCHEMA_VERSION`) + valid started_at/ended_at -> `"held"` (F3, a
         round-3 AMENDMENT to round 2's own dead-letter-everything fix):
         kept PENDING — never sent, never terminal — so a LATER plugin
         version that understands the new schema can still deliver it.
         Round 2 dead-lettered this case unconditionally, which made a
         genuine future-version row (or a transitional/partial-deploy row
         with no schema at all) PERMANENTLY unrecoverable: `digested_at` is
         already stamped at spool time, so there is no re-digest path.
      4. UNRECOGNIZED schema + missing started_at/ended_at -> `"legacy"`
         (AC8, unchanged from round 2): the true pre-PL-A2a shape — no
         schema stamp AND no timestamps at all, never recoverable
         regardless of schema — terminal (dead-lettered LOCALLY).

    Only `"legacy"`/`"incomplete"` are terminal; `"held"` is left pending
    and is therefore never reclaimed by `_compact_spool` (which only ever
    reclaims rows already in `delivered`/`dead_letter`)."""
    schema = row.get("schema")
    schema_recognized = schema == ambient_digest.SCHEMA_VERSION
    has_timestamps = bool(row.get("started_at")) and bool(row.get("ended_at"))

    if schema_recognized:
        if has_timestamps:
            return None
        return ("incomplete",
                f"incomplete spool row: current schema "
                f"({ambient_digest.SCHEMA_VERSION!r}) but missing "
                f"started_at/ended_at — a live producer defect (not a "
                f"stale, pre-dating leftover), needs investigation")

    if not has_timestamps:
        return ("legacy",
                "legacy (pre-enrichment) spool row: missing started_at/ended_at "
                "— this row predates the PL-A2a wire-schema convergence")

    return ("held",
            f"unrecognized schema {schema!r} on an otherwise well-formed "
            f"row (expected {ambient_digest.SCHEMA_VERSION!r}) — held "
            f"pending, never sent, until a plugin version that understands "
            f"this schema can deliver it")


# --------------------------------------------------------------------------- #
# T2-C3 — the events payload, and the one place a skeleton's fate is decided.
# --------------------------------------------------------------------------- #

def build_events_payload(row, tenancy, *, granted_classes=None):
    """The exact JSON body POSTed to the events door — a CLOSED key set, and
    PURE in the same sense as `build_wire_payload`: no clock, no filesystem, no
    env, no config read, so identical inputs always yield byte-identical output.

    (SEVEN until `projectorVersion` landed, and the docstring went on saying
    seven — which is why the count is now derived by the conformance test from
    the returned dict rather than restated here in prose. Every byte figure
    quoted against this envelope was re-measured on the eight-key form on
    2026-07-27; see `_EVENTS_MAX_ROWS`.)

    NEVER merged into the session-activity payload, and that is not a style
    choice. THE INLINE MEASUREMENT, 2026-07-27, and it is the ONE number both
    repos cite — derivation included so it can be reproduced rather than
    inherited: for every session with a non-empty skeleton, build the real
    `build_wire_payload` output, attach `events`+`lanes` to it, and take
    `len(json.dumps(payload).encode("utf-8"))` — the transport's own call,
    default separators. 68 of 265 sessions (25.7%) exceed the session-activity
    door's 262144-byte cap. That door returns 413 before parsing, and `_classify`
    maps 413 to `dead_letter`, which is never retried and whose spool bytes
    `_compact_spool` then reclaims. So inlining would permanently destroy the
    WHOLE session record — tokens, skills, tool counts and all — for a quarter of
    sessions, in exchange for an event skeleton nobody asked to be mandatory.
    (Two earlier figures, 22.0% and 26.5%, were a design-survey measurement of a
    bare `{"lanes":…,"events":…}` blob and a proxy measurement of the EVENTS
    payload against the ACTIVITY cap. Both are superseded by the one above, which
    measures the payload that would actually be sent. The server's schema comment
    cites the identical number, date and derivation.)

    `row` is the spool row `ambient_digest.digest(..., events=True)` produced;
    `row["events"]`/`row["lanes"]` are `ambient_digest.EVENT_ROW_KEYS`, the two
    keys `_raw_digest` excludes precisely so that attaching them cannot move the
    session payload's `rawDigest`. Both are read defensively (a non-list
    degrades to `[]`) because this function is called from inside `drain`'s send
    loop, where an exception costs every ack obtained earlier in the same drain.

    THE KEY NAMES ARE THE SERVER'S, and two of them were this client's own
    invention until 2026-07-26. `contractVersion` carrying
    `EVENTS_CONTRACT_VERSION` is what the events door declares; the
    first draft sent `schema` carrying `ambient_digest.EVENT_SCHEMA_VERSION`
    instead, and the server DROPPED it and substituted its own default — so
    every stored row claimed a contract version this client never sent, and
    the server's unknown-version warning could never fire on a drift. See
    `EVENTS_CONTRACT_VERSION` for why the local `EVENT_SCHEMA_VERSION` stamp is
    deliberately a DIFFERENT namespace and must not be unified with this one.

    THERE IS NO `eventCount`, and its absence is the fix rather than an omission.
    The server derives the count from `len(events)` — it declares no such field,
    so the first draft's copy was dropped on every request. A second, droppable
    copy of a number the receiver already has is worse than none: it can disagree
    with the array it describes, and a reader cannot tell which one is the truth.
    `_attempt_events` compares the door's reported counts against
    `len(payload["events"])`, i.e. against the rows themselves.

    `userId`/`company` are absent BY CONTRACT, not forgotten: the route injects
    them from the credential last, precisely so a body value can
    never win, and the conformance test bans both substrings from this payload.

    Privacy negative space, unchanged from the session payload: the opaque
    `tenancy` is the only repo identity, and a skeleton row carries structure
    only (kind/actor/uuid/parent/lane/seq, the four token ints, a tool NAME) —
    no text, no tool input, no file path, no command, no diff.

    JC5 — THIS PAYLOAD CARRIES THE CONSENT STAMP TOO, and an earlier draft said it
    did not. That was wrong, and it left the lane carrying the richest per-step
    data (and §F.6's reserved `reward` slot) with no class and no consent version
    at all, while class C explicitly covers `events`. The join to the activity
    row's stamp is NOT guaranteed and cannot be assumed: `_raw_digest` excludes
    `EVENT_ROW_KEYS`, the two payloads are classified independently (the activity
    row can 413 to `dead_letter` and have its spool bytes reclaimed while this
    batch is delivered), and this lane is gated separately per session. So the
    stamp is sourced HERE, from the same four frozen row keys, rather than
    inherited.

    ⚠️ THERE IS NO CLASS-C WITHHOLDING INSIDE THIS FUNCTION, deliberately. The
    whole batch IS class C, so "withholding" means NOT SENDING IT — an empty
    `events` list would be a positive claim that the session had no steps, which
    is a fabricated measurement, not a redaction. The decision is taken one level
    up, in `_insights_session.run_drain`, where class C is ANDed with the
    `event_skeleton` switch into the three-valued state this lane already
    consumes. `granted_classes` here therefore only ever narrows the reported
    `classesApplied`; it never empties a field."""
    # JC14 — THE ALLOWLIST IS APPLIED HERE, AT EMISSION, and it was not before.
    # These two arrays used to be lifted off the spool row behind nothing but an
    # `isinstance(..., list)` degrade, so every per-key guarantee the producer
    # makes — `_string_field`'s three properties (type, the 200-char bound,
    # non-empty), `_int_or_zero`'s bool exclusion, `_is_reserved_numeric`'s type
    # test — was enforced at RECORDING time with a laptop-local JSONL file
    # sitting between recording and emission. The projectors live in the
    # producer module, which owns the declaration (same import-graph rule as
    # `SCHEMA_VERSION` and `CONSENT_ROW_KEYS`: the lowest module owns the
    # literal), and they absorb the non-list degrade this code used to perform.
    #
    # ⚠️ SCOPE, so a reader does not take this payload for guarded: JC14 covers
    # `events[]` and `lanes[]` ONLY. `sessionId` and `pluginVersion` below are
    # still copied whole off the same spool row with no type check and no bound
    # — and the write-side allowlist for the first of them EXISTS
    # (`_insights_session._clean_session_id`, `_MAX_SESSION_ID_LEN = 200`,
    # `_BAD_ID_CHARS` rejecting `/\\\n\r\t`), it is simply not re-applied here.
    # Reproduced 2026-08-20 against the unprojected tree: a spool row carrying
    # `session_id = "../../etc/passwd\nX" + "S"*5000` emits a 5,018-character
    # `sessionId` WITH the embedded newline, and a dict `pluginVersion` emits as
    # a dict. The same two lines recur at `build_wire_payload`, along with
    # `agents[]`, `skills` and `toolCounts` on the activity door. Sibling card.
    events = ambient_digest.project_event_rows(row.get("events"))
    lanes = ambient_digest.project_lanes(row.get("lanes"))
    return {
        "sessionId": row.get("session_id"),
        "repoRef": tenancy,
        "repoRefScheme": "opaque-tenancy",
        "contractVersion": EVENTS_CONTRACT_VERSION,
        "pluginVersion": row.get("pluginVersion"),
        "lanes": lanes,
        "events": events,
        "consent": _consent_object(row, granted_classes),
        "projectorVersion": ambient_digest.EVENT_SCHEMA_VERSION,
    }


def _events_wire_bytes(payload):
    """The byte length of `payload` AS THE TRANSPORT ACTUALLY SERIALIZES IT.

    THIS IS THE WHOLE POINT OF THE FUNCTION, and it was a live bug in the first
    draft of this change. `make_urllib_transport` sends
    `json.dumps(payload).encode("utf-8")` — DEFAULT separators, i.e. `", "` and
    `": "`. The T2-C3 design survey measured the skeleton with
    `separators=(",", ":")` — COMPACT. Computing the cap check against the
    compact form and then sending the default form means a payload passes the
    check and still 413s, which is exactly the permanent-loss branch this cap
    exists to avoid. So the check is computed here, with the identical call.

    MEASURED 2026-07-27 over ~/.claude/projects, 265 sessions with a non-empty
    skeleton, real producer output through `build_events_payload` end to end:
        rows          p50=339   p90=1628   p99=2960   max=4801
        COMPACT bytes p50=84237 p90=422863 p99=788118 max=1277256
        WIRE    bytes p50=90334 p90=454845 p99=849003 max=1375673
    Wire is 1.077x compact at the maximum — the rows are small dicts, so the two
    extra characters per separator add ~8%, not the multiple a first draft of
    this comment asserted before anyone measured it. Against a 2 MiB door that
    margin changes no verdict today: 0 of 265 sessions exceed the cap under
    EITHER serialization, so the omission path really is unexercised by this
    corpus, exactly as the plan said. The identical call is used anyway, because
    the margin is data-dependent and an unaccounted 8% is a cap that is silently
    8% higher than the number declared next to it — which is how a cap check
    ends up passing a payload the door then 413s.

    The same pass produced the ONE inline-overflow number both repos now cite —
    68 of 265 sessions, 25.7%, over the session-activity door's 262144 cap when
    `events`+`lanes` are attached to the real activity payload. See
    `build_events_payload` for the derivation and for the two superseded figures
    (22.0% and 26.5%) it replaces."""
    return len(json.dumps(payload).encode("utf-8"))


_EventsCounts = collections.namedtuple("_EventsCounts", ["received", "stored"])


def _events_counts(body):
    """The door's own reported counts from a parsed response body, as
    `(received, stored)` — each an int or None when the door did not report it.

    BOTH ARE READ, because they answer two different questions and only together
    do they cover the drift this door exists to make visible. `received` is what
    the SERVER SCHEMA PARSED out of the batch — if it disagrees with the number of
    rows sent, the envelope or the rows are being reshaped in transit (extras
    dropped, a row rejected) even though the request 200'd. `stored` is what is
    durably present as a result of the call. `stored < received` is the server's
    own legitimate duplicate-`seq` collapse, so it is diagnostic, never a retry
    signal.

    THE SAME RULE AS `_ack_proves_persistence`, ASKED FOR THIS DOOR'S OWN KEYS:
    a 200 proves the request was well-formed, and only the door's declared
    success shape in the BODY proves anything was stored. This function cannot
    delegate to it — it needs the keys' VALUES, for the reason the next
    paragraph gives — so the two are separate implementations of one rule, and
    anyone hardening "is this body this door's answer" has to edit both. That
    cross-reference is stated in both directions on purpose: an earlier revision
    claimed the question was "asked once" when it was not.

    `make_urllib_transport` returns `Response(status, None)` for an empty or
    unparseable body, and a hostile/older door can return any JSON at all, so
    `body` may be None, a list, or a string. `isinstance(v, int) and not
    isinstance(v, bool)` is the same PREDICATE `ambient_digest._int_or_zero`
    applies, and it is not pedantry here: `True` is an `int` in Python, so a door
    answering `{"stored": true}` would otherwise read as "1 row stored" and a
    1-row skeleton would silently agree with it.

    It is stated inline rather than DELEGATED to that helper — which `_load_state`
    does call for its own `dropped` tally — because only the predicate is shared,
    not the answer. `_int_or_zero` substitutes 0, and 0 is a CLAIM: "the door told
    us it stored nothing". This function must return None, "the door reported no
    count at all", because that is what `_attempt_events` renders as an unproven
    delivery. Reusing the helper here would turn a silent door into one asserting
    a zero."""
    if not isinstance(body, dict):
        return _EventsCounts(None, None)

    def _count(key):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    return _EventsCounts(_count(EVENTS_RECEIVED_KEY), _count(EVENTS_STORED_KEY))


# `disposition` is one of "skip" (no skeleton on this row — the opt-in is off,
# the overwhelmingly common case, and NOTHING is recorded), "delivered",
# "discrepancy", "undelivered" (TERMINAL, `_EVENTS_TERMINAL_KINDS`), or one of
# the three RETRYABLE dispositions — "pending", "consent_unknown" and
# "uncounted" (`_EVENTS_RETRYABLE_KINDS` — the skeleton stays on the spool and a
# later drain re-attempts it); `entry` is the session_id for "delivered" and a
# dict for the rest. `halt` asks the caller to attempt NO further skeletons this
# drain (a 401/403 from the events door).
#
# The four RESOLVED dispositions are delivered / discrepancy / undelivered /
# skip; the three retryable ones leave the spool row retained. That mapping
# lives in `_events_record` and nowhere else.
#
# THE THREE RETRYABLE DISPOSITIONS SHARE A LANE BUT NOT A DIAGNOSIS, which is
# why they are three and not one: "the door is unreachable", "the repo config
# cannot be read" and "the door 200'd without proving persistence" send an
# operator to three different places, and a single hint would be wrong for two
# of them.
_EventsOutcome = collections.namedtuple("_EventsOutcome", ["disposition", "entry", "halt"])


def _attempt_events(row, sid, tenancy, events_transport, cap,
                    max_rows=_EVENTS_MAX_ROWS, granted_classes=None):
    """Attempt ONE session's skeleton against the events door and return its
    disposition. Never raises — the caller is inside `drain`'s send loop, where
    an escaping exception would abort the drain before `_save_state` and lose
    every ack already obtained this round.

    PRECONDITION, enforced at both call sites: this runs only for a session whose
    SESSION-ACTIVITY row is ACKED — either just now, in the send loop, or on an
    earlier drain, in the retry pass. The ordering is the requirement, not an
    implementation detail — the skeleton is an annotation on a session record that
    must already exist, and nothing here can ever un-ack or dead-letter that
    record: this function returns a disposition and the caller writes it to a
    SEPARATE part of the state file. `state["delivered"]` and
    `state["dead_letter"]` are not reachable from here at all.

    A 401/403 from the events door sets `halt` but deliberately does NOT touch
    `state["mute"]`: that window paces the SESSION door, and muting the primary
    lane because the secondary, opt-in one refused would trade the data that is
    always wanted for the data that was merely allowed. Halting for the rest of
    this drain is enough — the next drain re-attempts.

    THERE IS A CROSS-DRAIN RETRY LANE, and this function's own contract is what
    makes it possible: a `pending` disposition leaves the session OUT of
    `state["events"]["resolved"]`, `_retained_rows` therefore keeps its spool row,
    and the next drain finds the skeleton still on disk. Before that lane existed,
    a 401 halt (or any 404/429/503) acked the session, stopped yielding its row,
    and let compaction reclaim the skeleton with no durable record at all —
    executed on a real drain and a real state file: 4 sessions acked with a
    skeleton in hand, 1 with any record of it, 3 silently lost. The two bound
    breaches below stay TERMINAL, because no retry can shrink a payload."""
    if not _row_has_skeleton(row):
        return _EventsOutcome("skip", None, False)
    rows = len(row["events"])

    if events_transport is None:
        # RETRYABLE, not terminal. Unreachable in production today: both
        # transports are derived from the SAME resolved endpoint, so either both
        # exist or `drain` already returned on the `transport is None` no-op path.
        # Kept retryable because if a future config split ever separates the two
        # urls, a temporarily-unconfigured events door must not destroy skeletons.
        # THE DEPENDENCY THAT MAKES THAT SAFE, named so a config split has to
        # re-examine it: retention is unbounded while a skeleton stays pending, so
        # a permanently-absent events door alongside a working session door would
        # grow the spool indefinitely (with the backlog hint firing every drain).
        return _EventsOutcome("pending", {
            "session_id": sid, "kind": "no_door", "status": None, "rows": rows,
            "reason": "no events door was configured, so the skeleton was "
                      "not sent (the session row was delivered); it stays on "
                      "the spool for a later drain",
        }, False)

    try:
        payload = build_events_payload(row, tenancy,
                                       granted_classes=granted_classes)
    except Exception as exc:
        return _EventsOutcome("undelivered", {
            "session_id": sid, "kind": "unbuildable", "status": None, "rows": rows,
            "reason": f"events payload could not be built: {exc!r}",
        }, False)

    # ROW bound first, byte bound second — the cheaper check, and the one whose
    # breach the byte check cannot detect (see `_EVENTS_MAX_ROWS`: 20,001 minimal
    # rows are 2,049,203 bytes, under the 2 MiB cap, and the door 422s them).
    if rows > max_rows:
        return _EventsOutcome("undelivered", {
            "session_id": sid, "kind": "over_rows", "status": None,
            "rows": rows, "bytes": None,
            "reason": f"skeleton has {rows} rows, over the events door's "
                      f"{max_rows}-row batch limit — OMITTED WHOLE rather than "
                      f"sent and permanently 422'd",
        }, False)

    size = _events_wire_bytes(payload)
    if size > cap:
        return _EventsOutcome("undelivered", {
            "session_id": sid, "kind": "over_budget", "status": None,
            "rows": rows, "bytes": size,
            "reason": f"skeleton is {size} bytes, over the events door's "
                      f"{cap}-byte budget — OMITTED WHOLE, never truncated",
        }, False)

    try:
        response = events_transport(payload, sid)
    except Exception:
        response = None  # a transport that raises is "unreachable", never a delivery
    status = getattr(response, "status", None) if response is not None else None
    # The body is threaded here too, even though this door has no role gate
    # today (it resolves no project to gate on). Threading it costs nothing
    # and removes the one place a future gate on this door could silently
    # misclassify: with a status-only call, a role 403 from here would fold
    # into `mute_kill` and nothing would fail. `mute_role` halts this pass
    # like the other two mutes do (below), so it can never fall through to
    # the retry lane.
    kind = _classify(status, getattr(response, "body", None))

    if kind == "ack":
        counts = _events_counts(getattr(response, "body", None))
        sent = len(payload["events"])
        # THE BOUNDARY, in code, in the order it is decided. The words are in
        # `_EVENTS_RETRYABLE_KINDS` and are not restated here.
        #
        # DISAGREEMENT FIRST, because it is the only thing that settles a
        # skeleton: a count that is PRESENT and differs from what was sent is the
        # door telling us something true about a batch it did handle.
        disagreeing = [c for c in counts if c is not None and c != sent]
        if disagreeing:
            return _EventsOutcome("discrepancy", {
                "session_id": sid, "sent": sent,
                "received": counts.received, "stored": counts.stored,
                "reason": (
                    f"the door 200'd but reported {EVENTS_RECEIVED_KEY}="
                    f"{counts.received!r} {EVENTS_STORED_KEY}={counts.stored!r} for "
                    f"{sent} sent rows"
                ),
            }, False)
        # BOTH counts must be present and agree. `stored == sent` alone would
        # pass a door that parsed a different batch than it stored, and
        # `received == sent` alone proves only that the schema saw the rows.
        if counts.received == sent and counts.stored == sent:
            return _EventsOutcome("delivered", sid, False)
        # Everything left is PERSISTENCE UNPROVEN and nothing contradicted:
        # neither count, or exactly one and it agrees. `rows`, not `sent`, for
        # the same reason every other retryable entry carries it — nothing was
        # proven to have arrived, so the entry describes the skeleton in hand
        # rather than a delivery.
        reported = ", ".join(
            f"{key}={value!r}" for key, value in
            ((EVENTS_RECEIVED_KEY, counts.received),
             (EVENTS_STORED_KEY, counts.stored)) if value is not None)
        said = (f"reported only {reported}" if reported else
                f"reported neither {EVENTS_RECEIVED_KEY} nor {EVENTS_STORED_KEY}")
        return _EventsOutcome("uncounted", {
            "session_id": sid, "kind": "uncounted", "status": status,
            "rows": rows,
            "reason": f"the door 200'd but {said}, so persistence of {rows} rows "
                      f"is unproven and this is not the door the contract "
                      f"describes; the skeleton stays on the spool for a later "
                      f"drain",
        }, False)

    halt = kind in ("mute_auth", "mute_kill", "mute_role")
    if kind == "dead_letter":
        # 413/422 — the door refused the PAYLOAD, permanently. Terminal: the same
        # bytes would be refused identically on every future drain.
        return _EventsOutcome("undelivered", {
            "session_id": sid, "kind": "rejected", "status": status, "rows": rows,
            "reason": f"the events door answered {status!r} ({kind}) — a "
                      f"permanent rejection of this payload, never retried",
        }, False)
    return _EventsOutcome("pending", {
        "session_id": sid,
        "kind": "refused" if halt else "unreachable",
        "status": status, "rows": rows,
        "reason": f"the events door answered {status!r} ({kind}); the skeleton "
                  f"stays on the spool for a later drain",
    }, halt)


def _events_revoked_entry(sid, rows):
    """M3: the terminal record for a skeleton discarded because the decision was
    EXPLICITLY reversed. Read from local config, so there is no status and no door call
    — and terminal, so the spool row stops being retained for it and the bytes
    are reclaimed.

    The reason names the trigger EXACTLY (`"event_skeleton": false`) rather than
    "the opt-in is off", which was also true of a config nobody could read. This
    entry is the durable evidence that data was destroyed; it has to say which
    act destroyed it."""
    return {
        "session_id": sid, "kind": "revoked", "status": None, "rows": rows,
        "reason": "explicitly revoked in the repo config (\"event_skeleton\": "
                  "false), "
                  "so this already-captured skeleton was discarded locally and "
                  "never sent",
    }


def _events_consent_unknown_entry(sid, rows, scopes=()):
    """The NON-terminal record for a skeleton whose enablement state could not
    be determined — absent, unreadable, malformed or wrong-typed config.

    RETRYABLE ON PURPOSE, and that is the whole fix: it leaves the session
    unresolved, so `_retained_rows` keeps the spool row and a later drain — once
    the config parses again — delivers it. Fail-closed on SENDING (nothing goes
    out while the question is open), fail-safe on DESTRUCTION (nothing is
    deleted on a read error)."""
    where = (" (could not read: " + "; ".join(scopes) + ")") if scopes else ""
    return {
        "session_id": sid, "kind": "consent_unknown", "status": None,
        "rows": rows,
        "reason": "the event-skeleton decision could not be determined" + where
                  + ", so this skeleton was neither sent nor discarded; it "
                    "stays on the spool until an explicit true or false says "
                    "what to do with it",
    }


def _events_orphan_entry(sid, rows, reason_detail):
    """The terminal record for a skeleton whose SESSION row was dead-lettered.

    A skeleton annotates a session record; that record does not exist and never
    will (a dead-letter is never retried), so sending the skeleton would store
    rows nothing can be joined to. Resolved terminally rather than left pending,
    which also keeps the spool from retaining a row forever for a skeleton that
    is by construction unsendable."""
    return {
        "session_id": sid, "kind": "orphan", "status": None, "rows": rows,
        "reason": f"the SESSION row was dead-lettered ({reason_detail}), so "
                  f"there is no session record for this skeleton to annotate; "
                  f"it was discarded without being sent",
    }


# --------------------------------------------------------------------------- #
# PC-A2 status -> action classification (the project-context server contract).
# --------------------------------------------------------------------------- #

def _apply_mute(state, now, reason, duration, origin=None, identity=None):
    """Write one mute into `state` and hand back its reason.

    Three branches of `drain` set a mute and differ only in which
    reason/duration/hint triple they use; the two-line dict build was the part
    copied each time, and the part a fourth reason would copy wrong."""
    state["mute"] = {"until": (now + duration).isoformat(), "reason": reason, "origin": origin, "identity": identity}
    return reason


_DOCTOR_HINT_ENDPOINT = (
    "The insights endpoint returned an unrecognized 403. Check the configured "
    "endpoint and authentication; tenant capture status is unknown. Delivery will retry.")


def _classify(status, body=None):
    """Only a recognized refusal can establish a tenant or role mute."""
    if status == 200:
        return "ack"
    if status in (413, 422):
        return "dead_letter"
    if status == 401:
        return "mute_auth"
    if status == 403:
        if _is_role_denial(body):
            return "mute_role"
        detail = body.get("detail") if isinstance(body, dict) else body
        return "mute_kill" if detail == "ambient_disabled" else "retry"
    return "retry"  # 429/503 and any other/unknown status -> retry, never ack


def _is_role_denial(body):
    """Whether a 403 body is the door's role refusal rather than its kill switch.

    FastAPI serialises `HTTPException(403, detail=...)` as `{"detail": ...}`.
    The kill switch's detail is the bare key `ambient_disabled`; a role refusal's
    is prose the door's denial helper prefixes with `Access denied`. Unknown
    bodies remain retryable endpoint failures, without a tenant-policy claim."""
    if not isinstance(body, dict):
        return False
    detail = body.get("detail")
    return isinstance(detail, str) and detail.startswith(_ACCESS_DENIED_PREFIX)


def _drain_result(sent, acked, dead_lettered, retried, muted, mute_reason, doctor_hint, pending):
    return {
        "sent": sent,
        "acked": acked,
        "dead_lettered": dead_lettered,
        "retried": retried,
        "muted": bool(muted),
        "mute_reason": mute_reason,
        "doctor_hint": doctor_hint,
        "pending": pending,
    }


def _read_locked_spool_rows(fh):
    """Parse the CURRENT content of an already-open, already-locked spool file
    handle, in file order — the same degrade-graceful discipline as
    `ambient_digest._read_jsonl` (blank line skipped, unparseable line
    skipped) but reading from `fh` directly, since compaction re-reads under
    its own flock rather than opening a second, independent handle on the
    path."""
    fh.seek(0)
    rows = []
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return rows


def _compact_spool(spool_path, rows, delivered, dead_letter_ids, cap,
                   events_resolved=frozenset()):
    """Durable caps — the A1b-F2 invariant. `rows` (the caller's earlier spool
    snapshot) decides only WHETHER compaction is worth attempting; when its
    row count exceeds `cap`, this rewrites the spool keeping ONLY RETAINED
    rows — a still-pending row is NEVER dropped, even if that leaves the spool
    over cap. Returns a backlog doctor-hint when the distinct retained set alone
    still exceeds `cap` (nothing left to reclaim), else None; a no-op returning
    None while under cap.

    T2-C3: "retained" is `_retained_rows`, not `_pending_rows` — a row is kept
    while its activity row is pending OR its event skeleton is unresolved. That
    second clause IS the events retry lane: it is what leaves the skeleton's bytes
    on disk for a later drain instead of reclaiming them the moment the session
    acked. Nothing is dropped that was not dropped before; the set kept only grew.

    THE DISK CONSEQUENCE, stated rather than discovered. A retained skeleton is
    the biggest thing on a spool row: measured 2026-07-26 over 260 real sessions,
    p50 92,734 wire bytes and a maximum of 1,375,632. So an events door that stays
    down (404 because it is not deployed on that server yet is the realistic case)
    holds roughly one such row per session, indefinitely, and the backlog hint
    fires every drain naming the events share. That is the SAME degradation this
    module already accepts for a down session door — "the spool will keep growing
    (never dropping a pending rollup) until it drains" — and it is preferred here
    for the same reason: an attempt budget would put the loss back, silently, for
    the one door whose expected failure is "not deployed yet".

    The rewrite itself is IN-PLACE under a blocking `fcntl.flock` on the
    spool's own fd (guarded by `if _fcntl is not None`, degrading to a
    best-effort, non-exclusive rewrite on a non-POSIX host exactly like
    `_insights_session._spool_append`) — never mkstemp+`os.replace`, which
    would swap the spool's inode out from under a concurrent
    `_spool_append` (that function holds a blocking flock on the spool's OWN
    fd, so a rename-based rewrite shares no lock with it and a concurrent
    append lands on the dangling old inode and is silently lost). Coordinating
    on the SAME inode means this also RE-READS the spool under the lock — so
    any append since `rows` was snapshotted is folded in — before truncating
    and rewriting, rather than trusting the stale in-memory `rows` passed in."""
    if len(rows) <= cap:
        return None
    try:
        fh = open(spool_path, "r+", encoding="utf-8")
    except OSError:
        return None
    locked = False
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)  # blocking
            locked = True
        current_rows = _read_locked_spool_rows(fh)
        retained_pairs = list(_retained_rows(current_rows, delivered,
                                             dead_letter_ids, events_resolved))
        fh.seek(0)
        fh.truncate()
        for _, row in retained_pairs:
            fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    finally:
        try:
            if locked and _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()

    distinct_retained = {sid for sid, _ in retained_pairs}
    if len(distinct_retained) > cap:
        # How many are held ONLY by an unresolved skeleton — their rollup is
        # already delivered/dead-lettered. Computed from the SAME `retained_pairs`
        # the rewrite used (not a second walk of the spool) so the number in the
        # hint is the one on disk.
        events_held = len({
            sid for sid, _ in retained_pairs
            if sid in delivered or sid in dead_letter_ids
        })
        return _backlog_doctor_hint(len(distinct_retained), cap, events_held)
    return None


# --------------------------------------------------------------------------- #
# Tenant-wide drain lock. Two concurrent `drain()` calls on ONE tenancy must
# never both read pending state and both send — this serializes the WHOLE
# drain body per tenancy, not per session, so a would-be double-send is
# caught before either drain even loads state. Distinct from the spool flock
# in `_compact_spool` above: that one coordinates compaction against a
# concurrent `_spool_append`; this one coordinates drain against drain.
# --------------------------------------------------------------------------- #

def _drain_lock_path(tenancy):
    return os.path.join(_insights_session.data_dir(), "insights", "locks", tenancy + ".outbox.lock")


@contextlib.contextmanager
def _tenant_drain_lock(tenancy):
    """Non-blocking, tenant-wide drain lock — mirrors A1b's per-session digest
    lock (`_insights_session._lock_path` / `_digest_one_session`'s
    `LOCK_EX | LOCK_NB`) but scoped to a whole `drain()` call rather than one
    session: BUSY (another drain for this tenancy already holds it) means
    that other drain owns delivery this round, so the caller must skip —
    never wait, never reap.

    Yields True if this call holds the lock (proceed), False if it lost the
    race (the caller must return immediately, without ever loading state).
    Degrades to best-effort (always True, no real mutual exclusion) on a
    non-POSIX host or if the lock file itself cannot be opened — the same
    fail-open discipline every other lock in this codebase follows, so
    locking being unavailable never blocks delivery outright."""
    lock_dir = os.path.join(_insights_session.data_dir(), "insights", "locks")
    _insights_session._ensure_private_dir(lock_dir)
    lock_path = _drain_lock_path(tenancy)
    _insights_session._ensure_private_file(lock_path)
    try:
        lock_fh = open(lock_path, "a+")
    except OSError:
        lock_fh = None
    acquired = False
    try:
        if lock_fh is not None and _fcntl is not None:
            try:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False  # BUSY -> another drain holds it; skip, never reap
        else:
            acquired = True  # no real mutual exclusion possible; proceed best-effort
        yield acquired
    finally:
        if lock_fh is not None:
            try:
                if acquired and _fcntl is not None:
                    _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                lock_fh.close()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# drain() — the whole state machine.
# --------------------------------------------------------------------------- #

def drain(tenancy, transport, *, now=None, cwd=None, cap=_DEFAULT_CAP,
          events_transport=None, events_cap=_EVENTS_MAX_BODY_BYTES,
          events_consent=_insights_session.EVENTS_CONSENT_INDETERMINATE,
          events_consent_scopes=(), events_consent_for=None,
          consent_classes_for=None, delivery_origin=None, policy_verified=False,
          delivery_identity=None):
    """Deliver every PENDING (not yet delivered, not yet dead-lettered)
    session in `tenancy`'s spool to `transport`, exactly once per
    `session_id`, applying the PC-A2 status contract and persisting the
    result to the per-tenant outbox state file.

    `now` is the injected clock (defaults to the real UTC now) — every time
    computation in this function uses it, never `datetime.now()` directly, so
    the whole state machine is deterministic under test. `cwd` is accepted
    for interface symmetry with `_insights_session.resolve_tenancy(cwd)`
    (a production caller resolves tenancy from cwd once, upstream) but is
    deliberately UNUSED here — `tenancy` is always the authoritative,
    pre-resolved opaque id, so this function can never leak `cwd`'s raw value
    onto the wire or into persisted state by construction, and two different
    cwds that resolve to the same tenancy share exactly one state file.

    The WHOLE body runs under `_tenant_drain_lock(tenancy)` (fix 4): a second
    concurrent `drain()` call for the same tenancy that loses the race
    returns immediately, before ever loading state — never a double-send.

    PL-A2a additionally: (a) an UNSENDABLE row (legacy, held, or incomplete
    — see `_unsendable_reason`) is resolved LOCALLY, with zero transport
    calls, and never blocks its batch-mates (AC8/F1-F5); (b) every payload
    this drain sends carries the three PC-A2 runtime counters —
    pendingBacklogCount (this drain's own batch size), evictedCount (the
    rows THIS drain's own compaction step will reclaim), and flushLagS (now
    minus THAT row's own ended_at, per session) — computed here and passed
    into `build_wire_payload` as plain parameters, never read by that
    function itself (AC6/AC7). PL-A2a round 3: local classification of what
    can/cannot ever be sent (legacy/held/incomplete detection, and a row
    whose payload cannot even be BUILT) is disk/CPU work independent of
    whether SENDS are currently paced — it runs unconditionally, whether or
    not this drain is muted or backed off (F5); only the send loop itself
    is skipped while paced.

    T2-C3 adds a SECOND, SUBORDINATE request per session: the opt-in event
    skeleton, to its own door via `events_transport` (default None — a drain
    with no events transport behaves byte-for-byte as it did before T2-C3, and
    so does a drain over rows that carry no skeleton, which is every row until
    the opt-in is switched on). Four invariants, in the order they matter:

      1. A skeleton is attempted ONLY for a session whose session-activity row is
         ACKED — in the send loop for a row acked just now, or in the retry pass
         for one acked on an earlier drain. The skeleton annotates a session
         record; sending it for a record that does not exist would create an
         orphan, which is also why a skeleton whose session row was DEAD-LETTERED
         is resolved as `orphan` locally and never sent.
      2. Nothing about the skeleton can un-ack or dead-letter the session row,
         and that is structural rather than careful: `_attempt_events` returns a
         disposition and this function writes it under `state["events"]` only.
         `state["delivered"]`/`state["dead_letter"]`/`state["mute"]`/
         `state["backoff"]` are never written from an events outcome — a 401/403
         from the events door halts further SKELETONS for this drain and touches
         no mute window, because muting the primary lane over the secondary,
         opt-in one trades data that is always wanted for data that was merely
         allowed.
      3. The ack is NEVER conditional on the door's reported counts. A REPORTED
         count that MISMATCHES is recorded as a discrepancy and RESOLVED, never
         retried: the server declares its counts diagnostic and legitimately
         returns a short `stored` for a duplicate-`seq` collapse, so retrying on
         one would loop forever against a healthy store. A 200 that did not PROVE
         persistence is a different animal and is RETAINED AND RETRIED
         (`uncounted`) — a door that did not count is not exercising the collapse
         this invariant licenses, it is a door that does not speak this contract.
         Until 2026-07-27 the two were the same branch, so a version-skewed or
         proxied door silently destroyed every skeleton it touched — and the
         first fix drew the line at BOTH counts absent, so a door reporting only
         one destroyed them just the same. See `_EVENTS_RETRYABLE_KINDS` for the
         boundary and for why a PARTIAL count is on the retried side.
      4. A SKELETON IS NOT RECLAIMED UNTIL IT IS RESOLVED. `_compact_spool` keeps
         a row while its activity row is pending OR its skeleton is unresolved, so
         401/403/404/429/503 are recoverable across drains instead of terminal.
         Before this, one refusal halted the drain, the remaining sessions acked
         anyway, and their skeletons were reclaimed with no record at all.

    `events_consent` (M3, widened to three states 2026-07-27) is the RESOLVED
    state from `_insights_session.event_skeleton_consent`, one of the three
    `EVENTS_CONSENT_*` constants that module declares — the COMPANY's decision,
    read from the committable repo config and from nowhere else (that function's
    docstring says why the constants keep the older word internally).
    `events_consent_scopes` names the configs that could not be read, for the
    doctor hint only.

    IT IS THREE STATES BECAUSE FAIL-CLOSED IS RIGHT FOR CAPTURE AND WRONG FOR
    DESTRUCTION, and the two-state version of this parameter was a live
    data-loss bug:

      * GRANTED — skeletons are attempted. The only state that SENDS.
      * REVOKED — an EXPLICIT `"event_skeleton": false`. The only state that
        DISCARDS: every already-captured skeleton is resolved terminally, so
        `_compact_spool` reclaims its bytes. Terminal is the point: an
        explicit reversal has to mean the already-captured rows go too, or
        "turned off" would be true only of what had not happened yet.
      * INDETERMINATE — absent, unreadable, malformed or wrong-typed config, or
        an unresolvable repo. NEITHER sent NOR discarded: the skeleton is left
        unresolved so the row is retained, and a doctor hint names the scope
        that could not be read. This is the DEFAULT, so a caller who passes
        nothing sends nothing and destroys nothing.

    The predecessor `events_enabled=False` collapsed the last two, and `False`
    was also what `event_skeleton_enabled` returned for a config it could not
    parse — so a half-written JSON file permanently destroyed skeletons captured
    while it was genuinely in force. "I cannot read the company's decision" is
    not "the company reversed it".

    `events_consent_for` (OPEN-1 F4, delivery half — 2026-07-30) is a
    `session_id -> (state, unreadable_scopes)` callable, and when given it is the
    ONLY authority: `events_consent`/`events_consent_scopes` are then never read.
    THE SPOOL IS PER-TENANCY AND A TENANCY IS THE GIT COMMON DIR, so every linked
    worktree of one repo shares this spool while having its own toplevel and its
    own `.fairmind-insights.json`. One state for the whole spool therefore applied
    one checkout's decision to another checkout's session — and because REVOKED is
    terminal, the direction that destroyed was reachable from a single `cmd_sweep`:
    the sweep captured a granting worktree's skeleton and the drain discarded it on
    a sibling's explicit `false`, recording the granting repo's own config as the
    revoker. See `_insights_session.run_drain`, which resolves each session against
    its OWN recorded checkout.

    The scalar remains for callers that genuinely have ONE answer for every row,
    which is why it is still the documented default rather than being replaced: a
    caller with one answer must not be made to write a closure to say so. Both
    forms flow through ONE code path below — a missing `events_consent_for` is
    turned into a resolver that returns the scalar — so there is no second
    consent-handling branch to keep in step, which is the exact way capture and
    delivery drifted apart in the first place.

    `consent_classes_for` (JC5) is the LIVE half of the class stamp: a
    `session_id -> list[str] | None` callable, resolved per session against that
    session's OWN recorded checkout for the same per-worktree reason
    `events_consent_for` is. It is threaded into both payload builders and does
    exactly two things — narrow `consent.classesApplied`, and empty the class-C
    fields of the activity payload. It NEVER decides whether a row is sent: a
    withheld class ships a bare envelope, not a dropped row, so a narrowing config
    can never look like a delivery failure. DEFAULT `None`, under which nothing is
    narrowed and every payload is byte-identical to today's apart from the
    `consent` object itself — which is what makes this change safe for the callers
    (tests, `harness` scripts) that pass nothing.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if consent_classes_for is None:
        # Same idiom as the events resolver below: the absent callable becomes a
        # constant one, so there is exactly ONE code path from here down. `None`
        # is the "no live resolution" answer, not an empty grant — see
        # `_consent_object`.
        def consent_classes_for(_session_id):
            return None
    if events_consent_for is None:
        # ONE code path from here down: the scalar becomes the constant resolver.
        # Written as a default-arg capture rather than a closure over the
        # parameters so the values it answers with are the ones passed IN, immune
        # to any later rebinding of either name in this long function.
        def events_consent_for(_session_id, _state=events_consent,
                               _scopes=tuple(events_consent_scopes)):
            return _state, _scopes

    with _tenant_drain_lock(tenancy) as acquired:
        if not acquired:
            # Another drain for this tenancy already holds the lock and owns
            # delivery this round — mirror A1b's per-session BUSY skip:
            # never reap, never double-send, zero transport calls.
            return _drain_result(0, 0, 0, 0, False, None, None, 0)

        state = _load_state(tenancy)

        # 1. Mute status — a BOOLEAN only, computed but not acted on until
        # the skip-sends decision below (PL-A2a round 3, F5): being muted no
        # longer short-circuits BEFORE the spool is even read, so local
        # unsendable-row classification always runs regardless of whether
        # sends are currently paced by an active mute window.
        mute = state["mute"]
        # A pacing refusal is scoped to the endpoint that issued it. Legacy
        # records without identity provenance retain their expiry. A matching
        # identity and fresh policy response license retrying a changed origin.
        # Consent and revocation records remain untouched.
        if (transport is not None and delivery_origin and policy_verified
                and delivery_identity and mute.get("identity") == delivery_identity
                and mute.get("origin") != delivery_origin):
            state["mute"] = mute = {"until": None, "reason": None, "origin": delivery_origin, "identity": delivery_identity}
        already_muted, mute_until = _mute_active(mute, now)

        # 2. No endpoint configured -> byte-for-byte no-op (spool AND state
        # untouched), regardless of mute state. This still reports
        # `already_muted`/its reason in the result — identical to what the
        # OLD, separate mute early-return computed for this same
        # (muted, transport=None) combination (both branches used
        # `_pending_session_ids`  and returned all-zero counts), so folding
        # the two checks together changes no externally observable result.
        if transport is None:
            pending = len(_pending_session_ids(tenancy, state))
            muted_reason = mute.get("reason") if already_muted else None
            return _drain_result(0, 0, 0, 0, already_muted, muted_reason, None, pending)

        spool_path = _insights_session._spool_path(tenancy)
        rows = _read_spool_rows(spool_path)

        delivered = set(state["delivered"])
        dead_letter_entries = list(state["dead_letter"])
        dead_letter_ids = {e["session_id"] for e in dead_letter_entries}

        # PL-A2a evictedCount (AC7): `drain()` today compacts the spool AFTER
        # the sends (step 6, unchanged below — see the module docstring's
        # save-before-compact crash-safety invariant), but evictedCount must
        # be IN the payloads the send loop builds. Sends only ever ADD
        # terminal rows (an ack, or a legacy row's local dead-letter below) —
        # they never turn a terminal row back into a pending one — so the set
        # of rows compaction will reclaim is already computable HERE, from
        # the rows already terminal (delivered/dead-lettered) as of THIS
        # drain's START, before either the legacy split or the send loop run.
        # This is a safe, deliberately conservative count: it never includes
        # a row that becomes terminal DURING this same drain (that row is
        # reclaimed on a LATER drain's compaction instead), so it can never
        # overstate what THIS drain's compaction step is about to do.
        # Derived from `_retained_rows` — the SAME predicate compaction itself
        # uses — rather than from a second, hand-written "terminal" predicate.
        # Compaction keeps exactly the retained rows and drops everything else, so
        # what it reclaims is precisely `len(rows) - len(retained)`. A transcribed
        # predicate had already drifted from it: it counted only rows whose
        # session_id is terminal, while compaction ALSO drops a malformed row (no
        # dict, or no non-empty str session_id) that the predicate skips — so the
        # counter reported to the server undercounted exactly when the spool held a
        # bad line. Sends can only ADD terminal rows, so measuring at drain start
        # is a lower bound the payloads can carry before any send happens.
        #
        # T2-C3: this MUST be the retained predicate, not `_pending_rows`. Once
        # compaction keeps events-unresolved rows, `len(rows) - len(pending)` no
        # longer describes what it will reclaim — it OVERSTATES it, and this number
        # goes on the wire as `evictedCount`, so the server would be told rows were
        # reclaimed that are still on disk. `pending_backlog_count` below stays
        # activity-only on purpose: the two are different facts (what compaction
        # will free vs how many rollups this drain can actually attempt), and only
        # this one has anything to do with the skeleton.
        events_resolved = set(state["events"]["resolved"])
        pending_pairs = list(_pending_rows(rows, delivered, dead_letter_ids))
        retained_at_start = list(_retained_rows(rows, delivered, dead_letter_ids,
                                                events_resolved))
        evicted_count = (len(rows) - len(retained_at_start)) if len(rows) > cap else 0

        # 3. Group by session_id; a same-session duplicate row (A1b's
        # at-least-once spool duplicate) collapses to the LATEST SENDABLE
        # row for that session (PL-A2a round 3, F2/F2b) — never merely the
        # latest physical line irrespective of sendability. Round 2's
        # unconditional last-write-wins collapse could pick a CORRUPT
        # duplicate over an earlier, perfectly good row for the SAME
        # session (A1b's at-least-once re-spool can append more than one
        # physical line per session_id): the good row was never even
        # considered, and the session was dead-lettered outright with a
        # doctor_hint telling the operator it was "safe to ignore". Single
        # pass, file order: a SENDABLE row always overwrites whatever was
        # there (so the LATEST sendable duplicate wins, never merely the
        # first); an UNSENDABLE row only overwrites when the current holder
        # for that sid is itself still unsendable (`sid not in latest_row or
        # sid in unsendable`) — so a later corrupt duplicate can never evict
        # an already-established sendable candidate, but among a run of
        # unsendable-only duplicates the reason still tracks the latest one.
        # A session is classified unsendable only when EVERY physical
        # duplicate for it is unsendable.
        latest_row = {}
        unsendable = {}  # sid -> (kind, message), see _unsendable_reason
        for sid, row in pending_pairs:
            reason = _unsendable_reason(row)
            if reason is None:
                latest_row[sid] = row
                unsendable.pop(sid, None)
            elif sid not in latest_row or sid in unsendable:
                latest_row[sid] = row
                unsendable[sid] = reason
        sendable_sids = [sid for sid in latest_row if sid not in unsendable]

        # PL-A2a AC7/round-2 D2: pendingBacklogCount is this drain's own
        # SENDABLE batch size — one constant value every payload sent THIS
        # drain carries, not a running "remaining after this send" count that
        # would differ from send to send within the same drain, and never
        # `len(latest_row)` pre-split (which would also count rows this SAME
        # drain permanently, locally discards as unsendable — D2's exact
        # defect: those rows can never be retried, so counting them as
        # "pending" overstates what this drain can actually attempt/retain).
        pending_backlog_count = len(sendable_sids)

        backoff = dict(state["backoff"])
        sent = acked = dead_lettered = retried = 0
        muted_now = False
        mute_reason_now = None
        doctor_hint = None
        # PL-A2a round-2 D5 (widened round 3, F1): each locally-derived hint
        # is captured ONCE into its OWN variable — never into `doctor_hint`
        # itself — so a LATER, unrelated assignment in this same drain (a
        # same-drain 401/403 mute, or the end-of-drain backlog hint) can
        # never silently clobber it; see the final combine step below.
        unsendable_hint = None
        unbuildable_hint = None
        had_retry = had_ack = False
        # T2-C3. Accumulated into the state file's own `events` compartment
        # BEFORE `_save_state` runs — never only into the returned result dict,
        # which `_insights_session.run_drain` discards outright
        # (`_insights_session.py`, `run_drain`: it calls `ambient_outbox.drain`
        # and ignores the return value), so a disposition living only there
        # evaporates the instant the detached sweep process exits.
        events_state = state["events"]
        events_delivered = list(events_state["delivered"])
        events_new_undelivered = []
        events_new_discrepancy = []
        events_new_pending = []
        # Kept OUT of `events_new_pending` although they land in the same durable
        # `pending` list: they share a lane but not a diagnosis, and
        # `_events_pending_doctor_hint` says "investigate the events door", which
        # is exactly wrong for a question about a local config file and exactly
        # wrong for a door that just answered 200.
        events_new_consent_unknown = []
        events_new_uncounted = []
        # JC5 — kept out of both lists above for the same reason they are kept out
        # of each other: it shares the retained lane and not the diagnosis. Its
        # hint is the only one that says "no change to any config will help".
        events_new_class_ungranted = []
        events_halted = False
        # Sessions this drain already put through the events door, so the retry
        # pass below cannot attempt the same skeleton twice in one drain (a
        # `pending` outcome in the send loop leaves the session unresolved, which
        # is exactly what the retry pass looks for).
        events_attempted = set()

        def _events_record(outcome):
            """Fold ONE `_attempt_events` disposition into this drain's
            accumulators, and — for the four RESOLVED dispositions — into
            `events_resolved`, which is what stops the spool row being retained.

            The single place that mapping lives. Split across the three call sites
            (send loop, orphan pass, retry pass) it would be three chances to
            forget that a `discrepancy` resolves and a `pending` does not, and the
            symptom of forgetting either way is silent: a resolved skeleton
            re-sent forever, or a pending one reclaimed and lost."""
            if outcome.disposition == "delivered":
                events_delivered.append(outcome.entry)
                events_resolved.add(outcome.entry)
            elif outcome.disposition == "undelivered":
                events_new_undelivered.append(outcome.entry)
                events_resolved.add(outcome.entry["session_id"])
            elif outcome.disposition == "discrepancy":
                events_new_discrepancy.append(outcome.entry)
                events_resolved.add(outcome.entry["session_id"])
            elif outcome.disposition == "pending":
                events_new_pending.append(outcome.entry)
            elif outcome.disposition == "consent_unknown":
                # NOT resolved, deliberately: leaving the session unresolved is
                # what makes `_retained_rows` keep the spool row, which is the
                # entire fix. Its own accumulator so its own hint can fire.
                events_new_consent_unknown.append(outcome.entry)
            elif outcome.disposition == "uncounted":
                # Also NOT resolved, and for the identical reason: a 200 that
                # proves nothing must leave the skeleton on disk. It resolved
                # TERMINALLY as a `discrepancy` until 2026-07-27, which reclaimed
                # every skeleton a rolled-back or proxied door touched.
                events_new_uncounted.append(outcome.entry)
            elif outcome.disposition == _EVENTS_CLASS_UNGRANTED_KIND:
                # NOT resolved either — a frozen stamp is a label, never a switch,
                # so it may refuse a send and may never authorize a destruction.
                # Resolving here would reclaim the bytes on the strength of a
                # class list, which is the second revocation path three separate
                # docstrings in this codebase forbid.
                events_new_class_ungranted.append(outcome.entry)
            return outcome

        # T2-C3 / M3 — THE CONSENT PASS, and it runs FIRST, unconditionally,
        # before anything can send. Local disk/CPU work with no transport call, so
        # it is not gated by pacing, exactly like the unsendable-row
        # classification below it.
        #
        # WHY IT EXISTS AT ALL: the projection gate lives in `run_sweep`, so
        # turning the opt-in off stops NEW skeletons being built — it does nothing
        # about the ones already sitting on spool rows, which kept shipping.
        # Executed 2026-07-26 against a spool digested while the flag was on:
        # with the flag off, `events door calls AFTER it was turned off:
        # ['sess-old']`. (Measured while two config scopes still existed; only
        # the repo one is read since 2026-07-27, which does not change what this
        # branch does with an explicit false.)
        #
        # WHY IT IS TWO BRANCHES AND NOT ONE `if not enabled`, which is the shape
        # it had until 2026-07-27: the second branch used to fall into the first,
        # so a config that could not be PARSED discarded — terminally, bytes
        # reclaimed — every skeleton captured while it was genuinely in
        # force. Discarding is now reachable ONLY from an explicit
        # `"event_skeleton": false`. Everything else waits.
        #
        # AND IT IS DECIDED PER SESSION (OPEN-1 F4, delivery half). The branch
        # structure is unchanged; what moved is that the question is asked INSIDE
        # the loop, of `events_consent_for(sid)`, because one spool serves every
        # linked worktree of a repo and their configs can disagree. `unknown_scopes`
        # accumulates only what was actually collected, so the aggregate hint can
        # never name a config belonging to a different checkout than the sessions it
        # is reporting on.
        #
        # JC5 (2026-08-14) — IT NOW ASKS THE FROZEN GRANT TOO, and that is the
        # events half of the same widening fix `build_wire_payload` carries. The
        # resolver `run_drain` hands us ANDs the LIVE class-C state with the
        # `event_skeleton` switch and never looks at the row's own stamp, so a repo
        # that granted class C AFTER a session was collected had that session's
        # skeleton sent under a `consent.classesApplied` that excludes C — a grant
        # applied backwards, on the lane where "withholding the class" cannot mean
        # emptying a field because the batch IS the class. The question asked here
        # is the SAME one the activity payload asks — `"C" in
        # _consent_object(...)["classesApplied"]` — so one predicate decides both
        # lanes instead of two that can drift.
        #
        # `events_class_blocked` is the durable half of that answer WITHIN this
        # drain: the consent pass computes it once per candidate and `may_send`
        # reads it, exactly as the INDETERMINATE branch leaves a session unresolved
        # and relies on `may_send` to keep it away from the door.
        unknown_scopes = set()
        events_class_blocked = set()
        for sid, row in _events_candidate_rows(rows, events_resolved).items():
            # `consent_state`, never `state`: that name is the outbox STATE dict
            # this function persists at the end, and shadowing it here silently
            # replaced it with a string — every send still happened and NOTHING was
            # written, which `run_drain`'s fail-open `except` then swallowed whole.
            consent_state, scopes = events_consent_for(sid)
            if consent_state == _insights_session.EVENTS_CONSENT_REVOKED:
                _events_record(_EventsOutcome(
                    "undelivered", _events_revoked_entry(sid, len(row["events"])),
                    False))
            elif consent_state != _insights_session.EVENTS_CONSENT_GRANTED:
                unknown_scopes.update(scopes or ())
                _events_record(_EventsOutcome(
                    "consent_unknown",
                    _events_consent_unknown_entry(sid, len(row["events"]), scopes),
                    False))
            else:
                # GRANTED by the live switch AND by live class C — now the frozen
                # half. Ordered LAST on purpose: an explicit revocation still
                # discards first (a decision), and an undeterminable state is still
                # reported as the open question it is; only a row the live side
                # would have SENT can be refused by its own collection grant.
                applied = _consent_object(row, consent_classes_for(sid))["classesApplied"]
                if "C" not in applied:
                    events_class_blocked.add(sid)
                    _events_record(_EventsOutcome(
                        _EVENTS_CLASS_UNGRANTED_KIND,
                        _events_class_ungranted_entry(sid, len(row["events"]),
                                                      applied),
                        False))

        # The SEND authority, read by both send sites. Before the third state
        # existed, the switched-off pass resolved every candidate and the two sites
        # could rely on "already in `events_resolved`" alone — that reasoning is
        # gone: an INDETERMINATE skeleton is deliberately left unresolved, so
        # without this gate it would be sent by the very branch that is supposed to
        # be waiting.
        #
        # OPEN-1 F4: PER SESSION, not one boolean for the drain. This is the site
        # the capture-only fix would have left behind — the consent pass leaves an
        # INDETERMINATE skeleton unresolved on purpose, so this predicate is the
        # ONLY thing standing between it and the door. A launcher-derived boolean
        # here would have sent one worktree's skeleton on another worktree's grant
        # even after the loop above stopped discarding on another worktree's
        # revocation.
        # JC5: and the FROZEN half joins it here rather than at the two send sites,
        # for the reason the per-session fix above already paid for once — a second
        # eligibility test at a send site is a second place the rule can drift from
        # the pass that recorded the diagnostic.
        def may_send(sid):
            return (events_consent_for(sid)[0]
                    == _insights_session.EVENTS_CONSENT_GRANTED
                    and sid not in events_class_blocked)

        # Unsendable rows are resolved LOCALLY and unconditionally — there is
        # nothing to send, so pacing (backoff OR mute — neither of which
        # applies to anything but SENDS) does not gate this; it runs
        # regardless, mirroring how disk maintenance (step 6) already runs
        # regardless. PL-A2a round 3 (F3): only "legacy" (true
        # pre-enrichment) and "incomplete" (current schema, no timestamps)
        # are TERMINAL — dead-lettered here; "held" (unrecognized/absent
        # schema, otherwise well-formed) is left PENDING so a later plugin
        # version that understands its schema can still deliver it.
        if unsendable:
            legacy_sids, held_sids, incomplete_sids = [], [], []
            for sid, (kind, message) in unsendable.items():
                if kind == "held":
                    held_sids.append(sid)
                    continue
                dead_letter_entries.append({"session_id": sid, "status": None, "reason": message})
                dead_letter_ids.add(sid)
                dead_lettered += 1
                (legacy_sids if kind == "legacy" else incomplete_sids).append(sid)
            legacy_sids.sort()
            held_sids.sort()
            incomplete_sids.sort()
            hints = []
            if legacy_sids:
                hints.append(_legacy_row_doctor_hint(legacy_sids))
            if held_sids:
                hints.append(_held_schema_row_doctor_hint(held_sids))
            if incomplete_sids:
                hints.append(_incomplete_row_doctor_hint(incomplete_sids))
            unsendable_hint = " ".join(hints)

        # 4. Backoff/mute ENFORCEMENT (fix 1, widened round 3 F5): either an
        # already-active mute or a still-pacing backoff window skips the
        # SEND loop entirely — zero transport calls — but local disk
        # maintenance (classification above, save state, compact below)
        # still runs regardless, so a paced-off drain still reclaims
        # terminal rows and persists them.
        backoff_until = _loop_ledger._parse_iso(backoff.get("next_attempt_at"))
        backed_off = backoff_until is not None and now < backoff_until
        skip_sends = already_muted or backed_off

        unbuildable_sids = []
        unproven_ack_sids = []  # T2-C4: 200s that proved nothing (see the hint below)
        if not skip_sends:
            for sid in sendable_sids:
                if muted_now:
                    break  # a 401/403 this drain stops any further send (spec step 4)
                row = latest_row[sid]
                # PL-A2a flushLagS (AC7): per-session, from the INJECTED
                # clock `now` minus THIS row's own ended_at — never a single
                # shared constant, never a real clock read. A row that
                # somehow reaches here with an unparseable ended_at (should
                # not happen — unsendable rows were already filtered out
                # above) degrades to 0.0 rather than raising.
                ended_dt = _loop_ledger._parse_iso(row.get("ended_at"))
                flush_lag_s = max(0.0, (now - ended_dt).total_seconds()) if ended_dt is not None else 0.0
                # PL-A2a round 3 (F1): a row whose payload cannot be built
                # (e.g. a hostile/malformed `skills` value —
                # `build_wire_payload`'s own `sorted(skills)` raises
                # TypeError) must NEVER escape this loop as an exception —
                # that would abort `drain()` entirely, before `_save_state`
                # runs, silently losing every ack already obtained earlier
                # in THIS SAME drain and wedging the tenant's outbox on
                # every subsequent drain (the same row would raise again).
                # Handled exactly like any other locally-unsendable row:
                # dead-lettered here with a distinguishing reason, the loop
                # simply continues to the next row.
                try:
                    wire_payload = build_wire_payload(
                        row, tenancy,
                        flush_lag_s=flush_lag_s,
                        pending_backlog_count=pending_backlog_count,
                        evicted_count=evicted_count,
                        granted_classes=consent_classes_for(sid))
                except Exception as exc:
                    dead_letter_entries.append({
                        "session_id": sid, "status": None,
                        "reason": f"payload could not be built: {exc!r}"})
                    dead_letter_ids.add(sid)
                    dead_lettered += 1
                    unbuildable_sids.append(sid)
                    continue
                # PL-A2a round-2 D3: `session_id` travels to `transport` OUT
                # OF BAND — `transport(payload, session_id)` — never merged
                # into the wire payload dict itself. `wire_payload` (from
                # `build_wire_payload`) stays the pure, closed server
                # shape (AC3) all the way to `transport`; the "wire bytes are
                # correct" guarantee is therefore a property of THIS dict,
                # not of any one transport implementation choosing to strip a
                # widened key back off again before serializing.
                try:
                    response = transport(wire_payload, sid)
                except Exception:
                    response = None  # a transport that raises degrades to "retry", never ack
                sent += 1
                status = getattr(response, "status", None) if response is not None else None
                kind = _classify(status, getattr(response, "body", None))
                state["endpoint_refusal"] = status == 403 and kind == "retry"
                # T2-C4: `_classify` answers on the status for every case but
                # one — the body only splits a 403 into kill switch vs. role. A 200 says the request was well-formed; only
                # the door's own success shape says anything was stored. An
                # unproven 200 degrades to "retry" — never to a dead-letter,
                # because nothing about it is permanent, and never to an ack,
                # because that reclaims the only copy of the row.
                # (`getattr(None, "body", None)` is None, so no response guard.)
                if kind == "ack" and not _ack_proves_persistence(
                        getattr(response, "body", None)):
                    kind = "retry"
                    unproven_ack_sids.append(sid)

                if kind == "ack":
                    delivered.add(sid)
                    acked += 1
                    had_ack = True
                    # T2-C3 — a skeleton is sent HERE for a row acked just now,
                    # and it is inside the `ack` branch on purpose: reaching it is
                    # the proof that this session's activity row exists
                    # server-side. `events_halted` short-circuits after the
                    # events door refused (401/403) so one refusal does not
                    # become one failed request per remaining session — and a
                    # halted session is simply left UNRESOLVED, so its row is
                    # retained and the retry pass (this drain's, if the halt
                    # clears, or the next drain's) picks it up.
                    #
                    # `may_send(sid)` is the authority (an INDETERMINATE skeleton
                    # is left UNRESOLVED on purpose, so the `events_resolved`
                    # guard alone would let this branch send it);
                    # `events_resolved` additionally keeps the pass above
                    # authoritative for a skeleton it discarded. Asked of THIS
                    # session, because the sessions on one spool can come from
                    # checkouts whose configs disagree (OPEN-1 F4).
                    if (may_send(sid) and not events_halted
                            and sid not in events_resolved):
                        outcome = _events_record(_attempt_events(
                            row, sid, tenancy, events_transport, events_cap,
                            granted_classes=consent_classes_for(sid)))
                        events_attempted.add(sid)
                        events_halted = events_halted or outcome.halt
                elif kind == "dead_letter":
                    dead_letter_entries.append({"session_id": sid, "status": status})
                    dead_letter_ids.add(sid)
                    dead_lettered += 1
                elif kind == "mute_auth":
                    muted_now = True
                    mute_reason_now = _apply_mute(state, now, _MUTE_401_REASON, _MUTE_401_DURATION, delivery_origin, delivery_identity)
                    doctor_hint = _DOCTOR_HINT_401
                elif kind == "mute_kill":
                    muted_now = True
                    mute_reason_now = _apply_mute(state, now, _MUTE_403_REASON, _MUTE_403_DURATION, delivery_origin, delivery_identity)
                    doctor_hint = _DOCTOR_HINT_403
                elif kind == "mute_role":
                    # Retained, never dead-lettered: the row becomes deliverable
                    # the moment a role is granted, and this drain cannot know
                    # when that is. A mute rather than the retry lane because
                    # the backoff cap is an hour and the wait can be days.
                    muted_now = True
                    mute_reason_now = _apply_mute(state, now, _MUTE_ROLE_REASON, _MUTE_ROLE_DURATION, delivery_origin, delivery_identity)
                    doctor_hint = _DOCTOR_HINT_ROLE
                else:  # retry: 429/503/unknown -> bump backoff, never ack, never drop
                    retried += 1
                    had_retry = True
                    if status == 403:
                        doctor_hint = _DOCTOR_HINT_ENDPOINT
                        break  # Endpoint refusal stops this drain without creating a tenant mute.

            if unbuildable_sids:
                unbuildable_sids.sort()
                unbuildable_hint = _unbuildable_row_doctor_hint(unbuildable_sids)

            # Backoff attempts increment AT MOST ONCE per drain (fix 6), not
            # once per retried session, and a drain that still had a retry
            # never resets backoff even if it also had an ack this round.
            if had_retry:
                attempts = backoff.get("attempts", 0) + 1
                delay = min(_BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), _BACKOFF_MAX_SECONDS)
                backoff = {"attempts": attempts, "next_attempt_at": (now + timedelta(seconds=delay)).isoformat()}
            elif had_ack:
                backoff = {"attempts": 0, "next_attempt_at": None}

        # T2-C3 — THE ORPHAN PASS. A skeleton whose SESSION row is dead-lettered
        # can never be sent (invariant 1: it annotates a record that must already
        # exist, and a dead-letter is never retried), so leaving it unresolved
        # would retain its spool row forever waiting for a send that is forbidden.
        # Resolved terminally, LOCALLY, with no transport call — hence outside the
        # `skip_sends` gate, like every other local resolution in this function.
        for sid, row in _events_candidate_rows(rows, events_resolved).items():
            if sid in dead_letter_ids:
                _events_record(_EventsOutcome("undelivered", _events_orphan_entry(
                    sid, len(row["events"]),
                    "413/422 from the session door, or a locally-unsendable row"),
                    False))

        # T2-C3 — THE RETRY PASS, and the whole point of the retained-row change.
        # Every session whose activity row is ALREADY delivered (this drain or any
        # earlier one) but whose skeleton is still unresolved gets another attempt,
        # from the skeleton still sitting on its retained spool row. This is what
        # turns 401/403/404/429/503 from "silently lost" into "delivered on the
        # next drain".
        #
        # Gated by `skip_sends` AND by `muted_now`, and it takes both: both doors
        # take the SAME bearer, so spending a token already known to be refused is
        # pointless. `skip_sends` covers a mute that was ALREADY active when this
        # drain started; `muted_now` covers one this drain's own send loop just
        # earned from a 401/403 — `skip_sends` is computed before that loop and
        # stays False, so without the second condition this pass would fire a
        # burst of requests with a bearer proven bad seconds earlier. The send loop
        # guards itself with `if muted_now: break`; this is its counterpart.
        # Nothing is lost by waiting — the rows stay retained. `events_attempted`
        # excludes sessions the send loop just tried, so one drain never makes two
        # requests for one skeleton.
        #
        # WHAT IS NOT BOUNDED HERE, stated rather than discovered (the other half
        # of the retention trade-off `_compact_spool` documents in bytes). Only
        # 401/403 is volume-bounded, by `events_halted`, to ONE request per drain.
        # A door answering 404/429/503 is re-attempted once per RETAINED session
        # per drain — N requests every drain for as long as it stays down, with no
        # backoff, because the events lane deliberately never touches
        # `state["backoff"]` (that window paces the session door). 429 is the
        # sharpest case: the events door has its own rate limiter, so N requests
        # earn N 429s and the next drain sends N again. Accepted for now because no
        # disposition changes either way and the alternative — a per-drain attempt
        # cap — trades a bounded request burst for a slower drain of the backlog.
        # A per-lane backoff is the named follow-up, alongside chunking.
        #
        # The PACING half of the gate stays on the OUTER `if` and the CONSENT half
        # moved inside (OPEN-1 F4): pacing is a property of this drain, eligibility
        # is a property of each session. Dropping the outer gate entirely would make
        # a muted or backed-off drain start iterating where it used to
        # short-circuit, which is a pacing change smuggled in behind a consent fix.
        if not skip_sends and not muted_now:
            for sid, row in _events_candidate_rows(rows, events_resolved).items():
                if events_halted:
                    break
                if sid in events_attempted or sid not in delivered:
                    continue
                if not may_send(sid):
                    continue
                outcome = _events_record(_attempt_events(
                    row, sid, tenancy, events_transport, events_cap,
                    granted_classes=consent_classes_for(sid)))
                events_attempted.add(sid)
                events_halted = events_halted or outcome.halt

        state["delivered"] = sorted(delivered)
        state["dead_letter"] = dead_letter_entries
        state["backoff"] = backoff

        # T2-C3: the events dispositions join the SAME atomic write as the
        # session ones. Assigned here, unconditionally, so the "did someone
        # forget to persist it" failure mode cannot depend on which branch of
        # the send loop ran — an events outcome that is accumulated but never
        # assigned is silent, and it is the exact evaporation this compartment
        # exists to prevent. `events_delivered`/`resolved` are sorted for the same
        # reason `delivered` is: a set-like list whose order tracks send order
        # makes every state file diff noise.
        #
        # The three DIAGNOSTIC lists go through `_merge_events_entries` (M2):
        # deduped by session, bounded at `_EVENTS_DIAGNOSTIC_CAP`, and whatever the
        # bound discarded is added to the visible `dropped` tally. `resolved` is
        # NOT bounded and must not be — it is the retention authority, and an id
        # forgotten there resurrects a settled skeleton (see
        # `_EVENTS_DIAGNOSTIC_CAP`).
        #
        # A session that RESOLVED this drain leaves the pending lane in the same
        # step: it was recorded there by an earlier drain's failed attempt, and a
        # stale entry would report a delivered skeleton as still queued.
        events_pending = [e for e in events_state["pending"]
                          if e["session_id"] not in events_resolved]
        dropped = dict(events_state["dropped"])
        merged = {}
        for key, existing, new in (
                ("undelivered", events_state["undelivered"], events_new_undelivered),
                ("discrepancy", events_state["discrepancy"], events_new_discrepancy),
                # The undetermined-state and uncounted entries share the durable
                # `pending` list — same lane (retained, re-attempted later), same
                # dedup-by-session bound — while keeping their own accumulators
                # so their own hint, not the door's, is what an operator reads.
                ("pending", events_pending,
                 events_new_pending + events_new_consent_unknown
                 + events_new_uncounted + events_new_class_ungranted)):
            merged[key], just_dropped = _merge_events_entries(existing, new)
            dropped[key] = dropped.get(key, 0) + just_dropped
        state["events"] = {
            "resolved": sorted(events_resolved),
            "delivered": sorted(set(events_delivered)),
            "undelivered": merged["undelivered"],
            "discrepancy": merged["discrepancy"],
            "pending": merged["pending"],
            "dropped": dropped,
        }

        # 5. State is persisted BEFORE the spool is compacted (fix 2) — a
        # crash between the two must never lose a terminal row's disposition.
        _save_state(tenancy, state)

        # 6. Durable caps / compaction — the A1b-F2 invariant (see
        # _compact_spool): reclaim only rows that are terminal AND whose skeleton
        # is resolved, never a still-pending one. A 401/403 mute this drain already
        # owns doctor_hint, so a backlog hint never clobbers it. Runs even when
        # `backed_off` — disk maintenance is independent of whether sends were
        # paced this round. `events_resolved` is passed the POST-pass set (state was
        # just written from it), so a skeleton resolved this drain is reclaimable
        # this drain and one still pending survives.
        compaction_hint = _compact_spool(spool_path, rows, delivered,
                                         dead_letter_ids, cap, events_resolved)
        if doctor_hint is None:
            doctor_hint = compaction_hint

        # PL-A2a round-2 D5 fix (widened round 3, F1): combine every locally-
        # derived hint (captured, each ONCE, before or during the send loop,
        # into its own variable that nothing else in this function ever
        # writes to) with whatever mute/backlog hint this drain also
        # produced. Round 1's code assigned mute's hint directly into the
        # SAME `doctor_hint` variable the unsendable-row branch had already
        # set, with no `is None` guard — so a same-drain 401/403 silently
        # discarded the only local signal that rows were dropped, despite
        # this function's own prior comment claiming otherwise. Combining at
        # the very end, from variables that are each written in exactly one
        # place, makes the survival guarantee structural rather than a
        # discipline every future branch must remember to preserve.
        # T2-C3's own hints join that same combine, each from a variable
        # written in exactly one place. They are derived from THIS drain's new
        # entries only — re-announcing every historical omission on every drain
        # would make the hint grow without bound and bury the new signal.
        #
        # Every terminal kind now produces a hint, and so does the PENDING lane
        # (M1) — which produced none at all before, so a 404/429/503 that destroyed
        # a skeleton was the one disposition an operator could not see. Grouped by
        # what an operator would DO about it: declined-by-us (a declared bound),
        # refused-permanently (nothing to do, it is gone), discarded-because-
        # it-was-explicitly-revoked, waiting-because-the-config-is-unreadable,
        # waiting-
        # because-the-door-200'd-without-counts, waiting-because-the-row's-own-
        # frozen-grant-does-not-cover-it, and not-yet (investigate the
        # door). The last four are separate hints on purpose even though all
        # four are retained: one asks the operator to fix a local
        # config, one to reconcile two deployments, one to fix a door — and the
        # frozen-grant one asks for NOTHING, because nothing an operator can do
        # resolves it, which is exactly why it must not be worded like the others.
        events_hint = None
        by_kind = collections.defaultdict(list)
        for entry in events_new_undelivered:
            by_kind[entry.get("kind")].append(entry)
        omitted = [e for kind in _EVENTS_OMITTED_KINDS for e in by_kind[kind]]
        # BY SUBTRACTION from the declared tuple, never re-listed: every terminal
        # kind that has no hint of its own lands in the generic one, so adding a
        # seventh kind cannot leave it silent (see `_EVENTS_OWN_HINT_KINDS`).
        terminal = [e for kind in _EVENTS_TERMINAL_KINDS
                    if kind not in _EVENTS_OWN_HINT_KINDS
                    for e in by_kind[kind]]
        hints = []
        if omitted:
            hints.append(_events_omitted_doctor_hint(omitted))
        if terminal:
            hints.append(_events_terminal_doctor_hint(terminal))
        if by_kind[_EVENTS_REVOKED_KIND]:
            hints.append(_events_revoked_doctor_hint(by_kind[_EVENTS_REVOKED_KIND]))
        if events_new_discrepancy:
            hints.append(_events_discrepancy_doctor_hint(events_new_discrepancy))
        if events_new_pending:
            hints.append(_events_pending_doctor_hint(events_new_pending))
        if events_new_consent_unknown:
            # OPEN-1 F4: the scopes ACTUALLY collected above, never the caller's
            # single tuple. With per-session resolution that tuple would be one
            # checkout's config named in a hint about another checkout's sessions —
            # the same misattribution the per-session fix exists to remove. Sorted
            # so a set never makes the hint text order-dependent.
            hints.append(_events_consent_unknown_doctor_hint(
                events_new_consent_unknown, sorted(unknown_scopes)))
        if events_new_uncounted:
            hints.append(_events_uncounted_doctor_hint(events_new_uncounted))
        if events_new_class_ungranted:
            hints.append(_events_class_ungranted_doctor_hint(
                events_new_class_ungranted))
        if events_halted:
            hints.append(_DOCTOR_HINT_EVENTS_HALTED)
        if hints:
            events_hint = " ".join(hints)

        # T2-C4: an unproven 200 is reported ONCE per drain, not once per
        # session — the cause is the endpoint, which is one thing, and a hint
        # repeated per row would bury the other findings under it.
        unproven_hint = _DOCTOR_HINT_UNPROVEN_ACK if unproven_ack_sids else None

        final_hint = " ".join(h for h in (unsendable_hint, unbuildable_hint,
                                          unproven_hint, events_hint,
                                          doctor_hint) if h) or None

        # PL-A2a round 3 (F5): `muted` in the result reflects EITHER an
        # already-active mute this drain honored (sends skipped) OR a NEW
        # mute this drain's own send loop just triggered — never only the
        # latter, which would silently report `muted=False` on a drain that
        # in fact made zero sends because it was already muted.
        final_muted = muted_now or already_muted
        final_mute_reason = mute_reason_now if muted_now else (mute.get("reason") if already_muted else None)

        pending = len([sid for sid in latest_row if sid not in delivered and sid not in dead_letter_ids])
        return _drain_result(sent, acked, dead_lettered, retried, final_muted, final_mute_reason, final_hint, pending)


# --------------------------------------------------------------------------- #
# T2-C3 — THE READER. Without this, `state["events"]` is a write-only record.
#
# `drain`'s return value carries the same information for one drain, and that is
# not enough: `_insights_session.run_drain` calls `drain` and DISCARDS what it
# returns, in a detached, niced background process whose stdout the SessionStart
# hook sends to /dev/null. Anything that lives only in that dict is gone before
# a human could see it. The state file survives; this is how it is read.
# --------------------------------------------------------------------------- #

def _events_entry_detail(entry):
    """`kind=… status=… rows=… bytes=…` for ONE diagnostic entry, omitting every
    field the entry does not carry.

    One renderer for the NOT-DELIVERED and QUEUED-FOR-RETRY lines below, which
    had two copies of it eighteen lines apart. The two copies differed only in
    that the terminal one also rendered `bytes` — and that difference was not a
    rule, it was the incidental fact that only `over_budget` sets `bytes` and
    `over_budget` is terminal. Rendering a field the entry actually carries is
    the rule; a pending entry with no `bytes` key prints exactly what it printed
    before."""
    detail = f"kind={entry.get('kind')!r}"
    for field in ("status", "rows", "bytes"):
        if entry.get(field) is not None:
            detail += f" {field}={entry.get(field)}"
    return detail


def events_status(tenancy):
    """What the event skeleton has CAPTURED for `tenancy` and what became of it,
    as plain data: `{"captured_unresolved": int, "delivered": int,
    "resolved": int, "undelivered": [entry...], "discrepancy": [entry...],
    "pending": [entry...], "dropped": {...}}`.

    Reads the same normalized state `drain` writes (`_load_state` degrades a
    missing/corrupt file to defaults), so a tenancy that has never drained
    reports zeros rather than raising. `delivered`/`resolved` are COUNTS — the
    session ids themselves are an opaque list of no diagnostic value — while the
    three entry lists come through whole, because their `kind`/`reason`/`rows`
    fields are the whole point of having recorded them.

    `pending` and `dropped` are here because the state file grew them and this is
    the only reader: a `pending` skeleton is the one an operator can still act on
    (the door is down, the data is safe, go fix the door), and `dropped` is what
    keeps the bounded diagnostic lists from reading as a complete record.

    `captured_unresolved` READS THE SPOOL, and it is why this is no longer the
    DELIVERY record alone (W-2, fixed 2026-07-30). Every other field here is
    written exclusively by `drain`, i.e. keyed on delivery ATTEMPTS — so a
    skeleton projected and spooled but never drained appeared in NO field, while
    the one-time notice told its reader this verb shows "what has been captured".
    Two states turned that into an all-clear over a record sitting on disk: the
    ORDINARY one, because the notice prints at session start and the sweep that
    captures is spawned one process later; and a PERMANENT one, because `drain`
    returns a byte-for-byte no-op when `transport is None` (a project whose
    Fairmind MCP entry has no resolvable url + Authorization header), so nothing
    is ever written to `state["events"]` for any number of sessions while the
    spool grows. Counted with `_events_unresolved` — the SAME predicate
    compaction uses to decide what to KEEP — rather than a second, hand-written
    "is this captured" test, so this number cannot drift from what is actually
    retained on disk.

    CORRECTION (2026-07-30), recorded BESIDE the sentence it narrows rather than
    replacing it: the PREDICATE is unchanged, but it is no longer applied line by
    line. The count is `len(_events_candidate_rows(...))` — that same predicate
    already COLLAPSED PER SESSION, which is the only reason
    `_events_candidate_rows` exists and is what the send loop itself drives off.
    The spool is at-least-once, so ONE session can hold several skeleton-bearing
    lines; counting lines let `captured_unresolved` exceed the number of sessions
    and disagree in UNIT with `delivered`/`resolved`, which are session-id counts
    — a reader comparing "captured, not yet sent: 2" against "delivered: 1" was
    comparing two different things. Same predicate, same "cannot drift from what
    is retained" property, now in the same unit as its siblings.

    `_read_spool_rows` degrades a missing or corrupt spool to `[]` and never
    raises (it delegates to `ambient_digest._read_jsonl`, which swallows a
    missing file, an OSError and an unparseable line alike), matching
    `_load_state`: this verb is a diagnostic and must never become the thing that
    fails. No try/except is wrapped around it here for the same reason the two
    sibling call sites (`_pending_session_ids`, `drain`) wrap none — a guard
    around a reader that cannot raise reads as evidence that it can."""
    state = _load_state(tenancy)
    events = state["events"]
    events_resolved = set(events["resolved"])
    rows = _read_spool_rows(_insights_session._spool_path(tenancy))
    return {
        "captured_unresolved": len(_events_candidate_rows(rows, events_resolved)),
        "delivered": len(events["delivered"]),
        "resolved": len(events["resolved"]),
        "undelivered": list(events["undelivered"]),
        "discrepancy": list(events["discrepancy"]),
        "pending": list(events["pending"]),
        "dropped": dict(events["dropped"]),
    }


def _mute_active(mute, now):
    """`(active, until)` for one persisted mute — the single spelling of "is
    this mute still live", used by `drain` to decide whether to send and by
    `session_status` to decide whether to report. One predicate, so a boundary
    fix lands in both."""
    until = _loop_ledger._parse_iso((mute or {}).get("until"))
    return (until is not None and now < until), until


def _dead_letter_cause(entry):
    """One dead-letter entry's cause, from what `drain` stored on it."""
    status = entry.get("status")
    reason = entry.get("reason")
    if status is not None:
        return f"the door rejected the payload permanently ({status})"
    return reason or "not deliverable (no reason recorded)"


def session_status(tenancy, now=None):
    """What became of the SESSION rollup door's deliveries for `tenancy`, as
    plain data: `{"mute": {"active": bool, "reason": str|None, "until":
    str|None}, "dead_letter": [entry...], "pending": int}`.

    This is the reader `state["mute"]` and `state["dead_letter"]` never had.
    Both are written by `drain` on every refusal of the session-activity door,
    and until this function existed they were read by nothing: `events_status`
    reads the sibling `state["events"]` compartment only. So a 24h kill-switch
    mute, a role refusal, or a permanently dead-lettered rollup was recorded
    faithfully and surfaced nowhere — a developer whose delivery had been off
    for a week had exactly one way to learn it, opening the state file by hand.
    An unread record is not a diagnostic; it is a private diary.

    `now` is injectable for the same reason `drain`'s is: "is the mute still
    active" is a comparison against a clock, and a test that cannot pin the
    clock cannot pin the answer."""
    state = _load_state(tenancy)
    now = now or datetime.now(timezone.utc)
    mute = state.get("mute") or {}
    active, _until = _mute_active(mute, now)
    # `until`/`reason` are None unless the mute is live, so "is there a mute"
    # is `mute["until"] is not None` — one fact, stored once.
    return {
        "mute": {
            "reason": mute.get("reason") if active else None,
            "until": mute.get("until") if active else None,
        },
        "endpoint_refusal": state.get("endpoint_refusal") is True,
        "dead_letter": list(state.get("dead_letter") or []),
        "pending": len(_pending_session_ids(tenancy, state)),
    }


# The doctor hint that set each mute, keyed on the persisted reason — so the
# status verb, which runs in a later process than the drain that muted, needs
# nothing but the state file to explain itself. The SAME text the drain
# produced, not a second phrasing of it: `doctor_hint` never reaches a person
# through the drain (the sweep runs detached with stdout discarded), so this
# map is where those sentences are finally read, and the one place they are
# maintained.
_DOCTOR_HINT_BY_REASON = {
    _MUTE_401_REASON: _DOCTOR_HINT_401,
    _MUTE_403_REASON: _DOCTOR_HINT_403,
    _MUTE_ROLE_REASON: _DOCTOR_HINT_ROLE,
}


def session_status_report(tenancy, now=None):
    """`session_status` rendered for a human, as lines, or a single all-clear.

    Same contract as `events_status_report` and for the same reason: returned,
    never printed, so the CLI verb owns the channel and a test asserts on the
    string. The all-clear is gated on ALL THREE findings — an active mute, a
    non-empty dead-letter list, and a pending count — for the reason W-2 gave
    the events report: an all-clear printed beside a record that exists is a
    lie the reader will act on."""
    status = session_status(tenancy, now=now)
    lines = ["Ambient session rollup — delivery record for this repo:"]
    mute = status["mute"]
    if mute["until"] is not None:
        why = _DOCTOR_HINT_BY_REASON.get(
            mute["reason"], f"Muted (reason: {mute['reason']!r}).")
        lines.append(f"  DELIVERY PAUSED until {mute['until']}: {why}")
    if status.get("endpoint_refusal"):
        lines.append("  " + _DOCTOR_HINT_ENDPOINT)
    entries = status["dead_letter"]
    if entries:
        # Each entry says WHY it was dead-lettered, and the reasons are not
        # one thing: a 413/422 is the door refusing the payload, while
        # `status: None` with a `reason` is this client failing to BUILD one
        # (`drain` records both shapes). Rendered per entry from what was
        # stored rather than summarised as a single server rejection — a
        # report that blames the door for a local build failure sends the
        # reader to the wrong place.
        lines.append(f"  DEAD-LETTERED {len(entries)} session(s) — not retried, "
                     "rows reclaimed:")
        for e in entries[:5]:
            lines.append(f"    {e.get('session_id')}: {_dead_letter_cause(e)}")
        if len(entries) > 5:
            lines.append(f"    (+{len(entries) - 5} more)")
    if status["pending"]:
        lines.append(f"  pending, not yet delivered: {status['pending']}")
    if len(lines) == 1:
        lines.append("  nothing outstanding: no mute, no dead letters, nothing "
                     "pending.")
    return "\n".join(lines)


def events_status_report(tenancy):
    """`events_status` rendered for a human, one line per finding, or a single
    all-clear line. Returned as a string (never printed here) so the caller owns
    the channel — the CLI verb in `_insights_session` prints it, and a test can
    assert on it without capturing stdout.

    THE CAPTURE LINE IS WHY THE HEADING NO LONGER SAYS "delivery record" (W-2).
    Appending a captured count while still printing "no omissions … recorded"
    beneath it would have fixed the reported SITE and left the PREDICATE: an
    all-clear next to `captured, not yet sent: 7` is still an all-clear over a
    record that exists. So the all-clear is now gated on the captured count as
    well as on the three lists and the truncation tally — it means "nothing is
    outstanding", which is the only reading that survives a reader acting on
    it."""
    status = events_status(tenancy)
    lines = ["Ambient event skeleton (T2-C3) — capture and delivery record for "
             "this repo:",
             f"  captured, not yet sent: {status['captured_unresolved']}",
             f"  delivered (counts agreed): {status['delivered']}",
             f"  resolved (nothing further owed): {status['resolved']}"]
    for entry in status["undelivered"]:
        lines.append(f"  NOT DELIVERED {entry.get('session_id')}: "
                     f"{_events_entry_detail(entry)} — {entry.get('reason')}")
    for entry in status["discrepancy"]:
        lines.append(
            f"  COUNT MISMATCH {entry.get('session_id')}: sent "
            f"{entry.get('sent')}, door reported received="
            f"{entry.get('received')!r} stored={entry.get('stored')!r} — "
            f"{entry.get('reason')}")
    # The retry lane, reported as a delay rather than a loss — because the spool
    # row is retained, which is the whole difference this compartment now carries.
    for entry in status["pending"]:
        lines.append(f"  QUEUED FOR RETRY {entry.get('session_id')}: "
                     f"{_events_entry_detail(entry)} — {entry.get('reason')}")
    # THE TRUNCATION NOTICE IS PART OF THE FINDINGS, not a footnote to them, and
    # it is computed BEFORE the all-clear because it can be the ONLY finding.
    # Until 2026-07-27 the all-clear returned early on three empty lists and
    # never reached this block, so a state carrying
    # `dropped={undelivered: 7, discrepancy: 3, pending: 41}` with all three
    # lists since emptied printed "no omissions … recorded" and nothing else —
    # 51 forgotten entries announced to an operator as an all-clear, in the only
    # view an operator has of stuck skeletons. The lists empty exactly as
    # sessions RESOLVE, so "capped in the past, empty now" is the ordinary end
    # state of a real incident rather than a contrived one.
    dropped = status["dropped"]
    truncated = any(dropped.get(key) for key in _EVENTS_DIAGNOSTIC_LISTS)
    if truncated:
        lines.append(
            "  (diagnostic lists are bounded at "
            f"{_EVENTS_DIAGNOSTIC_CAP} entries each; entries no longer shown "
            f"here: undelivered={dropped.get('undelivered', 0)} "
            f"discrepancy={dropped.get('discrepancy', 0)} "
            f"pending={dropped.get('pending', 0)}. Delivery itself is "
            "unaffected — retention is keyed on the resolved-id set, not on "
            "these lists — but this report is NOT a complete history, so the "
            "lines above are what survives rather than everything that "
            "happened.)")
    # Reachable only when there is genuinely nothing OUTSTANDING: no current
    # entries in any of the three lists, nothing the cap forgot, AND nothing
    # captured that has not been resolved. That last clause is W-2's actual fix —
    # without it the two most common non-delivering states (the sweep not yet
    # spawned; no delivery credential at all, permanently) printed this line over
    # a spool that was growing, in the ONE view the notice tells a person to run.
    if not (status["undelivered"] or status["discrepancy"] or status["pending"]
            or truncated or status["captured_unresolved"]):
        lines.append("  no omissions, rejections, count discrepancies or "
                     "queued retries recorded.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Production transport factory. Never exercised by the gate (stdlib
# urllib only, config-gated by the caller passing a real `endpoint_url`).
# --------------------------------------------------------------------------- #

class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that refuses, so the bearer never leaves the host the
    project config named.

    THIS IS A CREDENTIAL LEAK IN THE STDLIB DEFAULT, and the comment in
    `_insights_session.run_drain` that the events endpoint is derived from the
    activity url "so the bearer cannot travel to a second host" was false
    without it. `urllib.request.HTTPRedirectHandler.redirect_request` follows
    301/302/303 on a POST and rebuilds the request with every header except
    `content-length` and `content-type` — `Authorization` included — so any
    endpoint able to answer 302 could walk the token to a host of its choosing,
    and then answer `{"id": ...}` and settle the row as well. Read out of
    CPython 3.12's own source rather than assumed.

    Raising `HTTPError` gives the transport the 3xx status, which `_classify`
    maps to a retry: the rows are retained and a misconfigured redirecting
    endpoint costs delivery rather than the token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


# ONE module-level opener, and it is the SEAM the suite drives. It holds no
# state and no credential; the reason it is not built per factory call is that a
# closure-local opener is unreachable from a test, and the bounded-response-read
# guarantee (`_MAX_RESPONSE_BYTES`) is pinned by monkeypatching exactly this
# name. A property nothing can reach is a property nothing protects.
_OPENER = urllib.request.build_opener(_RefuseRedirects)


def make_urllib_transport(endpoint_url, jwt_provider):
    """A `transport` callable that POSTs `payload` as JSON to `endpoint_url`
    via stdlib `urllib`. `jwt_provider` is called ONCE PER REQUEST, at call
    time — the token is never cached, stored, or logged by this module. A
    falsy `endpoint_url` ("no endpoint configured yet") returns None, so the
    caller's `drain(tenancy, make_urllib_transport(cfg.get("endpoint"), ...))`
    degrades to the spool-only NO-OP path without a separate feature flag."""
    if not endpoint_url:
        return None

    def _transport(payload, session_id=None):
        # PL-A2a round-2 D3: `drain()` calls EVERY transport — this real
        # urllib one included — as `transport(payload, session_id)`, with
        # `session_id` OUT OF BAND. `payload` is therefore ALREADY
        # `build_wire_payload`'s own pure, closed shape (the one
        # verified live to return 200) — nothing to strip here; the bytes
        # POSTed are `payload` itself, byte-for-byte. `session_id` is accepted
        # for interface symmetry with every other `transport` in this module
        # (FakeTransport included) but not needed to build the request: the
        # response is correlated back to its session by `drain()`'s own call
        # site, not by this closure.
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {jwt_provider()}",
            },
        )
        try:
            with _OPENER.open(request, timeout=10) as resp:
                # Bounded read (fix 5): the ack/error body is just `{"id": ...}`
                # or a short message — never an unbounded read from a
                # hostile/oversized endpoint response.
                raw = resp.read(_MAX_RESPONSE_BYTES)
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read(_MAX_RESPONSE_BYTES)
            status = e.code
        except Exception:
            # DNS/timeout/connection-refused/etc: no HTTP status available.
            # Status 0 classifies as "retry" in `_classify`, never ack.
            return Response(0, None)
        try:
            body_parsed = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            body_parsed = None
        return Response(status, body_parsed)

    return _transport
