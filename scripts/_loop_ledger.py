#!/usr/bin/env python3
"""Shared loop-ledger primitives for the two capture hooks (PL-A0).

`trace-op.sh` (PostToolUse) and `capture-subagent-tokens.sh` (SubagentStop) both
need the same two things, and used to copy-paste them — the exact drift trap
`_usage_dedup.py` was created to avoid (and it HAD drifted: capture wrapped the
rotation write in try/except, trace-op did not). Both now live here, imported by
both hooks the way capture already imports `deduped_usage_totals`:

  - `resolve_loop_context(cwd, session_id=None)` — the PCF-16 LIVE-loop gate,
    widened into the whole routing decision. Reads
    `.fairmind/active-context.json`; in loop mode it requires a non-terminal
    `loop-state.json` at `base_path` (mirrors check-journal.sh:84-95) and pulls
    `started_at` from `budget.spent.started_at`, and it then asks whether this
    session IS that loop — the payload's `session_id` against `owner_session`,
    the context's `task_ref` against `target.ref`. A loop context that is NOT
    live DEGRADES to an interactive one (JC8) rather than switching capture off;
    a live one that is another loop's or another session's is ROUTED. See
    `resolve_loop_context` for the routed cases and for why silence was the
    wrong failure mode; `INTERNALS.md` (Interactive mode vs loop mode)
    holds the documented table.
  - `loop_ledger_path(cwd, base, ref, name)` / `loop_ledger_paths(cwd, base,
    name)` — the WRITE and READ halves of a ledger a context keeps for itself.
    The writer writes exactly one file, keyed by ref; every reader unions the
    whole home, the legacy unkeyed name included. Keyed because `base_path`
    alone put two consecutive loops on ONE file and the second loop's rotation
    evicted the first loop's rows; unioned because those rows are still on disk
    under the old name and are never migrated. `ledger_in` is the ROUTED join
    and stays deliberately unkeyed.
  - `roll_window(path, started_at, cap=2000)` — window-safe, amortized rotation
    of an active JSONL ledger. NEVER drops a row whose `ts >= started_at` (those
    rows are read whole mid-loop by run_gate_checks settle/mutation-set,
    insights_flush_payload, loop_dashboard); only the OLDEST out-of-window rows
    roll. Pre-arm (started_at is None) it falls back to a pure newest-N cap so
    the ledger stays bounded before the loop arms.

stdlib only; a peer of `_usage_dedup.py`, importable with no third-party deps.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional

# POSIX-only advisory file locking (finding 1). Guarded so the plugin still
# imports on Windows, where `fcntl` is absent and `append_row` degrades to a
# best-effort append plus the rotation's re-read fold.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

# Reuse the canonical ISO parser rather than a weaker inline copy: it normalizes
# a trailing "Z" to +00:00 and forces tz-awareness, so a naive ts and an aware
# started_at never mix into a TypeError comparison. loop_tokens is a small
# stdlib-only sibling in scripts/; importing it is cheap even on the hot path.
from loop_tokens import _parse_iso

# PCF-28: the ONE writer of the consumer-repo ignore entry, in its own
# stdlib-only leaf module so the seven scripts that only need a makedirs
# wrapper do not pull this ledger (and loop_tokens, and _usage_dedup) in to
# get it. Re-exported for the capture hooks, which import from here already.
from _fm_ignore import ensure_ignored, makedirs_ignored  # noqa: F401

# The engine's terminal statuses: a loop in one of these is done iterating, so
# the capture hooks must stop attributing to it.
#
# ⚠️ THE SET IS MIRRORED IN BASH; SINCE JC8 THE CONSEQUENCE IS NOT.
# `check-journal.sh` detects the identical condition and STANDS DOWN, while this
# module DEGRADES AND KEEPS CAPTURING. Both are right for what they do, and the
# asymmetry is the point rather than an oversight: refusing to record a row costs
# data and nothing else, whereas blocking a sub-agent's completion on a journal is
# an intrusive act that a stale marker must not be able to trigger. Do not
# "reconcile" the two by copying either verb across — what is shared is the
# STATUS SET, never the answer. (`loop-check.sh` reaches the same stand-down but
# holds NO copy of the set: it reads the ENGINE's exit code, so it is not a
# parity surface — verified by grep, the statuses appear in it only in a comment.)
#
# THE PARITY IS PINNED BY tests/test_liveness_rule_parity.py, and until it
# existed nothing did: this set and `check-journal.sh`'s `case "$STATUS" in` glob
# are hand-written copies of the engine's named vocabulary
# (`run_gate_checks.TERMINAL_STATUSES`; the engine's own GATE predicate remains
# `status != "running"`). That test derives its matrix from the constant and
# asserts BOTH consequences per member: this module degrades, the hook exits 0.
# Keep this set in step with that glob; a `blocked_`-prefixed addition is free on
# both sides, anything else is not.
#
# ⚠️ TERMINALITY NOW HAS A THIRD CONSUMER, AND IT USES A THIRD PREDICATE.
# `run_gate_checks._desired_context_mode` decides the active-context `mode` by
# MEMBERSHIP in `TERMINAL_STATUSES`, where this module tests exact-set-or-prefix
# and the hook globs. The three disagree only on a status one of them has never
# heard of, and the failure directions were chosen rather than inherited:
#   * `blocked_new` added here/to the glob but NOT to the tuple -> the writer
#     answers "leave it alone", the marker stays `loop`, and this module
#     DEGRADES it. Data is diverted, never evicted: the safe direction.
#   * a terminal status added to the tuple that is neither `passed_pending_human`
#     nor `blocked_`-prefixed -> the writer closes the marker while this module
#     still reads the loop as LIVE. That is the unsafe direction, and it is what
#     turns test_liveness_rule_parity.py RED, because that test derives its
#     matrix from the tuple and asserts THIS module degrades on every member.
_TERMINAL_EXACT = {"passed_pending_human"}
_TERMINAL_PREFIX = "blocked_"

# The workspace directory itself, named once because everything that has to
# AGREE about it joins to this constant instead of respelling the literal: the
# two routed directories below; `loop_ledger_path`'s fallback for a context
# whose marker carries no usable `base_path` — which is also the `base_path`
# `commands/fairmind-loop.md`'s quickstart documents, so it is the ordinary
# shape rather than a corner; the ledger-home question that must ask about that
# same fallback (`_ledger_home_belongs_to_a_loop`); and trace-op.sh's
# unconditional `.fairmind/trace/`. Stated as a rule and deliberately without a
# tally, because a numeral is what cannot notice the next joiner: a new one
# imports the constant, it does not write the literal.
_WORKSPACE_DIR = ".fairmind"

# JC8: where a DEGRADED context's rows go — a DIRECTORY OF THEIR OWN, and the
# directory is the whole point rather than a tidiness preference.
#
# 🔴 THE FIRST CUT PUT THEM IN A SHARED FILE AND IT DESTROYED DATA. It reused the
# `"session"` ref (trace) and an empty `base` (tokens), reasoning that neither
# could collide with a live loop's ledger. Both can, and a cross-model review
# found it:
#   * TOKENS — `commands/fairmind-loop.md` documents bootstrapping with
#     `base_path: ".fairmind"`. An empty base resolves to that SAME directory, so
#     live and degraded rows shared one `subagent-tokens.jsonl`. REPRODUCED
#     2026-08-15: 2,400 in-window loop rows, ONE degraded append, **901 rows
#     evicted** — a degraded context has no `started_at`, so `roll_window` is a
#     pure newest-N cap and nothing is window-protected.
#   * TRACE — a loop whose `task_ref` is absent (or sanitizes to `session`) writes
#     to `session.jsonl` itself, so the same eviction applies once it closes.
#
# A dedicated directory removes the collision instead of narrowing it: a trace
# ledger is always `.fairmind/trace/<sanitize_ref(ref)>.jsonl` and `sanitize_ref`
# maps `/` to `-`, so no ref can name a path inside another directory. The one
# residual is a hand-written `base_path` of exactly this value; stated rather
# than claimed impossible.
#
# It is also readable ON PURPOSE. An operator who opens `.fairmind/` sees a
# `degraded/` directory and learns the context is stale — which is half of the
# visible-signal item JC8 left undecided, bought for free.
DEGRADED_DIR = os.path.join(_WORKSPACE_DIR, "degraded")
DEGRADED_REF = "session"

# JC16 round 2: where a CLEANLY-CLOSED context's rows go. Same mechanism as
# `DEGRADED_DIR` for the same reason — and it had to be a SECOND directory
# rather than a reuse of the first.
#
# 🔴 THE CLOSED BRANCH REBUILT THE EVICTION IT WAS WRITTEN TO PREVENT. Its
# normalized answer (`base -> ""`, `ref -> DEGRADED_REF`) is collision-free only
# where the loop's own ledgers are somewhere else, and on the shape
# `commands/fairmind-loop.md` DOCUMENTS they are not: `base_path: ".fairmind"`
# makes the token fallback the loop's own ledger, and a loop with no `task_ref`
# traces to `session.jsonl` itself. REPRODUCED 2026-08-15 on that documented
# shape, one post-close sub-agent completion: 2,001 in-window rows -> 1,500,
# **502 EVICTED** (`roll_window` has no window boundary here, so it is a pure
# newest-N cap and nothing is protected), while the SAME fixture under the JC8
# degrade evicted none. The fix is the directory split, which touches
# `roll_window` not at all — that function has already produced one such
# disaster and content-aware rotation would be a second way to get it wrong.
#
# 🔴 AND IT IS NOT `degraded/`. A clean close is not a fault, and JC18's operator
# signal counts the rows in that directory: routing every ordinary post-loop row
# there would turn the signal on permanently for every repo that has ever run a
# loop, which is precisely the negative control that card requires.
#
# 🔴 AND IT IS NOT ONLY THE `closed` MARKER THAT ROUTES HERE. Round 2 made the
# unknown-`mode` branch strict and left the permissive answer standing under the
# name `interactive` — which is the value `/fairmind-develop` writes. That branch
# never consults liveness and hands back the context's OWN `base` and `ref` with
# `started_at=None`, so on the DOCUMENTED `base_path: ".fairmind"` it writes the
# just-finished loop's own `subagent-tokens.jsonl` as a pure newest-N cap.
# REPRODUCED 2026-08-16 through the flow `commands/fairmind-develop.md` MANDATES
# — `loop_open.py --repoint --mode develop --task-ref <ref>`, which points
# `base_path` at `.fairmind` and `mode` at `interactive`: 2,001 in-window rows
# -> 1,500, **502 EVICTED**, oldest survivor s-00000502.
# The same held for a marker with no `mode` key at all (the shape README.md
# documents), on both ledgers, and that half is older than this branch.
#
# So the routing rule is stated as a RULE rather than as a list of cases, and
# deliberately without a tally of them: separate doors have kept opening onto
# one failure, each found after the previous list was written, and a numeral is
# exactly what cannot notice the next one. A CAPTURE HOOK MAY WRITE INTO A
# LOOP'S OWN LEDGERS ONLY WHEN THIS SESSION IS THAT LIVE LOOP. `resolve_loop_
# context` is where the code enumerates the doors; `INTERNALS.md`
# (Interactive mode vs loop mode) holds the documented table.
#
# ⚠️ THE RESIDUAL, STATED RATHER THAN CLAIMED IMPOSSIBLE — and it is the exact
# twin of the one DEGRADED_DIR already carries. The split is collision-free
# against every REF-named ledger (verified: `../no-loop/trace`, `no-loop/trace`
# and `.fairmind/no-loop/trace` all sanitize into `.fairmind/trace/`), and
# against a `base_path` that IS this directory it is now collision-free in the
# TOKEN lane too, for a reason that belongs to the ledger's KEY rather than to
# this split: a routed row is written unkeyed (`ledger_in`) while an own-ledger
# row is always `subagent-tokens-<ref>.jsonl`, so the two cannot name one file.
# Re-measured 2026-08-17 on the 2,001-row fixture that produced the earlier
# figure: `base_path: ".fairmind/no-loop"` and `".fairmind/degraded"` now evict
# **0** rows (2,001 -> 2,001) where each evicted 502 before, with the routed row
# landing in the unkeyed file beside the loop's own.
#
# 🔴 WHAT THAT SHAPE STILL COSTS, AND IT IS NOW A READ: `loop_ledger_paths`
# always unions the legacy unkeyed name, which in one of these directories IS
# the routed ledger — so a loop whose `base_path` is a routed directory reads
# diverted rows into its own figures. Measured 2026-08-17: 3 own rows (in=100
# each) plus 2 routed rows (in=7) reported `in: 314` against the loop's own 300.
# Still two hand-written values that no command writes; the loss changed from
# data to attribution.
NO_LOOP_DIR = os.path.join(_WORKSPACE_DIR, "no-loop")

# The THIRD value of `.fairmind/active-context.json`'s `mode`: "a loop ran here
# and finished; no Fairmind session is active". Written by `run_gate_checks.
# main()`'s one sync when the loop reaches a terminal status, and read here.
#
# It exists because NOTHING repointed the marker when a loop CLOSED —
# `loop_open.py --repoint` is the only writer of `mode` and it fires at OPEN. So
# from the moment a loop ended, the repo went on declaring `mode: loop` over a
# dead loop for ever (23 days, measured in the plugin's own repo), and every
# later session was a degraded session. That is the CAUSE of JC8, one level
# above where JC8 was fixed: it is why degradation was the steady state instead
# of the fallback it was designed to be.
#
# 🔴 DELIBERATELY NOT ADDED TO `loop_open.MODES`. That dict's keys are BOTH the
# `--mode` argparse choices and the `BANNERS` table's keys — one dict family —
# so a third key raises KeyError in `render_opening` (verified). Nothing is lost
# by keeping it out: `loop_open.repoint` FORCE-writes `mode` from its own
# per-command table on every open, so the next loop/develop run clears this
# value with no extra code.
#
# Defined HERE, in the module that READS it, and imported by the writer — one
# definition, so the two can never disagree about the word. (The bash hooks
# cannot import, so `check-journal.sh` hand-mirrors it, exactly as it already
# hand-mirrors the terminal-status set below.)
CONTEXT_MODE_CLOSED = "closed"


def sanitize_ref(ref):
    """A `task_ref` reduced to a filename component. THE trace lane's rule, named
    once instead of respelled: `[^A-Za-z0-9_.-] -> "-"`, and a ref that empties
    out falls back to `DEGRADED_REF`.

    🔴 A REF MUST NEVER BE ABLE TO NAME A PATH OUTSIDE ITS OWN DIRECTORY. It is
    free text off `.fairmind/active-context.json`, which a hand-edit or a bad
    merge can fill with anything, so `/` (and `..`'s separator half) is mapped to
    `-` before it reaches any join. Every artifact keyed by a ref goes through
    here: the trace ledger's filename, the token ledger's, and — same character
    class, an opaque id from a hook payload rather than a ref —
    `orchestrator_watermark_path`.

    ⚠️ `hooks/scripts/trace-op.sh` (its `safe = re.sub(...)` line)
    HAND-MIRRORS THIS IN BASH and cannot
    import it (a `python3 -c` block inside a hook), so that copy is a genuine
    cross-language mirror rather than a duplication this function can remove.
    It spells `str(ref)` where this spells `str(ref or "")`: the two differ only
    on None/False, which no resolver-supplied ref can be (`resolve_loop_context`
    falls back to `DEGRADED_REF` before returning), so the difference is
    unreachable from the hooks and is left visible rather than silently
    normalized on one side.

    ⚠️ AND IT IS NOT `insights_flush_payload.sanitize_ref`, which is the same
    expression with `str(ref)` and is the READER-side mirror of that same bash
    writer. Unifying the two would move the trace lane's answer for a None ref
    (`"None.jsonl"` there, `"session.jsonl"` here) — a change to a lane this
    function does not own.

    🔴 THE MAPPING IS LOSSY AND NOTHING HERE MAKES IT UNIQUE: `T/9` and `T-9`
    both become `T-9`, so two refs can name one ledger. That is the property the
    trace lane has always had; it is stated rather than defended, because the
    alternative (an escaping scheme) would rename every ledger on disk."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref or "")) or DEGRADED_REF


def ledger_in(cwd, directory, name):
    """The path of ledger `name` inside the repo-relative `directory`. The one
    join both capture hooks make for a routed context, so the two can never
    diverge about where a diverted row lives — which is exactly how the first
    cut of JC8 diverged from its own design. `directory` comes from
    `LoopContext.ledger_dir`, so the ROUTING decision stays in this module and
    each hook keeps a single branch.

    ⚠️ A ROUTED LEDGER'S NAME IS NOT KEYED BY REF and must not become so. The
    routed row's ref has already been normalized to `DEGRADED_REF` by `_routed`,
    it names no loop, and the DIRECTORY is what makes the destination
    collision-free; `_insights_session._diverted_rows` reads those directories
    by name, as does the maintainers' out-of-tree corpus reader. ⚠️ Cite the
    behaviour, not that reader's filename: it lives outside `plugins/`, so a
    name for it here is a citation the installed plugin cannot resolve. The
    same citation was already removed once from `run_gate_checks.py` and
    regrew here.
    Keying is the OWN-ledger question, and `loop_ledger_path` below is where it
    is answered."""
    return os.path.join(cwd, directory, name)


def loop_ledger_path(cwd, base, ref, name):
    """The path a context that keeps its OWN ledgers WRITES ledger `name` to —
    the NON-routed answer, and the sibling of `ledger_in` above.

    ONE CONSTRUCTOR, because this join used to be written independently at each
    end of the file it names — the two capture hooks that APPEND to it
    (SubagentStop and Stop), the windowed reader `loop_tokens` serves the
    dashboard and the close-time ledger row from, and the flush's per-agent
    rollup — in three different spellings, which did not agree. Most carried the
    empty-`base` fallback to `_WORKSPACE_DIR` inline; the rollup carried none at
    all and had its default supplied by a resolver a hundred lines away, so
    reading that site told you the wrong thing about where the file lives.

    That fallback is load-bearing rather than defensive: it resolves to
    `.fairmind`, the `base_path` `commands/fairmind-loop.md`'s quickstart
    bootstraps with (`:62`), which is why a routed row must not be allowed to
    reach it — there it would be appending to a live loop's own ledger.

    🔴 THE LEDGER IS KEYED BY REF, NOT BY `base` ALONE, AND THAT IS THE WHOLE
    POINT OF THIS FUNCTION. `<base>/subagent-tokens.jsonl` was indexed on the
    directory, so two consecutive loops bootstrapped on that documented flat
    `base_path` wrote ONE physical file — and rotation there is not a tidiness
    operation but a data loss: loop 1's rows are older than loop 2's window
    boundary, so `roll_window` reads them as legitimately out-of-window and
    rolls them (measured 2,001 -> 1,500, 502 of loop 1's rows evicted, on ONE
    sub-agent completion under an ARMED loop 2 — the same digit the pre-arm
    reproduction produced, through the windowed arm rather than the newest-N
    cap). No identity test can see that: the context names loop 2, the
    loop-state names loop 2, and this session genuinely IS that live loop. It is
    a ledger-KEY question, and the key is the ref — the way
    `.fairmind/trace/<sanitize_ref(ref)>.jsonl` has always been.

    The ref goes into the NAME rather than into a directory of its own, and the
    reason is legacy bytes rather than taste: every repo that has run a loop
    already holds a FILE called `subagent-tokens.jsonl` in that home, so
    `subagent-tokens/` cannot be created beside it. A caller with no ref in
    scope passes None and gets `DEGRADED_REF`'s fixed name, exactly as the trace
    lane's ref-less context gets `session.jsonl`.

    🔑 WRITERS USE THIS; READERS MUST USE `loop_ledger_paths`. This function
    names ONE file — the one this context appends to. The legacy unkeyed name
    still holds real rows on every existing repo, and a ref-less context writes
    a third name, so a reader that resolved only this path would silently drop
    both. That rule is stated ONCE, here and in `loop_ledger_paths`, and cited
    from the readers.

    `ledger_in` remains the ROUTED join: the same `os.path.join`, over a
    directory the resolver chose, rather than one derived from `base`, and
    deliberately NOT keyed by ref."""
    stem, ext = os.path.splitext(name)
    return ledger_in(cwd, base or _WORKSPACE_DIR,
                     "%s-%s%s" % (stem, sanitize_ref(ref), ext))


def loop_ledger_paths(cwd, base, name):
    """Every file a READER of ledger `name` must union for the context at `base`,
    the legacy unkeyed name FIRST and the ref-keyed ones after it, sorted.

    🔑 THE RULE, STATED HERE AND CITED FROM THE READERS RATHER THAN RESTATED:
    THE WRITER WRITES EXACTLY ONE FILE, CHOSEN BY REF; EVERY READER READS THE
    WHOLE SET, and attributes rows with the `[start, end]` window it already
    applies. Two reasons it is a set and not a lookup:

      * LEGACY BYTES. `<base>/subagent-tokens.jsonl` exists, with real captured
        rows in it, on every repo that ran a loop before the key changed. A
        reader that could not see it would destroy exactly the data this capture
        lane exists to collect. It is READ and never written, never migrated and
        never deleted — moving a customer's ledger is not a decision this module
        gets to take, and reading it costs nothing.
      * THE WRITER'S REF IS NOT ALWAYS THE READER'S. A context whose `task_ref`
        is absent writes `DEGRADED_REF`'s name while the loop-state naming it
        carries a real `target.ref` (the identity predicate fails OPEN on an
        undecidable pair, by design), so a lookup keyed on the loop's ref would
        miss that loop's own rows.

    Reading the set therefore leaves ATTRIBUTION exactly where it already was —
    on the window — and changes only WHERE the bytes sit. It is deliberately not
    sold as read-side isolation, which the window has always provided and still
    does.

    🔴 WHAT KEYING BUYS, AND THE HALF IT DOES NOT. It isolates two DIFFERENT
    refs: loop 2's rotation can no longer reach loop 1's rows, which is the
    failure the card measured. It does NOT isolate the same ref run TWICE — a
    task re-bootstrapped at the same `base_path` writes the previous run's own
    file, and the eviction recurs (reproduced on this branch: 2,001 -> 1,500,
    502 of run 1's rows gone, oldest survivor `s-00502`; the control at a
    different ref leaves run 1 at 2,001). A REF-LESS context is the same story
    with one fixed name. Closing that needs the key to be the loop's identity,
    `ref@started_at` (`scripts/loop_ledger.py:_loop_id`), and the obstacle is
    stated rather than left to be rediscovered: `started_at` is absent before the
    arm, so that key would MOVE at arm time and split one run across two files —
    readable here, since readers already union a set, but it is a design choice
    this round did not take.

    The legacy path is returned whether or not it exists, so a caller can name
    the canonical file; every reader here already tolerates a missing path.
    `.loop-ledger.*.tmp` (a rotation mid-flight) and `.orch-watermark-*.json`
    are excluded by the prefix/suffix pair rather than by a deny list."""
    home = os.path.join(cwd, base or _WORKSPACE_DIR)
    stem, ext = os.path.splitext(name)
    paths = [os.path.join(home, name)]
    try:
        keyed = sorted(n for n in os.listdir(home)
                       if n.startswith(stem + "-") and n.endswith(ext))
    except OSError:
        keyed = []  # absent, unreadable, or not a directory -> the legacy name alone
    paths.extend(os.path.join(home, n) for n in keyed)
    return paths


def degraded_ledger(cwd, name):
    """The path a DEGRADED row of ledger `name` goes to — the named entry point
    for readers of that directory (`_insights_session`'s JC18 probe, and the
    tests that seed it), which must never have to re-spell the constant."""
    return ledger_in(cwd, DEGRADED_DIR, name)

# getsize() proxy: a lower bound on the serialized length (incl. newline) of ANY
# row these hooks write. Every row carries at least a full ISO-8601 `ts`
# (~25 chars, e.g. "2026-01-01T00:00:00+00:00") plus several fixed keys, so it is
# always well over this. Because each row is >= _MIN_ROW_BYTES, a file of
# `size` bytes holds at most `size / _MIN_ROW_BYTES` rows; when
# `size <= cap * _MIN_ROW_BYTES` the row count cannot exceed `cap`, so rotation
# is provably unnecessary and we skip the full read (a stat(), not a scan). Kept
# conservatively small so the gate can never skip a rotation that is actually due.
#
# Known limitation (finding 7, low-risk): the proxy assumes EVERY row is at least
# `_MIN_ROW_BYTES`. A pathological ledger of many sub-40-byte rows could hold more
# than `cap` rows while still under `cap * _MIN_ROW_BYTES` bytes and thus bypass
# rotation. Real rows are always well over 40 bytes (a full ISO `ts` alone is
# ~25), so this is documented, not defended with a locked sidecar row counter
# (that would be over-engineering for an input these hooks never actually write).
_MIN_ROW_BYTES = 40


def _is_terminal(status):
    return status in _TERMINAL_EXACT or status.startswith(_TERMINAL_PREFIX)


def orchestrator_watermark_path(out_dir, session_id):
    """Where the Stop hook keeps its per-session transcript byte watermark.

    ONE FILE PER SESSION, and that is the entire concurrency design. The
    watermark is a read-modify-write of shared state, and unlike `append_row`
    it is NOT taken under a lock. With a single shared file keyed by session
    INSIDE it, two sessions live in the same repo (routine here — the
    active-context they both read is per-repo, not per-session) would race:
    A reads, B reads, A writes, B writes back A's STALE entry. A's offset
    would rewind, and its next fire would re-read and re-count a slice whose
    ids are no longer suppressed — an OVER-count, the one failure mode this
    whole capture path exists to avoid.

    Splitting the file removes the race instead of narrowing it: two sessions
    never touch the same path, so there is nothing to clobber and no lock to
    take. It also bounds growth — a shared dict accumulated one entry per
    session forever, and nothing ever pruned it.

    The basename goes through `sanitize_ref` — a session id is an opaque string
    from the hook payload, so it must never be able to escape `out_dir`, which
    is the identical requirement a ref has, and an empty or fully-sanitized-away
    id falls back to a fixed name rather than an empty one. Shared for the RULE,
    not because a session id is a ref: if the escaping rule ever changes it must
    change for both, and this used to be a hand-written copy of that expression.
    """
    return os.path.join(out_dir, ".orch-watermark-%s.json" % sanitize_ref(session_id))


@dataclass
class LoopContext:
    """What each hook needs to decide whether — and how — to capture.

    - `live` is the single go/no-go, and it is False in exactly one case: there
      is no `.fairmind/active-context.json`, i.e. this is not a Fairmind
      workspace. The caller does `if not lc.live: sys.exit(0)`. Since JC8 no
      LOOP state can set it False — a dead loop degrades, it does not silence.
    - `mode`/`base`/`ref` feed the row stamp, the ledger path, and (trace) the
      per-taskRef filename.
    - `started_at` is the rotation window boundary; None pre-arm (or interactive).
    - `ledger_dir` IS THE ROUTING, and it is a field rather than a second boolean
      per destination. A context that must not write where its own `ref`/`base`
      would send it carries the repo-relative DIRECTORY its rows belong in —
      `DEGRADED_DIR` when the marker is stale or unreadable, `NO_LOOP_DIR`
      whenever the marker is honest and those ledgers simply belong to a loop
      this session is not; None means the ordinary ref-named/base-joined
      location. Which contexts reach which is enumerated ONCE, in
      `resolve_loop_context`'s own table, and never restated here — that list
      has grown every round and a second copy of it drifts by the next.
      Each hook therefore has ONE branch and this module owns every
      routing decision — the shape that stopped `closed` from silently
      inheriting the interactive answer's collisions.
    - `degraded` is the STAMP flag, not the routing one: True on the JC8 path (a
      marker claiming `mode: loop` over a loop that is not live) and on an
      unreadable `mode`, both of which an operator should be told about.
      `degraded_from` carries the `task_ref` that context claimed, so a hook can
      STAMP the diversion onto the row — without it a diverted row is
      byte-identical to a genuinely interactive one and the distinction dies
      in-process. A `mode: closed` context is deliberately NOT degraded: nothing
      was diverted, the loop is simply over and there is no stale marker to fix.
      It stamps `after_loop` instead — the ref of the loop the row FOLLOWS,
      which is the provenance that makes a post-close row attributable at all
      (the pre-PR ceremony runs entirely in that window). `mode: closed` is the
      ONLY route into `NO_LOOP_DIR` that stamps it, and the rule is a property
      of the ref rather than of the route: `after_loop` is claimable exactly
      when the marker's own `task_ref` IS the loop's. Every other route there —
      an interactive marker over a loop's ledger home, and a live loop naming
      another task or another session — carries a ref that names THIS session's
      own work, so stamping it would put a false attribution on the artifact;
      those rows carry neither key and the directory alone says what happened.
    - `row_stamp` is the one place those extra row keys are derived, so a hook
      spreads it and never re-decides. Everything else about a routed context has
      ALREADY been rewritten to the interactive answer by the time a caller sees
      it — `mode` reads `interactive`, `base` is empty, `ref` is `DEGRADED_REF`.
    """

    mode: str
    base: str
    ref: str
    live: bool
    started_at: Optional[str]
    degraded: bool = False
    degraded_from: Optional[str] = None
    ledger_dir: Optional[str] = None
    after_loop: Optional[str] = None

    @property
    def row_stamp(self):
        """The extra keys this context's rows carry, as a dict to spread into the
        row. DERIVED from the fields above rather than re-decided per hook: two
        hooks writing two stamps is how the first cut let the token row lose the
        marker the trace row kept. Empty for an ordinary context, so no existing
        row shape moves.

        ⚠️ BOTH REFS GO ONTO THE ROW RAW, AND THE CLEANING BELONGS TO THE READER.
        `degraded_from` and `after_loop` are the same value from the same place —
        `active-context.json`'s `task_ref`, which a hand-edit can fill with
        anything — and neither is bounded or control-char-filtered here. That is
        the shipped convention, not an oversight: the ledger keeps what the
        marker actually said, and the guard sits at the boundary where a ref
        reaches a HUMAN channel. `_insights_session._clean_loop_ref` is that
        guard for `degraded_from` (bounded at 80 chars, C0/C7F rejected, DROPPED
        rather than mangled), applied when the row is RENDERED into the JC18
        message, never when it is written.

        So a future reader of `NO_LOOP_DIR` must clean `after_loop` at its own
        boundary exactly as that probe does. It cannot be done here: the cleaner
        lives in `_insights_session`, which imports THIS module, so importing it
        back would be a cycle — and a second hand-written copy of a
        sanitization rule is the drift trap this file exists to avoid."""
        if self.degraded:
            return {"degraded": True, "degraded_from": str(self.degraded_from or "")}
        if self.after_loop:
            return {"after_loop": str(self.after_loop)}
        return {}


def resolve_loop_context(cwd, session_id=None):
    """Resolve the capture context for a hook firing in `cwd`.

    `session_id` is the hook payload's own session id, and it is OPTIONAL because
    one of the four call sites has none to give: `_insights_session.
    _stale_loop_marker` resolves a context purely to read `degraded`/
    `degraded_from` for JC18's operator signal and never sees a payload. An
    absent session id makes the OWNERSHIP test below undecidable, and an
    undecidable identity FAILS OPEN — so that caller can never be diverted by a
    test it cannot answer.

    ⚠️ The guarantee is about those TWO FIELDS, not about the whole object, and
    the narrower claim is the true one. The `target.ref` test needs no session id
    at all, so a context naming another task now resolves ROUTED where it once
    resolved live — a different `LoopContext` than before, carrying the same
    `degraded=False` / `degraded_from=None` that caller reads. JC18's signal is
    therefore unmoved; a future reader of any OTHER field on a default-argument
    resolution is not covered by this sentence and must check the routing table.

    Reads `.fairmind/active-context.json`. `mode` is a CLOSED vocabulary of
    three values and every other value is treated as a broken marker:

      loop        -> the liveness gate below, THEN the two identity tests: a
                     live loop means capture under the loop's own ref only when
                     that loop-state names this context's own `task_ref` and
                     this session is the one that claimed it; a live loop that
                     is another loop's or another session's is routed to
                     `NO_LOOP_DIR` (not degraded — see
                     `_belongs_to_another_loop_or_session`), and not live means
                     the JC8 degrade
      interactive -> capture under the context's own ref, in the ordinary place
                     — UNLESS a `loop-state.json` sits in the ledger home, in
                     which case those ledgers are a loop's and this session is
                     not it: routed to `NO_LOOP_DIR`, NOT degraded
      (absent)    -> read as `interactive`; the shape README.md documents
      closed      -> a loop ran here and finished: the normalized answer,
                     routed to `NO_LOOP_DIR`, NOT degraded
      anything else -> the normalized answer, routed to `DEGRADED_DIR` AND
                     degraded, because a marker we cannot read is a broken one

    ONE RULE UNDERLIES THE TABLE, and it is stated — without a tally of the rows
    it covers — because reading the table as a set of independent cases is what
    let the same eviction through door after door, each one found only after the
    previous list was written: A CAPTURE HOOK MAY WRITE INTO A LOOP'S OWN
    LEDGERS ONLY WHEN THIS SESSION IS THAT LIVE LOOP. Everything else routes to
    a directory no `ref` can name. When a new door is found, widen the table
    above; it is the enumeration, and nothing else in this module restates it.

    🔴 JC8 — A LOOP CONTEXT THAT IS NOT LIVE DEGRADES TO INTERACTIVE; IT NO
    LONGER GOES DARK. PCF-16 made a `mode: loop` marker with no loop-state at
    `base_path`, or one pointing at a TERMINAL loop, return live=False so the
    caller no-opped. The INTENT was right — a stale active-context must not keep
    appending unrelated ops to a closed loop's ledger — but the failure mode was
    SILENCE, and silence in the one lane the Judge corpus depends on costs
    exactly the asset it exists to collect. Measured 2026-08-14 in this repo:
    the active-context still named a loop closed on 2026-07-22, so every session
    for 23 days wrote zero trace rows, with no warning and no marker. The
    documented fallback "interactive sessions always trace" never engaged,
    because the mode was not `interactive` — it was a loop that was over.

    So a non-live loop context resolves to the interactive answer instead of to
    nothing, and PCF-16's actual guarantee is preserved by REDIRECTING rather
    than by refusing:

      * `ledger_dir` -> `DEGRADED_DIR`, which is what actually keeps the closed
        loop's artifact intact: `roll_window` with no window boundary is a pure
        newest-N cap, so appending post-close rows to the loop's own ledger
        would EVICT the in-loop rows the harvest reads. A DIRECTORY is the
        mechanism because no `ref` can name a path inside one (`sanitize_ref`
        maps `/` to `-`) — emptying `base` and renaming `ref` is a redirect only
        where the loop was not already writing there, and on the documented
        `base_path: ".fairmind"` it was.
      * `base` -> "" and `ref` -> `DEGRADED_REF`: the closed loop's own ledger
        names can never reach `roll_window` or a filename, whatever a later
        caller does with them.
      * `mode` -> "interactive", which is also what keeps
        `capture-orchestrator-tokens.sh` no-opping: it requires `mode == "loop"`
        AND an armed `started_at`, and neither is true here.
      * `started_at` -> None: there is no window to anchor rotation to.

    Nothing reads a trace ledger by globbing the directory — `run_gate_checks.
    trace_path` and `insights_flush_payload._trace_path` both resolve a REF — so
    a degraded row can never reach a loop payload, and a `--dry-run` on a
    `passed_pending_human` loop still reads that loop's own ledger.
    """
    ctx_path = os.path.join(cwd, ".fairmind", "active-context.json")
    # 🔑 `live` MUST HAVE A FALSE CASE OR IT IS NOT A GATE. After JC8 every
    # resolvable context captures, so the `if not lc.live` branch in three hooks
    # was dead while still reading as the liveness check. The real go/no-go is
    # "am I in a Fairmind workspace", which each hook was answering separately in
    # bash. It is answered here too, so one function owns capture/no-capture.
    #
    # ABSENT is the only False: a file that EXISTS and does not parse is a
    # workspace with a broken marker, and answering that with silence is the
    # exact JC8 defect wearing a different hat. It degrades like any other
    # unusable loop context.
    if not os.path.isfile(ctx_path):
        return LoopContext(mode="interactive", base="", ref=DEGRADED_REF,
                           live=False, started_at=None)
    ctx = {}
    try:
        with open(ctx_path, encoding="utf-8") as fh:
            ctx = json.load(fh)
    except Exception:
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}

    mode = ctx.get("mode") or "interactive"
    # 🔴 `base_path` MUST BE A STRING, CHECKED RATHER THAN ASSUMED. A truthy
    # non-string (an int, a list, an object from a hand-edit or a bad merge)
    # reaches `os.path.join`, raises TypeError, and both hooks swallow the
    # Python failure and exit 0 — which is TOTAL SILENCE, the exact defect JC8
    # exists to remove, reachable again through a different door. Found by a
    # cross-model review; degrading is the same answer this module gives every
    # other unusable loop context.
    base = ctx.get("base_path") or ""
    if not isinstance(base, str):
        base = None  # signals "unusable loop context" to the branch below
    # 🔑 THE RAW VALUE IS KEPT BESIDE THE RESOLVED ONE, and the identity test
    # below reads the RAW one. `ref` falls back to `DEGRADED_REF` ("session"),
    # which is a perfectly good non-empty string and would MISMATCH every real
    # `target.ref` — turning "this context names no task at all" into "this
    # context names a different loop", i.e. diverting a loop's own rows on the
    # one shape that carries no evidence at all.
    #
    # The case is exercised by name rather than by line, since the file is
    # edited often: `tests/test_loop_identity_routing.py`'s
    # `test_c_a_context_with_no_task_ref_of_its_own_fails_open_over_any_target`
    # drives an active-context with NO `task_ref` over a loop-state whose
    # `target.ref` IS present and different, and asserts the loop's own ledgers.
    # ⚠️ `tests/test_scope_boundary.py`'s `test_w16_...` is NOT that proof, and
    # was cited as one for a round: its divergent `target.ref` lives in the
    # engine's `--state` file written OUTSIDE the repo, while the loop-state this
    # resolver actually reads (at the context's `base_path`) carries no `target`
    # key at all — so both sides are absent there and the comparison never
    # engages whichever value it read.
    raw_ref = ctx.get("task_ref") or ctx.get("taskRef")
    ref = raw_ref or DEGRADED_REF

    # 🔴 `mode` IS A CLOSED VOCABULARY, AND THE UNKNOWN BRANCH IS THE STRICT
    # ONE. This read `if mode != "loop":` and handed EVERY unrecognized value an
    # interactive context carrying the loop's own `task_ref` and `base_path` —
    # which is how `closed` (a value added one card later, by this plugin, to
    # this same file) sent post-close rows into the closed loop's own ledger and
    # evicted 502 in-window rows on the documented shape. A permissive default
    # is a trap for every future value of the field, so each of the three
    # spellings is answered by name and anything else is treated as a marker
    # this module cannot read — the same answer it already gives an unparseable
    # context, one door over.
    if mode == "loop":
        # ONE READ, TWO QUESTIONS. Both the liveness oracle and the identity test
        # below want this same file, and reading it twice cost a measured
        # +39.6 µs per resolution at the p50 on-disk `loop-state.json` (13,080 B
        # across this repo's 33) and +86.2 µs at the largest — on a PostToolUse
        # hook, i.e. per TOOL CALL for the whole life of a live loop, against an
        # `append_row` that costs 31.4 µs on a fresh ledger. Passing the parsed
        # state down narrows each helper's ARGUMENT and widens neither's return,
        # so the liveness rule `check-journal.sh:84-95` hand-mirrors is
        # untouched. It also hands both questions the SAME snapshot; two reads
        # can see two different files.
        ls = _load_loop_state(cwd, base) if base else None
        loop_live, started_at = _live_loop_started_at(ls)
        if loop_live:
            # 🔴 "A LIVE LOOP IS HERE" IS NOT THE QUESTION THE RULE ASKS. The rule
            # is *this session IS that live loop*, which is two claims — WHICH
            # loop and WHOSE session — and this branch used to test neither.
            # `_live_loop_started_at` reads `status` and `budget.spent.
            # started_at` and nothing else, so a context naming a different loop,
            # or a second session in the same checkout, was handed the loop's own
            # ledgers. Both are routed to `NO_LOOP_DIR` and neither is
            # `degraded/`: a second loop and a parallel session are ordinary
            # states, not stale markers, and JC18's operator signal counts the
            # rows in `degraded/`. Same destination and same (absent) stamp as the
            # interactive-marker-over-a-loop route, for the same reason — this
            # context's own ref names no loop, so `after_loop` would be a false
            # attribution on the artifact.
            if _belongs_to_another_loop_or_session(ls, raw_ref, session_id):
                return _routed(NO_LOOP_DIR)
            return LoopContext(mode=mode, base=base, ref=ref, live=True,
                               started_at=started_at)
        # The JC8 degrade — see the docstring above for what each redirect
        # protects, and `DEGRADED_DIR` for why the directory is the mechanism.
        return _routed(DEGRADED_DIR, degraded=True, degraded_from=ref)

    if mode == CONTEXT_MODE_CLOSED:
        # A loop ran here and finished. The same normalized answer a degraded
        # context gets, and for the same reason — the preserved `task_ref` and
        # `base_path` must never reach `roll_window`, and no new enum value may
        # reach a trace row or the wire (both hooks stamp `lc.mode` verbatim) —
        # but NOT degraded: nothing was diverted, the marker is not stale, it is
        # an honest record that the loop ended, and JC18's operator signal must
        # stay silent on a healthy repo. The rows carry `after_loop` instead, so
        # a post-close row is still attributable to the loop it follows.
        #
        # `task_ref` and `base_path` stay in the FILE: `run_gate_checks.
        # _active_context_ref` and `insights_flush_payload` join on `task_ref`,
        # and `check-journal.sh:71-74` exits 2 on an empty `base_path`, refusing
        # every sub-agent completion. What is emptied is this resolution, not
        # the marker.
        return _routed(NO_LOOP_DIR, after_loop=ref)

    if mode == "interactive":
        # 🔴 AN INTERACTIVE MARKER STILL DOES NOT MAKE THESE LEDGERS THIS
        # SESSION'S. `/fairmind-develop` repoints `mode` to `interactive` with
        # `base_path` pointing at `.fairmind` — the folder the LOOP lane
        # documents — whether by passing `--base-path .fairmind` or by
        # inheriting what the finished loop left behind. Either way the finished
        # loop's `loop-state.json` is still sitting there and the ordinary
        # answer below writes that loop's own token ledger: 502 in-window rows
        # evicted, measured; see NO_LOOP_DIR. The existence of a
        # `loop-state.json` in the ledger home is the whole test, and LIVE or
        # TERMINAL both route, because in both cases those ledgers are a loop's
        # and this session is not it.
        #
        # Nothing else about an interactive session changes: its journals, its
        # `base_path` scope and its workspace are untouched, and it is NOT
        # degraded — an inherited `base_path` is the documented hand-off, not a
        # stale marker, so JC18's operator signal must stay silent. Only the two
        # capture LEDGERS move.
        # TWO QUESTIONS, ONE PER LANE, because the two ledgers are keyed
        # differently — a directory test alone leaves the trace lane open in
        # BOTH directions of a `base_path` change while the ref is inherited,
        # and both directions are a shipped command's own instruction. See
        # `_trace_ledger_belongs_to_a_loop`.
        if (_ledger_home_belongs_to_a_loop(cwd, base)
                or _trace_ledger_belongs_to_a_loop(cwd, ref)):
            # 🔑 AND IT CARRIES NO `after_loop`. On the `closed` branch that key
            # is honest — the marker's `task_ref` IS the loop's own ref. Here it
            # is THIS session's ref (`STORY-7` for a develop run following loop
            # `T99`), so stamping it under a key that means "the loop this row
            # follows" would be a false claim on the artifact. The directory
            # already carries the whole fact.
            return _routed(NO_LOOP_DIR)
        # No loop owns the ledger home -> always capture, under the context's
        # own ref, in the ordinary location (`ledger_dir` stays None).
        return LoopContext(mode=mode, base=base or "", ref=ref, live=True,
                           started_at=None)

    # An unrecognized `mode`: a future value, a hand-edit, a non-string. Capture
    # (silence is the one answer JC8 forbids) but attribute to nothing, and say
    # so on the row — a marker we cannot read is a broken marker, which is
    # exactly what the operator signal exists to surface.
    return _routed(DEGRADED_DIR, degraded=True, degraded_from=ref)


