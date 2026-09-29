#!/usr/bin/env bash
# SubagentStop: record that a sub-agent of a given identity finished, while THIS
# session is driving a live loop.
#
# WHY THIS EXISTS. `--record-completeness --by <role>` is otherwise a claim the
# caller types about itself, and the caller -- the orchestrator -- is the party
# that wants the loop to close. maker != checker enforced on a self-reported
# string enforces nothing; this repo has already shipped that defect one layer
# over (a checker hand-editing loop-state.json to overturn admission).
#
# `agent_type` on the SubagentStop payload is the one identity the model does
# not author: the harness sets it, the same field check-journal.sh reads after
# PCF-1 proved CLAUDE_AGENT_NAME is set by nobody.
#
# IDENTITY IS NOT OPTIONAL HERE, and gating on `mode`+`status` alone was not
# enough (PR #105 review, round 3). `active-context.json` and `loop-state.json`
# are REPO-GLOBAL, so every session in the checkout resolves the same context: a
# second, unrelated session with a sub-agent finishing while a loop is armed
# would otherwise mint a spendable attestation for a tree it never reviewed --
# by accident, in an ordinary multi-session workflow. `_loop_ledger.
# resolve_loop_context` is the shared authority every sibling capture hook on
# this event already uses, and its own rule covers this file too: A CAPTURE HOOK
# MAY WRITE INTO A LOOP OWN LEDGERS ONLY WHEN THIS SESSION IS THAT LIVE LOOP.
# `ledger_dir is None` is that answer; anything routed writes NOTHING here,
# rather than writing elsewhere -- a diverted token row is still data, a
# diverted attestation would be evidence about a loop nobody reviewed.
#
# One place this is deliberately STRICTER than the resolver: an absent
# `session_id` makes ownership undecidable and the shared resolver fails OPEN
# (its other callers must never be diverted by a test they cannot answer). An
# attestation is evidence, so it fails CLOSED instead -- the cost is one more
# reviewer dispatch, and nothing is lost.
#
# IT RAISES THE COST; IT IS NOT UNFORGEABLE. The file lands under .fairmind/,
# which the owning session can write. What it buys is an audit artifact and a
# step that has to be deliberately faked rather than merely skipped. Do not
# describe it as more than that.
#
# NEVER BLOCKS. A missing dependency, an unreadable payload or a full disk must
# not stop a sub-agent from finishing: this hook records evidence, it does not
# gate anything. Every failure path exits 0. (check-journal.sh, on the same
# event, deliberately does the opposite -- it enforces a rule, so it fails
# closed. Two hooks, two jobs.)
set -u

PAYLOAD=$(cat 2>/dev/null) || exit 0
CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$SELF_DIR/../../scripts"

# NO APOSTROPHES anywhere inside this block: it is one bash single-quoted string
# and a single apostrophe ends it, silently, with the failure landing far away.
# The same warning is on trace-op.sh and capture-subagent-tokens.sh.
printf '%s' "$PAYLOAD" | python3 -c '
import json, os, re, sys
from datetime import datetime, timezone

cwd = sys.argv[1]
sys.path.insert(0, sys.argv[2])
try:
    from _loop_ledger import resolve_loop_context
except Exception:
    sys.exit(0)  # cannot gate safely -> record nothing (never block)

try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(0)

session_id = p.get("session_id")
if not session_id:
    sys.exit(0)  # ownership undecidable -> no evidence (see the header)

try:
    lc = resolve_loop_context(cwd, session_id)
except Exception:
    sys.exit(0)
# live + mode loop + NOT routed == this session is that live loop. Routed covers
# the stale, terminal, foreign-loop and foreign-session cases in one field, which
# is why the routing table lives in that module and is never restated here.
if not lc.live or lc.mode != "loop" or lc.ledger_dir is not None:
    sys.exit(0)

raw = p.get("agent_type") or ""
if not raw:
    sys.exit(0)
# Same normalization check-journal.sh applies, so one role spells identically in
# an attestation, a journal filename and a check owner/authored_by.
agent = raw.split(":")[-1].lower().replace(" ", "-")
# Both halves reach a FILENAME, so a slash in either would become a path
# segment. sanitize_ref does this for every other ref-to-path in the codebase.
safe = lambda s: re.sub(r"[^A-Za-z0-9._-]", "-", s)
agent = safe(agent)
ref = safe(str(lc.ref or "loop"))
if not agent:
    sys.exit(0)

dest = os.path.join(cwd, lc.base or ".fairmind", "attest")
try:
    os.makedirs(dest, exist_ok=True)
except Exception:
    sys.exit(0)

# A fresh file per sub-agent turn: the engine SPENDS an attestation on one
# verdict and refuses a second use, so the counter must never overwrite.
n = 1
while os.path.exists(os.path.join(dest, ref + "-" + agent + "-" + str(n) + ".json")):
    n += 1
    if n > 9999:
        sys.exit(0)

row = {"agent_type": agent, "agent_type_raw": raw,
       "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "seq": n, "session_id": session_id}
try:
    with open(os.path.join(dest, ref + "-" + agent + "-" + str(n) + ".json"), "w") as fh:
        json.dump(row, fh)
except Exception:
    pass
sys.exit(0)
' "$CWD" "$SCRIPTS_DIR" 2>/dev/null

exit 0
