#!/usr/bin/env python3
"""Stage a reference card: the one place an agent's words meet a collected source.

A card for the company brain is a short summary and a few rationale points about
a pull request or a ticket, never the source's own text. The collector
(`pr_collect.py`, or the tracker shaper) writes a SKELETON holding everything
mechanical: ids, state, author, dates, branches, files, participants, the
source's version and visibility. The agent that read the source writes only its
words. This script joins the two, so the agent never re-types a field:

    reference_card.py stage --card SKELETON --input WORDS --out FILE
                            --agent NAME --project ID [--repository ID]
                            [--git-remote URL] [--refs PR_REFS] [--restage]
    reference_card.py stage --card SKELETON --copy [--input WORDS] --out FILE
                            --agent NAME --project ID [--repository ID]
                            [--git-remote URL] [--refs PR_REFS] [--restage]
    reference_card.py erased --card SKELETON [--erased LOGIN ...]
                             [--staged FILE] [--project ID]
                             [--repository ID] [--git-remote URL]
                             [--refs PR_REFS]

SKELETON is `{"repository"?, "agent_accounts"?, "pull_request" | "item": {...}}`.
WORDS is `{"summary", "rationale_points"?: [{"text", "file_path"?}],
"implements"?, "resolves"?}` (the last two only for a pull request, and only
when the person named them). Nothing else is accepted from the agent.

A card is built from an allow-list of fields per door (below), so a key the door
does not take is refused by name, not passed through. A pull request needs
`--repository` (the repository's catalog id, which the door resolves; the
card's own `repository` field is the `owner/name` spelling) and `--git-remote`.
When a reading file (`read.md`) sits beside the skeleton, a
summary or point that shares a run of 12 or more words with it, or repeats
whole a paragraph of it of five to eleven words, is refused: the words are the
agent's own, never the source's. A skeleton with no reading file is refused.

`--copy` stages the copy form: the card with no `summary` and no
`rationale_points`, which the brain fills from a card of the same source that
another project (or this one) already holds. It is staged only after the
brain's check answered `summary_needed: false`. Nothing is read, so no reading
file is needed and none is compared; `--input` is optional and, when given, may
hold only `implements` and `resolves`. A copy card records no redaction, so
`erased` calls it stale as soon as an erased login took part in the source: it
is then staged again with words.

Refused, never cut to fit: a summary that is empty or over 1000 characters; more
than 5 rationale points, one that is empty or over 300 characters, one
whose `file_path` is not a canonical repo-relative path, or, for a pull
request, one whose `file_path` the reading file does not show (neither a
changed file nor the file of a review thread); more than 100
participants; a field the card must not carry (a description, turns, comments);
and any summary or point that looks like a credential. The refusal names the
kind of pattern, never the text it matched.

FILE receives `{"arguments": {...}, "expected_sha256": HEX}`: the arguments of
the import call (the skeleton's card with the agent's words merged in, plus
`project`, `agent`, `agent_accounts`, and for a pull request `repository`, the
catalog id, and `git_remote`), written as sorted-key, indent-2 JSON with one trailing newline.
`expected_sha256` is the SHA-256 of the compact canonical JSON (sorted keys, no
ASCII escaping, no whitespace) of the card alone, which is what the door echoes
back as `received_sha256`. A call is sent as `arguments` plus
`expected_sha256`.

`--refs PR_REFS` (the `pr-refs.json` of `ticket_refs.py freeze`, schema
`brain-seed-pr-refs/1`; any other schema exits 3) is for a pull request card
only. The entry whose `node_id` equals the card's `id` gives the card's
`tracker_items`: `[{"source", "id"}]`, source case-folded, id as a string,
de-duplicated and sorted by `(source, id)`. They are set by this script and are
never one of the agent's words (the words input is refused when it carries
them). The references must be those of `--project` (else exit 3), and an
entry whose repository (case-insensitive) or number is not the card's is
ignored, with a note on stderr. A pull request with an entry that cites no
ticket gets `tracker_items: []`, which tells the brain its references are known
and empty, so it clears the links it holds; one with no entry gets no
`tracker_items` key (the brain keeps what it holds), so its card is the one
staged without `--refs`. More than 50 are refused, never cut. `tracker_items` is part of the card, so it is part of `expected_sha256`.

A card already staged for the same source version, project, repository, remote
and `tracker_items` is not summarised again: staging over it is refused unless
`--restage` says the words are being replaced on purpose. A card staged with
other `tracker_items` is staged again without it.

`erased` takes the logins the brain's check answer lists as erased (it lists
every one erased in the project, not only the source's; none when the answer
lists nobody, which still compares a staged card's binding) and prints
`{"affects": [...], "stale": bool}`, plus `"stale_reason"` when `--refs` is given. `affects` is the logins that took part in
the source (the skeleton's participants, compared without regard to case, a
Notion user id in its one spelling, plus `ghost`, which every deleted account
shares and nobody's participant list holds, when the reading file beside the
skeleton shows it wrote something, or cannot be read) and that the reading file
has not been redacted for yet. `redact` leaves a record beside the reading file
(`redacted.json`, bound to the file's bytes), so a person already left out is
not asked for again; a record that does not match the file counts for nothing.
With `--staged FILE`, `stale` says whether that card was staged before the
reading file was redacted for every one of those people: `stage` records what
it covered. Given `--project` (and `--repository`, `--git-remote` for a pull
request) too, `stale` is also true when the card at `--staged` was staged under
other values for them, and, given `--refs`, when its `tracker_items` differ
from what the references give now (absent equals absent, and `[]` differs
from absent): the card is never
sent, it is staged again. A card is
sent only when `affects` is empty and `stale` is false. With `--refs`, `stale_reason` is null
when `stale` is false, else the first that applies: `redaction` (the card
predates the redactions), `binding` (another project, repository or remote),
`tickets` (only its `tracker_items` changed: stage again with the existing
words, nothing is redacted or summarised again). `--refs` on a ticket card is
ignored by `erased`.

stdout is one JSON document `{"out", "expected_sha256", "card_bytes",
"source_version"}`; `card_bytes` is the size of the card the door will count
(it refuses one over 32 KB). A card over that size is still staged, because
the door decides, but the document then carries a `warning` saying so, before
anything is sent. Exit codes: 0 ok; 2 usage or a refused card
(every problem is listed on stderr); 3 an input is not readable JSON; 4 already
staged; 5 the output cannot be written. stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

from login_key import match_key
from pr_collect import (GHOST_LOGIN, REVIEW_THREADS_HEADING,
                        _atomic_write_text, _sendable, redact_reading,
                        redaction_record, save_redaction_record)
from ticket_refs import RefsRefused, read_pr_refs, tickets_of

MAX_SUMMARY = 1000
MAX_POINTS = 5
MAX_POINT = 300
MAX_PARTICIPANTS = 100
MAX_FILES = 300
MAX_REFS = 50
SERVER_CARD_BYTES = 32 * 1024
FIELDS = {"pull_request": "pull_request", "item": "item"}
# The fields a skeleton may carry, per door: what the collector or the shaper
# fills. The agent's words (summary, rationale_points, implements, resolves)
# are joined by the stager and never come from a skeleton.
SKELETON_FIELDS = {
    "pull_request": frozenset((
        "source", "id", "number", "repository", "url", "title", "state",
        "author", "created_at", "closed_at", "head_ref", "base_ref",
        "head_sha", "merge_commit_sha", "files", "file_count", "participants",
        "source_version", "source_visibility")),
    "item": frozenset((
        "source", "id", "key", "url", "project_key", "kind", "type", "title",
        "status_category", "status", "author", "assignees", "created_at",
        "closed_at", "parent", "pull_request_urls", "commit_shas",
        "participants", "source_version", "source_visibility")),
}
# A summary or point that repeats this many words in a row from the reading
# file is the source's text, not the agent's.
VERBATIM_RUN = 12
# A source paragraph shorter than the run but at least this long counts when a
# text repeats it whole.
WHOLE_PARAGRAPH_MIN = 5
WORD_KEYS = {"summary", "rationale_points", "implements", "resolves"}
# What a reference card never holds: the source's own text.
FORBIDDEN = ("description", "body", "turns", "turn_count", "comments",
             "comment_count", "conversations", "conversation_count", "parts")
SECRET_PATTERNS = (
    ("a private key block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token",
     re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("an API key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
    ("a JSON web token",
     re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("a bearer credential", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("a URL with a password",
     re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("a credential assignment",
     re.compile(r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b"
                r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_.=-]{8,}")),
)


class Refused(Exception):
    def __init__(self, problems):
        Exception.__init__(self, "; ".join(problems))
        self.problems = problems


class Unreadable(Exception):
    pass


class AlreadyStaged(Exception):
    pass


class WriteFailed(Exception):
    pass


def canonical(value):
    """Compact canonical JSON: sorted keys, no ASCII escaping, no spaces."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _load(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        raise Unreadable("cannot read %s %s as JSON" % (what, path))


