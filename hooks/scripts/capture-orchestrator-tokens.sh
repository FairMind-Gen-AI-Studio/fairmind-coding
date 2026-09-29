#!/usr/bin/env bash
# capture-orchestrator-tokens.sh — Stop hook.
#
# The sibling SubagentStop hook (capture-subagent-tokens.sh) captures every
# DISPATCHED agent's tokens. Nobody captured the orchestrator's own: the main
# thread never fires SubagentStop, so the token ledger held sub-agents
# only and the loop payload's `agents[]` had no entry for the thread that did
# the dispatching. This hook closes that gap — Stop carries `transcript_path`,
# which is the MAIN session's transcript (SubagentStop's `agent_transcript_path`
# is the sub-agent's; the two are different fields, deliberately).
#
# WHAT IT APPENDS, AND WHY THAT SHAPE
# ===================================
# One row per Stop, `agent_type: "main"`, into the SAME
# `${base_path}/subagent-tokens-<sanitized task_ref>.jsonl` the sub-agent hook
# writes — carrying the
# tokens spent SINCE THE PREVIOUS STOP (a DELTA), never a running total.
# (The ledger is keyed by REF; `_loop_ledger.loop_ledger_path` builds the name
# and `loop_ledger_paths` is the rule every reader unions it back with.)
#
# The delta is the whole design, so it is worth being explicit about the trap it
# avoids. The main transcript is CUMULATIVE — one append-only file per session —
# and Stop fires at every turn end (plus extra times while `stop_hook_active`,
# since loop-check.sh returns 2 to iterate). Appending a cumulative SNAPSHOT per
# Stop and letting the flush sum them multiplies the orchestrator's tokens: for
# N snapshots of a monotonically growing counter, the sum is avg*N(N+1)/2
# against a true total of avg*N, i.e. an over-count of (N+1)/2. Measured over
# the 33 real loop-states on disk, N ranges 1..12 (median 5-6) — so ~3.5x at the
# median and ~7.5x worst case. Deltas are ADDITIVE, so
# `insights_flush_payload._loop_agents` — whose only arithmetic is `tot[k] += v`
# — consumes them with no special-casing at all, and a second copy of the
# summing rule (the thing that drifts) is never written.
#
# The delta is derived FROM THE FILE via a byte watermark, never as arithmetic
# on a stored previous total. That distinction is what makes it self-healing: a
# missed Stop (a user interrupt fires none), a transcript still lagging the
# in-memory conversation, a rotated ledger — all of them simply leave bytes
# unread, and the NEXT Stop picks them up. A stored-total subtraction would
# corrupt its base and every delta after it.
#
# Four narrower guards, each load-bearing:
#   - `last_ids`: a streamed message repeats its `usage` block once per content
#     block under one `message.id`, and those repeats can straddle the watermark
#     — in-slice dedup cannot see a copy counted in the PREVIOUS slice. The tail
#     of each slice's ids is carried forward and suppressed. The rule itself is
#     IMPORTED (`_usage_dedup.dedup_key`), never restated here.
#   - `timestamp >= started_at`: the first slice after `--arm` contains
#     everything since SESSION start, which routinely predates the loop (a
#     session opens, work happens, then the loop arms). Unfiltered, that pre-loop
#     prefix would land inside the loop's window. Every usage-bearing line
#     carries a `timestamp` (10,826/10,826 measured over 45 real transcripts).
#   - `isSidechain is not True`: measured today, sub-agent turns are NOT in the
#     main transcript (0 records with isSidechain True out of 43,135) — they live
#     in `<session>/subagents/agent-*.jsonl`, which is why this does not
#     double-count the SubagentStop hook. That is a Claude Code version detail,
#     not a contract. If a future version inlines them, this one line is what
#     stops the `main` row from swallowing every sub-agent's tokens.
#   - watermark BEFORE row: a crash between the two loses a delta (under-count)
#     instead of re-reading and double-counting it (over-count). An absent
#     orchestrator row is honest; an inflated one becomes a fleet answer.
#
# The watermark is ONE FILE PER SESSION (`_loop_ledger.orchestrator_watermark_
# path`), not one shared file keyed by session inside. Concurrent sessions in
# one repo are routine, this read-modify-write is not locked, and a shared file
# would let one session write back another's STALE entry — rewinding its offset
# and re-counting a slice whose ids are no longer suppressed. Splitting the file
# removes that race rather than narrowing it, and bounds the growth a
# never-pruned shared dict would have had.
#
# DIVERGENCE FROM capture-subagent-tokens.sh: loop mode ONLY
# ==========================================================
# The sibling captures in interactive sessions too. This one requires
# `mode == "loop"` AND an armed loop (`started_at` present). Not an oversight —
# do not "fix" the asymmetry:
#   - pre-arm/interactive rotation in `_loop_ledger._roll_window` is a pure
#     newest-N cap with NO window protection, so a per-turn-end row in every
#     interactive session would grow the repo-root ledger without bound and
#     could EVICT sub-agent rows. In loop mode rotation is window-anchored
#     (ts >= started_at never rolls), so an orchestrator row cannot evict one.
#   - without `started_at` there is no boundary to bound the delta against, and
#     `_loop_agents` would drop the row anyway (it windows on a parseable
#     [started_at, closed_at]). Capturing it would be pure noise.
#
# The same narrowness now covers the resolver's IDENTITY tests, and it is why
# this hook still has no routing branch of its own. `resolve_loop_context` routes
# a live loop that is another loop's (its `target.ref` is not this context's
# `task_ref`) or another session's (its `owner_session` is not this payload's),
# and every routed context is normalized to `mode: "interactive"` — so the
# `lc.mode != "loop"` test below drops it. DROPPED, not routed, deliberately: a
# `main` row records a loop TURN, and a session that is not the loop had no loop
# turn to record. Do not "fix" that asymmetry into a `no-loop/` write.
#
# Best-effort by construction: the transcript schema is a Claude Code internal,
# so any parse failure degrades silently. Like every Fairmind hook: fast path
# first (no active workspace -> do nothing) and NEVER block — always exit 0.
# `exit 2` from a Stop hook BLOCKS the stop; this hook must never do that.
#
# Note it runs in PARALLEL with loop-check.sh (hooks in one array always do), so
# it must not depend on whether the gate has already written a terminal status.
# The consequence is stated rather than papered over: on the Stop that CLOSES a
# loop, whether this hook sees the pre- or post-terminal status is a race, so the
# loop's final turn may be missing. Missing, never doubled.
set -uo pipefail

CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
CTX="$CWD/.fairmind/active-context.json"

# Fast path: not a Fairmind session → drain stdin and no-op.
[ -f "$CTX" ] || { cat >/dev/null 2>&1; exit 0; }

# scripts/ dir (holds _usage_dedup.py + _loop_ledger.py + loop_tokens.py)
# resolved from THIS hook's location, so the import works both under the
# installed plugin and when a test invokes the hook directly
# (CLAUDE_PLUGIN_ROOT is not set in the test env).
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$SELF_DIR/../../scripts"

python3 -c '
import json, os, sys, tempfile
from datetime import datetime, timezone

cwd = sys.argv[1]
sys.path.insert(0, sys.argv[2])
try:
    from _usage_dedup import deduped_usage_totals, dedup_key
    from _loop_ledger import (resolve_loop_context, append_row,
                              orchestrator_watermark_path, makedirs_ignored,
                              loop_ledger_path)
    from loop_tokens import _parse_iso, ORCHESTRATOR_AGENT_TYPE
except Exception:
    sys.exit(0)  # cannot dedup / gate / attribute safely -> record nothing

try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Liveness gate (PCF-16) + mode stamp + ledger home + window boundary, from the
# SHARED resolver, exactly as the SubagentStop hook does. The two extra
# conditions are the deliberate divergence documented in the header: loop mode
# only, and only once the loop is ARMED — an unparseable/absent started_at
# leaves the delta unbounded, so there is nothing safe to record.
# JC8 changes nothing here BY CONSTRUCTION: a stale/terminal loop context now
# resolves to mode "interactive" with no started_at, so both conditions fail and
# this hook keeps no-opping on it exactly as it did before. The SAME holds for
# the third active-context mode, "closed" (a loop ran here and finished, stamped
# by run_gate_checks at the terminal status) — but it holds only BECAUSE
# resolve_loop_context normalizes that value to "interactive" too, rather than
# returning it verbatim. Left to the generic non-loop branch it would come back
# as mode "closed" with the closed loop own base_path and task_ref, and while
# the `lc.mode != "loop"` test below would still no-op, the reason would have
# become an accident of spelling rather than the construction this comment
# claims. Named explicitly so the claim stays checkable.
# JC22: the payload session id goes IN, and here it is LOAD-BEARING rather than a
# consistency gesture. The gate own session guard (run_gate_checks.py:4606-4619)
# protects the GATE, not this hook: a foreign session Stop still fired this hook,
# and this hook joins its output straight to base_path (out_dir below) and never
# reads lc.ledger_dir, so a second session in the same checkout appended an
# agent_type main row into the owning loop own ledger, unrouted and unwindowed.
# With the id passed, that context comes back routed and therefore normalized to
# mode interactive, and the test one line down drops it — which is why this hook
# still needs no routing branch. The dropped row is the documented narrowness
# (see the DIVERGENCE note in the header), not a lost destination: a main row
# belongs to a loop turn, and a foreign session had no loop turn to record.
# JC20 rides the SAME line and is worth naming, because the header would
# otherwise read as if only a foreign session were dropped: a context whose
# task_ref differs from the live loop-state target.ref is routed by the same
# resolver, normalized the same way, and dropped by the same test — no session id
# required, since that comparison needs none.
lc = resolve_loop_context(cwd, p.get("session_id"))
if not lc.live or lc.mode != "loop" or not lc.started_at:
    sys.exit(0)
