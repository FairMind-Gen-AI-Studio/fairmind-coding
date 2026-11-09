#!/usr/bin/env python3
"""Shape a tracker ticket into the skeleton of a reference card.

A session reads a ticket and its comments through the tracker's own connection
and writes them, as they are, to a local file. This script turns that file into
what the brain's reference card for the ticket is built from, so the agent never
re-types a mechanical field:

    ticket_card.py assemble --in-dir PARTS_DIR --out-dir RAW_DIR
    ticket_card.py shape  --in-dir RAW_DIR --out-dir DIR
                          [--agent-account LOGIN ...] [--index FILE]
    ticket_card.py redact --read FILE --erased LOGIN [--erased LOGIN ...]

Each `RAW_DIR/*.json` is one ticket:

    {"source", "id", "key"?, "url"?, "project_key"?, "kind": "task" | "issue" |
     "requirement", "type"?, "title", "description"?, "status"?,
     "status_category": "todo" | "in_progress" | "done" | "cancelled" | null,
     "author": {"login", "is_bot"?}, "assignees"?: [{"login", "is_bot"?}],
     "created_at", "closed_at"?, "parent"?: {"id", "kind"?},
     "pull_request_urls"?, "commit_shas"?, "visibility"?: "public" |
     "private" | "restricted" | null,
     "comments"?: [{"id", "created_at", "author": {"login", "is_bot"?} | null,
                    "body"}]}

`assemble` is what writes those raw files, so that no model types a ticket's text
into JSON. A reader leaves, per ticket, a directory `PARTS_DIR/<name>/` with
`ticket.json` (the fields above, but no `description` and, in each comment, a
`body_file` in place of `body`), a `description_file` key naming a plain text
file beside it when the ticket has a description, and one plain text file per
comment body. Those text files hold the text exactly as the tracker returned it.
`assemble` reads them as they are (UTF-8, nothing stripped, reworded or
re-escaped) and writes `RAW_DIR/<name>.json`. stdout is `{"assembled",
"not_assembled": [{"dir", "reason"}]}`; a ticket whose part is missing is listed
there, its raw file of an earlier run is removed, and the others are assembled.
A raw file with no parts directory at all is removed too: `RAW_DIR` holds only
what this run's readers left.
A comment with no text still names a file, an empty one: a comment with no
`body_file` is not assembled, so that a forgotten file never drops it silently.

`shape` writes, per ticket, under `DIR/<source>-<id>/`:

* `card.json`, the SKELETON: every mechanical field of the ticket and none of its
  text (no description, no comment). `source_version` is the fingerprint
  `source_version.py` describes, `source_visibility` the tracker's own word for
  who can see the project (null when it reports none), `participants` the
  author, the assignees and everyone who commented, bots included, once each in
  login order, so that erasing a person by login still reaches the card.
  `reference_card.py stage` joins it with the agent's summary and points.
* `read.md`, what the agent reads to write its summary: the description and the
  comments, in order, each quoted. It stays on this machine and is never sent.
  Every line of the description and of a comment starts with `>`; the title and
  header lines do not. `ticket_links.py merge` reads the GitHub pull request
  addresses off those quoted lines and adds them to the card's links.

and an index (`DIR/shaped.json` unless `--index` names another): a list of
`{"source", "id", "key"?, "title", "url"?, "source_version", "card"}`, the rows
a seeding plan takes as its tasks. stdout is `{"shaped", "index", "not_shaped":
[{"file", "reason"}]}`: a ticket that cannot become a card is listed there and
the others are still shaped. A staged `write.json` beside a
card is left alone: it belongs to the version it was staged for.

Erased people. The brain's check answer may name `erased_participants`: logins
an administrator has erased. `redact` rewrites a `read.md` in place without what
those people wrote (their comments, and the description when one of them opened
the ticket), before the agent reads it, so a new summary cannot put their words
back. Other people's quotations of them are not found. It is the same operation
`pr_collect.py redact` performs on a pull request's reading file.

More than 100 participants, 20 assignees, 50 pull request links or 250 commits
is refused, never cut: a list cut short hides a person from an erasure or a link
from a read. The links the tracker carried stay on the card whole; `ticket_links.py
merge` adds to them only the room that is left up to 50. Exit codes: 0 ok; 2 usage or an unreadable input directory; 3 any
ticket in `not_shaped`; 5 a file cannot be written. stdout is one JSON document;
diagnostics go to stderr. stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from pr_collect import (CONVERSATION_HEADING, DESCRIPTION_HEADING, GHOST_LOGIN,
                        _atomic_write_text, _quote, redact_reading,
                        redaction_record, save_redaction_record, token)
from login_key import login_key, sanitize, source_of, wire_login
from source_version import source_version

KINDS = ("task", "issue", "requirement")
CATEGORIES = ("todo", "in_progress", "done", "cancelled")
VISIBILITIES = ("public", "private", "restricted")
MAX_PARTICIPANTS = 100
MAX_ASSIGNEES = 20
MAX_LINKS = 50
MAX_COMMITS = 250
MAX_TITLE = 1000
MAX_URL = 2048


class Refused(Exception):
    pass


class Unreadable(Exception):
    pass


class WriteFailed(Exception):
    pass


def safe(value):
    """A directory-name form of an id, as the seeding ledger spells it."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:120]