def secret_kinds(text):
    """The kinds of credential `text` looks like, never the matched text."""
    return [kind for kind, pattern in SECRET_PATTERNS if pattern.search(text)]


def _text_problems(label, value, limit):
    if not isinstance(value, str) or not value.strip():
        return ["%s must be a non-empty string" % label]
    problems = []
    if len(value) > limit:
        problems.append("%s is %d characters, over the limit of %d (it is "
                        "refused, never cut)" % (label, len(value), limit))
    for kind in secret_kinds(value):
        problems.append("%s looks like it contains %s; remove it" % (label, kind))
    return problems


def _refs(label, value):
    if (not isinstance(value, list) or len(value) > MAX_REFS
            or not all(isinstance(r, dict) and r for r in value)):
        return ["%s must be a list of at most %d reference objects"
                % (label, MAX_REFS)]
    return []


def check_words(words, field, copy=False):
    """Every problem with the agent's words, as a list of sentences. A copy
    card takes no summary and no rationale points."""
    if not isinstance(words, dict):
        return ["the words must be a JSON object"]
    problems = []
    if copy:
        for key in ("summary", "rationale_points"):
            if key in words:
                problems.append("%s is not accepted with --copy: the brain "
                                "fills it from the card it holds" % key)
        words = {k: v for k, v in words.items()
                 if k not in ("summary", "rationale_points")}
    for key in sorted(words):
        if key not in WORD_KEYS:
            problems.append("%s is not accepted from the agent: only summary, "
                            "rationale_points, implements and resolves are"
                            % key)
        elif key in ("implements", "resolves") and field != "pull_request":
            problems.append("%s applies to a pull request only" % key)
    if not copy:
        problems += _text_problems("summary", words.get("summary"), MAX_SUMMARY)
    points = words.get("rationale_points", [])
    if not isinstance(points, list):
        problems.append("rationale_points must be a list")
        points = []
    if len(points) > MAX_POINTS:
        problems.append("rationale_points holds %d points, over the limit of "
                        "%d" % (len(points), MAX_POINTS))
    for index, point in enumerate(points, 1):
        label = "rationale point %d" % index
        if not isinstance(point, dict):
            problems.append("%s must be an object" % label)
            continue
        extra = sorted(set(point) - {"text", "file_path"})
        if extra:
            problems.append("%s carries %s; only text and file_path are "
                            "accepted" % (label, ", ".join(extra)))
        problems += _text_problems(label + " text", point.get("text"), MAX_POINT)
        path = point.get("file_path")
        if "file_path" in point and (
                not isinstance(path, str) or not path or not _sendable(path)):
            problems.append("%s has a file_path that is not a canonical "
                            "repo-relative path" % label)
        elif isinstance(path, str) and field != "pull_request":
            problems.append("%s names a file, and a ticket has no repository"
                            % label)
    for key in ("implements", "resolves"):
        if key in words:
            problems += _refs(key, words[key])
    return problems


