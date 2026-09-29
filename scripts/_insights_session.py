#!/usr/bin/env python3
"""PL-A1a — the OPT-scoped ambient telemetry SESSION GATE (first slice of PL-A1).

Two thin bash hooks (`session-start-insights.sh` / `session-end-insights.sh`)
shell out to this module; the gate decision itself (`Decision`, `evaluate_gate`)
lives in `_consent_authority`, which this module re-exports. It is
stdlib-only and unit-testable in isolation, a peer of PL-A0's `_loop_ledger.py`
(whose atomic-append discipline it reuses for the registry).

What A1a lays down (NO digester, NO outbox — those are A1b/A1c):

  * A SessionStart gate that is re-evaluated FRESH every session from LIVE config
    VALUES (PCF-16: never from the mere existence of a leftover file, so a
    stale/planted registry row can never re-arm capture).
  * A per-tenant session registry (one JSONL row per live session) + a per-tenant
    one-time notice-shown flag, under a WRITABLE per-user data dir
    (`~/.fairmind/`, overridable via `FAIRMIND_INSIGHTS_HOME` so the suite runs
    hermetically).
  * A SessionEnd end-marker that stamps `ended_at` on the matching row.

Authority model (plan §6 dec 9 + SPIKE-A + scout, then narrowed by the ENTERPRISE
POLICY decided 2026-07-27 — see "WHO DECIDES" below):

  * FAIL CLOSED unless the repo has a PER-PROJECT Fairmind MCP configured — a
    user-global Fairmind entry (`~/.claude/settings.json` or top-level
    `~/.claude.json` mcpServers) MUST NOT arm capture (plan V7 "no honest
    tenant", the privacy guard). Any uncertainty (no `~/.claude.json`, non-git
    cwd, malformed JSON, unresolvable tenancy) resolves to capture=false.
  * Default-ON where configured + one-time notice + a REPO-SCOPE-ONLY off switch
    (the committable repo-root `.fairmind-insights.json`).

WHO DECIDES, AND WHY THERE IS EXACTLY ONE SCOPE. The owner's policy, stated
2026-07-27: in a company setting this is a CENTRALIZED decision — the company
enables it, and the developer can neither switch it off for themselves nor switch
it on for themselves. Both switches (`is_opted_out` for ambient capture,
`event_skeleton_consent` for the skeleton) therefore read the COMMITTABLE
repo-root `.fairmind-insights.json` and NOTHING ELSE. The per-user
`~/.fairmind/insights-config.json` no longer participates in either decision, in
either direction: a file under `$HOME` is by construction the developer's own,
so honouring it at all would hand the developer the veto (and the self-grant)
the policy removes. `~/.fairmind/` is still the writable STATE dir (registry,
notice markers, spool) — it is only the DECISION that left it.

THE REPO FILE IS AN INTERIM PROXY, NOT THE FINAL DESIGN. The authority is meant
to be the Fairmind platform, where a manager sets it for the organization. Until
that exists, the repo-root file stands in for the company's decision:
committable, reviewable, and outside any single developer's private state.
Nobody should mistake "it is a JSON file in the repo" for the end state — when
the platform becomes the authority, this is the seam that changes.

AND THE RESIDUAL THAT MAKES "INTERIM" THE RIGHT WORD, stated because a notice
was almost written that denied it: these reads take the FILE, never git. An
UNTRACKED `.fairmind-insights.json` a developer writes into the work tree
themselves is byte-for-byte indistinguishable, to this module, from the one the
company committed (probed 2026-07-27: `git ls-files` empty, `git status`
reporting `??`, and `event_skeleton_consent` still `granted`). So the guarantee
this code can actually make is NARROWER than "the developer cannot change it" —
it is "the switch is a repository file rather than a personal setting, and
nothing under the developer's own `~/.fairmind/` is read at all". Both notices
say exactly that and no more. Closing the gap needs an authority the working
tree cannot forge, which is the platform.

Privacy negative-space: the registry row / any wire-bound value carries ONLY the
OPAQUE tenancy id (a one-way hash of the canonicalized repo toplevel) — never the
raw repo path, `$HOME`, or the git branch name.

CORRECTION TO THE SENTENCE ABOVE (OPEN-1, owner decision 2026-07-30). It is left
standing, and corrected here rather than edited, because what changed is a
deliberate narrowing and the original claim is what the narrowing has to be read
against. The LOCAL registry row now carries exactly two raw local paths — its
session's own git `toplevel` and its own `transcript_dir` — so the claim holds as
"never WIRE-BOUND", not "never persisted". Everything else is unchanged: the
registry is 0600, per-user, and never transmitted; the branch name is still
recorded nowhere; the WIRE payload is built from `meta` (`_digest_one_session`),
which does not carry either field, and `build_wire_payload` returns a CLOSED key
set whose conformance test bans both absolute home-directory prefixes — the
macOS one and the Linux one — across the whole serialized payload. Closed is
what makes a blob-level ban reach every key: `test_wire_conformance.py` pins the
member set itself, so there is no unpinned field for a path to hide in. Two
tests in `test_open1_row_provenance.py` pin the narrowed claim, which prose
alone had protected until now: one bans any path-shaped value in the row OUTSIDE
those two named keys, and one asserts neither value appears anywhere in the wire
payload built from the same session's spooled row.
Why the row needs them at all: see `register_session`.

T2-C3 adds a SECOND, INVERTED lane on top of all of the above: the ordered event
skeleton. Ambient capture is on-by-default-with-an-off-switch; the skeleton is
OFF unless `event_skeleton` is the explicit boolean `true` in the repo file AND
not the explicit `false` there — off beats on, the same rule `is_opted_out`
already applies to ambient capture. `event_skeleton_consent` is the ONE function
that answers "is the skeleton on", and every site acting on that answer —
CAPTURE (`run_sweep`), DELIVERY (`run_drain`) and the NOTICE
(`cmd_session_start`) — asks it instead of re-deriving the question from
`event_skeleton_enabled`, which is a single-key read. Re-deriving is exactly how
capture and delivery came to give different answers, and the notice states they
do not. It carries its OWN one-time notice (`events_notice_message`) behind its
OWN sibling marker (`_events_notice_marker_path` — `record_notice` rewrites the
ambient marker wholesale, so a key inside it would be clobbered), it is resolved
PER SESSION and threaded down as a parameter, and it is delivered through its
OWN door (`fairmind_events_endpoint`) so it can never enlarge the
session-activity body past that door's cap. The decision also gates DELIVERY —
`run_drain` hands `drain(..., events_consent_for=…)` a per-session resolver —
because gating only the projection left already-captured skeletons on the spool
still shipping after the flag was turned off. That read has THREE states, not
two: only an explicit `"event_skeleton": false` may discard what was already
captured, while a config that cannot be read neither sends nor destroys it (see
`event_skeleton_consent`).

CORRECTION, OPEN-1 F4 (2026-07-30), stated here because this paragraph carried
the claim twice: this used to read "resolved ONCE per sweep" and "`run_drain`
resolves `event_skeleton_consent(toplevel)`", and BOTH were the same bug. A
tenancy is the git COMMON DIR, so one registry and one spool serve every linked
worktree of a repo, each with its own toplevel and its own
`.fairmind-insights.json`. One answer per pass therefore applied one checkout's
decision to another checkout's session. Capture was fixed first and delivery was
not, which made things WORSE for one pass: `cmd_sweep` calls `run_sweep` then
`run_drain` on the next line, in one process with one cwd, so the sweep correctly
captured a granting worktree's skeleton and the drain destroyed it on a sibling
worktree's explicit `false` — terminally, bytes reclaimed — then recorded the
GRANTING repo's config as the revoker. Both sites now ask the shared
`skeleton_consent_resolver` per session, and the ONE thing the launcher's
toplevel is still used for is the delivery-target lookup.
`--insights-status` (`cmd_status`) is the reader for what the outbox has
CAPTURED and what became of it — the spool's unresolved skeletons plus what the
outbox persists about delivery. Both halves, since 2026-07-30 (W-2): while it
read the delivery record alone it printed an all-clear over a skeleton that was
captured and never drained, which is the ordinary state one process after this
notice prints and the PERMANENT state in a project with no delivery credential.
"""

import contextlib
import glob
import json
import hashlib
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

# POSIX-only advisory file locking (the ambient digester's per-session flock,
# PL-A1b). Guarded the same way `_loop_ledger.py` guards it, so this module
# still IMPORTS on a non-POSIX host; `run_sweep` degrades to best-effort (no
# real cross-process mutual exclusion) when `fcntl` is unavailable rather than
# refusing to run.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

# PL-A0 shared ledger primitives: a locked/bounded append and an atomic rewrite
# that folds in concurrent appends. Reuse them rather than re-roll lock/atomic
# logic here (the exact drift trap `_loop_ledger.py` itself was created to avoid).
# Sibling module in scripts/; make our own dir importable the way `_loop_ledger`
# imports `loop_tokens`.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# `append_row` is deliberately NOT imported (2026-07-30): the registry stopped
# writing through the bounded ledger primitive — see `_consent_authority.
# register_session`.
# `DEGRADED_DIR` and the `_loop_ledger` module stay bound here for the
# compatibility surface; their reader is `_consent_authority`.
from _loop_ledger import _atomic_write_lines, DEGRADED_DIR  # noqa: E402
import _hook_line  # noqa: E402  (stdlib-only leaf; the one line the user reads)
import _loop_ledger  # noqa: E402
# PL-A1b's pure transform (the digester); a sibling module, imported top-level
# like `_loop_ledger` above — no circularity (ambient_digest only imports
# `_usage_dedup`).
import ambient_digest  # noqa: E402
# The ONE shared central-policy resolver (cache path/read/write + the
# precedence truth table). A sibling import like the two above; deliberately a
# one-way street — `_plugin_policy` never imports THIS module (the judge Stop
# hook is its other consumer and must stay light), which its own suite pins.
import _plugin_policy  # noqa: E402

# The shared consent, policy-resolution, session-registry, tenancy and health
# authority — every name below stays importable from THIS module too (no
# `import *`; an explicit list so a name quietly dropped here fails loudly).
from _consent_authority import (  # noqa: E402,F401 — re-exported, one definition
    # header consts + consent/policy resolution + Decision/evaluate_gate
    _SCHEMA,
    _FAIRMIND_NAME_RE, _fairmind_keys, _is_fairmind_name, _read_json, fairmind_configured,
    data_dir, _registry_path, _notice_marker_path, plugin_version,
    _GIT_REV_PARSE_CACHE, _git_rev_parse, _tenancy_from_common, resolve_tenancy,
    consent_config_path, _config_disables, _config_enables_event_skeleton,
    event_skeleton_enabled,
    EVENTS_CONSENT_GRANTED, EVENTS_CONSENT_REVOKED, EVENTS_CONSENT_INDETERMINATE,
    EVENTS_CONSENT_SCOPE_REPO, EVENTS_CONSENT_SCOPE_UNKNOWN_REPO, _NO_CONFIG,
    event_skeleton_consent, skeleton_consent_resolver, is_opted_out,
    CONSENT_VERSION, CONSENT_CONTENT_MODE, CONSENT_CONTENT_MODE_FAILED_ITERATIONS,
    CONTENT_CONSENT_CLASSES, PURPOSE_LOCAL_ONLY, PURPOSE_CUSTOMER_ONLY,
    PURPOSE_FAIRMIND_TRAINING, PURPOSE_ORDER, _PURPOSE_FEATURES,
    CONSENT_BASIS_EXPLICIT, CONSENT_BASIS_LEGACY_CONFIG, CONSENT_BASIS_NO_CONFIG,
    CONSENT_BASIS_PRE_CONSENT, CONSENT_BASIS_UNREADABLE, CONSENT_BASIS_ORDER,
    ALL_CONSENT_CLASSES, _CONSENT_CLASS_KEYS, config_consent_classes,
    _classes_from_cfg, _config_consent_classes, config_content_mode,
    content_mode_granted, content_purpose, class_consent_state, read_frozen_stamp,
    consent_stamp, consent_classes_resolver, class_consent_state_resolver,
    Decision, evaluate_gate,
    # session registry
    _now_iso, _ALLOWED_ENTRY_SOURCES, _MAX_SESSION_ID_LEN, _BAD_ID_CHARS,
    _clean_session_id, _clean_entry_source, _MAX_PROVENANCE_PATH_LEN,
    PROVENANCE_ROW_KEYS, _clean_provenance_path, _transcript_file,
    _provenance_fields, _ensure_private_dir, _ensure_private_file,
    _durable_append, _read_registry_lines, _registry_lock_path,
    _registry_write_lock, _touch_open_entry_source, register_session,
    mark_session_end,
    # insights endpoints
    _ACTIVITY_ENDPOINT_PATH, _EVENTS_ENDPOINT_PATH, _derive_insights_endpoint,
    fairmind_events_endpoint, fairmind_delivery_target,
    # health
    HEALTH_STATE_UNWRITABLE, HEALTH_NO_DELIVERY_CREDENTIAL, _HEALTH_MESSAGES,
    _state_home_writable, insights_health, DETAILS_AT, health_line,
    health_message, _health_marker_path, recorded_health, health_changed,
    record_health,
    # stale-loop / loop-context registry
    DIVERTED_REFS_SHOWN, _MAX_LOOP_REF_LEN, _clean_loop_ref, StaleLoopMarker,
    stale_loop_marker, _stale_loop_marker, _diverted_rows, stale_loop_message,
    stale_loop_line,
)



# --------------------------------------------------------------------------- #
# PL-A1b — the ambient DIGESTER's SessionStart-driven sweep.
# --------------------------------------------------------------------------- #
#
# `run_sweep` finalizes crash-orphans (a session that ended but never got
# digested, e.g. the process died before its own end-of-session digest could
# run) on the NEXT SessionStart for that tenancy. It never digests a session
# whose per-session lock is held by another process — that's a LIVE digester
# already working it, not an orphan — so a live one is never reaped, only
# skipped this pass (it will be picked up on a later sweep once its holder
# releases the lock).

def _lock_path(tenancy, session_id):
    return os.path.join(data_dir(), "insights", "locks", f"{tenancy}.{session_id}.lock")


def _spool_path(tenancy):
    return os.path.join(data_dir(), "insights", "rollups", tenancy + ".jsonl")


# --- the content compartment (JC6) ------------------------------------------
#
# A SIBLING of the rollup spool, never a row inside it, and the three reasons
# are code rather than taste: `ambient_outbox._pending_rows` keys on a non-empty
# `session_id`, so a row keyed by (loop, iteration) is silently invisible to the
# drain; giving it one instead collapses N red iterations of one session to the
# latest, because that drain dedups per session; and `_unsendable_reason` tests
# SCHEMA FIRST, so an unrecognised schema in the shared file is held forever and
# inflates a healthy tenant's backlog hint. A foreign row in the shared file
# poisons a healthy tenant's diagnostics.
#
# NOTHING DRAINS IT TODAY. JC6 is capture; delivery is its own card, and
# `ambient_outbox.drain(transport=None)` is already a byte-for-byte no-op, so
# "captured durably to local disk and delivered nowhere" is the shipped state
# rather than a gap. The consequence is stated where a reader will meet it:
# `--insights-status` says the compartment grows and is not drained.
CONTENT_SCHEMA = "fm-content/1"

# How long a capture will WAIT for the compartment's lock before giving up on
# the row. `_durable_append` takes a BLOCKING LOCK_EX by deliberate design and
# is shared with the session registry, so it is not the thing to change; but the
# gate's capture runs on the Stop-hook path, under a 600 s process timeout, and
# a telemetry side-effect that can hang the hook is a defect in the gate. Two
# seconds is four orders of magnitude above the measured hold (0.11-0.13 ms for
# 63 KB-256 KB), so today it is unreachable; it exists for the day a compactor
# holds the same fd.
CONTENT_LOCK_BUDGET_S = 2.0

# The size at which `--insights-status` starts saying the compartment is worth
# a look. A THRESHOLD AND NOT A CAP: nothing evicts a row, because the rows are
# the one thing in this system that cannot be recreated — a red iteration's
# bytes exist in no commit, ever. 16 MiB is roughly two hundred whole loops at
# the measured per-loop budget, i.e. far past any honest working set and far
# short of a disk anyone would notice.
CONTENT_WARN_BYTES = 16 * 1024 * 1024


def content_spool_path(tenancy):
    """The tenant's content compartment. Sibling of `_spool_path`, same tenancy
    key, its own file — see the block comment above for why it cannot be a row
    in the rollup spool."""
    return os.path.join(data_dir(), "insights", "content", tenancy + ".jsonl")


def content_failures_path(tenancy):
    """Where a capture that could NOT be written records that it happened.

    A swallowed failure that leaves no trace is indistinguishable from a red
    iteration that was never captured, and the two are opposite facts about the
    corpus. This file holds one line per failure — a timestamp and a REASON
    CATEGORY, never the row it failed to write — so `--insights-status` can
    report "n captures were lost" instead of nothing at all."""
    return os.path.join(data_dir(), "insights", "content", tenancy + ".failures.jsonl")


def _flock_bounded(fh, budget_s):
    """Take an exclusive lock on `fh`, waiting at most `budget_s`. True when
    held, False when the budget ran out with somebody else holding it.

    THE ONE BOUNDED ACQUISITION IN THIS MODULE, and it is a function because it
    arrived twice in one change. Every other lock here is a BLOCKING `LOCK_EX`
    (an appender to the registry or the rollup spool must wait its turn and must
    never skip its own write); the two content-compartment paths are the
    exception, because they run on the Stop-hook path where an unbounded wait
    costs the developer the gate's own feedback. Written once so the budget, the
    poll interval and — the part that had already drifted between the two copies
    — what "gave up" means are specified in one place.

    Degrades to True on a host without `fcntl`, matching `_durable_append`'s
    best-effort behaviour there: no lock is available, so there is nothing to
    wait for and refusing to proceed would disable the feature rather than
    protect it."""
    if _fcntl is None:
        return True
    deadline = time.monotonic() + budget_s
    while True:
        try:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)


def _bounded_append(path, line, budget_s=CONTENT_LOCK_BUDGET_S):
    """`_durable_append` with a CEILING on the lock wait. Returns True when the
    line landed, False when the budget ran out with the lock still held.

    ⚠️ IT IS A SEPARATE FUNCTION AND NOT A FLAG ON `_durable_append`, because
    that writer is shared with the SESSION REGISTRY, whose contract is the
    opposite of this one: a registry appender must WAIT its turn and must never
    skip its own write. Changing the lock there to buy this would change the
    behaviour of a path JC6 has no business touching.

    Everything else is `_durable_append`'s discipline verbatim, and deliberately
    so: 0700 dir, 0600 file, the lock on the file's OWN fd (so any rewriter must
    rewrite in place under that same fd — never mkstemp+`os.replace`, which
    shares no lock with an appender and would drop a concurrent write onto a
    dangling inode), `flush()` + `fsync()` inside the lock.

    An IO error still PROPAGATES — only lock contention is answered with False.
    A full disk and a busy lock are different facts and the caller records them
    differently."""
    _ensure_private_dir(os.path.dirname(path))
    _ensure_private_file(path)
    fh = open(path, "a", encoding="utf-8")
    try:
        if not _flock_bounded(fh, budget_s):
            return False
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        return True
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def content_append(tenancy, row):
    """Append ONE captured row to the tenant's content compartment. Returns True
    when it landed; on contention or an IO failure it records a dated reason
    CATEGORY in the failures file and returns False, so a lost capture is a fact
    somebody can read rather than a silence."""
    line = json.dumps(row, ensure_ascii=True, sort_keys=True)
    try:
        if _bounded_append(content_spool_path(tenancy), line):
            return True
        reason = "lock_contended"
    except Exception:  # noqa: BLE001 — the capture may never fail the gate
        reason = "write_failed"
    _record_content_failure(tenancy, reason)
    return False


def _record_content_failure(tenancy, reason):
    """One line per lost capture: when, and WHY as a closed category. Never the
    row, never a path, never an exception message — a failure record that
    carried the payload would be the content channel opening through the door
    marked 'this did not work'."""
    try:
        _bounded_append(content_failures_path(tenancy),
                        json.dumps({"at": _now_iso(), "reason": reason},
                                   ensure_ascii=True, sort_keys=True))
    except Exception:  # noqa: BLE001
        pass


#: Which row keys each content class owns, so a revocation of one letter finds
#: exactly its own bytes.
#:
#: 🔴 IT IS NOT JUST THE DIFF. The first version of this list held the five
#: `diff*` keys and stopped there, which left `mutation_signature` (the member
#: PATHS of the customer's tree, one per refused change), `paths_unstable` (more
#: of the same paths) and `verdicts` (the refused iteration's check ids and
#: measured values) sitting in the compartment after class B had been revoked in
#: writing. The map classifies all three as B — the loop door classifies the
#: same facts the same way — so the gap was between two documents that had to
#: agree, and a test that compares them is what found it.
#:
#: The `*_class` stamps are unclassified in the map (a stamp is never governed
#: by the class it declares) and are deleted here anyway: a stamp whose content
#: is gone describes nothing. The map records that with `reclaimed_with`, and
#: the plugin suite asserts the two lists are the same set.
CONTENT_CLASS_FIELDS = {"B": ("diff", "diff_class", "diff_bytes",
                              "diff_omitted", "paths_over_cap",
                              "paths_unstable", "paths_unreached",
                              "mutation_signature", "verdicts"),
                        "C": ("context", "context_class")}


