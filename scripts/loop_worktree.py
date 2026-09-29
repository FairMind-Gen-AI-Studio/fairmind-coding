#!/usr/bin/env python3
"""
loop_worktree.py — OPT-IN worktree isolation for a loop's maker (T9).

A loop's maker (Software Engineer) normally edits the user's own checkout in
place. This helper creates (or reuses) a dedicated git worktree on a
`loop/<task-ref>` branch, so the maker can be pointed at a throwaway location
instead — the user's working tree is never touched, and the exit report can
diff the `loop/<task-ref>` branch. Purely opt-in: nothing in the gate engine
requires this, and a loop that never calls it behaves exactly as before.

CLI contract (pinned — `tests/test_worktree.py` is written against this shape):

  --create --task-ref <ref> [--state <path>] [--cwd <repo-root>]
    Resolves the repo root from --cwd (default: cwd). Not inside a git work
    tree -> stderr error, non-zero exit, NO side effects (no worktree, no
    --state write).

    Branch is the literal `loop/<ref>`, based on HEAD. The worktree lives at a
    deterministic path, stable per (repo, ref), outside the repo's tracked
    tree: `<repo_root>/.fairmind/worktrees/<sanitized_ref>` (`.fairmind/` is
    already excluded from the loop's own mutation set, so this never pollutes
    a run's scope). `<ref>` is sanitized for filesystem safety — any character
    outside [A-Za-z0-9_.-] becomes `-` — but the BRANCH stays the literal
    `loop/<ref>`.

    IDEMPOTENT: re-running --create for the same (repo, ref) reuses the
    worktree already registered at that path — exit 0, path unchanged, no
    second worktree minted.

    CONFLICT: if `loop/<ref>` is already checked out at a DIFFERENT worktree
    (some path this helper does not own) -> stderr error, non-zero exit, no
    side effects — this mirrors git's own "branch already checked out"
    refusal instead of letting it surface as a raw crash.

    On success, if --state is given, `worktree.path` (the real, on-disk
    worktree path) and `worktree.branch` (== `loop/<ref>`) are recorded into
    that JSON file, preserving every other field already there. The worktree
    path is printed to stdout.

  --cleanup --task-ref <ref> [--cwd <repo-root>]
    Removes the worktree registered for `loop/<ref>` (`git worktree remove
    --force`) so it no longer appears in `git worktree list`. Never deletes
    the branch itself. Removing an already-absent worktree is a benign no-op
    success, not an error.

Style follows capture_baseline.py: stdlib `subprocess` only, no shell string
interpolation of paths, careful returncode handling, and preconditions
(git work tree? branch conflict?) are checked BEFORE any side effect —
including before the first byte of --state is ever touched.
"""

import argparse
import json
import os
import re
import subprocess
import sys

# PCF-28: one writer for the consumer-repo ignore entry, asked by every site
# that can create `.fairmind/`. A stdlib-only LEAF module — importing it
# costs nothing else — reached the way `_loop_ledger` and `ambient_digest`
# already make their own dir importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _fm_ignore import makedirs_ignored  # noqa: E402

_SANITIZE = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_ref(ref):
    """Filesystem-safe form of a task ref for use as a directory name. The
    branch name itself is never sanitized — it stays the literal `loop/<ref>`
    the rest of the loop machinery expects."""
    return _SANITIZE.sub("-", ref)


def sh(argv, cwd=None):
    """Run git (or any argv) and capture the result without raising — every
    caller inspects returncode/stdout/stderr itself, so a failed git command
    is always a controlled, reported failure, never an uncaught crash."""
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)


