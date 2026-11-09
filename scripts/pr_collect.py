#!/usr/bin/env python3
"""Collect a repository's pull requests for the brain import.

Reads a pull request and its review conversations through the person's own
`gh` credential, using read-only GraphQL queries, and writes what the brain's
reference card for it is built from. Nothing is posted to GitHub and nothing
is sent to the platform from here.

    pr_collect.py list    [--repo OWNER/NAME] [--states MERGED,CLOSED]
                          [--since YYYY-MM-DD | --days N] [--limit N]
                          [--out FILE]
    pr_collect.py collect [--repo OWNER/NAME] --out-dir DIR
                          [--agent-account LOGIN ...]
                          (--number N [--allow-open]
                           | --since YYYY-MM-DD | --days N)
                          [--max-seconds N] [--resume]
    pr_collect.py redact  --read FILE --erased LOGIN [--erased LOGIN ...]
    pr_collect.py remote
    pr_collect.py busiest --out-dir DIR [--number N ...] [--root DIR]

`remote` prints `{"repository", "git_remote"}`: the origin remote as the
import door may receive it, with any `user:token@` in front of the host
removed. `list` prints a JSON array, newest first. Without `--repo` the
repository is read from the `origin` remote, which must be on github.com.

Cards. `collect` writes two files per pull request under `DIR/<number>/`:

* `card.json`, the SKELETON of the reference card: every mechanical field of
  the pull request (ids, state, author, dates, branches, head and merge
  commits, changed paths, participants, the source's version and visibility)
  and none of its text. `reference_card.py stage` joins it with the agent's
  summary and rationale points; the agent never re-types a field. The pull
  request's description and comments are never in it.
* `read.md`, what the agent reads to write its summary: the description, the
  conversation and the review threads, in order. It stays on this machine and
  is never sent.

Erased people. The brain's check answer may name `erased_participants`:
logins an administrator has erased. `redact` rewrites a `read.md` in place
without what those people wrote (their comments, and the description when one
of them opened the pull request), before the agent reads it, so a new summary
cannot put their words back. Other people's quotations of them are not found.

Files from an earlier shape (`part-<k>.json`) in that directory are removed. A
staged `write.json` is left alone: it belongs to the version it was staged for.

The version. `source_version` is the fingerprint `source_version.py`
describes: the title, the description, GitHub's own state word and the human
comments (issue comments, submitted review bodies and every review-thread
comment of a person, not a bot, not one of the `--agent-account` logins).
Whitespace, a bot's remarks and the order the API returned them in never change
it. A review thread's comments are read to the end, a hundred at a time.

Participants. The pull request's author and everyone who commented or
submitted a review, bots included, once each in login order, so that erasing a
person by login still reaches the card. A deleted account (`ghost`) is not a
participant. More than 100 is refused, never cut.

Visibility. `source_visibility` is the repository's own word: `public`,
`private`, or `restricted` for an internal repository; null when GitHub reports
none.

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

Changed files. `files` lists the paths the pull request changed, in path order
(Python string order), without duplicates, at most 300, and `file_count` is the
real number (the largest of GitHub's own counts and the listing). A path over
1,024 characters, or not already canonical and repo-relative (surrounding
whitespace, a control character, a backslash, a leading `/`, an empty, `.` or
`..` segment), is left out (never cut or rewritten, because that names another
file, and the door would refuse the whole card).

Commits. A pull request carries `head_sha`, its last commit, and a merged one
`merge_commit_sha`, the commit its file anchors name; a squash merge leaves the
head commit off the default branch.

Logins. Spelling is identity: a project admin erases a person by login. A bot
account is spelled `name[bot]` (once), and a deleted account is spelled
`ghost`.

`list --since` pages by last update, newest first, and stops at the first pull
request not updated since the date: a merge or a close always bumps the update
time, so nothing merged or closed after the date is missed. The date filter
itself is on the merge or close time (the creation time for an open one).

Busiest file. `busiest` reads the `card.json` files a collect wrote under
`DIR/<number>/` (only the `--number` ones when given) and prints
`{"path", "pull_requests", "cards", "runners_up"}`: the changed path that the
most of those pull requests touched and still exists under `--root` (default:
the current directory), how many touched it, how many cards were read, and up
to four next ones. Lockfiles, changelogs, generated or vendored output, images
and anything under `.fairmind/` are never counted: every pull request touches
them and nobody asks why. Dependency and version manifests, `build/` and
`.github/` count only when no other path is left, and pull requests a bot
opened count only when no person's is left. Ties go to the path in string order. `path` is null when nothing is left. It reads only local
files and calls nothing.

Exit codes: 0 ok; 2 usage, unsupported remote, an open pull request without
`--allow-open`, or a pull request that does not exist; 3 `gh` failed (its
message is relayed); 4 the pull request cannot become a card (an identifier
over the door's cap, more than 100 participants); 5 the files cannot be written
(the one-line message names the path); 127 `gh` not on PATH.

stdout carries only the JSON document; diagnostics go to stderr. stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from login_key import match_key, sanitize
from source_version import human_texts as compute_texts
from source_version import source_version as compute_source_version

MAX_TITLE_CHARS = 1000
MAX_URL_CHARS = 2048
MAX_PATH_CHARS = 1024
MAX_FILES = 300
MAX_PARTICIPANTS = 100
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
    "repository(owner:$owner,name:$name){nameWithOwner visibility "
    "pullRequest(number:$number){id number url title body state mergedAt "
    "closedAt createdAt changedFiles headRefName baseRefName headRefOid "
    "mergeCommit{oid} " + _AUTHOR + "}}}"
)
_NODE = "query($id:ID!,$after:String){node(id:$id){... on PullRequest{%s}}}"
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
THREAD_COMMENTS_PAGE_QUERY = (
    "query($id:ID!,$after:String){node(id:$id){... on PullRequestReviewThread{"
    "comments(first:100,after:$after){totalCount " + _PAGE
    + " nodes{id body state createdAt " + _AUTHOR + "}}}}}"
)
THREADS_PAGE_QUERY = _NODE % (
    "reviewThreads(first:50,after:$after){totalCount " + _PAGE
    + " nodes{id isResolved isOutdated path line originalLine "
    "comments(first:100){totalCount nodes{id body state createdAt url "
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
    login = sanitize((node or {}).get("login")).strip()
    if not login:
        return {"login": GHOST_LOGIN, "is_bot": False}
    if node.get("__typename") == "Bot":
        if not login.endswith("[bot]"):
            login += "[bot]"
        return {"login": login, "is_bot": True}
    return {"login": login, "is_bot": False}


def _cap(value, limit):
    return value[:limit] if isinstance(value, str) else value


def comment_of(node, at):
    """A comment as the version and the reading file see it."""
    return {"id": node["id"], "author": author_of(node.get("author")),
            "body": node.get("body") or "", "created_at": at}


def pr_state(state, merged_at):
    if state == "MERGED" or (state == "CLOSED" and merged_at):
        return "merged"
    return "closed" if state == "CLOSED" else "open"


def review_comments(reviews):
    """The submitted reviews that say something: a draft that was never sent
    and a review with no body are left out."""
    found = []
    for review in reviews:
        if not review:
            continue
        if review.get("state") == "PENDING":
            continue
        if not (review.get("body") or "").strip():
            continue
        at = review.get("submittedAt") or review.get("createdAt")
        found.append(comment_of(review, at))
    return found


def _whole_thread(thread):
    """The thread with every one of its comments: the listing carries the
    first hundred, and a longer thread is read to the end, because the
    source's version covers what a person said at any position in it."""
    block = thread.get("comments") or {}
    nodes = block.get("nodes") or []
    total = block.get("totalCount")
    if not isinstance(total, int) or total <= len(nodes) or not thread.get("id"):
        return thread
    every, _ = paged(THREAD_COMMENTS_PAGE_QUERY, {"id": thread["id"]},
                     ("node", "comments"))
    return dict(thread, comments=dict(block, nodes=every))


