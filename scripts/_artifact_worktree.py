"""Where a linked git worktree's artifact opt-in and config come from.

Stdlib-only leaf shared by scripts/artifact_cards.py and the Claude Code adapter
(hooks/scripts/artifact_cards_hook.py), so the two cannot disagree. The bash wrapper
hooks/scripts/artifact-cards.sh implements the same rule in pure bash and must stay in step.

A linked worktree has a `.git` FILE naming its gitdir; that gitdir holds a `commondir`
(a submodule's does not, and stays a separate repository) and the common directory is a
`.git` that is not itself a bare repository (a bare repository has no checkout to inherit
from, whatever its directory is called; `core.bare` is read from the config file directly).
The marker and the config files are usually untracked, so they live in the main checkout,
not in the worktree.
"""

import os

STATE_DIR = os.path.join(".fairmind", "artifacts")
COMMITTED_CONFIG = ".fairmind-artifacts.json"


def is_bare(common):
    """Whether the git directory `common` is configured `core.bare = true`, read from its
    config file with no git process. Git's own spelling rules: the section and key names are
    case-insensitive, a key with no value is true, and true is true, yes, on or 1."""
    config = os.path.join(common, "config")
    if not os.path.isfile(config):  # a FIFO would block the open
        return False
    in_core = False
    bare = False
    try:
        with open(config, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("["):
                    in_core = line.split("]", 1)[0][1:].strip().lower() == "core"
                    line = line.split("]", 1)[1].strip() if "]" in line else ""
                if not in_core or not line or line[0] in "#;":
                    continue
                key, eq, value = line.partition("=")
                if key.strip().lower() == "bare":
                    value = value.split("#", 1)[0].split(";", 1)[0].strip().strip('"').lower()
                    bare = not eq or value in ("true", "yes", "on", "1")
    except OSError:
        return False
    return bare


def linked_worktree_root(d):
    """The main checkout's root when `d/.git` is a file naming a linked worktree's gitdir,
    else None."""
    gitfile = os.path.join(d, ".git")
    if not os.path.isfile(gitfile):
        return None
    try:
        with open(gitfile, encoding="utf-8", errors="replace") as fh:
            line = fh.readline().rstrip("\r\n")
        if not line.startswith("gitdir: "):
            return None
        gitdir = os.path.normpath(os.path.join(d, line[len("gitdir: "):]))
        commondir = os.path.join(gitdir, "commondir")
        if not os.path.isfile(commondir):  # a FIFO would block the open
            return None
        with open(commondir, encoding="utf-8", errors="replace") as fh:
            common = os.path.normpath(os.path.join(gitdir, fh.readline().rstrip("\r\n")))
    except OSError:
        return None
    if os.path.basename(common) != ".git" or is_bare(common):
        return None
    return os.path.dirname(common)


def config_files(top):
    """The artifacts config files that can speak for the checkout at `top`, in precedence
    order: the local (untracked) kind before the committed kind, and within each kind the
    checkout itself before (a linked worktree only) its main checkout. This is the order a
    main checkout already uses, carried across the worktree boundary."""
    roots = [top]
    if os.path.isfile(os.path.join(top, ".git")):
        main = linked_worktree_root(top)
        if main:
            roots.append(main)
    return ([os.path.join(r, STATE_DIR, "config.json") for r in roots]
            + [os.path.join(r, COMMITTED_CONFIG) for r in roots])