def _routed(ledger_dir, *, degraded=False, degraded_from=None, after_loop=None):
    """The normalized answer plus its routing: capture, but attribute to no loop.

    ONE construction for every case that reaches it — the callers are the rows of
    `resolve_loop_context`'s table and are counted nowhere, here least of all,
    since each round has added one — because the normalization
    is the part that must not drift — `mode -> interactive` (no new enum value
    reaches a row or the wire), `base -> ""` and `ref -> DEGRADED_REF` (the
    loop's own ledger names can never reach `roll_window`), `started_at -> None`
    (there is no window to anchor rotation to). What differs per case is only
    where the rows go and what they say, which is what the arguments carry."""
    return LoopContext(mode="interactive", base="", ref=DEGRADED_REF, live=True,
                       started_at=None, degraded=degraded,
                       degraded_from=degraded_from, ledger_dir=ledger_dir,
                       after_loop=after_loop)


def _ledger_home_belongs_to_a_loop(cwd, base):
    """Does a `loop-state.json` sit in the directory whose ledgers this context
    would write into? If it does, those ledgers are that loop's.

    ⚠️ THE QUESTION IS ABOUT THE LEDGER HOME, NOT ABOUT `base_path`, and the two
    differ in exactly one shape: a marker with NO (or a non-str) `base_path`.
    `_live_loop_started_at` reads that as "names no loop at all" and stops, which
    is right for LIVENESS — there is no loop to be live. It is wrong here,
    because the hooks do not stop: `loop_ledger_path`'s empty-`base` fallback
    still writes into `.fairmind/`, which IS a loop's own ledger home on the
    documented `base_path: ".fairmind"`. So this asks about `base or .fairmind`
    — the hooks' own fallback, named once in `_WORKSPACE_DIR` — and answers for
    the directory that will actually be appended into.

    ⚠️ THIS TEST AND `loop_ledger_path` SHARE THE HOME AND ANSWER DIFFERENT
    QUESTIONS. That one resolves where the TOKEN LEDGER is written — and since
    the ledger is keyed by ref, its FILENAME is no longer derivable from `base`
    at all; this one asks whether a `loop-state.json` is SITTING in that home.
    Only the directory half is shared, which is why the two are deliberately not
    collapsed onto one call. Keying the ledger did not move this test, exactly
    as the previous revision of this note predicted it would not.

    ⚠️ AND IT IS TWO HOMES, NOT ONE, BECAUSE THE TWO LANES ARE KEYED
    DIFFERENTLY. Both ledgers are now named by ref, but in different
    directories: the token ledger at `<base or .fairmind>/subagent-tokens-<ref>
    .jsonl`, the trace ledger ALWAYS at `.fairmind/trace/<ref>.jsonl`, a fixed
    directory that ignores `base_path` entirely. So a marker whose
    `base_path` MOVED while its `task_ref` was INHERITED passes a check on
    `base` alone and still writes the previous loop's own trace ledger.
    `commands/fairmind-develop.md`'s Phase 2 is exactly that shape — it narrows
    `--base-path` to `.fairmind/<project>/<session>` while reusing the command's
    `<ref>` — so after `/fairmind-loop STORY-7` on the documented
    `base_path: ".fairmind"`, `/fairmind-develop STORY-7` reached
    `.fairmind/trace/STORY-7.jsonl` and evicted 502 in-window rows (measured
    2026-08-16). Asking about the workspace root as well closes that shape.

    ⚠️ AND IT IS STILL NOT ENOUGH ALONE — see `_trace_ledger_belongs_to_a_loop`,
    which answers the same ownership question for the lane this one cannot
    reach. A directory test cannot cover a ledger named by a ref.

    ⚠️ THE PREDICATE IS NOW A DELIBERATE OVER-APPROXIMATION, AND THE REASON IT
    STAYS ONE IS THE ASYMMETRY OF THE TWO ERRORS. Its original cause was that the
    token ledger at this home was UNCONDITIONALLY the loop's file. Since the
    ledger is keyed by ref that holds only when the `task_ref` was INHERITED —
    with a different ref the two contexts name different files and there is
    nothing to collide. It is not narrowed to match, because a directory test
    cannot see the trace lane's own collision (`_trace_ledger_belongs_to_a_loop`
    exists for exactly that), and because the two errors do not cost the same: a
    context diverted when it did not need to be loses ATTRIBUTION — its rows are
    in `NO_LOOP_DIR`, whole — while one left in place when it did need diverting
    loses DATA to the other loop's rotation, which is unrecoverable.

    Nothing is inferred from the loop-state's CONTENTS: a running loop and a
    finished one both own their ledgers, and an unreadable one is still evidence
    that a loop lives there. Existence is the whole test."""
    homes = [base or _WORKSPACE_DIR]
    if homes[0] != _WORKSPACE_DIR:
        homes.append(_WORKSPACE_DIR)
    return any(os.path.isfile(os.path.join(cwd, home, "loop-state.json"))
               for home in homes)


