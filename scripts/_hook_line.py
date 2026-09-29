"""The one line a Fairmind hook shows the user.

Every hook that has something for the user to read says it the same way:
`◆ Fairmind <Component>: <status>`, one line, no color. The line goes to
`systemMessage`, which the harness prints as `<Event> says: …`; anything the
MODEL must act on (cited criteria, a page path, an outcome command) goes to
`additionalContext`, which the user never sees. Keeping the two apart is the
point: the user reads a status, the model reads the detail, and nobody has to
be told to relay one into the other.

Stdlib only, and nothing here raises: every caller is a hook that exits 0.
"""

from __future__ import annotations  # `str | None` stays importable before 3.10

import json

MARK = "◆"
#: A status longer than this is cut, because the harness prints the line as one
#: row and a wrapped status reads as two messages.
MAX_STATUS = 120


def clip(text: str, limit: int) -> str:
    """`text` on one row and at most `limit` characters, cut at a word."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit - 1]
    # At a word boundary when there is one, so the cut never reads as a typo —
    # and a slice that already ends on a whole word keeps it.
    if flat[limit - 1] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return (cut.rstrip(" ,;:—") or cut) + "…"


def line(component: str, status: str) -> str:
    """`◆ Fairmind <component>: <status>` on exactly one line."""
    return f"{MARK} Fairmind {component}: {clip(status, MAX_STATUS)}"


def payload(event: str, *, user: str | None = None,
            model: str | None = None) -> dict:
    """The hook output object: `systemMessage` and/or `additionalContext`."""
    out: dict = {}
    if user:
        out["systemMessage"] = user
    if model:
        out["hookSpecificOutput"] = {"hookEventName": event,
                                     "additionalContext": model}
    return out


def emit(event: str, *, user: str | None = None,
         model: str | None = None) -> None:
    """Print the payload as one JSON object; print nothing when it is empty."""
    out = payload(event, user=user, model=model)
    if out:
        print(json.dumps(out))