def thread_of(thread):
    """A review thread with the comments read from it, or None when it holds
    none. The thread's own context (path, line, state) is for the reader. A
    comment of a review that was never submitted is a draft only its writer
    can see, so it is left out: the version must not depend on who runs the
    collector."""
    nodes = [c for c in ((thread.get("comments") or {}).get("nodes") or [])
             if c and c.get("state") != "PENDING"]
    if not nodes:
        return None
    return {
        "id": thread["id"],
        "resolved": bool(thread.get("isResolved")),
        "outdated": bool(thread.get("isOutdated")),
        "file_path": thread.get("path"),
        "line": thread.get("line") if isinstance(thread.get("line"), int)
        else thread.get("originalLine"),
        "comments": sorted((comment_of(c, c.get("createdAt")) for c in nodes),
                           key=lambda c: (c["created_at"] or "", c["id"])),
    }


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
    """Return (paths sent, the real number). Path order, at most MAX_FILES;
    a path `_sendable` refuses is left out. The real number is the largest of
    GitHub's own counts and the listing, because GitHub stops listing files
    long before it stops counting them."""
    listed, seen = [], set()
    for node in nodes:
        path = (node or {}).get("path")
        if not isinstance(path, str) or not path or path in seen:
            continue
        seen.add(path)
        listed.append(path)
    counts = [n for n in declared
              if isinstance(n, int) and not isinstance(n, bool)]
    total = max([len(listed)] + counts)
    fitting = sorted(path for path in listed if _sendable(path))
    return fitting[:MAX_FILES], total


