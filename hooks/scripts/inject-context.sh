#!/usr/bin/env bash
# inject-context.sh — PreToolUse on Task: inject FAIRMIND_BASE (+ any human
# steering) INTO the dispatched sub-agent's own prompt via hookSpecificOutput.
# updatedInput, because that is the only mechanism that actually reaches it.
#
# Why not plain stdout: per the Claude Code hooks docs, a PreToolUse hook's
# plain stdout on exit 0 is written to the debug log ONLY — it is never added
# to any model's context. The sole events whose stdout becomes context are
# UserPromptSubmit, UserPromptExpansion, and SessionStart; PreToolUse is not
# among them. A dispatched Task sub-agent's context is built solely from the
# Task tool's `prompt` argument plus its own agent-definition file — nothing
# this hook prints to stdout is visible to it. (I5: this hook previously
# `printf`d the context line, on the mistaken assumption that PreToolUse
# stdout was injected like a slash-command's; it was a silent no-op for the
# sub-agent the whole time.)
#
# So instead of printing, this hook rewrites the Task's own input before it
# runs: it emits `hookSpecificOutput.updatedInput` with the FAIRMIND context
# (and steering, if present) prepended to `tool_input.prompt`, preserving every
# other tool_input field untouched. That rewritten prompt is what the
# sub-agent actually receives, so the injection is mechanical, not aspirational.
#
# Exit-code contract: PreToolUse blocks on exit 2 and ONLY on exit 2. Under
# `set -e` a missing jq (127) or an unparseable active-context.json (jq exits 5)
# aborts with a non-blocking code — the Task is dispatched anyway, and a sub-agent
# that was never told where FAIRMIND_BASE is writes outside the scoped path. So
# inside an active workspace an unusable dependency or input is a refusal (exit 2).
#
# Outside a Fairmind workspace (no active-context.json) there is nothing to inject
# and the hook stays a silent no-op.
#
# Steering channel (I5, see fairmind-gate/references/steering.md): a human may
# hand-author ${base_path}/steering.md to redirect the dispatched sub-agent. This
# hook folds its content into the rewritten prompt right after the context line,
# via the same updatedInput mechanism, so "the maker reads steering.md" is backed
# by the dispatch itself carrying the text — not merely documented. A missing or
# empty steering.md is a SILENT SKIP — never an error, never exit 2 — the same
# fail-open the rest of this hook reserves for "nothing to inject": steering.md is
# optional, human-only, and never emitted by any script, so its absence is the
# default healthy state.
#
# A malformed or unparseable Task payload on stdin never blocks the dispatch
# either: this hook emits no updatedInput and exits 0 rather than risk feeding
# Claude Code invalid JSON or wedging a Task call it cannot itself repair.
set -uo pipefail

# Drain stdin defensively: a stripped PATH (the "no workspace, no dependency"
# fixture) means `cat` itself may not resolve, and that failure must not leak
# onto stderr and break the silent-no-op contract for a non-Fairmind repo.
PAYLOAD=$(cat 2>/dev/null || true)
CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
CONTEXT_FILE="$CWD/.fairmind/active-context.json"

# No context file → silent exit (the Technical Lead hasn't bootstrapped yet, or
# this is not a FairMind project)
[ -f "$CONTEXT_FILE" ] || exit 0

if ! command -v jq >/dev/null 2>&1; then
  echo "fairmind context injection: jq is not on PATH, so FAIRMIND_BASE cannot be read from $CONTEXT_FILE. Install jq (brew install jq) or remove the fairmind-coding hooks; dispatching a sub-agent that does not know the scoped path is refused." >&2
  exit 2
fi

if ! CONTEXT_LINE=$(jq -r '"FairMind active context: FAIRMIND_BASE=\(.base_path), project_id=\(.project_id), session_mindstreamId=\(.session_mindstreamId)"' "$CONTEXT_FILE" 2>/dev/null); then
  echo "fairmind context injection: $CONTEXT_FILE is not valid JSON, so FAIRMIND_BASE cannot be read. Fix active-context.json before dispatching a sub-agent." >&2
  exit 2
fi

INJECT="$CONTEXT_LINE"

# Best-effort steering fold-in: any failure here (base_path absent/null, the
# path not existing, an empty file, a read error) is a silent skip. This block
# must never turn an otherwise-successful context injection into a refusal.
BASE_PATH=$(jq -r '.base_path // empty' "$CONTEXT_FILE" 2>/dev/null)
if [ -n "$BASE_PATH" ]; then
  STEERING_FILE="$CWD/$BASE_PATH/steering.md"
  if [ -s "$STEERING_FILE" ] 2>/dev/null; then
    INJECT="$INJECT"$'\n'"--- Human steering ($STEERING_FILE) — read before acting ---"$'\n'"$(cat "$STEERING_FILE" 2>/dev/null)"
  fi
fi

# Rewrite the Task prompt: prepend INJECT, PRESERVE the original prompt and
# every other tool_input field via the `.tool_input + {...}` merge. If the
# payload can't be parsed as the expected shape, emit nothing and fall through
# to exit 0 — never block the dispatch on a malformed payload, never emit
# invalid JSON.
if OUT=$(printf '%s' "$PAYLOAD" | jq -c --arg inject "$INJECT" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", updatedInput: (.tool_input + {prompt: ($inject + "\n\n" + (.tool_input.prompt // ""))})}}' \
    2>/dev/null); then
  printf '%s\n' "$OUT"
fi

exit 0
