#!/usr/bin/env python3
"""_consent_authority.py — the shared consent, policy and session-registry
authority behind fairmind-coding's ambient insights lane.

Three lanes ask this module the same questions and must never disagree:
`_insights_session.py` (the ambient capture CLI, which re-exports every name
here for backward compatibility), `run_gate_checks.py` (the loop gate, which
stamps `state["consent"]` at arm time through its own `_consent_authority()`
helper), and `fairmind_connect.py` (tenancy/endpoint resolution for the
`/fairmind-connect` command). It is therefore the ONE definition of:

  * whether ambient capture and the event skeleton are granted at all
    (`is_opted_out`, `event_skeleton_consent`, `event_skeleton_enabled`), read
    from the committable repo-root `.fairmind-insights.json` and nothing else
    — see `event_skeleton_consent`'s own docstring for the fail-closed rule
    and the reason a per-user config no longer participates in the decision;
  * the content purpose ladder (`content_purpose`, `PURPOSE_ORDER`) a
    consumer resolves a repo's grant against;
  * the per-tenant session registry (`register_session`, `mark_session_end`)
    and its private on-disk fields, under a WRITABLE per-user data dir
    (`data_dir`, `~/.fairmind` by default, overridable via
    `FAIRMIND_INSIGHTS_HOME` so the suite runs hermetically);
  * tenancy and endpoint derivation (`resolve_tenancy`, `_git_rev_parse`,
    `fairmind_delivery_target`, `fairmind_events_endpoint`);
  * capture health (`insights_health`, `record_health`) and the stale-loop /
    loop-context registry (`stale_loop_marker`, `_diverted_rows`) a diverted
    row's reader consults; and
  * `Decision`/`evaluate_gate`, the SessionStart authority decision itself.

Imports only stdlib plus the plugin's other leaf modules (`_loop_ledger`,
`_plugin_policy`, `_mcp_config`, `_hook_line`) — never an ambient-capture module
(`ambient_digest`, `ambient_outbox`, `content_capture`, `insights_flush_payload`,
`judge_stop_lane`), on any path, lazy or not. That is what lets the loop gate
and `fairmind_connect` depend on consent and tenancy without ever importing
the capture machinery behind them.
"""

import contextlib
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

# POSIX-only advisory file locking for the session-registry writers
# (`_durable_append`, `_registry_write_lock`). Guarded the same way
# `_loop_ledger.py` guards it, so this module still IMPORTS on a non-POSIX
# host and degrades to best-effort (no real cross-process mutual exclusion)
# when `fcntl` is unavailable rather than refusing to run.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

# PL-A0 shared ledger primitives (the locked/bounded append and the atomic
# rewrite the session registry and the health/notice markers reuse) plus the
# MODULE itself — `stale_loop_marker` (below) calls
# `_loop_ledger.resolve_loop_context` through this reference so the lookup
# happens at CALL time: a `from _loop_ledger import resolve_loop_context`
# binds the function object at import and is therefore invisible to any
# later redefinition, which is exactly what the never-raises test substitutes
# to prove the backstop covers this dependency.
# `DEGRADED_DIR` is IMPORTED rather than re-spelled: it is the contract between
# the capture hooks that write diverted rows and `_diverted_rows`, that
# directory's first reader. A second spelling of the path is a reader that
# silently reports "nothing was diverted" the day the writer moves.
from _loop_ledger import _atomic_write_lines, DEGRADED_DIR  # noqa: E402
import _loop_ledger  # noqa: E402
import _hook_line  # noqa: E402  (stdlib-only leaf; the one line the user reads)
# The ONE shared central-policy resolver (cache path/read/write + the
# precedence truth table). `_plugin_policy` never imports this module (the
# judge Stop hook is its other consumer and must stay light).
import _plugin_policy  # noqa: E402


_SCHEMA = "fm-insights.session/1"


# --------------------------------------------------------------------------- #
# Paths (all under a WRITABLE per-user data dir, override for hermetic tests).
# --------------------------------------------------------------------------- #

# The consent predicate and the two readers it needs live in `_mcp_config`,
# a stdlib-only leaf, because the judge hooks ask the same question and may
# not import this module to do it. Imported back so there is one definition:
# a second copy would be a consent predicate that can answer differently on
# two lanes, which is the drift nobody would see.
from _mcp_config import (  # noqa: E402,F401 — re-exported, one definition
    _FAIRMIND_NAME_RE,
    _fairmind_keys,
    _is_fairmind_name,
    _read_json,
    fairmind_configured,
)

def data_dir():
    """The writable per-user data dir. `$CLAUDE_PLUGIN_ROOT` is a read-only
    sha-addressed cache, so state cannot live there. Honors
    `FAIRMIND_INSIGHTS_HOME` (the test suite points it at a temp dir so it never
    touches the real ~/.fairmind); defaults to ~/.fairmind.

    IT IS NOT "GITIGNORE-IMMUNE", which this docstring used to claim on the
    grounds that it "lives under $HOME, never inside a repo work tree". A
    dotfiles $HOME IS a work tree — ordinary enough that PCF-28's first revision
    appended `.fairmind/` to the developer's own ~/.gitignore because of it.
    `_fm_ignore.ensure_ignored` now skips this directory by identity, and that
    guard is the reason the claim had to go."""
    override = os.environ.get("FAIRMIND_INSIGHTS_HOME")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".fairmind")


def _registry_path(tenancy):
    return os.path.join(data_dir(), "insights", "sessions", tenancy + ".jsonl")


def _notice_marker_path(tenancy):
    """Where the AMBIENT one-time notice's already-shown marker lives.

    It records that a notice was DISPLAYED, never that anyone agreed to
    anything — the name says `notice` for that reason (2026-07-27: nothing here
    is consent any more, it is a company decision the reader is told about). The
    on-disk directory is still `insights/consent/`: renaming a path only moves
    bytes and would orphan every marker already written, so the older word
    survives in the filesystem layout and nowhere in the vocabulary.

    THE MARKER IS KEYED BY TENANCY, AND DELIBERATELY NOT BY (tenancy, toplevel).
    Recorded here because OPEN-1's own register claimed the opposite as a class-A
    defect — "a worktree that grants can project skeletons for sessions of a
    worktree that never did, and never showed that person the notice, because the
    marker is per-tenancy and was consumed elsewhere" — and THAT CLAUSE IS WRONG.
    `data_dir()` is `~/.fairmind`, i.e. PER USER. Two worktrees of one repo on one
    machine are the same person, and that person HAS seen the notice. "Never showed
    that person the notice" requires a different reader, which requires a different
    `$HOME`, which is already a different marker namespace.
    Re-keying was therefore dropped (2026-07-30) rather than deferred, and the cost
    avoided is worth naming: 5+ test files, every live marker orphaned, a ~2 KB
    notice re-printed per worktree — plus a trap, since the marker VALIDATORS check
    `cfg.get("tenancy") != tenancy`, so re-keying the filename without re-keying
    that content check makes validation WEAKER than the path.
    Per-row consent (OPEN-1 F4, `run_sweep`) and a per-user marker are coherent in
    both directions: if worktree B grants and A does not, B's session start shows
    the notice — `cmd_session_start` gates it on
    `event_skeleton_consent(decision.toplevel)`, that session's OWN toplevel — and
    A captures no skeleton."""
    return os.path.join(data_dir(), "insights", "consent", tenancy + ".json")


