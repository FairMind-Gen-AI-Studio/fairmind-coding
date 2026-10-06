#!/usr/bin/env python3
"""
insights_flush_payload.py — Agentic Insights terminal-flush payload builder (PL-4).

`/fairmind-loop`'s Exit gate, on a closed loop, assembles two payloads for the
`Insights_record_loop_stats` / `Insights_record_agent_decisions` MCP tools and
hands them to project-context, which owns persistence — the record it keeps
and the stores it feeds. This script is the assembly step: it reads the closed
loop's own on-disk artifacts (loop-state.json, the loop ledger, the trace
ledger, the sub-agent token ledger, the PL-3 decisions log) and builds the
exact wire shape each MCP tool expects — nothing more, since the server
injects its own `userId`/`company`/`rawPayload`.

A re-flush of an unchanged close must be a no-op, so a small on-disk cursor
(`.fairmind/insights-sync.json`) tracks what has already gone out: one entry
per flushed `loop_id` (keyed to the `closed_at` it was flushed at, so a
REVIVED loop's later close is pending again) and one entry per flushed
`decisionId`. Nothing here calls the network — `main()` only emits the
pending payloads (or `null`) so the command body can call the MCP tools
itself and then `--commit` what it actually sent.

THE OUTPUT CHANNEL IS THE BINDING SIZE CONSTRAINT — not a server body cap.
`Insights_record_loop_stats` / `Insights_record_agent_decisions` /
`Insights_record_harness_audit` are MCP tools with no body cap and no 413 at
any size these payloads can reach. (The AMBIENT SESSION REST door is a
different door with a real body cap. Do not import its semantics here.)
A failed MCP call is not a dead letter either: the category simply stays
UNCOMMITTED on disk and `/fairmind-sync-insights` retries it.

The real cliff is the agent-mediated hand-off. Both `/fairmind-loop`'s Exit
gate and `/fairmind-sync-insights` run this script and read its stdout, and a
Bash tool result is capped at 30,000 bytes (measured: 30,000 B arrives whole,
30,001 B is replaced by a ~2 KB preview plus a file path). Above that the
consuming agent is handed a PREVIEW instead of the payload. The TRUNCATION is
announced — it prints "Output too large" and names a file holding the rest —
but nothing downstream of it is: reconstructing from the preview yields a
corrupted fleet number, skipping the category loses data, and erroring fails
the close on a telemetry step, and all three look like an ordinary turn from
the inside. This is not hypothetical — measured 2026-07-26, `--emit all` is
28,995 B in a consumer repo (96.6% of the cap) and 39,688 B on this
plugin's own `loop-t8` close. (The T8 figure needs `active-context.json` to
name T8: the trace is resolved from `task_ref`, not from `--base`, so
`--base <t8> --emit all` alone does not reproduce it.)

`--out` removes the dependency rather than tuning under it: the payload goes
to a FILE and stdout carries only a short, bounded summary naming the file.
`--emit` alone still prints the payload to stdout, byte-for-byte as before —
something may already depend on it.

The file has its OWN ceiling one layer down, and the shape of the file answers
it: an agent's file reader pages by LINE, so it caps a single-line file at
roughly 50,000 bytes and cannot page it at all. `--out` therefore writes
INDENTED JSON (see `_payload_file_text`) — 23% more bytes, but paginable, so
an oversized file degrades to "read it in two calls" instead of to a prefix.
Per-category `--emit loop|decisions|audit|brain --out` is the other lever, and
`--commit <category>` is unchanged by it.

Size discipline in the builders below is still right, but it is about these
channels and about what is worth STORING, never about a body the server would
reject.

Path contract (deliberately mixed fixed vs. base-relative — see the PL-4
dispatch): `loop-state.json` and the token ledgers
(`subagent-tokens-<sanitized ref>.jsonl`, plus the legacy unkeyed
`subagent-tokens.jsonl` that readers still union — `_loop_ledger.
loop_ledger_paths` owns that rule) live under the
loop's OWN `base_path` (resolved from `.fairmind/active-context.json` when
not given explicitly); the loop ledger, the trace ledger, the decisions log,
and the sync cursor are FIXED under `.fairmind/` regardless of `base_path` —
they are per-repo, not per-loop. The audit run's own three sources
(`run-meta.json`, `summary.json`, `assessment.jsonl`) are FIXED too; it
nonetheless consults
`loop-state.json`/`active-context.json` for the correlation keys below,
which is why `build_audit_payload` takes a `base` like its two siblings.

Correlation keys (T1·X1): all three payloads carry the session and the
project the work happened under, read from the state the loop already keeps
— `owner_session` from `loop-state.json`, the project from
`active-context.json`. They are what joins an audit run (the only outcome
record) to the behaviour that produced it. A key whose value is unavailable
is OMITTED, never sent as a sentinel: `/harness-audit` run standalone has no
loop-state and therefore no session, and "no session" must not arrive
server-side as the string "unknown".

No PAYLOAD FIELD reads the wall clock: every one is derived from what is
already on disk, so `build_loop_payload`/`build_decisions_batches` are
byte-identical across repeated calls on an unchanged tree (loop-mode
determinism gates on exactly this).

⚠️ Narrowed from "never reads the wall clock" on 2026-09-12, when
`brain_is_disabled` began reading the central policy — whose cache has a
freshness window and therefore needs a `now`. The clock decides whether a
category is OFFERED, never what a payload SAYS, so the determinism the loop
gates on is untouched; stating the narrower claim rather than leaving the wider
one standing falsely. Stdlib only.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
# ⚠️ `posixpath`, NOT `os.path`, and the choice is deliberately
# UNTESTABLE ON THIS HOST: `os.path is posixpath` is True here, so no
# test can go red if someone 'simplifies' `_decision_wire_path`'s
# `posixpath.normpath(...).split("/")` back to `os.path.normpath`. Off
# posix that swap breaks the predicate silently — `os.path.normpath(
# '../x')` is `'..\\x'` on Windows, whose `split('/')[0]` is not `'..'`,
# so a climb would pass. The wire contract is posix repo-relative, so the
# predicate must mean the same thing on every host. Rule 2 cannot cover
# this line; this comment is the only guard available.
import posixpath
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _binding  # noqa: E402
import _plugin_policy  # noqa: E402 — the `brain` switch, read at the PRODUCER
import audit_run_meta  # noqa: E402 — reused for normalize_git_remote + git helpers + atomic write
import loop_ledger  # noqa: E402 — reused for the ledger path/reader + the loop_id key formula
import loop_open  # noqa: E402 — the decision ledger's path, its line unit and a run's mark in it
from loop_tokens import _parse_iso, _FIELDS as _TOKEN_FIELDS  # noqa: E402 — the field list loop_tokens reads the token ledgers with
# ⚠️ `_loop_ledger` (leading underscore) is NOT the `loop_ledger` imported above:
# that one is the per-repo one-row-per-loop ledger, this one is the capture
# primitives the two hooks share. `loop_ledger_paths` is the READ side of the
# token ledger those hooks WRITE — the plural is the contract, not a spelling:
# the writer writes ONE ref-keyed file and every reader unions the home. That
# join used to be spelled independently at both ends, which is how a reader of
# either end learned the wrong thing about where the file lives.
from _loop_ledger import loop_ledger_paths  # noqa: E402

# Bumped 1 -> 2 by the judge-capture lane (JC1..JC5): five additive optional
# top-level keys plus one inside `iteration_timeline[]`. The server keeps
# accepting `/1` payloads — every new kwarg is defaulted there and a
# LoopExecution carrying a different contract_version is stored as-is with a
# warning, never rejected — so the bump is safe in either deploy order.
LOOP_CONTRACT_VERSION = "fm-insights.loop/2"
DECISION_CONTRACT_VERSION = "fm-insights.decision/1"
AUDIT_CONTRACT_VERSION = "fm-insights.audit/1"

# Display name (agent_type / trace `agent` field) -> {slug, model_id}. Pinned
# 1:1 with the PL-4 dispatch; the test suite asserts this table verbatim.
AGENT_ROLE_MAP = {
    "Technical Lead / Architect": {"slug": "technical-lead", "model_id": "claude-opus-4-8"},
    "Software Engineer": {"slug": "software-engineer", "model_id": "claude-sonnet-5"},
    "QA Engineer": {"slug": "qa-engineer", "model_id": "claude-sonnet-5"},
    "Code Reviewer": {"slug": "code-reviewer", "model_id": "claude-sonnet-5"},
    "Security Engineer": {"slug": "security-engineer", "model_id": "claude-sonnet-5"},
    "Debugging Specialist": {"slug": "debugging-specialist", "model_id": "claude-sonnet-5"},
}

# ---------------------------------------------------------------------------
# Small shared helpers. sanitize_ref mirrors hooks/scripts/trace-op.sh:75 —
# genuinely cross-language (the bash writer cannot be imported). _parse_iso /
# _TOKEN_FIELDS (imported above), the atomic cursor write, and the ledger
# reader + loop_id formula are IMPORTED from the same-dir stdlib siblings
# (loop_tokens / audit_run_meta / loop_ledger) so the two readers of a shared
# artifact (the token ledgers, resolved through _loop_ledger.loop_ledger_paths)
# and the two spellings of the loop_id cursor key can never drift.
# ---------------------------------------------------------------------------

def sanitize_ref(ref):
    """Byte-identical to hooks/scripts/trace-op.sh:75 — the trace filename
    for a given task_ref/agent display name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or "session"


def normalize_agent(agent_type):
    """(slug, model_id) for a display name, via AGENT_ROLE_MAP; an unmapped
    name falls back to (sanitize_ref(name).lower(), "unknown") so an unknown
    agent still gets a stable, filesystem-safe slug instead of failing."""
    entry = AGENT_ROLE_MAP.get(agent_type)
    if entry:
        return entry["slug"], entry["model_id"]
    return sanitize_ref(agent_type).lower(), "unknown"


def _read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _read_jsonl(path):
    """Every parseable row of a JSONL ledger. An absent or unreadable ledger
    degrades to empty; an undecodable byte costs only its own line
    (`loop_open.ledger_lines`), like the sibling readers."""
    return _parse_jsonl_lines(loop_open.ledger_lines(path))


def _parse_jsonl_lines(lines):
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a corrupt line never breaks the flush
    return rows


def _as_dict(x):
    """`x` if it is a dict, else `{}` — a read-boundary guard so a
    valid-JSON-but-wrong-shape value (a bare list/scalar top level, or a
    non-dict nested field) degrades instead of raising on the next `.get`."""
    return x if isinstance(x, dict) else {}


# 🔴 SHAPE GUARDS ON THE REFERENCE-TYPED FIELDS. Found by a cross-model review on
# 2026-08-14 and reproduced before fixing: `loop-state.json` is a LOCAL,
# gitignored, hand-editable file, and every projection that copies an `at` or a
# `commit_sha` off it read that value as "any non-empty string". A lifecycle
# record — or an `iterations[]` entry — carrying `at: "reviewer said ship it"` /
# `commit_sha: "customer-secret prose"` put that prose straight onto the wire, on
# the one lane whose whole promise is that only REFERENCES leave the machine.
#
# `_nonempty_str` was the wrong guard for these: it asks "is there a value", and
# the question here is "is this value the KIND of thing this field is". Every
# other free-prose key in this file is dropped by a whitelist; these are not keys
# a whitelist covers, because the whitelist ADMITS them — so the guard has to be
# on the SHAPE. A value that fails it is DROPPED, never sanitized: a reference we
# cannot recognise is not a reference.
#
# ⚠️ THEY LIVE IN THE SHARED HELPERS, AND THAT PLACEMENT IS THE ROUND-2 FIX.
# Round 1 defined them inside the JC1 transitions block and applied them to
# `transitions[]` alone — while the SAME round added `commit_sha` to
# `_ITERATION_FIELDS`, the identical value one projection away, copied verbatim.
# A guard that lives beside one of its call sites reads as that call site's
# private rule; the value CLASS is the module's, so the vocabulary is too. Both
# take an already-`_nonempty_str`-normalized value — the call site normalizes, so
# a non-string degrades to None and fails the guard instead of raising in `len`.
def _nonempty_str(value):
    """`value` stripped if it is a non-blank string, else None.

    Defined HERE, above the guards, because `_ITERATION_FIELD_GUARDS` and its
    sibling now name it as a normalizer in a module-level literal — it has to
    exist at import time, not merely by first call."""
    return value.strip() if isinstance(value, str) and value.strip() else None


_SHA_CHARS = set("0123456789abcdefABCDEF")


def _looks_like_sha(value):
    """A git object name: 7-64 hex characters and nothing else. `run_gate`
    writes a full 40, `git init --object-format=sha256` writes a 64 that sits
    exactly on the ceiling, and the lower bound tolerates a short sha a human
    may have pasted while leaving no room for a sentence.

    ⚠️ THE `isinstance` CLAUSE IS ADDITIVE AND IT IS LOAD-BEARING FOR JC13. Both
    of this predicate's original call sites normalize with `_nonempty_str`
    first, so a non-string arrived as None and `bool(value)` answered False. The
    audit door pairs it with `_as_is`, which hands it the RAW disk value, and
    `len(7)` raises `TypeError` — a guard that raises on a legal JSON number
    aborts the whole flush instead of dropping one key.

    WHERE THE CLAUSE IS INERT AND WHERE IT IS THE GUARD — re-derived from the
    code BY SYMBOL on 2026-08-20, and deliberately not by line number: the list
    this replaces cited five line numbers that the very commit which wrote them
    had already staled, and named a `_TRANSITION_*` matching no symbol in this
    module. It also aggregated the references of BOTH predicates into one count
    of "five", which is how that phantom got in; each predicate now states its
    own.
      * LOOP DOOR — `_ITERATION_FIELD_GUARDS["commit_sha"]`, `_transition_row`
        (its `commit_sha` argument) and `_loop_transitions`'s `arm_ref`. All
        three are `_nonempty_str` output, so a non-string could never have
        reached the predicate and the clause converts a crash that cannot
        happen into the False it would have returned anyway.
      * AUDIT DOOR — `_AUDIT_RUN_META_GUARDS["commit_sha"]`, paired with
        `_as_is`. This is the ONE reference where the clause can fire, because
        it is the only one handed an unnormalized disk value.
    The red for it is `test_t7_every_predicate_is_total_and_pure`, which sweeps
    every predicate over a domain containing non-strings and fails on exactly
    the `TypeError` the clause exists to prevent.

    REDUNDANT, NOT DEAD — `bool(value)`, and the distinction is worth the two
    lines: given the `7 <= len(value)` floor it can never change an answer, so
    it carries no red of its own; but remove BOTH and `""` PASSES, because
    `set("") <= _SHA_CHARS` is True (the empty set is a subset of every set).
    It is kept as the explicit statement of that, not as live coverage."""
    return (isinstance(value, str) and bool(value)
            and 7 <= len(value) <= 64 and set(value) <= _SHA_CHARS)


def _looks_like_timestamp(value):
    """An ISO-8601 instant, as `iso(now_utc())` writes it. Parsed rather than
    pattern-matched, because the point is that it IS a timestamp, and a regex
    loose enough to accept every ISO spelling is loose enough to accept prose
    that happens to start with digits.

    The leading `isinstance` clause is additive and carries the same reason as
    `_looks_like_sha`'s — see there, including why it is stated per-predicate:
    THIS one's references are `_ITERATION_FIELD_GUARDS["at"]` and
    `_transition_row` (its `at` argument) on the loop door, both
    `_nonempty_str`-fed and therefore inert, plus
    `_AUDIT_RUN_META_GUARDS["executed_at"]` on the audit door, paired with
    `_as_is` and the only place it can fire.

    THE 40-CHARACTER CEILING PREDATES JC13 (this change added only the
    `isinstance` clause) and it is NOT invented on the door it newly guards:
    `audit_run_meta._iso_now` is `datetime.now(timezone.utc).replace(
    microsecond=0).isoformat()`, whose output is 25 characters for every instant
    it can ever produce — `2026-08-20T12:00:00+00:00` — so the writer's range
    sits 15 characters inside the bound. `_parse_iso` does NOT backstop it:
    CPython's `fromisoformat` accepts an unbounded fractional part (measured on
    3.12.2 — a 100-digit fraction parses), so without the ceiling a PARSEABLE
    timestamp is an arbitrary-length channel. Its red is
    `test_t5b_every_numeric_and_length_clause_has_its_own_red`.

    The `not value` clause is inert unconditionally, not merely redundant:
    `_parse_iso` opens with its own `if not s: return None`."""
    if not isinstance(value, str) or not value or len(value) > 40:
        return False
    return _parse_iso(value) is not None


# JC12's two shape predicates, here rather than beside `_iteration_result_wire`
# for the reason the round-2 note above gives: the value CLASS is the module's,
# not one call site's.
_CHECK_ID_CHARS = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# Ceiling on an INCONCLUSIVE `value` list: one entry per `determinism.runs`, and
# the engine floors nothing above single digits. Bounded so a hand-edited
# loop-state cannot turn a measurement field into an arbitrary-length array.
_MAX_MEASUREMENT_RUNS = 32


def _looks_like_measurement(value):
    """A gate signal is a MEASUREMENT: a JSON number or boolean, and nothing
    else. Never a string.

    This is the wire half of JC12 and it is the one that holds whatever the
    producer does. `run_gate_checks.coerce_value` now refuses an unrecognised
    `value_type` at the source, but that closes only ONE of the two producers of
    `results[].value`: `evaluate_evidence` puts the verdict artifact's own
    `verdict` field there, a free string read off a JSON file an agent writes —
    reproduced 2026-08-15 shipping a whole prose sentence, and at the
    evidence-hash-mismatch site shipping a raw `dict` that was never even
    `str()`-ed. Guarding the SITE the card named would have left the other one
    open, so the predicate is enforced here, where every producer converges.

    ⚠️ THE TWO LAYERS ARE NOT TWO LAYERS FOR BOTH PRODUCERS, and saying "defence
    in depth" without this qualification overstates the coverage.
    `gate_clean_signal` is reached only from `admit_one` / `admit_guard`;
    `admit_evidence` never calls it — correctly, since an evidence check has no
    `signal` at all. So the command/guard producer has an authoring-time refusal
    AND this guard, while the EVIDENCE producer has only this one. That is the
    other reason this guard, not the vocabulary, is the load-bearing half.

    ⚠️ THIS CLOSES THE WIRE, NOT THE DISK. `loop-state.json` still records the
    evidence artifact's own verdict word in `value` — deliberately: it is what a
    human reading the file needs, and `reason` (which carries the artifact's
    notes) is dropped from the wire one level up anyway. The local file is
    governed by the CONSENT CLASSES like every other `.fairmind/**` export, not
    by this predicate.

    Measured cost on the 36 real loop-states (2026-08-15): of 516 result rows,
    506 carry an int and survive untouched; 6 carry the string `"pass"` from
    evidence checks, which restates the row's own `verdict: green` and is
    therefore dropped without losing a fact; 4 carry an explicit `null` from an
    ERROR result, which becomes absent — a shape `_iteration_result_wire`
    already emits for any key a row does not have.

    `bool` is admitted explicitly even though `isinstance(True, int)` is already
    True, because `value_type: "bool"` is a documented type and the reader should
    not have to know that Python subclasses it. Non-finite floats are refused: a
    hand-written `Infinity` parses fine through `json.load` and is not valid JSON
    on the way out.

    ⚠️ A LIST OF MEASUREMENTS IS A MEASUREMENT, and leaving it out was a FALSE
    DROP found by a cross-model review. `evaluate_check`'s INCONCLUSIVE verdict
    ships `value: [v1, v2, …]` — the differing per-run values that made the check
    non-deterministic, which is the whole content of that verdict. A scalar-only
    predicate silently deleted it, and the corpus could not catch that (all 516
    rows are settled ones). Every MEMBER must itself be a measurement, and the
    list is bounded: it holds one entry per `determinism.runs`, so anything long
    is not that record.

    The list case calls `_is_scalar_measurement`, NOT itself: recursing accepts
    `[[1]]`, because the inner list is a valid list-of-measurements. Caught while
    writing the test that pins the list case — nesting is exactly the kind of
    shape a shape guard exists to refuse."""
    if isinstance(value, list):
        return (0 < len(value) <= _MAX_MEASUREMENT_RUNS
                and all(_is_scalar_measurement(v) for v in value))
    return _is_scalar_measurement(value)


def _is_scalar_measurement(value):
    """One JSON number or boolean. No containers, no strings, nothing infinite.

    ⚠️ `int` IS ANSWERED WITHOUT `math.isfinite`, and that is not tidying: Python
    ints are unbounded, and `math.isfinite(10**1000)` raises `OverflowError`
    converting to float. A guard that RAISES on a legal JSON number aborts the
    whole flush instead of dropping one key — a payload lost to a shape guard is
    strictly worse than the shape it was refusing. An int is finite by
    construction; only a float can be inf/nan."""
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _looks_like_check_id(value):
    """A check id as the descriptor authors write them: a bounded token, no
    whitespace. All 516 real result rows pass it (`i4-ac4-presentation`,
    `t2c2-wire`); a sentence pasted into a hand-edited loop-state does not.

    The failure mode is the KEY, not the row: `verdict` and `value` are still
    data without it, and what is lost is the join back to `checks[]` — the same
    trade `commit_sha` already makes."""
    return bool(value) and bool(_CHECK_ID_CHARS.match(value))


# The gate's own closed verdict vocabulary (`run_gate_checks` GREEN / RED /
# ERROR / INCONCLUSIVE). Not imported from there on purpose: this is the wire's
# statement of what it will ship, and a guard that reads its allowed set from
# the module it is guarding can only confirm that module agrees with itself.
_WIRE_VERDICTS = ("green", "red", "error", "inconclusive")


def _is_wire_verdict(value):
    """One of the four verdicts the gate can produce, and nothing else."""
    return value in _WIRE_VERDICTS


# ---------------------------------------------------------------------------
# JC13 — the AUDIT door's value classes. Same block and same rule as the loop
# door's guards above: the value CLASS is the module's, not one call site's.
#
# 🔑 THE STOPPING RULE TWO DEAD DESIGNS PAID FOR. A field gets a predicate ONLY
# when this paragraph can be written for it: "writer W confines the value to S
# because <structural reason>; predicate P contains S because <structural
# reason>." A field for which it cannot is DECLARED OPEN and carries no
# predicate at all. That is why 13 of the audit door's 21 disk-sourced values
# are guarded and 8 are not — declaring a field open cannot move a byte, a
# guard can, so every close call falls to open.
#
# Round 1 died asserting a non-empty netloc on `git_remote`, which
# `normalize_git_remote` really writes empty (`file:///srv/Repo.git` ->
# `https:///srv/Repo`). Round 2 died on the OTHER half: `_nonempty_str` as a
# normalizer rewrote ` leadspace`, a legal APFS directory name, while its
# predicate happily accepted it. Different axes, one error — an unproven
# containment claim wearing the authority of a sample.


def _is_count(value):
    """A non-negative JSON integer. `bool` is refused explicitly: `True` is an
    `int` in Python and `passed: true` is not a count.

    NO UPPER BOUND, deliberately, and only on the two fields that keep it —
    `pillars[].level` and `criteria[].level`. `validate_catalog` accepts any
    `int >= 1` for a criterion level and `compute_ladder_level` derives the
    pillar level from those, so there is no container whose length bounds them
    and therefore no ceiling that could be DERIVED rather than invented. The
    residual that leaves is real and is enumerated at `_is_bounded_count`."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# The ceiling on the four COUNTERS, and it is derived rather than chosen: each
# of `totals.criteria`, `totals.passed`, `pillars[].criteria_passed` and
# `pillars[].criteria_total` is an integer initialised to 0 and incremented once
# per element of a list parsed out of a JSON file (`harness_audit.evaluate_
# catalog`), and no Python container can hold more than `sys.maxsize` elements.
# So the writer's range is a subset of [0, 2**63-1] by CONSTRUCTION.
#
# 🔴 "AN INT CARRIES NO PROSE" IS FALSE, MEASURED, and it was the argument for
# leaving these unbounded: `int.from_bytes(b"ACME lost the Q3 renewal, do not
# tell the board", "big")` is a 113-digit non-negative integer that a bare
# `>= 0` accepts, that `json.dumps` emits, and that `int.to_bytes` decodes back
# to the sentence verbatim. An unbounded integer is a content channel and an
# arbitrary-length payload on a 30,000-byte wire. Same precedent, same reason,
# as `_MAX_MEASUREMENT_RUNS` fourteen hundred lines up.
_MAX_AUDIT_COUNTER = 2 ** 63 - 1


def _is_bounded_count(value):
    """A counter: `_is_count` plus the DERIVED ceiling above.

    Only the four accumulators get it. Applying it to the two `level` fields
    would be inventing a bound, which is the class of claim rounds 1 and 2 died
    for lacking.

    THE LENGTH RESIDUAL AFTER THIS BOUND, ENUMERATED RATHER THAN ARGUED AWAY —
    prose and SIZE are different channels and the door still carries three
    unbounded values: `git_remote` (a hand-edited 250 KB value yields a
    259,729-byte payload on a channel the module docstring says truncates
    silently at 30,000), `pillars[].level` and `criteria[].level`. What is NOT a
    reachable residual, corrected from an earlier claim: a >4,300-digit integer
    cannot reach `json.dumps` through any of the three disk files, because
    Python's own int-parse limit makes `json.load`/`json.loads` raise first and
    `_read_json`/`_read_jsonl` degrade on `ValueError`."""
    return _is_count(value) and value <= _MAX_AUDIT_COUNTER


def _is_unit_score(value):
    """A Loop Readiness dimension score: None, or a number in [0, 1].

    ⚠️ THE None BRANCH IS FIRST AND IS LOAD-BEARING. `harness_audit._probe_test_
    determinism` returns None when the repo configures no test command, and the
    row that reaches disk is `{"id": "test-determinism", "score": null,
    "status": "not-probed"}` — a REAL producer row on every repo without one. A
    number-only predicate would drop the key on producer data.

    Containment for the rest: a fold score is `passed / total` with
    `0 <= passed <= total` and `total >= 1` guaranteed by validation, so it lies
    in [0, 1]; the probe returns only 1.0 or 0.0."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and 0.0 <= value <= 1.0


