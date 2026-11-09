#!/usr/bin/env python3
"""Send a seeding run's task and pull request cards to the brain from this machine.

`brain-seed` Phase 5 used to make the model session call the brain for every
task and pull request: a check, a write whose arguments it had to retype from a
file, a digest check, the ledger. That is a state machine written in prose and
executed by a model. This script runs it, through the SAME FairMind MCP server
the session uses (the address and key of the project's own MCP entry, read the
way the plugin's hooks read them), and leaves the model only the part that needs
one: the words of a card.

    seed_send.py send  --plan F --kind task|pull_request [--ledger L]
                       [--refs PR_REFS.json] [--limit N] [--timeout S]
                       [--needs-out FILE]
    seed_send.py stage --plan F --needs FILE [--refs PR_REFS.json]
                       [--ledger L]

`send` walks the rows the ledger offers for the kind, in order, up to
`--limit`: the check call, the erasure check, the write of a staged card with
the file's arguments exactly as loaded and its `expected_sha256`, the digest
verification and the record, all through `seed_ledger.py`'s own cores (the
ledger stays the single writer). A changed item with no usable staged card is
NOT recorded: it is handed back in the needs file (`--needs-out`, default
`<plan dir>/send-needs-<kind>.json`) with the paths of its `read.md` and
`words.json`, and the ledger offers it again once its card is staged. `stage`
stages every item of a needs file whose `words.json` exists.

stdout of `send` is one JSON summary: `route` ("script"), `kind`, `processed`,
`outcomes`, `needs_words`, `needs_out`, `stopped` (null, or the outcome that
ended the run), `attention` (lines for a person), `notes` (codes `seed_ledger.py
summary --note` takes), `remaining` and `lock`. Progress is one counts-only line
on stderr per 25 rows. Exit codes: 0 done or limit reached; 2 usage; 3 stopped
(a stop outcome, the brain unreachable, or the ledger unwritable); 5 no script
route (the project has no MCP entry a script can use, or the platform has no
file lock: stdout
`{"route": "session", "reason": ...}`, nothing called or recorded); 6 another
sender holds the lock.

Rules that hold the shape of the file:

* The credential is read by `_mcp_config` and handed to `_brain_mcp` and to
  nothing else. It is never printed, logged, put in an exception or stored.
* The table of Phase 5 ("Read the table by precedence") is code, in that
  precedence. Every stop is recorded and ends the whole run. A branch that
  cannot be classified with certainty records `failed` and never sends.
* A write is made at most twice per row per run. Every retry path (a `resend`,
  `retryable`, an edge page, `upstream connect error`, a transport failure,
  a bare failure) goes back to the check first, and the check of a pass is the
  "ask again just before the write": nothing is summarised between them.
"""
from __future__ import annotations

import argparse
import copy
import errno
import json
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import _brain_mcp
import _mcp_config
import reference_card as rc
import seed_ledger as sl
import ticket_card
from _fm_ignore import makedirs_ignored
from login_key import match_key

AGENT = sl.AGENT
TOOLS = {"task": "Brain_import_tracker_item",
         "pull_request": "Brain_import_pull_request"}
DEFAULT_TIMEOUT_S = 60.0
PROGRESS_EVERY = 25
# Consecutive calls that ended without the door ever answering: past this the
# brain (or the key) is not there, and hammering it for days helps nobody.
UNREACHABLE_AFTER = 6
MAX_WRITES = 2
MAX_UNSENT = 2
MAX_RESTAGES = 3
MAX_HANDBACKS = 2
MAX_DETAIL = 300
BRAIN_REFUSED = re.compile(r"^\s*brain\b.*\brefused", re.IGNORECASE | re.DOTALL)

EXIT_STOPPED, EXIT_USAGE, EXIT_NO_ROUTE, EXIT_LOCKED = 3, 2, 5, 6


class Usage(Exception):
    pass


class Locked(Exception):
    pass


class NoLock(Exception):
    def __init__(self, reason="no file lock on this platform"):
        super().__init__(reason)
        self.reason = reason


class Stopped(Exception):
    """A stop outcome was recorded: the whole run ends."""


# ---------------------------------------------------------------- helpers


def _short(text):
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL - 1] + "…"


