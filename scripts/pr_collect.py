#!/usr/bin/env python3
"""Collect a repository's pull requests for the brain import.

Reads a pull request and its review conversations through the person's own
`gh` credential, using read-only GraphQL queries, and writes the arguments of
the `Brain_import_pull_request` calls that bring it into the company brain.
Nothing is posted to GitHub and nothing is sent to the platform from here.

    pr_collect.py list    [--repo OWNER/NAME] [--states MERGED,CLOSED]
                          [--since YYYY-MM-DD | --days N] [--limit N]
                          [--out FILE]
    pr_collect.py collect [--repo OWNER/NAME] --out-dir DIR [--max-chars N]
                          (--number N [--allow-open]
                           | --since YYYY-MM-DD | --days N)
                          [--max-seconds N] [--resume]
    pr_collect.py remote

`remote` prints `{"repository", "git_remote"}`: the origin remote as the
import door may receive it, with any `user:token@` in front of the host
removed. `list` prints a JSON array, newest first. `collect` writes one file per call
under `DIR/<number>/part-<k>.json` (the `part-<k>.json` files already in that
directory are removed first, so a stale part can never be sent; nothing else in it is touched) and prints a JSON report. Without `--repo` the
repository is read from the `origin` remote, which must be on github.com.

Window mode. `--days N` names the window that ends today (N days back), as
`--since` names its first day; the two are exclusive. `list` over a window with
no `--limit` returns the whole window (without a window the limit defaults to
50), and `--out FILE` writes the array to a file and prints only its count.
`collect --since/--days` collects every merged or closed pull request of the
window, one `DIR/<number>/` each, and keeps one `DIR/collected.json`:
`{"since", "repository", "complete", "pull_requests": [one report each],
"not_collected": [{"number", "exit", "reason"}]}`. A pull request that fails is recorded there
and the run goes on. A long window may not fit one tool call, so the collect
works within `--max-seconds`
(default 420, always at least one pull request per call), rewrites
`collected.json` after every pull request, says `"complete": false` when it
stopped early, and `--resume` continues from that file without collecting a
pull request twice. `--resume` continues only an unfinished collect of the same
repository and window: a finished one, or one of another repository or window,
is started over, so a later run sees the pull requests as they are now. With
`--days`, an unfinished collect that began the day before keeps its first day,
so a collect that straddles UTC midnight does not start over. A `gh` failure for one pull request
is retried on later calls (three attempts in all, counted only when the pull
request is actually tried and fails on its own account) before it is settled in `not_collected`; a failure
every call would share (a login that expired, a rate limit) ends the run with
exit 3 (127 when `gh` itself is gone), the report left unfinished, and costs the pull request in flight no attempt. A 403 on one pull request is that pull
request's failure, not the run's. The time budget
counts every pull request tried, failed or not. The exit codes of a window
collect cover the run itself.

Parts. The agent re-emits each file as tool arguments, so one call is kept
under a text budget (`--max-chars`, 60,000 by default) far below the door's
own cap. Nothing is cut to meet it: bodies are never shortened, reordered or
re-spelled, and a pull request that does not fit is split. Every part repeats
the pull request itself (the card is rewritten by each call, so its turns
cannot be split) with a disjoint, ordered slice of the conversations, and
EVERY part, the last included, carries the real number of conversations as
`conversation_count`. A part sent without it would read as a complete listing
and retire every conversation the other parts wrote. The trades: a split pull
request never retires a conversation deleted on GitHub, its card keeps the
partial-import notice, a re-import writes again, and each part spends one unit
of the tenant's daily brain write budget. Pull-request-level turns are capped
at 99 by the door and cannot be split; the report says when some stayed out.

Changed files. Every part carries `pull_request.files`: the paths the pull
request changed with their added and deleted line counts, in path order (Python
string order), without duplicates, at most 300. A path over 1,024 characters,
or not already canonical and repo-relative (surrounding whitespace, a control
character, a backslash, a leading `/`, an empty, `.` or `..` segment), is left
out (never cut or rewritten, because that names another file, and the door
would refuse the whole import). When fewer files
were sent than the real number, `pull_request.file_count` carries the real
number (the largest of GitHub's own counts and the listing). The list repeats
in every part the agent re-emits, so its size is charged to the part budget;
the door does not count it as text, so it is left out of the door-counted
totals.

Merge commit. A merged pull request carries `pull_request.merge_commit_sha`, the
commit its file anchors name; a squash merge leaves the head commit off the
default branch.

Logins. Spelling is identity: a project admin erases a person's conversations
by login. A bot account is spelled `name[bot]` (once), and a deleted account
is spelled `ghost`.

`list --since` pages by last update, newest first, and stops at the first pull
request not updated since the date: a merge or a close always bumps the update
time, so nothing merged or closed after the date is missed. The date filter
itself is on the merge or close time (the creation time for an open one).

Exit codes: 0 ok; 2 usage, unsupported remote, an open pull request without
`--allow-open`, or a pull request that does not exist; 3 `gh` failed (its
message is relayed); 4 the pull request alone cannot fit one call; 5 the part
files cannot be written (the one-line message names the path); 127 `gh` not on
PATH.

stdout carries only the JSON document; diagnostics go to stderr. stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json

DEFAULT_MAX_CHARS = 60_000
MAX_COMMIT_SHAS = 250
MAX_TITLE_CHARS = 1000
MAX_URL_CHARS = 2048
MAX_PATH_CHARS = 1024
MAX_FILES = 300
SERVER_MAX_CHARS = 2_000_000
MAX_CONVERSATIONS = 200
MAX_THREAD_TURNS = 100
MAX_PR_TURNS = 99
MAX_ID_CHARS = 200
MAX_REPOSITORY_CHARS = 200
MAX_SOURCE_CHARS = 50
GHOST_LOGIN = "ghost"
STATES = ("OPEN", "MERGED", "CLOSED")
EXIT_USAGE, EXIT_GH, EXIT_TOO_LARGE, EXIT_NO_GH = 2, 3, 4, 127
EXIT_WRITE = 5
MAX_ATTEMPTS = 3
DEFAULT_MAX_SECONDS = 420
MAX_WINDOW_DAYS = 366

_PAGE = "pageInfo{hasNextPage endCursor}"
_AUTHOR = "author{__typename login}"

LIST_QUERY_TEMPLATE = (
    "query($owner:String!,$name:String!,$after:String){"
    "repository(owner:$owner,name:$name){"
    "pullRequests(first:50,after:$after,states: [%STATES%],"
    "orderBy:{field:UPDATED_AT,direction:DESC}){"
    + _PAGE
    + " nodes{id number title state url createdAt updatedAt mergedAt closedAt}}}}"
)
PR_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){"
    "repository(owner:$owner,name:$name){nameWithOwner "
    "pullRequest(number:$number){id number url title body state mergedAt "
    "createdAt changedFiles headRefName mergeCommit{oid} " + _AUTHOR + "}}}"
)
_NODE = "query($id:ID!,$after:String){node(id:$id){... on PullRequest{%s}}}"
COMMITS_PAGE_QUERY = _NODE % (
    "commits(first:100,after:$after){" + _PAGE + " nodes{commit{oid}}}"
)
FILES_PAGE_QUERY = _NODE % (
    "files(first:100,after:$after){totalCount " + _PAGE
    + " nodes{path additions deletions}}"
)
COMMENTS_PAGE_QUERY = _NODE % (
    "comments(first:100,after:$after){totalCount " + _PAGE
    + " nodes{id body createdAt " + _AUTHOR + "}}"
)
REVIEWS_PAGE_QUERY = _NODE % (
    "reviews(first:100,after:$after){" + _PAGE
    + " nodes{id body state submittedAt createdAt " + _AUTHOR + "}}"
)
THREADS_PAGE_QUERY = _NODE % (
    "reviewThreads(first:50,after:$after){totalCount " + _PAGE
    + " nodes{id isResolved isOutdated path line originalLine "
    "comments(first:100){totalCount nodes{id body createdAt url "
    + _AUTHOR + " commit{oid} originalCommit{oid}}}}}"
)


class UsageError(Exception):
    pass


class GhMissing(Exception):
    pass


class GhError(Exception):
    pass


class GhFatal(GhError):
    """A failure every later call would share: login, token or rate limit."""


class TooLarge(Exception):
    pass


class WriteError(Exception):
    pass


# What every later call would share: a login that is gone or a rate limit. A
# bare 403 is not here: it can be one pull request the token may not read.
_FATAL = re.compile(
    r"HTTP 401|bad credentials|gh auth login|not logged in"
    r"|requires authentication|rate limit|abuse detection|secondary rate",
    re.I)


def run_gh(args):
    """The one subprocess seam: run `gh` with `args`, return its stdout."""
    gh = shutil.which("gh")
    if gh is None:
        raise GhMissing("the GitHub CLI (gh) is not on PATH")
    try:
        done = subprocess.run(
            [gh] + list(args), capture_output=True, text=True,
            encoding="utf-8", timeout=180)
    except subprocess.TimeoutExpired:
        raise GhError("gh timed out")
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        failure = GhFatal if _FATAL.search(detail) else GhError
        raise failure(detail + " (check `gh auth status`)")
    return done.stdout


def graphql(query, variables):
    """Run one read-only query; return its `data`. Variables keep their type:
    an int goes in as a number, a string verbatim (a cursor is never coerced),
    None is left out (GraphQL null)."""
    argv = ["api", "graphql", "-f", "query=" + query]
    for key, value in variables.items():
        if value is None:
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            argv += ["-F", "%s=%d" % (key, value)]
        else:
            argv += ["-f", "%s=%s" % (key, value)]
    try:
        answer = json.loads(run_gh(argv))
    except ValueError:
        raise GhError("gh did not return JSON")
    if answer.get("errors"):
        first = answer["errors"][0]
        message = first.get("message") if isinstance(first, dict) else first
        raise GhError(str(message))
    return answer.get("data") or {}


def paged(query, variables, path):
    """Follow `pageInfo` to the end; return (all nodes, totalCount or None)."""
    nodes, total, after = [], None, None
    while True:
        data = graphql(query, dict(variables, after=after))
        for key in path:
            data = (data or {}).get(key)
        data = data or {}
        nodes.extend(data.get("nodes") or [])
        if data.get("totalCount") is not None:
            total = data["totalCount"]
        info = data.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor"):
            return nodes, total
        after = info["endCursor"]


_REMOTE = re.compile(
    r"^(?:https?://(?:[^@/\s]+@)?|ssh://(?:[^@/\s]+@)?|[^@/\s]+@)"
    r"github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def parse_github_remote(url):
    found = _REMOTE.match((url or "").strip())
    return "%s/%s" % found.groups() if found else None


def author_of(node):
    login = (node or {}).get("login")
    if not login:
        return {"login": GHOST_LOGIN, "is_bot": False}
    if node.get("__typename") == "Bot":
        if not login.endswith("[bot]"):
            login += "[bot]"
        return {"login": login, "is_bot": True}
    return {"login": login, "is_bot": False}


def _cap(value, limit):
    return value[:limit] if isinstance(value, str) else value


def turn_of(node, at):
    return {"id": node["id"], "author": author_of(node.get("author")),
            "body": node.get("body") or "", "created_at": at}


def pr_state(state, merged_at):
    if state == "MERGED" or (state == "CLOSED" and merged_at):
        return "merged"
    return "closed" if state == "CLOSED" else "open"


def review_turns(reviews):
    turns = []
    for review in reviews:
        if not review:
            continue
        if review.get("state") == "PENDING":
            continue
        if not (review.get("body") or "").strip():
            continue
        at = review.get("submittedAt") or review.get("createdAt")
        turns.append(turn_of(review, at))
    return turns


def _line_of(thread):
    for key in ("line", "originalLine"):
        value = thread.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def conversation_of(thread):
    comments = thread.get("comments") or {}
    nodes = [c for c in (comments.get("nodes") or []) if c]
    if not nodes:
        return None
    first = nodes[0]
    sent = nodes[:MAX_THREAD_TURNS]
    turns = [turn_of(c, c.get("createdAt")) for c in sent]
    conversation = {
        "id": thread["id"],
        "resolved": bool(thread.get("isResolved")),
        "url": _cap(first.get("url"), MAX_URL_CHARS),
        "file_path": _cap(thread.get("path"), MAX_PATH_CHARS),
        "line": _line_of(thread),
        "commit_sha": ((first.get("commit") or {}).get("oid")
                       or (first.get("originalCommit") or {}).get("oid")
                       or None),
        "outdated": bool(thread.get("isOutdated")),
        "turns": turns,
    }
    total = comments.get("totalCount")
    if isinstance(total, int) and total > len(turns):
        conversation["turn_count"] = total
    return conversation


def text_of(turns):
    return sum(len(t["body"]) for t in turns)


def _sendable(path):
    """The door refuses a whole import over one path that is not already
    canonical and repo-relative: surrounding whitespace, a control character
    (Unicode category Cc, which includes the C1 range),
    a backslash, a leading `/`, or an empty, `.` or `..` segment, besides the
    length limit. Such a path is left out rather than cut or rewritten,
    because a changed path names another file."""
    return (len(path) <= MAX_PATH_CHARS
            and path == path.strip()
            and not any(unicodedata.category(c) == "Cc" or c == "\\" for c in path)
            and not any(seg in ("", ".", "..") for seg in path.split("/")))


def changed_files(nodes, declared):
    """Return (files sent, the real number). Path order, at most MAX_FILES;
    a path `_sendable` refuses is left out. The real number is the largest of
    GitHub's own counts and the listing, because GitHub stops listing files
    long before it stops counting them."""
    seen, listed = set(), []
    for node in nodes:
        path = (node or {}).get("path")
        if not isinstance(path, str) or not path or path in seen:
            continue
        seen.add(path)
        listed.append({"path": path, "additions": node.get("additions"),
                       "deletions": node.get("deletions")})
    counts = [n for n in declared
              if isinstance(n, int) and not isinstance(n, bool)]
    total = max([len(listed)] + counts)
    fitting = sorted((f for f in listed if _sendable(f["path"])),
                     key=lambda f: f["path"])
    return fitting[:MAX_FILES], total


def plan_parts(description, pr_turns, conversations, max_chars, overhead=0):
    """Return (pr turns sent, pr turn count or None, parts, skipped ids).
    Counting is `len()` on `str`, as the door counts. `overhead` is the size
    of what every part repeats but the door does not count as text (the changed
    files): it is charged to the part budget only, never to the server cap."""
    desc_len = len(description or "")
    if desc_len > SERVER_MAX_CHARS:
        raise TooLarge("the description alone is over the server's text cap")
    pr_total = len(pr_turns)
    sent = list(pr_turns[:MAX_PR_TURNS])
    base = desc_len + text_of(sent)
    while base > SERVER_MAX_CHARS and sent:
        base -= len(sent.pop()["body"])
    pr_turn_count = pr_total if len(sent) < pr_total else None
    budget = max(max_chars, base + overhead)
    parts, current, used, skipped = [], [], base + overhead, []
    for conv in conversations:
        size = text_of(conv["turns"])
        if base + size > SERVER_MAX_CHARS:
            skipped.append(conv["id"])
            continue
        if current and (used + size > budget
                        or len(current) >= MAX_CONVERSATIONS):
            parts.append(current)
            current, used = [], base + overhead
        current.append(conv)
        used += size
    if current or not parts:
        parts.append(current)
    return sent, pr_turn_count, parts, skipped


def _logins(pull_request, conversations):
    found = {pull_request["author"]["login"]}
    for turn in pull_request["turns"]:
        found.add(turn["author"]["login"])
    for conv in conversations:
        for turn in conv["turns"]:
            found.add(turn["author"]["login"])
    return found


def _refuse_over_cap(pull_request, parts):
    """The door refuses an over-cap id, repository or source outright, and an
    id cannot be trimmed without changing which record it names: refuse here."""
    named = [("source", pull_request["source"], MAX_SOURCE_CHARS),
             ("repository", pull_request["repository"], MAX_REPOSITORY_CHARS),
             ("pull request id", pull_request["id"], MAX_ID_CHARS)]
    named += [("pull request turn id", t["id"], MAX_ID_CHARS)
              for t in pull_request["turns"]]
    for part in parts:
        for conv in part:
            named.append(("conversation id", conv["id"], MAX_ID_CHARS))
            named += [("conversation turn id", t["id"], MAX_ID_CHARS)
                      for t in conv["turns"]]
    for label, value, cap in named:
        if len(value) > cap:
            raise TooLarge("the %s %r is %d characters, over the door's cap "
                           "of %d; it cannot be trimmed, so nothing was written"
                           % (label, value[:40] + "...", len(value), cap))


def build_parts(repo, number, max_chars, allow_open):
    owner, name = repo.split("/", 1)
    data = graphql(PR_QUERY, {"owner": owner, "name": name, "number": number})
    pr = ((data.get("repository") or {}).get("pullRequest"))
    if not pr:
        raise UsageError("pull request %s#%d not found" % (repo, number))
    state = pr_state(pr.get("state"), pr.get("mergedAt"))
    if state == "open" and not allow_open:
        raise UsageError("open pull request: pass --allow-open only when the "
                         "person asked for open ones")
    ident = {"id": pr["id"]}
    commits, _ = paged(COMMITS_PAGE_QUERY, ident, ("node", "commits"))
    comments, _ = paged(COMMENTS_PAGE_QUERY, ident, ("node", "comments"))
    reviews, _ = paged(REVIEWS_PAGE_QUERY, ident, ("node", "reviews"))
    threads, thread_total = paged(
        THREADS_PAGE_QUERY, ident, ("node", "reviewThreads"))
    file_nodes, file_total = paged(FILES_PAGE_QUERY, ident, ("node", "files"))

    commits = [c for c in commits if c]
    comments = [c for c in comments if c]
    reviews = [r for r in reviews if r]
    threads = [t for t in threads if t]

    turns = [turn_of(c, c.get("createdAt")) for c in comments]
    turns += review_turns(reviews)
    turns.sort(key=lambda t: (t["created_at"], t["id"]))
    keyed = []
    for thread in threads:
        conv = conversation_of(thread)
        if conv is not None:
            first = [c for c in thread["comments"]["nodes"] if c][0]
            keyed.append(((first.get("createdAt") or "", conv["id"]), conv))
    keyed.sort(key=lambda pair: pair[0])
    conversations = [conv for _, conv in keyed]
    threads_total = max(thread_total or 0, len(conversations))

    files, file_count = changed_files(
        file_nodes, (pr.get("changedFiles"), file_total))
    overhead = len(json.dumps(files, ensure_ascii=False))
    description = pr.get("body") or None
    sent, turn_count, parts, skipped = plan_parts(
        description, turns, conversations, max_chars, overhead)
    pull_request = {
        "source": "github",
        "id": pr["id"],
        "number": pr["number"],
        "repository": repo,
        "url": _cap(pr["url"], MAX_URL_CHARS),
        "title": _cap(pr["title"], MAX_TITLE_CHARS),
        "description": description,
        "state": state,
        "author": author_of(pr.get("author")),
        "created_at": pr["createdAt"],
        "commit_shas": [(c.get("commit") or {}).get("oid") for c in commits
                        if (c.get("commit") or {}).get("oid")][-MAX_COMMIT_SHAS:],
        "turns": sent,
        "files": files,
    }
    merge_sha = (pr.get("mergeCommit") or {}).get("oid")
    if state == "merged" and merge_sha:
        pull_request["merge_commit_sha"] = merge_sha
    if turn_count is not None:
        pull_request["turn_count"] = turn_count
    if len(files) < file_count:
        pull_request["file_count"] = file_count
    _refuse_over_cap(pull_request, parts)
    whole = len(parts) == 1 and not skipped
    arguments = [{"pull_request": pull_request, "conversations": part,
                  "conversation_count": None if whole else threads_total}
                 for part in parts]
    base = len(description or "") + text_of(sent)
    in_parts = [conv for part in parts for conv in part]
    report = {
        "number": pr["number"],
        "id": pr["id"],
        "url": pr["url"],
        "branch": pr.get("headRefName"),
        "title": pr["title"],
        "state": state,
        "changed_files": file_count,
        "changed_files_sent": len(files),
        "changed_file_chars": overhead,
        "parts": len(parts),
        "conversations_per_part": [len(p) for p in parts],
        "chars_per_part": [base + sum(text_of(c["turns"]) for c in p)
                           for p in parts],
        "pr_turns_total": len(turns),
        "pr_turns_sent": len(sent),
        "conversations_total": threads_total,
        "conversations_sent": len(in_parts),
        "skipped_too_large": skipped,
        "max_chars": max_chars,
        "logins": sorted(_logins(pull_request, in_parts)),
    }
    return arguments, report


def _today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def _clock():
    return time.monotonic()


def list_pull_requests(repo, states, since, limit):
    """`limit` None lists everything the window holds."""
    owner, name = repo.split("/", 1)
    query = LIST_QUERY_TEMPLATE.replace("%STATES%", ", ".join(states))
    found, after = [], None
    while limit is None or len(found) < limit:
        data = graphql(query, {"owner": owner, "name": name, "after": after})
        page = ((data.get("repository") or {}).get("pullRequests")) or {}
        for node in page.get("nodes") or []:
            if not node:
                continue
            when = node.get("mergedAt") or node.get("closedAt") \
                or node.get("createdAt") or ""
            if since and (node.get("updatedAt") or when) < since:
                return found
            if since and when < since:
                continue
            found.append({
                "number": node["number"], "id": node["id"],
                "title": node["title"],
                "state": pr_state(node.get("state"), node.get("mergedAt")),
                "url": node["url"], "created_at": node.get("createdAt"),
                "merged_at": node.get("mergedAt"),
                "closed_at": node.get("closedAt"),
            })
            if limit is not None and len(found) >= limit:
                break
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor"):
            break
        after = info["endCursor"]
    return found


def public_remote(url):
    """The origin URL as it may be sent: credentials in front of the host
    (`user:token@`) are dropped, nothing else changes. An scp-style
    `git@host:path` carries no secret and is left as it is."""
    url = (url or "").strip()
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/]*)(.*)$", url)
    if scheme is None:
        return url
    head, authority, rest = scheme.groups()
    return head + authority.rsplit("@", 1)[-1] + rest


def _origin_url():
    try:
        done = subprocess.run(["git", "remote", "get-url", "origin"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise UsageError("cannot read the origin remote; pass --repo")
    return done.stdout.strip() if done.returncode == 0 else ""


def _repo_of(args):
    if args.repo:
        if not re.match(r"^[^/\s]+/[^/\s]+$", args.repo):
            raise UsageError("--repo must look like OWNER/NAME")
        return args.repo
    url = _origin_url()
    if not url:
        raise UsageError("cannot read the origin remote; pass --repo")
    repo = parse_github_remote(url) if url else None
    if repo is None:
        host = re.search(r"(?:@|//)([^/:@\s]+)", url)
        raise UsageError("the origin remote is not on github.com (host: %s)"
                         % (host.group(1) if host else "unknown"))
    return repo


def _parser():
    parser = argparse.ArgumentParser(prog="pr_collect.py")
    sub = parser.add_subparsers(dest="command", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("--repo")
    lst.add_argument("--states", default="MERGED,CLOSED")
    lst.add_argument("--since")
    lst.add_argument("--days", type=int)
    lst.add_argument("--limit", type=int)
    lst.add_argument("--out")
    sub.add_parser("remote")
    col = sub.add_parser("collect")
    col.add_argument("--repo")
    which = col.add_mutually_exclusive_group(required=True)
    which.add_argument("--number", type=int)
    which.add_argument("--since")
    which.add_argument("--days", type=int)
    col.add_argument("--out-dir", required=True)
    col.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    col.add_argument("--allow-open", action="store_true")
    col.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    col.add_argument("--resume", action="store_true")
    return parser


def _write_parts(out_dir, number, arguments):
    target = os.path.join(out_dir, str(number))
    files = []
    try:
        makedirs_ignored(target)
        # Only this collector's own part files are removed: --out-dir is the
        # caller's choice, so nothing else under it is ever deleted.
        for name in os.listdir(target):
            if re.match(r"^part-\d+\.json$", name):
                os.remove(os.path.join(target, name))
        left = [n for n in os.listdir(target)
                if re.match(r"^part-\d+\.json$", n)]
        if left:
            raise OSError(0, "stale %s could not be removed" % left[0])
        for index, call in enumerate(arguments, 1):
            path = os.path.join(target, "part-%d.json" % index)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(call, handle, ensure_ascii=False)
            files.append(path)
    except OSError as err:
        raise WriteError("cannot write the part files under %s: %s"
                         % (target, err.strerror or err.__class__.__name__))
    return files


def _window(args):
    """The first day of the window, or None when there is no window."""
    if args.since and args.days is not None:
        raise UsageError("--since and --days name the same window: pass one")
    if args.days is not None:
        if not 1 <= args.days <= MAX_WINDOW_DAYS:
            raise UsageError("--days must be between 1 and %d"
                             % MAX_WINDOW_DAYS)
        return (_today() - datetime.timedelta(days=args.days)).isoformat()
    if args.since and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.since):
        raise UsageError("--since must look like YYYY-MM-DD")
    return args.since or None


def _write_json(path, document):
    """Atomic: a temp file beside the target, then a rename."""
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-pr-", mode=0o644)
    except OSError as err:
        raise WriteError("cannot write %s: %s"
                         % (path, err.strerror or err.__class__.__name__))


def _read_report(path):
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise GhError("cannot read the earlier report %s; remove it or drop "
                      "--resume" % path)
    if (not isinstance(report, dict)
            or not isinstance(report.get("pull_requests"), list)
            or not isinstance(report.get("not_collected"), list)):
        raise GhError("the earlier report %s is not a collection report; "
                      "remove it or drop --resume" % path)
    return report


def _frozen_since(path, repo, since):
    """The first day of an unfinished collect of this repository, when it
    began no more than a day before `since`: a `--days` window moves at UTC
    midnight, and a collect that straddles it must not start over."""
    try:
        report = _read_report(path)
    except GhError:
        return since
    if (report is None or report.get("complete") is True
            or report.get("repository") != repo
            or not isinstance(report.get("since"), str)):
        return since
    try:
        begun = datetime.date.fromisoformat(report["since"])
        wanted = datetime.date.fromisoformat(since)
    except ValueError:
        return since
    return report["since"] if 0 <= (wanted - begun).days <= 1 else since


def _failure(number, code, reason, **extra):
    return dict(number=number, exit=code, reason=reason, **extra)


def _settled(report):
    """Numbers that need no further attempt: collected, or failed for good."""
    return ({p["number"] for p in report["pull_requests"]}
            | {p["number"] for p in report["not_collected"] if _final(p)})


def _final(entry):
    """A failure is settled when a retry cannot change it: a `gh` failure is
    retried on a later call, up to MAX_ATTEMPTS times in all."""
    return (entry.get("exit") != EXIT_GH
            or entry.get("attempts", 1) >= MAX_ATTEMPTS)


def collect_window(repo, since, out_dir, max_chars, max_seconds, resume):
    """Collect every merged or closed pull request of the window, one at a
    time, within `max_seconds`; see the module docstring."""
    rows = list_pull_requests(repo, ("MERGED", "CLOSED"), since, None)
    path = os.path.join(out_dir, "collected.json")
    report = _read_report(path) if resume else None
    # Only an unfinished collect of this same window is continued. A finished
    # one, or one of another window, describes pull requests that may have
    # changed since, so it is started over.
    if report is not None and (report.get("since") != since
                               or report.get("repository") != repo
                               or report.get("complete") is True):
        report = None
    report = report or {"pull_requests": [], "not_collected": []}
    done = _settled(report)
    attempts = {p["number"]: p.get("attempts", 1)
                for p in report["not_collected"]}
    todo = [r for r in rows if r["number"] not in done]
    report.update(since=since, repository=repo, complete=False)
    start, handled, now, stopped = _clock(), 0, 0, False
    for row in todo:
        if handled and _clock() - start >= max_seconds:
            stopped = True
            break
        number = row["number"]
        handled += 1
        # An earlier failure is replaced only by this attempt, so a pull
        # request the time budget never reached keeps its attempt count.
        earlier = [p for p in report["not_collected"] if p["number"] == number]
        report["not_collected"] = [p for p in report["not_collected"]
                                   if p["number"] != number]
        try:
            arguments, entry = build_parts(repo, number, max_chars, False)
            entry["files"] = _write_parts(out_dir, number, arguments)
            report["pull_requests"].append(entry)
            now += 1
        except (GhFatal, GhMissing):
            # A login, a limit or a `gh` that is gone is not this pull
            # request's failure: it keeps the entry it had, attempts
            # unchanged, so a resume still retries it. `main` maps the
            # exception to its exit code (3, or 127 for a missing `gh`).
            report["not_collected"].extend(earlier)
            _write_json(path, report)
            raise
        except UsageError as err:
            report["not_collected"].append(
                _failure(number, EXIT_USAGE, str(err)))
        except GhError as err:
            # The message carries gh's own stderr: it stays local (the
            # collection report and the summary shown to the person), and
            # never enters a part file or anything sent to the brain.
            report["not_collected"].append(
                _failure(number, EXIT_GH, str(err),
                         attempts=attempts.get(number, 0) + 1))
        except TooLarge as err:
            report["not_collected"].append(
                _failure(number, EXIT_TOO_LARGE, str(err)))
        except WriteError as err:
            report["not_collected"].append(
                _failure(number, EXIT_WRITE, str(err)))
        _write_json(path, report)
    settled = _settled(report)
    remaining = len([r for r in todo if r["number"] not in settled])
    report["complete"] = not stopped and remaining == 0
    _write_json(path, report)
    logins = sorted({login for p in report["pull_requests"]
                     for login in p.get("logins", [])})
    return {
        "since": since,
        "complete": report["complete"],
        "pull_requests": len(report["pull_requests"]),
        "collected_now": now,
        "remaining": remaining,
        "parts": sum(p.get("parts", 0) for p in report["pull_requests"]),
        "not_collected": report["not_collected"],
        "logins": logins,
        "collected": path,
        "files_cut": [p["number"] for p in report["pull_requests"]
                      if p.get("changed_files_sent", 0)
                      < p.get("changed_files", 0)],
    }


def _run(args):
    if args.command == "remote":
        repo = _repo_of(argparse.Namespace(repo=None))
        return {"repository": repo, "git_remote": public_remote(_origin_url())}
    repo = _repo_of(args)
    if args.command == "list":
        states = tuple(s.strip().upper() for s in args.states.split(",")
                       if s.strip())
        if not states or any(s not in STATES for s in states):
            raise UsageError("--states takes a comma list of %s"
                             % ", ".join(STATES))
        since = _window(args)
        limit = args.limit
        if limit is None and since is None:
            limit = 50
        if limit is not None and not 1 <= limit <= 500:
            raise UsageError("--limit must be between 1 and 500")
        rows = list_pull_requests(repo, states, since, limit)
        if args.out:
            _write_json(args.out, rows)
            return {"pull_requests": len(rows), "out": args.out}
        return rows
    if not 1 <= args.max_chars <= SERVER_MAX_CHARS:
        raise UsageError("--max-chars must be between 1 and %d"
                         % SERVER_MAX_CHARS)
    since = _window(args)
    if since is not None:
        if args.resume and args.days is not None:
            since = _frozen_since(os.path.join(args.out_dir, "collected.json"),
                                  repo, since)
        if args.allow_open:
            raise UsageError("--allow-open applies to one --number only")
        if not 1 <= args.max_seconds <= 3600:
            raise UsageError("--max-seconds must be between 1 and 3600")
        return collect_window(repo, since, args.out_dir, args.max_chars,
                              args.max_seconds, args.resume)
    if args.resume:
        raise UsageError("--resume applies to a --since/--days collect only")
    arguments, report = build_parts(
        repo, args.number, args.max_chars, args.allow_open)
    report["files"] = _write_parts(args.out_dir, args.number, arguments)
    return report


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    try:
        document = _run(args)
    except UsageError as err:
        print("pr_collect: %s" % err, file=sys.stderr)
        return EXIT_USAGE
    except GhMissing as err:
        print("pr_collect: %s" % err, file=sys.stderr)
        return EXIT_NO_GH
    except GhError as err:
        print("pr_collect: gh failed: %s" % err, file=sys.stderr)
        return EXIT_GH
    except TooLarge as err:
        print("pr_collect: %s" % err, file=sys.stderr)
        return EXIT_TOO_LARGE
    except WriteError as err:
        print("pr_collect: %s" % err, file=sys.stderr)
        return EXIT_WRITE
    print(json.dumps(document, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