def is_git_work_tree(cwd):
    proc = sh(["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"])
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def repo_toplevel(cwd):
    proc = sh(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def list_worktrees(repo_root):
    """Parse `git worktree list --porcelain` into a list of dicts, each with
    at least `path` and (when not detached/bare) `branch` == the full
    `refs/heads/<name>` ref. Blocks are separated by a blank line; the last
    block has no trailing blank line, so the pending block is flushed once
    more after the loop."""
    proc = sh(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    if proc.returncode != 0:
        return []
    entries = []
    cur = {}
    for line in proc.stdout.splitlines():
        if not line:
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line == "bare":
            cur["bare"] = True
        elif line == "detached":
            cur["detached"] = True
    if cur:
        entries.append(cur)
    return entries


def find_by_branch(entries, branch_ref):
    for entry in entries:
        if entry.get("branch") == branch_ref:
            return entry
    return None


def branch_exists(repo_root, branch):
    proc = sh(["git", "-C", repo_root, "show-ref", "--verify", "--quiet",
               f"refs/heads/{branch}"])
    return proc.returncode == 0


def write_state(state_path, worktree_path, branch):
    """Load -> set `state["worktree"]` -> dump, preserving every other field
    already in the file. Only ever called after the worktree side effect has
    already succeeded, so a --state read/write failure here never masks a
    partially-applied worktree; it does mean the caller should treat a
    non-zero exit from this function as needing investigation, not retry."""
    state = {}
    if os.path.isfile(state_path):
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
    state["worktree"] = {"path": worktree_path, "branch": branch}
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, state_path)


def do_create(args):
    cwd = args.cwd or "."
    if not is_git_work_tree(cwd):
        print(f"loop_worktree: {cwd!r} is not inside a git work tree; "
              f"no worktree created, no state touched", file=sys.stderr)
        return 1

    repo_root = repo_toplevel(cwd)
    if not repo_root:
        print(f"loop_worktree: could not resolve the git toplevel for {cwd!r}",
              file=sys.stderr)
        return 1

    ref = args.task_ref
    branch = f"loop/{ref}"
    branch_full = f"refs/heads/{branch}"
    target_path = os.path.normpath(
        os.path.join(repo_root, ".fairmind", "worktrees", sanitize_ref(ref))
    )

    entries = list_worktrees(repo_root)
    existing = find_by_branch(entries, branch_full)

    if existing is not None:
        if os.path.realpath(existing["path"]) != os.path.realpath(target_path):
            print(
                f"loop_worktree: branch {branch!r} is already checked out at "
                f"{existing['path']!r}, which is not this helper's worktree "
                f"({target_path!r}); refusing to create a second checkout of "
                f"the same branch. No side effects.",
                file=sys.stderr,
            )
            return 2
        # Idempotent reuse: same branch, same deterministic path.
        final_path = existing["path"]
    else:
        makedirs_ignored(os.path.dirname(target_path))
        if branch_exists(repo_root, branch):
            add_argv = ["git", "-C", repo_root, "worktree", "add", target_path, branch]
        else:
            add_argv = ["git", "-C", repo_root, "worktree", "add", "-b", branch,
                        target_path, "HEAD"]
        proc = sh(add_argv)
        if proc.returncode != 0:
            print(f"loop_worktree: `git worktree add` failed: {proc.stderr.strip()}",
                  file=sys.stderr)
            return 3
        created = find_by_branch(list_worktrees(repo_root), branch_full)
        if created is None:
            print("loop_worktree: `git worktree add` reported success but the "
                  "worktree is not registered for the branch afterwards",
                  file=sys.stderr)
            return 4
        final_path = created["path"]

    if args.state:
        write_state(args.state, final_path, branch)

    print(final_path)
    return 0


def do_cleanup(args):
    cwd = args.cwd or "."
    if not is_git_work_tree(cwd):
        print(f"loop_worktree: {cwd!r} is not inside a git work tree",
              file=sys.stderr)
        return 1

    repo_root = repo_toplevel(cwd)
    if not repo_root:
        print(f"loop_worktree: could not resolve the git toplevel for {cwd!r}",
              file=sys.stderr)
        return 1

    ref = args.task_ref
    branch = f"loop/{ref}"
    branch_full = f"refs/heads/{branch}"

    existing = find_by_branch(list_worktrees(repo_root), branch_full)
    if existing is None:
        # Nothing registered for this branch: a benign no-op success, never an
        # error — the caller may be cleaning up a loop that never opted in, or
        # one that was already cleaned up.
        return 0

    proc = sh(["git", "-C", repo_root, "worktree", "remove", "--force", existing["path"]])
    if proc.returncode != 0:
        print(f"loop_worktree: `git worktree remove` failed: {proc.stderr.strip()}",
              file=sys.stderr)
        return 5
    # The branch itself is deliberately left alone — --cleanup removes the
    # worktree checkout only, never `loop/<ref>` (the maker's committed work
    # must survive so the exit report can still diff it).
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create/reuse or clean up a dedicated git worktree for a loop's maker."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true", help="Create or reuse the worktree.")
    action.add_argument("--cleanup", action="store_true", help="Remove the worktree (keeps the branch).")
    parser.add_argument("--task-ref", required=True, help="Loop task ref; branch becomes loop/<ref>.")
    parser.add_argument("--state", default=None, help="loop-state.json to record worktree.* into (--create only).")
    parser.add_argument("--cwd", default=".", help="Repo root (or any path inside it). Default: cwd.")
    args = parser.parse_args(argv)

    if args.create:
        return do_create(args)
    return do_cleanup(args)


if __name__ == "__main__":
    sys.exit(main())
