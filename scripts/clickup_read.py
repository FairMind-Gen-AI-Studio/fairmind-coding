#!/usr/bin/env python3
"""Read a ClickUp workspace through its REST API for the brain seed.

    clickup_read.py check [--local]
    clickup_read.py root --link URL
    clickup_read.py list --root KIND:ID [--root KIND:ID ...] --days N --out FILE
                         [--vocabulary FILE]
    clickup_read.py map  --vocabulary FILE --mapping FILE [--answers FILE]
                         [--project P]
    clickup_read.py read --tasks FILE --mapping FILE --out-dir DIR
                         [--project P] [--vocabulary FILE] [--raw FILE ...]
                         [--only ID ...]
    clickup_read.py get  --lookup FILE --mapping FILE --out FILE
                         [--project P] [--vocabulary FILE] [--tasks FILE]
                         [--raw FILE ...]

The hosted ClickUp connector allows a few hundred calls a day; the REST API
allows 100 a minute. This script reads with a personal token and nothing else.
It only ever sends GET requests, to `api.clickup.com/api/v2`. The meaning of
the rows, the vocabulary and the mapping file is in
`skills/brain-seed/references/tracker-clickup.md` ("Through the token reader").

The token

* is read from the environment variable `CLICKUP_API_TOKEN` and from nowhere
  else, stripped of surrounding whitespace; a token with a character outside
  printable ASCII is refused before any request (`check` says so without
  echoing it, other commands exit 4); it is sent only as the `Authorization`
  header;
* is never in an argument, a file, a message or the JSON on stdout: every text
  this script prints has the token's value replaced before it leaves.

Requests

* At most 90 a minute (the API's limit is 100); the script waits when the
  window is full.
* A 429 waits until the time in `X-RateLimit-Reset` (at most 65 s at a time).
  All waiting in a run, the 5xx backoff included, shares one budget of 10
  minutes; past it, or after five 429s on one request, the item is not read.
* A 5xx or a network error is retried three times (1 s, 2 s, 4 s apart). An
  item that still fails, or that ClickUp answers with another 4xx, is listed in
  `not_read` with the reason: it is never dropped silently.
* A 401 or 403 is read by the `ECODE` of its body: a token code (OAUTH_025,
  the one seen live) means the token is the problem and the
  command stops at once (exit 4, "ClickUp refused the token"), writing nothing,
  except `read`, which keeps the tickets it had already written and says so;
  OAUTH_027 (team not authorized) means only that item is out of reach: it is
  `absent`. Any other code is checked against `GET /user` (reused for a minute,
  then asked again, so a token revoked in the middle of a run is noticed):
  refused there is the first case, answered there the second.

Commands

* `root` turns a ClickUp web address into the `--root` value `list` takes, with
  no request and no token: `{"root": "KIND:ID"}`. Only an address that names
  its kind is accepted; any other exits 2.
* `check` asks `GET /user` and prints `{"ready": true}` or `{"ready": false,
  "reason": "..."}`; it exits 0 either way. `check --local` makes no request and
  prints `{"token_set": bool}`.
* `list` writes the tasks of the roots (`space:ID`, `folder:ID`, `list:ID`,
  `task:ID`, `task:WORKSPACE/ID`; the kind is explicit because ids cannot be
  told apart by shape) as sorted rows to `--out`: the tasks of a space, folder
  or list updated inside the last N days, and every task of a `task:` root's
  tree whatever its date (an epic is a pick, not a filter). It also writes
  the vocabulary its tasks and their parents use to `--vocabulary` (default
  `clickup-vocabulary.json` beside `--out`), and every task it downloaded to
  `clickup-tasks.raw.json` beside `--out` (`{id: task}`, merged with what the
  file holds). A task root costs one request per descendant, old ones included,
  up to 2000 tasks. Summary: `{"command", "roots", "tasks_seen",
  "outside_window", "rows", "out", "vocabulary", "not_read"}`.
* `map` prints `{"command", "project", "merged", "agent_accounts",
  "agent_accounts_asked", "pending"}` and, with `--answers`, validates and
  merges the agent's decisions into the mapping file (an invalid answer exits 2
  and writes nothing). The project's entry holds `agent_accounts` once the
  question about them was answered, even as `[]`; that presence is
  `agent_accounts_asked`.
* `read` writes `DIR/clickup-<id>/` (`ticket.json`, `description.txt`,
  `comment-<n>.txt`) for the `--tasks` rows whose source is `clickup`, in the
  format `ticket_card.py assemble` reads. A task already in a `--raw` file
  (repeatable) is not downloaded again. A task whose status or type, or whose
  parent's type, the mapping lacks is listed in `unmapped` and gets no directory
  (`map` the names, then `--only ID` it again). The vocabulary met is merged
  into `--vocabulary` (default beside `--tasks`). A token refused mid-run
  stops the reading, prints the summary with `refused: true` and exits 4.
  Summary: `{"command", "tasks", "written", "written_ids", "refused",
  "parents_without_kind", "vocabulary", "out_dir", "unmapped", "not_read"}`.
* `get` reads the ids of `trackers.clickup` in `--lookup` (a permanent id or a
  custom task id) and each ticket's parent chain into rows in `--out`, merges
  the vocabulary it met, and writes the tasks it downloaded to the raw cache
  beside `--out`. A custom id is looked up first in the `key` of the `--tasks`
  rows and in the `--raw` tasks, then in ClickUp. An id ClickUp does not hold
  is `absent`. Summary: `{"command", "asked", "rows", "absent", "absent_ids",
  "parents_without_kind", "out", "vocabulary", "not_read"}`.

Exit codes: 0 ok; 2 usage, or an input file that does not exist; 3 an input file
that is not readable or not of the right shape (including a mapping or
vocabulary with a bad entry), or any entry in `not_read` or `unmapped`
(everything else is still finished and written); 4 the token is missing,
malformed or refused by ClickUp (the message says "ClickUp refused the token"
for the last, never the token); 5 a file cannot be written. stdout is one JSON
document; diagnostics go to stderr. stdlib only.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from _pull_request_url import is_pull_request_url
from source_version import normalise
from ticket_card import safe

API_BASE = "https://api.clickup.com/api/v2"
TOKEN_ENV = "CLICKUP_API_TOKEN"
REFUSED_MESSAGE = "ClickUp refused the token"
VOCABULARY_SCHEMA = "brain-seed-clickup-vocabulary/1"
VOCABULARY_NAME = "clickup-vocabulary.json"
RAW_NAME = "clickup-tasks.raw.json"
TOKEN_CODES = frozenset({"OAUTH_025"})
ITEM_CODES = frozenset({"OAUTH_027"})
ROOT_KINDS = ("space", "folder", "list", "task")

PACE_LIMIT = 90
PACE_WINDOW = 60.0
RETRIES = 3
BACKOFF = (1.0, 2.0, 4.0)
MAX_RATE_WAITS = 5
MAX_SINGLE_WAIT = 65.0
MAX_TOTAL_WAIT = 600.0
DEFAULT_RATE_WAIT = 60.0
TIMEOUT = 30
MAX_PAGES = 1000
MAX_TREE_NODES = 2000
CONFIRM_TTL = 60.0
TASK_QUERY = {"include_subtasks": "true", "include_markdown_description": "true"}
PAGE_SIZE = 100
DAY_MS = 86400 * 1000

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CUSTOM_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_WORKSPACE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_LINK = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_TRAILING = ".,;:!?*_"
_TOKEN_SHAPE = re.compile(r"^[\x21-\x7e]+$")
_NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError)


class Usage(Exception):
    pass


class Unauthorized(Exception):
    """One request answered 401 or 403; `Client.get` decides what it means from
    the `ECODE` of the body (None when there is none)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class Refused(Exception):
    """ClickUp refused the token (401 or 403): the command stops."""


