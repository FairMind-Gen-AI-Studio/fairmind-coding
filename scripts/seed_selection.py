#!/usr/bin/env python3
"""Save the selection the person approved for a seed, and compare it with the
project's repositories as they are now.

    seed_selection.py save --project ID --repositories FILE [--repo OWNER/NAME ...]
                           [--no-repositories] [--undecided OWNER/NAME ...] [--keep-repositories]
                           --tracker NAME|none [--root ID ...] --days N
                           [--document KEY ...] [--file FILE]
    seed_selection.py show --project ID --repositories FILE [--file FILE]

`--repositories` is the `repositories.json` of `project_repos.py`; its `ok`
rows are the candidates. `save` stores, under the project, the chosen
repositories (`--repo`, matched to `repo` case-insensitively; all candidates
when absent) and the declined ones, both as `git_remote` in the file's order;
`--no-repositories` chooses none (every candidate is declined) and is a usage error
together with `--repo`. A repository that is not among the current candidates (it
was `unreadable` this run, or is no longer in the project) keeps its saved state,
chosen or declined, after the candidates' own, so a run that could not see it
never forgets it. `--undecided` (repeatable) leaves an `ok` repository in neither list, so `show` still
offers it as new; it is a usage error for a repository that is also a `--repo` or is not an
`ok` row, and an undecided repository is never chosen. What each combination saves for the
current candidates: no flag, all chosen; `--repo`, those chosen and the rest declined;
`--no-repositories`, none chosen and all declined except the `--undecided` ones;
`--undecided` alone, none chosen, the named ones in neither list and the rest declined.
`--keep-repositories` copies
`repositories` and `declined` from the saved entry unchanged (an `ok` repository in neither
stays undecided) and is a usage error with `--repo`, `--no-repositories` or `--undecided`, or
when nothing was saved. It also stores the tracker
(`none` stores null and takes no `--root`), its roots, the window in days and
the document keys as given, deduplicated. Other projects in
the file are not touched.

The file (default `.fairmind/brain-seed/selection.json`, schema
`brain-seed-selection/1`) is `{"schema", "projects": {<project>: {repositories,
declined, tracker, roots, days, documents}}}`. A file that does not parse, or
carries another schema, is never overwritten.

`show` prints `{saved, same_as_last_time, new_repositories, gone_repositories}`:
`saved` is the project's entry or null; `same_as_last_time` holds the `repo`
(`owner/name`) of every current `ok` repository the entry chose, in file order
(`[]` with no entry); `new_repositories` are current `ok` repositories the
entry neither chose nor declined (`{name, repo, git_remote}`); and
`gone_repositories` are chosen ones no longer among the current `ok` rows.
`git_remote` is compared case-insensitively. A missing file is not an error.

Exit codes: 0 ok, 2 usage, 3 unreadable input, 5 output not writable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json

SCHEMA = "brain-seed-selection/1"
DEFAULT_FILE = os.path.join(".fairmind", "brain-seed", "selection.json")
EXIT_USAGE, EXIT_UNREADABLE, EXIT_WRITE = 2, 3, 5


class Usage(Exception):
    pass


class Unreadable(Exception):
    pass


class WriteError(Exception):
    pass


def _write_json(path, document):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-selection-", mode=0o644,
                           indent=2, sort_keys=True, trailing_newline=True)
    except OSError as err:
        raise WriteError("cannot write %s: %s"
                         % (path, err.strerror or err.__class__.__name__))


def _load(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        raise Unreadable("cannot read %s %s as JSON" % (what, path))


def _read_selection(path):
    if not os.path.exists(path):
        return {"schema": SCHEMA, "projects": {}}
    document = _load(path, "the selection")
    if (not isinstance(document, dict) or document.get("schema") != SCHEMA
            or not isinstance(document.get("projects"), dict)):
        raise Unreadable("the selection %s is not a seed selection of this "
                         "plugin; it was left as it is" % path)
    return document


def _candidates(path):
    rows = _load(path, "the repositories")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise Unreadable("%s is not a list of repositories" % path)
    return [r for r in rows
            if r.get("status") == "ok" and isinstance(r.get("git_remote"), str)]


def _key(remote):
    return remote.lower()


def _positive(text):
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError("--days must be a positive integer")
    return value


def _unique(items):
    return list(dict.fromkeys(items))


def _names(names, known):
    """The lower-cased `owner/name` of each of `names`, every one an `ok`
    repository of the project."""
    for name in names or []:
        if name.lower() not in known:
            raise Usage("%s is not a repository of the project that can be "
                        "seeded" % name)
    return {name.lower() for name in names or []}


def _unseen(earlier, field, present):
    """The saved `git_remote`s of `field` that no current candidate has, in
    saved order: a repository this run could not see keeps its saved state."""
    return [r for r in (earlier or {}).get(field, [])
            if isinstance(r, str) and _key(r) not in present]


def save(args):
    candidates = _candidates(args.repositories)
    if args.tracker.lower() == "none":
        tracker = None
        if args.root:
            raise Usage("--root needs a tracker; it was given with --tracker none")
    else:
        tracker = args.tracker
    document = _read_selection(args.file)
    known = {str(c.get("repo", "")).lower() for c in candidates}
    undecided = _names(args.undecided, known)
    earlier = document["projects"].get(args.project)
    if not isinstance(earlier, dict):
        earlier = None
    if args.keep_repositories:
        if args.repo or args.no_repositories or args.undecided:
            raise Usage("--keep-repositories excludes --repo, --no-repositories and --undecided")
        if earlier is None:
            raise Usage("--keep-repositories needs a saved selection for %s" % args.project)
        repositories = _unseen(earlier, "repositories", set())
        declined = _unseen(earlier, "declined", set())
    else:
        if args.no_repositories:
            if args.repo:
                raise Usage("--no-repositories and --repo exclude each other")
            wanted = set()
        elif args.repo:
            wanted = _names(args.repo, known)
            if wanted & undecided:
                raise Usage("a repository is both --repo and --undecided")
        else:
            wanted = set() if undecided else None

        def picked(c):
            name = str(c.get("repo", "")).lower()
            return name not in undecided and (wanted is None or name in wanted)

        present = {_key(c["git_remote"]) for c in candidates}
        repositories = [c["git_remote"] for c in candidates if picked(c)] \
            + _unseen(earlier, "repositories", present)
        declined = [c["git_remote"] for c in candidates if not picked(c)
                    and str(c.get("repo", "")).lower() not in undecided] \
            + _unseen(earlier, "declined", present)
    entry = {
        "repositories": repositories,
        "declined": declined,
        "tracker": tracker,
        "roots": _unique(args.root or []),
        "days": args.days,
        "documents": _unique(args.document or []),
    }
    document["projects"][args.project] = entry
    _write_json(args.file, document)
    return {"file": args.file, "project": args.project, "saved": entry}


def show(args):
    candidates = _candidates(args.repositories)
    saved = _read_selection(args.file)["projects"].get(args.project)
    if not isinstance(saved, dict):
        saved = None
    chosen = [r for r in (saved or {}).get("repositories", []) if isinstance(r, str)]
    declined = [r for r in (saved or {}).get("declined", []) if isinstance(r, str)]
    known = {_key(r) for r in chosen + declined}
    present = {_key(c["git_remote"]) for c in candidates}
    return {
        "saved": saved,
        "same_as_last_time": [
            c.get("repo") for c in candidates
            if _key(c["git_remote"]) in {_key(r) for r in chosen}],
        "new_repositories": [
            {"name": c.get("name"), "repo": c.get("repo"),
             "git_remote": c["git_remote"]}
            for c in candidates if _key(c["git_remote"]) not in known],
        "gone_repositories": [r for r in chosen if _key(r) not in present],
    }


def _parser():
    parser = argparse.ArgumentParser(prog="seed_selection.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("save", "show"):
        one = sub.add_parser(name, allow_abbrev=False)
        one.add_argument("--project", required=True)
        one.add_argument("--repositories", required=True)
        one.add_argument("--file", default=DEFAULT_FILE)
        if name == "save":
            one.add_argument("--repo", action="append")
            one.add_argument("--no-repositories", action="store_true")
            one.add_argument("--undecided", action="append")
            one.add_argument("--keep-repositories", action="store_true")
            one.add_argument("--tracker", required=True)
            one.add_argument("--root", action="append")
            one.add_argument("--days", required=True, type=_positive)
            one.add_argument("--document", action="append")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    try:
        document = save(args) if args.command == "save" else show(args)
    except Usage as err:
        print("seed_selection: %s" % err, file=sys.stderr)
        return EXIT_USAGE
    except Unreadable as err:
        print("seed_selection: %s" % err, file=sys.stderr)
        return EXIT_UNREADABLE
    except WriteError as err:
        print("seed_selection: %s" % err, file=sys.stderr)
        return EXIT_WRITE
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