def _person(who, what, source, required=False):
    """{"login", "is_bot"?} for an author or an assignee; None when absent.
    The login is spelled as the brain keys it under `source`."""
    if who is None and not required:
        return None
    login = who.get("login") if isinstance(who, dict) else None
    if not isinstance(login, str) or not login.strip():
        raise Refused("%s has no login (the tracker's account id)" % what)
    try:
        person = {"login": wire_login(source, login)}
    except ValueError as err:
        raise Refused("%s: %s" % (what, err))
    if who.get("is_bot"):
        person["is_bot"] = True
    return person


def _text(raw, key, required=True):
    value = raw.get(key)
    if value is None or value == "":
        if required:
            raise Refused("the ticket has no %s" % key)
        return None
    if not isinstance(value, str):
        raise Refused("%s must be a string" % key)
    return value


def _list(raw, key, limit):
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise Refused("%s must be a list" % key)
    if len(value) > limit:
        raise Refused("%s holds %d entries, over the door's limit of %d; "
                      "they cannot be cut" % (key, len(value), limit))
    return value


def participants_of(author, assignees, comments, source):
    found = {}
    for who in [author] + list(assignees or []) + [
            c.get("author") for c in comments]:
        login = who.get("login") if isinstance(who, dict) else None
        if not isinstance(login, str) or not login.strip():
            continue
        if sanitize(login).strip().casefold() == GHOST_LOGIN:
            continue
        try:
            key = login_key(source, login)
            wire = wire_login(source, login)
        except ValueError as err:
            raise Refused("a participant's login: %s" % err)
        found.setdefault(key, (wire, bool(who.get("is_bot"))))
    people = [{"login": found[key][0], "is_bot": found[key][1]}
              for key in sorted(found)]
    if len(people) > MAX_PARTICIPANTS:
        raise Refused("the ticket has %d participants, over the door's cap of "
                      "%d; they cannot be cut without hiding a person from an "
                      "erasure" % (len(people), MAX_PARTICIPANTS))
    return people


def _comments(raw):
    found = raw.get("comments")
    if found is None:
        return []
    if not isinstance(found, list) or not all(isinstance(c, dict) for c in found):
        raise Refused("comments must be a list of objects")
    return found


def skeleton_of(raw, agent_accounts):
    """The skeleton and the reading file of one raw ticket. Raises Refused."""
    if not isinstance(raw, dict):
        raise Refused("the ticket must be a JSON object")
    try:
        source = source_of(_text(raw, "source"))
    except ValueError as err:
        raise Refused(str(err))
    ident = str(raw.get("id") if raw.get("id") is not None else "")
    if not ident:
        raise Refused("the ticket has no id")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise Refused("kind must be one of %s" % ", ".join(KINDS))
    category = raw.get("status_category")
    if category is None and source != "notion":
        raise Refused("status_category must be one of %s: only a Notion page "
                      "may send none, and take the brain's default"
                      % ", ".join(CATEGORIES))
    if category is not None and category not in CATEGORIES:
        raise Refused("status_category must be one of %s%s"
                      % (", ".join(CATEGORIES),
                         ", or null" if source == "notion" else ""))
    visibility = raw.get("visibility")
    if visibility is not None and visibility not in VISIBILITIES:
        raise Refused("visibility must be one of %s, or null"
                      % ", ".join(VISIBILITIES))
    title = _text(raw, "title")
    if len(title) > MAX_TITLE:
        raise Refused("the title is %d characters, over the door's limit of %d"
                      % (len(title), MAX_TITLE))
    url = _text(raw, "url", required=False)
    if url and len(url) > MAX_URL:
        raise Refused("the url is over the door's limit of %d characters"
                      % MAX_URL)
    author = _person(raw.get("author"), "the author", source, required=True)
    assignees = _list(raw, "assignees", MAX_ASSIGNEES)
    links = _list(raw, "pull_request_urls", MAX_LINKS)
    commits = _list(raw, "commit_shas", MAX_COMMITS)
    comments = _comments(raw)
    status = _text(raw, "status", required=False)
    item = {
        "source": source, "id": ident, "kind": kind, "title": title,
        "status_category": category, "author": author,
        "created_at": _text(raw, "created_at"),
        "participants": participants_of(
            raw.get("author"), assignees, comments, source),
        "source_version": source_version(
            title, raw.get("description"), status, comments, agent_accounts,
            source),
        "source_visibility": visibility,
    }
    for key, value in (("key", _text(raw, "key", required=False)),
                       ("url", url),
                       ("project_key", _text(raw, "project_key", required=False)),
                       ("type", _text(raw, "type", required=False)),
                       ("status", status),
                       ("closed_at", _text(raw, "closed_at", required=False))):
        if value is not None:
            item[key] = value
    if assignees:
        item["assignees"] = [_person(a, "an assignee", source, required=True)
                             for a in assignees]
    if raw.get("parent"):
        parent = raw["parent"]
        if not isinstance(parent, dict) or not parent.get("id"):
            raise Refused("parent must be an object with an id")
        item["parent"] = {k: parent[k] for k in ("id", "kind") if parent.get(k)}
    if links:
        item["pull_request_urls"] = list(links)
    if commits:
        item["commit_shas"] = list(commits)
    skeleton = {"agent_accounts": sorted(agent_accounts), "item": item}
    return skeleton, reading_of(item, raw.get("description"), comments)


