#!/usr/bin/env python3
"""
artifact_cards_hook.py - the Claude Code adapter for scripts/artifact_cards.py.

Four modes, each reading one hook payload on stdin and calling the agent-neutral
CLI. All the deciding happens there; this file only translates the payload in and
the answer out:

  gate       PreToolUse Write|Edit: a write into the artifacts path of a public
             repository (or one whose visibility cannot be told) asks the person.
  bash-gate  PreToolUse Bash, every command: the shell line goes to the CLI whole, which
             measures each literal `git push` it reads in full inside its short grammar
             and passes the rest of the line unread. It asks when such a push carries an
             artifact into a public repository or cannot be measured, and on every path on
             which the CLI cannot answer. It is a courtesy gate, not a security boundary.
  pr-closed  PostToolUse Bash, `gh pr merge|close *`: the model is told that the
             pull-request import moves or retires the cards that follow the PR.
  refresh    SessionStart: a git pre-push hook the person installed, and the copy of the
             CLI it runs, are brought in step with this plugin. It never installs anything
             and prints nothing: what a SessionStart hook prints reaches the model.

It never blocks. `pr-closed` never fails loudly: a notice that crashes a tool call costs
more than the notice is worth. `gate` and `bash-gate` ask on every path where the CLI
cannot answer. The one decision it can emit is "ask", never a grant.
"""

import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.normpath(os.path.join(HERE, "..", "..", "scripts", "artifact_cards.py"))
sys.path.insert(0, os.path.dirname(CLI))
import _artifact_worktree  # noqa: E402  (stdlib-only leaf, so a dead CLI leaves it readable)
# Under the registered hook timeout (20 s): a slower CLI must not be killed by the
# harness, which would let the write through unasked.
CLI_TIMEOUT_S = float(os.environ.get("FM_ARTIFACTS_CLI_TIMEOUT_S") or 15)


def call_cli(*argv):
    done = subprocess.run([sys.executable, CLI, *argv], capture_output=True,
                          text=True, timeout=CLI_TIMEOUT_S)
    return json.loads(done.stdout) if done.stdout.strip() else {}


def ask_cli(*argv):
    """The CLI's answer, or {"error": ...} when it gave none: a timeout, a crash or
    output that is not JSON must read as "could not check", never as a pass."""
    try:
        answer = call_cli(*argv)
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    return answer if isinstance(answer, dict) else {"error": "unreadable answer"}


def emit(event, **fields):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))


def emit_ask(reason):
    emit("PreToolUse", permissionDecision="ask", permissionDecisionReason=reason)


# The CLI's own sentence, kept here because this adapter must still ask when the CLI is dead.
PUBLISHED = "A push to a public repository publishes every working artifact it carries"


def ask_unchecked_write(path, why):
    emit_ask("%s looks like a working artifact, and whether this repository is public "
             "could not be checked (%s). %s: confirm with the person before writing it."
             % (path, why, PUBLISHED))


def ask_unchecked_command(why):
    emit_ask("Whether this command pushes working artifacts into a public repository "
             "could not be checked (%s). %s: confirm with the person before running it."
             % (why, PUBLISHED))


def workdir(payload):
    return payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def gate(payload):
    path = (payload.get("tool_input") or {}).get("file_path")
    if not path:
        return
    answer = ask_cli("gate", "--repo", workdir(payload), "--file-path", path)
    if answer.get("decision") == "ask":
        emit_ask(answer.get("reason") or (
            "%s may be a working artifact, and this repository may be public. %s: confirm "
            "with the person before writing it." % (path, PUBLISHED)))
    elif answer.get("decision") != "pass" and looks_like_artifact(path, workdir(payload)):
        # The CLI did not answer (timeout, crash, unreadable config). For a path that
        # looks like a working artifact the safe default is to ask, never to let
        # the write through unseen.
        ask_unchecked_write(path, answer.get("error") or "no answer")