def _reason(err):
    """What went wrong, in words: a refused card lists its problems."""
    if isinstance(err, rc.Refused):
        return "; ".join(err.problems)
    return str(err) or err.__class__.__name__


def _git_toplevel(cwd):
    try:
        done = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.decode("utf-8", "replace").strip()
    return out if done.returncode == 0 and out else None


def _strings(value, depth=0):
    """Every string in a str, dict or list, however nested (a few levels)."""
    if depth > 4:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, depth + 1)


class Reply:
    """What a door call came to: an answer (possibly an error result) or a
    transport failure. Holds the server's words only to match on them and to
    hand a bounded detail to the ledger; the transport has already scrubbed the
    credential out of every string."""

    def __init__(self, answer=None, error=None):
        self.error = error
        self.is_error = bool(answer and answer.is_error)
        self.data = (answer.data if answer and isinstance(answer.data, dict) else {})
        self.text = (answer.text or "") if answer else ""

    @property
    def answered(self):
        return self.error is None

    def _first(self, *keys):
        for key in keys:
            for text in _strings(self.data.get(key)):
                if text.strip():
                    return text
        return ""

    @property
    def message(self):
        return self._first("message", "error", "detail") or self.text

    @property
    def first_error(self):
        return self._first("errors", "error", "message", "detail") or self.text

    @property
    def blob(self):
        parts = [t for key in ("message", "error", "errors", "detail")
                 for t in _strings(self.data.get(key))]
        return " ".join(parts + [self.text]).lower()

    @property
    def detail(self):
        if self.error is not None:
            return _short(str(self.error))
        return _short(self.first_error or "no message")


def _keys(row, logins):
    """Logins as the brain keys them, the spelling `reference_card` compares by."""
    return {match_key(row["source"], login) for login in logins}


def _links_of(data):
    """The answer's `unresolved_links` when the ledger can take them, else
    None (an answer the ledger cannot read must not end the run)."""
    links = data.get("unresolved_links")
    try:
        sl._links(links)
    except sl.Usage:
        return None
    return links


def _binding(row, refs):
    """The repository, remote and references a card is staged and checked
    under: a pull request's own, none for a task."""
    if row["kind"] != "pull_request":
        return {"repository": None, "git_remote": None, "refs": None}
    return {"repository": row.get("repository"), "git_remote": row.get("git_remote"),
            "refs": refs}


# --------------------------------------------------------------- the run