def check_skeleton(skeleton):
    """(field name, problems) for a collector's skeleton."""
    if not isinstance(skeleton, dict):
        return None, ["the skeleton must be a JSON object"]
    fields = [f for f in FIELDS if f in skeleton]
    if len(fields) != 1:
        return None, ["the skeleton must hold exactly one of pull_request or "
                      "item"]
    field = fields[0]
    card = skeleton[field]
    if not isinstance(card, dict):
        return field, ["the skeleton's %s must be an object" % field]
    problems = []
    for key in FORBIDDEN:
        if key in card:
            problems.append("the card must not carry %s: a reference card "
                            "holds no text from the source" % key)
    if "summary" in card or "rationale_points" in card:
        problems.append("the skeleton already carries the agent's words")
    for key in sorted(set(card) - SKELETON_FIELDS[field] - set(FORBIDDEN)
                      - {"summary", "rationale_points"}):
        problems.append("the card carries %s, which the %s door does not take"
                        % (key, "pull request" if field == "pull_request"
                           else "tracker"))
    version = card.get("source_version")
    if not isinstance(version, str) or not 1 <= len(version) <= 128:
        problems.append("the skeleton has no source_version")
    if "source_visibility" not in card:
        problems.append("the skeleton has no source_visibility (null when the "
                        "source reports none)")
    if len(card.get("participants") or []) > MAX_PARTICIPANTS:
        problems.append("the card has more than %d participants"
                        % MAX_PARTICIPANTS)
    if len(card.get("files") or []) > MAX_FILES:
        problems.append("the card has more than %d files" % MAX_FILES)
    return field, problems


