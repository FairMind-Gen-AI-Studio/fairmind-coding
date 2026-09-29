#!/usr/bin/env python3
"""PCF-28 — keep the plugin's own working directory out of the consumer's repo.

The plugin writes `.fairmind/` into whatever repository it is run in: journals,
loop state, contracts, audit output, and `trace/*.jsonl` rows that carry BASH
COMMAND TEXT. Nothing added an ignore entry, so on a consumer repo those files
showed up as untracked — one `git add -A` away from being committed into someone
else's history. `makedirs_ignored` is the one call every writer that can create
that directory makes instead of `os.makedirs`.

A LEAF MODULE, and deliberately its own file rather than a section of
`_loop_ledger.py`. Seven scripts call this — including `loop_open.py`, whose
banner is printed from a `UserPromptExpansion` hook and is therefore
user-visible — and putting it in the ledger would have made every one of them
import `_loop_ledger` -> `loop_tokens` -> `_usage_dedup` at module load to get a
`makedirs` wrapper. A token ledger is not a dependency of an audit renderer.

stdlib only, no subprocess: `trace-op.sh` calls this on EVERY PostToolUse, so
resolving the repository by shelling out to git is not available at this
altitude.
"""

import os

# POSIX-only advisory locking, guarded exactly the way `_loop_ledger` guards it
# so the plugin still imports on Windows.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

# The directory this plugin writes into a consumer repository, and the ONE
# ignore entry this module will ever manage. Narrow on purpose: `base_path` can
# nest under it (`.fairmind/<project>/<loop-ref>/`), but an ignore rule for an
# arbitrary directory is a far worse outcome than the untracked file it would
# be fixing, so anything not under this name is left completely alone.
_FM_DIR = ".fairmind"
_IGNORE_ENTRY = _FM_DIR + "/"

# THE ENTRY IS WRITTEN UNANCHORED — `.fairmind/`, no leading slash — and that
# is what makes ONE entry at the repository root enough. Git matches a
# slash-free pattern at ANY depth, so a root `.fairmind/` covers
# `packages/web/.fairmind/` too; `/.fairmind/` would not. Verified with
# `git check-ignore -v` rather than assumed, because an earlier revision of
# this module asserted the opposite in a test and used it to justify writing
# nothing at all for a subdirectory session.
#
# Every spelling git accepts as ignoring this directory AT THE ROOT. Checked as
# STRIPPED WHOLE LINES, never as a substring: `#.fairmind/` is a comment and
# `!.fairmind/` is a re-inclusion, and a substring test would read either as
# already done and leave the directory unignored forever.
_IGNORE_SPELLINGS = frozenset((_IGNORE_ENTRY, _FM_DIR, "/" + _IGNORE_ENTRY, "/" + _FM_DIR))
_NEGATION_SPELLINGS = frozenset("!" + s for s in _IGNORE_SPELLINGS)

# A SECOND, INDEPENDENT idiom a consumer repo reaches for on purpose: `dir/*`
# ignores every CHILD of `.fairmind` without excluding the directory entry
# itself, which is what lets a later `!.fairmind/<subdir>/` re-include one
# specific subdirectory — impossible with a bare `.fairmind/` spelling, since
# git will not descend into an excluded directory at all
# (`test_a_later_negation_re_arms_the_entry` proves the bare form's own limit
# the other way round). Another repository's `.gitignore` carves out
# `.fairmind/criteria/` this way to keep that one subdirectory tracked.
#
# Reported for real: `_already_ignored` did not recognise `.fairmind/*` as
# covering anything, so this module kept appending its own `.fairmind/` after
# the negation — the LAST rule matching the directory itself — which pruned
# traversal into it and silently re-ignored `criteria/` too, on every
# PostToolUse, forever. Tracked separately from `_IGNORE_SPELLINGS` rather
# than folded into it: the two idioms exclude the directory in different ways
# and a negation of one must not be read as a negation of the other (see
# `test_a_later_negation_of_the_wildcard_form_re_arms_the_entry`).
_WILDCARD_ENTRY = _FM_DIR + "/*"
_WILDCARD_SPELLINGS = frozenset((_WILDCARD_ENTRY, "/" + _WILDCARD_ENTRY))
_WILDCARD_NEGATIONS = frozenset("!" + s for s in _WILDCARD_SPELLINGS)

_IGNORE_HEADER = "# Fairmind plugin working directory (journals, loop state, trace rows)"

# Ceiling on the walk up to a repository marker. A path can only be so deep
# before "we are not in a repo" is the right answer, and an unbounded loop on a
# hook hot path is not.
_REPO_WALK_LIMIT = 64