class Run:
    def __init__(self, document, server, ledger_path, kind, timeout, refs):
        self.document = document
        self.server = server
        self.ledger_path = ledger_path
        self.kind = kind
        self.timeout = timeout
        self.refs = refs
        self.outcomes = {}
        self.needs = []
        self.attention = []
        self.notes = set()
        self.schemes = set()
        self.stopped = None
        self.unreachable = 0
        self.session = _brain_mcp.DoorSession()

    # -- door calls

    def call(self, name, arguments):
        deadline = time.monotonic() + self.timeout
        try:
            answer = _brain_mcp.call_door(self.server, name, arguments,
                                          deadline=deadline, session=self.session)
        except _brain_mcp.BrainTransportError as err:
            self.unreachable = 0 if err.edge_blocked else self.unreachable + 1
            return Reply(error=err)
        self.unreachable = 0
        reply = Reply(answer)
        self._read_scheme(reply)
        return reply

    def _read_scheme(self, reply):
        repository = reply.data.get("repository")
        if self.kind != "pull_request" or not isinstance(repository, dict):
            return
        scheme = repository.get("repo_ref_scheme")
        ident = repository.get("id") or repository.get("name") or "_"
        if isinstance(scheme, str) and ident not in self.schemes:
            self.schemes.add(ident)
            if scheme != "code-ingestion":
                self.notes.add("not_code_ingestion")

    # -- the ledger

    def record(self, row, outcome, detail=None, budget=None, unresolved=None):
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1
        result = sl.record_row(self.document, row, outcome, detail=detail,
                               budget=budget, unresolved=unresolved,
                               ledger=self.ledger_path)
        if result["stop"]:
            self.stopped = outcome
            raise Stopped()

    def todo(self):
        """The rows still to send for the kind (those handed back for words
        in this invocation left out) and the run's stop, if any."""
        _, found, entries = sl._entries(self.document, self.ledger_path)
        rows, stopped = sl.offered(self.document, entries, self.kind,
                                   sl.repairs_of(found, self.document["project"]))
        handed = {need["key"] for need in self.needs}
        return [row for row in rows if row["key"] not in handed], stopped

    # -- the stops of the table

    def stop_of(self, reply, row, args):
        """(outcome, detail, budget) when the answer ends the row or the run by
        a rule that comes before every other, else None."""
        if reply.error is not None:
            if reply.error.write_scope:
                return "access_denied", "write scope required", None
            if reply.error.edge_blocked:
                return "edge_refused", row.get("title") or row["key"], None
            return None
        data = reply.data
        blob = reply.blob
        # A key that cannot write stops the run whatever `retryable` says.
        if "access denied" in blob and "write scope" in blob:
            return "access_denied", _short(reply.first_error), None
        if data.get("status") == "throttled":
            return "throttled", _short(reply.first_error or "throttled"), \
                data.get("quota") if isinstance(data.get("quota"), dict) else None
        if data.get("key") == "brain_write_quota_daily":
            return "quota_unset", _short(reply.first_error or "no daily write allowance"), \
                data.get("quota") if isinstance(data.get("quota"), dict) else None
        if "access denied" in blob and data.get("retryable") is not True:
            if args.get("repository"):
                return "repository_denied", _short(reply.first_error), None
            return "access_denied", _short(reply.first_error), None
        return None

    # -- one row

    def process(self, row):
        """Send one row: it ends recorded in the ledger, or handed back in
        `self.needs` for words."""
        project = row["project"]
        arguments = copy.deepcopy(row["check"])
        arguments.update(project=project, agent=AGENT)
        if row["kind"] == "pull_request":
            if not row.get("repository") or not row.get("git_remote"):
                self.record(row, "failed",
                            "the plan carries no repository binding for this pull request")
                return
            arguments.update(repository=row["repository"], git_remote=row["git_remote"])
        tool = TOOLS[row["kind"]]
        writes = sl.row_counts(self.document, row, ledger=self.ledger_path)["writes"]
        unsent = restages = 0
        while True:
            checked = self._check(row, tool, arguments)
            if checked is None:
                return
            listed, data = checked
            step = self._prepare(row, project, data, listed)
            if step == "settled":
                return
            if step == "staged":
                # Something was staged since the check: ask again before the write.
                restages += 1
                if restages > MAX_RESTAGES:
                    self.record(row, "failed", "the card kept changing under staging")
                    return
                continue
            step = self._write(row, tool, project, listed)
            if step == "done":
                return
            if step == "summary_required":
                self._handoff(row, restage=True)
                return
            if step == "unsent":
                # The request never left: not a write. A brain that cannot be
                # reached twice at this point is not going to take it.
                unsent += 1
                if unsent >= MAX_UNSENT:
                    self.record(row, "failed", "the brain could not be reached for the write")
                    return
                continue
            writes = sl.count_row(self.document, row, "writes", ledger=self.ledger_path)
            if step == "repair":
                row = dict(row, repair=True)
            if writes >= MAX_WRITES:
                if step == "unknown":
                    # What the brain holds is not known: the next run must not
                    # settle it "unchanged". (`verify_row` marks a repair.)
                    sl.mark_repair(self.document, row, ledger=self.ledger_path)
                self.record(row, "failed", "a third send would have been needed")
                return

    # -- check

    def _check(self, row, tool, arguments):
        """None when the row was recorded, else (erased logins, answer).

        While a repair is pending for the row (a write of this version
        arrived altered, or its outcome was never seen) the brain calls the
        version unchanged because that write stored it: `source_changed:
        false` then does not settle the row. The erasure gates still apply and
        the staged file is sent again."""
        for _ in range(2):
            reply = self.call(tool, arguments)
            stop = self.stop_of(reply, row, arguments)
            if stop:
                outcome, why, budget = stop
                self.record(row, outcome, why, budget=budget)
                return None
            if reply.answered:
                if sl.OLDER_SERVER in reply.blob:
                    self.record(row, "failed", _short(reply.message))
                    return None
                data = reply.data
                if not reply.is_error and isinstance(data.get("source_changed"), bool):
                    if data["source_changed"] is False and not row.get("repair"):
                        self.record(row, "unchanged", budget=data.get("budget"))
                        return None
                    if data.get("erased_participants_truncated") is True:
                        self.record(row, "failed",
                                    "the brain's list of erased people is cut short")
                        return None
                    listed = data.get("erased_participants", [])
                    if not isinstance(listed, list) or not all(
                            isinstance(x, str) for x in listed):
                        self.record(row, "failed",
                                    "the brain's list of erased people is unreadable")
                        return None
                    return listed, data
        self.record(row, "failed", "the check failed twice: %s" % reply.detail)
        return None

    # -- erasure check, staging

    def _paths(self, row):
        directory = os.path.dirname(os.path.abspath(row["card"]))
        return (os.path.join(directory, "read.md"),
                os.path.join(directory, "words.json"))

    def _stage_args(self, row, project, **extra):
        values = dict(card=row["card"], input=None, copy=False, out=row["write"],
                      agent=AGENT, project=project, restage=False,
                      **_binding(row, self.refs))
        values.update(extra)
        return SimpleNamespace(**values)

    def _prepare(self, row, project, data, listed):
        """Decide what to send for a changed item: "send" (the staged card
        may be written), "staged" (the script staged something: ask again
        first) or "settled" (the row was recorded or handed back)."""
        step = sl._with_step(row)
        have = bool(step.get("staged"))
        is_copy = have and bool(step.get("copy"))
        try:
            found = rc.erased_check(SimpleNamespace(
                card=row["card"], erased=list(listed),
                staged=row["write"] if have else None, project=project,
                **_binding(row, self.refs)))
            affects = list(found["affects"])
            stale = bool(found["stale"])
            reason = found.get("stale_reason")
        except Exception as err:  # noqa: BLE001 - every failure means unknown erased people
            self.record(row, "failed", _short("the erased people are unknown: %s" % _reason(err)))
            return "settled"
        tickets_only = stale and reason == "tickets"
        if affects or (stale and not tickets_only):
            read, words = self._paths(row)
            if affects:
                try:
                    ticket_card.redact(SimpleNamespace(read=read, erased=affects))
                except Exception as err:  # noqa: BLE001
                    self.record(row, "failed", _short(
                        "the reading file could not be redacted: %s" % _reason(err)))
                    return "settled"
            if affects or reason in (None, "redaction"):
                # Words written before the redaction must never be staged.
                try:
                    os.remove(words)
                except FileNotFoundError:
                    pass
                except OSError:
                    self.record(row, "failed",
                                "the words written before an erasure could not be removed")
                    return "settled"
            return self._handoff(row, restage=os.path.exists(row["write"]))
        copy_ok = data.get("summary_needed") is False
        if tickets_only:
            return self._restage_tickets(row, project, is_copy, copy_ok)
        if not have:
            if copy_ok:
                return self._stage_copy(row, project)
            return self._handoff(row, restage=os.path.exists(row["write"]))
        if is_copy and not copy_ok:
            return self._handoff(row, restage=True)
        return "send"

    def _restage_tickets(self, row, project, is_copy, copy_ok):
        _, words = self._paths(row)
        if is_copy:
            if not copy_ok:
                return self._handoff(row, restage=True)
            return self._stage_copy(row, project, restage=True)
        if not os.path.exists(words):
            return self._handoff(row, restage=True)
        try:
            rc.stage(self._stage_args(row, project, input=words, restage=True))
        except Exception:  # noqa: BLE001 - the words are the writers' to fix
            return self._handoff(row, restage=True)
        return self._staged(row)

    def _stage_copy(self, row, project, restage=None):
        if restage is None:
            restage = os.path.exists(row["write"])
        try:
            rc.stage(self._stage_args(row, project, copy=True, restage=restage))
        except Exception as err:  # noqa: BLE001
            self.record(row, "failed", _short("the copy card could not be staged: %s" % _reason(err)))
            return "settled"
        return self._staged(row)

    def _staged(self, row):
        if sl._with_step(row).get("staged"):
            return "staged"
        self.record(row, "failed", "the staged card is not usable for this row")
        return "settled"

    def _handoff(self, row, restage):
        """Hand the item back for words ("settled" either way): recorded
        `failed` when it has been handed back twice already in this run. The
        script counts, not the model."""
        if sl.count_row(self.document, row, "handbacks", ledger=self.ledger_path) > MAX_HANDBACKS:
            self.record(row, "failed", "words could not be staged after two rounds")
            return "settled"
        read, words = self._paths(row)
        item = {"key": row["key"], "kind": row["kind"], "title": row.get("title"),
                "read": read, "words": words, "card": row["card"],
                "write": row["write"], "restage": bool(restage)}
        if row["kind"] == "pull_request":
            item.update(repository=row.get("repository"), git_remote=row.get("git_remote"))
        self.needs.append(item)
        return "settled"

    # -- write

    def _write(self, row, tool, project, listed):
        """Send the staged card. Returns "done" (recorded), "again" (back to
        the check, nothing was stored), "unknown" (back to the check, the
        outcome is not known), "repair" (back to the check, which will call the
        stored, altered card unchanged), "unsent" (the request never left, so
        it was not a write) or "summary_required"."""
        try:
            with open(row["write"], encoding="utf-8") as handle:
                staged = json.load(handle)
            arguments = staged["arguments"]
            expected = sl.usable_digest(staged["expected_sha256"])
        except (OSError, ValueError, KeyError, TypeError):
            self.record(row, "failed", "the staged card could not be read")
            return "done"
        if not isinstance(arguments, dict) or expected is None:
            self.record(row, "failed", "the staged card is malformed")
            return "done"
        call = dict(arguments, expected_sha256=expected)
        reply = self.call(tool, call)

        stop = self.stop_of(reply, row, call)
        if stop:
            outcome, why, budget = stop
            self.record(row, outcome, why, budget=budget)
            return "done"
        if reply.error is not None:
            # Whatever it was, the check says whether it landed. A request that
            # never left is not a write at all.
            return "unknown" if reply.error.sent else "unsent"
        data = reply.data
        code = data.get("code")
        received = data.get("received_sha256")
        if code in sl.CODES:
            return self._after_code(row, code, received)
        if isinstance(received, str):
            digest = sl.usable_digest(received)
            if digest is None:
                self.record(row, "failed", "the brain's digest of the write is unreadable")
                return "done"
            verdict = sl.verify_row(self.document, row, digest,
                                    ledger=self.ledger_path)["next"]
            if verdict == "resend":
                return "repair"  # the altered copy is stored: send the file again
            if verdict == "record_failed":
                if self._erasure_landed(row, data, listed):
                    return "done"
                self.record(row, "failed")
                return "done"
        if code == "summary_required":
            return "summary_required"
        return self._table(row, reply, listed)

    def _erasure_landed(self, row, data, listed):
        """Whether this write answer shows an erasure the last check did not
        list. Read only when an outcome is about to be recorded from the
        answer (after the digest is settled): a retry, a digest refusal and a
        `summary_required` stored nothing. Records the row `failed` and says
        so for a person."""
        landed = data.get("erased_participants")
        if not (isinstance(landed, list) and not _keys(row, landed) <= _keys(row, listed)):
            return False
        self.record(row, "failed", "an erasure landed during the write")
        self.attention.append({
            "key": row["key"], "title": row.get("title"),
            "reason": "erasure_landed_during_write",
            "text": "The card for %s may hold words written before an erasure "
                    "landed. An erasure by login can no longer find it, so "
                    "someone with the editor role on the project has to "
                    "check it and remove it by hand on the platform."
                    % (row.get("title") or row["key"])})
        return True

    def _after_code(self, row, code, received):
        verdict = sl.verify_row(self.document, row, sl.usable_digest(received), code=code,
                                ledger=self.ledger_path)["next"]
        if verdict == "resend":
            return "again"
        self.record(row, "digest_refused", "the brain received a different card (%s)" % code)
        return "done"

    def _table(self, row, reply, listed):
        """Record a write answer that is settled, or send the row back to the
        check. Everything the table does not name records `failed`."""
        data = reply.data
        status = data.get("status")
        message = reply.message
        outcome, detail = None, None
        if status == "ok":
            written = data.get("written")
            if isinstance(written, int) and not isinstance(written, bool) and written >= 0:
                outcome = "written" if written > 0 else "unchanged"
        elif status in ("partial", "revision_proposed", "not_reproposed"):
            outcome = status
        elif status in ("unchanged", "duplicate"):
            outcome = "unchanged"
        elif status == "refused":
            outcome, detail = "refused", reply.first_error
        elif status == "error":
            if data.get("retryable") is True:
                return "again"
            outcome, detail = "failed", reply.first_error
        elif data.get("retryable") is True:
            return "again"
        elif status is None and message.strip().endswith("failed") \
                and not BRAIN_REFUSED.match(message):
            return "again"
        elif BRAIN_REFUSED.match(message):
            outcome, detail = "refused", reply.first_error
        if outcome is None:
            self.record(row, "failed", "the brain's answer is not one this version knows")
            return "done"
        if sl.OLDER_SERVER in reply.blob:
            outcome = "failed"
        if self._erasure_landed(row, data, listed):
            return "done"
        self.record(row, outcome, _short(detail) if detail else None,
                    budget=data.get("budget"), unresolved=_links_of(data))
        return "done"