class NotRead(Exception):
    """One request that could not be answered; the item is listed, not dropped."""


class Absent(NotRead):
    """The item is not there for this token: a 404, or a 401 on an id the token
    cannot see while the token itself is good."""


class Missing(Exception):
    pass


class Unreadable(Exception):
    pass


class WriteFailed(Exception):
    pass


def _urllib_transport(url, headers):
    """`(status, headers, body bytes)` of one GET; a network error propagates."""
    request = urllib.request.Request(url, method="GET")
    for name, value in headers.items():
        if name.lower() == "authorization":
            # Not copied onto a redirected request: the token goes to ClickUp only.
            request.add_unredirected_header(name, value)
        else:
            request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers.items()), err.read()


class Runtime:
    """The four things a test replaces: the transport, the monotonic clock the
    pacing reads, the sleep, and the wall clock (epoch seconds) that `now` and
    `X-RateLimit-Reset` are read against."""

    def __init__(self, transport=None, clock=None, sleep=None, wall=None):
        self.transport = transport or _urllib_transport
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.wall = wall or time.time


def redact(text, token):
    """`text` with the token replaced, also in the forms Python's own error
    messages give a string (`repr`, and `repr` of its bytes)."""
    text = str(text)
    if token:
        for form in (token, repr(token)[1:-1], repr(token.encode("utf-8"))[2:-1]):
            if form:
                text = text.replace(form, "[redacted]")
    return text


def _ecode(body):
    """The `ECODE` of an error body, or None."""
    try:
        code = json.loads(body.decode("utf-8")).get("ECODE")
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    return code if isinstance(code, str) else None


class Client:
    def __init__(self, token, runtime):
        self.token = token
        self.runtime = runtime
        self.stamps = collections.deque()
        self.waited = 0.0
        self.confirmed_at = None
        self.team_ids = None

    def _wait(self, seconds):
        if seconds <= 0:
            return
        self.waited += seconds
        self.runtime.sleep(seconds)

    def _pace(self):
        while True:
            now = self.runtime.clock()
            while self.stamps and now - self.stamps[0] >= PACE_WINDOW:
                self.stamps.popleft()
            if len(self.stamps) < PACE_LIMIT:
                break
            # A sleep may return early: ask again until the window has room.
            self.runtime.sleep(max(PACE_WINDOW - (now - self.stamps[0]), 0.001))
        self.stamps.append(now)

    def teams(self):
        """The ids of the token's teams (workspaces), asked once."""
        if self.team_ids is None:
            answer = self.get("/team")
            self.team_ids = [t for t in (_ident(x) for x in answer.get("teams") or []) if t]
        return self.team_ids

    def _rate_wait(self, headers):
        try:
            seconds = float(headers.get("x-ratelimit-reset")) - self.runtime.wall()
        except (TypeError, ValueError):
            seconds = DEFAULT_RATE_WAIT
        return min(max(seconds, 1.0), MAX_SINGLE_WAIT)

    def get(self, path, query=None):
        """The parsed JSON answer of `GET path`; raises Refused or NotRead."""
        try:
            return self._get(path, query)
        except Unauthorized as err:
            if path == "/user" or err.code in TOKEN_CODES:
                self.confirmed_at = None
                raise Refused(REFUSED_MESSAGE)
            if err.code in ITEM_CODES:
                raise Absent("%s: not reachable with this token" % err)
            try:
                # An unrecognised code is read against the token's own state; the
                # answer is reused for CONFIRM_TTL seconds, then asked again, so
                # a token revoked mid-run is noticed.
                now = self.runtime.clock()
                if self.confirmed_at is None or now - self.confirmed_at >= CONFIRM_TTL:
                    self._get("/user")
                    self.confirmed_at = self.runtime.clock()
            except Unauthorized:
                self.confirmed_at = None
                raise Refused(REFUSED_MESSAGE)
            except NotRead:
                raise NotRead("%s, and ClickUp could not confirm the token" % err)
            raise Absent("%s: not reachable with this token" % err)

    def _get(self, path, query=None):
        url = API_BASE + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request_headers = {"Authorization": self.token,
                           "Accept": "application/json"}
        failures = 0
        rate_waits = 0
        while True:
            self._pace()
            try:
                status, headers, body = self.runtime.transport(url, request_headers)
                failure = None
            except _NETWORK_ERRORS as err:
                status, headers, body = None, {}, b""
                failure = "%s: %s" % (err.__class__.__name__,
                                      redact(err, self.token)[:200])
            headers = {str(k).lower(): v for k, v in (headers or {}).items()}
            if status in (401, 403):
                raise Unauthorized("HTTP %d" % status, _ecode(body))
            if status == 429:
                rate_waits += 1
                wait = self._rate_wait(headers)
                if rate_waits > MAX_RATE_WAITS or self.waited + wait > MAX_TOTAL_WAIT:
                    raise NotRead("rate limit: still limited after the wait bound")
                self._wait(wait)
                self.stamps.clear()
                continue
            if failure is not None or (status is not None and status >= 500):
                if failures >= RETRIES or self.waited + BACKOFF[failures] > MAX_TOTAL_WAIT:
                    raise NotRead(failure or "HTTP %d" % status)
                self._wait(BACKOFF[failures])
                failures += 1
                continue
            if status == 404:
                raise Absent("HTTP 404")
            if not 200 <= status < 300:
                raise NotRead("HTTP %d" % status)
            try:
                document = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise NotRead("the answer is not JSON")
            if not isinstance(document, dict):
                raise NotRead("the answer is not a JSON object")
            return document


# ----------------------------------------------------------------- root

_LINK_KINDS = {"li": "list", "f": "folder", "s": "space"}


def root_command(args):
    parts = urllib.parse.urlsplit(args.link.strip())
    segments = [p for p in parts.path.split("/") if p]
    if parts.scheme not in ("http", "https") or (parts.hostname or "").split(".", 1) != ["app", "clickup.com"]:
        raise Usage("not a ClickUp web address: give the id and its kind")
    if segments and re.fullmatch(r"\d+", segments[0]):
        segments = segments[1:]
    text = None
    if len(segments) == 2 and segments[0] == "t":
        text = "task:%s" % segments[1]
    elif len(segments) == 3 and segments[0] == "t" and re.fullmatch(r"\d+", segments[1]):
        text = "task:%s/%s" % (segments[1], segments[2])
    elif len(segments) == 3 and segments[0] == "v" and segments[1] in _LINK_KINDS:
        text = "%s:%s" % (_LINK_KINDS[segments[1]], segments[2])
    try:
        _parse_root(text or "")
    except Usage:
        raise Usage("this address does not name a task, list, folder or space "
                    "(t/ID, v/li/ID, v/f/ID, v/s/ID): give the id and its kind")
    return {"command": "root", "root": text}


