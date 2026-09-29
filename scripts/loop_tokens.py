#!/usr/bin/env python3
"""
loop_tokens.py — best-effort per-loop token totals for the loop dashboard.

Two sources, both attributed to a loop's `[start, end]` window:
  - orchestrator (main) tokens — summed from the Claude Code session transcript(s)
    `~/.claude/projects/<mangled-cwd>/*.jsonl`. The transcript SCHEMA is a CC internal
    (undocumented, may change), so this is best-effort: any failure returns None → the
    dashboard shows `n/a`.
  - sub-agent tokens — summed from the token ledgers under `${base}`, which the
    SubagentStop hook (capture-subagent-tokens.sh) writes at each dispatch. Those files
    are ours, so they are robust; still windowed by timestamp. The ledger is keyed by
    REF (`subagent-tokens-<sanitized ref>.jsonl`) and readers union the home — see
    `_loop_ledger.loop_ledger_paths`, which owns that rule.

Token fields are kept raw ({in, out, cache_creation, cache_read}); the dashboard forms
"↑" = in + cache_creation and "↓" = out (matching the harness `subagent_tokens`).
Stdlib only.
"""

import argparse
import itertools
import json
import os
import re
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _usage_dedup  # noqa: E402  (PL-A0 shared dedup helper — the single sum oracle)

# This module's SHORT field names -> the canonical Anthropic usage names
# `_usage_dedup.TOKEN_FIELDS` sums under. The two spellings exist because the
# dashboard's own ledger rows (`subagent-tokens.jsonl`) have always used the
# short form.
#
# WHAT IS AND IS NOT SINGLE-SOURCED, because an earlier revision of this comment
# said "written ONCE, here, so the translation cannot drift" and that was not
# true. Every PYTHON reader of the short names derives them from this dict
# (`_FIELDS` below; `insights_flush_payload` imports `_FIELDS`). The two BASH
# capture hooks build their ledger rows with the same four pairs spelled inline
# — `"in": totals["input_tokens"]`, and so on — inside a `python3 -c` block, and
# they are the writers this dict describes. So the guarantee is: one statement
# per PROCESS BOUNDARY, not one in the tree. A fifth usage field has to be added
# here and in both hooks.
_CANONICAL = {
    "in": "input_tokens",
    "out": "output_tokens",
    "cache_creation": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
}

# DERIVED, never restated: `read_subagent_ledger` and `insights_flush_payload`
# (which imports this name) iterate `_FIELDS`, so a field added to `_CANONICAL`
# reaches them without a second edit. Written as a literal tuple, this drifted
# the moment `_CANONICAL` grew — the readers would silently sum four of five.
_FIELDS = tuple(_CANONICAL)

# The `agent_type` the Stop hook (hooks/scripts/capture-orchestrator-tokens.sh)
# stamps on the orchestrator's own token rows. Defined HERE, next to the reader
# that has to skip them, and imported by the hook that writes them — one literal,
# so a producer/consumer rename cannot leave the filter below silently matching
# nothing.
#
# The value is not a new coinage: `trace-op.sh:63` already falls back to "main"
# for the orchestrator's trace rows (2,630 of them across 14 real trace files,
# the single largest agent bucket), and `ambient_digest.MAIN_ROLE` is the same
# string in the ambient plane. A fourth spelling would make "group by role
# across the fleet" unanswerable.
ORCHESTRATOR_AGENT_TYPE = "main"


