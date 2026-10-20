#!/usr/bin/env bash
# brain-record-answer.sh — PostToolUse hook on AskUserQuestion: record the
# person's answer to a design question in the company brain.
#
# Thin on purpose. Everything that decides anything lives in
# scripts/brain_record_answer_hook.py; this file guarantees only that a machine
# without python3, or a plugin root that does not resolve, records nothing and
# says nothing.
#
# EXIT 0 ON EVERY PATH, and no stdout unless the engine speaks. The payload is
# drained before leaving on the no-op path: exiting with it unread leaves the
# writer with a closed pipe.
set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
  PLUGIN_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi
ENGINE="$PLUGIN_ROOT/scripts/brain_record_answer_hook.py"

if [ ! -f "$ENGINE" ] || ! command -v python3 >/dev/null 2>&1; then
  cat >/dev/null 2>&1
  exit 0
fi

python3 "$ENGINE" 2>/dev/null
exit 0