def plugin_version():
    """Best-effort read of the sibling ../.claude-plugin/plugin.json version (this
    module lives in <plugin>/scripts/). Returns None on any failure — never raises."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Opaque tenancy: 'fm-' + sha256(realpath(git-common-dir itself))[:16].
# --------------------------------------------------------------------------- #

#: `_git_rev_parse`'s answer per cwd. Every verb in this module is a one-shot
#: process, so no repository can move under a memoized answer within one run.
_GIT_REV_PARSE_CACHE = {}


def _git_rev_parse(cwd):
    """ONE `git rev-parse` returning (toplevel, git_common_dir), each None on
    failure. Combined so the SessionStart fast path does a SINGLE git subprocess
    (well under the 5s hook budget) instead of one per field — the toplevel feeds
    the .mcp.json / opt-out lookups, the common-dir feeds the opaque tenancy.

    MEMOIZED per cwd, which finishes that sentence for the passes that ask more
    than once: the detached sweep resolved the same checkout three times (the
    sweep itself, the drain, the policy refresh) and paid three subprocesses
    for one answer. MEASURED 2026-08-21 on a real `--sweep` against a temp
    checkout, counting invocations through a logging `git` shim on PATH: 3
    without the memo, 1 with it. A failure is memoized too — a repository that
    is not one will not become one mid-process either."""
    if cwd in _GIT_REV_PARSE_CACHE:
        return _GIT_REV_PARSE_CACHE[cwd]
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel", "--git-common-dir"],
            capture_output=True, text=True, timeout=3)
    except Exception:
        r = None
    if r is None or r.returncode != 0:
        answer = (None, None)
    else:
        lines = r.stdout.splitlines()
        top = lines[0].strip() if len(lines) > 0 else ""
        common = lines[1].strip() if len(lines) > 1 else ""
        answer = ((top or None), (common or None))
    _GIT_REV_PARSE_CACHE[cwd] = answer
    return answer


def _tenancy_from_common(cwd, common):
    """Derive the OPAQUE tenancy id from the CANONICAL `git rev-parse
    --git-common-dir` ITSELF (NOT its parent dir, NOT --show-toplevel). Hashing
    the common-dir itself still collapses all linked worktrees of one repo to ONE
    tenancy (they share a single common-dir) but keeps sibling submodules /
    separate-git-dir repos DISTINCT (each has its own common-dir under
    <super>/.git/modules/<name>, which the old dirname-based formula collapsed to
    the shared parent, mixing their markers + sessions — N7). realpath collapses
    symlinks (e.g. macOS /tmp -> /private/tmp). It is a one-way hash, and ONLY
    this value is ever persisted or wire-bound. None on any uncertainty (fail
    closed).

    CORRECTED, OPEN-1 (2026-07-30), beside the original rather than in place of
    it: "ONLY this value is ever persisted or wire-bound" is now true of
    WIRE-BOUND only. The local registry row additionally carries its own
    session's raw `toplevel` and `transcript_dir` (see `register_session` for why
    and `_resolve_transcript_dir` for what reads them). This value remains the
    only repo identifier that ever leaves the machine."""
    if not common:
        return None
    if not os.path.isabs(common):
        common = os.path.join(cwd, common)
    try:
        canonical = os.path.realpath(common)
    except Exception:
        return None
    return "fm-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def resolve_tenancy(cwd):
    """The OPAQUE tenancy id, or None when it cannot be resolved (fail closed).
    A non-git cwd yields None (a Fairmind consumer is always a git repo)."""
    _top, common = _git_rev_parse(cwd)
    return _tenancy_from_common(cwd, common)


# --------------------------------------------------------------------------- #
# The Fairmind-MCP presence signal (fail-closed, PER-PROJECT only).
# --------------------------------------------------------------------------- #



# --------------------------------------------------------------------------- #
# The ambient switch, REPO SCOPE ONLY (R3 as narrowed by the 2026-07-27
# enterprise policy). Opt-in since 2026-09-29; malformed config -> fail closed.
# --------------------------------------------------------------------------- #

def consent_config_path(toplevel):
    """The ONE config every switch in this module reads: the committable
    repo-root `.fairmind-insights.json`, resolved from the git TOPLEVEL so a
    launch from a subdirectory still honors it.

    THE ONE WRITER OF THAT PATH, and the basename itself now comes from
    `_plugin_policy.INSIGHTS_CONFIG_BASENAME` — the one spelling every reader
    of this file shares, including the judge Stop hook, which lives outside
    this module and cannot call this helper without paying its import cost.
    Every switch here — the ambient opt-in, the event-skeleton enablement, the
    three-state skeleton read, the consent classes and their two resolvers —
    used to build the path by hand with the filename as an inline literal,
    which is a rename nobody can do in one edit and a typo no test would catch
    (a mistyped path is simply an ABSENT file, and an absent file is a
    legitimate, silent, well-defined state on every one of those reads).
    Callers outside this module hand-build it too; they can move onto this
    helper independently.

    A LABEL IS NOT A PATH: `EVENTS_CONSENT_SCOPE_REPO` below names this file in
    a sentence a human reads in a doctor hint, and stays a separate literal on
    purpose — a hint is deliberately local-path-free, so it is a different fact
    about the same file, not a second copy of this one."""
    return os.path.join(toplevel, _plugin_policy.INSIGHTS_CONFIG_BASENAME)


def _config_answer(path, key):
    """What the config at `path` says about the opt-in `key`: None when the file
    or the key is absent — nobody said anything — else whether the value is the
    explicit boolean True. A file that does not parse as a dict is an attempt at
    a decision, so it answers False. `is True`, never truthiness: `1` and
    `"true"` answer False, because a switch a typo can flip is not a decision
    anyone made, and a wrong-typed value must never ENABLE capture (N6)."""
    if not os.path.isfile(path):
        return None
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return False
    if key not in cfg:
        return None
    return cfg[key] is True


def _config_disables(path):
    """True unless the config at `path` carries `ambient_capture` as the
    explicit boolean True. Ambient capture is OPT-IN: an absent file, a file
    without the key, and every other value disable it."""
    return _config_answer(path, "ambient_capture") is not True


def _config_enables_event_skeleton(path):
    """T2-C3: True only when the config at `path` carries `event_skeleton` as
    the explicit boolean True — the same opt-in reading as ambient capture,
    under its own key."""
    return _config_answer(path, "event_skeleton") is True


def ambient_local_answer(toplevel):
    """The repository file's own answer on ambient capture: True (opted in),
    False (said no — including a malformed file or a wrong-typed value), or None
    (said nothing: no file, or no `ambient_capture` key). An unresolvable
    toplevel answers False, fail closed. The gate's reason and the status line
    both read this, so neither re-derives the default."""
    if not toplevel:
        return False
    return _config_answer(consent_config_path(toplevel), "ambient_capture")


def event_skeleton_enabled(toplevel):
    """True iff the event skeleton is explicitly enabled by the COMPANY, in the
    ONE scope that can express a company decision: the committable repo-root
    `.fairmind-insights.json` (resolved from the git TOPLEVEL, so a launch from a
    subdirectory still honors it).

    THE PER-USER `<data_dir>/insights-config.json` IS NOT READ HERE, and its
    removal on 2026-07-27 is the point of that change rather than a side effect.
    This used to be enable-OR across the two scopes, which meant a developer
    could switch the skeleton on for themselves in a file under their own `$HOME`
    — a self-grant, in a mechanism whose whole premise is that the company
    decides. There is now exactly one scope, so the answer cannot be changed by
    anything the developer alone controls.

    An unresolvable `toplevel` returns False — fail closed, same as
    `is_opted_out`'s own unresolvable-toplevel branch, so an unknown repo can
    never be the one that switches the finer-grained capture on.

    DELIBERATELY a pure single-key config read, and it does NOT also consult
    `is_opted_out`. Folding the opt-out in here would make the name lie: callers
    reading `event_skeleton_enabled(top)` would get an answer about ambient
    capture as well, which is the "a comment (or a name) asserting a property its
    own branch destroys" pattern this track has already hit three times. The
    conjunction is genuinely required — the skeleton is a strict SUBSET of
    ambient capture, so enabling the narrow thing while the broad thing is off
    must never mean MORE data — and it is written, once, in
    `event_skeleton_consent`, which every call site asks. That matters because
    `run_sweep` does NOT re-evaluate the gate: it digests every
    ended-not-digested registry row, so a repo that opted out AFTER those rows
    were registered would otherwise start emitting skeletons for them."""
    if not toplevel:
        return False
    return _config_enables_event_skeleton(consent_config_path(toplevel))


# --------------------------------------------------------------------------- #
# T2-C3 — the THREE states, and the asymmetry that makes three
# necessary. THIS MODULE OWNS THE DECLARATION (the same idiom
# `ambient_digest.SCHEMA_VERSION` uses): `ambient_outbox.drain` imports these
# rather than duplicating the literals, so producer and consumer cannot drift.
#
# FAIL-CLOSED IS RIGHT FOR CAPTURE AND WRONG FOR DESTRUCTION, and collapsing the
# two into one boolean was a live data-loss bug. `event_skeleton_enabled` answers
# ONE question — "may we record/send?" — and answers False for an absent,
# unreadable or malformed config, which is correct: an unstated decision
# must never mean more capture. `run_drain` then fed that same False into
# `drain(events_enabled=…)`, where False means REVOKED and resolves every
# already-captured skeleton as terminal, so its bytes are reclaimed at the next
# compaction. A transient bad JSON file — a half-written config, a merge
# conflict marker, an editor swap file — therefore DESTROYED skeletons that were
# captured while it was genuinely in force, and did it silently.
#
# So the destruction verb needs its own predicate, and it is the NARROWEST one
# the notice can honestly promise: only the EXPLICIT boolean `false` discards.
# Everything the notice does not name — no config, no key, an unreadable file, a
# wrong-typed value — is INDETERMINATE: never sent (fail-closed for capture) and
# never discarded (fail-safe for destruction), retained on the spool with a
# doctor hint. That is how this design degrades everywhere else: never drop,
# raise a hint.
EVENTS_CONSENT_GRANTED = "granted"
EVENTS_CONSENT_REVOKED = "revoked"
EVENTS_CONSENT_INDETERMINATE = "indeterminate"

# WHY THESE NAMES STILL SAY "CONSENT". They are the INTERNAL vocabulary of a
# three-state read — "may we send / must we discard / we cannot tell" — and the
# three-way distinction is the thing worth keeping a word for. What changed on
# 2026-07-27 is who the decision belongs to: the COMPANY grants and revokes, the
# developer does neither, so nothing user-facing may call this consent. The
# notice therefore no longer uses the word at all; these constants are never
# rendered to a reader, only compared.

# The ONE config SCOPE, named for a human reading a doctor hint. A LABEL, not a
# path: a hint is local, but the repo toplevel and the user's home directory are
# exactly the strings the opaque tenancy exists so that nothing has to carry —
# naming the file is enough to act on. The per-user scope label was deleted with
# the per-user scope itself; a label for a file nothing reads would send an
# operator to edit something inert.
EVENTS_CONSENT_SCOPE_REPO = ".fairmind-insights.json at the repo root"

# OPEN-1 F4 (delivery half): the scope is unreadable because WHICH FILE it is
# cannot be determined — the session's registry row records no repo (a row written
# before OPEN-1), so no `.fairmind-insights.json` is the right one to read. A
# label, not a path, for the same reason as the one above.
#
# IT IS A DISTINCT LABEL BECAUSE THE OPERATOR'S NEXT ACTION IS DIFFERENT, and
# because reusing `EVENTS_CONSENT_SCOPE_REPO` here would send them to a file that
# very likely reads perfectly — the misattribution this round exists to remove.
# There is no action that recovers the decision for such a row; the honest report
# is that the skeleton waits, unsent and undestroyed.
EVENTS_CONSENT_SCOPE_UNKNOWN_REPO = (
    "the repo config governing this session's own checkout — its registry row "
    "records no repo, so which .fairmind-insights.json applies is unknown")

_NO_CONFIG = object()  # "no file at this scope", distinct from "unparseable"


def event_skeleton_consent(toplevel):
    """`(state, unreadable_scopes)` — the T2-C3 state for CAPTURE and DELIVERY.

    THE WORD "CONSENT" SURVIVES HERE AS AN INTERNAL TERM ONLY, and the reason is
    worth one sentence because the whole round is about not saying it: this
    function answers a three-valued question — may we send / must we discard /
    can we not tell — and "consent" is the shortest accurate name for that
    three-way shape inside the code. It is NOT what the mechanism is any more.
    The company decides; the developer neither grants nor revokes; so nothing a
    person reads calls it consent (see `events_notice_message`), and no state
    name here is ever rendered to a reader.

    `state` is one of the three `EVENTS_CONSENT_*` constants above.
    `unreadable_scopes` is a tuple of `EVENTS_CONSENT_SCOPE_*` labels naming the
    configs that exist but could not be parsed; empty unless the state is
    INDETERMINATE, and possibly empty then too (nothing is unreadable when there
    is simply no config at all).

    ONE SCOPE, AND ONLY ONE: the committable repo-root `.fairmind-insights.json`.
    The per-user `insights-config.json` was removed from this read on 2026-07-27
    (enterprise policy — see the module docstring). It could previously do BOTH
    forbidden things: revoke a decision the company had made, and grant one the
    company had not. The repo file is the INTERIM PROXY for the company's
    decision until the Fairmind platform is the authority; it is committable and
    reviewable, which is the property `$HOME` can never have.

    THE THREE BRANCHES, in the order they are decided:

      1. REVOCATION IS DECIDED FIRST: `"event_skeleton": false` — the explicit
         boolean, in the repo file. Off beats on, exactly as `is_opted_out`
         already works for ambient capture, and the ordering is kept even though
         a single scope can no longer contradict itself: branch 2 is a
         conjunction that an unrelated `ambient_capture` value can falsify, so
         testing GRANTED first would resolve `{"ambient_capture": false,
         "event_skeleton": false}` to INDETERMINATE — retention — when the
         company has explicitly said discard. The order is what keeps an
         explicit `false` meaning the same thing in every file that carries it.
         Nothing else revokes, and deliberately not an ambient opt-out: there is
         exactly one way to discard already-captured skeletons, and a
         destruction path nobody named is the defect this function exists to
         remove. An ambient opt-out still stops DELIVERY through branch 2, which
         leaves those skeletons retained and unsent — the conservative outcome.
      2. GRANTED — reached only when nothing revoked — is then
         `event_skeleton_enabled(toplevel) and not is_opted_out(toplevel)`.
         `run_sweep` (capture), `drain` (delivery) and `cmd_session_start` (the
         notice) all ASK THIS FUNCTION rather than restating that expression, so
         "is the skeleton on" has ONE definition; re-deriving it in `run_sweep`
         is exactly how capture and delivery came to disagree. Both halves are
         needed: the skeleton is a strict SUBSET of ambient capture, so the
         narrow switch must never outlive the broad one.
      3. Everything else is INDETERMINATE: no config file, a config with no
         `event_skeleton` key, an unreadable/malformed/non-dict file, a
         wrong-typed value (`"false"`, `0`, `null`), or an unresolvable
         `toplevel`. "I cannot read the company's decision" is not "the company
         said no", and it is emphatically not "the company said yes".

    Consequence worth stating: REMOVING THE KEY IS NOT A DISCARD. It stops new
    capture (branch 2 is false, so the state is INDETERMINATE and capture
    requires GRANTED), but the already-captured skeletons are retained rather
    than thrown away, because nothing distinguishes a deleted key from a config
    that never had one."""
    scopes = []
    if toplevel:
        scopes.append((EVENTS_CONSENT_SCOPE_REPO, consent_config_path(toplevel)))
    configs = [(label, _read_json(path) if os.path.isfile(path) else _NO_CONFIG)
               for label, path in scopes]

    # REVOCATION IS TESTED FIRST, AND THE ORDER IS STILL THE GUARANTEE even now
    # that there is one scope. Branch 2 is a CONJUNCTION — the skeleton may not
    # outlive ambient capture — so `{"ambient_capture": false,
    # "event_skeleton": false}` fails it and would fall through to
    # INDETERMINATE, i.e. RETAIN, if GRANTED were tested first. That is the
    # company having written "discard" and the code answering "I am not sure".
    # Testing the explicit `false` first is what makes that literal mean one
    # thing regardless of what else the file says.
    if any(isinstance(cfg, dict) and cfg.get("event_skeleton") is False
           for _label, cfg in configs):
        return EVENTS_CONSENT_REVOKED, ()

    # Branch 2 (GRANTED), written as EXACTLY the expression the docstring names
    # — no extra `toplevel and` guard in front of it.
    # `event_skeleton_enabled` already returns False for a falsy toplevel (its
    # own first line, fail-closed), so the guard could never change an answer;
    # what it could do is drift from the one definition of "may we send" while
    # looking like it was strengthening it.
    if event_skeleton_enabled(toplevel) and not is_opted_out(toplevel):
        return EVENTS_CONSENT_GRANTED, ()

    return (EVENTS_CONSENT_INDETERMINATE,
            tuple(label for label, cfg in configs
                  if cfg is not _NO_CONFIG and not isinstance(cfg, dict)))


def skeleton_consent_resolver():
    """A memoized `toplevel -> (state, unreadable_scopes)` over the ONE
    `event_skeleton_consent` — the shared machinery for the two sites that must
    answer the skeleton question PER SESSION rather than once per process.

    WHY IT IS SHARED RATHER THAN WRITTEN TWICE. OPEN-1 F4 fixed the per-row
    predicate in `run_sweep` and left the sibling in `run_drain` resolving one
    answer from the LAUNCHING cwd — two adjacent lines of `cmd_sweep`, same
    process, same cwd, asking the same question two different ways. The result was
    strictly worse than the original defect: the sweep correctly captured a
    granting worktree's skeleton and the drain destroyed it two lines later on a
    sibling worktree's `false`, terminally, then recorded that the GRANTING repo's
    config had revoked it. This function is the one place the question is asked so
    that there is no second place to forget.

    IT RETURNS `None`, NEVER A STATE, FOR A TOPLEVEL IT CANNOT USE — and that is
    the deliberate part. "This row records no repo" has DIFFERENT right answers at
    the two sites, so a shared default would silently impose one on the other:

      * CAPTURE fails CLOSED (`run_sweep`): no projection may be inferred from
        somebody else's config.
      * DELIVERY must NOT (`run_drain`): at that site "closed" would mean REVOKED,
        which DESTROYS. It resolves INDETERMINATE — neither sent nor discarded —
        under `EVENTS_CONSENT_SCOPE_UNKNOWN_REPO`.

    Shared resolution, per-site policy. Fail-closed is right for capture and wrong
    for destruction, which is the same asymmetry `event_skeleton_consent`'s three
    states exist for.

    The memo makes the ordinary single-toplevel case cost exactly ONE resolution,
    which is the economy the old resolve-once code bought by asking the wrong
    repository. It is per-CALL (a fresh resolver per sweep/drain), so a config
    edited between two passes is always re-read: reading CURRENT state rather than
    a decision frozen earlier is what preserves revoke-wins."""
    memo = {}

    def resolve(toplevel):
        top = _clean_provenance_path(toplevel)
        if not top:
            return None
        if top not in memo:
            memo[top] = event_skeleton_consent(top)
        return memo[top]

    return resolve


def is_opted_out(toplevel):
    """True iff AMBIENT capture is OFF for this repo — switched off, or never
    opted in (it is opt-in since 2026-09-29) — read from the ONE
    scope that can express a company decision: the committable repo-root
    `.fairmind-insights.json` (resolved from the git TOPLEVEL, so a launch from a
    subdirectory still honors it). An unresolvable toplevel fails closed (treated
    as opted out).

    THE PER-USER `<data_dir>/insights-config.json` NO LONGER DISABLES ANYTHING,
    and this widening beyond T2-C3's own subject matter is REQUIRED rather than
    tidiness. The event skeleton is projected DURING THE AMBIENT DIGEST
    (`run_sweep` -> `_digest_one_session` -> `digest(..., events=…)`): with
    ambient capture off there is no digest to project from, so a developer who
    could still switch ambient off for themselves could switch the skeleton off
    for themselves too, transitively, and the enterprise policy would hold only
    on the switch someone remembered to close. Removing the per-user scope from
    `event_skeleton_consent` alone would have left the policy NOMINAL — true of
    the named key and false of the outcome. Both switches now read one file, and
    that file is not one the developer alone controls."""
    if not toplevel:
        return True
    return _config_disables(consent_config_path(toplevel))


# --------------------------------------------------------------------------- #
# JC5 — THE CONSENT CLASSES.
#
# Three independently grantable classes over data BOTH LANES ALREADY SEND:
#
#   A  merged diffs        loop: artifacts, artifact_mutations, stratification
#   B  rejected proposals  loop: non-green iteration_timeline entries, human_gate
#   C  generation context  loop: agents, transitions, mutation_divergence
#                          ambient: agents, toolCounts, skills, events
#
# THE MAP IS NOT TOTAL, and the remainder is stated rather than implied: the
# identifiers, status, counters and the consent object itself belong to no class
# and are always sent, exactly as today. The full versioned map — the artifact
# both repos hold, so that "revoke B" could ever name a set of fields — is
# `consent_class_map.json` beside this file. A consent class must never be read
# as covering the whole payload.
#
# ONE SCOPE, and it is the same `<toplevel>/.fairmind-insights.json` every other
# switch in this module reads. The per-user `~/.fairmind/insights-config.json`
# was removed from every decision path on 2026-07-27 by enterprise policy and is
# pinned inert in BOTH directions by an exhaustive matrix test; nothing here
# re-introduces it. "Both scopes" in the JC5 brief means both LANES.
#
# THE MISSING-FILE CASE: an absent file grants all three classes. The loop lane
# has no gate, so there "no file" means the record is sent and the classes
# narrow that grant rather than re-ask for it. The ambient lane is opt-in
# (since 2026-09-29), so without the file it captures nothing for them to apply
# to unless the platform forces it on. (Until then ambient capture was on by default, which is why this rule was
# written for both lanes — returning "grant nothing" would have been a capture
# regression dressed as a privacy win.)
#
# AND "GRANTED NOTHING" MUST STAY DISTINGUISHABLE FROM "NOBODY ASKED". That is
# what `basis` is for: `["A","B","C"] + explicit` and `["A","B","C"] + no_config`
# are the same fields on the wire and completely different facts about consent.
# --------------------------------------------------------------------------- #

# The stamp's own version. It is what makes `consent_class_map.json` findable
# from a stored row: a row says which classes it was collected under, and this
# says which EDITION of the class→field map those letters refer to. Bump it only
# together with that file's `version`.
CONSENT_VERSION = "fm-consent/1"

# §F.4 — THE DEFAULT, AND THE ONLY VALUE THE DEPLOYED DOORS EVER STAMP. Every
# ambient row and every loop payload carries this literal, on every repo,
# including one that has opted into content capture: the doors that are already
# deployed read a fixed field set, and widening what they stamp would change the
# consent bytes of records those doors' own conformance fixtures pin (JC6/S2-A1).
# So this constant is still asserted at those two sites rather than resolved.
#
# WHAT CHANGED WITH JC6, stated because the sentence that stood here until
# 2026-08-21 said "the only accepted value, permanently" and that is no longer
# true. `config_content_mode` below resolves a SECOND value out of the config,
# and it governs exactly one thing: whether the loop lane may write a captured
# red iteration into the LOCAL content compartment. It does not widen any door,
# it does not travel on any existing wire, and no config that omits it resolves
# to anything but this literal.
#
# The key still exists for the reason it always did — a row is SELF-DESCRIBING:
# a record collected references-only stays distinguishable from one that was
# not, with no migration.
CONSENT_CONTENT_MODE = "references"

# The one opt-in value, and the whole of what it authorizes: the diff of a loop
# iteration the gate REJECTED, plus this harness's own account of why it did.
# Named for the thing rather than for the format ("diffs" named one of the two
# channels it opens and left the other unnamed — JC6/S1-3b).
CONSENT_CONTENT_MODE_FAILED_ITERATIONS = "failed_iterations"

# The classes that can authorize content, in the letters the wire speaks. A is
# absent on purpose: class A is the MERGED diff, which is already what a repo's
# own history holds, and JC6 captures only what the gate refused.
CONTENT_CONSENT_CLASSES = ("B", "C")

# THE PURPOSE LADDER (JC39) — what a captured row may be USED for, which is a
# different question from whether it may be COLLECTED (`CONSENT_CONTENT_MODE`
# above) or SENT (nothing, in this build). Three rungs, and the two questions
# they cross are independent: how far the byte travels, and who benefits.
#
# A two-value vocabulary was written first and refused by the owner, because it
# collapsed those two axes and so had no rung for the customer who pools their
# own team's trajectories to specialize their OWN model — data that leaves the
# machine without entering our corpus, and the MODAL case rather than an edge
# one. A missing rung does not error: it takes the nearest existing label, and
# the nearest is the WIDEST. That is why the set is closed here.
PURPOSE_LOCAL_ONLY = "local_only"              # never leaves this checkout
PURPOSE_CUSTOMER_ONLY = "customer_only"        # leaves the machine, not the tenant
PURPOSE_FAIRMIND_TRAINING = "fairmind_training"  # may enter the FairMind corpus

# ⚠️ ORDERED, NARROWEST FIRST. `content_purpose` WALKS this tuple in reverse to
# try the widest grant first, so the order decides which grant is CHECKED first
# — and deliberately NOT what an ungranted row falls back to, which is the named
# constant above. That split is the point: a tuple is the wrong place to keep an
# authorization floor, because prepending a rung would move it silently.
# Membership is what matters here: a rung missing from this tuple is never
# reached and therefore never grantable, which `_PURPOSE_FEATURES` below is
# pinned against.
PURPOSE_ORDER = (PURPOSE_LOCAL_ONLY,
                 PURPOSE_CUSTOMER_ONLY,
                 PURPOSE_FAIRMIND_TRAINING)

# Which central-policy feature grants which rung. Deliberately a TABLE and not a
# derivation: `"purpose_" + rung` happens to spell these two correctly today,
# and welding a policy wire key to a corpus label that way means renaming the
# label silently renames the key — every repo would then resolve to the floor
# with nothing raised. The wire literals are hand-written here for the same
# reason `test_plugin_policy.py` hand-writes the perimeter.
# `local_only` is absent because it is not granted: it is the floor.
_PURPOSE_FEATURES = {PURPOSE_CUSTOMER_ONLY: "purpose_customer_only",
                     PURPOSE_FAIRMIND_TRAINING: "purpose_fairmind_training"}

# The four bases. `basis` answers the question the class list cannot: whether a
# grant was DECIDED or DEFAULTED.
CONSENT_BASIS_EXPLICIT = "explicit"          # a `consent` block was read+parsed
CONSENT_BASIS_LEGACY_CONFIG = "legacy_config"  # config present, no consent block
CONSENT_BASIS_NO_CONFIG = "no_config"        # no config file at all
CONSENT_BASIS_PRE_CONSENT = "pre_consent"    # collected before this machine existed
# A STORED STAMP WHOSE CLASS LIST CANNOT BE READ — non-list, absent, garbage.
# Added 2026-08-14 by owner decision, replacing a divergence the two readers of
# one record had been pinned apart on: this builder labelled such a record
# `explicit` and the authority `pre_consent`, and NEITHER is defensible.
# `explicit` claims a decision was parsed when nothing was read; `pre_consent`
# means "all three were in force" and was being carried over an EMPTY grant.
# The honest answer was a name for the state itself, so the payload stops
# asserting something about a record it could not read.
#
# NOT the same question as an unparseable CONFIG FILE, which stays
# `explicit` — see `config_consent_classes`: a config that exists and fails to
# parse is an attempt at a decision, and the classes there are refused anyway.
# Here the record is one WE wrote and can no longer read.
CONSENT_BASIS_UNREADABLE = "unreadable"

# ⚠️ ORDERED, WEAKEST FIRST, AND THE ORDER IS BEHAVIOUR — not a tidy way to list
# four names. A stamp is only as well-attributed as its worst-attributed window,
# so a re-arm keeps the WEAKEST basis of the windows that contributed data:
# `run_gate_checks` picks it with `min(prior, basis, key=CONSENT_BASIS_ORDER
# .index)`, and that call reads its answer out of THIS sequence. Reordering these
# four names therefore silently changes which basis a re-armed loop ships —
# `["A","B","C"] + explicit` and `["A","B","C"] + no_config` are the same fields
# on the wire and completely different facts about consent.
#
# It is exported as a TUPLE rather than left implicit in four constants because
# the order was previously asserted nowhere: the cross-lane tripwire compares the
# four bases as a SET, which is exactly the comparison that cannot see a swap.
CONSENT_BASIS_ORDER = (CONSENT_BASIS_UNREADABLE,
                       CONSENT_BASIS_PRE_CONSENT,
                       CONSENT_BASIS_NO_CONFIG,
                       CONSENT_BASIS_LEGACY_CONFIG,
                       CONSENT_BASIS_EXPLICIT)
# `unreadable` is FIRST, i.e. weakest, and that placement is the whole point of
# it: `min(prior, basis, key=CONSENT_BASIS_ORDER.index)` picks the weakest of
# the contributing windows, so one unreadable window makes the merged
# attribution unreadable rather than letting a corrupt record inherit the
# strength of the window beside it.

ALL_CONSENT_CLASSES = ("A", "B", "C")

# Config key -> class letter. The KEYS are the human-facing vocabulary a company
# writes in a committed file, the LETTERS are what travels on the wire: a letter
# is stable under a rename of the prose, and prose is what a config file needs to
# be reviewable. One pairing, here, so neither side can drift.
_CONSENT_CLASS_KEYS = (("merged_diffs", "A"),
                       ("rejected_proposals", "B"),
                       ("generation_context", "C"))


def config_consent_classes(path):
    """Which consent classes a config GRANTS, and on what BASIS. Returns
    `(classes, basis)` — `classes` a SORTED list of letters, `basis` one of the
    four `CONSENT_BASIS_*` values.

    THE `consent` BLOCK IS A NARROWING INSTRUMENT, NOT AN OPT-IN. Inside the
    block, every class is OFF unless explicitly granted — `is True` and not
    truthiness, mirroring `_config_enables_event_skeleton`, because `1`, `"true"`,
    `"yes"`, `[]` are all OFF and a switch a typo can flip is not a decision
    anyone made. But the ABSENCE of the block, and the absence of the whole FILE,
    both grant all three — because that is what the code does today (see the
    measurement in the section comment above).

    A MALFORMED file or block is the ONE fail-closed case, because there the
    company's intent is unknown rather than merely unstated. It reports
    `basis: "explicit"` deliberately: a file that exists and cannot be parsed is
    an attempt at a decision, and labelling it `no_config` would tell the server
    nobody had tried.

    NOT the destruction predicate. This function cannot tell `generation_context`
    ABSENT from `generation_context: false` — both simply leave "C" out of the
    list — and that distinction decides whether already-captured rows are
    DISCARDED. `class_consent_state` below is the three-valued read for that, and
    the two must never be collapsed: doing so is the shape of the 2026-07-27
    data-loss bug that `event_skeleton_consent` exists to document."""
    if not os.path.isfile(path):
        return list(ALL_CONSENT_CLASSES), CONSENT_BASIS_NO_CONFIG
    return _classes_from_cfg(_read_json(path))


# The PARSED half of the resolver above — every branch below its `isfile` check,
# split out so a caller that has ALREADY read the config can reach the same
# ladder without re-stat-ing and re-parsing the identical file. `class_consent_
# state` is that caller: it must look inside the `consent` block itself (only an
# explicit `false` may discard), so it holds the parsed config in its hand and
# used to ask for the grant by PATH anyway — two parses of one file per call.
# Private on purpose: the file's absence is half the answer (`no_config` grants
# all three), so a `cfg`-only entry point cannot be the one an outside caller
# reaches for.
def _classes_from_cfg(cfg):
    if not isinstance(cfg, dict):
        return [], CONSENT_BASIS_EXPLICIT      # unreadable/malformed -> fail closed
    block = cfg.get("consent")
    if block is None:
        return list(ALL_CONSENT_CLASSES), CONSENT_BASIS_LEGACY_CONFIG
    if not isinstance(block, dict):
        return [], CONSENT_BASIS_EXPLICIT      # present but malformed -> fail closed
    return ([letter for key, letter in _CONSENT_CLASS_KEYS
             if block.get(key) is True],
            CONSENT_BASIS_EXPLICIT)


# The underscore name, kept as a THIN ALIAS so the consumers that reach for it
# today — the gate engine's `arm`, the loop payload builder's CLI, and the
# repo-root corpus harvester, none of which live in this file — can migrate to
# the public name independently instead of in one flag-day edit across three
# call sites in two repos. It is expected to go once they have.
_config_consent_classes = config_consent_classes


def config_content_mode(path):
    """Whether the config at `path` authorizes CONTENT capture, and under which
    letters. Returns `(mode, classes)` — `mode` one of the two content-mode
    constants, `classes` the sorted subset of `CONTENT_CONSENT_CLASSES` that
    actually authorized it (empty whenever the mode is `references`).

    🔴 IT IS A CONJUNCTION, AND EVERY CONJUNCT IS LOAD-BEARING. Content is
    written only when ALL of:

      1. the config parses to an object carrying a well-formed `consent` block —
         i.e. `config_consent_classes` would report basis `explicit`;
      2. that block's `content` key is the literal opt-in value, compared with
         `==` against a string, never coerced;
      3. at least one of B / C is granted by the LITERAL boolean `true`, the
         same `is True` test the class ladder uses.

    Conjunct 3 is the one a single-lock reading loses, and it is not a corner
    case: `config_consent_classes` grants ALL THREE classes for an absent file
    (`no_config`) and for a config with no `consent` block (`legacy_config`).
    Those defaults exist so the class system can NARROW a grant that already
    exists — they are authority for references-only metadata and they are not
    authority for a byte of content. Without conjunct 1, every repo on earth
    with no config file would be one `content` key away from content capture;
    without conjunct 3, `{"consent": {"rejected_proposals": false,
    "generation_context": false, "content": "failed_iterations"}}` — a repo that
    REFUSED both classes in writing — would resolve to the opt-in mode.

    THE NON-OPTING BRANCH RETURNS A LITERAL, never a computed value, because
    every branch below that is not the opt-in must produce bytes identical to
    the ones this file emitted before JC6 existed: absent file, unparseable
    file, no `consent` block, block present without `content`, `content` present
    with any other value, and `content` opt-in with no class behind it. Six
    shapes, one literal, and the byte-identity test drives all six.

    NOT the destruction predicate, for the same reason `config_consent_classes`
    is not: it cannot tell `content` ABSENT from `content: false`. Reclamation
    asks `class_consent_state` per letter — an explicit `false` on B or C is
    what discards captured rows, and this function never destroys anything."""
    if not os.path.isfile(path):
        return CONSENT_CONTENT_MODE, []
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return CONSENT_CONTENT_MODE, []
    block = cfg.get("consent")
    if not isinstance(block, dict):
        return CONSENT_CONTENT_MODE, []
    if block.get("content") != CONSENT_CONTENT_MODE_FAILED_ITERATIONS:
        return CONSENT_CONTENT_MODE, []
    granted = sorted(letter for key, letter in _CONSENT_CLASS_KEYS
                     if letter in CONTENT_CONSENT_CLASSES and block.get(key) is True)
    if not granted:
        return CONSENT_CONTENT_MODE, []
    return CONSENT_CONTENT_MODE_FAILED_ITERATIONS, granted


def content_mode_granted(toplevel):
    """`config_content_mode` for a repository ROOT rather than a config path —
    the form every caller outside this file wants, and the one that keeps the
    `.fairmind-insights.json` filename in exactly one place.

    A falsy toplevel resolves to the default: a checkout whose root we could not
    read is a checkout whose consent we did not read, and that is never
    authority to write content."""
    if not toplevel:
        return CONSENT_CONTENT_MODE, []
    return config_content_mode(consent_config_path(toplevel))


def content_purpose(toplevel):
    """Which rung of `PURPOSE_ORDER` a row captured NOW may be used at.

    🔴 THE CENTRAL POLICY IS THE ONLY DOOR, and the repo file is deliberately
    not consulted. The upper two rungs are a CONTRACTUAL grant — the design
    partner discount buys use rights in writing — and a right anyone who can
    edit a file in the repository could grant themselves is not that. The
    committed `.fairmind-insights.json` governs what may be COLLECTED, which is
    the company's own decision to make locally; it does not govern what we may
    do with it afterwards.

    🔑 ONLY THE EXACT STRING "on" GRANTS, and that is the opposite reading from
    the two switch consumers of this cache. `evaluate_gate` and
    `judge_is_silenced` treat every non-None answer as a FORCE, because for a
    switch an unreadable force must still mean less capture. Here the question
    is a GRANT, so the same instinct points the other way: anything that is not
    the literal "on" — "off", None, a value a widened vocabulary added, a stale
    cache, a payload from another origin — leaves the rung ungranted and the
    row at `local_only`. `resolve_central` already collapses every ambiguous
    shape to None, so this function adds no failure mode of its own.

    NO NETWORK. `read_cache` is a file read of a small JSON; the one GET lives
    in `run_policy_refresh`, on the detached sweep's budget. The caller
    (`content_capture.capture`) reaches this only AFTER `content_mode_granted`
    has confirmed the grant, so a repository that captures nothing pays nothing
    — `test_the_cost_of_not_capturing_is_one_git_call_and_it_is_pinned` is what
    holds that, and moving this call above the grant check is what breaks it.

    Widest granted rung wins: a tenant holding both grants stamps
    `fairmind_training`, which SUBSUMES the customer-scoped use rather than
    conflicting with it. Fail-closed is about ambiguity, not about ignoring a
    grant that was actually made."""
    # EVERY fallback returns PURPOSE_ORDER[0] rather than the named constant,
    # and that is the whole reason the tuple is ordered. Written with the name
    # at first, which made the comment beside `PURPOSE_ORDER` false: nothing
    # read the tuple, so reordering it changed nothing and its stated guarantee
    # was decorative. Two reviewers caught it independently. `CONSENT_BASIS_ORDER`
    # earns the same claim by being CONSUMED (`min(..., key=...index)`); this
    # one now does too.
    if toplevel:
        try:
            cache = _plugin_policy.read_cache(
                _plugin_policy.cache_path(toplevel))
            now = datetime.now(timezone.utc)
            # ⚠️ THE TUPLE DRIVES THE WALK, NOT THE FLOOR. Widest first, first
            # "on" wins, so a tenant holding both grants pays one resolve.
            # An earlier revision returned `PURPOSE_ORDER[0]` from the fallback
            # to make the tuple load-bearing; a reviewer pointed out that this
            # puts AUTHORIZATION under an ordering declaration — prepend a rung
            # and the ungranted floor silently becomes it. The tuple earns its
            # keep here, in the iteration, where a reordering changes only which
            # grant is CHECKED first and can never widen an ungranted row. The
            # floor stays a named constant, and a test pins the two together.
            for rung in reversed(PURPOSE_ORDER):
                feature = _PURPOSE_FEATURES.get(rung)
                # `local_only` has no feature: it is the floor, never granted.
                if feature is None:
                    continue
                if _plugin_policy.resolve_central(feature, cache, now) == "on":
                    return rung
        except OSError:
            # Narrow on purpose, and NARROWER than the first revision, which
            # also caught ValueError. `read_cache` already swallows its own
            # parse failures and answers None, so a ValueError reaching here
            # would be a parser or schema DEFECT — and catching it would make
            # that defect indistinguishable from an ungranted tenant, silently,
            # forever. Anything but a filesystem error therefore propagates to
            # the caller's handler, which drops the ROW. That is still safe (a
            # row never written cannot over-claim) and it is visible, because
            # captures going missing is something somebody investigates.
            pass
    return PURPOSE_LOCAL_ONLY


def class_consent_state(toplevel, letter):
    """The THREE-VALUED state of ONE class for `toplevel` — one of the
    `EVENTS_CONSENT_*` constants — reusing the shape `event_skeleton_consent`
    already owns, and for the identical reason: FAIL-CLOSED IS RIGHT FOR CAPTURE
    AND WRONG FOR DESTRUCTION.

      * REVOKED — the class key is the EXPLICIT boolean `false` inside a present,
        well-formed `consent` block. The only state that DISCARDS already-captured
        rows of that class.
      * GRANTED — the class is in `config_consent_classes`' list. That covers an
        explicit `true` AND the two defaulted grants (no block, no file), which is
        what keeps this change behaviour-neutral on every repo that exists today.
      * INDETERMINATE — everything else: the key absent inside a present block, a
        wrong-typed value, a malformed file or block, an unresolvable toplevel.
        Never sent, never discarded.

    REVOCATION IS DECIDED FIRST at the call site for the same reason
    `event_skeleton_consent` decides it first — see its docstring. The order is
    what makes an explicit `false` mean one thing regardless of what else the file
    says.

    ⚠️ WHAT MAY DESTROY, precisely, because the two halves are easy to conflate.
    The LIVE config read here may discard: an explicit `false` is a decision the
    company made and "turned off" would be false if it applied only to what had
    not happened yet. The FROZEN STAMP on a row — `consent_classes`, resolved at
    collection — may NEVER discard: it is a label describing what a row was
    collected under, and a stamp that destroyed would mean re-reading history as
    a revocation. That is the whole of "a class stamp must never become a second
    way to destroy a captured skeleton": the stamp is not a switch.

    THE CONFIG IS READ EXACTLY ONCE per call. Both halves below need the same
    file — the revocation test needs the `consent` block itself, the grant test
    needs the ladder over it — and asking `config_consent_classes` for the second
    by PATH re-stat-ed and re-parsed the file already in hand. `_classes_from_cfg`
    is that same ladder over the config this function already holds, so the two
    halves cannot answer from two different reads of a file edited between
    them."""
    if not toplevel:
        return EVENTS_CONSENT_INDETERMINATE
    path = consent_config_path(toplevel)
    present = os.path.isfile(path)
    cfg = _read_json(path) if present else None
    block = cfg.get("consent") if isinstance(cfg, dict) else None
    if isinstance(block, dict):
        for key, class_letter in _CONSENT_CLASS_KEYS:
            if class_letter == letter and block.get(key) is False:
                return EVENTS_CONSENT_REVOKED
    # An ABSENT file is not a shape `_classes_from_cfg` can see — it grants all
    # three (`no_config`), which is the branch `config_consent_classes` decides
    # above its own read, and the one that keeps this whole machine
    # behaviour-neutral on the repos that exist today.
    granted = (list(ALL_CONSENT_CLASSES) if not present
               else _classes_from_cfg(cfg)[0])
    if letter in granted:
        return EVENTS_CONSENT_GRANTED
    return EVENTS_CONSENT_INDETERMINATE


def read_frozen_stamp(raw):
    """`(classes, basis)` read out of a stamp ALREADY WRITTEN — the frozen half
    of the split, the mirror of `config_consent_classes`' live half. `raw` is the
    stored record: the loop lane's nested `state["consent"]` dict. (The ambient
    lane's stamp is four FLAT row keys, `ambient_digest.CONSENT_ROW_KEYS`; a
    caller holding that shape passes `{"classes": …, "basis": …}` — the adaptation
    is one line at that call site, and it is not this function's business which
    lane's storage it came out of.)

    ⚠️ IT EXISTS BECAUSE TWO READERS OF THE SAME RECORD HAD ALREADY DIVERGED, and
    not on a corner case. Given `{"classes": ["A","B","C"], "basis": <a basis name
    this build does not know>}` — measured 2026-08-14 by calling both functions
    on that one input, with a recognized basis as the positive control:

      * `run_gate_checks._consent_stamp` treated the whole record as unreadable
        and failed closed to `[]` + `pre_consent`, so a re-arm intersected the
        loop's grant down to NOTHING and shipped it that way;
      * `insights_flush_payload._consent` kept all three classes and relabelled
        the basis `explicit`.

    THE SECOND IS THE RULE, and it is the one already written down: "a future
    writer adding a fifth basis name must not cost this loop its whole grant" —
    which is exactly what a re-arm was doing. So the two facts are read
    INDEPENDENTLY, because they are two facts:

      * `classes` — the sorted, deduped subset of `ALL_CONSENT_CLASSES` when the
        stored value is a list; `[]` otherwise. Fail closed: a class list we
        cannot read is a grant we cannot claim, and over-withholding is the safe
        direction.
      * `basis` — as stored when it is one of `CONSENT_BASIS_ORDER`; otherwise
        `explicit`, for the same reason `config_consent_classes` gives an
        unparseable config file: a record that EXISTS is an attempt at a
        decision, and labelling it otherwise would tell the server nobody tried.

    AN UNREADABLE RECORD GETS ITS OWN NAME, `unreadable` — owner decision,
    2026-08-14. It was briefly `pre_consent` here and `explicit` in the payload
    builder, pinned apart by a test that said in as many words that NEITHER was
    defensible: `explicit` claims a decision was parsed when nothing was read,
    and `pre_consent` means "all three were in force" while being carried over
    an EMPTY grant. Both are statements about consent that the record does not
    support. The fix was a name for the state itself rather than a choice
    between two wrong ones, and it is the WEAKEST entry in
    `CONSENT_BASIS_ORDER` so an intersecting caller's
    `min(..., key=CONSENT_BASIS_ORDER.index)` cannot let a corrupt record
    inherit the strength of the window beside it.

    ⚠️ ABSENCE IS THE CALLER'S DECISION AND THIS FUNCTION WILL NOT MAKE IT.
    "No stamp at all" has a different right answer per lane — a fresh arm has no
    earlier window to intersect with, while an unstamped pre-change spool row is
    inferred as all three on `pre_consent` — so a shared default would impose one
    lane's policy on the other. Same asymmetry, same resolution, as
    `skeleton_consent_resolver` returning None rather than a state. Call this
    only once you have decided the record is PRESENT."""
    classes = raw.get("classes") if isinstance(raw, dict) else None
    if not isinstance(classes, list):
        return [], CONSENT_BASIS_UNREADABLE
    basis = raw.get("basis")
    return (sorted({c for c in classes if c in ALL_CONSENT_CLASSES}),
            basis if basis in CONSENT_BASIS_ORDER else CONSENT_BASIS_EXPLICIT)


def consent_stamp(toplevel):
    """The four flat row keys (`ambient_digest.CONSENT_ROW_KEYS`) recording the
    consent `toplevel` grants RIGHT NOW — the value frozen onto a registry row at
    collection time. `{}` for a falsy toplevel, so a row with no usable origin
    carries no stamp rather than a stamp resolved from nowhere.

    Called from ONE place, `_provenance_fields`, and that is structural rather
    than tidy: the consent that governs a session is that of the checkout whose
    transcript will actually be read, so the stamp has to be decided by the same
    code, at the same instant, as the toplevel it is about. See that function."""
    if not toplevel:
        return {}
    classes, basis = config_consent_classes(consent_config_path(toplevel))
    return {"consent_classes": classes,
            "consent_version": CONSENT_VERSION,
            "consent_basis": basis,
            "consent_content_mode": CONSENT_CONTENT_MODE}


def consent_classes_resolver():
    """A memoized `toplevel -> list[str] | None` over `config_consent_classes` —
    the LIVE half of the split, asked per session at the drain.

    Shaped exactly like `skeleton_consent_resolver` above, and for the same
    reasons: the spool is per-TENANCY (the git common dir), so linked worktrees of
    one repo share it while having their own toplevel and their own config; and
    the memo is per-CALL, so a config edited between two drains is always re-read.
    Reading CURRENT state is what preserves revoke-wins.

    RETURNS `None`, NEVER A LIST, for a toplevel it cannot use, because the two
    plausible defaults are both wrong. `[]` would withhold every classed field
    from every legacy row — a capture regression justified by nothing, since no
    one revoked anything. `["A","B","C"]` would assert a live grant that was never
    read. `None` means "no live resolution available", under which the caller
    applies no narrowing at all and `classes_applied == classes_at_collection` —
    the only honest answer when nothing was resolved, and the same rule the loop
    lane's builder applies to its own `granted_classes=None`."""
    memo = {}

    def resolve(toplevel):
        top = _clean_provenance_path(toplevel)
        if not top:
            return None
        if top not in memo:
            memo[top] = config_consent_classes(consent_config_path(top))[0]
        return memo[top]

    return resolve


