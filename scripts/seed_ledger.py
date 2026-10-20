#!/usr/bin/env python3
"""The seeding ledger: what was sent to the brain, what is left, what changed.

A seeding run walks documents, tasks and pull requests through the brain's
write doors. This script owns the bookkeeping so a run can stop (a throttle, a
closed session, a compacted context) and a later run can pick up exactly where
it left off, and so a re-run sends only what changed. It never calls the brain.

    seed_ledger.py plan   --project P --work DIR [--ledger L] [--documents F]
                          [--tasks F] [--collected F] [--pull-list F]
                          [--all] [--out F]
    seed_ledger.py next   --plan F --kind document|task|pull_request
                          [--limit 20] [--ledger L]
    seed_ledger.py record --plan F --key K --outcome O [--detail TEXT]
                          [--budget JSON] [--unresolved JSON]
                          [--parts-written N] [--answer-json] [--ledger L]
    seed_ledger.py status --plan F [--ledger L]
    seed_ledger.py verify --plan F --key K [--part I] [--ledger L]
                          (--received HEX | --code CODE [--received HEX|null])
    seed_ledger.py summary --plan F --stage estimate|send|done [--ledger L]
                          [--collected F] [--note CODE ...] [--report F]

The ledger (default `.fairmind/brain-seed/ledger.json`) is keyed by project and
then by item, `"<kind>:<source>:<id>"`: a document by its canonical link, a task
by its permanent id, a pull request by its node id. Each entry keeps the last
attempt (`fingerprint`, `outcome`, `detail`, `run`, `seq`) and, apart from it,
`sent`: the fingerprint of the last attempt that succeeded. `sent` changes only
on a success, so a failed or throttled attempt never hides what the brain holds.

Fingerprints never hash model-written text, or every run would resend
everything and push revision proposals onto cards a person already confirmed.
A document uses the source's own edit marker (a document with no marker is sent
once and then left alone); a task uses the tracker's updated time and its
pull request links, compared by pull request, not by spelling; a pull request
uses the content of its collected part files, parsed, so whitespace does not
count.

A row is `new` (never sent), `changed` (sent, and the fingerprint differs or
the latest attempt was not a success), `retry` (an earlier attempt did not
succeed and nothing was sent before), `unchanged` (the latest attempt was a
success and the fingerprint is the one sent), or `refused` (the brain refused
this exact content; it is not resent until the content changes, or until a plan
made with `--all`). Only the brain's own refusal holds a row: after any other
latest outcome, an earlier success included, the next plan sends the row
again. `--all` makes everything `new` or `changed`, as for a backfill.

A pull request is sent as several parts. `record --parts-written N` stores how
many leading parts the brain has accepted, for the fingerprint then current,
when the pull request ends in anything but a success; the next plan gives the
row `resume_part` (the index of the first part still to send) and counts only
the parts left as `units`, so a pull request needing more calls than a day's
allowance still gets through, and a part is never sent twice. The progress
applies to that exact content: a changed pull request, or a plan made with
`--all`, starts from the first part. A pull request whose later part the
brain refused stays `refused`, partly imported, until its content changes or
an `--all` plan; one whose part the edge refused resumes at that part.

Outcomes are a closed vocabulary. Successes: written, unchanged,
revision_proposed, not_reproposed. Refusals: refused (by the brain; held),
edge_refused (by the hosting edge in front of it), digest_refused (the door
refused the call for its `expected_sha256`: `verify --code` ended in
`record_digest_refused`, which `record` requires for this outcome). Stops
(nothing more is attempted in this run): throttled, access_denied,
quota_unset. Others: partial, failed.

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

`next` lists the rows of one kind still to send in this run, `remaining` counting
those not yet recorded (the rows returned included); after a stop it lists none
and names the stop. A task row carries `expected_sha256`, and a pull request row
`parts_expected_sha256`, one per entry of `parts` in the same order (`null`
before `resume_part`): the digest `verify` will expect for that payload,
computed from the row's own file at the moment `next` runs. A document row
carries none. A row whose file cannot be read or has no canonical JSON form (for
a pull request, any part from `resume_part` on) carries `no_digest`, the reason,
in place of the digest. `status` reports counts per outcome, the stop, the last
budget the brain reported in this run, the brain's refusals (`refused`, held,
this run's and earlier ones), this run's `edge_refused`, `digest_refused` and
`failed` rows, and the links on recorded tasks that no imported pull request
resolves yet.
From the journal it adds `resends`, the `digest_differs` rows, and how many
tasks and pull requests recorded with a success this run were `verified` (at
least one verification matched) or `unverified` (none did, and none differed
the same way twice).

`verify` checks that the brain received a row's file byte for byte. The task
and pull request doors answer with `received_sha256` (a server that predates
it answers without one): the lowercase hex
SHA-256 of the UTF-8 bytes of the payload they parsed, serialised with sorted
keys, no ASCII escaping and no whitespace (`json.dumps(payload,
sort_keys=True, ensure_ascii=False, separators=(",", ":"))`). The payload is
a task's `item`; for a pull request part, always the three keys
`{"pull_request": ..., "conversations": ..., "conversation_count": ...}`, with
`conversations` `[]` when the part has none or null and `conversation_count`
null when the part has none. The other arguments of a call (`project`,
`repository`, `git_remote`, `agent_accounts`) are not covered. `verify`
computes the same digest from the row's own file (`--part I` is the part's
index in the row's `parts`, counted from 0 as `resume_part` is), appends the
check to the ledger's journal for this run, and prints `{"match", "expected",
"received", "next"}`. `next` is what the session does: `record` (the digests
match), `resend` (this key and part's first difference in this run),
`record_differs` (a later difference with the same digest as the first: not
noise in the relay) or `record_failed` (a later difference with another
digest). The journal, not the session's memory, holds the first digest, so
the decision survives a compacted context. It keeps the current run's checks
only: a check from another run of the same project is dropped when one is
written. A document row is a usage error: its door answers with no digest.

`verify --code` journals a refusal the door gave for its `expected_sha256`
(`payload_digest_mismatch` or `expected_sha256_malformed`), with the
`received_sha256` it carried or none, for that key and part, and prints
`{"code", "received", "next"}`: `resend` for the first such answer of the key
and part in this run, `record_digest_refused` for the second. A `resend` counts
in `resends` whichever kind of check gave it.

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
no file. A clean run ends with the summary alone. `--collected` is read for the pull requests the
collector could not read; `--note` is a closed list of observations the
session made, kept in the report.

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
from _pull_request_url import pull_request_key

LEDGER_SCHEMA = "brain-seed-ledger/1"
PLAN_SCHEMA = "brain-seed-plan/1"
REPORT_SCHEMA = "brain-seed-report/1"
DEFAULT_LEDGER = os.path.join(".fairmind", "brain-seed", "ledger.json")
KINDS = ("document", "task", "pull_request")
SUCCESS = ("written", "unchanged", "revision_proposed", "not_reproposed")
REFUSED = ("refused", "edge_refused", "digest_refused")
# Only the brain's own refusal holds a row until its content changes; the
# edge's never reached the brain, so its row is sent again.
HELD = ("refused",)
STOP = ("throttled", "access_denied", "quota_unset")
OTHER = ("partial", "failed")
OUTCOMES = SUCCESS + REFUSED + STOP + OTHER
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
NO_DIGEST = ("no digest to send: the row's file cannot be read or has no "
             "canonical JSON form")


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
    return ledger


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


def _resume_part(entry, fingerprint, parts, force):
    """The index of the first part still to send: the parts a failed attempt
    at this exact content already got accepted are skipped."""
    progress = (entry or {}).get("parts")
    if (force or not isinstance(progress, dict)
            or progress.get("fingerprint") != fingerprint):
        return 0
    # Anything but a true count in a hand-edited ledger means no part was
    # written.
    written = _count(progress.get("written")) or 0
    return min(written, len(parts) - 1)


def _rows(args, entries):
    rows = []
    if args.documents:
        for doc in _list(args.documents, "the documents"):
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
            if doc.get("local"):
                rows[-1]["local"] = True
    if args.tasks:
        for task in _list(args.tasks, "the tasks"):
            if task.get("id") is None or not task.get("source"):
                raise Unreadable("a task row has no source or id")
            source, tid = str(task["source"]).lower(), str(task["id"])
            links = list(task.get("pull_request_urls") or []) \
                + list(task.get("matched_pull_request_urls") or [])
            rows.append({
                "kind": "task", "source": source, "id": tid,
                "title": task.get("title"),
                "fingerprint": _digest({
                    "updated": task.get("updated"),
                    "pull_request_urls": sorted(
                        {pull_request_key(u) for u in links
                         if pull_request_key(u)})}),
                "units": 1,
                "file": os.path.join(args.work, "tasks",
                                     "%s-%s.json" % (source, _safe(tid)))})
    if args.collected:
        report = _collection_report(args.collected)
        for pull in report.get("pull_requests") or []:
            parts = list(pull.get("files") or [])
            fingerprint = _digest([_load(p, "the part file") for p in parts])
            key = "pull_request:github:%s" % pull["id"]
            resume = _resume_part(entries.get(key), fingerprint, parts,
                                  args.all)
            rows.append({
                "kind": "pull_request", "source": "github", "id": pull["id"],
                "title": pull.get("title"), "number": pull.get("number"),
                "url": pull.get("url"), "parts": parts,
                "fingerprint": fingerprint, "resume_part": resume,
                "units": max(len(parts) - resume, 1)})
    for row in rows:
        row["key"] = "%s:%s:%s" % (row["kind"], row["source"], row["id"])
        row["status"] = _status(entries.get(row["key"]), row["fingerprint"],
                                args.all)
    return rows


def _collection_report(path):
    report = _load(path, "the collection report")
    if not isinstance(report, dict):
        raise Unreadable("the collection report must be an object")
    return report


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
    ledger_path = args.ledger or DEFAULT_LEDGER
    ledger = _read_ledger(ledger_path)
    entries = ledger["projects"].get(args.project) or {}
    rows = _rows(args, entries)
    summary = _summary(rows)
    if args.pull_list and not args.collected:
        pulls = _list(args.pull_list, "the pull request list")
        summary["pull_request"] = {"in_window": len(pulls),
                                   "units_at_least": len(pulls)}
    # A run id is a random identifier only: a ledger row records what a
    # person approved to send, so no wall-clock value is written onto it.
    run = uuid.uuid4().hex
    document = {"schema": PLAN_SCHEMA, "run": run, "project": args.project,
                "ledger": ledger_path, "work": args.work, "rows": rows,
                "summary": summary}
    out = args.out or os.path.join(args.work, "plan.json")
    _write(out, document)
    return {"run": run, "out": out, "summary": summary}


def _plan(path):
    document = _load(path, "the plan")
    if not isinstance(document, dict) or document.get("schema") != PLAN_SCHEMA:
        raise Unreadable("%s is not a seeding plan" % path)
    return document


def _row(document, key):
    row = next((r for r in document["rows"] if r["key"] == key), None)
    if row is None:
        raise Usage("%s is not in the plan" % key)
    return row


def _entries(document, args):
    path = args.ledger or document["ledger"]
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


def next_rows(args):
    document = _plan(args.plan)
    _, _, entries = _entries(document, args)
    stopped = _stopped(document, entries)
    todo = [r for r in document["rows"]
            if r["kind"] == args.kind and r["status"] in TO_SEND
            and (entries.get(r["key"]) or {}).get("run") != document["run"]]
    shown = [] if stopped else [_with_digests(r) for r in todo[:args.limit]]
    return {"rows": shown, "remaining": len(todo), "stopped": stopped}


def _with_digests(row):
    """The row with the digest the door will echo, computed as `verify`
    computes it, so the two cannot disagree. A pull request row gets the digest
    of each part from `resume_part` on, with `None` before it so that the
    indices align with `parts`; the parts already accepted are not read. A row
    whose file cannot be read, or has no canonical JSON form (a lone
    surrogate), gets no digest and carries `no_digest`, the reason; for a pull
    request that is any part still to send."""
    try:
        if row["kind"] == "task":
            return dict(row, expected_sha256=_digest(_relayed(row, None)))
        if row["kind"] == "pull_request":
            start = row.get("resume_part", 0)
            return dict(row, parts_expected_sha256=[
                None if i < start else _digest(_relayed(row, i))
                for i in range(len(row["parts"]))])
    except (Unreadable, ValueError, TypeError, RecursionError):
        return dict(row, no_digest=NO_DIGEST)
    return row


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
    unresolved = _links(unresolved)
    written = args.parts_written
    if written is not None and (row["kind"] != "pull_request"
                                or not 0 <= written <= len(row["parts"])):
        raise Usage("--parts-written takes 0 to the number of parts of a "
                    "pull request")
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    path, ledger, entries = _entries(document, args)
    # What this run's verifications of the row decided is read from the
    # journal, never from the session.
    checks = _checks(ledger, document, row["key"])
    verdicts = [c.get("next") for c in checks]
    last = verdicts[-1] if verdicts else None
    if last == "record_failed" and args.outcome in SUCCESS:
        raise Usage("the last verification of %s found the brain holding a "
                    "payload different from the file: record it failed"
                    % row["key"])
    if args.outcome == "digest_refused" and last != "record_digest_refused":
        raise Usage("the last verification of %s did not end in "
                    "record_digest_refused: verify the refusal's code first"
                    % row["key"])
    if (args.outcome == "failed" and detail is None
            and last in ("resend", "record_failed")
            and not checks[-1].get("code")):
        detail = RELAY_DIFFERS
    ledger["seq"] += 1
    old = entries.get(row["key"]) or {}
    entry = {
        "fingerprint": row["fingerprint"], "outcome": args.outcome,
        "detail": detail, "run": document["run"],
        "seq": ledger["seq"],
        "sent": old.get("sent"),
        "url": row.get("url") or old.get("url"),
        "unresolved_links": unresolved if unresolved is not None
        else old.get("unresolved_links", []),
        "budget": budget,
    }
    if verdicts.count("resend"):
        entry["resends"] = verdicts.count("resend")
    if "record_differs" in verdicts:
        entry["digest_differs"] = DIGEST_DIFFERS
    if args.outcome in SUCCESS:
        entry["sent"] = {"fingerprint": row["fingerprint"],
                         "run": document["run"]}
    elif written is not None:
        entry["parts"] = {"fingerprint": row["fingerprint"],
                          "written": written}
    elif (old.get("parts") or {}).get("fingerprint") == row["fingerprint"]:
        entry["parts"] = old["parts"]
    entries[row["key"]] = entry
    ledger["projects"][document["project"]] = entries
    _write(path, ledger)
    return {"key": row["key"], "outcome": args.outcome,
            "stop": args.outcome in STOP}


def status(args):
    document = _plan(args.plan)
    _, ledger, entries = _entries(document, args)
    return _status_of(document, ledger, entries)


def _checks_by_key(ledger, document):
    """This run's verifications, grouped by the row they belong to."""
    made = {}
    for check in _checks(ledger, document):
        made.setdefault(check.get("key"), []).append(check)
    return made


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
                    and row["key"] not in differs):
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
    this_run = {k: e for k, e in entries.items() if e.get("run") == run}
    return {"run": run, "by_kind": by_kind, "stopped": _stopped(document, entries),
            "budget": budget, "refused": refused, **listed,
            # `outcome` is null while a row that differed is not recorded yet.
            "digest_differs": [{"key": key,
                                "outcome": this_run.get(key, {}).get("outcome"),
                                "detail": DIGEST_DIFFERS} for key in differs],
            "resends": sum(1 for made in checks.values() for c in made
                           if c.get("next") == "resend"),
            "verified": verified, "unverified": unverified,
            "open_links": open_links}


