#!/usr/bin/env bash
# python-floor.sh — SessionStart hook: say, in one line, when `python3` cannot run
# this plugin.
#
# The judge, criteria and insights hooks are python3 programs behind wrappers
# that swallow stderr and exit 0, so that a broken engine can never block a
# session. The price of that choice is that an interpreter too old to import the
# engine produces no signal at all: those hooks simply stop, and a green session
# reads as a working one. This hook is the one place that failure is said out
# loud.
#
# It is plain bash ON PURPOSE. The thing it checks is the interpreter, so it
# cannot ask the interpreter to report on itself in anything the old one might
# not parse. Silent when python3 is recent enough; exits 0 on every path, and
# bounds its own probe rather than relying on the harness timeout.

FLOOR_MAJOR=3
FLOOR_MINOR=9
PROBE_SECONDS=2

say() {
  # The one-line format the other hooks use (`◆ Fairmind <Component>: <status>`),
  # spelled here because this hook cannot import Python. Every caller builds
  # `$1` from literals and validated digits only, so it is JSON-safe as written.
  printf '{"systemMessage": "◆ Fairmind Python: %s"}\n' "$1"
  exit 0
}

command -v python3 >/dev/null 2>&1 \
  || say "python3 not found on PATH — the plugin's hooks cannot run; install Python ${FLOOR_MAJOR}.${FLOOR_MINOR}+"

# Bounded: a python3 that hangs must not hold the session open. Stock macOS has
# no `timeout` binary, so a background watchdog does it; its output goes to
# /dev/null so it never holds the substitution's pipe open.
version="$(
  python3 -S -E -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null &
  probe=$!
  ( sleep "$PROBE_SECONDS"; kill "$probe" 2>/dev/null ) >/dev/null 2>&1 &
  watchdog=$!
  wait "$probe" 2>/dev/null
  kill "$watchdog" 2>/dev/null
)"

# Exactly `<digits>.<digits>` or it is not a version: nothing else reaches the
# message, so a broken interpreter cannot break the JSON line.
major="${version%%.*}"
minor="${version#*.}"
case "$major" in ''|*[!0-9]*) major="" ;; esac
case "$minor" in ''|*[!0-9]*) minor="" ;; esac
if [ -z "$major" ] || [ -z "$minor" ]; then
  say "python3 did not report its version — the plugin's hooks may not run; needs Python ${FLOOR_MAJOR}.${FLOOR_MINOR}+"
fi

if [ "$major" -gt "$FLOOR_MAJOR" ] \
   || { [ "$major" -eq "$FLOOR_MAJOR" ] && [ "$minor" -ge "$FLOOR_MINOR" ]; }; then
  exit 0
fi
say "python3 is ${major}.${minor}, older than ${FLOOR_MAJOR}.${FLOOR_MINOR} — the plugin's hooks will not run reliably; install Python ${FLOOR_MAJOR}.${FLOOR_MINOR}+ first on PATH"