def _words_of(text):
    return re.findall(r"\w+", (text or "").casefold())


def _word_runs(text, size):
    words = _words_of(text)
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


_COMMENT_HEADER = re.compile(r"^\s*(?:###\s|-\s).+,\s*\S+\s*$")


def _text_units(reading):
    """The reading file's texts as blocks of lines: a blank line, a heading
    and a comment's header line (`### login, time` in the conversation,
    `- login, time` in a review thread) each end the block before them, so a
    comment is one unit however the collector laid it out."""
    unit = []
    for line in (reading or "").splitlines():
        if (not line.strip() or line.lstrip().startswith("#")
                or _COMMENT_HEADER.match(line)):
            if unit:
                yield unit
            unit = []
        else:
            unit.append(line)
    if unit:
        yield unit


def _short_paragraphs(reading, size):
    """The word tuples of the reading file's texts that are shorter than
    `size` words and at least WHOLE_PARAGRAPH_MIN long; headings, comment
    headers and quote markers are not part of a text's words."""
    found = set()
    for unit in _text_units(reading):
        words = tuple(_words_of(" ".join(unit)))
        if WHOLE_PARAGRAPH_MIN <= len(words) < size:
            found.add(words)
    return found


def _contains(words, part):
    return any(tuple(words[i:i + len(part)]) == part
               for i in range(len(words) - len(part) + 1))


def copied_from(reading, words, size=VERBATIM_RUN):
    """`(label, reason)` for each of the agent's texts that repeats `size` or
    more words in a row from the reading file (`"run"`), or repeats a whole
    shorter paragraph of it (`"paragraph"`). Never the text itself."""
    if not reading or not isinstance(words, dict):
        return []
    source = _word_runs(reading, size)
    short = _short_paragraphs(reading, size)
    if not source and not short:
        return []
    texts = [("summary", words.get("summary"))]
    points = words.get("rationale_points")
    if isinstance(points, list):
        texts += [("rationale point %d" % n, p.get("text"))
                  for n, p in enumerate(points, 1) if isinstance(p, dict)]
    found = []
    for label, value in texts:
        if not isinstance(value, str):
            continue
        if _word_runs(value, size) & source:
            found.append((label, "run"))
        elif any(_contains(_words_of(value), part) for part in short):
            found.append((label, "paragraph"))
    return found


_THREAD_HEADING = re.compile(
    r"^### (.+?)(?::[0-9]+)? \((?:resolved|open)(?:, outdated)?\)$")


def thread_paths(reading):
    """The files the review threads of a reading file are on."""
    paths, inside = set(), False
    for line in (reading or "").splitlines():
        if line.startswith("## "):
            inside = line == REVIEW_THREADS_HEADING
            continue
        match = _THREAD_HEADING.match(line) if inside else None
        if match and match.group(1) != "(no file)":
            paths.add(match.group(1))
    return paths


def unshown_files(card, words, reading):
    """A sentence for each rationale point whose file_path the reading file
    never showed the agent: neither a changed file of the card nor a file a
    review thread is on. A path with a directory added or dropped names
    another file, so it is refused, never corrected."""
    points = words.get("rationale_points") if isinstance(words, dict) else None
    if not isinstance(points, list):
        return []
    shown = set(card.get("files") or []) | thread_paths(reading)
    problems = []
    for index, point in enumerate(points, 1):
        path = point.get("file_path") if isinstance(point, dict) else None
        if isinstance(path, str) and path and path not in shown:
            problems.append(
                "rationale point %d names %s, which is not one of the pull "
                "request's files: name a changed file or the file of a review "
                "thread exactly as the reading file spells it, or leave "
                "file_path out" % (index, path))
    return problems