def _target(row, part):
    """Refuse a row or part that has no digest to check."""
    if row["kind"] == "task":
        if part is not None:
            raise Usage("--part applies to a pull request, not to %s" % row["key"])
        return
    if row["kind"] != "pull_request":
        raise Usage("verify applies to a task or a pull request part: the "
                    "document door answers with no digest")
    parts = row.get("parts") or []
    if part is None:
        raise Usage("--part is required for the pull request %s" % row["key"])
    if not 0 <= part < len(parts):
        raise Usage("--part takes 0 to %d for %s" % (len(parts) - 1, row["key"]))


def _relayed(row, part):
    """The payload the door digests for this row, read from the row's own
    file: a task's item as it is, or a pull request part's `pull_request`,
    `conversations` (`[]` when the part has none or null) and
    `conversation_count` (null when the part has none), always all three."""
    _target(row, part)
    if row["kind"] == "task":
        return _load(row["file"], "the task file")
    document = _load(row["parts"][part], "the part file")
    if not isinstance(document, dict) or "pull_request" not in document:
        raise Unreadable("the part file %s has no pull_request" % row["parts"][part])
    conversations = document.get("conversations")
    return {"pull_request": document["pull_request"],
            "conversations": [] if conversations is None else conversations,
            "conversation_count": document.get("conversation_count")}