# ----------------------------------------------------------------- lock


def _take_lock(ledger_path):
    """The open lock file. Raises Locked when another sender holds it and
    NoLock where this platform has no file lock, or the filesystem refuses
    one: without it nothing excludes a second writer, so the work goes to the
    session."""
    try:
        import fcntl
    except ImportError:
        raise NoLock()
    path = ledger_path + ".lock"
    makedirs_ignored(os.path.dirname(os.path.abspath(path)))
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as err:
        handle.close()
        if err.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            raise Locked()
        raise NoLock("no file lock on this filesystem")
    return handle


def _acquire(ledger_path, **said):
    """(lock, None), or (None, exit code) after saying why there is none."""
    try:
        return _take_lock(ledger_path), None
    except NoLock as err:
        print(json.dumps({"route": "session", "reason": err.reason}))
        return None, EXIT_NO_ROUTE
    except Locked:
        print(json.dumps(dict({"route": "script"}, **said, locked=True,
                              reason="another sender holds the ledger")))
        return None, EXIT_LOCKED


# ------------------------------------------------------------- commands


def _plan_and_ledger(args):
    try:
        document = sl._plan(args.plan)
    except sl.Unreadable as err:
        raise Usage(str(err))
    return document, args.ledger or document["ledger"]


def _refs_path(args, document, kind):
    if kind != "pull_request":
        return None
    if args.refs:
        return args.refs
    default = os.path.join(document.get("work") or ".", "pr-refs.json")
    return default if os.path.exists(default) else None