def build(skeleton, words, agent, project, git_remote=None, reading=None,
          repository=None, tracker_items=None, copy=False):
    """The card and the import call's arguments. Raises Refused. With `copy`
    the card carries no summary and no rationale points."""
    field, problems = check_skeleton(skeleton)
    if field is not None:
        problems += check_words(words, field, copy)
    if field == "pull_request" and reading is not None:
        problems += unshown_files(skeleton[field], words, reading)
    if tracker_items is not None and field != "pull_request":
        problems.append("tracker_items applies to a pull request only")
    if not agent or not project:
        problems.append("--agent and --project are required")
    if field == "pull_request" and not git_remote:
        problems.append("a pull request card needs --git-remote (the origin "
                        "remote the door files the card under)")
    if field == "pull_request" and not repository:
        problems.append("a pull request card needs --repository (the "
                        "repository's catalog id, which the door resolves)")
    for label, reason in copied_from(reading, words):
        if reason == "run":
            problems.append("%s repeats %d or more words in a row from the "
                            "reading file; write it in your own words"
                            % (label, VERBATIM_RUN))
        else:
            problems.append("%s repeats a whole paragraph of the reading "
                            "file (%d to %d words); write it in your own "
                            "words" % (label, WHOLE_PARAGRAPH_MIN,
                                       VERBATIM_RUN - 1))
    if problems:
        raise Refused(problems)
    card = dict(skeleton[field])
    if not copy:
        card["summary"] = words["summary"]
    if words.get("rationale_points"):
        card["rationale_points"] = words["rationale_points"]
    for key in ("implements", "resolves"):
        if words.get(key):
            card[key] = words[key]
    if tracker_items is not None:
        card["tracker_items"] = tracker_items
    arguments = {"agent": agent, "project": project, field: card}
    accounts = skeleton.get("agent_accounts")
    if accounts:
        arguments["agent_accounts"] = list(accounts)
    if field == "pull_request":
        arguments["repository"] = repository
        arguments["git_remote"] = git_remote
    return card, arguments


def _atomic_write(path, text):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _atomic_write_text(path, text)
    except OSError as err:
        raise WriteFailed("cannot write %s: %s"
                          % (path, err.strerror or err.__class__.__name__))


