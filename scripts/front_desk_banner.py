#!/usr/bin/env python3
"""
front_desk_banner.py — emit the /fairmind-coding front-desk banner as a display-only
`systemMessage`, for the plugin's `UserPromptExpansion` hook.

A Bash *tool* call's stdout is collapsed by the client to "Ran 1 shell command" and never
shown to the user, so a banner printed that way is invisible. A `UserPromptExpansion` hook
that writes `{"systemMessage": ...}` to stdout IS rendered — this is how the banner reaches
the user (the same mechanism claude-security uses). The hook fires only for this exact
command, so the model never prints the banner itself; its first act is the menu.

The wordmark is assembled from a per-letter table so the columns line up by construction,
and the version is read from the sibling .claude-plugin/plugin.json (never hardcoded). Always exits 0 with
either the banner or nothing — a failure here must never break command expansion. Stdlib only.
"""

import contextlib
import json
import os
import sys

# ANSI Shadow letters, six rows each; the right edge is rstripped on output.
_GLYPHS = {
    "F": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "██║     ", "╚═╝     "],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "M": ["███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║", "╚═╝     ╚═╝"],
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "D": ["██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "],
}

_WORDMARK = "FAIRMIND"
_INDENT = "  "


def _wordmark_lines():
    for row in range(6):
        yield (_INDENT + "".join(_GLYPHS[ch][row] for ch in _WORDMARK)).rstrip()


def _version():
    """The version from the sibling .claude-plugin/plugin.json, or None if it can't be read."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".claude-plugin", "plugin.json")
    try:
        with open(path, encoding="utf-8") as handle:
            version = json.load(handle).get("version")
    except (OSError, ValueError):
        return None
    return version if isinstance(version, str) and version else None


def banner():
    version = _version()
    tagline = "Fairmind coding workflow"
    if version:
        tagline += f"  ·  v{version}"
    lines = [
        "",
        *_wordmark_lines(),
        "",
        _INDENT + tagline,
        _INDENT + "Other jobs — just type: brain requirements · flush insights · report · fix an issue",
        "",
    ]
    return "\n".join(lines)


def emit(message):
    """Write one systemMessage. Never raises; a failed write is just no banner."""
    try:
        sys.stdout.write(json.dumps({"systemMessage": message}))
        sys.stdout.flush()
    except Exception:
        with contextlib.suppress(Exception):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def main():
    try:
        emit(banner())
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
