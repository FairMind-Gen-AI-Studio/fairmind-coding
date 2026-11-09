"""When two spellings of a tracker id are one ticket.

Shared by `ticket_refs.py` (which resolves a reference against the inventory)
and `ticket_links.py` (which matches a pull request's tickets to the
inventory's rows): both must give the same answer, or a pull request cites a
ticket the link pass then fails to find. stdlib only.
"""
from __future__ import annotations

from login_key import PREFIXED_SOURCE, notion_id

__all__ = ["id_key", "same_id"]


def id_key(source, ident):
    """The form of `ident` under `source` that equal ids share: every tracker's
    id is compared without regard to case, and a Notion page id also without
    regard to its dashes (`login_key.notion_id`)."""
    text = str(ident).strip()
    if str(source).strip().casefold() == PREFIXED_SOURCE:
        text = notion_id(text) or text
    return text.casefold()


def same_id(source, a, b):
    """Whether two spellings of an id are one id under `source`."""
    return id_key(source, a) == id_key(source, b)