def render(document):
    """The file's bytes: sorted keys, indent 2, no ASCII escaping, one
    trailing newline (the shape of the shared conformance fixture)."""
    return json.dumps(document, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


BOUND_KEYS = ("project", "repository", "git_remote")


def _staged_arguments(path):
    try:
        with open(path, encoding="utf-8") as handle:
            staged = (json.load(handle) or {}).get("arguments") or {}
    except (OSError, ValueError, AttributeError):
        return None
    return staged if isinstance(staged, dict) else None


def _bound_to(staged, arguments):
    """Whether the staged arguments name the same project, repository and
    remote as `arguments`."""
    return all(staged.get(key) == arguments.get(key) for key in BOUND_KEYS)


def _staged_for(path, card, arguments):
    """Whether the file holds a card staged for this version of the source
    under the same project, repository and remote as these arguments."""
    staged = _staged_arguments(path)
    if staged is None or not _bound_to(staged, arguments):
        return False
    for field in FIELDS:
        if isinstance(staged.get(field), dict):
            return (staged[field].get("source_version") == card.get("source_version")
                    and staged[field].get("tracker_items")
                    == card.get("tracker_items"))
    return False


def _reading(card_path):
    """The reading file beside a skeleton, or None when there is none."""
    path = os.path.join(os.path.dirname(os.path.abspath(card_path)), "read.md")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _tracker_items_of(path, skeleton, project):
    """The `tracker_items` the references at `path` give the skeleton's pull
    request: None when they hold no entry for it, `[]` when its entry cites
    nothing (see `ticket_refs.tickets_of`). Raises Refused for a ticket card, Unreadable for a file that is
    not references of the known schema and of `project`. An entry whose
    repository or number is not the card's is ignored, and said so."""
    if isinstance(skeleton, dict) and "item" in skeleton:
        raise Refused(["--refs applies to a pull request card only"])
    try:
        refs = read_pr_refs(path, project)
    except (OSError, ValueError) as err:
        raise Unreadable(str(err))
    card = (skeleton.get("pull_request") or {}) \
        if isinstance(skeleton, dict) else {}
    node_id = card.get("id")
    if not isinstance(node_id, str) or not node_id:
        return None
    try:
        return tickets_of(refs, node_id, card.get("repository"),
                          card.get("number"))
    except RefsRefused as err:
        raise Refused([str(err)])


def stage(args):
    skeleton = _load(args.card, "the skeleton")
    if args.copy:
        words, reading = (_load(args.input, "the words") if args.input
                          else {}), None
    else:
        if not args.input:
            raise Refused(["--input is required unless --copy is given"])
        words = _load(args.input, "the words")
        reading = _reading(args.card)
        if reading is None:
            raise Refused(["there is no reading file (read.md) beside the "
                           "skeleton, so the words cannot be checked against "
                           "the source"])
    tracker_items = _tracker_items_of(args.refs, skeleton, args.project) \
        if args.refs else None
    card, arguments = build(skeleton, words, args.agent, args.project,
                            args.git_remote, reading, args.repository,
                            tracker_items, args.copy)
    if (not args.restage and os.path.exists(args.out)
            and _staged_for(args.out, card, arguments)):
        raise AlreadyStaged(
            "%s is already staged for this version of the source: send it "
            "as it is, unless the brain named an erased login that took part "
            "in the source (then stage again with --restage), or pass "
            "--restage to replace the words on purpose"
            % args.out)
    expected = digest(card)
    _atomic_write(args.out, render({"arguments": arguments,
                                    "expected_sha256": expected}))
    reading = os.path.join(os.path.dirname(os.path.abspath(args.card)), "read.md")
    if not args.copy and os.path.exists(reading):
        try:
            save_redaction_record(reading, staged_out=args.out)
        except OSError as err:
            raise WriteFailed("cannot record the redactions the card covers: %s"
                              % (err.strerror or err.__class__.__name__))
    size = len(canonical(card).encode("utf-8"))
    document = {"out": args.out, "expected_sha256": expected,
                "card_bytes": size,
                "source_version": card.get("source_version")}
    if args.copy:
        document["copy"] = True
    if size > SERVER_CARD_BYTES:
        document["warning"] = (
            "the card is %d bytes, over the door's limit of %d: the door "
            "will refuse it" % (size, SERVER_CARD_BYTES))
    return document


def _names_ghost(skeleton, reading_path):
    """Whether a deleted account wrote something of the source: it is no
    participant (every deleted account shares one login), so the skeleton
    cannot say, but its author and its comments are in the reading file beside
    it. A reading file that cannot be read counts as naming it, since the
    erasure must not be skipped on a guess."""
    field = next((f for f in FIELDS if isinstance(skeleton.get(f), dict)), None)
    author = (skeleton.get(field) or {}).get("author")
    if isinstance(author, dict) and isinstance(author.get("login"), str) \
            and match_key(_source_of(skeleton), author["login"]) \
            == match_key(_source_of(skeleton), GHOST_LOGIN):
        return True
    try:
        with open(reading_path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeError):
        return True
    _, removed, dropped = redact_reading(text, [GHOST_LOGIN])
    return bool(removed or dropped)


def _source_of(skeleton):
    """The source a skeleton's people are keyed under: the card's own."""
    field = next((f for f in FIELDS if isinstance(skeleton.get(f), dict)), None)
    source = (skeleton.get(field) or {}).get("source")
    if isinstance(source, str) and source.strip():
        return source
    return "github"


def took_part(skeleton, erased, reading_path=None):
    """The logins of `erased` that took part in the source, once each:
    the ones the skeleton's participants hold, compared by the key the brain
    selects an erasure on (case, padding and a Notion id's spelling included),
    plus a deleted account (`ghost`, held by no skeleton) when the reading
    file at `reading_path` shows it wrote something or a redaction for it is
    on record."""
    field = next((f for f in FIELDS if isinstance(skeleton.get(f), dict)), None)
    source = _source_of(skeleton)
    held = set()
    for who in ((skeleton.get(field) or {}).get("participants") or []):
        if isinstance(who, dict) and isinstance(who.get("login"), str):
            held.add(match_key(source, who["login"]))
    ghost = match_key(source, GHOST_LOGIN)
    if reading_path is not None and (
            ghost in {match_key(source, x)
                      for x in redaction_record(reading_path)["logins"]}
            or _names_ghost(skeleton, reading_path)):
        held.add(ghost)
    seen, taken = set(), []
    for login in erased:
        key = match_key(source, login)
        if key in held and key not in seen:
            seen.add(key)
            taken.append(login)
    return taken


def erased_among(skeleton, erased, reading_path=None, staged=None):
    """What an erasure leaves to do for a source. Returns
    `{"affects": [...], "stale": bool}`.

    `affects` is the erased logins that took part in the source and that the
    reading file has not been redacted for yet: they must be redacted before
    anything is written from it, and a card is sent only when none is left. A
    redaction is on record beside the reading file (`redacted.json`, bound to
    the file's bytes), so a person already left out of it is not asked for
    again.

    `stale` is whether a card staged at `staged` was made before the reading
    file was redacted for every one of those people: such a card was written
    from words that may include theirs and is never sent. With no `staged`
    path it is false."""
    taken = took_part(skeleton, erased, reading_path)
    if reading_path is None:
        return {"affects": taken, "stale": bool(taken and staged)}
    source = _source_of(skeleton)
    record = redaction_record(reading_path)
    done = {match_key(source, x) for x in record["logins"]}
    covered = {match_key(source, x) for x in record["staged"].get(
        os.path.abspath(staged), [])} if staged else set()
    return {"affects": [x for x in taken if match_key(source, x) not in done],
            "stale": bool(staged) and any(
                match_key(source, x) not in covered for x in taken)}


def erased_check(args):
    skeleton = _load(args.card, "the skeleton")
    answer = erased_among(
        skeleton, args.erased,
        os.path.join(os.path.dirname(os.path.abspath(args.card)), "read.md"),
        args.staged)
    reason = "redaction" if answer["stale"] else None
    bound = {"project": args.project, "repository": args.repository,
             "git_remote": args.git_remote}
    on_disk = bool(args.staged) and os.path.exists(args.staged)
    staged = _staged_arguments(args.staged) if on_disk else None
    if on_disk and any(bound.values()):
        if staged is None or not _bound_to(staged, bound):
            answer["stale"] = True
            reason = reason or "binding"
    if (args.refs and on_disk
            and not (isinstance(skeleton, dict) and "item" in skeleton)):
        wanted = _tracker_items_of(args.refs, skeleton, args.project)
        have = ((staged or {}).get("pull_request") or {}).get("tracker_items")
        if staged is None or have != wanted:
            answer["stale"] = True
            reason = reason or "tickets"
    if args.refs:
        answer["stale_reason"] = reason
    return answer


def _parser():
    parser = argparse.ArgumentParser(prog="reference_card.py", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("stage", allow_abbrev=False)
    one.add_argument("--card", required=True)
    one.add_argument("--input")
    one.add_argument("--copy", action="store_true")
    one.add_argument("--out", required=True)
    one.add_argument("--agent", required=True)
    one.add_argument("--project", required=True)
    one.add_argument("--repository")
    one.add_argument("--git-remote")
    one.add_argument("--refs")
    one.add_argument("--restage", action="store_true")
    two = sub.add_parser("erased", allow_abbrev=False)
    two.add_argument("--card", required=True)
    two.add_argument("--erased", action="append", default=[])
    two.add_argument("--staged")
    two.add_argument("--project")
    two.add_argument("--repository")
    two.add_argument("--git-remote")
    two.add_argument("--refs")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    try:
        document = erased_check(args) if args.command == "erased" else stage(args)
    except Refused as err:
        for problem in err.problems:
            print("reference_card: %s" % problem, file=sys.stderr)
        return 2
    except Unreadable as err:
        print("reference_card: %s" % err, file=sys.stderr)
        return 3
    except AlreadyStaged as err:
        print("reference_card: %s" % err, file=sys.stderr)
        return 4
    except WriteFailed as err:
        print("reference_card: %s" % err, file=sys.stderr)
        return 5
    print(json.dumps(document, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