# The audit engine's three closed vocabularies, STATED BY THE WIRE and never
# imported from `harness_audit` — same rule `_WIRE_VERDICTS` states above: a
# guard that reads its allowed set from the module it is guarding can only
# confirm that module agrees with itself.
#
# ⚠️ SEPARATE FROM `_WIRE_VERDICTS` ON PURPOSE. Collapsing the two would admit
# `green` on a criterion row, and — far worse — reusing the gate's four-verdict
# vocabulary as a ROW guard here would reject all 81 criteria, empty the array,
# omit the key, and leave `totals` still saying 81.
#
# DRIFT IS THE COST OF STATING IT HERE: a producer that grows a sixth dimension
# or a fifth status would have the key silently dropped on producer data. That
# is closed by the three-way AST tripwire in
# `tests/test_jc13_audit_shape_guards.py::test_t6_...`, which asserts the
# producer's own literals, these tuples, and the test's own literals are equal.
_WIRE_DIMENSION_IDS = ("oracle-coverage", "test-determinism", "signal-quality",
                       "ci-gates", "traceability")
_WIRE_DIMENSION_STATUSES = ("not-probed", "clean", "weak", "absent")
_WIRE_AUDIT_VERDICTS = ("pass", "fail")


def _is_dimension_id(value):
    """One of the five fixed Loop Readiness dimension ids.

    CATALOG-INDEPENDENT, which makes this the strongest containment argument on
    the door: `evaluate_dimensions` builds `dimension_by_id` keyed on `d["id"]`
    and then looks up `FIXED_DIMENSION_IDS` members, so the emitted id is always
    the lookup key itself no matter what a custom `--catalog` declares."""
    return value in _WIRE_DIMENSION_IDS


def _is_dimension_status(value):
    """One of the four `harness_audit._dimension_status` can return. That
    function is total (an unconditional final `return "absent"`), has exactly
    four `Return` nodes, and all four are string constants."""
    return value in _WIRE_DIMENSION_STATUSES


def _is_audit_verdict(value):
    """`pass` or `fail`, and nothing else.

    Containment, stated accurately because an inaccurate construction argument
    is what killed the prior rounds: `evaluate_criterion` has a single return
    that dispatches through `_EVALUATORS`, whose three entries are keyed by the
    validated `ALLOWED_PRIMITIVES`; across those three evaluators every
    verdict-producing site is a `"pass"`/`"fail"` string constant. It is NOT
    "three ternaries" — `_eval_manifest_token` uses four explicit `return`
    statements."""
    return value in _WIRE_AUDIT_VERDICTS


def _is_https_ref(value):
    """A normalized git remote: the `https://` PREFIX, and after it nothing is
    asserted. The WEAKEST predicate that is provable, on purpose.

    🔴 THE OPEN CHARSET IS THE MEASUREMENT, NOT A SHORTCUT — this is the field
    that killed round 1 and then killed its repair. `normalize_git_remote`
    returns None or `urlunsplit(("https", netloc, path, "", ""))`, and CPython's
    `urlunsplit` takes `if netloc or (scheme and scheme in uses_netloc and
    url[:2] != "//")`; `https` IS in `uses_netloc`, so either that branch
    prepends `//` or the url already starts `//`. The output ALWAYS starts
    `https://`, and that is a claim about the WRITER'S CONSTRUCTION that no
    corpus could have supplied. Everything after the prefix is arbitrary, run
    against the real producer rather than reasoned:
      * a local bare clone      -> `https:///var/folders/.../up`  (EMPTY netloc)
      * a `file://` origin      -> `https:///srv/Repo`
      * `~/My Projects/sp.git`  -> `https:///.../My Projects/sp`  (A SPACE)
      * `../rel`, `~/repos/foo` -> `https://../rel`, `https://~/repos/foo`
    Round 1 asserted a non-empty netloc and dropped the first form; its repair
    asserted a URL charset and dropped the third.

    `_as_is` is its normalizer, never `_nonempty_str`, and that too is measured:
    `normalize_git_remote("/tmp/My Dir /")` returns `"https:///tmp/My Dir "` —
    the `rstrip("/")` leaves the space — so `.strip()` really would move bytes.

    RESIDUAL, declared rather than glossed: `https://` followed by prose crosses
    (`https://reviewer said ship it`), and there is NO LENGTH BOUND, so a
    hand-edited 250 KB value crosses too. No writer's construction yields a
    ceiling here, and an underivable bound is exactly the class of claim that
    killed both prior rounds."""
    return isinstance(value, str) and value.startswith("https://")


def _guarded_scalar(guards, key, value):
    """One scalar through one `(normalize, predicate)` pair — the non-row half
    of `_project_row`, for the audit door's top-level values.

    `_project_row` is deliberately NOT used at the top level: that record mixes
    three disk sources with three different absent-shapes (key retained and
    nulled, key omitted, key omitted-when-falsy) and `_project_row` knows one
    omit rule. Returning None here lets each call site keep the absent-shape it
    already had, which is what makes this change omit-vs-null neutral."""
    normalize, predicate = guards[key]
    value = normalize(value)
    return value if predicate(value) else None


def _as_is(value):
    """The normalizer for a guard that TYPES ITS OWN INPUT — see `_project_row`
    for why every guard now declares one rather than the loop assuming it."""
    return value


def _project_row(row, fields, field_guards, row_guards=None):
    """Project one hand-editable disk record down to its wire shape, or None
    when the record itself must not ship. THE ONE PROJECTION LOOP.

    ⚠️ IT IS ONE FUNCTION BECAUSE IT WAS TWO COPIES, AND THE SECOND IS HOW JC12
    GOT ITS `if key == "id"` BRANCH. (`_transition_row` is a third hand-written
    loop and deliberately stays one: it constructs a row from named fields rather
    than walking a whitelist, so it has no map to share.) `_iteration_wire` and
    `_iteration_result_wire` were the same loop line for line — whitelist tuple,
    `key not in row: continue`, guard lookup, normalize, `continue` on failure,
    assign — differing only in which tuple and which map. A projection that has
    to be re-typed for each new level is a fourth chance to forget the
    whitelist/guard pairing, which is the identical defect JC1 and JC12 both
    were.

    🔑 A GUARD IS A `(normalize, predicate)` PAIR, AND THAT PAIR IS THE FIX FOR A
    LATENT TRAP, not tidying. The old loops normalized with `_nonempty_str`
    UNCONDITIONALLY, which silently assumes every guard is string-shaped. It was
    already false: `_looks_like_measurement` must NOT be normalized, since
    `_nonempty_str(0)` is None and would drop every real value on the lane —
    which is exactly why the third copy grew a key-name branch. The same trap sat
    armed one level up: adding any non-string guard to `_ITERATION_FIELD_GUARDS`
    (a bound on `n`, say) would have dropped every real value there too, silently
    and with a green suite. Declaring the normalizer beside the predicate makes
    that impossible to get wrong by omission — a guard says what it eats.

    `row_guards` fail the WHOLE record; `field_guards` fail only their own key.
    Which a field gets is a decision per level, recorded at the map."""
    for key, (normalize, predicate) in (row_guards or {}).items():
        if not predicate(normalize(row.get(key))):
            return None
    wire = {}
    for key in fields:
        if key not in row:
            continue
        value = row[key]
        guard = field_guards.get(key)
        if guard is not None:
            normalize, predicate = guard
            value = normalize(value)
            if not predicate(value):
                continue
        wire[key] = value
    return wire


def _git_toplevel(cwd):
    """The git work-tree root containing `cwd`, else `cwd`'s own absolute path.

    ONE WRITER PER FACT: this is the file's only `rev-parse --show-toplevel`
    call. It had two, ~550 lines apart with the same fallback shape — the
    decisions payload's `repository` (then a private `_repository_name`, since
    deleted: `build_decisions_batches` resolves the toplevel ONCE and takes its
    basename, because it needs the root itself for the path projection) and the
    CLI's `_consent_config_root` — and two copies of "where is the repo root" is
    two places a degrade can be fixed in one and not the other. It already had been:
    only one of them checked that a returncode-0 `git` actually printed a path,
    so the other resolved an empty stdout to `os.path.normpath("")` == `"."` and
    reported the repository as literally `"."`. Unified on the checked form.

    Not `audit_run_meta.collect_run_meta`: that reads the wall clock and raises
    where this module must degrade (see `_loop_transitions`)."""
    toplevel = audit_run_meta._run_git(cwd, "rev-parse", "--show-toplevel")
    if toplevel.returncode == 0 and toplevel.stdout.strip():
        return os.path.normpath(toplevel.stdout.strip())
    return os.path.abspath(cwd)


# ---------------------------------------------------------------------------
# Path resolution — base_path is per-loop, everything else is fixed per-repo.
# ---------------------------------------------------------------------------

def _active_context(root):
    """The marker for this checkout, read at `root`.

    ⚠️ `root` IS THE GIT TOPLEVEL, and passing anything else is the bug this
    parameter exists to make visible. The file lives at the repo root by
    definition — every writer puts it there and every document says so — so
    reading it relative to the CALLER'S directory meant a flush issued from a
    subdirectory saw no marker at all: no `task_ref`, no project, and since PX2
    no binding either, which reads exactly like an unconnected checkout. The
    judge Stop hook anchors at the toplevel for the same reason.

    IT IS A PARAMETER RATHER THAN A `_git_toplevel()` CALL IN HERE, and that is
    not a style choice. Every builder in this module already holds the resolved
    root, `build_loop_payload`'s docstring states it "makes zero git/config/env
    calls", and `build_decisions_batches`'s says its net new git calls are zero —
    resolving in HERE made all three false, for a value the callers were
    already holding. MEASURED on this repository, one `--emit all` over the
    three builders, counting invocations through a logging `_run_git`: 4 git
    subprocesses before PX2, 10 with the resolution buried in this function, 3
    with it threaded from the callers as it is now."""
    return _as_dict(_read_json(os.path.join(root, ".fairmind", "active-context.json"), {}))


def _resolve_base(root, base=None):
    """The loop's `base_path`, from an explicit `base` or the marker at `root`.
    `root` is the git toplevel, for the reason `_active_context` gives."""
    if base:
        return base
    return _active_context(root).get("base_path") or ".fairmind"


# The three spellings `.fairmind/active-context.json` has been written with,
# MOST-AUTHORITATIVE first. One reader, tried in order, so no writer's spelling
# can silently zero the project again — and so `build_loop_payload` and
# `build_audit_payload` can never disagree about which key holds it.
#
# 🔑 `project_id` LEADS SINCE PX2, and the order is the whole point rather than
# a tidy-up. The server keys projects by Mongo ObjectId; `project` holds what
# `/fairmind-loop` Phase 0 writes, which is the REPOSITORY FOLDER NAME, and a
# folder called `payments-api` never equals a project called "Payments
# Platform" — so for as long as the folder name led, the ordinary connected
# checkout resolved to nothing at all and its decisions lost their function
# links before any match was attempted. `project_id` is the
# spelling `/fairmind-connect` writes from the bind answer, and the spelling
# `agents/tech-lead.md` already tells the orchestrator to merge in connected
# mode; both carry the ObjectId the server can actually resolve. `projectId` is
# what this module read before T1·X1 (so every real loop payload carried the
# literal "unknown" for its whole life) and stays last, for the checkouts that
# still hold it.
#
# CONSEQUENCE, STATED RATHER THAN DISCOVERED: a checkout that already carries
# BOTH — the tech-lead's `project_id` and Phase 0's `project` — changes what it
# sends the moment this order does, without anyone running the new command. It
# starts sending the identifier the server can resolve instead of one it never
# could, which is the fix and not a side effect of it; but it is a change in
# emitted bytes on an existing checkout, so it is named here.
_PROJECT_KEYS = ("project_id", "project", "projectId")


def _project(ctx):
    """The project named by active-context.json, or None when it names none.

    None means ABSENT, and every caller must omit its key rather than invent
    one — see the module docstring's correlation-keys paragraph. (The loop
    payload is the one exception: its `project_id` wire key predates T1·X1
    with a documented `"unknown"` default on both sides of the wire.)
    """
    for key in _PROJECT_KEYS:
        value = ctx.get(key)
        if value:
            return value
    return None


def _bound_repository(ctx, current_remote):
    """The code-ingestion catalog id `/fairmind-connect` bound this checkout
    to, or None when it was never connected — or when the binding no longer
    describes this checkout.

    WHY THE ID TRAVELS IN THE `repository` FIELD RATHER THAN A NEW ONE. The
    consumer's three record tools have a CLOSED keyword set — there is no
    `repository_id` parameter to send one on, and inventing one would be a
    two-repository wire change for a value the door already accepts. Its
    `repository` parameter is documented "name or ID" and the server's matcher
    tries an EXACT id match on it first (`_match_repository`, rung 1), so a
    bound checkout simply names itself by the identifier that resolves, and an
    unbound one keeps sending exactly the bytes it sent before. That last half
    is why this is a `None`-returning reader and not a default: the sealed wire
    fixtures stage no binding, and they must keep passing untouched.

    The rule itself lives in `_binding`, not here — three modules answer this
    question and the first round of PX2 gave each of them its own copy, which
    diverged inside one change."""
    return _binding.repository_id(ctx, current_remote)


def _owner_session(root, base=None):
    """The session id that owns the loop under `base`, or None when there is
    no loop (a standalone `/harness-audit`) or it has none (post `--arm`/
    `--recover`). Same `loop-state.json` value `build_loop_payload` emits as
    `owner_session` and `build_decisions_batches` as the closing loop's own
    batch's `session_ref`."""
    state = _as_dict(_read_json(
        os.path.join(root, _resolve_base(root, base), "loop-state.json"), {}))
    return state.get("owner_session")


def _trace_path(cwd, ref):
    return os.path.join(cwd, ".fairmind", "trace", sanitize_ref(ref) + ".jsonl")


def _decisions_path(cwd):
    return os.path.join(cwd, loop_open.DECISIONS_LEDGER_REL)


def _cursor_path(cwd):
    return os.path.join(cwd, ".fairmind", "insights-sync.json")


def _inflight_path(cwd):
    return os.path.join(cwd, ".fairmind", "insights-inflight.json")


def _audit_run_meta_path(cwd):
    return os.path.join(cwd, audit_run_meta.DEFAULT_OUT)


def _audit_summary_path(cwd):
    return os.path.join(cwd, ".fairmind", "audit", "summary.json")


def _audit_assessment_path(cwd):
    return os.path.join(cwd, ".fairmind", "audit", "assessment.jsonl")


# ---------------------------------------------------------------------------
# Loop payload
# ---------------------------------------------------------------------------

def _ledger_row_for(cwd, target_ref):
    """The LAST loop-ledger.jsonl row whose `task` == target_ref, or None.
    Reads via loop_ledger (which owns the ledger path + reader) so the flush
    and the ledger can never disagree on the file or its parse."""
    rows = [r for r in loop_ledger._read_rows(cwd) if r.get("task") == target_ref]
    return rows[-1] if rows else None


def _loop_identity(cwd, state):
    """(loop_id, closed_at) for the closed loop this state names — the loop's
    identity, cheaply: the LAST matching ledger row on the happy path, else the
    loop_ledger._loop_id(state) fallback (the SAME formula the ledger row was
    written with, so both paths key the cursor identically) with closed_at
    falling back to started_at. Touches only loop-state + the ledger, so a
    --commit needn't rebuild the whole payload just to key the cursor.
    `state` is re-coerced here (every call site already reads it via
    _as_dict, but this keeps the function safe to call standalone) so a
    non-dict `target`/`budget` degrades instead of raising."""
    state = _as_dict(state)
    target_ref = _as_dict(state.get("target")).get("ref")
    row = _ledger_row_for(cwd, target_ref)
    if row is not None:
        return row.get("loop_id"), row.get("closed_at")
    started_at = _as_dict(_as_dict(state.get("budget")).get("spent")).get("started_at")
    return loop_ledger._loop_id(state), started_at