started = _parse_iso(lc.started_at)
if started is None:
    sys.exit(0)
mode, base, ref = lc.mode, lc.base, lc.ref

tp = p.get("transcript_path")
if not tp or not isinstance(tp, str):
    sys.exit(0)
# The docs render this path tilde-prefixed (hooks.md:2190, the Stop example
# itself). Real payloads have been absolute — the sibling hook has produced 645
# rows with a bare isfile() — but an unexpanded "~" would fail isfile and make
# this hook no-op SILENTLY and permanently, which is the one failure mode that
# looks identical to "the feature is off". One call removes the possibility.
tp = os.path.expanduser(tp)
if not os.path.isfile(tp):
    sys.exit(0)

# DELIBERATELY NOT loop_ledger_path: `out_dir` is the WATERMARK home, not the
# ledger home, and the two are separate artifacts that happen to share a
# directory today. The watermark is this sessions read offset into a transcript
# — it belongs beside base_path whatever the ledger is keyed by — so this join
# stays spelled here while the ledger join below goes through the constructor.
# It also has to raise FIRST on a base that cannot be joined: this hook writes
# the watermark before the row (see the ordering note below), so a failure that
# surfaced only at the ledger join would advance the watermark and then die,
# losing the delta with no row to show for it.
out_dir = os.path.join(cwd, base) if base else os.path.join(cwd, ".fairmind")
session_id = p.get("session_id") or ""