def _refuse_over_cap(card):
    """The door refuses an over-cap id, repository or source outright, and an
    id cannot be trimmed without changing which record it names: refuse here."""
    for label, value, cap in (
            ("source", card["source"], MAX_SOURCE_CHARS),
            ("repository", card["repository"], MAX_REPOSITORY_CHARS),
            ("pull request id", card["id"], MAX_ID_CHARS)):
        if len(value) > cap:
            raise TooLarge("the %s %r is %d characters, over the door's cap "
                           "of %d; it cannot be trimmed, so nothing was written"
                           % (label, value[:40] + "...", len(value), cap))


def _attributed(comments):
    """The comments with a deleted account's author removed: nobody wrote
    them that the version can count as a person."""
    return [dict(c, author=None) if c["author"]["login"] == GHOST_LOGIN else c
            for c in comments]


def visibility_of(word):
    """The repository's own word for who can see it, as the door spells it."""
    return {"PUBLIC": "public", "PRIVATE": "private",
            "INTERNAL": "restricted"}.get(str(word or "").upper())


def participants_of(author, comments):
    """The people (and bots) who took part, once each, in login order. A
    deleted account is nobody to erase and is not listed."""
    found = {}
    for who in [author] + [c["author"] for c in comments]:
        if who["login"] != GHOST_LOGIN:
            found.setdefault(match_key("github", who["login"]), who)
    people = [found[key] for key in sorted(found)]
    if len(people) > MAX_PARTICIPANTS:
        raise TooLarge("the pull request has %d participants, over the door's "
                       "cap of %d; they cannot be cut without hiding a person "
                       "from an erasure" % (len(people), MAX_PARTICIPANTS))
    return people


def _quote(text):
    return "\n".join(("> " + line) if line else ">"
                     for line in (text or "").splitlines()) or ">"


def token(value):
    """A value as one whitespace-free word, the way the reading file spells a
    login or a timestamp, so that a header line has a fixed grammar whatever
    the source put in the field. `redact_reading` spells an erased login the
    same way."""
    return re.sub(r"\s+", "_", str(value))


REDACTED_NAME = "redacted.json"


