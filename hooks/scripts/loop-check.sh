#!/usr/bin/env bash
# loop-check.sh — Stop hook: the executed gate of fairmind-coding loop mode.
#
# Composition contract (design §6): this hook and check-journal.sh both fire on
# every Stop. Stop hooks compose with AND — a stop is blocked if EITHER hook
# exits 2. This hook is a silent no-op outside loop mode (no loop-state.json or
# status != running), so it never interferes with the interactive workflow.
#
# The "is there an active loop?" decision, the check evaluation, the budget
# accounting and the confirmation-gated stop all live in run_gate_checks.py
# (single source of truth). This wrapper only maps the engine's exit code to
# the Stop-hook contract:
#   engine 0  -> allow stop  (no loop, or terminal state) -> exit 0
#   engine 10 -> iterate     (feed feedback back, block)  -> exit 2
#   engine *  -> gate error  (surface, block once)        -> exit 2
#
# Exit 2 is the ONLY blocking code: any other non-zero exit is reported and the
# turn ends anyway. That cuts both ways, so this wrapper has two duties the engine
# cannot discharge for it:
#   - it must not need the engine to answer "is this even a Fairmind repo?".
#     Reaching run_gate_checks.py costs a python3 that a plain repo need not have,
#     and a failure to reach it maps to `gate error` -> exit 2, which would block
#     every stop in a repo that has no loop at all.
#   - once a workspace IS active it must never abort into a non-2 code. An unset
#     CLAUDE_PLUGIN_ROOT under `set -u` is exactly that shape of bug, so the
#     variable is defaulted and checked explicitly, and the gate refuses out loud.
set -uo pipefail

CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"

# Fast path. These are precisely the sources run_gate_checks.resolve_state_path
# consults (FAIRMIND_BASE env, .fairmind/active-context.json, and the
# conventional sibling .fairmind/loop-state.json); with none of them present the
# engine could only answer "no loop", so answer it here without paying for — or
# depending on — the interpreter.
if [ -z "${FAIRMIND_BASE:-}" ] &&
   [ ! -f "$CWD/.fairmind/active-context.json" ] &&
   [ ! -f "$CWD/.fairmind/loop-state.json" ]; then
  exit 0
fi

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ] || [ ! -f "$PLUGIN_ROOT/scripts/run_gate_checks.py" ]; then
  printf 'loop-check: CLAUDE_PLUGIN_ROOT does not point at the fairmind-coding plugin, so the gate engine (scripts/run_gate_checks.py) cannot be run. Reinstall the plugin, or run the gate manually with `python3 scripts/run_gate_checks.py --cwd %s`. Blocking the stop: a loop turn that ends ungated is a false green.\n' "$CWD" >&2
  exit 2
fi

# The Stop hook receives a JSON payload on stdin carrying `session_id`. Pass it to the
# engine so a running loop is bound to the session that drives it — an unrelated session
# stopping in the same repo must not be gated by (or drive) this loop. Read stdin only
# when it is piped (a hook), never a TTY (avoids blocking on a manual invocation), and
# with the `read` builtin so no external command stands between the payload and us.
INPUT=""
if [ ! -t 0 ]; then
  IFS= read -r -d '' INPUT || true
fi
SID="$(printf '%s' "$INPUT" | python3 -c "import sys,json
try:
    print(json.load(sys.stdin).get('session_id') or '')
except Exception:
    pass" 2>/dev/null || true)"

OUT=$(python3 "$PLUGIN_ROOT/scripts/run_gate_checks.py" --cwd "$CWD" --session-id "$SID" 2>&1)
CODE=$?

case "$CODE" in
  0)
    # Allow stop: no active loop, or a terminal state (passed_pending_human /
    # blocked_*). Surface any terminal message; do not block.
    [ -n "$OUT" ] && printf '%s\n' "$OUT"
    exit 0
    ;;
  10)
    # Iterate: gate not green (or green awaiting confirmations) with budget
    # left. Feed routed feedback back to the model and block the stop.
    printf '%s\n' "$OUT" >&2
    exit 2
    ;;
  *)
    # Internal error evaluating the gate — including a python3 that is not on
    # PATH: surface and block once so a broken gate is visible rather than
    # silently shipping a false green.
    printf 'loop-check: gate error (exit %s):\n%s\n' "$CODE" "$OUT" >&2
    exit 2
    ;;
esac
