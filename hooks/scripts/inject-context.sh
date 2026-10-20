#!/usr/bin/env bash
# inject-context.sh — hand a dispatched sub-agent the active Fairmind context
# (FAIRMIND_BASE, project_id, session_mindstreamId, plus any human steering
# from ${base_path}/steering.md — see fairmind-gate/references/steering.md).
#
# Registered twice, one job per event:
#
#   SubagentStart (`deliver`, the default) — DELIVERS. Emits
#     hookSpecificOutput.additionalContext, which Claude Code adds to the new
#     sub-agent's own context. Measured on Claude Code 2.1.280: a random token
#     emitted this way was quoted back verbatim by the dispatched sub-agent.
#     Plain stdout would not do: on most events, PreToolUse and SubagentStart
#     included, it goes to the debug log and reaches no model.
#
#   PreToolUse on Task (`check`) — REFUSES, and does nothing else. SubagentStart
#     cannot block, so the refusal lives here: inside an active workspace, a
#     missing jq or an unparseable active-context.json means the context cannot
#     be delivered, and a sub-agent that was never told where FAIRMIND_BASE is
#     writes outside the scoped path. This mode never writes to stdout and never
#     touches the tool input or the permission decision: it narrows what may
#     run, it grants nothing.
#
# Exit-code contract: PreToolUse blocks on exit 2 and ONLY on exit 2, so this
# script does not use `set -e` (a 127 or a jq 5 would abort with a non-blocking
# code). In deliver mode a failure is reported (exit 1) but cannot stop the
# sub-agent; the check that ran before the dispatch is what does.
#
# The two modes read active-context.json separately, so a passed check does not
# guarantee delivery: a file that changes or disappears between the dispatch and
# the start, or a delivery that times out, starts the sub-agent without the
# context. A sub-agent started by anything other than a PreToolUse-visible
# dispatch gets the delivery and no check.
set -uo pipefail

MODE="${1:-deliver}"
case "$MODE" in
  check|deliver) ;;
  *) echo "fairmind context injection: unknown mode '$MODE' (expected check or deliver)" >&2; exit 1 ;;
esac

cat >/dev/null 2>&1   # stdin is unused; drained like the sibling hooks
CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
CONTEXT_FILE="$CWD/.fairmind/active-context.json"

# No context file → silent exit (the Technical Lead hasn't bootstrapped yet, or
# this is not a FairMind project)
[ -f "$CONTEXT_FILE" ] || exit 0

# A failure the check mode turns into a refusal and the deliver mode can only report.
fail() {
  if [ "$MODE" = check ]; then
    echo "fairmind context injection: $1; dispatching a sub-agent that does not know the scoped path is refused." >&2
    exit 2
  fi
  echo "fairmind context injection: $1; this sub-agent starts without it." >&2
  exit 1
}

if ! command -v jq >/dev/null 2>&1; then
  fail "jq is not on PATH, so FAIRMIND_BASE cannot be read from $CONTEXT_FILE. Install jq (brew install jq) or remove the fairmind-coding hooks"
fi

if ! CONTEXT_LINE=$(jq -r '"FairMind active context: FAIRMIND_BASE=\(.base_path), project_id=\(.project_id), session_mindstreamId=\(.session_mindstreamId)"' "$CONTEXT_FILE" 2>/dev/null); then
  fail "$CONTEXT_FILE is not valid JSON, so FAIRMIND_BASE cannot be read. Fix active-context.json before dispatching a sub-agent"
fi

[ "$MODE" = check ] && exit 0

INJECT="$CONTEXT_LINE"

# Best-effort steering fold-in: any failure here (base_path absent/null, the
# path not existing, an empty file, a read error) is a silent skip. The file is
# optional and human-only, so its absence is the default healthy state, and this
# block must never turn an otherwise-successful delivery into a failure.
BASE_PATH=$(jq -r '.base_path // empty' "$CONTEXT_FILE" 2>/dev/null)
if [ -n "$BASE_PATH" ]; then
  STEERING_FILE="$CWD/$BASE_PATH/steering.md"
  if [ -s "$STEERING_FILE" ] 2>/dev/null; then
    INJECT="$INJECT"$'\n'"--- Human steering ($STEERING_FILE) — read before acting ---"$'\n'"$(cat "$STEERING_FILE" 2>/dev/null)"
  fi
fi

jq -nc --arg ctx "$INJECT" \
  '{hookSpecificOutput: {hookEventName: "SubagentStart", additionalContext: $ctx}}' \
  || fail "the context could not be encoded for delivery"
