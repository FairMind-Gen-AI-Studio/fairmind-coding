#!/usr/bin/env bash
# artifact-cards.sh - PreToolUse (Write|Edit, Bash), PostToolUse (Bash) and SessionStart wrapper
# for the working-artifact reference cards. Thin on purpose: everything that decides lives in
# scripts/artifact_cards.py (agent-neutral); hooks/scripts/artifact_cards_hook.py only
# translates this harness's payload. This file guarantees what neither can: a machine
# without python3 cannot be checked and so asks (never passes) for a Bash command or for a
# write that may be an artifact, and the common case is cheap.
#
# OPT-IN FIRST. Every mode is a silent pass with no network call (and, for an ordinary path, no python start; a
# path holding a backslash or a double quote is left to the Python adapter),
# unless the repository (found by walking up to the first .git; a linked worktree counts as
# its main checkout) carries a Fairmind marker: .fairmind/active-context.json,
# .fairmind/artifacts/config.json or .fairmind-artifacts.json. `refresh` asks instead whether
# the git pre-push check installed there is out of step with this plugin: it keeps in step only
# what the person installed, opted in or not.
#
# EXIT 0 ON EVERY PATH. The only decision the adapter can emit is "ask", printed as
# JSON on stdout; nothing here blocks a tool call.
#
# Usage: artifact-cards.sh gate | bash-gate | pr-closed | refresh
set -uo pipefail

MODE="${1:-}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
  PLUGIN_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi
HOOK="$PLUGIN_ROOT/hooks/scripts/artifact_cards_hook.py"

INPUT=""
if [ ! -t 0 ]; then
  IFS= read -r -d '' INPUT || true
fi

# A write is only worth a python start when it could be under the artifacts path:
# the default directory appears in the payload, or the file's repository configured
# another. The config that counts is the one at the written file's own repository
# root (found by walking up from the file to the first `.git`), not the session's
# project directory: a session started in a subdirectory, or a file in another
# repository, must still reach Python when that root carries a config.
CWD="${CLAUDE_PROJECT_DIR:-$PWD}"

# Sets NORMALIZED to absolute path $1 with its "." and ".." components resolved, lexically.
normalize_path() {
  local rest="$1" out="" part
  while [ -n "$rest" ]; do
    part="${rest%%/*}"
    if [ "$part" = "$rest" ]; then rest=""; else rest="${rest#*/}"; fi
    case "$part" in
      '' | .) ;;
      ..) out="${out%/*}" ;;
      *) out="$out/$part" ;;
    esac
  done
  NORMALIZED="$out"
}

# Sets IS_BARE (0 yes, 1 no): whether git directory $1 is configured `core.bare = true`,
# read from its config file with no git process (mirrors is_bare in
# scripts/_artifact_worktree.py: case-insensitive names, a bare key is true, and true is
# true, yes, on or 1). A config that is not a regular file (a FIFO would block the open)
# is not bare.
is_bare() {
  local line key val in_core=0
  IS_BARE=1
  [ -f "$1/config" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    case "$line" in
      "["*)
        key="${line%%]*}"
        key="${key#[}"
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        in_core=0
        case "$key" in [Cc][Oo][Rr][Ee]) in_core=1 ;; esac
        case "$line" in *"]"*) line="${line#*]}" ;; *) line="" ;; esac
        line="${line#"${line%%[![:space:]]*}"}"
        ;;
    esac
    [ "$in_core" = 1 ] || continue
    case "$line" in "" | "#"* | ";"*) continue ;; esac
    key="${line%%=*}"
    key="${key%"${key##*[![:space:]]}"}"
    case "$key" in [Bb][Aa][Rr][Ee]) ;; *) continue ;; esac
    case "$line" in
      *=*) val="${line#*=}" ;;
      *) IS_BARE=0; continue ;;
    esac
    val="${val%%#*}"
    val="${val%%;*}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#\"}"
    val="${val%\"}"
    case "$val" in
      [Tt][Rr][Uu][Ee] | [Yy][Ee][Ss] | [Oo][Nn] | 1) IS_BARE=0 ;;
      *) IS_BARE=1 ;;
    esac
  done <"$1/config" 2>/dev/null
  return 0
}

