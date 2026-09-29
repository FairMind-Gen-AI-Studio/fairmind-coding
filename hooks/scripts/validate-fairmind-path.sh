#!/usr/bin/env bash
# validate-fairmind-path.sh — PreToolUse on Write|Edit: keep writes to the
# .fairmind/ workspace inside the scope active-context.json ITSELF declares via
# its `base_path` field, derived per-session rather than hard-coded.
#
# PCF-12/PCF-14: this used to be a fixed two-level regex
# (`.fairmind/<slug>/<slug>/...`), which admitted ANY two-segment shape
# regardless of what a given session's active-context.json actually declared
# as its base_path (depth-not-scope: a sibling session at the same depth was
# wrongly admitted) and never inspected path segment content at all (a lexical
# `..` traversal segment sailed through — the regex only counted slashes). The
# scope is now DERIVED from the declared base_path, first-match decision
# ladder: (1) any '..' path segment refuses outright; (1.5) a target inside an
# EXISTING opt-in loop worktree parked at .fairmind/worktrees/<ref>/ is rebased onto
# that worktree's OWN root before anything else runs, since CLAUDE_PROJECT_DIR
# stays pinned to the main checkout for the life of the session — see the
# rung's own comment; (2) the bootstrap write to root-level
# active-context.json is always exempt; (3) if a base_path is declared, the
# target must lie under it; (4) a present-but-unreadable context refuses
# (fail-closed — it can never prove admission); (5) an absent context, or
# valid JSON with no base_path declared, falls back to the legacy
# two-level-scoped-path shape, byte-identical to the pre-PCF-12 behavior.
#
# Exit-code contract: PreToolUse blocks on exit 2 and ONLY on exit 2. Any other
# non-zero exit is reported and the Write/Edit proceeds. So an aborting script —
# `set -e` plus a missing jq (127) or a malformed payload (jq exits 5) — does not
# fail safe: it silently disables the guard at the one moment it cannot tell you
# it is disabled. Hence no `set -e`, and every failure inside an active workspace
# is a refusal (exit 2), never a shrug.
#
# Outside a Fairmind workspace (no active-context.json) the hook stays a silent
# no-op: the plugin may be installed in a repo that never opens a session, and a
# hook dependency it does not use must not block ordinary edits there.
set -uo pipefail

CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
CONTEXT_FILE="$CWD/.fairmind/active-context.json"

# Drain stdin with the `read` builtin, not `cat`: a stripped PATH is one of the
# failure modes the hook must survive long enough to refuse. Skip when stdin is a
# TTY so a manual invocation does not hang.
INPUT=""
if [ ! -t 0 ]; then
  IFS= read -r -d '' INPUT || true
fi

if ! command -v jq >/dev/null 2>&1; then
  [ -f "$CONTEXT_FILE" ] || exit 0
  echo "fairmind path guard: jq is not on PATH, so this write cannot be checked against the scoped .fairmind/ path. Install jq (brew install jq) or remove the fairmind-coding hooks; an unchecked write into an active Fairmind workspace is refused." >&2
  exit 2
fi

if ! FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null); then
  [ -f "$CONTEXT_FILE" ] || exit 0
  echo "fairmind path guard: the PreToolUse payload on stdin is not valid JSON, so the write target cannot be read. Refusing the write rather than letting it bypass the .fairmind/ scope check." >&2
  exit 2
fi

# Only check .fairmind/ paths
if [[ "$FILE_PATH" != *".fairmind/"* ]]; then
  exit 0
fi