def class_consent_state_resolver():
    """A memoized `(toplevel, letter) -> EVENTS_CONSENT_*` over
    `class_consent_state` — the THIRD of the per-call resolvers the drain asks
    per session, and the one that was missing between its two memoized siblings.

    MEASURED 2026-08-14, over ONE toplevel (the ordinary case: one repo per
    tenancy), by counting `_read_json` calls through the exact three-call body
    `run_drain` runs per candidate session — `skeleton_consent_resolver`,
    `class_consent_state`, `consent_classes_resolver`: 1 session cost 6 parses of
    `.fairmind-insights.json`, 5 sessions 14, 20 sessions 44. The two memoized
    siblings are the constant 4; every parse after them was this function's,
    re-reading one unchanged file per session in a loop that already had the
    answer. Re-measured the same way with this resolver and
    `class_consent_state`'s own single read in place: a constant 5, at 1, 5 and
    20 sessions alike.

    NOT `lru_cache`, and this is the trap rather than a preference. The memo is
    per-CALL — a fresh resolver per drain — so a config edited between two drains
    is always re-read, which is what preserves revoke-wins. A module-level cache
    would freeze the first answer for the life of the process and make an
    explicit `false` written mid-run invisible to the very code whose job is to
    honour it. Same rule, same words, as `consent_classes_resolver`.

    ⚠️ IT DIVERGES FROM ITS TWO SIBLINGS ON ONE POINT, DELIBERATELY: it does NOT
    clean the path and does NOT return `None` for a toplevel it cannot use. Those
    two return None because "this row records no repo" has different right
    answers at capture and at delivery, so the policy belongs to the call site.
    Here the policy is already decided, inside `class_consent_state` itself and
    pinned by its own test: an unusable toplevel is INDETERMINATE — neither sent
    nor discarded, because at this site fail-closed would mean DESTROY. Returning
    None instead would hand the caller a decision that has already been made, and
    a second place to get it wrong. So this resolver is exactly the function it
    memoizes, for every input, and the memo key is the raw pair."""
    memo = {}

    def resolve(toplevel, letter):
        key = (toplevel, letter)
        if key not in memo:
            memo[key] = class_consent_state(toplevel, letter)
        return memo[key]

    return resolve


