#!/usr/bin/env bash
# check-journal.sh — SubagentStop hook: refuse to let a sub-agent finish when it
# changed code in a Fairmind workspace and wrote no journal.
#
# WHY SubagentStop, not Stop (PCF-1): the harness fires `Stop` ONLY for the main
# agent; a Task/Agent sub-agent finishing fires `SubagentStop`. This hook used to
# be wired to `Stop` and gate on `CLAUDE_AGENT_NAME` matching a role slug — but
# `Stop` never sees a sub-agent, and `CLAUDE_AGENT_NAME` is not a variable the
# harness sets (verified against the hooks + env-vars docs), so the check
# enforced for NOBODY. It now fires on `SubagentStop` and takes the sub-agent's
# identity from the JSON payload's `agent_type`, not an env var. `SubagentStop`
# blocks on exit 2 exactly like `Stop`.
#
# Scope: enforces for ANY sub-agent that mutated non-.fairmind code — not a fixed
# role allowlist. The old four-slug allowlist could never match the payload's
# plugin-scoped display name (`fairmind-coding:QA Engineer`) anyway, and for an
# audit-trail guarantee "any code-mutating sub-agent must journal" is the safe,
# resilient rule. Read-only sub-agents mutate nothing, so the code-change check
# exempts them; the main orchestrator never reaches this event at all; and a
# loop-mode session enforces only while its loop is LIVE (PCF-11, below).
#
# Exit-code contract: a SubagentStop hook blocks on exit 2 and ONLY on exit 2;
# any other non-zero exit is reported and the sub-agent finishes anyway. So
# `set -e` is actively dangerous here — an abort (missing jq → 127, unparseable
# context → 5, `find` on a journals/ directory that does not exist yet → 1) lets
# the sub-agent finish clean in exactly the state this hook exists to block. So:
# no `set -e`; a missing dependency or an unreadable active-context.json is a
# refusal (exit 2), and a missing journals/ directory is simply "no journal was
# written" and must flow into the normal check rather than abort it.
#
# Outside a Fairmind workspace (no active-context.json) the hook stays a silent
# no-op — the plugin can be installed in a repo that never opens a session, and
# that no-op must hold even with no dependencies on PATH.
set -uo pipefail

# Read the SubagentStop payload now (before any dependency check), so the
# no-workspace path below stays a pure no-op even with an empty PATH.
PAYLOAD=$(cat 2>/dev/null || true)

# CWD from the environment (no jq needed — keeps the no-workspace/no-jq case a
# silent no-op). CLAUDE_PROJECT_DIR is the hook's project root.
CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
CONTEXT_FILE="$CWD/.fairmind/active-context.json"

# No active context → nothing to enforce.
if [ ! -f "$CONTEXT_FILE" ]; then
  exit 0
fi

# From here on a Fairmind session is active, so every input the rule needs must
# be there. Missing tools are not "the rule passes", they are "the rule cannot
# run" → refuse (exit 2), never skip.
MISSING=""
for dep in jq find git sed grep head tr; do
  command -v "$dep" >/dev/null 2>&1 || MISSING="$MISSING $dep"
done
if [ -n "$MISSING" ]; then
  echo "Journal check cannot run: missing command(s) on PATH:$MISSING. Install them (e.g. brew install jq) or remove the fairmind-coding hooks. Refusing to let the sub-agent finish rather than skipping the journal rule." >&2
  exit 2
fi