# Rung 0 — anchor an ABSOLUTE FILE_PATH to the workspace root itself, ahead of
# every later rung (traversal, bootstrap exemption, base_path scope, legacy
# fallback) so all of them operate on a workspace-relative path from here on.
#
# QW-1 round 2 (adversarial break-pass): rung 3 below used to derive REL by
# slicing FILE_PATH from its OWN first ".fairmind/" occurrence, without ever
# checking that FILE_PATH was actually rooted at this workspace's CWD. So an
# absolute, out-of-workspace target whose ".fairmind/<tail>" happened to
# lexically match the declared base_path was wrongly admitted — e.g. with
# base_path ".fairmind", both /home/victim/.fairmind/secret.md and
# /etc/.fairmind/passwd returned rc 0 (the legacy two-level regex refused
# both). This rung closes that: an absolute path must lie under "$CWD"/ or it
# is refused outright, regardless of what its .fairmind/ tail looks like. A
# relative FILE_PATH (what every path-scope case before this round exercised)
# skips this rung untouched.
if [[ "$FILE_PATH" = /* ]]; then
  case "$FILE_PATH" in
    "$CWD"/*)
      FILE_PATH="${FILE_PATH#"$CWD"/}"
      ;;
    *)
      echo "fairmind path guard: '$FILE_PATH' is an absolute path outside this workspace ($CWD). A .fairmind/ write must be rooted at the workspace itself, not merely contain a .fairmind/ segment somewhere else on the filesystem." >&2
      exit 2
      ;;
  esac
fi

# Rung 1 — closes the lexical traversal hole a fixed segment-count regex left
# open: it never inspects segment content, so `.fairmind/a/../../../etc/evil.md`
# used to sail through. Refused ahead of any scope comparison.
if [[ "$FILE_PATH" =~ (^|/)\.\.(/|$) ]]; then
  echo "fairmind path guard: '$FILE_PATH' contains a '..' path segment. Traversal segments are never allowed in a .fairmind/ write, regardless of the declared scope." >&2
  exit 2
fi

# Rung 1.5 — worktree rebasing.
#
# A loop's opt-in maker worktree (`loop_worktree.py`, T9) lives at
# "$CWD/.fairmind/worktrees/<ref>/" — a full, separate git checkout that is
# merely PARKED inside the main checkout's own .fairmind/ directory (already
# excluded from the loop's own mutation set, hence parking it there at all).
# CLAUDE_PROJECT_DIR is fixed for the life of a session and keeps naming the
# MAIN checkout even when the session's actual cwd is that nested worktree, so
# an ORDINARY, in-worktree repository file (e.g. plugins/.../foo.py, wholly
# unrelated to any Fairmind workspace state) arrives here as
# ".fairmind/worktrees/<ref>/plugins/.../foo.py" — it carries the literal
# ".fairmind/" substring purely because of WHERE the worktree happens to be
# checked out on disk, never because the file is itself workspace state. Left
# unhandled, rung 3/4 below would measure that worktree-relative tail against
# the MAIN checkout's base_path — an unrelated workspace, declared for an
# unrelated task — and refuse every write for the life of the loop.
#
# Detect the nesting and rebase onto the worktree's OWN root before any scope
# comparison runs: everything past this rung continues to operate on
# CWD/CONTEXT_FILE/FILE_PATH exactly as before, just re-rooted. A target under
# the worktree that is NOT itself under the worktree's own .fairmind/ is
# ordinary and exits 0 immediately here, mirroring the non-.fairmind
# pass-through above; a target that IS under the worktree's own .fairmind/
# (its own active-context.json, its own journals, ...) falls through to rungs
# 2-5 unchanged, but reading THAT workspace's own declared scope, never the
# main checkout's. The '..'-segment refusal above already ran on the
# untouched path, so a worktree "ref" segment can never itself be a traversal
# component by the time this rung reads it.
#
# Only a worktree that EXISTS is rebased onto: its root must carry the `.git`
# file `git worktree add` writes. Any other name under .fairmind/worktrees/ —
# never created, or a plain directory — is ordinary main-checkout .fairmind/
# state and falls through to rungs 2-5 against the MAIN checkout's scope, so
# the path text alone can never widen what this hook admits
# (test_validate_path_scope.sh cases 24-27).
if [[ "$FILE_PATH" =~ ^\.fairmind/worktrees/([^/]+)/(.*)$ ]]; then
  WT_ROOT="$CWD/.fairmind/worktrees/${BASH_REMATCH[1]}"
  WT_REL="${BASH_REMATCH[2]}"
  if [ -f "$WT_ROOT/.git" ]; then
    case "$WT_REL" in
      .fairmind/*)
        CWD="$WT_ROOT"
        CONTEXT_FILE="$CWD/.fairmind/active-context.json"
        FILE_PATH="$WT_REL"
        ;;
      *)
        exit 0
        ;;
    esac
  fi
fi

# Rung 2 — the bootstrap write to the pointer itself, exempt independent of
# any base_path shape or of whether the context file is even readable yet.
#
# QW-1 round 3 (Codex review finding C2-narrow): this used to be a SUFFIX match
# (`=~ \.fairmind/active-context\.json$`), which wrongly exempted ANY path that
# merely ENDS WITH that suffix — e.g. "tmp/.fairmind/active-context.json" under
# a nested base_path is neither the workspace's real bootstrap file nor inside
# the declared scope, yet the suffix match let it through. By this point rung 0
# has already rebased an absolute FILE_PATH to be workspace-relative, so an
# EXACT match on the root pointer's own workspace-relative path is now the
# correct and sufficient test — the real bootstrap write is always exactly
# ".fairmind/active-context.json", never nested under anything.
if [[ "$FILE_PATH" == ".fairmind/active-context.json" ]]; then
  exit 0
fi

# Rung 3/4 — scope derived from active-context.json's declared base_path.
#
# QW-1 round 3 (Codex review finding C4): `[ -f "$CONTEXT_FILE" ]` is false for
# BOTH "nothing at this path at all" and "something is clearly present here but
# is not a readable regular file" (a directory, or a dangling symlink) — the
# single test could not tell those apart, so both silently fell through to
# BASE_PATH="" and the legacy fallback, admitting an otherwise in-shape write
# even though a context plainly exists and its scope could not actually be
# read. Fail closed instead: `[ -e ] || [ -L ]` (an existence check that also
# catches a dangling symlink, which `-e` alone follows and reports absent) means
# "something is here, just not a regular file we can parse" — refuse outright,
# never guess from the legacy shape. TRUE absence (neither branch) is the only
# path that still reaches the legacy fallback, unchanged.
if [ -f "$CONTEXT_FILE" ]; then
  if ! BASE_PATH=$(jq -r '.base_path // empty' "$CONTEXT_FILE" 2>/dev/null); then
    echo "fairmind path guard: $CONTEXT_FILE exists but could not be parsed as JSON, so the declared base_path scope is unknowable. Refusing the write rather than guessing (fail-closed)." >&2
    exit 2
  fi
elif [ -e "$CONTEXT_FILE" ] || [ -L "$CONTEXT_FILE" ]; then
  echo "fairmind path guard: $CONTEXT_FILE exists but is not a readable regular file (a directory, or a symlink that does not resolve to one), so the declared base_path scope is unknowable. Refusing the write rather than guessing (fail-closed)." >&2
  exit 2
else
  BASE_PATH=""
fi

if [ -n "$BASE_PATH" ]; then
  # Repo-relative tail of the target path, from its own first ".fairmind/"
  # segment on. By this point rung 0 has already anchored FILE_PATH to the
  # workspace when it arrived absolute, so this slice is safe: it can no
  # longer be fooled by an out-of-workspace path whose tail merely looks
  # right.
  REL=".fairmind/${FILE_PATH#*.fairmind/}"
  case "$REL" in
    "${BASE_PATH%/}"/*)
      exit 0
      ;;
    *)
      echo "fairmind path guard: '$FILE_PATH' lies outside the scope declared by active-context.json's base_path ('$BASE_PATH'). Use a path under ${BASE_PATH%/}/." >&2
      exit 2
      ;;
  esac
fi

# Rung 5 — the legacy fallback's actual shape: a fixed two-level scoped
# path .fairmind/<slug>/<slug>/... (byte-identical to pre-PCF-12 behavior).
if [[ "$FILE_PATH" =~ \.fairmind/[^/]+/[^/]+/ ]]; then
  exit 0
fi

echo "Flat .fairmind/ path not allowed, and active-context.json declares no base_path to derive a scope from. Read .fairmind/active-context.json for FAIRMIND_BASE and use scoped path." >&2
exit 2