def _next(match, received, earlier, code=None):
    """What the session does after this check, from this run's earlier
    checks of the same key and part. An answer with a refusal code is a
    re-send the first time and ends the part the second."""
    if code:
        coded = [c for c in earlier if c.get("code")]
        return "record_digest_refused" if coded else "resend"
    if match:
        return "record"
    first = next((c for c in earlier if c.get("match") is False), None)
    if first is None:
        return "resend"
    return "record_differs" if first.get("received") == received else "record_failed"


def _received(args):
    """The digest of the answer as a lowercase hex string, or None for a
    refusal that carried none (only with --code)."""
    text = (args.received or "").strip().lower()
    if args.code and text in ("", "null"):
        return None
    if not SHA256_HEX.fullmatch(text):
        raise Usage("--received takes the 64 hexadecimal characters of the "
                    "answer's received_sha256")
    return text


def verify(args):
    if args.code is not None and args.code not in CODES:
        raise Usage("--code takes one of %s" % ", ".join(CODES))
    if args.code is None and args.received is None:
        raise Usage("pass --received, or --code for a refusal that carries one")
    received = _received(args)
    document = _plan(args.plan)
    row = _row(document, args.key)
    path, ledger, _ = _entries(document, args)
    earlier = [c for c in _checks(ledger, document, row["key"])
               if c.get("part") == args.part]
    check = {"run": document["run"], "key": row["key"], "part": args.part,
             "received": received}
    if args.code:
        _target(row, args.part)
        verdict = _next(None, received, earlier, args.code)
        result = {"code": args.code, "received": received, "next": verdict}
        check.update(code=args.code, match=None)
    else:
        expected = _digest(_relayed(row, args.part))
        verdict = _next(expected == received, received, earlier)
        result = {"match": expected == received, "expected": expected,
                  "received": received, "next": verdict}
        check["match"] = expected == received
    ledger["seq"] += 1
    check.update(next=verdict, seq=ledger["seq"])
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
                 "and it continues from here, or ask your FairMind contact to "
                 "open a setup window.",
    "quota_unset": "Your workspace has no daily write allowance yet. Ask your "
                   "FairMind admin to set one, then run this again.",
    "access_denied": "This session can't write to this project's brain: either "
                     "your account lacks the editor role here (ask a project "
                     "admin), or this folder's FairMind key belongs to another "
                     "project (run /fairmind-connect with the right project). "
                     "Then run this again."}
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
    "digest_refused_part": "%s was only partly stored: part of it arrived "
                           "different from the original, so the rest was not "
                           "written. The rest is sent again next run.",
    "relay_differs": "%s arrived different from the original and could not be "
                     "corrected. The brain may hold the altered copy until "
                     "the next run sends it again.",
    "digest_differs": "%s was stored, but what arrived differed from the "
                      "original the same way twice. Worth a look.",
    "edge_refused": "%s was blocked by a filter in front of FairMind before it "
                    "arrived. Nothing was stored; it is sent again next run.",
    "edge_refused_part": "%s was only partly stored: the rest was blocked by a "
                         "filter in front of FairMind before it arrived. The "
                         "rest is sent again next run.",
    "refused": "%s was not accepted by the brain. It is not sent again until "
               "it changes.",
    "failed": "%s didn't go through. It is sent again next run.",
    "differs_unsettled": "%s arrived different from the original; the brain "
                         "may hold the altered copy until the next run sends "
                         "it again.",
    "unrecorded": "%s was sent but its answer wasn't recorded. It is sent "
                  "again next run.",
    "partial": "%s was only partly sent; the rest goes next run.",
    "unknown": "%s ended in a state this version doesn't recognise. It is "
               "sent again next run."}
