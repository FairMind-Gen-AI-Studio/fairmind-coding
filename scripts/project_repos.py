#!/usr/bin/env python3
"""List the repositories a FairMind project is associated with.

The session asks the MCP tool `Code_list_repositories(project=...)` and saves
its markdown answer verbatim; this script turns that file into the list the
seed works from:

    project_repos.py build --answer FILE --out FILE [--no-access-check]

The answer is a numbered list; each entry has `ID:` and `URL:` lines and an
optional `Description:`. The catalog holds one row per branch of a repository,
so rows that share a canonical URL are one repository here, with every id kept
in `catalog_ids`. Nothing about the checkout the skill runs in enters the
output: the same project gives the same file from any checkout.

`--out` is a JSON list, one row per distinct repository in the answer's order:
`{name, repo, url, git_remote, repository, catalog_ids, status, detail?,
source_visibility}`.

  * `url` is canonical: `https`, lowercase host, no `.git`, no trailing `/`, no
    `user:token@`; listings of one non-GitHub repository over `http` and `https`
    are one row. `git_remote` and `repo` (`owner/name`) are set for GitHub.
  * `repository` is what the brain's doors receive: the catalog id when the
    repository has one, its name when it has several (the server then resolves
    the name and prefers the default branch). A staged card is bound to this
    value, so it is never derived from anything that can change between runs.
  * A repository with several rows binds by name unless its name is shared
    with another repository of the project, or its rows carry different names
    (then it is `unreadable`).
  * `status` is `ok`, `not_github` (the URL is on another host, or is a
    github.com URL that is not a repository URL) or `unreadable` (no id or
    url in the listing, an id that is not 24 lowercase hex characters, a name
    that two repositories of the project share and this one needs to be bound
    by, or, unless `--no-access-check`, a `gh repo view` that
    failed; `detail` says why).
  * `source_visibility` comes from that same `gh repo view`: `public`,
    `private`, or `restricted` for `internal`; any other value is null, never
    guessed. It is null without the check, and on a row that did not pass it.

Each `ok` row costs one `gh repo view`; `gh` missing exits 127 before any row is
checked. stdout is one JSON document: `{out, repositories, ok, skipped}`.

Exit codes: 0 ok; 2 usage; 3 an answer that cannot be read or holds no entry;
5 the output cannot be written; 127 `gh` not on PATH. stdlib only.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from _binding import is_object_id
from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from audit_run_meta import normalize_git_remote
from pr_collect import parse_github_remote, public_remote, visibility_of

EXIT_USAGE, EXIT_UNREADABLE, EXIT_WRITE, EXIT_NO_GH = 2, 3, 5, 127

_ENTRY = re.compile(r"^\s*\d+\.\s+(\S.*?)\s*$")
_FIELD = re.compile(r"^\s+(ID|URL|Description):\s*(.*?)\s*$")

NO_LISTING = "no id or url in the listing"
BAD_ID = "catalog id is not a 24-character hex id"


class GhMissing(Exception):
    pass


class Unreadable(Exception):
    pass


class WriteError(Exception):
    pass


def run_gh(args):
    """The one subprocess seam: run `gh`, return (exit code, stdout, stderr)."""
    gh = shutil.which("gh")
    if gh is None:
        raise GhMissing("the GitHub CLI (gh) is not on PATH")
    try:
        done = subprocess.run([gh] + list(args), capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
    except subprocess.TimeoutExpired:
        return 1, "", "gh timed out"
    return done.returncode, done.stdout or "", done.stderr or ""


def parse_answer(text):
    """The numbered entries of the listing: `{name, id, url}`, None when absent."""
    entries, current = [], None
    for line in text.splitlines():
        head = _ENTRY.match(line)
        if head:
            current = {"name": head.group(1), "id": None, "url": None}
            entries.append(current)
            continue
        field = _FIELD.match(line)
        if field and current is not None and field.group(1) != "Description":
            current[field.group(1).lower()] = field.group(2) or None
    return entries


_GITHUB_HOSTS = ("github.com", "www.github.com")


def canonical_url(url):
    """(canonical url, 'owner/name' or None, looks_like_github); https, lowercase
    host, no credentials, no `.git`, no trailing slash. An scp-form remote
    (`git@github.com:acme/a.git`) is read like the https one."""
    clean = public_remote(url)
    try:
        clean = normalize_git_remote(clean) or clean
        host = urlsplit(clean).netloc
    except ValueError:
        return clean.rstrip("/"), None, False
    repo = parse_github_remote(clean)
    if repo is not None:
        return "https://github.com/%s" % repo, repo, True
    return clean, None, host in _GITHUB_HOSTS


def _row(name, status, detail=None, **fields):
    row = {"name": name, "repo": None, "url": None, "git_remote": None,
           "repository": None, "catalog_ids": [], "status": status,
           "source_visibility": None}
    row.update(fields)
    if detail:
        row["detail"] = detail
    return row


def _id_problem(entry_id):
    if not entry_id or entry_id.lower() == "unknown":
        return NO_LISTING
    return None if is_object_id(entry_id) else BAD_ID


def rows_of(entries):
    """One row per distinct repository. Entries are grouped by canonical URL
    BEFORE their ids are judged, so a mistyped sibling id cannot change the
    value a repository is bound by."""
    slots, groups = [], {}
    for entry in entries:
        if not entry["url"]:
            slots.append(_row(entry["name"], "unreadable", NO_LISTING))
            continue
        url, repo, github_like = canonical_url(entry["url"])
        key = url.lower()
        if key not in groups:
            groups[key] = {"name": entry["name"], "url": url, "repo": repo,
                           "github_like": github_like, "entries": []}
            slots.append(groups[key])
        groups[key]["entries"].append(entry)
    built = [_group_row(slot) if "entries" in slot else (slot, False)
             for slot in slots]
    shared = collections.Counter(row["name"] for row, _ in built)
    for row, by_name in built:
        if by_name and shared[row["name"]] > 1:
            row.update(status="unreadable", repository=None, detail=(
                "two repositories in the project share the name %s; the brain "
                "cannot tell them apart by name" % row["name"]))
    return [row for row, _ in built]


def _group_row(group):
    """(row, bound by name) of one repository's entries."""
    problems = [(item["id"], _id_problem(item["id"])) for item in group["entries"]]
    valid = list(dict.fromkeys(i for i, problem in problems if problem is None))
    invalid = sum(1 for _, problem in problems if problem)
    rows_in_group = len(valid) + invalid
    status = "ok" if group["repo"] else "not_github"
    fields = dict(url=group["url"], repo=group["repo"],
                  git_remote=group["url"] if group["repo"] else None,
                  catalog_ids=valid)
    if rows_in_group == 1:
        if problems[0][1]:
            return _row(group["name"], "unreadable", problems[0][1], **fields), False
        detail = None
    else:
        detail = ("%d catalog id(s) in the listing are unreadable" % invalid
                  if invalid and status == "ok" else None)
    if status == "not_github" and group["github_like"]:
        detail = "not a github.com repository URL"
    names = list(dict.fromkeys(item["name"] for item in group["entries"]))
    if rows_in_group > 1 and len(names) > 1:
        return _row(group["name"], "unreadable", (
            "the catalog lists this repository under different names (%s); "
            "the brain cannot bind it by name" % ", ".join(names)), **fields), False
    row = _row(group["name"], status, detail, **fields)
    row["repository"] = valid[0] if rows_in_group == 1 else group["name"]
    return row, rows_in_group > 1