# $1 is a directory whose `.git` is a file. Sets WORKTREE_ROOT to the main checkout's root
# when it is a linked worktree: the gitdir it names holds a `commondir` (a submodule's does
# not, and stays a separate repository) and that common directory is a `.git` that is not
# itself a bare repository (a bare one has no checkout to inherit from, whatever it is called).
linked_worktree_root() {
  local line="" gd common
  [ -f "$1/.git" ] || return 1
  { IFS= read -r line; } 2>/dev/null <"$1/.git" || [ -n "$line" ] || return 1
  line="${line%$'\r'}"
  case "$line" in "gitdir: "*) gd="${line#gitdir: }" ;; *) return 1 ;; esac
  case "$gd" in /*) ;; *) gd="$1/$gd" ;; esac
  normalize_path "$gd"
  gd="$NORMALIZED"
  [ -f "$gd/commondir" ] || return 1
  common=""
  { IFS= read -r common; } 2>/dev/null <"$gd/commondir" || [ -n "$common" ] || return 1
  common="${common%$'\r'}"
  case "$common" in /*) ;; *) common="$gd/$common" ;; esac
  normalize_path "$common"
  common="$NORMALIZED"
  [ "${common##*/}" = ".git" ] || return 1
  is_bare "$common"
  [ "$IS_BARE" = 1 ] || return 1
  WORKTREE_ROOT="${common%/*}"
  [ -n "$WORKTREE_ROOT" ]
}

# Whether directory $1 itself carries an artifacts config.
has_config() {
  [ -f "$1/.fairmind/artifacts/config.json" ] || [ -f "$1/.fairmind-artifacts.json" ]
}