MAX_ATTENTION = 5
# The sentences that say a row arrived different from the original; the
# integrity line counts exactly the rows that get one.
DIFFERENCES = ("digest_refused", "digest_refused_part", "relay_differs",
               "digest_differs", "differs_unsettled")


def _counted(count, noun):
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def _joined(items):
    return items[0] if len(items) < 2 else \
        "%s and %s" % (", ".join(items[:-1]), items[-1])


def _tally(counts, up_to=()):
    """`2 tasks, 1 pull request and 3 documents`, the kinds with none left
    out; a kind in `up_to` is said as an upper bound."""
    return _joined([("up to " if kind in up_to else "") + _counted(counts[kind], noun)
                    for kind, noun in SUMMARY_KINDS if counts.get(kind)])


def _estimate_lines(document):
    found, units, skipped, held, atleast = {}, 0, 0, 0, False
    for kind, counts in document["summary"].items():
        if "in_window" in counts:
            found[kind] = counts["in_window"]
            units += counts["units_at_least"]
            atleast = True
            continue
        found[kind] = sum(counts[s] for s in TO_SEND)
        units += counts["units"]
        skipped += counts["unchanged"]
        held += counts["refused"]
    if not any(found.values()):
        return ["Nothing new to send."]
    # The estimate cannot see a task's pull request links yet, so it counts
    # a task that a re-run would find unchanged.
    line = "Found %s · %s %s." % (
        _tally(found, ("task",)), "at least" if atleast else "about",
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
    partly = _count((entry.get("parts") or {}).get("written")) or 0
    if outcome not in OUTCOMES:
        return "unknown"
    if outcome == "digest_refused":
        return "digest_refused_part" if partly else "digest_refused"
    if outcome == "failed" and entry.get("detail") == RELAY_DIFFERS:
        return "relay_differs"
    if outcome in SUCCESS:
        return "digest_differs" if row["key"] in differs else None
    if row["key"] in differs:
        return "differs_unsettled"
    if outcome == "edge_refused":
        return "edge_refused_part" if partly else "edge_refused"
    return outcome if outcome in ("refused", "failed", "partial") else None


def _confirmed(row, made):
    """Whether every part of the row sent this run has a matching check, so
    one matching part of three confirms nothing."""
    parts = [None] if row["kind"] == "task" else list(
        range(row.get("resume_part", 0), len(row.get("parts") or [])))
    return bool(parts) and all(
        any(c.get("part") == part and c.get("match") is True for c in made)
        for part in parts)


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
            and not different):
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
            "parts": entry.get("parts"),
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
    path, ledger, entries = _entries(document, args)
    report = args.report or os.path.join(os.path.dirname(path) or ".",
                                         "report-%s.json" % document["run"])
    if _same_file(report, (path, args.plan, args.collected)):
        raise Usage("--report must not be the ledger, the plan or the "
                    "collection report")
    state = _status_of(document, ledger, entries)
    problems, not_collected = [], []
    if args.collected:
        try:
            not_collected = _collection_report(args.collected).get("not_collected") or []
            if not isinstance(not_collected, list):
                raise Unreadable("not_collected must be a list")
        except Unreadable as err:
            problems.append(str(err))
            not_collected = []
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
    documents = state["by_kind"].get("document", {})
    proposed = documents.get("written", 0) + documents.get("revision_proposed", 0)
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
            # Every part must match here, where `status` counts a row as
            # verified when any part did.
            "integrity": {"confirmed": integrity["confirmed"],
                          "unchecked": integrity["unchecked"],
                          "different": different},
            "rows": _report_rows(document, entries, made, classes),
            "notes": [{"code": code, "text": NOTES[code]}
                      for code in args.note or []],
            "not_collected": not_collected, "problems": problems})
    except WriteFailed:
        report = None
    # A clean run ends with the summary alone; anything else offers the
    # explanation, and says where the details are when they were saved.
    calm = not (said or unsent or state["stopped"] or shown or problems
                or not_collected or integrity["unchecked"] or different)
    if not calm:
        lines.append(("Details are saved in %s/. " % _where(report) if report else "")
                     + "Want me to explain any of these?")
    return {"text": "\n".join(lines), "report": report}