# --------------------------------------------------------------------------- #
# The gate: FRESH every call from LIVE values (PCF-16).
# --------------------------------------------------------------------------- #

class Decision:
    # `toplevel` (T2-C3) is the git toplevel `evaluate_gate` already resolved on
    # its one-and-only `git rev-parse` and used to throw away. It is carried out
    # so `cmd_session_start` can answer `event_skeleton_enabled(toplevel)`
    # WITHOUT a second subprocess on the SessionStart fast path (the 5s hook
    # budget is the reason `_git_rev_parse` returns a pair in the first place).
    # Optional, defaulting to None, so every existing 3-positional
    # `Decision(...)` construction keeps working unchanged.
    __slots__ = ("capture", "tenancy", "reason", "toplevel")

    def __init__(self, capture, tenancy, reason, toplevel=None):
        self.capture = capture
        self.tenancy = tenancy
        self.reason = reason
        self.toplevel = toplevel


def evaluate_gate(cwd):
    """Decide should-capture FRESH from live config VALUES — no marker
    file is ever consulted as the signal (PCF-16). Fail-closed at every step.

    THE CENTRAL POLICY LAYER sits between `not_configured` and `opted_out`:
    a fresh central answer for `ambient_capture` overrides the local repo-file
    answer in BOTH directions (reasons `forced_off` / `forced_on`), while
    unset — no cache, a stale cache, any ambiguous shape — falls through to
    the local layer unchanged. `_plugin_policy.resolve_central` is the ONE
    precedence implementation (the judge Stop hook consults the same cache
    through the same function; two resolvers is how capture and delivery once
    came to disagree). It sits BELOW `no_tenancy` / `not_configured` on
    purpose: a central "on" cannot conjure a tenant — the privacy guard still
    short-circuits first. The cache itself is written ONLY by the detached
    sweep (`run_policy_refresh`); this fast-path read is one small file, no
    network."""
    # Resolve git toplevel + tenancy in ONE subprocess, shared by every step
    # below (Grok #8: no repeated git calls on the SessionStart fast path).
    toplevel, common = _git_rev_parse(cwd)
    tenancy = _tenancy_from_common(cwd, common)
    if not tenancy:
        return Decision(False, None, "no_tenancy", toplevel)
    if not fairmind_configured(cwd, toplevel):
        return Decision(False, tenancy, "not_configured", toplevel)
    if toplevel:  # `cache_path` needs a real path; no toplevel -> no central layer
        central = _plugin_policy.resolve_central(
            "ambient_capture",
            _plugin_policy.read_cache(_plugin_policy.cache_path(toplevel)),
            datetime.now(timezone.utc))
        if central is not None:
            # ANY non-None central answer is a FORCE, and only the exact "on"
            # opens capture. `resolve_central` emits nothing but "on", "off"
            # and None today, so this is the belt for a WIDENED vocabulary: a
            # force this gate cannot read must resolve to LESS capture, never
            # to the absence of a force. Falling through to the local layer
            # (the previous shape: `== "on"` / `== "off"`, otherwise nothing)
            # would let a value nobody here understands re-enable a lane the
            # platform was plainly trying to say something about. Same posture,
            # deliberately, as the review hook's silence check; the two
            # readers of one cache disagreeing is the defect this whole module
            # keeps one resolver to avoid.
            if central == "on":
                # Forced on: the local opt-out below is deliberately NOT read —
                # the platform's decision beats the repo file in both directions.
                return Decision(True, tenancy, "forced_on", toplevel)
            return Decision(False, tenancy, "forced_off", toplevel)
    answer = ambient_local_answer(toplevel)
    if answer is not True:
        # Two reasons for one outcome, because a person reads the reason: a
        # repository whose file says nothing about ambient capture never
        # decided, and calling that an opt-out would put words in its mouth.
        return Decision(False, tenancy,
                        "not_opted_in" if answer is None else "opted_out", toplevel)
    return Decision(True, tenancy, "capture", toplevel)


# --------------------------------------------------------------------------- #
# Registry + notice markers (atomic writes; opaque tenancy only).
# --------------------------------------------------------------------------- #

def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# The SessionStart `source` values the harness emits; anything else normalizes to
# "other" so no arbitrary payload string is persisted verbatim into a row (N2b).
_ALLOWED_ENTRY_SOURCES = {"startup", "resume", "compact", "clear", "other"}
_MAX_SESSION_ID_LEN = 200
_BAD_ID_CHARS = frozenset("/\\\n\r\t")