# The per-user STATE home, which is NOT a consumer repo and must never be
# touched. Spelled here rather than imported from `_insights_session.data_dir()`
# because that module imports `_loop_ledger`, which imports this one — and a
# lazy import of a 1,900-line module on the PostToolUse path to answer a
# question `os.environ` and `expanduser` already answer would be worse than the
# duplication. The two must agree, so the ONE thing they share is the shape:
# `$FAIRMIND_INSIGHTS_HOME`, else `~/.fairmind`.
_STATE_HOME_ENV = "FAIRMIND_INSIGHTS_HOME"


def _state_home():
    override = os.environ.get(_STATE_HOME_ENV)
    if override:
        return os.path.abspath(override)
    return os.path.abspath(os.path.join(os.path.expanduser("~"), _FM_DIR))


def _repo_toplevel_of(directory):
    """The directory holding the `.git` at or above `directory`, or None when
    there is none — i.e. the repository work tree `directory` belongs to.

    `os.path.exists`, NOT `os.path.isdir`: in a linked worktree `.git` is a FILE
    holding a `gitdir:` pointer (and a submodule's is too), and this fix was
    itself developed in one — an isdir() guard fails exactly where the work
    happens.

    It RETURNS the toplevel rather than answering "are we in a repo at all",
    because the caller needs to compare against it. Answering only the yes/no
    question is what let an earlier revision create a brand-new `.gitignore`
    inside a consumer SUBDIRECTORY: the session cwd is routinely
    `<repo>/packages/web`, so `<repo>/packages/web/.gitignore` appeared,
    untracked and semantically load-bearing for that whole subtree — the exact
    artifact class this module exists to remove, one level down."""
    current = os.path.abspath(directory)
    for _ in range(_REPO_WALK_LIMIT):
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def _fairmind_parent(path):
    """The directory that CONTAINS the `.fairmind` component of `path`, or None
    when `path` is not under one.

    The FIRST `.fairmind` component wins, so a nested `base_path` like
    `.fairmind/<project>/<loop-ref>/` resolves to the same parent as a bare
    `.fairmind/trace/` — one entry, beside the directory a reader would look
    for, never one buried inside it."""
    parts = os.path.abspath(path).split(os.sep)
    try:
        index = parts.index(_FM_DIR)
    except ValueError:
        return None
    return os.sep.join(parts[:index]) or os.sep


def ensure_ignored(path):
    """Best-effort: make sure the consumer repository's own `.gitignore` ignores
    `.fairmind/`, so the plugin's working directory never sits
    untracked-and-unignored there (PCF-28).

    WHY THIS IS WORTH A WRITE INTO SOMEONE ELSE'S TREE. What accumulates under
    `.fairmind/` is journals, loop state and `trace/*.jsonl` rows carrying BASH
    COMMAND TEXT. Untracked and unignored, that is one `git add -A` away from
    being committed into a repository it was never meant to reach. The entry is
    the smallest thing that closes it.

    THE TWO PLACES IT MUST DO NOTHING, both found by review after a first
    revision did exactly the wrong thing in each:

      * THE PER-USER STATE HOME. `_insights_session.register_session` appends to
        `~/.fairmind/insights/sessions/<tenancy>.jsonl`, which has a `.fairmind`
        component like any other. With `$HOME` a dotfiles repo — ordinary — the
        first revision appended `.fairmind/` to the developer's own
        `~/.gitignore`, in a repository this plugin was never run in. That also
        falsified `data_dir()`'s own docstring, which says `~/.fairmind` "is
        gitignore-immune... never inside a repo work tree". The suite could not
        see it: `FAIRMIND_INSIGHTS_HOME` points at a temp dir with no
        `.fairmind` component, so the state-home path never reached this code.
      * ANYWHERE THAT IS NOT THE REPOSITORY TOPLEVEL — as the place it WRITES.
        See `_repo_toplevel_of`.

    IT STILL COVERS A SUBDIRECTORY SESSION, and getting that wrong was the
    second half of the same mistake. The first revision refused to write
    anything when `.fairmind/` was not at the toplevel, on the stated grounds
    that "the entry there would not cover packages/web/.fairmind/" — which is
    false, and was pinned as intentional by a test. A slash-free git pattern
    matches at any depth. So the rule is: work out the toplevel and write the
    ONE unanchored entry there, whatever depth the directory was created at.
    That covers the monorepo case, which the module's own docstring calls the
    ordinary one, and still never creates a `.gitignore` in someone's subtree.

    WHAT IT WILL NOT DO, because a tool editing a repository it was merely run
    inside has to be narrow: it manages ONE literal entry and never any other;
    it never rewrites, reorders or removes existing content — append only; it
    does nothing outside a git work tree; and it does nothing when any spelling
    of the entry is already present.

    ALREADY-TRACKED FILES ARE UNAFFECTED, and deliberately so: git ignores
    `.gitignore` for paths already in the index, so a repository that committed
    something under `.fairmind/` on purpose keeps it. The entry changes what
    happens to NEW files only. (`run_gate_checks`'s structural `.fairmind/`
    exclusion from the mutation set therefore stays necessary — this does not
    make it redundant.)

    NEVER RAISES. Every caller is a fail-open hook path, and the ledger row is
    always the thing that matters more than the ignore entry."""
    try:
        parent = _fairmind_parent(path)
        if parent is None:
            return
        # The state home is ours, not the consumer's. Compared on the `.fairmind`
        # directory itself, not on `parent`, so an override pointing anywhere
        # still matches by identity rather than by name.
        if os.path.abspath(os.path.join(parent, _FM_DIR)) == _state_home():
            return
        toplevel = _repo_toplevel_of(parent)
        if toplevel is None:
            return
        _append_entry(os.path.join(toplevel, ".gitignore"))
    except Exception:
        return


