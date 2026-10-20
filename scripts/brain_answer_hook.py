#!/usr/bin/env python3
"""PreToolUse hook on `AskUserQuestion`: ask the company brain before the person.

Before the agent puts questions to the person, this hook sends them (the
question texts and the option LABELS, never the descriptions) to the brain's
`Brain_answer` tool. In `answer` mode a question a confirmed, taken decision
already answers is removed from the call and disclosed in one line; in `suggest`
mode no question is removed and the brain's choice is only placed first as the
suggested option. Every other question is asked exactly as before.

THE FAIL DIRECTION IS ASKING. Any exception, at any step, ends with no output
and exit 0, and the tool then runs unchanged. The hook is off unless the
`answer_from_brain` setting names a mode (`_plugin_policy.answer_from_brain_mode`),
and it makes no network call unless a Fairmind MCP entry with a URL is
configured for the project.

WHAT IT REMEMBERS (`_answer_memory`). Every question a confirmed, taken decision
answered or was suggested against is written down for the session. The same
question asked again, or a reworded one the same decision answers, is asked
unchanged and its entry is marked pending. The record hook
(`brain_record_answer_hook`) reads the person's pick as a replacement of that
decision for any remembered question the brain answered, pending or not. A question is removed only when
its entry was stored: a session with no id, or a store that cannot be written,
degrades to `suggest`, because a question the brain removed and cannot
remember is one it would answer again.

WHAT IT NEVER DOES: grant. The partial case returns `updatedInput` with NO
`permissionDecision`, so the remaining questions still reach the person through
the normal permission path; the all-answered case returns `deny` with the
answers as the reason. The grant spelling of a permission decision appears
nowhere in this file, and a static check in the hook test suite keeps it that
way.

A question is removed ONLY when the answering decision is both `confirmed` and
`taken` AND its recorded choice is one of the labels the question offers. The
rule is checked here as well as on the server, because a proposal silently
taking a decision away from the person is the one outcome this feature must not
produce, whatever a server returns. An answer whose choice is not an offered
label (the server falls back to the decision's title) cannot be handed to the
agent as the person's pick, so it is asked like a suggestion.

Questions about the session itself ("continue?", "which file?", yes/no, path
options; see `_design_question`) are never sent to the brain and never removed.

Stdlib plus small leaf modules; it imports none of the delivery lanes (pinned by
test_the_hook_stays_light).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
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
import _plugin_policy  # noqa: E402
import audit_run_meta  # noqa: E402

__all__ = ["main"]

#: The whole hook, from its entry: git, the three requests and the output.
_BUDGET_S = 5.0

_SUGGESTED_PREFIX = ("Suggested by the company brain "
                     "(proposed decision, not confirmed): ")
_CONFIRMED_PREFIX = "Suggested by the company brain (decision recorded {date}): "
_ASKED_AGAIN = "asked again — you overruled the brain's answer earlier this session"
_MODES = _plugin_policy.ANSWER_MODES
_PERSON_WINS = ("If the person was asked one of these questions anyway, "
                "their own answer wins over the brain's.")
_COUNTER_MAX_AGE_S = 7 * 24 * 3600
_COUNTER_MAX_PRUNE = 200
_TEXT_LIMIT = 40


# --------------------------------------------------------------------------- #
# Small readers.
# --------------------------------------------------------------------------- #

def _git(args, cwd, deadline):
    """One local git call under the remaining budget, stdout or None.

    The floor keeps a spent budget from passing a non-positive timeout; these
    are local calls, not network sends."""
    timeout = max(0.05, deadline - time.monotonic())
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _toplevel(cwd, deadline):
    return _git(["rev-parse", "--show-toplevel"], cwd, deadline)


def _repository_id(ctx, toplevel, deadline):
    """The catalog id of the bound repository, only while the binding still
    describes this checkout's origin. An origin that cannot be read or
    normalized omits the id: `repository_id` given no remote skips its
    staleness check, and "I could not look" must not send a possibly stale id."""
    if not _binding.is_bound(ctx):
        return None
    remote = audit_run_meta.normalize_git_remote(
        _git(["remote", "get-url", "origin"], toplevel, deadline))
    if not remote:
        return None
    return _binding.repository_id(ctx, remote)


# --------------------------------------------------------------------------- #
# The reply.
# --------------------------------------------------------------------------- #

def _valid_reply(reply, questions):
    """The verdict list when `reply` is aligned with `questions`, else None."""
    if not isinstance(reply, dict) or reply.get("status") != "ok":
        return None
    verdicts = reply.get("verdicts")
    if not isinstance(verdicts, list) or len(verdicts) != len(questions):
        return None
    for i, verdict in enumerate(verdicts):
        if not isinstance(verdict, dict):
            return None
        index = verdict.get("index")
        if isinstance(index, bool) or index != i:
            return None
        if verdict.get("question") != questions[i]["question"].strip():
            return None
        kind = verdict.get("verdict")
        if kind not in ("answered", "suggested", "unanswered"):
            return None
        if kind != "unanswered":
            decision = verdict.get("decision")
            if (not isinstance(decision, dict)
                    or not isinstance(decision.get("node_id"), str)
                    or not isinstance(decision.get("title"), str)):
                return None
    return verdicts


def _labels_of(question):
    options = question.get("options")
    if not isinstance(options, list):
        return []
    return [o["label"] for o in options
            if isinstance(o, dict) and isinstance(o.get("label"), str)]


def _chooses_an_offered_label(question, decision):
    """Whether the decision's recorded choice is one of the question's labels,
    compared the way `_rewrite_suggested` finds the option to propose."""
    chosen = decision.get("chosen_option")
    if not _binding.nonblank(chosen):
        return False
    target = chosen.strip().casefold()
    return any(label.strip().casefold() == target for label in _labels_of(question))


def _is_confirmed(decision):
    return (decision.get("review_state") == "confirmed"
            and decision.get("status") == "taken")


def _classify(questions, verdicts, mode="answer"):
    """`(answered, suggested)` index lists.

    `answered` needs `answer` mode AND the verdict AND a decision that is
    confirmed and taken AND a recorded choice that is one of the offered labels;
    an `answered` verdict on anything else is asked, as a suggestion. In
    `suggest` mode nothing is answered."""
    answered, suggested = [], []
    for i, verdict in enumerate(verdicts):
        kind = verdict["verdict"]
        if kind == "unanswered":
            continue
        decision = verdict["decision"]
        if (mode == "answer" and kind == "answered" and _is_confirmed(decision)
                and _chooses_an_offered_label(questions[i], decision)):
            answered.append(i)
        else:
            suggested.append(i)
    return answered, suggested


# --------------------------------------------------------------------------- #
# Texts.
# --------------------------------------------------------------------------- #

def _answer_of(decision):
    chosen = decision.get("chosen_option")
    return chosen.strip() if _binding.nonblank(chosen) else decision["title"]


def _who(decision):
    return {"human": "a person", "agent": "an agent"}.get(
        decision.get("decided_by"), "unknown")


def _who_long(decision):
    return {"human": "decided by a person", "agent": "decided by an agent"}.get(
        decision.get("decided_by"), "decider not recorded")


def _date_of(decision):
    recorded = decision.get("recorded_at")
    return recorded[:10] if _binding.nonblank(recorded) else "date not recorded"


def _fit(first, second, fixed_len):
    """`first` and `second` clipped so a status of `fixed_len` other characters
    fits `MAX_STATUS`: each at most `_TEXT_LIMIT`, and a short text's spare room
    goes to the other. Short texts come back unchanged."""
    budget = max(4, _hook_line.MAX_STATUS - fixed_len)
    a, b = len(" ".join(first.split())), len(" ".join(second.split()))
    la, lb = min(a, _TEXT_LIMIT), min(b, _TEXT_LIMIT)
    if la + lb > budget:
        if la <= budget // 2:
            lb = budget - la
        elif lb <= budget // 2:
            la = budget - lb
        else:
            la = budget // 2
            lb = budget - la
    return _hook_line.clip(first, max(2, la)), _hook_line.clip(second, max(2, lb))


def _answered_line(question, decision, count):
    tail = f" — {_who_long(decision)}, {_date_of(decision)}"
    if count is not None:
        tail += f" · {count} this session"
    q, a = _fit(question, _answer_of(decision), len('answered "" with ""') + len(tail))
    return _hook_line.line("Brain", f'answered "{q}" with "{a}"{tail}')


def _suggested_line(question, decision, mode="answer"):
    if mode == "suggest" and _is_confirmed(decision):
        tail = f" — {_who_long(decision)}, {_date_of(decision)}; still asked"
    else:
        tail = " — proposed, not confirmed; still asked"
    a, q = _fit(_answer_of(decision), question,
                len('suggests "" for ""') + len(tail))
    return _hook_line.line("Brain", f'suggests "{a}" for "{q}"{tail}')


def _answer_block(question, decision):
    node = decision["node_id"]
    decision_id = decision.get("decision_id")
    ident = f", id {decision_id.strip()}" if _binding.nonblank(decision_id) else ""
    return (f'- "{question}" → {_answer_of(decision)} '
            f'(decision "{decision["title"]}", node {node}{ident}, '
            f'decided by {_who(decision)}, recorded {_date_of(decision)})')


def _suggested_block(question, decision):
    return (f'- "{question}" → {_answer_of(decision)} '
            f'(proposed decision "{decision["title"]}", node {decision["node_id"]})')


def _confirmed_block(question, decision):
    return _answer_block(question, decision)


def _overrule_sentence():
    """How the model handles a change of mind: it asks the question again.

    The hook holds a re-asked question back from the brain and the record hook
    turns the person's pick into the replacement of the decision, so the model
    records nothing itself; a second successor written by hand would leave two
    decisions answering one question."""
    return ("If the person overrules one because they changed their mind, ask "
            "that question again with AskUserQuestion, with the same question "
            "text and options. The brain does not answer it a second time in "
            "this session, and the choice the person makes is recorded as "
            "replacing the decision. Do not record it yourself.")


# --------------------------------------------------------------------------- #
# The per-session counter. Disclosure only: a failure here drops the count from
# the lines and never stops the output.
# --------------------------------------------------------------------------- #

def _counter_path(session_id):
    name = hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()[:32]
    return os.path.join(_plugin_policy._data_dir(), "brain-answer", "sessions",
                        name + ".json")


def _bump_counter(session_id, delta):
    """The count before this call, after adding `delta`, or None on failure."""
    if not _binding.nonblank(session_id):
        return None
    try:
        path = _counter_path(session_id)
        before = 0
        try:
            with open(path, encoding="utf-8") as fh:
                stored = json.load(fh)
            value = stored.get("answered") if isinstance(stored, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                before = value
        except FileNotFoundError:
            pass
        except ValueError:
            before = 0  # a corrupt counter restarts rather than blocking disclosure
        _plugin_policy._atomic_write_json(
            path, {"answered": before + delta}, prefix=".fm-brain-answer-",
            mode=0o600, ensure_makedirs=True)
        _prune_counters(os.path.dirname(path), keep=path)
        return before
    except (OSError, ValueError):
        return None


def _prune_counters(directory, keep):
    now = time.time()
    removed = 0
    for name in os.listdir(directory):
        if removed >= _COUNTER_MAX_PRUNE:
            break
        path = os.path.join(directory, name)
        if path == keep:
            continue
        try:
            if now - os.stat(path).st_mtime > _COUNTER_MAX_AGE_S:
                os.remove(path)
                removed += 1
        except OSError:
            continue


# --------------------------------------------------------------------------- #
# Output.
# --------------------------------------------------------------------------- #

def _rewrite_suggested(question, decision, prefix=_SUGGESTED_PREFIX):
    """`question` with the option the brain proposes moved first and described
    as a proposal. The label is never changed: it is what the person's choice
    comes back as."""
    rewritten = copy.deepcopy(question)
    options = rewritten.get("options")
    chosen = decision.get("chosen_option")
    if not isinstance(options, list) or not _binding.nonblank(chosen):
        return rewritten
    target = chosen.strip().casefold()
    for i, option in enumerate(options):
        label = option.get("label") if isinstance(option, dict) else None
        if isinstance(label, str) and label.strip().casefold() == target:
            description = option.get("description")
            description = description if isinstance(description, str) else ""
            if not description.startswith(prefix):
                option["description"] = prefix + description
            options.insert(0, options.pop(i))
            break
    return rewritten


def _prefix_for(decision, mode):
    """The note on the suggested option: a confirmed decision in `suggest` mode
    says when it was recorded; anything else is a proposal."""
    if mode == "suggest" and _is_confirmed(decision):
        return _CONFIRMED_PREFIX.format(date=_date_of(decision))
    return _SUGGESTED_PREFIX


def _build_output(payload, tool_input, questions, verdicts, answered, suggested,
                  mode="answer", asked_again=False):
    session_id = payload.get("session_id")
    total = len(questions)
    n0 = _bump_counter(session_id, len(answered)) if answered else None

    lines = []
    for i, verdict in enumerate(verdicts):
        decision = verdict.get("decision")
        text = questions[i]["question"].strip()
        if i in answered:
            count = None if n0 is None else n0 + answered.index(i) + 1
            lines.append(_answered_line(text, decision, count))
        elif i in suggested:
            lines.append(_suggested_line(text, decision, mode))
    if asked_again:
        lines.append(_hook_line.line("Brain", _ASKED_AGAIN))
    if answered:
        lines.append(_hook_line.line("Brain", "reply to overrule any answer above"))
    system_message = "\n".join(lines)

    if not answered and not suggested:
        return {"systemMessage": system_message}

    blocks = "\n".join(_answer_block(questions[i]["question"].strip(), verdicts[i]["decision"])
                       for i in answered)
    overrule = _overrule_sentence()

    if len(answered) == total:
        reason = ("Fairmind company brain: every question was already answered by "
                  "a recorded decision, so none was put to the person:\n"
                  f"{blocks}\n"
                  f"Continue with these answers. {_PERSON_WINS} {overrule}")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason},
                "systemMessage": system_message}

    parts = []
    if answered:
        parts.append(
            f"Fairmind company brain: {len(answered)} of the {total} questions were "
            "already answered by recorded decisions and were removed before the "
            f"person saw them:\n{blocks}\n"
            f"Treat these as the person's answers. {_PERSON_WINS} {overrule}")
    confirmed = [i for i in suggested
                 if mode == "suggest" and _is_confirmed(verdicts[i]["decision"])]
    proposed = [i for i in suggested if i not in confirmed]
    if confirmed:
        parts.append(
            "For these questions the brain holds a confirmed decision. "
            "Answer-from-brain is set to suggest, so they are still being asked, "
            "with the decision marked as the suggested option:\n"
            + "\n".join(_confirmed_block(questions[i]["question"].strip(),
                                         verdicts[i]["decision"]) for i in confirmed))
    if proposed:
        parts.append(
            "For these questions the brain holds only a proposed decision; they "
            "are still being asked, with the proposal marked as the suggested option:\n"
            + "\n".join(_suggested_block(questions[i]["question"].strip(),
                                         verdicts[i]["decision"]) for i in proposed))

    remaining = []
    for i, question in enumerate(questions):
        if i in answered:
            continue
        if i in suggested:
            decision = verdicts[i]["decision"]
            remaining.append(_rewrite_suggested(question, decision, _prefix_for(decision, mode)))
        else:
            remaining.append(copy.deepcopy(question))
    updated = copy.deepcopy(tool_input)
    updated["questions"] = remaining
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "updatedInput": updated},
           "systemMessage": system_message}
    if parts:
        out["hookSpecificOutput"]["additionalContext"] = "\n\n".join(parts)
    return out


# --------------------------------------------------------------------------- #
# Flow.
# --------------------------------------------------------------------------- #

def _questions_of(tool_input):
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if (not isinstance(questions, list) or not questions
            or not all(isinstance(q, dict) and isinstance(q.get("question"), str)
                       for q in questions)):
        return None
    return questions


def _wire_questions(questions):
    wired = []
    for question in questions:
        item = {"question": question["question"]}
        options = question.get("options")
        labels = [o["label"] for o in options
                  if isinstance(o, dict) and isinstance(o.get("label"), str)] \
            if isinstance(options, list) else []
        if labels:
            item["options"] = labels
        wired.append(item)
    return wired


def _entry_for(question, decision, verdict):
    """The memory entry for `question` answered by `decision`, or None.

    Only a confirmed, taken decision whose recorded choice is one of the labels
    the question offers is remembered: it is the only kind a person's pick can be
    compared with and the only kind that can be superseded as an overrule."""
    if not (_is_confirmed(decision) and _chooses_an_offered_label(question, decision)):
        return None
    decision_id = decision.get("decision_id")
    return {"decision_id": decision_id.strip() if _binding.nonblank(decision_id) else None,
            "node_id": decision["node_id"],
            "chosen_option": decision["chosen_option"].strip(),
            "verdict": verdict,
            "review_state": decision["review_state"],
            "status": decision["status"]}


def _hold_back(session_id, askable, questions, mode):
    """The indices among `askable` whose question this session already put to the
    brain, each marked pending: asked again means overruled.

    In `suggest` mode the brain removed nothing, so asking a question again is
    not an overrule: only an entry already pending (the `--hold` route) is held
    back, and the rest are suggested again, which keeps a different pick on the
    confirm-first route."""
    held = []
    for i in askable:
        entry = _answer_memory.read(session_id, questions[i]["question"])
        if entry is None or entry.get("kind") == _answer_memory.KIND_CONFIRM:
            continue
        if mode != "answer" and not entry.get("pending"):
            continue
        held.append(i)
        if not entry.get("pending"):
            _answer_memory.write(session_id, questions[i]["question"], dict(entry, pending=True))
    return held


def _downgrade_reasked(session_id, questions, verdicts, sent_indices):
    """Turn into `unanswered` every answer whose decision already answered an
    earlier question of this session (a reworded re-ask), writing the pending
    entry under the text actually asked. The indices downgraded.

    Only an answer `_entry_for` would remember counts: a suggestion, or a
    decision whose choice is not one of this question's labels, is a different
    question that happens to match the same node, and a pending entry for it
    would let any pick retire that decision as overruled."""
    reasked = []
    for i in sent_indices:
        verdict = verdicts[i]
        if verdict["verdict"] != "answered":
            continue
        decision = verdict["decision"]
        if _answer_memory.find_by_node(session_id, decision["node_id"]) is None:
            continue
        entry = _entry_for(questions[i], decision, "answered")
        if entry is None:
            continue
        _answer_memory.write(session_id, questions[i]["question"], dict(entry, pending=True))
        verdicts[i] = {"verdict": "unanswered"}
        reasked.append(i)
    return reasked


def _remember(session_id, questions, verdicts, answered, suggested):
    """Write the entry of every answered or suggested question. An answered
    question whose entry cannot be stored is asked instead (`answered` loses it):
    the brain must not remove what it could not remember removing. True when one
    was demoted that way."""
    demoted = False
    for i in list(answered):
        entry = _entry_for(questions[i], verdicts[i]["decision"], "answered")
        if entry is None or not _answer_memory.write(session_id, questions[i]["question"], entry):
            answered.remove(i)
            suggested.append(i)
            demoted = True
    for i in suggested:
        entry = _entry_for(questions[i], verdicts[i]["decision"], "suggested")
        if entry is not None:
            _answer_memory.write(session_id, questions[i]["question"], entry)
    suggested.sort()
    return demoted


def _run(stdin):
    start = time.monotonic()
    deadline = start + _BUDGET_S
    payload = json.load(stdin)
    if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
        return None
    tool_input = payload.get("tool_input")
    questions = _questions_of(tool_input)
    if questions is None:
        return None

    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else os.getcwd()
    toplevel = _toplevel(cwd, deadline)
    if not toplevel:
        return None
    mode, _source = _plugin_policy.answer_from_brain_mode(toplevel)
    if mode == "off":
        return None
    server = _mcp_config.fairmind_server(cwd, toplevel)
    if server is None:
        return None

    askable = [i for i, q in enumerate(questions)
               if not _design_question.is_session_shaped(q["question"], _labels_of(q))]
    if not askable:
        return None

    session_id = payload.get("session_id")
    has_session = _binding.nonblank(session_id)
    # Nothing can be remembered without a session, and a question the brain
    # removed but cannot remember is one it would answer again.
    if not has_session:
        mode = "suggest"
    held = _hold_back(session_id, askable, questions, mode) if has_session else []
    sendable = [i for i in askable if i not in held]

    verdicts = [{"verdict": "unanswered"} for _ in questions]
    reasked = []
    if sendable:
        ctx = _binding.read_context(toplevel)
        # `apply` tells the brain the caller will skip what it answers, and the
        # brain counts time saved from it: only `answer` mode skips anything.
        args = {"questions": _wire_questions([questions[i] for i in sendable]),
                "apply": mode == "answer"}
        project = _binding.project_id(ctx)
        if project:
            args["project"] = project
        repository = _repository_id(ctx, toplevel, deadline)
        if repository:
            args["repository"] = repository
        if has_session:
            args["session_ref"] = session_id

        # Imported here: building its HTTPS opener costs more than the gates
        # above, and most calls end at one of them.
        import _brain_mcp  # noqa: PLC0415
        reply = _brain_mcp.call_tool(server, "Brain_answer", args, deadline=deadline)
        sent = _valid_reply(reply, [questions[i] for i in sendable])
        if sent is None:
            # The held-back questions are still asked, and said so.
            return {"systemMessage": _hook_line.line("Brain", _ASKED_AGAIN)} if held else None
        # The held-back questions keep their place, as unanswered.
        for i, verdict in zip(sendable, sent):
            verdicts[i] = verdict
        # Only a removed question can be overruled by asking it again.
        if has_session and mode == "answer":
            reasked = _downgrade_reasked(session_id, questions, verdicts, sendable)

    answered, suggested = _classify(questions, verdicts, mode)
    if has_session and _remember(session_id, questions, verdicts, answered, suggested):
        # A decision that could not be remembered is shown as the confirmed
        # suggestion it is, not as a proposal.
        mode = "suggest"
    asked_again = bool(held or reasked)
    if not answered and not suggested:
        if asked_again:
            return {"systemMessage": _hook_line.line("Brain", _ASKED_AGAIN)}
        return None
    return _build_output(payload, tool_input, questions, verdicts, answered, suggested,
                         mode, asked_again)


def _cmd_enabled():
    """Print `on` or `off`: whether the switch is on, in either mode, for the
    checkout the shell is standing in. Never reads stdin, because a Bash-tool
    call can leave it open."""
    print("on" if _current_mode() != "off" else "off")
    return 0


def _current_mode():
    """The mode for the checkout the shell is standing in; `off` on any failure."""
    try:
        top = _toplevel(os.getcwd(), time.monotonic() + _BUDGET_S)
        if top:
            return _plugin_policy.answer_from_brain_mode(top)[0]
    except Exception:  # noqa: BLE001 - off is the safe answer to any failure
        pass
    return "off"


def _cmd_mode():
    """Print `off`, `suggest` or `answer`."""
    print(_current_mode())
    return 0


def _cmd_hold(argv):
    """Remember a question the person is about to overrule, for a route that put
    the question to the brain itself (a command or a skill, which the hook never
    sees): `--hold --session S --question Q --node N [--decision-id D] --choice C`.

    The entry is the same pending one the hook writes when it sees a question
    asked again, so the question the agent now asks through `AskUserQuestion` is
    held back from the brain and the pick is read as a replacement."""
    values = {}
    flags = {"--session": "session", "--question": "question", "--node": "node",
             "--decision-id": "decision_id", "--choice": "choice"}
    i = 0
    while i < len(argv):
        if argv[i] in flags and i + 1 < len(argv):
            values[flags[argv[i]]] = argv[i + 1]
            i += 2
        else:
            i += 1
    if not all(_binding.nonblank(values.get(k)) for k in ("session", "question", "node", "choice")):
        print("not remembered: --session, --question, --node and --choice are required")
        return 0
    entry = {"decision_id": values.get("decision_id") if _binding.nonblank(values.get("decision_id")) else None,
             "node_id": values["node"].strip(), "chosen_option": values["choice"].strip(),
             "verdict": "answered", "review_state": "confirmed", "status": "taken",
             "pending": True}
    stored = _answer_memory.write(values["session"], values["question"], entry)
    print("remembered" if stored else "not remembered")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--enabled" in argv:
        return _cmd_enabled()
    if "--mode" in argv:
        return _cmd_mode()
    if "--hold" in argv:
        try:
            return _cmd_hold(argv)
        except Exception:  # noqa: BLE001
            print("not remembered")
            return 0
    try:
        out = _run(sys.stdin)
    except Exception:  # noqa: BLE001 - every failure means: ask everything
        out = None
    if out:
        sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
