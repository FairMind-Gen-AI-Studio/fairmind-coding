#!/usr/bin/env bash
# session-end-insights.sh — SessionEnd hook (PL-A1a).
#
# Writes the session end-marker: shells to scripts/_insights_session.py, which
# stamps `ended_at` on the matching (tenancy, session_id) registry row. It is a
# no-op unless a registry already exists for this tenant — a session that never
# captured (no per-project Fairmind MCP) writes nothing, and the end-marker never
# CREATES a registry.
#
# Fail-open + fast, like every Fairmind hook: fast path first (module absent ->
# drain stdin, exit 0); every path exits 0 and prints nothing.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULE="$SELF_DIR/../../scripts/_insights_session.py"

# Fast path: module missing -> not installed -> drain stdin and no-op.
[ -f "$MODULE" ] || { cat >/dev/null 2>&1; exit 0; }

python3 "$MODULE" --session-end || exit 0
exit 0