def _loop_agents(cwd, base, started_at, closed_at, trace_rows):
    """One entry per distinct agent_type seen in the token ledgers under <base>,
    over rows whose `ts` falls in [started_at, closed_at], token-summed over that
    window and enriched with a trace-derived tool-call count windowed the
    SAME way — a revived loop's shared token/trace files would otherwise leak
    stats from outside this close into the count."""
    start = _parse_iso(started_at)
    end = _parse_iso(closed_at)
    # THE SET, NOT ONE FILE. `_loop_ledger.loop_ledger_paths` owns and states the
    # rule — writer writes one ref-keyed ledger, every reader unions the home
    # (keyed names plus the LEGACY unkeyed one, which holds real captured rows on
    # every repo that ran a loop before the key changed) — and this site cites it
    # rather than restating it. Resolving one keyed name here instead would drop
    # both the legacy rows and the rows of any context that wrote under a ref
    # other than the loop's own `target.ref`, which the identity predicate
    # permits by design when the pair is undecidable.
    #
    # ⚠️ IT SUPPLIES A FALLBACK THIS SITE DID NOT HAVE, AND THAT IS A REAL
    # DIFFERENCE ON A FALSY `base` — declared rather than normalized silently.
    # The bare join gave `<cwd>/subagent-tokens.jsonl` for `base=""` and raised
    # TypeError for None/0/False; the constructor resolves all of them under
    # `<cwd>/.fairmind/`. The default was always meant to apply here — it simply
    # arrived from `_resolve_base`, a caller away, which is why this looked like
    # the one site with no fallback. `build_loop_payload` is the only caller
    # (checked across the plugin and the repo scripts) and it runs `_resolve_base`
    # first, which cannot return a falsy value, so no reachable call changes
    # path. A caller that hands this function an empty `base` directly now reads
    # the workspace default.
    token_rows = []
    for path in loop_ledger_paths(cwd, base, "subagent-tokens.jsonl"):
        token_rows.extend(_read_jsonl(path))

    def _in_window(ts_raw):
        ts = _parse_iso(ts_raw)
        return ts is not None and start is not None and end is not None and start <= ts <= end

    sums = {}  # agent_type -> {field: total}
    for row in token_rows:
        if not isinstance(row, dict):
            continue  # a malformed ledger row never aborts the whole rollup
        agent_type = row.get("agent_type")
        if not agent_type or not _in_window(row.get("ts")):
            continue
        tot = sums.setdefault(agent_type, {k: 0 for k in _TOKEN_FIELDS})
        for k in _TOKEN_FIELDS:
            value = row.get(k, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                tot[k] += value  # a non-numeric field is skipped rather than crashing the sum

    tool_calls = {}
    for row in trace_rows:
        if not isinstance(row, dict):
            continue  # same guard the token ledger above already had (T2-C2)
        agent_type = row.get("agent")
        if agent_type and _in_window(row.get("ts")):
            tool_calls[agent_type] = tool_calls.get(agent_type, 0) + 1

    agents = []
    for agent_type, tot in sums.items():
        slug, model_id = normalize_agent(agent_type)
        agents.append({
            "agentRole": slug,
            "modelId": model_id,
            "inputTokens": tot["in"],
            "outputTokens": tot["out"],
            "cacheReadTokens": tot["cache_read"],
            "cacheCreationTokens": tot["cache_creation"],
            "toolCalls": tool_calls.get(agent_type, 0),
        })
    agents.sort(key=lambda a: a["agentRole"])
    return agents


# ---------------------------------------------------------------------------
# THE CORPUS these projections were measured against, so the numbers below can
# be re-derived rather than believed.
#
# Method: every `loop-state.json` under the six working repos on this machine,
# parsed and counted, on 2026-07-26. It is LOCAL WORKING STATE, not shipped —
# `.fairmind/` is gitignored in this repo, so no test can read it and nobody
# else's checkout reproduces it. Re-measure before trusting these numbers, and
# restate the date when you do.
#
#   34 loop-state files · 211 `iterations[]` entries · 0 non-dict entries
#   entry shapes: 163 × {n, at, results[]}  +  48 × {at, event}
#   210 of 211 carry `at`
#   501 result rows, ALL of them exactly {id, verdict, value}
#
# (A previous revision of these comments claimed "223 entries in 36
# loop-states". Measured two independent ways on the date above it is 211/34.
# Loop-state files are live, so the old number is "no longer reproducible"
# rather than invented — which is exactly why the method and the date are
# recorded here now.)
# ---------------------------------------------------------------------------

# The four fields projected out of a loop-state `iterations[]` entry (T2-C2).
# Everything else the gate writes there stays local: `mutation_signature` is
# 89% of the bytes on the worst real loop on disk (30,057 B of 33,613 B) and
# is a list of file paths + content hashes — the gate's own change-detection
# mechanism, not an analytics signal. `prev_status`/`started_at`/`in_flight`/
# `confirmations_reset_from` are gate bookkeeping. The rework signal lives in
# `artifact_mutations` (below), not in the mutation signature.
#
# The whitelist is the PRIVACY boundary as much as the size one: the keys it
# drops carry free prose on real data — a human-gate rejection paragraph, a git
# command line with a sha, an operator's own words (in whatever language they
# typed) — none of which anything server-side filters. Widening it is a wire
# change, and `test_t2c2_gate_internals_never_reach_the_wire` stages every one
# of them so widening it turns red.
#
# NO LITERAL COUNT OF THE DROPPED SET IS WRITTEN HERE, deliberately. This
# comment said "the eight keys it drops" while the checker's ban list held
# thirteen — a count that closes a set nobody enumerated, which is a failure
# mode this repo has already paid for. The set is named in exactly one place
# (`_GATE_INTERNAL_KEYS` in the checker) and derived from there.
#
# JC1 widens this by EXACTLY ONE key, `commit_sha` — the git object id of the
# tree the gate evaluated, written by `run_gate` at evaluation time. It has no
# free-text surface at all: machine-generated, fixed-alphabet, fixed-length,
# and the producer OMITS it rather than emitting an error string when git
# fails. Note specifically that `error` — which carries a git command line AND
# a full sha — stays dropped: a sha is safe, the command line that produced it
# is not, and the new key carries the former without the latter.
#
# ⚠️ D2 NOW BINDS BOTH HALVES — AND THE HALF THAT IS STILL NOT CLOSED IS NAMED
# BELOW, BECAUSE THE UNACCEPTABLE STATE IS THE SILENT ONE. Owner decision
# 2026-08-20 (JC11): D2 — timestamps on agent-authored records only, a
# human-originated record carries sequence position and no clock — binds the loop
# lane. `human_gate[]` obeyed it already; the `arm` audit entry did not, and now
# does (`_ITERATION_D2_CLOCKLESS_EVENTS` below, and `_D2_CLOCKLESS_TRANSITIONS`
# for the same verb's other clock). Its sequence position survives as its
# POSITION in the ordered timeline. Every other entry shape keeps its clock: an
# iteration and an engine-written audit row (`scope_violation`,
# `worktree_degraded`) are agent-authored, and declocking them would destroy
# time-to-first-green and cadence, the two measurements T2-C2 exists for.
#
# 🔵 WHAT THE REMOVAL ACTUALLY BUYS, MEASURED. `budget.spent.first_armed_at` is
# write-once and survives every re-arm, so the `transitions[].arm` clock never
# carried a RE-ARM instant at all — these audit rows are the only carrier.
# Corpus 2026-08-20, every `loop-state.json` under `~/Projects/**/.fairmind/**`
# (36 files; `.fairmind/` is gitignored, so no test can read this corpus —
# re-run the glob and restate the date rather than trusting these numbers): 45
# `arm` rows, all 45 carrying `at`, across 23 files — 9 files with one arm, 8
# with two, 4 with three, 2 with four, i.e. 22 RE-ARMS beyond the first. A
# re-arm's `at` minus `lifecycle.gate_green.at` is exactly how long a named
# developer deliberated before rejecting, joinable to `owner_session`. That is
# the measurement that stops shipping, and it is the one the card is about.
# (The card itself says "19 re-arm reali nei 37 loop"; disk on 2026-08-20 says
# 22 across 36 files. Re-measured, not transcribed.)
#
# 🔴 WHAT STILL SHIPS THE FIRST ARM'S INSTANT — VERBATIM, TWICE, AFTER THIS
# CHANGE. Not derived, not bracketed: the same string. A FRESH `--arm` stamps
# `budget.spent.started_at` and then `budget.spent.first_armed_at` from THAT ONE
# value (`run_gate_checks.py`, the fresh-arm branch), and they are EQUAL on 22 of
# the 22 corpus files carrying both — 0 exceptions, 0 files carrying
# `first_armed_at` without `started_at`. That instant leaves this machine as the
# top-level `started_at` (`consent_class_map.json` gives it `classes: []`, so no
# grant withholds it) and again inside `loop_id`, `f"{ref}@{started_at}"`, which
# is the ledger's dedup key and the server's identity for the run. SO: for a
# FIRST arm this change removes a duplicate, not the fact; for a RE-ARM it
# removes the only copy. Closing that half is its own card and its own cost —
# `started_at` is the window `_loop_agents` attributes tokens inside, and
# `loop_id` is an identity two repos dedup on, so it is a migration rather than a
# field removal — and it is not a rider on this one. It is written here so that
# nobody has to rediscover it, and it is ASSERTED rather than merely written:
# `test_jc4_human_gate_rows_carry_no_clock_and_no_prose` pins both surfaces.
#
# 🔵 THE THIRD-ORDER RESIDUAL, ALSO ACCEPTED. The rows on either side are
# agent-authored, keep their clocks, and BRACKET the human's moment. On the
# committed conformance fixture the arm sits at 21:02 between `started_at` 21:00
# and the first evaluation at 21:14, so an analyst can still state "the arm
# happened inside this 14-minute window" and, for a re-arm, "the human took at
# most the gap between the evaluation before it and the one after".
#
# 🔴 THAT BOUND IS USUALLY WIDER THAN THE INSTANT AND SOMETIMES IS THE INSTANT,
# and the difference is measured rather than assumed. An earlier revision of this
# paragraph said the bound "is not joinable to a second" — a universal, and one
# real row refutes it. MEASURED 2026-08-20 over the 45 `arm` rows in the 36
# loop-states under `~/Projects/**/.fairmind/**`: bracket widths run 42 s to
# 43,119 s (median 1,228 s), and on **1 of the 22 re-arms** an ADJACENT
# agent-authored row shares the arm's second exactly, so for that one the bound
# collapses and the instant is on the wire verbatim. The case is
# `loop-i5`, re-arm at `2026-07-18T10:18:30+00:00` out of `blocked_scope`,
# immediately followed by a `scope_violation` stamped the same second — the
# agent's next Stop re-detecting the still-unresolved path. `--arm` does not
# write that row, so this is a recurring SHAPE and not a collision: any re-arm
# whose cause the next evaluation re-detects immediately can reproduce it.
# It is left open, like the rest of this residual, but it is NOT understated —
# understating it is the exact failure this whole block exists to prevent, and a
# claim of "never" about a value that leaves the machine is the kind of sentence
# the next reader designs against.
# `test_t2c2_iteration_timeline_is_reconstructable` computes the FIRST-ARM
# window from the payload alone, so that half is checked rather than asserted;
# the re-arm bracket above is measured here and asserted by no test. Closing it would
# mean declocking the whole timeline or dropping the arm row: the first destroys
# time-to-first-green, the second destroys the re-arm signal JC4 exists to
# capture. Both pay a capability the product needs for a bound that data which
# MUST ship already approximates. That trade is the decision, not an oversight.
#
# ⚠️ AND THE SEQUENCE POSITION D2 ASKS FOR IS WEAKER HERE THAN ON `human_gate`,
# which is stated rather than glossed. The emitted row is `{"event": "arm"}` — no
# `n`, only its index in this list — and class-B/C withholding removes its
# NEIGHBOURS, so under a narrowed grant it has no position relative to any
# iteration at all. `human_gate[]` carries an explicit `after_iteration_n` and
# does not degrade that way. Adding `n` here would be a byte move this card did
# not decide, so it is named, not fixed.
#
# 🔴 AND THE PART THIS CARD DELIBERATELY DID NOT TAKE. `arm` is not the only
# human-invoked verb the engine writes an audit row for: `hold`, `release`,
# `recover` and `extend_budget` are CLI verbs a person runs (`--recover` and
# `--extend-budget` refuse to fire without `--user-confirmed`), and each appends
# an entry carrying `at`. D2 reaches them by the same reasoning it reaches `arm`.
# They are NOT in `_ITERATION_D2_CLOCKLESS_EVENTS` because widening a wire change
# past the card that authorised it is how a scope becomes unreviewable — and
# because the corpus above contains ZERO rows of all four (45 arm, 5
# scope_violation, 1 human_gate_reopen, and nothing else, 2026-08-20), so the
# omission costs nothing on any data that exists today. `human_gate_reopen` is
# human-originated too and its single corpus row already carries no `at` — 1 of
# 1, i.e. it satisfies D2 BY ACCIDENT, which is a rule the next writer can break
# without failing anything. All of it is a card, not a silence.
_ITERATION_FIELDS = ("n", "at", "event", "results", "commit_sha")
# 🔴 THE PER-KEY SHAPE GUARD, DECLARED BESIDE THE WHITELIST RATHER THAN BURIED IN
# THE PROJECTION. Found by a cross-model review 2026-08-14 and reproduced: JC1
# added `commit_sha` to the tuple above and `_iteration_wire` copied it VERBATIM,
# so a hand-edited `iterations[].commit_sha` of `"customer-secret prose"` reached
# the wire — while the IDENTICAL key on `transitions[]`, added in the same round,
# was guarded. Nothing catches it downstream either: the server's
# `iterationTimeline` is `Optional[List[dict]]` with no second filter behind it.
#
# THE DEFECT IS THE SHAPE OF THE OLD CODE, NOT THE MISSING LINE. A whitelist that
# ADMITS a key and a projection that COPIES it are two statements, and only the
# first was reviewed when the key was added. A key listed above with no entry
# here is admitted UNGUARDED — so the pairing is what a new key has to decide,
# and `test_no_free_string_reaches_the_wire_through_a_shape_the_guard_does_not_
# check` asserts every `_ITERATION_FIELDS` member is either guarded here or named
# in a hand-written no-guard literal IN THE TEST FILE. Hand-written on purpose:
# import that list from this module and the next key added above exempts itself,
# and round 3 of this same defect ships green.
#
# The three unguarded members, each a decision rather than an oversight:
#   * `n`       — the gate's own monotone integer counter, not a string. It is
#                 the `human_gate[].after_iteration_n` join key, and a guard that
#                 dropped it would break the join to save nothing.
#   * `results` — projected through its OWN whitelist
#                 (`_ITERATION_RESULT_FIELDS`) one level down, which is where its
#                 shape rule belongs — and since JC12 that level carries a real
#                 guard map (`_ITERATION_RESULT_FIELD_GUARDS` /
#                 `_ITERATION_RESULT_ROW_GUARDS`) rather than only the promise of
#                 one. Until then this sentence pointed at a projection that
#                 copied `value` verbatim off the check's own stdout.
#   * `event`   — ⚠️ A GENUINELY OPEN FREE-STRING CHANNEL, STATED RATHER THAN
#                 CLOSED. The names `run_gate_checks.py` writes today, grepped
#                 2026-08-20 rather than remembered, are `arm`, `hold`,
#                 `release`, `recover`, `extend_budget`, `scope_violation` and
#                 `worktree_degraded`, and no other script in this plugin writes
#                 an `"event"` key at all. NO count is given: this sentence read
#                 "at least nine names" and listed `mutation_attribution` and
#                 `human_gate_reopen` among them, which are real values ON DISK
#                 (they appear in the corpus and in this suite's fixtures) with
#                 NO writer anywhere in the tree — so it was wrong about the
#                 tense and about the source, not merely about the arithmetic.
#                 Re-run the grep rather than trusting the list. NONE is enforced
#                 by an argparse `choices=`, so the vocabulary is open and a
#                 hand-edited value travels — which is also why
#                 `_ITERATION_D2_CLOCKLESS_EVENTS` is a membership test over this
#                 field and not a claim about what the engine can produce.
#                 It is not closed here because the only guard available
#                 is a bounded-token check (short, alnum+underscore — it accepts
#                 all nine and rejects prose), and failing it would blank the one
#                 key a STRUCTURAL audit row consists of, destroying the re-arm
#                 signal JC4 exists to capture while keeping the row. That is a
#                 wire decision on a shipped T2-C2 field with its own trade-off,
#                 so it gets its own card instead of riding along on this one.
_ITERATION_FIELD_GUARDS = {
    "at": (_nonempty_str, _looks_like_timestamp),
    "commit_sha": (_nonempty_str, _looks_like_sha),
}
# `results` is projected by its own level (`_iteration_result_wire`), not copied
# by the scalar loop — precomputed rather than filtered per entry.
_ITERATION_SCALAR_FIELDS = tuple(f for f in _ITERATION_FIELDS if f != "results")
# 🔴 JC11 — THE SECOND WHITELIST, DERIVED RATHER THAN HAND-LISTED, AND SELECTED
# PER ROW BY THE ROW'S ORIGIN. D2 governs WHO AUTHORED a record, and on this
# lane `event` is what names the author: `arm` is a verb a person runs. So the
# rule is a membership test over `event`, declared HERE beside the whitelist it
# narrows, and `_iteration_wire` reads it.
#
# THE SHAPE IS THE DECISION, and the three it is not are worth naming because
# each would have been shorter:
#   * NOT "remove `at` from `_ITERATION_FIELDS`" — that declocks EVERY entry
#     shape and destroys time-to-first-green and cadence. An iteration is
#     agent-authored.
#   * NOT a `wire.pop("at")` after the projection — that buries the rule inside
#     the projection, which is the exact shape this file's own guard comment
#     already refuses ("declared beside the whitelist rather than buried in the
#     projection").
#   * NOT a fifth parameter on `_project_row` — one caller would use it, and that
#     function's docstring is a record of why it is ONE loop shared by three
#     levels.
#
# Two properties fall out rather than being arranged, and both are load-bearing:
# the D2 tuple is a FILTER of the tuple above, so a surviving row emits its keys
# in the same relative order (key order is emitted bytes, and every pinned test
# compares with `sort_keys=True` and is blind to a reorder); and
# `_ITERATION_FIELDS` itself is unchanged, so the negative-space assertion in
# `test_no_free_string_reaches_the_wire_through_a_shape_the_guard_does_not_check`
# still covers `at` and needs no edit.
_ITERATION_D2_CLOCKLESS_EVENTS = ("arm",)
_ITERATION_SCALAR_FIELDS_D2 = tuple(f for f in _ITERATION_SCALAR_FIELDS if f != "at")
# ⚠️ THIS WHITELIST CARRIES NO CLASS INFORMATION, and that is not an omission:
# `iteration_timeline` is the one ROW-GRANULAR field in `consent_class_map.json`
# (`doors.loop.fields.iterationTimeline.granularity == "row"`). The class of a
# datum here is decided by WHICH ROW it sits in, never by which key it is —
# `at` and `commit_sha` are class B on a rejected proposal and class C on a
# green one, the same key in two classes. `_iteration_class` is the whole
# boundary; see `_loop_iteration_timeline`.
# The per-result projection: all 501 result rows in the corpus above carry
# exactly {id, verdict, value} and nothing else, so this is the whole row,
# pinned by name rather than copied blind.
_ITERATION_RESULT_FIELDS = ("id", "verdict", "value")
# 🔴 JC12 — THE SHAPE GUARD ONE LEVEL DOWN, WHICH IS WHERE THE COMMENT ON
# `results` IN `_ITERATION_FIELDS` ALREADY SAID ITS RULE BELONGED. It said so
# while there was no rule: this projection copied all three values verbatim, and
# `value` is the one the gate fills from a check's OWN COMMAND OUTPUT. A check
# declaring `signal.value_type: "string"` — authorable through the supported path,
# admitted by all four gates until this change — put raw stdout on the wire.
#
# Every member is decided, and the two FAILURE MODES differ because the fields
# do (the same reasoning `_ITERATION_FIELD_GUARDS` records for `at` vs
# `_transition_row`):
#   * `id`, `value` -> drop the KEY. What is left is still a record.
#   * `verdict`     -> drop the ROW, via `_ITERATION_RESULT_ROW_GUARDS`. A result
#                      with no verdict asserts a check ran and declines to say
#                      how it went. It is also the field the SERVER reads to
#                      VERIFY that a payload omitting class B really carries no
#                      non-green iteration (see `INTERNALS.md`), so a row
#                      shipped without one would silently weaken a check the
#                      server performs on our behalf. Dropping the row keeps that
#                      property true of everything that ships.
_ITERATION_RESULT_FIELD_GUARDS = {
    "id": (_nonempty_str, _looks_like_check_id),
    "value": (_as_is, _looks_like_measurement),
}
# Guards whose failure costs the whole row rather than the key. Kept as its own
# map rather than a flag beside the guard, so the negative-space test can assert
# over the UNION and no member can be in neither.
_ITERATION_RESULT_ROW_GUARDS = {
    "verdict": (_as_is, _is_wire_verdict),
}


def _iteration_result_wire(row):
    """One `iterations[].results[]` row -> its wire shape, or None when the row
    itself must not ship. See the guard maps above for why the two failure modes
    differ, and `_project_row` for the loop."""
    return _project_row(row, _ITERATION_RESULT_FIELDS,
                        _ITERATION_RESULT_FIELD_GUARDS, _ITERATION_RESULT_ROW_GUARDS)


def _iteration_wire(entry):
    """Project ONE loop-state iterations[] entry down to the wire shape, or
    None when nothing survives the projection.

    Order and `at` are the point: the timeline is what makes time-to-first-
    green and iteration cadence computable server-side, and both die if this
    is turned into a set or sorted. Of the 211 real entries in the corpus
    above, 210 carry `at`. The single exception is still emitted — an entry
    with a verdict but no clock is data, and dropping it would silently
    shorten the timeline.

    ⚠️ SINCE JC11 THERE IS A SECOND, DELIBERATE CLOCKLESS SHAPE, and it is not
    an exception to the sentence above but a narrowing of it: an entry whose
    `event` is in `_ITERATION_D2_CLOCKLESS_EVENTS` is projected through the
    reduced whitelist, so its `at` is absent BY RULE rather than by absence on
    disk. Cadence is untouched — the rule reaches only human-invoked verbs, and
    time-to-first-green reads results-bearing rows, which no verb writes.

    That exception is worth naming precisely, because a previous revision of
    this docstring called it "a bare `{"event": ...}` row" and it is nothing
    of the kind ON DISK. It is loop-t18's `human_gate_reopen`, and it carries
    a ~500-character human-gate rejection paragraph in `reason` plus an
    operator's own words in `user_confirmed`. It is bare only AFTER this
    projection runs — the projection is what makes it bare, which is the
    whole reason the whitelist is the privacy boundary and not just a size
    one.

    `commit_sha` (JC1) follows the same rule as every other key here — copied
    only when the entry carries it, and therefore ABSENT rather than null on an
    entry the gate wrote while git was unavailable. It is absent on every entry
    written before this change too, which is every entry on disk today.

    🔴 `at` AND `commit_sha` BOTH PASS A SHAPE GUARD (`_ITERATION_FIELD_GUARDS`),
    and `at` needs one for exactly the reason `commit_sha` does — it is a free
    string read off a hand-editable local file and copied onto a lane whose whole
    promise is that only references leave the machine. The PREDICATE is shared
    with `transitions[]`; what differs is the FAILURE MODE, and the difference is
    reasoned rather than inherited:

      * here a failing value drops its own KEY and never the row. An iteration is
        a real record identified by `n` and its `results[]`, and D2 already ships
        clockless rows on this lane by design (a human-originated record carries
        sequence position and no clock), so a row with no `at` is an
        already-valid shape — loop-t18's `human_gate_reopen`, the one corpus
        entry carrying no `at` at all, is emitted exactly like that today.
      * `_transition_row` drops the whole row instead, because a transition with
        no clock asserts an event happened and then declines to say when. It
        carries no `n`, no results and no other content: strip its clock and
        nothing is left but the claim.

    An entry whose ONLY projected key fails its guard therefore yields nothing
    and leaves the timeline — the pre-existing `wire or None` rule reached by a
    new road, and the right answer: a row that is a bad clock and nothing else
    carries no datum worth keeping."""
    if not isinstance(entry, dict):
        return None  # a malformed entry costs only itself, never the timeline
    # JC11 — the whitelist is chosen by the ROW'S ORIGIN, and `event` names it.
    # A row selected here is selected BECAUSE its `event` is in the D2 set, and
    # `event` is in the reduced tuple, so `wire` always holds at least
    # `{"event": ...}` and the `wire or None` below can never delete it.
    fields = (_ITERATION_SCALAR_FIELDS_D2
              if entry.get("event") in _ITERATION_D2_CLOCKLESS_EVENTS
              else _ITERATION_SCALAR_FIELDS)
    wire = _project_row(entry, fields, _ITERATION_FIELD_GUARDS)
    results = entry.get("results")
    if isinstance(results, list):
        # A row `_iteration_result_wire` refuses (JC12: no recognisable verdict)
        # leaves the list; the iteration itself stays, identified by `n`. An
        # entry whose results ALL fail ships `results: []`, which says "this
        # iteration happened and no result row was shippable" — true, and
        # narrower than dropping the iteration from the timeline.
        kept = []
        for row in results:
            if not isinstance(row, dict):
                continue
            projected = _iteration_result_wire(row)
            if projected is None:
                # 🔴 THE WHOLE ITERATION GOES, AND THE REASON IS CONSENT CLASS
                # INTEGRITY, not tidiness. `_iteration_class` reads the DISK
                # entry, so `[prose verdict, green]` classifies as B (rejected
                # proposal) — but dropping only the unreadable row leaves an
                # all-green, C-SHAPED iteration on the wire while C may be
                # withheld. The server verifies a class omission by reading
                # `results[].verdict` back off the payload, so that row would
                # make it certify something false. Found by a cross-model review
                # and reproduced; the earlier drop-the-row rule was decided at
                # the row's altitude and the invariant lives at the iteration's.
                return None
            kept.append(projected)
        wire["results"] = kept
    return wire or None


def _iteration_class(entry):
    """The consent class ONE `iterations[]` entry belongs to: `"B"`, `"C"`, or
    None for a row no class covers.

    ONE FUNCTION, THREE OUTCOMES, ONE BOUNDARY. The class-C kind is the exact
    COMPLEMENT of the class-B one over results-bearing rows, so the letter is
    returned from a single classification rather than derived from two
    predicates asked in sequence — two predicates over one boundary is how the
    two halves of a row-granular withholding drift apart, and a row could then
    fall into both classes or neither. It is also why the caller needs no
    negation and no second flag: see `_loop_iteration_timeline`.

      * `"B"` — a REJECTED PROPOSAL: a results-bearing iteration the gate did
        NOT pass, i.e. `results` is a non-empty list and some verdict is not
        green.
      * `"C"` — the surviving CADENCE: results-bearing and every verdict green.
      * None — a STRUCTURAL audit row (arm, hold, release, scope_violation: no
        `results` key at all), which is not a proposal of any class and is
        always kept — dropping it would silently shorten the timeline and
        destroy the re-arm signal JC4 exists to capture.

    Verdicts are the closed set green/red/error/inconclusive
    (`run_gate_checks.py`'s GREEN/RED/ERROR/INCONCLUSIVE constants), and its
    `all_green`, in `run_gate`, is `all(r["verdict"] == GREEN for r in
    results)` — this is that same
    predicate read back off the record, so the wire's class-B boundary and the
    gate's own pass/fail boundary can never drift apart. It is also computable
    from the wire SHAPE (`results[].verdict` is emitted), which is what lets the
    server VERIFY that a payload omitting `"B"` from `classes_applied` really
    carries no non-green iteration, instead of trusting the claim.

    NOT `feedback_to is None`, which an earlier draft used, and the corpus is
    why. MEASURED 2026-08-14 over every `loop-state.json` on this machine —
    `glob.glob(expanduser("~/Projects/**/.fairmind/**/loop-state.json"),
    recursive=True)`, 36 files with an `iterations[]` array, 223 entries: **172
    results-bearing (152 all-green -> class C, 20 rejected -> class B) + 51
    structural audit rows.** So withholding class B removes 20 of 223 entries.
    On that same corpus `feedback_to is None` COINCIDES exactly with all-green
    on the results-bearing rows — 152 and 152 — and coincidence is precisely
    what makes it dangerous. It is wrong in principle twice: `feedback_to` means
    "which role gets the feedback", not "was this rejected"; and
    `.get("feedback_to")` returns None for all 51 audit rows because the key is
    absent from every one of them (measured: 51 of 51 carry no such key), so it
    cannot tell "no feedback owner" from "not that kind of record". It is also a
    DROPPED key, so nothing on the wire could ever verify a boundary drawn with
    it. (`.fairmind/` is gitignored, so this corpus is local working state no
    test can read — re-run the count and restate the date rather than trusting
    these numbers.)"""
    if not isinstance(entry, dict):
        return None  # a malformed entry belongs to no class, like an audit row
    results = entry.get("results")
    if not isinstance(results, list) or not results:
        return None
    if all(r.get("verdict") == "green" for r in results if isinstance(r, dict)):
        return "C"
    return "B"


def _loop_iteration_timeline(state, applied):
    """The TOP-LEVEL `iterations[]` array — not `budget.spent.iterations`,
    which is the integer `iter` already on the wire. Both are kept: the int is
    the existing field and the gate writes them independently (a real loop
    showed 14 array entries against an int of 2).

    ⚠️ THIS IS THE ONE ROW-GRANULAR FIELD IN `consent_class_map.json`, and the
    row — not the key — carries the class. `at` and `commit_sha` are class B on
    a rejected proposal and class C on a green one: the SAME key, two classes,
    which is why no key whitelist can express this boundary and why the map
    marks the field `granularity: "row"`. `applied` is the payload's own
    `classes_applied` SET — the one the caller already holds — and a row whose
    class is not in it leaves whole. Three kinds, one predicate
    (`_iteration_class`):

      * results-bearing and NOT all-green -> a rejected proposal, CLASS B. Its
        whole row leaves, with its ids, verdicts, values, clock and commit sha.
      * results-bearing and all-green -> the surviving cadence, CLASS C. This is
        generation context in the same sense `transitions` is: what the process
        did, at what rhythm, against which commit. Its whole row leaves too.
      * no `results` key at all (arm, hold, release, scope_violation) -> a
        STRUCTURAL audit row, UNCLASSIFIED. It belongs to no class, so it
        survives every withholding byte-for-byte. Dropping it would shorten the
        timeline and destroy the re-arm signal JC4 exists to capture; narrowing
        it under one grant and not another would withhold data no class covers,
        which the map forbids as plainly as it forbids under-withholding.

        ⚠️ THAT SENTENCE USED TO END "`at` INCLUDED", AND JC11 (owner decision
        2026-08-20) MADE IT FALSE WITHOUT MAKING THE RULE FALSE — the altitude
        is the whole answer, so it is written here rather than left for the
        reader to reconstruct. The `arm` row's clock is now absent AT THE
        PROJECTION (`_ITERATION_D2_CLOCKLESS_EVENTS`), by the D2 origin rule,
        which is UPSTREAM of the class question entirely: the key is on the wire
        for no grant, so no grant is the thing withholding it. Over-withholding
        is a consent verb — sending less under a narrower grant than under a
        wider one — and this row is still byte-identical under all five, which
        is what `test_jc5_a_withheld_class_empties_its_fields_and_the_stamp_says_so`
        asserts. `consent_class_map.json` therefore needs no edit and gets none:
        its `unclassified` paragraph is a rule about CLASSES, it never mentions
        `at`, and it stays true. (Read that entry before concluding otherwise —
        the map is byte-identical across both repos, so editing it widens a
        two-line wire change into a two-repo prose change.)

    ONE PARAMETER, NOT TWO NEGATED FLAGS, and the shape is the lesson rather
    than a preference. This took `drop_rejected=not class_b, drop_green=not
    class_c` — a double negation at the call site, two arguments that were only
    ever correct when resolved together, and a polarity opposite to every other
    switch in this file. That is the shape the class-C defect below grew in, and
    the first fix restated the problem rather than removing it: a second flag
    beside the first is a second thing to forget. With the SET, the two halves
    are structurally inseparable — there is no way to pass B's answer and omit
    C's — and a fourth class costs no signature change.

    CLASS C'S HALF EXISTS BECAUSE FOR ONE REVISION IT DID NOT — the timeline is
    the only field TWO classes reach, and only one of them had teeth. The
    contract's §F.1 listed the cadence under C while §F.5's enforcement table
    omitted it; the implementation followed §F.5, faithfully, and a customer who
    set `"generation_context": false` still got every green iteration's `at`,
    `commit_sha` and full `results[]`. MEASURED 2026-08-14 on the committed
    `loop_execution_conformance.json` loop-state: `granted_classes=["A","B"]`
    produced a payload BYTE-IDENTICAL to the full grant — the class-C
    withholding of this field was a literal no-op — and with `granted_classes=[]`
    the payload still carried 3 rows bearing 3 `at`, 1 `commit_sha` and 1 full
    `results[]` while `classes_applied` was `[]`. **A spec that disagreed with
    itself, transcribed faithfully**, which is how a green suite plus a correct
    implementation still ships wrong behaviour.

    ⚠️ IT MATTERS BEYOND THE FIELD, and the map's `granularity_values.row` names
    both directions of the damage: "a deletion job that unsets it removes more
    than the revoked class — and a producer that empties it withholds more than
    the revoked class." A server deletion job asking which stored fields are
    class C reaches these rows, so the store was prepared to delete data the
    producer never stopped sending: ONE map, wrong in OPPOSITE directions on each
    side, which no single-repo test can see. `consent_class_map.json` is the
    single artifact that makes that visible, and this function is the producer
    half of what it says.

    THE BOUNDARY IS COMPUTABLE FROM THE WIRE SHAPE, which is what lets the server
    VERIFY a withholding instead of trusting the stamp — the property §F.5
    already demands of class B, now held by both classes because both read the
    SAME predicate off the SAME emitted `results[].verdict`:

      * B withheld ⇒ no surviving row is results-bearing and non-green.
      * C withheld ⇒ no surviving row is results-bearing and all-green.
      * either way ⇒ every audit row is present and unchanged.

    ⚠️ NEITHER CLASS WITHHOLDS CARDINALITY, and for class C that holds even
    though whole rows now leave. `iter` (`budget.spent.iterations`) is
    UNCLASSIFIED and counts evaluations unconditionally, so how much green work
    happened stays computable with zero green rows on the wire — the map states
    this under its own `iter` entry, and it is the same argument that has always
    covered class B. Class B additionally leaves `n` gaps in the survivors: `n`
    is the gate's monotone counter (`run_gate_checks.py` writes it as
    `len(state["iterations"]) + 1`), and renumbering
    densely would make the wire's `n` a different quantity from the gate's and
    break the `human_gate[].after_iteration_n` join. So what each class withholds
    is what those iterations WERE, never the fact that they existed."""
    entries = state.get("iterations")
    if not isinstance(entries, list):
        return []  # absent, null or a non-list degrades to "no timeline"
    timeline = []
    for entry in entries:
        # ONE call, THREE outcomes — the row's own class, or None for the
        # unclassified structural rows, which no grant can withhold.
        letter = _iteration_class(entry)
        if letter is not None and letter not in applied:
            continue
        wire = _iteration_wire(entry)
        if wire is not None:
            timeline.append(wire)
    return timeline


# The one string a target that cannot be named as a file INSIDE the repo is
# replaced with. A LITERAL, never derived from the target it replaces, so it is
# structurally incapable of carrying a byte of it — the property a "sanitized"
# path built by trimming or hashing the original would not have.
#
# WHY A MARKER AND NOT A DROP. 769 of the 2,724 real mutate targets measured
# below (28.2%) are outside their own repo, so dropping them would silently
# under-count the loop's own mutation total by more than a quarter. The marker
# withholds WHAT the path was and never THAT the mutation happened — the same
# rule `_loop_iteration_timeline` follows for a withheld class ("what each class
# withholds is what those iterations WERE, never the fact that they existed").
# `<angle-bracketed>` is this codebase's existing spelling for a placeholder
# that is not a real value (`ambient_digest` excludes the `<synthetic>` model
# the same way), so a reader meets one convention rather than two.
OUTSIDE_REPO_TARGET = "<outside-repo>"


def _contained(path, root):
    """The part of `path` strictly below `root`, `""` when it IS `root`, or None
    when it is not under it at all.

    ONE WRITER FOR "WHERE DOES THE ROOT END": the trailing-slash normalization
    and the rejection of `/` both live here rather than at the caller, so there
    is no second place that can decide a root is usable. `/` is rejected because
    nothing is a repo there and its prefix matches every absolute path on the
    machine — a root of `/` would turn `/<home-root>/<account>/x.py` (the
    home-directory root, then the account) into `<home-root>/<account>/x.py`,
    which is neither absolute nor `..`-bearing and would satisfy every
    downstream guard while carrying the exact value they exist to remove.

    The case-insensitive fallback is macOS, where two spellings of one directory
    are one directory (`os.path.normcase` is IDENTITY on posixpath — it does not
    cover this) and a raw prefix comparison would miss and cost a real in-repo
    path. It compares equal-LENGTH slices of the originals, so a `.lower()` that
    changes length (`İ`) makes the comparison fail rather than mis-slice — a miss
    costs a target, an over-eager match could not leak anyway (the stripped
    prefix is the leaky half, and a same-length case variant of the root is the
    same directory on any filesystem that accepted both spellings)."""
    root = root.rstrip("/")
    if not root:
        return None  # "/" is nobody's repo root — see `_wire_target`
    if path == root:
        return ""
    prefix = root + "/"
    if path.startswith(prefix):
        return path[len(prefix):]
    if path[:len(prefix)].lower() == prefix.lower():
        return path[len(prefix):]
    return None


def _wire_target(raw, root, root_real, memo):
    """One trace target -> the string the wire may carry, or None when the row
    is not a target at all.

    🔴 THIS IS THE PRIVACY FIX (option A, producer-side relativization). The
    absolute developer path is stored beside `userId` server-side, and
    the same bytes reach a second store verbatim, so ONE collection resolved
    an opaque subject id to a developer's OS account name with no platform
    mapping involved. MEASURED 2026-08-15 over the 48 `.fairmind/trace/*.jsonl`
    ledgers under `~/Projects/*/` on this machine (`.fairmind/` is gitignored, so
    this corpus is local working state no test can read — re-run the walk and
    restate the date rather than trusting these numbers): **2,724 mutate targets,
    2,724 of them absolute — 100%**.

    RELATIVE TO THE WORKTREE TOPLEVEL, not the git common dir, and the two differ
    exactly when it matters. Tenancy (`repoRef`) is `sha256(realpath(git-common-
    dir))` because it must be equal across a repo's linked worktrees — but the
    developer's FILES live under the worktree toplevel, and a linked worktree's
    common dir is `<main>/.git/worktrees/<name>`, under which no source file
    sits. Relativizing against it would put 100% of a worktree loop's targets
    "outside the repo" and replace every one with the marker. The toplevel is
    also what the gate's own attribution already anchors on
    (`run_gate_checks.py:561`, `_resolve_repo_root`).

    🔴 A PATH OUTSIDE THE ROOT IS NEVER RELATIVIZED. `os.path.relpath` answers
    every question with a string, and the string it returns for an outside path
    is `../../…`, which reads as safe and is not: of the 769 outside-repo targets
    in that same corpus (28.2%, 218 distinct), **642 — 83.5% — still carry the OS
    account name after `relpath`**, because Claude Code's own directories embed
    it in a SLUG rather than a path component
    (`../../.claude/projects/-Users-<account>-Projects-<repo>/memory/…`,
    `../../../../private/tmp/claude-<uid>/-Users-<account>-…/scratchpad/…`). A
    `..`-blind filter would have shipped the leak it was written to remove. The
    inverse holds too, which is what makes the containment rule sufficient: **0
    of the 1,955 in-repo targets (386 distinct) carry the account name once the
    root prefix is gone.**

    `".." ANYWHERE IN THE EMITTED STRING` — a substring ban, not a component
    test, and it is deliberately stricter than the rule next door. It also
    catches the trace hook's truncation marker (`tr(s, n=120)` -> a trailing
    `"..."`, `hooks/scripts/trace-op.sh:79`), which survives `normpath` and would
    otherwise ship as a repo-relative path naming a file that does not exist.
    Cost of the strictness, measured on the same corpus: **15 of 1,955 in-repo
    targets (0.77%)**, every one of them an already-truncated
    `.fairmind/…/journals/…` string (47 mutate targets carry the marker in total;
    the hook stopped truncating mutate targets, so these are old rows — and edge
    case 3 is that this projection runs at FLUSH over strings already on disk,
    which is exactly why they still have to be handled).

    NOT `run_gate_checks._normalize_trace_target` (`run_gate_checks.py:578`),
    though it is the same shape and a reader should be sent there. Two different
    questions: that one builds a JOIN KEY for git-reported paths and answers
    "cannot attribute" with None, so its rule 4 accepts an already-relative
    target unchanged — including `../../<home-root>/<account>/…`, which is correct
    for a key nothing ever emits and is precisely the leak here. Reusing it would
    also drag the gate module into this builder's import graph, which the
    conformance staging depends on staying small. The divergence is one rule and
    it is stated so nobody "unifies" them back into one function.

    `realpath` is tried only when the raw comparison misses (a symlinked home, or
    macOS `/var` -> `/private/var`), and it is a read-only path resolution: not a
    git call, not a config read, not the clock — the module contract at the top
    of this file stands, and `test_producer_is_deterministic` gets STRONGER here,
    since a relativized payload no longer varies with the staging directory.

    ONE GUARD DECIDES, AND IT READS THE OUTPUT RATHER THAN THE INPUT. Everything
    above it only proposes a CANDIDATE — the relative target itself, or the
    remainder below the root — and a candidate is emitted only if it is a
    non-empty, non-absolute, `..`-free string. An unresolvable absolute path is
    deliberately carried down to that guard as its own candidate instead of being
    short-circuited: with one choke point there is no second branch that can
    return a value the guard never saw, and every clause of the guard is
    load-bearing (each one, removed on its own, turns a case in
    `test_a_target_outside_the_repo_is_marked_never_relativized` red — an earlier
    revision pre-checked `".." in raw` and made the guard's own `..` clause
    unreachable, i.e. dead coverage that read as protection).

    THE RESIDUAL, stated rather than left to be discovered: a root so shallow
    that the account directory is INSIDE the repo (a git repo at `/Users`) makes
    the account name a legitimate repo-relative component, and no producer-side
    rule can see the difference — the account name is then part of the repository
    layout. `/` is rejected outright (`_contained`) because nothing is a repo
    there and its prefix matches every absolute path on the machine."""
    if not isinstance(raw, str) or not raw:
        return None  # not a target: a non-str would also crash `sorted(targets)`
    if raw in memo:
        return memo[raw]
    candidate = raw  # an already-relative target is its own candidate
    if os.path.isabs(raw):
        candidate = None
        if root:
            # `normpath` first, so an in-repo path written through its own tree
            # (`<root>/a/../b.py`) resolves to the file it names instead of being
            # marked; `realpath` only if that missed.
            candidate = _contained(os.path.normpath(raw), root)
            if candidate is None and root_real:
                # `realpath` is the ONE call here that touches the filesystem,
                # and it RAISES on a target the rest of this module tolerates:
                # a NUL byte gives `ValueError: embedded null byte`, and a
                # pathological path can give `OSError`. Unguarded, ONE corrupt
                # trace row killed the whole flush — reproduced 2026-08-15
                # through the shipped CLI, exit 1. That contradicts this
                # module's own posture two hundred lines up, where `_read_jsonl`
                # "deliberately tolerates a corrupt trace ledger": a ledger is
                # local, append-only, hand-editable state, and losing an entire
                # loop's record to one bad byte in it is the wrong trade.
                # Falling through leaves `candidate` None, so the row lands on
                # the marker — the safe direction, never a passthrough.
                try:
                    candidate = _contained(os.path.realpath(raw), root_real)
                except (ValueError, OSError):
                    candidate = None
        if candidate is None:
            # No root, or no containment. Carry the ORIGINAL to the guard, which
            # rejects it for being absolute. NEVER `os.path.relpath` here: that
            # is the `../../<home-root>/<account>/…` escape — 83.5% of the real
            # outside-repo corpus still names the account after it.
            candidate = raw
    # ⚠️ ONE LINE, AND IT IS THE GUARANTEE. Weaken any clause and a relative-
    # LOOKING string carrying the same account name goes out — which is worse
    # than an absolute one, because it reads as already filtered.
    out = (candidate if candidate and not os.path.isabs(candidate) and ".." not in candidate
           else OUTSIDE_REPO_TARGET)
    memo[raw] = out
    return out


def _loop_artifact_mutations(trace_rows, repo_root):
    """Per-file mutation COUNTS in first-touch trace order (T2-C2).

    `repo_root` is the worktree toplevel every emitted target is expressed
    relative to, resolved by the CALLER (see `_wire_target` for the rules and the
    measured reasons). `None` means "no root was resolved", NOT "use something
    nearby": every absolute target then becomes `OUTSIDE_REPO_TARGET`. Falling
    back to `cwd` was considered and rejected — `--cwd /Users` would then emit
    `<account>/Projects/<repo>/x.py`, a string that is neither absolute nor
    `..`-bearing and would pass every assertion in this change while carrying the
    exact value it exists to remove.

    `artifacts` beside this stays the sorted distinct list it has always been:
    it is the one loop field with a live reader, and that reader forwards it
    verbatim into a record in the store one hop downstream, whose `artifacts`
    is a `Vec<String>` — dicts there would 400 the whole batch — and does so
    once PER AGENT. Un-collapsing it in place would cost, on the worst trace in
    this repo — `.fairmind/trace/T8.jsonl`, 304 mutate rows over 102 distinct
    files, re-measured 2026-07-26 — **10,924 B as the sorted set against
    32,768 B as a repeated list**, times the agent count: on its own enough to
    push `--emit all` past the 30,000-byte agent-output cap the module
    docstring describes. The counted form is **13,071 B, +2,147 B over the
    set**, names each path exactly ONCE, and answers the rework question
    directly (`count > 1`).

    (An earlier revision of this docstring quoted 6,895 -> 27,010 B and
    +1,589 B and called that "the worst real trace". Those figures are real but
    they are PC-A2's, which lives in the other repo and is smaller on every
    axis than T8 — which the same change pins as the worst ledger fifty lines
    away in the test file. A superlative citing the runner-up understates the
    cost it exists to justify.)

    Mongo-only by construction, exactly like
    `LoopAgentStats.toolCalls`: the telemetry dispatcher reads a named
    whitelist, so a new field cannot leak onto that wire by accident.

    A dict accumulator, never a set — insertion order IS first-touch order,
    and determinism is pinned by `test_producer_is_deterministic`.

    NOT WINDOWED, and that is a KNOWN GAP rather than a decision — external
    review (Codex, P1/P2, 9/10) is right that a rework count ought to mean
    "how many times did THIS loop come back to this file". The trace ledger is
    per-repo/per-task_ref, so it outlives a close and is shared by a revived
    loop: unwindowed, a second close inherits the first's rework and a
    post-close edit inflates it. `_loop_agents` windows tokens and tool calls
    on `[started_at, closed_at]` for exactly this reason.

    Deliberately NOT fixed here, and the reason is that fixing it alone would
    make things worse: `artifacts` beside it has never been windowed either
    (a pre-existing asymmetry, recorded when the loop payload was first pinned),
    so windowing only the count ships a windowed number next to an unwindowed
    list — a third behaviour where there are currently two. Windowing both is a
    change to a field with a LIVE reader that forwards it into
    a downstream store, and it moves a sha-pinned fixture in both repos. That
    earns its own change with its own verification, not a rider on a quality
    pass. Nothing reads either field yet, so the cost of the delay is bounded.

    ONE WALK, TWO ANSWERS. Returns `(artifacts, mutations)`:

      * `artifacts` — every distinct emitted target, sorted, UNWINDOWED.
      * `mutations` — `[{target, count}]` in first-touch order.

    Both derive from ONE definition of "what counts as a mutation" AND ONE
    normalization of what a target may say — the two used to be filtered
    independently six lines apart, and this projection is the filter that comment
    predicted (PCF-30, then deferred): landing it in one of the two derivations
    would have shipped a relativized `artifacts` beside absolute
    `artifact_mutations`, leaking exactly the paths it was added to remove. The
    marker is therefore aggregated in BOTH — one `artifacts` entry, one
    `mutations` row whose count is the sum over every target it stands for.

    ⚠️ `artifacts` IS THE FIELD WITH THE LIVE READER, so its VALUES move here on
    purpose: that reader forwards them verbatim into the store one hop
    downstream, which is the second of the two stores the absolute path was
    reaching. Its TYPE is untouched (`Vec<String>`, dicts there would 400 the
    batch) and so is its shape — sorted, distinct, unwindowed — so this is a
    change of what the strings SAY, never of what the field IS. The earlier
    revision of this sentence read "unchanged from what this payload has always
    sent"; that was true of the collapse T2-C2 was defending and is false of a
    privacy projection, which has to reach every path-bearing field or it reaches
    none of them.

    The size argument the collapse rests on is unaffected in direction and
    smaller in magnitude: on `.fairmind/trace/T8.jsonl` (304 mutate rows over 102
    distinct files, re-measured 2026-07-26) the sorted set was 10,924 B against
    32,768 B as a repeated list, and the counted form 13,071 B. Relativization
    only shortens each string, so the ratio survives; the exact post-projection
    figure is re-measured in the test file, which is where the budget that
    depends on it lives."""
    targets = set()
    counts = {}
    root = repo_root if isinstance(repo_root, str) and repo_root else None
    # Resolved ONCE per call, and only if there is a root to resolve: `realpath`
    # on the root is what makes a symlinked home or macOS `/var` -> `/private/var`
    # comparable, and doing it per row would repeat the syscall 2,724 times on
    # the corpus this was measured over.
    root_real = os.path.realpath(root) if root else None
    memo = {}
    for row in trace_rows:
        if not isinstance(row, dict) or row.get("kind") != "mutate":
            continue
        target = _wire_target(row.get("target"), root, root_real, memo)
        if target is None:
            continue
        targets.add(target)
        counts[target] = counts.get(target, 0) + 1
    return (sorted(targets),
            [{"target": target, "count": count} for target, count in counts.items()])


# ---------------------------------------------------------------------------
# JC1..JC5 — the judge-capture lane's five new top-level keys.
#
# Every one of them is a PROJECTION of state some other writer already owns
# (`run_gate`, the two new `run_gate_checks.py` verbs, `arm`). This builder
# derives, it never recomputes: it makes no git call, reads no config and never
# touches the clock — the module contract at the top of this file, enforced by
# `test_producer_is_deterministic`, which re-stages into a different temp dir
# and diffs run 1 against run 2.
# ---------------------------------------------------------------------------

_MUTATION_DIGEST_PREFIX = "sha256:"

# Sentinel for "this path is not in that signature at all". NOT `None`: `None`
# is a legitimate signature value (a file unreadable or deleted at signature
# time — `_working_tree_sha` returning None), and using it as the missing
# marker would make "the file vanished from the set" byte-identical to "the
# file became unreadable" in `changed_path_count`.
_ABSENT = object()


def mutation_digest(signature):
    """The divergence detector's canonical form. `signature` is the gate's OWN
    `[[path, "sha256:<hex>" | null], ...]` list, ALREADY SORTED by path by
    `run_gate_checks._no_work_signature` — so this function introduces NO new
    ordering rule to get wrong, which is the single most important property it
    has.

    `ensure_ascii=True` (the json default, pinned explicitly here because it is
    load-bearing) makes the hashed bytes pure ASCII regardless of how a path is
    encoded on the filesystem that produced it — a non-ASCII filename must not
    make two otherwise identical trees hash differently across platforms.
    `separators` is pinned for the same reason: whitespace must not be a degree
    of freedom in a value two machines compare for equality.

    The alternative considered was `"\\n".join(f"{path}\\t{sha}")`. Rejected: it
    introduces a separator a path could contain, a null-encoding rule and an
    ordering rule — three new things to get wrong — where reusing the gate's
    already-sorted list introduces none.

    The digest is a ONE-WAY summary of a list that never leaves the machine: no
    per-file content hash and no path reaches the wire through it. `null`
    entries serialize as JSON `null` and are part of the hashed bytes — "this
    path existed and could not be read" is a distinct tree state from "this
    path is not in the set"."""
    if not isinstance(signature, list):
        return None
    blob = json.dumps(signature, separators=(",", ":"), ensure_ascii=True)
    return _MUTATION_DIGEST_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _lifecycle(state):
    """`state["lifecycle"]` — the block the gate's transition writers own
    (`run_gate` at the status flip, `--record-transition`, `--human-gate`).
    Absent on every loop armed before this change, which is the normal case."""
    return _as_dict(state.get("lifecycle"))


# The transitions the LIFECYCLE BLOCK carries, in the order their rows appear on
# the wire. `arm` is deliberately NOT here: it is projected from two pre-existing
# fields by a hand-written block in `_loop_transitions`, and it used to sit at the
# head of one combined tuple that the loop then skipped with a `[1:]` slice. That
# made the tuple's ORDER load-bearing in a way nothing stated — alphabetising the
# three names, or inserting one, emits `arm` twice or drops a real transition —
# so the two things are two names. Order still matters here, but only for the one
# reason a reader would guess: it is the order of the emitted rows.
#
# `merge` is deliberately not a name in EITHER: the loop payload is built at close
# and the PR merges afterwards, so a client-emitted merge sha is permanently null
# unless somebody re-flushes — and a transition that is usually null is not a join
# key. The deferred outcome is computed SERVER-side; the client's job here is
# to emit the join key it can guarantee.
_LIFECYCLE_TRANSITION_NAMES = ("gate_green", "post_ceremony")
# JC11 — the transition names D2 leaves without a clock, because a person runs
# the verb that produces them. `arm` is the only one today; see the D2 block
# above `_ITERATION_FIELDS` for the four sibling verbs this card deliberately did
# not take and for the half of the residual that is still open.
_D2_CLOCKLESS_TRANSITIONS = ("arm",)


# The two shape guards `_transition_row` applies (`_looks_like_timestamp` /
# `_looks_like_sha`) are defined ONCE, up in the shared helpers beside
# `_as_dict`, because `iterations[]` copies the same two value classes through
# `_ITERATION_FIELD_GUARDS`. See the block there for what they refuse and why
# living beside one call site was itself the defect.


def _transition_row(name, at, commit_sha):
    """One `transitions[]` row, or None when the transition left no record.

    ABSENCE IS THE SIGNAL, never a null-filled placeholder. EVERY row must carry
    at least one FACT beyond its own name, or it asserts that a transition
    happened and then declines to say anything about it — and the invariant is
    ONE rule enforced against two different fields, because the rows differ in
    what they carry:

      * `gate_green` / `post_ceremony` — the fact is the CLOCK. A row with a null
        or unparseable timestamp is worse than no row, so the clock is required
        and its failure costs the whole row. UNCHANGED by JC11, in both
        directions: a prose `at` still deletes the row, and an absent one still
        does.
      * `arm` (`_D2_CLOCKLESS_TRANSITIONS`) — the fact is `commit_sha`, the
        baseline ref the whole loop's mutation set is diffed from. D2 removes the
        clock from this row BY RULE (owner decision 2026-08-20, JC11), so the
        same invariant is enforced against the sha instead: no clock is required,
        none is projected, and a row that would carry no sha is refused as the
        bare claim it would be.

    THIS IS AN ASYMMETRY, NOT DRIFT, AND THE PREVIOUS SENTENCE HERE HAS BEEN
    REPLACED RATHER THAN ANNOTATED. It read "a row with a null timestamp is worse
    than no row, because it asserts a transition happened and then declines to
    say when" — which after JC11 is exactly what the `arm` row does, on purpose.
    A pointer beside a sentence the code contradicts is worse than either alone.

    `commit_sha` is omitted (never null) when the writer degraded: `run_gate`
    drops the key entirely on any git failure, and the omit-don't-sentinel rule
    is this module's own convention."""
    row = {"name": name}
    clockless = name in _D2_CLOCKLESS_TRANSITIONS
    if not clockless:
        at = _nonempty_str(at)
        if not _looks_like_timestamp(at):
            return None
        row["at"] = at
    commit_sha = _nonempty_str(commit_sha)
    if _looks_like_sha(commit_sha):
        row["commit_sha"] = commit_sha
    if clockless and "commit_sha" not in row:
        return None  # a clockless row with no sha carries nothing but its name
    return row


def _loop_transitions(state):
    """The loop's lifecycle transitions, in lifecycle order, or None when none
    is known. Never `[]` — an empty list would claim "this loop had no
    transitions", which is never true of a loop that ran.

    ONE WRITER PER FACT: the `arm` row is a pure projection of two fields that
    ALREADY EXIST — `contract.mutation_set.baseline.ref` (the first-arm HEAD the
    scope boundary already diffs from) and `budget.spent.first_armed_at`.
    Nothing new is written at arm time for JC1, and
    `audit_run_meta.collect_run_meta()` is deliberately NOT called here: it
    reads the wall clock, it raises where this module must degrade, and the
    conformance staging has no git at all — so its reuse lands at the WRITERS
    (the two new verbs persist its output into `lifecycle`) and never in this
    builder.

    BOTH `arm` SOURCES ARE PARTIAL ON REAL DATA, and the row handles it.
    Measured 2026-08-14 over every `loop-state.json` under
    `~/Projects/**/.fairmind/**` (36 files): 23 carry
    `contract.mutation_set.baseline.ref`, 22 carry `budget.spent.first_armed_at`
    and **22 carry both** — so the `arm` row is legitimately absent on 14 of 36
    (39%) of the loops already on disk. Emitted only when BOTH are present AND
    both PASS THEIR SHAPE GUARD: a `ref` that is not a git object name is not a
    baseline, and a `first_armed_at` that is not an instant is not a clock.

    `arm` describes the FIRST arm while `gate_green` describes the LAST round.
    That is deliberate, not drift: the baseline ref is frozen at the first arm
    and is the reference the whole loop's mutation set — and therefore
    `stratification.diff_size` — is diffed from. A re-arm preserves it.

    ⚠️ SINCE JC11 THE `arm` ROW CARRIES NO CLOCK — `{name, commit_sha}`, not
    `{name, at, commit_sha}`. `first_armed_at` is still read (both-or-neither is
    unchanged, and the paragraph above still describes what makes the row
    absent on 14 of 36 loops); it is simply not projected. Because
    `first_armed_at` is write-once and survives every re-arm, this clock only
    ever carried the FIRST arm's instant, which still ships as `started_at` and
    inside `loop_id` — see the residual block above `_ITERATION_FIELDS`."""
    lifecycle = _lifecycle(state)
    baseline = _as_dict(_as_dict(_as_dict(state.get("contract")).get("mutation_set")).get("baseline"))
    spent = _as_dict(_as_dict(state.get("budget")).get("spent"))

    # 🔴 ONE APPEND SITE AND ONE None-FILTER, FOR EVERY ROW ALIKE. Found by a
    # cross-model review 2026-08-14 and reproduced: this had TWO append sites,
    # and only the loop's checked `_transition_row`'s answer. Round 1 gave that
    # function a new way to return None (an `at` failing the shape guard) and
    # taught only one of the two callers about it, so a
    # `budget.spent.first_armed_at` of `"manual-arm"` — non-empty, not ISO —
    # shipped literal `transitions: [None]`. Building CANDIDATES and filtering
    # once is what makes an unfiltered append unexpressible here, rather than a
    # second `if row is not None` that the third caller forgets in turn.
    candidates = []
    arm_ref = _nonempty_str(baseline.get("ref"))
    # BOTH OR NEITHER, AND "BOTH" MEANS A REAL REF — the sha is checked HERE
    # rather than left to `_transition_row`, because `arm` is the one row whose
    # sha IS the fact it exists to carry, and that claim has to be enforced
    # rather than merely asserted in a comment. Round 1 left the two disagreeing:
    # the comment said the sha was the point while a `baseline.ref` of
    # `"please-review"` still emitted an `arm` row with no `commit_sha` at all —
    # a row announcing an arm and naming no baseline, which is exactly the
    # half-record "both or neither" exists to refuse. Safe because the ref is
    # never user-supplied: `run_gate_checks` writes it from `_head_sha()` (`git
    # rev-parse HEAD`, a full 40 hex) and writes nothing when git fails, and the
    # `{value, ref, clean}` object `capture_baseline.py` emits — where a `ref`
    # CAN be a human-typed `HEAD~3` — is a CHECK's regression baseline, a
    # different field this projection never reads.
    # 🔴 JC11 — `first_armed_at` IS STILL READ AND IS NO LONGER PROJECTED, and
    # the two halves of that sentence answer two different questions. THIS one is
    # "is there a real first arm recorded on disk" (a valid ref AND a valid
    # clock) — the emission PRECONDITION, unchanged, so the emitted row SET is
    # byte-identical to what shipped before. `_transition_row` asks the other
    # one, "does this row carry a datum", against the sha. Dropping the clock
    # from the precondition as well would have ADDED an arm row to the 1 of 36
    # corpus loops carrying a valid `baseline.ref` and no `first_armed_at`
    # (measured 2026-08-20: 23 carry the ref, 22 the clock, 22 both) — a payload
    # change this card did not decide. Independent confirmation that this is the
    # right branch: `test_jc1_a_partial_arm_record_emits_no_arm_row`, the test
    # that pins both-or-neither, survives VERBATIM and unedited.
    if _looks_like_sha(arm_ref) and _looks_like_timestamp(
            _nonempty_str(spent.get("first_armed_at"))):
        candidates.append(("arm", None, arm_ref))
    for name in _LIFECYCLE_TRANSITION_NAMES:
        record = _as_dict(lifecycle.get(name))
        candidates.append((name, record.get("at"), record.get("commit_sha")))

    rows = [row for row in (_transition_row(*c) for c in candidates) if row is not None]
    return rows or None


def _signature_of_green_iteration(state):
    """The mutation signature of the iteration the gate went green on, found by
    the POINTER `lifecycle.gate_green.iteration_n` rather than by searching.

    The pointer is written at the instant of the flip precisely so this is not a
    search: audit entries can follow the green iteration, and every K-th
    confirmation green looks alike, so "the last results-bearing entry" is a
    fragile way to name the round that mattered."""
    lifecycle = _lifecycle(state)
    n = _as_dict(lifecycle.get("gate_green")).get("iteration_n")
    if not isinstance(n, int) or isinstance(n, bool):
        return None
    entries = state.get("iterations")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("n") == n:
            signature = entry.get("mutation_signature")
            return signature if isinstance(signature, list) else None
    return None


def _signature_map(signature):
    """`[[path, sha], ...]` -> `{path: sha}`, tolerating a malformed row."""
    out = {}
    for pair in signature or []:
        if isinstance(pair, (list, tuple)) and pair and isinstance(pair[0], str):
            out[pair[0]] = pair[1] if len(pair) > 1 else None
    return out


def _mutation_divergence(state):
    """JC3 — did the tree the gate labelled survive the pre-PR ceremony?

    WHAT THE SERVER CAN AND CANNOT DO, plainly: it holds no repo bytes, so it
    can never recompute a content-hash digest. What it CAN do is compare the two
    digests the client emits (so `diverged` is checkable rather than trusted)
    and read the magnitude off `changed_path_count`.

    THERE IS NO PATH LIST, and that is the design's largest deliberate
    omission. `mutation_signature` is the first key `_ITERATION_FIELDS` exists
    to drop; emitting its path half at top level would route around the privacy
    whitelist one step from its own boundary. The paths are also NOT already on
    the wire — the signature's set is git-grounded while `artifacts` comes from
    trace mutate targets, and roughly a fifth of signed paths are absent from
    `artifacts` on the measured corpus, including files a human edited directly
    that the trace never saw. If path naming is genuinely wanted it is its own
    card with its own disclosure decision, not a rider on a divergence detector.

    A CARDINALITY IS EMITTABLE WHERE THE LIST IT COUNTS IS NOT, and the reason
    has to be stated rather than inherited: a cardinality NAMES NOTHING. It is
    a magnitude — how far the ceremony moved the tree — and is not invertible to
    any path, extension or directory. That is the same shape of claim the digest
    makes, one level simpler.

    DIVERGENCE POLICY (owner decision, 2026-08-14): a diverged episode is
    FLAGGED AND STAYS IN THE EVAL POOL. Divergence is a feature the judge can
    condition on; nothing is dropped at capture and selection filters on the
    flag. This function therefore never suppresses anything — it only reports.

    Returns None only when NEITHER side exists (a loop with no signature-bearing
    green iteration and no recorded ceremony). When exactly one side exists the
    object is emitted with per-key nulls, because "the gate's tree is known and
    the ceremony's is not" is a different fact from "nothing is known", and the
    two must not serialize identically."""
    green = _signature_of_green_iteration(state)
    after = _as_dict(_lifecycle(state).get("post_ceremony")).get("mutation_signature")
    after = after if isinstance(after, list) else None
    if green is None and after is None:
        return None

    green_digest = mutation_digest(green)
    after_digest = mutation_digest(after)
    both = green is not None and after is not None
    changed = None
    if both:
        gmap = _signature_map(green)
        amap = _signature_map(after)
        changed = sum(1 for path in set(gmap) | set(amap)
                      if gmap.get(path, _ABSENT) != amap.get(path, _ABSENT))
    return {
        "gate_green_digest": green_digest,
        "post_ceremony_digest": after_digest,
        "diverged": (green_digest != after_digest) if both else None,
        "changed_path_count": changed,
    }


# The diff-size buckets, on `insertions + deletions`. A FIRST CUT, not a
# measurement: no loop on disk carries a diff stat, because nothing wrote one
# before this change (verified 2026-08-14 — `grep -rl diff_stat` over every
# loop-state.json on this machine returns nothing). RE-MEASURE AND RE-CUT once
# >= 30 loops have emitted `diff_size`, and record the new thresholds with
# their date, in the style of the corpus comment above.
_DIFF_SIZE_BUCKETS = ((50, "S"), (400, "M"))
_DIFF_SIZE_BUCKET_MAX = "L"
_DIFF_STAT_FIELDS = ("files", "insertions", "deletions")

# The key an extensionless path counts under. A literal, not the empty string,
# so an absent extension is legible in a histogram rather than looking like a
# serialization accident.
_NO_EXTENSION_KEY = "(none)"
# The bucket for a suffix that is not a short alphanumeric extension. A
# NAMED bucket, not a silent drop: the count still reaches the histogram, so
# the distribution stays honest while the free text does not travel.
_ODD_EXTENSION_KEY = "other"


def _diff_size(state):
    """`{files, insertions, deletions, bucket}` for the loop's diff, or None.

    THE POST-CEREMONY STAT WINS when both exist: the ceremony mutates the tree
    after the gate goes green (`/simplify` rewrites code, review findings get
    applied), so the post-ceremony numbers are the ones that describe what
    actually merges.

    Counts only, never hunks — which is what keeps this axis inside the
    no-content-bytes rule. The three integers are computed at the WRITER
    (`run_gate_checks._numstat`), over the GATE'S OWN MUTATION SET rather than
    over `git diff --numstat`, because that diff is blind to untracked files and
    in loop mode a brand-new deliverable file is untracked until PR time. The
    `bucket` is derived HERE, from the two integers, so the threshold table has
    exactly one home and re-cutting it never means rewriting loop-state."""
    lifecycle = _lifecycle(state)
    stat = _as_dict(lifecycle.get("post_ceremony")).get("diff_stat")
    if not isinstance(stat, dict):
        stat = _as_dict(lifecycle.get("gate_green")).get("diff_stat")
    if not isinstance(stat, dict):
        return None
    out = {}
    for key in _DIFF_STAT_FIELDS:
        value = stat.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            return None  # a partial stat is not a stat; absence says so honestly
        out[key] = value
    lines = out["insertions"] + out["deletions"]
    out["bucket"] = next((name for limit, name in _DIFF_SIZE_BUCKETS if lines <= limit),
                         _DIFF_SIZE_BUCKET_MAX)
    return out


def _language_histogram(state):
    """`{extension: count}` over the union of the gate's own mutation-set paths
    across every signature-bearing iteration, or None when no signature exists.

    WHY AN AGGREGATE IS EMITTABLE WHERE THE SET IS NOT: an extension histogram
    names no path. It is a projection of the same fact `diff_size` describes,
    which is why it is class A and is withheld with class A — with the divergence
    path list gone, this is the ONLY derivation of the dropped
    `mutation_signature` path set that reaches the wire, and it is bounded to
    that shape on purpose.

    ⚠️ BUILT OVER A SORTED SEQUENCE, NEVER OVER A `set`. A dict whose insertion
    order comes from iterating a set of strings has PYTHONHASHSEED-dependent key
    order: two calls in ONE process agree (so a determinism test that re-runs
    the builder in-process stays green) while the cross-process, byte-pinned
    conformance fixture flaps. The counts are then re-emitted in sorted key
    order, so the serialized bytes are a function of the input alone."""
    entries = state.get("iterations")
    if not isinstance(entries, list):
        return None
    paths = set()
    seen_signature = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        signature = entry.get("mutation_signature")
        if not isinstance(signature, list):
            continue
        seen_signature = True
        for pair in signature:
            if isinstance(pair, (list, tuple)) and pair and isinstance(pair[0], str):
                paths.add(pair[0])
    if not seen_signature:
        return None
    # 🔴 THE EXTENSION BECOMES A WIRE KEY, so it is bounded rather than trusted.
    # Found by cross-model review 2026-08-14 and reproduced: a mutation-set path
    # `src/x.customer said no` produced the wire key `{"customer said no": 1}`.
    # A file suffix is attacker- and accident-controlled free text — it is
    # whatever follows the last dot in a path a developer chose — so an
    # unbounded one is a prose channel wearing an aggregate's clothes. Anything
    # that is not a short alphanumeric suffix buckets into one named key rather
    # than being emitted: the histogram's job is a DISTRIBUTION, and an
    # unrecognised suffix still counts toward it without naming itself.
    counts = {}
    for path in sorted(paths):
        ext = os.path.splitext(path)[1].lstrip(".").lower() or _NO_EXTENSION_KEY
        if ext is not _NO_EXTENSION_KEY and not (
                len(ext) <= 12 and ext.isalnum() and ext.isascii()):
            ext = _ODD_EXTENSION_KEY
        counts[ext] = counts.get(ext, 0) + 1
    # `{}` is returned, NOT None, when a signature exists and is empty: the gate
    # records an empty signature to mean "nothing has been touched since arm",
    # which is a real answer. Collapsing it to null would make it identical to
    # "no signature was ever recorded", and those are different facts.
    return {ext: counts[ext] for ext in sorted(counts)}


def _stratification(state):
    """JC2 — the two axes an eval set is stratified on: `language` and
    `diff_size`.

    TWO KEYS, NOT THREE. `task_kind` is out of scope by owner decision
    (2026-08-14) and no classifier is built here: the label does not exist
    anywhere in the loop contract today (`grep -E "task_kind|taskKind|task_type"`
    over every loop-state on this machine and over the whole plugin returns
    zero), and a vocabulary invented with no corpus behind it gets re-cut
    anyway. It is tracked on its own card.

    Returns None when neither axis is derivable. Note that class-A WITHHOLDING
    empties the two members in place and never destroys the container — the
    field shape is preserved so a reader can see WHICH axis is missing, exactly
    as `artifacts` is emptied to `[]` rather than removed."""
    language = _language_histogram(state)
    diff_size = _diff_size(state)
    if language is None and diff_size is None:
        return None
    return {"language": language, "diff_size": diff_size}


# JC4's two closed enums, transcribed from the verb that writes them
# (`run_gate_checks.py --human-gate`, whose argparse `choices=` rejects anything
# else before disk is touched). They are re-checked HERE as well, and that is
# not belt-and-braces: loop-state is a local JSON file a human can edit, and a
# verdict is the one field in this object with a plausible free-prose failure
# mode. An unrecognized verdict drops its row; an unrecognized cause drops to
# null. The vocabularies are first cuts derived from the real re-arms on disk —
# RE-CUT once >= 30 causes have been recorded, and record the date.
_HUMAN_GATE_VERDICTS = ("approved", "approved_with_changes_applied_first",
                        "rejected_and_re_armed")
_HUMAN_GATE_REARM_CAUSES = ("review_finding_substantive", "gate_evidence_insufficient",
                            "false_green", "contract_defect", "scope_violation",
                            "requirements_changed", "other")
# Bounded client-side; the server bounds it again at the same number. No loop on
# disk approaches it.
_HUMAN_GATE_MAX_ROWS = 32


def _human_gate(state):
    """JC4 — one row per human-gate visit, in order, or None when no verdict was
    recorded.

    A LIST, NOT A SCALAR. The corpus carries real re-arms after a green gate, so
    a loop that is rejected, re-armed and later approved is a common shape and a
    last-write-wins scalar records only the approval — throwing away exactly the
    data this field exists to capture. The list is append-only and a re-arm never
    clears it: the rejection history IS the signal.

    NO CLOCK, BY RULE. Each row carries `after_iteration_n` — the sequence
    position of the round it judged, taken from `lifecycle.gate_green.iteration_n`
    — and no timestamp. A `human_gate.at` beside `lifecycle.gate_green.at` would
    let the server compute exactly how long the named developer deliberated
    before approving or rejecting, per gate, joined to `owner_session`. That is
    the measurement decision D2 exists to withhold: timestamps on agent-authored
    records only, human-originated ones carry sequence position and no clock.
    (Since JC11 the pre-existing half — the `arm` audit entry's clock, and the
    `arm` transition row's — obeys the same rule. See the D2 block above
    `_ITERATION_FIELDS` for what that closed, for the half that is still open,
    and for why the sequence position this row carries as an explicit
    `after_iteration_n` is STRONGER than the arm row's bare list index.)

    THE ROW IS PROJECTED THROUGH ITS OWN WHITELIST, for the same reason
    `_ITERATION_FIELDS` is one: nothing server-side filters this object, and a
    writer that drifted — or a hand-edited loop-state — must not be able to put
    an operator's own words on the wire by adding a key. The human's reasons stay
    where `reason` and `user_confirmed` already are: dropped."""
    rows = _lifecycle(state).get("human_gate")
    if not isinstance(rows, list):
        return None
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        verdict = row.get("verdict")
        if verdict not in _HUMAN_GATE_VERDICTS:
            continue  # an unrecognized verdict is not a verdict; the row says nothing
        cause = row.get("rearm_cause")
        after = row.get("after_iteration_n")
        out.append({
            "verdict": verdict,
            "rearm_cause": cause if cause in _HUMAN_GATE_REARM_CAUSES else None,
            "after_iteration_n": (after if isinstance(after, int)
                                  and not isinstance(after, bool) else None),
        })
    # The TAIL, when the cap bites: the operative verdict for the current round
    # is the last row, and losing it would make the record unreadable, while
    # losing the oldest rejection of a 33-visit loop costs one data point.
    out = out[-_HUMAN_GATE_MAX_ROWS:]
    return out or None


# ---------------------------------------------------------------------------
# JC5 — the consent stamp on the loop lane.
# ---------------------------------------------------------------------------

# The class VOCABULARY version, and the four bases. Literals HERE and literals
# again in `_insights_session.py` (the ambient lane's own stamp), deliberately:
# importing that module would put the whole ambient lane into this pure
# builder's import graph, and a second copy of the grant LADDER is the drift the
# ambient lane already paid for once. What keeps the two honest is a tripwire
# instead — `test_the_consent_vocabulary_matches_the_one_authority` asserts
# every one of these values against that module, so a rename fails loudly in a
# test rather than quietly on the wire. The RESOLVER itself is imported, never
# copied: see `_live_granted_classes`.
#
# ⚠️ EMITTED AS A LITERAL, not projected from the frozen stamp — and the hazard
# is worth naming rather than discovering. `version` describes the vocabulary
# `classes_at_collection` is written in; if that vocabulary is ever bumped, a
# loop armed under `/1` and flushed by a `/2` builder would be labelled `/2`
# while its letters mean `/1`. Nothing bumps it today and both lanes agree on
# the literal, so the choice costs nothing now; the day a second version exists,
# this becomes a projection of `state["consent"]["version"]`.
CONSENT_VERSION = "fm-consent/1"
# `content_mode` is references-only, permanently — the ONLY accepted value. A
# config carrying anything else, including a future "snippets", normalizes to
# this here and now, because content bytes are gated behind a card that has not
# landed and no code path in either repo may emit them before it does. The field
# exists so a row is self-describing: when that card eventually ships, a row
# collected under references-only is distinguishable from one that was not,
# without a migration.
CONSENT_CONTENT_MODE = "references"
_ALL_CONSENT_CLASSES = ("A", "B", "C")
# Weakest first, and the ORDER is behaviour, not tidiness: an intersecting
# caller picks the weakest basis of the contributing windows with
# `min(..., key=ORDER.index)` (`run_gate_checks._consent_stamp`), so reordering
# these four names silently changes which basis a re-armed loop ships. This
# builder does not intersect — it reports the frozen basis — but it is the same
# sequence as the authority's `CONSENT_BASIS_ORDER` and the tripwire now asserts
# it as an ORDERED sequence rather than as a set, which is the comparison that
# cannot see a swap. An unrecognized basis is not a claim this builder repeats.
# ORDER IS DATA here, weakest-first, and the tripwire below asserts it as a
# SEQUENCE rather than a set: `run_gate_checks` picks the weakest basis of the
# contributing windows with `min(..., key=...index)`, so a reordering silently
# changes which basis a re-arm ships. `unreadable` is first because a record we
# cannot read must not inherit the strength of the window beside it.
_CONSENT_BASES = ("unreadable", "pre_consent", "no_config", "legacy_config", "explicit")


# --- the canonical class->field map, read rather than re-implemented ---------
#
# `consent_class_map.json` sits beside this file, byte-identical to the copy the
# server holds, and BOTH suites pin its sha. The server already reads it as a
# MECHANISM (the query a deletion job will ask); the producer had no equivalent
# and knew only what its own if-statements said — which is exactly how class C's
# half of `iteration_timeline` went unimplemented for a revision with a green
# suite. This is the producer-side reader, and its consumer is the test that
# binds what `build_loop_payload` ACTUALLY narrows to what the map SAYS is in
# each class.
#
# ⚠️ NO RUNTIME CONSUMER IN THE BUILDER, and deliberately so — stated plainly
# rather than left to be discovered, exactly as the server states it. The
# narrowing in `build_loop_payload` stays hand-written because it is genuinely
# field-SHAPED: `artifacts` empties to `[]` for a `Vec<String>` reader,
# `artifact_mutations` to null, `stratification`'s members to null in place, and
# the timeline drops ROWS. A generic dispatcher driven off this map could
# express none of that, and would trade a real distinction for a false economy.
# What the map is read for is the CLASSIFICATION — which field belongs to which
# letter — and that is asserted, not executed.
_CONSENT_CLASS_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "consent_class_map.json")
_CONSENT_CLASS_MAP = None