def _trace_ledger_belongs_to_a_loop(cwd, ref):
    """Was the trace ledger THIS context would write to opened by a LOOP?

    🔴 THE SECOND OWNERSHIP QUESTION, AND THE DIRECTORY TEST ABOVE STRUCTURALLY
    CANNOT ANSWER IT. The trace ledger is chosen by REF in a FIXED directory
    (`.fairmind/trace/<sanitize_ref(ref)>.jsonl`) that ignores `base_path`
    entirely, so both directions of a `base_path` change slip past a test on the
    directory while the ref is inherited:

      * base NARROWS — `/fairmind-develop` Phase 2 repoints to
        `.fairmind/<project>/<session>` after a loop bootstrapped on the
        documented flat `.fairmind`;
      * base WIDENS — the loop ran NESTED (the Workspace contract's own shape,
        `loop-state.json` at `.fairmind/<project>/<session>/`) and
        `/fairmind-develop` Phase 0 repoints to `--base-path .fairmind`, where
        no `loop-state.json` sits at all.

    Both were measured at 502 evicted in-window rows, and BOTH are a shipped
    command's own instruction rather than a hand-edit. An earlier revision of
    this module claimed closing them "needs the set of refs every loop on disk
    has used, which is a scan this hot path cannot afford". That was wrong, and
    the correction came from a cross-model review: the ledger in question is
    named by THIS context's own ref, so it is one `open` of one file.

    THE FIRST ROW IS THE ORACLE, not the file's existence. Existence alone would
    route an ORDINARY interactive session away from its OWN accumulating ledger
    from its second session onward — the ledger exists precisely because that
    session has been writing it. Every row carries the `mode` it was written
    under, so the first row says who opened the file. A missing, empty or
    unreadable ledger is not evidence of a loop and answers False: this predicate
    only ever DIVERTS, so the conservative answer is the one that leaves the
    ordinary interactive path alone.

    Bounded on purpose — ONE line, not the file. This runs on every tool call."""
    path = os.path.join(cwd, _WORKSPACE_DIR, "trace", sanitize_ref(ref) + ".jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                return json.loads(line).get("mode") == "loop"
    except (OSError, ValueError):
        return False
    return False


def _load_loop_state(cwd, base):
    """The parsed `loop-state.json` at the ledger home, or None when there is
    none, it does not parse, or it is not an object.

    ONE reader for the two questions asked of that file — liveness and identity —
    so the guard against a non-dict / unparseable state cannot come to differ
    between them. `resolve_loop_context` calls it ONCE and passes the parsed
    state to both, which is the cheap form on a per-tool-call hook and also the
    correct one: two reads can answer the two questions from two different
    files. Neither consumer's RETURN carries the other's fields, so
    `_live_loop_started_at` stays the narrow liveness oracle that
    `check-journal.sh:84-95` hand-mirrors and
    `tests/test_liveness_rule_parity.py` keeps in step."""
    ls_path = os.path.join(cwd, base, "loop-state.json") if base else ""
    if not ls_path or not os.path.isfile(ls_path):
        return None
    try:
        with open(ls_path, encoding="utf-8") as fh:
            ls = json.load(fh)
    except Exception:
        return None
    return ls if isinstance(ls, dict) else None


def _stripped(v):
    """`v` with surrounding whitespace removed when it is a string, unchanged
    otherwise. Whitespace-only becomes `""`, which the caller reads as absent."""
    return v.strip() if isinstance(v, str) else v


def _both_present_and_differ(a, b):
    """True only when both sides are non-empty strings AND they differ.

    The shared shape of the two identity tests below, in one place so they can
    never come to disagree about what "undecidable" means. Anything that is not
    a non-empty string on either side yields False — the fail-open the caller's
    docstring justifies."""
    return bool(isinstance(a, str) and a and isinstance(b, str) and b and a != b)


def _belongs_to_another_loop_or_session(ls, task_ref, session_id):
    """Is the live loop whose already-parsed state is `ls` a loop — or a
    session — OTHER than the one this context names? True routes the rows to
    `NO_LOOP_DIR`.

    TWO INDEPENDENT TESTS, WITH DELIBERATELY DIFFERENT COVERAGE WINDOWS. Both
    fail OPEN (return False) the moment either side of the comparison is missing
    or is not a non-empty string, and an undecidable identity must never divert:
    the cost of a wrong divert is the loop losing its own rows, which is the
    failure JC8 measured at 23 days of silence, while the cost of a wrong stay is
    the collision this function narrows.

    1. WHICH LOOP (JC20) — `target.ref` vs the context's `task_ref`. Needs no
       session id, so it holds from the moment the context is repointed.
       `loop_open.py --repoint` rewrites active-context's `task_ref` and touches
       no loop-state, so a context naming loop 2 over loop 1's loop-state is what
       the shipped opener leaves behind until the Technical Lead emits the new
       contract — and in that window the loop-state's `started_at` is still loop
       1's, so loop 2's rows would be stamped into loop 1's window.
       An ABSENT or non-str `target.ref` fails open because `target` is written
       freehand by the Technical Lead (`skills/fairmind-gate/references/
       loop-state.md:12`) and by `scripts/loop_import.py:499` in code, so a
       loop-state without one is an ordinary legacy or hand-armed shape; routing
       its OWN rows away would be a regression worse than the collision.
    2. WHOSE SESSION (JC22) — `owner_session` vs the payload's session id.
       `run_gate_checks.arm()` stamps the arming session as the owner —
       `--session-id`, else `CLAUDE_CODE_SESSION_ID`, which the host sets in
       every Bash subprocess and which is how `/fairmind-loop`'s bare `--arm`
       names itself — and clears it when neither is available. A running loop
       left without an owner is claimed by the first session whose Stop drives
       the gate (the session-ownership block in `run_gate_checks._dispatch()`).
       This test engages only once an owner is recorded.

    ⚠️ THE ASYMMETRY IN 2 IS MANDATORY, NOT A CONVENIENCE. A running loop can
    legitimately have no owner — armed with no session id, it has none until its
    first Stop — and a hook payload can carry no session id, or a blank one.
    Failing closed on either would divert the OWNING session's own rows; after
    an ownerless arm, precisely the rows of the iteration that just started.

    🔴 THE RESIDUALS, DECLARED RATHER THAN LEFT SILENT.
      * A loop armed with no session id is NOT covered by test 2 until its
        first Stop: two sessions working it in that window are
        indistinguishable here, and both write its ledgers. Test 1 covers that
        window in full, since `target.ref` is written before the arm and never
        popped — which is why the two halves are not folded into one test with
        one condition.
      * Test 1 narrows the WINDOW between the repoint and loop 2's own
        loop-state; it never closed the collision, and no identity test could.
        Once loop 2's loop-state is emitted at the same `base_path`, its
        `target.ref` matches the context, this session genuinely IS that live
        loop, and every test here answers "stay" — correctly. That case was a
        ledger-KEY question rather than a routing one, and it is answered where
        it lives: `loop_ledger_path` keys the token ledger by ref, the way the
        trace ledger already was. What remains this function's job is the
        window BEFORE that loop-state exists, which is what test 1 covers.
      * 🔴 THE `target.ref` TEST COSTS A LEGITIMATE SESSION ITS SETTLE SIGNAL,
        AND THAT REACHES A TERMINAL VERDICT. A session legitimately working a
        `task_ref` OTHER than the loop's target — a divergence
        `_gate_mutation.trace_path` names as valid — now has its capture
        routed to `no-loop/`, and `_gate_mutation.trace_path` still resolves
        `.fairmind/trace/<task_ref>.jsonl`, which is now empty.
        THE EARLIER VERSION OF THIS BULLET NAMED ATTRIBUTION AS THE WHOLE COST
        AND CONCLUDED "it changes no gate VERDICT". That conclusion is false,
        and the reason it survived is that it reasoned about ONE reader of that
        file. The readers were then enumerated from source — `trace_path(`
        call sites in `run_gate_checks.py` (`:1298` scope attribution, `:2401`
        `_no_work_signature`, `:2510` `_settle_age`), `insights_flush_payload.
        _trace_path` (`:2005`) and `loop_dashboard`'s own join (`:81`) — and
        each was driven on both arms. The list is a survey of the call sites
        that grep found, NOT a proof that no other reader exists; re-run it
        rather than trusting this sentence, and treat anything it surfaces that
        is missing here as the defect.
        What the survey found, on one fixture (`active-context` `task_ref` X
        over a live loop-state whose `target.ref` is Y, one work-product write
        by the real hook), before this rule -> after it:
          - `_settle_age` 0.9 s -> None, so `in_flight` (`:2947`) is pinned
            False and H8's in-flight freeze — which exists precisely so a
            Stop-hook gate does not charge a half-written tree — silently stops
            engaging. THIS IS THE ONE THAT REACHES A VERDICT.
          - the loop payload's trace rows 1 -> 0, i.e. the Judge corpus lane
            loses that session's ops entirely. Arguably the larger cost of the
            two, since a verdict is recoverable by re-arming and data is not.
          - the dashboard's trace ops 2 -> 0.
          - scope attribution agents ['software-engineer', 'unknown'] ->
            ['unknown'], the cost the earlier version named.
          - `_no_work_signature` UNAFFECTED, 2 members and non-degraded in both
            arms — membership is git-derived and the trace only decorates it
            (`_gate_mutation._numstat`), which is why `no_work` does not
            move and the verdict change below is H8's alone.
        Driven end to end through the real engine on the same shape, with the
        maker writing inside the 45 s window: before, `in_flight` true on every
        evaluation, `consecutive_failures` 0 and `budget.spent.iterations` 0
        through the third evaluation, still `running` at the fourth (1 charged);
        after, `in_flight` absent, `consecutive_failures` 1 -> 2 -> 3 and
        `budget.spent.iterations` 1 -> 2 -> 3, reaching the TERMINAL
        `blocked_failures` at the third. The control — the identical fixture
        with `target.ref` == `task_ref` — is `running` / 0 / 0 in both arms, so
        the measurement is not vacuous.
        This is a REGRESSION on a shape the engine documents as valid, declared
        here rather than softened: it is not closed, and closing it means the
        trace lane keying off the loop's `target.ref` instead of the context's
        `task_ref`, which is the same ledger-KEY direction named above.
      * THE `owner_session` TEST MOVES THE SAME SIGNAL THE OTHER WAY, and the
        two must not be read as one cost. Before it, a foreign session's mutate
        ops appended into `.fairmind/trace/<the loop's own ref>.jsonl`, so work
        the loop did not do froze the loop's own gate. Measured on the same
        harness with only the foreign session writing: `in_flight` True before,
        False after, while the owning session's own write keeps it True in both
        arms. That direction REMOVES a false freeze rather than losing a real
        one — the foreign session's Stop never evaluates the gate anyway (the
        session-ownership block in `run_gate_checks._dispatch()` returns
        `EXIT_ALLOW_STOP` on the mismatch)."""
    if ls is None:
        return False

    # 🔴 BOTH TESTS NORMALIZE, AND THE ASYMMETRY THIS REPLACES WAS A DEFECT
    # (found by Grok in the cross-model round). The session half stripped and
    # the ref half did not, so `task_ref: "T99 "` against `target.ref: "T99"`
    # read as a DIFFERENT loop and diverted a legitimate session's rows — and a
    # whitespace-only `task_ref` is truthy, so it missed the absent-ref
    # fail-open and looked like a different loop too. Stripping both sides of
    # both tests collapses the two spellings of empty ("" and "   ") into one
    # before the comparison, so an absent value takes the fail-open path
    # whichever way it is spelled. The session half already mirrored the gate's
    # own normalization of the value it stores (`run_gate_checks` writes the id
    # `.strip()`ped, in `arm()` and in the Stop claim alike); the ref half now
    # matches it.
    target = ls.get("target")
    state_ref = target.get("ref") if isinstance(target, dict) else None
    if _both_present_and_differ(_stripped(state_ref), _stripped(task_ref)):
        return True

    return _both_present_and_differ(_stripped(ls.get("owner_session")),
                                    _stripped(session_id))


def _live_loop_started_at(ls):
    """`(loop_is_live, started_at)` for the already-parsed loop-state `ls`
    (None when there is none at the ledger home, or it was unreadable).

    `started_at` is None for a live loop that has not ARMED yet — a real state
    in which capture is real — which is why the pair exists rather than a
    None-means-dead return. The DETECTION mirrors check-journal.sh:84-95; the
    consequence deliberately does not — see the note on `_TERMINAL_EXACT`."""
    if ls is None:
        # No loop-state at base_path (or an unreadable one) -> the loop never
        # armed or is long gone.
        return False, None

    # Finding 3: a non-str status (int/list/dict) would make the terminal test's
    # `status.startswith(...)` / `status in {set}` raise — coerce to str first.
    if _is_terminal(str(ls.get("status") or "")):
        # passed_pending_human / blocked_* -> the loop is done; stop attributing
        # to it, but keep capturing (JC8).
        return False, None

    try:
        return True, (((ls.get("budget") or {}).get("spent") or {}).get("started_at")) or None
    except Exception:
        return True, None


def _atomic_write_lines(path, lines, reconcile_from=None):
    """Rewrite `path` from already-serialized JSONL `lines` (each ending in "\\n")
    atomically: mkstemp in the same dir, write, `os.replace`. Mirrors
    audit_run_meta._atomic_write_json / loop_ledger._write_rows — a rotation can
    never leave a reader with a truncated or half-written ledger.

    Reconcile channel (finding 1): when `reconcile_from` is not None, `_roll_window`
    passes the row count of the snapshot it read. This writer then re-reads `path`
    immediately before its replace and folds in any rows at positions past that
    boundary — i.e. appended by a CONCURRENT fire since the snapshot — so
    window-safe rotation never clobbers a concurrent in-window row. None disables
    the fold (a plain rewrite)."""
    lines = list(lines)
    boundary = reconcile_from
    if boundary is not None:
        try:
            with open(path, encoding="utf-8") as fh:
                current = [ln if ln.endswith("\n") else ln + "\n"
                           for ln in fh if ln.strip()]
            if len(current) > boundary:
                lines = lines + current[boundary:]
        except OSError:
            pass
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".loop-ledger.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _in_window(line, started):
    """True if `line`'s row must be KEPT (never rolled). With a window boundary
    `started`, a row is in-window iff its ts >= started; a row whose ts is
    missing or unparseable is treated as in-window (kept). Pre-arm (started is
    None) is handled by the caller — no row is window-protected there."""
    try:
        t = _parse_iso(json.loads(line).get("ts"))
    except Exception:
        t = None
    if t is None:
        return True
    try:
        return t >= started
    except TypeError:
        return True


def roll_window(path, started_at, cap=2000):
    """Best-effort window-safe rotation of the active JSONL ledger at `path`.

    Contract:
      - efficiency: an `os.path.getsize` gate returns after a single stat() when
        the file is small enough that its row count cannot exceed `cap`.
        ⚠️ MEASURED 2026-08-15, because this used to claim "the hot path never
        reads the whole ledger" without qualification and that is false past a
        few hundred rows. `_MIN_ROW_BYTES` is 40 while a real row measures 217 B
        (33 ledgers, 12,160 rows), so the stat-only path holds to ~369 real rows
        and no further: at 369 rows the gate costs 50.6 us instead of 1.6, at
        1999 rows 200.6 us. Past that every fire reads and splits the file and
        usually rotates nothing. It is left as is deliberately — 200 us sits
        against ~35 ms of interpreter startup per hook fire (0.6%), and the
        constant cannot be raised far without eroding the margin that makes the
        gate SAFE (the smallest row ever written is 101 B). The number to fix
        here was the CLAIM, not the constant. The two routed ledgers
        (`DEGRADED_DIR`, `NO_LOOP_DIR`) live in this regime permanently by
        design: neither has a window, so each saturates at 1500-2000 rows and
        never resets.
      - hysteresis: rotation triggers only once the count exceeds `cap`, and then
        trims down to a lower watermark (~0.75*cap), so a steady stream of over-cap
        fires rewrites the file about once per 0.25*cap fires, not every fire
        (amortized O(1)).
      - window-safety (INVARIANT): a row with ts >= started_at is NEVER dropped;
        only the OLDEST out-of-window rows (ts < started_at) roll. A row with a
        missing/unparseable ts is kept.
      - pre-arm (started_at is None): no window exists yet, so fall back to a pure
        newest-N cap (keep the newest rows by file position) — the ledger stays
        bounded even before the loop arms.
      - a rotation failure must NEVER break the hook: everything here is
        swallowed, so the caller keeps its fail-open exit 0.
    """
    try:
        _roll_window(path, started_at, cap)
    except Exception:
        pass


def _roll_window(path, started_at, cap):
    # Finding 2: distinguish a truly-ABSENT started_at (None -> pre-arm; fall back
    # to the newest-N positional cap so the ledger stays bounded before the loop
    # arms) from one SUPPLIED but UNPARSEABLE (garbage). An unparseable boundary is
    # NOT trustworthy, so rotation is SKIPPED and every row kept — never risk
    # dropping a current-loop row behind a malformed window. (The old code parsed
    # both to None and took the pre-arm trim, dropping live rows on a garbage ts.)
    if started_at is None:
        started = None
    else:
        started = _parse_iso(started_at)
        if started is None:
            return

    try:
        size = os.path.getsize(path)
    except OSError:
        return
    # Size proxy: below this the row count provably cannot exceed cap -> skip the
    # full read+parse entirely (this is the every-fire fast path).
    if size <= cap * _MIN_ROW_BYTES:
        return

    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
    except OSError:
        return

    n = len(lines)
    if n <= cap:  # high watermark == cap; nothing over the cap to roll
        return

    low = cap - cap // 4              # low watermark: trim target, headroom for amortization

    if started is None:
        # Pre-arm: no window to protect -> every row is roll-eligible, keep the
        # newest `low` by file position.
        protected, trimmable = [], list(range(n))
    else:
        protected, trimmable = [], []
        for i, ln in enumerate(lines):
            (protected if _in_window(ln, started) else trimmable).append(i)

    # Keep every protected row plus the NEWEST out-of-window rows up to the low
    # watermark; drop only the OLDEST out-of-window rows (by file position).
    keep_old = max(0, low - len(protected))
    n_drop = max(0, len(trimmable) - keep_old)
    if n_drop == 0:
        return  # nothing droppable (all rows are in-window) -> leave as is
    drop = set(trimmable[:n_drop])
    kept = [ln for i, ln in enumerate(lines) if i not in drop]

    # Pass the snapshot boundary so `_atomic_write_lines` can fold in any rows a
    # concurrent fire appended since we read `lines` (window-safety, finding 1).
    _atomic_write_lines(path, kept, reconcile_from=n)


def append_row(path, row, started_at, cap=2000):
    """Append one serialized JSONL `row` (no trailing newline required) to the
    active ledger at `path` and rotate it, as ONE unit, under a per-ledger
    advisory lock (finding 1).

    Both capture hooks call this INSTEAD of a bare `open(path,"a").write(row)`
    followed by a SEPARATE `roll_window`. Done as two steps, a concurrent fire
    could append an in-window row between the rotation's snapshot read and its
    `os.replace`, and that row was clobbered (lost) — violating window-safety,
    which the gate relies on when it reads the trace whole for the mutation set /
    settle timing / attribution.

    Portability + fail-open:
      - POSIX (`fcntl`): take a NON-BLOCKING `LOCK_EX` on the ledger and hold it
        across append+rotate. If the lock is contended, still append the row but
        SKIP rotation this fire (rotation defers to an uncontended one — the row
        is never lost and the hook never blocks).
      - Windows / no `fcntl`: best-effort append, then rotate; the rotation
        re-reads the file immediately before its atomic replace and folds in any
        rows appended since its snapshot, shrinking the clobber window.
      - Any lock/IO error is swallowed: the append is best-effort and this NEVER
        raises or blocks, so the caller keeps its exit-0 fail-open contract.
    """
    try:
        _append_row(path, row, started_at, cap)
    except Exception:
        pass


def _append_row(path, row, started_at, cap):
    line = row if row.endswith("\n") else row + "\n"
    # PCF-28: the same makedirs as before, plus the consumer-repo ignore entry.
    # THE CHOKE POINT for both capture hooks — trace-op writes only through
    # here, and capture-subagent/orchestrator-tokens write their ledger through
    # here — which is why neither of them manages the entry itself.
    try:
        makedirs_ignored(os.path.dirname(os.path.abspath(path)))
    except OSError:
        pass

    if _fcntl is None:
        # No advisory locking (Windows): best-effort append, then rotate — the
        # rotation's re-read-before-replace fold is the concurrency safety net.
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            return
        roll_window(path, started_at, cap)
        return

    # POSIX: hold a per-ledger advisory lock across append+rotate as one unit.
    try:
        fh = open(path, "a", encoding="utf-8")
    except OSError:
        return
    locked = False
    try:
        try:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            locked = True
        except OSError:
            locked = False  # contended -> append only, defer rotation to a free fire
        fh.write(line)
        fh.flush()
        if locked:
            roll_window(path, started_at, cap)
    finally:
        try:
            if locked:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()
