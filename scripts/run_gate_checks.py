#!/usr/bin/env python3
"""
run_gate_checks.py — the executed gate of fairmind-coding loop mode.

Reads the authoritative loop-state.json, evaluates every admitted machine
check (and reads evidence verdicts), applies the confirmation-gated stop rule
(K consecutive green evaluations), maintains the budget / consecutive-failure
accounting, and reports a decision that the Stop hook maps to an exit code.

Portable by construction: standard library only, no sandbox dependency.
Tier A hermeticity (Anthropic `srt`) is used when present and requested;
otherwise Tier B applies (k-run determinism probe) and checks are reported
as `hermeticity-unverified`. Determinism is *detected* anywhere; the sandbox
only upgrades detection to prevention.

Exit codes (consumed by hooks/scripts/loop-check.sh):
  0   allow stop   — no active loop, or terminal state reached
                     (passed_pending_human / blocked_*)
  10  iterate      — not green with budget remaining, or green awaiting
                     more confirmations; stdout carries routed feedback
  1   internal error — unreadable / inconsistent loop-state.json

The maker (implementer) never runs this to self-certify: the gate is invoked
by the Stop hook, and each check it runs was authored by an agent other than
the maker (recorded in check.source.authored_by).
"""

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone

# POSIX-only advisory file locking for `_state_write_lock` below. Guarded so
# the engine still imports on Windows, where `fcntl` is absent and the lock
# degrades to a best-effort no-op (matching every other flock user in this
# plugin: `_loop_ledger.py`, `_consent_authority.py`, `ambient_outbox.py`, ...).
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

# --- exit codes / decisions -------------------------------------------------

EXIT_ALLOW_STOP = 0
EXIT_ITERATE = 10
EXIT_INTERNAL_ERROR = 1

DECISION_NOOP = "noop"
DECISION_ITERATE = "iterate"
DECISION_STOP_PASSED = "stop_passed"
DECISION_STOP_BLOCKED = "stop_blocked"

# Verdicts a single check can yield.
GREEN = "green"
RED = "red"
ERROR = "error"
INCONCLUSIVE = "inconclusive"

# The two vocabularies the contract fixes (T10 / R9). Exported so the doc-agreement
# assertion (AC8(d)) and admit_check have a single machine source of truth instead
# of a hand-copied list that rots.
#
# CRITERION_DISPOSITIONS — the grammar of `contract.criteria[].disposition` (R2):
# three `<prefix>:<check_id>` forms plus the bare `unverifiable`. The `<id>` is
# data, not vocabulary — the members are spelled with the placeholder so the
# grammar reads at a glance; consumers compare on the prefix (see AC8(d)'s
# `_normalize_enum_token`, which splits on the first ":").
CRITERION_DISPOSITIONS = ("checked:<id>", "evidence:<id>", "quarantined:<id>", "unverifiable")

# CHECK_KINDS — the descriptor `kind` vocabulary. `guard` (T10/R4) is the new
# member: a check GREEN at spec by construction. NOT `regression_guard`, which is
# the pre-existing baseline-predicate FIELD (a member of _CONTRACT_FIELDS), never
# a kind value.
CHECK_KINDS = ("machine", "evidence", "guard")

# TERMINAL_STATUSES (adversarial-review amendment A1) — the vocabulary of
# `state["status"]` values that END a loop for good: the one succeeding state
# (`passed_pending_human`) plus every `blocked_*` state a fail-closed guard can
# leave behind (`blocked_budget`/`blocked_failures`/`blocked_timeout` from
# `budget_exhausted`; `blocked_no_checks`; `blocked_scope`; `blocked_worktree`
# from `resolve_work_dir`, H1/F34) or a human recovery action can set
# (`blocked_recovered`, `--recover`). Exported for the same reason as
# CRITERION_DISPOSITIONS/CHECK_KINDS above: a single machine source of truth
# for the doc-agreement assertion (AC8(d)) instead of a hand-copied list that
# rots. `running` is deliberately EXCLUDED — it is the one active,
# non-terminal status; the pre-arm `specified` status (or an absent/unknown
# one) is likewise not a member — this constant names the states a loop STOPS
# in, not every value the `status` field can ever hold.
TERMINAL_STATUSES = (
    "passed_pending_human",
    "blocked_budget",
    "blocked_failures",
    "blocked_timeout",
    "blocked_no_checks",
    "blocked_scope",
    "blocked_recovered",
    "blocked_worktree",
)

# CHECK_ENV_SCRUB (F31/AC4) — every var run_command() strips from a check's
# child environment before subprocess.run. A var belongs here IFF (a) the gate
# or its hooks read it to resolve state / cwd / sandbox / policy, AND (b) a
# check retains a correct OWN-source fallback without it. That second clause
# is what stops this list rotting into "every var that looks gate-ish" — it is
# also why CLAUDE_PLUGIN_ROOT is deliberately NOT a member (see below).
#
# Per-var reason, honestly scoped (they are not equally earned):
#   FAIRMIND_BASE, CWD        — F27's original state pointers. Own-source
#                                fallback: active-context.json / $PWD.
#   CLAUDE_PROJECT_DIR        — the real F31 fix. All six hooks under
#                                hooks/scripts/*.sh resolve
#                                `CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"` —
#                                this var OUT-RANKS the already-scrubbed CWD,
#                                so F27's scrub was nearly a no-op for any
#                                check that spawns a hook. Fallback: $PWD, the
#                                check's own cwd (subprocess.run(cwd=...)).
#   FAIRMIND_SRT_CMD,
#   FAIRMIND_SRT_PREFIX       — proven cross-resolution: a check that spawns a
#                                NESTED engine would otherwise inherit the
#                                OUTER gate's sandbox config, and the inner
#                                loop can then report AND PERSIST a FALSE
#                                hermeticity_tier ("B"). Fallback: the check's
#                                own PATH/config resolves `srt` independently.
#   FAIRMIND_GATE_DEADLINE_S  — class-closure only; no demonstrated harm (the
#                                OUTER gate's own deadline always fires first
#                                in practice, so a nested engine's inherited
#                                deadline never bites). Included for
#                                consistency with the other policy knobs, not
#                                because a concrete leak was observed.
#
# CLAUDE_PLUGIN_ROOT is DELIBERATELY EXCLUDED. It locates the plugin's CODE,
# not this loop's STATE: two loops share one installed plugin, so there is
# nothing to cross-resolve. It also fails clause (b) above — it has NO
# own-source fallback (hooks/scripts/loop-check.sh:42 fails CLOSED without it,
# "does not point at the fairmind-coding plugin", rc=2) — so scrubbing it would
# turn a working nested invocation into a silent refusal. A guard test
# (test_check_env_hermeticity.py, AC3) pins this exemption: it stays GREEN
# today and must FAIL if CLAUDE_PLUGIN_ROOT is ever added here, because an
# over-scrub would otherwise ship silently — nothing else in the suite would
# notice.
CHECK_ENV_SCRUB = (
    "FAIRMIND_BASE", "CWD", "CLAUDE_PROJECT_DIR",
    "FAIRMIND_SRT_CMD", "FAIRMIND_SRT_PREFIX", "FAIRMIND_GATE_DEADLINE_S",
)

# Visual markers for reports. Glyphs only — no ANSI color: the gate's stdout is
# re-fed to the model as the next turn's input, so terminal escape codes would be
# noise. Emoji render in every modern terminal and stay parseable as text.
_VERDICT_GLYPH = {GREEN: "🟢", RED: "🔴", ERROR: "🟠", INCONCLUSIVE: "🟡"}


def glyph(verdict):
    return _VERDICT_GLYPH.get(verdict, "⚪")  # ⚪ = no prior verdict (new check)

DEFAULT_CONFIRMATION_K = 3

# Fail-closed wall-clock cap for a whole gate evaluation. 540 = the Stop-hook
# timeout (600s) minus a 60s teardown margin, so the engine always finishes and
# reports before the hook can be killed mid-run (a kill could end the turn
# unguarded — a false green). The env var FAIRMIND_GATE_DEADLINE_S can only
# tighten this, never extend it.
DEFAULT_DEADLINE_CAP_S = 540

# How long an invocation waits for `_state_write_lock` before giving up without
# reading or writing the state. It must stay ABOVE DEFAULT_DEADLINE_CAP_S, so
# only a holder that is alive but no longer progressing outlasts it, and BELOW
# the Stop hook's 600s timeout, so a queued Stop answers before the hook kills
# it. test_recover_eval_race.py pins both bounds against hooks.json.
STATE_LOCK_WAIT_S = DEFAULT_DEADLINE_CAP_S + 30

from _gate_mutation import (  # noqa: E402,F401 — re-exported, one definition
    now_utc, iso, _parse_iso,
    MUTATION_SET_DEGRADED_NO_GIT, MUTATION_SET_DEGRADED_GIT_QUERY_FAILED,
    MUTATION_SET_DEGRADED_NO_BASELINE_REF, LOOP_WORKSPACE_DIR,
    _is_loop_workspace_path, _working_tree_sha, pre_dirty_anchors,
    _GitQueryError, _is_git_work_tree, _run_git_query, _split_nul,
    _git_changed_paths, _git_untracked_paths, _resolve_repo_root,
    _normalize_trace_target, _load_trace_attribution, compute_mutation_set,
    _mutation_baseline, _head_sha, _numstat_count, _numstat_no_index,
    _numstat, resolve_work_dir, _active_context_ref, trace_path,
    evaluate_scope, DEGRADED_SCOPE_RETRY_CAP, _trailing_degraded_scope_run,
    _degraded_scope_recoverable,
    _no_work_signature, _signature_members, SETTLE_WINDOW_S,
    SETTLE_MAX_CONSECUTIVE, _settle_window_s, _settle_max_consecutive,
    _settle_age,
)


# --- state resolution & IO --------------------------------------------------

def resolve_state_path(args):
    """Locate loop-state.json.

    Priority: explicit --state, then FAIRMIND_BASE env, then base_path read
    from <cwd>/.fairmind/active-context.json (relative to the repo root),
    then the conventional <cwd>/.fairmind/loop-state.json when that file
    actually exists on disk (never fabricated — see below).
    Returns (state_path or None, cwd).
    """
    cwd = args.cwd or os.environ.get("CWD") or os.getcwd()

    if args.state:
        return os.path.abspath(args.state), cwd

    base = os.environ.get("FAIRMIND_BASE")
    if base:
        return os.path.join(cwd, base, "loop-state.json"), cwd

    ctx = os.path.join(cwd, ".fairmind", "active-context.json")
    if os.path.isfile(ctx):
        try:
            with open(ctx, encoding="utf-8") as fh:
                base_path = json.load(fh).get("base_path")
            if base_path:
                return os.path.join(cwd, base_path, "loop-state.json"), cwd
        except (OSError, ValueError):
            return None, cwd
        # active-context.json exists but carries no base_path: fall back to
        # the conventional sibling loop-state.json, but only when it's
        # genuinely there — a missing base_path must never fabricate a path.
        fallback = os.path.join(cwd, ".fairmind", "loop-state.json")
        if os.path.isfile(fallback):
            return fallback, cwd

    return None, cwd