def _who(comment, source):
    author = comment.get("author")
    login = author.get("login") if isinstance(author, dict) else None
    if not isinstance(login, str) or not login.strip():
        return GHOST_LOGIN
    try:
        login = wire_login(source, login)
    except ValueError:
        login = sanitize(login).strip()
    return token(login) + (" (bot)" if author.get("is_bot") or
                           login.casefold().endswith("[bot]") else "")


def reading_of(item, description, comments):
    """What the agent reads to write its summary. Local only, never sent. It
    has the shape of a pull request's reading file, so one `redact` serves
    both."""
    label = token(item.get("key") or item["id"])
    lines = ["# %s: %s" % (label, " ".join(item["title"].split())), "",
             "State: %s, author %s, opened %s" % (
                 token(item.get("status") or "(unknown)"),
                 token(item["author"]["login"]), token(item["created_at"])),
             "Tracker: %s %s%s" % (token(item["source"]), item["kind"],
                                   " (%s)" % " ".join(item["type"].split())
                                   if item.get("type")
                                   else ""),
             "", DESCRIPTION_HEADING, "",
             _quote(description) if (description or "").strip() else "(none)",
             "", CONVERSATION_HEADING, ""]
    ordered = sorted(comments, key=lambda c: (c.get("created_at") or "",
                                              str(c.get("id") or "")))
    if not ordered:
        lines += ["(none)", ""]
    for comment in ordered:
        lines += ["### %s, %s" % (
                      _who(comment, item["source"]), token(comment.get("created_at") or "?")),
                  _quote(comment.get("body")), ""]
    return "\n".join(lines)


def _write_card(out_dir, skeleton, reading):
    item = skeleton["item"]
    target = os.path.join(out_dir, "%s-%s" % (item["source"], safe(item["id"])))
    card, read = (os.path.join(target, "card.json"),
                  os.path.join(target, "read.md"))
    try:
        makedirs_ignored(target)
        _atomic_write_json(card, skeleton, prefix=".fm-ticket-", mode=0o644,
                           indent=2, sort_keys=True, trailing_newline=True)
        _atomic_write_text(read, reading)
    except OSError as err:
        raise WriteFailed("cannot write the card files under %s: %s"
                          % (target, err.strerror or err.__class__.__name__))
    return card


def _read_part(base, name, what):
    if not isinstance(name, str) or not name or os.path.isabs(name) \
            or ".." in name.replace("\\", "/").split("/"):
        raise Refused("%s must name a file inside the ticket's directory" % what)
    try:
        with open(os.path.join(base, name), encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeError):
        raise Refused("cannot read %s (%s)" % (what, name))