def _already_ignored(text):
    """True iff `text` — the current `.gitignore` — leaves `.fairmind/`
    ignored, by EITHER idiom this module recognises: the bare directory
    spellings, or `dir/*` (a consumer repo's way of carving a subdirectory
    back out — see `_WILDCARD_SPELLINGS`).

    LAST MATCH WINS, which is git's rule and not an implementation detail worth
    approximating. A file carrying `.fairmind/` and then, later, `!.fairmind/`
    re-INCLUDES the directory: a set-membership test sees the positive line,
    returns "already handled", and the entry is never appended — the directory
    stays visible and the code believes it closed the hole. So the lines are
    scanned in order and only the LAST rule that speaks to this path decides —
    tracked as TWO independent verdicts, one per idiom, because a negation of
    one spelling says nothing about the other: `.fairmind/*` then
    `!.fairmind/` does not re-arm the wildcard verdict, and `.fairmind/` then
    `!.fairmind/*` does not re-arm the bare one. Either verdict landing True is
    sufficient — the two idioms exclude the same directory by different
    means, and this module only needs to know that something already does."""
    bare = False
    wildcard = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in _IGNORE_SPELLINGS:
            bare = True
        elif stripped in _NEGATION_SPELLINGS:
            bare = False
        elif stripped in _WILDCARD_SPELLINGS:
            wildcard = True
        elif stripped in _WILDCARD_NEGATIONS:
            wildcard = False
    return bare or wildcard