def _parser():
    parser = argparse.ArgumentParser(prog="seed_ledger.py")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("plan")
    one.add_argument("--project", required=True)
    one.add_argument("--work", required=True)
    one.add_argument("--ledger")
    one.add_argument("--documents")
    one.add_argument("--tasks")
    one.add_argument("--collected")
    one.add_argument("--pull-list")
    one.add_argument("--all", action="store_true")
    one.add_argument("--out")
    two = sub.add_parser("next")
    two.add_argument("--plan", required=True)
    two.add_argument("--kind", required=True, choices=KINDS)
    two.add_argument("--limit", type=int, default=20)
    two.add_argument("--ledger")
    three = sub.add_parser("record")
    three.add_argument("--plan", required=True)
    three.add_argument("--key", required=True)
    three.add_argument("--outcome", required=True)
    three.add_argument("--detail")
    three.add_argument("--budget")
    three.add_argument("--unresolved")
    three.add_argument("--parts-written", type=int)
    three.add_argument("--answer-json", action="store_true")
    three.add_argument("--ledger")
    four = sub.add_parser("status")
    four.add_argument("--plan", required=True)
    four.add_argument("--ledger")
    five = sub.add_parser("verify")
    five.add_argument("--plan", required=True)
    five.add_argument("--key", required=True)
    five.add_argument("--part", type=int)
    five.add_argument("--received")
    five.add_argument("--code")
    five.add_argument("--ledger")
    six = sub.add_parser("summary")
    six.add_argument("--plan", required=True)
    six.add_argument("--stage", required=True, choices=STAGES)
    six.add_argument("--ledger")
    six.add_argument("--collected")
    six.add_argument("--note", action="append", choices=sorted(NOTES))
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
        if args.command == "next" and args.limit < 1:
            raise Usage("--limit must be at least 1")
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