def discard_content_classes(tenancy, revoked, revoked_for=None):
    """Remove the bytes of the REVOKED classes from this tenant's content
    compartment, in place, and return how many rows lost content.

    🔴 SURGICAL, NOT WHOLESALE, AND THE DIFFERENCE IS A HARM EITHER WAY. A repo
    that sets `rejected_proposals: false` and leaves `generation_context: true`
    has said one thing precisely; deleting the compartment would destroy data it
    did not ask to lose, and that data is irreplaceable in exactly the way this
    whole feature exists to point out — a red iteration's bytes are in no
    commit, ever. So each row loses the fields of the revoked letters (the per-
    field stamps are what makes that computable without re-deriving anything),
    and a row left carrying no content at all goes.

    REWRITTEN IN PLACE UNDER THE FILE'S OWN FD LOCK, never mkstemp+`os.replace`:
    the appender's lock is on the fd, a rename shares no lock with it, and a
    concurrent capture would land on the dangling old inode and be lost. Same
    discipline, same reason, as `ambient_outbox._compact_spool`.

    Never raises. Reclamation runs on the gate's hook path and at session start;
    a failure to reclaim must show up in `--insights-status`, never fail a
    check."""
    letters = [c for c in revoked if c in CONTENT_CLASS_FIELDS]
    if not letters:
        return 0
    path = content_spool_path(tenancy)
    if not os.path.isfile(path):
        return 0
    # LOOP-INVARIANT, hoisted: `letters` is fixed above, so rebuilding this set
    # per row cost two comprehensions on every line of a file this module
    # explicitly budgets to 16 MiB, inside a 2 s bounded lock on the Stop-hook
    # path.
    doomed = {k for c in letters for k in CONTENT_CLASS_FIELDS[c]}
    kept, changed = [], 0
    try:
        fh = open(path, "r+", encoding="utf-8")
    except OSError:
        return 0
    locked = False
    try:
        # BOUNDED for the reason the append is: reclamation runs on the same
        # Stop-hook path, on a file that may be large. Giving up costs one
        # DEFERRED reclamation — the next refused iteration or session start
        # runs it again, and until then the bytes are unsent, not un-deleted.
        if not _flock_bounded(fh, CONTENT_LOCK_BUDGET_S):
            return 0
        locked = _fcntl is not None
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # KEPT VERBATIM, never dropped. A line this reader cannot parse
                # is a line whose class it cannot know — and "destroy what you
                # cannot read" on the one path whose job is to honour a
                # narrowly-scoped deletion would take bytes nobody revoked.
                # Fail-closed governs sending; it never governs destroying.
                kept.append(line)
                continue
            if not isinstance(row, dict):
                kept.append(row)      # not ours to interpret, not ours to drop
                continue
            # 🔴 THE REVOCATION APPLIES TO THE CHECKOUT THAT AUTHORIZED THE ROW,
            # and `revoked_for` is that checkout's toplevel. The compartment is
            # keyed by the git COMMON DIR, which every linked worktree of one
            # repository shares — so without this scoping a `false` written in
            # one worktree reaches rows captured under another worktree's own,
            # still-granting config. A row with no recorded toplevel predates
            # the field and is treated as in scope, which is the safe direction
            # for a deletion.
            origin = row.get("toplevel")
            if revoked_for and origin and origin != revoked_for:
                kept.append(row)
                continue
            if not doomed & set(row):
                kept.append(row)
                continue
            changed += 1
            for key in doomed:
                row.pop(key, None)
            row["classes"] = [c for c in row.get("classes", [])
                              if c not in letters]
            # The stamp says which classes the row was collected under, in TWO
            # places — the top-level list and the nested consent object — and
            # both are updated. Leaving the nested copy claiming a class whose
            # bytes are gone would make the row assert, durably, that it holds
            # content it no longer holds: the same defect as a stale map entry,
            # one level down.
            nested = row.get("consent")
            if isinstance(nested, dict) and isinstance(nested.get("classes"), list):
                nested["classes"] = [c for c in nested["classes"]
                                     if c not in letters]
            # A row with no content left is a stamp about nothing. It goes:
            # keeping it would leave a durable record that an iteration was
            # captured, on a repo that has just said it must not have been.
            if any(k in row for c in CONTENT_CLASS_FIELDS
                   for k in CONTENT_CLASS_FIELDS[c]):
                kept.append(row)
        # NOTHING WENT, SO NOTHING IS REWRITTEN. Without this, a repository
        # that revoked a class once kept re-serialising and re-fsyncing its
        # whole compartment on every session start and every refused iteration,
        # forever — the revocation stays in the config, so `letters` stays
        # non-empty long after the last matching byte is gone.
        if not changed:
            return 0
        # THE REPLACEMENT IS BUILT WHOLE BEFORE THE FILE IS TOUCHED. Truncating
        # and then serialising row by row leaves a window in which a raise
        # between the two has destroyed the spool and written nothing back; the
        # rows are irreplaceable, so the window is closed by doing all the work
        # that can fail while the old bytes are still on disk. Residual, stated
        # rather than engineered away: the rewrite is still in place under this
        # fd's own lock (never mkstemp+`os.replace`, which shares no lock with a
        # concurrent appender), so a crash between `truncate` and `write` still
        # loses the file. That window is one syscall wide instead of N.
        replacement = "".join(
            (row if isinstance(row, str)
             else json.dumps(row, ensure_ascii=True, sort_keys=True)) + "\n"
            for row in kept)
        fh.seek(0)
        fh.truncate()
        fh.write(replacement)
        fh.flush()
        os.fsync(fh.fileno())
    except OSError:
        return 0
    finally:
        try:
            if locked and _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()
    return changed


def revoked_content_classes(toplevel):
    """The subset of B / C this checkout has EXPLICITLY revoked — the sorted
    letters whose `class_consent_state` is `EVENTS_CONSENT_REVOKED`.

    ⚠️ IT ASKS THE THREE-VALUED STATE AND NEVER THE MODE. Turning the content
    mode off says "stop capturing"; setting a class to the literal `false` says
    "you should not have what you took". Collapsing the two would make an
    ordinary opt-out destroy data, which is the 2026-07-27 data-loss shape this
    module keeps two separate predicates to avoid."""
    if not toplevel:
        return []
    return sorted(c for c in CONTENT_CONSENT_CLASSES
                  if class_consent_state(toplevel, c) == EVENTS_CONSENT_REVOKED)


def reclaim_content(tenancy, toplevel):
    """Carry out whatever the live config says must go, and report the count.
    The two halves kept apart above, joined at the one call the hook paths make.

    THE COMPARTMENT'S EXISTENCE IS THE GUARD, and it is checked before the
    config is read rather than after. This runs on the SessionStart fast path of
    every repository, and the overwhelming majority have never captured a byte:
    for them the whole call is one `stat` that finds nothing, instead of a JSON
    parse per class asking whether data that does not exist was revoked."""
    if not tenancy or not os.path.isfile(content_spool_path(tenancy)):
        return 0
    return discard_content_classes(tenancy, revoked_content_classes(toplevel),
                                   revoked_for=toplevel)


def _spool_append(spool_path, line):
    """Durably append ONE line to the spool (PL-A1b review Fix 1). The spool is
    A1c's DURABLE DRAIN QUEUE — every rollup waits here until something reads
    and clears it downstream — never a bounded ledger, so unlike
    `_loop_ledger.append_row` this write NEVER rotates/trims: all rollups
    are retained regardless of how many accumulate. It also NEVER swallows an IO
    failure, so `_digest_one_session` can decide NOT to stamp `digested_at`
    (Fix 2) rather than silently losing the rollup forever.

    CORRECTION, 2026-07-30, recorded BESIDE the two properties above because they
    are still the spool's contract and still the reason it is written this way:
    both are now PROVIDED BY `_durable_append`, not implemented here. This
    function is the spool's named POLICY wrapper — it says WHICH file and WHY
    that file needs an unbounded durable append — and the mechanism (blocking
    fd-flock, fsync, no rotation, no swallowed IO error) lives in one place,
    because the registry needs exactly the same thing and a second copy of it
    would be a second thing to get wrong. `ambient_outbox`'s "the only writer of
    NEW spool rows" is unchanged: this is still it.

    AT-LEAST-ONCE semantics: a crash between a successful append here and the
    registry stamp in `_digest_one_session` leaves that row's `digested_at`
    unset, so the NEXT sweep re-digests and re-appends the same session — a
    duplicate rollup (same session_id) in the spool. This is the accepted
    A1b boundary; A1c's outbox dedups by session_id on drain, so exactly-once
    delivery is A1c's job, not this module's."""
    _durable_append(spool_path, line)


def _find_registry_row(registry_path, session_id):
    """The full registry row dict for `session_id`, or None if the registry
    is missing/unreadable or carries no matching row. Used by
    `_digest_one_session` (PL-A2a) as its FALLBACK ONLY — a fresh re-read of
    the registry when the caller did not already have the row in hand (a
    direct call with no `registry_row=` kwarg, e.g. the pre-existing
    4-positional calls in `test_ambient_spool_durability.py`). A
    missing/non-matching row degrades to None (never raises) — the caller
    then leaves started_at/ended_at unset rather than guessing, which is the
    correct behavior for a direct `_digest_one_session` call made with no
    registry row at all (Property 1's own fixture).

    PL-A2a round 2 (D7): `run_sweep` already parses the WHOLE registry once
    to build its own candidate list, so it now passes the row it already
    parsed straight through via `_digest_one_session`'s `registry_row=`
    keyword instead of making THIS function re-read + re-parse the same
    file a second time per session — this function is reached only on the
    direct-call fallback path, never from `run_sweep` itself."""
    lines = _read_registry_lines(registry_path)
    if not lines:
        return None
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("session_id") == session_id:
            return row
    return None


def _registry_toplevels(registry_path):
    """`{session_id: toplevel}` for every registry row that records one — the
    join DELIVERY needs to ask the skeleton question per session.

    WHY DELIVERY HAS TO JOIN BACK HERE AT ALL. The SPOOL row deliberately does not
    carry the toplevel: `meta` is what the wire payload is built from, so OPEN-1 F1
    stopped the row's raw paths at the registry, and adding the field to the spool
    would put a raw local path one careless key-copy away from the wire (and move
    every conformance fixture's `rawDigest`). The registry is the local, 0600
    provenance store, so `run_drain` reads it by session id instead.

    ONE read of the whole file, not one per session: the drain then answers N
    sessions from a dict, which is why this returns a MAP rather than exposing a
    per-session lookup that would re-read and re-parse the registry N times.

    FIRST row THAT RECORDS A USABLE TOPLEVEL wins for a repeated session id.
    `register_session` is idempotent per id (N4), but `mark_session_end` documents
    that duplicate open rows can survive from before that fix.

    CORRECTED 2026-07-30 (cross-model pre-PR review): this said "FIRST row wins
    …, matching `_find_registry_row` exactly", and it does NOT match it.
    `_find_registry_row` returns the first row with the id whatever it contains;
    this skips a row whose `toplevel` is missing or rejected and keeps looking. On
    a registry holding a pre-OPEN-1 row and a post-upgrade one for the same id,
    the two functions therefore answer differently — which is the very "two
    functions disagreeing about WHICH row a session id means" the old sentence
    named as a defect class while introducing an instance of it.

    THE BEHAVIOUR IS KEPT AND THE CLAIM IS FIXED, because skipping is the better
    rule here: this map exists to answer "under which repo was this captured", and
    a row with no toplevel does not answer it. Falling back to a later row that
    does is strictly more informative than returning nothing. The residual, stated
    rather than hidden: where the two rows are genuinely different checkouts, the
    later one's decision is applied to the earlier one's skeleton. That is bounded
    by N4 idempotency (normally there is one row) and is why the delivery side
    treats an unresolved row as INDETERMINATE — retained, never destroyed — rather
    than trusting a guess.

    Absent/unreadable registry -> `{}`, which resolves every session to "no
    recorded repo": retained, never sent, never discarded."""
    lines = _read_registry_lines(registry_path)
    if not lines:
        return {}
    found = {}
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        top = _clean_provenance_path(row.get("toplevel"))
        if isinstance(sid, str) and sid and top and sid not in found:
            found[sid] = top
    return found


def _row_already_digested(registry_path, session_id):
    """Fresh re-read of the registry: True iff the row for `session_id` already
    carries `digested_at`. Used AFTER a lock is acquired, as the idempotency
    recheck that makes two racing sweeps converge on exactly one digest — the
    lock only serializes; without this recheck, a sweep that acquires the lock
    just after a concurrent sweep released it (having already digested) would
    digest a second time."""
    lines = _read_registry_lines(registry_path)
    if not lines:
        return False
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("session_id") == session_id:
            if row.get("digested_at"):
                return True
    return False


def _stamp_digested(tenancy, registry_path, session_id, digested_at, *,
                    degraded_reason=None):
    """Stamp `digested_at` on the matching (session_id) row, mirroring
    `mark_session_end`'s reconcile-on-rewrite discipline so a concurrent
    `register_session`/`mark_session_end` fire is never clobbered.

    `degraded_reason` (OPEN-1 F3) records WHY this row's rollup is not a
    measurement — `_DEGRADED_TRANSCRIPT_MISSING` or
    `_DEGRADED_TRANSCRIPT_UNPARSEABLE`. It lives HERE, on the LOCAL row, and
    deliberately nowhere else: both causes now take the one `parserDegraded =
    True` path, so without a recorded reason they would be indistinguishable —
    and putting the distinction on the WIRE would add an unconditional spool-row
    key, which moves `rawDigest` for EVERY session (`_raw_digest` hashes every
    key except `events`/`lanes`) and would cost a two-repo fixture regeneration
    plus a coordinated sha bump. Keyword-only with a default, so the four
    pre-existing 4-positional call sites are unaffected.

    Holds the tenancy-wide registry lock (Fix 3) across the read + write: the
    caller's per-session flock only ever protects THIS session against a
    second concurrent digest of itself, never a DIFFERENT session's
    concurrent stamp racing on the one shared registry file — this lock
    closes that gap."""
    with _registry_write_lock(tenancy):
        lines = _read_registry_lines(registry_path)
        if lines is None:
            return
        changed = False
        out = []
        for ln in lines:
            try:
                row = json.loads(ln)
            except Exception:
                out.append(ln if ln.endswith("\n") else ln + "\n")
                continue
            if (isinstance(row, dict) and row.get("session_id") == session_id
                    and row.get("ended_at") and not row.get("digested_at")):
                row["digested_at"] = digested_at
                if degraded_reason:
                    row["degraded_reason"] = degraded_reason
                changed = True
            out.append(json.dumps(row) + "\n")
        if changed:
            _atomic_write_lines(registry_path, out, reconcile_from=len(lines))


# --------------------------------------------------------------------------- #
# Registry compaction (2026-07-30) — the registry's retention rule, transplanted
# from `ambient_outbox._compact_spool`/`_retained_rows` rather than invented:
# the cap decides only WHETHER compaction is worth ATTEMPTING, and then only
# genuinely SETTLED rows are reclaimed. It is the counterpart to
# `register_session`'s now-unbounded append: a file nothing trims needs a rule
# for what may be dropped, and that rule is STATE, never age.
# --------------------------------------------------------------------------- #

_REGISTRY_COMPACT_CAP = 2000


def _spool_session_ids(tenancy):
    """Every session id currently referenced by a row on `tenancy`'s spool.

    Read through `ambient_digest._read_jsonl` — the one degrade-graceful JSONL
    reader (missing file -> [], unparseable line skipped, never raises), the same
    reader `ambient_outbox._read_spool_rows` delegates to. THE LAYERING IS
    DELIBERATE: `ambient_outbox` imports THIS module at its top, so importing it
    back here would be a cycle; `ambient_digest` imports neither, so reading the
    spool through it inverts nothing and duplicates no reader.

    A non-str id is skipped, but the EMPTY string is kept — `_clean_session_id`
    can legitimately produce "" and a row that cannot be identified must be
    treated as possibly-referenced. Every ambiguity here resolves toward KEEPING
    the registry row: reclaiming one wrongly is unrecoverable, keeping one
    wrongly costs a line of disk."""
    ids = set()
    for row in ambient_digest._read_jsonl(_spool_path(tenancy)):
        if isinstance(row, dict):
            sid = row.get("session_id")
            if isinstance(sid, str):
                ids.add(sid)
    return ids


def _registry_row_settled(row, spool_session_ids):
    """True iff `row` is genuinely SETTLED and may be reclaimed: it has been
    DIGESTED and its session is no longer referenced by any spool row.

    Both halves are required. `digested_at` alone says the rollup was PRODUCED,
    not that it was DELIVERED — it sits on the spool until a drain acks it, and
    `run_drain` joins BACK to this row for the session's `toplevel`
    (`_registry_toplevels`), so reclaiming a still-spooled row is exactly the
    unknown-repo defect this fix exists to end. Everything else — never ended,
    ended-not-digested (a crash-orphan the next sweep still has to finalize),
    digested-but-still-on-the-spool — is KEPT regardless of count.

    Mirrors `ambient_outbox._retained_rows` in shape and in direction: a
    non-dict row is NOT settled (kept), because a row that cannot be understood
    is never one this may destroy."""
    if not isinstance(row, dict):
        return False
    if not row.get("digested_at"):
        return False
    sid = row.get("session_id")
    if isinstance(sid, str) and sid in spool_session_ids:
        return False
    return True


def _settled_session_ids(lines, spool_session_ids):
    """The session ids the registry SNAPSHOT `lines` shows as settled against
    `spool_session_ids`.

    THE PAIR IS THE POINT, and so is the order it was read in. The producer
    commits a digest as spool-append THEN `digested_at` stamp (PL-A1b Fix 2), so
    a reclaimer must read in the INVERSE order — this snapshot first, the spool
    second — and must then decide from THAT pair. Observing a stamp in `lines`
    therefore implies the matching spool append was already on disk before the
    spool read, so a session digested by a CONCURRENT sweep after the snapshot
    can never be judged settled-and-unreferenced. Deciding instead from a
    registry re-read taken AFTER the spool read sees exactly that: the stamp
    without the row, and the session is reclaimed while its rollup is still
    waiting to be sent — the original defect through a different door. Two sweeps
    of one tenancy at once is ordinary, not contrived: a tenancy is the git
    COMMON DIR, shared by every worktree and every subdirectory cwd of the repo."""
    ids = set()
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if _registry_row_settled(row, spool_session_ids):
            sid = row.get("session_id")
            if isinstance(sid, str):
                ids.add(sid)
    return ids


def _retained_registry_lines(lines, settled_ids):
    """The subset of `lines` compaction must KEEP, in file order. An unparseable
    line is KEPT — never reclaim what cannot be read.

    A line is dropped only when its session id is in `settled_ids` (the ordered
    snapshot+spool verdict above) AND THE LINE ITSELF still carries
    `digested_at`. That second conjunct is load-bearing, not belt-and-braces:
    `_clean_session_id` legitimately yields "" for a missing/junk id, so keying
    the drop purely by session id would collapse every empty-id row into one
    bucket and take a LIVE empty-id row out along with a settled one. The
    per-line check is what keeps them apart — do not delete it as redundant."""
    kept = []
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            kept.append(ln)
            continue
        sid = row.get("session_id") if isinstance(row, dict) else None
        if (isinstance(sid, str) and sid in settled_ids
                and isinstance(row, dict) and row.get("digested_at")):
            continue
        kept.append(ln)
    return kept


