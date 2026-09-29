#!/usr/bin/env bash
# loop-banner.sh <mode> — UserPromptExpansion hook for /fairmind-loop and /fairmind-develop.
# Display-only: emits the command's opening (banner + first-run map) as a `{"systemMessage": ...}`
# on stdout, which the client renders. A Bash *tool* call's stdout is collapsed to "Ran 1 shell
# command" and never shown, so the opening must come from a hook. <mode> is "loop" or "develop".
# The repo root comes from the same env chain the other hooks use (never $0's dir). Never blocks:
# every path exits 0.
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
python3 "$SELF_DIR/../../scripts/loop_open.py" --mode "${1:-loop}" --emit-hook --cwd "$CWD" 2>/dev/null || true
exit 0
