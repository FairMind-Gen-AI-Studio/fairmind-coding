#!/usr/bin/env python3
"""Link the pull requests that cite a ticket to that ticket, and keep the links
in one fixed order.

    ticket_links.py match --tasks INVENTORY.json [--tasks MORE.json ...]
                          [--refs PR_REFS.json] --out TASKS.json
    ticket_links.py merge --tasks TASKS.json --dir CARDS_DIR
                          [--summary-out SUMMARY.json] [--alias OLD=NEW ...]

`match` reads the inventory rows (`{source, id, key?, title?, url?, updated?,
pull_request_urls?, ...}`; any number of `--tasks` files, their rows joined in
the order given, a `(source, id)` met twice kept once, the first) and, when
`--refs` is given, `pr-refs.json` (`ticket_refs.py freeze` writes it, schema
`brain-seed-pr-refs/1`: `pull_requests: [{repository, number, node_id, url,
tickets: [{source, id, key?}]}]`, in order). To each row it adds
`matched_pull_request_urls` (the canonical url of every pull request whose
`tickets` hold this task, in the order of the file, one per repository and
number). Without `--refs` every row gets it empty. A task is held by a ticket
when the `source` agrees without regard to case and the `id` is the same id
(`_ticket_identity.id_key`, the rule `ticket_refs.py` resolves by), so two
inventory rows that differ only so are one task. Nothing else is matched: a key
written in a title is not read here. A `--refs` path that does not exist is a
usage error, exit 2; a refs file of another schema, exit 3. Every other field
of a row is kept.

`merge` writes the links into each card skeleton (`<CARDS_DIR>/*/card.json`, the
`item` of `ticket_card.py shape`, found by its `source` and `id`). It draws them
from three places: the links the tracker itself carried (`item.pull_request_urls`,
never more than 50, because `shape` refuses more), the GitHub pull request
addresses (`https://github.com/<owner>/<name>/pull/<n>`) quoted in the card's
`read.md` (the lines of the description and of the comments, not the title or
the header), and the row's `matched_pull_request_urls`. Every address is
canonicalised and counted once per pull request; where several spellings of one
pull request meet, the smallest is kept. Each `--alias OLD=NEW` (both
`owner/name`, OLD without regard to case) spells a GitHub pull request of the
repository OLD under its current name NEW, so a link quoting a renamed or
transferred repository names the pull request that was imported; a malformed
alias is a usage error. The tracker's own links are always
kept. The scanned and matched ones fill the room that is left up to the door's
limit of 50, smallest first; what did not fit is reported in `overflow`. The
kept list is ordered by repository (without regard to case), then pull request
number, an address that is not a GitHub pull request after them in the order of
its spelling, so the order does not depend on the order the links arrived in. A
file is written only when its list changes, and only the item's
`pull_request_urls` key is touched. The links are not part of a ticket's
`source_version`, so they move nothing the brain compares. A card whose
`pull_request_urls` is present but not a list is left as it was and listed in
`errors`, whatever else is known about it. A `read.md` that cannot be read
counts for no scanned link and is listed in `errors`.

`merge --summary-out` also writes its summary to that file, for
`seed_ledger.py summary --extra`.

Exit codes: 0 ok; 2 usage or a missing input file; 3 an input that is not
readable JSON, an unknown refs schema, or any entry in the summary's `errors`
(the command still finishes everything else); 5 a file cannot be written. stdout
is one JSON document; diagnostics go to stderr. stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from _ticket_identity import id_key
from _pull_request_url import canonical_pull_request_url, pull_request_key
from ticket_refs import read_pr_refs

MAX_LINKS = 50

# A GitHub pull request address written in free text. The left guard keeps a
# longer word from lending its tail ("xhttps://..."); the number is read to its
# last digit and must not run into a letter or digit; what follows it (a path,
# a fragment, punctuation) is left to the canonical form.
_GITHUB_PULL = re.compile(
    r"(?<![A-Za-z0-9_])https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+"
    r"/[A-Za-z0-9_.-]+/pull/\d+(?![A-Za-z0-9])", re.IGNORECASE)
_CANONICAL_GITHUB_PULL = re.compile(
    r"^https://(?:www\.)?github\.com/([^/]+/[^/]+)/pull/(\d+)$", re.IGNORECASE)


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class Missing(Exception):
    pass


class Unreadable(Exception):
    pass


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise Missing("%s does not exist" % path)
    except (OSError, ValueError):
        raise Unreadable("cannot read %s as JSON" % path)


def _write_json(path, document, **layout):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-links-", mode=0o644,
                           **layout)
    except OSError as err:
        raise OSError("cannot write %s: %s"
                      % (path, err.strerror or err.__class__.__name__))


def _ident(obj):
    """The (source, id) a task is matched by; None when `obj` is not an object.
    The id is in the form equal ids share (`_ticket_identity.id_key`)."""
    if not isinstance(obj, dict):
        return None
    source = str(obj.get("source") or "").strip().casefold()
    return (source, id_key(source, obj.get("id")))


def _spelling(link, aliases=None):
    """(key, rank, spelling) of one address, or None when it is not text. A
    GitHub pull request is spelled `https://github.com/<owner>/<name>/pull/<n>`
    with `n` as a plain integer, so `pull/011` and `pull/11` are one pull request;
    any other address keeps the canonical form when there is one. `aliases` maps
    a repository's former `owner/name` (case folded) to its current one, so a
    link quoting the old name is spelled with the new."""
    if not isinstance(link, str) or not link.strip():
        return None
    canonical = canonical_pull_request_url(link)
    github = _CANONICAL_GITHUB_PULL.match(canonical) if canonical else None
    if github:
        repository, number = github.group(1), int(github.group(2))
        repository = (aliases or {}).get(repository.casefold(), repository)
        spelled = "https://github.com/%s/pull/%d" % (repository, number)
        return (spelled.lower(), (0, repository.casefold(), number, ""), spelled)
    spelled = canonical or link.strip()
    return ((pull_request_key(link) or spelled), (1, "", 0, spelled), spelled)


def ordered_links(links, aliases=None):
    """`links` once per pull request, in the fixed order: GitHub pull requests
    by (repository without regard to case, number), then every other address by
    its spelling. Of several spellings of one pull request the smallest stays.
    Entries that are not text are left out."""
    found = {}
    for link in links:
        one = _spelling(link, aliases)
        if one:
            key, rank, spelled = one
            if key not in found or (rank, spelled) < found[key]:
                found[key] = (rank, spelled)
    return [spelled for _, spelled in sorted(found.values())]


def _refs(path):
    try:
        document = read_pr_refs(path)
    except FileNotFoundError as err:
        raise Missing(str(err))
    except ValueError as err:
        raise Unreadable(str(err))
    if not isinstance(document.get("pull_requests"), list):
        raise Unreadable("the references %s hold no pull_requests list" % path)
    by_task = {}
    for pull in document["pull_requests"]:
        if not isinstance(pull, dict) or not isinstance(pull.get("tickets"), list):
            raise Unreadable("%s holds a pull request with no tickets list" % path)
        one = _spelling(pull.get("url"))
        if one is None:
            continue
        pull_key = (str(pull.get("repository")).casefold(), pull.get("number"))
        for ticket in pull["tickets"]:
            ident = _ident(ticket)
            if ident is None:
                raise Unreadable("%s holds a ticket that is not an object" % path)
            by_task.setdefault(ident, {}).setdefault(pull_key, one[2])
    return {ident: list(held.values()) for ident, held in by_task.items()}


def _aliases(args):
    """{former owner/name casefolded: current owner/name} from the repeated
    --alias OLD=NEW arguments."""
    found = {}
    for given in args.alias or []:
        old, _, new = given.partition("=")
        if not (_REPOSITORY.match(old) and _REPOSITORY.match(new)):
            raise Missing("--alias takes OLD=NEW, each an owner/name: %r" % given)
        found[old.casefold()] = new
    return found


def match(args):
    rows, seen = [], set()
    for path in args.tasks:
        tasks = _load(path)
        if not isinstance(tasks, list):
            raise Unreadable("the inventory %s must be a list" % path)
        for task in tasks:
            row = dict(task) if isinstance(task, dict) else {}
            ident = _ident(row)
            if isinstance(task, dict):
                if ident in seen:
                    continue
                seen.add(ident)
            rows.append(row)
    by_task = _refs(args.refs) if args.refs else {}
    total = 0
    for row in rows:
        row["matched_pull_request_urls"] = list(by_task.get(_ident(row), []))
        total += len(row["matched_pull_request_urls"])
    _write_json(args.out, rows)
    return {"tasks": len(rows), "links": total, "out": args.out}


def _quoted_addresses(path):
    """The pull request addresses on the quoted lines of a reading file (the
    description and the comments); (addresses, error text or None). A reading
    file that does not exist holds none and is no error."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return [], None
    except (OSError, UnicodeError):
        return [], "cannot read %s" % path
    return [m.group(0) for line in text.splitlines() if line.startswith(">")
            for m in _GITHUB_PULL.finditer(line)], None


