#!/usr/bin/env python3
"""PostToolUse hook on `AskUserQuestion`: tell the company brain a person's
answer to a design question as a decision, and turn an overrule of a brain
answer into the replacement of that decision.

When the person picks one of the options the agent offered, this hook sends one
`Brain_tell` report per answered question: the question, the offered option
LABELS (never their descriptions) and the chosen label, as a taken decision
under the person's own key. Only a design choice counts: a question about the
session itself ("continue?", "which file?") is never sent, and neither is a
typed answer to an ordinary question.

OVERRULES. The answer hook (`brain_answer_hook`) remembers, per session, every
question a confirmed decision answered. When one of those questions is asked
again and the person picks something else, the pick is sent as the new decision
with `replaces` naming the earlier one and the reason `overruled`, and not also
as a plain decision (two successors would leave two live answers). The brain
answers what became of the earlier decision (`replaces_outcome`) and the hook
says exactly that: replaced, filed as a proposal, kept, or not replaced. A pick
that equals the brain's own choice records nothing. In `suggest` mode the person
saw a suggestion rather than an answer, so a different pick is not replaced on
the spot: the hook dictates a yes/no confirmation question to the agent and
sends the replacement only on the person's yes (the question is session-shaped,
so neither hook sends or records it). Of two hooks that read the same
confirmation, only the one whose removal of it succeeded sends.

WHAT LEAVES THE MACHINE. The question, the offered option labels and the chosen
label; the question text may name a file, but no file of the checkout is
attached. A design question is answered before the code it decides is written,
so no file of the checkout is evidence of the decision, and anchoring it to the
dirty working tree would make the brain cite it as the reason for files it has
nothing to do with. The title is the question and the pick joined (the brain
keys a decision on its title). A typed ("Other") answer is the person's own
words and is skipped whole EXCEPT when it answers a question the brain answered
earlier in the session: then, and only then, it is the pick of the replacement
decision (cut at 256 characters), because the typed words are the new decision.
Option descriptions, notes and annotations are not read at all.

THE FAIL DIRECTION IS A LINE, NOT A BLOCK. Any failure after the answer was
classified as a design decision ends with one status line per question saying it
was not recorded (or not confirmed, when the brain did not answer), and exit 0;
before that point it ends silent. The hook never asks for or changes a
permission and never alters the tool result. A replacement the brain refused, or
could not make, says so: the old decision stays in force. A send that gets no
answer ends the run: the remaining answers are not tried, so a stalled brain
holds the hook for one send, not for one per question.

TIME. The harness kills a hook at its registered timeout and a killed hook says
nothing, so the whole run is held under `HOOK_BUDGET_S`: every git call carries a
timeout capped by the time left, every send deadline is capped at the hook's own
start plus that budget, and an answer that finds the budget spent is not sent.

The `brain` switch governs it, exactly as it governs the loop's brain write-back;
with it off an overrule is not sent and a line says the earlier decision stays in
force. Stdlib plus small leaf modules.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _answer_memory  # noqa: E402
import _binding  # noqa: E402
import _design_question  # noqa: E402
import _hook_line  # noqa: E402
import _mcp_config  # noqa: E402
import _tell_report  # noqa: E402
import audit_run_meta  # noqa: E402

#: The send alone, from the moment it starts.
SEND_BUDGET_S = 5.0
#: The whole hook, from its entry. Under the 10 s the hook is registered with.
HOOK_BUDGET_S = 8.0
#: One local git call.
GIT_TIMEOUT_S = 1.0
AGENT = "ask-user-question"
RATIONALE = "Chosen by the person in answer to a design question in the session."
DECIDED_BY = "human"
ORIGIN = "taken"

_QUESTION_TEXT_LIMIT = 40
#: A longer typed answer is cut to this many characters before it joins the
#: question in the title.
TITLE_MAX = 256
REASON = "overruled"


# --------------------------------------------------------------------------- #
# Time-boxed git. A git process that stalls (a credential helper, a network
# filesystem) must not hold a hook whose budget the harness enforces by killing
# it, so every call runs in its own process group and the whole group dies at
# the timeout: killing only `git` leaves a grandchild holding the pipe open.
# --------------------------------------------------------------------------- #

def _git(repo, args, start):
    """Stripped stdout of `git -C repo <args>`, or None when it is empty, on a
    non-zero exit, an OS error or a timeout. The timeout is `GIT_TIMEOUT_S`
    capped by what is left of the hook budget counted from `start`."""
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
    if proc.returncode != 0:
        return None
    return out.decode("utf-8", "replace").strip() or None


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
# The report.
# --------------------------------------------------------------------------- #

def build_report(entry, *, repository, project, git_remote, session_id):
    """The `Brain_tell` report for a kept question, or None when no title the
    brain could key can be made of the question and the pick.

    The title is the question and the pick, so the same exchange told twice is
    one decision (the brain answers the second `duplicate`) and a question asked
    again with another pick is another. `files` and `functions` are never set
    (see WHAT LEAVES THE MACHINE). Nothing here says who acted: the brain stamps
    that from the key."""
    title = _tell_report.fit_pair(entry["question"], " \u2192 ", entry["answer"],
                                  slug_max=_tell_report.KEY_SLUG_MAX)
    if title is None:
        return None
    chosen = set(entry["chosen"])
    options = []
    for label in entry["options"]:
        folded = _tell_report.fit_text(_tell_report.one_line(label), _tell_report.TITLE_MAX)
        if folded:
            options.append({"title": folded, "chosen": label in chosen})
    report = {"intent": "decision",
              "repository": _tell_report.fit_ref(repository) or repository}
    if _binding.is_object_id(project):
        report["project"] = project
    if git_remote:
        report["git_remote"] = git_remote
    report.update({
        "title": title,
        "summary": RATIONALE,
        "question": _tell_report.fit_text(entry["question"], _tell_report.TEXT_MAX),
        "options": options,
        "decided_by": DECIDED_BY,
        "origin": ORIGIN,
        "agent": AGENT,
    })
    ref = _tell_report.fit_ref(session_id) if _binding.nonblank(session_id) else None
    if ref:
        report["session_ref"] = ref
    return report


def build_replacement(entry, old_node, *, repository, project, git_remote, session_id):
    """The report that replaces the decision `old_node` with `entry`, because
    the person overruled it, or None as `build_report`. The reason is the fixed
    word `overruled` (the brain's read side keys on it)."""
    report = build_report(entry, repository=repository, project=project,
                          git_remote=git_remote, session_id=session_id)
    if report is not None:
        report["replaces"] = {"node_id": old_node, "reason": REASON}
    return report


def send_deadline(start, now):
    """The instant the send must be over: `SEND_BUDGET_S` from `now`, and never
    later than `HOOK_BUDGET_S` after the hook started."""
    return min(now + SEND_BUDGET_S, start + HOOK_BUDGET_S)


# --------------------------------------------------------------------------- #
# Output.
# --------------------------------------------------------------------------- #

_PLAIN_CLAUSES = {
    "conflict": "was not recorded \u2014 another decision has that title",
    "retry": "was not recorded \u2014 the brain could not finish recording it",
    "throttled": "was not recorded \u2014 today's write budget is spent",
    "not_accepted": "was not recorded \u2014 the brain did not accept the call",
    "refused": "was not recorded \u2014 the brain refused it",
    "unreachable": "was not confirmed \u2014 the brain did not answer",
    "untimed": "was not recorded \u2014 the hook ran out of time",
    "untitled": "was not recorded \u2014 it has no title the brain can use",
    "failed": "was not recorded \u2014 the hook failed",
}


def plain_outcome(reply):
    """The outcome of a plain decision's answer: `written`, `duplicate`,
    `conflict`, `retry`, `throttled`, `not_accepted` (the plugin and the brain
    disagree on the report's shape) or `refused` (everything else, an answer of
    no known shape included, which must never read as a recorded one)."""
    outcome = _tell_report.tell_outcome(reply)
    if outcome == "written":
        return "written"
    if outcome in ("duplicate", "conflict", "retry", "throttled"):
        return outcome
    if outcome == "mismatch":
        return "not_accepted"
    return "refused"


def _node_of(reply):
    node = reply.get("node_id") if isinstance(reply, dict) else None
    return node if _binding.nonblank(node) else None


def _plain_line(question, outcome, node=None):
    short = _hook_line.clip(question, _QUESTION_TEXT_LIMIT)
    suffix = f" ({node})" if node else ""
    if outcome == "written":
        return _hook_line.line("Brain", f'recorded your answer to "{short}" as a decision{suffix}')
    if outcome == "duplicate":
        return _hook_line.line("Brain", f'your answer to "{short}" was already recorded{suffix}')
    return _hook_line.line("Brain", f'your answer to "{short}" {_PLAIN_CLAUSES[outcome]}')


def render(results, server_key, repository):
    """The hook output for the plain answers. `results` is
    `[(entry, outcome, node)]` in the order they were told.

    One status line per question for the person. For the model, the answers the
    brain holds (recorded now, or already there) and the instruction for
    retracting one; a failure adds nothing for the model to act on."""
    lines = [_plain_line(entry["question"], outcome, node) for entry, outcome, node in results]
    recorded = [(entry, node) for entry, outcome, node in results if outcome == "written"]
    held = [(entry, node) for entry, outcome, node in results if outcome == "duplicate"]
    if not (recorded or held):
        return _hook_line.payload("PostToolUse", user="\n".join(lines))
    text = ""
    if recorded:
        items = []
        for entry, node in recorded:
            where = f"(node {node})" if node else "(node id not returned by the brain)"
            items.append(f'- "{entry["question"]}" \u2192 "{entry["answer"]}" {where}')
        text = ("Fairmind company brain: the person's answers to these design questions were "
                "recorded as taken decisions:\n" + "\n".join(items) + "\n")
    if held:
        items = [f'- "{entry["question"]}" \u2192 "{entry["answer"]}"'
                 + (f" (node {node})" if node else "") for entry, node in held]
        text += ("Fairmind company brain: the brain already held the person's answers to these "
                 "design questions; a decision that has been replaced or retracted since stays so:\n"
                 + "\n".join(items) + "\n")
    text += ("If the person says one of them was not a design decision, retract it at once: call "
             f'mcp__{server_key}__Brain_tell with intent "decision", repository "{repository}", and '
             "retract set to an object holding node_id (that node) and reason (the person's own words).")
    return _hook_line.payload("PostToolUse", user="\n".join(lines), model=text)


def render_failed(entries):
    """Status lines for answers the hook failed to tell, and nothing for the model."""
    lines = [_hook_line.line("Brain", f'your answer to "{_hook_line.clip(e["question"], _QUESTION_TEXT_LIMIT)}" '
                                      f'{_PLAIN_CLAUSES["failed"]}') for e in entries]
    return _hook_line.payload("PostToolUse", user="\n".join(lines))


# --------------------------------------------------------------------------- #
# Overrules: a pick against a decision the brain answered earlier in the session.
# --------------------------------------------------------------------------- #

PLAIN, MATCH, SUPERSEDE, CONFIRM, SKIP = "plain", "match", "supersede", "confirm", "skip"
OUTCOME_OK = "ok"
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


def replace_outcome(reply):
    """`(outcome, detail)` for the answer to a replacing report.

    `ok`: the new decision was recorded and the old one superseded. `proposed`:
    the replacement was filed as a proposal and the old decision stays in force.
    `kept`: the new decision landed and the brain did not replace the old one.
    `not_replaced`: the new decision landed (or the brain already held it) and
    the brain says nothing replaced the old one; `detail` is `duplicate` when it
    already held it. `conflict`, `retry`, `throttled`, `not_accepted` and
    `refused` are the plain outcomes of the report that was not recorded."""
    outcome = plain_outcome(reply)
    if outcome not in ("written", "duplicate"):
        return outcome, None
    detail = "duplicate" if outcome == "duplicate" else None
    said = reply.get("replaces_outcome")
    if said == "superseded":
        return OUTCOME_OK, detail
    if said in ("proposed", "kept"):
        return said, detail
    return "not_replaced", detail


def _short(node):
    return _hook_line.clip(node, 32) if _binding.nonblank(node) else "unknown"


def _is_node_id(value):
    return (isinstance(value, str) and len(value) == 32
            and all(c in "0123456789abcdef" for c in value))


_OVERRULE_CLAUSES = {
    "conflict": "not recorded: another decision has that title",
    "retry": "the brain could not finish recording your choice",
    "throttled": "not recorded: today's write budget is spent",
    "not_accepted": "not recorded: the brain did not accept the call",
    "refused": "not recorded: the brain refused it",
}


def _overrule_lines(outcome, node, detail=None):
    if outcome == OUTCOME_OK:
        return f"recorded your choice as replacing decision {node} (overruled)"
    if outcome == "proposed":
        return f"decision {node} stays in force \u2014 your choice was filed as a proposal to replace it"
    if outcome == "kept":
        return f"your choice was recorded, but the brain did not replace decision {node} with it"
    if outcome == "not_replaced":
        if detail == "duplicate":
            return (f"decision {node} was not replaced \u2014 the brain already holds your choice "
                    "as another decision")
        return f"decision {node} was not replaced \u2014 your choice was recorded, but not as its replacement"
    if outcome in _OVERRULE_CLAUSES:
        return f"decision {node} was not replaced \u2014 {_OVERRULE_CLAUSES[outcome]}"
    if outcome == "unreachable":
        return f"decision {node} may still be in force \u2014 the brain did not answer, so nothing is confirmed"
    if outcome == "off":
        return (f"your choice was not recorded \u2014 brain write-back is off; "
                f"decision {node} stays in force")
    if outcome == "noproject":
        return f"your choice was not recorded \u2014 no project is bound here; decision {node} stays in force"
    if outcome == "unprepared":
        return f"your choice was not recorded \u2014 the confirmation is unusable; decision {node} stays in force"
    if outcome == "noid":
        return f"your choice was not recorded \u2014 no usable id for decision {node}; it stays in force"
    if outcome == "untimed":
        return f"your choice was not recorded \u2014 the hook ran out of time; decision {node} stays in force"
    if outcome == "untitled":
        return f"your choice was not recorded \u2014 it has no usable title; decision {node} stays in force"
    return f"your choice was not recorded \u2014 the brain refused it; decision {node} stays in force"


def _overrule_model_text(outcome, entry, node):
    pick = f'"{entry["answer"]}"' if entry.get("answer") else "the person's choice"
    head = "Fairmind company brain: "
    if outcome == OUTCOME_OK:
        return (f"{head}the person overruled the brain's answer; their choice {pick} "
                f"was recorded as replacing decision {node} (reason: overruled). The earlier decision "
                "is kept for history and no longer answers questions. Record nothing further about it.")
    if outcome == "proposed":
        return (f"{head}the person's choice {pick} was filed as a proposal to replace decision {node}, "
                "which stays in force until someone allowed to confirm the proposal does. Tell the person.")
    if outcome == "kept":
        return (f"{head}the person's choice {pick} was recorded, but the brain did not replace "
                f"decision {node}. Two decisions now answer this question until someone allowed "
                "to retire the earlier one does; tell the person.")
    if outcome == "not_replaced":
        return (f"{head}the person's choice {pick} is held by the brain, but not as the replacement of "
                f"decision {node}, which stays in force for future sessions. Tell the person; do not "
                "record it yourself unless they ask.")
    if outcome == "unreachable":
        return (f"{head}the brain did not answer, so it is not confirmed that the person's choice {pick} "
                f"was recorded; decision {node} may still be in force for future sessions. Tell the person; "
                "do not record it yourself unless they ask.")
    return (f"{head}the person's choice {pick} was NOT recorded, so decision {node} "
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

def _untimed(start):
    """Whether the hook's own budget, counted from `start`, is spent."""
    return time.monotonic() >= start + HOOK_BUDGET_S


def _tell(server, report, start, down):
    """`(reply, failure)`: the brain's answer to one report, else `(None,
    failure)` with `failure` one of `untimed` (no budget left: nothing was
    sent), `not_accepted` (an MCP error result: the brain answered the call
    with an error) and `unreachable` (no answer: a deadline, an HTTP status, a
    body that cannot be read). `down` is shared by a run: once a send got no
    answer the rest are not tried, so a stalled brain holds the hook for one
    send."""
    import _brain_mcp  # noqa: PLC0415 - its HTTPS opener is built at import
    if down:
        return None, "unreachable"
    if _untimed(start):
        return None, "untimed"
    deadline = send_deadline(start, time.monotonic())
    try:
        return _brain_mcp.call_tool(server, "Brain_tell", report, deadline=deadline), None
    except _brain_mcp.BrainToolError:
        return None, "not_accepted"
    except Exception:  # noqa: BLE001 - every transport failure reads the same
        down.append(True)
        return None, "unreachable"


def _replace(server, report, entry, node, session_id, question, start, down, node_id=None):
    """Send one replacing report and say what became of it: `(status line, model
    text)`. The memory entry of the question is dropped only when the old
    decision is actually superseded, so any other outcome keeps holding the
    brain back from answering the question with the decision the person
    overruled."""
    reply, failure = _tell(server, report, start, down)
    if failure:
        outcome, detail = failure, None
    else:
        outcome, detail = replace_outcome(reply)
    if outcome == OUTCOME_OK:
        _answer_memory.remove(session_id, question)
        _answer_memory.remove_by_node(session_id, node_id)
    return (_hook_line.line("Brain", _overrule_lines(outcome, node, detail)),
            _overrule_model_text(outcome, entry, node))


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
        return render_failed(entries + typed)


def _prepared(entry):
    """The report a confirmation prepared, or None when it was prepared under
    another shape (it carries no `intent`, or names no node to replace)."""
    args = entry.get("args")
    if (isinstance(args, dict) and args.get("intent") == "decision"
            and isinstance(args.get("replaces"), dict)
            and _is_node_id(args["replaces"].get("node_id"))):
        return args
    return None


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
            "Brain", f"matches the brain's decision ({_short(item['memo']['node_id'])}) \u2014 not recorded again")))
    yes = []
    for text, answer, entry in confirmations:
        # The removal is the claim: of two hooks that read this confirmation
        # only the one whose delete succeeded may send what it prepared.
        if _answer_memory.remove(session_id, text) and answer.strip().casefold() == "yes":
            yes.append(entry)
    if not (plain or replaces or asks or yes):
        return _merge(parts) or None

    # Imported here: the module is the heaviest thing this hook touches, and the
    # cheap gates above decide most calls without it.
    import insights_flush_payload  # noqa: PLC0415
    overruled = [(i["entry"], i["memo"]) for i in replaces + asks]
    overruled += [({"answer": e.get("answer", "")}, {"node_id": e.get("old_node")}) for e in yes]
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
    down = []

    def replacement_of(entry, memo):
        return build_replacement(entry, memo["node_id"], repository=repository, project=project,
                                 git_remote=git_remote, session_id=session_id)

    for item in replaces:
        entry, memo = item["entry"], item["memo"]
        node = _short(memo["node_id"])
        if not _is_node_id(memo["node_id"]):
            parts.append(_blocked(entry, memo, "noid"))
            continue
        report = replacement_of(entry, memo)
        if report is None:
            parts.append(_blocked(entry, memo, "untitled"))
            continue
        line, model = _replace(server, report, entry, node, session_id, entry["question"], start, down,
                               node_id=memo["node_id"])
        parts.append(_hook_line.payload("PostToolUse", user=line, model=model))
    for item in asks:
        entry, memo = item["entry"], item["memo"]
        node = _short(memo["node_id"])
        if not _is_node_id(memo["node_id"]):
            parts.append(_blocked(entry, memo, "noid"))
            continue
        report = replacement_of(entry, memo)
        if report is None:
            parts.append(_blocked(entry, memo, "untitled"))
            continue
        text = confirmation_text(entry)
        stored = _answer_memory.write(session_id, text, {
            "kind": _answer_memory.KIND_CONFIRM, "args": report, "old_node": memo["node_id"],
            "answer": entry["answer"]})
        if not stored:
            parts.append(_blocked(entry, memo, "unprepared"))
            continue
        _answer_memory.remove(session_id, entry["question"])
        parts.append(_hook_line.payload(
            "PostToolUse",
            user=_hook_line.line("Brain", f"your choice differs from the brain's decision ({node}) \u2014 "
                                          "you will be asked whether to replace it"),
            model=_confirm_model_text(entry, node, text)))
    for entry in yes:
        report = _prepared(entry)
        shown = {"answer": entry.get("answer", "")}
        node = _short(entry.get("old_node"))
        if report is None:
            parts.append(_blocked(shown, {"node_id": entry.get("old_node")}, "unprepared"))
            continue
        line, model = _replace(server, report, shown, node, session_id, report.get("question", ""), start,
                               down, node_id=entry.get("old_node"))
        parts.append(_hook_line.payload("PostToolUse", user=line, model=model))

    results = []
    for entry in plain:
        report = build_report(entry, repository=repository, project=project,
                              git_remote=git_remote, session_id=session_id)
        if report is None:
            results.append((entry, "untitled", None))
            continue
        reply, failure = _tell(server, report, start, down)
        if failure:
            results.append((entry, failure, None))
        else:
            results.append((entry, plain_outcome(reply), _node_of(reply)))
    if results:
        parts.append(render(results, server["key"], report_repository(repository)))
    return _merge(parts) or None


def report_repository(repository):
    """The repository value as the report carries it."""
    return _tell_report.fit_ref(repository) or repository


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