# Pure bash on purpose (no dirname, sed or head): the same test runs on the branch
# below, where PATH may hold nothing at all. A linked worktree whose own tree carries no
# config takes its main checkout's, whose untracked config is absent from the worktree.
has_config_above() {
  # $1: the written file's path as the payload spelled it.
  local p="$1" d
  case "$p" in /*) ;; *) p="$CWD/$p" ;; esac
  d="${p%/*}"
  while [ -n "$d" ] && [ ! -d "$d" ]; do d="${d%/*}"; done
  while [ -n "$d" ]; do
    has_config "$d" && return 0
    if [ -d "$d/.git" ]; then
      return 1
    elif [ -e "$d/.git" ]; then
      linked_worktree_root "$d" && has_config "$WORKTREE_ROOT" && return 0
      return 1
    fi
    d="${d%/*}"
  done
  # Reached the filesystem root ("/" leaves an empty string): check it as well.
  [ -f "/.fairmind-artifacts.json" ] && return 0
  return 1
}

# Whether directory $1 itself carries a Fairmind marker.
has_marker() {
  [ -f "$1/.fairmind/active-context.json" ] || [ -f "$1/.fairmind/artifacts/config.json" ] \
    || [ -f "$1/.fairmind-artifacts.json" ]
}

# Whether the repository holding directory $1 carries a Fairmind marker. Walks up to the
# first `.git` (inclusive); a linked worktree also takes its main checkout's marker, since
# the marker file is usually untracked and so absent from the worktree. A project that never
# opted in costs nothing: no python, no network. Pure bash, like has_config_above.
opted_in_at() {
  local d="$1"
  case "$d" in /*) ;; *) d="$CWD/$d" ;; esac
  while [ -n "$d" ] && [ ! -d "$d" ]; do d="${d%/*}"; done
  while [ -n "$d" ]; do
    has_marker "$d" && return 0
    if [ -d "$d/.git" ]; then
      return 1
    elif [ -e "$d/.git" ]; then
      linked_worktree_root "$d" && has_marker "$WORKTREE_ROOT" && return 0
      return 1
    fi
    d="${d%/*}"
  done
  has_marker ""
}

# The directory the call acts in: the written file's directory for a write, else the
# payload's cwd, else the session's. Returns 1 (printing nothing) when the value cannot be
# read here: a backslash means a JSON escape, which the quote-bounded match cuts short.
opt_in_dir() {
  local re p
  if [ "$MODE" = "gate" ]; then
    re='"file_path"[[:space:]]*:[[:space:]]*"([^"]*)"'
    if [[ "$INPUT" =~ $re ]] && [ -n "${BASH_REMATCH[1]}" ]; then
      p="${BASH_REMATCH[1]}"
      case "$p" in *\\*) return 1 ;; esac
      case "$p" in /*) ;; *) p="$CWD/$p" ;; esac
      printf '%s' "${p%/*}"
      return
    fi
  fi
  re='"cwd"[[:space:]]*:[[:space:]]*"([^"]*)"'
  if [[ "$INPUT" =~ $re ]] && [ -n "${BASH_REMATCH[1]}" ]; then
    case "${BASH_REMATCH[1]}" in *\\*) return 1 ;; esac
    printf '%s' "${BASH_REMATCH[1]}"
    return
  fi
  printf '%s' "$CWD"
}

# Sets COMMON_DIR to the git directory the repository holding directory $1 shares with its
# worktrees, read the way git reads it: a `.git` directory, or a `.git` file naming a gitdir
# and, for a linked worktree, a `commondir` (a submodule and a worktree of a bare repository
# included, as `git rev-parse --git-common-dir` resolves them).
common_dir_of() {
  local d="$1" line="" gd common=""
  case "$d" in /*) ;; *) d="$CWD/$d" ;; esac
  while [ -n "$d" ] && [ ! -e "$d/.git" ]; do d="${d%/*}"; done
  [ -n "$d" ] || return 1
  if [ -d "$d/.git" ]; then
    COMMON_DIR="$d/.git"
    return 0
  fi
  [ -f "$d/.git" ] || return 1  # a FIFO would block the read
  { IFS= read -r line; } 2>/dev/null <"$d/.git" || [ -n "$line" ] || return 1
  line="${line%$'\r'}"
  case "$line" in "gitdir: "*) gd="${line#gitdir: }" ;; *) return 1 ;; esac
  case "$gd" in /*) ;; *) gd="$d/$gd" ;; esac
  if [ -f "$gd/commondir" ]; then
    { IFS= read -r common; } 2>/dev/null <"$gd/commondir" || [ -n "$common" ] || return 1
    common="${common%$'\r'}"
    case "$common" in /*) gd="$common" ;; *) gd="$gd/$common" ;; esac
  fi
  normalize_path "$gd"
  COMMON_DIR="$NORMALIZED"
}

# Whether the repository holding directory $1 has something of the git pre-push check to keep
# in step, opted in or not: a copy of the CLI that differs from this plugin's, or a hook of ours
# with no copy yet (an older one pinned the plugin's path). `--refresh` writes the hook before
# the copy, so a copy equal to this plugin's means a current hook, and the common case costs no
# python start. The names are COPY_DIR, COPIED and HOOK_MARKER in scripts/artifact_cards.py.
needs_refresh_at() {
  local name
  common_dir_of "$1" || return 1
  if [ -f "$COMMON_DIR/fairmind/artifact_cards.py" ]; then
    for name in artifact_cards.py _artifact_worktree.py; do
      cmp -s "$COMMON_DIR/fairmind/$name" "$PLUGIN_ROOT/scripts/$name" || return 0
    done
    return 1
  fi
  [ -f "$COMMON_DIR/hooks/pre-push" ] &&
    grep -q -F "# fairmind-coding artifact push gate" "$COMMON_DIR/hooks/pre-push" 2>/dev/null
}

# An unreadable directory is left to the Python adapter, which parses the payload as JSON
# and applies the same rule.
check=opted_in_at
[ "$MODE" = "refresh" ] && check=needs_refresh_at
OPT_DIR="$(opt_in_dir)" && ! "$check" "$OPT_DIR" && exit 0

may_be_artifact() {
  case "$INPUT" in *artifacts*) return 0 ;; esac
  local re='"file_path"[[:space:]]*:[[:space:]]*"([^"]*)"'
  [[ "$INPUT" =~ $re ]] || return 0
  [ -z "${BASH_REMATCH[1]}" ] && return 0
  case "${BASH_REMATCH[1]}" in *\\*) return 0 ;; esac
  has_config_above "${BASH_REMATCH[1]}"
}

if [ "$MODE" = "gate" ] && ! may_be_artifact; then
  exit 0
fi

if [ ! -f "$HOOK" ] || ! command -v python3 >/dev/null 2>&1; then
  # Nothing here can say whether the repository is public, so the person decides.
  case "$MODE" in
    bash-gate) ;;  # every command asks here: no line can be read without python3
    gate) ;;  # may_be_artifact already let it through
    *) exit 0 ;;
  esac
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Whether this repository is public could not be checked, because python3 is not available to the artifact-cards hook. A push to a public repository publishes every working artifact it carries: confirm with the person first."}}'
  exit 0
fi

# An interpreter that exists but cannot run the adapter (broken install, crash) says
# nothing, and silence reads as a pass: a non-zero exit asks, exactly as a missing
# python3 does.
OUT="$(printf '%s' "$INPUT" | python3 "$HOOK" "$MODE" 2>/dev/null)"
RC=$?
if [ "$RC" -ne 0 ]; then
  case "$MODE" in
    bash-gate | gate)
      printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"The artifact-cards hook could not run, so this call was not checked. A push to a public repository publishes every working artifact it carries: confirm with the person first."}}'
      ;;
  esac
  exit 0
fi
[ -n "$OUT" ] && printf '%s\n' "$OUT"
exit 0