def consent_class_map():
    """The parsed canonical class->field map.

    RAISES rather than degrading. Every other read in this module tolerates a
    corrupt file, because a corrupt trace or ledger costs one field of one
    payload; a map that cannot be read means the classification has no basis at
    all, and answering "which fields are class B" out of an empty dict would
    report that no field is. Parsed once and cached — the file is immutable at
    runtime, and nothing on the flush path reads it."""
    global _CONSENT_CLASS_MAP
    if _CONSENT_CLASS_MAP is None:
        with open(_CONSENT_CLASS_MAP_PATH, encoding="utf-8") as fh:
            _CONSENT_CLASS_MAP = json.load(fh)
    return _CONSENT_CLASS_MAP


def wire_fields_in_class(door, letter):
    """Every WIRE key on `door` that a revocation of `letter` must narrow,
    sorted — a PROJECTION of the canonical map, never a second list.

    Every classed entry already carries its producer key as `wire`, so this asks
    the artifact instead of restating it. Two entry shapes are excluded, and
    both exclusions are load-bearing rather than tidying:

      * `wire: null` — a stored field with NO producer key. On the loop door
        that is `rawPayload`, which is classed A+B+C because the store keeps a
        verbatim copy of the request; the DOOR builds it, so no producer
        narrowing could reach it and listing it here would make every
        producer-side assertion permanently red.
      * `emitted: false` — a reserved wire slot nothing writes yet (§F.6's
        `reward`). A field that is never on the wire cannot be narrowed off it.
        That a reserved slot must be re-declared before it starts shipping is a
        separate assertion, over the map's own `emitted` flag, in the
        door-coverage test that owns key coverage.

    An unknown door or letter RAISES: the questions "which fields are class Z"
    and "which fields are on door typo" have no true answer, and `[]` would
    read as "none", which is the answer that quietly passes every set
    comparison."""
    if letter not in _ALL_CONSENT_CLASSES:
        raise KeyError(f"{letter!r} is not a {CONSENT_VERSION} class")
    doors = consent_class_map()["doors"]
    if door not in doors:
        raise KeyError(f"no consent class map for door {door!r}")
    spec = doors[door]
    entries = dict(spec.get("fields", {}), **spec.get("derived_stored_fields", {}))
    return sorted(e["wire"] for e in entries.values()
                  if letter in e["classes"] and e.get("wire") is not None
                  and e.get("emitted", True))