def _clean_session_id(session_id):
    """A plausible session id: a non-empty, bounded string with no path separator
    or control char. Anything else (odd type, over-long, or path/newline junk like
    '../../etc/passwd\\ninjected') is DROPPED to "" so a raw path can never be
    injected into a supposedly path-free registry row (N2b). Applied identically
    on register and on end-match so a dropped id still pairs with its end-marker."""
    if not isinstance(session_id, str):
        return ""
    if not session_id or len(session_id) > _MAX_SESSION_ID_LEN:
        return ""
    if any(c in _BAD_ID_CHARS for c in session_id):
        return ""
    return session_id


def _clean_entry_source(entry_source):
    """Allowlist the SessionStart source; an unknown/odd value -> "other" (N2b)."""
    return entry_source if entry_source in _ALLOWED_ENTRY_SOURCES else "other"


# OPEN-1 F9 — the FIRST raw value a registry row has ever carried, so it gets a
# validator in the same idiom as `_clean_session_id` / `_clean_entry_source`
# above. A provenance path arrives in the SAME untrusted SessionStart payload
# `_resolve_cwd` refuses to trust for arming, so it is bounded, required to be
# absolute, and screened for control characters.
#
# 4096 IS NOT "POSIX PATH_MAX", which this comment claimed until 2026-07-30
# (cross-model pre-PR review). There is no single such number: POSIX guarantees
# only `_POSIX_PATH_MAX` = 256, Linux uses 4096, and `getconf PATH_MAX /` on this
# macOS host returns 1024. So the bound is a SANITY CEILING and not a portable
# limit — it exists to stop an absurd value being persisted, and it deliberately
# does NOT try to predict what `open()` will accept, because that varies by host
# and by filesystem. A 1500-character value therefore passes here and fails later
# at the existence oracle, which is the correct place for it to fail: the oracle
# asks the filesystem instead of guessing on its behalf.
_MAX_PROVENANCE_PATH_LEN = 4096

# THE ALLOWLIST — the ONLY registry-row keys permitted to hold a raw local path,
# declared here because two different tests turn on it and both used to respell
# it as literals of their own.
#
# `test_open1_row_provenance.py` asserts that exactly these keys carry a path
# (positively, so the exemption cannot silently become "no provenance at all"),
# and `test_pla1a_session_gate.py` EXEMPTS them from its privacy negative-space
# scan of the whole row. That second use is why a hand-copied set was a real
# hole rather than a tidiness point: a THIRD provenance key added to the row
# would have been scanned by one test and silently exempted by neither — or, had
# someone widened the local copy in the exempting test alone, exempted without
# ever being declared. One declaration, in the module that WRITES the row.
#
# ONLY ONE OF THE TWO TESTS READS IT, and that is deliberate — this comment
# claimed "both tests read it" until 2026-07-30 (cross-model pre-PR review), one
# edit after the other test declined to. `test_open1_row_provenance.py` reads it,
# because its assertion is POSITIVE (exactly these keys carry a path) and an
# imported list that drifts wider still turns it red. `test_pla1a_session_gate.py`
# keeps its own literal ON PURPOSE, because its use is an EXEMPTION from a privacy
# scan: importing this tuple would point the exemption at the thing being scanned,
# so every key added here would exempt itself from that scan the moment it was
# written. A literal there can only ever be too NARROW, and too narrow is the safe
# direction — an undeclared third key gets SCANNED rather than silently skipped.
# Same constant, right in one test and wrong in the other, decided by which way
# each fails when it is out of date.
#
# `register_session` / `_touch_open_entry_source` still spell the two keys as
# literals at the point of assignment. That is deliberate: the two writers apply
# DIFFERENT rules to the two keys (`transcript_dir` refreshes on the resume path
# only under an existence oracle), so there is no loop to fold them into, and
# indexing this tuple positionally would couple the row's shape to its ORDER for
# no gain. A rename in a writer without a rename here turns the assertions above
# red, which is the property the constant exists to provide.
#
# CORRECTION, 2026-07-30, recorded beside the paragraph it overturns because that
# paragraph's premise was the defect. "The two writers apply DIFFERENT rules to
# the two keys" is no longer true, and it never should have been: applying two
# rules to the two halves of one origin is what let a row carry directory A with
# toplevel B. Both writers now go through `_provenance_fields`, which decides the
# pair as ONE value; the only remaining difference is between the two WRITERS
# (append vs refresh), never between the two KEYS.
#
# The rest of the paragraph survives verbatim and is now the whole reason the
# keys are literals: `_provenance_fields` spells them out rather than indexing
# this tuple positionally, because indexing would couple the row's shape to this
# tuple's ORDER for no gain. There is simply ONE such spelling now instead of
# three, which is what makes "a rename without a rename here turns the assertions
# above red" a property of one site rather than a hope about several.
PROVENANCE_ROW_KEYS = ("toplevel", "transcript_dir")