def _reading_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redaction_record(read_path):
    """The logins a reading file was redacted for, as `{"logins": [...],
    "staged": {path: [logins]}}`. The record is bound to the file's bytes: a
    reading file written again (a new collection) or edited by hand since
    carries no redaction, so the record is empty then."""
    empty = {"logins": [], "staged": {}}
    try:
        with open(read_path, encoding="utf-8") as handle:
            digest = _reading_digest(handle.read())
        with open(os.path.join(os.path.dirname(os.path.abspath(read_path)),
                               REDACTED_NAME), encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, UnicodeError, ValueError):
        return empty
    if not isinstance(record, dict) or record.get("read_sha256") != digest:
        return empty
    logins = [str(x) for x in record.get("logins") or []]
    staged = record.get("staged") if isinstance(record.get("staged"), dict) else {}
    return {"logins": logins,
            "staged": {str(k): [str(x) for x in v] for k, v in staged.items()
                       if isinstance(v, list)}}


def save_redaction_record(read_path, logins=(), staged_out=None,
                          record=None):
    """Add `logins` (the ones the file was just redacted for) and, with
    `staged_out`, the redactions a card staged at that path covers. Written
    after the file itself, bound to its present bytes. `record` is what
    `redaction_record` said before the file was rewritten."""
    record = record if record is not None else redaction_record(read_path)
    with open(read_path, encoding="utf-8") as handle:
        source = reading_source(handle.read())
    held = {match_key(source, x): x for x in record["logins"]}
    for login in logins:
        held.setdefault(match_key(source, login), _held(login))
    record["logins"] = sorted(held.values())
    if staged_out is not None:
        record["staged"][os.path.abspath(staged_out)] = sorted(held.values())
    with open(read_path, encoding="utf-8") as handle:
        record["read_sha256"] = _reading_digest(handle.read())
    target = os.path.join(os.path.dirname(os.path.abspath(read_path)),
                          REDACTED_NAME)
    _atomic_write_text(target, json.dumps(record, indent=2, sort_keys=True)
                       + "\n")


def _who(comment):
    return token(comment["author"]["login"]) + (
        " (bot)" if comment["author"]["is_bot"] else "")


DESCRIPTION_HEADING = "## Description"
CONVERSATION_HEADING = "## Conversation"


REVIEW_THREADS_HEADING = "## Review threads"
ERASED_COMMENT = "(a comment by an erased account was left out)"
ERASED_DESCRIPTION = "(the description by an erased account was left out)"
_STATE_LINE = re.compile(r"^(State: .*, author )(\S+)(, opened .*)$")
_COMMENT_HEADER = {"conversation": re.compile(r"^### (\S+?)(?: \(bot\))?, .*$"),
                   "threads": re.compile(r"^- (\S+?)(?: \(bot\))?, .*$")}


_TRACKER_LINE = re.compile(r"^Tracker: (\S+) ", re.M)


def reading_source(text):
    """The source a reading file was written for: a ticket's names its tracker
    on the line before its description, a pull request's is GitHub."""
    head = text.split("\n" + DESCRIPTION_HEADING + "\n", 1)[0]
    found = _TRACKER_LINE.search(head)
    return found.group(1) if found else "github"


def _held(login):
    """How a redaction record spells a login: scrubbed, stripped, casefolded
    and without whitespace. Whoever reads the record compares by the brain's
    key under the reading file's own source."""
    return token(sanitize(login).strip().casefold())


