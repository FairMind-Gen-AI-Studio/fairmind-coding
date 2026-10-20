"""What the answer-before-asking hooks remember about a session's questions.

Two hooks share it. The one that asks the brain before the person
(`brain_answer_hook`) writes an entry for every question a recorded decision
answered or was shown against; the one that records the person's pick
(`brain_record_answer_hook`) reads it to tell an overrule from a first answer.

One file per entry, in a directory per session:
`<data dir>/brain-answer/answered/<session key>/<question key>.json`. Two hooks of
one session running at once (subagents) therefore never write the same file, and
a lost entry cannot happen the way it would in a shared read-modify-write file.
A lost entry matters: it is how the brain would answer a question the person
already overruled.

A DECISION ENTRY says which decision answered a question and how it stands:
`{decision_id, node_id, chosen_option, verdict, review_state, status, at}` plus
`pending: true` once the question was asked again. `decision_id` is null when the
brain named only the node, which still lets a re-ask be held back but leaves a
supersession without the id it needs. The record hook replaces the decision on
a different pick whether or not the entry is pending: a person who was shown a
question the brain had answered and answers differently has overruled it.

A CONFIRMATION ENTRY (`kind: "confirm"`) holds the prepared supersession that
waits for a person's yes to the confirmation question the record hook dictated.

The question key is the hash of the normalized question text, so what was ASKED
is what is looked up. Nothing here raises: every function answers "nothing
remembered" on any failure, which every caller reads as the safe direction
(ask the person).

Stdlib plus `_plugin_policy` (path and atomic write), Python 3.9 floor.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from datetime import datetime, timezone

import _plugin_policy

__all__ = ["KIND_CONFIRM", "KIND_DECISION", "MAX_AGE_S", "MAX_PRUNE",
           "MAX_SCAN", "find_by_node", "normalize", "question_key", "read",
           "remove", "remove_by_node", "write"]

KIND_DECISION = "decision"
KIND_CONFIRM = "confirm"

#: A session directory untouched for this long is removed by the next write.
MAX_AGE_S = 7 * 24 * 3600
#: Directories removed per write, so a backlog never costs one hook its budget.
MAX_PRUNE = 200
#: Files read by one `find_by_node`; a session never holds more questions than
#: the hook budget could ask about, so the bound is never the answer.
MAX_SCAN = 200


def normalize(text):
    """The text two askings of one question share: whitespace collapsed, case
    folded."""
    return " ".join(str(text).split()).casefold()


def question_key(text):
    return hashlib.sha256(normalize(text).encode("utf-8", "replace")).hexdigest()[:32]


def _root():
    return os.path.join(_plugin_policy._data_dir(), "brain-answer", "answered")


def _session_dir(session_id):
    name = hashlib.sha256(str(session_id).encode("utf-8", "replace")).hexdigest()[:32]
    return os.path.join(_root(), name)


def _usable(session_id):
    return isinstance(session_id, str) and bool(session_id.strip())


def _valid(entry):
    """Whether `entry` has the fields its kind needs."""
    if not isinstance(entry, dict):
        return False
    if entry.get("kind") == KIND_CONFIRM:
        return isinstance(entry.get("args"), dict)
    decision_id = entry.get("decision_id")
    return (isinstance(entry.get("node_id"), str) and bool(entry["node_id"])
            and (decision_id is None or isinstance(decision_id, str)))


def _load(path):
    import json  # noqa: PLC0415 - the common miss never reaches the parser
    try:
        with open(path, encoding="utf-8") as fh:
            entry = json.load(fh)
    except (OSError, ValueError):
        return None
    return entry if _valid(entry) else None


def read(session_id, question):
    """The entry remembered for `question` in this session, or None. A missing
    session id, file or well-formed entry all answer None."""
    if not _usable(session_id):
        return None
    try:
        path = os.path.join(_session_dir(session_id), question_key(question) + ".json")
        return _load(path)
    except Exception:  # noqa: BLE001 - nothing remembered is the safe answer
        return None


def write(session_id, question, entry):
    """Remember `entry` for `question`. True when it was stored.

    A missing session id stores nothing: there is no way to find the entry
    again, and the callers then behave as if the brain could not remember."""
    if not _usable(session_id) or not _valid(entry):
        return False
    try:
        stored = dict(entry)
        stored.setdefault("at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        directory = _session_dir(session_id)
        _plugin_policy._atomic_write_json(
            os.path.join(directory, question_key(question) + ".json"), stored,
            prefix=".fm-answered-", mode=0o600, ensure_makedirs=True)
        _prune(keep=directory)
        return True
    except Exception:  # noqa: BLE001
        return False


def remove(session_id, question):
    if not _usable(session_id):
        return
    try:
        os.remove(os.path.join(_session_dir(session_id), question_key(question) + ".json"))
    except Exception:  # noqa: BLE001 - already gone is the goal
        pass


def remove_by_node(session_id, node_id):
    """Forget every decision entry of this session that points at the decision
    `node_id`, under whatever text it was asked: once the decision is retired, no
    question may be held back or routed against it. Confirmation entries stay."""
    if not _usable(session_id) or not isinstance(node_id, str):
        return
    try:
        directory = _session_dir(session_id)
        for name in sorted(os.listdir(directory))[:MAX_SCAN]:
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            entry = _load(path)
            if (entry is not None and entry.get("kind") != KIND_CONFIRM
                    and entry.get("node_id") == node_id):
                os.remove(path)
    except Exception:  # noqa: BLE001 - what stays is read as "ask the person"
        pass


def find_by_node(session_id, node_id):
    """The decision entry this session holds for the decision `node_id`, or
    None: how a REWORDED question is recognised as one already answered by the
    same decision."""
    if not _usable(session_id) or not isinstance(node_id, str):
        return None
    try:
        directory = _session_dir(session_id)
        for name in sorted(os.listdir(directory))[:MAX_SCAN]:
            if not name.endswith(".json"):
                continue
            entry = _load(os.path.join(directory, name))
            if (entry is not None and entry.get("kind") != KIND_CONFIRM
                    and entry.get("node_id") == node_id):
                return entry
    except Exception:  # noqa: BLE001
        return None
    return None


def _prune(keep):
    now = time.time()
    removed = 0
    try:
        names = os.listdir(_root())
    except OSError:
        return
    for name in names:
        if removed >= MAX_PRUNE:
            break
        path = os.path.join(_root(), name)
        if path == keep:
            continue
        try:
            if os.path.isdir(path) and now - os.stat(path).st_mtime > MAX_AGE_S:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
