#!/usr/bin/env python3
"""List, pick and read the documents kept in a project's GitHub repositories.

    repo_docs.py list --repositories FILE --out FILE [--repo OWNER/NAME ...]
                      [--under DIR ...] [--ext EXT ...]
    repo_docs.py pick --in FILE --index N [--index N ...] --out FILE
    repo_docs.py read --repo OWNER/NAME --path PATH --commit SHA --out FILE

`list` takes the `repositories.json` of `project_repos.py` and, for every `ok`
repository, asks GitHub for its default branch, the head commit of that branch
and the recursive tree at that commit. It keeps the files that sit under one of
the `--under` folders (default `docs/ specs/ adr/ design/`; a folder matches at
the repository root only, as a path prefix) and whose extension is in `--ext`
(default `.md .markdown .html .txt .rst .adoc`, compared case-insensitively).
`--repo` (repeatable, compared case-insensitively) limits the listing to those
`ok` rows; a name that is not an `ok` row exits 2.

`--out` is a JSON list, ordered by repository (file order) and then path; each
row is the entry of a file kept in a repository, which `seed_ledger.py plan
--documents` takes as it is; before `artifact_cards.py args` the session adds
`agent`, `project` and, for the write form, `title` and `summary`:
`{git: true, repo, repository, git_remote, path, commit, branch, blob_sha,
source_visibility, title}`. `repository`, `git_remote` and `source_visibility`
come from the repositories row, `commit` is the head sha, `blob_sha` the tree
entry's, `title` the file name without its extension.

A truncated tree is still listed and its repository reported in `truncated`; a
repository whose calls fail is reported in `failed` (gh's first stderr line)
and the others are still listed (a reply without its `default_branch`, `sha`
or `tree` is a failure; an empty tree lists zero files; symlinks are skipped).
stdout is one JSON document:
`{out, documents, by_repository, truncated, failed}`.

`pick` copies the rows of a listing at the given 1-based indexes, unchanged and
in the order given: a model never types a row, the ticked files become a file
here. No index at all is a valid answer and writes `[]`; an index out of range
or repeated exits 2.

`read` checks `--repo`, `--commit` and `--path` first (exit 2 naming the
argument), removes `--out` so a failed read never leaves an earlier copy, then writes
one file's raw bytes, at the given commit, to `--out`. It is what the summariser
reads; it is never sent to the brain.

Exit codes: 0 ok; 2 usage; 3 unreadable input (or a file that cannot be read);
5 the output cannot be written; 127 `gh` not on PATH. stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
import urllib.parse

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json

EXIT_USAGE, EXIT_UNREADABLE, EXIT_WRITE, EXIT_NO_GH = 2, 3, 5, 127

DEFAULT_UNDER = ("docs/", "specs/", "adr/", "design/")
DEFAULT_EXT = (".md", ".markdown", ".html", ".txt", ".rst", ".adoc")


class GhMissing(Exception):
    pass


class Usage(Exception):
    pass


class Unreadable(Exception):
    pass


class WriteError(Exception):
    pass


def run_gh(args):
    """The one subprocess seam: run `gh`, return (exit code, stdout bytes,
    stderr text). Bytes, so a file is never re-encoded on its way out."""
    gh = shutil.which("gh")
    if gh is None:
        raise GhMissing("the GitHub CLI (gh) is not on PATH")
    try:
        done = subprocess.run([gh] + list(args), capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return 1, b"", "gh timed out"
    return (done.returncode, done.stdout or b"",
            (done.stderr or b"").decode("utf-8", "replace"))


def _first_line(err, out, code):
    for text in (err, out.decode("utf-8", "replace")):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            return lines[0]
    return "gh api exited %d" % code


class ApiFailed(Exception):
    pass


def _api(path, key):
    """One `gh api` call, its JSON object; `key` must be in it."""
    code, out, err = run_gh(["api", path])
    if code != 0:
        raise ApiFailed(_first_line(err, out, code))
    try:
        body = json.loads(out.decode("utf-8"))
    except ValueError:
        body = None
    value = body.get(key) if isinstance(body, dict) else None
    # An empty tree is a repository with no files; an empty name is no answer.
    if not (isinstance(value, list) if key == "tree" else isinstance(value, str) and value):
        raise ApiFailed("GitHub's answer for %s has no %s" % (path, key))
    return body


def _folders(under):
    return tuple(u if u.endswith("/") else u + "/" for u in (u.lstrip("/") for u in under))


def _extensions(ext):
    return tuple(e.lower() if e.startswith(".") else "." + e.lower() for e in ext)


def _wanted(path, folders, extensions):
    return (path.startswith(folders)
            and posixpath.splitext(path)[1].lower() in extensions)


def list_repository(row, folders, extensions):
    """(rows, truncated) of one `ok` repository; raises ApiFailed."""
    repo = row["repo"]
    branch = _api("repos/%s" % repo, "default_branch")["default_branch"]
    commit = _api("repos/%s/commits/%s" % (repo, urllib.parse.quote(branch, safe="/")),
                  "sha")["sha"]
    tree = _api("repos/%s/git/trees/%s?recursive=1" % (repo, commit), "tree")
    rows = []
    for entry in tree["tree"]:
        path = entry.get("path") if isinstance(entry, dict) else None
        # A symlink's blob is the link text, not a document.
        if (not isinstance(path, str) or entry.get("type") != "blob"
                or entry.get("mode") == "120000"
                or not isinstance(entry.get("sha"), str)
                or not _wanted(path, folders, extensions)):
            continue
        rows.append({
            "git": True, "repo": repo, "repository": row.get("repository"),
            "git_remote": row.get("git_remote"), "path": path, "commit": commit,
            "branch": branch, "blob_sha": entry["sha"],
            "source_visibility": row.get("source_visibility"),
            "title": posixpath.splitext(posixpath.basename(path))[0]})
    rows.sort(key=lambda r: r["path"])
    return rows, bool(tree.get("truncated"))


def _read_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        raise Unreadable("cannot read %s %s as JSON" % (what, path))


def _write_json(path, document):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-repo-docs-", mode=0o644,
                           indent=2, sort_keys=True, trailing_newline=True)
    except OSError as err:
        raise WriteError("cannot write %s: %s"
                         % (path, err.strerror or err.__class__.__name__))


def list_documents(args):
    repositories = _read_json(args.repositories, "the repositories")
    if not isinstance(repositories, list) or not all(
            isinstance(r, dict) for r in repositories):
        raise Unreadable("the repositories %s must be a JSON list of objects"
                         % args.repositories)
    folders = _folders(args.under or DEFAULT_UNDER)
    extensions = _extensions(args.ext or DEFAULT_EXT)
    eligible = [r for r in repositories if r.get("status") == "ok"
                and isinstance(r.get("repo"), str) and r["repo"]]
    ok = {r["repo"].lower() for r in eligible}
    wanted = {name.lower() for name in args.repo or []}
    for name in args.repo or []:
        if name.lower() not in ok:
            raise Usage("%s is not an ok repository of %s" % (name, args.repositories))
    documents, by_repository, truncated, failed = [], {}, [], []
    for row in eligible:
        repo = row["repo"]
        if wanted and repo.lower() not in wanted:
            continue
        if not _is_repo_slug(repo):
            failed.append({"repo": repo, "detail": "not an OWNER/NAME repository"})
            continue
        try:
            rows, cut = list_repository(row, folders, extensions)
        except ApiFailed as err:
            failed.append({"repo": repo, "detail": str(err)})
            continue
        documents.extend(rows)
        by_repository[repo] = len(rows)
        if cut:
            truncated.append(repo)
    _write_json(args.out, documents)
    return {"out": args.out, "documents": len(documents),
            "by_repository": by_repository, "truncated": truncated,
            "failed": failed}


def pick(args):
    rows = _read_json(args.input, "the listing")
    if not isinstance(rows, list):
        raise Unreadable("the listing %s must be a JSON list" % args.input)
    indexes = args.index or []
    if len(set(indexes)) != len(indexes):
        raise Usage("an index is repeated")
    for index in indexes:
        if not 1 <= index <= len(rows):
            raise Usage("index %d is out of range (1 to %d)" % (index, len(rows)))
    _write_json(args.out, [rows[i - 1] for i in indexes])
    return {"out": args.out, "picked": len(indexes)}


_HEX = frozenset("0123456789abcdef")


_REPO_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def _is_repo_slug(repo):
    """OWNER/NAME in the characters GitHub allows; anything else (`?`, `#`, `%`,
    whitespace) would end or rewrite the API path it is interpolated into."""
    owner, _, name = repo.partition("/")
    return all(part and part not in (".", "..") and set(part) <= _REPO_CHARS
               for part in (owner, name))


def _checked_location(args):
    """Refuse an argument that would reach a different URL than it names."""
    if not _is_repo_slug(args.repo):
        raise Usage("--repo must be OWNER/NAME")
    if len(args.commit) not in (40, 64) or not set(args.commit) <= _HEX:
        raise Usage("--commit must be 40 or 64 lowercase hex characters")
    if args.path.startswith("/") or any(
            part in ("", ".", "..") for part in args.path.split("/")):
        raise Usage("--path must be a relative path with no empty, . or .. segment")


def read(args):
    _checked_location(args)
    try:
        os.remove(args.out)
    except FileNotFoundError:
        pass
    except OSError as err:
        raise WriteError("cannot write %s: %s"
                         % (args.out, err.strerror or err.__class__.__name__))
    path = urllib.parse.quote(args.path, safe="/")
    code, out, err = run_gh([
        "api", "repos/%s/contents/%s?ref=%s" % (args.repo, path, args.commit),
        "-H", "Accept: application/vnd.github.raw"])
    if code != 0:
        raise Unreadable(_first_line(err, b"", code))
    try:
        makedirs_ignored(os.path.dirname(args.out) or ".")
        with open(args.out, "wb") as handle:
            handle.write(out)
    except OSError as err:
        raise WriteError("cannot write %s: %s"
                         % (args.out, err.strerror or err.__class__.__name__))
    return {"out": args.out, "bytes": len(out)}


def _parser():
    parser = argparse.ArgumentParser(prog="repo_docs.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("list", allow_abbrev=False)
    one.add_argument("--repositories", required=True)
    one.add_argument("--out", required=True)
    one.add_argument("--repo", action="append")
    one.add_argument("--under", action="append")
    one.add_argument("--ext", action="append")
    two = sub.add_parser("pick", allow_abbrev=False)
    two.add_argument("--in", dest="input", required=True)
    two.add_argument("--index", type=int, action="append")
    two.add_argument("--out", required=True)
    three = sub.add_parser("read", allow_abbrev=False)
    three.add_argument("--repo", required=True)
    three.add_argument("--path", required=True)
    three.add_argument("--commit", required=True)
    three.add_argument("--out", required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    handler = {"list": list_documents, "pick": pick, "read": read}[args.command]
    try:
        document = handler(args)
    except GhMissing as err:
        print("repo_docs: %s" % err, file=sys.stderr)
        return EXIT_NO_GH
    except Usage as err:
        print("repo_docs: %s" % err, file=sys.stderr)
        return EXIT_USAGE
    except Unreadable as err:
        print("repo_docs: %s" % err, file=sys.stderr)
        return EXIT_UNREADABLE
    except WriteError as err:
        print("repo_docs: %s" % err, file=sys.stderr)
        return EXIT_WRITE
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