def redact_reading(text, erased):
    """Return (the reading file without what the `erased` logins wrote, the
    number of comments left out, whether the description was). Every text of
    the source is quoted in the file, so a comment is its header line and the
    quoted lines after it, and no quoted text can pass for a header."""
    source = reading_source(text)
    erased = {match_key(source, e) for e in erased}
    out, section, skipping, removed, dropped = [], None, False, 0, False
    author_erased = False
    for line in text.split("\n"):
        if line.startswith(">"):
            if skipping:
                continue
            if section == "description" and author_erased:
                if not dropped:
                    out.append(ERASED_DESCRIPTION)
                    dropped = True
                continue
            out.append(line)
            continue
        skipping = False
        state = _STATE_LINE.match(line)
        if state and section is None:
            author_erased = match_key(source, state.group(2)) in erased
            if author_erased:
                line = state.group(1) + "(erased)" + state.group(3)
        elif line == DESCRIPTION_HEADING:
            section = "description"
        elif line == CONVERSATION_HEADING:
            section = "conversation"
        elif line == REVIEW_THREADS_HEADING:
            section = "threads"
        elif section in _COMMENT_HEADER:
            header = _COMMENT_HEADER[section].match(line)
            if header and match_key(source, header.group(1)) in erased:
                out.append(ERASED_COMMENT)
                skipping, removed = True, removed + 1
                continue
        out.append(line)
    return "\n".join(out), removed, dropped


def read_file(card, pr, conversation, threads):
    """What the agent reads to write its summary. Local only, never sent."""
    lines = ["# %s#%d: %s" % (card["repository"], card["number"],
                                " ".join(str(card["title"]).split())),
             "",
             "State: %s, author %s, opened %s" % (
                 pr.get("state"), token(card["author"]["login"]),
                 card["created_at"]),
             "Branch: %s -> %s" % (card["head_ref"], card["base_ref"]),
             "Changed files (%d): %s" % (card["file_count"],
                                         ", ".join(card["files"]) or "none"),
             "", DESCRIPTION_HEADING, "",
             _quote(pr["body"]) if (pr.get("body") or "").strip() else "(none)",
             "",
             CONVERSATION_HEADING, ""]
    if not conversation:
        lines += ["(none)", ""]
    for comment in conversation:
        lines += ["### %s, %s" % (_who(comment), token(comment["created_at"])),
                  _quote(comment["body"]), ""]
    lines += ["## Review threads", ""]
    if not threads:
        lines += ["(none)", ""]
    for thread in threads:
        where = " ".join(str(thread["file_path"] or "(no file)").split())
        if thread["line"] is not None:
            where += ":%s" % thread["line"]
        lines += ["### %s (%s%s)" % (
            where, "resolved" if thread["resolved"] else "open",
            ", outdated" if thread["outdated"] else "")]
        for comment in thread["comments"]:
            lines += ["- %s, %s" % (_who(comment), token(comment["created_at"])),
                      _quote(comment["body"])]
        lines.append("")
    return "\n".join(lines)


