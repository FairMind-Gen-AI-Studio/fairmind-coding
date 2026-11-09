#!/usr/bin/env python3
"""The seeding ledger: what was sent to the brain, what is left, what changed.

A seeding run walks documents, tasks and pull requests through the brain's
write doors. This script owns the bookkeeping so a run can stop (a throttle, a
closed session, a compacted context) and a later run can pick up exactly where
it left off, and so a re-run sends only what changed. It never calls the brain:
`seed_send.py` does, and keeps this ledger the single writer by going through the
cores the commands below call too: `offered`, `record_row`, `verify_row`,
`mark_repair`, `repairs_of`, `row_counts`, `count_row`, `usable_digest`, and
`_plan`, `_row`, `_entries`, `_links`, `_with_step`, `_write`.

    seed_ledger.py plan   --project P --work DIR [--ledger L] [--documents F ...]
                          [--tasks F] [--collected F ...] [--pull-list F ...]
                          ([--repository ID] [--git-remote URL]
                           | --repositories F)
                          [--refs PR_REFS.json] [--all] [--out F]
    seed_ledger.py next   --plan F --kind document|task|pull_request
                          [--ledger L]
    seed_ledger.py record --plan F --key K --outcome O [--detail TEXT]
                          [--budget JSON] [--unresolved JSON]
                          [--answer-json] [--ledger L]
    seed_ledger.py status --plan F [--ledger L]
    seed_ledger.py verify --plan F --key K [--ledger L]
                          (--received HEX | --code CODE [--received HEX|null])
    seed_ledger.py summary --plan F --stage estimate|send|done [--ledger L]
                          [--collected F ...] [--note CODE ...] [--extra F ...]
                          [--report F]

The ledger (default `.fairmind/brain-seed/ledger.json`, schema
`brain-seed-ledger/2`) is keyed by project and then by item,
`"<kind>:<source>:<id>"`: a document by its canonical link, a task by its
permanent id, a pull request by its node id. A document kept in a repository
(`"git": true` on its row, from `repo_docs.py`) is `document:git:<owner/name>:<path>`
with the owner and name lower-cased: one card per file, so two rows with the
same key in a plan are refused. Each entry keeps the last attempt
(`fingerprint`, `outcome`, `detail`, `run`, `seq`) and, apart from it, `sent`:
the fingerprint of the last attempt that succeeded. `sent` changes only on a
success, so a failed or throttled attempt never hides what the brain holds.
A ledger of the earlier schema is read with its task and pull request rows
dropped (they were fingerprints of verbatim payloads and mean nothing now) and
is rewritten in the current schema by the next `record`; its documents stay.

Fingerprints never hash model-written text, or every run would resend
everything and push revision proposals onto cards a person already confirmed.
A document uses the source's own edit marker (a document with no marker is sent
once and then left alone); a file in a repository uses its `blob_sha`, so a new
commit that left the file alone changes nothing and a changed blob is `changed`.
`--documents` may be repeated (the linked documents and the ticked repository
files are two files); a git row must carry `repo`, `repository`, `git_remote`,
`path`, `commit`, `branch` and `blob_sha` or the plan exits 3 naming the field,
and it keeps every field it was given so the session can file its card. Its
`file` (`<work>/documents/<slug>.json`) is where the summariser writes
`{"title", "summary"}` for that document, as for a linked one. A repository
file's card is confirmed on arrival, so the done summary does not count it as
waiting in the Review Queue.
A page row (a linked document) carries `version`, the source's edit marker
from the inventory, when it has one and is not a local file: the version its
check is made at. A page the check settled is not read: its `file` is written
as `{"link", "source", "version", "copy": true}`, and `next` returns its row
with `copy: true` (the copy form, as for a task or a pull request below). A
file with words, or a summariser's `error`, is never a copy.
A task and a pull request use their `source_version`, the fingerprint of what
a person wrote in them (`source_version.py`): the collector computes it, the
row carries it, and nothing the agent writes enters it.

A row is `new` (never sent), `changed` (sent, and the fingerprint differs or
the latest attempt was not a success), `retry` (an earlier attempt did not
succeed and nothing was sent before), `unchanged` (the latest attempt was a
success and the fingerprint is the one sent), or `refused` (the brain refused
this exact content; it is not resent until the content changes, or until a plan
made with `--all`). Only the brain's own refusal holds a row: after any other
latest outcome, an earlier success included, the next plan sends the row
again. `--all` makes everything `new` or `changed`, as for a backfill.

Check first. A task or pull request row is a reference card, and the brain
decides whether its source changed: every such row starts with a check call
(`check`: `item`, or `pull_request`, holding exactly the `source`, `id` and
`source_version` the door compares; the session adds `project`, `agent` and,
for a pull request, `repository` and `git_remote`) that writes nothing and
spends nothing. A pull request's check also holds `tracker_items`
(`[{source, id}]`) when `plan` was given `--refs` (the `pr-refs.json` of
`ticket_refs.py freeze`, of the same project, else exit 3) and that holds an
entry for the pull request: `[]` when the entry cites nothing (the references
are known and empty; the brain clears the links it holds), else the tickets. The
brain treats a change of them as a change of the card. They come from
`ticket_refs.tickets_of`, the rule `stage` applies; a pull request with no
entry, or whose references `stage` would refuse (more than 50, a malformed
source), gets no key (omitted: the brain keeps what it holds), so its check is
the one made without `--refs`.
The row's fingerprint is its `source_version` still: the brain decides. An
answer of `source_changed: false` is recorded as `unchanged` and the item is
never summarised. Only a changed item is read, summarised once
and staged (`reference_card.py stage` writes the row's `write` file), then
written.

`next` says which step a row is at, and for a reference card it is always
`step: "check"`: the first call of a row in any run is the free check, whether
or not a card is staged, because only the brain's answer says whether the card
is to be sent and whether its participants have been erased since it was
staged (`erased_participants`), which makes the staged words stale. A row
carries `staged: true`, with `expected_sha256` (the digest the door will echo,
computed from the staged card as `verify` computes it) and `card_bytes`, when a
card for its current `source_version`, project and (pull request) repository
and remote is on disk; a check answer of
`source_changed: true` without an erased participant who took part
(`reference_card.py erased`) then sends that card without a new summary. A check answer of
`source_changed: true` that also carries `summary_needed: false` (a card of the
same item in this project or another holds a summary of exactly this version)
is written in the copy form: the card is staged with `reference_card.py stage
--copy`, has no `summary` and no `rationale_points`, and its row carries
`copy: true`. The copy form is outcome `written` like any other write. A row
with `copy: true` is sent only while the latest check says `summary_needed:
false`; an answer without the key (an older server) or `true`, or a write
refused with `code: summary_required`, means the words are written and the card
staged again. A row the ledger already holds as sent (`unchanged`) is offered
too, once per run: the brain may have lost the card, and the check is how that
is found. Such a row is written again at one budget unit without a new
question: the estimate and the plan count only the rows to send, and a settled
row is not one of them. A staged card is sent again, never summarised again, after a failed or
interrupted write, in this run or a later one, for as long as the source's
version has not moved. After a restage the digest is the stager's, never the
row's.

A task row of an inventory has no `source_version` yet (a ticket is read, with
its comments, only after the plan is approved): its fingerprint is then null,
it is counted by the ledger alone, and the plan that is sent from is made from
the shaped tasks, which carry one. A pull request row always has one.

One repository or several. Without `--repositories` the plan holds one
`--collected` and binds its pull requests by `--repository` and `--git-remote`.
With `--repositories` (`project_repos.py`'s file) `--collected` and
`--pull-list` are repeatable and `--repository` / `--git-remote` are a usage
error: each collection report names its `repository` (`owner/name`), and its
pull requests are bound to the `repository` and `git_remote` of the file's row
with that `repo` (compared case-insensitively). A report whose repository is
not in the file, whose row is not `ok`, or whose row has an empty `repository`
or `git_remote`, exits 3 naming it; two reports naming the same repository, and
several `--collected` or `--pull-list` without `--repositories`, are a usage
error. The pull requests of two repositories never share a key (their node ids
differ), and a card staged for one repository is not staged for another. The
plan's `summary.pull_request` adds up the pull lists and carries
`repositories`, the number of lists (or of collection reports), once
`--repositories` is given.

Every row has one binding source. With `--repositories`, a repository file row
(`git: true`) takes `repository` and `git_remote` from the file's row with its
`repo` (compared case-insensitively; a repository that is not an `ok` row of the
file exits 3 naming it, and the copies the row carried are replaced, never
trusted), and every other document row takes the binding of the first `ok` row
of the file, the other `ok` rows riding along as `fallbacks` that `next` offers
it under when the brain refuses that repository. Without it, a document row that is not kept in a repository takes
`--repository` and `--git-remote` when they are given.

Only a collection report whose `complete` is true is read: any other
contributes no row and is listed in the output of `plan` as `skipped_reports`
(`[{"path", "repository", "reason"}]`), which is not an error.

Task rows are ordered parent before child: a task whose `parent.id` is another
task row of the plan, of the same source, comes after that row; otherwise the
input order stands, and the rows of a parent cycle keep their relative order
(a task whose parent chain reaches a cycle follows the whole cycle). The parent
is read from the task row's own `parent` (`{id, kind?}`), else from the
`item.parent` of its card skeleton when that file exists.

`next` on a repository file's row adds `check_arguments` (the `Brain_tell`
arguments of the free check, as `artifact_cards.py args --mode check` prints them
for the row, with `agent` `brain-seed` and the plan's `project`) and, once the
row's `file` holds a non-empty `title` and `summary`, `write_arguments` (the
write form), or `write_error` (the refusal) when the write form refuses them. The row also carries `read_path`, where the file's text is read to
(`<work>/documents/<owner>__<name>/<path>`).

`next` lists the one row of a kind still to send, with `remaining` counting
those not yet recorded (the row returned included); after a stop it lists none
and names the stop. A document row carries no step and no digest.

Outcomes are a closed vocabulary. Successes: written, unchanged,
revision_proposed, not_reproposed. Refusals: refused (by the brain; held),
edge_refused (by the hosting edge in front of it), digest_refused (the door
refused the call for its `expected_sha256`: `verify --code` ended in
`record_digest_refused`, which `record` requires for this outcome). Stops
(nothing more is attempted in this run): throttled, access_denied,
quota_unset. Scoped: repository_denied (the brain refused the repository the
call carried, which `record` works out as `next` offered it; a row with no
repository is refused that outcome). `next` then stops offering that
repository's rows, and offers a page again under a repository that is not
refused. Others: partial, failed.

`record --answer-json` reads `{"detail", "budget", "unresolved"}` from stdin
instead of the three flags: server text never has to be quoted for a shell.
`unresolved` is the task answer's `unresolved_links`, as links or as objects
with a `url`.

`record` also reads this run's verifications of the row from the journal
(below) and writes what they decided onto the entry, so the session never
types it: `resends`, the number of re-sends they called for;
`digest_differs`, when the brain's digest differed from the file's the same
way twice, whatever the row's kind and outcome; and, for a `failed` record
with no detail after a difference, the detail that the brain received a
payload different from the file. It refuses a success after a verification
that ended `record_failed`, and `digest_refused` unless the row's latest
verification ended `record_digest_refused`.

`status` reports counts per outcome, the stop, the last budget the brain
reported in this run, the brain's refusals (`refused`, held, this run's and
earlier ones), this run's `edge_refused`, `digest_refused` and `failed` rows,
and the links on recorded tasks that no imported pull request resolves yet.
From the journal it adds `resends`, the `digest_differs` rows, and how many
tasks and pull requests recorded with a success this run were `verified` (at
least one verification matched) or `unverified` (none did, and none differed
the same way twice). A row recorded `unchanged` with no verification was
settled by its check call, stored nothing, and is neither.

`verify` checks that the brain received a row's staged card byte for byte. The
task and pull request doors answer with `received_sha256` (a server that
predates it answers without one): the lowercase hex SHA-256 of the UTF-8 bytes
of the payload they parsed, serialised with sorted keys, no ASCII escaping and
no whitespace (`json.dumps(payload, sort_keys=True, ensure_ascii=False,
separators=(",", ":"))`). The payload is a task's `item` and a pull request's
`pull_request`; the other arguments of a call (`project`, `repository`,
`git_remote`, `agent`, `agent_accounts`) are not covered. `verify` computes the
same digest from the row's staged card, appends the check to the ledger's
journal for this run, and prints `{"match", "expected", "received", "next"}`.
`next` is what the session does: `record` (the digests match), `resend` (this
key's first difference in this run), `record_differs` (a later difference with
the same digest as the first: not noise in the relay) or `record_failed` (a
later difference with another digest). The journal, not the session's memory,
holds the first digest, so the decision survives a compacted context. It keeps
the current run's checks only: a check from another run of the same project is
dropped when one is written. A document row is a usage error: its door answers
with no digest.

`verify --code` journals a refusal the door gave for its `expected_sha256`
(`payload_digest_mismatch` or `expected_sha256_malformed`), with the
`received_sha256` it carried or none, for that key, and prints
`{"code", "received", "next"}`: `resend` for the first such answer of the key
in this run, `record_digest_refused` for the second. A `resend` counts in
`resends` whichever kind of check gave it.

`summary` writes the lines a person reads, so the session shows them as they
come and never builds a report of its own from the other commands' output. It
only reads the ledger. `--stage estimate` counts what the plan holds to send,
`send` says what is about to go, and `done` says how the run ended in plain
words, one sentence for each item that needs attention (at most five); the
rest, with the ledger's own names, goes to the report file, which is the only
thing it writes (`.fairmind/brain-seed/report-<run>.json` beside the ledger,
unless `--report` names another, and never the ledger, the plan or the
collection report). stdout is `{"text", "report"}`, `report` null before the
`done` stage and when the report could not be written; the text then names
no file. A clean run ends with the summary alone. Each `--collected` (any
number) is read for the pull requests the collector could not read; the lists
are joined; an entry gets its report's `repository` only when the entry is an
object and its report names a repository. `--note` is a closed list of
observations the session made, kept in the report. Each `--extra` (any number)
is a JSON file another script wrote (`ticket_refs.py freeze` and
`ticket_links.py merge` with `--summary-out`); the report keeps it, unchanged,
under `extras` as `{file, summary}`. One that cannot be read as JSON is listed
in `problems` and the others are kept.

Exit codes: 0 ok (for `verify`, the digests match); 1 `verify` only, the
brain received a payload different from the file (act on `next`, not on
this code); 2 usage (an unknown key or
outcome leaves the ledger as it was); 3 an input or the ledger is not
readable JSON (a ledger that does not parse is never overwritten); 5 a file
cannot be written. stdout is one JSON document; diagnostics go to stderr.
stdlib only.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import uuid

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from link_normalize import document_slug
from login_key import notion_id, source_of
from _pull_request_url import pull_request_key
from pr_collect import (ReportUnreadable, parse_github_remote,
                        read_collection_report, repeated_repository,
                        skipped_report)
from ticket_refs import RefsRefused, read_pr_refs, tickets_of

LEDGER_SCHEMA = "brain-seed-ledger/2"
LEGACY_LEDGER_SCHEMA = "brain-seed-ledger/1"
# The kinds that are reference cards, and the argument that carries each card.
CARD_FIELDS = {"task": "item", "pull_request": "pull_request"}
# What a pull request row is staged under besides its project: the repository's
# catalog id and the checkout's remote, given to `plan`.
PULL_BOUND = ("repository", "git_remote")
PLAN_SCHEMA = "brain-seed-plan/2"
REPORT_SCHEMA = "brain-seed-report/1"
DEFAULT_LEDGER = os.path.join(".fairmind", "brain-seed", "ledger.json")
KINDS = ("document", "task", "pull_request")
SUCCESS = ("written", "unchanged", "revision_proposed", "not_reproposed")
# A refusal saying the call carries a field the contract does not know comes
# from a server older than the plugin. It is recorded `failed`, never
# `refused`: a refusal holds the row until its content changes, and an update
# of the server changes nothing in the row.
OLDER_SERVER = "carries fields this contract has not"
REFUSED = ("refused", "edge_refused", "digest_refused")
# Only the brain's own refusal holds a row until its content changes; the
# edge's never reached the brain, so its row is sent again.
HELD = ("refused",)
STOP = ("throttled", "access_denied", "quota_unset")
# A refusal that names one repository ("Access denied" to a call that carries
# its `repository`): that repository's items are not offered again this run
# and the others go on. `access_denied` is the stop for what applies to the
# whole project (a task, which carries no repository; a missing write scope).
SCOPED = ("repository_denied",)
OTHER = ("partial", "failed")
OUTCOMES = SUCCESS + REFUSED + STOP + SCOPED + OTHER
TO_SEND = ("new", "changed", "retry")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# The two codes of a refusal that the door's `expected_sha256` check gives.
CODES = ("payload_digest_mismatch", "expected_sha256_malformed")
# What `verify` tells the session to do next, and the details the ledger
# writes or hands out itself so that the session never types them.
NEXT = ("record", "resend", "record_differs", "record_failed",
        "record_digest_refused")
DIGEST_DIFFERS = "the brain's digest differs from the file's the same way twice"
RELAY_DIFFERS = "the brain received a payload different from the file"
NO_STAGED_CARD = ("the row has no staged card: stage it with "
                  "reference_card.py first")


class Usage(Exception):
    pass


class Unreadable(Exception):
    pass


class WriteFailed(Exception):
    pass


def _digest(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        raise Unreadable("cannot read %s %s as JSON" % (what, path))


def _write(path, document):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-seed-", mode=0o644,
                           indent=1)
    except OSError as err:
        raise WriteFailed("cannot write %s: %s"
                          % (path, err.strerror or err.__class__.__name__))


def _read_ledger(path):
    if not os.path.exists(path):
        return {"schema": LEDGER_SCHEMA, "seq": 0, "projects": {}, "journal": {}}
    ledger = _load(path, "the ledger")
    if (not isinstance(ledger, dict)
            or not isinstance(ledger.get("projects"), dict)
            or not isinstance(ledger.setdefault("journal", {}), dict)):
        raise Unreadable("the ledger %s is not a seeding ledger; it was left "
                         "as it is" % path)
    ledger.setdefault("seq", 0)
    schema = ledger.get("schema")
    if schema == LEGACY_LEDGER_SCHEMA or schema is None:
        _drop_legacy_rows(ledger)
    elif schema != LEDGER_SCHEMA:
        raise Unreadable("the ledger %s was written by another version of the "
                         "plugin (schema %r); it was left as it is"
                         % (path, schema))
    return ledger


def _drop_legacy_rows(ledger):
    """An earlier schema fingerprinted verbatim payloads: its task and pull
    request rows say nothing about a reference card, so they are forgotten and
    the items are checked again. Documents keep their rows."""
    for project, entries in list(ledger["projects"].items()):
        if not isinstance(entries, dict):
            continue
        ledger["projects"][project] = {
            key: entry for key, entry in entries.items()
            if not key.startswith(("task:", "pull_request:"))}
    ledger["journal"] = {}
    ledger["schema"] = LEDGER_SCHEMA


def _count(value):
    """A true non-negative int, else None: a hand-edited ledger may hold
    anything where a count belongs."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _safe(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:120]