def _progress(kind, handled, run):
    counts = ", ".join("%s %d" % item for item in sorted(run.outcomes.items()))
    print("seed_send: %s %d handled (%s%s%d needing words)"
          % (kind, handled, counts, ", " if counts else "", len(run.needs)),
          file=sys.stderr)


def send(args):
    document, ledger_path = _plan_and_ledger(args)
    cwd = os.getcwd()
    server = _mcp_config.fairmind_server(cwd, _git_toplevel(cwd))
    if server is None:
        print(json.dumps({"route": "session", "reason": "this project has no Fairmind MCP "
                          "entry a script can use (no address, or a key it cannot resolve)"}))
        return EXIT_NO_ROUTE
    lock, refused = _acquire(ledger_path, kind=args.kind)
    if lock is None:
        return refused
    run = Run(document, server, ledger_path, args.kind, args.timeout,
              _refs_path(args, document, args.kind))
    needs_out = args.needs_out or os.path.join(
        os.path.dirname(os.path.abspath(args.plan)), "send-needs-%s.json" % args.kind)
    handled = 0
    code = 0
    try:
        while args.limit is None or handled < args.limit:
            todo, stopped = run.todo()
            if stopped:
                run.stopped = stopped["outcome"]
                break
            if not todo:
                break
            handled += 1
            run.process(todo[0])
            if handled % PROGRESS_EVERY == 0:
                _progress(args.kind, handled, run)
            if run.unreachable >= UNREACHABLE_AFTER:
                run.stopped = "unreachable"
                break
    except Stopped:
        pass
    except (sl.Usage, sl.Unreadable, sl.WriteFailed) as err:
        print("seed_send: the ledger refused: %s" % err, file=sys.stderr)
        run.stopped = run.stopped or "ledger"
    if run.stopped:
        code = EXIT_STOPPED
    remaining = 0
    try:
        remaining = len(run.todo()[0])
    except (sl.Usage, sl.Unreadable):
        pass
    try:
        sl._write(needs_out, run.needs)
    except sl.WriteFailed as err:
        print("seed_send: %s" % err, file=sys.stderr)
        code = EXIT_STOPPED
    summary = {"route": "script", "kind": args.kind, "processed": handled,
               "outcomes": run.outcomes, "needs_words": len(run.needs),
               "needs_out": needs_out, "stopped": run.stopped,
               "attention": run.attention, "notes": sorted(run.notes),
               "remaining": remaining, "lock": "held"}
    print(json.dumps(summary, ensure_ascii=False))
    lock.close()
    return code