def looks_like_artifact(path, cwd):
    """Whether a path may be a working artifact when the CLI cannot say: it sits in
    the default directory, or under a path its repository configured (a config file
    that cannot be read counts as a match)."""
    path = path.replace("\\", "/")
    if "artifacts/" in path:
        return True
    full = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    d = os.path.dirname(full)
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    while d:
        for config in _artifact_worktree.config_files(d):
            if os.path.isfile(config):
                try:
                    with open(config, encoding="utf-8") as fh:
                        prefixes = [p.strip().strip("/") + "/" for p in json.load(fh)["paths"]]
                    rel = os.path.relpath(full, d).replace(os.sep, "/")
                    return any(rel.startswith(p) for p in prefixes)
                except Exception:  # noqa: BLE001 - unreadable config: assume it matches
                    return True
        parent = os.path.dirname(d)
        if os.path.exists(os.path.join(d, ".git")) or parent == d:
            return False
        d = parent
    return False


# ----------------------------------------------------------------------- shell lines
#
# The line is handed to the CLI whole (`--command`, `--cwd`): what it may pass, what it
# asks about and how a literal push is measured is the CLI's job, so every agent gets the
# same answer. This adapter only relays the payload.


def bash_gate(payload):
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        raise ValueError("the hook payload carries no command text")
    answer = ask_cli("push-gate", "--command", command, "--cwd", workdir(payload))
    if answer.get("decision") == "pass":
        return
    if answer.get("reason"):
        emit_ask(answer["reason"])
    else:
        ask_unchecked_command(answer.get("error") or "no answer")


def command_failed(payload):
    """Whether the hook payload says the command did not succeed."""
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return False
    for key in ("exit_code", "exitCode", "returncode"):
        if isinstance(response.get(key), int) and response[key] != 0:
            return True
    return bool(response.get("is_error") or response.get("interrupted"))


def pr_verb(command):
    """'merge' or 'close' for the first `gh pr merge|close` command of a shell line, else
    None: each command of a chained line is read from its own first words, after any
    leading VAR=value assignments."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    segment = []
    for token in list(lexer) + [";"]:
        if token and all(c in "();&|" for c in token):
            while segment and "=" in segment[0] and not segment[0].startswith("="):
                segment.pop(0)
            if segment[:2] == ["gh", "pr"] and segment[2:3] in (["merge"], ["close"]):
                return segment[2]
            segment = []
        else:
            segment.append(token)
    return None


def pr_closed(payload):
    command = (payload.get("tool_input") or {}).get("command", "")
    cwd = workdir(payload)
    verb_word = pr_verb(command)
    if verb_word is None or command_failed(payload):
        return
    if not call_cli("state", "--repo", cwd).get("filed"):
        return
    verb = "merged" if verb_word == "merge" else "closed"
    emit("PostToolUse", additionalContext=(
        "A pull request was just %s in this session, and working artifacts have been "
        "filed from this checkout. Their reference cards follow the pull request at its "
        "next import (a merge moves them to the base branch, a close without a merge "
        "retires them). Offer to run the brain-import-pull-requests skill for it now, "
        "or leave it to the next seeding run." % verb))


def refresh(payload):
    call_cli("install-git-hook", "--repo", workdir(payload), "--refresh")


MODES = {"gate": gate, "bash-gate": bash_gate, "pr-closed": pr_closed, "refresh": refresh}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in MODES:
        return 0
    payload = None
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            MODES[mode](payload)
        elif mode == "bash-gate":
            raise ValueError("the hook payload is not an object")
    except Exception as exc:  # noqa: BLE001 - a hook must never break the tool call
        try:
            if mode == "bash-gate":  # the gate asks on every path where it cannot answer
                ask_unchecked_command(type(exc).__name__)
            elif mode == "gate":
                path = ((payload.get("tool_input") or {}).get("file_path")
                        if isinstance(payload, dict) else None)
                if isinstance(path, str) and looks_like_artifact(path, workdir(payload)):
                    ask_unchecked_write(path, type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