def check_access(rows):
    for row in rows:
        if row["status"] != "ok":
            continue
        code, out, err = run_gh(["repo", "view", row["repo"], "--json",
                                 "nameWithOwner,visibility"])
        if code != 0:
            lines = [l.strip() for l in (err or out).splitlines() if l.strip()]
            row["status"] = "unreadable"
            row["detail"] = lines[0] if lines else "gh repo view exited %d" % code
            continue
        try:
            body = json.loads(out)
        except ValueError:
            body = None
        row["source_visibility"] = visibility_of(
            body.get("visibility") if isinstance(body, dict) else None)


def _write_json(path, document):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-repos-", mode=0o644,
                           indent=2, sort_keys=True, trailing_newline=True)
    except OSError as err:
        raise WriteError("cannot write %s: %s"
                         % (path, err.strerror or err.__class__.__name__))


def build(args):
    try:
        with open(args.answer, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as err:
        raise Unreadable("cannot read %s: %s" % (
            args.answer, getattr(err, "strerror", None) or err.__class__.__name__))
    entries = parse_answer(text)
    if not entries:
        first = next((l.strip() for l in text.splitlines() if l.strip()), "")
        if first.lower().startswith("project repositories"):
            first = ""
        raise Unreadable(first or "the answer lists no repository")
    rows = rows_of(entries)
    if not args.no_access_check:
        check_access(rows)
    _write_json(args.out, rows)
    return {"out": args.out, "repositories": len(rows),
            "ok": sum(1 for r in rows if r["status"] == "ok"),
            "skipped": [{"name": r["name"], "status": r["status"],
                         "detail": r.get("detail")}
                        for r in rows if r["status"] != "ok"]}


def _parser():
    parser = argparse.ArgumentParser(prog="project_repos.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("build", allow_abbrev=False)
    one.add_argument("--answer", required=True)
    one.add_argument("--out", required=True)
    one.add_argument("--no-access-check", action="store_true")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    try:
        document = build(args)
    except GhMissing as err:
        print("project_repos: %s" % err, file=sys.stderr)
        return EXIT_NO_GH
    except Unreadable as err:
        print("project_repos: %s" % err, file=sys.stderr)
        return EXIT_UNREADABLE
    except WriteError as err:
        print("project_repos: %s" % err, file=sys.stderr)
        return EXIT_WRITE
    print(json.dumps(document, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