def _clean_provenance_path(value):
    """A plausible ABSOLUTE local directory path, or None.

    Fails to None — the caller then OMITS the field rather than persisting junk,
    which is safe precisely because `_resolve_transcript_dir`'s fallback still
    routes a row with no recorded provenance (F2 step 2) and `run_sweep` treats a
    row with no recorded toplevel as fail-closed (F4). So a rejected value costs
    a row nothing it was not already able to survive, whereas a persisted one
    would be a raw attacker-chosen string in a row whose whole discipline is that
    no such string lands in it unvalidated.

    Absolute-only on purpose: a relative path is meaningless to the sweep, which
    runs in a detached background process whose cwd is not the session's."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) > _MAX_PROVENANCE_PATH_LEN:
        return None
    if not os.path.isabs(value):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    return value


def _transcript_file(transcript_dir, session_id):
    """Where `session_id`'s own transcript would live under `transcript_dir`.

    ONE definition, because the EXISTENCE of this file is OPEN-1's correctness
    oracle for "is this the right transcript directory" (the file is named by
    session id, so its presence is strong evidence) and that question is asked
    from three places — the resume refresh above, F2's resolution order, and the
    tests' own fixtures. Three spellings of one path join is how the resume path
    and the sweep path would come to disagree about which directory is right.

    POINTER CORRECTION, 2026-07-30: "the resume refresh" no longer asks this
    directly. It asks through `_provenance_fields` (immediately below), which is
    now the ONE site that applies the existence oracle on behalf of
    `_touch_open_entry_source`. The count of three is unchanged; only where the
    first of them lives is."""
    return os.path.join(transcript_dir, session_id + ".jsonl")


def _provenance_fields(session_id, toplevel, transcript_dir, *,
                       replaces_existing):
    """The provenance a row is to carry, decided as ONE value rather than as two
    fields updated side by side: **a row describes one origin, or it describes
    the previous one; never a blend.** Returns the mapping the caller merges into
    the row — `{}` means "write nothing", which on the refresh path means the row
    keeps the origin it already had.

    ONE helper for BOTH writers (`register_session` and
    `_touch_open_entry_source`) on purpose. The invariant is then STRUCTURAL —
    the two fields cannot be written apart because there is no code path that
    writes one of them — instead of a convention two call sites have to remember,
    which is precisely how the defect below arrived.

    JC5 WIDENS "ONE ORIGIN" TO INCLUDE THE CONSENT STAMP, and it is written HERE
    rather than beside this call for exactly the reason the defect below states.
    A session registered in checkout A (classes `["A","B","C"]`) and resumed from
    checkout B (classes `["A"]`) would, with three consent keys written on the
    row literal instead, keep A's classes while `toplevel` and `transcript_dir`
    both moved to B — the sweep would then digest B's transcript and ship it
    stamped with A's decision. That is the same blend, one field wider, one step
    from its own fix. The stamp is therefore resolved FROM the toplevel this
    function is about to return, and returned in the same mapping, so there is no
    code path that writes one without the others.

    `Decision.__slots__` is deliberately NOT widened to carry classes. With the
    resolution here it would be unnecessary, and on the resume path it would be
    actively wrong: the Decision was built at SessionStart against the checkout
    the developer launched from, which is not always the checkout whose transcript
    is read.

    THE DEFECT, 2026-07-30. The resume path used to refresh the two fields under
    DIFFERENT rules — `toplevel` whenever a valid value arrived (consent must be
    read against the repo the session is actually in), `transcript_dir` only when
    the candidate actually held `<session_id>.jsonl` (so a not-yet-written
    transcript could not blank a good pointer). Each is defensible ALONE.
    Together they let one row describe TWO origins: resume from another cwd
    before the new transcript exists and the row carries **directory A with
    toplevel B**. The sweep then digests A's transcript and applies B's consent
    decision to it — data collected in one checkout, governed by another
    checkout's decision, which is the exact class OPEN-1 exists to close.

    THE SEMANTIC ANSWER THIS ENCODES. When a session spans two checkouts, the
    consent that governs it is that of THE CHECKOUT WHOSE TRANSCRIPT WE ARE
    ACTUALLY READING, because consent governs the DATA, not the person's current
    location. Moving `toplevel` alone answers "where is the developer now", which
    is not the question a consent decision answers.

    THE HONEST RESIDUAL, stated rather than left to be discovered: such a session
    is ATTRIBUTED WHOLE to the readable checkout rather than SPLIT between the
    two. The work done after the resume is not re-attributed to B — it is
    reported under A, or (when B's transcript is the one that exists) under B.
    A registry row is one row with one origin, so splitting a session across two
    would need a different record shape, not a different rule here.

    `replaces_existing` is the only difference between the two writers, and it is
    the existence oracle plus its precondition:

      * True (`_touch_open_entry_source`, an EXISTING row) — replace the whole
        origin only when the new context is FULLY RESOLVABLE: both values valid
        AND the candidate directory actually holds `<session_id>.jsonl`.
        Otherwise `{}`, and the previous origin stands.
      * False (`register_session`'s APPEND, a NEW row) — no oracle, and a
        partial record is accepted. There is no previous origin to blend with:
        both values come from ONE SessionStart, so a row that records only what
        validated still describes exactly one context. The oracle would be
        actively wrong here — at a genuinely new SessionStart the harness-supplied
        `transcript_path` is THIS session's own authoritative path and the file
        may not have been flushed yet, so requiring it to exist would leave every
        fresh session with no provenance at all, which is the state the fix
        exists to end. Requiring the PAIR would be worse still: a payload with no
        `transcript_path` would then also lose its `toplevel`, and a row with no
        recorded toplevel fails closed at capture (`_row_grants_skeleton`) and is
        retained-never-sent at delivery (`run_drain`) — forever."""
    top = _clean_provenance_path(toplevel)
    tdir = _clean_provenance_path(transcript_dir)
    if replaces_existing:
        if not top or not tdir:
            return {}
        if not os.path.isfile(_transcript_file(tdir, session_id)):
            return {}
        # THE CONFIG READ HAPPENS AFTER THE ALL-OR-NOTHING DECISION, NEVER BEFORE
        # IT. Resolving the stamp first and then refusing the origin would build
        # the blend one level up — the row would keep checkout A's paths and gain
        # checkout B's classes, which is the 2026-07-30 defect with a third field
        # in it. Every return above this line is a refusal, and a refusal reads no
        # config at all.
        fields = {"toplevel": top, "transcript_dir": tdir}
        fields.update(consent_stamp(top))
        return fields
    fields = {}
    if top:
        fields["toplevel"] = top
        # Bound to `toplevel` and to nothing else: the stamp is a fact ABOUT that
        # checkout's config, so a row that could not record a toplevel must not
        # carry a consent decision either. Such a row already fails closed at
        # capture (`_row_grants_skeleton`) and is retained-never-sent at delivery;
        # an unstamped row is picked up at the drain as `pre_consent`, which is
        # the honest label for "nobody resolved this".
        fields.update(consent_stamp(top))
    if tdir:
        fields["transcript_dir"] = tdir
    return fields


def _ensure_private_dir(directory):
    """Create `directory` (and its immediate parent) and lock both to mode 0700 —
    owner-only — regardless of a permissive ambient umask (N5). Best-effort; never
    raises. `append_row`'s own makedirs(exist_ok=True) then leaves the mode
    untouched, so the sessions/marker dirs stay private.

    CORRECTION, 2026-07-30: that second sentence is now VESTIGIAL rather than
    wrong. No caller of this function reaches `append_row` any more — the
    registry appends through `_durable_append`, which calls this and then opens
    the file directly, with no makedirs of its own to leave a mode untouched.
    The property it was defending (the dir stays 0700) is now simply this
    function's, unconditionally."""
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return
    for d in (directory, os.path.dirname(directory)):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _ensure_private_file(path):
    """Ensure the registry/marker file at `path` exists mode 0600 — owner-only —
    before it is appended to, so `open(path, "a")` under a 0022 umask never lands
    it 0644 world-readable (N5). Best-effort; never raises."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
    except OSError:
        pass


def _durable_append(path, line):
    """Durably append ONE line to the private JSONL file at `path` — the
    UNBOUNDED, NEVER-ROTATING counterpart to `_loop_ledger.append_row`.

    IT IS NOT SPOOL-SPECIFIC (2026-07-30). It was born as `_spool_append` (see
    below, which is now the spool's named policy wrapper over it) and its
    mechanism is simply "durably append one line under a blocking lock", so the
    SESSION REGISTRY appends through it too: both files are records that must
    survive, not bounded ledgers whose oldest rows are expendable.

    NEVER rotates/trims: every row is retained regardless of how many
    accumulate. Removal, where a file needs it at all, is the caller's
    STATE-DRIVEN business (`ambient_outbox._compact_spool` for the spool,
    `_compact_registry` for the registry) — never this writer's, and never by
    age.

    It also NEVER swallows an IO failure (disk full, permission, a directory
    sitting where the file belongs, ...): any exception propagates to the
    caller, which is what lets `_digest_one_session` decide NOT to stamp
    `digested_at` (Fix 2) rather than silently losing a rollup forever. A caller
    that must not raise — `register_session`, on the SessionStart hook path —
    re-establishes fail-soft AT ITS OWN LEVEL; see the seam comment there.

    Locking mirrors every other lock in this module: a BLOCKING `fcntl.flock`
    (never `LOCK_NB` — a concurrent appender must WAIT its turn, never skip its
    own write) guarded by `if _fcntl is not None`, degrading to a best-effort,
    non-exclusive append on a non-POSIX host rather than refusing to run.
    `flush()` + `os.fsync()` before releasing the lock make the append durable
    across a crash immediately after this call returns.

    THE LOCK IS ON THE FILE'S OWN FD, which is why every rewriter of a file
    written through here rewrites it IN PLACE under the same fd lock rather than
    via mkstemp+`os.replace`: a rename shares no lock with an fd-flock appender,
    so a concurrent append would land on the dangling old inode and be silently
    lost."""
    _ensure_private_dir(os.path.dirname(path))
    _ensure_private_file(path)
    fh = open(path, "a", encoding="utf-8")  # raises IsADirectoryError etc.
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)  # blocking
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _read_registry_lines(path):
    """The non-blank lines of the registry at `path`, or None if it cannot be
    read (absent/unreadable). Never raises."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return [ln for ln in fh if ln.strip()]
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Tenancy-wide registry lock (PL-A1b review Fix 3).
#
# The per-session flock in `_digest_one_session` only ever protects the SAME
# session against a second concurrent digest of ITSELF; it says nothing about
# two DIFFERENT sessions' registry rewrites racing on the ONE shared registry
# file. `_atomic_write_lines`'s reconcile-on-replace only re-folds a file that
# grew LONGER since the snapshot (a concurrent APPEND) — a same-length
# concurrent REWRITE (a concurrent stamp/end-marker) is invisible to it, so
# whichever writer's atomic replace lands LAST silently clobbers the other's
# already-applied change. `_stamp_digested` and `mark_session_end` — the two
# rewriters that matter for this fix's own RED tests — now hold this lock
# across their OWN read + `_atomic_write_lines`, so two concurrent rewrites of
# the SAME tenancy's registry always serialize instead of racing.
# `_touch_open_entry_source` deliberately does NOT take it; see its own
# docstring for why (a real, test-proven self-deadlock via `fcntl.flock`'s
# per-file-description, non-reentrant scoping, not a hypothetical concern).
# --------------------------------------------------------------------------- #

def _registry_lock_path(tenancy):
    return os.path.join(data_dir(), "insights", "locks", tenancy + ".registry.lock")


@contextlib.contextmanager
def _registry_write_lock(tenancy):
    """Serialize a registry read-modify-write rewrite TENANCY-WIDE. Blocking
    `fcntl.flock` (never `LOCK_NB` — a concurrent rewriter must WAIT its turn
    and still land its change, never skip and lose it), guarded the same way
    every other lock in this module is (`if _fcntl is not None`) so it
    degrades to a best-effort no-op (no real cross-process exclusion) on a
    non-POSIX host rather than refusing to run.

    This is a DIFFERENT lock file from the per-session digest lock
    (`_lock_path`), acquired only for the brief span of one rewrite and always
    released before the caller returns — never held while blocking
    indefinitely on anything else — so it cannot deadlock against the
    per-session lock: the per-session lock (held, at most, by
    `_digest_one_session`) may enclose an acquisition of THIS lock (via
    `_stamp_digested`), but this lock never tries to acquire a per-session
    lock, so the two can only nest in one direction, never both ways around."""
    lock_dir = os.path.join(data_dir(), "insights", "locks")
    _ensure_private_dir(lock_dir)
    lock_path = _registry_lock_path(tenancy)
    _ensure_private_file(lock_path)
    lock_fh = None
    try:
        lock_fh = open(lock_path, "a+")
    except OSError:
        lock_fh = None
    acquired = False
    try:
        if lock_fh is not None and _fcntl is not None:
            try:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX)  # blocking
                acquired = True
            except OSError:
                acquired = False
        yield
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


def _touch_open_entry_source(path, session_id, entry_source, *,
                             toplevel=None, transcript_dir=None):
    """Idempotency seam for N4: if a LIVE (ended_at is None) row for `session_id`
    already exists, update its entry_source in place (a repeated SessionStart from
    resume/compaction is the SAME session, not a new one) and return True; else
    return False so the caller appends a fresh row. Best-effort — any failure ->
    False (fall back to append). The rewrite reconciles concurrent appends so a
    row added since the snapshot is never clobbered.

    OPEN-1 F1/F11 — it also REFRESHES the row's provenance, because a resumed
    session can be launched from a different cwd and a STALE provenance value
    would re-introduce the very defect F2 exists to fix, on the row that looks
    correct. The refresh is now ALL-OR-NOTHING and lives in `_provenance_fields`
    (2026-07-30): **a row describes one origin, or it describes the previous one;
    never a blend.** Read that function for the rule and for the semantics it
    encodes; the two bullets below are kept because they are the argument that
    has to be understood to see why it changed.

    THE TWO FIELDS USED TO REFRESH UNDER DIFFERENT RULES, and this is the
    superseded wording, recorded beside rather than deleted because each rule
    read as correct on its own and only the PAIR was wrong — a row could come to
    carry directory A with toplevel B, so the sweep digested A's transcript and
    applied B's consent to it. Both fields now move together or not at all, and
    "not at all" is what a resume before the new transcript exists gets:

      * `toplevel` refreshes whenever a valid new value is supplied. It decides
        consent (F4), and consent must be read against the repo the session is
        ACTUALLY in. SUPERSEDED 2026-07-30: it now refreshes only TOGETHER WITH
        the transcript dir. "The repo the session is actually in" was the wrong
        question — consent governs the DATA, so the repo that matters is the one
        whose transcript the sweep will actually read, not the one the developer
        is sitting in now.
      * `transcript_dir` refreshes only if the new candidate ACTUALLY CONTAINS
        `<session_id>.jsonl`; otherwise the stored value is kept. Whether a
        resumed session's transcript follows a moved cwd is genuinely unsettled
        (the local corpus cannot distinguish "the path is stable" from
        "resume-from-another-cwd never happened here"). A row with nothing stored
        and a candidate that does not hold the file simply keeps no provenance,
        which F2's fallback still routes.

        NARROWED 2026-07-30 (cross-model pre-PR review), beside the original
        wording rather than in place of it: this bullet used to end "and the
        existence oracle is correct under BOTH answers, so no probe is needed to
        choose". THAT IS TRUE ONLY WHILE AT MOST ONE CANDIDATE HOLDS THE FILE.
        When BOTH hold it, the oracle does not discover which is right —
        `_resolve_transcript_dir` PREFERS THE ROW'S, by policy, and never looks
        at the launcher's. So the reachable loss is: a session starts under cwd A
        (row records A, `A/<sid>.jsonl` written), resumes under cwd B BEFORE
        `B/<sid>.jsonl` exists (the oracle correctly refuses the empty B and
        keeps A), the harness then continues that session under B while A's now
        stale file remains — and the sweep builds the rollup from A, reports
        `parserDegraded=False` because A parsed fine, and stamps `digested_at`,
        so the post-resume work is gone with nothing red anywhere.

        STILL TRUE AFTER THE ALL-OR-NOTHING CHANGE (2026-07-30) — the loss is
        not repaired here, it is REDUCED, and saying which half went is the
        point. The row now keeps A's `toplevel` as well as A's dir, so what
        remains is purely a DATA loss: B's post-resume work is silently absent.
        What is gone is the GOVERNANCE half — A's transcript is no longer read
        under B's consent decision. The MEASURED paragraph below applies
        unchanged, and so does the live probe it asks for.

        MEASURED, so the residual is sized rather than asserted: over
        ~/.claude/projects/*/*.jsonl on 2026-07-30, 250 distinct session ids
        across 98 project directories, and **0** appear in more than one — the
        dual-file precondition has never occurred here. That is evidence about
        FREQUENCY and not about the claim, which is why the claim is narrowed
        instead of defended. Settling it properly still needs the live probe the
        original bullet argued could be skipped: resume a session from a
        different directory and observe which file grows.

    Deliberately does NOT take the tenancy-wide registry lock (Fix 3, PL-A1b
    review): `register_session` (this function's only caller) is itself
    sometimes invoked from WITHIN another rewriter's own critical section —
    e.g. `test_pla1a_review_fixes.py`'s test_C injects a concurrent
    `register_session` call from inside a patched `_atomic_write_lines` while
    `mark_session_end` still holds the registry lock for the SAME tenancy —
    and `fcntl.flock` is scoped to the open file description, not the
    process/thread, so a second, independent lock acquisition by the same
    thread on a different fd self-deadlocks (blocks forever waiting on a lock
    only that same thread could release). `mark_session_end` and
    `_stamp_digested` carry the lock instead, which already closes the
    concurrent-DIFFERENT-session-stamp race this fix targets."""
    lines = _read_registry_lines(path)
    if not lines:
        return False
    out, found = [], False
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            out.append(ln if ln.endswith("\n") else ln + "\n")
            continue
        if (isinstance(row, dict) and not found
                and (row.get("session_id") or "") == session_id
                and row.get("ended_at") is None):
            row["entry_source"] = entry_source
            # `entry_source` is the N4 idempotency contract and is NOT part of
            # provenance: it refreshes unconditionally, above, so a REFUSED
            # origin never costs the row its source. Provenance is one value —
            # `{}` here means the row keeps the origin it already had.
            row.update(_provenance_fields(session_id, toplevel, transcript_dir,
                                          replaces_existing=True))
            found = True
        out.append(json.dumps(row) + "\n")
    if found:
        _atomic_write_lines(path, out, reconcile_from=len(lines))
    return found


def register_session(tenancy, session_id, started_at, entry_source, *,
                     toplevel=None, transcript_dir=None):
    """Register a live session. Carries the OPAQUE tenancy id ONLY — never a raw
    path/branch.

    CORRECTION, OPEN-1 (owner decision 2026-07-30), recorded BESIDE the sentence
    it narrows rather than replacing it: the row now ALSO carries two raw LOCAL
    paths — `toplevel` and `transcript_dir` — so the property above is narrowed
    from "never persisted" to "never WIRE-BOUND". The registry is local, 0600 and
    never transmitted; the wire payload is built from `meta`, which does NOT gain
    either field, and `test_open1_row_provenance.py` pins both halves (no path
    outside those two keys in the row, and neither value anywhere in
    `build_wire_payload`'s output). Real exposure is unchanged:
    `~/.claude/projects/<slug-of-cwd>` already exists in cleartext on the same
    disk, and the branch name is still never recorded anywhere.

    WHY THE ROW HAS TO CARRY THEM. `run_sweep` used to apply the LAUNCHING
    session's transcript dir and consent decision to every ended-not-digested row
    in the shared registry. `~/.claude/projects/<slug>` is slugged from the CWD
    while the tenancy is slugged from the git COMMON DIR, so a subdirectory cwd
    shares tenancy AND toplevel and still has its own transcript directory — and
    a missing transcript takes `digest_transcript_file`'s SUCCESS path, so the
    loss surfaced as a clean-looking zero (`parserDegraded` False, `rollups` []),
    indistinguishable on the wire from a session in which the developer did
    nothing. A row now records the context it was captured under and the sweep
    uses the ROW's context, never the launcher's.

    `session_id`/`entry_source` are VALIDATED first (N2b): a
    path/newline-bearing or over-long id is dropped to "", and an odd source
    normalizes to "other", so no attacker/odd payload value lands raw in a row.
    `toplevel`/`transcript_dir` go through `_clean_provenance_path` for the same
    reason (F9) and are OMITTED entirely when it rejects them — never persisted
    as junk, never persisted as an explicit null.

    NARROWED 2026-07-30, beside the sentence rather than in place of it: they go
    through `_provenance_fields`, which calls that validator — and they are no
    longer omitted INDEPENDENTLY. On THIS function's append path a partial record
    is still written exactly as described (one origin, recorded as far as it
    validated). On the resume path below, the pair is all-or-nothing: a rejected
    or unresolvable value costs BOTH fields and the row keeps the origin it
    already had, because a row that mixes one field from each of two contexts
    describes neither.

    Idempotent per session_id (N4): a repeated SessionStart (resume/compaction) for
    an already-registered LIVE session updates that row's entry_source instead of
    appending a duplicate open row. An empty/unknown id cannot be identified, so it
    always appends. The dir/file are pre-created owner-only (0700/0600) so the
    registry is never world-readable under a permissive umask (N5). `append_row`
    (PL-A0) is locked, atomic and bounded and swallows every error, so a wedge here
    skips the row (fail-soft) rather than racing the end-marker with a bare append.

    CORRECTION, 2026-07-30, recorded BESIDE the sentence it overturns because that
    sentence names the exact property that was the defect: "and BOUNDED". Bounded
    meant `append_row`'s `cap=2000` rotation, which trims the registry to its
    newest 1500 rows OLDEST-FIRST and is BLIND to row state — so a plain
    SessionStart append could delete an EARLIER session's row, taking its
    `toplevel`, `degraded_reason` and `digested_at` with it. `_registry_toplevels`
    could then no longer answer for that session and `run_drain` treated it as
    unknown-repo forever: retained, never sent, never discarded. The registry had
    both faults at once — it never removed a SETTLED row and it did remove
    UNSETTLED ones, by age — which is the opposite of both halves of the rule.

    The write is therefore UNBOUNDED now (`_durable_append`, the same primitive
    the spool uses); reclamation is STATE-DRIVEN and lives in `_compact_registry`,
    which the BACKGROUND SWEEP calls. "locked" and "atomic" survive verbatim, and
    the append is now additionally fsynced.

    "swallows every error" also survives, but it is no longer a property of the
    WRITER — see the seam comment at the call site."""
    session_id = _clean_session_id(session_id)
    entry_source = _clean_entry_source(entry_source)
    path = _registry_path(tenancy)
    _ensure_private_dir(os.path.dirname(path))
    _ensure_private_file(path)
    # The RAW values go through: `_provenance_fields` validates them, at the one
    # site that decides what a row's origin is. Pre-cleaning here and again there
    # would be the two-places-to-forget shape the helper exists to remove.
    if session_id and _touch_open_entry_source(path, session_id, entry_source,
                                               toplevel=toplevel,
                                               transcript_dir=transcript_dir):
        return
    row = {
        "schema": _SCHEMA,
        "tenancy": tenancy,
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": None,
        "entry_source": entry_source,
        "digested_at": None,  # stamped by run_sweep (PL-A1b) once digested
    }
    # The APPEND path stores the transcript dir UNCONDITIONALLY (no existence
    # oracle), unlike the resume/touch path above. At a genuinely new
    # SessionStart the harness-supplied `transcript_path` is THIS session's own
    # authoritative path and the file may not have been flushed yet — requiring
    # it to exist here would leave every fresh session with no provenance at
    # all, which is the state the fix exists to end. That difference, and the
    # reason a partial record is safe on THIS path only, now lives in
    # `_provenance_fields` as `replaces_existing=False` rather than as two rules
    # spelled out at two call sites.
    row.update(_provenance_fields(session_id, toplevel, transcript_dir,
                                  replaces_existing=False))
    # THE SEAM (2026-07-30). `_durable_append` deliberately PROPAGATES IO errors
    # — the spool's digest path needs that, because a swallowed failure there
    # would stamp `digested_at` over a rollup that was never written. THIS
    # function runs inside the SessionStart hook, which must never break session
    # open nor blow its ~5s budget, so the fail-soft contract `append_row` used to
    # give for free is re-established HERE, at the hook boundary, and nowhere
    # lower: a wedged registry skips the row rather than raising into the hook.
    # The fsync `_durable_append` performs costs ONE per session OPEN (not one per
    # tool call, which is why `append_row`'s hot-path economics do not apply) and
    # must not be "optimized" away: an unfsynced row is a session whose provenance
    # a crash can erase, which is the class of loss this whole fix is about.
    try:
        _durable_append(path, json.dumps(row))
    except Exception:
        pass


def mark_session_end(tenancy, session_id, ended_at):
    """Stamp `ended_at` on the matching (tenancy, session_id) row. No-op if the
    registry does not exist (a never-captured session writes nothing) — the end
    marker never CREATES a registry.

    The rewrite routes through the shared `_loop_ledger._atomic_write_lines`
    (the MODULE reference, not the bare imported name, so a test's monkeypatch
    of `_loop_ledger._atomic_write_lines` is observed at CALL time — the same
    reason `stale_loop_marker` reaches `_loop_ledger.resolve_loop_context`
    through the module rather than a `from`-imported binding) with a
    `reconcile_from` boundary (the snapshot's row count): it re-reads the file
    immediately before the atomic replace and folds in any rows a CONCURRENT
    `register_session` appended since the snapshot, so a live row is never
    clobbered (Grok #3). `session_id` is normalized identically to register (via
    `_clean_session_id`) — a row registered with a missing/None or junk id ("")
    still matches its end-marker (Grok #4). Closes ALL matching open rows: N4 makes
    register idempotent so there is normally one, but if duplicate open rows already
    exist (from before the fix) SessionEnd stamps every matching one.

    Holds the tenancy-wide registry lock (Fix 3) across the read + write, so a
    concurrent _stamp_digested/_touch_open_entry_source rewrite of the SAME
    registry file is serialized rather than racing (last-writer-wins)."""
    path = _registry_path(tenancy)
    with _registry_write_lock(tenancy):
        lines = _read_registry_lines(path)
        if lines is None:
            return
        want = _clean_session_id(session_id)
        changed = False
        out = []
        for ln in lines:
            try:
                row = json.loads(ln)
            except Exception:
                out.append(ln if ln.endswith("\n") else ln + "\n")
                continue
            if (isinstance(row, dict)
                    and (row.get("session_id") or "") == want
                    and row.get("ended_at") is None):
                row["ended_at"] = ended_at
                changed = True
            out.append(json.dumps(row) + "\n")
        if changed:
            _loop_ledger._atomic_write_lines(path, out, reconcile_from=len(lines))


# The two insights REST doors, as PATHS on the Fairmind MCP's own origin. Named
# constants because both `_derive_insights_endpoint` and
# `fairmind_events_endpoint` build a url from them, and because the events path
# is the one literal the project-context side has to agree with (it is repeated,
# deliberately, in `tests/test_events_conformance.py`'s module docstring — the
# only written contract the server half has for it, since the shared fixture
# pins the request BODY and cannot pin a route).
_ACTIVITY_ENDPOINT_PATH = "/insights/v1/session-activity"
_EVENTS_ENDPOINT_PATH = "/insights/v1/session-events"


def _derive_insights_endpoint(mcp_url, path=_ACTIVITY_ENDPOINT_PATH):
    """From a Fairmind MCP url (e.g. ``…/mcp`` or ``…/mcp/mcp``) derive an
    insights REST door on the SAME origin:
    ``<scheme>://<host>[:port]<path>``. Returns None for anything without a
    usable scheme+host. Deriving from the MCP url (rather than accepting a
    separately-configurable endpoint) is deliberate: the bearer below can then
    only ever be sent to the exact host that already holds it.

    `path` defaults to the session-activity door, so every pre-T2-C3 caller is
    unchanged; T2-C3's events door reaches it through
    `fairmind_events_endpoint`, which re-derives from the ALREADY-DERIVED
    activity url rather than from config."""
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(mcp_url)
        if not parts.scheme or not parts.netloc:
            return None
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    except Exception:
        return None


def fairmind_events_endpoint(activity_endpoint):
    """T2-C3: the EVENT SKELETON's door, derived from the already-resolved
    session-activity endpoint by swapping the path only. None in, None out.

    Deriving from the activity endpoint rather than re-reading config is what
    makes "same origin" structural: the events door is, by construction, on the
    exact host that `fairmind_delivery_target` already decided may hold this
    tenant's bearer. A separately configurable events url would be a second
    place a token could be pointed at, which is the property
    `_derive_insights_endpoint` exists to deny."""
    if not activity_endpoint:
        return None
    return _derive_insights_endpoint(activity_endpoint, _EVENTS_ENDPOINT_PATH)


def fairmind_delivery_target(cwd, toplevel):
    """Resolve (endpoint_url, bearer_token) for the PER-PROJECT Fairmind MCP the
    same way :func:`fairmind_configured` arms — PRIMARY project entry only, an
    enabled (not-disabled) Fairmind key — reading the server's own ``url`` and
    ``headers.Authorization``. The endpoint is DERIVED from that url, never taken
    from a separate config, so a token is bound to its issuing host. Returns
    (None, None) on any uncertainty (no per-project Fairmind MCP, disabled,
    missing url/header) — the caller degrades to a spool-only no-op. The token is
    returned for immediate per-request use only; it is never persisted or logged."""
    claude_json = _read_json(os.path.join(os.path.expanduser("~"), ".claude.json"))
    projects = claude_json.get("projects") if isinstance(claude_json, dict) else None
    projects = projects if isinstance(projects, dict) else {}
    proj = projects.get(cwd)
    if not isinstance(proj, dict):
        proj = projects.get(toplevel) if toplevel else None
    if not isinstance(proj, dict):
        return None, None
    servers = proj.get("mcpServers")
    matched = _fairmind_keys(servers)
    if not matched:
        return None, None
    disabled = proj.get("disabledMcpServers")
    disabled = disabled if isinstance(disabled, list) else []
    for key in matched:
        if key in disabled:
            continue
        srv = servers.get(key)
        if not isinstance(srv, dict):
            continue
        url = srv.get("url")
        headers = srv.get("headers")
        auth = headers.get("Authorization") if isinstance(headers, dict) else None
        if not isinstance(url, str) or not isinstance(auth, str):
            continue
        endpoint = _derive_insights_endpoint(url)
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else auth.strip()
        if endpoint and token:
            return endpoint, token
    return None, None


# --------------------------------------------------------------------------- #
# T2-C4 — the facts that make this pipeline USELESS while every hook exits 0.
#
# Both were found the same way and are the same defect: something ordinary and
# recoverable stops the pipeline dead, and the only way to find out was to go
# looking. The gate answers a narrower question than people read it as — "may we
# capture for this repo" — and it answered `capture=True` in both states below.
#
# ONE PREDICATE, TWO READERS. `cmd_session_start` (the only channel a human
# actually sees) and `cmd_status` (the only one that survives a background
# process) both ASK THIS FUNCTION. A second copy of "is this working" is how the
# two would come to disagree, which is the failure this track already shipped
# between capture and delivery.
# --------------------------------------------------------------------------- #

HEALTH_STATE_UNWRITABLE = "state_home_unwritable"
HEALTH_NO_DELIVERY_CREDENTIAL = "no_delivery_credential"

#: Each finding as (the full sentence `/fairmind-config` prints, the session
#: start's one-line clause). ONE table, so a new finding cannot reach one
#: channel and silently miss the other; the clause compresses the sentence
#: beside it and never adds a claim of its own.
_HEALTH_MESSAGES = {
    HEALTH_STATE_UNWRITABLE: (
        "its state directory cannot be written (~/.fairmind, or wherever "
        "FAIRMIND_INSIGHTS_HOME points), so NOTHING is being recorded at all",
        "nothing is recorded (state dir not writable)"),
    HEALTH_NO_DELIVERY_CREDENTIAL: (
        "this project's Fairmind MCP server has no resolvable url + "
        "Authorization header, so nothing captured can be delivered — records "
        "queue on this machine and stay there",
        "nothing can be sent (no resolvable url + Authorization header)"),
}


def _state_home_writable():
    """True iff `data_dir()` is writable — or, when it does not exist yet, iff it
    could be created.

    THE FAILURE THIS ANSWERS IS SILENT BY DESIGN. Every append below is
    best-effort — `_loop_ledger.append_row` swallows its own IO errors so a hook
    can never block a session — so an unwritable state home produces no error
    anywhere: the gate still says capture, the sweep still runs, and not one row
    is ever written. Fail-open is right for the hook and wrong for the person,
    and this is the difference.

    `os.access` rather than a probe file: this runs on the SessionStart path
    under a 5s budget, and writing a file to find out whether files can be
    written is a poor trade for a question `access(2)` answers. It reports the
    real uid/gid answer, which is exactly the 0500-directory /
    wrong-owner / nonexistent-parent case that produced this.

    AND IT CREATES NOTHING. An earlier revision opened with
    `os.makedirs(directory, exist_ok=True)`, which made this probe a WRITE — and
    `cmd_status`, whose own docstring promises it "arms nothing, registers
    nothing, writes nothing", then created the state home just by being run. A
    diagnostic that changes the thing it is diagnosing is not one.

    IT ASKS ABOUT THE PATH CAPTURE ACTUALLY WRITES, not about the state root.
    Two independent reviews found the same hole in the shallower version: with
    `data_dir()` writable but its `insights/sessions` child unwritable (or a
    regular FILE, or a dangling symlink), every append fails and the probe
    reported healthy. So the question is asked of the registry directory itself,
    and it is asked of the DEEPEST EXISTING ANCESTOR — because the directories
    below that are ones `register_session` would create, and "can it be created"
    is the same question one or more levels up. Any component that exists but is
    NOT a directory is disqualifying: `makedirs` cannot pass through it.

    `os.access` remains an approximation — it does not know about ACLs, NFS
    mount options or a macOS sandbox denial, and it can therefore both
    false-clear and false-alarm on those. It is the honest ceiling for a
    non-writing probe on a 5s budget, and it answers the cases this defect was
    actually reported for."""
    target = os.path.abspath(os.path.join(data_dir(), "insights", "sessions"))
    current = target
    while True:
        if os.path.lexists(current):
            # `lexists`, so a DANGLING symlink is seen rather than skipped as
            # absent — makedirs fails on it and the probe must say so.
            if not os.path.isdir(current) or os.path.islink(current):
                return False
            return os.access(current, os.W_OK | os.X_OK)
        parent = os.path.dirname(current)
        if parent == current:
            return False  # walked to the filesystem root and found nothing
        current = parent


def insights_health(cwd, toplevel=None, capture=None):
    """The health findings for `cwd`, as a tuple of `HEALTH_*` constants —
    empty when nothing is wrong. Never raises: a probe that fails to answer
    reports NOTHING rather than inventing a finding, because a false alarm on
    the startup channel costs more than one missed report of a state the person
    will meet again next session.

    `toplevel` is the git toplevel when the caller already resolved it (the
    SessionStart path has it in hand from `evaluate_gate`); None makes this
    resolve its own.

    `capture` IS THE GATE'S ANSWER, AND IT IS PART OF THE PREDICATE RATHER THAN
    THE CALLER'S BUSINESS. "Capture is on but not working" is only a finding
    where capture is on: in a repo with no per-project Fairmind MCP the gate
    already says no, the delivery target is unresolvable BY DESIGN, and
    reporting that as a fault would make `--insights-status` — the verb the
    health message itself tells people to run — emit a false statement in every
    ordinary git repository, guaranteed to appear exactly where the credential
    finding does. `cmd_session_start` passes True because it has already gated
    on it; anything else passes None and this asks."""
    if capture is None:
        try:
            capture = evaluate_gate(cwd).capture
        except Exception:
            capture = False
    if not capture:
        return ()
    findings = []
    try:
        if not _state_home_writable():
            findings.append(HEALTH_STATE_UNWRITABLE)
    except Exception:
        pass
    try:
        if toplevel is None:
            toplevel = _git_rev_parse(cwd)[0]
        endpoint, token = fairmind_delivery_target(cwd, toplevel)
        if not endpoint or not token:
            findings.append(HEALTH_NO_DELIVERY_CREDENTIAL)
    except Exception:
        pass
    return tuple(findings)


#: Where the line sends the reader: `/fairmind-config` with no argument runs
#: `--insights-status`, which prints `health_message` and `stale_loop_message`
#: in full.
DETAILS_AT = "see /fairmind-config"


def health_line(findings):
    """`health_message` as the session start's one line, or None when clean.

    Names the FIRST finding and counts the rest, so a second one is never
    hidden behind the first."""
    findings = tuple(f for f in findings if f in _HEALTH_MESSAGES)
    if not findings:
        return None
    more = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
    return _hook_line.line("Insights", f"capture NOT working — "
                                       f"{_HEALTH_MESSAGES[findings[0]][1]}{more} · {DETAILS_AT}")


def health_message(findings):
    """The findings rendered for the human channel, or None for a clean bill.

    It names `--insights-status` because the finding is the START of a
    diagnosis, not the whole of one — and because the verb that holds the rest
    of the record is the thing nobody knew to run."""
    findings = tuple(f for f in findings if f in _HEALTH_MESSAGES)
    if not findings:
        return None
    lines = ["Fairmind insight capture is ON for this project but is NOT working:"]
    lines += ["  - " + _HEALTH_MESSAGES[f][0] + "." for f in findings]
    # RUNNABLE AS TYPED, which it was not until 2026-07-30. This said
    # "`_insights_session.py --insights-status`" — no interpreter, and the file is
    # mode 0644, so a reader got command-not-found bare and permission-denied with
    # a path. The `python3` prefix is the fix (chmod would not be: the file is not
    # on PATH either way). The events notice carries the same instruction and had
    # the same defect; both were corrected together, and neither can name an
    # absolute path — the module ships in a sha-addressed plugin cache directory,
    # and the notice's bytes are sha-pinned so a machine-dependent string is not
    # available to it. "in this repository" is load-bearing, not politeness:
    # `cmd_status` resolves the tenancy from the process cwd.
    lines.append("Run `python3 scripts/_insights_session.py --insights-status` "
                 "from inside this repository, using the copy in the "
                 "fairmind-coding plugin directory, for the full record.")
    return "\n".join(lines)


def _health_marker_path(tenancy):
    """A SIBLING of the two notice markers, for the same reason they are
    siblings of each other: `record_notice` rewrites its file wholesale, so a
    key inside it would be erased by the next notice."""
    return os.path.join(data_dir(), "insights", "health", tenancy + ".json")


def recorded_health(tenancy):
    """The finding set last recorded for this tenant, sorted, as a tuple — `()`
    for an absent, unreadable, malformed or wrong-tenancy marker.

    Absent and unreadable both mean "nothing has been reported", which errs
    toward TELLING the person: a live finding compared against `()` is a
    change, so it reports. Same discipline as `notice_needed`, same reason."""
    try:
        with open(_health_marker_path(tenancy), encoding="utf-8") as fh:
            marker = json.load(fh)
        if not isinstance(marker, dict) or marker.get("tenancy") != tenancy:
            return ()
        recorded = marker.get("findings")
        if not isinstance(recorded, list):
            return ()
        return tuple(sorted(str(f) for f in recorded))
    except Exception:
        return ()


def health_changed(tenancy, findings):
    """True iff `findings` differs from what was last recorded for this tenant.

    THE MARKER TRACKS REALITY, NOT "HAS THIS EVER BEEN REPORTED", and the
    difference is the whole state machine. A finding that CLEARS prints nothing
    and must still be written down: leave the marker holding the old set and the
    next recurrence compares equal, is read as already-reported, and is
    suppressed forever — which is the same silence this whole item is about, one
    level up.

    KEYED ON THE SET, so a NEW finding appearing beside an old one is a change
    too, rather than being hidden by the old one's marker.

    Consequence worth naming: when the finding IS `state_home_unwritable`, the
    marker cannot be written either, so it reports every session. That is
    correct. A capture that records nothing, says so once, and then goes quiet
    is the exact shape of the defect."""
    return tuple(sorted(findings)) != recorded_health(tenancy)


def record_health(tenancy, findings):
    """Persist the reported finding set. Best-effort: an unwritable state home
    is itself one of the findings, so this is expected to fail in exactly the
    case it cannot record."""
    path = _health_marker_path(tenancy)
    # `_ensure_private_file` is deliberately NOT called, unlike the append paths
    # (`register_session`, `_spool_append`): `_atomic_write_lines` lands the file
    # 0600 via mkstemp + os.replace, which is what both sibling marker writers
    # rely on too.
    _ensure_private_dir(os.path.dirname(path))
    _atomic_write_lines(path, [json.dumps({"tenancy": tenancy,
                                           "findings": sorted(findings),
                                           "reported_at": _now_iso()}) + "\n"])


# --------------------------------------------------------------------------- #
# JC8's missing half — THE FACT THAT THE MARKER IS STALE, said out loud.
#
# JC8 closed a data loss (a stale `mode: loop` marker used to switch the capture
# hooks OFF; now they DEGRADE and keep recording, into `.fairmind/degraded/`) and
# gave the operator no way to notice it had happened. `insights_health` above
# cannot cover it and must not be widened to: it answers "is ambient capture
# working", diverted capture IS working, and its message opens with a sentence
# ("capture is ON for this project but is NOT working") that would be false here.
#
# SAME SHAPE, THOUGH — ONE PREDICATE, TWO READERS, for the same reason stated at
# the head of the health block: `cmd_session_start` (the only channel a human
# sees without knowing to look) and `cmd_status` (the only one that survives a
# background process) both ASK `stale_loop_marker`. A second copy of "is this
# marker stale" is how the two would come to disagree.
#
# 🔴 THE TRIGGER IS THE CURRENT STATE, NOT THE ARTIFACT — and the first revision
# of this block had it the other way round. It keyed on rows under
# `.fairmind/degraded/`, which is a record of HISTORY: nothing ever removes those
# rows, so the signal fired for ever, including long after a new loop or a
# recorded verdict had resolved the condition. Reproduced 2026-08-15 by driving
# the SessionStart hook over a fixture repo: a `mode: "closed"` marker — a loop
# that ended cleanly, nothing left to fix — still printed 602 bytes of signal at
# every session open, while a genuinely stale marker that had not yet diverted a
# row printed nothing at all. Both verdicts were exactly backwards, which is what
# an artifact-keyed predicate buys: it answers "did this ever happen" when the
# question is "is this true now".
#
# So the trigger is `_loop_ledger.resolve_loop_context(cwd).degraded`. MEASURED
# truth table (2026-08-15, the shipped resolver driven over a fixture context per
# status). It is a claim about ANOTHER module, so it is not left as prose:
# `tests/test_degraded_operator_signal.py::
# test_the_trigger_truth_table_is_what_the_resolver_answers` asserts every row on
# every run, against a hand-written expected column. When the two disagree, that
# test is right and this comment is the defect:
#
#     no .fairmind at all                     live=False degraded=False
#     mode=loop, status=running               live=True  degraded=False
#     mode=loop, status=passed_pending_human  live=True  degraded=True
#     mode=loop, status=blocked_budget        live=True  degraded=True
#     mode=loop, NO loop-state at base        live=True  degraded=True
#     mode=closed                             live=True  degraded=False
#     mode=interactive                        live=True  degraded=False
#     SUBDIR of a repo with a live loop       live=False degraded=False
#     SUBDIR of a repo with a stale marker    live=False degraded=False
#
# Two consequences worth naming. `degraded=True` never co-occurs with
# `live=False` — the single `live=False` branch is the absent-marker one, which
# cannot set the flag (`_loop_ledger.py:277-279`) — so a `lc.live and
# lc.degraded` conjunction here would be a guard that can never fire, and this
# file has already paid once for a second guard that quietly corrected the
# predicate behind it (see the renderer below). And the last two rows are the
# residual: `resolve_loop_context` is rooted at `cwd` and never searches upward,
# so a session opened in a subdirectory finds no marker and stays silent. That is
# the SAME direction the artifact-keyed predicate failed in (a subdirectory has
# no `.fairmind/degraded/` either), so this is unchanged rather than introduced —
# and the alternative is far worse: resolving `base_path` from a subdirectory
# would find no loop-state and call a perfectly healthy running loop stale.
#
# WHICH LOOP STATUSES REACH THIS STATE is decided by
# `run_gate_checks._desired_context_mode`, which owns the repoint rule. It is
# deliberately NOT restated here: a transcription of its current answer is a
# claim that goes stale the next time that function is edited, and the previous
# revision of this comment carried exactly such a transcription.
#
# 🔴 THE ROWS ARE THE DETAIL, NEVER THE TRIGGER. Each diverted row carries
# `degraded: true` + `degraded_from: "<task_ref>"` (`trace-op.sh:130-132`,
# `capture-subagent-tokens.sh:128-129`), which is real evidence and worth
# reporting — how many, and which loops they were diverted from. But a context
# can be stale before a single row has been diverted, and that is the EARLIEST
# and best moment to say so, so the message reports rows when they exist and
# never requires them.
#
# 🔴 AND NO CLOCK. The condition is a state, not its age — no N-day threshold, no
# "stale enough" cutoff. A loop that sat at a terminal status for 23 days is
# precisely the case this signal exists for (that is the measured JC8 incident),
# so nothing here may exclude it by waiting.
# --------------------------------------------------------------------------- #

# How many distinct task refs the message names before collapsing the tail into
# "+N more". Three fits one readable line; the tail count keeps the sentence
# honest about how many it is not showing.
DIVERTED_REFS_SHOWN = 3

# A LOOP REF IS AN UNSANITIZED CONTENT CHANNEL and it lands inside the ONE
# `systemMessage` string a person reads at session start. Two values pass through
# here and both originate in `.fairmind/active-context.json`'s own `task_ref`:
# the MARKER's ref (via `resolve_loop_context`'s `degraded_from`, which is the
# more direct of the two since the trigger moved) and each diverted ROW's
# `degraded_from` stamp. A hand-edit, a bad merge or a generated ticket import
# can fill either with newlines, control characters or a kilobyte of prose. So
# both get exactly the `_clean_session_id` treatment (bounded, control-char-free,
# DROPPED rather than mangled), and the ceiling is set from what a real ref looks
# like: the ones this plugin's own board uses are `F1.1`, `PL-A2a`, `T4·S1+S2`,
# `JC16`. 80 characters is generous for that and short enough that a dropped
# value is visibly a defect rather than a truncation nobody notices.
_MAX_LOOP_REF_LEN = 80


def _clean_loop_ref(value):
    """A task ref safe to render into the human channel, or None.

    Non-str, empty, over-long, or carrying any C0/C7F control character (newline
    included — the message is a joined multi-line string, so a newline in a ref
    could forge a line of its own) -> None. Dropping rather than stripping is
    deliberate: a ref that needed repair is not a ref anyone can act on. Losing it
    never costs the SIGNAL — the marker is stale whatever its ref says, and a
    diverted row is still COUNTED — so neither the condition nor the magnitude
    depends on the attribution being clean. Unicode is deliberately not filtered —
    `T4·S1+S2` is a real ref on this project's own board."""
    if not isinstance(value, str):
        return None
    ref = value.strip()
    if not ref or len(ref) > _MAX_LOOP_REF_LEN:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in ref):
        return None
    return ref


class StaleLoopMarker:
    """A marker pointing at a loop that is over, plus whatever evidence is on
    disk yet.

    `ref` is the loop the marker still names (None when it was dropped by
    `_clean_loop_ref`, or absent). `rows`/`refs` are the DETAIL: how many rows
    have been diverted so far and which loops they were diverted from (ordered by
    row count, ties alphabetical, so the loop that contributed most is named
    first and the ordering is deterministic). `rows` is legitimately 0."""

    __slots__ = ("ref", "rows", "refs")

    def __init__(self, ref, rows, refs):
        self.ref = ref
        self.rows = rows
        self.refs = refs


def stale_loop_marker(cwd):
    """`StaleLoopMarker` when `cwd`'s active context points at a loop that is
    over, else None.

    NEVER RAISES, and that is load-bearing rather than defensive habit: this runs
    on the SessionStart path BEFORE `register_session`, inside a hook with a 5s
    budget whose only exception handler is `main`'s catch-all — so an exception
    here would cost the session its registration, i.e. the ambient capture this
    signal is merely commenting on. The backstop now has to cover
    `_loop_ledger.resolve_loop_context` too, which parses two JSON files this
    module does not own.

    COST, stated per case rather than as a comparison. The trigger is at most two
    small JSON reads — `.fairmind/active-context.json` and the `loop-state.json`
    at its `base_path` — and a repo with no marker stops at the first
    `os.path.isfile`. The directory scan below runs ONLY once the marker is known
    to be stale, so no repo with a healthy or absent loop ever lists it."""
    try:
        return _stale_loop_marker(cwd)
    except Exception:  # noqa: BLE001 — see the never-raises contract above
        return None


def _stale_loop_marker(cwd):
    # Through the MODULE, not a from-import: see the import note at the head of
    # this file for why the call-time lookup is the point.
    context = _loop_ledger.resolve_loop_context(cwd)
    if not context.degraded:
        return None
    rows, refs = _diverted_rows(cwd)
    return StaleLoopMarker(_clean_loop_ref(context.degraded_from), rows, refs)


def _diverted_rows(cwd):
    """`(rows, refs)` for `.fairmind/degraded/` — `(0, ())` when there is nothing.

    ALWAYS A PAIR, never None: this is the message's DETAIL, and an absent
    directory is a real, reportable state ("no rows have been diverted yet"), not
    an absence of answer. Returning None for it would put a second copy of the
    condition into the renderer, which is the mistake documented below.

    THE BOUND IS PER FILE, NOT PER DIRECTORY, and the difference is worth stating
    because the first draft of this sentence claimed the directory-level one.
    Each ledger is capped at 2000 rows by `_loop_ledger.roll_window`, and the two
    the capture hooks write today are `trace.jsonl` and `subagent-tokens.jsonl` —
    but `degraded_ledger(cwd, name)` takes an arbitrary `name`, so NOTHING in the
    code caps how many files this directory can hold. A third capture hook, or a
    ledger copied in by hand, is read too. That is the intended behaviour (a
    reader that ignored a file would under-report), and it is left uncapped
    deliberately: the alternative is a signal that silently stops counting past
    some file, which is the shape of defect this whole item exists to remove.

    `cwd`, NOT the git toplevel: `_loop_ledger.degraded_ledger(cwd, name)` is how
    the WRITER resolves the same directory, and the two must join on the same
    string or the reader reports clean over rows that exist. Both this scan and
    the trigger above are rooted at that same `cwd`, so they can never disagree
    about which repo is being described."""
    directory = os.path.join(cwd, DEGRADED_DIR)
    try:
        # `.jsonl` only. The directory also holds `_atomic_write_lines`'
        # `.loop-ledger.*.tmp` files while a rotation is mid-flight
        # (`_loop_ledger.py:401`), and counting a half-written snapshot of a
        # ledger alongside the ledger itself would double the reported number
        # for as long as that replace takes.
        names = sorted(n for n in os.listdir(directory) if n.endswith(".jsonl"))
    except OSError:
        # Absent (the ordinary repo), unreadable, or not a directory at all.
        return 0, ()
    rows = 0
    counts = {}
    for name in names:
        path = os.path.join(directory, name)
        # 🔴 REGULAR FILES ONLY, AND THE REASON IS A HANG RATHER THAN TIDINESS.
        # This runs in the SessionStart foreground, under a 5s budget, BEFORE
        # the ambient gate. `open()` on a FIFO blocks until a writer appears and
        # a read on a character device can block indefinitely — either one costs
        # the session its registration and the sweep that follows it, which is a
        # far worse outcome than an unreported row. Named by a cross-model
        # review; `os.path.isfile` follows symlinks, so a symlink TO a regular
        # file is still read, which is the intended reading.
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    # COUNTED BEFORE IT IS PARSED. A truncated or corrupt row is
                    # still a row that was diverted, and a ledger of them must
                    # not read as "nothing diverted yet" — losing the attribution
                    # is a smaller failure than under-reporting the evidence.
                    rows += 1
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    ref = _clean_loop_ref(row.get("degraded_from")) \
                        if isinstance(row, dict) else None
                    if ref:
                        counts[ref] = counts.get(ref, 0) + 1
        except OSError:
            continue
    return rows, tuple(sorted(counts, key=lambda r: (-counts[r], r)))


def stale_loop_message(marker):
    """`marker` rendered for the human channel, or None when there is nothing.

    🔴 THE REGISTER IS DELIBERATELY LOW-ALARM. This line rides the same
    `systemMessage` as the one-time privacy notice, and an alarm printed beside a
    notice teaches the reader to skip the whole block — which costs the notice as
    well as itself. So it states the state, names both ways out of it, and stops.

    IT PRESCRIBES NO DELETION, and that is a decision rather than an omission.
    The rows it points at may be the record of the very window the reader is
    standing in: `run_gate_checks._desired_context_mode` deliberately keeps some
    of them attributed, so `rm -r .fairmind/degraded` can destroy exactly the
    evidence the engine went out of its way to preserve. The message says where
    the rows are, that nothing removes them automatically, and leaves the
    decision to someone who has read them.

    IT NAMES NO CAUSE. A stale marker is what a crash, a `kill` or a hand-edited
    status leaves behind, but WHICH loop statuses reach this state is
    `_desired_context_mode`'s answer and not this module's — so diagnosing one
    here would be an unmeasured claim about a rule living in another file, and
    would go false the next time that rule moves."""
    # 🔴 ONE GUARD, IN THE PREDICATE, AND THIS IS NOT A STYLE POINT. An earlier
    # revision ALSO re-tested the condition here, and that second copy made every
    # negative control in tests/test_degraded_operator_signal.py insensitive: a
    # mutant with the wrong predicate survived all of them, because this line
    # silently corrected it back. `stale_loop_marker` answers None when there is
    # nothing to say; the renderer's only job is to render.
    if marker is None:
        return None
    loop = ("loop %s" % marker.ref) if marker.ref else "a loop"
    if marker.rows:
        count = ("1 row is" if marker.rows == 1 else "%d rows are" % marker.rows)
        shown = marker.refs[:DIVERTED_REFS_SHOWN]
        hidden = len(marker.refs) - len(shown)
        if not shown:
            # Every row ref was absent or dropped by `_clean_loop_ref`. The count
            # still stands, so the sentence is still worth printing without it.
            where = ""
        else:
            listed = ", ".join(shown) + (" +%d more" % hidden if hidden else "")
            where = " (diverted from %s %s)" % (
                "loop" if len(shown) == 1 and not hidden else "loops", listed)
        # ⚠️ "Nothing was lost" STOOD HERE AND WAS FALSE, and a cross-model
        # review caught it. These ledgers are capped like any other and they
        # have NO window boundary — a degraded context has no `started_at`, so
        # `roll_window` is a pure newest-N cap and the OLDEST diverted rows roll
        # off once the file passes the cap. So the honest word is RETAINED: the
        # number is what is on disk now, not a total of what was diverted. The
        # claim to make is the one that is true and still actionable — these
        # rows are held out of the loop's ledger deliberately, and nothing
        # clears them for you.
        evidence = (
            "%s currently retained in %s/%s. These are the trace and token rows "
            "recorded while the marker pointed at a finished loop, held out of "
            "that loop's own ledger on purpose — and nothing clears them for "
            "you. They are capped and have no window, so the oldest roll off "
            "once the cap is passed. Read them before deciding what to keep."
            % (count, DEGRADED_DIR, where))
    else:
        evidence = ("No rows have been diverted yet: from here on they land in "
                    "%s/ instead of in that loop's own ledger." % DEGRADED_DIR)
    return "\n".join([
        # "diverted out of that loop's ledger", NOT "attributed to no loop":
        # a diverted row keeps its `degraded_from` stamp (trace-op.sh:130-132),
        # so the attribution survives — what it loses is its place in the loop's
        # own ledger. The stronger sentence was in a draft and was false.
        "Fairmind's .fairmind/active-context.json still points at %s, and that "
        "loop is over: the marker says mode \"loop\" while the loop-state at its "
        "base_path is missing or already terminal. Until a new loop opens here, "
        "or that loop's outcome is recorded, this session is recorded as "
        "interactive and its rows are diverted out of that loop's ledger." % loop,
        evidence,
    ])


def stale_loop_line(marker):
    """`stale_loop_message` as the session start's one line, or None.

    Names the marker, the loop it points at and the rows retained — what the
    reader needs to act — and stops, for the reason the full message does. The
    ref is bounded so the pointer at the end survives the cut."""
    if marker is None:
        return None
    ref = _hook_line.clip(marker.ref or "", 20)
    loop = f"ended loop {ref}" if ref else "an ended loop"
    rows = (f"{marker.rows} row{'s' if marker.rows != 1 else ''} in {DEGRADED_DIR}"
            if marker.rows else f"new rows go to {DEGRADED_DIR}")
    return _hook_line.line("Insights", f"active-context.json points at {loop} — "
                                       f"{rows} · {DETAILS_AT}")