def _compact_registry(tenancy, cap=_REGISTRY_COMPACT_CAP):
    """Reclaim SETTLED rows from `tenancy`'s registry once it exceeds `cap`.

    `cap` is ONLY THE TRIGGER — it decides whether compaction is worth
    attempting, never what survives it. Under cap this is a no-op; over cap the
    file is rewritten to hold exactly `_retained_registry_lines`, which may still
    be more than `cap` rows. That is the same bargain `_compact_spool` strikes
    ("a still-pending row is NEVER dropped, even if that leaves the spool over
    cap") and it is the whole point: age is not evidence of anything.

    RECLAMATION IS TRANSITIVELY GATED ON THE SPOOL, stated here rather than left
    to be discovered: a settled session keeps its registry row until its SPOOL row
    is reclaimed, and `_compact_spool` only reclaims once the SPOOL is over its
    own cap. So a healthy, quiet tenancy can sit legitimately far over `cap`
    indefinitely. Conservative in the right direction — the alternative is a
    registry row that disappears while the delivery side still needs to join to it.

    WHERE THIS RUNS: `run_sweep`, the niced detached background process — NEVER
    the SessionStart fast path. It reads the spool (an extra file read) and
    rewrites the registry; that is fine in the sweep and not fine on a hook's ~5s
    budget. Never raises: it is called from inside `run_sweep`'s own guard, and
    every IO failure here degrades to "compact later".

    TWO LOCKS, in one direction only:
      * `_registry_write_lock(tenancy)` serializes this against the REPLACE-based
        rewriters of the same file that take it — `_stamp_digested` and
        `mark_session_end` — which share no fd with anyone. NOT ALL OF THEM DO:
        `_touch_open_entry_source` rewrites through `_atomic_write_lines` and
        deliberately takes NO registry lock (a real, test-proven self-deadlock via
        `fcntl.flock`'s non-reentrant per-file-description scoping — see its own
        docstring), so a concurrent touch's `os.replace` can discard this
        compaction's rewrite wholesale. That is a WASTED PASS, not a loss: the
        touch reconciles appends past its own snapshot boundary and its snapshot
        is a superset of what compaction kept, so every row it restores is a row
        that existed; the next sweep compacts again. Stated as "the ones that take
        it" rather than a count, because the set of rewriters is open.
      * a blocking `fcntl.flock` on the registry file's OWN fd serializes it
        against `register_session`'s `_durable_append`, which holds exactly that.
    Hence the rewrite is IN PLACE (seek/truncate/write/fsync), never
    mkstemp+`os.replace`: a rename would swap the inode out from under a
    concurrent append, which would then land on the dangling old inode and be
    silently lost — `_compact_spool` refuses replace-based rewrite for precisely
    this reason and the registry now has precisely this reason too. The file is
    RE-READ under the lock, so an append made since the trigger check is folded in
    rather than clobbered. A waiting appender is safe across the truncate: it
    opened in append mode and seeks to EOF at write time.
    Nesting can only go compaction -> (registry lock, then fd lock); a stamp takes
    the registry lock and no fd lock, an append takes the fd lock and no registry
    lock, so no cycle exists."""
    registry_path = _registry_path(tenancy)
    lines = _read_registry_lines(registry_path)
    if lines is None or len(lines) <= cap:
        return
    # ORDER, not just contents: registry snapshot (above) THEN spool (here), the
    # inverse of the producer's append-then-stamp — see `_settled_session_ids`.
    # Both reads stay OUTSIDE the locks on purpose: the spool is the drain queue
    # and can be hundreds of MB in the documented events-door-down case, and the
    # fd lock below is the one a SessionStart `register_session` blocks on.
    referenced = _spool_session_ids(tenancy)
    settled_ids = _settled_session_ids(lines, referenced)
    with _registry_write_lock(tenancy):
        try:
            fh = open(registry_path, "r+", encoding="utf-8")
        except OSError:
            return
        locked = False
        try:
            if _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)  # blocking
                locked = True
            # The in-lock re-read PRESERVES rows appended since the snapshot; it
            # never widens what may be dropped (that verdict is `settled_ids`,
            # already fixed by the ordered pair above).
            current = [ln for ln in fh if ln.strip()]
            kept = _retained_registry_lines(current, settled_ids)
            fh.seek(0)
            fh.truncate()
            for ln in kept:
                fh.write(ln if ln.endswith("\n") else ln + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            try:
                if locked and _fcntl is not None:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def _sidecar_role(sidecar_path):
    """The agent role for one `agent-*.jsonl` sidecar, read from its SIBLING
    `agent-*.meta.json` (T2-C1). Returns a role string, never raises.

    Only `agentType` is ever taken from that file. `description` is free text
    (a prompt fragment, i.e. potentially anything the user typed) and
    `toolUseId` is an internal handle; neither is a fact about WHICH agent
    ran, and the wire's `agents[]` key set is CLOSED by design.

    Best-effort by construction: a missing, unreadable, unparseable or
    non-dict meta file, or one with no usable `agentType`, yields
    `ambient_digest.UNATTRIBUTED_ROLE` — the sidecar's TOKENS are still
    counted, they are simply not attributed. A metadata read must never cost
    a session its numbers, and the meta schema is not stable across Claude
    Code versions (some carry no `spawnDepth` at all), so only `agentType` is
    required and every other key may be absent.

    FORKS are mapped to `ambient_digest.MAIN_ROLE`, not to their own bucket,
    because a fork sidecar is the main thread CONTINUING — not a sub-agent.
    That is an ATTRIBUTION statement, and (since the T2-C1 amendment) ONLY an
    attribution statement: the mapping no longer carries any token
    conservation.

    It briefly did, and that was the defect. A fork re-emits the fork-point
    message — the same `message.id`, byte-identical usage, marked
    `isSidechain`. Before T2-C1 main and sidecars shared one per-model bucket,
    so `_usage_dedup` absorbed the duplicate; splitting the bucket by role put
    the two copies in DIFFERENT buckets and doubled the message (measured: 6
    of 142 real sessions, up to +7.6% output tokens). Mapping forks here was
    the first fix, and it was reachable ONLY when the meta read above
    SUCCEEDED — the missing/unreadable/non-dict branches return
    `UNATTRIBUTED_ROLE` before ever reaching this check, and a future
    `agentType` spelling falls past it, so a FAILED METADATA READ INVENTED
    TOKENS (+33% on a 2-message fixture). That is precisely the outcome the
    "a metadata read must never cost a session its numbers" rule above exists
    to forbid, inverted.

    The invariant is therefore enforced where it belongs — in
    `ambient_digest.digest`, which claims each `message.id` at most once per
    MODEL before bucketing, so no id can span two buckets whatever this
    function returns. Mislabelling a fork now costs a role name and nothing
    else. Both signals are still honoured (`isFork` and `agentType == "fork"`)
    because they were 10/10 equivalent in the corpus but this metadata's shape
    drifts between versions — a census over 613 real sidecars found `isFork`
    present in only 10 of them."""
    meta_path = sidecar_path[: -len(".jsonl")] + ".meta.json"
    # `_read_json` IS this module's read-and-swallow contract for an optional
    # JSON sidefile (it returns None on any failure), so the missing /
    # unreadable / unparseable cases and the non-dict case collapse into one
    # guard: `isinstance(None, dict)` is already False. Re-inlining the
    # try/except here would give the plugin two definitions of "how we tolerate
    # an unreadable JSON sidefile" in one file.
    meta = _read_json(meta_path)
    if not isinstance(meta, dict):
        return ambient_digest.UNATTRIBUTED_ROLE
    agent_type = meta.get("agentType")
    if meta.get("isFork") is True or agent_type == "fork":
        return ambient_digest.MAIN_ROLE
    if not isinstance(agent_type, str) or not agent_type:
        return ambient_digest.UNATTRIBUTED_ROLE
    return agent_type


def _discover_sidecars(transcript_dir, session_id):
    """Subagent sidecars for `session_id` as `(path, agent_role)` PAIRS: every
    `<transcript_dir>/<session_id>/subagents/agent-*.jsonl`, sorted by path for
    deterministic aggregation order, each paired with the role `_sidecar_role`
    reads from its sibling `agent-*.meta.json`.

    The `.jsonl` suffix in the glob still means a `.meta.json` file is never
    read as a TRANSCRIPT — T2-C1 did not widen the glob; it reads that sibling
    separately, as JSON, for one field. Before T2-C1 the metadata was ignored
    entirely, so every sub-agent's tokens merged anonymously into the main
    thread's per-model sum and no ambient token figure was attributable.

    No `subagents/` dir (the common no-subagent case) degrades to an empty
    list via `glob.glob`, never raises. The return shape is consumed by
    `ambient_digest.digest_transcript_file(..., sidecars=)`, which also accepts
    a bare path string per entry for a sidecar whose role is unknown."""
    pattern = os.path.join(transcript_dir, session_id, "subagents", "agent-*.jsonl")
    return [(path, _sidecar_role(path)) for path in sorted(glob.glob(pattern))]


# OPEN-1 F3 — the two causes of a rollup that is NOT a measurement. Both take the
# ONE `parserDegraded = True` path; these strings are what keeps them apart, and
# they are recorded on the LOCAL registry row only (never on the wire).
_DEGRADED_TRANSCRIPT_MISSING = "transcript_missing"
_DEGRADED_TRANSCRIPT_UNPARSEABLE = "transcript_unparseable"


def _degraded_rollup(meta):
    """The ONE explicitly-degraded rollup: `digest([], meta)` with
    `parserDegraded` set.

    A function rather than two inline pairs of lines, so "there is exactly one
    degraded path" is a fact about the code and not a claim in a comment — OPEN-1
    routes a MISSING transcript here alongside the pre-existing UNPARSEABLE case,
    and the two must stay byte-identical in shape (they differ only in the reason
    recorded on the local registry row). It reuses the digester's own empty-digest
    so the rollup schema + version oracle live in ONE place and this path never
    re-encodes `digest()`'s output shape."""
    rollup = ambient_digest.digest([], meta)
    rollup["parserDegraded"] = True
    return rollup


def _resolve_transcript_dir(session_id, row_dir, launcher_dir):
    """OPEN-1 F2 — WHICH directory holds `session_id`'s transcript, with
    EXISTENCE as the oracle. Returns the directory, or None when no candidate
    holds the file (the caller then takes F3's explicitly degraded path).

    Order:

      1. the ROW's own recorded `transcript_dir` — the context that session was
         actually captured under;
      2. the LAUNCHER's `transcript_dir` — for rows written before OPEN-1, which
         carry no provenance at all; this loses nothing on upgrade, because a
         pending legacy row keeps working whenever the launcher's dir was in fact
         the right one;
      3. neither.

    THE ORACLE IS THE FILE, NOT THE RECORD. `<dir>/<session_id>.jsonl` is named by
    session id, so its presence is strong evidence that directory is the right
    one — which is why a recorded value that no longer holds the file falls
    through to (2) instead of being trusted, and why (2) is not reached merely
    because the row happens to record nothing. `row_dir` is re-validated here
    rather than trusted as read: it came off disk, and a hand-edited or planted
    registry must not be able to point the sweep at an arbitrary relative path."""
    for candidate in (_clean_provenance_path(row_dir), launcher_dir):
        if candidate and os.path.isfile(_transcript_file(candidate, session_id)):
            return candidate
    return None


def _digest_one_session(tenancy, session_id, entry_source, transcript_dir, *,
                        registry_row=None, events=False):
    """Digest ONE ended-not-digested session under its own per-session flock.
    Wrapped so a bad/missing transcript or a mid-digest error for THIS session
    can never abort the sweep for the others. BUSY (a live digester holds the
    lock) -> skip, never reap; the session is picked up on a later sweep.

    `registry_row` (PL-A2a round 2, D7) is an OPTIONAL keyword: when the
    caller already has this session's parsed registry row in hand (`run_sweep`
    always does — it parses the whole registry once to build its candidate
    list), pass it here and this function uses it AS GIVEN, with zero extra
    disk I/O. When omitted (the pre-existing 4-positional call form, used
    directly by `test_ambient_spool_durability.py` and unaffected by this
    change), this function falls back to `_find_registry_row` exactly as
    before.

    `events` (T2-C3) is the OPT-IN event-skeleton flag, forwarded verbatim to
    `ambient_digest.digest_transcript_file`. It is a PARAMETER, never a config
    read here, so this function stays as testable as `digest()` itself is.
    Default False, so both pre-existing call forms (the 4-positional one and
    `run_sweep`'s) keep producing byte-identical spool rows until someone opts in.

    HALF OF THAT SENTENCE STOPPED BEING TRUE OF THE FUNCTION (2026-07-30 cleanup
    review), and it is corrected here rather than rewritten above. It remains
    exactly true of `events`: still a parameter, still resolved by the caller,
    still never read from config in here. It is NO LONGER true as a claim about
    the FUNCTION, because OPEN-1 F2 made this function resolve the transcript
    DIRECTORY itself — `_resolve_transcript_dir` consults the row, then the
    launcher's fallback, and decides between them by asking the filesystem
    whether `<dir>/<sid>.jsonl` exists. So the two per-row decisions now sit at
    two different altitudes: consent is resolved by the CALLER and threaded in,
    the directory is resolved by this CALLEE off disk state. That asymmetry is
    deliberate only in the weak sense that it is where the two fixes landed; if a
    third piece of row context ever arrives (see `run_sweep` on the delivery
    target), resolve it in `run_sweep` beside consent rather than adding a second
    precedent here.

    CORRECTION, OPEN-1 F4 (2026-07-30), recorded beside the claim it replaces:
    the paragraph above used to continue "`run_sweep` resolves it ONCE per sweep
    from the git toplevel it already has, so N sessions cost one resolution rather
    than N". That economy was the defect. The launching session's toplevel is not
    the toplevel of every row in a SHARED per-tenancy registry — two linked
    worktrees have one tenancy and two configs — so `run_sweep` now resolves the
    decision PER ROW, from that row's own recorded toplevel, memoized by toplevel
    so the common single-toplevel sweep still costs one resolution. This
    function's contract is unchanged: it still receives the answer, never derives
    it.

    NOTE the degraded path below deliberately does NOT pass `events`: a transcript
    that could not be READ has no ordered projection to offer, and
    `_degraded_rollup` is the one empty-digest shape the whole module funnels
    through.

    OPEN-1 F2: `transcript_dir` is now the LAUNCHER'S candidate, not the answer.
    The directory actually read is resolved by `_resolve_transcript_dir` from the
    ROW's own recorded provenance first, with this parameter as the legacy-row
    fallback, and no candidate holding the transcript takes F3's explicitly
    degraded path."""
    registry_path = _registry_path(tenancy)
    lock_dir = os.path.join(data_dir(), "insights", "locks")
    _ensure_private_dir(lock_dir)
    lock_path = _lock_path(tenancy, session_id)
    _ensure_private_file(lock_path)
    try:
        lock_fh = open(lock_path, "a+")
    except OSError:
        return

    acquired = False
    try:
        if _fcntl is not None:
            try:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                acquired = True
            except OSError:
                return  # BUSY -> a live digester holds it; skip, never reap
        else:
            acquired = True  # non-POSIX: best-effort, no real mutual exclusion

        # Re-check AFTER acquiring the lock: a concurrent sweep may have already
        # digested this session between our candidate listing and now (the
        # "clean sequential pass" interleaving of the two-sweep race).
        if _row_already_digested(registry_path, session_id):
            return

        # PL-A2a: started_at/ended_at are sourced from THIS session's own
        # registry row and copied VERBATIM into meta, so `ambient_digest.
        # digest` can echo them unchanged onto the spool row (AC1) exactly
        # the way it already echoes entry_source. Round 2 (D7): use the
        # caller-supplied `registry_row` AS GIVEN when present (no re-read);
        # only fall back to `_find_registry_row`'s own fresh disk read when
        # the caller did not already have the row in hand.
        if registry_row is None:
            registry_row = _find_registry_row(registry_path, session_id) or {}
        # OPEN-1 F1: `meta` is what the WIRE payload is built from, so it does NOT
        # gain either provenance field. The row's raw paths stop here.
        meta = {"session_id": session_id, "tenancy": tenancy, "entry_source": entry_source,
                "started_at": registry_row.get("started_at"), "ended_at": registry_row.get("ended_at")}
        # JC5 — the consent stamp rides `meta` for the SAME reason
        # `started_at`/`ended_at` do (PL-A2a): it is a fact about this session
        # recorded on ITS OWN registry row, and `meta` is the only channel from
        # the row into `ambient_digest.digest`'s output.
        #
        # ⚠️ THE BOUNDARY AT `:2030` — "`meta` is what the WIRE payload is built
        # from, so it does NOT gain either provenance field. The row's raw paths
        # stop here." — is respected in substance and widened in letter, and the
        # difference matters. That boundary exists to keep RAW PATHS off the wire;
        # a consent stamp is the opposite kind of field. It is ABOUT what may
        # cross, it carries no path, and it is worthless anywhere the wire cannot
        # see it. No path is added, so nothing the boundary was drawn against
        # moves.
        #
        # PROJECTED, never re-resolved: reading the config here would re-label the
        # row with whatever it says at SWEEP time — a detached background process
        # on a later session, often days after collection — which is precisely the
        # retro-relabelling §F.3 freezes the stamp to prevent.
        meta.update(ambient_digest.consent_row_fields(registry_row))
        # OPEN-1 F2: resolve the directory from the ROW's own recorded context
        # first, falling back to the launcher's only for a legacy row. Both the
        # transcript path and the sidecar glob come off the RESOLVED directory —
        # leaving the sidecar line on the parameter would digest one session's
        # main transcript alongside another session's sub-agents.
        resolved_dir = _resolve_transcript_dir(
            session_id, registry_row.get("transcript_dir"), transcript_dir)
        degraded_reason = None
        if resolved_dir is None:
            # OPEN-1 F3: NO candidate directory holds the transcript. Take the
            # EXPLICITLY degraded path rather than `digest_transcript_file`'s
            # silent empty — that function degrades a MISSING file to an empty
            # record_set on its SUCCESS path, so this case used to spool
            # `parserDegraded: False` with `rollups: []` and stamp `digested_at`,
            # which is the only re-attempt guard. The result was a clean-looking
            # zero, permanently indistinguishable on the wire from a session in
            # which the developer did nothing. A zero must not assert a
            # measurement the producer never made.
            #
            # `parserDegraded` is reused rather than a new key added because it is
            # ALREADY in every row and already on the wire: setting it True
            # changes a VALUE, not the key set, so `rawDigest` moves only for the
            # affected row — correct, since that row genuinely differs — and no
            # conformance fixture has to be re-sealed on either side of the wire.
            # The upshot is ONE degraded path instead of two, with the CAUSE kept
            # locally on the registry row.
            rollup = _degraded_rollup(meta)
            degraded_reason = _DEGRADED_TRANSCRIPT_MISSING
        else:
            try:
                rollup = ambient_digest.digest_transcript_file(
                    _transcript_file(resolved_dir, session_id), meta,
                    sidecars=_discover_sidecars(resolved_dir, session_id),
                    events=events)
            except Exception:
                # A transcript that EXISTS but fails to parse must still finalize
                # the session (never re-attempted forever) — spool an explicitly
                # degraded record rather than crashing the sweep. Same shape as
                # the missing case above; only the locally-recorded REASON differs.
                rollup = _degraded_rollup(meta)
                degraded_reason = _DEGRADED_TRANSCRIPT_UNPARSEABLE

        # Fix 1/2 (PL-A1b review): the spool is a durable drain queue, never a
        # bounded ledger, so it is appended via `_spool_append` (locked,
        # non-rotating, never swallows an IO failure) rather than
        # `_loop_ledger.append_row` (rotates past a 2000-row cap AND swallows
        # every append error). `digested_at` is stamped ONLY after a verified
        # successful append — a failed append must leave the row eligible so
        # the NEXT sweep retries it, rather than losing the rollup silently
        # while the registry claims it was already digested. See
        # `_spool_append`'s docstring for the resulting AT-LEAST-ONCE
        # semantics (a crash between append and stamp yields a duplicate on
        # the next sweep; A1c's outbox dedups by session_id on drain).
        spool = _spool_path(tenancy)
        try:
            _spool_append(spool, json.dumps(rollup))
        except Exception:
            return  # append failed -> stay eligible, retried on the next sweep
        _stamp_digested(tenancy, registry_path, session_id, _now_iso(),
                        degraded_reason=degraded_reason)
    finally:
        try:
            if acquired and _fcntl is not None:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            lock_fh.close()
        except OSError:
            pass


def _row_grants_skeleton(row, resolve_consent):
    """That ROW's skeleton eligibility, from ITS OWN recorded toplevel.

    FAIL CLOSED when the row records no usable toplevel (a row written
    before OPEN-1, or one whose value the validator rejected): a
    projection must never be inferred from somebody else's config. This is
    deliberately the OPPOSITE of `_resolve_transcript_dir`'s legacy
    fallback — losing a MEASUREMENT on upgrade is a regression, whereas
    inheriting a GRANT would emit data the company never enabled for that
    checkout. It is also the opposite of what the DELIVERY site does with
    the same unusable row (`run_drain`): there, "closed" would mean
    DESTROY, so it retains instead. That is why the shared resolver hands
    back `None` rather than a state — the policy is the caller's.

    Asks `event_skeleton_consent` and nothing else — never a re-derived
    conjunction. Reading CURRENT state (rather than a decision frozen at
    register time) is what preserves revoke-wins, which the suite pins
    elsewhere.

    JC5 — AND IT DELIBERATELY DOES NOT CONSULT CONSENT CLASS C, even though
    `events` is class C. §F.2 ANDs the two gates for SENDING, and the AND is
    applied once, at the delivery site (`run_drain`'s resolver), so there is one
    place the precedence between them is decided rather than two that can drift —
    which is exactly how capture and delivery came to disagree in the first place.
    THE COST IS STATED RATHER THAN LEFT TO BE DISCOVERED: a skeleton captured
    while class C is ungranted is built, spooled, and then never sent — it resolves
    INDETERMINATE at the drain, so the row is retained (never destroyed) and the
    spool grows with a doctor hint. That is this module's standing degradation —
    never drop, raise a hint — and not a new one; what it is NOT is a silent
    discard, which is the outcome a class check added here would risk if it were
    ever pointed at the destruction path by mistake.

    MODULE LEVEL, taking its `resolve_consent` as an argument, rather than the
    closure over `run_sweep`'s local it used to be. It captured exactly one name,
    and being unreachable from outside `run_sweep` meant ~20 lines of fail-closed
    POLICY that no test could drive directly and no mutation could target: the
    only way to observe it was to run a whole sweep and infer the answer from a
    spool row. It is the capture half of the asymmetry `skeleton_consent_resolver`
    documents, so it deserves to be assertable on its own."""
    resolved = resolve_consent(row.get("toplevel"))
    return resolved is not None and resolved[0] == EVENTS_CONSENT_GRANTED


def run_sweep(cwd, transcript_dir):
    """SessionStart-driven ambient sweep (PL-A1b): for `cwd`'s resolved tenancy,
    digest every registry row that is ENDED but not yet DIGESTED. Idempotent
    (an already-digested row is skipped) and never raises — a bad session, a
    missing transcript, or an unresolvable tenancy degrades to a no-op sweep,
    never a crash out of a background process a hook spawned detached.

    PL-A2a round 2 (D7): this function already parses the WHOLE registry once
    (right here) to build `candidates` — each candidate's own already-parsed
    row is carried through and handed to `_digest_one_session` via its
    `registry_row=` keyword, so that function never re-reads + re-parses the
    same registry file a second time per session (O(sessions x registry rows)
    of pure re-work at every session open, on the pre-fix tree).

    T2-C3: the event-skeleton flag is resolved HERE, exactly once per sweep, and
    threaded down. Two things about that resolution are load-bearing:

      * It asks `event_skeleton_consent`, and captures ONLY on GRANTED. It does
        NOT re-derive the conjunction, and that is the whole point: this line
        used to read `event_skeleton_enabled(toplevel) and not
        is_opted_out(toplevel)`, which was enable-OR across two config scopes,
        while the DELIVERY side had been made disable-wins. The two then
        disagreed, and the notice — which stated that `"event_skeleton": false`
        "stops BOTH halves: no new skeleton is recorded" — was true of delivery
        and false of capture. Reproduced end to end 2026-07-27: repo scope
        false, per-user scope true, and the sweep still spooled a skeleton. The
        second scope has since been removed entirely (enterprise policy), which
        retires that particular disagreement; asking ONE function is what stops
        the next one.
        A statement about capture has to be answered by whatever decides
        capture, and there must be exactly one of those.
        `event_skeleton_consent` is it — it is where revocation beats
        enablement, so asking it here is what makes that sentence true rather
        than nearly true.
        GRANTED still carries the conjunction (see that function): the skeleton
        is a strict SUBSET of ambient capture and must never outlive it, and
        this function does not re-evaluate the ambient gate — it digests every
        registry row that is ended-but-not-digested, whenever it was registered,
        so a repo that opted out AFTER those rows were registered would
        otherwise have the narrower opt-in override the broader opt-out and emit
        MORE data than before.
      * The git toplevel now comes from `_git_rev_parse` directly rather than
        from `resolve_tenancy`, which computes it and discards it. Same ONE
        subprocess as before (the pair is exactly why `_git_rev_parse` returns
        two values — see `run_drain`, which was combined for the same reason);
        going through `resolve_tenancy` and then re-shelling for the toplevel
        would re-introduce the per-field subprocess split it exists to avoid.

    CORRECTION, OPEN-1 F4 (2026-07-30). The two bullets above are kept because the
    argument they make is the one that has to be read to understand what changed —
    but "resolved ONCE per sweep and threaded down" is now WRONG, and the sentence
    "it is resolved ONCE per sweep" was itself the bug.

    THIS FUNCTION'S `cwd` IS THE LAUNCHING SESSION'S. The registry it sweeps is
    per-TENANCY, and a tenancy is the git COMMON DIR — so every linked worktree of
    one repo shares it while having its own toplevel and therefore its own
    `.fairmind-insights.json`. Resolving `event_skeleton_consent(launching
    toplevel)` and applying it to every ended-not-digested row was wrong in BOTH
    directions: a session from a granting worktree lost its skeleton when the
    sweep happened to start in a non-granting one, and — the direction that
    matters — a session from a worktree that never granted got a full ordered
    projection when the sweep started in one that did.

    So eligibility is now resolved PER ROW, from that row's own recorded
    `toplevel`, through the SAME single `event_skeleton_consent` function (never a
    re-derived conjunction — that is the class this feature has already shipped
    twice). A row with no recorded toplevel fails CLOSED.

    THE COST, STATED RATHER THAN GLOSSED: consent now varies WITHIN one tenancy.
    That is not a compromise, it is the correct model — the config file is a
    property of a checkout, and two checkouts can disagree. The per-sweep economy
    the old code bought survives as a memo keyed by toplevel, so the ordinary
    single-toplevel sweep still performs exactly one resolution. The bullet's
    other claim is untouched: the answer is still resolved HERE and threaded down
    as a parameter, so `digest()` stays contractually pure.

    The LAUNCHER'S toplevel is now used for NOTHING **IN THIS FUNCTION**. It is
    still unpacked because `_git_rev_parse` returns the pair on the one
    subprocess this function is allowed, and the common-dir half is what the
    tenancy needs.

    SCOPE CORRECTION (2026-07-30 cleanup review), because the sentence above
    used to stop at "NOTHING" and reads as a claim about the whole fix rather
    than about this function. IT IS NOT TRUE OF THE SWEEP AS A WHOLE. `cmd_sweep`
    calls this function and then `run_drain` in the SAME process with the SAME
    cwd, and `run_drain` still resolves `fairmind_delivery_target(cwd, toplevel)`
    from the LAUNCHER — so the launcher's context still selects the delivery
    ENDPOINT and BEARER TOKEN under which every row of the shared, per-tenancy
    spool is sent. Consequence, stated rather than left to be discovered: where
    two checkouts of one repo are registered against different Fairmind projects,
    a session captured in one can be delivered under the other's credential and
    host. The precondition was probed and does not hold on this machine (the two
    `~/.claude.json` entries with divergent urls are different REPOS, hence
    different tenancies and different spools), which is why this is recorded here
    rather than changed under a cleanup pass: generalising the delivery target to
    per-row is a behavioural change and belongs to its own gate, not to a
    docstring edit. `fairmind_delivery_target` is the last consumer of launcher
    context that still holds FULL AUTHORITY — it decides alone, with nothing from
    the row overriding it. Say it that way rather than "the third and last
    consumer", which a reader can fairly over-read as "the launcher is used
    nowhere else": the launcher's `transcript_dir` is still consulted below, as a
    SECOND-CLASS candidate that only wins when the row has none and the file is
    actually there. Two consumers remain, with different standing; only one of
    them can decide against the row.

    REGISTRY COMPACTION (2026-07-30) also runs here, once, after the digest loop
    — see `_compact_registry`. It belongs to the sweep and to nothing else: it
    reads the spool and rewrites the registry, which is affordable in this
    detached background process and is not affordable on the SessionStart hook's
    budget. This is the reclaiming half of the same fix that made
    `register_session`'s append unbounded; without it a file nothing trims would
    only grow.

    OPEN-1 F2, same shape one layer down: `transcript_dir` is likewise the
    LAUNCHER'S, and is now a FALLBACK candidate rather than the answer — see
    `_resolve_transcript_dir`. The transcript half of this defect needs only TWO
    CWDS IN ONE REPO (no worktree at all), because `~/.claude/projects/<slug>` is
    slugged from the cwd while the tenancy is slugged from the common dir."""
    try:
        _launch_toplevel, common = _git_rev_parse(cwd)
        tenancy = _tenancy_from_common(cwd, common)
        if not tenancy:
            return
        lines = _read_registry_lines(_registry_path(tenancy))
        if not lines:
            return
        # OPEN-1 F4: memoized per TOPLEVEL, not resolved once per sweep. The
        # ordinary single-toplevel sweep still costs exactly one resolution — the
        # economy the old code bought by asking the LAUNCHER is preserved without
        # asking the wrong repository. The memo lives in the SHARED
        # `skeleton_consent_resolver`, which `run_drain` asks too: this defect was
        # fixed here first and left standing there, so the machinery is now one
        # object rather than two implementations of one rule.
        resolve_consent = skeleton_consent_resolver()

        candidates = []
        for ln in lines:
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("ended_at") and not row.get("digested_at"):
                session_id = row.get("session_id")
                if session_id:
                    candidates.append((session_id, row.get("entry_source"), row,
                                       _row_grants_skeleton(row, resolve_consent)))
        for session_id, entry_source, registry_row, events in candidates:
            try:
                _digest_one_session(tenancy, session_id, entry_source, transcript_dir,
                                     registry_row=registry_row, events=events)
            except Exception:
                continue  # one bad session must never abort the sweep for the rest
        # AFTER the digest loop, deliberately: a row digested in THIS pass has
        # just been spooled, so it is still referenced and stays. Compacting
        # first would only ever look at a staler picture of the same file.
        _compact_registry(tenancy)
    except Exception:
        return
# The central plugin-policy door — the one GET among the doors above. Read by
# `run_policy_refresh` (detached sweep only); the gate and the judge Stop hook
# read the CACHE it fills, never this endpoint.
_CLIENT_POLICY_ENDPOINT_PATH = "/insights/v1/client-policy"


# Bounded read cap on a client-policy response body. The same idiom as
# `ambient_outbox._MAX_RESPONSE_BYTES`, but its own constant on purpose: that
# cap is sized for an ack body (`{"id": ...}`), while this body is a whole
# company policy map — 256 KiB is thousands of project entries, far past any
# real tenant, and still bounded against a hostile/oversized endpoint.
_POLICY_MAX_RESPONSE_BYTES = 262144

# Timeout for the client-policy GET. It runs ONLY in the detached, niced sweep
# process, so it costs no session-open latency; it exists so a black-holed
# endpoint cannot pin that process on urllib's unbounded default.
_POLICY_FETCH_TIMEOUT_S = 5

# TOTAL wall-clock budget for the whole GET — connect, headers and body.
# IT IS NOT A DUPLICATE OF THE TIMEOUT ABOVE: urllib's timeout is PER SOCKET
# OPERATION, so a server that dribbles one byte every four seconds resets it on
# every byte and never trips it (the byte cap does not help — the process is
# blocked, not full). Enforcement is in TWO layers because no single one
# covers the whole request: the body loop checks this deadline per chunk
# (`_read_within_deadline`), and the connect+header phase — which lives
# inside `_OPENER.open()`, where no deadline can reach — runs on a DAEMON
# worker thread that the caller `join`s against the same deadline, so a
# slow-loris during headers strands a daemon thread in a short-lived niced
# process, never the sweep itself. Generous against the 256 KiB cap on any
# link a session start would tolerate, and finite, which is the whole point.
_POLICY_FETCH_DEADLINE_S = 20.0

# The ONLY shape the server's `policies` map is keyed by: a 24-hex Mongo
# ObjectId. See `run_policy_refresh`'s project ladder for why the
# active-context fallback is filtered through it and the bearer claim is not.
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$")


def _policy_payload_is_servable(payload):
    """Whether a 200 body IS the client-policy document — not merely JSON, and
    not merely a dict.

    THE CACHE IS A BINDING ANSWER, so what may enter it is a closed shape:
    this module's payload version, plus both maps `resolve_central` reads. The
    failure it exists to stop is not a hostile server but an ORDINARY one: a
    gateway, WAF or load balancer answering `200 {"detail": "maintenance"}`.
    Cached, that body resolves to unset for every feature — so a company's
    forced-OFF would be replaced by a document that says nothing, and the local
    layer (default: ON) would decide for up to `POLICY_TTL_S`. Last-known-wins
    means the last known POLICY wins; a 200 that carries none is a failure, and
    failures leave the previous cache standing.

    Stricter than `resolve_central`, deliberately: that reader defaults a
    missing `companyDefault` to `{}` because it must survive whatever is
    already on disk, while this one decides what is allowed onto disk in the
    first place. A server that stops emitting either key stops refreshing the
    cache, loudly enough to notice — the fail direction a write-side guard
    wants."""
    return (isinstance(payload, dict)
            and payload.get("version") == _plugin_policy.PAYLOAD_VERSION
            and isinstance(payload.get("policies"), dict)
            and isinstance(payload.get("companyDefault"), dict))


def _drop_cache_from_another_origin(cache_file, policy_url):
    """THE FAILURE-PATH HALF of last-known-wins: keep the cache when the
    platform we still answer to is unreachable, DELETE it when this checkout
    now answers to somebody else.

    The cache is keyed by checkout path alone, so re-pointing a checkout at a
    different Fairmind project or company — a new MCP entry, a new bearer —
    lands on the SAME cache file. Without this, the previous company's force
    (its `forced_on` over the new company's opt-out, say) would keep applying
    until a fetch SUCCEEDED, which is exactly the window an unreachable
    platform makes long. A force must not outlive the credential that granted
    it: an origin that is absent (a cache written before this field existed, or
    by a hand) or different from the door we are talking to now is provenance
    we cannot vouch for, and it goes.

    Same-origin failures are untouched — that is the case last-known-wins was
    written for. A no-op when either argument is missing, because a failure
    BEFORE a credential resolves cannot tell whose cache this is: not knowing
    the current origin is not evidence it changed."""
    if not cache_file or not policy_url:
        return
    cache = _plugin_policy.read_cache(cache_file)
    if cache is None:
        return
    if cache.get("origin") == policy_url:
        return
    try:
        os.remove(cache_file)
    except OSError:
        pass


def _read_within_deadline(resp, cap, deadline):
    """Read at most `cap` bytes from `resp`, giving up at the monotonic
    `deadline`. Raises `TimeoutError` when the deadline passes with the body
    still arriving — a failure like any other, so the caller's failure path
    runs and the previous cache stands.

    `read1` RATHER THAN `read`, WITH A FALLBACK, and that choice is what makes
    the deadline real: `HTTPResponse.read(n)` blocks until it has all n bytes
    (or EOF), so a single call can absorb an entire trickle and the loop would
    never get to look at the clock. `read1(n)` returns whatever one socket read
    produced, so the deadline is checked between dribbles. The fallback keeps
    this working over any response object that does not implement it."""
    read_chunk = getattr(resp, "read1", None) or resp.read
    chunks = []
    remaining = cap
    while remaining > 0:
        if time.monotonic() > deadline:
            raise TimeoutError(
                "client-policy body exceeded the %ss wall-clock deadline"
                % _POLICY_FETCH_DEADLINE_S)
        chunk = read_chunk(min(remaining, 65536))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def run_policy_refresh(cwd, *, expected_target=None):
    """Refresh the central plugin-policy cache for `cwd`'s checkout — THE ONE
    PLACE THE POLICY ENDPOINT IS EVER FETCHED. Both consumers of the answer
    (`evaluate_gate`'s central step on the SessionStart fast path, and the
    judge Stop hook) read ONLY the cache this writes: no network, no new
    subprocess on either of their budgets. Runs in the detached `--sweep`
    process, so a slow endpoint costs a background process a few seconds,
    never a session open or a Stop.

    Endpoint + bearer resolve exactly the way the drain's do
    (`fairmind_delivery_target`): the per-project Fairmind MCP entry's own url
    and Authorization header, with the policy door DERIVED from that url
    (`_derive_insights_endpoint`) so the bearer can only ever be presented to
    the host that already holds it — and the GET goes through
    `ambient_outbox._OPENER`, whose redirect refusal is what keeps that
    sentence true against a 3xx (see `_RefuseRedirects`; a second opener here
    would be a second place that guarantee could silently not hold).

    DELIBERATELY NOT GATED by the ambient kill switch or the gate: a read is
    not capture, and a judge force must reach a tenant whose ambient lane is
    muted. No row, no payload, no local fact leaves the machine on this call —
    the request carries the bearer and nothing else.

    LAST-KNOWN-WINS, AND WHAT IT IS NOT. On any failure — no credential,
    connection error, non-200, a body that is not the policy document
    (`_policy_payload_is_servable`), a body still arriving at
    `_POLICY_FETCH_DEADLINE_S` — the existing cache is left byte-for-byte
    untouched, so a transient outage degrades to the last fetched policy until
    `_plugin_policy.POLICY_TTL_S` says it is stale, then to the local layer.
    The ONE exception is a cache from ANOTHER ORIGIN
    (`_drop_cache_from_another_origin`): "the last known policy" means the last
    one THIS door served, and a checkout re-pointed at a different Fairmind
    project keeps no force from the previous one. Never raises (detached
    process, nobody to hear it)."""
    # Both start unset so the failure funnel below is safe before either
    # resolves: `_drop_cache_from_another_origin` no-ops without them, because
    # not knowing the current origin is not evidence the origin changed.
    cache_file = None
    policy_url = None
    try:
        toplevel, _common = _git_rev_parse(cwd)
        if not toplevel:
            return
        endpoint, token = fairmind_delivery_target(cwd, toplevel)
        if expected_target is not None and (endpoint, token) != expected_target:
            return
        if not endpoint or not token:
            return
        policy_url = _derive_insights_endpoint(endpoint, _CLIENT_POLICY_ENDPOINT_PATH)
        if not policy_url:
            return
        cache_file = _plugin_policy.cache_path(toplevel)
        import urllib.request  # lazy: only this detached path pays the import
        import ambient_outbox  # lazy: ambient_outbox imports THIS module at top
        # Lazy for the SAME reason, and safe for the same one: this module is
        # imported on the SessionStart fast path, where insights_flush_payload
        # must never load — and the cycle does not close, because that module
        # imports THIS one lazily too (`_config_consent_classes`, in a
        # CLI-only helper).
        from insights_flush_payload import _active_context, _project
        request = urllib.request.Request(
            policy_url,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        # STAMPED BEFORE THE GET, and used twice below. It is the instant this
        # refresh's answer became "current"; anything already on disk that is
        # newer than it was delivered by a sibling refresh that started later
        # and finished first.
        start = datetime.now(timezone.utc)
        deadline = time.monotonic() + _POLICY_FETCH_DEADLINE_S
        # The connect+header phase happens inside `.open()`, where the body
        # loop's deadline cannot reach and the per-op timeout resets on every
        # dribbled byte — so the whole network half runs on a daemon worker
        # and the deadline is enforced from OUTSIDE with `join`. On expiry the
        # worker is abandoned (daemon: it dies with this short-lived sweep
        # process) and the refresh takes the failure path.
        outcome = {}

        def _fetch_within_deadline():
            try:
                with ambient_outbox._OPENER.open(
                        request, timeout=_POLICY_FETCH_TIMEOUT_S) as resp:
                    if resp.status != 200:
                        outcome["status"] = resp.status
                        return
                    outcome["raw"] = _read_within_deadline(
                        resp, _POLICY_MAX_RESPONSE_BYTES, deadline)
            except Exception as exc:  # noqa: BLE001 — carried to the caller
                outcome["error"] = exc

        worker = threading.Thread(
            target=_fetch_within_deadline, name="fm-policy-fetch", daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        raw = outcome.get("raw")
        if worker.is_alive() or raw is None:
            # Deadline expired mid-connect/headers, non-200, or a raised
            # transport error — all the same failure to the cache layer.
            _drop_cache_from_another_origin(cache_file, policy_url)
            return
        payload = json.loads(raw)
        if not _policy_payload_is_servable(payload):
            # A 200 that is not the policy document — a gateway's
            # `{"detail": "maintenance"}`, a version this plugin does not
            # speak. Caching it would REPLACE a binding force with a document
            # that resolves to unset, which is not what last-known-wins means.
            _drop_cache_from_another_origin(cache_file, policy_url)
            return
        # Project resolution ladder: the bearer's own `projectId` claim first
        # (selection only, never authz — see `project_id_from_bearer`), then
        # the checkout's `.fairmind/active-context.json` through
        # `insights_flush_payload`'s OWN reader and its three-spelling key
        # order — the function itself, not a copy of its ladder, so no writer's
        # spelling can zero the project here while it resolves there — else
        # None, and `resolve_central` answers from `companyDefault` alone.
        # `_active_context(toplevel)`, not `cwd`: both policy layers are
        # anchored at the git toplevel.
        project_id = _plugin_policy.project_id_from_bearer(token)
        if not project_id:
            # THE FALLBACK IS FILTERED, and the bearer claim is not. What
            # `/fairmind-loop` Phase 0 actually writes into `project` is the
            # REPOSITORY FOLDER NAME, while the server keys `policies` by
            # Mongo ObjectId — so an unfiltered fallback selects an entry that
            # can never exist and quietly binds nothing, which reads on disk
            # exactly like a project the platform has no policy for. Rejecting
            # a non-id leaves `project_id` null, and null is the documented
            # answer for a claimless bearer: `companyDefault` decides.
            candidate = _project(_active_context(toplevel))
            if isinstance(candidate, str) and _OBJECT_ID_RE.match(candidate):
                project_id = candidate
        # THE LAST WRITE MUST NOT BE THE OLDEST ANSWER. Two sessions opening
        # together spawn two detached sweeps; they can finish in either order,
        # and the loser would otherwise stamp an OLD payload with a NEW
        # `fetched_at` — rolling the cache backwards and refreshing its TTL
        # while doing it. Re-read here rather than at the top: the window that
        # matters is the one the GET spent open.
        #
        # The skip is bounded TWICE, and both bounds are the same rule — only a
        # SIBLING'S answer may win. (a) Same door: a newer cache from another
        # origin is not a sibling, it is the previous company's, and letting it
        # survive a SUCCESSFUL fetch would reopen the very window
        # `_drop_cache_from_another_origin` closes on the failure side. (b)
        # Inside the skew ceiling: a stamp years ahead is nobody's sibling, and
        # such a cache is already `unreadable` to the resolver — it must stay
        # overwritable or it wedges this checkout out of central policy for
        # good.
        existing = _plugin_policy.read_cache(cache_file)
        existing_stamp = _plugin_policy._parse_iso_utc(
            existing.get("fetched_at")) if isinstance(existing, dict) else None
        if (existing_stamp is not None
                and existing.get("origin") == policy_url
                and existing_stamp > start
                and (existing_stamp - start).total_seconds()
                <= _plugin_policy._MAX_CLOCK_SKEW_S):
            return
        written = _plugin_policy.write_cache(cache_file, payload, project_id,
                                             _now_iso(), origin=policy_url)
        return _plugin_policy.read_cache(cache_file) == written
    except Exception:
        # Last-known-wins for a failure against the SAME door; a cache from
        # another origin does not survive it (see the helper).
        _drop_cache_from_another_origin(cache_file, policy_url)
        return


def run_drain(cwd):
    """PL-A1c wiring: after the sweep spools, DELIVER this tenancy's pending
    rollups to the per-project Fairmind endpoint. Runs in the SAME detached,
    niced background process the SessionStart hook already spawns for the sweep,
    so it never touches the request/response hot path. Never raises (fail-open).
    No resolvable endpoint/token -> ``make_urllib_transport(None, …)`` is None ->
    ``drain`` is a spool-only no-op (rows stay durably queued for a later pass)."""
    try:
        # ONE `git rev-parse`, not two: this function needs the common-dir (for
        # the opaque tenancy) AND the toplevel (for the delivery-target lookup),
        # which is exactly the pair `_git_rev_parse` was combined to return.
        # Going through `resolve_tenancy` and then re-shelling for the toplevel
        # would re-introduce the per-field subprocess split it exists to avoid.
        toplevel, common = _git_rev_parse(cwd)
        tenancy = _tenancy_from_common(cwd, common)
        if not tenancy:
            return
        # `fairmind_delivery_target` returns either (None, None) or a pair that
        # is truthy on both sides, so no re-guard is needed here: a None
        # endpoint makes `make_urllib_transport` return None, and `drain` then
        # runs spool-only, leaving every row durably queued for a later pass.
        endpoint, token = fairmind_delivery_target(cwd, toplevel)
        import ambient_outbox  # lazy: ambient_outbox imports THIS module at top
        # Revalidate policy before a mute from a different endpoint can lapse.
        from urllib.parse import urlsplit
        parsed = urlsplit(endpoint or "")
        delivery_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}" if parsed.netloc else None
        claims = _plugin_policy.claims_from_bearer(token)
        # Identity is compared, never trusted as authorization. The fresh policy
        # response authenticates the current key; an old company's mute cannot
        # license moving a queued delivery to another company.
        identity = [claims.get("company"), claims.get("projectId") or claims.get("project_id")]
        delivery_identity = hashlib.sha256(json.dumps(identity).encode()).hexdigest() if identity[0] else None
        mute = ambient_outbox._load_state(tenancy).get("mute") or {}
        policy_verified = False
        if (delivery_origin and delivery_identity and mute.get("identity") == delivery_identity
                and mute.get("until") and mute.get("origin") != delivery_origin):
            policy_verified = run_policy_refresh(cwd, expected_target=(endpoint, token)) is True
        transport = ambient_outbox.make_urllib_transport(endpoint, lambda: token)
        # T2-C3: the event skeleton's own door, same origin by construction
        # (`fairmind_events_endpoint` swaps the PATH on the ALREADY-derived
        # activity url, so neither door is a separate configurable host) and the
        # SAME `token` provider. That construction bounds where the bearer is
        # SENT; it did NOT bound where it could END UP, because urllib follows a
        # POST 302 and carries `Authorization` to the new host — see
        # `ambient_outbox._RefuseRedirects`, which is what actually closes it. A falsy events endpoint makes
        # `make_urllib_transport` return None, which `drain` records per session
        # as `kind: "no_door"` instead of dropping the skeleton silently.
        events_transport = ambient_outbox.make_urllib_transport(
            fairmind_events_endpoint(endpoint), lambda: token)
        # T2-C3 (M3) — the DELIVERY side of the opt-in.
        #
        # WHY THE DRAIN NEEDS CONSENT AT ALL. Gating only the PROJECTION (in
        # `run_sweep`) stops new skeletons being built and does nothing about the
        # ones already on spool rows: with the flag off at both scopes, a session
        # digested while it was on still shipped its skeleton.
        #
        # THIS IS A THREE-STATE READ, not a boolean, and that is the whole point
        # of `event_skeleton_consent` — see its docstring. The boolean here used
        # to be `event_skeleton_enabled(...) and not is_opted_out(...)`, whose
        # False conflates "you revoked" with "I could not read your config", and
        # `drain` treats False as a licence to DISCARD. A transient bad JSON file
        # therefore destroyed skeletons captured while it was in force.
        #
        # AND IT IS RESOLVED PER SESSION, NOT ONCE FROM `toplevel` (OPEN-1 F4,
        # delivery half — 2026-07-30). `toplevel` here is the LAUNCHER'S, and the
        # spool this drains is per-TENANCY, i.e. the git COMMON DIR, which every
        # linked worktree of one repo shares while having its own toplevel and its
        # own `.fairmind-insights.json`. Applying one launcher's answer to the whole
        # shared spool was wrong in both directions, and the destructive direction
        # was reachable from `cmd_sweep` in ONE process: `run_sweep(cwd)` correctly
        # captured a GRANTING worktree's skeleton, then `run_drain(cwd)` on the very
        # next line resolved REVOKED from a SIBLING worktree's explicit `false` and
        # destroyed it — terminally, bytes reclaimed by compaction, with no
        # re-projection path once `digested_at` is stamped — and then recorded that
        # it had been "explicitly revoked in the repo config" for a checkout whose
        # config says `true`. Fixing the capture site alone made that WORSE than
        # before: pre-F4 the skeleton was never built, so nothing was destroyed and
        # nothing was misattributed.
        #
        # So the whole spool no longer gets one answer. `drain` is handed a
        # RESOLVER it asks per candidate session, and the resolver joins each
        # session back to its OWN toplevel through the registry (the spool row does
        # not carry one — see `_registry_toplevels`) and then through the SAME
        # `skeleton_consent_resolver` the sweep uses. `toplevel` keeps its ONE real
        # job here, the delivery-target lookup above.
        registry_toplevels = _registry_toplevels(_registry_path(tenancy))
        resolve_consent = skeleton_consent_resolver()
        resolve_classes = consent_classes_resolver()
        resolve_class_state = class_consent_state_resolver()

        def consent_classes_for(session_id):
            """This SESSION's LIVE class grant, from its own recorded checkout —
            the half of the stamp that is NOT frozen (§F.5).

            `None` for a session whose checkout cannot be resolved, which the
            builders read as "apply no narrowing": the row then ships with
            `classesApplied == classesAtCollection`. See
            `consent_classes_resolver` for why neither `[]` nor a fabricated
            all-three is acceptable there."""
            return resolve_classes(registry_toplevels.get(session_id))

        def events_consent_for(session_id):
            """This SESSION's skeleton state, from its own recorded checkout.

            A session with no usable recorded toplevel resolves INDETERMINATE —
            never REVOKED, and never the launcher's GRANTED. Both alternatives are
            wrong in the same way: the launcher's grant would send data on a
            decision made for a different checkout, and "fail closed" at the
            DELIVERY site means DESTROY. Indeterminate is the state that neither
            sends nor discards, so the skeleton waits on the spool and the durable
            record names the question that is actually open (which repo), not a
            config file that very likely reads perfectly."""
            top = registry_toplevels.get(session_id)
            resolved = resolve_consent(top)
            if resolved is None:
                return (EVENTS_CONSENT_INDETERMINATE,
                        (EVENTS_CONSENT_SCOPE_UNKNOWN_REPO,))
            state, scopes = resolved
            # JC5 — CLASS C AND THE `event_skeleton` SWITCH ARE TWO GATES OVER THE
            # SAME DATA (`events` is class C), so their precedence is decided HERE
            # rather than left undefined. The AND lives in this resolver and
            # nowhere else: `drain`'s events state machine already consumes a
            # three-valued answer per session, so combining before we hand it over
            # means there is no second consent branch inside `drain` to keep in
            # step — which is exactly how capture and delivery drifted apart the
            # first time.
            #
            # REVOKE-FIRST, mirroring `event_skeleton_consent`'s own branch order
            # and for the same reason: an explicit `false` must mean one thing
            # regardless of what else the file says. Testing GRANTED first would
            # resolve `{"event_skeleton": true, "consent": {"generation_context":
            # false}}` to "not both granted" -> INDETERMINATE, i.e. RETAIN, when
            # the company has explicitly said discard.
            #
            # ONLY AN EXPLICIT `false` CAN DISCARD — on EITHER switch. Everything
            # else that is not a grant is INDETERMINATE: neither sent nor
            # destroyed. And the row's FROZEN stamp is not consulted here at all;
            # a stamp is a label describing what a row was collected under, never
            # a switch, so it can never become a second way to destroy a captured
            # skeleton. See `class_consent_state` — asked here through the
            # per-drain memo, its two neighbours' shape, so one repo's config is
            # parsed once for the whole spool instead of once per session.
            class_state = resolve_class_state(top, "C")
            if EVENTS_CONSENT_REVOKED in (state, class_state):
                return EVENTS_CONSENT_REVOKED, ()
            if state == EVENTS_CONSENT_GRANTED and class_state == EVENTS_CONSENT_GRANTED:
                return EVENTS_CONSENT_GRANTED, scopes
            return EVENTS_CONSENT_INDETERMINATE, scopes

        ambient_outbox.drain(tenancy, transport, events_transport=events_transport,
                             events_consent_for=events_consent_for,
                             consent_classes_for=consent_classes_for,
                             delivery_origin=delivery_origin, policy_verified=policy_verified,
                             delivery_identity=delivery_identity)
    except Exception:
        return


def run_judge_stop_drain(cwd):
    """R21 wiring: deliver this CHECKOUT's spooled judge-stop rows.

    ITS OWN CALL, NOT A TAIL OF `run_drain`, and the separation is the point:
    the two lanes must not be able to starve each other. `run_drain`'s whole
    body is one `try` that returns on the first exception, so a judge-lane bug
    folded into it would stop the ambient rollups, and an ambient failure would
    stop the judge rows. They share nothing but the delivery target.

    PER CHECKOUT, NOT PER TENANCY, and that is deliberate. The rows are gated on
    the class-C consent of the `.fairmind-insights.json` that governs THEM, and
    a tenancy is shared by every linked worktree of a repository — each with its
    own toplevel and its own config. Resolving one launcher's answer over a
    shared spool is the OPEN-1 F4 defect the ambient lane already shipped and
    fixed (it destroyed a GRANTING sibling worktree's rows); a spool keyed by
    the toplevel cannot express it.

    REVOKE-FIRST AND THREE-VALUED, through the SAME `class_consent_state` every
    other class read here goes through — never a second implementation of it,
    and never the event-skeleton switch, which is ANDed with class C for the
    session transcripts and would make a judge row's fate depend on a decision
    about something else.

    Never raises: `judge_stop_lane.drain` swallows its own failures and this
    wrapper covers the resolution above it. A missing endpoint degrades to a
    spool-only no-op with every row still queued."""
    try:
        # Memoized in this process — `run_drain` above already paid for it, so
        # this costs no subprocess of its own.
        toplevel, common = _git_rev_parse(cwd)
        if not toplevel:
            return
        import judge_stop_lane  # lazy: stdlib-only leaf, but only the sweep needs it
        import ambient_outbox  # lazy: it imports THIS module at top
        endpoint, token = fairmind_delivery_target(cwd, toplevel)
        transport = ambient_outbox.make_urllib_transport(
            _derive_insights_endpoint(endpoint, judge_stop_lane.ENDPOINT_PATH)
            if endpoint else None,
            lambda: token)
        consent = class_consent_state(toplevel, judge_stop_lane.CONSENT_CLASS)
        judge_stop_lane.drain(toplevel, transport, consent_state=consent)
        # AND THE GIT DIR, under the SAME toplevel's consent. A stop issued
        # from inside `.git` is `not_a_work_tree` — git refuses `--show-toplevel`
        # there — so its row is filed under the common dir instead, and a drain
        # that only ever resolved a work tree would leave those rows on disk
        # for ever with no delivery path and nothing sweeping them. They belong
        # to THIS checkout, so they are governed by this checkout's config;
        # resolving it twice would be asking the same file the same question.
        git_dir = judge_stop_lane.canonical_common(cwd, common)
        if git_dir and git_dir != os.path.realpath(toplevel):
            judge_stop_lane.drain(git_dir, transport, consent_state=consent)
    except Exception:
        return


def notice_digest(text):
    """The identity of a notice's COPY — sha256 of the exact bytes a person was
    shown. Both markers store it and both readers compare it, so a correction to
    either notice re-reaches the tenancies that were shown the old one.

    WHY THE TEXT AND NOT THE VERSION. `plugin_version` was already recorded and
    was never compared, and comparing it would re-notify every tenancy on every
    release — including the releases that did not touch a word of the copy. A
    notice a reader has learned to scroll past is worth nothing, so the trigger
    is the only event that gives them something new to read: the bytes moved.

    It is deliberately NOT truncated. This value is never displayed, never
    transported and never used as a filename — nothing pays for its length — and
    a truncated digest buys a collision risk for no benefit at all."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def notice_needed(tenancy):
    """The one-time notice is due iff there is no VALID marker for THIS tenant
    RECORDING THE COPY THIS BUILD WOULD SHOW. A corrupt / unreadable /
    wrong-tenancy marker is NOT trusted as 'already shown' — it re-shows the
    notice (err toward TELLING the person, never toward SILENT capture: N3).
    Only a well-formed marker whose `tenancy` matches suppresses it, so a
    planted/other-tenant file cannot silence a genuine first-time notice.

    AND SINCE 2026-08-19 A MARKER SHOWING A DIFFERENT COPY DOES NOT SUPPRESS IT
    EITHER. Until then this compared `tenancy` ALONE while `record_notice`
    persisted a `plugin_version` nothing read, so a corrected notice was read
    only by tenancies that had never been notified — every reader already
    holding the wrong model kept it. That is what JC26 had to declare as
    unfixable from inside `notice_message`; it is fixed here instead, in the one
    place that decides whether anybody sees a correction.

    🔑 A MARKER WITH NO DIGEST RE-SHOWS, and it needs no branch of its own to do
    it: `.get` returns None, None never equals a sha, and the person is told.
    That is the N3 direction above arriving at the answer by itself, and it is
    the right one on the merits too — every marker written before this build is
    digest-less, and those are exactly the readers who were shown the copy JC26
    corrected. The cost is one extra notice, once, for a tenancy already
    notified; the alternative is a correction that by construction reaches
    nobody who needed it."""
    path = _notice_marker_path(tenancy)
    if not os.path.isfile(path):
        return True
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return True  # corrupt/unreadable -> re-show
    if cfg.get("tenancy") != tenancy:
        return True  # wrong-tenancy marker -> re-show
    if cfg.get("notice_digest") != notice_digest(notice_message()):
        return True  # different (or unrecorded) copy -> re-show
    return False


def record_notice(tenancy, version):
    """Persist the notice marker so the notice is not shown again UNTIL ITS COPY
    CHANGES — never re-shown was true until 2026-08-19, and the `notice_digest`
    written below is exactly what makes it no longer so (see `notice_needed`).
    Uses the
    shared `_atomic_write_lines` (mkstemp -> os.replace, so the file lands 0600);
    it does not create parent dirs, so ensure the marker dir exists first, locked
    owner-only (0700) like the sessions dir (N5).

    T2-C3 — WHY THE SECOND NOTICE GETS ITS OWN FILE. This function REWRITES the
    marker file WHOLESALE: `_atomic_write_lines` replaces the path with exactly
    the one line built below. Any additional key stored inside that file by
    someone else is therefore silently clobbered the next time this runs, so the
    event-skeleton notice could not be tracked as a flag alongside
    `notice_shown_at`. It uses a SIBLING marker instead — see
    `_events_notice_marker_path`."""
    path = _notice_marker_path(tenancy)
    _ensure_private_dir(os.path.dirname(path))
    _atomic_write_lines(path,
                        [json.dumps({"tenancy": tenancy, "notice_shown_at": _now_iso(),
                                     "plugin_version": version,
                                     "notice_digest": notice_digest(notice_message())})
                         + "\n"])


def _events_notice_marker_path(tenancy):
    """T2-C3's own notice-shown marker — a SIBLING of `_notice_marker_path`,
    never a key inside it, because `record_notice` rewrites that file wholesale
    (see its docstring) and would clobber anything added there. The `consent/`
    directory in the path is historical; see `_notice_marker_path`."""
    return os.path.join(data_dir(), "insights", "consent", tenancy + ".events.json")


def events_notice_needed(tenancy):
    """The T2-C3 notice is due iff there is no VALID events marker for THIS
    tenant. Identical discipline to `notice_needed`, for the identical reason
    (N3): a corrupt, unreadable or wrong-tenancy marker is NOT trusted as
    'already shown' — it RE-SHOWS the notice, erring toward telling the person
    and never toward silent capture, so a planted or other-tenant file cannot
    suppress a genuine first-time notice."""
    path = _events_notice_marker_path(tenancy)
    if not os.path.isfile(path):
        return True
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return True  # corrupt/unreadable -> re-show
    if cfg.get("tenancy") != tenancy:
        return True  # wrong-tenancy marker -> re-show
    if cfg.get("notice_digest") != notice_digest(events_notice_message()):
        return True  # different (or unrecorded) copy -> re-show
    return False


def record_events_notice(tenancy, version):
    """Persist the T2-C3 notice marker — not shown again until its copy changes,
    the same rule and the same `notice_digest` mechanism as `record_notice`.
    Same atomic write, same 0600/0700
    discipline, its OWN file — writing it never touches the ambient-capture
    notice marker and vice versa."""
    path = _events_notice_marker_path(tenancy)
    _ensure_private_dir(os.path.dirname(path))
    _atomic_write_lines(path,
                        [json.dumps({"tenancy": tenancy, "notice_shown_at": _now_iso(),
                                     "plugin_version": version,
                                     "notice_digest":
                                         notice_digest(events_notice_message())}) + "\n"])


def _content_notice_marker_path(tenancy):
    """JC6's own notice-shown marker. A THIRD sibling, for the reason the second
    one has: `record_notice` rewrites its file wholesale, so a flag stored
    alongside another notice's marker is clobbered the next time that notice is
    recorded."""
    return os.path.join(data_dir(), "insights", "consent", tenancy + ".content.json")


def content_notice_needed(tenancy):
    """The JC6 notice is due iff there is no VALID content marker for THIS
    tenant recording THIS copy. Same discipline as the two notices above and for
    the same reason (N3): corrupt, unreadable, wrong-tenancy or a different copy
    all RE-SHOW, so the error is always toward telling the person."""
    path = _content_notice_marker_path(tenancy)
    if not os.path.isfile(path):
        return True
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return True  # corrupt/unreadable -> re-show
    if cfg.get("tenancy") != tenancy:
        return True  # wrong-tenancy marker -> re-show
    if cfg.get("notice_digest") != notice_digest(content_notice_message()):
        return True  # different (or unrecorded) copy -> re-show
    return False


def record_content_notice(tenancy, version):
    """Persist the JC6 notice marker. Its own file, same atomic write, same
    0600/0700 discipline; writing it touches neither of the other two."""
    path = _content_notice_marker_path(tenancy)
    _ensure_private_dir(os.path.dirname(path))
    _atomic_write_lines(path,
                        [json.dumps({"tenancy": tenancy, "notice_shown_at": _now_iso(),
                                     "plugin_version": version,
                                     "notice_digest":
                                         notice_digest(content_notice_message())}) + "\n"])


# --------------------------------------------------------------------------- #
# The one-time notice copy (systemMessage — the HUMAN channel).
#
# BOTH NOTICES ARE NOTIFICATIONS OF A COMPANY DECISION, NOT CONSENT REQUESTS.
# Since 2026-07-27 neither switch reads anything the developer alone controls, so
# a reader has neither agreed to this nor been given a way to stop it. Copy that
# says "opt out any time" would be describing a switch they do not have — the
# exact defect class this feature has already shipped twice (a residual that
# claimed a protection the code did not provide, and an off switch that was a
# no-op). What replaces it is where to take the request instead.
# --------------------------------------------------------------------------- #

def notice_message():
    """Self-contained, unmissable copy (it renders alongside other startup output
    like '1 MCP server needs authentication'). Carries NO raw repo path / branch.

    IT NO LONGER PROMISES A PER-USER OPT-OUT, because there is no longer one:
    `is_opted_out` reads the repo-root file and nothing else. The old copy named
    `~/.fairmind/insights-config.json` as somewhere a person could switch this
    off for themselves, and that file is now inert — a disclosure naming a switch
    that does nothing is worse than naming none.

    AND IT DOES NOT SAY THE COMPANY SET IT "IN" THAT FILE, which the first draft
    of this rewrite did. Ambient capture is ON BY DEFAULT wherever a per-project
    Fairmind MCP is configured: `_config_disables` returns False for a MISSING
    path, so the ordinary armed repo has no `.fairmind-insights.json` at all
    (measured 2026-07-27: `repo=absent -> is_opted_out=False`, and the notice
    fires). Attributing the decision to the contents of a file that is usually
    absent is the same defect class as the off switch that was a no-op. What is
    true, and all this says, is that the SWITCH lives there and nowhere personal.

    IT NOW SAYS THE RECORDS LEAVE THE MACHINE, which it did not until
    2026-07-30. This is the ON-BY-DEFAULT lane, so it is the notice most readers
    ever see, and it announced what was collected while saying nothing about
    egress at all — an omission rather than a false claim, which is why it
    survived four rounds of correcting false ones. `cmd_sweep` calls `run_sweep`
    and then `run_drain` on the next line, in the detached, niced process the
    SessionStart hook spawns, and `fairmind_delivery_target` reads the url and
    the Authorization header off the project's OWN Fairmind MCP entry — so
    "a background process spawned when a session opens" and "the credential
    configured for this project" are both what the code does, not a gloss.

    AND IT NAMES TWO ROUTES, because the first draft of that egress sentence
    named the WRONG SENDER for three of the four record classes its own opening
    parenthetical lists. It read "Those records do not stay on your machine: a
    background process spawned when a session opens sends them…", whose subject
    was the whole list — harness-audit trends, agent decisions, loop/token/tool
    stats included. That background process sends `build_wire_payload` and
    nothing else, and that payload is a closed key set carrying no harness-audit
    run, no agent decision and no loop stats (`decisionsCount` is the literal
    `0`, its only occurrence in `ambient_outbox`; `ambient_digest` has no
    harness-audit concept at all). Those three egress by a DIFFERENT door and at
    a different time: `mcp__Fairmind__Insights_record_harness_audit` /
    `_record_agent_decisions` / `_record_loop_stats`, MCP calls the AGENT makes
    IN SESSION (`harness-audit.md:95`, `fairmind-loop.md:125`, the routing table
    in `fairmind-sync-insights.md`). So a reader asking the question the sentence
    exists to answer — when does my data leave, and what sends it — was told
    nothing leaves while they work. Reassuring direction, on timing, in the
    on-by-default lane. The fix is the SENDER attribution only: no content
    enumeration was added, because every new sentence is a new claim owing its
    own evidence and this notice's history is rounds eaten by exactly that.

    THE QUEUE CLAUSE IS SCOPED, AND THE SCOPE IS THE POINT. "while this project
    has no reachable Fairmind server the records queue on this machine and stay
    there" is the state `HEALTH_NO_DELIVERY_CREDENTIAL` names, word for word,
    and it is verified: `drain` returns a byte-for-byte no-op when
    `transport is None`, and `_compact_spool` keeps every retained row even over
    cap (probed — 20 undelivered rows survive a cap of 5, activity-only and
    skeleton alike). It is deliberately NOT the broader "an undelivered record
    always waits here until it can be sent", which is FALSE: a 413/422
    dead-letters terminally and compaction then reclaims the row (same probe —
    once dead-lettered, 20 of 20 gone). Writing the broad version would have
    been the reassuring reading of the code, which is how this notice's earlier
    false claims were written.

    AND IT DELIBERATELY STOPS THERE, one sentence short of the event-skeleton
    notice's WHERE IT GOES paragraph. That paragraph names the harness
    identifiers a row carries (`uuid`, `parentUuid`, `toolUseId`) and the
    transcript join they make possible; `build_wire_payload`'s key set carries
    none of them (sessionId, repoRef, entrySource, pluginVersion, started/
    endedAt, skills, toolCounts, agents, decisionsCount, the three drain
    counters, parserDegraded, rawDigest), so importing that detail here would be
    a false statement about THIS lane. Same discipline as every correction above:
    each notice claims what its own payload does — and it is that same rule,
    read against the sentence three paragraphs up rather than against this one,
    that the wrong-sender defect broke.

    AND THE SWITCH SENTENCE IS NOW SCOPED TO THE ROUTE THE SWITCH REACHES. It
    read "the switch is .fairmind-insights.json in the repository" — a bare
    definite article, in a notice whose own subject is BOTH routes, since the
    opening parenthetical lists the three classes the MCP route carries and the
    sentence four lines above it says that route is not the background process.
    So the copy handed the reader one file as the control over everything it had
    just described, and for the second route that is false. Measured on the
    shipped resolvers in a throwaway git repo carrying only
    `{"ambient_capture": false}`: `is_opted_out` returns True — the background
    process's digest stops for sessions opening AFTER it, and only those: a
    session already on the registry when the switch is flipped is still swept,
    still spooled and still drained (measured 2026-08-17 end to end through
    `--session-end` + `--sweep` and then `ambient_outbox.drain`, which called
    the transport once with the full session payload while `is_opted_out` was
    True) — while `run_gate_checks._resolve_consent_grant`
    returns `(['A', 'B', 'C'], 'legacy_config')`, i.e. the MCP route keeps every
    class it had. There is no gate for it to lose: a grep for `is_opted_out` and
    `ambient_capture` over `scripts/`, `commands/`, `hooks/`, `agents/` and
    `skills/` on 2026-08-17 returns hits in this module alone — none in
    `insights_flush_payload.py`, none in `loop_ledger.py`, none in the loop,
    audit or sync commands that make those MCP calls. (A survey of those five
    trees, not a proof over the plugin: re-run it rather than trusting the
    sentence.) THE CORRECTION IS THE SCOPE AND NOTHING ELSE. It does not say the
    file cannot REACH the second route, which would be a new falsehood — a
    `consent` block narrows what that route's record carries — and it names no
    control for that route, because naming one is a new claim owing its own
    evidence and this notice's register above is the running cost of writing
    those. The one addressable fact was already in the last sentence: who to
    ask. WHAT IS NOT FIXED HERE, stated because a scoped switch makes it easier
    to miss: the opening still files all four record classes under the heading
    "Ambient insight capture", and three of them do not travel that lane. The
    body distinguishes them by sender and now by switch; the heading does not,
    and correcting it is a rewrite of the sentence every pin on this notice is
    anchored to rather than a subtraction from it.

    AND IT NOW STATES THE REPOSITORY-IDENTITY ASYMMETRY BETWEEN THE TWO
    ROUTES. Nothing here was false, and the imprecise version of this
    correction would have to be retracted: the sentence a reader takes the
    wrong model from lives in `events_notice_message` — "an opaque one-way hash
    of this checkout's location — not your repository's name and not its path"
    — and is TRUE of that payload. The hole was SCOPE. This notice's opening
    parenthetical files harness-audit trends and agent decisions under its own
    heading, and then said nothing at all about what identifies the repository
    on that route, so a reader of both notices carried one lane's promise onto
    the other and neither notice contained a sentence they could be shown to
    have misread. What the second route actually sends, read off the producers
    rather than inferred: `insights_flush_payload.build_decisions_batches` emits
    `repository` (basename of the git toplevel) beside `git_remote`, and
    `build_audit_payload` emits the same pair off `run-meta.json`;
    `audit_run_meta.normalize_git_remote` strips `user[:pass]@` and keeps
    everything else, which is why the copy says credentials are stripped and
    "whatever host and path it names" are not. ⚠️ It deliberately does NOT
    promise a host and an organisation: that normalizer maps
    `file:///srv/Repo.git` to `https:///srv/Repo` and `Repo.git` to
    `https://repo.git` (measured), so a local or single-segment origin has
    neither — and a checkout with no origin sends no `git_remote` at all. An
    earlier draft of these sentences promised all three components
    unconditionally, which is the same overclaim shape as the paragraphs above,
    caught by an external review rather than by a test.
    `build_loop_payload` carries NEITHER — hence "the agent-decisions and
    harness-audit payloads" and never "what the agent sends", which would have
    been another overclaim of exactly the shape the paragraphs above keep
    correcting. `tests/test_ambient_notice.py` pins the claim against those
    three builders over one staged repo, so a producer that stops sending
    either field reds THERE and this copy is what changes.

    AND A CORRECTION TO THIS COPY NOW REACHES THE PEOPLE WHO WERE SHOWN THE OLD
    ONE — which it did not until 2026-08-19, when the paragraph standing here
    declared it a limitation no edit to this function could close. That was
    true, and the hole was in the MARKER: `record_notice` persisted a
    `plugin_version` that `notice_needed` never compared, so a tenancy shown an
    earlier revision was suppressed for good (measured, with a marker stamped
    `0.0.1-old-copy` against a current `plugin_version()` of 0.2.57:
    `notice_needed` still returned False). It is closed there instead —
    `notice_digest` persisted and compared — so a release that moves this copy
    re-notifies and a release that does not is silent. `events_notice_needed`
    got the same treatment in the same change, because fixing one lane would
    have re-created the two-lane asymmetry these sentences exist to remove; and
    a marker written before that build carries no digest, so it re-shows once,
    which is the N3 direction and also the only reading under which THIS
    correction reaches the readers it was written for.

    ⚠️ THE COST ESTIMATE THAT USED TO STAND HERE WAS WRONG, and it is kept
    because it is the kind of wrong that decides the next author's design: it
    named the version-differs variant as "the cheap version of the fix" and
    rejected it for re-notifying every tenancy on every plugin release — true of
    THAT variant, and an argument against a fix nobody would build. Rejecting a
    whole direction on the weakest member of it is the move to watch for."""
    return ("Ambient insight capture is ON for this Fairmind project "
            "(harness-audit trends, agent decisions, loop/token/tool stats). "
            "Those records do not stay on your machine, and they leave by TWO "
            "routes with different senders. A digest of each session is sent "
            "to a Fairmind server over the network by a background process "
            "spawned when a session opens. The harness-audit trends, agent "
            "decisions and loop/token/tool stats are sent DURING a session, "
            "by the agent itself, through this project's Fairmind MCP server "
            "— not by that background process. The two routes also IDENTIFY "
            "this repository differently. The digest names it by a one-way "
            "hash of this checkout's location — not the repository's name "
            "and not its path. The agent-decisions and harness-audit "
            "payloads do the opposite: they carry your repository's NAME "
            "and, when this checkout has an origin remote, that URL in "
            "plaintext — credentials are stripped out of it, and "
            "whatever host and path it names are not. "
            "Once you connect this checkout with /fairmind-connect, those two "
            "payloads name this repository by the identifier the platform's "
            "catalogue minted for it instead, and the digest's hash — "
            "unchanged on the wire, with no stored record rewritten — becomes "
            "something the platform can join to that same repository. "
            "Both routes use the Fairmind "
            "credential configured for this project, and while this project "
            "has no reachable Fairmind server the records queue on this "
            "machine and stay there. "
            "It was not your choice: the decision lives in "
            ".fairmind-insights.json in the repository, not a personal "
            "setting, and your own ~/.fairmind/insights-config.json is not "
            "read at all. That file's switch reaches ONE of the two routes: it "
            "governs the digest the background process sends, and it takes "
            "effect on sessions that open after it — a session already "
            "recorded when the switch is flipped is still digested and still "
            "delivered. It does not reach the other route at all: what the "
            "agent sends can be narrowed there, never switched off. To have "
            "any of this changed, talk to whoever owns this repository's "
            "Fairmind setup.")


def events_notice_message():
    """The SECOND one-time notice (T2-C3), shown once per tenancy the first time
    the event skeleton is in force. Longer than `notice_message` on purpose: that
    one announces an aggregate, this one announces a per-event ORDERED record,
    and the residuals below are the parts a reader would otherwise have to
    reverse-engineer from the projector.

    IT IS A NOTIFICATION, NOT A CONSENT NOTICE, AND SINCE 2026-07-27 IT SAYS SO.
    The enterprise policy the owner stated that day is that the COMPANY enables
    this centrally and the developer can neither disable it nor enable it for
    themselves; `event_skeleton_consent` and `is_opted_out` were narrowed to the
    committable repo file to make that executable. The copy had to follow. It
    used to open by telling the reader the switch might be theirs and close with
    two paragraphs of instructions for operating an off switch — under this
    policy the reader chose nothing and can turn nothing off, so BOTH were false,
    and "here is your off switch" is the single defect class this notice has now
    shipped twice (see the two histories below). The word "consent" is gone from
    the copy with them: what is described is a decision made ABOUT the reader,
    and calling it consent would be the same overclaim in the vocabulary rather
    than in a sentence. What replaces the off-switch section is where the
    decision actually lives and who to ask.

    NOTHING BELOW IS SOFTENED IN COMPENSATION. The D2 timing rule and every
    residual are unchanged in substance and unchanged in number, because the
    thing that changed is who decides, not what is taken.

    THE COPY WAS UNTRUE UNTIL 2026-07-27 AND THAT IS THE REASON THE RESIDUALS
    READ THE WAY THEY DO. It said deliberation before an approval was "genuinely
    removed". It is not removed — it is directly computable from the delivered
    rows, because a human verdict row sits between two CLOCKED agent rows and the
    interval between those two clocks IS the deliberation, to the second. The
    notice's own next paragraph already conceded the bracketing, so the copy
    contradicted the code AND itself. A notice claiming a protection the code
    does not provide is the most serious defect this feature can carry, so the
    residual states the real shape: what is removed is the person's own timestamp
    AS A STORED FIELD; when they acted and how long they took are not hidden. No
    "may in some cases be inferable" softening — it is subtraction.

    EVERY NUMBER WAS RE-MEASURED ON 2026-07-27, AFTER THE AskUserQuestion FIX,
    in ONE pass — because that fix moved 300 rows from agent to human and every
    figure below is computed over human rows or over clocked ones. Quoting the
    previous pass\'s numbers beside the corrected projector would have understated
    the deliberation residual by a factor of SIX, on the one claim in this copy
    that has already had to be corrected twice.

    PROVENANCE, since a survey number without it hides a scope error: 269
    sessions under `~/.claude/projects/*/*.jsonl` plus each session\'s
    `<sid>/subagents/agent-*.jsonl` (603 sidecars), 265 with a non-empty
    skeleton, 164,365 projected rows; driven through the shipped
    `ambient_digest.build_event_skeleton` and reading ONLY the projected rows —
    i.e. exactly what a holder of the delivered data can compute, not what the
    transcript happens to contain. Percentiles are NEAREST-RANK, stated because
    an earlier revision quoted p90/p99 with no method and they could not be
    reproduced from the corpus afterwards.

      * RESIDUAL 1/2 — DELIBERATION, defined as the `ts` delta between the
        NEAREST clocked agent row BEFORE a human row and the NEAREST clocked
        agent row AFTER it, within the same lane; n counts the human decisions
        where both brackets exist. THE WORD USED TO BE "IMMEDIATELY", AND IT
        NAMED THE WRONG DEFINITION FOR THE NUMBER BESIDE IT: this is the
        EXISTS-ANYWHERE reading — the same one THE BRACKET ITSELF below reports
        as 93.1% — and the strict adjacent-on-each-side reading gives n=338, not
        this n=357. That is the identical defect the bracket paragraph was
        corrected for on 2026-07-27 (a figure quoted under one definition while
        the prose implied the other), surviving one step away from its own fix,
        in the paragraph the corrected one points at. Nothing was re-measured to
        say this: the two paragraphs disagree with each other on the page.
        Over every explicit approval,
        denial AND ANSWER: n=357, p50=181.4s, p90=2,505.0s, p99=38,738.2s, MAX
        153,815.1s = 42.73 HOURS (70 under a minute, 30 over an hour). Split
        out: answers n=300 (p50 177.5s, p90 2,505.0s, max 42,639.2s), denials
        n=52 (p50 181.4s, p90 1,705.1s, max 153,815.1s), plan approvals n=5
        (p50 221.7s, max 526.5s). The corpus is live and drifts within a day, so
        this figure is dated and re-measured rather than remembered.
        ANSWERS ARE 84% OF THIS DISTRIBUTION and were entirely absent from it
        until 2026-07-27 — they were on the WRONG SIDE of D2, carrying the
        person\'s own clock, so they were not a residual at all but a direct leak.
      * RESIDUAL 3 — the tool_use -> tool_result interval, joined on `toolUseId`
        within a session: n=37,213, p50=1.1s, p90=9.8s, p99=133.0s, MAX 5,126.1s
        = 1.42 HOURS, 2.1% over 60s. A 1.4-hour tool execution is not a tool
        executing; a permission approval leaves no transcript record at all, so
        that interval contains the person\'s decision time and nothing
        distinguishes the two. This is the residual easiest to omit, and the
        strongest reason not to.
        AN EARLIER PASS PUT THIS MAXIMUM AT 42,635.7s — 11.8 HOURS — AND THAT
        NUMBER WAS THE BUG, not evidence of it: the "tool execution" it described
        was an AskUserQuestion result, i.e. a person taking 11.8 hours to answer
        a question, wrongly clocked as the machine. Fixing D2 moved it out of
        this distribution and into the one above, where it is now the answer
        class\'s maximum (42,639.2s). A residual measured through a broken
        classifier reports the classifier, not the residual.
      * THE BRACKET ITSELF — distance in ROWS from each human row to the nearest
        CLOCKED agent row: 2,357 human rows, 2,356 have such a neighbour, p50=1,
        mean 1.02, max 5; 98.3% sit exactly one row from a clocked row and 93.1%
        are bracketed on BOTH sides. BOTH SIDES MEANS A CLOCKED AGENT ROW EXISTS
        somewhere before AND somewhere after the human row inside its lane — not
        that the IMMEDIATELY adjacent row on each side is one. The definition is
        worth twenty points and is therefore stated rather than assumed: driven
        BOTH ways in ONE later pass the same day (2026-07-27 13:30 UTC, the same
        270 transcripts, 2,374 human rows by then — the corpus grows while it is
        read), exists-anywhere gives 2,211 = 93.1% and adjacent-on-each-side
        gives 1,736 = 73.1%, and the deliberation set narrows with it (n=338
        rather than 359, p50 unchanged, p90 2,792.5s). A later pass re-deriving
        this share under the other reading would look like a CORRECTION and
        would be a definition change.
        THE COPY STATES WHICH READING ITS NUMBER IS, and until 2026-07-27 it
        did not: "93.1% have one on each side" sat immediately after "ONE row
        away", so it read as the ADJACENT definition while quoting the
        EXISTS-ANYWHERE figure — twenty points apart. It erred toward
        OVER-stating exposure, so it is not the false-reassurance class the rest
        of this docstring is about; it is still a person being told a different
        quantity from the one they were reading. Both figures are in the copy
        now, each beside the definition that produces it.
        This is what makes the deliberation figure above computable at all, so
        the two are stated together. Both percentages ROSE despite 300 rows
        leaving the clocked pool, which is the shape to expect: the rows that
        lost their clock are themselves tightly bracketed.

    Also stated: switching this on projected any session that had ALREADY ENDED
    but was not yet digested (`run_sweep` finalizes every ended-not-digested
    registry row, and the ordinary case is the session immediately before this
    one). That is material and is NOT filtered — the honest fix is an
    `ended_at`-vs-enablement filter, which is a separate decision, so the fact is
    disclosed here instead of being quietly true. See also `run_sweep`.

    WHAT THE DELETED OFF-SWITCH SECTION USED TO SAY, AND WHY ITS REMOVAL IS NOT
    A LOSS OF DISCLOSURE. It told the reader to set `"event_skeleton": false` in
    the repo file or in the per-user one, and then spent a second paragraph on
    what did and did not discard already-captured rows. Every word of it
    addressed a reader who owns the switch. Under this policy they do not: the
    per-user scope is inert, and editing the committed repo file is a change to
    the repository, not a local preference. The retention/discard mechanics
    themselves are unchanged and still documented where the people who operate
    them read it — `event_skeleton_consent`, `ambient_outbox.drain`, and the
    `INTERNALS.md`. What a person subject to the decision needs is who made it
    and where to take a request, and that is what the copy now ends with.

    THE ONE THING NO COVERAGE SWEEP COULD HAVE FOUND MISSING is a fact that was
    never written, because the sweep derives its universe from the notice STRING.
    Two have been caught by reading the code instead of sweeping: that the
    skeleton is a strict SUBSET of ambient capture (branch 2 of
    `event_skeleton_consent` is `event_skeleton_enabled(toplevel) and not
    is_opted_out(toplevel)`, so no session ambient capture does not cover ever
    gets a skeleton — now stated in the opening paragraph), and, on 2026-07-27,
    that the reader had no off switch at all while the copy described one.

    AND TWO OVER-CLAIMS CAUGHT THE SAME WAY, IN THE FIRST DRAFT OF THIS VERY
    REWRITE, which is why they are named here rather than quietly fixed. It said
    the enabling file was "committed to the repository" — this module reads the
    FILE and never git, and an UNTRACKED one enables identically (probed
    2026-07-27). And it said "you cannot turn it off on this machine", which is
    broader than what the code enforces: what is enforced is that
    `~/.fairmind/insights-config.json` is not read AT ALL, in either direction,
    and that the only switch is a repository file. The copy now claims exactly
    that and stops. Writing a policy INTENT into a notice as though it were a
    code guarantee is how three of this notice\'s four corrections happened.

    WHERE IT GOES (2026-07-30) CLOSES SIX OMISSIONS AT ONCE, and they are the
    class the four earlier rounds could not reach: not false claims but facts the
    copy never stated, which no sweep can report because the sweep\'s universe is
    derived from the notice STRING. The claim->evidence register listed seven;
    six are now written — that the record leaves the machine, that the server
    attributes it via the project\'s own credential, that the rows carry the
    harness\'s native identifiers, that the envelope carries `sessionId` and
    `repoRef`, that `toolName` ships the full `mcp__<server>__<tool>` namespace,
    and that there is a verb for inspecting the record. The seventh (`lanes[].
    agentRole` is unbounded) is robustness rather than disclosure and stays
    recorded as deferred.

    THAT PARAGRAPH THEN SHIPPED FIVE DEFECTS OF ITS OWN, ALL FIVE FIXED
    2026-07-30 IN THE SAME SESSION THEY WERE FOUND, and they are named here
    because four of the five were held in place by PINS this same round added —
    a coverage pin whose needle is a string FROM the artifact makes the falsehood
    a REQUIRED disclosure, so correcting the copy turns the suite red and the
    cheapest green path is to put the falsehood back. (1) `repoRef` was called a
    "per-machine" key and has no machine component. (2) The verb sentence claimed
    the reader could see what had been CAPTURED and the verb reported delivery
    only — fixed in the VERB, which now counts the spool. (3) The same sentence
    was not runnable as typed: no interpreter, and the file is mode 0644.
    (4) "which MCP servers this project has connected" over-stated what a row
    reveals — only a server whose tools were actually called appears. (5) The
    egress claim was UNCONDITIONAL while a reachable state never delivers at all.
    AND THE FIX FOR (5) THEN SHIPPED A SIXTH, one step from its own fix and the
    fifth time on this track a rule-defect has survived that one step: it named
    the state it had found — no reachable Fairmind server — and asserted
    EXHAUSTIVENESS over the rest with the words "ONE EXCEPTION". There is a
    second, and it needs neither an unreachable server nor an edited config. A
    session whose registry row records no usable toplevel resolves
    INDETERMINATE under `EVENTS_CONSENT_SCOPE_UNKNOWN_REPO`, and INDETERMINATE
    neither sends nor discards: `_events_unresolved` keeps the row, drain after
    drain, with the door answering 200. Probed 2026-07-30 — a live
    `events_transport` returning 200, ZERO events calls, the skeleton still on
    the spool, and a second drain identical. The code's own comment beside that
    label already called it terminal ("There is no action that recovers the
    decision for such a row; the honest report is that the skeleton waits,
    unsent and undestroyed"), so the fact was written down one file away while
    the copy counted to one. It is also the state this notice points its reader
    AT: `register_session` stamps `toplevel` for the session starting now, so
    the rows that resolve INDETERMINATE are OLDER ones — exactly the
    already-ended-but-not-yet-digested sessions residual 5 tells them were
    projected when the switch went on. The correction drops the COUNT and keeps
    the named condition as an EXAMPLE ("THERE ARE EXCEPTIONS … for instance"):
    enumerating the second cause would be a new claim owing its own evidence,
    and broadening to "waits until it can be sent" is the false version this
    docstring already warns against below. Note the shape: the pin
    `("that there is an exception to the record leaving at all", "ONE
    EXCEPTION")` made the COUNT a required disclosure, so this was a sixth
    falsehood a pin was holding in place.
    Two of the five (1 and 4) came into the copy FROM the register\'s own wording
    of the omission, so the register propagated them instead of catching them;
    both were corrected on that page beside the original text. The lesson worth
    keeping is that closing an omission WRITES NEW CLAIMS, each owing its own
    evidence — a widening is not the safe kind of edit it appears to be, and this
    one produced a higher defect rate per sentence than any correction round
    before it.

    TWO SENTENCES ARE PHRASED AGAINST WHAT THE CODE CAN SUPPORT AND NOT AGAINST
    WHAT IT IMPLIES, because this notice has already shipped ten false claims and
    both would have been the eleventh:

      * ATTRIBUTION. Server-side attribution is real: the server takes the
        identity from the credential and stamps `userId`/`company` LAST, so a
        body value can never win. What NEITHER repo can answer is whether that
        credential is PERSONAL or SHARED — one team API key would make
        "attributed to YOU" false. "whoever that credential identifies" is true
        under both answers; the open question belongs in the register, not in the
        copy as an assumption.
      * THE JOIN. The transcript is never delivered (`_body_text` is never
        emitted and `_event_row`\'s key set is closed), so "join keys back into a
        transcript that holds everything" would OVER-state what the recipient can
        do. The sentence therefore names WHO can perform the join — someone
        holding the delivered rows AND this machine\'s transcripts — because an
        over-statement of exposure is still a correction (the bracket paragraph
        was corrected for exactly that on 2026-07-27), not a licence.

    AND `repoRef` IS CALLED AN OPAQUE KEY, NEVER A REPOSITORY IDENTIFIER
    (`test_events_conformance.py`, `repoRefScheme: "opaque-tenancy"`) — the wire
    carries neither the repository name nor its path, only
    `sha256(realpath(git-common-dir))[:16]`.

    AND IT IS NOT CALLED A "PER-MACHINE" KEY, WHICH IT WAS FOR ONE ROUND —
    the eleventh false claim, caught by the cross-model review on 2026-07-30 and
    corrected here. `_tenancy_from_common` is that hash and NOTHING else: no
    hostname, no uid, no `$HOME`, no salt (probed — the function body contains
    none of `gethostname`, `getuid`, `uname`, `getlogin`, `uuid.getnode`). So
    the value is per-ABSOLUTE-PATH, and the layouts where one path is shared by
    every user of a repo are the ORDINARY ones: a devcontainer at
    `/workspaces/<repo>/.git` and CI at `/home/runner/work/<repo>/<repo>/.git`
    resolve to one key for everybody (verified live: two identities, two cwds,
    byte-identical `fm-813e2e546194af6f`). "per-machine" therefore failed in the
    REASSURING direction — a reader infers the recipient cannot line their rows
    up with a teammate's on the same repository, and on those layouts it can,
    under the same key. The copy now states the collision positively rather than
    merely dropping the false word, because a reader who is told only "opaque"
    still assumes the narrower scope. NOTE WHERE THE WORD CAME FROM: the
    claim->evidence register's own description of the omission ("a stable
    per-repo, per-machine key"), so the register propagated it instead of
    catching it — the sibling of "a pin can protect a falsehood", one artifact
    up."""
    return (
        "Event skeleton capture is ON for this Fairmind project. "
        "\"event_skeleton\": true is set in .fairmind-insights.json at the repo "
        "root — a repository file, not a personal setting of yours, and nothing "
        "you put in ~/.fairmind/insights-config.json changes it. THIS TOOL "
        "CANNOT TELL YOU "
        "WHO SET IT: it reads that file and never git, so it knows the switch "
        "is on and nothing about who turned it on or whether it is committed. "
        "Under your organisation's policy that is a company decision and not "
        "yours to make. It rides on the ambient insight capture this project "
        "already has, so no session that capture does not cover is recorded "
        "here either.\n"
        "\n"
        "WHAT IS CAPTURED: the ORDER and STRUCTURE of a session — one row per "
        "assistant turn, tool call and tool result — with the model id, the "
        "tool NAME, token counts, and an error flag. NOT captured: conversation "
        "content, prompt or reply text, tool inputs, file paths, command text, "
        "diffs.\n"
        "\n"
        "WHERE IT GOES. This record does not stay on your machine. A "
        "background process spawned when a session opens here sends it to a "
        "Fairmind server over the network, using the Fairmind credential "
        "configured for this project — so the server attributes the record to "
        "whoever that credential identifies, not to an anonymous sender. THERE "
        "ARE EXCEPTIONS, and they are states this tool recognises rather than "
        "hypotheticals: while this project has no reachable Fairmind server, "
        "for instance, the "
        "record queues on this machine and stays there instead — captured, not "
        "sent, not discarded. The "
        "payload carries this session\'s id and repoRef, an opaque one-way "
        "hash of this checkout\'s location — not your repository\'s name and "
        "not its path. It is NOT scoped to you or to this machine: the same "
        "absolute path anywhere produces the same key, which is the ordinary "
        "case for a devcontainer or a CI checkout, so a teammate\'s rows can "
        "arrive under the very same repoRef as yours. Connecting this checkout "
        "with /fairmind-connect does not change that key or rewrite a record "
        "already delivered; what it adds is a binding the platform can follow "
        "from the key to the repository you bound it to. The "
        "rows carry the harness\'s own identifiers: uuid, parentUuid, "
        "toolUseId and a sequence position. Your transcript itself is never "
        "sent — but those identifiers are the ones your local transcript "
        "uses, so anyone holding both the delivered rows and this machine\'s "
        "own transcripts can line the two up exactly. Tool names are sent in "
        "full, and an MCP tool is named mcp__<server>__<tool>, so the rows "
        "also name any MCP server whose tools were actually called. To see what "
        "has been captured, queued, delivered, rejected or held, run python3 "
        "with the plugin\'s scripts/_insights_session.py and the "
        "--insights-status flag, from inside this repository — it reports "
        "whichever repository it is run in. That file ships in the "
        "fairmind-coding plugin directory of your Claude Code plugin cache.\n"
        "\n"
        "TIMESTAMPS: recorded for what the AGENT did. A row that originated "
        "from YOU — a prompt, an interrupt, an approval, a denial, an answer to "
        "a question the agent asked you — carries its position in the sequence "
        "and no clock at all.\n"
        "\n"
        "WHAT THAT DOES AND DOES NOT HIDE. Plainly, because the honest version "
        "is narrower than it sounds:\n"
        "1. Leaving the clock off your rows is a narrow thing. It means no "
        "field on your row says what time it was. It does NOT mean the record "
        "forgets when you acted — the ORDER is kept exactly, so a row of yours "
        "almost always sits between agent rows that ARE timestamped, and those "
        "brackets are tight. Measured on real sessions 2026-07-27: 98.3% of the rows "
        "that originated from a person are ONE row away from a timestamped "
        "agent row, and 93.1% have one on each side — counting a timestamped "
        "row anywhere before and anywhere after it in the same lane. Requiring "
        "the immediately adjacent row on both sides gives a stricter 73.1%. "
        "Your moment is bracketed, usually to within a single row.\n"
        "2. So the time you spent deliberating is NOT removed — it is "
        "computable. For an explicit approval, a denial, or an answer to a "
        "question the agent asked you, the gap between the NEAREST agent clock "
        "before your row and the NEAREST one after it BOUNDS how long you "
        "took, to the second. No inference, just subtraction, and anyone "
        "holding the delivered rows can do it. It is a bound and not a "
        "stopwatch reading, because that same gap also contains whatever the "
        "agent did either side of your row. Measured over 357 real "
        "approvals, denials and answers, 2026-07-27, those bounds were: half "
        "under 181.4 seconds, one in ten over 2,505.0 seconds, and the longest "
        "153,815.1 seconds — 42.7 hours.\n"
        "3. Approving a permission prompt leaves NO record in the transcript at "
        "all, so the interval between the agent calling a tool and its result "
        "can contain the time you spent deciding whether to allow it — and it "
        "is indistinguishable from the tool\'s own execution time. Measured "
        "2026-07-27 over 37,213 real intervals: half under 1.1 seconds, but "
        "2.1% over a minute and the longest 5,126.1 seconds — 1.4 hours.\n"
        "4. Agent timestamps also reveal WHEN a session was active at all, so a "
        "coarse working-cadence signal survives — roughly which days and hours "
        "you worked.\n"
        "5. Switching this on also projected any session that had already ended "
        "but was not yet digested — usually the one just before this.\n"
        "\n"
        "SO, WITHOUT HEDGING: what this shape avoids is writing YOUR clock into "
        "the record. It does not hide when you acted, and it does not hide how "
        "long you took. What it genuinely removes is your own timestamp as a "
        "stored field — not think time, not response latency, not deliberation "
        "before an approval, all of which the surrounding agent clocks still "
        "bound.\n"
        "\n"
        "IF YOU WANT THIS CHANGED, IT IS A COMPANY DECISION AND NOT A LOCAL "
        "SETTING. Your own Fairmind insights settings are not read at all: "
        "nothing you put in ~/.fairmind/insights-config.json switches this on "
        "or off, in either direction. Today the decision lives in this "
        "repository\'s own .fairmind-insights.json, so the people to raise it "
        "with are whoever owns this repository\'s Fairmind setup. In future it "
        "will be set centrally, for the whole organisation, on the Fairmind "
        "platform.\n"
        "\n"
        "ONE THING THIS NOTICE WILL NOT CLAIM. It does not tell you that you "
        "CANNOT stop this — you are running the tool on your own machine, and a "
        "notice that told you otherwise would be false. What is true is "
        "narrower: it is not a setting of yours, none of your own Fairmind "
        "insights settings reach it, and it is your organisation\'s decision "
        "rather than a preference this notice is offering you."
    )


def content_notice_message():
    """The THIRD one-time notice (JC6), shown once per tenancy while the content
    mode is in force. It announces the only channel in this plugin that records
    the TEXT OF YOUR CODE, so it is the one notice whose residuals cannot be
    left for a reader to infer.

    WHY A RUNTIME NOTICE AT ALL, when the design recommended documenting the
    switch instead: the person who reads the config file and the person whose
    code is captured are not the same person. The switch is a committed
    repository file — a company decision, exactly like the other two — and the
    developer whose working tree is being diffed may never open it. Owner
    decision, 2026-08-21, over the design's §8.6 recommendation.

    THE PRE-IMAGE RESIDUAL IS THE PARAGRAPH THIS NOTICE EXISTS FOR. A unified
    diff carries the lines a change REMOVED as well as the lines it added, so a
    secret that was in a tracked file and got deleted during the loop is in the
    capture — the deletion is precisely what puts it there. Measured: deleting a
    32,890-byte tracked file yields a 35,016-byte diff, 106% of the file, not
    half of it. Nobody would derive that from "we capture the diff".

    NOTHING HERE IS SOFTENED. There is no scrubber and this copy does not
    suggest one: a regex that finds some secrets and is described as protection
    is worse than an honest sentence, because it converts "nobody checked" into
    "checked and fine"."""
    return (
        "Failed-iteration capture is ON for this Fairmind project. "
        "\"content\": \"failed_iterations\" is set inside the consent block of "
        ".fairmind-insights.json at the repo root — a repository file, not a "
        "personal setting of yours, and nothing in ~/.fairmind changes it. THIS "
        "TOOL CANNOT TELL YOU WHO SET IT: it reads that file and never git, so "
        "it knows the switch is on and nothing about who turned it on. Under "
        "your organisation's policy that is a company decision and not yours to "
        "make; ask whoever owns this repository's Fairmind configuration.\n"
        "\n"
        "WHAT IS CAPTURED: when the loop gate REJECTS an iteration, the diff of "
        "the files that iteration changed, measured against the commit the loop "
        "started from — the actual text of the change that was refused. Also "
        "captured, and separately switched: this tool's own account of why it "
        "was refused, as check ids, verdicts and a fixed vocabulary of failure "
        "categories. NOT captured: your conversation with the model, prompt or "
        "reply text, the command text of your checks, and the output of those "
        "commands. A file git is NOT TRACKING is captured when the iteration "
        "created it — a new source file is exactly the work that was "
        "refused; what is never captured is a file git IGNORES, or a path "
        "your .gitattributes marks as -diff.\n"
        "\n"
        "A DIFF CARRIES WHAT WAS REMOVED. This is the part nobody derives from "
        "the sentence above: a unified diff records the deleted lines as well "
        "as the added ones, so a secret that was committed in a tracked file "
        "and got deleted during this loop is captured BY the deletion. Removing "
        "it from your working tree is what puts it in the record. There is no "
        "scrubber and this notice will not pretend otherwise — the exclusions "
        "that do work are per-path: git-ignored files are never included at "
        "all, and a path marked -diff in .gitattributes contributes no bytes.\n"
        "\n"
        "WHERE IT GOES: nowhere, today. It is written under ~/.fairmind on this "
        "machine and this build has no route that sends it — delivery is a "
        "separate, unbuilt change. Two consequences follow and both are "
        "deliberate. It OUTLIVES the repository: ~/.fairmind is outside your "
        "checkout, so git clean, deleting the branch and deleting the whole "
        "working copy all leave the capture where it is. And it GROWS: nothing "
        "drains or trims it. Run `python3 scripts/_insights_session.py "
        "--insights-status` from this repository to see how much is there, and "
        "the same file is what setting either class to false in that config "
        "deletes on the next REFUSED iteration or the next session "
        "start, whichever comes first."
    )


# --------------------------------------------------------------------------- #
# CLI entrypoints for the thin hooks. ALWAYS exit 0 (fail-open).
# --------------------------------------------------------------------------- #

def _read_payload():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        p = json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}
    return p if isinstance(p, dict) else {}


def _resolve_cwd(payload):
    """The cwd comes ONLY from the SessionStart/End payload (a confirmed harness
    field). We NEVER fall back to $CLAUDE_PROJECT_DIR or os.getcwd() to arm: a
    malformed/empty payload (-> {}) or one with no usable `cwd` field must make the
    hook NO-OP, not arm off the process/env cwd of whatever repo the session
    happens to open in (N2a). None -> the caller returns without touching state."""
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def _payload_transcript_dir(payload):
    """The directory holding THIS session's transcript, from the payload's own
    `transcript_path` — or None when the field is absent/unusable.

    ONE definition, asked by BOTH verbs the SessionStart hook feeds the SAME
    payload to (`--session-start`, which records it as the row's provenance, and
    `--sweep`, which uses it as the launcher's fallback dir). Two spellings of
    "where is the transcript" is exactly the drift OPEN-1 is repairing, one layer
    up: the two must be the same string or the row's provenance and the sweep's
    fallback could disagree about the same session. Never guesses off env/getcwd,
    mirroring `_resolve_cwd`'s N2a discipline."""
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    return os.path.dirname(transcript_path) or None


def cmd_session_start():
    """SessionStart: fresh-evaluate the gate; on capture, register the session and
    (first time only) emit the one-time notice as strict JSON on stdout. There
    are TWO such notices — the ambient one, and the T2-C3 event-skeleton one
    shown only while `event_skeleton_consent` says GRANTED — and they ride ONE
    JSON object, joined; each is recorded separately, so either can be due
    without the other. A virgin / non-Fairmind / opted-out session WRITES
    nothing.

    ⚠️ IT NO LONGER PRINTS NOTHING, and the sentence that said so is corrected
    here rather than deleted, because "prints nothing" was true of every earlier
    revision and a reader may be carrying it. One message can now reach a
    NON-capturing session: the JC8 stale-marker line, which reports the state of
    a file inside the user's own repository and is the one thing here that is not
    about ambient capture at all (see `stale_loop_marker`). The WRITE half of the
    guarantee is unchanged and is what the gate still protects — no registry row,
    no notice marker, no health marker, nothing under the per-user data dir."""
    payload = _read_payload()
    cwd = _resolve_cwd(payload)
    if not cwd:
        return  # N2a: no usable cwd in the payload -> no-op, never arm off env/getcwd.

    # 🔴 READ BEFORE THE GATE, AND THE ORDER IS THE WHOLE POINT OF THE
    # RESTRUCTURE. Everything below used to sit behind `if not decision.capture:
    # return`, and `evaluate_gate` answers False in every repo with no
    # per-project Fairmind MCP. `/fairmind-loop` is explicitly STANDALONE
    # (loop_open.py:61), so a repo can sit on a stale marker for months while that
    # channel is permanently silent — the signal would have been unreachable in
    # precisely the repos that most need it.
    #
    # NOTHING ELSE MOVED ACROSS THE GATE. This probe reads two files and at most
    # one directory inside the user's own repository, writes nothing anywhere,
    # and involves no consent, capture or delivery decision: no Fairmind request,
    # no spool row, no marker.
    #
    # ⚠️ "AND NO BYTE LEAVES THE MACHINE" STOOD HERE AND WAS FALSE — a cross-model
    # review caught it, and the correction matters because this is the ONE claim
    # that justifies emitting above the gate. What this returns is rendered as a
    # `systemMessage`, and a `systemMessage` becomes MODEL CONTEXT (this repo
    # measured that channel itself — see the internal log's FD-F3), so the
    # cleaned loop ref and the row count travel with the next prompt to the model
    # provider like any other prompt text. What is true, and what the gate
    # actually protects, is narrower and worth stating exactly: nothing reaches
    # FAIRMIND, and nothing is persisted. The content is bounded and repo-local
    # by construction — a `_clean_loop_ref`-sanitized task ref, capped at 80
    # characters, plus integer counts — and it is the same channel the notices
    # already used; what is new is that it now fires where capture is OFF.
    #
    # The `capture` branch below still owns everything the early return
    # protected — no registry row, no notice marker, no health marker — and those
    # are now guarded by the branch rather than by the function having already
    # returned.
    stale = stale_loop_marker(cwd)

    messages = []
    decision = evaluate_gate(cwd)
    # Declared before the branch because the emit below is SHARED with the
    # non-capturing path: on `capture=False` these keep the values that make
    # every recording step below a no-op, which is the early return's old
    # guarantee expressed as state instead of as control flow.
    events_due = False
    findings = ()
    changed = False
    if decision.capture:
        # OPEN-1 F1: the row records the context it was captured under. Both
        # values are already in hand here — `decision.toplevel` came off
        # `evaluate_gate`'s one-and-only `git rev-parse`, and the transcript dir
        # off the same payload the hook forwards to `--sweep` — so this costs no
        # extra subprocess and no extra read on the SessionStart fast path.
        register_session(decision.tenancy,
                         payload.get("session_id"),
                         _now_iso(),
                         payload.get("source"),
                         toplevel=decision.toplevel,
                         transcript_dir=_payload_transcript_dir(payload))

        # One-time notice per (user, repo) on the HUMAN channel. Print FIRST so a
        # crash between print and record re-shows (twice) rather than never.
        #
        # T2-C3: the event-skeleton notice rides the SAME single JSON object. The
        # hook's contract is one strict-JSON document on stdout, so two
        # `sys.stdout.write(json.dumps(...))` calls would emit two concatenated
        # objects and the harness would parse neither — the messages are JOINED
        # into one `systemMessage` instead.
        #
        # WHY HERE AND NOT IN THE SWEEP, where the skeleton is actually built: the
        # sweep runs in a detached, niced background process whose stdout the hook
        # sends to /dev/null (`... --sweep >/dev/null 2>&1 &`), so a notice printed
        # there reaches nobody. This is the only channel a human sees. The
        # consequence, stated so it is a decision rather than an accident: the
        # notice prints at session start and the sweep that projects skeletons is
        # spawned immediately after it — the person is told before the capture it
        # describes, but by one process spawn, not by a session.
        if notice_needed(decision.tenancy):
            messages.append(notice_message())
        # Asks the SAME question `run_sweep` asks — `event_skeleton_consent` — and
        # not `event_skeleton_enabled`, which is a bare single-key read: it answers
        # True for `{"ambient_capture": false, "event_skeleton": true}`, where the
        # skeleton is in fact OFF (it cannot outlive ambient capture), so the notice
        # would announce capture that is not happening. One function answers "is the
        # skeleton on", and every site that acts on the answer — capture, delivery,
        # and this notice — asks it rather than re-deriving it, which is precisely
        # how capture and delivery came to disagree.
        events_due = (events_notice_needed(decision.tenancy)
                      and event_skeleton_consent(decision.toplevel)[0]
                      == EVENTS_CONSENT_GRANTED)
        if events_due:
            messages.append(events_notice_message())
        # T2-C4 — the health findings ride the SAME object, for the same reason the
        # second notice does: the hook's contract is ONE strict-JSON document on
        # stdout. This is the only channel a human sees, and the two states it
        # reports (nothing is being recorded / nothing can be delivered) were
        # previously observable only by running a verb nobody runs.
        #
        # ONE-TIME PER FINDING SET, not per session. Repeating it every startup
        # would be spam, and spam next to a privacy notice devalues the notice as
        # well as itself; suppressing it forever would repeat the defect. See
        # `health_changed`.
        findings = insights_health(cwd, decision.toplevel, capture=True)
        changed = health_changed(decision.tenancy, findings)
        if changed and findings:
            messages.append(health_line(findings))

    # JC6 — THE THIRD NOTICE, AND THE ONLY ONE OUTSIDE THE `capture` BRANCH.
    # That placement is the whole point rather than an oversight: `decision
    # .capture` is the AMBIENT gate, and content capture rides the LOOP lane,
    # which has never had an ambient master switch. A repo that sets
    # `"ambient_capture": false` and opts into failed-iteration capture is
    # capturing the text of its code while every notice inside that branch is
    # unreachable — the exact shape of "silent capture" the N3 rule exists to
    # refuse. Gated on its own two conditions instead: a resolvable tenant, and
    # the content mode actually in force for this checkout's root.
    content_due = (decision.tenancy
                   and content_mode_granted(decision.toplevel)[0]
                   == CONSENT_CONTENT_MODE_FAILED_ITERATIONS
                   and content_notice_needed(decision.tenancy))
    if content_due:
        messages.append(content_notice_message())
    # Reclamation, HERE, because this is the one hook that already holds both a
    # tenancy and a toplevel and can therefore act on a revocation for free. The
    # gate's capture path reclaims too, but only on a RED evaluation — a loop
    # that revoked a class and then went green forever would otherwise keep the
    # bytes it asked to have deleted. It runs whatever the ambient gate says,
    # for the reason the notice does: content capture is not the ambient lane.
    if decision.tenancy:
        try:
            reclaim_content(decision.tenancy, decision.toplevel)
        except Exception:  # noqa: BLE001 — never fail a session start
            pass

    # LAST, so the one-time notices keep the top of the block: this line is the
    # only one here that can print on many consecutive sessions, and the notices
    # are the messages a reader must not learn to scroll past. It is computed
    # above the gate and rendered here — the read and the placement are separate
    # decisions.
    #
    # RESIDUAL, stated rather than wrapped away: there is exactly ONE emit, so an
    # exception raised inside the capture branch above still costs this line
    # (`main`'s catch-all swallows it and the hook exits 0 silently). Wrapping
    # that branch would buy the line at the price of changing how every existing
    # failure on the capture path behaves, which is a larger change than the
    # signal is worth.
    stale_line = stale_loop_line(stale)
    if stale_line:
        messages.append(stale_line)

    if messages:
        _hook_line.emit("SessionStart", user="\n\n".join(messages))
        sys.stdout.flush()
        if decision.capture:
            if notice_needed(decision.tenancy):
                record_notice(decision.tenancy, plugin_version())
            if events_due:
                record_events_notice(decision.tenancy, plugin_version())
        # Outside the branch above, mirroring where it was DECIDED: a notice
        # recorded only when ambient capture happens to be on would re-show
        # forever on exactly the repos that reach it, and the marker is what
        # makes "once" true.
        if content_due:
            record_content_notice(decision.tenancy, plugin_version())
    # Recorded whether or not anything was PRINTED, and outside the `if messages`
    # block on purpose: a finding set that CLEARED prints nothing and must still
    # be recorded — see `health_changed`.
    if decision.capture and changed:
        try:
            record_health(decision.tenancy, findings)
        except Exception:
            pass


def cmd_session_end():
    """SessionEnd: stamp ended_at on the matching row. No gate re-eval needed —
    mark_session_end no-ops unless the registry (hence a captured session) exists,
    so a never-captured repo writes nothing."""
    payload = _read_payload()
    cwd = _resolve_cwd(payload)
    if not cwd:
        return  # N2a: no usable cwd -> no-op (never resolve tenancy off env/getcwd).
    tenancy = resolve_tenancy(cwd)
    if not tenancy:
        return
    mark_session_end(tenancy, payload.get("session_id"), _now_iso())


def cmd_sweep():
    """--sweep: the ambient digester's SessionStart-spawned entrypoint (PL-A1b).
    Reads the SAME SessionStart payload shape as cmd_session_start (the hook
    forwards it a second time to a detached, niced background process) and
    derives `transcript_dir` from the payload's own `transcript_path` — the
    harness-provided path to THIS session's transcript file. A payload missing
    either field no-ops (never guesses a transcript dir off env/getcwd,
    mirroring the N2a discipline of the other commands).

    CORRECTED 2026-07-30 (cross-model pre-PR review): that sentence used to end
    "whose dirname is where EVERY SIBLING SESSION'S transcript (and subagent
    sidecars) also live". It does not, and the counter-example is OPEN-1 itself.
    `~/.claude/projects/<slug>` is slugged from the **cwd**, while the tenancy is
    hashed from the git common dir — so sessions opened at the repository root
    and in a subdirectory share ONE registry and have TWO transcript directories.
    Assuming otherwise is exactly the defect this change exists to fix, and the
    assumption was still written down two lines from the fix. What this dirname
    actually holds is every sibling session launched from THE SAME CWD; that is
    why the sweep now routes on the ROW's recorded directory and treats this one
    as a fallback.

    NARROWED with the central-policy refresh: "a payload missing either field
    no-ops" now holds for the DIGEST + DRAIN only. A payload with a usable
    `cwd` but no `transcript_path` still runs `run_policy_refresh` — the fetch
    needs only the checkout, and the judge Stop hook reads ONLY the cache it
    fills, so tying the policy to a field the policy never uses would let a
    payload shape starve an unrelated feature. The N2a discipline is intact:
    nothing here guesses a transcript dir (or a cwd) off env/getcwd."""
    payload = _read_payload()
    cwd = _resolve_cwd(payload)
    if not cwd:
        return
    transcript_dir = _payload_transcript_dir(payload)
    if transcript_dir:
        run_sweep(cwd, transcript_dir)
        # PL-A1c: deliver what the sweep (and prior sweeps) spooled. Same
        # detached, niced process; fail-open and a spool-only no-op when no
        # endpoint resolves.
        run_drain(cwd)
        # R21: the judge-stop lane, delivered beside the rollups and isolated
        # from them — see `run_judge_stop_drain` for why it is not folded in.
        run_judge_stop_drain(cwd)
    # Central-policy refresh, LAST on purpose: a slow policy endpoint (up to
    # _POLICY_FETCH_TIMEOUT_S) must never delay what this detached pass exists
    # to deliver. Failure leaves the previous cache standing (last-known-wins).
    run_policy_refresh(cwd)


def _forced_project_label(cache, source, company_default="company default"):
    """WHICH ENTRY of the policy cache THIS FORCE came through:
    `"project <id>"` when the project entry answered, `company_default` when
    the company default did.

    ONE label, two readers — the `--insights-status` block and the
    `--set-policy` refusal — because the two sentences differ only in their
    surrounding words, and a project a status line names while a refusal calls
    it something else is a drift a customer would report as a bug. The
    company-default half stays a PARAMETER: the two sentences take it in
    different grammatical positions ("centrally forced (company default)" vs
    "forced off for the company default"), and bending either to fit the other
    would change a message no cleanup was asked to touch.

    `source` COMES FROM THE RESOLVER (`resolve_central_with_source`) AND IS NOT
    OPTIONAL, because the cache alone cannot answer this and used to be asked
    anyway: `project_id` records which entry was looked UP, and resolution
    falls through to `companyDefault` PER FEATURE — so a cache carrying a
    project id, whose project entry does not name this feature, printed
    "centrally forced (project X)" over a value the company default decided.
    A label that is true-looking and wrong is worse than a vaguer one: it sends
    a customer to change a project setting that was never involved."""
    project = cache.get("project_id") if isinstance(cache, dict) else None
    if source == "project" and isinstance(project, str) and project:
        return f"project {project}"
    return company_default


def _policy_status_lines(toplevel):
    """ADDITIVE `--insights-status` lines (existing lines carry substring pins
    in tests/test_t2c3_optin_delivery.py — this block only ever appends): for
    each policy feature, the EFFECTIVE state and WHICH LAYER decided it —
    centrally forced (project …) / repo file / default — plus the central
    cache's freshness. Empty when no toplevel resolved (the gate line above
    already says why nothing applies).

    "default" is each feature's shipped posture where no layer speaks: judge
    ON (advisory — only an explicit boolean `false` under the repo file's
    `judge` key silences it, the mirror of the `is True` idioms elsewhere) and
    ambient capture ON-where-configured. The judge line reports the POLICY
    layers only: the `FM_JUDGE_HOOK` env escape hatch beats central, but it is
    per-process and this verb cannot see the environment of the session that
    matters, so naming it here would be a guess dressed as a fact.

    The forced line names the project the CACHE resolved for, via
    `_forced_project_label` — see there for why it is the entry rather than the
    half that answered. The freshness label comes from
    `_plugin_policy.cache_freshness`, THE resolver's own rule rather than a
    copy of its arithmetic: a hand-copied `> ttl` mirror here used to print
    "(fresh)" over a `ttl_s: Infinity` cache that `resolve_central` was
    treating as unset, which is precisely the sentence this block exists to
    stop a reader believing. It stays display-only — the resolver's answer, not
    this label, is what the gate obeys — but it can no longer say the opposite
    of it."""
    if not toplevel:
        return []
    now = datetime.now(timezone.utc)
    cache = _plugin_policy.read_cache(_plugin_policy.cache_path(toplevel))
    cfg_path = consent_config_path(toplevel)
    file_present = os.path.isfile(cfg_path)
    cfg = _read_json(cfg_path) if file_present else None
    cfg = cfg if isinstance(cfg, dict) else None

    def forced_src(source):
        """The attribution phrase for a force, per FEATURE — not per cache.
        Which half answered can differ between any two features of one cache
        (a project entry that names `judge` and not `ambient_capture` takes the
        company default for the second), so this cannot be computed once above
        the block the way it used to be."""
        return f"centrally forced ({_forced_project_label(cache, source)})"

    lines = ["Plugin policy:"]

    # The TRI-STATE features share one ladder: central force, then the repo
    # file's own key, then the on-by-default floor. `ambient_capture` below is
    # deliberately NOT folded in — it reads `file_present`/`is_opted_out`
    # instead of `cfg`, and that difference IS its fail-closed rule.
    # ⚠️ BOTH lookups take `key`, the CONFIG key — never `cli`, the command-line
    # word. They are equal for `judge` and `brain` and NOT for `ambient`
    # (`ambient_capture`), so a version of this loop keyed on the CLI word works
    # today and silently reports "on — default" for the first feature whose two
    # names differ, while `--set-policy` keeps writing the right key. Caught in
    # the PR review, 2026-09-12: the whole point of one table is that the config
    # key travels with the word, so read it rather than assuming they match.
    for cli, (key, tristate) in _SET_POLICY_FEATURES.items():
        if not tristate:
            continue
        label = "brain write-back" if cli == "brain" else cli
        central, source = _plugin_policy.resolve_central_with_source(
            key, cache, now)
        if central in ("on", "off"):
            lines.append(f"  {label}: {central} — {forced_src(source)}")
        elif cfg is not None and key in cfg:
            lines.append("  %s: %s — repo file"
                         % (label, "off" if cfg.get(key) is False else "on"))
        else:
            lines.append(f"  {label}: on — default")

    central, source = _plugin_policy.resolve_central_with_source(
        "ambient_capture", cache, now)
    if central in ("on", "off"):
        lines.append(f"  ambient capture: {central} — {forced_src(source)}")
    elif file_present:
        # `is_opted_out` owns the fail-closed reading of every present-file
        # shape (malformed included), so the effective state comes from it.
        lines.append("  ambient capture: %s — repo file"
                     % ("off" if is_opted_out(toplevel) else "on"))
    else:
        lines.append("  ambient capture: on — default")

    freshness = _plugin_policy.cache_freshness(cache, now)
    fetched_raw = cache.get("fetched_at") if isinstance(cache, dict) else None
    if freshness == "none":
        lines.append("  central policy cache: none (no successful fetch "
                     "recorded for this checkout)")
    elif freshness == "unreadable":
        # Every shape the resolver cannot use, INCLUDING the ones whose
        # `fetched_at` reads perfectly well — an unknown envelope version, a
        # non-finite ttl. The timestamp is still worth printing: it is the one
        # field a human can act on, and naming it is how the reader learns the
        # file was found and rejected rather than not found.
        lines.append("  central policy cache: present but unreadable "
                     f"(fetched_at {fetched_raw!r}) — treated as unset")
    elif freshness == "stale":
        lines.append(f"  central policy cache: fetched {fetched_raw} "
                     "(stale — older than its ttl, so the local answer "
                     "applies)")
    else:
        lines.append(f"  central policy cache: fetched {fetched_raw} (fresh)")
    return lines


def cmd_status(cwd=None):
    """--insights-status: print the durable EVENT-SKELETON delivery record for
    this repo's tenancy. THE READER for what `drain` persists under
    `state["events"]`.

    It exists because `run_drain` calls `ambient_outbox.drain` and DISCARDS the
    result dict, inside a detached background process with stdout redirected to
    /dev/null — so an omitted, rejected or count-mismatched skeleton that lived
    only in that dict would be unobservable. The state file survives; this verb
    reads it.

    WHY THIS VERB MAY USE `os.getcwd()` WHEN N2a FORBIDS IT FOR THE OTHERS. The
    hook verbs take `cwd` only from the harness payload because a wrong cwd there
    would ARM capture against a repo the user never opted in for. This verb arms
    nothing, registers nothing, writes nothing: it resolves a tenancy in order to
    READ that tenancy's own state file, and the worst outcome of a wrong cwd is
    printing an all-clear for a tenancy that has no record. A human running a
    diagnostic in a terminal has no payload to pass, so refusing to look at the
    process cwd would leave the record with no reader at all — which is the
    defect this function exists to close."""
    import ambient_outbox  # lazy: ambient_outbox imports THIS module at top
    cwd = cwd or os.getcwd()
    # ONE `git rev-parse` for the tenancy — `evaluate_gate` below runs its own
    # for the gate + toplevel, which is the pair it exists to resolve together.
    _toplevel, common = _git_rev_parse(cwd)
    tenancy = _tenancy_from_common(cwd, common)
    # JC8 — THE SECOND READER of `stale_loop_marker`, and the one that survives
    # after the startup line has scrolled away. It prints BEFORE the tenancy
    # check, and above everything else, because it is the only block here that
    # depends on neither: it reports the state of a file in this working tree,
    # while every line below reports what a background process did with a
    # tenancy. Put after that check it would go silent in exactly the state where
    # an operator is most likely to be poking around — a directory that is not a
    # git work tree, or one whose git resolution fails — over a marker that is
    # plainly there.
    #
    # It is deliberately NOT `ambient_outbox.events_status_report`, the other
    # candidate reader in this file: that function takes a tenancy and has no
    # `cwd`, so it cannot resolve `.fairmind/active-context.json` at all, and it
    # is scoped to the events compartment whose all-clear line is pinned by
    # substring assertions in tests/test_t2c3_optin_delivery.py.
    stale_line = stale_loop_message(stale_loop_marker(cwd))
    if stale_line:
        sys.stdout.write(stale_line + "\n\n")
    if not tenancy:
        sys.stdout.write("No Fairmind insights tenancy resolves for this "
                         "directory (not a git repo?), so there is no delivery "
                         "record to show.\n")
        return
    # T2-C4 — the health block prints ABOVE THE DELIVERY RECORD, and from the
    # SAME predicate the startup channel asks. It comes above it because it
    # changes what that record MEANS: an all-clear delivery record under
    # "nothing can be delivered" is an all-clear about a queue that is not
    # moving. (It read "prints FIRST" until JC8's degraded line was added above
    # it — that line needs neither the gate nor a tenancy, so it precedes both.
    # The claim this comment makes is about the ORDER RELATIVE TO THE THING IT
    # QUALIFIES, which is unchanged; "first" was only ever true incidentally.)
    #
    # THE GATE IS REPORTED, NOT SILENTLY OBEYED. `health_message` opens with
    # "capture is ON ... but is NOT working", which only `cmd_session_start` had
    # earned — it early-returns unless the gate said capture, and this verb never
    # asked. In an ordinary repo with no per-project Fairmind MCP that sentence
    # was simply false, and guaranteed to appear, because the state that makes
    # the gate say no is the same state that makes the delivery target
    # unresolvable. Gating the block on `decision.capture` fixes the falsehood
    # and would introduce a worse one by omission: a reader whose gate does not
    # resolve would get an all-clear. So the OFF case prints its own line with
    # the gate's own reason, and there is no state in which this verb is silent
    # about why.
    decision = evaluate_gate(cwd)
    if not decision.capture:
        sys.stdout.write(
            "Ambient insight capture is NOT on for this repository "
            f"(gate: {decision.reason}), so nothing is being captured or "
            "delivered from here.\n\n")
    # The plugin-policy block — ADDITIVE lines only (see _policy_status_lines).
    policy_lines = _policy_status_lines(decision.toplevel)
    if policy_lines:
        sys.stdout.write("\n".join(policy_lines) + "\n\n")
    message = health_message(insights_health(cwd, decision.toplevel,
                                             capture=decision.capture))
    if message:
        sys.stdout.write(message + "\n\n")
    sys.stdout.write(ambient_outbox.events_status_report(tenancy) + "\n")
    # The SESSION door's own record — mute, dead letters, pending — which the
    # events report above does not cover and nothing else ever printed. Placed
    # right after its sibling so the two doors read as the pair they are.
    # ADDITIVE ONLY, like every block below: the events lines carry substring
    # pins in tests/test_t2c3_optin_delivery.py.
    sys.stdout.write("\n" + ambient_outbox.session_status_report(tenancy) + "\n")
    # JC6 — LAST, and ADDITIVE ONLY. Every line above carries substring pins in
    # tests/test_t2c3_optin_delivery.py, so this block may only ever append.
    content_lines = _content_status_lines(tenancy)
    if content_lines:
        sys.stdout.write("\n" + "\n".join(content_lines) + "\n")
    # PX2 — LAST, and ADDITIVE ONLY, for the same reason the block above is.
    sys.stdout.write("\n" + _binding_status_lines(decision.toplevel) + "\n")


def _binding_status_lines(toplevel):
    """Whether this checkout is bound to a catalog repository, and to which.

    It belongs in this verb because `--insights-status` is where a developer
    goes to ask "is any of this actually working", and an unbound checkout is
    the state in which the answer is "the rows are delivered and they key to
    nothing" — which every other line here reports as healthy, correctly and
    uselessly.

    A STALE binding gets its own sentence rather than the healthy one. The
    payload builders stop using a binding whose origin has moved, so reporting
    "bound" over that state would be this verb asserting the opposite of what
    the lanes do — the exact false all-clear it exists to prevent. `_binding`
    owns that rule; this function only reports it."""
    import _binding  # lazy: this verb is not on the SessionStart fast path
    ctx = _binding.read_context(toplevel or "")
    repository_id = ctx.get(_binding.REPOSITORY_ID)
    if not isinstance(repository_id, str) or not repository_id.strip():
        return ("Repository binding: NOT BOUND. This repository is named to the platform "
                "by this directory's name, which the catalogue can rarely resolve — run "
                "/fairmind-connect to bind it to the repository that was ingested.")
    name = ctx.get(_binding.REPOSITORY_NAME) or "?"
    branch = ctx.get(_binding.REPOSITORY_BRANCH) or "?"
    project = ctx.get(_binding.PROJECT_ID) or "?"
    remote = _run_git_remote(toplevel)
    if remote and not _binding.origin_matches(ctx, remote):
        return (f"Repository binding: STALE. This checkout is bound to {name} "
                f"({repository_id}), but its `origin` now points somewhere else — so the "
                "agent-decisions and harness-audit payloads have gone back to naming this "
                "repository by its directory name. Re-run /fairmind-connect.")
    return (f"Repository binding: bound to {name} ({repository_id}) on branch {branch}, "
            f"in project {project}. The agent-decisions and harness-audit payloads name "
            "this repository by that id.")


def _run_git_remote(toplevel):
    """This checkout's normalized `origin`, or None. Its own helper so the
    status verb pays the subprocess only on a checkout that HAS a binding to
    check."""
    if not toplevel:
        return None
    try:
        import audit_run_meta
        proc = subprocess.run(["git", "-C", toplevel, "remote", "get-url", "origin"],
                              capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return audit_run_meta.normalize_git_remote(proc.stdout.strip())


def _content_status_lines(tenancy):
    """The content compartment's own `--insights-status` block, or `()` when
    this tenant has no compartment.

    🔴 GATED ON THE COMPARTMENT EXISTING, not on the mode being on, and the
    difference is the whole design. A line reading "0 captured" on a repo that
    never opted in would be a new unconditional output on every repo — and
    neither the stamp byte-identity test nor the data-dir hash test can see
    stdout, so nothing in the default-off suite would have caught it. No
    compartment, no line: the block appears exactly when there is something to
    report, and its absence is the report for everyone else.

    IT REPORTS THE GROWTH HONESTLY. Nothing drains this file in this build, so
    "unbounded until a delivery route exists" is the true statement and the one
    printed; a warn threshold marks the point at which somebody should look, not
    a cap that will be enforced."""
    spool = content_spool_path(tenancy)
    failures = content_failures_path(tenancy)
    if not os.path.isfile(spool) and not os.path.isfile(failures):
        return ()
    lines = ["Failed-iteration content capture (JC6):"]
    try:
        # The spool may be ABSENT while the failures file exists — the state a
        # tenant lands in when the very first capture lost its lock. Reading it
        # unconditionally raised there, and the handler below then reported "the
        # compartment could not be read" while swallowing the one number that
        # state exists to surface.
        size, rows = 0, 0
        if os.path.isfile(spool):
            size = os.path.getsize(spool)
            with open(spool, encoding="utf-8") as fh:
                rows = sum(1 for ln in fh if ln.strip())
    except OSError:
        return ("Failed-iteration content capture (JC6): the compartment exists "
                "and could not be read.",)
    lines.append(f"  {rows} captured iteration(s), {size} bytes, at {spool}")
    lines.append("  NOT delivered anywhere and NOT trimmed — this build has no "
                 "route that sends it, and nothing evicts a row, so the file "
                 "grows without bound until one exists.")
    if size >= CONTENT_WARN_BYTES:
        lines.append(f"  ⚠️  past the {CONTENT_WARN_BYTES}-byte mark where this "
                     "is worth a look. Setting `rejected_proposals` and "
                     "`generation_context` to false in .fairmind-insights.json "
                     "deletes it at the next refused iteration or session start.")
    try:
        if os.path.isfile(failures):
            with open(failures, encoding="utf-8") as fh:
                lost = sum(1 for ln in fh if ln.strip())
            if lost:
                lines.append(f"  ⚠️  {lost} capture(s) were LOST (see {failures}) "
                             "— those red iterations are unrecoverable.")
    except OSError:
        pass
    return tuple(lines)


# --------------------------------------------------------------------------- #
# THE LOCAL WRITER. `--set-policy` is the only verb in this module that writes
# the COMMITTABLE repo-root config, and the only one a human runs on purpose
# (through `/fairmind-config`); everything above it is a session hook.
# --------------------------------------------------------------------------- #

#: The `--set-policy` CLI vocabulary -> the key BOTH policy layers name. The
#: command line says `ambient` because that is the word a developer uses (and
#: the word `_policy_status_lines` prints); the repo file and the central
#: payload both say `ambient_capture`. ONE mapping, so the two spellings can
#: never drift into two independent vocabularies.
#: CLI word -> (config key, is it tri-state?). ONE table, so adding a feature
#: cannot leave the second question unanswered: a feature is tri-state when it
#: can genuinely hold "no opinion", and `unset` then REMOVES the key and an
#: absent key means on. `ambient_capture` is not one of them — a file that
#: exists without it fails closed to OFF, which is why its `unset` writes `true`
#: instead. Carried beside the key rather than in a second set because a new
#: feature added to one table and forgotten in the other would silently inherit
#: whichever branch is `else`, which is the outcome this distinction exists to
#: prevent. Nothing pins the two tables against each other; one table needs no
#: pin.
_SET_POLICY_FEATURES = {
    "judge": ("judge", True),
    "brain": ("brain", True),
    "ambient": ("ambient_capture", False),
}

#: The closed value vocabulary of the verb. `unset` is not a third state ON
#: DISK — it removes the key for judge, and for ambient it is `on` under a
#: printed note (see `cmd_set_policy`): a file that exists cannot express "no
#: opinion" about ambient, because `_config_disables` fails closed.
_SET_POLICY_VALUES = ("on", "off", "unset")


def _write_consent_config(path, cfg):
    """Atomically rewrite the repo-root `.fairmind-insights.json` from `cfg`.

    Through `_plugin_policy._atomic_write_json`, the ONE mkstemp + `os.replace`
    writer this feature shares with the policy cache (and the same idiom as
    `loop_import._atomic_write_json`): a concurrent reader — every hook in this
    module reads this file — never sees a truncated config, and a crash leaves
    the previous one standing. 2-space indent + a trailing newline because this
    file is COMMITTED and read in diffs by people.

    THE MODE IS PRESERVED, and that is not decoration. mkstemp lands 0600, so
    a plain replace would silently downgrade a team-readable, committable file
    to owner-only — a change `git status` cannot show, since git tracks only
    the exec bit. An existing file keeps exactly the mode it had (that is what
    `preserve_mode` buys); a new one lands 0644, which is what a file meant to
    be committed and read by the rest of the team should be.

    NO `makedirs`: the directory is a git toplevel the caller already resolved,
    so a missing one is a bug to surface, not a tree to create."""
    _plugin_policy._atomic_write_json(
        path, cfg, prefix=".fairmind-insights.", mode=0o644,
        preserve_mode=True, indent=2, trailing_newline=True)


def cmd_set_policy(operands, cwd=None):
    """--set-policy <judge|brain|ambient> <on|off|unset>: THE SAFE WRITER of this
    repository's LOCAL plugin policy, `<toplevel>/.fairmind-insights.json`.

    Returns an exit code — 0 wrote, 2 refused — and `main` hands it straight
    back, OUTSIDE the fail-open blanket every other verb here sits under. That
    blanket exists so a session hook can never block session open; applied to a
    writer it would report success over a refusal, which is the one outcome a
    policy switch must never produce.

    THREE REFUSALS, all of which leave the file BYTE-IDENTICAL, in this order:

    1. **The feature is centrally forced.** Defence in depth: the command doc
       says the same, but a doc is not an enforcement point. Only the feature
       BEING SET is checked — a judge force must not block `ambient off`.
    2. **The file exists and does not parse as a JSON object.** A config that
       fails to parse is an attempt at a decision (house rule); clobbering it
       would destroy the only evidence of what someone meant.
    3. **A judge op would have to carry a WRONG-TYPED `ambient_capture`
       forward** (`"true"`, `0`, `null`, `[]`). Same rule, one level in: the
       plugin already reads that shape as OFF, and freezing it to boolean
       `false` behind a judge op erases the evidence of intent. It refuses
       ONLY for a judge op — `--set-policy ambient on|off` is the way OUT of
       that state, so refusing there too would lock the developer out.

    THE FOOTGUN RULE: every write emits `ambient_capture` EXPLICITLY as a
    boolean, never a file without it. `_config_disables` enables capture only
    on the explicit boolean `True`, so a `judge` op that created a file
    carrying only `{"judge": false}` would silently switch AMBIENT CAPTURE OFF
    for the whole repository — a second feature disabled by a command about the
    first. What the explicit value must BE is the current EFFECTIVE state, not
    a constant: an absent file means capture ON, so a new file gets `true`; a
    present file with no `ambient_capture` key already reads as OFF, so it gets
    `false`. Both cases print a note saying which one happened.

    Unknown keys survive: the file is round-tripped through `json`, so
    `consent`, `event_skeleton` and any key a future slice adds keep their
    values and their order. Preservation is SEMANTIC, not byte-level —
    formatting is normalized to the 2-space shape above.

    WHY `os.getcwd()` IS ALLOWED HERE, when N2a forbids it for the hook verbs.
    Those take `cwd` only from the harness payload because a wrong cwd there
    would ARM capture against a repo nobody opted in for. This verb is run by a
    human standing in a directory, has no payload to read, and writes to that
    directory's git toplevel — which IS the repository they mean. A wrong cwd
    produces a visible, reversible, uncommitted `git status` entry, not silent
    capture."""
    import argparse  # lazy: this module is imported on the SessionStart fast
    # path, where argparse is never used (the same reason `cmd_status` defers
    # `ambient_outbox`).
    parser = argparse.ArgumentParser(
        prog="_insights_session.py --set-policy",
        description="Set this repository's LOCAL plugin policy in the "
                    "committable .fairmind-insights.json. A centrally forced "
                    "feature is refused: change it on the Fairmind platform.")
    parser.add_argument("feature", choices=sorted(_SET_POLICY_FEATURES))
    parser.add_argument("value", choices=_SET_POLICY_VALUES)
    # argparse exits 2 on a usage error (unknown feature, value outside the
    # vocabulary, wrong operand count) — the same code the refusals below use,
    # and it does it before anything is opened.
    args = parser.parse_args(operands)
    key, tristate = _SET_POLICY_FEATURES[args.feature]

    def refuse(message):
        sys.stderr.write(message + "\n")
        return 2

    toplevel, _common = _git_rev_parse(cwd or os.getcwd())
    if not toplevel:
        return refuse(
            "Not inside a git work tree, so there is no repository root to "
            "write .fairmind-insights.json to. Run this from inside the "
            "repository whose plugin policy you want to change. Nothing was "
            "written.")

    # 1. The central force, for THIS feature only.
    cache = _plugin_policy.read_cache(_plugin_policy.cache_path(toplevel))
    central, source = _plugin_policy.resolve_central_with_source(
        key, cache, datetime.now(timezone.utc))
    if central in ("on", "off"):
        where = _forced_project_label(cache, source, "the company default")
        return refuse(
            f"{args.feature} is centrally forced {central} for {where}, so "
            "this repository's local file cannot change it. The change has to "
            "happen on the Fairmind platform; the plugin picks the new answer "
            "up on the next session after a successful policy fetch. Nothing "
            "was written.")

    path = consent_config_path(toplevel)
    present = os.path.isfile(path)
    cfg = _read_json(path) if present else {}

    # 2. A present file that is not a JSON object.
    if not isinstance(cfg, dict):
        return refuse(
            f"{path} exists but does not parse as a JSON object, and a config "
            "that fails to parse is an attempt at a decision — this verb will "
            "not overwrite it. Fix or delete the file by hand, then run this "
            "again. Nothing was written.")

    # 3. A tri-state op that would have to carry a wrong-typed ambient value.
    existing_ambient = cfg.get("ambient_capture")
    if (tristate and "ambient_capture" in cfg
            and not isinstance(existing_ambient, bool)):
        return refuse(
            f"{path} carries ambient_capture as {existing_ambient!r}, which is "
            "not a boolean; the plugin already reads that as capture OFF "
            "(fail-closed), but this verb will not silently freeze it to "
            f"false behind a change about {args.feature}. Set it explicitly first — "
            "--set-policy ambient on (or off) — then run this again. Nothing "
            "was written.")

    # Every write goes through `key` — the config key `_SET_POLICY_FEATURES`
    # mapped this CLI word to — never through the literal that key happens to
    # equal today. Which branch a feature takes is declared beside that key
    # rather than falling out of an `else`, so the ambient-specific `unset` NOTE
    # below reaches ambient alone.
    notes = []
    if tristate:
        if args.value == "unset":
            cfg.pop(key, None)
        else:
            cfg[key] = args.value == "on"
    else:
        cfg[key] = args.value != "off"
        if args.value == "unset":
            notes.append(
                "Note: ambient has no local 'no opinion'. An ABSENT "
                ".fairmind-insights.json defaults to capture ON, but a file "
                "that exists and does not carry ambient_capture as the "
                "explicit boolean true fails closed to OFF — so unset is "
                "written as true, exactly equivalent to on. To leave this "
                "repository with no local answer at all, delete the file (only "
                "if it carries nothing else) and commit the deletion.")

    # THE FOOTGUN RULE. Reachable only on a tri-state op (an ambient op just
    # wrote a boolean, and the wrong-typed shape was refused above), so what
    # lands here is a missing key, and the value chosen is the effective state
    # it already had.
    if not isinstance(cfg.get("ambient_capture"), bool):
        cfg["ambient_capture"] = not present
        if present:
            notes.append(
                "Note: the file was present without an explicit "
                "ambient_capture, which the plugin already read as capture OFF "
                "(fail-closed). It is now written as false — the same "
                "effective state, said out loud. Use --set-policy ambient on "
                "to turn capture on.")
        else:
            notes.append(
                "Note: there was no .fairmind-insights.json. An absent file "
                "means ambient capture is ON, and a file that exists without "
                "an explicit ambient_capture means OFF — so true was written "
                "to keep this repository's capture exactly where it was.")

    _write_consent_config(path, cfg)

    sys.stdout.write(f"Wrote {path}: {args.feature} {args.value}.\n")
    for note in notes:
        sys.stdout.write(note + "\n")
    # The SAME rendering `--insights-status` prints, so the answer a developer
    # reads after a write is produced by the code that reads the policy, not by
    # this function's memory of what it just wrote.
    sys.stdout.write("\n" + "\n".join(_policy_status_lines(toplevel)) + "\n")
    return 0


def main(argv):
    # `--set-policy` FIRST, and OUTSIDE the fail-open blanket below. Every other
    # verb here is a session hook, where an exception must never block or slow
    # session open — so the blanket swallows it and exits 0. This one is a
    # human-invoked WRITER: swallowed, it would print a refusal (or fail a
    # write) and still report success, which is the one outcome a policy switch
    # must never produce. It returns its own exit code: 0 wrote, 2 refused.
    if "--set-policy" in argv:
        return cmd_set_policy(argv[argv.index("--set-policy") + 1:])
    try:
        if "--session-start" in argv:
            cmd_session_start()
        elif "--session-end" in argv:
            cmd_session_end()
        elif "--sweep" in argv:
            cmd_sweep()
        elif "--insights-status" in argv:
            cmd_status()
    except Exception:
        pass  # fail-open: a session hook must NEVER block or slow session open.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
