#!/usr/bin/env python3
"""PostToolUse hook on `AskUserQuestion`: record a person's answer to a design
question as a decision in the company brain, and turn an overrule of a brain
answer into the replacement of that decision.

When the person picks one of the options the agent offered, this hook sends the
question, the offered option LABELS (never their descriptions) and the chosen
label to the brain's `Brain_record_decision`, as a taken decision under the
person's own key. Only a design choice counts: a question about the session
itself ("continue?", "which file?") is never sent, and neither is a typed answer
to an ordinary question.

OVERRULES. The answer hook (`brain_answer_hook`) remembers, per session, every
question a confirmed decision answered. When one of those questions is asked
again and the person picks something else, the pick is sent to
`Brain_supersede_decision` as the replacement of that decision, with the reason
`overruled`, and not also as a plain decision (two successors would leave two
live answers). The decision is kept, readable and marked superseded. A pick that
equals the brain's own choice records nothing. In `suggest` mode the person
saw a suggestion rather than an answer, so a different pick is not replaced on
the spot: the hook dictates a yes/no confirmation question to the agent and
sends the replacement only on the person's yes (the question is session-shaped,
so neither hook sends or records it).

WHAT LEAVES THE MACHINE. The question, the offered option labels and the chosen
label, and repo-relative changed file paths. A typed ("Other") answer is the
person's own words and is skipped whole EXCEPT when it answers a question the
brain answered earlier in the session: then, and only then, it is the title of
the replacement decision (capped at 256 characters), because the typed words
are the new decision. Option descriptions, notes and annotations are not read at
all.

THE FAIL DIRECTION IS A LINE, NOT A BLOCK. Any failure after the answer was
classified as a design decision ends with one status line per question saying it
was not recorded, and exit 0; before that point it ends silent. The hook never
asks for or changes a permission and never alters the tool result. A replacement
the brain refused, or could not make, says so: the old decision stays in force.

TIME. The harness kills a hook at its registered timeout and a killed hook says
nothing, so the whole run is held under `HOOK_BUDGET_S`: every git call carries a
timeout capped by the time left, and every send deadline is capped at the hook's
own start plus that budget.

The `brain` switch governs it, exactly as it governs the loop's brain write-back;
with it off an overrule is not sent and a line says the earlier decision stays in
force. Stdlib plus small leaf modules.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _answer_memory  # noqa: E402
import _binding  # noqa: E402
import _design_question  # noqa: E402
import _hook_line  # noqa: E402
import _mcp_config  # noqa: E402
import audit_run_meta  # noqa: E402

#: The send alone, from the moment it starts.
SEND_BUDGET_S = 5.0
#: The whole hook, from its entry. Under the 10 s the hook is registered with.
HOOK_BUDGET_S = 8.0
#: One local git call.
GIT_TIMEOUT_S = 1.0
MAX_FILES = 20
CONTRACT_VERSION = "fm-insights.decision/2"
AGENT = "ask-user-question"
KIND = "other"
RATIONALE = "Chosen by the person in answer to a design question in the session."
DECIDED_BY = "human"
ORIGIN = "taken"

_QUESTION_TEXT_LIMIT = 40
#: The brain caps a decision title at this many characters; a longer typed answer
#: is cut here rather than sent whole.
TITLE_MAX = 256
REASON = "overruled"


# --------------------------------------------------------------------------- #
# Time-boxed git. A git process that stalls (a credential helper, a network
# filesystem) must not hold a hook whose budget the harness enforces by killing
# it, so every call runs in its own process group and the whole group dies at
# the timeout: killing only `git` leaves a grandchild holding the pipe open.
# --------------------------------------------------------------------------- #

def _git_bytes(repo, args, start):
    """stdout of `git -C repo <args>` as bytes, or None on a non-zero exit, an
    OS error or a timeout. The timeout is `GIT_TIMEOUT_S` capped by what is left
    of the hook budget counted from `start`."""
    timeout = min(GIT_TIMEOUT_S, start + HOOK_BUDGET_S - time.monotonic())
    if timeout <= 0:
        return None
    try:
        proc = subprocess.Popen(["git", "-C", repo, *args], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.communicate(timeout=1.0)
        except Exception:  # noqa: BLE001 - the group is dead; reaping is best effort
            pass
        return None
    except Exception:  # noqa: BLE001
        return None
    return out if proc.returncode == 0 else None


def _git(repo, args, start):
    out = _git_bytes(repo, args, start)
    if out is None:
        return None
    text = out.decode("utf-8", "replace").strip()
    return text or None


def changed_files(toplevel, start):
    """Repo-relative paths changed since HEAD or untracked, sorted and capped.

    `-z` keeps git from octal-quoting a non-ASCII path. A path that is not UTF-8
    is dropped, and so is anything under `.fairmind/`, which is the plugin's own
    working directory. A repository with no HEAD contributes only its untracked
    half."""
    found = set()
    for args in (("diff", "--name-only", "-z", "HEAD"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        raw = _git_bytes(toplevel, args, start)
        if not raw:
            continue
        for chunk in raw.split(b"\0"):
            if not chunk:
                continue
            try:
                path = chunk.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if path == ".fairmind" or path.startswith(".fairmind/"):
                continue
            found.add(path)
    return sorted(found)[:MAX_FILES]


# --------------------------------------------------------------------------- #
# Classification: which answers are design decisions.
# --------------------------------------------------------------------------- #

def _labels_of(question):
    """The offered labels in order, or None when an option has no string label."""
    options = question.get("options")
    if not isinstance(options, list):
        return None
    labels = []
    for option in options:
        label = option.get("label") if isinstance(option, dict) else None
        if not isinstance(label, str):
            return None
        labels.append(label)
    return labels


def _chosen_of(answer, labels, multi):
    """The chosen labels, or None when the answer is not made of offered labels.

    A whole-answer match is tried first, so a label that itself contains the
    multi-select separator still matches."""
    if answer in labels:
        return [answer]
    if multi:
        parts = answer.split(", ")
        if parts and all(part in labels for part in parts):
            return parts
    return None


def _answered_questions(tool_input, tool_response):
    """`(text, labels, multi, answer)` for every question that has at least two
    offered labels, a non-blank answer and a design-shaped text, in order.

    Pure: no I/O. A response that is not a mapping holding a mapping of answers
    (the form a rejected question takes) yields nothing. The questions come from
    the response, falling back to the tool input. A question text that appears
    twice is skipped, because the answers are keyed by text."""
    if not isinstance(tool_response, dict) or not isinstance(tool_response.get("answers"), dict):
        return
    answers = tool_response["answers"]
    questions = tool_response.get("questions")
    if not isinstance(questions, list):
        questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list):
        return
    texts = [item.get("question") for item in questions if isinstance(item, dict)]
    for item in questions:
        if not isinstance(item, dict):
            continue
        text = item.get("question")
        if not isinstance(text, str) or texts.count(text) != 1:
            continue
        labels = _labels_of(item)
        if labels is None or len(labels) < 2:
            continue
        answer = answers.get(text)
        if not _binding.nonblank(answer):
            continue
        if _design_question.is_session_shaped(text, labels):
            continue
        yield text, labels, item.get("multiSelect") is True, answer


def kept_questions(tool_input, tool_response):
    """The questions whose answer is a design decision, each as
    `{"question", "answer", "options": [labels], "chosen": [labels]}`.

    An answer is kept only when it is made of offered labels; a typed answer is
    never kept here (see `typed_answers` for the one case it is used)."""
    kept = []
    for text, labels, multi, answer in _answered_questions(tool_input, tool_response):
        chosen = _chosen_of(answer, labels, multi)
        if chosen is None:
            continue
        kept.append({"question": text, "answer": answer, "options": labels, "chosen": chosen})
    return kept


def typed_answers(tool_input, tool_response):
    """The design-shaped questions the person answered in their own words, each
    as `{"question", "answer", "options": [labels], "chosen": []}`.

    These are NEVER recorded on their own. The caller uses one only when the
    brain answered the same question earlier in the session (an overrule), where
    the typed words are the replacement decision."""
    typed = []
    for text, labels, multi, answer in _answered_questions(tool_input, tool_response):
        if _chosen_of(answer, labels, multi) is None:
            typed.append({"question": text, "answer": answer.strip()[:TITLE_MAX],
                          "options": labels, "chosen": []})
    return typed


# --------------------------------------------------------------------------- #
# Row and batch.
# --------------------------------------------------------------------------- #

def build_row(entry, *, project, session_id, tool_use_id, ts, files):
    """One `fm-insights.decision/2` row for a kept question.

    The id is a function of the session, the tool call and the question, so the
    same exchange recorded twice is one decision. The title is the chosen label,
    which is always an offered one."""
    scope = project if _binding.is_object_id(project) else "unscoped"
    digest = hashlib.sha256(
        f"{session_id or ''}\n{tool_use_id or ''}\n{entry['question']}".encode("utf-8")
    ).hexdigest()[:16]
    chosen = set(entry["chosen"])
    return {
        "decisionId": f"decision:{scope}:answer-{digest}",
        "ts": ts,
        "agent": AGENT,
        "kind": KIND,
        "title": entry["answer"],
        "rationale": RATIONALE,
        "question": entry["question"],
        "options": [{"title": label, "rejected_because": "", "chosen": label in chosen}
                    for label in entry["options"]],
        "decided_by": DECIDED_BY,
        "origin": ORIGIN,
        "files": list(files),
        "functions": [],
    }


def build_batch(rows, *, repository, project, git_remote, session_id):
    batch = {"repository": repository}
    if _binding.is_object_id(project):
        batch["project"] = project
    if git_remote:
        batch["git_remote"] = git_remote
    if _binding.nonblank(session_id):
        batch["session_ref"] = session_id
    batch["contract_version"] = CONTRACT_VERSION
    batch["decisions"] = rows
    return batch


def build_supersede(row, old_decision_id, *, repository, project, git_remote, session_id):
    """The `Brain_supersede_decision` arguments that replace `old_decision_id`
    with `row`, because the person overruled it.

    The reason is the fixed word `overruled` (the brain's read side keys on it),
    the replacement is the same row a plain answer would have been, and nothing
    here says who acted: the brain stamps that from the key."""
    args = {"old_decision_id": old_decision_id, "reason": REASON, "new_decision": row,
            "repository": repository}
    if _binding.is_object_id(project):
        args["project"] = project
    if _binding.nonblank(session_id):
        args["session_ref"] = session_id
    if git_remote:
        args["git_remote"] = git_remote
    return args


def send_deadline(start, now):
    """The instant the send must be over: `SEND_BUDGET_S` from `now`, and never
    later than `HOOK_BUDGET_S` after the hook started."""
    return min(now + SEND_BUDGET_S, start + HOOK_BUDGET_S)


# --------------------------------------------------------------------------- #
# Output.
# --------------------------------------------------------------------------- #

def _is_success(reply):
    return (isinstance(reply, dict) and "message" not in reply
            and ("inserted" in reply or "duplicates" in reply))


def render(rows, reply, server_key, *, refused=False):
    """The hook output for a send that succeeded (`reply` is the brain's answer)
    or failed (`reply` is None; `refused` says the brain answered with a refusal
    rather than not answering).

    Success: one status line per question for the person, and the instruction for
    retiring a decision for the model. Failure: status lines only, because
    nothing was recorded for the model to act on."""
    if reply is None:
        reason = "refused it" if refused else "could not be reached"
        lines = [_hook_line.line(
            "Brain", f'your answer to "{_hook_line.clip(r["question"], _QUESTION_TEXT_LIMIT)}" '
                     f"was not recorded — the brain {reason}") for r in rows]
        return _hook_line.payload("PostToolUse", user="\n".join(lines))
    node_ids = reply.get("node_ids")
    node_ids = node_ids if isinstance(node_ids, dict) else {}
    lines, items = [], []
    for row in rows:
        node = node_ids.get(row["decisionId"])
        node = node if _binding.nonblank(node) else None
        short = _hook_line.clip(row["question"], _QUESTION_TEXT_LIMIT)
        suffix = f" ({node})" if node else ""
        lines.append(_hook_line.line("Brain", f'recorded your answer to "{short}" as a decision{suffix}'))
        where = f"(node {node})" if node else "(node id not returned by the brain)"
        items.append(f'- "{row["question"]}" → "{row["title"]}" {where}')
    text = ("Fairmind company brain: the person's answers to these design questions were "
            "recorded as taken decisions:\n" + "\n".join(items) + "\n"
            "If the person says one of them was not a design decision, retire it at once: "
            f"call mcp__{server_key}__Brain_set_status with knowledge_id set to that node, "
            'status "rejected", and reason set to the person\'s own words.')
    return _hook_line.payload("PostToolUse", user="\n".join(lines), model=text)




# --------------------------------------------------------------------------- #
# Overrules: a pick against a decision the brain answered earlier in the session.
# --------------------------------------------------------------------------- #

PLAIN, MATCH, SUPERSEDE, CONFIRM, SKIP = "plain", "match", "supersede", "confirm", "skip"
OUTCOME_OK, OUTCOME_KEPT, OUTCOME_REFUSED = "ok", "kept", "refused"
_PICK_TEXT_LIMIT = 60
_CONFIRM_QUESTION_LIMIT = 80


def same_pick(answer, chosen_option):
    """Whether the person's label answer is the choice the decision recorded:
    equal after casefolding, or, for a multi-select, the same set of parts."""
    a, b = answer.strip().casefold(), chosen_option.strip().casefold()
    if a == b:
        return True
    return {p.strip() for p in a.split(", ")} == {p.strip() for p in b.split(", ")}


def route_of(entry, memo, typed):
    """What a pick does, given what the answer hook remembered for its question.

    - no memory: a plain answer is recorded as ever; a typed one is skipped;
    - the label the decision recorded: nothing is recorded again;
    - a decision the person was only SUGGESTED: a different pick is confirmed
      with the person before anything is replaced;
    - a decision the brain answered, however the question reached the person
      (asked again, or put to them although the removal should have kept it
      away): the pick replaces it, so two live answers never stand side by
      side."""
    if memo is None:
        return SKIP if typed else PLAIN
    if not typed and same_pick(entry["answer"], memo.get("chosen_option") or ""):
        return MATCH
    if memo.get("verdict") == "suggested":
        return CONFIRM
    return SUPERSEDE


def confirmation_text(entry):
    """The yes/no question the agent is told to ask, word for word. The hook finds
    the person's answer by this text, so a paraphrase simply records nothing.
    It is session-shaped ("want me to"), so neither answer hook handles it as a
    design question."""
    pick = _hook_line.clip(entry["answer"], _PICK_TEXT_LIMIT)
    question = _hook_line.clip(entry["question"], _CONFIRM_QUESTION_LIMIT)
    return (f'Do you want me to record your choice "{pick}" as replacing the '
            f'brain\'s decision for "{question}"?')


def supersede_outcome(reply, old_decision_id):
    """`(outcome, detail)` for the reply to a supersession.

    `ok`: the successor was recorded and the old decision retired. `kept`: the
    successor landed but a warning names the old decision, which stays in force
    (a company-wide decision, another project, a person's rejection; `detail` is
    the warning). `refused`: anything else."""
    if (not isinstance(reply, dict) or "message" in reply
            or reply.get("status") != "ok"):
        return OUTCOME_REFUSED, None
    warnings = []
    for holder in (reply.get("decisions"), reply):
        found = holder.get("warnings") if isinstance(holder, dict) else None
        if isinstance(found, list):
            warnings.extend(w for w in found if isinstance(w, str))
    for warning in warnings:
        if old_decision_id and old_decision_id in warning:
            return OUTCOME_KEPT, warning
    return OUTCOME_OK, None


def _short(node):
    return node if _binding.nonblank(node) else "unknown"


def _overrule_lines(outcome, node, detail=None):
    if outcome == OUTCOME_OK:
        return f"recorded your choice as replacing decision {node} (overruled)"
    if outcome == OUTCOME_KEPT:
        return f"your choice was recorded, but decision {node} stays in force: {detail}"
    if outcome == "unreachable":
        return f"your choice was not recorded — the brain could not be reached; decision {node} stays in force"
    if outcome == "off":
        return (f"your choice was not recorded — brain write-back is off; "
                f"decision {node} stays in force")
    if outcome == "noproject":
        return f"your choice was not recorded — no project is bound here; decision {node} stays in force"
    if outcome == "unprepared":
        return f"your choice was not recorded — the confirmation could not be prepared; decision {node} stays in force"
    if outcome == "noid":
        return f"your choice was not recorded — the brain gave no id for decision {node}; it stays in force"
    return f"your choice was not recorded — the brain refused it; decision {node} stays in force"


def _overrule_model_text(outcome, entry, node, detail=None):
    pick = f'"{entry["answer"]}"' if entry.get("answer") else "the person's choice"
    if outcome == OUTCOME_OK:
        return (f"Fairmind company brain: the person overruled the brain's answer; their choice {pick} "
                f"was recorded as replacing decision {node} (reason: overruled). The earlier decision "
                "is kept for history and no longer answers questions. Record nothing further about it.")
    if outcome == OUTCOME_KEPT:
        return (f"Fairmind company brain: the person's choice {pick} was recorded, but decision {node} "
                f"stays in force: {detail} Two decisions now answer this question until someone allowed "
                "to retire the earlier one does; tell the person.")
    return (f"Fairmind company brain: the person's choice {pick} was NOT recorded, so decision {node} "
            "stays in force for future sessions. Tell the person; do not record it yourself unless "
            "they ask.")


def _confirm_model_text(entry, node, text):
    return (f'Fairmind company brain: the person picked "{entry["answer"]}" for a question the brain '
            f'suggested an answer to (confirmed decision, node {node}). Ask the person once, with '
            "AskUserQuestion, this question word for word and with exactly the two options Yes and "
            f"No: {text}\n"
            "Do not record anything yourself and do not act on the answer: the replacement is made "
            "only if the person answers Yes, and by the hook.")


def _merge(parts):
    """One hook payload from several: status lines joined, model texts joined."""
    users = [p["systemMessage"] for p in parts if p.get("systemMessage")]
    models = [p["hookSpecificOutput"]["additionalContext"] for p in parts
              if p.get("hookSpecificOutput", {}).get("additionalContext")]
    return _hook_line.payload("PostToolUse", user="\n".join(users),
                              model="\n\n".join(models))


def _confirmations(tool_response, session_id):
    """`(question text, answer, entry)` for every answer to a confirmation
    question this session's hook dictated."""
    answers = tool_response.get("answers") if isinstance(tool_response, dict) else None
    if not isinstance(answers, dict):
        return []
    found = []
    for text, answer in answers.items():
        if not isinstance(text, str) or not isinstance(answer, str):
            continue
        entry = _answer_memory.read(session_id, text)
        if entry is not None and entry.get("kind") == _answer_memory.KIND_CONFIRM:
            found.append((text, answer, entry))
    return found


def _memo(session_id, question):
    entry = _answer_memory.read(session_id, question)
    if entry is None or entry.get("kind") == _answer_memory.KIND_CONFIRM:
        return None
    return entry


def classify(entries, typed, session_id):
    """`[{"route", "entry", "memo"}]` for the kept and the typed answers."""
    items = []
    for typed_flag, group in ((False, entries), (True, typed)):
        for entry in group:
            memo = _memo(session_id, entry["question"])
            if typed_flag and memo is None:
                continue
            items.append({"route": route_of(entry, memo, typed_flag), "entry": entry, "memo": memo})
    return items


# --------------------------------------------------------------------------- #
# Flow.
# --------------------------------------------------------------------------- #

def _send(server, batch, start):
    """`(reply, refused)`: the brain's success reply, else `(None, refused)`.

    A spent deadline is refused by `call_tool` itself and reads as any other
    transport failure."""
    import _brain_mcp  # noqa: PLC0415 - its HTTPS opener is built at import
    deadline = send_deadline(start, time.monotonic())
    try:
        reply = _brain_mcp.call_tool(server, "Brain_record_decision", batch, deadline=deadline)
    except Exception:  # noqa: BLE001 - every transport failure reads the same
        return None, False
    if _is_success(reply):
        return reply, False
    return None, isinstance(reply, dict) and "message" in reply


def _supersede(server, args, start):
    """`(reply, reached)`: the brain's reply to a supersession, and whether the
    brain was reached at all."""
    import _brain_mcp  # noqa: PLC0415
    deadline = send_deadline(start, time.monotonic())
    try:
        return _brain_mcp.call_tool(server, "Brain_supersede_decision", args, deadline=deadline), True
    except Exception:  # noqa: BLE001
        return None, False


def _replace(server, args, entry, node, session_id, question, start, node_id=None):
    """Send one supersession and report it: `(status line, model text)`. The
    memory entry of the question is dropped only when the old decision is
    actually retired, so a refused replacement keeps holding the brain back from
    answering the question with the decision the person overruled."""
    reply, reached = _supersede(server, args, start)
    if not reached:
        outcome, detail = "unreachable", None
    else:
        outcome, detail = supersede_outcome(reply, args.get("old_decision_id"))
    if outcome == OUTCOME_OK:
        _answer_memory.remove(session_id, question)
        _answer_memory.remove_by_node(session_id, node_id)
    return (_hook_line.line("Brain", _overrule_lines(outcome, node, detail)),
            _overrule_model_text(outcome, entry, node, detail))


def run(payload):
    """The hook's output object, or None for silence.

    Before any answer is classified as a design decision an exception propagates
    (`main` turns it into silence); after it, one is reported as the documented
    not-recorded line per question."""
    start = time.monotonic()
    if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
        return None
    tool_input, tool_response = payload.get("tool_input"), payload.get("tool_response")
    session_id = payload.get("session_id")
    entries = kept_questions(tool_input, tool_response)
    # A typed answer is considered only where the brain answered the question
    # earlier in this session; everywhere else it never leaves the machine.
    typed = [t for t in typed_answers(tool_input, tool_response)
             if _memo(session_id, t["question"]) is not None]
    confirmations = _confirmations(tool_response, session_id)
    if not entries and not typed and not confirmations:
        return None
    try:
        return _record(payload, entries, typed, confirmations, start)
    except Exception:  # noqa: BLE001 - the person is told, never left guessing
        return render(entries + typed, None, None)


def _record(payload, entries, typed, confirmations, start):
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else os.getcwd()
    toplevel = _git(cwd, ("rev-parse", "--show-toplevel"), start)
    if not toplevel:
        return None
    session_id = payload.get("session_id")
    items = classify(entries, typed, session_id)
    plain = [i["entry"] for i in items if i["route"] == PLAIN]
    matches = [i for i in items if i["route"] == MATCH]
    replaces = [i for i in items if i["route"] == SUPERSEDE]
    asks = [i for i in items if i["route"] == CONFIRM]

    parts = []
    for item in matches:
        _answer_memory.remove(session_id, item["entry"]["question"])
        parts.append(_hook_line.payload("PostToolUse", user=_hook_line.line(
            "Brain", f"matches the brain's decision ({_short(item['memo']['node_id'])}) — not recorded again")))
    yes = []
    for text, answer, entry in confirmations:
        _answer_memory.remove(session_id, text)
        if answer.strip().casefold() == "yes":
            yes.append(entry)
    if not (plain or replaces or asks or yes):
        return _merge(parts) or None

    # Imported here: the module is the heaviest thing this hook touches, and the
    # cheap gates above decide most calls without it.
    import insights_flush_payload  # noqa: PLC0415
    overruled = [(i["entry"], i["memo"]) for i in replaces + asks]
    overruled += [({"answer": e["args"]["new_decision"].get("title", "")}, {"node_id": e.get("old_node")})
                  for e in yes if isinstance(e["args"].get("new_decision"), dict)]
    if insights_flush_payload.brain_is_disabled(cwd, toplevel=toplevel):
        return _merge(parts + [_blocked(entry, memo, "off") for entry, memo in overruled]) or None
    server = _mcp_config.fairmind_server(cwd, toplevel)
    if server is None:
        return _merge(parts) or None

    # A decision recorded without a verified project would belong to the whole
    # company rather than to this checkout's project, so nothing is sent.
    ctx = _binding.read_context(toplevel)
    project = _binding.project_id(ctx)
    if not project:
        return _merge(parts + [_blocked(entry, memo, "noproject") for entry, memo in overruled]) or None
    git_remote = audit_run_meta.normalize_git_remote(
        _git(toplevel, ("remote", "get-url", "origin"), start))
    repository = _binding.repository_id(ctx, git_remote) or os.path.basename(toplevel)
    tool_use_id = payload.get("tool_use_id")
    tool_use_id = tool_use_id if isinstance(tool_use_id, str) else ""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = changed_files(toplevel, start) if (plain or replaces or asks) else []

    def row_of(entry):
        return build_row(entry, project=project, session_id=session_id,
                         tool_use_id=tool_use_id, ts=ts, files=files)

    for item in replaces:
        entry, memo = item["entry"], item["memo"]
        node = _short(memo["node_id"])
        if not _binding.nonblank(memo.get("decision_id")):
            parts.append(_blocked(entry, memo, "noid"))
            continue
        args = build_supersede(row_of(entry), memo["decision_id"], repository=repository,
                               project=project, git_remote=git_remote, session_id=session_id)
        line, model = _replace(server, args, entry, node, session_id, entry["question"], start,
                            node_id=memo["node_id"])
        parts.append(_hook_line.payload("PostToolUse", user=line, model=model))
    for item in asks:
        entry, memo = item["entry"], item["memo"]
        node = _short(memo["node_id"])
        if not _binding.nonblank(memo.get("decision_id")):
            parts.append(_blocked(entry, memo, "noid"))
            continue
        args = build_supersede(row_of(entry), memo["decision_id"], repository=repository,
                               project=project, git_remote=git_remote, session_id=session_id)
        text = confirmation_text(entry)
        stored = _answer_memory.write(session_id, text, {
            "kind": _answer_memory.KIND_CONFIRM, "args": args, "old_node": memo["node_id"]})
        if not stored:
            parts.append(_blocked(entry, memo, "unprepared"))
            continue
        _answer_memory.remove(session_id, entry["question"])
        parts.append(_hook_line.payload(
            "PostToolUse",
            user=_hook_line.line("Brain", f"your choice differs from the brain's decision ({node}) — "
                                          "you will be asked whether to replace it"),
            model=_confirm_model_text(entry, node, text)))
    for entry in yes:
        args = entry["args"]
        node = _short(entry.get("old_node"))
        shown = {"answer": args["new_decision"].get("title", "")} if isinstance(args.get("new_decision"), dict) else {}
        line, model = _replace(server, args, shown, node, session_id, args.get("new_decision", {}).get("question", ""), start,
                            node_id=entry.get("old_node"))
        parts.append(_hook_line.payload("PostToolUse", user=line, model=model))

    if plain:
        rows = [row_of(entry) for entry in plain]
        batch = build_batch(rows, repository=repository, project=project,
                            git_remote=git_remote, session_id=session_id)
        reply, refused = _send(server, batch, start)
        parts.append(render(rows, reply, server["key"], refused=refused))
    return _merge(parts) or None


def _blocked(entry, memo, why):
    """The payload for an overrule that was not sent, and why."""
    node = _short(memo.get("node_id"))
    return _hook_line.payload("PostToolUse", user=_hook_line.line("Brain", _overrule_lines(why, node)),
                              model=_overrule_model_text(why, entry, node))


def main():
    out = None
    try:
        raw = sys.stdin.read()
        out = run(json.loads(raw)) if raw.strip() else None
    except Exception:  # noqa: BLE001 - a hook never raises into the harness
        out = None
    if out:
        try:
            sys.stdout.write(json.dumps(out) + "\n")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
