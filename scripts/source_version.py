#!/usr/bin/env python3
"""The version of a ticket or pull request, as the brain's import doors compare it.

A reference card is rewritten only when the source it points at changed. The
client decides that, so it fingerprints the parts of the source a person wrote:

    version = sha256(canonical_json({"v": 1, "title", "description",
                                     "status", "texts"}))

* `title` and `description` are the source's own, normalised.
* `status` is the source's OWN word for its state ("MERGED", "in review",
  "Backlog Grooming"), normalised, never a category this client mapped it to.
* `texts` are the bodies of the HUMAN comments, normalised, ordered by
  `(created_at, id)`, empty ones dropped. For a pull request they are the
  issue comments, the non-empty submitted review bodies and every review-thread
  comment; for a ticket, its comments.
* A comment is human when it has an author with a login, the author is not
  flagged as a bot, the login does not end in `[bot]`, and the login is not one
  of the project's agent accounts. An unattributed comment (a deleted account)
  is not human.
* Normalisation is `" ".join(NFC(text).split())`: a whitespace-only edit never
  changes the version. Timestamps decide order only and are not hashed;
  reactions are never fetched; changed files are not part of it.

Canonical JSON is sorted keys, no ASCII escaping, compact separators. The
server treats the value as opaque; the recipe is pinned by this client's tests
and by the shared conformance fixture.

    source_version.py --in FILE [--agent-account LOGIN ...]

FILE (or `-` for stdin) holds `{"title", "description", "status", "comments":
[{"id", "created_at", "author": {"login", "is_bot"} | null, "body"}]}` and the
command prints `{"source_version": HEX}`. Exit 0 ok, 2 usage or unreadable
input. stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata

from login_key import match_key

FORMAT = 1


def normalise(text):
    """NFC, then collapse every run of whitespace to one space and trim."""
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def is_human(author, agent_accounts=(), source="github"):
    """Whether `author` ({"login", "is_bot"}) is a person who wrote the text.
    Logins are compared the way the brain keys them under `source`."""
    if not isinstance(author, dict):
        return False
    login = author.get("login")
    if not isinstance(login, str) or not login.strip():
        return False
    key = match_key(source, login)
    if author.get("is_bot") or key.endswith("[bot]"):
        return False
    return key not in {match_key(source, a) for a in agent_accounts or ()
                       if isinstance(a, str)}


def human_texts(comments, agent_accounts=(), source="github"):
    """The normalised bodies of the human comments in `(created_at, id)`
    order, the empty ones dropped."""
    kept = [c for c in comments or []
            if isinstance(c, dict) and is_human(c.get("author"), agent_accounts, source)]
    kept.sort(key=lambda c: (c.get("created_at") or "", str(c.get("id") or "")))
    texts = [normalise(c.get("body")) for c in kept]
    return [t for t in texts if t]


def source_version(title, description, status, comments, agent_accounts=(),
                   source="github"):
    """The 64-hex version of one source, as described in the module docstring."""
    document = {
        "v": FORMAT,
        "title": normalise(title),
        "description": normalise(description),
        "status": normalise(status),
        "texts": human_texts(comments, agent_accounts, source),
    }
    text = json.dumps(document, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="source_version.py")
    parser.add_argument("--in", dest="source", required=True)
    parser.add_argument("--agent-account", action="append", default=[])
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    try:
        if args.source == "-":
            document = json.load(sys.stdin)
        else:
            with open(args.source, encoding="utf-8") as handle:
                document = json.load(handle)
        if not isinstance(document, dict):
            raise ValueError("the input must be a JSON object")
        version = source_version(
            document.get("title"), document.get("description"),
            document.get("status"), document.get("comments"),
            args.agent_account, document.get("source") or "github")
    except (OSError, ValueError, UnicodeError) as err:
        print("source_version: %s" % err, file=sys.stderr)
        return 2
    print(json.dumps({"source_version": version}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