def build_card(repo, number, allow_open, agent_accounts=()):
    """Return (skeleton, reading text, report) for one pull request."""
    owner, name = repo.split("/", 1)
    data = graphql(PR_QUERY, {"owner": owner, "name": name, "number": number})
    repository = data.get("repository") or {}
    pr = repository.get("pullRequest")
    if not pr:
        raise UsageError("pull request %s#%d not found" % (repo, number))
    state = pr_state(pr.get("state"), pr.get("mergedAt"))
    if state == "open" and not allow_open:
        raise UsageError("open pull request: pass --allow-open only when the "
                         "person asked for open ones")
    ident = {"id": pr["id"]}
    comments, _ = paged(COMMENTS_PAGE_QUERY, ident, ("node", "comments"))
    reviews, _ = paged(REVIEWS_PAGE_QUERY, ident, ("node", "reviews"))
    threads, _ = paged(THREADS_PAGE_QUERY, ident, ("node", "reviewThreads"))
    file_nodes, file_total = paged(FILES_PAGE_QUERY, ident, ("node", "files"))

    comments = [c for c in comments if c]
    reviews = [r for r in reviews if r]
    threads = [_whole_thread(t) for t in threads if t]

    conversation = [comment_of(c, c.get("createdAt")) for c in comments]
    conversation += review_comments(reviews)
    conversation.sort(key=lambda c: (c["created_at"], c["id"]))
    keyed = []
    for thread in threads:
        found = thread_of(thread)
        if found is not None:
            keyed.append(((found["comments"][0]["created_at"] or "",
                           found["id"]), found))
    keyed.sort(key=lambda pair: pair[0])
    thread_list = [thread for _, thread in keyed]
    thread_comments = [c for t in thread_list for c in t["comments"]]
    # A submitted review that said nothing still took part.
    reviewers = [comment_of(r, None) for r in reviews
                 if r.get("state") != "PENDING"]
    author = author_of(pr.get("author"))
    participants = participants_of(
        author, conversation + thread_comments + reviewers)

    paths, file_count = changed_files(
        file_nodes, (pr.get("changedFiles"), file_total))
    version = compute_source_version(
        pr["title"], pr.get("body"), pr.get("state"),
        _attributed(conversation + thread_comments), agent_accounts)
    card = {
        "source": "github",
        "id": pr["id"],
        "number": pr["number"],
        "repository": repo,
        "url": _cap(pr["url"], MAX_URL_CHARS),
        "title": _cap(pr["title"], MAX_TITLE_CHARS),
        "state": state,
        "author": author,
        "created_at": pr["createdAt"],
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "files": paths,
        "file_count": file_count,
        "participants": participants,
        "source_version": version,
        "source_visibility": visibility_of(repository.get("visibility")),
    }
    if state != "open":
        card["closed_at"] = pr.get("closedAt") or pr.get("mergedAt")
    if pr.get("headRefOid"):
        card["head_sha"] = pr["headRefOid"]
    merge_sha = (pr.get("mergeCommit") or {}).get("oid")
    if state == "merged" and merge_sha:
        card["merge_commit_sha"] = merge_sha
    _refuse_over_cap(card)
    skeleton = {"repository": repo, "agent_accounts": sorted(agent_accounts),
                "pull_request": card}
    report = {
        "number": pr["number"],
        "id": pr["id"],
        "repository": repo,
        "url": pr["url"],
        "branch": pr.get("headRefName"),
        "title": pr["title"],
        "state": state,
        "source_version": version,
        "source_visibility": card["source_visibility"],
        "changed_files": file_count,
        "changed_files_sent": len(paths),
        "human_texts": len(compute_texts(
            _attributed(conversation + thread_comments), agent_accounts)),
        "logins": [p["login"] for p in participants],
    }
    return skeleton, read_file(card, pr, conversation, thread_list), report


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


# Paths every pull request touches and nobody asks about: lockfiles,
# changelogs, generated or vendored output, binary assets.
NOT_BUSIEST_NAMES = frozenset((
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock", "go.sum",
    "composer.lock", "Gemfile.lock", "packages.lock.json", ".gitignore",
    "CODEOWNERS"))
NOT_BUSIEST_DIRS = frozenset((
    ".fairmind", "node_modules", "dist", "vendor", "coverage",
    ".next", "__snapshots__", "__generated__", "generated"))
# Version bumps touch these too, but they can hold real decisions: asked
# about only when nothing else is left.
LAST_RESORT_NAMES = frozenset((
    "package.json", "pyproject.toml", "setup.cfg", "Cargo.toml", "go.mod",
    "pom.xml"))
LAST_RESORT_DIRS = frozenset(("build", ".github"))
NOT_BUSIEST_SUFFIXES = (
    ".lock", ".min.js", ".min.css", ".map", ".snap", ".png", ".jpg", ".jpeg",
    ".gif", ".ico", ".svg", ".pdf", ".woff", ".woff2")
RUNNERS_UP = 4


def worth_asking(path):
    """Whether a changed path is a fair subject for a first question."""
    segments = path.split("/")
    name = segments[-1]
    # Only a repo-relative path: anything else would be probed outside --root.
    return not (path.startswith("/") or ".." in segments
                or name in NOT_BUSIEST_NAMES
                or name.upper().startswith("CHANGELOG")
                or name.lower().endswith(NOT_BUSIEST_SUFFIXES)
                or any(seg in NOT_BUSIEST_DIRS for seg in segments[:-1]))