# Sub-agent identity from the payload's agent_type (NOT CLAUDE_AGENT_NAME, which
# the harness does not set). Derive a journal-filename-safe label: drop a plugin
# scope prefix ("fairmind-coding:"), lowercase, spaces → hyphens. Absent → a
# generic label; identity drives the label, never the enforcement decision.
AGENT_TYPE=$(printf '%s' "$PAYLOAD" | jq -r '.agent_type // empty' 2>/dev/null) || AGENT_TYPE=""
AGENT=$(printf '%s' "$AGENT_TYPE" | sed 's/^[^:]*://' | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
[ -z "$AGENT" ] && AGENT="subagent"

BASE_PATH=$(jq -r '.base_path // empty' "$CONTEXT_FILE" 2>/dev/null) || BASE_PATH=""
if [ -z "$BASE_PATH" ]; then
  echo "Journal check cannot run: $CONTEXT_FILE has no readable base_path (invalid JSON or missing key). Fix active-context.json — it must carry base_path pointing at .fairmind/<project-slug>/<session-slug>." >&2
  exit 2
fi

# PCF-11: a loop-mode session enforces only while its loop is LIVE. A stale
# active-context left pointing at a CLOSED/merged loop — its loop-state.json gone
# or in a terminal state — must not arm the journal check on every unrelated
# sub-agent. That is the exact false-fire PCF-1's now-working enforcement first
# produced (a fork writing a doc, blocked against a dead loop's workspace). This
# mirrors loop-check.sh, which already no-ops on "no loop-state, or a terminal
# state". Interactive sessions carry no liveness marker, so they are unchanged:
# active-context existence remains their signal.
MODE=$(jq -r '.mode // "interactive"' "$CONTEXT_FILE" 2>/dev/null) || MODE="interactive"
# `closed` is the THIRD mode value: a loop ran here and FINISHED. run_gate_checks
# writes it at the terminal status, which is what nothing did before — the marker
# used to keep claiming `mode: loop` over a dead loop for ever. The stand-down is
# mandatory rather than nice-to-have: `closed` would otherwise fall through to the
# rule below and journal-gate every code-mutating sub-agent against the DEAD
# loop's journals/, blocking it on exit 2. That is the exact PCF-11 false fire
# (see the note below), resurrected by a value PCF-11 could not have known about.
#
# So JOURNAL BEHAVIOUR IS UNCHANGED BY THE NEW VALUE: it stood down before, over
# a stale `mode: loop` marker whose loop-state was terminal, and it stands down
# after, over the `closed` marker that now replaces it. What changed is only
# which branch reaches the same answer.
#
# Hand-mirrored from `_loop_ledger.CONTEXT_MODE_CLOSED` (bash cannot import it),
# exactly as the terminal-status set below is hand-mirrored — keep both in step.
if [ "$MODE" = "closed" ]; then
  exit 0
fi
if [ "$MODE" = "loop" ]; then
  LOOP_STATE="$CWD/$BASE_PATH/loop-state.json"
  # No loop-state at the pointed base_path → the loop never armed or is long gone.
  [ -f "$LOOP_STATE" ] || exit 0
  STATUS=$(jq -r '.status // empty' "$LOOP_STATE" 2>/dev/null) || STATUS=""
  # Terminal states (passed_pending_human / blocked_*) mean the loop is done
  # iterating; nothing to journal-gate. Any other (running/specified/…) is live.
  #
  # PINNED, at last, by tests/test_liveness_rule_parity.py. This glob is a
  # hand-mirrored copy of the engine's named vocabulary
  # (`run_gate_checks.TERMINAL_STATUSES`) and bash cannot import it, so until
  # that test existed a status the engine named and this glob did not match
  # turned nothing red anywhere. The test DERIVES its matrix from that constant and
  # drives this hook for real, asserting exit 0 per member — and asserts the
  # OPPOSITE consequence on `_loop_ledger.resolve_loop_context` for the same
  # member, because the two are deliberately not reconciled (that module's
  # `_TERMINAL_EXACT` comment says why). A new `blocked_`-prefixed status is
  # picked up free by the glob; one outside that prefix needs an edit here, and
  # the test is what will say so.
  case "$STATUS" in
    passed_pending_human|blocked_*) exit 0 ;;
  esac
fi

# Check for a recently written journal (last 30 min). ANY .md in the dedicated
# journals/ directory counts as a journal — the directory holds nothing else, and
# validate-fairmind-path.sh admits any write under the base_path
# active-context.json declares — journals/ included. Filtering on a
# `*_journal.md` suffix only added a footgun: a validly-named journal whose name
# did not end in `_journal.md` (e.g. `qa-i1-checks.md`) went unseen, so the hook
# demanded a journal that had in fact been written and a duplicate had to be
# added (I1 internal finding F1). A journals/ directory that does not exist means
# no journal was written: `find` fails, RECENT stays empty, and the code-change
# check below decides — it must never end the script.
RECENT=$(find "$CWD/$BASE_PATH/journals" -name "*.md" -mmin -30 2>/dev/null | head -1)
if [ -n "$RECENT" ]; then
  exit 0
fi

# Check if code was actually written — any change to a non-.fairmind path, INCLUDING
# brand-new untracked files. `git diff --name-only HEAD` lists only modified TRACKED
# files, so a task that creates new modules (a whole common case) would escape the
# journal requirement; `git status --porcelain` also reports untracked additions.
#
# A project that is not a git repo at all is the one legitimate blind spot: there
# is no mutation set to read, so there is no evidence of code changes to demand a
# journal for. That is an absence of signal, not a broken dependency, and stays
# non-blocking.
#
# Known limitation: `git status` reports the WHOLE tree, so this cannot yet
# attribute a change to *this* sub-agent versus one a sibling left un-journaled.
# It fails toward demanding a journal (safe for an audit-trail guarantee); precise
# per-agent attribution off the trace is a follow-up (PCF-1).
CODE_CHANGES=$(git -C "$CWD" status --porcelain 2>/dev/null | sed 's/^...//' | grep -v "^\.fairmind/" | head -1)
if [ -z "$CODE_CHANGES" ]; then
  exit 0
fi

echo "Journal missing. This sub-agent ($AGENT) modified code but did not create a journal. Write your journal (any .md file) in $BASE_PATH/journals/ — e.g. ${AGENT}_journal.md — before finishing." >&2
exit 2