def _status(entry, fingerprint, force):
    sent = (entry or {}).get("sent")
    if force:
        return "changed" if sent else "new"
    if entry is None:
        return "new"
    # Only a success leaves a row alone: any other latest attempt stored
    # nothing, or may have stored a payload the file does not hold, so the row
    # is sent again even when an earlier run stored the same content.
    if (sent and entry.get("outcome") in SUCCESS
            and (fingerprint is None or fingerprint == sent.get("fingerprint"))):
        return "unchanged"
    # Only the brain's own refusal of this exact content holds the row.
    if entry.get("outcome") in HELD and fingerprint == entry.get("fingerprint"):
        return "refused"
    if sent:
        return "changed"
    return "retry"


def _binding(args, bindings, name, what):
    """The `repository` and `git_remote` a row of `what` (a collection report,
    a repository file row, the repositories file itself) is bound to: from the
    file's row named `name`, else from the flags when there is no file."""
    if bindings is None:
        return {key: getattr(args, key) for key in PULL_BOUND
                if getattr(args, key)}
    if not isinstance(name, str) or not name:
        raise Unreadable("%s names no repository" % what)
    row = bindings.get(name.lower())
    if row is None:
        raise Unreadable("the repository %s of %s is not in the repositories "
                         "file" % (name, what))
    if row.get("status") != "ok":
        raise Unreadable("the repository %s is %s in the repositories file"
                         % (name, row.get("status") or "unusable"))
    missing = _missing(row)
    if missing:
        raise Unreadable("the repository %s has no %s in the repositories "
                         "file" % (name, missing))
    return {key: row.get(key) for key in PULL_BOUND}