def assemble_one(base):
    """The raw ticket of one parts directory. Raises Refused."""
    try:
        with open(os.path.join(base, "ticket.json"), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        raise Refused("cannot read ticket.json")
    if not isinstance(raw, dict):
        raise Refused("ticket.json must be a JSON object")
    raw = dict(raw)
    for key in ("description", "body"):
        if key in raw:
            raise Refused("ticket.json must not carry %s: put the text in a "
                          "file and name it" % key)
    name = raw.pop("description_file", None)
    if name is not None:
        raw["description"] = _read_part(base, name, "the description")
    comments = []
    for comment in _comments(raw):
        comment = dict(comment)
        if "body" in comment:
            raise Refused("a comment carries body: put the text in a file and "
                          "name it as body_file")
        name = comment.pop("body_file", None)
        if name is None:
            raise Refused("a comment has no body_file: every comment names a "
                          "file, empty when the comment has no text")
        comment["body"] = _read_part(base, name, "a comment body")
        comments.append(comment)
    if "comments" in raw:
        raw["comments"] = comments
    return raw


def assemble(args):
    try:
        names = sorted(n for n in os.listdir(args.in_dir)
                       if os.path.isdir(os.path.join(args.in_dir, n)))
    except OSError:
        raise Unreadable("%s is not a directory" % args.in_dir)
    done, not_assembled = 0, []
    # A raw ticket whose parts directory is gone is a ticket of an earlier run:
    # shaped, it would be planned and written from text this run never read.
    try:
        for stale in os.listdir(args.out_dir):
            if stale.endswith(".json") and stale[:-5] not in names:
                try:
                    os.remove(os.path.join(args.out_dir, stale))
                except OSError:
                    pass
    except OSError:
        pass
    for name in names:
        base = os.path.join(args.in_dir, name)
        try:
            raw = assemble_one(base)
        except Refused as err:
            not_assembled.append({"dir": base, "reason": str(err)})
            # A raw ticket of an earlier run would be shaped as if it were
            # this one.
            try:
                os.remove(os.path.join(args.out_dir, name + ".json"))
            except OSError:
                pass
            continue
        try:
            makedirs_ignored(args.out_dir)
            _atomic_write_json(os.path.join(args.out_dir, name + ".json"), raw,
                               prefix=".fm-ticket-", mode=0o644, indent=2,
                               trailing_newline=True)
        except OSError as err:
            raise WriteFailed("cannot write under %s: %s" % (
                args.out_dir, err.strerror or err.__class__.__name__))
        done += 1
    return {"assembled": done, "not_assembled": not_assembled}


def shape(args):
    try:
        names = sorted(n for n in os.listdir(args.in_dir) if n.endswith(".json"))
    except OSError:
        raise Unreadable("%s is not a directory" % args.in_dir)
    tickets, not_shaped = [], []
    for name in names:
        path = os.path.join(args.in_dir, name)
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            skeleton, reading = skeleton_of(raw, args.agent_account)
        except (OSError, ValueError):
            not_shaped.append({"file": path,
                               "reason": "cannot read %s as JSON" % path})
            continue
        except Refused as err:
            not_shaped.append({"file": path, "reason": str(err)})
            continue
        item = skeleton["item"]
        row = {"source": item["source"], "id": item["id"], "title": item["title"],
               "source_version": item["source_version"],
               "card": _write_card(args.out_dir, skeleton, reading)}
        for key in ("key", "url"):
            if item.get(key):
                row[key] = item[key]
        tickets.append(row)
    index = args.index or os.path.join(args.out_dir, "shaped.json")
    try:
        makedirs_ignored(os.path.dirname(index) or ".")
        _atomic_write_json(index, tickets, prefix=".fm-ticket-", mode=0o644)
    except OSError as err:
        raise WriteFailed("cannot write %s: %s"
                          % (index, err.strerror or err.__class__.__name__))
    return {"shaped": len(tickets), "not_shaped": not_shaped, "index": index}


def redact(args):
    try:
        with open(args.read, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as err:
        raise Unreadable("cannot read %s: %s" % (args.read, err.strerror))
    before = redaction_record(args.read)
    text, removed, dropped = redact_reading(text, args.erased)
    try:
        _atomic_write_text(args.read, text)
        save_redaction_record(args.read, args.erased, record=before)
    except OSError as err:
        raise WriteFailed("cannot write %s: %s" % (args.read, err.strerror))
    return {"read": args.read, "comments_left_out": removed,
            "description_left_out": dropped}


def _parser():
    parser = argparse.ArgumentParser(prog="ticket_card.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("shape", allow_abbrev=False)
    one.add_argument("--in-dir", required=True)
    one.add_argument("--out-dir", required=True)
    one.add_argument("--agent-account", action="append", default=[])
    one.add_argument("--index")
    three = sub.add_parser("assemble", allow_abbrev=False)
    three.add_argument("--in-dir", required=True)
    three.add_argument("--out-dir", required=True)
    two = sub.add_parser("redact", allow_abbrev=False)
    two.add_argument("--read", required=True)
    two.add_argument("--erased", action="append", required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    try:
        document = {"shape": shape, "redact": redact,
                    "assemble": assemble}[args.command](args)
    except Unreadable as err:
        print("ticket_card: %s" % err, file=sys.stderr)
        return 2
    except WriteFailed as err:
        print("ticket_card: %s" % err, file=sys.stderr)
        return 5
    print(json.dumps(document, ensure_ascii=False))
    return 3 if document.get("not_shaped") or document.get("not_assembled") else 0


if __name__ == "__main__":
    sys.exit(main())