def _consent(state, granted_classes):
    """The two-list consent stamp: what was granted WHEN THE DATA WAS COLLECTED,
    and what is actually honoured in THIS payload.

    ALWAYS PRESENT, NEVER NULL, on any loop. A loop armed before this change has
    no `state["consent"]`; it is stamped with all three classes and
    `basis == "pre_consent"`. That removes a null branch from every withholding
    rule and from the server's read, and it makes the flushable loops already on
    disk honest rather than silent.

    TWO LISTS, NOT ONE, and this is what makes withholding legible. With a single
    frozen list, a payload armed under ["A","B","C"] whose class B was revoked
    before flush would carry `classes: ["A","B","C"]` AND `human_gate: null` —
    and `human_gate: null`, documented to mean "no human verdict recorded", would
    be actively misread as an absent verdict rather than a withheld one. With the
    pair: **a field empty under a class present in `classes_at_collection` but
    absent from `classes_applied` is WITHHELD; empty under a class in
    `classes_applied` is GENUINELY EMPTY.**

    `granted_classes` is the LIVE resolution, passed IN by the caller — this
    builder never reads a config, a repo or a clock. `None` means "no live
    resolution available", under which `classes_applied` equals
    `classes_at_collection`: a library caller and the conformance staging both
    get the frozen stamp with no narrowing, which is the only honest answer when
    nothing was resolved.

    A MALFORMED STAMP FAILS CLOSED. `state["consent"]` absent (or not a dict) is
    the pre-change case and grants all three. But a stamp that is PRESENT and
    whose `classes` is not a list is a corrupted record, not an old one — there
    the developer's intent is unknown rather than merely unstated, so it grants
    nothing.

    ⚠️ `pre_consent` IS REACHABLE ONLY FROM AN ABSENT STAMP, and that is a
    decision rather than an accident of ordering. It means "collected before
    this machine existed", which carries the claim that all three classes were
    in force; stamping it over a PRESENT record whose `classes` say `["A"]`
    would be internally contradictory — the server reads `basis` to tell a grant
    that was DECIDED from one that was DEFAULTED, and a narrowed list labelled
    "nobody asked" says both at once. So a present stamp with an unrecognized
    basis reports `explicit`, for exactly the reason the one authority gives for
    an unparseable config file: a record that exists is an attempt at a
    decision, and labelling it otherwise would tell the server nobody had tried.
    The classes are KEPT in that case rather than zeroed — a future writer
    adding a fifth basis name must not cost this loop its whole grant."""
    # 🔴 ABSENT AND MALFORMED ARE DIFFERENT FACTS AT THIS LEVEL TOO. Found by a
    # cross-model review 2026-08-14 and reproduced: `{"consent": "corrupt"}`,
    # `{"consent": null}` and `{"consent": []}` all took the pre-change branch
    # and granted ALL THREE classes. Only a genuinely ABSENT key is the
    # pre-change case; a `consent` that is present and is not an object is a
    # corrupted record, and the rule for that is fail-closed — which this
    # function already applied ONE LEVEL DOWN, to a non-list `classes`, in the
    # paragraph of its own docstring directly above. The inconsistency was
    # internal: the same "present but unreadable" input got opposite answers
    # depending on which level it was unreadable at.
    # ⚠️ `null` BELONGS WITH ABSENT, NOT WITH CORRUPT, and the boundary is
    # exactly there. `test_jc5_a_loop_armed_before_this_change_is_stamped_
    # pre_consent` pins both shapes as the pre-change case with a stated reason
    # — no writer in this engine ever emits `null`, and `null` is what a JSON
    # round-trip or a normalizer produces for a key that was not there. A first
    # cut of this fix failed `null` closed along with the genuinely corrupt
    # shapes and that test caught it, which is the test doing its job: the
    # review finding was real for the shape it NAMED (`{"consent": "corrupt"}`)
    # and widening it past a pinned decision was mine, not the reviewer's.
    raw = state.get("consent")
    if raw is None:
        collected = list(_ALL_CONSENT_CLASSES)   # pre-change loop: no stamp ever written
        basis = "pre_consent"
    elif not isinstance(raw, dict):
        collected = []                            # present, non-null, not an object
        basis = "unreadable"
    else:
        classes = raw.get("classes")
        if isinstance(classes, list):
            collected = sorted({c for c in classes if c in _ALL_CONSENT_CLASSES})
            basis = raw.get("basis")
            if basis not in _CONSENT_BASES:
                basis = "explicit"
        else:
            # THE CLASS LIST ITSELF IS UNREADABLE — non-list, absent, garbage.
            # The record is corrupt rather than oddly-labelled, so whatever
            # `basis` it carries is not evidence of anything and is discarded
            # with it. Owner decision 2026-08-14: this reports `unreadable`,
            # its own name, because the two candidates were both false — this
            # builder used to say `explicit` (a decision was parsed when
            # nothing was read) and the authority `pre_consent` ("all three in
            # force" over an EMPTY grant). A test pinned them apart and said
            # neither was defensible; the fix was to stop choosing between two
            # wrong labels and name the state.
            collected = []
            basis = "unreadable"
    if granted_classes is None:
        applied = list(collected)
    elif not isinstance(granted_classes, (list, tuple, set, frozenset)):
        # 🔴 THE CONTAINER IS VALIDATED, NOT JUST ITS MEMBERS. Found by a
        # cross-model review 2026-08-14 and reproduced: the membership filter
        # below iterates whatever it is given, so the STRING "ABC" iterated to
        # three single characters and granted all three classes, and the dict
        # `{"A": False}` iterated to its KEYS and granted A — a malformed live
        # resolution failing OPEN on the one axis whose whole job is to narrow.
        # An int raised `TypeError` and took the entire flush down with it.
        # Anything that is not a real collection is treated as "no class
        # granted", which is the fail-closed direction and the same answer the
        # authority gives a malformed config.
        applied = []
    else:
        live = {c for c in granted_classes if c in _ALL_CONSENT_CLASSES}
        applied = sorted(set(collected) & live)
    return {
        "classes_at_collection": collected,
        "classes_applied": applied,
        "version": CONSENT_VERSION,
        "basis": basis,
        "content_mode": CONSENT_CONTENT_MODE,
    }