def _missing(row):
    """The first of `repository` and `git_remote` a repositories file row
    lacks, or None."""
    return next((key for key in PULL_BOUND
                 if not isinstance(row.get(key), str) or not row[key]), None)


def _page_binding(args, bindings):
    """What a document that is not kept in a repository is bound to: the first
    `ok` repository of the file, else the flags. Nothing when the file has no
    `ok` repository. The door wants a repository on every call, but a page
    belongs to none, so the other `ok` repositories ride along as `fallbacks`:
    `next` offers the page under the first one the brain has not refused."""
    if bindings is None:
        return _binding(args, None, None, None)
    ok_rows = [r for r in bindings.values() if r.get("status") == "ok"]
    if not ok_rows:
        return {}
    bound = _binding(args, bindings, ok_rows[0]["repo"], "the repositories file")
    others = [{key: r[key] for key in PULL_BOUND} for r in ok_rows[1:]
              if not _missing(r)]
    if others:
        bound["fallbacks"] = others
    return bound


def _bindings(path):
    """The repositories file by lower-cased `owner/name`."""
    found = {}
    for row in _list(path, "the repositories file"):
        if isinstance(row.get("repo"), str):
            found.setdefault(row["repo"].lower(), row)
    return found


def _item_key(kind, source, ident):
    """The ledger key of an item: `<kind>:<source>:<id>`."""
    return "%s:%s:%s" % (kind, source, ident)


def _task_id(source, raw):
    """A task's permanent id as the ledger keys it: a Notion id in one
    spelling."""
    ident = str(raw)
    return (notion_id(ident) or ident) if source == "notion" else ident


def _parent_key(task, source, card):
    """`task:<source>:<id>` of the task's parent, or None: the row's own
    `parent`, else the card skeleton's `item.parent` when that file exists."""
    parent = task.get("parent")
    if not isinstance(parent, dict):
        try:
            document = _load(card, "the card")
        except Unreadable:
            return None
        item = document.get("item") if isinstance(document, dict) else None
        parent = item.get("parent") if isinstance(item, dict) else None
    if not isinstance(parent, dict) or parent.get("id") is None:
        return None
    return _item_key("task", source, _task_id(source, parent["id"]))


def _parents_first(tasks):
    """`(row, parent key)` pairs as rows in the order the module docstring
    gives. Iterative: a long chain never recurses."""
    index = {}
    for n, (row, _) in enumerate(tasks):
        index.setdefault(_item_key("task", row["source"], row["id"]), n)
    up = {n: index[parent] for n, (_, parent) in enumerate(tasks)
          if parent in index and index[parent] != n}
    cycle_of = {}
    for start in up:
        seen, at = set(), start
        while at in up and at not in seen:
            seen.add(at)
            at = up[at]
        if at == start:
            members = sorted(seen)
            for member in seen:
                cycle_of[member] = members
    ordered, done = [], set()

    def emit(member):
        if member not in done:
            done.add(member)
            ordered.append(tasks[member][0])

    for n in range(len(tasks)):
        if n in done:
            continue
        if n in cycle_of:
            emit(n)
            continue
        chain, at = [], n
        while at is not None and at not in done and at not in cycle_of:
            chain.append(at)
            at = up.get(at)
        if at in cycle_of:
            for member in cycle_of[at]:
                emit(member)
        for member in reversed(chain):
            emit(member)
    return ordered


_GIT_FIELDS = ("repo", "repository", "git_remote", "path", "commit", "branch",
               "blob_sha")


def _git_document(doc, work, seen, bound=None):
    """The row of a file kept in a repository: its key is the repository and
    path, its fingerprint the blob, and every entry field travels with it.
    `bound` (the repositories file's binding for its repository) replaces the
    row's own `repository` and `git_remote`."""
    for field in _GIT_FIELDS:
        value = doc.get(field)
        if bound is not None and field in PULL_BOUND:
            continue
        if not isinstance(value, str) or not value.strip():
            raise Unreadable("a repository file row has no %s" % field)
    ident = "%s:%s" % (doc["repo"].lower(), doc["path"])
    if ident in seen:
        raise Unreadable("two repository file rows name %s" % ident)
    seen.add(ident)
    row = dict(doc)
    row.update(bound or {})
    folder = "__".join(doc["repo"].split("/"))
    row.update({
        "kind": "document", "source": "git", "id": ident,
        "title": doc.get("title"), "fingerprint": _digest(doc["blob_sha"]),
        "units": 1,
        "file": os.path.join(work, "documents", document_slug("git:" + ident) + ".json"),
        "read_path": os.path.join(work, "documents", folder, *doc["path"].split("/"))})
    return row


def _rows(args, entries, bindings=None, skipped=None):
    rows = []
    seen_git = set()
    page_bound = None
    for documents in args.documents or []:
        for doc in _list(documents, "the documents"):
            if doc.get("git") is True:
                bound = None if bindings is None else _binding(
                    args, bindings, doc.get("repo"), "a repository file row")
                rows.append(_git_document(doc, args.work, seen_git, bound))
                continue
            if page_bound is None:
                page_bound = _page_binding(args, bindings)
            link = doc.get("link")
            if not isinstance(link, str) or not link:
                raise Unreadable("a document row has no link")
            source = str(doc.get("source") or "web").lower()
            slug = doc.get("slug") or document_slug(link)
            edited = doc.get("edited")
            rows.append({
                "kind": "document", "source": source, "id": link,
                "title": doc.get("title"),
                "fingerprint": _digest(edited) if edited else None,
                "units": 1,
                "file": os.path.join(args.work, "documents", slug + ".json")})
            rows[-1].update(page_bound)
            if doc.get("local"):
                rows[-1]["local"] = True
            elif isinstance(edited, (str, int)) and not isinstance(edited, bool) \
                    and str(edited):
                # Confluence's marker is a number; the door compares strings.
                rows[-1]["version"] = str(edited)
    if args.tasks:
        tasks = []
        for task in _list(args.tasks, "the tasks"):
            if task.get("id") is None or not task.get("source"):
                raise Unreadable("a task row has no source or id")
            try:
                source = source_of(task["source"])
            except ValueError:
                source = str(task["source"]).lower()
            tid = _task_id(source, task["id"])
            # The estimate is planned from the inventory, before any ticket is
            # read, so a row may have no version yet: it is then counted by the
            # ledger alone, and the plan that is sent from carries the versions.
            version = (None if task.get("source_version") is None
                       else _version(task, "a task row"))
            card = task.get("card") or os.path.join(
                args.work, "tasks", "%s-%s" % (source, _safe(tid)), "card.json")
            tasks.append((_card_row(
                "task", source, tid, task.get("title"), version, card,
                {"item": {"source": source, "id": tid,
                          "source_version": version}}),
                _parent_key(task, source, card)))
        rows.extend(_parents_first(tasks))
    refs = _references(args.refs, args.project) if args.refs else None
    named = set()
    for path in args.collected or []:
        report = _collection_report(path)
        again = repeated_repository(report, named)
        if again:
            raise Usage("two --collected reports name the repository %s" % again)
        if report.get("complete") is not True:
            skipped.append(skipped_report(path, report))
            continue
        bound = _binding(args, bindings, report.get("repository"),
                         "a collection report")
        for pull in report.get("pull_requests") or []:
            version = _version(pull, "a pull request in the collection report")
            if not pull.get("card"):
                raise Unreadable("a pull request in the collection report has "
                                 "no card")
            check = {"pull_request": {"source": "github", "id": pull["id"],
                                      "source_version": version}}
            items = _tracker_items(refs, pull, report.get("repository")
                                   or pull.get("repository"))
            if items is not None:
                check["pull_request"]["tracker_items"] = items
            row = _card_row("pull_request", "github", pull["id"],
                            pull.get("title"), version, pull["card"], check)
            row.update(number=pull.get("number"), url=pull.get("url"), **bound)
            rows.append(row)
    for row in rows:
        row["key"] = _item_key(row["kind"], row["source"], row["id"])
        row["status"] = _status(entries.get(row["key"]), row["fingerprint"],
                                args.all)
    return rows


