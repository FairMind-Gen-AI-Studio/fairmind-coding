#!/usr/bin/env bash
# brain-answer.sh — PreToolUse hook on AskUserQuestion: ask the company brain
# before the person is asked.
#
# Thin on purpose. Everything that decides anything lives in
# scripts/brain_answer_hook.py; this file guarantees only that a machine
# without python3, or a plugin root that does not resolve, lets the question
# through exactly as if the plugin were not installed.
#
# EXIT 0 ON EVERY PATH, and no stdout unless the engine speaks. A hook that
# printed nothing leaves the tool call unchanged, so every failure here means
# the person is asked. The payload is drained before leaving on the no-op path:
# exiting with it unread leaves the writer with a closed pipe.
set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
  PLUGIN_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi
ENGINE="$PLUGIN_ROOT/scripts/brain_answer_hook.py"

if [ ! -f "$ENGINE" ] || ! command -v python3 >/dev/null 2>&1; then
  cat >/dev/null 2>&1
  exit 0
fi

python3 "$ENGINE" 2>/dev/null
exit 0
