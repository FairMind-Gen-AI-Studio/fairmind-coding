#!/usr/bin/env python3
"""List, verify and freeze the ticket references the agent found in pull requests.

    ticket_refs.py pending --project P --tracker T --collected COLLECTED.json
                           [--collected COLLECTED.json ...] [--store STORE.json]
                           --out PENDING.json [--batch N]
    ticket_refs.py verify  --project P --tracker T --collected COLLECTED.json
                           [--collected COLLECTED.json ...] --proposals DIR
                           --tasks INVENTORY.json [--store STORE.json]
                           --out-dir DIR
    ticket_refs.py freeze  --project P --tracker T --verified VERIFIED.json
                           [--cited CITED.json] --repositories REPOSITORIES.json
                           [--store STORE.json] --out PR_REFS.json
                           [--summary-out SUMMARY.json]

A sub-agent reads a pull request and proposes the tickets it names; this script
decides what of that proposal stands. It never calls a network, `gh` or a model.
`--project` is required wherever the store is touched. `--tracker` names the
run's tracker (see `_tracker`), and the three commands that take it treat an
entry frozen for another tracker, or for none, as not frozen: what it stored is
neither reused, nor carried, nor looked up.

Files

* The store (`--store`, default `.fairmind/brain-seed/references.json`, schema
  `brain-seed-references/1`) holds, per project and per pull request node id,
  `{repository, number, url, reading, tracker, tickets}`. `reading` is the
  sha256 of the bytes of the pull request's `read.md`; `tracker` is the run's
  tracker the entry was frozen for; `tickets` is `[{source, id, key?}]`;
  `unresolved` (absent when empty) is `[{source, id}]`, the references that
  passed verification but that neither the inventory nor the tracker held when
  the entry was frozen. A missing store is an empty one; any other schema exits
  3.
* `pending --out` is `{"batches": [[{repository, number, node_id, url, read,
  proposal}, ...], ...], "pending": N, "frozen": M}`. `proposal` is where the
  proposer writes its answer, `<dir of --out>/<owner>__<name>/<number>.json`: a
  JSON list of `{"tracker", "id", "span"}`, `[]` when the pull request names no
  ticket. `read` and `proposal` are both relative to the working directory
  when they lie inside it, else absolute. Its summary adds `skipped_reports`
  (`{path, repository, reason}`).
* `verify` writes into `--out-dir` `verified.json` (`{schema, project, dropped,
  pull_requests: [{repository, number, node_id, url, reading, stored_reading,
  status, tickets, unresolved}]}`; `status` is `fresh`, `frozen` or `stale`;
  `stored_reading` is the reading the store held when verify ran, null when
  none) and `lookup.json` (`{"trackers": {<tracker>: [<id>, ...]}}`, sorted; the
  ids no inventory row holds, which a reader looks up in the tracker and writes
  as `cited-tasks.json` in the inventory's row format).
* `freeze --out` is `{schema, project, pull_requests: [{repository, number,
  node_id, url, tickets}]}`; `--cited` takes the inventory's row format. The
  same inputs give a byte-identical file.
* `pr-refs.json` is read by `read_pr_refs`, which `reference_card.py`,
  `seed_ledger.py` and `ticket_links.py` use, and a pull request's tickets by
  `tickets_of`. A pull request with an entry is one whose references are
  known, even when it cites nothing; one with no entry is not.

Exit codes: 0 ok; 2 usage or a missing input file; 3 an input that is not
readable JSON or of the wrong shape, or any entry in the summary's `errors`
(everything else is still finished and written); 5 a file cannot be written.
stdout is one JSON document; diagnostics go to stderr. stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json
from _ticket_identity import id_key
from login_key import source_of
from pr_collect import (ReportMissing, ReportUnreadable, read_collection_report,
                        repeated_repository, skipped_report)

STORE_SCHEMA = "brain-seed-references/1"
VERIFIED_SCHEMA = "brain-seed-verified/1"
PR_REFS_SCHEMA = "brain-seed-pr-refs/1"
DEFAULT_STORE = os.path.join(".fairmind", "brain-seed", "references.json")
DEFAULT_BATCH = 20
MAX_TICKETS = 50
REASONS = ("span_not_in_text", "span_multiline", "id_not_in_span",
           "span_is_bare_id", "not_in_tracker")
STATUSES = ("fresh", "frozen", "stale")


class Missing(Exception):
    pass


class Unreadable(Exception):
    pass


class Usage(Exception):
    pass


class RefsUnreadable(ValueError):
    """A `pr-refs.json` that is not references of the known schema and project."""


class RefsRefused(ValueError):
    """A pull request's tickets that a card cannot carry."""


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
        _atomic_write_json(path, document, prefix=".fm-refs-", mode=0o644,
                           indent=2, sort_keys=True, trailing_newline=True)
    except OSError as err:
        raise OSError("cannot write %s: %s"
                      % (path, err.strerror or err.__class__.__name__))