def _references(path, project):
    """The parsed `pr-refs.json` at `path`, which must be of `project`."""
    try:
        return read_pr_refs(path, project)
    except (OSError, ValueError) as err:
        raise Unreadable(str(err))


def _tracker_items(refs, pull, repository):
    """The tickets a pull request cites, as its check carries them: None (no
    key) with no references, no entry, or an entry `reference_card.py stage`
    would refuse (that refusal is the row's to record, not the plan's); `[]`
    for an entry that cites nothing."""
    if refs is None:
        return None
    try:
        return tickets_of(refs, pull["id"], repository, pull.get("number"))
    except RefsRefused as err:
        print("seed_ledger: %s: %s; its check carries no tickets"
              % (pull["id"], err), file=sys.stderr)
        return None


def _version(record, what):
    version = record.get("source_version")
    if not isinstance(version, str) or not version:
        raise Unreadable("%s has no source_version" % what)
    return version


def _card_row(kind, source, ident, title, version, card, check):
    """A reference-card row: it is checked against the brain first, staged
    beside its skeleton once the agent has written its words, then sent."""
    return {"kind": kind, "source": source, "id": ident, "title": title,
            "fingerprint": version, "source_version": version, "units": 1,
            "card": card,
            "write": os.path.join(os.path.dirname(card) or ".", "write.json"),
            "check": check}


def _collection_report(path):
    try:
        return read_collection_report(path)
    except ReportUnreadable as err:
        raise Unreadable(str(err))


def _list(path, what):
    document = _load(path, what)
    if not isinstance(document, list) or not all(
            isinstance(r, dict) for r in document):
        raise Unreadable("%s %s must be a JSON list of objects" % (what, path))
    return document


def _summary(rows):
    summary = {}
    for row in rows:
        counts = summary.setdefault(row["kind"], {
            "new": 0, "changed": 0, "retry": 0, "unchanged": 0, "refused": 0,
            "units": 0})
        counts[row["status"]] += 1
        if row["status"] in TO_SEND:
            counts["units"] += row["units"]
    return summary


def plan(args):
    if not (args.documents or args.tasks or args.collected or args.pull_list):
        raise Usage("pass at least one of --documents, --tasks, --collected, "
                    "--pull-list")
    collected, lists = args.collected or [], args.pull_list or []
    if args.repositories:
        if args.repository or args.git_remote:
            raise Usage("--repositories binds each repository itself: do not "
                        "pass --repository or --git-remote with it")
    elif len(collected) > 1 or len(lists) > 1:
        raise Usage("several --collected or --pull-list need --repositories "
                    "to bind each repository")
    bindings = _bindings(args.repositories) if args.repositories else None
    ledger_path = args.ledger or DEFAULT_LEDGER
    ledger = _read_ledger(ledger_path)
    entries = ledger["projects"].get(args.project) or {}
    skipped = []
    rows = _rows(args, entries, bindings, skipped)
    for row in rows:
        if row["kind"] in CARD_FIELDS:
            row["project"] = args.project
    summary = _summary(rows)
    if lists and not collected:
        total = sum(len(_list(path, "the pull request list")) for path in lists)
        summary["pull_request"] = {"in_window": total, "units_at_most": total}
        repositories = len(lists)
    else:
        repositories = len(collected)
    if bindings is not None and "pull_request" in summary:
        summary["pull_request"]["repositories"] = repositories
    # A run id is a random identifier only: a ledger row records what a
    # person approved to send, so no wall-clock value is written onto it.
    run = uuid.uuid4().hex
    document = {"schema": PLAN_SCHEMA, "run": run, "project": args.project,
                "ledger": ledger_path, "work": args.work, "rows": rows,
                "summary": summary}
    out = args.out or os.path.join(args.work, "plan.json")
    _write(out, document)
    result = {"run": run, "out": out, "summary": summary}
    if skipped:
        result["skipped_reports"] = skipped
    return result


def _plan(path):
    document = _load(path, "the plan")
    if not isinstance(document, dict) or document.get("schema") != PLAN_SCHEMA:
        raise Unreadable("%s is not a seeding plan" % path)
    for row in document.get("rows") or []:
        if isinstance(row, dict) and row.get("kind") in CARD_FIELDS \
                and "project" not in row:
            row["project"] = document.get("project")
    return document


def _row(document, key):
    row = next((r for r in document["rows"] if r["key"] == key), None)
    if row is None:
        raise Usage("%s is not in the plan" % key)
    return row


def _entries(document, ledger=None):
    path = ledger or document["ledger"]
    ledger = _read_ledger(path)
    return path, ledger, ledger["projects"].get(document["project"]) or {}


def _journal(ledger, project):
    checks = ledger["journal"].get(project)
    if not isinstance(checks, list):
        return []
    return [c for c in checks if isinstance(c, dict)]


def _checks(ledger, document, key=None):
    """This run's verifications, in the order they were made; only one row's
    when `key` is given."""
    return [c for c in _journal(ledger, document["project"])
            if c.get("run") == document["run"]
            and (key is None or c.get("key") == key)]


def _stopped(document, entries):
    found = [(e.get("seq", 0), k, e) for k, e in entries.items()
             if e.get("run") == document["run"] and e.get("outcome") in STOP]
    if not found:
        return None
    _, key, entry = max(found, key=lambda f: f[0])
    return {"key": key, "outcome": entry["outcome"]}


def _denied(document, entries):
    """The repositories the brain refused in this run, by id."""
    return {repository for e in _this_run(entries, document["run"]).values()
            for repository in e.get("denied") or []}


def _candidates(row):
    """The bindings a row can be sent under, in the order they are tried: its
    own, then a page's `fallbacks`."""
    return [row, *row.get("fallbacks", [])]


def _offered(row, denied):
    """The row as it can be sent now, under its first binding the brain has
    not refused and without its `fallbacks`, or None when every binding is
    refused. A row with no repository (a task) is always offered."""
    for bound in _candidates(row):
        if bound.get("repository") not in denied:
            offered = dict(row, **{key: bound[key] for key in PULL_BOUND
                                   if key in bound})
            offered.pop("fallbacks", None)
            return offered
    return None


def offered(document, entries, kind, repairs=None):
    """The rows `next` would offer for `kind`, in order, and the run's stop
    (or None). A stopped run offers nothing to send: `todo` is still the
    count of what is left. A row whose repair is pending (`repairs`, from
    `repairs_of`) carries `repair: true`: the brain calls its version unchanged
    because a write of it stored a card the staged file does not match, so
    `source_changed: false` must not settle it."""
    stopped = _stopped(document, entries)
    denied = _denied(document, entries)
    # A reference card the ledger holds as sent is still offered, at the check
    # step: the brain decides whether its source changed, which is how a card
    # removed there comes back. A document settled in the ledger is not.
    todo = [r for r in document["rows"]
            if r["kind"] == kind
            and (r["status"] in TO_SEND
                 or (r["status"] == "unchanged" and r["kind"] in CARD_FIELDS))
            and ((entries.get(r["key"]) or {}).get("run") != document["run"]
                 or entries[r["key"]].get("outcome") in SCOPED)]
    todo = [row for r in todo if (row := _offered(r, denied))]
    repairs = repairs or {}
    todo = [dict(row, repair=True) if repairs.get(row["key"]) == row["fingerprint"] else row
            for row in todo]
    return todo, stopped


def next_rows(args):
    document = _plan(args.plan)
    _, ledger, entries = _entries(document, args.ledger)
    todo, stopped = offered(document, entries, args.kind,
                            repairs_of(ledger, document["project"]))
    shown = [] if stopped else [_with_step(todo[0])] if todo else []
    if shown and shown[0].get("git"):
        shown = [_with_arguments(shown[0], document["project"])]
    elif shown and shown[0]["kind"] == "document" and _page_copy(shown[0]):
        shown = [dict(shown[0], copy=True)]
    return {"rows": shown, "remaining": len(todo), "stopped": stopped}