# ---------------------------------------------------------------- check

def check(runtime):
    try:
        token = _token()
    except Refused as err:
        return {"ready": False, "reason": str(err)}
    try:
        Client(token, runtime).get("/user")
    except Refused:
        return {"ready": False, "reason": "ClickUp refused the token"}
    except NotRead as err:
        return {"ready": False,
                "reason": "ClickUp could not be asked: %s" % redact(err, token)}
    return {"ready": True}


# ----------------------------------------------------------------- list

def _parse_root(text):
    kind, _, ident = text.partition(":")
    team, slash, tail = ident.rpartition("/")
    if kind == "task":
        ok = _CUSTOM_ID.match(tail) and (not slash or _WORKSPACE.match(team))
    else:
        ok = kind in ROOT_KINDS and _ID.match(ident)
    if not ok:
        raise Usage("a root is space:ID, folder:ID, list:ID, task:ID or task:WORKSPACE/ID, "
                    "not %r" % text)
    return kind, ident


def _ms(task):
    value = task.get("date_updated")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ms_iso(value):
    """ISO-8601 UTC of a millisecond epoch given as number or text, else None."""
    try:
        moment = datetime.datetime.fromtimestamp(int(value) / 1000.0, datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_document_link(link):
    """False for the links a task text holds that are not documents: ClickUp
    task links, ClickUp attachments, pull request pages and GitHub commits
    (those have their own readers)."""
    parts = urllib.parse.urlsplit(link)
    host = (parts.hostname or "").lower()
    if host == "clickup.com" or host.endswith(".clickup.com"):
        if host == "attachments.clickup.com" or re.search(r"/t/", parts.path):
            return False
    if host.endswith("clickup-attachments.com"):
        return False
    if host in ("github.com", "www.github.com") and re.match(
            r"^/[^/]+/[^/]+/commit/", parts.path):
        return False
    return not is_pull_request_url(link)


def _links(text):
    seen = []
    for found in _LINK.findall(text or ""):
        link = found.rstrip(_TRAILING)
        if link and link not in seen and _is_document_link(link):
            seen.append(link)
    return seen


def _ident(item):
    """A response item's id as text, or None when it has none."""
    value = item.get("id") if isinstance(item, dict) else None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def resolve_task(client, ident, team=None):
    """A task by its permanent id or by a custom task id, as ClickUp returns it
    (subtasks and markdown included). Raises Absent when ClickUp holds neither,
    NotRead on an error or when a custom id is ambiguous.

    A custom id needs a workspace. With `team` (from a `/t/WORKSPACE/ID` link)
    it is looked up there only; without, in every team of the token, and a
    match in more than one is refused rather than guessed."""
    if not _CUSTOM_ID.match(ident):
        raise Absent("not an id")
    quoted = urllib.parse.quote(ident, safe="")

    def plain():
        return client.get("/task/%s" % quoted, TASK_QUERY)

    def custom():
        forms = [ident] + ([ident.upper()] if ident != ident.upper() else [])
        found, error = {}, None
        for one in [team] if team else client.teams():
            for form in forms:
                try:
                    task = client.get("/task/%s" % urllib.parse.quote(form, safe=""),
                                      dict(TASK_QUERY, custom_task_ids="true", team_id=one))
                except Absent:
                    continue
                except NotRead as err:
                    error = error or err
                    continue
                if _ident(task) is None:
                    error = error or NotRead("the answer has no id")
                    continue
                found.setdefault(_ident(task), task)
                break
        if len(found) > 1:
            raise NotRead("the custom id %s matches a ticket in %d workspaces; "
                          "give the workspace (a /t/WORKSPACE/ID address)" % (ident, len(found)))
        if found:
            return next(iter(found.values()))
        raise error or Absent("HTTP 404")

    failures = []
    for attempt in ((custom,) if team else (custom, plain) if "-" in ident else (plain, custom)):
        try:
            return attempt()
        except NotRead as err:
            failures.append(err)
    real = [f for f in failures if not isinstance(f, Absent)]
    raise (real or failures)[0]


class Source:
    """What `Lister` and `Reader` share: the client, the items not read, the
    custom type names, and the statuses and types met (the vocabulary)."""

    def __init__(self, client):
        self.client = client
        self.not_read = []
        self.types = Types(client, self.miss)
        self.statuses = set()
        self.names = set()

    def miss(self, kind, ident, err):
        entry = {"kind": kind, "id": ident, "reason": str(err)}
        if entry not in self.not_read:
            self.not_read.append(entry)

    def sorted_misses(self):
        return sorted(self.not_read, key=lambda e: (e["kind"], e["id"], e["reason"]))

    def fetch_raw(self, ident):
        """`GET /task/ident` (subtasks and markdown included), once, uncached."""
        return self.client.get("/task/%s" % urllib.parse.quote(ident, safe=""), TASK_QUERY)

    def note_status(self, task):
        state = task.get("status") or {}
        if state.get("status"):
            self.statuses.add((str(state["status"]), str(state.get("type") or "")))

    def note_type(self, task):
        """The task's custom type name, or None; the name joins the vocabulary."""
        name = self.types.name(task)
        if name:
            self.names.add(name)
        return name

    def vocabulary(self):
        return _vocabulary_doc(self.statuses, self.names, self.types.unnamed)

    def kind_of(self, task, mapping):
        """`(kind, reference)`: kind is None when the mapping does not hold the
        task's type; the reference is what the agent maps (None for a plain task)."""
        item = Types.item_of(task)
        if item is None:
            return "task", None
        name = self.note_type(task)
        ref = {"type": name} if name else {"custom_item_id": int(item) if item.isdigit() else item}
        return mapping.kind(ref), ref


class Lister(Source):
    def __init__(self, client, cutoff):
        super().__init__(client)
        self.cutoff = cutoff
        self.tasks = {}
        self.parents = {}
        self.requested = set()
        self.picked = set()

    def add(self, task):
        ident = _ident(task)
        if ident is None:
            self.miss("task", "(no id)", "the answer has no id")
            return None
        self.tasks.setdefault(ident, task)
        return ident

    def ids_of(self, kind, owner, items):
        found = []
        for item in items:
            ident = _ident(item)
            if ident is None:
                self.miss(kind, owner, "the answer has no id")
            else:
                found.append(ident)
        return found

    def lists_of_space(self, space_id):
        found = []
        try:
            folders = self.client.get("/space/%s/folder" % space_id,
                                      {"archived": "false"}).get("folders") or []
        except NotRead as err:
            self.miss("space", space_id, err)
            folders = []
        for folder in folders:
            folder_id = _ident(folder)
            if folder_id is None:
                self.miss("space", space_id, "the answer has no id")
            elif isinstance(folder.get("lists"), list):
                found.extend(self.ids_of("space", space_id, folder["lists"]))
            else:
                found.extend(self.lists_of_folder(folder_id))
        try:
            loose = self.client.get("/space/%s/list" % space_id,
                                    {"archived": "false"}).get("lists") or []
        except NotRead as err:
            self.miss("space", space_id, err)
            loose = []
        found.extend(self.ids_of("space", space_id, loose))
        return found

    def lists_of_folder(self, folder_id):
        try:
            lists = self.client.get("/folder/%s/list" % folder_id,
                                    {"archived": "false"}).get("lists") or []
        except NotRead as err:
            self.miss("folder", folder_id, err)
            return []
        return self.ids_of("folder", folder_id, lists)

    def read_list(self, list_id):
        """The tasks of a list updated after the window's start, every page.
        A task that fails to parse is listed; a page that fails ends the list."""
        found = []
        for page in range(MAX_PAGES):
            try:
                answer = self.client.get(
                    "/list/%s/task" % list_id,
                    {"subtasks": "true", "include_closed": "true",
                     "include_markdown_description": "true",
                     "include_timl": "true",
                     "date_updated_gt": self.cutoff - 1, "page": page})
            except NotRead as err:
                self.miss("list", list_id, err)
                return found
            tasks = answer.get("tasks")
            tasks = [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []
            found.extend(tasks)
            last = answer.get("last_page")
            if last is True or not tasks:
                return found
            if last is None and len(tasks) < PAGE_SIZE:
                return found
        self.miss("list", list_id, "more than %d pages" % MAX_PAGES)
        return found

    def fetch(self, ident):
        """`GET /task/ident` once per id; the task, or None when not read."""
        if ident in self.requested:
            return self.tasks.get(ident)
        self.requested.add(ident)
        try:
            task = self.fetch_raw(ident)
        except NotRead as err:
            self.miss("task", ident, err)
            return None
        return task if self.add(task) else None

    def resolve_root(self, ident, team=None):
        """The root task by its permanent id or by a custom task id; a miss is
        listed once."""
        if ident in self.requested:
            return self.tasks.get(ident)
        try:
            task = resolve_task(self.client, ident, team)
        except NotRead as err:
            self.requested.add(ident)
            self.miss("task", ident, err)
            return None
        self.requested.add(ident)
        permanent = self.add(task)
        if permanent:
            self.requested.add(permanent)
        return task if permanent else None

    def stubs(self, task):
        """The ids of the subtasks a task response names."""
        found = []
        for child in task.get("subtasks") or []:
            ident = _ident(child)
            if ident is None:
                self.miss("task", "(no id)", "the answer has no id")
            else:
                found.append(ident)
        return found

    def read_task_tree(self, root_text):
        """The task and every descendant. The walk follows each stub whatever
        its date (an old intermediate can have a recent child, and a task
        response names direct children only), each id read once. Every task it
        reaches is a deliberate pick and goes into `picked`, which the window
        never cuts. A tree over MAX_TREE_NODES is cut and said so."""
        team, _, root_id = root_text.rpartition("/")
        root = self.resolve_root(root_id, team or None)
        if root is None:
            return
        root_id = _ident(root)
        self.picked.add(root_id)
        queue = collections.deque(self.stubs(root))
        reached = 1
        while queue:
            ident = queue.popleft()
            if ident in self.requested:
                continue
            if reached >= MAX_TREE_NODES:
                self.miss("task", root_id, "the tree has more than %d tasks; "
                          "the rest was not read" % MAX_TREE_NODES)
                break
            task = self.fetch(ident)
            reached += 1
            if task is not None:
                self.picked.add(ident)
                queue.extend(self.stubs(task))

    def fetch_parents(self):
        """Each parent the result set lacks, once: its status and type join the
        vocabulary, so that the first `map` sees every name `read` will meet. A
        parent ClickUp does not show this token is left out."""
        wanted = {str(t["parent"]) for t in self.tasks.values() if t.get("parent")}
        for ident in sorted(wanted - set(self.tasks) - self.requested):
            try:
                task = self.fetch_raw(ident)
            except Absent:
                continue
            except NotRead as err:
                self.miss("task", ident, err)
                continue
            permanent = _ident(task)
            if permanent is None:
                self.miss("task", ident, "the answer has no id")
            else:
                self.parents[permanent] = task


class Types:
    """The name of a task's custom type, from the team's table (read once per
    team); an id the table does not name is remembered for the vocabulary."""

    def __init__(self, client, miss):
        self.client = client
        self.miss = miss
        self.tables = {}
        self.unnamed = set()

    @staticmethod
    def item_of(task):
        value = task.get("custom_item_id")
        item = str(value if value is not None else 0).strip()
        return None if item in ("", "0") else item

    def name(self, task):
        item = self.item_of(task)
        if item is None:
            return None
        team = str(task.get("team_id") or "")
        if team not in self.tables:
            table = {}
            try:
                if not team:
                    raise NotRead("the task names no team")
                answer = self.client.get("/team/%s/custom_item" % team)
                for entry in answer.get("custom_items") or []:
                    if _ident(entry) is not None and entry.get("name"):
                        table[_ident(entry)] = str(entry["name"])
            except NotRead as err:
                self.miss("custom_types", team or "(no team)", err)
            self.tables[team] = table
        name = self.tables[team].get(item)
        if name is None:
            self.unnamed.add(item)
        return name


def _id_order(n):
    return (not n.isdigit(), int(n) if n.isdigit() else 0, n)


def _vocabulary_doc(statuses, names, unnamed):
    return {"schema": VOCABULARY_SCHEMA,
            "statuses": [{"status": s, "status_type": t} for s, t in sorted(statuses)],
            "types": sorted(names) + [
                {"custom_item_id": int(n) if n.isdigit() else n}
                for n in sorted(unnamed, key=_id_order)]}


def _load_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise Missing("%s does not exist" % path)
    except (OSError, ValueError):
        raise Unreadable("cannot read %s as JSON (%s)" % (path, what))


def _merge_vocabulary(path, statuses, names, unnamed):
    """Write the vocabulary of what a command met, joined with the file's own."""
    if os.path.exists(path):
        old = _load_vocabulary(path)
        statuses = set(statuses) | {(str(e["status"]), str(e.get("status_type") or ""))
                                    for e in old.get("statuses") or []}
        names, unnamed = set(names), set(unnamed)
        for entry in old.get("types") or []:
            if isinstance(entry, dict):
                unnamed.add(str(entry["custom_item_id"]))
            else:
                names.add(str(entry))
    _write_json(path, _vocabulary_doc(statuses, names, unnamed))


def _row(task, type_name, parent_kind=None, links=True):
    row = {"source": "clickup", "id": str(task["id"]),
           "title": " ".join(str(task.get("name") or "").split()),
           "url": task.get("url"), "updated": _ms_iso(task.get("date_updated"))}
    if task.get("custom_id"):
        row["key"] = str(task["custom_id"])
    if type_name:
        row["type"] = type_name
    if task.get("parent"):
        row["parent"] = {"id": str(task["parent"])}
        if parent_kind:
            row["parent"]["kind"] = parent_kind
    found = _links(task.get("markdown_description")) if links else []
    if found:
        row["cited_links"] = found
    return {k: v for k, v in row.items() if v is not None}


def _valid_token(token):
    if not token:
        return "%s is not set" % TOKEN_ENV
    if not _TOKEN_SHAPE.match(token):
        return "%s holds a character that cannot be sent in a header" % TOKEN_ENV
    return None


def _token():
    """The token of the environment; Refused, saying why, when it is unusable."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    problem = _valid_token(token)
    if problem:
        raise Refused(problem)
    return token


def _beside(explicit, path, name):
    """`explicit`, else `name` in the directory of `path`."""
    return explicit or os.path.join(os.path.dirname(path) or ".", name)


def list_tasks(args, runtime):
    token = _token()
    roots = []
    for text in args.root:
        root = _parse_root(text)
        if root not in roots:
            roots.append(root)
    if args.days < 0:
        raise Usage("--days must not be negative")
    cutoff = int(runtime.wall() * 1000) - args.days * DAY_MS
    lister = Lister(Client(token, runtime), cutoff)
    list_ids = []
    for kind, ident in roots:
        if kind == "space":
            list_ids.extend(lister.lists_of_space(ident))
        elif kind == "folder":
            list_ids.extend(lister.lists_of_folder(ident))
        elif kind == "list":
            list_ids.append(ident)
    for ident in dict.fromkeys(list_ids):
        for task in lister.read_list(ident):
            lister.add(task)
    for kind, ident in roots:
        if kind == "task":
            lister.read_task_tree(ident)
    lister.fetch_parents()

    rows, outside = [], 0
    for ident in sorted(lister.tasks):
        task = lister.tasks[ident]
        lister.note_status(task)
        name = lister.note_type(task)
        updated = _ms(task)
        if ident in lister.picked or (updated is not None and updated >= cutoff):
            rows.append(_row(task, name))
        elif updated is None:
            lister.miss("task", ident, "no usable date_updated")
        else:
            outside += 1
    for task in lister.parents.values():
        lister.note_status(task)
        lister.note_type(task)
    vocabulary_path = _beside(args.vocabulary, args.out, VOCABULARY_NAME)
    _write_json(args.out, rows)
    _write_json(vocabulary_path, lister.vocabulary())
    _merge_raw(_beside(None, args.out, RAW_NAME), dict(lister.parents, **lister.tasks))
    return {"command": "list", "roots": ["%s:%s" % root for root in roots],
            "tasks_seen": len(lister.tasks), "outside_window": outside,
            "rows": len(rows), "out": args.out, "vocabulary": vocabulary_path,
            "not_read": lister.sorted_misses()}


def _load_raw(paths):
    """The tasks of the raw caches `paths`, by id (a later file wins)."""
    tasks = {}
    for path in paths or ():
        doc = _load_json(path, "the raw tasks")
        if not isinstance(doc, dict) or not all(isinstance(t, dict) for t in doc.values()):
            raise Unreadable("%s is not a map of ClickUp tasks by id" % path)
        for task in doc.values():
            if _ident(task) is not None:
                tasks[_ident(task)] = task
    return tasks


def _merge_raw(path, tasks):
    """Write the raw cache of what a command downloaded, joined with the file's own."""
    merged = _load_raw([path]) if os.path.exists(path) else {}
    merged.update(tasks)
    _write_json(path, merged)


def _write_json(path, document):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-clickup-", mode=0o644,
                           indent=2, sort_keys=True, trailing_newline=True)
    except OSError as err:
        raise WriteFailed("cannot write %s: %s"
                          % (path, err.strerror or err.__class__.__name__))


# -------------------------------------------------------------- mapping

MAPPING_SCHEMA = "brain-seed-clickup-mapping/1"
DEFAULT_PROJECT = "default"
CATEGORIES = ("todo", "in_progress", "done", "cancelled")
KINDS = ("task", "issue", "requirement")


def _norm(text):
    return normalise(str(text)).casefold()


def _status_key(status, status_type):
    return (_norm(status), str(status_type or "").casefold())


def _type_key(entry):
    if "custom_item_id" in entry:
        return ("id", str(entry["custom_item_id"]))
    return ("name", _norm(entry["type"]))


_ACCOUNT = re.compile(r"^[1-9][0-9]*$")


def _parse_entry(doc, fail, stored=False):
    """The statuses, types and agent accounts of an answers file or of one
    project's mapping, validated; `fail(message)` builds the exception. A
    stored entry may also carry the flag `agent_accounts_asked`, which is read
    and never written: the key `agent_accounts` says the question was answered."""
    allowed = {"statuses", "types", "agent_accounts"} | ({"agent_accounts_asked"} if stored else set())
    if not isinstance(doc, dict) or set(doc) - allowed:
        raise fail('expected {"statuses": [...], "types": [...], "agent_accounts": [...]}')
    statuses, types = {}, {}
    for entry in doc.get("statuses") or []:
        if (not isinstance(entry, dict)
                or set(entry) != {"status", "status_type", "status_category"}
                or not isinstance(entry["status"], str) or not entry["status"].strip()
                or not isinstance(entry["status_type"], str)):
            raise fail("a status needs exactly status, status_type and status_category")
        if entry["status_category"] not in CATEGORIES:
            raise fail("status_category must be one of %s" % ", ".join(CATEGORIES))
        clean = {"status": " ".join(entry["status"].split()),
                 "status_type": entry["status_type"],
                 "status_category": entry["status_category"]}
        key = _status_key(clean["status"], clean["status_type"])
        if key in statuses and statuses[key] != clean:
            raise fail("the status %r is given twice differently" % clean["status"])
        statuses[key] = clean
    for entry in doc.get("types") or []:
        ok = (isinstance(entry, dict) and "kind" in entry
              and set(entry) in ({"type", "kind"}, {"custom_item_id", "kind"}))
        if ok and "type" in entry:
            ok = isinstance(entry["type"], str) and entry["type"].strip()
        if ok and "custom_item_id" in entry:
            ok = (isinstance(entry["custom_item_id"], int)
                  and not isinstance(entry["custom_item_id"], bool))
        if not ok:
            raise fail("a type needs type or custom_item_id, and kind")
        if entry["kind"] not in KINDS:
            raise fail("kind must be one of %s" % ", ".join(KINDS))
        clean = dict(entry)
        if "type" in clean:
            clean["type"] = " ".join(clean["type"].split())
        key = _type_key(clean)
        if key in types and types[key] != clean:
            raise fail("the type %r is given twice differently" % (key[1],))
        types[key] = clean
    accounts = doc.get("agent_accounts") or []
    if (not isinstance(accounts, list) or not all(
            (isinstance(a, str) and _ACCOUNT.match(a))
            or (isinstance(a, int) and not isinstance(a, bool) and a > 0) for a in accounts)):
        raise fail("agent_accounts must be a list of numeric user ids")
    if "agent_accounts_asked" in doc and not isinstance(doc["agent_accounts_asked"], bool):
        raise fail("agent_accounts_asked must be true or false")
    return statuses, types, {str(a) for a in accounts}


def _load_mapping(path):
    if not os.path.exists(path):
        return {"schema": MAPPING_SCHEMA, "projects": {}}
    doc = _load_json(path, "the mapping")
    if (not isinstance(doc, dict) or doc.get("schema") != MAPPING_SCHEMA
            or not isinstance(doc.get("projects"), dict)):
        raise Unreadable("%s is not a ClickUp mapping" % path)
    for name, entry in doc["projects"].items():
        _parse_entry(entry, lambda message, n=name: Unreadable(
            "%s: project %r: %s" % (path, n, message)), stored=True)
    return doc


def _load_vocabulary(path):
    doc = _load_json(path, "the vocabulary")
    ok = (isinstance(doc, dict) and doc.get("schema") == VOCABULARY_SCHEMA
          and isinstance(doc.get("statuses"), list) and isinstance(doc.get("types"), list))
    if ok:
        ok = all(isinstance(e, dict) and isinstance(e.get("status"), str)
                 and isinstance(e.get("status_type") or "", str) for e in doc["statuses"])
    if ok:
        ok = all(isinstance(e, str) or (isinstance(e, dict) and "custom_item_id" in e
                                        and set(e) == {"custom_item_id"})
                 for e in doc["types"])
    if not ok:
        raise Unreadable("%s is not a ClickUp vocabulary" % path)
    return doc


class Mapping:
    """What the agent decided for one project: status name to category, type to
    kind, and which accounts are agents. This class only looks decisions up;
    it never makes one."""

    def __init__(self, project_entry):
        entry = project_entry or {}
        self.statuses = {_status_key(e["status"], e.get("status_type")): e["status_category"]
                         for e in entry.get("statuses") or []}
        self.types = {_type_key(e): e["kind"] for e in entry.get("types") or []}
        self.agent_accounts = set(entry.get("agent_accounts") or [])
        self.accounts_asked = "agent_accounts" in entry or bool(entry.get("agent_accounts_asked"))

    def category(self, task):
        state = task.get("status") or {}
        return self.statuses.get(_status_key(state.get("status"), state.get("type")))

    def kind(self, ref):
        return self.types.get(_type_key(ref))


def _pending(vocabulary, mapping):
    statuses, types = [], []
    for entry in vocabulary.get("statuses") or []:
        if _status_key(entry["status"], entry.get("status_type")) not in mapping.statuses:
            statuses.append({"status": entry["status"],
                             "status_type": entry.get("status_type") or ""})
    for entry in vocabulary.get("types") or []:
        ref = entry if isinstance(entry, dict) else {"type": entry}
        if _type_key(ref) not in mapping.types:
            types.append(entry)
    return {"statuses": statuses, "types": types}


def map_command(args):
    vocabulary = _load_vocabulary(args.vocabulary)
    project = args.project or DEFAULT_PROJECT
    doc = _load_mapping(args.mapping)
    entry = doc["projects"].get(project) or {"statuses": [], "types": []}
    merged = 0
    if args.answers:
        try:
            answers = _load_json(args.answers, "the answers")
            statuses, types, accounts = _parse_entry(
                answers, lambda message: Usage("answers: %s" % message))
        except Unreadable as err:
            raise Usage(str(err))
        known = Mapping(entry)
        open_s = {_status_key(e["status"], e.get("status_type"))
                  for e in vocabulary["statuses"]} | set(known.statuses)
        open_t = {_type_key(e if isinstance(e, dict) else {"type": e})
                  for e in vocabulary["types"]} | set(known.types)
        for key in statuses:
            if key not in open_s:
                raise Usage("answers: the status %r (%s) is not in the vocabulary and not mapped"
                            % (statuses[key]["status"], statuses[key]["status_type"]))
        for key in types:
            if key not in open_t:
                raise Usage("answers: the type %r is not in the vocabulary and not mapped" % (key[1],))
        old_s = {_status_key(e["status"], e.get("status_type")): e for e in entry["statuses"]}
        old_t = {_type_key(e): e for e in entry["types"]}
        old_a = set(entry.get("agent_accounts") or [])
        asked = known.accounts_asked
        now_asked = asked or "agent_accounts" in answers
        merged = int(now_asked != asked) + sum(1 for k, v in statuses.items() if old_s.get(k) != v) \
            + sum(1 for k, v in types.items() if old_t.get(k) != v) + len(accounts - old_a)
        old_s.update(statuses)
        old_t.update(types)
        entry = {"statuses": [old_s[k] for k in sorted(old_s)],
                 "types": [old_t[k] for k in sorted(old_t)]}
        if now_asked:
            entry["agent_accounts"] = sorted(old_a | accounts, key=int)
        doc["projects"][project] = entry
        if merged:
            _write_json(args.mapping, doc)
    return {"command": "map", "project": project, "merged": merged,
            "agent_accounts": sorted(entry.get("agent_accounts") or [], key=int),
            "agent_accounts_asked": Mapping(entry).accounts_asked,
            "pending": _pending(vocabulary, Mapping(entry))}


# ------------------------------------------------------- reading tickets

COMMENT_PAGE = 25


def _account(user, agents):
    """`{"login", "is_bot"}` of a ClickUp user object, or None without an id.
    ClickUp's user object has no bot flag: a bot is an account the mapping
    names as an agent, or an automation, whose id is not a positive number."""
    ident = _ident(user)
    if ident is None:
        return None
    if ident.isdigit() and int(ident) > 0:
        return {"login": ident, "is_bot": ident in agents}
    return {"login": ident, "is_bot": True}


def _project_mapping(args):
    doc = _load_mapping(args.mapping)
    return Mapping(doc["projects"].get(args.project or DEFAULT_PROJECT))


class Reader(Source):
    """Tasks by id, once each, with the lookups reading a ticket needs. `raw` is
    a map of tasks an earlier command downloaded: those are not asked again."""

    def __init__(self, client, raw=None):
        super().__init__(client)
        self.cache = {}
        self.aliases = {}
        self.downloaded = {}
        self.spaces = {}
        for task in (raw or {}).values():
            self._remember(task)

    def _remember(self, task):
        ident = _ident(task)
        self.cache.setdefault(ident, task)
        if task.get("custom_id"):
            self.aliases.setdefault(str(task["custom_id"]).casefold(), ident)
        return self.cache[ident]

    def _download(self, task):
        ident = _ident(task)
        if ident is None:
            raise NotRead("the answer has no id")
        self.downloaded[ident] = task
        return self._remember(task)

    def alias_rows(self, rows):
        """Let a custom id be found among the `key` of inventory rows: a key
        two rows share names no one task and is left to ClickUp."""
        keys = {}
        for row in rows:
            if (isinstance(row, dict) and row.get("key") and row.get("id") is not None
                    and str(row.get("source", "")).casefold() == "clickup"):
                keys.setdefault(str(row["key"]).casefold(), set()).add(str(row["id"]))
        for key, ids in keys.items():
            if len(ids) == 1:
                self.aliases.setdefault(key, next(iter(ids)))

    def fetch(self, ident):
        """The task by its permanent id, cached. Absent or NotRead propagate."""
        if ident in self.cache:
            return self.cache[ident]
        return self._download(self.fetch_raw(ident))

    def resolve(self, ident):
        """A task by its permanent id or by a custom id; None when ClickUp
        holds neither. Raises NotRead on an error and for an ambiguous custom id."""
        if ident in self.cache:
            return self.cache[ident]
        known = self.aliases.get(ident.casefold())
        if known is not None:
            try:
                return self.fetch(known)
            except Absent:
                pass
        try:
            task = resolve_task(self.client, ident)
        except Absent:
            return None
        task = self._download(task)
        self.aliases.setdefault(ident.casefold(), _ident(task))
        return task

    def private_space(self, task):
        """True when the task's space is private. Raises NotRead when it cannot
        be told: a task of unknown visibility is not written."""
        space = _ident(task.get("space"))
        if space is None:
            raise NotRead("the task names no space: its visibility is unknown")
        if space not in self.spaces:
            try:
                self.spaces[space] = bool(self.client.get("/space/%s" % space).get("private"))
            except NotRead as err:
                self.spaces[space] = err
        if isinstance(self.spaces[space], NotRead):
            raise NotRead("the space's visibility is unknown: %s" % self.spaces[space])
        return self.spaces[space]

    def comments(self, ident):
        """Comments oldest first, each followed by its replies oldest first."""
        found, query = {}, {}
        while True:
            page = self.client.get("/task/%s/comment" % ident, query).get("comments") or []
            fresh = 0
            for comment in page:
                cid = _ident(comment)
                if cid is None:
                    raise NotRead("a comment has no id")
                if cid not in found:
                    found[cid] = comment
                    fresh += 1
            if len(page) < COMMENT_PAGE or not fresh:
                break
            dated = [c for c in page if str(c.get("date") or "").isdigit()]
            if not dated:
                break
            oldest = min(dated, key=_comment_order)
            query = {"start": oldest.get("date"), "start_id": _ident(oldest)}
        ordered = []
        for comment in sorted(found.values(), key=_comment_order):
            ordered.append(comment)
            try:
                replies = int(comment.get("reply_count") or 0)
            except (TypeError, ValueError):
                replies = 0
            if replies > 0:
                answer = self.client.get("/comment/%s/reply" % _ident(comment))
                got = answer.get("comments") or []
                if len(got) < replies:
                    raise NotRead("comment %s has %d replies and ClickUp returned %d"
                                  % (_ident(comment), replies, len(got)))
                seen = set(found)
                for reply in sorted(got, key=_comment_order):
                    rid = _ident(reply)
                    if rid is None:
                        raise NotRead("a reply has no id")
                    if rid not in seen:
                        seen.add(rid)
                        ordered.append(reply)
        return ordered


def _comment_order(comment):
    try:
        date = int(comment.get("date"))
    except (TypeError, ValueError):
        date = 0
    return (date, str(_ident(comment) or ""))


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _write_ticket_dir(out_dir, ident, files):
    """The files are built in a temporary directory beside `out_dir` (never
    inside it: `ticket_card.py assemble` reads every directory it finds there)
    and renamed into place."""
    final = os.path.join(out_dir, "clickup-%s" % safe(ident))
    scratch = None
    try:
        makedirs_ignored(out_dir)
        scratch = tempfile.mkdtemp(prefix=".clickup-", dir=os.path.dirname(os.path.abspath(out_dir)))
        for name, text in files.items():
            _write_text(os.path.join(scratch, name), text)
        shutil.rmtree(final, ignore_errors=True)
        os.rename(scratch, final)
        scratch = None
    except OSError as err:
        raise WriteFailed("cannot write %s: %s" % (final, err.strerror or err.__class__.__name__))
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _remove_ticket_dir(out_dir, ident):
    shutil.rmtree(os.path.join(out_dir, "clickup-%s" % safe(ident)), ignore_errors=True)


def _lacks_kind(parent):
    return bool(parent) and "kind" not in parent


def read_command(args, runtime):
    token = _token()
    rows = _load_json(args.tasks, "the tasks")
    if not isinstance(rows, list):
        raise Unreadable("%s must be a JSON list of rows" % args.tasks)
    ids = sorted({str(r["id"]) for r in rows if isinstance(r, dict)
                  and str(r.get("source", "")).casefold() == "clickup" and r.get("id") is not None})
    if args.only is not None:
        for ident in args.only:
            if not _ID.match(ident):
                raise Usage("--only takes a task id, not %r" % ident)
        ids = [i for i in ids if i in set(args.only)]
    mapping = _project_mapping(args)
    reader = Reader(Client(token, runtime), _load_raw(args.raw))
    unmapped, written_ids, kindless, refused = [], [], 0, False
    for ident in ids:
        if refused:
            reader.miss("task", ident, REFUSED_MESSAGE)
            continue
        try:
            files, problems, parent = _ticket_files(reader, mapping, ident)
        except Refused:
            refused = True
            reader.miss("task", ident, REFUSED_MESSAGE)
            continue
        except NotRead as err:
            reader.miss("task", ident, err)
            _remove_ticket_dir(args.out_dir, ident)
            continue
        if problems:
            unmapped.extend(problems)
            _remove_ticket_dir(args.out_dir, ident)
            continue
        _write_ticket_dir(args.out_dir, ident, files)
        written_ids.append(ident)
        kindless += _lacks_kind(parent)
    vocabulary_path = _beside(args.vocabulary, args.tasks, VOCABULARY_NAME)
    _merge_vocabulary(vocabulary_path, reader.statuses, reader.names, reader.types.unnamed)
    return {"command": "read", "tasks": len(ids), "written": len(written_ids),
            "written_ids": written_ids, "refused": refused,
            "parents_without_kind": kindless, "vocabulary": vocabulary_path,
            "out_dir": args.out_dir, "unmapped": sorted(unmapped, key=json.dumps),
            "not_read": reader.sorted_misses()}


def _ticket_files(reader, mapping, ident):
    """`(files, problems, parent)` of one ticket: the files to write, or the
    mapping problems that keep it from being written. Reads nothing beyond the
    task and its parent until the mapping holds every name."""
    task = reader.fetch(ident)
    if _ident(task) != ident:
        raise NotRead("the answer is for another task")
    problems = []
    reader.note_status(task)
    category = mapping.category(task)
    state = task.get("status") or {}
    if category is None:
        problems.append({"id": ident, "what": "status", "status": state.get("status"),
                         "status_type": state.get("type") or ""})
    kind, ref = reader.kind_of(task, mapping)
    if kind is None:
        problems.append(dict(ref, id=ident, what="type"))
    parent = None
    if task.get("parent"):
        parent = {"id": str(task["parent"])}
        try:
            parent_task = reader.fetch(parent["id"])
        except Absent:
            parent_task = None
        if parent_task is not None:
            parent_kind, parent_ref = reader.kind_of(parent_task, mapping)
            if parent_kind is None:
                problems.append(dict(parent_ref, id=ident, what="parent_type",
                                     parent=parent["id"]))
            else:
                parent["kind"] = parent_kind
    agents = mapping.agent_accounts
    creator = _account(task.get("creator"), agents)
    if creator is None:
        raise NotRead("the task has no creator account")
    if problems:
        return None, problems, parent
    private = reader.private_space(task)
    comments = reader.comments(ident)
    files = {"description.txt": task.get("markdown_description") or ""}
    parts = []
    for number, comment in enumerate(comments, 1):
        name = "comment-%d.txt" % number
        files[name] = comment.get("comment_text") or ""
        parts.append({"id": _ident(comment), "author": _account(comment.get("user"), agents),
                      "created_at": _ms_iso(comment.get("date")), "body_file": name})
    ticket = {"source": "clickup", "id": ident, "url": task.get("url"),
              "project_key": _ident(task.get("list")), "kind": kind,
              "title": task.get("name") or "", "description_file": "description.txt",
              "status": state.get("status"), "status_category": category,
              "author": creator,
              "assignees": sorted((a for a in (_account(u, agents) for u in task.get("assignees") or []) if a),
                                  key=lambda a: _id_order(a["login"])),
              "created_at": _ms_iso(task.get("date_created")),
              "closed_at": _ms_iso(task.get("date_closed") or task.get("date_done")),
              "comments": parts}
    if task.get("custom_id"):
        ticket["key"] = str(task["custom_id"])
    name = reader.types.name(task)
    if name:
        ticket["type"] = name
    if private:
        ticket["visibility"] = "private"
    if parent:
        ticket["parent"] = parent
    ticket = {k: v for k, v in ticket.items() if v is not None}
    files["ticket.json"] = json.dumps(ticket, indent=2, sort_keys=True) + "\n"
    return files, [], parent


def get_command(args, runtime):
    token = _token()
    lookup = _load_json(args.lookup, "the lookup")
    trackers = lookup.get("trackers") if isinstance(lookup, dict) else None
    if not isinstance(trackers, dict):
        raise Unreadable('%s must be {"trackers": {...}}' % args.lookup)
    wanted = trackers.get("clickup") or []
    if not isinstance(wanted, list):
        raise Unreadable("trackers.clickup must be a list")
    ids = sorted({str(i).strip() for i in wanted if str(i).strip()})
    mapping = _project_mapping(args)
    reader = Reader(Client(token, runtime), _load_raw(args.raw))
    if args.tasks:
        inventory = _load_json(args.tasks, "the tasks")
        if not isinstance(inventory, list):
            raise Unreadable("%s must be a JSON list of rows" % args.tasks)
        reader.alias_rows(inventory)
    found, absent = {}, []
    for ident in ids:
        try:
            task = reader.resolve(ident)
        except NotRead as err:
            reader.miss("task", ident, err)
            continue
        if task is None:
            absent.append(ident)
            continue
        while task is not None and _ident(task) not in found:
            found[_ident(task)] = task
            parent = task.get("parent")
            task = None
            if parent:
                try:
                    task = reader.fetch(str(parent))
                except Absent:
                    pass
                except NotRead as err:
                    reader.miss("task", str(parent), err)
    rows = []
    for ident in sorted(found):
        task = found[ident]
        reader.note_status(task)
        parent_kind = None
        if task.get("parent") and str(task["parent"]) in found:
            parent_kind, _ = reader.kind_of(found[str(task["parent"])], mapping)
        rows.append(_row(task, reader.note_type(task), parent_kind, links=False))
    vocabulary_path = _beside(args.vocabulary, args.out, VOCABULARY_NAME)
    _write_json(args.out, rows)
    _merge_vocabulary(vocabulary_path, reader.statuses, reader.names, reader.types.unnamed)
    _merge_raw(_beside(None, args.out, RAW_NAME), reader.downloaded)
    return {"command": "get", "asked": len(ids), "rows": len(rows), "absent": len(absent),
            "parents_without_kind": sum(_lacks_kind(r.get("parent")) for r in rows),
            "absent_ids": absent, "out": args.out, "vocabulary": vocabulary_path,
            "not_read": reader.sorted_misses()}


# ------------------------------------------------------------------ CLI

def _parser():
    parser = argparse.ArgumentParser(prog="clickup_read.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    zero_check = sub.add_parser("check", allow_abbrev=False)
    zero_check.add_argument("--local", action="store_true")
    zero = sub.add_parser("root", allow_abbrev=False)
    zero.add_argument("--link", required=True)
    one = sub.add_parser("list", allow_abbrev=False)
    one.add_argument("--root", required=True, action="append")
    one.add_argument("--days", required=True, type=int)
    one.add_argument("--out", required=True)
    one.add_argument("--vocabulary")
    two = sub.add_parser("map", allow_abbrev=False)
    two.add_argument("--vocabulary", required=True)
    two.add_argument("--mapping", required=True)
    two.add_argument("--answers")
    two.add_argument("--project")
    three = sub.add_parser("read", allow_abbrev=False)
    three.add_argument("--tasks", required=True)
    three.add_argument("--mapping", required=True)
    three.add_argument("--project")
    three.add_argument("--out-dir", required=True)
    three.add_argument("--only", action="append")
    three.add_argument("--vocabulary")
    three.add_argument("--raw", action="append")
    four = sub.add_parser("get", allow_abbrev=False)
    four.add_argument("--lookup", required=True)
    four.add_argument("--mapping", required=True)
    four.add_argument("--project")
    four.add_argument("--out", required=True)
    four.add_argument("--vocabulary")
    four.add_argument("--tasks")
    four.add_argument("--raw", action="append")
    return parser


def _print(document, token):
    print(redact(json.dumps(document, ensure_ascii=False, sort_keys=True), token))


def main(argv=None, runtime=None):
    runtime = runtime or Runtime()
    token = os.environ.get(TOKEN_ENV, "").strip()

    def say(message):
        print("clickup_read: %s" % redact(message, token), file=sys.stderr)

    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    if args.command == "check":
        _print({"token_set": _valid_token(token) is None} if args.local else check(runtime), token)
        return 0
    command = {"root": lambda: root_command(args),
               "list": lambda: list_tasks(args, runtime),
               "map": lambda: map_command(args),
               "read": lambda: read_command(args, runtime),
               "get": lambda: get_command(args, runtime)}[args.command]
    try:
        document = command()
    except (Usage, Missing) as err:
        say(err)
        return 2
    except Unreadable as err:
        say(err)
        return 3
    except Refused as err:
        say(err)
        return 4
    except WriteFailed as err:
        say(err)
        return 5
    _print(document, token)
    if document.get("refused"):
        return 4
    failed = document.get("not_read") or document.get("unmapped")
    return 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