def build_loop_payload(cwd, base=None, *, granted_classes=None, repo_root=None):
    """Assemble the `Insights_record_loop_stats` wire payload for the closed
    loop this repo's active-context.json / base_path currently names.
    Deterministic: every field is read off disk, nothing from the clock.

    `granted_classes` is the LIVE consent resolution (JC5), passed IN by the
    caller — the builder never reads a config, a repo or a clock (see the module
    contract at the top of this file). `None` means "no live resolution
    available", under which `classes_applied` equals `classes_at_collection` and
    nothing is withheld.

    It is keyword-WITH-DEFAULT rather than required on purpose: there are ~30
    existing call sites across the test suite and a required kwarg would rewrite
    all of them for no safety gain. The safety comes from the CLI instead, which
    ALWAYS passes an explicit value — a defaulted CLI path is how a live revoke
    silently stops being applied, and `test_the_cli_always_passes_an_explicit_
    granted_classes` is what stops that.

    `repo_root` is the second value of that kind and it is passed in for the same
    structural reason: it is `git rev-parse --show-toplevel`, which this function
    may not call. It is the anchor every emitted artifact path is made relative
    to (`_wire_target`), and `None` means "no root was resolved", under which
    every ABSOLUTE target is replaced by `OUTSIDE_REPO_TARGET` rather than
    relativized against some nearby directory. It defaults for the same reason
    `granted_classes` does — ~30 existing call sites — and the safety again comes
    from the CLI always passing an explicit value, pinned by
    `test_the_cli_always_passes_an_explicit_repo_root`.

    THE LIVE RESOLUTION CANNOT HAPPEN HERE, three ways, all of them structural:
    this function makes zero git/config/env calls; the consent config resolves
    through a git toplevel while the conformance staging is a bare `mkdtemp`
    with no `git init`, so a live read here would deterministically pin an
    all-withheld payload into the sha-sealed fixture; and it is the split the
    ambient lane already got right, where the resolver is per-call at the
    caller."""
    # `repo_root` is the toplevel the CLI already resolved; `cwd` is the
    # fallback for the ~30 call sites that pass none, and is what this builder
    # read before the marker moved to the root. Not a `_git_toplevel()` call:
    # the docstring above says this function makes zero git calls, and it is
    # still true.
    root = repo_root or cwd
    base = _resolve_base(root, base)
    ctx = _active_context(root)

    state = _as_dict(_read_json(os.path.join(cwd, base, "loop-state.json"), {}))
    target_ref = _as_dict(state.get("target")).get("ref")
    spent = _as_dict(_as_dict(state.get("budget")).get("spent"))
    started_at = spent.get("started_at")

    loop_id, closed_at = _loop_identity(cwd, state)

    task_ref = ctx.get("task_ref") or target_ref
    trace_rows = _read_jsonl(_trace_path(cwd, task_ref))
    # The `isinstance` guard is NOT decoration: `_read_jsonl` deliberately
    # tolerates a corrupt trace ledger, and a bare JSON scalar on a line
    # parses to a str — on which `.get` raised, crashing the whole flush. The
    # token-ledger reader has always had this guard; the two trace readers did
    # not (found by the T2-C2 degrade test).
    # DERIVED from the mutation counts, not filtered a second time. The two
    # answer different questions — which files were touched, and how often —
    # but "which rows count as a mutation" is ONE rule, and it must stay one:
    # `artifacts` is by construction the distinct targets of the same rows.
    #
    # The forward hazard this closes is specific. Filtering the loop path for
    # privacy is deferred, documented work (PCF-30): these targets are absolute
    # developer paths today. Whoever lands that filter will normalize `target`
    # in the derivation they happen to be reading — and with two copies, the
    # payload would then carry a filtered `artifacts` beside an unfiltered
    # `artifact_mutations`, leaking exactly the paths the filter was added to
    # remove. The same applies to any future trace `kind` that means "mutation".
    #
    # That filter is this one, and it landed where the comment said it had to:
    # `_wire_target` is called from the single walk, so `artifacts` and
    # `artifact_mutations` are relativized by construction rather than by two
    # call sites agreeing.
    artifacts, artifact_mutations = _loop_artifact_mutations(trace_rows, repo_root)

    checks = state.get("checks")
    iterations = spent.get("iterations")

    # JC5 ENFORCEMENT — the stamp is a label, this is the teeth. Withholding is
    # driven by `classes_applied`, i.e. the frozen grant INTERSECTED with the
    # live resolution the caller passed in. With the default resolution (no
    # config file, or a config with no `consent` block — which is the entire
    # real population today) all three classes are applied and every field keeps
    # its exact current value: this change is behaviour-neutral on every repo
    # that exists, file or no file. That property is what makes it shippable.
    consent = _consent(state, granted_classes)
    applied = set(consent["classes_applied"])

    # Class A — merged diffs. `artifacts` empties to `[]` (it has a live reader
    # that forwards it into a downstream store as a `Vec<String>`; `null` there
    # would be a different type, not an empty one), `artifact_mutations` to
    # null, and `stratification`'s two members to null IN PLACE — the container
    # survives so a reader can see which axis went missing.
    stratification = _stratification(state)
    if "A" not in applied:
        artifacts = []
        artifact_mutations = None
        if stratification is not None:
            stratification = {"language": None, "diff_size": None}

    # ⚠️ `iteration_timeline` IS THE ONE FIELD TWO CLASSES REACH, so the whole
    # applied SET is handed to the ONE builder that owns the field — not one
    # flag per letter. Splitting the letters across the two class blocks below
    # is what let class C's half go unimplemented for a revision: the class-C
    # block narrowed `agents` / `transitions` / `mutation_divergence` and simply
    # never mentioned the timeline, and nothing in the code said it should have.
    # Passing the set is what makes that omission unexpressible here.
    #
    #   * Class B — rejected proposals: the non-green iterations LEAVE the
    #     timeline, and `human_gate` goes null.
    #   * Class C — generation context: the ALL-GREEN iterations leave it. The
    #     field is row-granular in `consent_class_map.json`, so this is a row
    #     drop and not a key strip — a green row's `at`/`commit_sha` are class C
    #     while a rejected row's are class B, the same keys in two classes.
    #
    # The structural audit rows are UNCLASSIFIED and survive both, untouched.
    # See `_loop_iteration_timeline` for the measured defect this closes, for why
    # neither class withholds cardinality, and for the wire-shape predicate that
    # lets the server VERIFY both withholdings rather than trust the stamp.
    timeline = _loop_iteration_timeline(state, applied)
    class_b = "B" in applied
    class_c = "C" in applied
    human_gate = _human_gate(state) if class_b else None

    # The rest of class C. `mutation_divergence` is class C and NOT class A:
    # with the path list gone what remains is two one-way digests, a boolean and
    # a cardinality — none of it diff content, none of it naming a file — and
    # its whole subject is what the process did between the gate and the merge,
    # the same altitude and the same subject as `transitions`.
    agents = _loop_agents(cwd, base, started_at, closed_at, trace_rows) if class_c else []
    transitions = _loop_transitions(state) if class_c else None
    mutation_divergence = _mutation_divergence(state) if class_c else None

    return {
        "loop_id": loop_id,
        "target_ref": target_ref,
        "status": state.get("status"),
        "tier": state.get("hermeticity_tier", "B"),
        "checks": len(checks) if isinstance(checks, list) else 0,
        "iter": iterations if isinstance(iterations, int) and not isinstance(iterations, bool) else 0,
        "started_at": started_at,
        "closed_at": closed_at,
        "owner_session": state.get("owner_session"),
        # `_project`, not a raw ctx key: this builder read `projectId` while
        # every real active-context.json wrote `project`, so this field was
        # the literal "unknown" on every loop payload ever flushed. The
        # "unknown" default is kept (it is the wire+schema default on the
        # server side too) but is now genuinely the no-project case.
        "project_id": _project(ctx) or "unknown",
        "task_ref": task_ref,
        "artifacts": artifacts,
        # The repetition `artifacts` set-collapses away, and the machine-checked
        # clock `iter` collapses to one integer (T2-C2). Both are additive:
        # `artifacts` and `iter` keep their exact previous values.
        "artifact_mutations": artifact_mutations,
        "iteration_timeline": timeline,
        "agents": agents,
        "contract_version": LOOP_CONTRACT_VERSION,
        # JC1..JC5. Additive and optional on the server, which defaults every
        # one of them — so a `/1` payload and a `/2` payload are both accepted
        # and the deploy order does not matter. `transitions`, `human_gate` and
        # `mutation_divergence` are NULL rather than `[]`/`{}` when unknown:
        # absence is the signal, and an empty list would claim the loop had no
        # transitions / no verdict, which is a different (and usually false)
        # statement. `iteration_timeline` keeps its `[]` — it is a pre-existing
        # unconditional field and narrowing it would be a wire change.
        "transitions": transitions,
        "mutation_divergence": mutation_divergence,
        "stratification": stratification,
        "human_gate": human_gate,
        # NEVER NULL, on any loop — see `_consent`.
        "consent": consent,
    }


# ---------------------------------------------------------------------------
# Decisions batch
# ---------------------------------------------------------------------------

def _decision_wire_path(raw, root, root_real, memo):
    """One agent-written path from `decisions.jsonl` -> the string the wire may
    carry: the RAW bytes for a repo-relative value, a relativized path for an
    absolute in-repo one, `OUTSIDE_REPO_TARGET` for anything that escapes.

    TOTAL, AND IT NEVER RETURNS None. Round 1 had two spellings of "refused" —
    None for a relative climb, the marker for an absolute escape — while this
    same summary already claimed the marker for both; a cross-model review read
    the summary, then the code, and found them disagreeing. None is also
    OVERLOADED one function down: in `_wire_target` it means "not a target at
    all", a third meaning neither caller here wants. Both call sites were
    already collapsing the two spellings back into one disposition (`x or
    MARKER`; `not x or x == MARKER` -> the same `continue`), so returning the
    marker in both cases is byte-identical by inspection and lets those clauses
    go. `_wire_target` cannot return None from here either: its only None is
    `not isinstance(raw, str) or not raw`, and `raw` is a non-empty `str` by the
    callers' `_nonempty_str` pre-filter.

    🔴 WHY THIS IS A WRAPPER AND NOT A SECOND CONTAINMENT RULE. `_wire_target`
    stays the ONE writer of containment, the realpath fallback, the marker and
    the memo; nothing is copied out of it. What is added is one predicate for a
    question it has no byte-neutral answer to on THIS lane — is a RELATIVE value
    an escape?

    THE TWO LANES LEGITIMATELY DIFFER, AND THE REASON IS MEASURED. `_wire_target`
    bans `..` as a SUBSTRING, which also catches `trace-op.sh`'s truncation
    marker (`tr(s, 120)` -> a trailing `"..."`); re-measured over 48 trace
    ledgers, 2,729 mutate targets, 47 carry `..` and ALL 47 are that marker, 0
    have `..` as a component. `decisions.jsonl` is written by an agent's `jq`
    append and is never touched by `trace-op.sh`, so that justification has no
    analogue here — while the substring ban DOES delete well-formed agent paths:
    `src/app/api/auth/[...nextauth]/route.ts`, `docs/v1.2..v1.3.md`, `a/../b.py`,
    8 of 92,942 real tracked paths on this machine. So this lane gets a
    COMPONENT test on the normalized path and EMITS THE RAW BYTES. Widening
    `_wire_target` itself would reopen a real loop-lane hole; narrowing it for
    both lanes is refused for that reason.

    WHY RELATIVIZING AN ABSOLUTE VALUE IS NOT A CONTRACT CHANGE — the rule-1
    argument, which is the DOCUMENTED PRODUCER SHAPE and not a downstream
    property. `agents/software-engineer.md`'s `## Decision capture` block —
    byte-identical in `tech-lead.md` and `qa-engineer.md`, pinned by
    `test_decision_convention.py` — says `files` lists paths "repo-relative with
    no leading slash (`app/services/needService.py`, **not**
    `/path/to/repo/app/...`)". An absolute path is named there as wrong, and a
    leading `..` is not repo-relative by definition. Losslessness is a SECOND,
    independent reason the movement is harmless (the server expects paths
    repo-relative, so an absolute value matches nothing today and the
    projected one matches) — but it can
    never license a rewrite on its own: normalizing `./app/x.py` -> `app/x.py`
    would be equally lossless and is deliberately NOT done, because the
    convention permits `./app/x.py`.

    `raw` is always a non-empty `str`: both callers pre-filter through
    `_nonempty_str`. No isinstance/empty pre-check is written here on purpose —
    an unreachable clause is dead coverage that reads as protection, which
    `_wire_target`'s own docstring records as a defect this module already paid
    for.

    THE RESIDUAL IS A CLASS, NOT A LIST OF SHAPES, and it is stated that way so
    nobody reads this as closing the field: EVERY non-climbing relative value is
    emitted byte-for-byte, so any account-bearing string that is neither
    absolute nor a leading-`..` climb still ships — `Users/<account>/secret.py`,
    `~<account>/Projects/secret.py`, and a Windows drive-letter absolute (`C:`
    followed by backslash-separated components) — for which `os.path.isabs` is
    False on posix, so it never reaches `_wire_target` at all, and which is
    DISTINCT from a backslash CLIMB, whose "a legitimate posix filename
    containing backslashes" justification does not transfer to it. NONE of these is introduced here — the unprojected producer
    emitted them identically — so the honest claim is that this closes the
    ABSOLUTE and CLIMBING classes, not that `files[]` is unleaky. Note also that
    this residual is NOT `_wire_target`'s own stated one: that one needs a root
    so shallow the account directory is inside the repo, while this one occurs
    at ANY root depth, because a relative value is never compared against the
    root at all.

    An ABSOLUTE in-repo path whose repo-relative form carries `..` (a catch-all
    segment under an absolute root) becomes the marker rather than the relative
    path. Accepted: recovering it means re-deciding containment outside
    `_wire_target`, and the input is in the leaky class either way, so no
    well-formed byte moves."""
    if not os.path.isabs(raw):
        # The convention's OWN shape. TEST on the normalized path, EMIT THE RAW
        # BYTES — a component test, never a substring scan.
        if posixpath.normpath(raw).split("/")[0] == "..":
            return OUTSIDE_REPO_TARGET
        return raw
    return _wire_target(raw, root, root_real, memo)


def _git_remote(cwd):
    remote = audit_run_meta._run_git(cwd, "remote", "get-url", "origin")
    if remote.returncode != 0:
        return None
    return audit_run_meta.normalize_git_remote(remote.stdout.strip())