def _parse_iso(s):
    if not s:
        return None
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mangle(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def orchestrator_transcripts(cwd, home=None):
    """The CC session transcript files for this repo (best-effort; [] if none)."""
    home = home or os.path.expanduser("~")
    d = os.path.join(home, ".claude", "projects", _mangle(cwd))
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl"))


def _usage_records_in_window(paths, start, end):
    """Yield every usage-bearing transcript record across `paths` whose top-level
    `timestamp` falls in [start, end], in file order.

    THE WINDOW IS APPLIED HERE, i.e. BEFORE the dedup downstream, and the order
    is load-bearing rather than incidental. A streamed message can straddle the
    loop's start: its first copy lands before `start` and later copies inside.
    Filtering first lets the first IN-WINDOW copy contribute; deduping first
    would let the out-of-window copy claim the id and the whole message would
    vanish from the loop's figure.

    A record carrying a `usage` DICT is yielded whether or not that dict is
    empty, so "usage present but zero" (report 0) stays distinguishable from "no
    usage at all" (report nothing) — the same rule
    `hooks/scripts/capture-subagent-tokens.sh` applies on the other half of this
    pair. The caller reads that distinction off whether this generator yields at
    all, so it never materializes a 17 MB transcript into a list."""
    for p in paths:
        try:
            fh = open(p, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                # A transcript line may be valid JSON but not an object (null,
                # []); guard before .get() so one junk line never unwinds the
                # whole read.
                if not isinstance(o, dict):
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                    continue
                ts = _parse_iso(o.get("timestamp"))
                if ts is None or ts < start or ts > end:
                    continue
                yield o


def sum_usage_in_window(paths, start, end):
    """Sum message.usage over transcript files for lines whose top-level `timestamp`
    is in [start, end]. Returns a dict, or None if no usage block was found.

    PCF-27 — SUMMING IS DELEGATED ENTIRELY TO `_usage_dedup`, the same oracle
    `ambient_digest` and both capture hooks already use. This function summed
    per LINE, and a streamed assistant message repeats its `usage` block once
    per content block under one `message.id`, so the figure a developer read at
    loop close carried the ~2.7x inflation while the fleet-facing figure for the
    same session was deduped. Two readers of one transcript reporting two
    numbers is the defect; importing the one that is right is the fix.

    ONE `seen` SET ACROSS ALL `paths`, not one per file — a resumed or compacted
    session's lines land in more than one `*.jsonl` under the same project dir,
    and `orchestrator_transcripts` returns all of them. Deduping per file and
    adding the dicts would count a repeated id once per file, which is the same
    defect one directory level up.

    THE ONE BEHAVIOURAL CHANGE, stated rather than discovered: a record whose
    `usage` is an EMPTY dict used to be skipped by the old falsiness test, so a
    transcript of nothing but empty-usage records returned None (`n/a`). It now
    counts as usage-present-but-zero and returns zeros. That is deliberate on
    both counts: it is what the sub-agent capture hook already decided for the
    other half of this pair, and it is what `_usage_dedup` does — an empty-usage
    record claims its id — so the two figures agree on that shape as well
    instead of only on the common one.

    THE `n/a` SENTINEL IS "THE GENERATOR YIELDED NOTHING", read by pulling ONE
    record and putting it back. An earlier revision threaded a mutable flag into
    the generator and read it afterwards, which made the sentinel silently
    depend on `deduped_usage_totals` consuming the iterable to completion:
    make that sum short-circuit or lazy and a session with real usage starts
    rendering `n/a`, with nothing failing at the seam."""
    records = _usage_records_in_window(paths, start, end)
    first = next(records, None)
    if first is None:
        return None
    totals = _deduped_per_model(itertools.chain((first,), records))
    return {short: totals[canonical] for short, canonical in _CANONICAL.items()}


def _deduped_per_model(records):
    """Sum `records` with `_usage_dedup`, claiming each `message.id` at most once
    PER MODEL — the scope `ambient_digest.digest` uses, not a flat global one.

    THE SCOPE IS THE WHOLE POINT OF THE FIX. Routing through the oracle removed
    the per-line inflation and left a second, quieter split: this reader deduped
    globally while the fleet reader claims an id once per model (`seen_by_model`
    in `ambient_digest.digest`, which keys by model deliberately so per-model
    totals stayed identical to pre-T2C1 by construction). One `message.id`
    appearing under two model ids therefore counted ONCE here and TWICE there —
    the same session, two numbers, which is the defect PCF-27 was opened for,
    surviving one scope away from its own fix. Found by cross-model review; the
    parity tests could not see it because every fixture used one model.

    `_usage_dedup.dedup_key` is asked for the key rather than re-deriving it, so
    "may this record be suppressed as a repeat" still has exactly one
    definition."""
    totals = {field: 0 for field in _usage_dedup.TOKEN_FIELDS}
    seen_by_model = {}
    for rec in records:
        # MEMBERSHIP AND SCOPE FROM THE SAME ORACLE. `accounting_model` is what
        # `ambient_digest.digest` asks too, so "is this record in the token
        # accounting, and under which model" has one definition. Reading
        # `message.model` here instead left the developer-facing figure counting
        # `<synthetic>` and model-less records the fleet figure excludes — the
        # same session, two numbers, one skip away from the fix for that.
        model = _usage_dedup.accounting_model(rec)
        if model is None:
            continue
        mid = _usage_dedup.dedup_key(rec)
        if mid is not None:
            claimed = seen_by_model.setdefault(model, set())
            if mid in claimed:
                continue
            claimed.add(mid)
        # Handed to the oracle ONE record at a time: it owns the field list and
        # the int coercion, and an id already claimed above never reaches it, so
        # its own flat dedup can never additionally suppress a second model.
        single = _usage_dedup.deduped_usage_totals((rec,))
        for field in _usage_dedup.TOKEN_FIELDS:
            totals[field] += single[field]
    return totals


def read_subagent_ledger(path, start, end):
    """Sum the SUB-AGENT rows in [start, end]. Our file → robust.

    Orchestrator rows (`agent_type == ORCHESTRATOR_AGENT_TYPE`) share this
    ledger — the Stop hook appends them there deliberately, so that
    `insights_flush_payload._loop_agents`, which groups by `agent_type`, picks
    the main thread up with no special-casing. They are EXCLUDED here because
    this reader has no such grouping: it returns one flat sum, and
    `loop_dashboard._tokens_cell` ADDS it to the `orchestrator` figure that
    `sum_usage_in_window` derives independently from the session transcript.
    Counting the rows here too would report the orchestrator twice in the one
    cell — a defect introduced by the hook, not a pre-existing one, so the
    filter ships with it.
    """
    if not os.path.isfile(path):
        return None
    tot = {k: 0 for k in _FIELDS}
    seen = False
    try:
        for line in open(path, encoding="utf-8"):
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not isinstance(o, dict):
                continue
            if o.get("agent_type") == ORCHESTRATOR_AGENT_TYPE:
                continue
            ts = _parse_iso(o.get("ts"))
            if ts is None or ts < start or ts > end:
                continue
            seen = True
            for k in _FIELDS:
                tot[k] += o.get(k, 0) or 0
    except OSError:
        return None
    return tot if seen else None


def loop_tokens(cwd, base, start_iso, end_iso=None, home=None):
    """Best-effort {orchestrator, subagent} token dicts (each dict or None) for a loop
    window. Everything is guarded — a broken/absent source degrades to None, never
    raises, so the dashboard can always render."""
    try:
        # DEFERRED ON PURPOSE, and a module-level import here does not work.
        # `_loop_ledger` imports `_parse_iso` from THIS module at its own top
        # level, so the two are mutually dependent; probed both orders and both
        # raise ImportError on a partially initialized module, whichever is
        # imported first. Reversing the dependency is not the fix either — that
        # import sits on the rotation hot path and the direction it encodes
        # (`_loop_ledger` -> `loop_tokens` -> `_usage_dedup`) is what keeps this
        # module a small leaf its own callers can import cheaply. Resolving the
        # name at CALL time breaks the cycle without moving anything: by then
        # both modules are fully loaded.
        #
        # DECLARED, because it is a real difference rather than a pure
        # refactor: this import is inside the same guard as the rest of the
        # body, so a `_loop_ledger` that could not be imported would degrade
        # this function to the all-None answer instead of computing the
        # sub-agent half from the ledger. What was checked is that the module
        # imports cleanly on this interpreter and that both capture hooks
        # already import it at their own top level, exiting 0 when it fails —
        # so the degraded arm is not one the observed paths take. That is a
        # statement about what was looked at, not a proof that nothing reaches
        # it; the residual is left visible rather than argued away.
        from _loop_ledger import loop_ledger_paths

        start = _parse_iso(start_iso)
        if start is None:
            return {"orchestrator": None, "subagent": None}
        end = _parse_iso(end_iso) or datetime.now(timezone.utc)
        try:
            orch = sum_usage_in_window(orchestrator_transcripts(cwd, home), start, end)
        except Exception:
            orch = None
        # THE SET, NOT ONE FILE — `_loop_ledger.loop_ledger_paths` states the rule
        # and is not restated here: the writer writes one ref-keyed file, every
        # reader unions the home (the keyed names plus the LEGACY unkeyed one,
        # which holds real rows on every repo that ran a loop before the key
        # changed) and attributes rows with the window this function already
        # applies. This function has no `ref` parameter and needs none — the
        # union is what lets its two callers (`loop_dashboard`, `loop_ledger`)
        # stay unchanged.
        #
        # ONE UNREADABLE FILE COSTS ITS OWN ROWS AND NO OTHERS — declared,
        # because it is a real change of shape rather than a pure move. On one
        # path `read_subagent_ledger` returns None for an OSError exactly as it
        # always did; across the set that None is now SKIPPED rather than
        # returned, so a permission-denied ledger no longer zeroes the whole
        # sub-agent figure. The `None` sentinel is preserved for the case it
        # actually describes: no usage-bearing row in ANY of them.
        sub = None
        for led in loop_ledger_paths(cwd, base, "subagent-tokens.jsonl"):
            try:
                part = read_subagent_ledger(led, start, end)
            except Exception:
                part = None
            if part is None:
                continue
            if sub is None:
                sub = part
            else:
                for field in _FIELDS:
                    sub[field] += part[field]
        return {"orchestrator": orch, "subagent": sub}
    except Exception:
        return {"orchestrator": None, "subagent": None}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Best-effort per-loop token totals (JSON).")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--base", default="")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=None)
    args = ap.parse_args(argv)
    print(json.dumps(loop_tokens(args.cwd or os.getcwd(), args.base, args.start, args.end)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
