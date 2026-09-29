#!/usr/bin/env bash
# capture-subagent-tokens.sh — SubagentStop hook.
#
# When a dispatched sub-agent finishes, Claude Code fires SubagentStop with the
# sub-agent's own transcript path (`agent_transcript_path`). We read that transcript,
# sum its token usage, and append ONE row to the loop's subagent-token ledger
# (`${base_path}/subagent-tokens-<sanitized task_ref>.jsonl`) — so the dashboard can
# show per-loop token stats. Capturing at completion (not at render time) is immune
# to transcript retention/cleanup.
#
# THE LEDGER IS KEYED BY REF, and the reason is a measured data loss rather than
# tidiness: keyed by `base_path` alone, two consecutive loops on the documented
# flat `.fairmind` wrote ONE file, and loop 2's rotation read loop 1's rows as
# out-of-window and rolled them. The name is built by
# `_loop_ledger.loop_ledger_path`; the legacy unkeyed `subagent-tokens.jsonl` is
# still READ by every reader and is never written, migrated or deleted here
# (`_loop_ledger.loop_ledger_paths` states that rule and this hook does not
# restate it).
#
# Best-effort by construction: the transcript schema is a Claude Code internal, so any
# parse failure degrades silently. Like the other Fairmind hooks: fast path first (no
# active workspace → do nothing), and NEVER block (always exit 0).
#
# PL-A0:
#   - PCF-16 liveness gate + window-safe rotation are shared with trace-op.sh via
#     scripts/_loop_ledger.py (resolve_loop_context / append_row), so the two
#     hooks can never drift. EVERY session inside a workspace captures; what the
#     resolver decides is WHERE, under one rule — a capture hook may write into a
#     LOOP's own ledgers only when this session IS that live loop, which is TWO
#     claims, WHICH loop and WHOSE session, and a live loop-state alone answers
#     neither. So a live loop writes its own ledger only when that loop-state's
#     `target.ref` is this context's own `task_ref` AND its `owner_session` is
#     this hook's payload session (or is not yet claimed); a stale/terminal loop
#     DEGRADES to `.fairmind/degraded/` (JC8, and NOT a no-op — that was the
#     defect); and a context sitting over ledgers that are some OTHER loop's
#     routes to `.fairmind/no-loop/`. A context with no loop-state in either
#     ledger home is not routed at all and keeps the ordinary ledgers. It
#     records nothing only outside a workspace. Which shape lands where is the
#     plugin `INTERNALS.md` table (Capture routing), and `resolve_loop_context`
#     implements it. This
#     header names the rule, not the list, because the list has grown every
#     round — so read the destination off one of those two, never off here.
#     ⚠️ check-journal.sh detects the same condition and draws the OPPOSITE
#     conclusion (it stands down). That divergence is deliberate — see the note
#     on `_is_terminal` in scripts/_loop_ledger.py.
#   - PCF-15 dedup: a streamed message repeats its usage per content block under
#     one message.id, so the naive per-line sum over-counts ~2.7x. Dedup is routed
#     through scripts/_usage_dedup.py (the SINGLE source of truth PL-A1's digester
#     also imports) — never re-implemented inline, so the two can never drift.
#   - Every row carries session_id (from stdin) + mode; the active ledger is
#     always capped (~2000 rows) — window-anchored once the loop is armed (a row
#     with ts >= the loop started_at never rolls) and newest-N before arm.
set -uo pipefail

CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
CTX="$CWD/.fairmind/active-context.json"

# Fast path: not a Fairmind session → drain stdin and no-op.
[ -f "$CTX" ] || { cat >/dev/null 2>&1; exit 0; }

# scripts/ dir (holds _usage_dedup.py + _loop_ledger.py) resolved from THIS hook's
# location, so the import works both under the installed plugin and when a test
# invokes the hook directly (CLAUDE_PLUGIN_ROOT is not set in the test env).
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$SELF_DIR/../../scripts"

python3 -c '
import json, os, sys
from datetime import datetime, timezone

cwd = sys.argv[1]
sys.path.insert(0, sys.argv[2])
try:
    from _usage_dedup import deduped_usage_totals
    from _loop_ledger import (resolve_loop_context, append_row, ledger_in,
                              loop_ledger_path)
except Exception:
    sys.exit(0)  # cannot dedup / gate safely -> record nothing (never block)