def _decision_id(row):
    """Stable id: same (agent, at, decision, rationale) -> same id, always —
    so a re-flush of an unchanged decisions.jsonl never produces new ids."""
    key = [row.get("agent"), row.get("at"), row.get("decision"), row.get("rationale")]
    digest = hashlib.sha256(
        json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return "sha256:" + digest


def _decision_files(row, root, root_real, memo):
    """The paths a captured decision says it concerns, projected to the wire, or
    None when it names none — blank/non-string entries dropped, order preserved,
    duplicates collapsed.

    Repo-relative on the wire, EXCEPT for a withheld entry, which is
    `OUTSIDE_REPO_TARGET` and is therefore neither a path nor repo-relative.
    That marker is deliberate: a row that recorded paths must still SAY it
    recorded paths. It is this module's own rule one lane over — the marker
    "withholds WHAT the path was and never THAT the mutation happened" — and it
    is inert past the boundary, where `files` is stored as a plain list
    property with no pattern matching behind the write, and the one query over
    it is an exact element match whose own comment says "never a regex". ACCEPTED as well
    as inert, which is the half that decides it: the consumer declares
    `files: List[str]` and `functions: Optional[List[dict]]` with no `pattern=`,
    no `constr` and no `field_validator`, and `record_decisions` validates
    fail-fast atomically — a rejecting validator would have turned this privacy
    fix into total batch loss. Verified by NEGATIVE SPACE rather than by a site
    list: no `os.path.`, `PurePath`, `Path(`, `splitext`, `dirname`, `basename`,
    `startswith("/")` or `split("/")` anywhere over these values.

    None still means NOT OBSERVED: the row was written under the original
    four-field convention (agent/decision/rationale/at), which had no way to
    say. It is NOT the same as "this decision touched no code", and the caller
    omits the key rather than sending `[]`, which would assert the second — so
    the third state survives and is now genuinely distinct from
    observed-and-withheld.

    ⚠️ WIRE FIRST, THEN DEDUP, matching how `_loop_artifact_mutations` builds
    its set. The two orders differ only where two distinct raw strings project
    to one path, which needs at least one absolute value; deduping the raw value
    would then emit the same path twice. On an all-relative list they are
    identical, because the relative branch emits `raw`."""
    raw = row.get("files")
    if not isinstance(raw, list):
        return None
    out = []
    for entry in raw:
        path = _nonempty_str(entry)
        if not path:
            continue
        wired = _decision_wire_path(path, root, root_real, memo)
        if wired not in out:
            out.append(wired)
    return out or None


def _decision_functions(row, root, root_real, memo):
    """The function refs a captured decision names, or None.

    Each surviving ref is narrowed to the keys the ref itself carries for
    the server to identify a function by — `file_path`, `name`, and
    `start_line` when the row knows it; the repository comes from the
    checkout's binding, never from the ref. A ref missing either `file_path`
    or `name` can never be linked, so it is dropped here rather than shipped
    to fail silently server-side;
    `start_line` is NEVER invented, since a wrong line links nothing at all
    (exact equality, not a range).

    This is the ONLY field that can link a decision to code: `files` is
    stored as plain data with no link behind it.

    ⚠️ A WITHHELD `file_path` DROPS THE WHOLE REF, where `files[]` emits the
    marker — and the asymmetry is measured, not stylistic. `file_path` is one
    of the keys the link is made on, so a marker there would store a fake
    path in a KEY field and link nothing.
    Dropping is this function's existing failure mode for an unmatchable ref.
    NO DEDUP IS ADDED: there is none today, and adding one would collapse two
    identical refs into one — a byte change on well-formed input.

    THE COST, named beside the `continue`: a row whose ONLY record was such refs
    becomes indistinguishable from a row that recorded none. That collapse is
    pre-existing behaviour for an unmatchable ref, and when the same path
    appears in both fields — which the documented `jq` example does exactly —
    the `files[]` marker preserves the observed bit for the row."""
    raw = row.get("functions")
    if not isinstance(raw, list):
        return None
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        # BOTH NATURAL-KEY HALVES FIRST, THEN THE PROJECTION. Round 1 projected
        # before knowing whether `name` was present and then tested `not
        # file_path or file_path == MARKER or not name` — three clauses for two
        # dispositions, one of which (`not file_path`) only existed because the
        # projection had a second refusal spelling. Output-identical in all five
        # cases (missing path, missing name, marker, climb, ordinary path); the
        # one observable difference is that `_wire_target`'s memo no longer gets
        # an entry for a ref that has a path but no name, and that memo is a
        # pure per-batch cache, so no emitted byte depends on it.
        file_path = _nonempty_str(entry.get("file_path"))
        name = _nonempty_str(entry.get("name"))
        if not file_path or not name:
            continue
        file_path = _decision_wire_path(file_path, root, root_real, memo)
        if file_path == OUTSIDE_REPO_TARGET:
            continue
        ref = {"file_path": file_path, "name": name}
        start_line = entry.get("start_line")
        if isinstance(start_line, int) and not isinstance(start_line, bool):
            ref["start_line"] = start_line
        out.append(ref)
    return out or None


def _decision_wire_row(row, root, root_real, memo):
    """One `.fairmind/insights/decisions.jsonl` row -> one wire decision.

    `kind`/`files`/`functions` come from the CAPTURED row and are OMITTED
    when it does not carry them. Until T1·C1 all three were hardcoded here
    (`"process"`, `[]`, `None`), which meant every decision this system has
    ever flushed claimed — falsely, and indistinguishably from a real
    observation — to be a process decision concerning no code, and no decision
    was ever linked to code, no matter what the agents actually recorded.

    An absent key therefore means NOT RECORDED, never "recorded as empty":
    the server's `kind` is `Optional` for exactly this reason and the four
    older, always-present fields are the only ones that can be assumed. A
    default that pretends to be an observation is worse than a gap, because a
    gap can be seen. `kind` is passed through VERBATIM (the server accepts
    a kind this producer has never heard of, so validating it here would
    only reject the future).

    DELIBERATELY NOT DONE HERE, NAMED SO THE SET IS COMPLETE: `ts` is
    `row.get("at")` and travels UNGUARDED, while this same module applies
    `_looks_like_timestamp` to the identical value class on both other doors
    (`_ITERATION_FIELD_GUARDS["at"]`, `_AUDIT_RUN_META_GUARDS["executed_at"]`).
    The predicate is already here; what is missing is the containment argument,
    and it is missing for a real reason rather than an oversight. Those two
    values are written by CODE (`iso(now_utc())`, `audit_run_meta._iso_now`) —
    a construction whose range can be stated. This one is written by an AGENT:
    `agents/software-engineer.md`'s capture block passes `--arg at
    "<iso-8601-utc>"` through `jq`, so the documented shape is a convention the
    writer is asked to follow, not a range the writer is confined to. Guarding
    it would null the field on every row where an agent wrote something else,
    which is a byte change on data this producer really emits and therefore its
    own card, with its own measurement of how many rows on disk it would move.
    `agent`/`title`/`rationale` are free prose by contract and are open for the
    ordinary reason. JC31 closed the two PATH fields and nothing else on this
    door."""
    wire = {
        "decisionId": _decision_id(row),
        "ts": row.get("at"),
        "agent": row.get("agent"),
        "title": row.get("decision"),
        "rationale": row.get("rationale"),
    }
    kind = _nonempty_str(row.get("kind"))
    if kind:
        wire["kind"] = kind
    files = _decision_files(row, root, root_real, memo)
    if files:
        wire["files"] = files
    functions = _decision_functions(row, root, root_real, memo)
    if functions:
        wire["functions"] = functions
    return wire


def _run_start_line(ctx, line_count):
    """The ledger line the run `active-context.json` names began at — the mark
    `loop_open.py --repoint` records when it opens a new run — or None when
    there is no usable mark: absent, not a line count, or past the end of the
    readable ledger (rewritten shorter, or cut short by an undecodable byte).
    None means nobody can tell which rows are that run's."""
    start = ctx.get(loop_open.DECISIONS_START_KEY)
    if isinstance(start, bool) or not isinstance(start, int):
        return None
    return start if 0 <= start <= line_count else None


def build_decisions_batches(cwd, base=None):
    """Assemble the `Insights_record_agent_decisions` wire payloads from the
    PL-3 decision-capture convention's `.fairmind/insights/decisions.jsonl`:
    a list of batches, none of them empty.

    ONE BATCH PER ATTRIBUTION. Both decision doors (`Insights_record_agent_
    decisions`, `Brain_record_decision`) stamp a batch's `session_ref`/
    `task_ref` onto EVERY row they store and read no attribution off a row,
    while the ledger is shared by every run that ever wrote to it in this
    checkout. So the rows appended since the run `active-context.json` names
    was opened travel in one batch under its refs, and every earlier pending
    row travels in a second batch whose refs are null — unattributed, never
    relabeled as this run's.

    🔴 NO USABLE MARK MEANS NO REFS AT ALL (`_run_start_line`): a context
    written before the mark existed, one kept by a same-ref repoint, a mark the
    readable ledger no longer reaches. Nothing then says which rows are this
    run's, so every row travels in the one null-ref batch. Stamping the run's
    refs instead would label a whole backlog with one loop, permanently.

    🔴 THE SPLIT IS BY APPEND POSITION, NEVER BY A ROW'S `at`. The agent types
    that clock (the capture convention's `--arg at`), and the Technical Lead
    records its Phase-0 decisions before `--arm` stamps `started_at`, so a time
    window files a run's own decisions as someone else's — and both doors keep
    the first attribution a decisionId arrives with."""
    # ONE RESOLUTION FOR THE WHOLE PAYLOAD — the marker's home, the
    # `repository` basename and the root every emitted path is relative to are
    # the same fact, and this builder used to resolve it twice.
    root = _git_toplevel(cwd)
    base = _resolve_base(root, base)
    ctx = _active_context(root)
    state = _as_dict(_read_json(os.path.join(cwd, base, "loop-state.json"), {}))

    lines = list(loop_open.ledger_lines(_decisions_path(cwd)))
    start = _run_start_line(ctx, len(lines))
    # THE TOPLEVEL IS RESOLVED ONCE, ABOVE, and used four ways: the marker's
    # home, the `repository` basename, the root every emitted path is expressed
    # relative to, and the binding's own anchor. Resolved HERE rather than
    # threaded in from the CLI: this builder already shells to git and its
    # conformance staging does a real `git init`, so neither of the reasons the
    # loop lane threads its root applies — and threading one in is what would
    # CREATE a two-writer problem, with the CLI resolving the toplevel for the
    # projection while a second helper re-resolved it for `repository`. Net new
    # git calls: zero — the resolution above REPLACED the one that used to sit
    # on this line. One new `os.path.realpath`, a read-only path resolution —
    # not git, not config, not the clock — so the module contract at the top of
    # this file stands.
    #
    # ONE MEMO PER BATCH, threaded into both helpers for every row, so an
    # ABSOLUTE path appearing in `files[]` and again in a `functions[]` ref
    # resolves once. ABSOLUTE IS THE WHOLE OF THE CLAIM, corrected 2026-08-20
    # from a version that said "a path": `_decision_wire_path` returns on its
    # relative branch WITHOUT touching the memo, and repo-relative is the
    # documented common case — `agents/software-engineer.md` tells the agents to
    # write nothing else — so on a well-formed batch this memo is empty and
    # saves nothing. It is kept because the case it does cover is the expensive
    # one (`os.path.realpath` touches the filesystem), not because the common
    # path benefits. `_wire_target`'s memo is keyed on `raw` alone and so
    # assumes one `(root, root_real)` per memo — satisfied, since a batch has
    # one root.
    root_real = os.path.realpath(root)
    # ONE resolution of the origin, used twice: as the payload's own
    # `git_remote` and as what the binding above is checked against. Two calls
    # would be the same command microseconds apart, and a repoint landing
    # between them would key the payload's repository against a different
    # remote than the one it reports.
    git_remote = _git_remote(cwd)
    memo = {}
    # BOUND FIRST, folder name second. `os.path.basename(root)` is what this
    # producer has always sent and is what the server can least often resolve;
    # the catalog id is what `/fairmind-connect` established for this checkout.
    # An unbound checkout takes the second branch and its payload is unchanged
    # byte for byte.
    repository = _bound_repository(ctx, git_remote) or os.path.basename(root)

    def wire_rows(ledger_lines):
        return [_decision_wire_row(r, root, root_real, memo)
                for r in _parse_jsonl_lines(ledger_lines) if isinstance(r, dict)]

    if start is None:
        groups = ((wire_rows(lines), None, None),)
    else:
        groups = ((wire_rows(lines[start:]), state.get("owner_session"), ctx.get("task_ref")),
                  (wire_rows(lines[:start]), None, None))
    batches = [{
        "repository": repository,
        "decisions": decisions,
        "git_remote": git_remote,
        "session_ref": batch_session_ref,
        "task_ref": batch_task_ref,
        "contract_version": DECISION_CONTRACT_VERSION,
    } for decisions, batch_session_ref, batch_task_ref in groups if decisions]
    # `project` is not decoration: server-side it is the ONLY input that
    # resolves the repository under scheme `code-ingestion`, and a decision
    # whose scheme is anything else has its function refs discarded before
    # any link is attempted. This builder emitted no `project` key at all
    # until T1·C1, so a decision could never be linked to code from this
    # producer by construction. Omitted when active-context.json
    # names none — same rule as the correlation keys above.
    project = _project(ctx)
    if project:
        for batch in batches:
            batch["project"] = project
    return batches


# ---------------------------------------------------------------------------
# Audit payload — this builder OWNS the `Insights_record_harness_audit` wire
# contract; `commands/harness-audit.md` step 5 delegates to it (via `--emit
# audit`/`--commit audit`) rather than hand-assembling the payload itself.
# Assembled from the three FIXED per-repo files that command writes:
# `.fairmind/audit/run-meta.json` (repo identity), `.fairmind/audit/summary.json`
# (the 9 pillar rollups) and `.fairmind/audit/assessment.jsonl` (the 81
# per-criterion verdicts underneath them — T2-C2; the rollups alone can say a
# pillar dropped but never WHICH criterion did, and the pillar counters are
# derived from exactly these rows). Unlike the loop/decisions
# builders these paths are NOT base-relative — an audit run is per-repo,
# not per-loop, same as the sync cursor itself. The T1·X1 correlation keys are
# the one base-relative read here (`loop-state.json`, for the owning session):
# WHICH repo was audited is per-repo, but WHEN/under-what it was audited is
# per-loop, and that is exactly the join the audit run was missing.
# ---------------------------------------------------------------------------

# JC13 — the three row whitelists and their guard maps.
#
# ⚠️ THE TUPLES REPRODUCE THE WIRE'S DICT-LITERAL ORDER, NEVER THE DISK ROW'S,
# and the two differ where it bites: `harness_audit` writes a pillar as
# `id, name, level, criteria_total, criteria_passed, levels` while the wire has
# always emitted `criteria_passed` BEFORE `criteria_total`. `_project_row`
# iterates `fields`, so these tuples ARE the emitted key order, and
# `_payload_text` serializes with `sort_keys` defaulted False — while every
# existing conformance assertion sorts and is structurally blind to a reorder.
_AUDIT_PILLAR_FIELDS = ("id", "name", "level", "criteria_passed", "criteria_total")
_AUDIT_DIMENSION_FIELDS = ("id", "score", "status")
_AUDIT_CRITERION_FIELDS = ("criterion_id", "pillar_id", "level", "verdict")

# 🔑 EVERY NORMALIZER IS `_as_is`, WITHOUT EXCEPTION. Round 2 died because
# `_nonempty_str` returns `value.strip()` and `_project_row` assigns
# `value = normalize(value)` before emitting, so ` leadspace` — a legal APFS
# directory name — passed its predicate and was still rewritten, silently, with
# the whole suite green. There is no allowlist of fields permitted to rewrite.
#
# 🔑 `row_guards` IS ABSENT ON ALL THREE. Not one row guard exists in this
# change, so no array can shorten, `totals` can never desynchronize from
# `pillars`/`criteria`, and `_project_row` can never return None. The
# `isinstance(row, dict)` and `row.get("criterion_id")` filters stay exactly
# where they were, OUTSIDE this projection.
#
# The fields absent from each map are DECLARED OPEN, each for a reason recorded
# at its predicate or below: `pillars[].id`/`pillars[].name`/
# `criteria[].criterion_id`/`criteria[].pillar_id` are catalog strings whose
# validated range is "any non-empty string" (`--catalog` is a shipped producer
# path), and any charset tight enough to refuse prose already refuses the
# SHIPPED `Debugging & Observability`.
_AUDIT_PILLAR_FIELD_GUARDS = {
    "level": (_as_is, _is_count),
    "criteria_passed": (_as_is, _is_bounded_count),
    "criteria_total": (_as_is, _is_bounded_count),
}
_AUDIT_DIMENSION_FIELD_GUARDS = {
    "id": (_as_is, _is_dimension_id),
    "score": (_as_is, _is_unit_score),
    "status": (_as_is, _is_dimension_status),
}
_AUDIT_CRITERION_FIELD_GUARDS = {
    "level": (_as_is, _is_count),
    "verdict": (_as_is, _is_audit_verdict),
}
# The top-level scalars. `repository` and `criteria_version` carry NO guard:
# `repo_name` is `os.path.basename` of a filesystem path, so its producer range
# is every string without `/` or NUL — the field that killed round 2 — and
# `criteria_version` is presence-checked only (`validate_catalog`), so even
# `isinstance(str)` is not a containment claim, and it is the one field whose
# producer range includes a MUTABLE CONTAINER. Leaving it open is what keeps
# every predicate on this door from ever receiving one.
_AUDIT_RUN_META_GUARDS = {
    "commit_sha": (_as_is, _looks_like_sha),
    "executed_at": (_as_is, _looks_like_timestamp),
    "git_remote": (_as_is, _is_https_ref),
}
_AUDIT_TOTALS_GUARDS = {
    "criteria": (_as_is, _is_bounded_count),
    "passed": (_as_is, _is_bounded_count),
}


def _audit_pillar_wire(p):
    # `name` is REQUIRED by the consumer: the server dispatches a pillar carrying
    # `criteria_passed`/`criteria_total` into its own pillar normalizer, which reads
    # `pillar['name']` and raises without it. It was dropped here while the server's
    # own docstring quoted `harness_audit.py::evaluate_catalog` (which has it) as the
    # producer — the shape that crosses the wire is the one this function builds.
    # That is also why `name` can be NEITHER a field guard (the consumer raises
    # without it) NOR a row guard (dropping the pillar lies about the catalog
    # while `totals` still describes nine): the only admissible disposition is
    # no guard, which is what it has.
    return _project_row(p, _AUDIT_PILLAR_FIELDS, _AUDIT_PILLAR_FIELD_GUARDS)


def _audit_dimension_wire(dim):
    # A row whose every field is refused becomes `{}`. That is CHOSEN, not an
    # artifact: refusing the row would install a row guard, and `dimensions` has
    # no counter in `totals`, so the empty object is the cheaper of the two.
    return _project_row(dim, _AUDIT_DIMENSION_FIELDS, _AUDIT_DIMENSION_FIELD_GUARDS)


def _audit_criterion_wire(row):
    """The four fields of an `assessment.jsonl` record that cross the wire.

    SELECTION, not truncation: the row `harness_audit.py` writes has eight
    fields, and the four dropped ones (`primitive`, `detail`, `title`,
    `expected`) are engine free prose — on a real 81-criterion run, 38 rows
    carry path-shaped tokens inside `detail`/`expected` (`path=.editorconfig`,
    `path=tests/**/*.py`). Nothing server-side privacy-filters an audit run
    (`sanitize_session_artifacts` runs on the session door only), so the four
    that cross have to be the four that are safe to store unfiltered:
    catalog-controlled identifiers, an int, and a two-valued verdict.

    Dropping them is also what keeps the payload small — 7.5 KB of criteria at
    full scale instead of 19.9 KB — and size is a correctness property here,
    not an optimization. NOT because the consumer would reject it: the MCP
    door has no body cap. Because the payload has to survive the 30,000-byte
    agent-output channel between this script and the MCP call (see the module
    docstring), where an oversized result is not refused but silently replaced
    by a preview — a failure with no error to catch.

    Selecting at the producer rather than capping the count is deliberate: a
    cap drops whichever criteria fall past the limit, which is precisely the
    criterion someone is querying for.

    ⚠️ `verdict` IS A FIELD GUARD AND MUST NEVER BECOME A ROW GUARD. Reusing the
    loop door's `_is_wire_verdict` (green/red/error/inconclusive) as a row guard
    here would reject all 81 criteria, `criteria` would become `[]`, `if
    criteria:` would omit the key entirely, and `totals` would still say 81.
    `criterion_id` is open for the mirror-image reason: a field guard drops the
    key and leaves exactly the unnamed criterion this array must not carry,
    while a row guard desynchronizes it from `totals`. Neither is admissible, so
    it carries no guard."""
    return _project_row(row, _AUDIT_CRITERION_FIELDS, _AUDIT_CRITERION_FIELD_GUARDS)


def _audit_criteria_wire(cwd):
    """The per-criterion verdicts for the audit run on disk, in the catalog's
    own order, or `[]` when `.fairmind/audit/assessment.jsonl` is absent,
    unreadable or yields no nameable row.

    Never raises — `_read_jsonl` already degrades a missing/unreadable file
    and a corrupt line to "the rows that did parse", and this adds the two
    shape guards it cannot make on its own: a non-dict row is skipped, and so
    is a dict with no `criterion_id`. A criterion that cannot be NAMED answers
    the only question this array exists for, so emitting it with a null id
    would add a row to a fleet count while telling nobody which criterion it
    was."""
    return [_audit_criterion_wire(row) for row in _read_jsonl(_audit_assessment_path(cwd))
            if isinstance(row, dict) and row.get("criterion_id")]


def _audit_missing_advisory(path):
    """The one-line 'run /harness-audit first' advisory for a missing audit
    source. Only the explicit `--emit audit` request raises it (via the
    `advise` flag below); the bulk `all` path and `commit()` stay silent,
    because a null audit run is the normal case there — a `/fairmind-loop`
    close has no run-meta, and `/fairmind-sync-insights` runs `--emit all` on
    every repo whether or not `/harness-audit` was ever run."""
    print(f"insights_flush_payload: no {path} — audit category degraded to null "
          "(run /harness-audit first)", file=sys.stderr)


def build_audit_payload(cwd, base=None, advise=False):
    """The `Insights_record_harness_audit` wire payload for the audit run
    currently on disk, or None when either source file is missing/unreadable.
    A pure disk→wire assembler like the loop/decisions builders: silent by
    default, printing the missing-source advisory to stderr only when `advise`
    is set (the explicit `--emit audit` path). Reads only disk, never the
    clock, so a re-flush of an unchanged audit run is byte-identical across
    calls.

    `session_ref`/`project` (T1·X1) are the correlation keys — the audit run
    is the system's only outcome record, and without them it joins to nothing.
    Both are read from the state the loop already keeps (`base`-relative
    `loop-state.json`, `active-context.json`), and both are OMITTED when
    unavailable: `/harness-audit` run standalone genuinely has no session, and
    the wire must say so by silence rather than by sentinel. `session_ref` is
    spelled to match `build_decisions_batches`'s key of the same name (and the
    `d.session_ref`/`a.session_ref` graph properties behind it), not the loop
    payload's older `owner_session`."""
    run_meta_path = _audit_run_meta_path(cwd)
    run_meta_raw = _read_json(run_meta_path)
    if run_meta_raw is None:
        if advise:
            _audit_missing_advisory(run_meta_path)
        return None
    run_meta = _as_dict(run_meta_raw)

    summary_path = _audit_summary_path(cwd)
    summary_raw = _read_json(summary_path)
    if summary_raw is None:
        if advise:
            _audit_missing_advisory(summary_path)
        return None
    summary = _as_dict(summary_raw)

    totals = _as_dict(summary.get("totals"))
    pillars = summary.get("pillars")
    dimensions = summary.get("dimensions")
    # ONE read of active-context for this payload — used by `repository` below,
    # by `project` at the end, and by the session lookup in between, which is
    # why `base` is resolved from it HERE rather than left for `_owner_session`
    # to re-resolve. Two reads would be two chances for a concurrent repoint to
    # land between them and key one payload's repository against another's
    # project.
    root = _git_toplevel(cwd)
    base = _resolve_base(root, base)
    ctx = _active_context(root)
    # The origin is resolved ONLY for a checkout that HAS a binding to check:
    # every checkout that never ran `/fairmind-connect` — which is all of them
    # until it is — would otherwise pay a subprocess to be told it is unbound.
    # `_git_remote(cwd)` LIVE, deliberately not `run_meta["git_remote"]`: that
    # one was recorded when the audit ran and may itself be stale, so comparing
    # the binding against it would answer a different question.
    bound_repository = (_bound_repository(ctx, _git_remote(cwd))
                        if _binding.is_bound(ctx) else None)

    payload = {
        # snake_case, matching this module's own loop/decisions builders and the
        # server's `contract_version` parameter. `source` is NOT sent: the server
        # owns provenance, and the MCP tool
        # exposes no such parameter, so sending it was rejected outright.
        "contract_version": AUDIT_CONTRACT_VERSION,
        # DECLARED OPEN — see `_AUDIT_RUN_META_GUARDS`. Also a non-Optional
        # consumer kwarg, so nulling it would manufacture a payload the
        # consumer must reject.
        #
        # The bound catalog id wins over `repo_name` for the reason
        # `_bound_repository` gives, and the precedence also RETIRES this
        # field's worst input: `repo_name` is `os.path.basename` of a
        # filesystem path, whose producer range is every string without `/` or
        # NUL. A bound checkout sends a catalog id the server minted instead.
        "repository": bound_repository or run_meta.get("repo_name"),
        # ⚠️ THE KEY IS RETAINED AND THE VALUE NULLED — this is NOT drop-key.
        # Both are non-Optional consumer kwargs whose absent-shape is already
        # `null`, and nulling either makes `_audit_key_from_payload` return
        # None, so the run is unkeyable and never sent at all. That refusal
        # routes through machinery that already exists; no new one is added.
        "commit_sha": _guarded_scalar(_AUDIT_RUN_META_GUARDS, "commit_sha",
                                      run_meta.get("commit_sha")),
        "executed_at": _guarded_scalar(_AUDIT_RUN_META_GUARDS, "executed_at",
                                       run_meta.get("executed_at")),
        "criteria_version": summary.get("criteria_version"),  # DECLARED OPEN
        # `totals` keeps its FIXED 2-KEY LITERAL: a refused value is nulled and
        # the key is never dropped, because the consumer relies on its arity and
        # because `totals.get(...)` already yielded `null` when absent.
        "totals": {"criteria": _guarded_scalar(_AUDIT_TOTALS_GUARDS, "criteria",
                                               totals.get("criteria")),
                   "passed": _guarded_scalar(_AUDIT_TOTALS_GUARDS, "passed",
                                             totals.get("passed"))},
        "pillars": [_audit_pillar_wire(p) for p in (pillars if isinstance(pillars, list) else [])
                    if isinstance(p, dict)],
    }
    # TRUE drop-key: omitting is already this key's established absent-shape,
    # so a refused value takes the same shape a missing one always has.
    git_remote = _guarded_scalar(_AUDIT_RUN_META_GUARDS, "git_remote",
                                 run_meta.get("git_remote"))
    if git_remote is not None:
        payload["git_remote"] = git_remote
    if isinstance(dimensions, list):
        payload["dimensions"] = [_audit_dimension_wire(dm) for dm in dimensions if isinstance(dm, dict)]

    # The per-criterion verdicts (T2-C2). Read AFTER the two `return None`
    # guards above, so a missing run-meta/summary still short-circuits exactly
    # as before — this is an addition to the outcome record, never a new way
    # for it to fail. OMITTED, never `[]`, when nothing readable is on disk:
    # every audit run evaluated criteria (81 of them), so an empty array would
    # assert "no criterion was evaluated" — never true — where absence
    # correctly says "this run's per-criterion verdicts could not be read".
    # Same omit-don't-sentinel rule as git_remote/dimensions/session_ref/
    # project on either side of it.
    criteria = _audit_criteria_wire(cwd)
    if criteria:
        payload["criteria"] = criteria

    # Correlation keys — present only when the disk really holds them.
    session_ref = _owner_session(root, base)
    if session_ref:
        payload["session_ref"] = session_ref
    project = _project(ctx)
    if project:
        payload["project"] = project
    return payload


def _audit_key_from_payload(payload):
    """The cursor key for an audit payload: `<commit_sha>@<executed_at>` — same
    spelling convention as the loop cursor's `loop_id`, so re-running the audit
    at a different commit or a later timestamp is pending again."""
    commit_sha = payload.get("commit_sha")
    executed_at = payload.get("executed_at")
    if commit_sha is None or executed_at is None:
        return None
    return f"{commit_sha}@{executed_at}"


def _audit_unkeyable_advisory():
    """The 'identity keys missing' advisory for a run-meta.json that EXISTS
    and parses fine but lacks `commit_sha`/`executed_at`, so
    `_audit_key_from_payload` cannot form a cursor key. Without this, that
    case is silently indistinguishable from "already flushed" (null, empty
    stderr) though nothing was ever flushed and there is no cursor entry to
    delete to recover it. Same explicit-only gating as
    `_audit_missing_advisory`: only the explicit `--emit audit` request (the
    `advise` flag, forwarded from `pending_audit`) raises it; `--emit all` /
    `commit()` stay silent, since a null audit run is the normal case there."""
    print("insights_flush_payload: run-meta.json lacks commit_sha/executed_at "
          "— audit run unkeyable; re-run /harness-audit", file=sys.stderr)


def _audit_refused_identity_advisory():
    """The 'identity key REFUSED' advisory — and it fires on EVERY path, unlike
    its `advise`-gated sibling above.

    THE TWO CASES ARE NOT THE SAME CASE, which is why the gate differs. An
    ABSENT `commit_sha`/`executed_at` is the ordinary degraded shape and
    `--emit all` is right to stay silent about it (`/fairmind-sync-insights`
    runs `--emit all` on every repo, audited or not). A value that WAS PRESENT
    on disk and was REFUSED by a shape guard is never the ordinary case: the run
    is discarded whole, with exit 0, valid JSON and — before this — empty
    stderr, indistinguishable from "already flushed".

    THE REJECTED VALUE IS NEVER PRINTED. A log line is a content channel, and
    the value that tripped a shape guard is exactly the value most likely to be
    the thing the guard exists to keep off the wire."""
    print("insights_flush_payload: run-meta.json commit_sha/executed_at is "
          "present but not a recognizable git object name / ISO-8601 instant "
          "— audit run unkeyable and NOT sent; re-run /harness-audit",
          file=sys.stderr)


# ---------------------------------------------------------------------------
# Cursor — {"loops": {loop_id: closed_at}, "decisions": {decisionId: true},
#           "audits": {commit_sha@executed_at: true}}
# ---------------------------------------------------------------------------

def read_cursor(cwd):
    return _read_json(_cursor_path(cwd), {}) or {}


def _read_inflight(cwd):
    data = _read_json(_inflight_path(cwd), {})
    return data if isinstance(data, dict) else {}


def record_sent_decision_ids(cwd, category, ids):
    """Persist the exact `decisionId`s a `--emit <category>` call is about to
    hand its caller, so a LATER `--commit <category>` — always a SEPARATE
    process, after the MCP round trip the command body makes in between —
    marks delivered only what was actually sent, never a row the ledger grew
    by in the meantime (see `commit`'s docstring). `category` is
    `"decisions"` or `"brain"`; the two are independent keys because the two
    doors are independent (`pending_brain`).

    ⚠️ **`--emit` LOOKS read-only and is not: it writes this file.** The key is
    the category, not the emission, so ANY later `--emit <category>` in the
    same checkout overwrites the recorded set and the next `--commit
    <category>` spends the later one — marking delivered a row nobody sent.
    Reproduced: emit A `[1]`, append `2`, emit B `[1,2]`, A commits, `2` is
    gone, and the emitting pair never sees it again.

    The reading to avoid is that this needs two competing automations. It
    does not, and the likelier way in is a person: `--emit decisions` run
    once just to SEE what is pending — no MCP call, nothing sent, no intent
    to commit — is a mutation of this file, and it is enough. Troubleshooting
    a stuck flush by looking at it is exactly how the hole gets opened.

    This NARROWS the hole it was written for (the window shrinks from
    emit→commit to emit→emit) and does not close it; closing it needs a
    per-emission token threaded through `--commit`, which changes the
    protocol both command bodies speak."""
    inflight = _read_inflight(cwd)
    inflight[category] = list(ids)
    audit_run_meta._atomic_write_json(_inflight_path(cwd), inflight)


def pending_loop(cwd, base=None, *, granted_classes=None, repo_root=None):
    """The loop payload if this close has not yet been flushed (absent from
    the cursor, or flushed at an earlier `closed_at` — a revived loop's later
    close is pending again); else None.

    `granted_classes` (JC5) and `repo_root` (the artifact-path anchor) are both
    forwarded VERBATIM to `build_loop_payload`: this function is a cursor filter,
    not a second policy site and not a second place to decide where the repo is.
    Neither is defaulted here — a default invented at this layer is a second
    answer to a question the CLI already answered."""
    payload = build_loop_payload(cwd, base, granted_classes=granted_classes,
                                 repo_root=repo_root)
    cursor = read_cursor(cwd)
    committed_closed_at = (cursor.get("loops") or {}).get(payload["loop_id"])
    if committed_closed_at is None:
        return payload
    committed = _parse_iso(committed_closed_at)
    current = _parse_iso(payload["closed_at"])
    if committed is not None and current is not None:
        return payload if current > committed else None
    # Unparseable timestamps: fall back to a straight inequality rather than
    # silently treating this close as already flushed.
    return payload if committed_closed_at != payload["closed_at"] else None


def _pending_batches(batches, committed):
    """`batches` with every row already in `committed` removed and every batch
    that leaves empty dropped; None when nothing at all is pending."""
    for batch in batches:
        batch["decisions"] = [d for d in batch["decisions"]
                              if d["decisionId"] not in committed]
    return [batch for batch in batches if batch["decisions"]] or None


def pending_decisions(cwd, base=None):
    """The decisions batches filtered to rows not yet in the cursor, or None
    when every row is already committed (including an empty source log)."""
    return _pending_batches(build_decisions_batches(cwd, base),
                            read_cursor(cwd).get("decisions") or {})


#: The one `kind` from the decision-capture categories that belongs in the
#: company brain. The agents write one of six (`recurring-fix`, `business-rule`,
#: `architecture`, `standard-deviation`, `dependency`, `other`), and rows logged
#: earlier carry older words (`implementation`, `testing`, `process`), so this
#: is a MEMBERSHIP test against one name, never an exclusion list of the
#: others — a `kind` nobody anticipated must not fall through into the brain
#: by default.
BRAIN_DECISION_KIND = "architecture"


def brain_is_disabled(cwd):
    """Whether this repository has turned the brain write-back off.

    🔴 **READ AT THE PRODUCER, not only in the commands.** The switch shipped
    enforced by prose alone — two markdown blocks telling the orchestrating
    agent to check `/fairmind-config` and skip the category — so `--emit brain`
    sent the rows anyway for any caller that did not read them: a direct
    invocation, a compacted session, a future non-LLM caller. A consent switch
    honoured only by a model reading an instruction is not a control, and this
    is the first flush category that has one (`loop`, `decisions` and `audit`
    have none by design, and the README says so). Raised in the PR review,
    2026-09-12.

    Two layers, judge's precedence exactly: the central policy wins where it
    speaks, and where it is silent the repo file decides.

    ⚠️ **The fail direction is ambient's, NOT judge's, and the difference is
    what the route carries.** `judge_is_silenced` leaves an ambiguous shape
    running, because a judge call asks a question and an unreadable file should
    not cost a review nobody got. This route SENDS repository-derived content —
    decision titles, free-prose rationales, file paths, symbol names — so a
    `brain` key that is present but not a boolean is treated as **off**: a value
    nobody can read is not consent. An ABSENT key stays on, because that is a
    clean file expressing no opinion and is exactly what
    `/fairmind-config brain unset` writes.
    """
    toplevel = _git_toplevel(cwd)
    central = _plugin_policy.resolve_central(
        "brain",
        _plugin_policy.read_cache(_plugin_policy.cache_path(toplevel)),
        datetime.now(timezone.utc))
    if central is not None:
        # "on" or "off" — every other answer is None. Written as "is not None"
        # so widening the vocabulary can never let an unrecognised FORCE fall
        # through to the local file.
        return central == "off"
    cfg = _read_json(
        os.path.join(toplevel, _plugin_policy.INSIGHTS_CONFIG_BASENAME), {})
    if not isinstance(cfg, dict) or "brain" not in cfg:
        return False  # no opinion recorded here
    return cfg["brain"] is not True  # False, or any non-boolean shape


def _is_brain_decision(row):
    """Does this decision belong in the company brain?

    ⚠️ **ONE predicate, called on both sides, because the two sides read
    DIFFERENT rows.** `build_brain_batches` filters the WIRE rows and `commit`
    filters the RAW ledger rows — and `_decision_wire_row` normalises `kind`
    through `_nonempty_str`, which strips. So a captured `" architecture "`
    became `"architecture"` on the wire (sent) while a raw `==` comparison
    rejected it (never committed): the row was re-sent on every single close,
    for ever, and nothing anywhere reported it. Caught in the pre-PR
    cross-model review, 2026-09-12, and reproduced before this fix.

    Normalising here rather than at the two call sites is the point — a fix
    applied twice is a fix that drifts once.
    """
    return _nonempty_str(row.get("kind")) == BRAIN_DECISION_KIND


def build_brain_batches(cwd, base=None):
    """The `Brain_record_decision` payloads: the architecture decisions of
    `.fairmind/insights/decisions.jsonl`, and nothing else, split into the same
    batches as `build_decisions_batches` (that door stamps a batch's refs onto
    every row too) with any batch the filter empties dropped.

    It is `build_decisions_batches` filtered, not a second projection, and that is
    deliberate rather than convenient: `fm-insights.decision/2` is the contract
    BOTH doors take — the brain's `decisions` rows carry the same `decisionId`,
    `ts`, `agent`, `kind`, `title`, `rationale`, `files`, `functions`, under the
    same batch-level `repository`/`git_remote`/`session_ref`/`task_ref`. A
    separate projection here would be a second place for the wire shape to drift
    from the one the conformance tests pin.

    The filter is the whole difference. Every decision an agent takes goes to
    Agentic Insights; only the ones it typed `architecture` are proposed as
    company knowledge. A loop produces tens of decisions and the brain is not a
    transcript.
    """
    batches = build_decisions_batches(cwd, base)
    for batch in batches:
        batch["decisions"] = [row for row in batch["decisions"]
                              if _is_brain_decision(row)]
    return [batch for batch in batches if batch["decisions"]]


def pending_brain(cwd, base=None):
    """The brain batches filtered to rows not yet in the cursor, or None when
    there is nothing to propose.

    Keyed on the SAME `decisionId` as the `decisions` category and in its own
    cursor map, because the two doors are independent: a repository that has
    flushed its insights has not therefore told the brain anything, and a brain
    write that failed must stay pending even though the insights call succeeded.
    """
    if brain_is_disabled(cwd):
        return None  # the repository declined this route; nothing is pending
    return _pending_batches(build_brain_batches(cwd, base),
                            read_cursor(cwd).get("brain") or {})


def pending_audit(cwd, base=None, advise=False):
    """The audit payload currently on disk if it has not yet been flushed
    (absent from the cursor's `audits` map, keyed `<commit_sha>@<executed_at>`);
    else None. Also None when the source files are missing (degraded, see
    `build_audit_payload`) — a missing run-meta.json is never "pending" — or
    when a PRESENT run-meta.json is unkeyable (missing `commit_sha`/
    `executed_at`, so no cursor key can be formed). `advise` is forwarded to
    the builder so only the explicit `--emit audit` request surfaces the
    missing-source advisory, and gates the unkeyable-source advisory here the
    same way. `base` is forwarded so the correlation keys resolve against the
    SAME loop this flush is closing (see `build_audit_payload`)."""
    payload = build_audit_payload(cwd, base, advise=advise)
    if payload is None:
        return None
    key = _audit_key_from_payload(payload)
    if key is None:
        # PRESENT-AND-REFUSED vs ABSENT, decided by re-reading the ONE file that
        # can tell them apart. The extra read only ever happens on a path that
        # is already returning None, never on the normal one.
        disk = _as_dict(_read_json(_audit_run_meta_path(cwd), {}))
        if any(disk.get(field) is not None and payload.get(field) is None
               for field in ("commit_sha", "executed_at")):
            _audit_refused_identity_advisory()
        elif advise:
            _audit_unkeyable_advisory()
        return None
    committed = (read_cursor(cwd).get("audits") or {})
    if key in committed:
        return None
    return payload


def commit(cwd, base=None, loop=True, decisions=True, audit=True, brain=True):
    """Atomically merge the current loop/decisions/audit/brain state into the
    cursor. Never clobbers a category not being committed. For loop and
    decisions it derives only the keys it records — the loop identity, the
    decision ids — rather than rebuilding the whole payload (which would
    re-read the token/trace ledgers and spawn git just to discard everything
    but those keys). The audit key comes from the full `build_audit_payload`
    (silent here), which is only two small local JSON reads — no ledgers, no
    git — and reusing it keeps the emittability guard (the cursor advances
    only when the run is fully emittable, exactly what `--emit audit` would
    send) in one place.

    **`decisions` and `brain` are BOUNDED to what the corresponding `--emit`
    handed its caller**, when there was one. `--emit <category>` records that
    set (`record_sent_decision_ids`, after the hand-off) and this call spends
    it: the ledger walk below marks delivered only ids in the set, and pops it
    so it cannot bound a later commit that had no emit of its own. With no set
    recorded the walk is unbounded, exactly as it was before — that is the path
    every direct `commit()` caller takes. The point is the gap between the two
    processes: the command bodies run `--emit`, make the MCP call, and only
    then run `--commit`, and a row appended in between was never on the wire.

    ⚠️ **The set is keyed by category, not by emission, and `--emit` writes
    it**, so any later `--emit <category>` in the checkout — including one run
    only to look at what is pending — replaces what this call will spend. See
    `record_sent_decision_ids` for what that costs and what closing it takes.

    A caller that sends only some categories must say so — `brain` defaults to
    True like the three before it."""
    cursor = read_cursor(cwd)
    loops = dict(cursor.get("loops") or {})
    committed_decisions = dict(cursor.get("decisions") or {})
    committed_audits = dict(cursor.get("audits") or {})
    committed_brain = dict(cursor.get("brain") or {})

    if loop:
        base_r = _resolve_base(cwd, base)
        state = _as_dict(_read_json(os.path.join(cwd, base_r, "loop-state.json"), {}))
        loop_id, closed_at = _loop_identity(cwd, state)
        loops[loop_id] = closed_at
    # Nothing sent, nothing marked: the same switch that empties `pending_brain`
    # must stop the cursor advancing, or a disabled repository would have its
    # rows marked delivered by a `--commit brain` that sent nothing.
    brain_on = brain and not brain_is_disabled(cwd)
    # `inflight`/`spent` are decided here but NOT written yet — see the ordering
    # note above the two writes below for why.
    inflight = None
    spent = False
    if decisions or brain_on:
        inflight = _read_inflight(cwd)
        # `None` means "no --emit recorded a sent-set for this category" — walk
        # the live ledger, exactly as before this fix (a direct `commit()` call
        # with no preceding `--emit`, which is every pre-existing caller of this
        # function). A recorded set, even an empty one, means "the corresponding
        # --emit already decided what counts as sent" and BOUNDS the walk to it:
        # mark delivered only what was actually sent, never whatever the ledger
        # grew to by commit time. Stated in this function's docstring, with the
        # limit that comes with it.
        decisions_sent = set(inflight["decisions"]) if "decisions" in inflight else None
        brain_sent = set(inflight["brain"]) if "brain" in inflight else None
        # ONE walk of the ledger for both maps, and one `_decision_id` per row.
        # That the two maps key on the SAME id is the point: they are separate
        # because one door succeeding says nothing about the other, not because
        # the rows differ. Sharing the derivation here makes that structural
        # rather than a claim in `pending_brain`'s docstring.
        for row in _read_jsonl(_decisions_path(cwd)):
            if not isinstance(row, dict):
                continue  # a malformed decision row is skipped, not committed as an id
            decision_id = _decision_id(row)
            if decisions and (decisions_sent is None or decision_id in decisions_sent):
                committed_decisions[decision_id] = True
            # Filtered to the brain's own kind, so committing this category can
            # never mark an implementation decision as proposed to the brain —
            # it was never sent there.
            if (brain_on and _is_brain_decision(row)
                    and (brain_sent is None or decision_id in brain_sent)):
                committed_brain[decision_id] = True
        # The snapshot is spent: clear the categories just committed so a
        # stale sent-set never bounds a LATER commit that had no --emit of
        # its own before it (the next --emit overwrites it anyway, but a
        # commit with no intervening emit must fall back to the ledger, not
        # to a leftover set from an earlier, already-committed round).
        #
        # Gated on a pop that actually removed something, not on the file being
        # non-empty: `--commit decisions` run twice, or run while only `brain`
        # is snapshotted, would otherwise rewrite a byte-identical file. An
        # EMPTY recorded set still counts as spent — `[]` is a decision the
        # emit made, not a missing one — which is why this tests presence
        # rather than truthiness.
        if decisions:
            spent = inflight.pop("decisions", None) is not None
        if brain_on:
            spent = inflight.pop("brain", None) is not None or spent
    if audit:
        payload = build_audit_payload(cwd, base)
        if payload is not None:
            key = _audit_key_from_payload(payload)
            if key is not None:
                committed_audits[key] = True

    # 🔴 WRITE THE CURSOR *BEFORE* SPENDING THE SENT-SET — THIS ORDER IS WHAT
    # KEEPS AN INTERRUPT BETWEEN THE TWO WRITES FROM MARKING UNSENT ROWS
    # DELIVERED. Both writes
    # are individually atomic (`_atomic_write_json`) but nothing covers the two
    # of them together, and `build_audit_payload` — called above, between the
    # old inflight-write and the old cursor-write — forks git twice with no
    # `FileNotFoundError` guard (`audit_run_meta._run_git`). An interrupt in
    # that gap used to land BETWEEN "clear the sent-set" and "advance the
    # cursor": the claim of what was sent was gone and the cursor never
    # recorded it, so the NEXT commit found no sent-set to bound it and fell
    # back to the unbounded ledger walk — marking delivered every row on disk,
    # including ones this process never put on the wire. Writing the cursor
    # FIRST moves that entire crash window (all of `build_audit_payload`, plus
    # everything computed above it) to BEFORE either write happens: an
    # interrupt there leaves both files untouched, and the sent-set is still on
    # disk to re-spend on the next attempt. The one gap that remains — between
    # THIS write and the inflight write below — is the SAFE direction: a crash
    # there leaves the cursor already advanced and the sent-set still present,
    # so the next commit just re-marks the same (already-committed) ids, which
    # is idempotent, never lossy.
    #
    # Deliberately NOT the other candidate fix (folding the sent-set into this
    # same file so one write covers both): that would make `--emit` a writer of
    # the delivery cursor for the first time, opening a NEW race against a
    # concurrent `--commit` on a file that is durable, delivered state — a
    # different and worse failure (re-sending, on a file this repo has already
    # documented independent processes race against — see
    # test_commit_ledger_race.py) than the one being closed here. This reorder
    # touches nothing about how `--emit` writes; that race, already known and
    # already documented, is unchanged either way.
    audit_run_meta._atomic_write_json(
        _cursor_path(cwd),
        {"loops": loops, "decisions": committed_decisions, "audits": committed_audits,
         "brain": committed_brain})
    if spent:
        audit_run_meta._atomic_write_json(_inflight_path(cwd), inflight)


# ---------------------------------------------------------------------------
# Emit channel — stdout (unchanged default) vs. a file (`--out`)
# ---------------------------------------------------------------------------

# Sentinel for a bare `--out` with no path: the script picks one itself. The
# commands pass it bare precisely so no caller has to invent a unique name.
_OUT_AUTO = object()

_OUT_PREFIX = "fairmind-insights-flush-"

# The categories, in a FIXED order, so the `--out` summary's key set is
# closed and its size cannot grow with the payload.
_CATEGORY_ORDER = ("loop", "decisions", "audit", "brain")


def _payload_text(out):
    """The exact bytes `--emit` writes to stdout: one compact JSON line,
    newline-terminated. Unchanged, so any existing caller of the stdout mode
    keeps byte-for-byte what it had."""
    return json.dumps(out) + "\n"


def _payload_file_text(out):
    """What `--out` writes: the SAME payload, INDENTED.

    Not a style choice — it is what keeps the file readable when it is large.
    The consuming agent reads this file with a tool that pages by LINE, and a
    single-line file cannot be paged at all: past roughly 50,000 bytes it
    returns a prefix and says so ("this file has very long lines and cannot be
    paginated by line"). Measured on a real flush: 19,688 B compact on ONE
    line, versus 24,289 B over 454 lines indented. The 23% of extra bytes buys
    the difference between a hard ceiling and a two-call read.

    Both forms parse to the identical object, and a test asserts exactly that
    rather than asserting the strings match — which is why this function exists
    separately from `_payload_text` instead of the file reusing stdout's bytes.
    """
    return json.dumps(out, indent=2, sort_keys=False) + "\n"


def _write_payload_file(text, path=None):
    """Write `text` to `path`, or to a freshly minted file when `path` is
    None; returns the path actually written.

    The auto path is `tempfile.mkstemp` in the OS temp dir, and all three
    properties are load-bearing:

    * OUTSIDE THE CONSUMER REPO. This payload carries absolute developer home
      paths in `artifacts`/`artifact_mutations`, and a file dropped under
      `.fairmind/` in a consumer repo could be committed by a `git add -A`.
      PCF-28 now adds the ignore entry on first write (`_fm_ignore.
      ensure_ignored`), which NARROWS that exposure without removing the reason
      to stay out: the entry is added on the first write, so a repo whose
      `.fairmind/` predates the fix has none until something writes again; it is
      never added outside a git work tree; and git ignores `.gitignore` entirely
      for paths already TRACKED. Writing outside the repo needs none of those to
      hold.
    * UNIQUE PER INVOCATION, never a fixed name. Two sessions flushing the
      same repo concurrently would otherwise overwrite each other's file
      between the write and the consumer's read, handing one agent the other's
      payload — the same class of defect as the outbox watermark race.
      `mkstemp` makes the name atomically, so there is no check-then-create
      window either.
    * MODE 0600, which `mkstemp` gives for free. The payload used to cross as
      plain stdout with no protection at all; a file readable only by the
      invoking user is strictly better for the home-path exposure, though it
      does not close it (filtering the loop path is its own change).

    An explicit `path` is honored verbatim — that is the escape hatch for a
    caller that needs a known location (the tests use it) — and then the
    caller owns uniqueness and permissions."""
    if path is None:
        fd, path = tempfile.mkstemp(prefix=_OUT_PREFIX, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _emit_summary(out, path, total_bytes):
    """The BOUNDED stdout line `--out` prints in place of the payload.

    Bounded is the whole point, and it is structural rather than hoped-for:
    the key set is closed (`out`, `bytes`, and one entry per category in
    `_CATEGORY_ORDER`), every value but `out` is an integer or null, and `out`
    is a filesystem path. Nothing here grows with the size of the payload, so
    this line stays a few hundred bytes for any input — well under the
    30,000-byte agent-output cap that made `--out` necessary.

    A category's value is its own byte size, or `null` when there is nothing
    pending for it. That is exactly the signal the calling command needs (call
    the MCP tool for the non-null ones) without carrying the payload."""
    categories = {}
    for key in _CATEGORY_ORDER:
        value = out.get(key)
        categories[key] = (None if value is None
                           else len(json.dumps(value).encode("utf-8")))
    return json.dumps({"out": path, "bytes": total_bytes, "categories": categories})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _categories(spec):
    return set(_CATEGORY_ORDER) if spec == "all" else {spec}


def _consent_config_root(cwd):
    """The directory whose `.fairmind-insights.json` governs this flush: the git
    toplevel, else `cwd`.

    The config is read as a FILE, never through git — the same rule the ambient
    lane's own reads follow. Outside a git work tree `cwd` is the only root the
    flush was ever pointed at, and if no config lives there the resolver returns
    the default grant, which is the behaviour-neutral answer for the entire real
    population. Falling back to "no live resolution" instead would ALSO apply no
    narrowing, and would do it without even looking.

    A one-line delegation on purpose: WHERE the root is, is `_git_toplevel`'s
    fact and is shared with the decisions payload; that the FLUSH's consent scope
    is that root is this lane's policy, and it is the fact this name carries."""
    return _git_toplevel(cwd)


def _live_granted_classes(cwd):
    """The LIVE consent resolution for this flush — the half `build_loop_payload`
    structurally cannot do (it makes no git/config/env call and must stay
    byte-identical across repeated runs).

    ⚠️ THE RESOLVER IS NOT DEFINED HERE ON PURPOSE. `_config_consent_classes`
    lives in `_insights_session.py`, which is the one module that owns reads of
    `.fairmind-insights.json` (`_config_disables`, `_config_enables_event_
    skeleton`, `event_skeleton_consent` are all there). One writer per fact: a
    second copy of the grant ladder in this file is a second place the two lanes
    can disagree about what a config means, which is the exact defect the
    ambient lane already paid for once.

    Imported LAZILY, inside this CLI-only helper, so `_insights_session` never
    enters `build_loop_payload`'s import graph — nor the conformance staging's.

    A resolver that cannot be reached returns None ("no live resolution
    available", so nothing is narrowed) and SAYS SO ON STDERR. That is a broken
    install, not a policy decision, and the difference between the two has to be
    visible: silently defaulting is exactly how a live revoke stops being
    applied without anyone noticing."""
    try:
        import _insights_session  # noqa: PLC0415 — lazy by design, see the docstring
        resolve = _insights_session._config_consent_classes
    except (ImportError, AttributeError) as exc:
        print(f"insights_flush_payload: consent resolver unavailable ({exc}) — no live "
              "narrowing applied; the frozen collection-time stamp is used as-is",
              file=sys.stderr)
        return None
    classes, _basis = resolve(os.path.join(_consent_config_root(cwd),
                                           ".fairmind-insights.json"))
    return classes


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Assemble (and track flush state for) the Agentic Insights "
                    "terminal-flush payloads for a closed /fairmind-loop run "
                    "or a /harness-audit run."
    )
    parser.add_argument("--cwd", default=None, help="repo root (default: process cwd)")
    parser.add_argument("--base", default=None,
                         help="loop base_path (default: resolved from active-context.json)")
    parser.add_argument("--emit", choices=list(_CATEGORY_ORDER) + ["all"],
                         help="print {\"loop\": ..., \"decisions\": ..., \"audit\": ..., \"brain\": ...} "
                              "restricted to these categories")
    # ⚠️ `all` STAYS, and the hazard it carries is real and NOT new. A bulk
    # commit marks every category delivered, including ones this caller never
    # sent — `brain` most visibly, since a repository can switch it off, but
    # equally `loop` and `decisions` after a `/harness-audit` run that only ever
    # sent `audit`. Dropping `all` from the write side was tried on 2026-09-12
    # and reverted: `test_pl5_sync_insights.py:440` pins it as a shipped
    # acceptance criterion, so removing it is a CLI contract change that wants
    # its own slice and its own decision — not a side effect of adding a fourth
    # category. Until then the guard is the per-category rule in the commands.
    parser.add_argument("--commit", choices=list(_CATEGORY_ORDER) + ["all"],
                         help="mark these categories' current payload as flushed")
    parser.add_argument("--out", nargs="?", const=_OUT_AUTO, default=None, metavar="PATH",
                         help="write the --emit payload to a FILE and print only a short, "
                              "bounded summary ({out, bytes, categories}) on stdout. Bare "
                              "`--out` picks a fresh 0600 file outside the repo; an explicit "
                              "PATH is used verbatim. Use this whenever an agent reads the "
                              "output: a Bash tool result is capped at 30,000 bytes and a "
                              "real repo's `--emit all` already exceeds it. Without --out, "
                              "the payload still goes to stdout exactly as before.")
    args = parser.parse_args(argv)

    cwd = args.cwd or os.getcwd()

    if args.out is not None and not args.emit:
        # Never silently create an empty file, and never let a caller believe
        # a payload was written when nothing was emitted.
        print("insights_flush_payload: --out has no effect without --emit "
              "(nothing was written)", file=sys.stderr)

    if args.emit:
        cats = _categories(args.emit)
        # ALWAYS an explicit value on the CLI path, never the kwarg's default:
        # `granted_classes=None` means "nothing was resolved", and a CLI that
        # defaulted into it would stop applying a live revoke with no symptom.
        granted_classes = _live_granted_classes(cwd) if "loop" in cats else None
        # ALWAYS explicit here too, and for a stricter reason than the consent
        # value above: `repo_root=None` means "no root resolved", under which
        # every absolute artifact path becomes `OUTSIDE_REPO_TARGET` — so a CLI
        # that defaulted it would not leak, it would silently empty the field.
        # `_git_toplevel` is the file's ONE writer of "where is the repo root"
        # (shared with the decisions payload and the consent config root) and
        # returns the WORKTREE toplevel, which is the anchor `_wire_target`
        # documents and the same one the gate's attribution uses.
        repo_root = _git_toplevel(cwd) if "loop" in cats else None
        out = {
            "loop": (pending_loop(cwd, args.base, granted_classes=granted_classes,
                                  repo_root=repo_root)
                     if "loop" in cats else None),
            "decisions": pending_decisions(cwd, args.base) if "decisions" in cats else None,
            "audit": (pending_audit(cwd, args.base, advise=(args.emit == "audit"))
                      if "audit" in cats else None),
            "brain": pending_brain(cwd, args.base) if "brain" in cats else None,
        }
        if args.out is None:
            # Unchanged default — byte-identical to the previous
            # `print(json.dumps(out))`, so any existing caller is untouched.
            sys.stdout.write(_payload_text(out))
        else:
            # INDENTED, not the stdout bytes: the file exists to be read by an
            # agent whose reader pages by line, and a one-line file cannot be
            # paged. See `_payload_file_text`.
            text = _payload_file_text(out)
            path = _write_payload_file(
                text, None if args.out is _OUT_AUTO else args.out)
            print(_emit_summary(out, path, len(text.encode("utf-8"))))
        # Snapshot exactly what THIS emit HANDED the caller, for `commit` to
        # bound itself to later — see `record_sent_decision_ids`. Both
        # categories, whether or not this call found anything pending: an empty
        # snapshot still means "commit nothing new", which is correct when
        # nothing was on the wire this time.
        #
        # ⚠️ AFTER the hand-off, not before, and the order is the whole point.
        # `_write_payload_file` does real I/O and is unguarded, and a bare
        # `sys.stdout.write` can raise EPIPE — either way the process dies
        # non-zero having delivered NOTHING. Snapshotting first left a file on
        # disk claiming those ids went out, and a later bare `--commit` with no
        # fresh emit in front of it would spend that claim and mark them
        # delivered: the loss this fix exists to prevent, relocated one step
        # earlier. Raised by the PR review; the inverse risk is benign, because
        # a hand-off that succeeds and a snapshot that then fails leaves NO
        # snapshot, and commit falls back to the unbounded walk it did before
        # this branch.
        #
        # One loop rather than two near-identical blocks: `out`'s keys ARE the
        # category names, so a copy-paste pair differing only in which literal
        # goes where admits a silent swap — `"brain"` recorded from
        # `out["decisions"]` is syntactically fine and records the wrong set,
        # and no test would catch it (the suite exercises `decisions` only).
        for category in ("decisions", "brain"):
            if category in cats:
                record_sent_decision_ids(
                    cwd, category,
                    [d["decisionId"]
                     for batch in (out[category] or [])
                     for d in batch["decisions"]])

    if args.commit:
        cats = _categories(args.commit)
        commit(cwd, args.base, loop="loop" in cats, decisions="decisions" in cats,
               audit="audit" in cats, brain="brain" in cats)

    if not args.emit and not args.commit:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
