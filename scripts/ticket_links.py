#!/usr/bin/env python3
"""Link the pull requests that name a ticket's key to that ticket.

A pull request title, branch or description that says `WID-42` is the strongest
evidence there is that the pull request belongs to that ticket. The brain keeps
that link on the task, so it has to be on the task when it is sent.

    ticket_links.py match --tasks INVENTORY.json --collected COLLECTED.json
                          --out TASKS.json
    ticket_links.py merge --tasks TASKS.json --dir SHAPED_DIR

`match` reads the inventory rows (`{source, id, key?, title?, url?, updated?,
pull_request_urls?, ...}`) and the window report `pr_collect.py collect` wrote,
and adds `matched_pull_request_urls` (canonical, in pull request number order,
without repeats) and `matched_by` (`[{"number", "field"}]`, the field being
title, branch or description) to each row. A key matches as a whole token in
any case: `WID-42` is not in `WID-420` or `XWID-42`. A ClickUp task also
answers to `CU-<id>`, and to `#<id>` when its id has a letter; an Azure DevOps
work item answers to `AB#<id>`. A purely numeric `key` is not a token (a bare number
is in any `#314` or `line 314`). A task with no key and no such alias gets no
token, and no match: none is ever made up.

`merge` adds the matched links to each shaped task file in a directory (found
by its `source` and `id`), after the links the tracker itself carried, which
keep their order and spelling. A link already there, however spelled, is not
added again. At most 50 links stay, the door's limit, and what did not fit is
reported. A file is written only when it changes, and only its
`pull_request_urls` key is touched.

`match` reads each pull request's description from its first part file; one
that cannot be read counts as an empty description (title and branch still
match) and is listed in `errors`. `merge` leaves a file whose
`pull_request_urls` is present but not a list as it was, and lists it in
`errors`.

Exit codes: 0 ok; 2 usage or a missing input file; 3 an input that is not
readable JSON, or any entry in the summary's `errors` (the command still
finishes everything else, and `match` still writes its output); 5 a file cannot be
written. stdout is one JSON document; diagnostics go to stderr. stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from _pull_request_url import canonical_pull_request_url, pull_request_key

MAX_LINKS = 50
FIELDS = ("title", "branch", "description")


class Missing(Exception):
    pass


class Unreadable(Exception):
    pass


def tokens_for(task):
    source = str(task.get("source") or "").lower()
    tid = str(task.get("id") or "").strip()
    out = []
    key = str(task.get("key") or "").strip()
    if key and not key.isdigit():
        out.append(key)
    if source == "clickup" and tid:
        out.append("CU-" + tid)
        if re.search(r"[A-Za-z]", tid):
            out.append("#" + tid)
    if source == "azure_devops" and tid.isdigit():
        out.append("AB#" + tid)
    return list(dict.fromkeys(out))


def token_re(token):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(token)
                      + r"(?![A-Za-z0-9])", re.I)


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise Missing("%s does not exist" % path)
    except (OSError, ValueError):
        raise Unreadable("cannot read %s as JSON" % path)


def _write_json(path, document):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-links-", mode=0o644)
    except OSError as err:
        raise OSError("cannot write %s: %s"
                      % (path, err.strerror or err.__class__.__name__))


def _ident(obj):
    """The (source, id) a task is matched by; None when `obj` is not an object."""
    if not isinstance(obj, dict):
        return None
    return (str(obj.get("source") or "").lower(), str(obj.get("id")))


def _description(entry, errors):
    """The description of a pull request, from its first part file; empty,
    and reported in `errors`, when that file cannot be read."""
    files = entry.get("files") or []
    if not files:
        return ""
    try:
        part = _load(files[0])
    except (Missing, Unreadable) as err:
        errors.append({"file": files[0], "reason": str(err)})
        return ""
    pull = part.get("pull_request") if isinstance(part, dict) else None
    return str((pull or {}).get("description") or "") if isinstance(pull, dict) else ""


def _pulls(collected, errors):
    pulls = []
    for entry in collected.get("pull_requests") or []:
        pulls.append({
            "number": entry.get("number"),
            "url": canonical_pull_request_url(entry.get("url")) or entry.get("url"),
            "title": str(entry.get("title") or ""),
            "branch": str(entry.get("branch") or ""),
            "description": _description(entry, errors),
        })
    pulls.sort(key=lambda p: (p["number"] if isinstance(p["number"], int) else 0))
    return pulls


def match(args):
    tasks, collected = _load(args.tasks), _load(args.collected)
    if not isinstance(tasks, list) or not isinstance(collected, dict):
        raise Unreadable("the inventory must be a list and the report an object")
    errors = []
    pulls = _pulls(collected, errors)
    rows, total = [], 0
    for task in tasks:
        row = dict(task) if isinstance(task, dict) else {}
        patterns = [token_re(t) for t in tokens_for(row)]
        urls, by = [], []
        for pull in pulls:
            hits = [field for field in FIELDS
                    if any(p.search(pull[field]) for p in patterns)]
            by.extend({"number": pull["number"], "field": field}
                      for field in hits)
            if hits and pull["url"] not in urls:
                urls.append(pull["url"])
        row["matched_pull_request_urls"], row["matched_by"] = urls, by
        total += len(urls)
        rows.append(row)
    _write_json(args.out, rows)
    summary = {"tasks": len(rows), "pull_requests": len(pulls), "links": total,
               "out": args.out}
    if errors:
        summary["errors"] = errors
    return summary


def merge(args):
    rows = _load(args.tasks)
    if not isinstance(rows, list):
        raise Unreadable("%s must hold a JSON list" % args.tasks)
    matched = {_ident(r): r.get("matched_pull_request_urls") or []
               for r in rows if isinstance(r, dict)}
    summary = {"updated": 0, "unchanged": 0, "overflow": [], "errors": []}
    try:
        names = sorted(n for n in os.listdir(args.dir) if n.endswith(".json"))
    except OSError:
        raise Missing("%s is not a directory" % args.dir)
    for name in names:
        path = os.path.join(args.dir, name)
        try:
            item = _load(path)
        except Unreadable as err:
            summary["errors"].append({"file": path, "reason": str(err)})
            continue
        found = matched.get(_ident(item)) or []
        present = item.get("pull_request_urls") if found else None
        if present and not isinstance(present, list):
            summary["errors"].append({
                "file": path,
                "reason": "pull_request_urls is not a list; file left as it was"})
            continue
        links = list(present) if isinstance(present, list) else []
        known = {pull_request_key(u) for u in links if pull_request_key(u)}
        added = []
        for link in found:
            k = pull_request_key(link)
            if k and k not in known:
                known.add(k)
                added.append(link)
        room = max(MAX_LINKS - len(links), 0)
        kept, dropped = added[:room], added[room:]
        if dropped:
            summary["overflow"].append({"source": item.get("source"),
                                        "id": item.get("id"),
                                        "dropped": dropped})
        if kept:
            item["pull_request_urls"] = links + kept
            _write_json(path, item)
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ticket_links.py")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("match")
    one.add_argument("--tasks", required=True)
    one.add_argument("--collected", required=True)
    one.add_argument("--out", required=True)
    two = sub.add_parser("merge")
    two.add_argument("--tasks", required=True)
    two.add_argument("--dir", required=True)
    try:
        args = parser.parse_args(argv)
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
    print(json.dumps(document, ensure_ascii=False))
    return 3 if document.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