try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Liveness gate (PCF-16) + mode stamp + ledger home + window boundary, all from
# the shared resolver. JC8: a stale/terminal loop context comes back DEGRADED to
# interactive, ROUTED to a directory of its own rather than to the empty-base
# fallback loop_ledger_path applies below — that fallback is `.fairmind/`, which
# on the documented base_path IS the ledger belonging to the loop that just
# closed.
# (NO APOSTROPHES anywhere from here to the closing quote: this whole block
# lives inside a bash single-quoted string, and one apostrophe ends it. The
# failure is silent and lands far away — it cost a suite run whose only symptom
# was the DEGRADED token ledger coming back empty 90 lines below. trace-op.sh
# carries the same warning; this file did not.)
# JC22: the payload session id goes IN. loop-state.json is repo-global, so every
# session in the checkout resolves the same context; the resolver compares this id
# with the loop-state owner_session (stamped by --arm for the arming session, or,
# when the arm had no session id, by the first Stop gate that drives the loop)
# and routes a foreign session away from the ledgers the owning loop is
# accumulating. An absent id on either side means "cannot tell" and stays live.
lc = resolve_loop_context(cwd, p.get("session_id"))
if not lc.live:
    sys.exit(0)
mode, base, ref, started_at = lc.mode, lc.base, lc.ref, lc.started_at

atp = p.get("agent_transcript_path")
if not atp or not os.path.isfile(atp):
    sys.exit(0)

# Stream the transcript once: dedup consumes the generator, which flags whether
# ANY usage block was seen. This preserves the deliberate "usage present but
# zero" (record a zero row) vs "no usage at all" (record nothing) distinction
# without materializing the whole transcript into a list.
state = {"has_usage": False}
def _records(fh):
    for line in fh:
        try:
            o = json.loads(line)
        except Exception:
            continue
        # A transcript line may be valid JSON but NOT an object (null, []); guard
        # before .get() so it never unwinds and discards the whole capture.
        if not isinstance(o, dict):
            continue
        m = o.get("message")
        if isinstance(m, dict) and isinstance(m.get("usage"), dict):
            state["has_usage"] = True
        yield o

try:
    with open(atp, encoding="utf-8") as fh:
        totals = deduped_usage_totals(_records(fh))
except Exception:
    sys.exit(0)
if not state["has_usage"]:
    sys.exit(0)  # no usage in the transcript -> nothing trustworthy to record

# A ROUTED context names the directory its rows belong in — ONE branch, and the
# resolver owns the decision, so no shape reaching it is spelled out here: that
# list has grown every round, so a copy here would be a second thing to forget.
# It is written out in resolve_loop_context and in the plugin INTERNALS.md
# Capture routing table.
# The other arm is the contexts OWN ledger, and this hook no longer spells that
# join either: loop_ledger_path owns it — the empty-base fallback to
# `.fairmind/`, which is the base_path commands/fairmind-loop.md DOCUMENTS, and
# the ref that names the file inside it. That fallback is why a routed row must
# not reach it — live and routed rows would share ONE ledger home and a routed
# append (no started_at => pure newest-N cap) evicts in-window loop rows: 901 in
# the JC8 reproduction, 502 more when the closed branch rebuilt it, 502 again
# through the interactive door — and it is also why the resolver asks about
# `base or .fairmind` rather than about `base`. Keying by ref does NOT make the
# routing redundant: a routed rows ref is normalized to the same fallback value
# for every routed context, so a directory is still the only thing that
# separates them. See DEGRADED_DIR and NO_LOOP_DIR in _loop_ledger.py.
if lc.ledger_dir:
    led = ledger_in(cwd, lc.ledger_dir, "subagent-tokens.jsonl")
else:
    led = loop_ledger_path(cwd, base, ref, "subagent-tokens.jsonl")
out_dir = os.path.dirname(led)
try:
    os.makedirs(out_dir, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "session_id": p.get("session_id") or "",
        "mode": mode,
        "agent_id": p.get("agent_id"),
        "agent_type": p.get("agent_type"),
        "task_ref": ref,
        # See trace-op.sh — a routed row must say so on the artifact, or it is
        # indistinguishable from a genuinely interactive one. DERIVED by the
        # resolver so both hooks stamp identically; empty for an ordinary
        # context, so no existing row shape moves.
        **lc.row_stamp,
        "in": totals["input_tokens"],
        "out": totals["output_tokens"],
        "cache_creation": totals["cache_creation_input_tokens"],
        "cache_read": totals["cache_read_input_tokens"],
    }
    # Append + window-safe cap/rollover as ONE locked unit (shared, best-effort):
    # a concurrent fire cannot clobber this in-window row, and rotation rolls ONLY
    # rows older than the loop started_at, never a row with ts >= started_at.
    append_row(led, json.dumps(rec), started_at)
except Exception:
    sys.exit(0)
' "$CWD" "$SCRIPTS_DIR" || exit 0

exit 0