def stage(args):
    document, ledger_path = _plan_and_ledger(args)
    try:
        with open(args.needs, encoding="utf-8") as handle:
            needs = json.load(handle)
    except (OSError, ValueError):
        raise Usage("cannot read %s as JSON" % args.needs)
    if not isinstance(needs, list) or not all(isinstance(n, dict) for n in needs):
        raise Usage("%s is not a needs file" % args.needs)
    lock, refused = _acquire(ledger_path)
    if lock is None:
        return refused
    result = {"staged": 0, "problems": [], "missing_words": [], "no_reading": []}
    for item in needs:
        try:
            row = sl._row(document, item.get("key"))
        except sl.Usage:
            result["problems"].append({"key": item.get("key"), "words": item.get("words"),
                                       "problems": ["the key is not in the plan"]})
            continue
        if not os.path.exists(item.get("read") or ""):
            result["no_reading"].append(row["key"])
            sl.record_row(document, row, "failed",
                          detail="there is no reading file for this item",
                          ledger=ledger_path)
            continue
        if not os.path.exists(item.get("words") or ""):
            result["missing_words"].append(row["key"])
            continue
        binding = _binding(row, _refs_path(args, document, row["kind"]))
        if row["kind"] == "pull_request":
            # The item's own binding wins over the plan's.
            for name in ("repository", "git_remote"):
                binding[name] = item.get(name) or binding[name]
        values = SimpleNamespace(
            card=row["card"], input=item["words"], copy=False, out=row["write"],
            agent=AGENT, project=row["project"], restage=bool(item.get("restage")),
            **binding)
        try:
            rc.stage(values)
            result["staged"] += 1
        except rc.AlreadyStaged:
            result["staged"] += 1
        except rc.Refused as err:
            result["problems"].append({"key": row["key"], "words": item["words"],
                                       "problems": [_short(p) for p in err.problems]})
        except (rc.Unreadable, rc.WriteFailed) as err:
            result["problems"].append({"key": row["key"], "words": item["words"],
                                       "problems": [_short(err)]})
    print(json.dumps(result, ensure_ascii=False))
    lock.close()
    return 0


def _parser():
    parser = argparse.ArgumentParser(prog="seed_send.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("send", allow_abbrev=False)
    one.add_argument("--plan", required=True)
    one.add_argument("--kind", required=True, choices=sorted(TOOLS))
    one.add_argument("--ledger")
    one.add_argument("--refs")
    one.add_argument("--limit", type=int)
    one.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    one.add_argument("--needs-out", dest="needs_out")
    two = sub.add_parser("stage", allow_abbrev=False)
    two.add_argument("--plan", required=True)
    two.add_argument("--needs", required=True)
    two.add_argument("--refs")
    two.add_argument("--ledger")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    if args.command == "send" and (
            (args.limit is not None and args.limit < 1) or args.timeout <= 0):
        print("seed_send: --limit and --timeout must be positive", file=sys.stderr)
        return EXIT_USAGE
    try:
        return send(args) if args.command == "send" else stage(args)
    except Usage as err:
        print("seed_send: %s" % err, file=sys.stderr)
        return EXIT_USAGE
    except Exception as err:  # noqa: BLE001 - no traceback: it could carry the server entry
        print("seed_send: unexpected %s" % err.__class__.__name__, file=sys.stderr)
        return EXIT_STOPPED


if __name__ == "__main__":
    sys.exit(main())