AGENT = "brain-seed"


def _with_arguments(row, project):
    """A repository file's row with the `Brain_tell` arguments of its two
    calls, built by `artifact_cards.build_args` so a session never types a row
    or a digest: `check_arguments` always, `write_arguments` once the row's
    `file` holds a non-empty `title` and `summary` and the write form accepts
    them."""
    from artifact_cards import CliError, build_args
    entry = {key: row.get(key) for key in (
        "path", "repository", "git_remote", "blob_sha", "commit", "branch",
        "source_visibility")}
    entry.update(agent=AGENT, project=project)
    found = dict(row)
    try:
        found["check_arguments"] = build_args(entry, mode="check")
    except CliError as err:
        raise Unreadable("%s: %s" % (row["key"], err))
    try:
        with open(row["file"], encoding="utf-8") as handle:
            words = json.load(handle)
    except (OSError, ValueError):
        return found
    if not isinstance(words, dict) or not all(
            isinstance(words.get(k), str) and words[k].strip()
            for k in ("title", "summary")):
        return found
    try:
        found["write_arguments"] = build_args(
            dict(entry, title=words["title"], summary=words["summary"]),
            mode="write")
    except CliError as err:
        found["write_error"] = str(err)
    return found


def _page_copy(row):
    """Whether a page row's `file` is the copy form for the row's version:
    `copy: true`, no words, and the version the row is checked at. A file that
    cannot be read, that holds a summary, or that a run wrote for another
    version is not."""
    try:
        words = _load(row["file"], "the document file")
    except Unreadable:
        return False
    return (isinstance(words, dict) and words.get("copy") is True
            and not str(words.get("summary") or "").strip()
            and row.get("version") is not None
            and words.get("version") == row["version"])


def _staged(row):
    """The staged card for this row's current source version, project and, for
    a pull request, repository and remote, or None: no file, an unreadable
    one, one staged for an older version of the source, or one staged for
    another project, repository or remote than the plan's. A plan made without
    a pull request's repository and remote holds no staged card for it."""
    try:
        document = _load(row["write"], "the staged card")
    except Unreadable:
        return None
    arguments = document.get("arguments") if isinstance(document, dict) else None
    card = (arguments or {}).get(CARD_FIELDS[row["kind"]]) \
        if isinstance(arguments, dict) else None
    if not isinstance(arguments, dict):
        return None
    bound = ("project",) + (PULL_BOUND if row["kind"] == "pull_request" else ())
    if any(arguments.get(key) != row.get(key) for key in bound):
        return None
    if isinstance(card, dict) and card.get("source_version") == row["fingerprint"]:
        return card
    return None


