#!/usr/bin/env bash
# session-start-insights.sh — SessionStart hook (PL-A1a + PL-A1b).
#
# The OPT-scoped ambient telemetry GATE, evaluated FRESH every session (outside
# loop mode). It shells to scripts/_insights_session.py, which:
#   - fail-CLOSES unless this repo has a PER-PROJECT Fairmind MCP configured (a
#     user-global Fairmind entry MUST NOT arm capture — plan V7 "no honest
#     tenant", the privacy guard) and is not switched off in the COMMITTABLE
#     repo-root .fairmind-insights.json — the only scope either switch reads
#     since 2026-07-27, because the decision is the company's and not the
#     developer's (the per-user ~/.fairmind/insights-config.json is inert);
#   - on capture, registers the session (the only repo IDENTIFIER is the opaque
#     tenancy id — never a raw path or branch on the WIRE) and, the FIRST time
#     only per (user, repo), emits the one-time
#     notice as strict JSON {"systemMessage": ...} on stdout — the HUMAN
#     channel a SessionStart hook makes visible before the first turn. T2-C3
#     added a SECOND, separately-recorded notice for the event skeleton, shown
#     only while `event_skeleton_consent` says GRANTED; both ride that ONE
#     JSON object (two writes would emit two concatenated objects and the
#     harness would parse neither). Both notices tell the reader about a company
#     decision; neither asks for anything.
#
# CORRECTION, OPEN-1 (2026-07-30), recorded here because this is the file wired in
# hooks.json and therefore the first one a privacy reviewer opens. The line above
# used to read "opaque tenancy id ONLY — no raw path/branch". That is now narrowed
# from "never PERSISTED" to "never WIRE-BOUND": the LOCAL registry row also carries
# that session's own git `toplevel` and its own `~/.claude/projects/<slug>`
# transcript directory, both raw and both able to embed $HOME. So
# ~/.fairmind/insights/sessions/<tenancy>.jsonl is NOT path-free — treat it as
# sensitive in any support/diagnostic bundle. It is 0600 and local; the WIRE
# payload is built from `meta`, which gains neither field, and
# tests/test_open1_row_provenance.py pins both halves. The row has to carry them
# because the sweep used to apply the LAUNCHING session's transcript dir and
# skeleton decision to every ended-not-digested row of a registry that is shared
# by every worktree and cwd of the repo. The branch name is still recorded nowhere.
#
# PL-A1b: after the foreground gate above returns, this hook ALSO spawns the
# ambient DIGESTER's sweep (`_insights_session.run_sweep`, via `--sweep`) —
# detached (`&`) and niced (`nice -n 10`) so it never blocks or slows session
# open. The sweep finalizes any of this tenancy's crash-orphans (an ended
# session whose digest never ran, e.g. the process died mid-digest); a session
# with a live digester already holding its per-session lock is skipped, never
# reaped. The same payload is fed to both invocations, so stdin is drained
# ONCE into a variable rather than consumed twice.
#
# Fail-open + fast (SPIKE-A): a SessionStart hook must NEVER block or slow session
# open. Fast path first (module absent -> drain stdin, exit 0); the gate is a
# couple of cheap file reads + one `git rev-parse`; every path exits 0.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULE="$SELF_DIR/../../scripts/_insights_session.py"

# Fast path: module missing -> not installed -> drain stdin and no-op.
[ -f "$MODULE" ] || { cat >/dev/null 2>&1; exit 0; }

PAYLOAD="$(cat)"

printf '%s' "$PAYLOAD" | python3 "$MODULE" --session-start || true

# Detached + niced ambient digester sweep (PL-A1b). Backgrounded before this
# script exits; a non-interactive script's background children are not sent
# SIGHUP on the parent's exit, so this keeps running after the hook returns.
printf '%s' "$PAYLOAD" | nice -n 10 python3 "$MODULE" --sweep >/dev/null 2>&1 &

exit 0