def _text(value):
    return value if isinstance(value, str) else ""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _scalar(value):
    """A tracker id or key as text: a non-empty string or an int; None for
    anything else (a bool, a float, a missing value)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _ticket(row):
    """The ticket a row of the inventory is: its permanent identity. The
    source is case-folded so one tracker spelled two ways is one ticket."""
    out = {"source": str(row.get("source") or "").strip().casefold(),
           "id": _scalar(row.get("id"))}
    key = _scalar(row.get("key"))
    if key is not None:
        out["key"] = key
    return out


class Inventory:
    """The rows of one tracker, found by id or by key. An id is compared as
    `_ticket_identity.id_key` spells it; an exact `id` wins over a `key`, and
    the first row in inventory order wins among equals."""

    def __init__(self, rows, tracker):
        self._tracker = tracker
        self._by_id, self._by_key = {}, {}
        for row in rows:
            if str(row.get("source") or "").strip().casefold() != tracker:
                continue
            for index, field in ((self._by_id, "id"), (self._by_key, "key")):
                found = _scalar(row.get(field))
                if found is not None:
                    index.setdefault(id_key(tracker, found), row)

    def resolve(self, ident):
        key = id_key(self._tracker, ident)
        row = self._by_id.get(key)
        return row if row is not None else self._by_key.get(key)


def _rows(document, what):
    """The usable rows of an inventory: objects with an id."""
    if not isinstance(document, list):
        raise Unreadable("%s must hold a JSON list" % what)
    return [r for r in document
            if isinstance(r, dict) and _scalar(r.get("id")) is not None]


def _tickets_ok(value):
    return isinstance(value, list) and all(
        isinstance(t, dict) and isinstance(t.get("source"), str)
        and isinstance(t.get("id"), str)
        and isinstance(t.get("key", ""), str) for t in value)


def _entries(store, project):
    """The stored entries of `project`, `{node id: entry}`, shape-checked."""
    entries = store["projects"].get(project, {})
    if not isinstance(entries, dict):
        raise Unreadable("the store holds no object for the project %s" % project)
    for node, entry in entries.items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("reading"), str)
                or not _tickets_ok(entry.get("tickets"))
                or not isinstance(entry.get("tracker", ""), str)
                or not _tickets_ok(entry.get("unresolved", []))):
            raise Unreadable("the store entry %s of %s is malformed"
                             % (node, project))
    return entries


def read_store(path):
    """The store at `path`; an empty one when the file does not exist."""
    try:
        store = _load(path)
    except Missing:
        return {"schema": STORE_SCHEMA, "projects": {}}
    if (not isinstance(store, dict) or store.get("schema") != STORE_SCHEMA
            or not isinstance(store.get("projects"), dict)):
        raise Unreadable("%s is not a %s store" % (path, STORE_SCHEMA))
    return store


def read_pr_refs(path, project=None):
    """The parsed `pr-refs.json` at `path` (schema `PR_REFS_SCHEMA`; a
    `pull_requests` that is present is a list), of `project` when one is
    given. Raises FileNotFoundError when the file does not exist,
    RefsUnreadable for any other file that is not such a document."""
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        raise FileNotFoundError("%s does not exist" % path)
    except (OSError, ValueError):
        raise RefsUnreadable("cannot read the references %s as JSON" % path)
    if not isinstance(document, dict) or document.get("schema") != PR_REFS_SCHEMA \
            or not isinstance(document.get("pull_requests", []), list):
        raise RefsUnreadable("the references %s are not of schema %s"
                             % (path, PR_REFS_SCHEMA))
    if project and document.get("project") != project:
        raise RefsUnreadable("the references %s belong to another project "
                             "than --project" % path)
    return document


def tickets_of(refs, node_id, repository, number):
    """The tickets pull request `node_id` cites in parsed references, as its
    card carries them: `[{source, id}]`, the source lower case, de-duplicated
    and sorted. `None` when the references hold no entry for it (the references
    know nothing about it: its card says nothing of tickets), `[]` when they
    hold an entry that cites none (the references know it cites nothing: its
    card clears any link the brain holds). An entry whose repository
    (case-insensitive) or number is not the pull request's is ignored, and
    said so on stderr. Raises RefsRefused for a ticket with a source that is
    empty or holds a colon or whitespace, for a ticket with no id and for more
    than MAX_TICKETS tickets (refused, never cut)."""
    own = [e for e in refs.get("pull_requests") or []
           if isinstance(e, dict) and e.get("node_id") == node_id]
    agreeing = [e for e in own if isinstance(e.get("repository"), str)
                and e["repository"].casefold() == str(repository).casefold()
                and e.get("number") == number]
    if len(agreeing) != len(own):
        print("ticket_refs: a references entry for %s names another repository "
              "or number than the card; it is ignored" % node_id, file=sys.stderr)
    if not agreeing:
        return None
    found = set()
    for entry in agreeing:
        for ticket in entry.get("tickets") or []:
            source = ticket.get("source") if isinstance(ticket, dict) else None
            ident = ticket.get("id") if isinstance(ticket, dict) else None
            if isinstance(ident, int) and not isinstance(ident, bool):
                ident = str(ident)
            try:
                if not isinstance(source, str):
                    raise ValueError(source)
                source = source_of(source)
            except ValueError:
                raise RefsRefused("a ticket in the references has a source "
                                  "that is empty or holds a colon or whitespace")
            if not isinstance(ident, str) or not ident.strip():
                raise RefsRefused("a ticket in the references has no id")
            found.add((source, ident))
    if len(found) > MAX_TICKETS:
        raise RefsRefused("the pull request cites %d tickets, over the limit "
                          "of %d (it is refused, never cut)"
                          % (len(found), MAX_TICKETS))
    return [{"source": s, "id": i} for s, i in sorted(found)]


def _from_checkout(path):
    """`path` relative to the working directory (the checkout root, from where
    the skill runs everything) when it lies inside it, else absolute."""
    absolute = os.path.abspath(path)
    try:
        relative = os.path.relpath(absolute)
    except ValueError:
        return absolute
    return absolute if relative.split(os.sep)[0] == os.pardir else relative


def _proposal_path(directory, repository, number):
    return os.path.join(directory, repository.replace("/", "__"),
                        "%s.json" % number)


def _run(collected_paths, errors):
    """(pull requests, skipped reports) of the complete reports, in report
    order and then number. Each pull request carries its reading digest and
    text; one whose `read.md` cannot be read is reported in `errors` and
    carries `reading` None, so it is never frozen."""
    named, skipped, pulls = set(), [], []
    for path in collected_paths:
        try:
            report = read_collection_report(path)
        except ReportMissing as err:
            raise Missing(str(err))
        except ReportUnreadable as err:
            raise Unreadable(str(err))
        again = repeated_repository(report, named)
        if again:
            raise Usage("two --collected reports name the repository %s" % again)
        if report.get("complete") is not True:
            skipped.append(skipped_report(path, report))
            continue
        repository = report.get("repository")
        entries = report.get("pull_requests")
        if not isinstance(repository, str) or "/" not in repository \
                or not isinstance(entries, list):
            errors.append({"file": path,
                           "reason": "the report names no OWNER/NAME repository"})
            continue
        found = []
        for entry in entries:
            if not (isinstance(entry, dict) and isinstance(entry.get("number"), int)
                    and isinstance(entry.get("id"), str) and entry["id"]
                    and isinstance(entry.get("read"), str) and entry["read"]):
                errors.append({"file": path, "reason":
                               "a pull request entry has no number, id or read"})
                continue
            pull = {"repository": repository, "number": entry["number"],
                    "node_id": entry["id"], "url": _text(entry.get("url")),
                    "read": entry["read"], "reading": None, "text": None}
            try:
                with open(entry["read"], "rb") as handle:
                    data = handle.read()
                pull["text"] = data.decode("utf-8")
                pull["reading"] = _digest(data)
            except (OSError, UnicodeError):
                errors.append({"file": entry["read"],
                               "reason": "cannot read %s" % entry["read"]})
            found.append(pull)
        found.sort(key=lambda p: p["number"])
        pulls.extend(found)
    return pulls, skipped


def _of_tracker(entry, tracker):
    """The stored entry when it was frozen for `tracker`, else None."""
    return entry if entry and entry.get("tracker") == tracker else None


def _is_frozen(pull, stored):
    """Whether `stored`, the entry frozen for the run's tracker (or None), holds
    the reading the pull request has now. A frozen pull request has its tickets
    reused and no proposal is asked for; an empty ticket list is frozen like any
    other."""
    return bool(stored) and pull["reading"] is not None \
        and stored["reading"] == pull["reading"]


def _split(pulls, entries, tracker):
    """(frozen, pending) of the pull requests."""
    frozen, pending = [], []
    for pull in pulls:
        stored = _of_tracker(entries.get(pull["node_id"]), tracker)
        (frozen if _is_frozen(pull, stored) else pending).append(pull)
    return frozen, pending


def _project(args):
    if not args.project or not args.project.strip():
        raise Usage("--project must name the project")
    return args.project


def _tracker(args):
    """The run's tracker, lower case, with no colon and no whitespace (else
    Usage, exit 2). It is the source of every ticket the run freezes: `freeze`
    sets the `source` of every ticket and unresolved reference it writes to it,
    and reads the cited rows as that tracker's, so a name a reader spelled with
    a space or in another case never reaches a card."""
    try:
        return source_of(args.tracker or "")
    except ValueError:
        raise Usage("--tracker must name the run's tracker with no ':' and "
                    "no whitespace")


def pending(args):
    """List the pull requests that need a proposal. Only a report whose
    `complete` is true is read; any other is left out and listed in
    `skipped_reports`, which is not an error. Two reports naming the same
    repository (case-insensitive) are a usage error. The pending pull requests
    come in the order of the reports given, then pull request number, cut into
    batches of `--batch` (default 20)."""
    project = _project(args)
    tracker = _tracker(args)
    if args.batch < 1:
        raise Usage("--batch must be at least 1")
    store = read_store(args.store)
    entries = _entries(store, project)
    errors = []
    pulls, skipped = _run(args.collected, errors)
    frozen, todo = _split(pulls, entries, tracker)
    directory = os.path.dirname(_from_checkout(args.out))
    items = [{"repository": p["repository"], "number": p["number"],
              "node_id": p["node_id"], "url": p["url"], "read": _from_checkout(p["read"]),
              "proposal": _proposal_path(directory, p["repository"], p["number"])}
             for p in todo if p["reading"] is not None]
    batches = [items[i:i + args.batch] for i in range(0, len(items), args.batch)]
    _write_json(args.out, {"batches": batches, "pending": len(items),
                           "frozen": len(frozen)})
    summary = {"out": args.out, "pending": len(items), "frozen": len(frozen),
               "batches": len(batches)}
    if skipped:
        summary["skipped_reports"] = skipped
    if errors:
        summary["errors"] = errors
    return summary


def _proposals(path):
    """The proposals at `path`, trimmed, or the reason the file is unusable."""
    try:
        document = _load(path)
    except Missing:
        return None, "no proposal file at %s" % path
    except Unreadable as err:
        return None, str(err)
    clean = []
    if isinstance(document, list):
        for item in document:
            if not (isinstance(item, dict) and all(
                    isinstance(item.get(f), str) and item[f].strip()
                    for f in ("tracker", "id", "span"))):
                clean = None
                break
            clean.append({f: item[f].strip() for f in ("tracker", "id", "span")})
    if not isinstance(document, list) or clean is None:
        return None, ("%s is not a JSON list of {tracker, id, span} with "
                      "non-empty strings" % path)
    return clean, None


def _sorted_tickets(tickets):
    """`tickets` once per (source, id as `id_key` spells it), sorted by
    `(source, id)`."""
    seen, out = set(), []
    for ticket in sorted(tickets, key=lambda t: (t["source"], t["id"])):
        key = (ticket["source"], id_key(ticket["source"], ticket["id"]))
        if key not in seen:
            seen.add(key)
            out.append(ticket)
    return out


def _token_pattern(ident):
    """`ident` as a whole token, without regard to case: no letter or digit
    touches it on either side, so `ENG-4` is not in `ENG-42`."""
    return re.compile(r"(?<![0-9A-Za-z])" + re.escape(ident)
                      + r"(?![0-9A-Za-z])", re.I)


def _touches(char):
    return char.isascii() and char.isalnum()


def _whole_id_in_span(text, span, ident):
    """Whether, at some occurrence of `span` in `text`, `ident` occurs inside
    it as a whole token, without regard to case: no letter or digit touches it
    on either side, the characters just outside the span included."""
    inside = re.compile("(?=" + re.escape(ident) + ")", re.I)
    start = text.find(span)
    while start != -1:
        for hit in inside.finditer(span):
            first = start + hit.start()
            last = first + len(ident)
            if (first == 0 or not _touches(text[first - 1])) \
                    and (last >= len(text) or not _touches(text[last])):
                return True
        start = text.find(span, start + 1)
    return False


def verify(args):
    """Decide what of the agent's proposals stands.

    A proposed reference (`tracker`, `id`, `span`, each trimmed; empty after
    trimming is malformed) is kept only when its `span` has no line break (else
    dropped, counted `span_multiline`), occurs verbatim in that pull request's
    `read.md` (else `span_not_in_text`) and holds its `id` as a whole token
    (else `id_not_in_span`; see `_whole_id_in_span`). A span that is nothing but
    the id, when the id is all digits, is dropped (`span_is_bare_id`): a bare
    number is no evidence of a ticket, while `#123` or `AB#123` stand. The
    proposal's own `tracker` label is not read: every kept reference is
    resolved against the inventory under the run's tracker, and one no row
    resolves is *unresolved*. A row with no usable id (a string or an integer)
    is ignored.

    A frozen pull request keeps every stored ticket, whatever the inventory
    holds: a ticket the inventory resolves takes the inventory's form, any
    other stays as stored and its id goes to `lookup.json`. Its stored
    `unresolved` references are asked for again on every run, with no model
    call: one the inventory now resolves becomes a ticket, any other stays in
    `lookup.json` and is carried in `unresolved`.

    A pull request whose reading changed but whose proposal file is missing, is
    not a JSON list of objects, or whose `read.md` cannot be read is listed in
    `errors`; with a stored entry it is carried as *stale* with its stored
    tickets and is never frozen again (the store keeps the old reading, so it
    is pending next run); without one it is left out. A pull request whose
    reading changed and whose proposal is used keeps, besides the proposal's
    tickets, every stored ticket whose `id` or `key` still occurs as a whole
    token in its new `read.md`; a stored ticket the new text no longer names is
    gone.

    `lookup.json` lists the unresolved ids of the fresh pull requests and every
    stored unresolved id and stored ticket id of a frozen or stale pull request
    that no inventory row holds."""
    project = _project(args)
    tracker = _tracker(args)
    store = read_store(args.store)
    entries = _entries(store, project)
    inventory = Inventory(_rows(_load(args.tasks), "the inventory %s" % args.tasks),
                          tracker)
    errors = []
    pulls, skipped = _run(args.collected, errors)
    dropped = {r: 0 for r in REASONS if r != "not_in_tracker"}
    lookup = set()

    def carried(stored):
        """A stored ticket as this run holds it: the inventory's form where it
        resolves, else the stored form, whose id the tracker is asked for."""
        row = inventory.resolve(stored["id"])
        if row is not None:
            return _ticket(row)
        lookup.add(stored["id"])
        return {k: stored[k] for k in ("source", "id", "key") if k in stored}

    def stored_tickets(entry):
        """(tickets, unresolved) a stored entry holds. A ticket is kept
        whatever the inventory holds. A stored unresolved reference joins the
        tickets once the inventory resolves it; until then the tracker is asked
        for it again."""
        tickets = [carried(stored) for stored in entry["tickets"]]
        unresolved = []
        for stored in entry.get("unresolved", []):
            row = inventory.resolve(stored["id"])
            if row is None:
                unresolved.append({"source": stored["source"], "id": stored["id"]})
                lookup.add(stored["id"])
            else:
                tickets.append(_ticket(row))
        return tickets, unresolved

    decided = []
    for pull in pulls:
        stored = _of_tracker(entries.get(pull["node_id"]), tracker)
        if _is_frozen(pull, stored):
            decided.append((pull, "frozen") + stored_tickets(stored))
            continue
        proposals = None
        if pull["reading"] is not None:
            path = _proposal_path(args.proposals, pull["repository"], pull["number"])
            proposals, reason = _proposals(path)
            if proposals is None:
                errors.append({"repository": pull["repository"],
                               "number": pull["number"], "file": path,
                               "reason": reason})
        if proposals is None:
            if stored:
                decided.append((pull, "stale") + stored_tickets(stored))
            continue
        tickets, unresolved, seen = [], [], set()
        for old in (stored or {}).get("tickets", []):
            if any(_token_pattern(name).search(pull["text"])
                   for name in (old["id"], old.get("key"))
                   if isinstance(name, str) and name.strip()):
                tickets.append(carried(old))
        # A stored unresolved reference follows the same rule as a stored
        # ticket: kept while the new text still names it whole, so a proposer
        # that misses it on the new reading does not drop it for good.
        for old in (stored or {}).get("unresolved", []):
            name = old.get("id")
            if (not isinstance(name, str) or not name.strip()
                    or id_key(tracker, name) in seen
                    or not _token_pattern(name).search(pull["text"])):
                continue
            row = inventory.resolve(name)
            if row is not None:
                tickets.append(_ticket(row))
                continue
            seen.add(id_key(tracker, name))
            unresolved.append({"source": tracker, "id": name})
            lookup.add(name)
        for proposal in proposals:
            if "\n" in proposal["span"] or "\r" in proposal["span"]:
                dropped["span_multiline"] += 1
                continue
            if proposal["span"] not in pull["text"]:
                dropped["span_not_in_text"] += 1
                continue
            if not _whole_id_in_span(pull["text"], proposal["span"], proposal["id"]):
                dropped["id_not_in_span"] += 1
                continue
            if proposal["id"].isdigit() and proposal["span"] == proposal["id"]:
                dropped["span_is_bare_id"] += 1
                continue
            row = inventory.resolve(proposal["id"])
            if row is not None:
                tickets.append(_ticket(row))
            elif id_key(tracker, proposal["id"]) not in seen:
                seen.add(id_key(tracker, proposal["id"]))
                unresolved.append({"source": tracker, "id": proposal["id"]})
                lookup.add(proposal["id"])
        decided.append((pull, "fresh", tickets, unresolved))

    document = {
        "schema": VERIFIED_SCHEMA, "project": project, "dropped": dropped,
        "pull_requests": [{
            "repository": p["repository"], "number": p["number"],
            "node_id": p["node_id"], "url": p["url"],
            "reading": (entries[p["node_id"]]["reading"] if status == "stale"
                        else p["reading"]),
            "stored_reading": (entries[p["node_id"]]["reading"]
                               if p["node_id"] in entries else None),
            "status": status,
            "tickets": _sorted_tickets(tickets),
            "unresolved": sorted(unresolved,
                                 key=lambda t: (t["source"], t["id"]))}
            for p, status, tickets, unresolved in decided]}
    verified = os.path.join(args.out_dir, "verified.json")
    lookup_path = os.path.join(args.out_dir, "lookup.json")
    _write_json(verified, document)
    _write_json(lookup_path, {"trackers": {tracker: sorted(lookup)}
                              if lookup else {}})
    rows = document["pull_requests"]
    summary = {"verified": verified, "lookup": lookup_path,
               "pull_requests": len(rows),
               "fresh": sum(1 for r in rows if r["status"] == "fresh"),
               "frozen": sum(1 for r in rows if r["status"] == "frozen"),
               "stale": sum(1 for r in rows if r["status"] == "stale"),
               "tickets": sum(len(r["tickets"]) for r in rows),
               "unresolved": sum(len(r["unresolved"]) for r in rows),
               "dropped": dropped}
    if skipped:
        summary["skipped_reports"] = skipped
    if errors:
        summary["errors"] = errors
    return summary


def _verified(path, project):
    document = _load(path)
    if (not isinstance(document, dict) or document.get("schema") != VERIFIED_SCHEMA
            or not isinstance(document.get("pull_requests"), list)
            or not isinstance(document.get("dropped", {}), dict)):
        raise Unreadable("%s is not a %s document" % (path, VERIFIED_SCHEMA))
    if document.get("project") != project:
        raise Usage("%s was verified for the project %r, not %r"
                    % (path, document.get("project"), project))
    for entry in document["pull_requests"]:
        if not (isinstance(entry, dict) and isinstance(entry.get("repository"), str)
                and isinstance(entry.get("number"), int)
                and not isinstance(entry.get("number"), bool)
                and isinstance(entry.get("node_id"), str)
                and isinstance(entry.get("url"), str)
                and isinstance(entry.get("reading"), str)
                and "stored_reading" in entry
                and isinstance(entry["stored_reading"], (str, type(None)))
                and entry.get("status") in STATUSES
                and _tickets_ok(entry.get("tickets"))
                and _tickets_ok(entry.get("unresolved"))):
            raise Unreadable("%s holds a malformed pull request entry" % path)
    return document


def _repository_order(path):
    rows = _load(path)
    if not isinstance(rows, list):
        raise Unreadable("the repositories %s must hold a JSON list" % path)
    order = {}
    for row in rows:
        name = row.get("repo") if isinstance(row, dict) else None
        if isinstance(name, str) and name:
            order.setdefault(name.casefold(), len(order))
    return order


def _under(tracker, refs):
    """`refs` with every source set to the run's tracker: a run seeds one
    tracker, and a source spelled any other way (a name with a space in it,
    another case) must not reach a card."""
    return [dict(ref, source=tracker) for ref in refs]


def freeze(args):
    """Store what stands.

    An unresolved reference becomes a ticket when a row of `--cited` (optional)
    resolves it, by the same rule as `verify`; any other is counted
    `not_in_tracker` (every unresolved reference the tracker did not return
    this run, a stored one included) and kept in the store under its pull
    request as `unresolved`, so a later run that finds it adds it to `tickets`
    without asking a model again. The store is written for every fresh and
    frozen pull request of `verified.json` (a stale one is left alone); `--out`
    lists them all, stale ones included, in the order of the rows of
    `--repositories` (their `repo`, compared case-insensitively), then pull
    request number; a pull request of a repository that list does not name
    comes after, ordered by repository name. Tickets are de-duplicated and
    sorted by `(source, id)`. An entry whose stored reading in the store is no
    longer the `stored_reading` verify saw is refused: listed in `errors`, left
    out of the store and of `--out`. Other projects in the store are never
    touched. The summary's `dropped` adds `not_in_tracker` to the counts
    `verify` carried. `--summary-out` also writes the summary to that file
    (any exit that prints one), for `seed_ledger.py summary --extra`."""
    project = _project(args)
    tracker = _tracker(args)
    document = _verified(args.verified, project)
    cited = Inventory(_under(tracker, _rows(
        _load(args.cited), "the cited tasks %s" % args.cited)), tracker) \
        if args.cited else Inventory([], tracker)
    order = _repository_order(args.repositories)
    store = read_store(args.store)
    _entries(store, project)
    entries = store["projects"].setdefault(project, {})
    dropped = document.get("dropped") or {}
    dropped = {r: int(dropped.get(r) or 0) for r in REASONS}
    errors, out = [], []
    for entry in document["pull_requests"]:
        node = entry["node_id"]
        tickets = _under(tracker, [
            {k: t[k] for k in ("source", "id", "key") if k in t}
            for t in entry["tickets"]])
        if entry["status"] != "stale":
            current = entries[node]["reading"] if node in entries else None
            if current != entry["stored_reading"]:
                errors.append({"repository": entry["repository"],
                               "number": entry["number"],
                               "reason": "the store changed since verify; "
                                         "entry left alone"})
                continue
        unresolved = []
        for ref in entry["unresolved"]:
            row = cited.resolve(ref["id"])
            if row is None:
                dropped["not_in_tracker"] += 1
                unresolved.append({"source": tracker, "id": ref["id"]})
            else:
                tickets.append(_ticket(row))
        tickets = _sorted_tickets(tickets)
        unresolved = _sorted_tickets(unresolved)
        if entry["status"] != "stale":
            entries[node] = {"repository": entry["repository"],
                             "number": entry["number"], "url": entry["url"],
                             "reading": entry["reading"], "tracker": tracker,
                             "tickets": tickets}
            if unresolved:
                entries[node]["unresolved"] = unresolved
        out.append({"repository": entry["repository"], "number": entry["number"],
                    "node_id": node, "url": entry["url"], "tickets": tickets})
    out.sort(key=lambda p: (order.get(p["repository"].casefold(), len(order)),
                            p["repository"].casefold(), p["number"]))
    _write_json(args.store, store)
    _write_json(args.out, {"schema": PR_REFS_SCHEMA, "project": project,
                           "pull_requests": out})
    summary = {"out": args.out, "store": args.store, "pull_requests": len(out),
               "tickets": sum(len(p["tickets"]) for p in out),
               "dropped": dropped}
    if errors:
        summary["errors"] = errors
    return summary


def _parser():
    parser = argparse.ArgumentParser(prog="ticket_refs.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, collected):
        p.add_argument("--project", required=True)
        p.add_argument("--tracker", required=True)
        p.add_argument("--store", default=DEFAULT_STORE)
        if collected:
            p.add_argument("--collected", required=True, action="append")

    one = sub.add_parser("pending", allow_abbrev=False)
    common(one, True)
    one.add_argument("--out", required=True)
    one.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    two = sub.add_parser("verify", allow_abbrev=False)
    common(two, True)
    two.add_argument("--proposals", required=True)
    two.add_argument("--tasks", required=True)
    two.add_argument("--out-dir", required=True)
    three = sub.add_parser("freeze", allow_abbrev=False)
    common(three, False)
    three.add_argument("--verified", required=True)
    three.add_argument("--cited")
    three.add_argument("--repositories", required=True)
    three.add_argument("--out", required=True)
    three.add_argument("--summary-out")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    command = {"pending": pending, "verify": verify, "freeze": freeze}
    try:
        document = command[args.command](args)
    except (Missing, Usage) as err:
        print("ticket_refs: %s" % err, file=sys.stderr)
        return 2
    except Unreadable as err:
        print("ticket_refs: %s" % err, file=sys.stderr)
        return 3
    except OSError as err:
        print("ticket_refs: %s" % err, file=sys.stderr)
        return 5
    try:
        if getattr(args, "summary_out", None):
            _write_json(args.summary_out, document)
    except OSError as err:
        print("ticket_refs: %s" % err, file=sys.stderr)
        return 5
    print(json.dumps(document, ensure_ascii=False))
    return 3 if document.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