def _with_step(row):
    """The row with the step it is at. A reference card is always offered at
    `check`, staged or not: its check answer says whether to send a staged card
    and whether an erasure made it stale. A staged card carries the digest the
    door will echo, computed as `verify` computes it, so the two cannot
    disagree, and is never summarised again. A staged card with no `summary`
    is the copy form (`reference_card.py stage --copy`) and carries
    `copy: true`: it is sent only while the latest check answers
    `summary_needed: false`, and is otherwise staged again with words."""
    if row["kind"] not in CARD_FIELDS:
        return row
    card = _staged(row)
    if card is None:
        return dict(row, step="check", staged=False)
    try:
        digest = _digest(card)
        size = len(json.dumps(card, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return dict(row, step="check", staged=False)
    staged = dict(row, step="check", staged=True, expected_sha256=digest,
                  card_bytes=size)
    if "summary" not in card:
        staged["copy"] = True
    return staged


def _links(unresolved):
    """The links of a task answer's `unresolved_links`: each is a link, or an
    object that carries it as `url` (the shape the door answers with)."""
    if unresolved is None:
        return None
    if not isinstance(unresolved, list):
        raise Usage("--unresolved takes a JSON list of links")
    links = []
    for item in unresolved:
        link = item.get("url") if isinstance(item, dict) else item
        if not isinstance(link, str):
            raise Usage("--unresolved takes a JSON list of links, or of "
                        "objects with a url")
        links.append(link)
    return links


def record(args):
    document = _plan(args.plan)
    row = _row(document, args.key)
    if args.outcome not in OUTCOMES:
        raise Usage("--outcome takes one of %s" % ", ".join(OUTCOMES))
    detail, budget, unresolved = args.detail, None, None
    try:
        if args.answer_json:
            answer = json.loads(sys.stdin.read())
            if not isinstance(answer, dict):
                raise Usage("--answer-json takes a JSON object on stdin")
            detail = answer.get("detail", detail)
            budget = answer.get("budget")
            unresolved = answer.get("unresolved")
        if args.budget is not None:
            budget = json.loads(args.budget)
        if args.unresolved is not None:
            unresolved = json.loads(args.unresolved)
    except ValueError:
        raise Usage("--budget, --unresolved and --answer-json take JSON")
    return record_row(document, row, args.outcome, detail=detail, budget=budget,
                      unresolved=unresolved, ledger=args.ledger)


def record_row(document, row, outcome, *, detail=None, budget=None,
               unresolved=None, ledger=None):
    """Record `outcome` for `row` of `document`. `unresolved` is a list of
    links or of objects with a `url` (see `_links`); `ledger` overrides the
    plan's ledger path. What the `record` command does once it has parsed its
    arguments."""
    unresolved = _links(unresolved)
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    path, ledger, entries = _entries(document, ledger)
    # What this run's verifications of the row decided is read from the
    # journal, never from the session.
    checks = _checks(ledger, document, row["key"])
    verdicts = [c.get("next") for c in checks]
    last = verdicts[-1] if verdicts else None
    if last == "record_failed" and outcome in SUCCESS:
        raise Usage("the last verification of %s found the brain holding a "
                    "payload different from the file: record it failed"
                    % row["key"])
    if outcome == "digest_refused" and last != "record_digest_refused":
        raise Usage("the last verification of %s did not end in "
                    "record_digest_refused: verify the refusal's code first"
                    % row["key"])
    if (outcome == "failed" and detail is None
            and last in ("resend", "record_failed")
            and not checks[-1].get("code")):
        detail = RELAY_DIFFERS
    if outcome == "refused" and OLDER_SERVER in (detail or ""):
        outcome = "failed"
    refused = _refused_repository(row, document, entries, outcome)
    ledger["seq"] += 1
    old = entries.get(row["key"]) or {}
    entry = {
        "fingerprint": row["fingerprint"], "outcome": outcome,
        "detail": detail, "run": document["run"],
        "seq": ledger["seq"],
        "sent": old.get("sent"),
        "url": row.get("url") or old.get("url"),
        "unresolved_links": unresolved if unresolved is not None
        else old.get("unresolved_links", []),
        "budget": budget,
    }
    # A refusal is remembered on the row for the rest of the run, even when
    # the row is later sent under another repository.
    denied = list(old.get("denied") or []) if old.get("run") == document["run"] else []
    if refused and refused not in denied:
        denied.append(refused)
    if denied:
        entry["denied"] = denied
    if verdicts.count("resend"):
        entry["resends"] = verdicts.count("resend")
    if "record_differs" in verdicts:
        entry["digest_differs"] = DIGEST_DIFFERS
    if outcome in SUCCESS:
        entry["sent"] = {"fingerprint": row["fingerprint"],
                         "run": document["run"]}
        _clear_repair(ledger, document["project"], row["key"])
    entries[row["key"]] = entry
    ledger["projects"][document["project"]] = entries
    _write(path, ledger)
    return {"key": row["key"], "outcome": outcome,
            "stop": outcome in STOP}


def _refused_repository(row, document, entries, outcome):
    """The repository a scoped refusal of `row` names: the one `next` offered
    the row under, worked out as `next` did, so the session never types it.
    None for any other outcome."""
    if outcome not in SCOPED:
        return None
    offered = _offered(row, _denied(document, entries))
    if offered is None:
        raise Usage("%s is not offered: every repository it can go under is "
                    "refused this run" % row["key"])
    if not offered.get("repository"):
        raise Usage("%s carries no repository: record its Access denied as "
                    "access_denied" % row["key"])
    return offered["repository"]


def status(args):
    document = _plan(args.plan)
    _, ledger, entries = _entries(document, args.ledger)
    return _status_of(document, ledger, entries)


def _checks_by_key(ledger, document):
    """This run's verifications, grouped by the row they belong to."""
    made = {}
    for check in _checks(ledger, document):
        made.setdefault(check.get("key"), []).append(check)
    return made


def _nothing_written(row, outcome, made):
    """A row settled by the check call (the source had not changed) stored
    nothing, so there is no stored card to confirm and none that went
    unconfirmed. That holds for a row the ledger already held as sent, and for
    any row with no card staged for its current version. A row that was to be
    sent and has a card staged was at the write step, so an `unchanged` answer
    recorded for it without a verification stays among the unverified."""
    return (outcome == "unchanged" and not made
            and (row["status"] == "unchanged" or _staged(row) is None))


def _this_run(entries, run):
    """The entries recorded in run `run`."""
    return {k: e for k, e in entries.items() if e.get("run") == run}


def _denied_names(document, entries):
    """The repositories the brain refused in this run as `owner/name`, taken
    from the remote of the plan's rows that carry the refused id. A binding
    with no remote (`--repository` alone) is this folder's own."""
    remotes = {}
    for row in document["rows"]:
        for bound in _candidates(row):
            if bound.get("repository") and bound.get("git_remote"):
                remotes.setdefault(bound["repository"], bound["git_remote"])
    return [(parse_github_remote(remotes[r]) or remotes[r]) if r in remotes
            else UNNAMED_REPOSITORY for r in sorted(_denied(document, entries))]


def _key_may_be_foreign(document, entries):
    """Whether the refusals point at the key rather than at one repository:
    the brain refused every repository a row of the plan can go under, and no
    task went through this run. A task's call names the project, and the door
    refuses a key bound to another project before anything else."""
    repositories = {bound["repository"] for row in document["rows"]
                    for bound in _candidates(row) if bound.get("repository")}
    this_run = _this_run(entries, document["run"])
    task_through = any(key.startswith("task:") and e.get("outcome") in SUCCESS
                       for key, e in this_run.items())
    return (bool(repositories) and not task_through
            and repositories <= _denied(document, entries))


def _status_of(document, ledger, entries):
    run = document["run"]
    by_kind, refused, budget, best = {}, [], None, -1
    listed = {outcome: [] for outcome in ("edge_refused", "digest_refused", "failed")}
    # The verification counts come from the journal, which holds a check made
    # before a compaction even when its row was never recorded.
    checks = _checks_by_key(ledger, document)
    differs = [key for key, made in checks.items()
               if any(c.get("next") == "record_differs" for c in made)]
    verified = unverified = 0
    for row in document["rows"]:
        counts = by_kind.setdefault(row["kind"], {"not_attempted": 0})
        entry = entries.get(row["key"])
        if entry and entry.get("run") == run:
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
            if entry["outcome"] in HELD:
                refused.append({"key": row["key"], "outcome": entry["outcome"],
                                "detail": entry.get("detail")})
            elif entry["outcome"] in listed:
                listed[entry["outcome"]].append(
                    {"key": row["key"], "detail": entry.get("detail")})
            if (entry["outcome"] in SUCCESS and row["kind"] != "document"
                    and row["key"] not in differs
                    and not _nothing_written(row, entry["outcome"],
                                             checks.get(row["key"], []))):
                if any(c.get("match") is True for c in checks.get(row["key"], [])):
                    verified += 1
                else:
                    unverified += 1
            if entry.get("budget") is not None and entry.get("seq", 0) > best:
                best, budget = entry.get("seq", 0), entry["budget"]
        elif row["status"] in TO_SEND:
            counts["not_attempted"] += 1
        elif row["status"] == "unchanged":
            counts["unchanged_earlier"] = counts.get("unchanged_earlier", 0) + 1
        elif row["status"] == "refused":
            counts["refused_earlier"] = counts.get("refused_earlier", 0) + 1
            refused.append({"key": row["key"], "outcome": entry["outcome"],
                            "detail": entry.get("detail"), "earlier": True})
    resolved = {pull_request_key(e.get("url")) for k, e in entries.items()
                if k.startswith("pull_request:")
                and e.get("outcome") in SUCCESS and e.get("url")}
    open_links = [{"task": k, "url": u} for k, e in entries.items()
                  if k.startswith("task:")
                  for u in e.get("unresolved_links") or []
                  if pull_request_key(u) not in resolved]
    this_run = _this_run(entries, run)
    return {"run": run, "by_kind": by_kind, "stopped": _stopped(document, entries),
            "budget": budget, "refused": refused, **listed,
            "denied_repositories": _denied_names(document, entries),
            # `outcome` is null while a row that differed is not recorded yet.
            "digest_differs": [{"key": key,
                                "outcome": this_run.get(key, {}).get("outcome"),
                                "detail": DIGEST_DIFFERS} for key in differs],
            "resends": sum(1 for made in checks.values() for c in made
                           if c.get("next") == "resend"),
            "verified": verified, "unverified": unverified,
            "open_links": open_links}


def _target(row):
    """Refuse a row that has no digest to check."""
    if row["kind"] not in CARD_FIELDS:
        raise Usage("verify applies to a task or a pull request: the document "
                    "door answers with no digest")


def _relayed(row):
    """The payload the door digests for this row, read from the staged card:
    a task's `item`, or a pull request's `pull_request`, as the door parsed
    it."""
    _target(row)
    document = _load(row["write"], "the staged card")
    arguments = document.get("arguments") if isinstance(document, dict) else None
    card = arguments.get(CARD_FIELDS[row["kind"]]) \
        if isinstance(arguments, dict) else None
    if not isinstance(card, dict):
        raise Unreadable("%s holds no %s: %s" % (
            row["write"], CARD_FIELDS[row["kind"]], NO_STAGED_CARD))
    return card


def _next(match, received, earlier, code=None):
    """What the session does after this check, from this run's earlier
    checks of the same key. An answer with a refusal code is a re-send the
    first time and ends the row the second."""
    if code:
        coded = [c for c in earlier if c.get("code")]
        return "record_digest_refused" if coded else "resend"
    if match:
        return "record"
    first = next((c for c in earlier if c.get("match") is False), None)
    if first is None:
        return "resend"
    return "record_differs" if first.get("received") == received else "record_failed"


def usable_digest(value):
    """`value` as a lowercase 64-digit hex digest, or None when it is not one
    (padding and case are forgiven)."""
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if SHA256_HEX.fullmatch(text) else None


def _received(args):
    """The digest of the answer as a lowercase hex string, or None for a
    refusal that carried none (only with --code)."""
    text = args.received or ""
    if args.code and text.strip().lower() in ("", "null"):
        return None
    digest = usable_digest(text)
    if digest is None:
        raise Usage("--received takes the 64 hexadecimal characters of the "
                    "answer's received_sha256")
    return digest


def verify(args):
    if args.code is not None and args.code not in CODES:
        raise Usage("--code takes one of %s" % ", ".join(CODES))
    if args.code is None and args.received is None:
        raise Usage("pass --received, or --code for a refusal that carries one")
    received = _received(args)
    document = _plan(args.plan)
    return verify_row(document, _row(document, args.key), received,
                      code=args.code, ledger=args.ledger)


def _repairs(ledger, project):
    """The ledger's `repair` map for `project`, created on demand: key -> the
    source version whose stored card is known (or may be) different from the
    staged file."""
    return ledger.setdefault("repair", {}).setdefault(project, {})


def _clear_repair(ledger, project, key):
    ((ledger.get("repair") or {}).get(project) or {}).pop(key, None)


def repairs_of(ledger, project):
    """The pending repairs of `project` (key -> source version), read only."""
    return (ledger.get("repair") or {}).get(project) or {}


def mark_repair(document, row, ledger=None):
    """Record that a write of this row's current version has an unknown
    outcome (the cap was reached before it could be looked at again)."""
    path, found, _ = _entries(document, ledger)
    _repairs(found, document["project"])[row["key"]] = row["fingerprint"]
    _write(path, found)


def _counts(ledger, document, create=False):
    """The per-row counters of the plan's run: `{key: {"writes", "handbacks"}}`.
    A different run starts them again, and the old run's are dropped."""
    held = ledger.get("counts", {}).get(document["project"])
    if not isinstance(held, dict) or held.get("run") != document["run"]:
        if not create:
            return {}
        held = ledger.setdefault("counts", {})[document["project"]] = {
            "run": document["run"], "rows": {}}
    return held["rows"]


def row_counts(document, row, ledger=None):
    """How many writes the sender made for this row in this run and how many
    times it handed the row back for words."""
    _, found, _ = _entries(document, ledger)
    held = _counts(found, document).get(row["key"]) or {}
    return {"writes": held.get("writes", 0), "handbacks": held.get("handbacks", 0)}


def count_row(document, row, name, ledger=None):
    """Add one to counter `name` ("writes" or "handbacks") of this row in this
    run and return the new value."""
    path, found, _ = _entries(document, ledger)
    held = _counts(found, document, create=True).setdefault(
        row["key"], {"writes": 0, "handbacks": 0})
    held[name] += 1
    _write(path, found)
    return held[name]


def verify_row(document, row, received, *, code=None, ledger=None):
    """Check the digest a write answer echoed (`received`, lowercase hex or
    None for a refusal that carried none) against the staged file, or the
    refusal `code` of a write, and journal the check. Returns what `verify`
    prints; `next` says what to do."""
    path, ledger, _ = _entries(document, ledger)
    earlier = _checks(ledger, document, row["key"])
    check = {"run": document["run"], "key": row["key"], "received": received}
    if code:
        _target(row)
        verdict = _next(None, received, earlier, code)
        result = {"code": code, "received": received, "next": verdict}
        check.update(code=code, match=None)
    else:
        expected = _digest(_relayed(row))
        verdict = _next(expected == received, received, earlier)
        result = {"match": expected == received, "expected": expected,
                  "received": received, "next": verdict}
        check["match"] = expected == received
    ledger["seq"] += 1
    check.update(next=verdict, seq=ledger["seq"])
    if not code:
        # A mismatch means the write stored the changed version: the brain now
        # calls it unchanged, so the repair has to outlive this process and
        # this run. Only a recorded success clears it (`record_row`): a
        # matching digest on an answer that stored nothing settles nothing.
        if verdict == "resend":
            _repairs(ledger, document["project"])[row["key"]] = row["fingerprint"]

    # The journal keeps this run's checks only: an earlier run's are dropped.
    project = document["project"]
    ledger["journal"][project] = [
        c for c in _journal(ledger, project) if c.get("run") == document["run"]
    ] + [check]
    _write(path, ledger)
    return result


# What a person reads for each outcome, as the label of the group it is counted
# in. An outcome added to the vocabulary has to be placed here.
PLAIN = {
    "written": "stored", "revision_proposed": "stored",
    "unchanged": "already up to date", "not_reproposed": "already up to date",
    "refused": "not accepted",
    "edge_refused": "to redo", "digest_refused": "to redo",
    "throttled": "to redo", "access_denied": "to redo", "quota_unset": "to redo",
    "repository_denied": "to redo",
    "partial": "to redo", "failed": "to redo",
    # Counted by `status`, not recorded: they are not outcomes.
    "unchanged_earlier": "already up to date", "refused_earlier": "not accepted",
    "not_attempted": "to redo"}
GROUPS = ("stored", "already up to date", "to redo", "not accepted")
SUMMARY_KINDS = (("task", "task"), ("pull_request", "pull request"),
                 ("document", "document"))
STAGES = ("estimate", "send", "done")
STOP_LINES = {
    "throttled": "Today's write allowance is used up. Run this again tomorrow "
                 "and it continues from here, or a project admin can open "
                 "setup mode for this project to raise the allowance for a few "
                 "days (if setup mode is not enabled for your workspace, ask "
                 "FairMind).",
    "quota_unset": "Your workspace has no daily write allowance yet. Ask your "
                   "FairMind admin to set one, then run this again.",
    "access_denied": "This session can't write to this project's brain: either "
                     "your account lacks the editor role here (ask a project "
                     "admin), or this folder's FairMind key belongs to another "
                     "project (run /fairmind-connect with the right project). "
                     "Then run this again."}
UNNAMED_REPOSITORY = "the repository this folder is bound to"
EVERY_REPOSITORY_DENIED = (
    "It refused every repository this run could send to, so this folder's "
    "FairMind key may belong to another project: run /fairmind-connect with "
    "the right project. ")
NOTES = {
    "no_files": "The lists of changed files could not be sent for these pull "
                "requests.",
    "not_code_ingestion": "Links from these pull requests to the code could "
                          "not be resolved.",
    "no_relay_digest": "The server cannot confirm yet that what it received "
                       "is what was sent.",
    "no_expected_sha256": "The server cannot check a send before it writes "
                          "yet.",
    "no_source": "Documents were saved without the name of the system they "
                 "come from."}
# The notes a person reads by default; the others stay in the report.
SHOWN_NOTES = ("no_files", "not_code_ingestion")
ATTENTION = {
    "digest_refused": "%s was not stored: it arrived different from the "
                      "original, so nothing was written. It is sent again "
                      "next run.",
    "relay_differs": "%s arrived different from the original and could not be "
                     "corrected. The brain may hold the altered copy until "
                     "the next run sends it again.",
    "digest_differs": "%s was stored, but what arrived differed from the "
                      "original the same way twice. Worth a look.",
    "edge_refused": "%s was blocked by a filter in front of FairMind before it "
                    "arrived. Nothing was stored; it is sent again next run.",
    "refused": "%s was not accepted by the brain. It is not sent again until "
               "it changes.",
    "failed": "%s didn't go through. It is sent again next run.",
    "differs_unsettled": "%s arrived different from the original; the brain "
                         "may hold the altered copy until the next run sends "
                         "it again.",
    "unrecorded": "%s was sent but its answer wasn't recorded. It is sent "
                  "again next run.",
    "partial": "%s was only partly sent; the rest goes next run.",
    "repository_denied": "%s was not sent: the brain refused its repository. "
                         "It is sent again next run.",
    "unknown": "%s ended in a state this version doesn't recognise. It is "
               "sent again next run."}
MAX_ATTENTION = 5
# The outcomes whose ATTENTION sentence is keyed by the outcome itself.
SAID_AS_OUTCOME = ("refused", "failed", "partial", "repository_denied")
# The sentences that say a row arrived different from the original; the
# integrity line counts exactly the rows that get one.
DIFFERENCES = ("digest_refused", "relay_differs",
               "digest_differs", "differs_unsettled")


def _counted(count, noun):
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def _joined(items):
    return items[0] if len(items) < 2 else \
        "%s and %s" % (", ".join(items[:-1]), items[-1])


def _tally(counts, up_to=(), repositories=1):
    """`2 tasks, 1 pull request and 3 documents`, the kinds with none left
    out; a kind in `up_to` is said as an upper bound, and the pull requests of
    several repositories say how many."""
    return _joined([("up to " if kind in up_to else "") + _counted(counts[kind], noun)
                    + (" across %d repositories" % repositories
                       if kind == "pull_request" and repositories > 1 else "")
                    for kind, noun in SUMMARY_KINDS if counts.get(kind)])


def _estimate_lines(document):
    found, units, skipped, held, atmost = {}, 0, 0, 0, False
    for kind, counts in document["summary"].items():
        if "in_window" in counts:
            found[kind] = counts["in_window"]
            units += counts["units_at_most"]
            atmost = True
            continue
        found[kind] = sum(counts[s] for s in TO_SEND)
        units += counts["units"]
        skipped += counts["unchanged"]
        held += counts["refused"]
    if not any(found.values()):
        return ["Nothing new to send."]
    # The estimate cannot see a task's pull request links yet, so it counts
    # a task that a re-run would find unchanged.
    repositories = document["summary"].get("pull_request", {}).get("repositories", 1)
    line = "Found %s · %s %s." % (
        _tally(found, ("task",), repositories), "up to" if atmost else "about",
        _counted(units, "unit"))
    left_out = ([] if not skipped else ["%d already in the brain" % skipped]) \
        + ([] if not held else ["%d refused earlier" % held])
    if left_out:
        line = line[:-1] + " (%s %s skipped)." % (
            " and ".join(left_out), "is" if skipped + held == 1 else "are")
    return [line]


def _send_lines(document):
    counts = collections.Counter(
        row["kind"] for row in document["rows"] if row["status"] in TO_SEND)
    return ["Sending %s…" % _tally(counts)] if counts else \
        ["Nothing to send."]


def _name(row):
    title = " ".join(str(row.get("title") or "").split())[:80]
    if not title:
        title = "(untitled %s)" % dict(SUMMARY_KINDS)[row["kind"]]
    if row.get("number") is not None:
        title = "#%s %s" % (row["number"], title)
    return '"%s"' % title


def _reason(row, entry, run, made, differs):
    """The key of the sentence this row needs, or None. A row is said once,
    under the reason that fits first; what an earlier run left held comes
    out as `refused`."""
    if not entry or entry.get("run") != run:
        if row["key"] in differs:
            return "differs_unsettled"
        if row["status"] in TO_SEND and made:
            return "unrecorded"
        return "refused" if row["status"] == "refused" else None
    outcome = entry.get("outcome")
    if outcome not in OUTCOMES:
        return "unknown"
    if outcome == "digest_refused":
        return "digest_refused"
    if outcome == "failed" and entry.get("detail") == RELAY_DIFFERS:
        return "relay_differs"
    if outcome in SUCCESS:
        return "digest_differs" if row["key"] in differs else None
    if row["key"] in differs:
        return "differs_unsettled"
    if outcome == "edge_refused":
        return "edge_refused"
    return outcome if outcome in SAID_AS_OUTCOME else None


def _confirmed(row, made):
    """Whether the card sent this run has a matching check."""
    return any(c.get("match") is True for c in made)


def _classify(row, entry, run, made, differs):
    """Everything the summary says about one row, from one reading of it:
    the group it is counted in, the sentence it needs, whether it arrived
    different, what the checks allow saying and whether it was not sent."""
    this = bool(entry) and entry.get("run") == run
    if this:
        counted = entry.get("outcome")
    elif row["status"] in TO_SEND:
        counted = "not_attempted"
    else:
        counted = "%s_earlier" % row["status"]
    reason = _reason(row, entry, run, made, differs)
    different = reason in DIFFERENCES
    integrity = None
    if (this and entry.get("outcome") in SUCCESS and row["kind"] != "document"
            and not different
            and not _nothing_written(row, entry.get("outcome"), made)):
        integrity = "confirmed" if _confirmed(row, made) else "unchecked"
    return {"group": PLAIN.get(counted, "to redo"), "reason": reason,
            "earlier": not this, "different": different, "integrity": integrity,
            "unsent": not this and row["status"] in TO_SEND and not made}


def _integrity_lines(confirmed, unchecked, different):
    """What the checks of this run allow saying about the tasks and pull
    requests."""
    if confirmed and not unchecked and not different:
        return ["Integrity check: %d/%d ok." % (confirmed, confirmed)]
    if not confirmed and not different:
        return (["Integrity check: what was stored could not be confirmed "
                 "this run."] if unchecked else [])
    said = (["%d confirmed ok" % confirmed] if confirmed else []) \
        + (["%d arrived different from the original (see below)" % different]
           if different else []) \
        + (["%d could not be checked" % unchecked] if unchecked else [])
    return ["Integrity check: %s." % ", ".join(said)]


def _report_rows(document, entries, made, classes):
    run, rows = document["run"], []
    for row, cls in zip(document["rows"], classes):
        entry = entries.get(row["key"]) or {}
        if entry.get("run") != run and row["status"] == "unchanged":
            continue
        this_run = entry if entry.get("run") == run else {}
        rows.append({
            "key": row["key"], "kind": row["kind"], "title": row.get("title"),
            "number": row.get("number"), "url": row.get("url"),
            "status": row["status"], "outcome": this_run.get("outcome"),
            "detail": this_run.get("detail"),
            "resends": this_run.get("resends"),
            "digest_differs": this_run.get("digest_differs"),
            "integrity": cls["integrity"],
            "checks": made.get(row["key"], [])})
    return rows


def _same_file(path, others):
    real = os.path.realpath(path)
    return any(other and os.path.realpath(other) == real for other in others)


def _where(report):
    """The folder the report is in, as a person finds it from the checkout."""
    folder = os.path.dirname(report) or "."
    try:
        near = os.path.relpath(folder)
    except ValueError:
        near = folder
    return folder if near.startswith("..") else near


def summary(args):
    document = _plan(args.plan)
    if args.stage == "estimate":
        return {"text": "\n".join(_estimate_lines(document)), "report": None}
    if args.stage == "send":
        return {"text": "\n".join(_send_lines(document)), "report": None}
    path, ledger, entries = _entries(document, args.ledger)
    report = args.report or os.path.join(os.path.dirname(path) or ".",
                                         "report-%s.json" % document["run"])
    if _same_file(report, (path, args.plan, *(args.collected or []),
                              *(args.extra or []))):
        raise Usage("--report must not be the ledger, the plan or the "
                    "collection report")
    state = _status_of(document, ledger, entries)
    problems, not_collected, extras = [], [], []
    for source in args.extra or []:
        try:
            extras.append({"file": source,
                           "summary": _load(source, "the extra summary")})
        except Unreadable as err:
            problems.append(str(err))
    for source in args.collected or []:
        try:
            found = _collection_report(source)
            named = found.get("repository")
            left = found.get("not_collected") or []
            if not isinstance(left, list):
                raise Unreadable("not_collected must be a list")
        except Unreadable as err:
            problems.append(str(err))
            continue
        not_collected += [dict(entry, repository=named)
                          if isinstance(entry, dict) and named else entry
                          for entry in left]
    made = _checks_by_key(ledger, document)
    differs = {f["key"] for f in state["digest_differs"]}
    classes = [_classify(row, entries.get(row["key"]), document["run"],
                         made.get(row["key"], []), differs)
               for row in document["rows"]]
    groups = collections.Counter(cls["group"] for cls in classes)
    done = ["%d %s" % (groups[g], g) for g in GROUPS if groups[g]]
    lines = ["Done: %s." % ", ".join(done) if done else "Done: nothing to send."]
    integrity = collections.Counter(cls["integrity"] for cls in classes)
    different = sum(cls["different"] for cls in classes)
    lines += _integrity_lines(integrity["confirmed"], integrity["unchecked"],
                              different)
    if state["stopped"]:
        lines.append(STOP_LINES[state["stopped"]["outcome"]])
    denied = state["denied_repositories"]
    if denied:
        lines.append(
            "The brain refused %s, so what belongs to %s was not sent: this "
            "account or key has no write access there (a project admin can "
            "check it). %sRun this again once that is fixed." % (
                _joined(denied), "it" if len(denied) == 1 else "them",
                EVERY_REPOSITORY_DENIED if _key_may_be_foreign(
                    document, entries) else ""))
    # A repository file's card is confirmed on arrival; only a change to a
    # confirmed card (`revision_proposed`) waits, as a linked document does.
    this_run = _this_run(entries, document["run"])
    proposed = sum(
        1 for row in document["rows"]
        if row["kind"] == "document" and not row.get("git")
        and this_run.get(row["key"], {}).get("outcome") in ("written", "revision_proposed"))
    proposed += sum(
        1 for row in document["rows"]
        if row.get("git") and this_run.get(row["key"], {}).get("outcome") == "revision_proposed")
    if proposed:
        lines.append("%s for your confirmation in Studio's Review Queue." % (
            _counted(proposed, "document") + (" waits" if proposed == 1 else " wait")))
    shown = [NOTES[code] for code in SHOWN_NOTES if code in (args.note or [])]
    lines += shown
    # This run's problems come before what earlier runs left held.
    said = [ATTENTION[cls["reason"]] % _name(row)
            for cls, row in sorted(
                ((c, r) for c, r in zip(classes, document["rows"]) if c["reason"]),
                key=lambda pair: pair[0]["earlier"])]
    lines += said[:MAX_ATTENTION]
    if len(said) > MAX_ATTENTION:
        lines.append("…and %d more." % (len(said) - MAX_ATTENTION))
    unsent = sum(cls["unsent"] for cls in classes)
    if unsent and not state["stopped"]:
        lines.append("%s not sent this run; %s next run." % (
            _counted(unsent, "item") + (" was" if unsent == 1 else " were"),
            "it goes" if unsent == 1 else "they go"))
    if not_collected:
        lines.append("%s couldn't be read from GitHub. %s tried again next run." % (
            _counted(len(not_collected), "pull request"),
            "It is" if len(not_collected) == 1 else "They are"))
    try:
        _write(report, {
            "schema": REPORT_SCHEMA, "run": document["run"],
            "project": document["project"], "plan": args.plan, "status": state,
            "integrity": {"confirmed": integrity["confirmed"],
                          "unchecked": integrity["unchecked"],
                          "different": different},
            "rows": _report_rows(document, entries, made, classes),
            "notes": [{"code": code, "text": NOTES[code]}
                      for code in args.note or []],
            "not_collected": not_collected, "extras": extras,
            "problems": problems})
    except WriteFailed:
        report = None
    # A clean run ends with the summary alone; anything else offers the
    # explanation, and says where the details are when they were saved.
    calm = not (said or unsent or state["stopped"] or denied or shown or problems
                or not_collected or integrity["unchecked"] or different)
    if not calm:
        lines.append(("Details are saved in %s/. " % _where(report) if report else "")
                     + "Want me to explain any of these?")
    return {"text": "\n".join(lines), "report": report}


def _parser():
    parser = argparse.ArgumentParser(prog="seed_ledger.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("plan", allow_abbrev=False)
    one.add_argument("--project", required=True)
    one.add_argument("--work", required=True)
    one.add_argument("--ledger")
    one.add_argument("--documents", action="append")
    one.add_argument("--tasks")
    one.add_argument("--collected", action="append")
    one.add_argument("--pull-list", action="append")
    one.add_argument("--repositories")
    one.add_argument("--repository")
    one.add_argument("--git-remote", dest="git_remote")
    one.add_argument("--refs")
    one.add_argument("--all", action="store_true")
    one.add_argument("--out")
    two = sub.add_parser("next", allow_abbrev=False)
    two.add_argument("--plan", required=True)
    two.add_argument("--kind", required=True, choices=KINDS)
    two.add_argument("--ledger")
    three = sub.add_parser("record", allow_abbrev=False)
    three.add_argument("--plan", required=True)
    three.add_argument("--key", required=True)
    three.add_argument("--outcome", required=True)
    three.add_argument("--detail")
    three.add_argument("--budget")
    three.add_argument("--unresolved")
    three.add_argument("--answer-json", action="store_true")
    three.add_argument("--ledger")
    four = sub.add_parser("status", allow_abbrev=False)
    four.add_argument("--plan", required=True)
    four.add_argument("--ledger")
    five = sub.add_parser("verify", allow_abbrev=False)
    five.add_argument("--plan", required=True)
    five.add_argument("--key", required=True)
    five.add_argument("--received")
    five.add_argument("--code")
    five.add_argument("--ledger")
    six = sub.add_parser("summary", allow_abbrev=False)
    six.add_argument("--plan", required=True)
    six.add_argument("--stage", required=True, choices=STAGES)
    six.add_argument("--ledger")
    six.add_argument("--collected", action="append")
    six.add_argument("--note", action="append", choices=sorted(NOTES))
    six.add_argument("--extra", action="append")
    six.add_argument("--report")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    handler = {"plan": plan, "next": next_rows, "record": record,
               "status": status, "verify": verify,
               "summary": summary}[args.command]
    try:
        document = handler(args)
    except Usage as err:
        print("seed_ledger: %s" % err, file=sys.stderr)
        return 2
    except Unreadable as err:
        print("seed_ledger: %s" % err, file=sys.stderr)
        return 3
    except WriteFailed as err:
        print("seed_ledger: %s" % err, file=sys.stderr)
        return 5
    print(json.dumps(document, ensure_ascii=False))
    if args.command == "verify" and document.get("match") is False:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