def _write_watermark(wm_path, out_dir, tp, offset, last_ids):
    """Atomically record where this session has read up to. True on success.

    Written via mkstemp + `os.replace` so a crash mid-write leaves the PREVIOUS
    watermark intact rather than a half-file: a corrupt watermark parses as
    `{}`, which means offset 0, which means a rescan — the exact over-count
    this hook exists to avoid.

    Two callers, and the shape is shared on purpose: the normal end-of-slice
    commit, and the shrink branch, which records the new end and accounts for
    nothing. A second inlined copy is how the two would drift on the next
    change to the shape of that record.
    """
    try:
        # PCF-28: the watermark can be the FIRST thing this hook writes into a
        # consumer repo — the shrink branch records a new offset and no ledger
        # row at all — so the ignore entry is owed here, not only at append_row.
        makedirs_ignored(out_dir)
        fd, tmp = tempfile.mkstemp(prefix=".orch-watermark.", suffix=".tmp", dir=out_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"path": tp, "offset": offset, "last_ids": last_ids}, fh)
            os.replace(tmp, wm_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except Exception:
        return False
    return True


# ---- watermark: {path, offset, last_ids}, ONE FILE PER SESSION ------------
# Per session because a loop can span two sessions, each with its own
# transcript and its own byte offset — and per FILE rather than one shared
# dict because this read-modify-write is not locked; see
# `orchestrator_watermark_path` for why a shared file would let one session
# rewind the offset of another and re-count a slice.
wm_path = orchestrator_watermark_path(out_dir, session_id)
entry = {}
try:
    with open(wm_path, encoding="utf-8") as fh:
        entry = json.load(fh)
except Exception:
    entry = {}
if not isinstance(entry, dict):
    entry = {}

offset = entry.get("offset")
if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
    offset = 0
if entry.get("path") != tp:
    offset = 0  # a different transcript under this session id -> rescan whole
last_ids = entry.get("last_ids")
last_ids = {x for x in last_ids if isinstance(x, str)} if isinstance(last_ids, list) else set()

try:
    size = os.path.getsize(tp)
except OSError:
    sys.exit(0)
if size < offset:
    # The transcript SHRANK under the same path within one session: compacted,
    # truncated, or rewritten. Resume from the new end and account for NOTHING
    # this round.
    #
    # An earlier revision rescanned from 0 here, on the reasoning that "the
    # timestamp filter and dedup keep that honest". They do not, and the flush
    # over-counted as a result. The timestamp filter only drops rows older than
    # the `started_at` of the loop — it cannot know a row was already counted —
    # the only cross-slice dedup is `last_ids`, which THIS branch clears. The
    # ledger is append-only, so a rescan emits a second row covering messages a
    # previous row already accounted for. Reproduced: two turns (100 + 200
    # tokens) counted as one row of 300; the transcript rewritten to keep only
    # the first turn; the rescan appended a second row of 100, so the flush
    # summed 400 for 300 tokens genuinely spent.
    #
    # Resuming at `size` is not merely the safe direction, it is the correct
    # one for the realistic cause: a compaction keeps a SUFFIX of what was
    # there, and every byte of that suffix is content this session already
    # counted. What it gives up is the case where a shrink also brought
    # genuinely new content — indistinguishable from a compaction without a
    # file-identity fingerprint, and the same trade the watermark-before-append
    # ordering already makes: this hook prefers to UNDER-count, because a
    # missing figure is visibly missing while an inflated one becomes a fleet
    # answer.
    offset, last_ids = size, set()
    _write_watermark(wm_path, out_dir, tp, offset, [])
    sys.exit(0)
if size == offset:
    sys.exit(0)  # no new bytes (e.g. a stop_hook_active re-fire) -> no row

# ---- read ONLY the new bytes, and only COMPLETE lines ---------------------
# Binary + explicit slice, not line iteration: TextIOWrapper.tell() raises
# "telling position disabled by next() call" once the file has been iterated,
# so the offset could not be read back. Truncating at the last newline leaves a
# partially-written trailing line (the transcript is written asynchronously and
# may lag) unconsumed, for the next Stop to pick up whole.
try:
    with open(tp, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
except OSError:
    sys.exit(0)
nl = data.rfind(b"\n")
if nl < 0:
    sys.exit(0)  # no complete line in the slice -> advance nothing
new_offset = offset + nl + 1
text = data[:nl + 1].decode("utf-8", "replace")

slice_ids = []
state = {"has_usage": False, "ts_hi": None, "ts_hi_raw": None}


def _records(lines):
    """Yield the records of this slice that are THIS loop and NOT already counted.

    Single pass: it also collects the ids seen (for the straddle carry) and the
    highest timestamp actually counted (for the row audit trail). Anything
    filtered out here — a sub-agent turn, a pre-arm turn, a straddle repeat —
    contributes to neither.
    """
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not isinstance(o, dict):
            continue
        if o.get("isSidechain") is True:
            continue  # a sub-agent turn: capture-subagent-tokens.sh owns it
        raw = o.get("timestamp")
        ts = _parse_iso(raw)
        if ts is not None and ts < started:
            continue  # spent before this loop armed -> not a cost of this loop
        key = dedup_key(o)
        if key is not None:
            slice_ids.append(key)
            if key in last_ids:
                continue  # already counted in the previous slice (straddle)
        m = o.get("message")
        if isinstance(m, dict) and isinstance(m.get("usage"), dict):
            state["has_usage"] = True
            if ts is not None and (state["ts_hi"] is None or ts > state["ts_hi"]):
                state["ts_hi"], state["ts_hi_raw"] = ts, str(raw)
        yield o


try:
    totals = deduped_usage_totals(_records(text.split("\n")))
except Exception:
    sys.exit(0)

# ---- persist the watermark FIRST, then the row ----------------------------
# Order matters and is the conservative one: if the process dies between the
# two, the delta is LOST (under-count) rather than re-read on the next Stop and
# counted twice (over-count). A failed watermark write records nothing at all —
# the next Stop simply re-reads the same slice and folds it into one row.
carry = []
for key in reversed(slice_ids):
    if key not in carry:
        carry.append(key)
    if len(carry) >= 32:
        break
if not _write_watermark(wm_path, out_dir, tp, new_offset, list(reversed(carry))):
    sys.exit(0)

if not state["has_usage"]:
    sys.exit(0)  # bytes advanced but no usage in them -> nothing to record

# The SAME constructor the SubagentStop hook uses for the ledger it shares with
# this one — the two used to spell this join differently, and a shared file with
# two authors is a file that drifts. It is NO LONGER join(out_dir, name): the
# ledger is keyed by REF now, so out_dir names only the home the two artifacts
# share and the filename comes from lc.ref. This hook only ever reaches this
# line on the identity-matched live-loop row (mode loop AND armed), so the ref
# it passes is the loop own target ref, which is the key the readers union over.
led = loop_ledger_path(cwd, base, ref, "subagent-tokens.jsonl")
try:
    rec = {
        # `ts` is the capture instant, which is what _loop_agents windows on
        # (identical to the sibling hook). `ts_hi` is the newest transcript
        # timestamp this delta actually counted — the audit trail for WHICH
        # turns it covers, which the capture instant alone cannot say.
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "ts_hi": state["ts_hi_raw"],
        "session_id": session_id,
        "mode": mode,
        "agent_id": None,
        "agent_type": ORCHESTRATOR_AGENT_TYPE,
        "task_ref": ref,
        "in": totals["input_tokens"],
        "out": totals["output_tokens"],
        "cache_creation": totals["cache_creation_input_tokens"],
        "cache_read": totals["cache_read_input_tokens"],
    }
    # Same locked append+rotate unit the sibling hook uses: a concurrent fire
    # cannot clobber this in-window row, and rotation rolls ONLY rows older than
    # the loop started_at.
    append_row(led, json.dumps(rec), lc.started_at)
except Exception:
    sys.exit(0)
' "$CWD" "$SCRIPTS_DIR" || exit 0

exit 0