def _append_entry(gitignore):
    """Read `gitignore` and append the entry if it is missing — DOUBLE-CHECKED:
    an unlocked read decides the common case, and only the branch that is
    actually going to write takes a lock, non-blocking.

    A SYMLINK IS REFUSED. `open(path, "a+")` follows one, and `.gitignore` is a
    file the REPOSITORY controls — a checkout can point it at `~/.zshrc` or any
    other writable path and this function would append two lines there, outside
    the work tree, contradicting the one thing its caller promises. Git itself
    does read a symlinked `.gitignore`, so refusing costs a real repository its
    entry; writing through one costs an arbitrary file its contents, and that is
    not a trade to make silently on someone else's machine.

    THE READ AND THE APPEND STILL HAVE TO BE ONE STEP ON THE WRITE PATH.
    `trace-op.sh` is one process per PostToolUse, so several fires can be inside
    this function at once on a repo whose entry is still absent — each reads
    "missing", each appends, and the consumer gets the block two or three times.
    Measured with a barrier: 20 of 25 trials duplicated it; 0 of 160 realistic
    concurrent fires did, because the critical section is tens of microseconds
    against tens of milliseconds of process startup. Rare, not impossible, and
    it is someone else's file.

    ⚠️ WHICH IS WHY THE RE-READ AND RE-CHECK UNDER THE LOCK ARE NOT REDUNDANT
    WITH THE FAST PATH ABOVE THEM, and must not be deleted as a duplicate of it.
    The fast-path read is deliberately UNLOCKED, so every concurrent fire can
    see "missing" at the same instant and all of them reach the write branch.
    Refusing the lock only stops the fires that COLLIDE with the winner; a fire
    that reaches `flock` after the winner has released it takes the lock
    cleanly, and the only thing standing between it and a second block is that
    it reads the file again first. Measured 2026-08-15 by deleting exactly the
    two-line `if _already_ignored(existing): return` below from a copy of this
    module and re-running the barrier: 21 of 25 trials wrote the block twice
    (with it, 40 of 40 wrote it once). `tests/test_pcf28_consumer_gitignore.py::
    test_concurrent_first_writes_add_the_block_once` (16 barrier-synchronised
    threads, exactly one entry) is the pin, and it is the sensitive one now.

    THE LOCK IS NON-BLOCKING, AND A CONTENDED FIRE SIMPLY DECLINES. This is a
    file the plugin does not own, on a path that runs inside a PostToolUse hook:
    waiting on it means the hook waits. Measured on this machine 2026-08-15,
    python3 3.12.2, one `ensure_ignored` call ON A REPO WHOSE ENTRY IS STILL
    ABSENT — i.e. reaching the write path — while another fd held `LOCK_EX` on
    the `.gitignore` for 200 ms (4 runs, each a single unwarmed call): the old
    blocking `LOCK_EX` returned after 202.4-205.2 ms, `LOCK_NB` returns in
    83-98 us. That figure is the WRITE path's, deliberately: on a settled repo
    the fast path below returns before any lock is attempted, so contention on
    that file costs a steady-state fire nothing at all. Declining costs nothing
    either: the entry is still owed and the next fire writes it, which is the
    same self-healing property the unlocked read preserves.

    ⚠️ THE MICROSECONDS ARE NOT WHAT THIS BUYS, and the earlier version of this
    paragraph — which called a stat-first shape "a real follow-up" — was aiming
    at the wrong cost. Measured 2026-08-15 (N=2000 in-process calls, min of 7
    runs, python3 3.12.2): a settled repository's fire went 30.3 us -> 21.6 us.
    The remainder is dominated by the `open()` the read needs, not by the lock —
    an uncontended `flock` measures 0.25 us on the same harness — so the fast
    path trades one `O_RDWR|O_APPEND|O_CREAT` open for one `O_RDONLY` open and
    keeps everything else. What it removes is the BLOCK: on a settled repo
    (every fire after the first, forever) no lock is taken at all, so nothing
    else holding that file's lock can hold up a capture hook. Both facts are
    pinned by test, the second by
    `test_a_fire_on_a_settled_repo_takes_no_blocking_lock`.

    EVERY FIRE STILL READS THE FILE, and that is a requirement rather than a
    leftover: it is what makes the entry SELF-HEALING when someone deletes it.
    A `stat`-first or `create-on-FileNotFoundError` shape would be faster still
    and would lose exactly that.

    ON A HOST WITHOUT `fcntl` (Windows) THERE IS NO LOCK, so the duplicate the
    paragraph above describes is reachable there rather than merely rare. The
    worst case is the same: one repeated comment-plus-entry block in a consumer
    `.gitignore`, once, with the directory correctly ignored either way. Stated
    rather than defended, because the alternative — a lock file beside someone
    else's `.gitignore` — is a worse artifact than the one it would prevent.

    `a+` rather than `r` then `a` on the write path: one handle, one inode, so
    the lock covers both halves. The file is created by the open, which only
    matters in the branch that was going to write anyway."""
    if os.path.islink(gitignore):
        return
    if os.path.exists(gitignore) and not os.path.isfile(gitignore):
        return  # a directory, a fifo, a device — not something to append to

    # FAST PATH — read-only, unlocked. A partial read of a concurrent append
    # cannot mislead in the dangerous direction: a torn block scans as "not
    # ignored", which sends this fire to the write path, where it re-reads the
    # settled bytes under the lock and returns without writing.
    try:
        with open(gitignore, "r", encoding="utf-8") as fh:
            if _already_ignored(fh.read()):
                return
    except FileNotFoundError:
        pass  # no file yet — the write path below creates it
    except OSError:
        return  # unreadable: there is nothing this can safely do

    with open(gitignore, "a+", encoding="utf-8") as fh:
        if _fcntl is not None:
            try:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError:
                return  # someone else is writing it; the next fire retries
        fh.seek(0)
        existing = fh.read()
        if _already_ignored(existing):
            return
        # A file whose last line has no newline is common, and appending to it
        # blind produces `*.log.fairmind/` — one broken pattern and one missing
        # one. Open the block with a newline whenever the file does not end in
        # one, and never touch the bytes already there.
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        fh.seek(0, os.SEEK_END)
        fh.write("%s%s\n%s\n" % (prefix, _IGNORE_HEADER, _IGNORE_ENTRY))


def makedirs_ignored(directory):
    """`os.makedirs(directory, exist_ok=True)` plus the PCF-28 ignore entry, as
    the one call every writer that can create `.fairmind/` makes instead.

    ONE FUNCTION, asked by every site, rather than the entry being added where
    the defect was reported: a rule enforced at one of its sites and not the
    others is the shape this track has already shipped three times.

    THE `OSError` PROPAGATES, exactly as the bare `os.makedirs` each call site
    used to make did. Swallowing it here would silently move every caller's
    failure diagnostic downstream — `harness_audit` would surface an
    unwritable output directory as a `FileNotFoundError` on the first
    `open()` instead of on the directory — so the error behaviour of all nine
    call sites is unchanged and only the ignore entry is added."""
    os.makedirs(directory, exist_ok=True)
    ensure_ignored(directory)