def load_state(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write_json(path, data, prefix):
    """Write `data` as JSON to `path` atomically: temp file in the SAME
    directory, then `os.replace`. A crash mid-write leaves either the old file
    or the new one, never a half-written one a reader would then mis-resolve.

    `prefix` names the temp file after what is being written, because this is
    now used for TWO different artifacts (loop-state.json and
    active-context.json) that can live in the same directory: a stray `.tmp`
    left by a crash says which writer left it. Same shape as
    `loop_open._atomic_write_json` and `loop_import`'s (which names this family
    in its own comment at loop_import.py:233); factored out here rather than
    copied a fourth time."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def save_state(path, state):
    """Atomic write: temp file in the same dir, then replace."""
    _atomic_write_json(path, state, prefix=".loop-state.")


def _state_lock_path(state_path):
    return state_path + ".lock"


def _report_state_lock_failure(lock_path, exc):
    print(f"loop-check: could not lock {lock_path} ({exc}); this invocation runs "
          "without cross-process serialization, so a concurrent verb on the same "
          "loop can be overwritten", file=sys.stderr)


def _report_state_lock_timeout(lock_path, waited_s):
    print(f"loop-check: {lock_path} is still held by another invocation after "
          f"{waited_s:g}s, longer than any gate evaluation may run, so its holder is "
          "probably hung; nothing was read or changed. Stop the process holding it "
          f"(`lsof {lock_path}` lists who has it open) and retry", file=sys.stderr)


@contextlib.contextmanager
def _state_write_lock(state_path):
    """Serialize the ENTIRE read -> evaluate/mutate -> save cycle for one
    loop-state.json, across processes.

    Every verb (`--arm`, `--recover`, `--hold`, ..., and the plain gate
    evaluation `run_gate` drives) loads the state once near the top of
    `_dispatch_locked`, may then run for a long time — a full-suite gate
    evaluation can run up to `DEFAULT_DEADLINE_CAP_S` — and only afterwards
    calls `save_state` with that in-memory copy. Without a lock spanning the
    whole cycle, a FAST human verb (`--recover`) that reads, writes and
    returns while a SLOW evaluation is still in flight gets silently
    overwritten the moment the evaluation's own `save_state` fires: the
    evaluation snapshotted `state` before the human verb ran, so its write
    reinstates the pre-recover status and drops the recover audit entry with
    no trace on disk — `--arm` then refuses ("the loop is already 'running'")
    and nothing in `loop-state.json` explains why.

    An `fcntl.flock` on a sidecar `<state_path>.lock` file closes the window:
    whichever invocation starts first holds the lock across its own full
    cycle, and every other invocation — however short — waits for it rather
    than skipping its turn. The lock is acquired BEFORE `load_state` and
    released AFTER `save_state` (see `_dispatch`), because a lock that only
    guarded the final write would still let two invocations load the same
    stale snapshot and each compute a decision from it; the fix has to cover
    the read too, not just the write it produces.

    The wait is bounded by `STATE_LOCK_WAIT_S`, polled with `LOCK_NB`. A
    process that dies releases the flock (the kernel drops it on exit), but one
    that is alive and stuck holds it for as long as it lives, and `--recover` —
    the verb for a loop whose session is gone — must not queue behind it
    forever. Past the bound the body must NOT run: one stderr line names the
    lock and the context yields False, so `_dispatch` returns without reading or
    writing the state. It yields True whenever the body may run: lock held, or
    one of the degraded cases below.

    Same shape as `_consent_authority._registry_write_lock`: guarded by
    `if _fcntl is not None`, degrading to a best-effort no-op (no real
    cross-process exclusion) on a host without `fcntl` (Windows) rather than
    refusing to run — every other locker in this plugin makes the identical
    trade.

    A lock that could not be taken on a host that HAS `fcntl` (the lock file
    cannot be opened, or `flock` itself fails) still runs the body, but prints
    one stderr line first: the cycle is then unserialized and the race above can
    recur, so it must not pass for a lock that was held. Only the no-`fcntl`
    host stays silent — that degrade is by design, not a fault.

    A foreign session's plain Stop never reaches this lock — see
    `_stop_is_foreign`."""
    lock_path = _state_lock_path(state_path)
    lock_fh = None
    try:
        lock_fh = open(lock_path, "a+")
    except OSError as exc:
        lock_fh = None
        if _fcntl is not None:
            _report_state_lock_failure(lock_path, exc)
    acquired = False
    timed_out = False
    try:
        if lock_fh is not None and _fcntl is not None:
            wait_s = STATE_LOCK_WAIT_S
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:  # held by someone else: keep waiting
                    if time.monotonic() >= deadline:
                        timed_out = True
                        _report_state_lock_timeout(lock_path, wait_s)
                        break
                    time.sleep(0.1)
                except OSError as exc:  # flock itself failed: run unserialized
                    _report_state_lock_failure(lock_path, exc)
                    break
        yield not timed_out
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


# --- signal extraction ------------------------------------------------------

_SELECTOR_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def resolve_selector(obj, selector):
    """Resolve a minimal JSONPath subset: $.a.b[0].c

    Raises KeyError/IndexError/TypeError when the path is absent so callers
    can treat "missing signal" distinctly from a present value.
    """
    if selector in (None, "", "$"):
        return obj
    path = selector[2:] if selector.startswith("$.") else selector.lstrip("$")
    cur = obj
    for key, idx in _SELECTOR_TOKEN.findall(path):
        if idx != "":
            cur = cur[int(idx)]
        else:
            cur = cur[key]
    return cur


class InvalidValueType(ValueError):
    """A check declares a `signal.value_type` outside the closed vocabulary.

    A `ValueError` on purpose: `admit_check.gate_red_first_live` already catches
    `(ValueError, TypeError)` around its `coerce_value` call, so the recorded
    `red_value` path gets a clean rejection with no new except clause."""


# 🔴 THE CLOSED VALUE-TYPE VOCABULARY (JC12). `coerce_value` used to end in a
# bare `return value`, so a `value_type` it did not recognise returned its input
# UNTOUCHED — and the input on a `stdout_regex` signal is the raw stdout of the
# check's own command. A check declaring `value_type: "string"` therefore put a
# command's whole output onto the wire as `iterations[].results[].value`, through
# the SUPPORTED authoring path with all four admission gates green, breaching the
# capture lane's one hard invariant ("no content on the wire"). Reproduced
# end-to-end 2026-08-14.
#
# The gate's signal is a MEASUREMENT — a count, a duration, a boolean — and the
# four names `skills/fairmind-gate/references/loop-state.md` documents
# (`count`/`number`/`duration_ms`/`bool`) already said so. The vocabulary was
# closed everywhere except in the code. Anything outside it now RAISES rather
# than falling through, and `admit_check.gate_clean_signal` refuses it at
# authoring time so the raise is a backstop rather than the user-visible path.
#
# The undocumented aliases below are kept deliberately: they are live behaviour a
# check may already have been authored against, and narrowing to the four
# documented spellings would be a second, unrelated break. Measured over the 36
# real loop-states on this machine (2026-08-15): 107 checks, 105 `count`, 2 with
# no `value_type` at all (defaulting to `count`) — zero would start erroring.
def _coerce_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "pass", "passed")
    return bool(value)


_VALUE_TYPE_COERCERS = {
    "count": int, "int": int, "integer": int,
    "number": float, "float": float, "duration_ms": float, "ms": float,
    "bool": _coerce_bool, "boolean": _coerce_bool,
}
VALUE_TYPES = tuple(sorted(_VALUE_TYPE_COERCERS))

# The default a descriptor that omits `signal.value_type` gets. ONE spelling,
# because JC12's guarantee is "admission refuses exactly what the engine
# refuses" and that holds only while every read site defaults identically — it
# was a `"count"` literal at five of them, which is the same shape as the defect
# one notch down (closed in the docs, open in the code).
DEFAULT_VALUE_TYPE = "count"


def declared_value_type(check):
    """The `signal.value_type` a check declares, or the default."""
    return check.get("signal", {}).get("value_type", DEFAULT_VALUE_TYPE)


def coerce_value(value, value_type):
    """Coerce a raw signal to its declared type. Raises `InvalidValueType` when
    the type is not in `VALUE_TYPES` — never returns the raw value."""
    coercer = _VALUE_TYPE_COERCERS.get(value_type)
    if coercer is None:
        raise InvalidValueType(
            f"signal.value_type {value_type!r} is not one of {', '.join(VALUE_TYPES)} — "
            "the gate's signal is a measurement, and an unrecognised type used to "
            "pass the raw value (a command's stdout) straight through")
    return coercer(value)


class MissingSignal(Exception):
    """Raised when the signal cannot be located — never read as a pass."""


def _uncoercible_reason(check, what):
    """The `reason` for a signal that is present and does not coerce.

    🔴 IT NAMES THE TYPE, NEVER THE VALUE, AND THAT IS THE WHOLE POINT.
    `int("SECRET-STDOUT the customer is ACME")` raises `ValueError: invalid
    literal for int() with base 10: 'SECRET-STDOUT the customer is ACME'` —
    Python's message QUOTES the input, which on a `stdout_regex` signal is the
    check's own command output. Interpolating the exception here would write
    that output into `loop-state.json` — not this script's private scratch file
    but the loop's durable `.fairmind/**` record, read by consumers the gate
    does not own and carried off this machine by every exporter of that tree.
    Leaning on a downstream drop is fixing the reported site instead of the
    predicate: `insights_flush_payload._ITERATION_RESULT_FIELDS` does whitelist
    a result row down to `id`/`verdict`/`value`, but that is one exporter's
    choice and the gate cannot unwrite what it has already put on disk. JC12's
    invariant breached by JC12's own fix, one surface over. The failure is fully
    described by which signal and which declared type, so the value adds nothing
    a reader needs."""
    value_type = declared_value_type(check)
    return (f"uncoercible {what}: the value does not coerce to the declared "
            f"value_type {value_type!r} (value withheld — it is the check's own output)")


def extract_signal(check, run):
    """Pull the raw signal from a completed run according to check.signal.

    `run` is a dict: {returncode, stdout, stderr, result_file_json}.
    Raises MissingSignal when the value is absent (clean-signal guarantee).
    """
    signal = check.get("signal", {})
    src = signal.get("from", "exit_code")
    selector = signal.get("selector")

    if src == "exit_code":
        raw = run["returncode"]
    elif src == "file_json":
        data = run.get("result_file_json")
        if data is None:
            raise MissingSignal("result file missing or not JSON")
        try:
            raw = resolve_selector(data, selector)
        except (KeyError, IndexError, TypeError) as exc:
            raise MissingSignal(f"selector {selector!r} not found: {exc}")
    elif src == "stdout_json":
        text = run["stdout"].strip()
        if not text:
            raise MissingSignal("empty stdout")
        try:
            data = json.loads(text)
            raw = resolve_selector(data, selector)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MissingSignal(f"stdout JSON / selector error: {exc}")
    elif src == "stdout_regex":
        match = re.search(signal.get("pattern", ""), run["stdout"])
        if not match:
            raise MissingSignal("regex did not match stdout")
        raw = match.group(1) if match.groups() else match.group(0)
    else:
        raise MissingSignal(f"unknown signal source {src!r}")

    return coerce_value(raw, declared_value_type(check))


# --- predicate evaluation ---------------------------------------------------

_OPERATORS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def eval_predicate(value, predicate):
    op = _OPERATORS.get(predicate.get("operator", "=="))
    if op is None:
        raise ValueError(f"unknown predicate operator {predicate.get('operator')!r}")
    return op(value, predicate.get("value"))


def baseline_value(baseline):
    """A baseline is either a bare number (back-compat) or a provenance object
    `{value, ref, clean}`. Return the numeric value for guard comparison."""
    if isinstance(baseline, dict):
        return baseline.get("value")
    return baseline


def baseline_dirty(baseline):
    """True only for an object baseline explicitly captured on a dirty tree.
    A bare number is assumed clean (nothing to surface)."""
    return isinstance(baseline, dict) and baseline.get("clean") is False


def eval_regression_guards(value, guards, baseline):
    """Guards protect a reduce/improve baseline from regression.

    Each guard: {operator, value|"baseline"}. `baseline` substitutes the
    frozen baseline. Returns (ok, failed_descriptions).
    """
    failed = []
    for guard in guards or []:
        target = guard.get("value")
        if target == "baseline":
            target = baseline
        op = _OPERATORS.get(guard.get("operator", "<="))
        # Fail-closed: a misconfigured guard (unknown operator or an unresolved
        # target such as a missing baseline) must not be silently skipped — that
        # would drop a regression protection and risk a false green.
        if op is None:
            failed.append(f"unknown guard operator {guard.get('operator')!r}")
            continue
        if target is None:
            failed.append(f"regression guard target unresolved (missing baseline?) for {guard}")
            continue
        if not op(value, target):
            failed.append(f"{value} {guard.get('operator')} {target}")
    return (len(failed) == 0, failed)


# --- command execution ------------------------------------------------------

def srt_available():
    return shutil.which(os.environ.get("FAIRMIND_SRT_CMD", "srt")) is not None


def srt_prefix():
    """Tokens prepended to run the inner argv inside `srt`, network denied."""
    return os.environ.get("FAIRMIND_SRT_PREFIX", "srt exec --deny-network --").split()


def build_argv(check, tier):
    """Build the argv to execute, honouring hermeticity.

    A descriptor may provide `exec.argv` (a list — run verbatim, no shell) or
    `exec.command` (a string — run through the platform shell so pipes,
    redirects and env expansion behave as a developer expects). We invoke the
    shell *explicitly* (POSIX `/bin/sh -c` or Windows `cmd /c`) rather than
    subprocess `shell=True`, keeping the shell choice explicit and portable.

    `command` is trusted in-repo config: it lives in loop-state.json, authored
    by the checker-side agent (the QA Engineer / the Code Reviewer), at the same trust level as an
    npm or Make script. Containment of untrusted *code under test* is the
    Tier-A `srt` layer (network-denied), not shell avoidance — our hermeticity
    need is determinism, not defense.

    Tier A wraps the WHOLE inner argv (including the shell) inside `srt`, i.e.
    `srt exec --deny-network -- /bin/sh -c '<command>'`, so pipes/redirections
    run *inside* the sandbox rather than being interpreted by an outer shell.
    If `srt` is absent we degrade to Tier B — the sandbox is never required.
    """
    exec_spec = check.get("exec", {})
    argv = exec_spec.get("argv")
    if isinstance(argv, list) and argv:
        inner = list(argv)  # run verbatim, no shell involved
    else:
        command = exec_spec.get("command", "")
        if os.name == "nt":
            inner = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
        else:
            inner = ["/bin/sh", "-c", command]

    if tier == "A" and exec_spec.get("network") == "forbidden" and srt_available():
        return srt_prefix() + inner
    return inner


def run_command(check, cwd, tier, deadline=None):
    exec_spec = check.get("exec", {})
    timeout_s = exec_spec.get("timeout_s", 300)
    env = dict(os.environ)
    # A check is a hermetic black box: it must resolve its OWN state from its own
    # --state/--cwd/active-context, never silently inherit the outer loop's
    # pointer. Handing it the gate's state-resolution, sandbox, or policy vars is
    # the leak (a check that spawns a nested engine, or one of this plugin's own
    # hooks, would cross-resolve to the OUTER loop's state/config). See
    # CHECK_ENV_SCRUB's docstring above for the membership rule and the
    # deliberate CLAUDE_PLUGIN_ROOT exemption. The check's working directory
    # comes from subprocess.run(cwd=…) below, NOT from any of these env vars, so
    # scrubbing them does not change where the check runs.
    for v in CHECK_ENV_SCRUB:
        env.pop(v, None)

    # Cap this run's timeout to the nearer of the check's own timeout and the
    # remaining gate-deadline budget, so no single check can overrun the gate.
    # A kill caused by the deadline (not the check's timeout) is flagged so the
    # verdict attributes it to the deadline — fail-closed, never the check's fault.
    effective_timeout = timeout_s
    capped_by_deadline = False
    if deadline is not None:
        remaining = deadline - time.monotonic()  # same monotonic clock as the deadline
        if remaining < effective_timeout:
            effective_timeout = max(0.05, remaining)
            capped_by_deadline = True

    # Capture the moment just before execution so we can reject a stale result
    # file that this run did not (re)produce — a stale file would otherwise be a
    # silent false green if the command failed without rewriting it.
    started = time.time()
    try:
        proc = subprocess.run(
            build_argv(check, tier), cwd=cwd, env=env,
            capture_output=True, text=True, timeout=effective_timeout,
        )
        returncode, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        returncode, stdout, stderr, timed_out = 124, exc.stdout or "", exc.stderr or "", True

    run = {"returncode": returncode, "stdout": stdout, "stderr": stderr,
           "timed_out": timed_out, "deadline_timeout": timed_out and capped_by_deadline,
           "result_file_json": None}

    # Load a JSON result file when the check reports its signal through one —
    # but only if it was produced by THIS run (mtime at/after start). A missing
    # or stale file leaves result_file_json = None → MissingSignal downstream.
    signal = check.get("signal", {})
    if signal.get("from") == "file_json":
        result_file = signal.get("file") or exec_spec.get("result_file")
        if result_file:
            fpath = result_file if os.path.isabs(result_file) else os.path.join(cwd, result_file)
            try:
                if os.path.getmtime(fpath) + 1e-3 >= started:
                    with open(fpath, encoding="utf-8") as fh:
                        run["result_file_json"] = json.load(fh)
                # else: stale (older than this run) → treated as missing signal
            except (OSError, ValueError):
                run["result_file_json"] = None
    return run


# --- single-check evaluation ------------------------------------------------

def maker_checker_error(check):
    """maker != checker: the check must be authored by an agent other than the
    maker who fixes it. Returns a reason string when violated, else None. The
    engine enforces this structurally so a self-authored check can never close
    the loop even if admission missed it. Both roles must be explicit — a
    missing owner or authored_by makes the separation unverifiable, which is an
    error, never a pass."""
    authored_by = check.get("source", {}).get("authored_by")
    owner = check.get("owner")
    if not owner:
        return "check has no owner (maker unknown; maker != checker unverifiable)"
    if not authored_by:
        return "check has no source.authored_by (maker != checker unverifiable)"
    if authored_by == owner:
        return f"check authored_by ({authored_by}) == owner ({owner}); maker != checker violated"
    return None


# Fields that define the check contract. If any changes after admission the
# descriptor was tampered with (e.g. a maker relaxing the predicate) — the
# engine recomputes this hash and refuses to gate on a mutated descriptor.
# (External test-artifact immutability + a git-identity firewall remain in the
# hardening backlog; this closes the in-descriptor mutation path.)
#
# The hash must cover EVERY descriptor field a verdict is computed from, or a
# post-admission edit of an uncovered field silently changes the verdict without
# tripping `integrity_error`. The top-level fields below plus the `source.*`
# fields folded into the payload by `descriptor_hash` are that complete set:
#   - `source.authored_by` gates maker != checker;
#   - `source.evidence_hash` is the evidence-freshness anchor an evidence check
#     compares its verdict file against — editing it to MATCH a stale verdict
#     file would revive that stale pass as GREEN, so it must be under the hash.
# Deliberately EXCLUDED: `source.admitted_hash` is this hash's own storage slot
# (covering it would be self-referential); `id` names the check but no verdict is
# read from it; `source.red_first_proof` is admission-time evidence the gate
# never consults at evaluation time. `admission.status` is not a descriptor field
# — it is admission's own output, and a check flipped to admitted without a
# matching `admitted_hash` is caught by admission, not by this hash.
_CONTRACT_FIELDS = ("type", "kind", "owner", "exec", "signal",
                    "predicate", "regression_guard", "baseline", "determinism")


def descriptor_hash(check):
    payload = {k: check.get(k) for k in _CONTRACT_FIELDS}
    source = check.get("source", {})
    payload["authored_by"] = source.get("authored_by")
    # evidence_hash joins the payload ONLY when the descriptor carries one (every
    # admitted evidence check does; admission requires it). Folding an absent
    # anchor in as a constant None would change the hash of every non-evidence
    # check too, mass-invalidating already-recorded admitted_hashes on deploy —
    # a spurious integrity_error for checks whose verdict never reads the field.
    if source.get("evidence_hash") is not None:
        payload["evidence_hash"] = source["evidence_hash"]
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def integrity_error(check):
    """ERROR if the descriptor changed since admission (admitted_hash mismatch).
    Skipped when no admitted_hash is recorded (backward compatible)."""
    admitted = check.get("source", {}).get("admitted_hash")
    if admitted and descriptor_hash(check) != admitted:
        return "descriptor changed since admission (integrity hash mismatch) — re-run admit_check.py"
    return None


def evaluate_check(check, cwd, tier, deadline=None):
    """Run one machine check `determinism.runs` times; return a result dict."""
    mc = maker_checker_error(check)
    if mc:
        return _result(check, ERROR, None, mc, tier)
    integrity = integrity_error(check)
    if integrity:
        return _result(check, ERROR, None, integrity, tier)

    runs = max(1, int(check.get("determinism", {}).get("runs", 1)))
    values = []
    on_missing = check.get("signal", {}).get("on_missing", "error")

    for _ in range(runs):
        run = run_command(check, cwd, tier, deadline)
        if run["timed_out"]:
            reason = "gate deadline exceeded" if run.get("deadline_timeout") else "check timed out"
            return _result(check, ERROR, None, reason, tier)
        try:
            values.append(extract_signal(check, run))
        except MissingSignal as exc:
            # Clean-signal guarantee: absence never reads as a passing value.
            if on_missing == "error":
                return _result(check, ERROR, None, f"missing signal: {exc}", tier)
            if on_missing == "fail":
                return _result(check, RED, None, f"missing signal (treated as fail): {exc}", tier)
            try:
                values.append(coerce_value(on_missing, declared_value_type(check)))
            except (ValueError, TypeError):
                return _result(check, ERROR, None, _uncoercible_reason(check, "on_missing fallback"), tier)
        except (ValueError, TypeError):
            # A signal that is PRESENT but does not coerce — an unrecognised
            # `value_type` (JC12), or a `count` check whose stdout matched
            # something that is not a number. Both used to escape `evaluate_check`
            # as an uncaught exception and kill the gate run; an ERROR verdict is
            # the honest answer and, unlike a crash, carries `value: None` so
            # nothing uncoerced can reach `results[].value`.
            return _result(check, ERROR, None, _uncoercible_reason(check, "signal"), tier)

    # Determinism: differing values across runs → inconclusive (collect more).
    if len({repr(v) for v in values}) > 1:
        return _result(check, INCONCLUSIVE, values, f"non-deterministic signal across {runs} runs: {values}", tier)

    value = values[0]
    passed = eval_predicate(value, check.get("predicate", {}))
    guards_ok, guard_fail = eval_regression_guards(
        value, check.get("regression_guard"), baseline_value(check.get("baseline")))
    if passed and guards_ok:
        return _result(check, GREEN, value, "predicate satisfied", tier)
    reason = "predicate not satisfied" if not passed else "regression guard violated: " + "; ".join(guard_fail)
    return _result(check, RED, value, reason, tier)


def _result(check, verdict, value, reason, tier):
    return {
        "id": check.get("id"),
        "type": check.get("type"),
        "owner": check.get("owner"),
        "verdict": verdict,
        "value": value,
        "reason": reason,
        "hermeticity": "enforced" if tier == "A" else "unverified",
    }


def evaluate_evidence(check, cwd):
    """Evidence checks are settled by a verdict artifact written by an agent
    that is not the maker. We recompute the AND — never trust a transcript."""
    mc = maker_checker_error(check)
    if mc:
        return _result(check, ERROR, None, mc, "B")
    integrity = integrity_error(check)
    if integrity:
        return _result(check, ERROR, None, integrity, "B")
    exec_spec = check.get("exec", {})
    artifact = exec_spec.get("verdict_file") or check.get("signal", {}).get("file")
    if not artifact:
        return _result(check, ERROR, None, "evidence check has no verdict_file", "B")
    fpath = artifact if os.path.isabs(artifact) else os.path.join(cwd, artifact)
    try:
        with open(fpath, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return _result(check, ERROR, None, f"verdict artifact unreadable: {exc}", "B")

    # Freshness: an optional content hash anchors the verdict to what was seen.
    expected_hash = check.get("source", {}).get("evidence_hash")
    if expected_hash and data.get("evidence_hash") != expected_hash:
        return _result(check, ERROR, data.get("verdict"),
                       "evidence hash mismatch (stale verdict)", "B")

    verdict_by = data.get("verifier")
    maker = check.get("owner")
    if not verdict_by:
        return _result(check, ERROR, None,
                       "evidence verdict has no 'verifier' (maker != checker unverifiable)", "B")
    if maker and verdict_by == maker:
        return _result(check, ERROR, None,
                       f"evidence verified by the maker ({maker}); maker != checker violated", "B")

    verdict = str(data.get("verdict", "")).lower()
    if verdict in ("pass", "green", "true"):
        return _result(check, GREEN, verdict, data.get("notes", "evidence verdict: pass"), "B")
    return _result(check, RED, verdict, data.get("notes", "evidence verdict: fail"), "B")


# --- budget / accounting ----------------------------------------------------

def budget_exhausted(state):
    """Return a blocked-reason string when a budget guard trips, else None.
    Pure read — no mutation of state."""
    budget = state.get("budget", {})
    spent = budget.get("spent", {})

    if spent.get("iterations", 0) >= budget.get("max_iterations", 8):
        return "blocked_budget"

    cap = budget.get("max_consecutive_failures", 3)
    # Only the checks the gate actually evaluates can trip the failure cap. A
    # quarantined (or never-admitted) check is excluded from every evaluation, so
    # `run_gate` never advances its `consecutive_failures` — a stale count left on
    # it from before it was quarantined must not block a loop whose admitted
    # checks are healthy. `admitted_checks` is the one definition of "admitted"
    # `run_gate` selects on, reused verbatim so the cap and the evaluation loop
    # can never disagree about which checks count.
    admitted, _ = admitted_checks(state)
    for check in admitted:
        if check.get("consecutive_failures", 0) >= cap:
            return "blocked_failures"

    timeout_min = budget.get("timeout_min")
    if timeout_min:
        # Fail closed: a timeout budget that cannot resolve its start instant
        # must never be silently skipped — that silent skip (the old
        # `if timeout_min and started_at:` short-circuit, paired with
        # `setdefault` never overwriting a bootstrap-written null) is exactly
        # the bug this guards against. In normal operation the stamping site
        # in `run_gate` makes `started_at` always resolvable by the time this
        # runs, so this branch is defence in depth for a hand-edited or
        # otherwise corrupted state file, not the primary fix.
        start = _parse_iso(spent.get("started_at"))
        if start is None:
            return "blocked_timeout"
        if (now_utc() - start).total_seconds() > timeout_min * 60:
            return "blocked_timeout"
    return None


def confirmation_threshold(state):
    """Consecutive greens required to stop. Floored at DEFAULT_CONFIRMATION_K
    (design invariant: K >= 3) so no descriptor can lower it to a single green."""
    ks = [DEFAULT_CONFIRMATION_K]
    for c in state.get("checks", []):
        try:
            ks.append(int(c.get("determinism", {}).get("confirmation_k", DEFAULT_CONFIRMATION_K)))
        except (TypeError, ValueError):
            ks.append(DEFAULT_CONFIRMATION_K)
    return max(ks)


# --- feedback ---------------------------------------------------------------

BOARD_W = 84        # target total width of a board row — see `status_board`
BOARD_WHY_MIN = 28  # ...but never squeeze WHY below this, however long the ids
BOARD_WHY_LINES = 3  # WHY wraps up to this many lines; beyond it, see board_why


def _pad(s, w):
    """Left-align a plain-text cell to `w` columns. Never truncates: a check id
    the board shortened would not be the id the maker greps for."""
    return str(s).ljust(w)


def board_why(r, width):
    """The WHY cell, wrapped to `width`: why the check reads the way it does, the
    value it measured, and any degradation tag. Returns `(lines, full_reason_if_
    truncated)`.

    The reason is whitespace-collapsed first, so a multi-line stderr excerpt
    cannot tear the table apart, then wrapped INSIDE the column — the terminal
    wrapping a long row at column 0 is what destroys the alignment the table
    exists for.

    A green check carries no reason (`predicate satisfied` restates the glyph);
    it carries its VALUE, which the glyph does not — that is what makes a green
    row worth its width.

    Only text past `BOARD_WHY_LINES` lines (a crash trace, not a reason) is cut,
    and the caller restates it in full below the table: `iterations[]` persists
    id / verdict / value but NOT reason, so this feedback is the only place a
    reason is ever written and the board must never be why it is lost.
    Degradation tags survive truncation by construction — they are appended
    after the cut, never inside it, because a `[DEGRADED: ...]` label the board
    silently swallowed is a downgrade the maker never learns about."""
    one_line = " ".join(str(r.get("reason") or "").split())
    val = "" if r.get("value") is None else f"value={r['value']}"
    body = (val or "—") if r["verdict"] == GREEN else one_line + (f" · {val}" if val else "")
    tags = "".join(f" [DEGRADED: {d}]" for d in r.get("degraded", []))

    limit = max(BOARD_WHY_MIN, width * BOARD_WHY_LINES - len(tags))
    truncated = len(body) > limit
    if truncated:
        body = body[:limit - 1] + "…"
    return textwrap.wrap(body + tags, width) or ["—"], (one_line if truncated else None)


def status_board(results, state, prev_verdicts):
    """The whole gate as one table — greens included — grouped by the owner who
    has to act on it and the kind of check they are acting on. Each row carries
    the verdict transition since the previous evaluation (Δ), the check id, its
    consecutive-failure count against the cap, and why it reads the way it does.
    `prev_verdicts` maps id → the previous iteration's verdict.

    A row carries ONLY what varies per check, because everything a row repeats
    unchanged is what made the old board unreadable — six checks printing six
    copies of the same three ratios. `conf`, `eval` and `budget` are the LOOP's
    counters, identical on every row by construction; they belong to the `▸ loop`
    progress line in `build_feedback` and appear there exactly once. `owner` and
    `type` are constant WITHIN a group by construction, so they are stated once
    in the group header: `owner` because "who is up?" is the first question a red
    board has to answer, `type` because it decides what acting on the row even
    means (a red `functional` is code to fix; a red `evidence` is a verdict
    artifact to request from someone who is not the maker). Grouping is what
    keeps a row inside ~80 columns — a wrapped row is not a table.

    (`eval` and `budget` are also two DIFFERENT counters — see `build_feedback`
    for why they must never be conflated into one `iter` ratio (F25/F11).)"""
    prev_verdicts = prev_verdicts or {}
    if not results:
        return []
    max_cf = state.get("budget", {}).get("max_consecutive_failures", 3)
    cf_by_id = {c.get("id"): c.get("consecutive_failures", 0) for c in state.get("checks", [])}

    def fails(r):
        return f"{cf_by_id.get(r.get('id'), 0)}/{max_cf}"

    groups = {}
    for r in results:
        groups.setdefault((r.get("owner") or "—", r.get("type") or "?"), []).append(r)
    # Groups with red work first, then the busiest, then alphabetical — a total
    # order over the data, so the same gate always renders the same board.
    order = sorted(groups, key=lambda key: (all(r["verdict"] == GREEN for r in groups[key]),
                                            -len(groups[key]), key))

    id_w = max([len("CHECK")] + [len(str(r.get("id"))) for r in results])
    f_w = max([len("FAILS")] + [len(fails(r)) for r in results])
    # A `🟢→🔴` cell is 5 columns wide (two double-width glyphs + an arrow) while
    # being 3 characters long, so the Δ header and every continuation indent are
    # padded to 5 by hand — `str.ljust` would count the glyphs as 1 and shear the
    # column. Everything right of Δ is plain text and pads normally.
    head_w = 2 + 5 + 2 + id_w + 2 + f_w + 2
    why_w = max(BOARD_WHY_MIN, BOARD_W - head_w)

    # The column header belongs to the BOARD, not to each group: repeating it per
    # group would reintroduce, one level up, the same restatement this table was
    # rewritten to remove.
    lines = [f"  Δ      {_pad('CHECK', id_w)}  {_pad('FAILS', f_w)}  WHY"]
    details = []
    for key in order:
        owner, kind = key
        rows = groups[key]
        green_n = sum(1 for r in rows if r["verdict"] == GREEN)
        lines.append(f"{green_n}/{len(rows)} green · {kind} · owner {owner}")
        for r in rows:
            prev = prev_verdicts.get(r["id"])
            prev_glyph = glyph(prev) if prev else "⚪"
            why, full = board_why(r, why_w)
            lines.append(f"  {prev_glyph}→{glyph(r['verdict'])}  {_pad(r.get('id'), id_w)}  "
                         f"{_pad(fails(r), f_w)}  {why[0]}")
            lines.extend(" " * head_w + w for w in why[1:])
            if full:
                details.append(f"  {r.get('id')}: {full}")
    # Only a reason too long for its cell is restated in full — the feedback text
    # is the only place a reason is ever written (iterations[] persists id /
    # verdict / value, not reason), so the board may shorten it but must never be
    # the reason it is lost.
    if details:
        lines.append("Full reason for the truncated row(s) above:")
        lines.extend(details)
    return lines


def cost_ask(results, state):
    """The tail of a blocked report: what remains red, the trend across the last
    few evaluations, and the human-only verb to grant more budget. The gate never
    extends itself — it lays out the cost and hands the decision to the human."""
    problems = [r for r in results if r["verdict"] != GREEN]
    ids = [r["id"] for r in problems]
    eval_iters = [it for it in state.get("iterations", []) if "results" in it]
    counts = [sum(1 for x in it["results"] if x.get("verdict") != GREEN) for it in eval_iters[-3:]]
    trend = " → ".join(str(c) for c in counts) if counts else "n/a"
    return [
        f"Cost to continue: {len(problems)} check(s) still not green {ids}; "
        f"red/error count (last {len(counts)} eval(s)): {trend}.",
        "This is a HUMAN decision — the gate never extends its own budget. If the remaining work "
        "is worth more budget, a human grants it with:",
        '  run_gate_checks.py --extend-budget iterations=<n>[,failures=<n>][,timeout_min=<n>] '
        '--user-confirmed "<the human\'s answer>"',
    ]


def build_feedback(results, state, decision, blocked_reason=None, prev_verdicts=None, iter_n=None):
    lines = []
    # One-time entry banner on the first evaluation, so the user sees the loop go live.
    if iter_n == 1:
        lines.append(f"▶ ENTERING LOOP — fairmind gate (Tier {state.get('hermeticity_tier', 'B')}, "
                     f"K={confirmation_threshold(state)})")
    if decision == DECISION_STOP_PASSED:
        k = confirmation_threshold(state)
        lines.append(f"🟢 LOOP GREEN — all {len(results)} admitted check(s) passed on {k} consecutive "
                     "evaluations. status=passed_pending_human. Awaiting the final human gate; "
                     "no auto-merge/deploy.")
        quarantine = state.get("quarantine", [])
        if quarantine:
            ids = [q.get("id") for q in quarantine]
            lines.append(f"NOTE: {len(quarantine)} check(s) QUARANTINED and excluded from the gate — "
                         f"the human must confirm their criteria are otherwise covered: {ids}")
    elif decision == DECISION_STOP_BLOCKED:
        lines.append(f"⛔ LOOP STOPPED — {blocked_reason}. The stop condition was NOT met.")
    elif decision == DECISION_ITERATE:
        greens = [r for r in results if r["verdict"] == GREEN]
        problems = [r for r in results if r["verdict"] != GREEN]
        if not problems:
            k = confirmation_threshold(state)
            c = state.get("confirmations", 0)
            lines.append(f"🟢 All {len(greens)} check(s) GREEN — confirmation {c}/{k}. "
                         "Re-verifying for stability; make no changes, just let the gate re-run.")
        else:
            lines.append(f"🔴 Gate RED — {len(problems)} of {len(results)} check(s) not green. "
                         "Address the following, then the loop will re-verify:")

    # Loop-level progress line, distinct from the per-check status board below:
    # a single-glance readout of where this loop stands (green ratio, confirmation
    # streak, evaluations run, budget consumed), emitted on every evaluation that
    # reaches feedback.
    #
    # `eval` and `budget` are TWO DIFFERENT COUNTERS and must never share a word or
    # a slash (F25, and F11 before it). `eval` counts every evaluation the gate has
    # run and is unbounded — a green evaluation is still an evaluation — so it has
    # no denominator. `budget` is `spent.iterations`, which counts ONLY the
    # budget-consuming (red/error) evaluations, against `max_iterations`. Printing
    # them as one `iter {n}/{max_iterations}` ratio divided an evaluation count by a
    # budget cap and told the operator a green evaluation had burned budget when it
    # burns none. Read `spent` here, never `n`.
    ref = state.get("target", {}).get("ref")
    green_n = sum(1 for r in results if r["verdict"] == GREEN)
    total_n = len(results)
    conf_n = state.get("confirmations", 0)
    k_n = confirmation_threshold(state)
    n = iter_n if iter_n is not None else sum(1 for it in state.get("iterations", []) if "results" in it)
    budget = state.get("budget", {})
    max_iter = budget.get("max_iterations", 8)
    spent_n = budget.get("spent", {}).get("iterations", 0)
    lines.append(f"▸ loop {ref} · {green_n}/{total_n} green · conf {conf_n}/{k_n} · "
                 f"eval {n} · budget {spent_n}/{max_iter}")

    if any((r.get("reason") == "gate deadline exceeded") for r in results):
        lines.append("[DEGRADED: gate deadline] the gate ran out of wall-clock budget; "
                     "unfinished checks are ERROR (fail-closed), never green.")

    # One table, one row per check. It replaced a board plus a per-item list that
    # restated every non-green check's id, verdict and owner a second line down —
    # so a six-check gate printed twelve rows to say six things.
    board = status_board(results, state, prev_verdicts)
    if board:
        lines.append("Status board · Δ = change since the previous evaluation · "
                     "fails = consecutive/cap:")
        lines.extend(board)

    by_owner = {}
    for r in results:
        if r["verdict"] != GREEN:
            by_owner.setdefault(r["owner"], []).append(r["id"])

    if decision == DECISION_STOP_BLOCKED:
        lines.extend(cost_ask(results, state))

    if any(r["verdict"] != GREEN for r in results):
        lines.append("Journal rule: APPLY or REBUT every non-green item above; "
                     "rebuttals go to the checker, never edit descriptors.")

    primary_owner = None
    if by_owner:
        primary_owner = sorted(by_owner.items(), key=lambda kv: -len(kv[1]))[0][0]
    return "\n".join(lines), primary_owner


# --- commitment boundaries --------------------------------------------------

def _anti_correlated_pair(window):
    """Given up to N result-lists (each `[{id, verdict}, ...]`), return a pair of
    check ids that are *strictly anti-correlated* over the last 3 evaluations
    (one green exactly when the other is not) and that actually vary — a genuine
    tension, not just one always-passing + one always-failing. Else None."""
    recent = [w for w in window if w][-3:]
    if len(recent) < 3:
        return None
    series = {}
    for res in recent:
        greens = {x["id"]: (x.get("verdict") == GREEN) for x in res}
        for cid, g in greens.items():
            series.setdefault(cid, []).append(g)
    ids = [cid for cid, s in series.items() if len(s) == 3]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            sa, sb = series[a], series[b]
            if all(x != y for x, y in zip(sa, sb)) and len(set(sa)) > 1:
                return (a, b)
    return None


def commitment_boundaries(results, state, in_flight=False):
    """Warn-and-route boundaries read from iteration history. Never quarantine —
    only surface a strategic question and re-route the feedback. Returns
    `(banners, routing_override, strategy_ids)`.

    `in_flight` (H8/PCF-10): the current evaluation is reading a half-written
    tree, so its verdicts are unreliable and must not seed the anti-correlation
    detector; past in-flight evaluations are dropped from the window too."""
    banners = []
    routing_override = None
    strategy_ids = []

    checks_by_id = {c.get("id"): c for c in state.get("checks", [])}
    already = {cid for it in state.get("iterations", []) for cid in it.get("strategy_turn", [])}

    # (a) Strategy turn — a check one failure below the cap. The question is no
    # longer "fix it" but "is the CHECK wrong, or the APPROACH?" Route once to the
    # checker (authored_by), not the maker, before the last iteration is burned.
    for r in results:
        c = checks_by_id.get(r["id"], {})
        if c.get("consecutive_failures") == 2 and r["id"] not in already:
            checker = c.get("source", {}).get("authored_by")
            banners.append(
                f"STRATEGY TURN [{r['id']}]: 2 consecutive failures (one below the cap). "
                f"Is the check wrong, or the approach? Routing to the checker ({checker}) to "
                "reconsider the check before the maker burns the last iteration.")
            strategy_ids.append(r["id"])
            if routing_override is None:
                routing_override = checker

    # (b) Contract conflict — two checks strictly anti-correlated over the last 3
    # evaluations: satisfying one breaks the other, so the two criteria may be in
    # tension. Warn and route to the Technical Lead to reconcile the contract.
    #
    # H8/PCF-10: consider only SETTLED evaluations. An in-flight evaluation reads a
    # half-written tree, so its verdicts are noise — feeding them here produced a
    # FALSE CONTRACT CONFLICT when two checks flipped one evaluation apart across
    # an amendment that was merely mid-write. Drop in-flight iterations from the
    # window, and skip this eval's own verdicts while it is itself in-flight;
    # `_anti_correlated_pair` needs 3, so a window thinned below 3 yields nothing.
    prior = [it["results"] for it in state.get("iterations", [])
             if "results" in it and not it.get("in_flight")]
    window = prior[-2:]
    if not in_flight:
        window = window + [[{"id": r["id"], "verdict": r["verdict"]} for r in results]]
    conflict = _anti_correlated_pair(window)
    if conflict:
        a, b = conflict
        banners.append(
            f"CONTRACT CONFLICT [{a} vs {b}]: strictly anti-correlated over the last 3 "
            "evaluations — satisfying one breaks the other. The two criteria may conflict. "
            "Routing to the Technical Lead to reconcile the contract (warn only — neither "
            "check is quarantined).")
        routing_override = "tech-lead"

    return banners, routing_override, strategy_ids


# --- main evaluation --------------------------------------------------------

def admitted_checks(state):
    """Split state['checks'] into (admitted, pending) using the ONE definition of
    "admitted": `admission.status == "passed"` AND `source.admitted_hash` is
    PRESENT and equals `descriptor_hash(check)`, AND `id` not in `quarantine[]`.

    (F35/H2) `admission.status == "passed"` alone is a CLAIM, not proof: a
    hand-forged descriptor — a plausible `authored_by`, a hand-typed
    `admission.status: "passed"`, never actually probed by `admit_check.py` —
    used to satisfy this predicate and could gate a loop green forever.
    `admitted_hash` is the one artifact only `admit_check.py`'s `_finalize`
    stamps (`admit_check.py:230-231`, on every admission path — `admit_one`,
    `admit_evidence`, `admit_guard`), computed by the same `descriptor_hash`
    this function calls: possession of a hash that still matches the live
    descriptor IS the proof admission genuinely ran over THIS exact
    descriptor. A missing hash (legacy/hand-forged) or a present-but-stale one
    (descriptor edited post-admission — the tamper case `integrity_error`
    also independently ERRORs on, once selected) both fail this predicate —
    neither is "admitted", so neither can be selected into the evaluation
    loop, gate the confirmation streak, or satisfy `--arm`'s coverage check.

    Migration policy (AC5, deliberate, REFUSE — never auto-heal): a legacy
    `passed`-with-no-hash check is NOT silently granted a stamped hash on
    first sight — that would launder a forged/never-probed descriptor into a
    "proven" one. It is treated as UNADMITTED: `run_gate` reports it pending
    (ERROR, "not admitted") and `--arm` refuses until a human re-runs
    `admit_check.py`.

    Shared by `run_gate` (evaluation), `budget_exhausted`, `_classify_criteria`
    and `--arm` (C3/AC1) so the engine never carries two drifting definitions
    of 'admitted'."""
    quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}
    admitted, pending = [], []
    for c in state.get("checks", []):
        if c.get("id") in quarantine_ids:
            continue  # explicitly quarantined → surfaced to the human, excluded
        admitted_hash = c.get("source", {}).get("admitted_hash")
        if (c.get("admission", {}).get("status") == "passed"
                and admitted_hash
                and admitted_hash == descriptor_hash(c)):
            admitted.append(c)
        else:
            pending.append(c)
    return admitted, pending


# --- contract validation (T10) ----------------------------------------------
# Arm-time / contract-time only (R8): NEVER called from run_gate — the evaluation
# path gains no criteria logic. `--validate-contract` (read-only) and `--arm`
# (which refuses on error) share this ONE code path so the guarantee lives in the
# engine, not in an orchestrator's compliance with a markdown instruction (R1).

# The disposition prefixes that assert real coverage vs. the advisory escape hatch.
_COVERAGE_PREFIXES = ("checked", "evidence")
_ADVISORY_UNVERIFIABLE = "unverifiable"


def _parse_disposition(disposition):
    """Parse `contract.criteria[].disposition` into `(kind, check_id, malformed)`.

    `kind` ∈ {"checked", "evidence", "quarantined", "unverifiable"} (the prefix
    set of CRITERION_DISPOSITIONS); `check_id` is the id a prefix form names (None
    for the bare `unverifiable`). `malformed` is a reason string when the value is
    not a well-formed disposition at all (null/absent, empty, unknown prefix, or a
    prefix form with no id) — AC2(e). A well-formed disposition still has to AGREE
    with reality; that cross-check is `_classify_criteria`'s job, not this one's."""
    if disposition is None:
        return None, None, "disposition is null or absent"
    if not isinstance(disposition, str):
        return None, None, f"disposition is not a string ({type(disposition).__name__})"
    d = disposition.strip()
    if d == "":
        return None, None, "disposition is empty"
    if d == _ADVISORY_UNVERIFIABLE:
        return _ADVISORY_UNVERIFIABLE, None, None
    if ":" in d:
        prefix, _, cid = d.partition(":")
        prefix, cid = prefix.strip(), cid.strip()
        if prefix not in _COVERAGE_PREFIXES + ("quarantined",):
            return None, None, f"unknown disposition prefix {prefix!r}"
        if not cid:
            return None, None, f"disposition {prefix!r} names no check id"
        return prefix, cid, None
    # A bare token that is not `unverifiable` and carries no ':' — e.g. a bare
    # `checked` (prefix with no id and no colon).
    return None, None, f"malformed disposition {d!r} (prefix with no check id)"


def _admission_gap_reason(check):
    """(H2/AC5) WHY a check failed `admitted_checks`'s strengthened predicate —
    three factually distinct human fixes, never conflated into one string that
    could lie about a check's actual `admission.status`:

      - "never admitted"       — `admission.status` is not (yet) 'passed'.
        `admission.status != 'passed'` is a TRUE claim here.
      - "admitted, hash missing" — status IS 'passed' but `source.admitted_hash`
        is absent (the legacy/hand-forged shape H2 exists to catch). Saying
        "admission.status != 'passed'" for this check would be FALSE — the
        status genuinely is 'passed'; only the proof artifact is missing.
      - "hash mismatched"      — status is 'passed' and a hash IS present, but
        it no longer matches `descriptor_hash(check)` (the descriptor was
        edited after admission — the same tamper `integrity_error` also
        catches independently once a check reaches evaluation).

    Every branch names `admit_check.py` / re-admission as the true remedy, so
    a reader who only sees this string still knows what to run."""
    status = check.get("admission", {}).get("status")
    admitted_hash = check.get("source", {}).get("admitted_hash")
    if status != "passed":
        return ("never admitted (admission.status is not 'passed') — run "
                "admit_check.py")
    if not admitted_hash:
        return ("admission.status is 'passed' but source.admitted_hash is "
                "MISSING — admission was never proven for this exact "
                "descriptor (legacy or hand-authored 'passed'); re-admit with "
                "admit_check.py")
    return ("admission.status is 'passed' but source.admitted_hash no longer "
            "matches descriptor_hash(check) — the descriptor changed since "
            "admission; re-admit with admit_check.py")


def _classify_criteria(state):
    """Classify every `contract.criteria[]` entry against the loop's real check
    state, returning `(errors, advisories)` — each a list of
    `{"id", "disposition", "reason"}`.

    `errors` are the coverage failures that make the loop UNARMABLE (AC2/AC4):
    a HARD criterion not backed by a live, admitted check. `advisories` are the
    explicit `hard: false` holes — armable, but named on stdout so the human who
    signs the arm sees them (AC3, a silent pass is a defect).

    Reuses `admitted_checks(state)` — the engine's ONE definition of "admitted"
    (`admission.status == "passed"` AND not in `quarantine[]`) — so validation can
    never carry a second, drifting definition (AC2(c))."""
    contract = state.get("contract") or {}
    criteria = contract.get("criteria")
    if not criteria:  # absent, null, or empty → mandatory, never inferred (AC4)
        return ([{"id": None, "disposition": None,
                  "reason": "contract.criteria is absent, null, or empty — it is "
                            "mandatory for arming and is never inferred"}], [])

    admitted, _pending = admitted_checks(state)
    admitted_ids = {c.get("id") for c in admitted}
    checks = state.get("checks") or []
    all_ids = {c.get("id") for c in checks}
    check_by_id = {c.get("id"): c for c in checks}
    quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}

    errors, advisories = [], []
    for crit in criteria:
        disp = crit.get("disposition")
        hard = crit.get("hard")
        if hard is None:
            hard = True  # R3: fail-closed — hardness defaults to True
        entry = {"id": crit.get("id"), "disposition": disp}

        def err(reason):
            errors.append({**entry, "reason": reason})

        def adv(reason):
            advisories.append({**entry, "reason": reason})

        kind, target, malformed = _parse_disposition(disp)
        if malformed is not None:
            err(malformed)  # AC2(e)
            continue

        if kind == _ADVISORY_UNVERIFIABLE:
            if hard:
                err("hard criterion is 'unverifiable' — a hard criterion must be "
                    "covered by a live, admitted check")  # AC2(a)
            else:
                adv("advisory (hard:false) criterion is unverifiable")  # AC3
            continue

        if kind == "quarantined":
            if target not in all_ids:
                err(f"disposition names check {target!r}, which is absent from "
                    "checks[]")  # AC2(b)
            elif hard:
                err(f"hard criterion is covered only by quarantined check {target!r}, "
                    "which contributes nothing to the stop condition")  # AC2(d)
            elif target not in quarantine_ids:
                err(f"disposition claims check {target!r} is quarantined, but it is "
                    "not in quarantine[]")
            else:
                adv(f"advisory (hard:false) criterion covered only by quarantined "
                    f"check {target!r}")  # AC3
            continue

        # kind ∈ {"checked", "evidence"} — a claim of real coverage.
        if target not in all_ids:
            err(f"disposition names check {target!r}, which is absent from "
                "checks[]")  # AC2(b)
            continue
        if target not in admitted_ids:
            # The disposition must AGREE with the engine's own admitted predicate
            # (AC2(c)). Distinguish WHY it is not admitted — the two failures need
            # two different fixes, so the reason must actually differ (AC6 teeth).
            # (H2/AC5) A quarantined check is reported via the quarantine branch;
            # anything else routes through `_admission_gap_reason`, which itself
            # distinguishes never-admitted / hash-missing / hash-mismatched so the
            # message can never falsely claim `admission.status != 'passed'` for a
            # check whose status genuinely IS 'passed'.
            if target in quarantine_ids:
                err(f"check {target!r} is quarantined (admitted, then excluded from "
                    "the stop condition) — re-admit it with admit_check.py")
            else:
                err(f"check {target!r} is not admitted: "
                    f"{_admission_gap_reason(check_by_id.get(target, {}))}")
            continue
        if kind == "evidence":
            tc = check_by_id.get(target, {})
            if not (tc.get("kind") == "evidence" or tc.get("type") == "evidence"):
                err(f"disposition 'evidence:{target}' but check {target!r} is not an "
                    "evidence-kind check")
                continue
        # Fully covered by a live, admitted check — nothing to report.
    return errors, advisories


def validate_contract(state):
    """The single contract-validation predicate (R1). Returns the list of coverage
    errors (empty ⇒ armable). A HARD criterion is covered iff its disposition is
    `checked:<id>` or `evidence:<id>` at an ADMITTED check (and, for `evidence:`,
    that check is evidence-kind). Everything else on a hard criterion — an absent
    contract, `unverifiable`, `quarantined:<id>`, an absent/non-admitted check, or
    a malformed disposition — is an error. Read-only: mutates nothing, evaluates
    no check (R8)."""
    errors, _advisories = _classify_criteria(state)
    return errors


def _contract_offender_line(offender):
    """One human-readable offender line: names the criterion id, its disposition,
    and the specific reason it is uncovered."""
    cid = offender.get("id")
    disp = offender.get("disposition")
    return f"  - {cid} ({disp!r}): {offender['reason']}"


def _emit_contract_refusal(errors, verb):
    """The refusal text on STDERR, shared verbatim by `--validate-contract` and
    `--arm` (AC6): every offender by id + disposition + reason, then an explicit
    recommendation to run the task in interactive mode rather than arm a weak gate.
    The recommendation lives where it is EXECUTED — a human who never reads the
    command doc still gets it."""
    n = len(errors)
    print(f"{verb} refused: {n} hard criterion/criteria are not covered by a live, "
          "admitted check — this contract is not armable:", file=sys.stderr)
    for e in errors:
        print(_contract_offender_line(e), file=sys.stderr)
    print("RECOMMENDATION: do not arm a weak gate. A task whose hard criteria "
          "cannot be covered by a live, admitted check should be run in INTERACTIVE "
          "mode, not armed behind a gate that cannot enforce them. Cover each "
          "criterion with an admitted check, or downgrade a genuinely advisory one "
          "to `hard: false`, then re-run.", file=sys.stderr)


# --- the design brief, checked at ARM -----------------------------------------
#
# The completeness verdict is refused six ways (status, streak, maker role,
# attested identity, replay, stale signature) while the artifact that verdict is
# ABOUT was, for one round, not checked at all — the engine would accept a
# `complete` verdict on a loop with no brief, and every question the reviewer
# asks begins "does the diff implement every decision in the brief". That is
# `fairmind-gate`'s own rule ("a guarantee that depends on a step having run is
# not a guarantee unless the engine can prove the step ran") applied to one half
# of a pair and not the other — and the brief is the half that carries the
# retrodiction: without it naming the layer, the completeness gate has nothing to
# compare and catches nothing.
#
# ⚠️ WHAT THIS PREDICATE CAN AND CANNOT SEE. It proves a brief EXISTS and carries
# some prose. It cannot tell a brief that answered "where does this invariant
# live?" from one that filled the heading and moved on — a five-line brief is
# legitimate (the sections genuinely have one answer each) and looks much like a
# stub. So this refuses the absent and the empty, and the reviewer at the exit
# gate is what catches the hollow. Stated rather than implied, because a check
# whose limits are unwritten gets read as covering more than it does.
_BRIEF_MIN_PROSE = 120


def design_brief_path(state, state_path):
    ref = ((state.get("target") or {}).get("ref") or "loop")
    ref = re.sub(r"[^A-Za-z0-9._-]", "-", str(ref))
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), "design", f"{ref}.md")


def validate_design_brief(state, state_path):
    """None when the loop has a usable design brief, else the refusal reason."""
    path = design_brief_path(state, state_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return (f"no design brief at {path}. The loop's second exit check asks whether the diff "
                "implements every decision in the brief, at the layer the brief named — with no "
                "brief there is nothing to review against, and the contract alone cannot ask the "
                "question (it is compiled from the ticket, so when the ticket is wrong about a "
                "layer the checks are wrong with it, and green). Write it before arming: "
                "fairmind-gate/references/design-brief.md.")
    prose = "\n".join(ln for ln in text.splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#"))
    if len(prose.strip()) < _BRIEF_MIN_PROSE:
        return (f"the design brief at {path} carries {len(prose.strip())} characters of prose "
                f"outside its headings (minimum {_BRIEF_MIN_PROSE}) — that is a template, not a "
                "brief. A short brief is fine when the questions genuinely have one answer each; "
                "an empty one means they were not asked.")
    return None


def validate_contract_verb(state, state_path):
    """`--validate-contract` (AC1): the read-only twin of `--arm`'s validation.
    Prints the coverage report to STDOUT (naming every offender AND every advisory
    hole), and on failure the actionable refusal to STDERR. Never mutates
    loop-state.json and never evaluates a check. Exit 0 when armable, non-zero
    otherwise."""
    errors, advisories = _classify_criteria(state)
    brief_error = validate_design_brief(state, state_path)

    # Report → stdout, on both the pass and the fail path. The brief is reported
    # on BOTH too: this verb answers "is this armable?", and since --arm refuses a
    # loop with no design brief, a report that only mentioned it when coverage
    # happened to be clean would answer that question wrong in exactly the case a
    # human runs it for.
    print("Design brief: " + (f"MISSING — {brief_error}" if brief_error
                              else "OK — " + design_brief_path(state, state_path)))
    if errors:
        print(f"Contract coverage: {len(errors)} uncovered hard criterion/criteria "
              "— this loop is NOT armable:")
        for e in errors:
            print(_contract_offender_line(e))
    else:
        print("Contract coverage: OK — every hard criterion is covered by a live, "
              "admitted check.")
    if advisories:
        print("Advisory (hard:false) uncovered criteria — armable, but the human "
              "must confirm each hole is acceptable:")
        for a in advisories:
            print(_contract_offender_line(a))

    if errors:
        _emit_contract_refusal(errors, "--validate-contract")
        return EXIT_INTERNAL_ERROR
    if brief_error:
        print(f"--validate-contract: {brief_error}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    return EXIT_ALLOW_STOP


# --- state["lifecycle"] — the loop's own transition record (JC1/JC3/JC4) ------
#
# One top-level block holding the instants a loop passes THROUGH, as opposed to
# `iterations[]`, which holds what the gate EVALUATED. The split is deliberate:
# an entry with no `results` key is already invisible to every evaluation
# counter, but keeping the human-control instants out of that array entirely
# means `_ITERATION_FIELDS` never has to classify a new event kind, and no
# future counter can accidentally see one.
#
# Nothing here moves `status`, `confirmations` or `iterations[]`. A verb that
# can touch the gate's decision surface is a gate bypass; the two verbs below
# are deliberately inert with respect to it.
#
# ⚠️ THE `arm` TRANSITION IS NOT HERE, and that is not an omission: its sha is
# `contract.mutation_set.baseline.ref` and its instant is
# `budget.spent.first_armed_at`, both already written at arm time. One writer
# per fact — the payload builder PROJECTS those two, it does not re-record them.
# There is likewise NO `merge` key: the merge outcome is computed server-side,
# so a client key that is permanently null
# unless someone re-flushes would be a join key that does not join.
_LIFECYCLE_KEY = "lifecycle"

# What survives a re-arm. Everything ELSE under `lifecycle` is dropped, and the
# default direction is deliberate: the failure this exists to stop is a value
# from an ABANDONED round surviving into the next one and producing a confident
# wrong answer (a stale post-ceremony signature makes the divergence detector
# report `diverged` against a round that never happened). Dropping an unknown
# future key costs a diagnostic; keeping it can cost a false measurement.
# TWO exceptions, and both for the same reason: the record a re-arm produces is
# the point of keeping it. `human_gate` holds the rejection history, so clearing
# it would erase exactly what it records. `completeness` holds the review
# round-trips (a `gaps` verdict, the fix, the `complete` that followed), and a
# stale row cannot mislead the way a stale ceremony signature can: the flip
# additionally requires the latest row to still MATCH this tree, so a verdict
# carried across a re-arm unblocks nothing on its own.
_LIFECYCLE_PRESERVED_ON_REARM = ("human_gate", "completeness")


def _lifecycle(state):
    """`state["lifecycle"]` as a dict, for READERS.

    `state.get("lifecycle", {})` substitutes the default only when the key is
    ABSENT. A `"lifecycle": null` on disk is legal JSON, returns None, and the
    `.get()` chained onto it raises — inside `_completeness_blocker`, which the
    gate calls unconditionally once the streak reaches K, so a single null would
    crash the evaluation rather than fail it. Every reader goes through here."""
    lifecycle = state.get(_LIFECYCLE_KEY)
    return lifecycle if isinstance(lifecycle, dict) else {}



def _record_gate_green(state, iteration, n, work_dir, members):
    """Record the instant the gate went green (§B.2), at the status flip.

    `at` is the iteration's OWN timestamp — the clock is not read a second time,
    so the row can never disagree with the evaluation it describes. `iteration_n`
    is written here rather than searched for later: every K-th confirmation
    green looks alike from the outside and audit entries can follow it, so the
    index is only unambiguous at the instant of the flip. It is also the anchor
    a human-gate verdict records its position against.

    Both optional keys are OMITTED on degrade, never null: `commit_sha` when
    HEAD does not resolve, `diff_stat` when the mutation set is unknown.

    `members` is the mutation set `run_gate` computed for THIS evaluation's
    no-work signature (`_signature_members`), handed down rather than rebuilt —
    see `_numstat`. None (a degraded signature) makes it rebuild, exactly as
    before."""
    row = {"at": iteration["at"]}
    sha = iteration.get("commit_sha")
    if sha:
        row["commit_sha"] = sha
    row["iteration_n"] = n
    diff_stat = _numstat(work_dir, _mutation_baseline(state).get("ref"), members)
    if diff_stat is not None:
        row["diff_stat"] = diff_stat
    lifecycle = state.get(_LIFECYCLE_KEY)
    if not isinstance(lifecycle, dict):
        lifecycle = {}
        state[_LIFECYCLE_KEY] = lifecycle
    lifecycle["gate_green"] = row


# --- state["consent"] — frozen at collection, intersected across windows -----
#
# ⚠️ THIS MODULE HOLDS NO CONSENT VOCABULARY OF ITS OWN, AND THAT IS THE POINT.
# The stamp version, the three class letters and the weakest-first basis order
# used to be spelled here as `_CONSENT_VERSION` / `_CONSENT_ALL_CLASSES` /
# `_CONSENT_BASIS_ORDER` — a THIRD copy of what `_consent_authority` owns and
# `insights_flush_payload` reads. `grep` over `tests/` on 2026-08-14 returned
# ZERO reads of any of the three, i.e. nothing could ever have caught a drift.
# This module WRITES the stamp that `insights_flush_payload` READS, so a
# vocabulary bump applied to the authority and not to the copy fails loudly in
# one module and SILENTLY ON THE WIRE in the other. The three values are now
# read off the authority at call time; the WHY behind each of them lives beside
# it in `_consent_authority` and is deliberately not restated here.


def _consent_authority():
    """`_consent_authority` — the ONE authority for what a config grants AND for
    the vocabulary a stamp is written in — or None when it is not importable.

    LAZY AND GUARDED, the same idiom `loop_ledger` uses at the end of `main`: a
    module-level import would let a broken or absent consent module take the
    whole gate engine down at load time, and a consent read must never be able
    to fail an arm. After the first successful call it is a `sys.modules`
    lookup, so the two asks one arm makes cost one import between them."""
    try:
        import _consent_authority
        return _consent_authority
    except Exception:  # noqa: BLE001 — a consent read must never fail an arm
        return None


def _resolve_consent_grant(cwd):
    """`(classes, basis)` resolved from the repo-root config, or None when no
    resolution was possible at all.

    The resolution itself lives in `_consent_authority` — ONE authority for what
    a config grants, asked by both lanes — reached through `_consent_authority`.
    The config PATH comes from that module too (`consent_config_path`) rather
    than a second hand-built `join(root, ".fairmind-insights.json")`: a mistyped
    filename here is simply an ABSENT file, and an absent file is a legitimate,
    silent, well-defined state (`no_config`, which grants all three), so no test
    could ever catch the typo.

    None is NOT a fourth basis. `pre_consent` / `no_config` / `legacy_config` /
    `explicit` all describe a resolution that HAPPENED; "the resolver was not
    reachable" is a different fact, and inventing a fifth value for it would be
    a contract change this file cannot make on its own. The caller therefore
    writes nothing on None — see `_consent_stamp`."""
    root = _resolve_repo_root(cwd)
    if not root:
        return None
    consent = _consent_authority()
    if consent is None:
        return None
    try:
        classes, basis = consent.config_consent_classes(
            consent.consent_config_path(root))
    except Exception:  # noqa: BLE001 — a consent read must never fail an arm
        return None
    if not isinstance(classes, list) or basis not in consent.CONSENT_BASIS_ORDER:
        return None
    return sorted({c for c in classes if isinstance(c, str)}), basis


def _consent_stamp(existing, resolved, ever_armed):
    """The `state["consent"]` value to persist at arm time, or None to leave
    whatever is on disk alone (§B.7).

    ⚠️ ON A RE-ARM THE STAMP IS AN INTERSECTION, NEVER LAST-WRITE-WINS, and
    this is a live case rather than a corner: `iterations[]` is append-only
    across the whole loop lifetime — every write site is `.append(...)`, nothing
    truncates it — so a loop armed under three classes, rejected, narrowed, then
    re-armed and re-greened SHIPS all of its iterations, including the ones
    collected under the wider grant, under ONE stamp. Last-write-wins is wrong
    in both directions: narrow→wide would claim a grant that did not exist when
    the data was collected, and wide→narrow would label old data with a grant it
    was never collected under. A class is claimable only if it was granted in
    EVERY window that contributed data. Narrowing therefore applies
    retroactively (over-withholding is safe); widening never applies backwards.

    Three inputs and no config file to stage, so the rule stays testable on its
    own; the consent VOCABULARY is read off the authority (`_consent_authority`)
    rather than copied here, and by the time a real arm reaches this function
    the resolution has just imported it, so that read is a `sys.modules` lookup:
      `existing`   — `state["consent"]` as found on disk (may be absent/garbage)
      `resolved`   — `_resolve_consent_grant`'s answer, or None
      `ever_armed` — arm()'s own fresh-vs-re-arm fact

    A loop armed BEFORE this stamp existed is not a fresh loop: its earlier
    windows really did collect data, under a grant nobody resolved. That is
    exactly what `pre_consent` means, so it is folded in as an implicit prior
    stamp of all three classes on the weakest basis — which is also what the
    payload builder infers for such a loop, so the two never disagree.

    An UNREADABLE existing stamp fails closed to no classes: over-withholding is
    the safe direction, and a label we cannot read is a label we cannot claim.

    ONE RETURN, THREE PRIORS. The fresh arm used to return early with
    `{sorted(classes), version, basis}`; it IS the common return with the
    resolved grant as its own prior, since `sorted(set(c) & set(c)) ==
    sorted(c)` and `min(b, b, key=…) == b`. ⚠️ That equivalence RESTS ON
    `_resolve_consent_grant` handing back a duplicate-free list (it builds one
    with a set comprehension): a resolver that stopped deduping would make
    `set(classes)` drop duplicates `sorted(classes)` kept, and the two spellings
    would silently part."""
    consent = _consent_authority() if resolved is not None else None
    if consent is None:
        # No resolution — preserve. Intersecting with "unknown" is not
        # intersecting with "nothing"; a resolver that could not be reached must
        # never narrow a grant a config actually made. An UNREACHABLE AUTHORITY
        # is that same fact one step earlier, so it gets the same answer.
        return existing if isinstance(existing, dict) else None

    classes, basis = resolved
    if existing is not None:
        # ⚠️ The authority's own reader, adopted 2026-08-14 because the inline
        # read that stood here had DIVERGED from `insights_flush_payload.
        # _consent` — the other reader of this very record — on
        # `{"classes": ["A","B","C"], "basis": <a basis this build does not
        # know>}`: this side read the whole record as unreadable and failed
        # closed to `[]` + `pre_consent`, intersecting the loop's grant down to
        # NOTHING, while the payload builder kept all three classes and
        # relabelled the basis. The payload builder's rule is the one already
        # written down ("a future writer adding a fifth basis name must not cost
        # this loop its whole grant"), so it wins. The two facts are read
        # INDEPENDENTLY: an unreadable CLASS LIST still fails closed (to `[]` on
        # the weakest basis, so a corrupt record can never strengthen the
        # attribution the `min` below picks), while an unrecognized BASIS alone
        # no longer costs the loop its classes. Distinct from ABSENT, below.
        prior_classes, prior_basis = consent.read_frozen_stamp(existing)
    elif ever_armed:
        prior_classes, prior_basis = (list(consent.ALL_CONSENT_CLASSES),
                                      consent.CONSENT_BASIS_PRE_CONSENT)
    else:
        # A fresh arm has no earlier window to intersect with — the resolved
        # grant is its own prior, and the return below then reduces to it.
        prior_classes, prior_basis = classes, basis

    return {
        "classes": sorted(set(prior_classes) & set(classes)),
        "version": consent.CONSENT_VERSION,
        "basis": min(prior_basis, basis, key=consent.CONSENT_BASIS_ORDER.index),
    }


def run_gate(state, cwd, dry_run=False):
    """Evaluate all checks, mutate state in place, return a decision dict.

    `cwd` is `state_root` — unchanged (state resolution, the run ledger, and
    the trace file all still key off it). `dry_run` is threaded through to
    `evaluate_scope` ONLY (F26) — every other behavior in this function is
    unaffected by it; `main` still gates its own persistence and
    terminal-status semantics on `args.dry_run` separately.

    H1/F34: the FIRST thing this function does is resolve `work_dir` — the
    tree every git query and every check subprocess actually run against
    (`resolve_work_dir`). A recorded `worktree.path` that cannot be trusted
    degrades the WHOLE evaluation closed, before the scope boundary and
    before any check runs: a silent fallback to state_root here would be
    F34 recurring under a different name."""
    work_dir, wt_degradation = resolve_work_dir(state, cwd)
    if wt_degradation is not None:
        reason, detail = wt_degradation
        state["status"] = "blocked_worktree"
        # Named in PERSISTED state (status + an iterations[] audit entry, the
        # same family as `scope_violation`) — never only on stdout/feedback.
        # No "results" key: every consumer that counts EVALUATIONS by testing
        # `"results" in it` (confirmation streaks, budget accounting) stays
        # blind to this entry, exactly like `arm`'s C8 audit entry — this is
        # not an evaluation of any check, it is a refusal to evaluate at all.
        audit_entry = {
            "event": "worktree_degraded",
            "at": iso(now_utc()),
            "reason": reason,
            "detail": detail,
        }
        state.setdefault("iterations", []).append(audit_entry)
        feedback = (
            "⛔ LOOP STOPPED — blocked_worktree: this loop records a "
            f"worktree.path that could not be resolved to a real, registered "
            f"worktree of this repo's own git history ({reason}: {detail}). "
            "Failing closed rather than silently evaluating the main tree — "
            "a silent fallback there would be the exact defect (F34) this "
            "guard exists to prevent. A human must resolve the worktree "
            "(re-create it via loop_worktree.py --create, or clear "
            "state['worktree'] and re-arm) before this loop can proceed."
        )
        return {"decision": DECISION_STOP_BLOCKED, "feedback": feedback, "results": []}

    # T8: the scope-boundary hard stop runs BEFORE anything else — a check's
    # verdict (or even whether there is an admitted check at all) must never
    # mask an out-of-scope mutation. `evaluate_scope` is a no-op (returns
    # None) whenever no scope is declared, so this is transparent to every
    # loop that doesn't use `contract.scope` (AC3). Diffs `work_dir` (the
    # worktree when one is recorded and valid) but resolves the trace FILE
    # against `trace_root=cwd` (ALWAYS state_root — H1/F34's three-concept
    # split; see `evaluate_scope`'s docstring).
    scope_result = evaluate_scope(state, work_dir, dry_run=dry_run, trace_root=cwd)
    if scope_result is not None:
        audit_entry, feedback = scope_result
        state["status"] = "blocked_scope"
        state.setdefault("iterations", []).append(audit_entry)
        return {"decision": DECISION_STOP_BLOCKED, "feedback": feedback, "results": []}

    requested_tier = state.get("hermeticity_tier", "B")
    tier = requested_tier
    hermeticity_downgraded = False
    if tier == "A" and not srt_available():
        tier = "B"  # graceful degradation — sandbox absent at run time
        hermeticity_downgraded = True  # requested A, forced to B → surface it
        state["hermeticity_tier"] = "B"

    admitted, pending = admitted_checks(state)
    quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}

    # No admitted check → the loop can never legitimately close. Stop and flag
    # for the human rather than silently passing or burning the whole budget.
    if not admitted:
        reason = "no admitted checks to gate on"
        if pending:
            reason += f"; {len(pending)} check(s) not admitted (run admit_check.py)"
        if quarantine_ids:
            reason += f"; {len(quarantine_ids)} quarantined"
        state["status"] = "blocked_no_checks"
        state.setdefault("iterations", []).append(
            {"n": len(state.get("iterations", [])) + 1, "at": iso(now_utc()),
             "results": [], "feedback_to": None})
        return {"decision": DECISION_STOP_BLOCKED,
                "feedback": f"LOOP STOPPED — {reason}. The stop condition was NOT met.",
                "results": []}

    # Compute the fail-closed wall-clock deadline for this whole evaluation.
    # Budget = min(sum(timeout_s × runs over admitted checks) + 60, 540s cap);
    # FAIRMIND_GATE_DEADLINE_S can only tighten it (a test escape hatch). Once the
    # budget is spent, every unfinished check is ERROR — which can never be green,
    # so the gate never exits 0 on a deadline.
    natural = sum(
        int(c.get("exec", {}).get("timeout_s", 300))
        * max(1, int(c.get("determinism", {}).get("runs", 1)))
        for c in admitted
    ) + 60
    budget_s = min(natural, DEFAULT_DEADLINE_CAP_S)
    env_deadline = os.environ.get("FAIRMIND_GATE_DEADLINE_S")
    if env_deadline:
        try:
            budget_s = min(budget_s, float(env_deadline))
        except ValueError:
            pass
    # Monotonic: the deadline must measure REAL elapsed time, immune to a wall-clock
    # jump (NTP step, DST, a laptop waking from sleep) that would otherwise distort the
    # remaining budget and fail-OPEN the very cap this deadline exists to enforce.
    deadline = time.monotonic() + budget_s
    # WALL-CLOCK, beside the monotonic deadline and not instead of it. The
    # deadline measures elapsed time and must stay immune to a clock jump; this
    # is an INSTANT that has to be comparable with the timestamps in the
    # mutation trace, which are wall-clock ISO. It is returned on the not-green
    # branches (JC6) so a capture can ask whether a maker wrote DURING this run
    # — the one tree-movement the signature, the settle probe and the stability
    # re-read are all blind to, because all three read the tree after the last
    # check has already finished.
    checks_started_at = iso(now_utc())

    results = []
    deadline_hit = False
    for check in admitted:
        if deadline_hit or time.monotonic() >= deadline:
            deadline_hit = True
            results.append(_result(check, ERROR, None, "gate deadline exceeded", tier))
            continue
        try:
            if check.get("type") == "evidence" or check.get("kind") == "evidence":
                # Evidence artifacts (`verdict_file`) are written by an agent
                # (e.g. the QA Engineer) operating on state_root — a
                # worktree's own `.fairmind/` does not exist at all (H1/F34)
                # — so evidence resolution stays on `cwd` (state_root), never
                # `work_dir`. Only a check's own EXEC subprocess follows the
                # worktree; the artifact it reads about does not move.
                r = evaluate_evidence(check, cwd)
            else:
                r = evaluate_check(check, work_dir, tier, deadline=deadline)
        except Exception as exc:  # noqa: BLE001 — one bad check must not crash the gate
            r = _result(check, ERROR, None, f"evaluation crashed: {exc}", tier)
        results.append(r)
        # Once the deadline bites, skip remaining checks fast (no more subprocesses).
        if r.get("reason") == "gate deadline exceeded":
            deadline_hit = True

    for check in pending:
        results.append(_result(check, ERROR, None,
                               "check not admitted (run admit_check.py)", tier))

    # Append an iteration record. Capture the previous *evaluation's* verdicts
    # BEFORE appending, so the status board can show each check's transition.
    # `iterations[]` may also hold non-evaluation audit entries (extend_budget);
    # count and look past them by testing for a "results" key. Moved ahead of
    # the consecutive-failure accounting below (H3/F21+F33) — the no-work
    # signal that gates that accounting needs `prev_iter` to read the
    # immediately preceding results-bearing evaluation's mutation signature.
    state.setdefault("iterations", [])
    n = sum(1 for it in state["iterations"] if "results" in it) + 1
    prev_iter = next((it for it in reversed(state["iterations"]) if "results" in it), None)
    prev_verdicts = {x["id"]: x["verdict"] for x in prev_iter["results"]} if prev_iter else {}

    # H3/F21+F33: "did work happen since the immediately preceding
    # results-bearing evaluation?" Computed UNCONDITIONALLY (independent of
    # `contract.scope` — see `_no_work_signature`). `no_work` gates ONLY the
    # not-green branch's budget/consecutive-failures accounting below; it
    # never affects which checks ran, their verdicts, or the all-green
    # confirmation streak.
    #
    # Fail-closed default (`no_work = False`, i.e. "count this evaluation as
    # today") unless ALL of the following hold:
    #   1. a predecessor results-bearing iteration exists (`prev_iter is not
    #      None`) — AC1's "the FIRST evaluation always counts".
    #   2. THIS evaluation's signal is not degraded (`sig_degraded is None`)
    #      — AC4, an unanswerable "did work happen?" is never read as "no
    #      work".
    #   3. the PREDECESSOR iteration recorded a non-degraded signature
    #      (`prev_iter.get("mutation_signature") is not None`) — the same
    #      fail-closed rule applied to the other side of the comparison: a
    #      predecessor whose own signal was unknown (or predates H3, so the
    #      key is simply absent) can never prove "unchanged" either.
    # Only when both signatures are known and byte-identical is this a
    # genuine no-work re-evaluation.
    current_signature, sig_degraded = _no_work_signature(state, work_dir, cwd)
    prev_signature_known = prev_iter is not None and prev_iter.get("mutation_signature") is not None
    no_work = (
        prev_iter is not None
        and sig_degraded is None
        and prev_signature_known
        and current_signature == prev_iter.get("mutation_signature")
    )

    # H8 (PCF-8/PCF-5): is the tree still being WRITTEN? A background maker that
    # keeps writing across turn boundaries makes the Stop-hook gate judge a
    # half-written tree — the signature moved (so `no_work` above is False) yet
    # the fix attempt is not complete. `_settle_age` reads the trace for the most
    # recent work-product mutation; within the settle window this evaluation is
    # "in flight". Two distinct freezes result (they intentionally differ — see
    # `freeze_cf` vs `freeze_budget` below); the green branch checks `in_flight`
    # on its own to freeze the confirmation streak. Fail toward counting on any
    # unknown (`settle_age is None`), and skip the whole probe when the window is
    # disabled (<= 0).
    now = now_utc()
    settle_window = _settle_window_s()
    settle_age = _settle_age(work_dir, cwd, now) if settle_window > 0 else None
    # `0 <= settle_age` floors the signal: a future / forward-skewed trace ts
    # (negative age) is NOT read as in-flight — it fails toward counting rather
    # than freezing the loop on a bad clock or a tampered trace (H8-F4).
    in_flight = settle_age is not None and 0 <= settle_age < settle_window
    # Count the run of results-bearing iterations immediately preceding this one
    # that were already in-flight. A non-results audit entry (arm / extend_budget
    # / recover / hold / release — a human control action) BREAKS the run, so a
    # resumed loop gets a fresh grace window rather than inheriting an exhausted
    # one (H8-F-C); a settled (non-in-flight) eval breaks it too.
    trailing_in_flight = 0
    for it in reversed(state["iterations"]):
        if "results" not in it:
            break  # a human control action starts a fresh grace run (H8-F-C)
        if it.get("in_flight"):
            trailing_in_flight += 1
        else:
            break
    settle_grace_left = trailing_in_flight < _settle_max_consecutive()
    # The two freezes differ on purpose:
    #   • `freeze_cf` — an in-flight evaluation is NEVER a completed failed
    #     attempt, so it must never advance `consecutive_failures` (which would
    #     feed a misattributed STRATEGY TURN or `blocked_failures` against work
    #     that is merely still being written — H8-F-A). Frozen on ANY in-flight
    #     eval, grace or no grace.
    #   • `freeze_budget` — deferred while in-flight, but only up to
    #     SETTLE_MAX_CONSECUTIVE evals, after which it charges so `max_iterations`
    #     stays a backstop that does not depend on `timeout_min` (H8-F1). (This
    #     bounds only the IN-FLIGHT path; the H3 `no_work` path is uncapped by
    #     design — a byte-identical tree can never go green, so it can only waste
    #     compute, never false-close, and the wall clock / human bound it.)
    freeze_cf = no_work or in_flight
    freeze_budget = no_work or (in_flight and settle_grace_left)

    # Per-check consecutive-failure accounting (admitted checks only). A frozen
    # re-evaluation (H3 no-work, or H8 in-flight) freezes the RED/ERROR side of
    # this — the check's `consecutive_failures` stays exactly where it was,
    # because nothing has actually failed a second time (or the failure is not
    # yet a complete attempt); a still-GREEN check's reset to 0 is unaffected
    # either way (idempotent).
    result_by_id = {r["id"]: r for r in results}
    for check in admitted:
        r = result_by_id.get(check["id"])
        if r and r["verdict"] == GREEN:
            check["consecutive_failures"] = 0
        elif not freeze_cf:
            check["consecutive_failures"] = check.get("consecutive_failures", 0) + 1
        # else: frozen re-evaluation of a still-red/error check — cf frozen.

    # Provenance degradations — attach every weakened guarantee to the result so
    # the status board surfaces it: a Tier-A→B downgrade (sandbox absent) or a
    # baseline measured on a dirty tree. A green with a weaker provenance is still
    # a green, but the human must see how it was proven.
    checks_by_id = {c.get("id"): c for c in state.get("checks", [])}
    for r in results:
        degraded = []
        if hermeticity_downgraded:
            degraded.append("hermeticity-unverified")
        if baseline_dirty(checks_by_id.get(r["id"], {}).get("baseline")):
            degraded.append("baseline dirty-tree")
        r["degraded"] = degraded

    all_green = bool(results) and all(r["verdict"] == GREEN for r in results)

    budget = state.setdefault("budget", {})
    spent = budget.setdefault("spent", {})
    # Engine-owned accounting field (same class as `confirmations`, T11): every
    # loop bootstrap writes `started_at: null`, and `setdefault` does not
    # overwrite a *present* null, so the stamp never landed and the timeout
    # guard below ran permanently disarmed. The engine's own clock must own
    # this field: stamp it on whichever evaluation first finds it unresolvable
    # (absent, null, or garbage), then never touch it again — a value that
    # already parses (including one this same stamp wrote on a prior
    # evaluation) is left alone.
    if _parse_iso(spent.get("started_at")) is None:
        spent["started_at"] = iso(now_utc())

    # `n`, `prev_iter` and `prev_verdicts` were computed earlier (H3/F21+F33),
    # ahead of the consecutive-failure accounting above, so they are already
    # available here. `mutation_signature` (and, when degraded, `mutation_
    # signature_degraded`) is persisted on EVERY results-bearing iteration —
    # regardless of verdict — so the NEXT evaluation (whatever its own
    # verdict) has a predecessor signature to compare against.
    iteration = {
        "n": n,
        "at": iso(now_utc()),
        "results": [{"id": r["id"], "verdict": r["verdict"], "value": r["value"]} for r in results],
        "mutation_signature": current_signature,
    }
    if sig_degraded is not None:
        iteration["mutation_signature_degraded"] = sig_degraded
    # JC1/§B.1: WHICH TREE this evaluation actually judged. The verdicts above
    # are only interpretable against the commit they were measured on, and
    # nothing on disk recorded it — no loop-state file on this machine carries a
    # HEAD sha for any iteration. Read from `work_dir` (the worktree when the
    # state records a valid one), the same tree `compute_mutation_set` and every
    # check subprocess already run against. OMITTED, never null, when HEAD does
    # not resolve — see `_head_sha`.
    head_sha = _head_sha(work_dir)
    if head_sha:
        iteration["commit_sha"] = head_sha

    if all_green:
        k = confirmation_threshold(state)

        # A fresh loop's streak must start at 0 regardless of any persisted
        # (possibly seeded) value — n==1 means no prior results-bearing iteration
        # exists yet, so this is the very first evaluation. Done BEFORE the
        # hold/in-flight freeze below (H8-F3): otherwise a first evaluation that
        # is held or in-flight would consume the n==1 slot, and the next
        # (counting) evaluation — seeing n==2 — would build on a seeded streak
        # that was never reset, closing the loop on fewer than K genuine greens.
        if n == 1:
            state["confirmations"] = 0

        # H4/F24: a hold in force means a human-approved contract amendment is
        # in flight — this green evaluation must not be allowed to advance,
        # let alone close, the confirmation streak, because the very check(s)
        # it satisfies are what the amendment exists to replace. Checked
        # BEFORE the n==1 reset and BEFORE any increment below, so a held
        # loop's `confirmations` never moves at all (frozen, not merely
        # capped) — H4-AC1 evaluates a held gate 6 times, well past K, and
        # requires it stay at 0 (or whatever it already was) throughout. The
        # iteration record still carries `results` and `mutation_signature`
        # (H3 stays intact — a held evaluation IS a real evaluation) plus
        # `"held": True` so a human reading `iterations[]` can tell a held
        # green apart from an ordinary one. Without a hold this branch is a
        # no-op and behavior below is byte-identical to pre-H4.
        # Two independent reasons an all-green evaluation must NOT advance the
        # confirmation streak, frozen identically (H4-AC1: frozen, not merely
        # capped):
        #   • H4/F24 --hold: a human-approved amendment is in flight, so the
        #     checks this green satisfies are the ones the amendment replaces.
        #   • H8/PCF-8: the tree is still being written, so this "green" may be a
        #     read of a half-written tree whose RED-making test has not landed
        #     yet — a FALSE green, the one class a streak must never advance on.
        # The banner names whichever reason applies (hold wins when both hold,
        # being the human-driven one). Without either, this block is a no-op and
        # behavior below is byte-identical to pre-H8.
        hold = state.get("hold")
        if hold or in_flight:
            if hold:
                iteration["held"] = True
            if in_flight:
                iteration["in_flight"] = True
            feedback, owner = build_feedback(results, state, DECISION_ITERATE,
                                             prev_verdicts=prev_verdicts, iter_n=n)
            iteration["feedback_to"] = owner
            state["iterations"].append(iteration)
            if hold:
                reason_line = (
                    f"⏸ HOLD IN FORCE (since {hold.get('at', 'unknown')}) — a human-approved "
                    "contract amendment is in flight. This all-green evaluation does NOT advance "
                    f"the confirmation streak (frozen at {state.get('confirmations', 0)}/{k}), and "
                    "the loop can NEVER reach passed_pending_human while held. Release with "
                    "--release once the amendment lands — release also zeroes the streak, so a "
                    "streak earned against the superseded check does not carry over."
                )
            else:
                reason_line = (
                    f"⏳ TREE STILL SETTLING (H8) — a maker wrote work product {settle_age:.0f}s "
                    f"ago, within the {settle_window:.0f}s settle window. This all-green evaluation "
                    "is treated as in-flight and does NOT advance the confirmation streak (frozen "
                    f"at {state.get('confirmations', 0)}/{k}): a green read of a half-written tree "
                    "is a false green. Hold the orchestrator turn until the maker completes so the "
                    "gate evaluates a finished tree."
                )
            return {"decision": DECISION_ITERATE, "feedback": reason_line + "\n" + feedback,
                    "results": results}

        state["confirmations"] = state.get("confirmations", 0) + 1
        if state["confirmations"] >= k:
            completeness_blocker = _completeness_blocker(state, current_signature)
            if completeness_blocker:
                # The SECOND exit check has not been answered for THIS tree.
                # Freeze exactly like the `hold` branch above — streak held at K
                # (not reset: the checks really are green), no budget spent, no
                # status flip — and route the turn to the reviewer. The loop
                # stays `running` until a verdict lands, which is the whole
                # point: green-on-iteration-1 must not also mean done.
                state["confirmations"] = k
                iteration["feedback_to"] = _COMPLETENESS_OWNER
                state["iterations"].append(iteration)
                feedback, _ = build_feedback(results, state, DECISION_ITERATE,
                                             prev_verdicts=prev_verdicts, iter_n=n)
                return {"decision": DECISION_ITERATE,
                        "feedback": completeness_blocker + "\n" + feedback,
                        "results": results}
            state["status"] = "passed_pending_human"
            iteration["feedback_to"] = None
            _record_gate_green(state, iteration, n, work_dir,
                               _signature_members(current_signature))
            state["iterations"].append(iteration)
            feedback, _ = build_feedback(results, state, DECISION_STOP_PASSED,
                                         prev_verdicts=prev_verdicts, iter_n=n)
            return {"decision": DECISION_STOP_PASSED, "feedback": feedback, "results": results}
        # Confirmation turns do not consume max_iterations budget: a genuinely
        # green run must always be allowed to reach K without being starved.
        feedback, owner = build_feedback(results, state, DECISION_ITERATE,
                                         prev_verdicts=prev_verdicts, iter_n=n)
        iteration["feedback_to"] = owner
        state["iterations"].append(iteration)
        return {"decision": DECISION_ITERATE, "feedback": feedback, "results": results}

    # Not green: a red/error evaluation consumes budget — UNLESS this is a
    # budget-frozen re-evaluation (`freeze_budget`): either nobody did any work
    # since the immediately preceding evaluation (H3 no-work, the SAME tree
    # already charged for) or the change is still in progress and within grace
    # (H8 in-flight, a half-written tree). The verdicts still record below (the
    # status board still shows red); only the spend is frozen. Because
    # `consecutive_failures` is frozen on EVERY in-flight eval (`freeze_cf`,
    # above), `commitment_boundaries` cannot reach the cap-1 threshold from
    # in-flight work, so no STRATEGY TURN is ever misattributed to a tree that is
    # merely still being written (H8-F-A) — even once budget grace is exhausted.
    state["confirmations"] = 0
    if not freeze_budget:
        spent["iterations"] = spent.get("iterations", 0) + 1
    feedback, owner = build_feedback(results, state, DECISION_ITERATE,
                                     prev_verdicts=prev_verdicts, iter_n=n)

    # Commitment boundaries — computed BEFORE appending this iteration so history
    # is the prior evaluations; may prepend banners and re-route the feedback.
    banners, routing_override, strategy_ids = commitment_boundaries(results, state, in_flight=in_flight)
    banner_prefix = ("\n".join(banners) + "\n\n") if banners else ""
    if strategy_ids:
        iteration["strategy_turn"] = strategy_ids
    # H8: surface WHY a red/error evaluation was or was not charged, and mark the
    # iteration so a human reading `iterations[]` can tell an in-flight evaluation
    # from a genuine one. `iteration["in_flight"]` is stamped whenever the tree is
    # in-flight — INCLUDING a grace-exhausted evaluation that now counts — so the
    # trailing-in-flight run keeps growing and every subsequent evaluation keeps
    # charging (H8-F1) until the tree genuinely settles.
    if in_flight:
        iteration["in_flight"] = True
        if freeze_budget:
            settle_banner = (
                f"⏳ TREE STILL SETTLING (H8) — a maker wrote work product {settle_age:.0f}s ago, "
                f"within the {settle_window:.0f}s settle window. This evaluation is treated as "
                f"in-flight and is NOT charged: budget frozen at "
                f"{spent.get('iterations', 0)}/{budget.get('max_iterations', 8)}, no consecutive-"
                "failure counted, no strategy turn. The gate is reading a half-written tree — hold "
                "the orchestrator turn until the maker completes. The wall-clock timeout still applies."
            )
        else:
            settle_banner = (
                f"⏳ SETTLE GRACE EXHAUSTED (H8) — the tree has been in-flight for "
                f"{trailing_in_flight + 1} consecutive evaluations (cap {_settle_max_consecutive()}). "
                "The settle window only DEFERS a budget charge — it can never suspend the budget "
                f"forever — so this red evaluation now consumes one: budget "
                f"{spent.get('iterations', 0)}/{budget.get('max_iterations', 8)}. Consecutive-failure "
                "is still NOT counted (an in-flight tree is not a completed attempt, so no STRATEGY "
                "TURN). If the maker is genuinely still working, hold the orchestrator turn until it "
                "completes rather than ending the turn into the gate."
            )
        banner_prefix = settle_banner + "\n\n" + banner_prefix
    iteration["feedback_to"] = routing_override or owner
    state["iterations"].append(iteration)

    blocked = budget_exhausted(state)
    if blocked:
        state["status"] = blocked
        feedback, _ = build_feedback(results, state, DECISION_STOP_BLOCKED, blocked,
                                     prev_verdicts=prev_verdicts, iter_n=n)
        return {"decision": DECISION_STOP_BLOCKED,
                "feedback": banner_prefix + feedback, "results": results,
                "checks_started_at": checks_started_at}

    # `checks_started_at` rides ONLY the two not-green returns, and the asymmetry
    # is the point rather than an omission: it exists for the content capture,
    # which fires on a refused iteration and on nothing else. Both branches here
    # append one — STOP_BLOCKED is the red iteration that exhausted the budget —
    # so a capture keyed on the decision code alone would have missed the last
    # red verdict of every loop that ran out.
    return {"decision": DECISION_ITERATE,
            "feedback": banner_prefix + feedback, "results": results,
            "checks_started_at": checks_started_at}


_EXTEND_KEYS = {"iterations": "max_iterations",
                "failures": "max_consecutive_failures",
                "timeout_min": "timeout_min"}


def extend_budget(state, state_path, args):
    """Human-only verb: grant more budget to a *blocked* loop and resume it.

    Refused on any non-blocked status — a running loop still has budget, and the
    gate must never extend its own budget (that would defeat the point of a cap).
    There is no fake technical lock (in-band channel separation is impossible in
    Claude Code): the guard is procedural — the command asks the user first — plus
    this auditable record, `user_confirmed`, which the final human gate reviews.

    Resuming zeroes the confirmation streak AND every check's consecutive-failure
    counter, exactly as `--arm` zeroes the streak. Both are load-bearing: a
    resumed loop that kept a stale streak would close on fewer than K greens, and
    one that kept a check's failure count at the cap would re-block
    `blocked_failures` on its very next evaluation — spending the granted budget
    on nothing, since the failure guard reads that same counter.

    Honors `--dry-run`: prints what WOULD change to stderr and persists nothing.
    """
    status = state.get("status", "")
    if not status.startswith("blocked_"):
        print(f"--extend-budget refused: status is {status!r}, not a blocked_* state. Only a "
              "blocked loop can be extended (a running loop still has budget).", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # The procedural guard promised in the docstring: no non-empty --user-confirmed,
    # no extension. Checked before any mutation so a refusal leaves state untouched.
    if not (args.user_confirmed or "").strip():
        print("--extend-budget refused: --user-confirmed is required (must be a non-empty "
              "string) to extend a blocked loop's budget.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    budget = state.setdefault("budget", {})
    changes = {}
    for pair in args.extend_budget.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            print(f"--extend-budget: bad token {pair!r} (want key=value)", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        key, _, raw = pair.partition("=")
        key = key.strip()
        if key not in _EXTEND_KEYS:
            print(f"--extend-budget: unknown key {key!r} (allowed: {sorted(_EXTEND_KEYS)})",
                  file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        try:
            delta = int(raw)
        except ValueError:
            print(f"--extend-budget: {key} value must be an integer, got {raw!r}", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if delta <= 0:
            print(f"--extend-budget: {key} must be positive (grants more budget)", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        field = _EXTEND_KEYS[key]
        old = budget.get(field, 0) or 0
        budget[field] = old + delta
        changes[field] = {"from": old, "to": budget[field]}

    if not changes:
        print("--extend-budget: no changes parsed", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_status = status
    # Resume like --arm: zero the confirmation streak, and clear every check's
    # consecutive-failure counter so the granted budget is not immediately
    # re-consumed by a stale count already sitting at the cap (the failure guard
    # in budget_exhausted reads these counters on the very next evaluation).
    confirmations_reset_from = state.get("confirmations", 0)
    state["confirmations"] = 0
    failures_cleared = [c.get("id") for c in state.get("checks", [])
                        if c.get("consecutive_failures", 0)]
    for c in state.get("checks", []):
        c["consecutive_failures"] = 0
    state["status"] = "running"
    audit = {
        "event": "extend_budget",
        "at": iso(now_utc()),
        "prev_status": prev_status,
        "changes": changes,
        "confirmations_reset_from": confirmations_reset_from,
        "consecutive_failures_cleared": failures_cleared,
        "user_confirmed": args.user_confirmed,
    }
    summary = (f"{prev_status} → running; {changes}; confirmations "
               f"{confirmations_reset_from} → 0; consecutive_failures cleared for "
               f"{failures_cleared}; user_confirmed={args.user_confirmed!r}")

    if args.dry_run:
        print(f"[dry-run] extend_budget: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"extend_budget: {summary}", file=sys.stderr)
    return EXIT_ALLOW_STOP


# `iterations[]` entries `run_gate` itself appends OUTSIDE a results-bearing
# evaluation (`"results" in it`), and which — unlike an audit-verb entry
# (arm/extend_budget/recover/hold/release) — can ONLY exist because the loop
# was genuinely `status == "running"` at the moment they were appended (both
# come from `run_gate`, reached only through the non-dry-run `status !=
# "running"` early return in `main()`, which itself requires a prior `--arm`).
# Used by `arm()`'s fresh-vs-re-arm classification below (H4 defect #2) as the
# legacy fallback's ALLOWLIST — narrower than "iterations[] is non-empty".
_ENGINE_EVALUATION_EVENTS = ("scope_violation", "worktree_degraded")


def _iterations_prove_prior_arm(iterations):
    """True iff `iterations[]` contains an entry that could only have been
    appended while the loop was genuinely `running` — a results-bearing
    evaluation (including the empty-results `blocked_no_checks` entry), or
    one of `run_gate`'s own non-results audit events (`_ENGINE_EVALUATION_
    EVENTS`). Both require a prior successful `--arm` to have happened at
    all (status can only reach "running" through `arm()`).

    Deliberately NOT satisfied by a bare verb-audit entry (`arm`,
    `extend_budget`, `recover`, `hold`, `release`) or by any unrecognized
    event — those prove nothing about whether the loop was EVER armed
    on their own. This is the H4 defect #2 hardening: before this, ANY
    non-empty `iterations[]` (`bool(state.get("iterations"))`) was read as
    proof of a prior arm, which a pre-arm `--hold`/`--release` audit entry
    (H4 defect #1, now closed by `hold_verb`/`release_verb`'s own status
    guard) — or any OTHER future writer that appends to `iterations[]`
    without going through a real evaluation — could poison into skipping
    the arm-time baseline freeze on a truly fresh arm."""
    for it in iterations or []:
        if "results" in it:
            return True
        if it.get("event") in _ENGINE_EVALUATION_EVENTS:
            return True
    return False


def arm(state, state_path, cwd, args):
    """Engine verb (T19): the ONLY place that flips a loop into `running`. Owns
    `budget.spent.started_at`, `budget.spent.first_armed_at`, `confirmations`,
    and the arm-time `contract.mutation_set` baseline, so no orchestrator ever
    hand-writes an accounting field again — see loop-state.json contract.arming
    (decisions C1-C10) for the full ruling this implements.

    Refuses (state untouched — checked before any mutation, same idiom as
    `extend_budget`) in these cases:
      - the loop is already `running` (C4/AC2) — arming it again would silently
        re-stamp a live loop's start instant and reset its confirmation streak;
      - there is no admitted check to gate on (C3/AC1), using the engine's own
        `admitted_checks` predicate — a loop with nothing admitted could never
        legitimately close;
      - a `validate_contract` coverage failure (T10/AC5) — a hard criterion is
        not wired to any admitted check;
      - (H7/F6) an admitted `kind:"guard"` check is NOT green at arm time — its
        guarded artifact has already regressed, so arming would only flip to
        `running` and burn the whole budget blocking on a guard doomed at t=0.
        This is the ONE place arm evaluates a check's live value, and it does so
        for GUARDS ONLY (a red-first machine check is RED at arm by construction).

    Every other status (`specified`, every `blocked_*` including
    `blocked_recovered`, or an absent/unknown status) is armable — this is the
    re-arm path after a human gate rejection or a `--recover`.

    Fresh-vs-re-arm turns on whether the loop was EVER armed — a durable fact
    (`budget.spent.first_armed_at`, plus `_iterations_prove_prior_arm` as the
    fallback for a loop armed before that marker existed). It is NEVER
    inferred from bare "has a results-bearing iteration", nor (H4 defect #2)
    from bare "iterations[] is non-empty": a `blocked_scope` loop whose only
    history is a `{"event":"scope_violation"}` entry HAS run and HAS been
    armed (W1.7a) and correctly counts, but a loop whose only history is a
    verb-only audit entry (`arm`/`extend_budget`/`recover`/`hold`/`release`,
    or any other unrecognized event landing in `iterations[]` before the
    first arm) has NOT — `_iterations_prove_prior_arm` allowlists only the
    entries `run_gate` itself can append (which require a prior `running`
    status to exist at all), so a stray pre-arm audit entry can never again
    misread a truly fresh arm as a re-arm and skip the baseline freeze below.

    On success: sets `status = "running"`, resets `confirmations` to 0, and:
      - FRESH arm — stamps `started_at` and `first_armed_at` from the engine's
        own clock (overwriting any pre-seeded value), and freezes the arm-time
        `contract.mutation_set` baseline: the `HEAD` sha to diff "changed since
        arm" FROM, plus each already-dirty path anchored to its exact arm-time
        bytes (`pre_dirty_anchors`), so a file dirty at arm cannot later be
        rewritten out of scope for free.
      - RE-ARM (already armed once) — PRESERVES `started_at`, `first_armed_at`,
        budget spend and the frozen baseline (C5: a re-stamp would truncate the
        whole-run window and mint a second ledger loop_id; re-freezing the
        baseline to the post-mutation `HEAD` would erase every mutation committed
        before the re-arm from the set), only stamping `started_at` if it is
        itself unresolvable (backstop for a hand-armed/legacy loop).
    Stamps the arming session as `owner_session` — `--session-id`, else the
    host's `CLAUDE_CODE_SESSION_ID` — right here instead of leaving ownership to
    whichever session's Stop hook fires first, so a second,
    unrelated session in the checkout cannot claim a loop it was never armed
    for; a re-arm from a new session overwrites the stale id. With neither id,
    clears it (C7 — a stale id would strand a re-armed loop behind the
    foreign-session no-op branch). And appends one `{"event": "arm", ...}` audit entry to
    `iterations[]` with no `results` key (C8), so every consumer that counts
    evaluations by testing `"results" in it` stays blind.

    Honors `--dry-run`: prints what WOULD change to stderr and persists nothing.

    Never invoked by the gate itself: `--arm` is a human/orchestrator verb,
    exactly like `--extend-budget`. Arming does not RUN the gate; its one
    deliberate evaluation is the H7/F6 guard-only pre-check above, which
    evaluates admitted `kind:"guard"` checks solely to REFUSE an already-broken
    loop up front — it never confirms, mutates, or persists.
    """
    status = state.get("status")

    # C4: the ONE status --arm refuses is 'running' — no silent re-stamp of
    # started_at, no silent streak reset, no audit entry under a live gate.
    if status == "running":
        print("--arm refused: the loop is already 'running'. Arming a live loop "
              "would silently re-stamp its start instant and reset its "
              "confirmation streak — nothing to do.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # C3/AC1: reuse the engine's own admitted-check predicate verbatim — a
    # second, drift-prone definition of "admitted" is a bug waiting to happen.
    admitted, pending = admitted_checks(state)
    if not admitted:
        quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}
        reason = "no admitted check to gate on"
        if pending:
            reason += f"; {len(pending)} check(s) not admitted (run admit_check.py)"
        if quarantine_ids:
            reason += f"; {len(quarantine_ids)} quarantined"
        print(f"--arm refused: {reason}.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # (3) T10/AC5/R1: contract validation — the guarantee cannot be bypassed by an
    # orchestrator that forgets to run --validate-contract. Placed LAST so the more
    # precise C4/C3 diagnostics above still fire first (a nothing-admitted loop is
    # told to run admit_check.py, not handed a list of criteria uncovered *because*
    # nothing is admitted). Refused before ANY mutation — same idiom as C3/C4 — so
    # loop-state.json is byte-untouched, status is not flipped, started_at is not
    # stamped, and no arm audit entry is appended. Honors --dry-run (nothing is
    # persisted on this path either, because we return before the dry-run block).
    errors = validate_contract(state)
    if errors:
        _emit_contract_refusal(errors, "--arm")
        return EXIT_INTERNAL_ERROR

    # (3b) The design brief must exist before the loop can arm — see
    # `validate_design_brief` for why this is a gate and what it cannot see.
    # Placed with the other PURE-STATIC refusals, after coverage so the more
    # precise diagnostic still fires first, and before ANY mutation: loop-state
    # stays byte-untouched, status is not flipped, no audit entry is appended.
    brief_error = validate_design_brief(state, state_path)
    if brief_error:
        print(f"--arm refused: {brief_error}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # (4) H7/F6: arm-time GUARD gate. A `kind:"guard"` check is proven GREEN exactly
    # ONCE, at admission (admit_guard's green_at_spec); its admitted_hash freezes the
    # DESCRIPTOR, not the world it guards. So a loop whose guarded artifact has since
    # regressed — guard admitted, descriptor untouched (hash still valid) — would
    # otherwise sail through --arm, flip to 'running', and burn its whole budget
    # blocking on a guard that was doomed at t=0. Here, and ONLY here, arm deliberately
    # crosses the "arm never evaluates" line: it evaluates the LIVE value of every
    # admitted GUARD (via the engine's own evaluate_check) and REFUSES if any is not
    # GREEN.
    #
    # GUARD-SCOPED, on purpose: a red-first MACHINE/functional/metric check is RED at
    # arm BY CONSTRUCTION, so evaluating those here would refuse every legitimate loop
    # — hence the `kind == "guard"` filter and NOT `admitted` wholesale. This step is
    # SEPARATE from validate_contract, which stays PURE-STATIC (no evaluation folded
    # in). FAIL-CLOSED: RED *or* ERROR (crash / missing signal / non-determinism) is
    # not a pass. Refused BEFORE any mutation — the same untouched-state idiom as the
    # C3/C4/contract refusals above (loop-state.json byte-unchanged, status not
    # flipped, started_at not stamped, no arm audit entry); this path returns before
    # the dry-run persist block, so --dry-run writes nothing either way. Both the TREE
    # (work_dir) and the TIER are resolved exactly as run_gate does: work_dir via
    # resolve_work_dir, so the guards evaluate against the SAME tree the running gate
    # will (H1/F34 — the recorded worktree when valid, else state_root), failing
    # closed on an untrustworthy worktree rather than silently falling back to
    # state_root; the tier by requested tier degrading A→B when `srt` is absent but
    # WITHOUT persisting the downgrade (a refusal must leave the bytes intact); a
    # bounded monotonic deadline over just the guards keeps arm from hanging on a
    # runaway guard command.
    guards = [c for c in admitted if c.get("kind") == "guard"]
    if guards:
        # H1/F34: evaluate the guards against the SAME tree run_gate uses (work_dir),
        # resolved by the very helper run_gate calls. A recorded worktree.path that
        # cannot be PROVEN to be a real, registered worktree of this repo must FAIL
        # CLOSED here — refuse to arm, naming the condition — never silently evaluate
        # state_root instead: that silent fallback is exactly F34 wearing a different
        # hat (arm would accept a loop whose guard is green on the main tree but red
        # in the worktree the gate actually runs against). Same byte-untouched refusal
        # idiom as the C3/C4/contract refusals above — resolve_work_dir reads state,
        # it never mutates it, so loop-state.json is left byte-for-byte intact.
        work_dir, wt_degradation = resolve_work_dir(state, cwd)
        if wt_degradation is not None:
            reason, detail = wt_degradation
            print(
                "--arm refused: this loop records a worktree.path that could not be "
                f"resolved to a real, registered worktree of this repo ({reason}: "
                f"{detail}). Failing closed rather than silently evaluating the main "
                "tree — a silent fallback there would be the exact defect (F34) this "
                "guard exists to prevent, since the running gate evaluates the "
                "worktree. A human must resolve the worktree (re-create it via "
                "loop_worktree.py --create, or clear state['worktree']) before this "
                "loop can be armed.",
                file=sys.stderr)
            return EXIT_INTERNAL_ERROR

        guard_tier = state.get("hermeticity_tier", "B")
        if guard_tier == "A" and not srt_available():
            guard_tier = "B"  # sandbox absent at arm time — degrade locally, don't persist
        natural = sum(
            int(c.get("exec", {}).get("timeout_s", 300))
            * max(1, int(c.get("determinism", {}).get("runs", 1)))
            for c in guards
        ) + 60
        budget_s = min(natural, DEFAULT_DEADLINE_CAP_S)
        env_deadline = os.environ.get("FAIRMIND_GATE_DEADLINE_S")
        if env_deadline:
            try:
                budget_s = min(budget_s, float(env_deadline))
            except ValueError:
                pass
        guard_deadline = time.monotonic() + budget_s

        not_green = []
        for c in guards:
            try:
                r = evaluate_check(c, work_dir, guard_tier, deadline=guard_deadline)
                verdict, reason = r.get("verdict"), r.get("reason")
            except Exception as exc:  # noqa: BLE001 — fail closed: a crash is not a pass
                verdict, reason = ERROR, f"evaluation crashed: {exc}"
            if verdict != GREEN:
                not_green.append((c.get("id"), verdict, reason))

        if not_green:
            detail = "; ".join(f"{cid!r} → {verdict} ({reason})"
                               for cid, verdict, reason in not_green)
            print(
                f"--arm refused: {len(not_green)} admitted guard(s) NOT green at arm "
                f"time — the guarded behaviour is already broken, so arming would only "
                f"burn the whole budget blocking on a guard doomed at t=0: {detail}. "
                "Fix the guarded artifact (or re-author the guard via admit_check.py), "
                "then re-arm.",
                file=sys.stderr)
            return EXIT_INTERNAL_ERROR

    prev_status = status
    confirmations_reset_from = state.get("confirmations", 0)

    budget = state.setdefault("budget", {})
    spent = budget.setdefault("spent", {})

    # W1.7a + H4 defect #2: fresh-vs-re-arm on a DURABLE "ever armed" fact,
    # never the results proxy `any("results" in it ...)` (would misread a
    # blocked_scope loop whose only iteration entry is a
    # {"event":"scope_violation"} as fresh) and never bare "iterations[] is
    # non-empty" (would misread a pre-arm verb-only audit entry — e.g. a
    # {"event":"hold"/"release"} that slipped in before hold_verb/release_verb
    # grew their own status guard, or any other future pre-arm audit writer —
    # as proof of a prior arm). `first_armed_at` is stamped once at the first
    # arm and never cleared; `_iterations_prove_prior_arm` is the legacy
    # fallback for a loop armed before that marker existed, narrowed to the
    # entries `run_gate` itself can append (which require a prior `running`
    # status to exist at all — see that helper's docstring).
    ever_armed = bool(spent.get("first_armed_at")) or _iterations_prove_prior_arm(
        state.get("iterations"))
    fresh = not ever_armed

    # C5: a FRESH arm stamps started_at UNCONDITIONALLY from the engine's own
    # clock, overwriting any pre-seeded value on disk. A RE-ARM PRESERVES the
    # existing resolvable started_at, only stamping if it is itself unresolvable
    # (backstop for a hand-armed/legacy loop).
    if fresh or _parse_iso(spent.get("started_at")) is None:
        spent["started_at"] = iso(now_utc())
    started_at = spent["started_at"]
    # Durable ever-armed marker: stamped once (to the value now on disk), never
    # overwritten — a re-arm leaves the original first-arm instant intact.
    if not spent.get("first_armed_at"):
        spent["first_armed_at"] = started_at

    # W1.2-arm: freeze the arm-time mutation-set baseline on a FRESH arm inside a
    # git tree — the frozen HEAD sha the scope boundary diffs "changed since arm"
    # FROM, and each already-dirty path anchored to its exact arm-time bytes so a
    # file dirty at arm cannot be rewritten out of scope for free (pre_existing
    # is byte-identity, not path membership). A re-arm PRESERVES the original
    # baseline: re-freezing to the post-mutation HEAD would erase every mutation
    # this run already committed from the set. Outside a git tree the baseline is
    # left as-is and the scope boundary fails closed (no-baseline-ref) rather
    # than silently under-blocking.
    #
    # Freeze the baseline from the SAME tree resolve_work_dir
    # names — the recorded worktree when it resolves to a real, registered one,
    # else state_root — not from `cwd` unconditionally. `run_gate`'s own
    # mutation-set query at evaluation time runs `compute_mutation_set(work_dir,
    # arm_ref)`, and arm's own H1/F34 guard pre-check above already evaluates
    # guards against this same resolved tree "so the guards evaluate against
    # the SAME tree the running gate will" — the baseline freeze must follow
    # the identical rule, or the ref the whole run's scope boundary diffs
    # "changed since arm" FROM is anchored to a tree nothing else in the run
    # ever uses.
    #
    # Freezing on cwd would rest on "a worktree is created from HEAD, so at
    # `--create` time the two HEADs coincide". That is not the contract `loop_worktree.py --create` actually
    # implements (`worktree add <path> <branch>` reuses an EXISTING branch
    # as-is when one is passed — no `HEAD` argument, no coincidence to rely
    # on), and even for a worktree freshly cut from HEAD, state_root's OWN
    # HEAD can move between `--create` and `--arm` (a pull/rebase on the main
    # branch, independent of the worktree's branch). Either way, freezing on
    # `cwd` bakes in a ref the worktree's own commits will never reproduce:
    # `git diff <that ref>` run against the worktree at evaluation time then
    # reports every path that differs between the WRONG ref and the
    # worktree's real history — including commits the maker never made — and
    # a hard stop on one of those reads as an out-of-scope mutation nobody in
    # this loop committed.
    #
    # A recorded `worktree.path` that resolve_work_dir cannot trust (the
    # H1/F34 degradation shapes) is treated exactly like "outside a git tree"
    # already was: the baseline is left unfrozen and the scope boundary fails
    # closed on `no-baseline-ref` at evaluation time, rather than freezing
    # against a tree that was never proven to be the one the gate will use.
    if fresh:
        baseline_work_dir, baseline_wt_degradation = resolve_work_dir(state, cwd)
        if baseline_wt_degradation is None and _is_git_work_tree(baseline_work_dir):
            head_sha = _head_sha(baseline_work_dir)   # same query, same fail-soft contract, one spelling
            if head_sha:
                arm_set = compute_mutation_set(baseline_work_dir, head_sha)
                if not arm_set.get("degraded"):
                    baseline = (state.setdefault("contract", {})
                                     .setdefault("mutation_set", {})
                                     .setdefault("baseline", {}))
                    baseline["recorded_at_arm"] = True
                    baseline["ref"] = head_sha
                    baseline["pre_dirty"] = pre_dirty_anchors(
                        baseline_work_dir, [p["path"] for p in arm_set["paths"]])

    # §B.9: a re-arm INVALIDATES the round it abandoned. `lifecycle.gate_green`
    # and `lifecycle.post_ceremony` describe a green and a ceremony that this arm
    # is discarding; leaving them would let the divergence detector compare THIS
    # round's signature against the ABANDONED round's and report `diverged` with
    # complete confidence — on exactly the population (19 real re-arms after a
    # green gate, across 37 loops on this machine, measured 2026-08-14) the
    # human-gate record exists to measure. `lifecycle.human_gate` is preserved:
    # the rejection history is the signal, not residue. Run on EVERY arm, fresh
    # and re-arm alike — a fresh arm has nothing to clear, so one unconditional
    # reset is simpler than a branch that can be wrong. `state["consent"]` is at
    # the state root, NOT under `lifecycle`, and is deliberately out of reach of
    # this clear (see the intersection below).
    lifecycle = state.get(_LIFECYCLE_KEY)
    if isinstance(lifecycle, dict):
        preserved = {k: v for k, v in lifecycle.items()
                     if k in _LIFECYCLE_PRESERVED_ON_REARM}
        if preserved:
            state[_LIFECYCLE_KEY] = preserved
        else:
            state.pop(_LIFECYCLE_KEY, None)

    # JC5/§B.7: freeze the consent grant AT COLLECTION — resolved here, at the
    # instant the window opens, and never re-read at flush, so a config edited
    # between arm and flush cannot retro-relabel data already collected.
    # (Revocation stays live and is applied at the flush caller, which is a
    # different question: what may still be SENT.) On a re-arm the stamp is the
    # INTERSECTION of what is already recorded and what resolves now — see
    # `_consent_stamp` for why last-write-wins is wrong in both directions.
    consent = _consent_stamp(state.get("consent"), _resolve_consent_grant(cwd), ever_armed)
    if consent is not None:
        state["consent"] = consent

    state["status"] = "running"
    state["confirmations"] = 0  # C6: every arm resets the streak, fresh or re-arm
    # The arming session becomes the owner, named by
    # --session-id or, when that is absent, by CLAUDE_CODE_SESSION_ID — the host
    # sets it in every Bash subprocess, equal to the `session_id` its Stop hook
    # payload carries, which is how `/fairmind-loop`'s bare `--arm` identifies
    # itself. Stamping here, instead of leaving ownership to whichever session's
    # Stop hook fires first, keeps an unrelated session in the same checkout
    # from claiming a loop it was never armed for. `.strip()` only, exactly as
    # the plain Stop path normalises its id: any other transform on one side
    # would make every Stop read as foreign and the gate would never fire.
    # With neither id, clear owner_session so the next session's Stop can claim
    # it; a stale id left behind would strand the loop behind the
    # foreign-session no-op branch.
    sid = ((args.session_id or "").strip()
           or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip())
    if sid:
        state["owner_session"] = sid
        owner = f"owner_session={sid}"
    else:
        state.pop("owner_session", None)
        owner = "owner_session cleared (the next session's Stop claims it)"

    # C8: one uniform audit entry on every successful arm (fresh and re-arm) —
    # no "n" key, no "results" key, so every consumer that counts evaluations by
    # testing "results" in it stays blind to it, exactly like extend_budget.
    entry = {
        "event": "arm",
        "at": iso(now_utc()),
        "prev_status": prev_status,
        "confirmations_reset_from": confirmations_reset_from,
        "started_at": started_at,
    }
    if (args.user_confirmed or "").strip():
        entry["user_confirmed"] = args.user_confirmed

    # C6: arming grants no budget — warn (never block) when re-arming a loop
    # that is already at/over its iteration cap, so the human sees it will
    # legitimately re-block on the next non-green evaluation unless they also
    # run --extend-budget.
    warn = ""
    max_iterations = budget.get("max_iterations", 8)
    if spent.get("iterations", 0) >= max_iterations:
        warn = (f" WARNING: spent.iterations ({spent.get('iterations', 0)}) already >= "
                f"max_iterations ({max_iterations}) — this loop will re-block on its next "
                "non-green evaluation unless the budget is also extended via --extend-budget.")

    if args.dry_run:
        print(f"[dry-run] arm: WOULD set {prev_status!r} → running; started_at={started_at}; "
              f"confirmations {confirmations_reset_from} → 0; {owner}.{warn} NOTHING persisted.",
              file=sys.stderr)
        return EXIT_ALLOW_STOP

    state.setdefault("iterations", []).append(entry)
    save_state(state_path, state)
    print(f"arm: {prev_status!r} → running; started_at={started_at}; "
          f"confirmations {confirmations_reset_from} → 0; {owner}.{warn}", file=sys.stderr)
    return EXIT_ALLOW_STOP


def recover(state, state_path, args):
    """Human-only recovery verb (W2.1): free a loop wedged in `running` whose
    owning session is gone. No other verb reaches that state — `--arm` refuses a
    running loop (C4), `--extend-budget` refuses a non-blocked one, a fresh
    session's Stop hook silently no-ops on the ownership mismatch, and
    `timeout_min` is only ever re-evaluated by an actual evaluation that, with no
    live session driving stops, never comes. So a crashed/closed session leaves
    its loop pinned `running` forever with no engine path out.

    Gated ONLY on an explicit, audited human confirmation (`--user-confirmed`) —
    NEVER on a session-id mismatch. A mismatch is exactly what a *second*
    concurrent session trips while the first is still legitimately driving the
    loop; recovering on that signal would let the second session silently steal a
    live loop. Only a human asserting "the session is gone" may force the release.

    Transitions `running` → `blocked_recovered` (a `blocked_*` status: `--arm`
    re-arms it, `--extend-budget` extends it, and the Stop hook allows the
    wedged turn to end) and clears `owner_session` so the next session can claim
    the re-armed loop. Refused (state untouched) on any non-`running` status — a
    non-running loop is already recoverable via `--arm`/`--extend-budget`.

    Honors `--dry-run`: prints what WOULD change to stderr and persists nothing.
    """
    status = state.get("status")
    if status != "running":
        print(f"--recover refused: status is {status!r}, not 'running'. Recovery only "
              "applies to a loop wedged in 'running' with no live session; a non-running "
              "loop is already recoverable via --arm (re-arm) or --extend-budget.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Human-only, checked before any mutation so a refusal leaves state untouched.
    if not (args.user_confirmed or "").strip():
        print("--recover refused: --user-confirmed is required (a non-empty human reason). "
              "Recovery force-frees a running loop, so it is gated on an explicit human "
              "confirmation, never on a session-id mismatch — a second concurrent session "
              "must never silently steal a loop the first is still driving.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_owner = state.get("owner_session")
    audit = {
        "event": "recover",
        "at": iso(now_utc()),
        "prev_status": "running",
        "prev_owner_session": prev_owner,
        "user_confirmed": args.user_confirmed,
    }
    summary = (f"running → blocked_recovered; owner_session {prev_owner!r} → cleared; "
               f"user_confirmed={args.user_confirmed!r}")

    if args.dry_run:
        print(f"[dry-run] recover: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state["status"] = "blocked_recovered"
    state.pop("owner_session", None)
    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"recover: {summary}. Re-arm with --arm (or grant budget with --extend-budget) "
          "to resume.", file=sys.stderr)
    return EXIT_ALLOW_STOP


def hold_verb(state, state_path, args):
    """Human-only verb (H4/F24): suspend confirmation counting while a
    human-approved contract amendment is in flight, so a green loop cannot
    close on the very check the amendment exists to replace (F24). Sets
    `state["hold"] = {"at": <iso>[, "user_confirmed": <str>]}` — the marker
    `run_gate`'s all-green branch reads (see there) to freeze the
    confirmation streak and force ITERATE regardless of how green the
    checks are, for as long as the marker stays present.

    Refuses (state untouched, checked before any mutation) on any status
    OTHER than `running` (H4 adversarial-pass amendment). A hold suspends
    CONFIRMATION COUNTING, which is only a meaningful concept on a live,
    running loop — a pre-arm hold used to be accepted silently (the original
    "conservative by construction, needs no status guard" design) and that
    is exactly what broke `arm()`'s fresh-vs-re-arm classification: a hold
    audit entry landing in `iterations[]` BEFORE the first `--arm` made a
    truly fresh arm look like a re-arm and skip the baseline freeze (see
    `arm()`'s `ever_armed`, and `test_h4_hold.py`'s
    `test_hold_and_release_refused_when_not_running`). "Conservative" now
    means "can only prevent a close on a loop that could otherwise close" —
    which presupposes the loop is running; it does not mean "accepted in any
    state". A hold on a genuinely `running` loop still works exactly as
    before.

    No mandatory `--user-confirmed` — recorded on the audit entry when
    supplied, but not required. Idempotent: holding an already-held loop
    simply overwrites the marker with a fresh timestamp.

    Appends one `{"event": "hold", ...}` audit entry to `iterations[]` with
    NO "results" key — the same shape as `arm`/`extend_budget`/`recover` —
    so evaluation numbering and the confirmation streak stay blind to it
    (H4-AC3).

    Honors `--dry-run`: prints what WOULD change to stderr and persists
    nothing.
    """
    status = state.get("status")
    if status != "running":
        print(f"--hold refused: status is {status!r}, not 'running'. A hold suspends "
              "confirmation counting, which is only meaningful on a live, running loop — "
              "a pre-arm hold is exactly what poisons --arm's fresh-vs-re-arm "
              "classification (see arm()'s ever_armed). Arm the loop first (--arm), "
              "then --hold.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_hold = state.get("hold")
    at = iso(now_utc())
    hold_marker = {"at": at}
    confirmed = (args.user_confirmed or "").strip()
    if confirmed:
        hold_marker["user_confirmed"] = confirmed
    audit = {"event": "hold", "at": at, "prev_hold": prev_hold}
    if confirmed:
        audit["user_confirmed"] = confirmed
    summary = (f"hold set (at={at}); confirmations frozen at "
               f"{state.get('confirmations', 0)} until --release")

    if args.dry_run:
        print(f"[dry-run] hold: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state["hold"] = hold_marker
    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"hold: {summary}", file=sys.stderr)
    return EXIT_ALLOW_STOP


def release_verb(state, state_path, args):
    """Human-only verb (H4/F24): end a hold set by `--hold` and resume
    confirmation counting FROM 0 (H4-AC2) — a streak earned against the
    superseded check the amendment just replaced must not carry over once
    the amendment lands. Clears `state["hold"]` and zeroes
    `state["confirmations"]` UNCONDITIONALLY, even if no hold was in force:
    a release with nothing to release is harmless (there is nothing to
    un-freeze, and zeroing an already-zero streak changes nothing
    observable), and keeping the verb unconditional avoids a second,
    drifting definition of "is a hold active" from creeping into this
    function alone.

    Refuses (state untouched, checked before any mutation) on any status
    OTHER than `running` (H4 adversarial-pass amendment, mirroring
    `hold_verb`'s guard) — a release only makes sense undoing a hold that
    could only ever have been placed on a running loop now that `--hold`
    itself refuses pre-arm. Keeping the two verbs' status guards symmetric
    also closes the same `arm()`-poisoning path from the release side (a
    release-only audit entry landing in `iterations[]` before the first
    `--arm` would trip the identical fresh-vs-re-arm misread).

    Appends one `{"event": "release", ...}` audit entry to `iterations[]`
    with NO "results" key — the same shape as `hold`/`arm`/`extend_budget`
    — so evaluation numbering and the confirmation streak stay blind to it
    (H4-AC3).

    Honors `--dry-run`: prints what WOULD change to stderr and persists
    nothing.
    """
    status = state.get("status")
    if status != "running":
        print(f"--release refused: status is {status!r}, not 'running'. A release only "
              "makes sense on a loop that --hold could have suspended, which itself now "
              "requires 'running'. Arm the loop first (--arm) if it needs one.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_hold = state.get("hold")
    confirmations_reset_from = state.get("confirmations", 0)
    confirmed = (args.user_confirmed or "").strip()
    audit = {
        "event": "release",
        "at": iso(now_utc()),
        "prev_hold": prev_hold,
        "confirmations_reset_from": confirmations_reset_from,
    }
    if confirmed:
        audit["user_confirmed"] = confirmed
    summary = (f"hold cleared (was {prev_hold!r}); confirmations "
               f"{confirmations_reset_from} → 0, resuming the streak from scratch")

    if args.dry_run:
        print(f"[dry-run] release: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state.pop("hold", None)
    state["confirmations"] = 0
    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"release: {summary}", file=sys.stderr)
    return EXIT_ALLOW_STOP


RECORD_TRANSITIONS = ("post_ceremony",)

_PASSED = "passed_pending_human"


def _refuse_unless_passed(state, verb):
    """Both lifecycle verbs describe something that happens AFTER the gate goes
    green, so both refuse on any other status — state byte-untouched, the same
    check-before-any-mutation idiom `--arm` / `--hold` / `--release` use."""
    status = state.get("status")
    if status == _PASSED:
        return None
    print(f"{verb} refused: status is {status!r}, not {_PASSED!r}. This verb records "
          "something that happens after the gate goes green; before that there is "
          "nothing for it to describe.", file=sys.stderr)
    return EXIT_INTERNAL_ERROR


def record_transition(state, state_path, cwd, args):
    """Human/orchestrator verb (§B.3): record the post-ceremony tree.

    The pre-PR ceremony (`/simplify`, a cross-model review, the fixes that
    survive triage) MUTATES the tree after the gate went green, so the tree that
    merges is not necessarily the tree the gate labelled. This verb records the
    second one, and a later comparison of the two signatures is what makes that
    difference measurable instead of assumed.

    Stores the RAW signature in the gate's own `[[path, sha], ...]` shape and
    derives nothing from it: the digest, the divergence boolean and the change
    count are all computed in the payload builder, so there is exactly one
    derivation site and one storage shape. Loop-state is local and gitignored,
    where a few kB of signature is free.

    Last write wins — the ceremony legitimately re-runs after review findings
    are applied, and the tree that ships is the last one.

    ⚠️ WHY A VERB AND NOT `--dry-run`. `--dry-run` persists NOTHING, and that is
    load-bearing: the ceremony re-evaluates the checks without mutating loop
    state, which is what makes the diff a human approves the diff that merges.
    Making it write would destroy the property the ceremony exists to have.

    Writes nothing else — no status change, no `iterations[]` entry, no
    confirmation. Honors `--dry-run`."""
    name = args.record_transition
    refusal = _refuse_unless_passed(state, f"--record-transition {name}")
    if refusal is not None:
        return refusal

    # Same fail-closed worktree rule as `--arm` and `run_gate`: a recorded
    # worktree.path that cannot be PROVEN to be a real registered worktree must
    # never be silently swapped for the main tree, or this verb would record a
    # signature of a tree the gate never looked at.
    work_dir, wt_degradation = resolve_work_dir(state, cwd)
    if wt_degradation is not None:
        reason, detail = wt_degradation
        print(f"--record-transition {name} refused: this loop records a worktree.path that "
              f"could not be resolved to a real, registered worktree of this repo "
              f"({reason}: {detail}). Failing closed rather than recording the main tree's "
              "signature as if it were the worktree's.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # §B.5: the two human-driven verbs are the one place `collect_run_meta` CAN
    # be reused — it reads the wall clock and RAISES outside a git tree, which
    # the gate path must never do but a verb a human just typed can degrade from
    # cleanly. Guarded import for the same reason `loop_ledger` is: recording a
    # transition must never be able to crash on a missing sibling module.
    #
    # Asked of `work_dir`, not of `cwd`: the sha recorded here is compared
    # against the one `run_gate` recorded at the flip, which is `work_dir`'s
    # (`_head_sha(work_dir)`), and the signature below is `work_dir`'s too. On a
    # worktree loop the maker's commits advance the worktree branch while
    # state_root's HEAD does not move at all, so reading state_root here would
    # put a sha from a different branch beside a signature from this one — and
    # the whole point of the pair is that they describe the same tree.
    run_meta = {}
    try:
        import audit_run_meta
        run_meta = audit_run_meta.collect_run_meta(work_dir)
    except Exception:  # noqa: BLE001 — degrade to the engine's own clock/sha rules
        run_meta = {}

    row = {"at": run_meta.get("executed_at") or iso(now_utc())}
    # ⚠️ THE TWO `or`s HAVE DIFFERENT TRIGGERS, and the sha one is NOT the git
    # fallback it reads as. `collect_run_meta` either RAISES or returns a
    # non-empty `commit_sha` (`audit_run_meta.py:141-146` — it raises on a
    # non-work-tree and on an unresolvable HEAD, and never reports partially),
    # so `run_meta` is `{}` here exactly when the `try` above failed. On the
    # raising branches `_head_sha` runs the SAME `git rev-parse HEAD` against
    # the same `work_dir` and degrades to None identically — it cannot rescue
    # them. What it DOES cover is the OTHER way into that `except`: the guarded
    # `import audit_run_meta` failing, i.e. a broken or partial install, where
    # the engine's own git query still works fine. The clock `or` above is the
    # opposite — it genuinely fires on every raise, `now_utc()` needing no repo.
    commit_sha = run_meta.get("commit_sha") or _head_sha(work_dir)
    if commit_sha:
        row["commit_sha"] = commit_sha

    signature, sig_degraded = _no_work_signature(state, work_dir, cwd)
    row["mutation_signature"] = signature
    if sig_degraded is not None:
        # Mirrors the iteration record's own shape: an UNKNOWN signature is
        # stored as null WITH its cause beside it, never as an empty list — the
        # two are the difference between "nothing was touched" and "we could not
        # tell", and a consumer that cannot distinguish them will guess wrong.
        row["mutation_signature_degraded"] = sig_degraded

    diff_stat = _numstat(work_dir, _mutation_baseline(state).get("ref"),
                         _signature_members(signature))
    if diff_stat is not None:
        row["diff_stat"] = diff_stat

    sig_summary = f"{len(signature)} path(s)" if signature is not None else f"UNKNOWN ({sig_degraded})"
    summary = (f"{name}: at={row['at']}; commit_sha={row.get('commit_sha', '(unresolved)')}; "
               f"mutation_signature={sig_summary}; "
               f"diff_stat={diff_stat if diff_stat is not None else '(unknown)'}")

    if args.dry_run:
        print(f"[dry-run] record-transition: WOULD record {summary}. NOTHING persisted.",
              file=sys.stderr)
        return EXIT_ALLOW_STOP

    lifecycle = state.get(_LIFECYCLE_KEY)
    if not isinstance(lifecycle, dict):
        lifecycle = {}
        state[_LIFECYCLE_KEY] = lifecycle
    lifecycle[name] = row
    save_state(state_path, state)
    print(f"record-transition: recorded {summary}", file=sys.stderr)
    return EXIT_ALLOW_STOP


# --- state["lifecycle"]["completeness"] — the loop's SECOND exit check -------
#
# The executed gate answers ONE question: do the checks pass? That is not the
# same question as "is this complete and faithful", and `fairmind-gate/SKILL.md`
# has named BOTH as required exits since loop mode shipped ("Both must pass to
# close the task") while nothing ever implemented the second one.
#
# WHY A GATE AND NOT A CONVENTION. Measured 2026-08-24 on a real card: the
# contract compiled from the ticket's own prose pinned an invariant at the HTTP
# boundary and left the store accepting anything. Every check went green on the
# FIRST evaluation, the loop spent 0 of 8 iterations, and the shipped diff let
# any non-HTTP caller write an invalid row into an APPEND-ONLY log. Nothing in
# the contract could have caught it, because the contract was what was wrong. A
# loop whose only completeness signal is its own checks rewards writing nothing
# more: the fastest route to a confirmation streak is to stop.
#
# ⚠️ WHAT THIS PROVES, AND WHAT IT DOES NOT — read before calling it "enforced".
# The engine proves a verdict row EXISTS, that it names a role which does not
# own any check (maker != checker, read off the contract rather than hardcoded),
# that it is backed by an attestation the HARNESS wrote (a `SubagentStop`
# payload's `agent_type` — the one identity a model cannot author, per
# `hooks/scripts/check-journal.sh`), and that the tree has not moved since. It
# does NOT prove the reviewer reasoned well, and the attestation is a plain file
# under `.fairmind/` a determined orchestrator can write itself. That raises the
# cost and leaves an audit artifact; it is not unforgeable, and it is the same
# residual class the journal hook has always carried. Say so wherever this is
# described — a guarantee overclaimed is worse than one stated with its hole.
COMPLETENESS_VERDICTS = ("complete", "gaps")
_COMPLETENESS_KEY = "completeness"
_COMPLETENESS_PASS = "complete"
# Who the gate routes the turn to when the review is outstanding. A ROLE, not an
# agent name: the dispatch is the orchestrator's, and any reviewer that does not
# own a check satisfies `_maker_roles`.
_COMPLETENESS_OWNER = "code-reviewer"


def _maker_roles(state):
    """The roles that MAKE the code, read off the checks' own `owner` field
    rather than spelled here.

    maker != checker is the property; which slug spells the maker is the
    contract's business. Hardcoding `"software-engineer"` would silently
    un-enforce this the day a consumer renames the role — the same class of
    defect as `CLAUDE_AGENT_NAME`, a variable nothing set, gating the journal
    hook for nobody (PCF-1)."""
    checks = state.get("checks")
    if not isinstance(checks, list):
        return set()
    return {c.get("owner") for c in checks
            if isinstance(c, dict) and isinstance(c.get("owner"), str)}


def _read_attestation(path):
    """Return `(row, error)` for the harness-written attestation at `path`.

    Written by the `SubagentStop` hook when a sub-agent finishes during a live
    loop: `{agent_type, at, signature}`. `agent_type` comes from the hook
    payload, not from anything the model can set."""
    if not path:
        return None, ("--record-completeness requires --attestation: a verdict that names its own "
                      "author proves only that someone typed a role. Point it at the attestation "
                      "the SubagentStop hook wrote when the reviewer finished "
                      "(${FAIRMIND_BASE}/attest/<ref>-<agent_type>-<n>.json).")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            row = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"--record-completeness: cannot read attestation {path!r}: {exc}"
    if not isinstance(row, dict) or not isinstance(row.get("agent_type"), str):
        return None, (f"--record-completeness: attestation {path!r} carries no string "
                      "'agent_type'. It is not something this hook wrote.")
    return row, None


def _completeness_blocker(state, current_signature):
    """`None` when the loop may flip to `passed_pending_human`; otherwise the
    reason it may not, phrased for the reviewer who has to clear it.

    ⚠️ THE STALENESS GUARD IS NOT UNIFORMLY FAIL-CLOSED, and saying so plainly
    here is the point — an earlier draft of this docstring claimed it was, which
    would have had the next reader designing against a guarantee the code does
    not make. Three cases, deliberately different:
      - both signatures known and equal → the reviewer judged this tree, allow;
        unequal → REFUSE, the code moved after the review.
      - one known and one not → REFUSE. The repo's own ability to answer "did
        the tree move?" changed between the verdict and now, which is a real
        difference rather than a missing signal.
      - NEITHER known → ALLOW. The repo cannot answer the question at all
        (`no-baseline-ref`, `no-git-work-tree`, `git-query-failed`), and
        refusing would make the completeness gate unusable in exactly the repos
        where the mutation machinery is already degraded, while buying nothing:
        the verdict still had to be recorded against a harness-written
        attestation, and that attestation is still spent. The row carries
        `signature_degraded` so a reader can tell this case from a real match.
    """
    rows = _lifecycle(state).get(_COMPLETENESS_KEY)
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    latest = rows[-1] if rows else None
    cmd = ("python3 <plugin>/scripts/run_gate_checks.py --record-completeness "
           "--verdict complete|gaps --by <role> --attestation <path> [--summary <text>]")

    if latest is None:
        return ("⏸ GATE GREEN — COMPLETENESS REVIEW REQUIRED. Every check passes and the "
                f"confirmation streak has reached K, but the loop's SECOND exit check has not "
                "been answered: does the diff implement every decision in "
                "${FAIRMIND_BASE}/design/<ref>.md, at the layer that brief named? No check in "
                "the contract can ask that — the contract is what the brief exists to correct. "
                "Dispatch a reviewer (NOT the maker), then record the verdict:\n"
                f"    {cmd}\n"
                "This evaluation spent no budget and the streak is held, not reset.")
    if latest.get("verdict") != _COMPLETENESS_PASS:
        return ("⏸ GATE GREEN — COMPLETENESS REVIEW RETURNED "
                f"{latest.get('verdict')!r}: {latest.get('summary') or '(no summary recorded)'}\n"
                "The checks pass; the review says the work does not. Fix what it named, then "
                "have the reviewer record a fresh verdict:\n"
                f"    {cmd}\n"
                "The checks staying green through this is expected — they were never the "
                "signal that would catch it.")
    recorded_signature = latest.get("signature")
    if current_signature is None and recorded_signature is None:
        # BOTH sides unknown — the repo cannot answer "did the tree move?" at
        # all (`no-baseline-ref` on a loop armed outside a git work tree,
        # `no-git-work-tree`, `git-query-failed`). Blocking here would make the
        # completeness gate unusable in exactly the repos where the mutation
        # machinery is already degraded, and it would buy nothing: the verdict
        # still had to be recorded, against an attestation the harness wrote,
        # and that attestation is still spent. So the staleness guard stands
        # down when it has no signal — and says so on the row it wrote
        # (`signature_degraded`) rather than implying it checked.
        return None
    if current_signature is None or recorded_signature is None:
        # ASYMMETRIC — one side could be computed and the other could not, so
        # the repo's own answerability CHANGED between the review and now.
        # That is a real difference, not a missing signal: fail closed.
        return ("⏸ GATE GREEN — COMPLETENESS VERDICT CANNOT BE MATCHED TO THIS TREE. The "
                "mutation signature could be computed on one side and not the other, so "
                "something about this repo changed between the review and now. Failing closed: "
                "re-run the review against the current tree and record it again.")
    if recorded_signature != current_signature:
        return ("⏸ GATE GREEN — COMPLETENESS VERDICT IS STALE. It was recorded against a "
                "different tree: the code has changed since the reviewer read it, so the "
                "verdict describes work that is no longer what would ship. Re-review the "
                "current tree and record a fresh verdict:\n"
                f"    {cmd}")
    return None


def record_completeness(state, state_path, cwd, args):
    """Checker verb: record the loop's second exit check for the current tree.

    APPENDS to `state['lifecycle']['completeness']` — a loop that came back
    with gaps, was fixed, and passed keeps both rows, for the same reason
    `human_gate` appends: the round-trip IS the signal. Only the LAST row is
    operative, and only when its signature still matches the tree.

    Refuses before touching disk on: a loop that is not `running`; a streak
    short of K (before green there is nothing to review); a `--by` role that
    owns a check (maker != checker); a missing or non-matching attestation.
    Every refusal leaves loop-state.json byte-identical."""
    verdict = args.record_completeness
    status = state.get("status")
    if status != "running":
        print(f"--record-completeness refused: status is {status!r}, not 'running'. This verb "
              "records the review of a tree the gate has already called green.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    k = confirmation_threshold(state)
    confirmations = state.get("confirmations", 0)
    if not isinstance(confirmations, int) or confirmations < k:
        print(f"--record-completeness refused: the confirmation streak is {confirmations}/{k}. "
              "The completeness review reads the tree the gate certified; before the gate is "
              "green there is no such tree.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    by = (args.by or "").strip()
    if not by:
        print("--record-completeness refused: --by names the role that performed the review.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    makers = _maker_roles(state)
    if by in makers:
        print(f"--record-completeness refused: --by {by!r} owns a check in this contract, so it "
              "is the MAKER. A maker reviewing its own work is the one shape this gate exists "
              f"to refuse (maker != checker). Contract makers: {', '.join(sorted(makers))}.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # ONE resolved path for BOTH the read and the hash. `_read_attestation` used
    # to `open()` the argument as given (process cwd) while `_working_tree_sha`
    # joins it onto `--cwd` — agreeing only when the two happen to be the same
    # directory. That is not merely untidy: a relative path under a different
    # `--cwd` reads fine and hashes to None, and the replay guard below skips a
    # None sha, so the "one attestation, one verdict" property would quietly
    # disappear on exactly the invocation that misresolves.
    attestation_path = args.attestation
    if attestation_path and not os.path.isabs(attestation_path):
        attestation_path = os.path.join(cwd, attestation_path)
    attestation, err = _read_attestation(attestation_path)
    if err:
        print(err, file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    attested = attestation["agent_type"]
    attestation_sha = _working_tree_sha(cwd, attestation_path)
    if attestation_sha is None:
        # It was readable a line ago, so this is a race or a permission change.
        # Refuse rather than record a row the replay guard cannot recognise.
        print(f"--record-completeness refused: the attestation at {attestation_path!r} could not "
              "be hashed, so it could not be spent — a verdict whose attestation cannot be "
              "recognised later would let the same review sign off a second tree.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    # ONE ATTESTATION, ONE VERDICT. Without this, the first reviewer sub-agent
    # that ever ran in this loop leaves a file that satisfies every later
    # verdict for the rest of the run — including the verdict recorded after
    # the maker changed the code again. Consuming it is what makes "a reviewer
    # ran" a statement about THIS round rather than about the loop's history.
    # (The signature match on the row is the other half and covers a different
    # hole: it catches the tree moving AFTER a genuine review.)
    spent = {r.get("attestation_sha")
             for r in (_lifecycle(state).get(_COMPLETENESS_KEY) or [])
             if isinstance(r, dict)}
    if attestation_sha in spent:
        print(f"--record-completeness refused: attestation {attestation_path!r} has already "
              "been spent on an earlier verdict in this loop. A review is evidence about the "
              "round it ran in; re-using one would let a single reviewer turn sign off every "
              "later tree. Dispatch the reviewer again.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    if attested in makers:
        print(f"--record-completeness refused: the attestation was written for {attested!r}, "
              "which owns a check in this contract. The harness recorded the maker finishing, "
              "not a reviewer.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    if attested != by:
        print(f"--record-completeness refused: --by is {by!r} but the attestation was written "
              f"for {attested!r}. The role that ran is the role that reviews.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    work_dir, wt_degradation = resolve_work_dir(state, cwd)
    if wt_degradation is not None:
        reason, detail = wt_degradation
        print(f"--record-completeness refused: this loop records a worktree.path that could not "
              f"be resolved to a real, registered worktree of this repo ({reason}: {detail}). "
              "Failing closed rather than signing off the main tree as if it were the "
              "worktree's.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    signature, sig_degraded = _no_work_signature(state, work_dir, cwd)
    row = {
        "verdict": verdict,
        "by": by,
        "attested_by": attested,
        "attestation_sha": attestation_sha,
        "iteration_n": len(state.get("iterations") or []),
        "signature": signature,
    }
    if sig_degraded is not None:
        # Same shape the iteration record and `post_ceremony` use: an UNKNOWN
        # signature is stored as null WITH its cause, never as an empty list.
        # `_completeness_blocker` then fails closed on it rather than reading
        # "nothing changed" out of "we could not tell".
        row["signature_degraded"] = sig_degraded
    if args.summary:
        row["summary"] = args.summary

    sig_summary = f"{len(signature)} path(s)" if signature is not None else f"UNKNOWN ({sig_degraded})"
    summary = (f"completeness: verdict={verdict}; by={by}; attested_by={attested}; "
               f"signature={sig_summary}; iteration_n={row['iteration_n']}")
    if args.dry_run:
        print(f"--dry-run: would append {summary}")
        return EXIT_ALLOW_STOP

    lifecycle = state.get(_LIFECYCLE_KEY)
    if not isinstance(lifecycle, dict):
        lifecycle = {}
        state[_LIFECYCLE_KEY] = lifecycle
    rows = lifecycle.get(_COMPLETENESS_KEY)
    if not isinstance(rows, list):
        rows = []
    rows.append(row)
    lifecycle[_COMPLETENESS_KEY] = rows
    save_state(state_path, state)
    print(summary)
    return EXIT_ALLOW_STOP


HUMAN_GATE_VERDICTS = (
    "approved",
    "approved_with_changes_applied_first",
    "rejected_and_re_armed",
)
HUMAN_GATE_REARM_CAUSES = (
    "review_finding_substantive",
    "gate_evidence_insufficient",
    "false_green",
    "contract_defect",
    "scope_violation",
    "requirements_changed",
    "other",
)
_HUMAN_GATE_REJECTION = "rejected_and_re_armed"


def human_gate(state, state_path, args):
    """Human verb (§B.6): record the verdict a human gave at the final gate.

    APPENDS to a list, one row per gate visit — never a scalar. A loop that is
    rejected, re-armed and later approved is a real and common shape (19 arms on
    this machine carry `prev_status == "passed_pending_human"`, i.e. a re-arm
    after a green gate, across 37 loops sitting at that status — measured
    2026-08-14), and a last-write-wins scalar records only the approval, which
    is precisely the half that carries no information.

    The list is NEVER cleared by a re-arm (`_LIFECYCLE_PRESERVED_ON_REARM`), and
    it is not truncated here: loop-state is local and append-only by design, so
    any bound on how many rows travel belongs to the payload builder, at the one
    place where bytes actually cost something.

    ⚠️ NO CLOCK ON THIS ROW. `after_iteration_n` is sequence position, and that
    is the whole record of when. With a timestamp here, `human_gate.at` minus
    `lifecycle.gate_green.at` would be exactly how long the named developer
    deliberated before approving or rejecting, per gate — the measurement the
    ratified no-clock-on-human-records rule exists to withhold, on a lane that
    carries no notice at all. `at` is ABSENT rather than null so that a later
    widening of the row cannot fill it in by accident.

    ⚠️ AND THE RULE IS NOT YET WHOLE ON THIS LANE — stated because a reader of
    this function will otherwise assume it is. The `arm` audit entry already
    ships its own `at`, and an arm is a human action, so for a rejected-then-
    re-armed loop `gate_green.at → arm.at` still brackets the deliberation on
    the wire today. What this row's clocklessness buys is narrower and worth
    naming exactly: it does not ADD the measurement for the APPROVAL path, which
    today leaves no record at all. Closing the pre-existing half means changing
    what the payload projects from a shipped field, which is its own decision on
    its own card — not a silent third state.

    Refuses a rejection with no cause (the missing cause is the exact datum this
    verb exists to stop losing), a cause on either approve verdict, and any
    status other than `passed_pending_human`. Both enums are closed at
    `argparse`, so an unknown value exits non-zero before this function is
    reached, let alone disk.

    Correcting a mis-typed verdict re-runs the verb and appends a SECOND row for
    the same `after_iteration_n`; the last row for a given position is the
    operative one. Deliberately not an in-place edit — append-only is the one
    shape a human cannot use to erase a verdict they already gave.

    Writes nothing else. In particular it does NOT re-arm: the human still runs
    `--arm` afterwards. Honors `--dry-run`."""
    verdict = args.human_gate
    cause = args.rearm_cause

    if verdict == _HUMAN_GATE_REJECTION and not cause:
        print(f"--human-gate {verdict} refused: --rearm-cause is required for a rejection. "
              "A rejection with no recorded cause is exactly the datum this verb exists to "
              f"stop losing; pick one of {', '.join(HUMAN_GATE_REARM_CAUSES)}.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    if verdict != _HUMAN_GATE_REJECTION and cause:
        print(f"--human-gate {verdict} refused: --rearm-cause describes why a loop was sent "
              "BACK, so it cannot accompany an approval. Drop it, or record the rejection "
              f"as {_HUMAN_GATE_REJECTION}.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    refusal = _refuse_unless_passed(state, f"--human-gate {verdict}")
    if refusal is not None:
        return refusal

    # READ-ONLY until the write below: every refusal above and every refusal
    # here must leave `state` exactly as it was found, so `lifecycle` is never
    # created merely by asking about it.
    lifecycle = state.get(_LIFECYCLE_KEY)
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    existing = lifecycle.get("human_gate")
    if existing is not None and not isinstance(existing, list):
        print("--human-gate refused: lifecycle.human_gate is not a list, so appending would "
              "silently discard whatever is there. This record is append-only by design — a "
              "human must inspect loop-state.json rather than have a verdict overwritten.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # The green iteration's index, written at the flip. NULL is the ordinary
    # answer for every loop that reached `passed_pending_human` before that
    # index existed — which is all 37 of them on this machine today — and it
    # stays null rather than being searched for: audit entries can follow the
    # green one and every K-th confirmation green looks alike from the outside,
    # so a backwards search is a guess wearing a number.
    gate_green = lifecycle.get("gate_green")
    after_iteration_n = gate_green.get("iteration_n") if isinstance(gate_green, dict) else None

    row = {"verdict": verdict, "rearm_cause": cause or None,
           "after_iteration_n": after_iteration_n}
    summary = (f"{verdict} (cause={cause or 'n/a'}, after_iteration_n="
               f"{after_iteration_n if after_iteration_n is not None else 'unknown'})")

    if args.dry_run:
        print(f"[dry-run] human-gate: WOULD append {summary}. NOTHING persisted.",
              file=sys.stderr)
        return EXIT_ALLOW_STOP

    lifecycle = state.get(_LIFECYCLE_KEY)
    if not isinstance(lifecycle, dict):
        lifecycle = {}
        state[_LIFECYCLE_KEY] = lifecycle
    rows = lifecycle.setdefault("human_gate", [])
    rows.append(row)
    save_state(state_path, state)
    print(f"human-gate: appended {summary}; {len(rows)} verdict(s) recorded", file=sys.stderr)
    return EXIT_ALLOW_STOP


# --- active-context `mode` sync: the CLOSE half of the repoint --------------
#
# `loop_open.py --repoint` owns the OPEN half and is the only writer of `mode`.
# NOTHING owned the CLOSE half, so from the moment a loop reached a terminal
# status the repo went on declaring `mode: loop` over a dead loop FOR EVER —
# 23 days, measured in the plugin's own repo — and every later session was a
# degraded session. That is the CAUSE of JC8 one level above where JC8 was
# fixed: it is why degradation was the steady state instead of the fallback.
#
# 🔑 ONE PREDICATE, ONE SITE, AND THE SITE IS THE END OF `main()`. A repoint
# call beside each status write is the shape this track has already shipped
# three times, and the survey says why it cannot work here. The status writes
# are named by their FUNCTION rather than by a line number, because a line
# number in a file this size is stale the next time anyone edits above it —
# `grep -n 'state\["status"\] = ' run_gate_checks.py` re-derives the list:
#   * terminal, inside `run_gate`  — blocked_worktree, blocked_scope,
#     blocked_no_checks, passed_pending_human, and the budget block;
#   * terminal, inside `recover()` — blocked_recovered, which RETURNS from its
#     own function and so never reaches main's `record_terminal` branch;
#   * resume to running — `extend_budget()` (whose signature carries no `cwd` at
#     all), `arm()`, and one inside `main`'s own dispatch: a VERB-LESS self-heal
#     where a degraded `blocked_scope` loop flips itself back to running with no
#     verb dispatched at all.
# Every naive choke point misses at least one of the last two. Syncing once
# after the verb has dispatched needs no signature change anywhere, and covers
# every present AND future status write by construction — including any added
# after this comment, which is the property a per-site call cannot have.

_CONTEXT_MODE_LOOP = "loop"


def _desired_context_mode(state, closed):
    """The `mode` the active context SHOULD carry for the loop state on disk, or
    None for "leave it alone" — a first-class answer here, not a fallthrough.

    `closed` is `_loop_ledger.CONTEXT_MODE_CLOSED`, passed in from the one call
    site: the module that READS the value owns its spelling, so the writer and
    the reader cannot disagree about the word.

      running            armed, resumed, iterating   -> loop
      a TERMINAL status  the loop is done iterating  -> closed
      anything else      `specified`, a never-armed contract, an unrecognized
                         value from a future build   -> None

    🔑 THE FIELD IS THE CAPTURE REGIME, NOT THE LIFECYCLE STAGE — "what may
    capture attribute a row to?", and a loop awaiting its human verdict is
    already a loop no capture may attribute to. Its `started_at` window is over,
    its ledgers are the artifact a harvest reads, and `roll_window` without a
    boundary is a pure newest-N cap.

    The first cut answered None at `passed_pending_human` until an approving
    `--human-gate` verdict existed, to protect the pre-PR ceremony's
    `degraded_from` attribution. That attribution now arrives by another route —
    `after_loop` on rows in `_loop_ledger.NO_LOOP_DIR` — and the split had a
    cost the split itself could not see: a loop that goes GREEN leaves
    `mode: loop` over a terminal loop-state, so `resolve_loop_context()` reports
    DEGRADED for the whole ceremony window (`/simplify`, two model reviews, the
    PR) on every successful loop, and JC18's operator signal fires on a repo
    where nothing is wrong. A healthy repo must produce no signal.

    TERMINAL_STATUSES is the engine's own named vocabulary — the same constant
    `tests/test_liveness_rule_parity.py` derives its matrix from, so this writer
    and that matrix share one spelling. It is read by membership here while
    `_loop_ledger` tests exact-set-or-prefix and `check-journal.sh` globs; the
    note on `_loop_ledger._TERMINAL_EXACT` sets out how those three come apart
    and which direction each failure falls in. A status outside the tuple
    answers None, which leaves the marker at `loop`, which DEGRADES: the safe
    fallback, not a silent eviction.

    `status` is coerced to `str` for the same reason
    `_loop_ledger._live_loop_started_at` coerces: a non-str status from a
    hand-edited state must not raise, because every predicate on this path has
    to be unable to fail the gate."""
    status = str(state.get("status") or "")
    if status == "running":
        return _CONTEXT_MODE_LOOP
    if status in TERMINAL_STATUSES:
        return closed
    return None


def sync_active_context_mode(cwd, state_path, inspection=False):
    """Point `.fairmind/active-context.json`'s `mode` at what the loop on disk
    ACTUALLY is. THE ONE SITE — see the block comment above for why it is here
    and not beside each status write.

    NEVER RAISES and never changes an exit code. The gate's verdict is the
    product; this marker is bookkeeping, and bookkeeping that can fail-close a
    gate is worse than bookkeeping that is occasionally one run stale — the next
    invocation re-reads disk and heals it, which is also why this needs no
    retry and no lock.

    🔴 `inspection` IS TWO VERBS, NOT ONE, AND THE SECOND IS EASY TO MISS. An
    invocation that only LOOKS at the loop must leave the disk exactly as it
    found it, and this engine has two such verbs:

      * `--dry-run` — persists nothing, which is the pre-PR ceremony's whole
        guarantee and the only reason a human can run it on a green loop they
        have not yet approved.
      * `--validate-contract` — the read-only twin ("Never mutates
        loop-state.json, never evaluates a check", its own `--help`). It takes
        no `--dry-run` and needs none, so a guard keyed on that flag alone let
        it acquire a write — and the shape it is USED on is precisely the one
        that writes: a human asking "is this contract armable?" of a `blocked_*`
        loop, one command before re-arming it. Verified before it was fixed:
        `--validate-contract` on a `blocked_budget` loop left `mode: closed`.

    The guard is here rather than inside the predicate because the predicate
    reads DISK, which on an inspection still holds the pre-verb status: the
    marker would be rewritten to describe a loop the run deliberately did not
    advance. Nothing is lost by skipping — the next acting invocation re-reads
    disk and heals the marker anyway."""
    if inspection:
        return
    try:
        _sync_active_context_mode(cwd, state_path)
    except Exception:  # noqa: BLE001 — see the never-raises contract above
        pass


def _sync_active_context_mode(cwd, state_path):
    # Imported lazily and guarded by the caller, the same shape as `import
    # loop_ledger` / `import _consent_authority` elsewhere in this file: a
    # failed import means no sync, which is the correct fail-safe for a
    # best-effort write; the only sibling this module imports at module level
    # is the stdlib-only `_gate_mutation`.
    from _loop_ledger import CONTEXT_MODE_CLOSED

    ctx_path = os.path.join(cwd, ".fairmind", "active-context.json")
    # 🔴 SYNC AN EXISTING FILE ONLY — NEVER CREATE ONE. `active-context.json` is
    # the signal every hook in this plugin gates on (check-journal.sh:46,
    # trace-op.sh's fast path, capture-subagent-tokens.sh), so creating one
    # turns a plain directory into a Fairmind workspace and changes hook
    # behaviour for everything under it. A repo that never opened a session
    # must not gain one because a loop was driven through `--state` there.
    if not os.path.isfile(ctx_path):
        return
    if not state_path or not os.path.isfile(state_path):
        return

    # Read-merge-write: `mode` is the ONLY key touched, every other field is
    # carried through untouched (`fairmind`, `project`, `task_ref`, `base_path`
    # and anything a Fairmind bootstrap added). `task_ref` and `base_path` in
    # particular MUST survive — `_active_context_ref` and
    # `insights_flush_payload` join on `task_ref`, and check-journal.sh:71-74
    # exits 2 on an empty `base_path`, refusing every sub-agent completion.
    #
    # A malformed or non-dict context is left BYTE-INTACT: `json.load` raises
    # before any write (so the caller's swallow is what preserves it), and a
    # non-dict returns. The same refusal `loop_open.repoint` already makes — a
    # file this cannot parse is a file whose other fields it cannot preserve,
    # and it may be a truncation or a merge conflict a human still has to fix.
    with open(ctx_path, encoding="utf-8") as fh:
        # The RAW bytes are kept for the compare-and-swap below, so the snapshot
        # this decision was made from is the snapshot that gets replaced.
        raw = fh.read()
    ctx = json.loads(raw)
    if not isinstance(ctx, dict):
        return

    # 🔴 WHOSE FIELD IT IS — AND THE ANSWER IS ASYMMETRIC, WHICH THE FIRST FIX
    # HERE WAS NOT. The sync used to key only on the loop-state it could reach at
    # `base_path`, never on the mode already in the file, and two live shapes
    # broke:
    #   * a context with NO `mode` key — the shape README.md:125 documents,
    #     `{base_path, project_id, session_mindstreamId}` — became `closed` on
    #     the first Stop-hook engine run that could reach a terminal loop-state
    #     at its `base_path`, and `check-journal.sh` then stood the journal gate
    #     down for the rest of the session. That gate is an enforcement rule the
    #     sub-agent cannot finish without, so this silently retired it.
    #   * the mirror: a leftover `running` loop-state flipped a
    #     `/fairmind-develop` run's `interactive` marker to `loop`, which enables
    #     `capture-orchestrator-tokens.sh` and stamps every row `mode: "loop"`.
    #
    # 🔴 THE FIX FOR THOSE WAS `mode not in (loop, closed) -> return`, AND IT
    # BLOCKED A HEAL ALONG WITH THE DAMAGE. Only `-> closed` was destructive.
    # `-> loop` over an ABSENT marker is a CLAIM OF A FIELD NO LANE HOLDS, and
    # it is what makes the capture lane work for a loop bootstrapped on the
    # README shape: measured 2026-08-16 on that shape over a LIVE armed loop,
    # the first engine run used to write `mode: "loop"` (0 rows evicted, the
    # orchestrator token hook capturing). Under the blanket guard the marker
    # stayed unset, the live loop's own token ledger lost 502 in-window rows to
    # a windowless newest-N cap, and `capture-orchestrator-tokens.sh` — which
    # requires `mode == "loop"` — was dark for the loop's whole lifetime, all of
    # it silent because the resolver reports `degraded=False` on that shape and
    # JC18's operator signal never fires.
    #
    # So: an ABSENT (or explicitly null) `mode` is unclaimed and may be claimed
    # by `loop`, never closed by `closed`; an EXPLICIT value is somebody's claim
    # and is never touched in either direction — `interactive` is
    # `/fairmind-develop`'s own, and a non-string or future value is a marker
    # `_loop_ledger` treats as BROKEN, which the engine has no evidence to
    # repair. Pinned row by row in tests/test_loop_close_repoint.py (k).
    have = ctx.get("mode")
    if have is None:
        allowed = (_CONTEXT_MODE_LOOP,)
    elif have in (_CONTEXT_MODE_LOOP, CONTEXT_MODE_CLOSED):
        allowed = (_CONTEXT_MODE_LOOP, CONTEXT_MODE_CLOSED)
    else:
        return

    # 🔴 AND THE MARKER MUST DESCRIBE THE LOOP THIS RUN OPERATED ON. `--state`
    # without a matching `--cwd` was enough to rewrite a BYSTANDER repo's LIVE
    # marker to `closed` — disabling its journal gate and diverting its capture
    # — while its own loop was still running. This used to be STATED rather than
    # guarded, on the argument that only tests and hand inspection pass
    # `--state`; a hand inspection in the wrong directory is exactly the case
    # that fired. The predicate is the marker's OWN `base_path` resolving to the
    # `loop-state.json` this run read; `realpath` on both sides because a repo
    # (and every temp dir on darwin) can sit behind a symlink, and an absent or
    # non-str `base_path` names no loop at all rather than joining to
    # `<cwd>/loop-state.json` and matching by accident.
    base = ctx.get("base_path")
    if not isinstance(base, str) or not base:
        return
    if os.path.realpath(os.path.join(cwd, base, "loop-state.json")) \
            != os.path.realpath(state_path):
        return

    # RE-READ loop-state FROM DISK rather than trusting main's in-memory copy.
    # The in-memory state can hold mutations that were never persisted — the
    # `--dry-run` evaluation, the optimistic `blocked_scope` -> running flip
    # before `run_gate` decides, a verb that refused after touching nothing.
    # What the marker must describe is what the loop IS, which is what was
    # written; disk is the only place that answer exists. (Every status write
    # DOES persist before this point, including the verb-less `blocked_scope`
    # self-heal that sets `running` in `main`'s own dispatch — driven end to end
    # by test (d) in tests/test_loop_close_repoint.py, which would read `closed`
    # over a live loop if it did not.)
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    if not isinstance(state, dict):
        return
    want = _desired_context_mode(state, CONTEXT_MODE_CLOSED)
    if want is None or want not in allowed or have == want:
        return  # nothing to correct -> not one byte rewritten

    # 🔴 COMPARE-AND-SWAP, BECAUSE THIS IS A READ-MERGE-WRITE OF SOMEBODY ELSE'S
    # FILE. Both cross-model reviewers found the same race independently: this
    # rewrites the WHOLE marker from a snapshot taken before the verb ran, and
    # it shares no lock with `loop_open.repoint` — the very next
    # `/fairmind-loop` or `/fairmind-develop` Phase 0, which runs as that run's
    # FIRST mutation. Last `os.replace` wins, so a concurrent repoint's
    # `task_ref`/`base_path`/`mode` can be replaced wholesale by this snapshot.
    #
    # 🔑 AND "the next invocation heals it" IS TRUE OF THIS FIELD AND FALSE OF
    # THAT WRITE. The next gate run re-reads disk and re-syncs `mode`; nothing
    # ever re-points a `task_ref` a repoint had already set, so a clobbered
    # repoint leaves the marker describing the PREVIOUS loop — which is the
    # exact stale-marker state this whole card exists to remove, restored by the
    # mechanism meant to remove it.
    #
    # Re-reading the bytes immediately before the replace narrows the window to
    # the two syscalls between them rather than the whole verb. It is not a
    # lock: a writer landing inside that window still wins the race. A real lock
    # is the correct fix and belongs with the marker's OTHER writer rather than
    # here — `loop_open.repoint` does the same read-merge-write and takes none
    # either, so a lock added on one side alone would buy nothing.
    try:
        with open(ctx_path, encoding="utf-8") as fh:
            if fh.read() != raw:
                return  # somebody rewrote the marker while the verb ran
    except OSError:
        return

    ctx["mode"] = want
    _atomic_write_json(ctx_path, ctx, prefix=".active-context.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the fairmind-coding loop gate.")
    parser.add_argument("--state", help="Explicit path to loop-state.json (testing/inspection).")
    parser.add_argument("--cwd", help="Repository root (defaults to $CWD or process cwd).")
    parser.add_argument("--emit-json", help="Also write the full decision object to this path.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate and report without mutating loop-state.json.")
    parser.add_argument("--extend-budget", metavar="KEY=VALUE[,KEY=VALUE]",
                        help="Human-only: grant a blocked loop more budget "
                             "(keys: iterations, failures, timeout_min). Never invoked by the gate.")
    parser.add_argument("--arm", action="store_true",
                        help="Human/orchestrator-only: flip a loop into 'running', stamping "
                             "budget.spent.started_at and zeroing confirmations. Refused on an "
                             "already-'running' loop or with no admitted check. Never invoked "
                             "by the gate itself.")
    parser.add_argument("--recover", action="store_true",
                        help="Human-only: free a loop wedged in 'running' whose owning session "
                             "is gone (running -> blocked_recovered, owner_session cleared) so "
                             "--arm/--extend-budget can resume it. Requires --user-confirmed; "
                             "gated on that human confirmation, never on a session-id mismatch.")
    parser.add_argument("--hold", action="store_true",
                        help="Human-only (H4/F24): suspend confirmation counting while a "
                             "human-approved contract amendment is in flight, so a green loop "
                             "cannot close on the very check the amendment exists to replace. "
                             "Sets state['hold']; a held all-green evaluation stays 'running' "
                             "forever (never reaches passed_pending_human). Refused on any "
                             "status other than 'running' (a pre-arm hold poisons --arm's "
                             "fresh-vs-re-arm classification) — arm the loop first. "
                             "--user-confirmed is optional. Never invoked by the gate itself.")
    parser.add_argument("--release", action="store_true",
                        help="Human-only (H4/F24): clear a hold set by --hold and resume "
                             "confirmation counting FROM 0 — a streak earned against the "
                             "superseded check does not carry over. Refused on any status "
                             "other than 'running', mirroring --hold's guard. --user-confirmed "
                             "is optional. Never invoked by the gate itself.")
    parser.add_argument("--record-transition", choices=RECORD_TRANSITIONS,
                        help="Human/orchestrator-only: record a loop lifecycle transition "
                             "in state['lifecycle']. 'post_ceremony' captures the tree AFTER "
                             "the pre-PR ceremony mutated it (signature + commit sha + diff "
                             "stat), so the tree that merges can be compared with the tree "
                             "the gate labelled. Refused unless the gate is green. Touches "
                             "neither status, confirmations nor iterations[].")
    parser.add_argument("--human-gate", choices=HUMAN_GATE_VERDICTS,
                        help="Human-only: record the verdict given at the final human gate. "
                             "APPENDS to state['lifecycle']['human_gate'] — a rejected, "
                             "re-armed, later-approved loop keeps every verdict. Requires "
                             "--rearm-cause on a rejection and refuses it on an approval. "
                             "Carries sequence position, never a clock. Does NOT re-arm.")
    parser.add_argument("--record-completeness", choices=COMPLETENESS_VERDICTS,
                        metavar="VERDICT",
                        help="Checker-only: record the loop's SECOND exit check — does the diff "
                             "implement every decision in the design brief, at the layer it "
                             "named? APPENDS to state['lifecycle']['completeness']. Only a "
                             "'complete' verdict whose signature still matches the tree lets the "
                             "gate flip to passed_pending_human. Requires --by (a role owning no "
                             "check: maker != checker) and --attestation.")
    parser.add_argument("--by",
                        help="The role that performed the completeness review. Refused when it "
                             "owns a check in this contract.")
    parser.add_argument("--attestation",
                        help="Path to the attestation the SubagentStop hook wrote when the "
                             "reviewer finished. Its harness-supplied agent_type is what makes "
                             "--by evidence rather than a claim.")
    parser.add_argument("--summary",
                        help="What the completeness review found, in one line. Recorded on the "
                             "verdict row and echoed back by the gate on a 'gaps' verdict.")
    parser.add_argument("--rearm-cause", choices=HUMAN_GATE_REARM_CAUSES,
                        help="Why a --human-gate rejection sent the loop back. Required for "
                             "'rejected_and_re_armed', refused on the two approve verdicts.")
    parser.add_argument("--validate-contract", action="store_true",
                        help="Read-only: check that every HARD contract.criteria[] entry is "
                             "covered by a live, admitted check. Exits non-zero (naming every "
                             "offender + recommending interactive mode) on an uncovered hard "
                             "criterion. Never mutates loop-state.json, never evaluates a check. "
                             "--arm runs the SAME validation internally and refuses on error.")
    parser.add_argument("--user-confirmed",
                        help="Records the human's literal confirmation in the "
                             "arm/extend-budget/recover audit entry.")
    parser.add_argument("--session-id",
                        help="Claude Code session id (from the Stop hook, or the orchestrator's own "
                             "session id when passed to --arm). On a plain gate call, binds a running "
                             "loop that has no owner yet to the first session that drives it; sessions "
                             "other than the owner no-op. On --arm, stamps this id as owner_session immediately, at arm time, "
                             "instead of leaving ownership to whichever session's Stop hook fires first; "
                             "absent, --arm falls back to $CLAUDE_CODE_SESSION_ID, and with neither it "
                             "clears owner_session.")
    args = parser.parse_args(argv)

    state_path, cwd = resolve_state_path(args)

    # Dispatch the verb, then sync the active-context marker to whatever the
    # loop ended up as — ONE call, on the way out, so every verb and the
    # verb-less paths alike are covered without any of them knowing about it.
    # Sequential, deliberately NOT a `finally`: if the dispatch raises, the
    # verb half-executed and a marker written off that state describes nothing
    # anyone can reason about. The next invocation re-reads disk and heals it.
    rc = _dispatch(args, state_path, cwd)
    sync_active_context_mode(cwd, state_path,
                             inspection=args.dry_run or args.validate_contract)
    return rc


def _dispatch(args, state_path, cwd):
    """Run the verb `args` selects (or the gate, when it selects none) and
    return the exit code. Split out of `main()` so the active-context sync has
    exactly one call site — see `sync_active_context_mode`. `cwd` and
    `state_path` come from `main`'s `resolve_state_path`, which is also what
    lets the sync reach `cwd` for the verbs whose own signatures never take it
    (`extend_budget`, `human_gate`, `recover`)."""
    # No active loop → silent no-op so the Stop hook composes outside loop mode.
    if not state_path or not os.path.isfile(state_path):
        if args.extend_budget:
            print("--extend-budget: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.arm:
            print("--arm: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.recover:
            print("--recover: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.hold:
            print("--hold: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.release:
            print("--release: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.record_transition:
            print("--record-transition: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.human_gate:
            print("--human-gate: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.record_completeness:
            print("--record-completeness: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.validate_contract:
            print("--validate-contract: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.dry_run:
            print("Gate inspection: no loop-state.json found; not applicable, not a passed gate.",
                  file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        return EXIT_ALLOW_STOP

    # A foreign session's plain Stop is answered here, before the lock, so it
    # never waits behind the owner's evaluation (see `_stop_is_foreign`).
    if _stop_is_foreign(args, state_path):
        return EXIT_ALLOW_STOP  # foreign session → silent no-op, no mutation, no wait

    # Everything from here on is one read -> evaluate/mutate -> save cycle,
    # serialized across processes by `_state_write_lock` (see its docstring
    # for the race this closes). The lock is acquired BEFORE `load_state`, in
    # `_dispatch_locked`, not around this call — the read has to be inside it
    # too, or a lock guarding only the write would still let two invocations
    # load the same stale snapshot.
    with _state_write_lock(state_path) as proceed:
        if not proceed:
            # The lock never came free; nothing was read or written. A plain Stop
            # lets the turn end — failing it would re-block every Stop behind the
            # same stuck holder — while a verb reports that it did not run.
            return EXIT_ALLOW_STOP if _is_plain_stop(args) else EXIT_INTERNAL_ERROR
        return _dispatch_locked(args, state_path, cwd)


# The only options a plain Stop-hook evaluation carries (loop-check.sh passes
# --cwd and --session-id). Any other option set means a verb or an inspection,
# which must take the locked path — an allow-list, so a new verb falls there by
# default instead of being answered by `_stop_is_foreign`.
_PLAIN_STOP_OPTIONS = frozenset({"state", "cwd", "emit_json", "session_id"})


def _is_plain_stop(args):
    return not any(value for name, value in vars(args).items()
                   if name not in _PLAIN_STOP_OPTIONS)


def _stop_is_foreign(args, state_path):
    """True when this is a plain Stop evaluation from a session that does not
    own the running loop, decided WITHOUT the state lock.

    Otherwise that Stop would queue on `_state_write_lock` behind the owner's
    in-flight evaluation — up to `DEFAULT_DEADLINE_CAP_S`, inside its own hook
    timeout — only to reach the ownership check in `_dispatch_locked` and
    allow the stop. The lock-free read is safe: `save_state` replaces the file
    atomically, so it sees one whole version, and "another session owns this
    loop" read from any version is the answer the locked path gives at that
    instant. Anything else — no owner yet (the claim), this session's own loop,
    an unreadable file — returns False and goes through the locked path, which
    re-checks and stays authoritative."""
    sid = (args.session_id or "").strip()
    if not sid:
        return False
    if not _is_plain_stop(args):
        return False
    try:
        state = load_state(state_path)
    except (OSError, ValueError):
        return False
    owner = state.get("owner_session") if isinstance(state, dict) else None
    return bool(owner) and owner != sid


def _dispatch_locked(args, state_path, cwd):
    """The body of `_dispatch` for a loop-state.json that exists on disk, run
    entirely under `_state_write_lock(state_path)`. Split out purely so that
    lock spans the load, not just the eventual save — see the lock's own
    docstring."""
    try:
        state = load_state(state_path)
    except (OSError, ValueError) as exc:
        print(f"loop-check: cannot read {state_path}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Human-only budget extension: an explicit flag, never an implicit env path.
    if args.extend_budget:
        return extend_budget(state, state_path, args)

    # Human/orchestrator-only arming verb (C1/C2): handled here, BEFORE the
    # status != "running" early return below — placing it after that guard
    # would make arming a `specified` (never-armed) loop a silent no-op, exit
    # 0 with nothing done. Also short-circuits before the session-ownership
    # claim: arming never evaluates checks.
    if args.arm:
        return arm(state, state_path, cwd, args)

    # Human-only recovery verb (W2.1): the ONLY path out of a `running` loop
    # whose session is gone. Handled here, before the status != "running" guard
    # (a running loop would otherwise fall through to the gate) and before the
    # session-ownership claim (recovery is deliberately session-agnostic).
    if args.recover:
        return recover(state, state_path, args)

    # Human-only hold/release verbs (H4/F24): handled here, ahead of the
    # generic status != "running" guard below, but each now enforces ITS OWN
    # equivalent guard internally (hold_verb/release_verb both refuse on any
    # status other than "running" — H4 adversarial-pass amendment). A hold
    # settable on any status, including pre-arm, was the original design and
    # is exactly what broke arm()'s fresh-vs-re-arm classification: a
    # pre-arm hold/release audit entry in iterations[] made a genuinely
    # fresh --arm look like a re-arm and skip the baseline freeze. Dispatched
    # here (before the generic guard, before the session-ownership claim)
    # only so each verb's own refusal message and exit code are the ones the
    # caller sees, matching --arm/--recover's placement — not because the
    # status check itself is skipped.
    if args.hold:
        return hold_verb(state, state_path, args)

    if args.release:
        return release_verb(state, state_path, args)

    # The two lifecycle verbs (§B.3, §B.6). Dispatched HERE, with the other
    # human verbs, for one load-bearing reason: both require
    # `passed_pending_human`, and the generic `status != "running"` guard below
    # returns EXIT_ALLOW_STOP on exactly that status — placed after it, each verb
    # would silently do nothing on the only status it is ever used for. Before
    # the session-ownership claim too: neither evaluates a check, and the human
    # running the ceremony is routinely in a different session from the one that
    # drove the loop.
    if args.record_transition:
        return record_transition(state, state_path, cwd, args)

    if args.human_gate:
        return human_gate(state, state_path, args)

    # The completeness verb. Dispatched HERE, with the other lifecycle verbs and
    # ahead of both the generic status guard and the `--dry-run` gate branch:
    # unlike its two neighbours it requires `running` (the loop is held green at
    # K, not yet flipped), so falling through would run an EVALUATION instead of
    # recording a verdict — and `--record-completeness --dry-run` would silently
    # mean "evaluate the gate", which is a different verb entirely.
    if args.record_completeness:
        return record_completeness(state, state_path, cwd, args)

    # T10/AC1: `--validate-contract` — the read-only twin. Handled here, before
    # the `status != "running"` guard below (it must answer for a `specified`,
    # never-armed loop, which is exactly when a human asks "is this armable?"),
    # and before the session-ownership claim (it evaluates nothing and mutates
    # nothing, so ownership is irrelevant). Composes with --state/--cwd like every
    # other verb; --dry-run is a no-op for it (it is read-only either way).
    if args.validate_contract:
        return validate_contract_verb(state, state_path)

    if args.dry_run:
        contract = state.get("contract")
        contract = contract if isinstance(contract, dict) else {}
        mutation = contract.get("mutation_set")
        mutation = mutation if isinstance(mutation, dict) else {}
        baseline = mutation.get("baseline")
        baseline = baseline if isinstance(baseline, dict) else {}
        print(f"Gate inspection: state={state_path!r}; status={state.get('status')!r}; "
              f"baseline={baseline.get('ref')!r}. This evaluates only the selected loop.",
              file=sys.stderr)
        if not args.state:
            print("State selected from ambient context. For this task's acceptance check, "
                  "verify the loop identity and pass --state explicitly; an unrelated loop "
                  "is not an acceptance gate for this change.", file=sys.stderr)

    # AC6(a): `--dry-run` evaluates the gate regardless of `status` — it never
    # persists (the `save_state` call below is itself gated on `not
    # args.dry_run`), so there is nothing to protect by refusing it on a
    # not-yet-armed (`specified`) or terminal loop. This is what lets the
    # arm-time smoke run (Phase 0, before `--arm` is ever called) actually
    # evaluate something, instead of forcing the orchestrator to hand-write
    # `status: "running"` first — the exact accounting write T19 exists to
    # abolish (AC6, mid-loop contract amendment).
    #
    # AC6(b), non-negotiable: this relaxation is scoped to `args.dry_run`
    # ONLY. The real (non-dry-run) path — what the Stop hook actually drives —
    # MUST still early-return on any status != "running": the loop stays an
    # inert signal outside itself. Do not widen this bypass to the plain path.
    #
    # F28 special case: a `blocked_scope` loop is NOT necessarily as terminal
    # as every other blocked_*/passed_pending_human status — a DEGRADED stop
    # (the mutation set was merely UNKNOWN, never a real violation) may be a
    # transient that has since cleared. `_degraded_scope_recoverable` keys
    # this off the one signal already persisted on disk (the trailing
    # scope_violation entries' `"degraded"` key, A1a/A1b) and bounds the
    # retries at DEGRADED_SCOPE_RETRY_CAP: a REAL violation (no `"degraded"`
    # key) or a cap-exhausted transient falls through to the same terminal
    # no-op as before. Eligible cases fall through to `run_gate` below instead
    # of no-op'ing — optimistically flipping status back to "running" first so
    # a genuinely recovered evaluation proceeds exactly like any other running
    # loop; if the transient has NOT cleared, `evaluate_scope`/`run_gate`
    # re-block to "blocked_scope" and append another degraded audit entry.
    if state.get("status") != "running" and not args.dry_run:
        if state.get("status") == "blocked_scope" and _degraded_scope_recoverable(state):
            state["status"] = "running"
        else:
            return EXIT_ALLOW_STOP  # already terminal — nothing to enforce

    # Session ownership (portable multi-session guard). loop-state.json is repo-global
    # and the Stop hook fires on EVERY session's stop in this repo — so without this an
    # unrelated session (a different workstream in the same checkout) would have its stop
    # gated by, and could iterate/consume budget on, a loop it has nothing to do with.
    # `arm()` normally names the owner already (the arming session); a running loop with
    # no `owner_session` (armed with no session id) is CLAIMED by the first session that
    # drives the gate. Any session other than the owner is a
    # silent no-op (allow stop), never blocked and never a competing maker. Skipped under
    # --dry-run (inspection) and when no session id is supplied (backward compatible).
    sid = (args.session_id or "").strip()
    if not args.dry_run and sid:
        owner = state.get("owner_session")
        if not owner:
            state["owner_session"] = sid  # claim; persisted by the save_state below
        elif owner != sid:
            return EXIT_ALLOW_STOP  # foreign session → silent no-op, no mutation

    try:
        decision = run_gate(state, cwd, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — a gate crash must be surfaced, not silent
        print(f"loop-check: gate evaluation error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if not args.dry_run:
        save_state(state_path, state)
        # JC6 — content capture, AFTER the persist and under the same guard.
        #
        # 🔴 AFTER `save_state`, NOT INSIDE `run_gate`, AND THE ORDER IS THE
        # WHOLE SAFETY ARGUMENT. The capture writes a file under a lock; the
        # Stop hook that invokes this runs under a 600 s process timeout. Placed
        # before the persist, a capture that waited on a contended lock past the
        # remaining budget would get this process killed with the gate's own
        # record still unwritten — losing the verdicts, the budget charge, the
        # session claim and the very red iteration it existed to keep. Here, the
        # worst a slow or broken capture can cost is itself.
        #
        # Lazy, guarded import and a bare except, the same shape as the ledger
        # call below and for the same reason: a telemetry side-effect must never
        # be able to fail a check.
        #
        # 🔴 GATED ON `checks_started_at` — THE PROOF THAT THIS EVALUATION
        # PRODUCED THE ROW. `run_gate` stamps it on the two not-green
        # results-bearing returns and nowhere else.
        #
        # WHAT THAT BUYS, stated precisely because the first version of this
        # comment claimed something the code next to it already did. The three
        # early refusals (`blocked_scope`, `blocked_worktree`,
        # `blocked_no_checks`) DO return an empty `results` list, so the second
        # conjunct alone already excluded them — and without either, the capture
        # would have walked past their results-less audit entry to re-capture an
        # OLDER red row. The conjunct that is doing work here is the FIRST one,
        # and what it buys is that a GREEN evaluation never imports the module
        # at all: "a repository that did not opt in writes nothing" then covers
        # the interpreter's own bytecode cache, not just the data directory.
        if decision.get("checks_started_at") and decision.get("results"):
            try:
                import content_capture
                content_capture.capture(state, cwd, decision)
            except Exception:  # noqa: BLE001 — never let a capture error fail-close a check
                pass

    if args.emit_json:
        with open(args.emit_json, "w", encoding="utf-8") as fh:
            json.dump({"decision": decision["decision"],
                       "results": decision["results"],
                       "status": state.get("status")}, fh, indent=2)

    feedback = decision.get("feedback", "")
    if feedback:
        print(feedback)

    if decision["decision"] == DECISION_ITERATE:
        return EXIT_ITERATE
    # Block ONCE on the transition into a terminal state (passed_pending_human or
    # blocked_*) so the Stop hook surfaces the final report — the human gate on a
    # pass, the cost-ask on a block — and the orchestrator is re-invoked to present
    # it, instead of the turn ending silently. The terminal status is now persisted,
    # so the NEXT stop returns EXIT_ALLOW_STOP at the `status != "running"` guard
    # above: this blocks exactly once and never loops. Skipped under --dry-run (state
    # is not persisted, so it is inspection only and would otherwise block forever).
    if not args.dry_run and decision["decision"] in (DECISION_STOP_PASSED, DECISION_STOP_BLOCKED):
        # Record the closed loop in the run ledger. Lazy, guarded import: the ledger
        # is cosmetic, so a missing or broken loop_ledger must never fail the gate.
        try:
            import loop_ledger
            loop_ledger.record_terminal(cwd, state, decision["results"])
        except Exception:  # noqa: BLE001 — never let a ledger error fail-close a check
            pass
        return EXIT_ITERATE
    return EXIT_ALLOW_STOP  # noop, or a terminal state already surfaced on a prior stop


if __name__ == "__main__":
    sys.exit(main())