def last_resort(path):
    segments = path.split("/")
    return (segments[-1] in LAST_RESORT_NAMES
            or any(seg in LAST_RESORT_DIRS for seg in segments[:-1]))


def busiest_file(out_dir, numbers, root):
    """The changed path most of the collected pull requests touched."""
    if not os.path.isdir(out_dir):
        raise UsageError("%s is not a directory" % out_dir)
    wanted = {str(n) for n in numbers}
    counts, bot_counts, cards = {}, {}, 0
    for name in sorted(os.listdir(out_dir)):
        if wanted and name not in wanted:
            continue
        path = os.path.join(out_dir, name, "card.json")
        try:
            with open(path, encoding="utf-8") as handle:
                card = json.load(handle)["pull_request"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        cards += 1
        # A bot's pull requests (dependency bumps) count only when no person's
        # is left: they touch the same few files and nobody asks why.
        author = card.get("author")
        tally = bot_counts if isinstance(author, dict) and author.get("is_bot") else counts
        for changed in set(card.get("files") or ()):
            if isinstance(changed, str) and worth_asking(changed):
                tally[changed] = tally.get(changed, 0) + 1
    counts = counts or bot_counts
    present = {p: n for p, n in counts.items()
               if os.path.isfile(os.path.join(root, p))}
    preferred = {p: n for p, n in present.items() if not last_resort(p)}
    ranked = sorted((-n, p) for p, n in (preferred or present).items())
    rows = [{"path": p, "pull_requests": -n} for n, p in ranked]
    top = rows[0] if rows else {"path": None, "pull_requests": 0}
    return dict(top, cards=cards, runners_up=rows[1:1 + RUNNERS_UP])


def _parser():
    parser = argparse.ArgumentParser(prog="pr_collect.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    lst = sub.add_parser("list", allow_abbrev=False)
    lst.add_argument("--repo")
    lst.add_argument("--states", default="MERGED,CLOSED")
    lst.add_argument("--since")
    lst.add_argument("--days", type=int)
    lst.add_argument("--limit", type=int)
    lst.add_argument("--out")
    sub.add_parser("remote", allow_abbrev=False)
    red = sub.add_parser("redact", allow_abbrev=False)
    red.add_argument("--read", required=True)
    red.add_argument("--erased", action="append", required=True)
    bus = sub.add_parser("busiest", allow_abbrev=False)
    bus.add_argument("--out-dir", required=True)
    bus.add_argument("--number", type=int, action="append", default=[])
    bus.add_argument("--root", default=".")
    col = sub.add_parser("collect", allow_abbrev=False)
    col.add_argument("--repo")
    which = col.add_mutually_exclusive_group(required=True)
    which.add_argument("--number", type=int)
    which.add_argument("--since")
    which.add_argument("--days", type=int)
    col.add_argument("--out-dir", required=True)
    col.add_argument("--agent-account", action="append", default=[])
    col.add_argument("--allow-open", action="store_true")
    col.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    col.add_argument("--resume", action="store_true")
    return parser


def _write_card(out_dir, number, skeleton, reading):
    """Write `card.json` and `read.md`; return both paths. The part files of
    an earlier shape are removed so none can be sent; a staged `write.json`
    stays, because it belongs to the version it was staged for."""
    target = os.path.join(out_dir, str(number))
    card, read = (os.path.join(target, "card.json"),
                  os.path.join(target, "read.md"))
    try:
        makedirs_ignored(target)
        for name in os.listdir(target):
            if re.match(r"^part-\d+\.json$", name):
                os.remove(os.path.join(target, name))
        left = [n for n in os.listdir(target)
                if re.match(r"^part-\d+\.json$", n)]
        if left:
            raise OSError(0, "stale %s could not be removed" % left[0])
        _atomic_write_json(card, skeleton, prefix=".fm-pr-", mode=0o644,
                           indent=2, sort_keys=True, trailing_newline=True)
        _atomic_write_text(read, reading)
    except OSError as err:
        raise WriteError("cannot write the card files under %s: %s"
                         % (target, err.strerror or err.__class__.__name__))
    return card, read


def _atomic_write_text(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".fm-pr-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


class ReportUnreadable(Exception):
    """A collection report that is not a JSON object."""


class ReportMissing(ReportUnreadable):
    """A collection report whose file does not exist."""


def read_collection_report(path):
    """The collection report at `path`, an object. A consumer sends or links
    only the pull requests of a report whose `complete` is true; this reader
    does not decide that, `skipped_report` names the ones left out."""
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except FileNotFoundError:
        raise ReportMissing("%s does not exist" % path)
    except (OSError, ValueError):
        raise ReportUnreadable("cannot read the collection report %s as JSON"
                               % path)
    if not isinstance(report, dict):
        raise ReportUnreadable("the collection report %s must be an object"
                               % path)
    return report


def repeated_repository(report, seen):
    """The repository `report` names when an earlier report of the same run
    already did (compared case-insensitively), else None; the name is added
    to `seen`, the set of lower-cased names of the reports read so far."""
    name = str(report.get("repository") or "")
    if name and name.lower() in seen:
        return name
    if name:
        seen.add(name.lower())
    return None


def skipped_report(path, report):
    """The entry that lists a report left out because its collection did not
    finish."""
    return {"path": path, "repository": report.get("repository"),
            "reason": "the collection is not complete"}


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


def _is_card_report(report):
    """Whether every collected entry is of the current shape: it names its
    card and its source version. A report of the part-file shape has neither,
    and its entries are collected again."""
    return all(isinstance(p, dict) and p.get("card") and p.get("source_version")
               for p in report["pull_requests"])


def collect_window(repo, since, out_dir, max_seconds, resume,
                   agent_accounts=()):
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
                               or report.get("complete") is True
                               or not _is_card_report(report)):
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
            skeleton, reading, entry = build_card(
                repo, number, False, agent_accounts)
            entry["card"], entry["read"] = _write_card(
                out_dir, number, skeleton, reading)
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
        "not_collected": report["not_collected"],
        "logins": logins,
        "collected": path,
        "files_cut": [p["number"] for p in report["pull_requests"]
                      if p.get("changed_files_sent", 0)
                      < p.get("changed_files", 0)],
    }


def _redact(args):
    try:
        with open(args.read, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as err:
        raise UsageError("cannot read %s: %s" % (args.read, err.strerror))
    before = redaction_record(args.read)
    text, removed, dropped = redact_reading(text, args.erased)
    try:
        _atomic_write_text(args.read, text)
        save_redaction_record(args.read, args.erased, record=before)
    except OSError as err:
        raise WriteError("cannot write %s: %s" % (args.read, err.strerror))
    return {"read": args.read, "comments_left_out": removed,
            "description_left_out": dropped}


def _run(args):
    if args.command == "remote":
        repo = _repo_of(argparse.Namespace(repo=None))
        return {"repository": repo, "git_remote": public_remote(_origin_url())}
    if args.command == "redact":
        return _redact(args)
    if args.command == "busiest":
        return busiest_file(args.out_dir, args.number, args.root)
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
    since = _window(args)
    if since is not None:
        if args.resume and args.days is not None:
            since = _frozen_since(os.path.join(args.out_dir, "collected.json"),
                                  repo, since)
        if args.allow_open:
            raise UsageError("--allow-open applies to one --number only")
        if not 1 <= args.max_seconds <= 3600:
            raise UsageError("--max-seconds must be between 1 and 3600")
        return collect_window(repo, since, args.out_dir, args.max_seconds,
                              args.resume, args.agent_account)
    if args.resume:
        raise UsageError("--resume applies to a --since/--days collect only")
    skeleton, reading, report = build_card(
        repo, args.number, args.allow_open, args.agent_account)
    report["card"], report["read"] = _write_card(
        args.out_dir, args.number, skeleton, reading)
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