def merge(args):
    aliases = _aliases(args)
    rows = _load(args.tasks)
    if not isinstance(rows, list):
        raise Unreadable("%s must hold a JSON list" % args.tasks)
    matched = {_ident(r): r.get("matched_pull_request_urls") or []
               for r in rows if isinstance(r, dict)}
    summary = {"updated": 0, "unchanged": 0, "overflow": [], "errors": []}
    try:
        names = sorted(n for n in os.listdir(args.dir) if os.path.isfile(
            os.path.join(args.dir, n, "card.json")))
    except OSError:
        raise Missing("%s is not a directory" % args.dir)
    for name in names:
        path = os.path.join(args.dir, name, "card.json")
        try:
            card = _load(path)
        except Unreadable as err:
            summary["errors"].append({"file": path, "reason": str(err)})
            continue
        item = card.get("item") if isinstance(card, dict) else None
        if not isinstance(item, dict):
            summary["errors"].append({"file": path,
                                      "reason": "%s holds no item" % path})
            continue
        present = item.get("pull_request_urls")
        if present is not None and not isinstance(present, list):
            summary["errors"].append({
                "file": path,
                "reason": "pull_request_urls is not a list; file left as it was"})
            continue
        read = os.path.join(args.dir, name, "read.md")
        scanned, failure = _quoted_addresses(read)
        if failure:
            summary["errors"].append({"file": read, "reason": failure})
        own = ordered_links(present or [], aliases)
        taken = {_spelling(u)[0] for u in own}
        extra = [u for u in ordered_links(
            scanned + list(matched.get(_ident(item)) or []), aliases)
                 if _spelling(u)[0] not in taken]
        room = max(MAX_LINKS - len(own), 0)
        kept, dropped = extra[:room], extra[room:]
        if dropped:
            summary["overflow"].append({"source": item.get("source"),
                                        "id": item.get("id"),
                                        "dropped": dropped})
        links = ordered_links(own + kept)
        if links and links != present:
            item["pull_request_urls"] = links
            _write_json(path, card)
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1
    return summary


def _parser():
    parser = argparse.ArgumentParser(prog="ticket_links.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("match", allow_abbrev=False)
    one.add_argument("--tasks", required=True, action="append")
    one.add_argument("--refs")
    one.add_argument("--out", required=True)
    two = sub.add_parser("merge", allow_abbrev=False)
    two.add_argument("--tasks", required=True)
    two.add_argument("--dir", required=True)
    two.add_argument("--summary-out")
    two.add_argument("--alias", action="append")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    try:
        document = (match if args.command == "match" else merge)(args)
    except Missing as err:
        print("ticket_links: %s" % err, file=sys.stderr)
        return 2
    except Unreadable as err:
        print("ticket_links: %s" % err, file=sys.stderr)
        return 3
    except OSError as err:
        print("ticket_links: %s" % err, file=sys.stderr)
        return 5
    try:
        if getattr(args, "summary_out", None):
            _write_json(args.summary_out, document, indent=2, sort_keys=True,
                        trailing_newline=True)
    except OSError as err:
        print("ticket_links: %s" % err, file=sys.stderr)
        return 5
    print(json.dumps(document, ensure_ascii=False))
    return 3 if document.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
