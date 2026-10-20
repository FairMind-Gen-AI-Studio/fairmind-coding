#!/usr/bin/env bash
# front-desk-banner.sh — UserPromptExpansion hook for /fairmind-coding:fairmind-coding.
# Display-only: it emits the front-desk banner as a `{"systemMessage": ...}` on stdout
# (which the client renders) and returns no permission decision. A Bash *tool* call's
# stdout is collapsed to "Ran 1 shell command" and never shown, so the banner must come
# from a hook, not from the command body. Never blocks: every path exits 0.
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SELF_DIR/../../scripts/front_desk_banner.py" 2>/dev/null || true
exit 0
