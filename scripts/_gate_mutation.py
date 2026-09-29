#!/usr/bin/env python3
"""_gate_mutation.py — git-grounded mutation tracking for the executed gate.

The gate's ground truth for "what changed": the mutation set
(`compute_mutation_set`, diffed against a frozen baseline ref with `git diff
--numstat` plus untracked-file scans), work-dir and worktree resolution
(`resolve_work_dir`, `_working_tree_sha`), the trace path a hook's
PostToolUse events land in (`trace_path`, `_normalize_trace_target`), scope
enforcement against the contract's `allowed_paths` (`evaluate_scope`), the
no-work re-evaluation signature (`_no_work_signature`), and the settle window
a confirmed-green run ages through (`_settle_age`). Also carries the three
UTC time primitives (`now_utc`/`iso`/`_parse_iso`) that `evaluate_scope` and
`_settle_age` need outside `run_gate_checks.py`'s own core.

Stdlib only — no plugin-local import, and no network (every git subprocess
here is local and read-only). `run_gate_checks.py` imports every name here
back, so `roc.now_utc`, `roc.compute_mutation_set` and the rest keep
resolving exactly as before for a caller that reaches them through
`run_gate_checks`'s namespace (an import binding, not a copy). But every
name a function DEFINED HERE reads bare — including `subprocess`, `re`,
`os`, and every sibling function in this module — resolves against THIS
module's own globals, not `run_gate_checks`'s: a seam that needs to
intercept behaviour inside one of these functions (a monkeypatch, a text
patch, or any other injection) has to target `_gate_mutation`, never
`run_gate_checks`, however the call arrived there.
"""

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone



def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(value):
    """Parse an ISO datetime string to an *aware* UTC datetime, or return None if
    absent/garbage/wrong type. The single predicate both `run_gate`'s stamping
    site and `budget_exhausted`'s deadline guard use to agree on what counts as a
    *resolved* `started_at` — so "the stamp is missing" and "the deadline
    can't be computed" can never disagree with each other.

    A naive result (no tzinfo) is pinned to UTC before returning: `budget_
    exhausted` subtracts this from an aware `now_utc()`, and subtracting a naive
    from an aware datetime raises `TypeError` — a hand-written or externally
    supplied `started_at` lacking a "+00:00" offset would otherwise crash the
    deadline guard (exit 1 on every Stop, a wedged loop) instead of resolving.
    A trailing "Z" (UTC designator) is normalized to "+00:00" so it parses on
    every supported Python, not only 3.11+."""
    if not isinstance(value, str) or not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- mutation set (T18) ------------------------------------------------------
# Ground-truths the loop's mutation set against git rather than the trace.
# `hooks/scripts/trace-op.sh` classifies Write/Edit/MultiEdit/NotebookEdit as
# `kind: "mutate"` and Bash as `kind: "exec"`, so a file changed by a *script*
# invoked through Bash (e.g. a heredoc write) leaves no mutate trace op for
# that path — proven live in the T14-T15 internal run (finding F12). Git
# decides WHAT changed; the trace only decorates WHO changed it. Consumed by
# the T8 scope-boundary hard stop (`evaluate_scope`, in the next section) —
# this section only defines the helper itself.

MUTATION_SET_DEGRADED_NO_GIT = "no-git-work-tree"
# A git query INSIDE a real work tree can still fail (unresolvable arm_ref
# after a reset/gc, a loop-state copied into another clone, a typo). Such a
# failure must never be swallowed to `[]`: with a bad arm_ref the
# tracked-modified half of the set would vanish silently while the untracked
# half kept landing, yielding a healthy-looking, non-empty, PARTIAL set —
# worse than an empty one, because nothing signals what is missing.
MUTATION_SET_DEGRADED_GIT_QUERY_FAILED = "git-query-failed"
# No arm-time sha to diff against. Without a baseline ref there is no instant
# to measure "changed since" FROM, so the set is UNKNOWN — not empty. This is
# its own marker (never folded into `git-query-failed`) because its remedy is
# specific and nameable: re-arm the loop, or write the sha. The alternative —
# letting a `None` ref reach a git argv — raised a raw TypeError that escaped
# `_GitQueryError`, so the gate exited 1, never saved state, and left `status`
# stuck on "running": every subsequent Stop re-crashed and `--arm` refuses a
# running loop, so the documented re-arm recovery did not exist.
MUTATION_SET_DEGRADED_NO_BASELINE_REF = "no-baseline-ref"

# The loop's own workspace. Everything under it (loop-state.json, the trace
# ledger, journals) is the loop's BOOKKEEPING, not the run's work product, and
# is excluded from the mutation set STRUCTURALLY — by this constant, not by
# `--exclude-standard` and therefore not by the consumer repo's .gitignore.
# Relying on the ignore was a hard-stop-on-our-own-bookkeeping bug: in a repo
# that had not gitignored `.fairmind/`, the gate's own state file and trace
# landed in the untracked half of the set and tripped the scope boundary
# against the loop itself. `--exclude-standard` stays — it still serves the
# CONSUMER's ignores (build output, node_modules, ...), which are genuinely not
# ours.
#
# SINCE PCF-28 THE PLUGIN DOES MANAGE THAT IGNORE ENTRY (`_fm_ignore.
# ensure_ignored`, called by every writer that can create the directory), which
# falsifies the sentence this comment used to carry — "nothing in the plugin
# ever establishes that precondition (the zero-config bootstrap writes
# active-context.json and never touches .gitignore)". THE STRUCTURAL EXCLUSION
# IS STILL REQUIRED and must not be "reconciled" away: the entry is added on the
# FIRST write, so a consumer repo that already has `.fairmind/` is unprotected
# until something writes again; it is never added outside a git work tree, nor
# when the plugin's directory is not at the repository toplevel; and git ignores
# `.gitignore` entirely for paths already TRACKED, so a repo that once committed
# something under `.fairmind/` keeps it in the set. Three ways back to the
# original bug, all of which this constant closes unconditionally.
LOOP_WORKSPACE_DIR = ".fairmind"


def _is_loop_workspace_path(path):
    """True for a repo-relative path inside the loop's own workspace, which is
    never a member of the mutation set — gitignored or not, tracked or not."""
    return path == LOOP_WORKSPACE_DIR or path.startswith(LOOP_WORKSPACE_DIR + "/")


def _working_tree_sha(cwd, path):
    """A content hash (sha256 of the raw bytes) of the working-tree file at
    `<cwd>/<path>`, or None when it cannot be read (missing, a directory,
    unreadable). The SAME function anchors a pre-dirty path at arm time
    (`pre_dirty_anchors`) and re-checks it in `compute_mutation_set`, so
    "unchanged since arm" is a byte-identity test the loop cannot bluff. It is a
    content anchor, never a security signature — both sides of the comparison
    are the loop's own tree; only equality against the recorded anchor matters."""
    abspath = path if os.path.isabs(path) else os.path.join(cwd, path)
    try:
        with open(abspath, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return "sha256:" + hashlib.sha256(data).hexdigest()


def pre_dirty_anchors(cwd, paths):
    """Build `contract.mutation_set.baseline.pre_dirty` in its ANCHORED shape,
    `[{"path": <repo-relative str>, "sha": <content hash or None>}, ...]`, for
    the given already-dirty paths — called at ARM time so each pre-dirty path is
    frozen to the exact bytes it had when the loop started. `compute_mutation_
    set` marks such a path `pre_existing` ONLY while it stays byte-identical to
    this anchor: a pre-dirty path rewritten mid-run becomes an ordinary member
    subject to scope, so a file dirty at arm can no longer be edited out of
    scope for free. A path whose bytes cannot be read is anchored `None` (it can
    never prove unchanged → fails closed to a scoped member)."""
    return [{"path": p, "sha": _working_tree_sha(cwd, p)} for p in paths]


class _GitQueryError(Exception):
    """Raised by a git-query helper on a non-zero exit. Carries the exact
    "<argv> -> exit <code>: <stderr>" string `compute_mutation_set` surfaces
    verbatim as the degraded result's "error" field — never swallowed to []."""


def _is_git_work_tree(cwd):
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=cwd, capture_output=True, text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _run_git_query(cwd, *args):
    """Run one git subcommand used to build the mutation set. Returns stdout
    on success; raises `_GitQueryError` on a non-zero exit rather than
    swallowing the failure to an empty list — an empty return here used to be
    indistinguishable from "genuinely nothing to report" (AC6).

    Decoding is pinned to UTF-8 with `surrogateescape` rather than left to the
    process locale: under a C/POSIX locale the default decoder would raise on
    the very non-ASCII path bytes `-z` exists to deliver intact, and a decode
    crash here is a wedged gate, not a degraded one."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          encoding="utf-8", errors="surrogateescape")
    if proc.returncode != 0:
        stderr = " ".join(proc.stderr.split())  # whitespace-collapsed, trimmed
        raise _GitQueryError(
            f"git {' '.join(args)} -> exit {proc.returncode}: {stderr}")
    return proc.stdout


def _split_nul(stdout):
    """Split NUL-delimited git output. `-z` is the ONLY way to read a path from
    git verbatim: without it git C-quotes any path with a non-ASCII byte, a
    space or a quote — `src/caffè.ts` arrives as the literal 9-token string
    `"src/caff\\303\\250.ts"`, quotes and octal escapes included. That mangled
    string matches no glob, no pre_dirty entry and no trace target, so a file
    INSIDE the declared scope was reported as an out-of-scope violation and
    hard-stopped the loop. Newline-splitting was also wrong for a path that
    legitimately contains a newline; NUL cannot appear in a path at all."""
    return [p for p in stdout.split("\0") if p]


def _git_changed_paths(cwd, arm_ref):
    """Staged + unstaged + committed-since-arm, diffed against the frozen
    `arm_ref` sha captured at arm time — never live HEAD. This repo's loops
    commit mid-run (T16 committed while running), and diffing live HEAD would
    silently erase every already-committed mutation from the set — the same
    false-empty failure mode T18 exists to kill. Raises `_GitQueryError`
    (never returns a silent partial result) if `arm_ref` cannot be resolved.

    `--no-renames` is load-bearing, not a style choice. Git's default rename
    detection collapses a move into a single R entry and `--name-only` then
    prints ONLY the destination: `git mv legacy/old.ts src/old.ts` under scope
    `src/**` reported exactly `['src/old.ts']` and passed — the out-of-scope
    half of the move, the DELETION of `legacy/old.ts`, never appeared in the
    set at all. Splitting the rename restores both sides, which is what the
    boundary is actually asserting over."""
    stdout = _run_git_query(cwd, "diff", "--name-only", "-z", "--no-renames", arm_ref)
    return _split_nul(stdout)


def _git_untracked_paths(cwd):
    """Untracked paths. `--exclude-standard` honors the CONSUMER's .gitignore
    (build output, node_modules, ...) and is kept for that reason — but it is
    NOT what keeps the loop's own bookkeeping out of the set: `.fairmind/` is
    excluded structurally, by `_is_loop_workspace_path`. Since PCF-28 the plugin
    does add that ignore entry itself, so `--exclude-standard` would often cover
    it now — see `LOOP_WORKSPACE_DIR` for the three ways it still would not, and
    why the structural exclusion is the guarantee and the entry is not. Raises
    `_GitQueryError` (never a silent partial result) if the query fails."""
    stdout = _run_git_query(cwd, "ls-files", "--others", "--exclude-standard", "-z")
    return _split_nul(stdout)


def _resolve_repo_root(cwd):
    """`realpath(git rev-parse --show-toplevel)` — the anchor the trace's
    absolute `target`s are normalized against. Returns None if the query
    fails (e.g. a stripped-down or corrupted work tree): attribution then
    becomes UNAVAILABLE (every path reports agent "unknown"), but this is
    NOT a degraded marker — git already answered the membership question via
    `_is_git_work_tree`/`_git_changed_paths`/`_git_untracked_paths`; only the
    "who" half is lost, not the "what"."""
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return os.path.realpath(proc.stdout.strip())


def _normalize_trace_target(target, repo_root):
    """Normalize one trace op's `target` to a POSIX, repo-relative path
    comparable to git's own output, or return None if it cannot be
    attributed. `trace-op.sh` writes `target` as the tool's raw
    `tool_input.file_path` — verified ABSOLUTE against this loop's own real
    trace — while git reports repo-relative paths; comparing them raw never
    matches (the amendment-2 bug: every path came back "unknown", including
    ones the trace explicitly attributed). Rules, in order, per
    `contract.mutation_set.attribution.normalization`:

      1. empty / non-string target -> unattributable.
      2. target ending in "..." -> the hook's truncation marker
         (`tr(s, n=120)`, trace-op.sh) -> UNATTRIBUTABLE. Never prefix-,
         substring- or fuzzy-matched: a wrong attribution is worse than
         "unknown".
      3. absolute target -> `relpath(realpath(target), repo_root)`.
         `realpath` BOTH sides (a symlinked temp root, e.g. macOS /tmp ->
         /private/tmp, would otherwise never join). A result that escapes
         the tree (starts with "..") -> unattributable (the op is ignored;
         the git-reported path, if any, is never dropped because of it).
      4. already-relative target -> accepted unchanged (liberal in what we
         accept — the hook's format may vary by tool/version).
      5. POSIX "/" separators — the form git uses.
    """
    if not target or not isinstance(target, str):
        return None
    if target.endswith("..."):
        return None
    if os.path.isabs(target):
        if repo_root is None:
            return None  # attribution unavailable — see _resolve_repo_root
        rel = os.path.relpath(os.path.realpath(target), repo_root)
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return None  # escapes the work tree — ignore the op
        target = rel
    return target.replace(os.sep, "/")


def _load_trace_attribution(trace_path, repo_root):
    """path -> agent, sourced from `kind == "mutate"` trace lines only (an
    "exec" line must never attribute, even if its `target` string happens to
    match a mutated path). Each `target` is normalized via
    `_normalize_trace_target` before it can join a git-reported path — see
    that function for the absolute/truncated/escaping-target rules. A
    missing trace file, an unreadable line, an unattributable target, or no
    `trace_path`/`repo_root` at all yields no entry for that op — never
    raises, never drops a git-reported path; callers default unattributed
    paths to "unknown".

    Contested paths (more than one mutate op normalizing to the same path)
    resolve to the LAST op in trace order: the trace is append-only
    chronological, so later entries overwrite earlier ones in this dict —
    last-writer-wins. `agent` is reported verbatim, never reformatted."""
    attribution = {}
    if not trace_path or not os.path.isfile(trace_path):
        return attribution
    with open(trace_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                op = json.loads(line)
            except ValueError:
                continue
            if not isinstance(op, dict):
                continue  # a bare JSON scalar/array is not an op — never raise on .get()
            if op.get("kind") != "mutate":
                continue
            agent = op.get("agent")
            if not agent:
                continue
            normalized = _normalize_trace_target(op.get("target"), repo_root)
            if normalized is None:
                continue
            attribution[normalized] = agent  # last mutate op wins (append-only order)
    return attribution


def compute_mutation_set(cwd, arm_ref, pre_dirty=None, trace_path=None):
    """The loop's mutation set, ground-truthed against git — see
    `skills/fairmind-gate/references/loop-state.md` (`contract.mutation_set`)
    for the frozen contract this implements.

    Membership is `union(git diff --name-only <arm_ref>, git ls-files
    --others --exclude-standard)` MINUS the loop's own workspace (`.fairmind/`,
    dropped structurally via `_is_loop_workspace_path`, gitignored or not),
    never trace `kind == "mutate"` alone: a script writing through Bash leaves
    no mutate op for its target (F12), so a trace-only view silently misses it.
    `arm_ref` must be the sha frozen at arm time, not live HEAD (see
    `_git_changed_paths`).

    Attribution decorates, it does not filter: a git-reported path with a
    matching `mutate` trace op — after normalizing that op's `target` to a
    repo-relative POSIX path, see `_normalize_trace_target` — is tagged with
    that op's agent; anything else (no trace file, no matching op after
    normalization, a truncated/out-of-tree target, or a non-mutate op on the
    same target) is reported with `agent: "unknown"` — never dropped. If the
    repo-root query itself fails (`_resolve_repo_root` returns None),
    attribution is UNAVAILABLE — every path reports "unknown" — but
    membership stays intact and this is explicitly NOT a degraded marker
    (git already answered "what changed"; only "who" is unresolved).

    `pre_dirty` (paths already dirty at arm time, supplied by the caller —
    git alone cannot tell *when* a path became dirty) marks a matching path
    `pre_existing: True` ONLY while it is byte-identical to its arm-time content
    anchor; a pre-dirty path rewritten during the run becomes an ordinary
    member, fully subject to scope. Two shapes are accepted: the anchored
    `[{"path","sha"}]` shape (`pre_dirty_anchors`, written by `--arm`) and a
    legacy bare-string list — but a legacy / anchor-less entry cannot be proven
    unchanged and FAILS CLOSED to `pre_existing: False`. Every member is still
    reported, never dropped; policy on them is T8's, not this helper's.

    Three distinguishable degraded markers, checked in this order, `paths`
    always `[]` for any of them:
      - no baseline ref at all (`arm_ref` None/empty) →
        `{"degraded": "no-baseline-ref", "paths": []}`. Checked FIRST, before
        any git argv is built, so a None ref can never reach `git diff` as a
        raw TypeError. The set is UNKNOWN (no instant to measure "changed
        since" FROM), never empty; remedy is re-arm / write the sha.
      - no git work tree at all → `{"degraded": "no-git-work-tree", "paths": []}`.
      - inside a work tree, but a git query used to build the set fails
        (unresolvable `arm_ref`, corrupted object, ...) →
        `{"degraded": "git-query-failed", "paths": [], "error": "<failing
        git argv> -> exit <code>: <stderr>"}`. This is a WHOLE-set failure:
        if either the changed-paths query or the untracked-paths query
        fails, the other query's (possibly successful) result is discarded
        too — a partial set would look like a healthy, non-empty set while
        actually missing half its members (AC6).

    A consumer MUST treat any non-null `degraded` as "the mutation set is
    UNKNOWN", never as "nothing was mutated" — fail closed / surface to a
    human rather than silently proceeding as if the set were empty.
    `paths` is sorted by path for determinism when not degraded.
    """
    # No baseline ref → no arm-time instant to diff "changed since" FROM, so the
    # set is UNKNOWN, not empty. Checked BEFORE any git argv is built: a
    # None/empty ref reaching `git diff <ref>` raises a raw TypeError that
    # escapes `_GitQueryError`, crashing the gate (exit 1, state never saved)
    # instead of degrading. Its own marker — the remedy is re-arm / write the sha.
    if not arm_ref:
        return {"degraded": MUTATION_SET_DEGRADED_NO_BASELINE_REF, "paths": []}

    if not _is_git_work_tree(cwd):
        return {"degraded": MUTATION_SET_DEGRADED_NO_GIT, "paths": []}

    try:
        changed = _git_changed_paths(cwd, arm_ref)
        untracked = _git_untracked_paths(cwd)
    except _GitQueryError as exc:
        # WHOLE-set failure: neither query's result is used, even if the
        # other one succeeded — a partial set is indistinguishable from a
        # complete one and is exactly the false-negative AC6 closes.
        return {"degraded": MUTATION_SET_DEGRADED_GIT_QUERY_FAILED,
                "paths": [], "error": str(exc)}

    # The loop's own workspace (`.fairmind/`) is BOOKKEEPING, never work product.
    # Drop it from both halves STRUCTURALLY — not via `--exclude-standard` —
    # because a consumer repo is under no obligation to have gitignored it, and
    # without this the gate's own state file and trace ledger land in the
    # untracked half and trip the scope boundary against the loop itself.
    members = {p for p in (set(changed) | set(untracked))
               if not _is_loop_workspace_path(p)}
    repo_root = _resolve_repo_root(cwd)  # None -> attribution unavailable, NOT degraded
    attribution = _load_trace_attribution(trace_path, repo_root)

    # pre_dirty carries a per-path arm-time content anchor. A member is
    # pre_existing ONLY while byte-identical to its anchor — a pre-dirty path
    # rewritten during the run is an ordinary member, fully subject to scope.
    # Two shapes are accepted: the anchored [{"path","sha"}] shape (`--arm`
    # writes it via `pre_dirty_anchors`) and the legacy bare-string list. A
    # legacy entry — or an anchored entry whose sha could not be captured —
    # carries NO proof of arm-time content, so it CANNOT be shown unchanged and
    # FAILS CLOSED to pre_existing=False (never exempt on path membership alone).
    anchor_by_path = {}
    for entry in pre_dirty or []:
        if isinstance(entry, dict) and entry.get("path"):
            anchor_by_path[entry["path"]] = entry.get("sha")
        # a legacy bare string records no anchor → absent here → fails closed

    def _pre_existing(path):
        anchor = anchor_by_path.get(path)
        if not anchor:
            return False  # legacy / anchor-less → cannot prove unchanged (fail closed)
        return _working_tree_sha(cwd, path) == anchor

    paths = [
        {
            "path": path,
            "agent": attribution.get(path, "unknown"),
            "pre_existing": _pre_existing(path),
        }
        for path in sorted(members)
    ]
    return {"degraded": None, "paths": paths}


def _mutation_baseline(state):
    """`contract.mutation_set.baseline` — the arm-time freeze — or `{}`.

    ONE reader for a nested path four call sites now need (`evaluate_scope`,
    `_no_work_signature`, and JC2's two `_numstat` sites). The `or {}` at every
    level is what lets a legacy / hand-edited state degrade here instead of
    raising: an absent baseline is "the set is UNKNOWN", which every caller
    already handles, and a TypeError is not."""
    return ((state.get("contract") or {}).get("mutation_set") or {}).get("baseline") or {}


def _head_sha(cwd):
    """`git rev-parse HEAD` in `cwd`, or None on ANY git failure — outside a
    work tree, in a repo with zero commits, on a broken object store.

    None means the key is OMITTED by the caller — never null, never a marker
    string (JC1/§B.1). This is the engine's own fail-soft contract, and it is
    exactly why `audit_run_meta.collect_run_meta()` is NOT called from the gate
    path (§B.5): it RAISES on both conditions above, where the gate must
    degrade. `--record-transition`, a verb a human just typed and which can
    afford to refuse, does reuse it (and falls back here for the one condition
    that reaches its `except` without git having failed — see that call site).

    EVERY CALLER OF THIS HELPER MUST DEGRADE RATHER THAN REFUSE, which is what
    the three have in common: the gate path's per-iteration sha, `arm`'s
    mutation-set baseline freeze — outside a git tree the baseline is simply
    left unfrozen and the scope boundary then fails closed on `no-baseline-ref`,
    where refusing would block arming a loop in a non-git tree, which is legal —
    and `--record-transition`'s fallback just named."""
    try:
        return _run_git_query(cwd, "rev-parse", "HEAD").strip() or None
    except _GitQueryError:
        return None


def _numstat_count(field):
    """One `--numstat` count column as an int. Git writes `-` for a BINARY
    file (no line concept), which contributes to `files` and to neither line
    total — see `_numstat`."""
    try:
        return int(field)
    except ValueError:
        return 0


def _numstat_no_index(work_dir, path):
    r"""`(insertions, deletions)` for a path git is not tracking, or None.

    ⚠️ `git diff --no-index` exits **1 BY DESIGN when there is a difference** —
    which is the only case we ever call it for. Measured 2026-08-14 in a
    throwaway repo: a new 4-line file gives `4\t0\t/dev/null => brandnew.txt`
    and exit **1**; two empty inputs give no output and exit **0**. Treating
    that 1 as a failure is the obvious way to get a silent zero, so 0 and 1 are
    both success here and anything else degrades to None.

    Run through `subprocess` directly rather than `_run_git_query`, which
    raises on every non-zero exit — the one place in this engine where that
    contract does not fit, stated here rather than by widening it for
    everyone."""
    proc = subprocess.run(
        ["git", "diff", "--numstat", "--no-index", "--", os.devnull, path],
        cwd=work_dir, capture_output=True, encoding="utf-8", errors="surrogateescape",
    )
    if proc.returncode not in (0, 1):
        return None
    insertions = deletions = 0
    for line in proc.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        insertions += _numstat_count(fields[0])
        deletions += _numstat_count(fields[1])
    return insertions, deletions


def _numstat(work_dir, arm_ref, members=None):
    r"""`{"files", "insertions", "deletions"}` for the loop's whole mutation set,
    or None on any git failure. JC2's `stratification.diff_size` (§B.8).

    `members` is the member PATH LIST when the caller ALREADY HOLDS ONE — both
    call sites do, `_no_work_signature` having just built the identical set from
    the identical `(work_dir, arm_ref)` — and None to walk the tree here.
    Passing it is not a cache: same process, same instant, same tree, no
    staleness window to reason about. Measured 2026-08-14 with a counting `git`
    shim (a script that logs its argv then execs the real git) over a real armed
    loop driven to its flip: the close ran `rev-parse --is-inside-work-tree`,
    `diff --name-only -z`, `ls-files --others --exclude-standard` and
    `rev-parse --show-toplevel` **twice each** — 10 git subprocesses at 0
    untracked files, 6 after. That is a FIXED saving of 4, on top of which the
    per-untracked-member walk below adds its own +1 each, unchanged and
    deliberately so (⚠️ there). A caller whose signature DEGRADED passes None
    and degrades exactly as before: the walk below runs and returns None.

    COUNTS ONLY — `--numstat` emits per-file added/removed line counts and never
    a hunk, which is what keeps this axis inside the no-content-bytes rule.
    No S/M/L bucket here: the bucket is cut in the payload builder, so the
    thresholds have exactly one home to re-cut when a corpus exists.

    ⚠️ WHY THE MUTATION SET AND NOT THE PLAIN DIFF. `git diff --numstat <ref>`
    is BLIND to untracked files. Reproduced by execution 2026-08-14 in a
    throwaway repo — one tracked file gaining 2 lines beside a new 4-line
    untracked file:

        $ git diff --numstat HEAD
        2   0   tracked.txt          # brandnew.txt does not appear at all

    In loop mode all work is uncommitted and a brand-new deliverable file stays
    untracked until PR time, so the plain diff would report
    `{"files": 0, ...}` for a loop whose entire output is new modules — and the
    S/M/L thresholds would then be cut against systematically biased-low
    numbers, including by the "re-measure after ~30 loops" instruction, which
    would re-cut on the same bias. `compute_mutation_set` already unions
    `git diff --name-only <arm_ref>` with `git ls-files --others
    --exclude-standard` and structurally drops `.fairmind/**`, so reusing it
    makes `diff_size` and `stratification.language` describe THE SAME SET —
    the property that lets a reader hold one against the other. THE SET IS THE
    SAME WHETHER `pre_dirty`/`trace_path` ARE PASSED OR NOT — both decorate
    members (`pre_existing`, `agent`), neither changes membership (:739-740) —
    which is exactly what makes the `members` handoff above safe, since the
    caller builds its set WITH those two and the walk below without them.
    Verified by execution 2026-08-14 on a fixture carrying a file dirty at arm,
    a file dirty at arm and rewritten since, a tracked file first touched after
    arm and an untracked one, with a trace attributing one of them: the two
    member lists were identical while `pre_existing` and `agent` genuinely
    differed across paths (so the fixture was not vacuous).

    `files` is the member count, not the number of numstat rows: a binary file
    (`-\t-`) and a brand-new EMPTY file both belong to the set while
    contributing no lines, and counting rows would silently drop them.

    Record parsing is measured, not assumed. `git diff --numstat -z
    --no-renames <ref>` writes `<add>\t<del>\t<path>\0` per file (verified
    with `od -c` 2026-08-14, binary rows included: `-\t-\tbin.dat\0`). `-z` is
    load-bearing for the same reason `_split_nul` documents — without it git
    C-quotes any non-ASCII path and the member match below would silently miss
    it. `--no-renames` matches `_git_changed_paths`, so both halves of a move
    are counted exactly as the mutation set already reports them.

    Degrades to None on any git failure, matching every other query here."""
    if members is None:
        mutation_set = compute_mutation_set(work_dir, arm_ref)
        if mutation_set.get("degraded"):
            return None
        members = [p["path"] for p in mutation_set["paths"]]
    if not members:
        # A real, comparable answer: nothing has been touched since arm. Not a
        # degradation — the same distinction `_no_work_signature` draws.
        return {"files": 0, "insertions": 0, "deletions": 0}

    member_set = set(members)
    insertions = deletions = 0
    counted = set()
    try:
        stdout = _run_git_query(work_dir, "diff", "--numstat", "-z", "--no-renames", arm_ref)
    except _GitQueryError:
        return None
    for record in stdout.split("\0"):
        if not record:
            continue
        fields = record.split("\t")
        if len(fields) != 3:
            continue  # not a numstat record (a rename pair's path halves)
        added, removed, path = fields
        if path not in member_set:
            continue  # `.fairmind/**` and anything else the set excludes
        counted.add(path)
        insertions += _numstat_count(added)
        deletions += _numstat_count(removed)

    for path in members:
        if path in counted:
            continue
        # Every remaining member is untracked (that is the other half of the
        # union `compute_mutation_set` builds), so git's own diff cannot see it.
        #
        # ⚠️ THIS LOOP IS ONE SUBPROCESS PER UNTRACKED MEMBER AND IT STAYS THAT
        # WAY — DO NOT "OPTIMISE" IT. Measured 2026-08-14 with a counting `git`
        # shim over a real armed loop driven to its flip: a close costs 6 git
        # calls at 0 untracked files and 56 at 50, i.e. exactly +1 here per
        # member, linear. The batching alternative that was measured and works
        # — a throwaway `GIT_INDEX_FILE` plus `git add -A`, then one diff — is
        # REJECTED ON PRINCIPLE, not on cost: it WRITES LOOSE OBJECTS INTO THE
        # USER'S OBJECT STORE during what is conceptually a measurement, and a
        # gate that mutates the repository it is measuring is a gate nobody can
        # trust. The slope is only ever paid at a close, never per evaluation
        # (the mid-loop signature costs a flat 5), so there is no hot path here
        # to buy back.
        stat = _numstat_no_index(work_dir, path)
        if stat is None:
            return None  # whole-set failure, never a partial count (AC6's rule)
        insertions += stat[0]
        deletions += stat[1]

    return {"files": len(members), "insertions": insertions, "deletions": deletions}


# --- worktree resolution (H1/F34) --------------------------------------------
# `scripts/loop_worktree.py:144` is the SOLE writer of the top-level
# `state["worktree"] = {"path": ..., "branch": "loop/<ref>"}` key (T9's
# opt-in isolation offer). Before this fix nothing in this engine ever READ
# it: every git query and every check subprocess ran against a single `cwd`
# scalar (== `--cwd` == the STATE root), so a loop that opted into worktree
# isolation had its checks silently evaluated against the MAIN tree while the
# maker's actual change lived only in the worktree (F34) — including the T8
# scope boundary, which then passed VACUOUSLY on a worktree-only mutation.
#
# The fix splits that one scalar into three named, independently-motivated
# roles:
#   state_root  — `--cwd` exactly as resolved by `resolve_state_path`. Owns
#                 state resolution (`load_state`/`save_state`) and the run
#                 ledger (`loop_ledger.record_terminal`). NEVER changed by
#                 worktree resolution.
#   work_dir    — `worktree.path` when (and ONLY when) the state records one
#                 that resolves to a real, currently-registered worktree of
#                 state_root's own repo; else `state_root` (AC3: absent key
#                 -> identical to today). Feeds every git query and every
#                 check subprocess: `compute_mutation_set`, and — via
#                 `run_gate` — `evaluate_check`/`run_command` (functional
#                 checks execute against the tree the maker actually edited).
#   trace_root  — ALWAYS `state_root`. `hooks/scripts/trace-op.sh` writes the
#                 trace file under `<CLAUDE_PROJECT_DIR>/.fairmind/trace/`,
#                 i.e. under state_root, and nowhere else — a worktree's own
#                 `.fairmind/` does not exist at all (it is excluded from the
#                 mutation set structurally, `_is_loop_workspace_path`).
#                 Conflating trace_root with work_dir would silently kill
#                 agent attribution (every path would report "unknown")
#                 without ever touching membership.
#
# `resolve_work_dir` is the ONLY function that decides work_dir. A recorded
# `worktree.path` that cannot be PROVEN to be a real, registered worktree of
# state_root's own repo must NEVER be silently substituted with state_root —
# that silent substitution is exactly F34 wearing a different hat. Every
# failure shape below returns a distinct `(reason, detail)` pair; the caller
# (`run_gate`) fails the WHOLE evaluation closed on any non-None
# degradation — no check may run anywhere, and the condition is named in
# PERSISTED state (`status` + an `iterations[]` audit entry), never only on
# stdout.

def resolve_work_dir(state, state_root):
    """Returns `(work_dir, degradation)`.

    `degradation` is `None` in exactly two cases: no `worktree` key at all
    (AC3 — legitimate, backward-compatible no-op, `work_dir == state_root`),
    or a `worktree.path` that resolves to a real, currently-registered
    worktree of `state_root`'s own repo (`work_dir == that path`).

    Otherwise `work_dir` is `None` and `degradation` is a `(reason, detail)`
    pair naming EXACTLY why the recorded worktree could not be trusted. The
    caller MUST fail the evaluation closed on this path — it must never fall
    back to `state_root`.
    """
    wt = state.get("worktree")
    if wt is None:
        return state_root, None  # AC3: nothing declared -> identical to today

    if not isinstance(wt, dict):
        return None, ("worktree-malformed", f"state['worktree'] is not an object: {wt!r}")

    path = wt.get("path")
    if not path or not isinstance(path, str):
        return None, ("worktree-path-missing",
                      f"state['worktree']['path'] is missing/empty/non-string: {path!r}")

    try:
        exists = os.path.exists(path)
        is_dir = exists and os.path.isdir(path)
    except OSError as exc:
        return None, ("worktree-path-unreadable", f"stat({path!r}) failed: {exc}")

    if not exists:
        # VERIFIED expected shape, not exotic: `loop_worktree.py --cleanup`
        # (`do_cleanup`) removes the worktree's directory AND its git
        # registration but never clears `state["worktree"]`, so a loop that
        # ran --cleanup carries exactly this dangling path indefinitely.
        return None, ("worktree-path-not-found",
                      f"worktree.path does not exist on disk: {path!r}")
    if not is_dir:
        return None, ("worktree-path-not-a-directory",
                      f"worktree.path exists but is not a directory: {path!r}")
    if not _is_git_work_tree(path):
        return None, ("worktree-not-git-work-tree",
                      f"worktree.path is not inside a git work tree: {path!r}")

    # Not merely "a" git work tree — a REGISTERED worktree of THIS state's own
    # repo, ruling out a foreign checkout or a copied loop-state.json pointing
    # at an unrelated repo's worktree. Two work trees linked by `git worktree
    # add` share one object database and therefore one `--git-common-dir`.
    try:
        wt_common = _run_git_query(path, "rev-parse", "--git-common-dir").strip()
        root_common = _run_git_query(state_root, "rev-parse", "--git-common-dir").strip()
    except _GitQueryError as exc:
        return None, ("worktree-git-query-failed", str(exc))

    wt_common_real = os.path.realpath(os.path.join(path, wt_common))
    root_common_real = os.path.realpath(os.path.join(state_root, root_common))
    if wt_common_real != root_common_real:
        return None, ("worktree-not-registered",
                      f"worktree.path {path!r} is not a registered worktree of "
                      f"state_root's own repo ({wt_common_real!r} != {root_common_real!r})")

    return path, None


# --- scope boundary (T8) -----------------------------------------------------
# A loop contract may declare an allowed mutation scope (`contract.scope.
# allowed_paths`, a glob list). `run_gate` cross-checks this run's git-derived
# mutation set (T18's `compute_mutation_set` above) against it BEFORE any
# check verdict is considered: a mutated path outside scope is a TERMINAL hard
# stop (`status: "blocked_scope"`) — never an ordinary red, never counted
# against budget. Membership is git (T18), never the trace; the trace only
# attributes *who* mutated a path, decorating the report for the human.

def _active_context_ref(cwd):
    """The active task ref from `<cwd>/.fairmind/active-context.json` —
    `task_ref`, then `taskRef`, else "session" — resolved EXACTLY as
    `hooks/scripts/trace-op.sh` resolves it, so the gate reads the very file the
    hook wrote. Absent or unreadable → "session"."""
    ctx = os.path.join(cwd, ".fairmind", "active-context.json")
    try:
        with open(ctx, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "session"
    return data.get("task_ref") or data.get("taskRef") or "session"


def trace_path(cwd, ref=None):
    """The production trace-file location for `cwd`: `<cwd>/.fairmind/trace/
    <safe>.jsonl`, `safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or
    "session"` — byte-for-byte the path `hooks/scripts/trace-op.sh` writes and
    `scripts/loop_dashboard.py` reads.

    `ref=None` (the scope boundary's live case) resolves the ref the SAME way
    the hook does — from `<cwd>/.fairmind/active-context.json` — NOT from
    loop-state's `target.ref`. The hook keys the filename off active-context and
    never sees loop-state; the two can differ (a session working a task ref
    other than the loop's target), and reading the wrong file made every path
    "unknown", silently erasing attribution. A caller replaying a HISTORICAL row
    (`loop_dashboard`) already knows the row's ref and passes it explicitly."""
    if ref is None:
        ref = _active_context_ref(cwd)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or "session"
    return os.path.join(cwd, ".fairmind", "trace", f"{safe}.jsonl")


def evaluate_scope(state, cwd, dry_run=False, trace_root=None):
    """Cross-check this run's git-derived mutation set against a declared
    `contract.scope.allowed_paths`. `cwd` here is `work_dir` (H1/F34: the
    tree to diff — the worktree when the state records a valid one, else
    state_root; see `resolve_work_dir`). `trace_root` is the SEPARATE root
    the trace FILE lives under — ALWAYS `state_root` — and defaults to `cwd`
    when omitted, which is exactly today's behavior for every caller that
    predates the worktree split (work_dir == state_root == trace_root when
    no worktree is recorded, AC3). Returns `None` when there is nothing to
    enforce (AC3: no `contract.scope`, or an empty/missing `allowed_paths` —
    identical to pre-T8 behavior; the absence of a trace file is NOT a
    trigger). Otherwise returns `(audit_entry, feedback)` describing a
    terminal `blocked_scope` stop, either because the mutation set itself is
    degraded/unknown (AC5, fail-closed) or because a genuine out-of-scope
    mutation was found (AC1). The audit entry carries a `"degraded"` key
    (the marker, e.g. `"no-git-work-tree"`) ONLY on the degraded path (A1a);
    a real, non-degraded violation's entry has NO `"degraded"` key at all
    (A1b) — presence/absence of the key is itself the persisted signal
    distinguishing "set unknown" from "set known, violation found".

    `evaluate_check`/`admitted_checks` are never consulted here — this must
    run and short-circuit BEFORE any check verdict is considered (see
    `run_gate`), because a scope violation is not a check result and must
    never be folded into (or masked by) the ordinary green/red decision.

    `dry_run` (F26): pre-arm, `contract.mutation_set.baseline.ref` does not
    exist yet — arming is the only thing that ever freezes it (see `arm()`).
    A missing ref is otherwise fail-closed (`no-baseline-ref`, AC5/W1.4) for
    good reason: on an ARMED loop it signals a genuine, unexplained loss of
    the diff anchor. But on a `--dry-run` smoke run of a never-armed loop
    there is no transient to fail closed against — the baseline is simply not
    frozen yet, exactly as expected pre-arm — so failing closed here only
    prevents the smoke run from ever reaching a single check evaluation
    (F26). Scoped STRICTLY to `dry_run AND never armed`: the real (non-dry-run)
    path below is untouched, and still fails closed to `blocked_scope` /
    `no-baseline-ref` on an armed loop missing its baseline (test_scope_
    boundary.py's W1.4, pinned again as a companion guard in
    test_dryrun_scope_smoke.py).

    A1 (adversarial-review amendment): "never armed" is NOT the same
    predicate as "no `arm_ref`" — `arm()` sets `state["status"] = "running"`
    unconditionally but only writes `contract.mutation_set.baseline.ref` when
    the arm-time git query succeeds, so an ARMED loop can still have no
    `arm_ref` (outside a git work tree, on an unborn HEAD, or under a
    transient git failure at arm time). Gating the deferral on `not arm_ref`
    would wrongly wave such a loop's `--dry-run` smoke run through. The
    deferral is keyed on the T19 write-once `budget.spent.first_armed_at`
    marker instead (see `ever_armed` below), which is set by `arm()` and
    never cleared — so an armed-but-baseline-less loop still fails closed
    even under `--dry-run` (test_dryrun_scope_smoke.py's
    test_dry_run_armed_but_no_baseline_still_fails_closed).
    """
    scope = (state.get("contract") or {}).get("scope") or {}
    allowed_paths = scope.get("allowed_paths")
    if not allowed_paths:
        return None  # AC3: no declaration -> backward-compatible no-op

    baseline = _mutation_baseline(state)
    arm_ref = baseline.get("ref")
    # A1 (adversarial-review amendment): the deferral must key off *never
    # armed*, not off `arm_ref` presence. `not arm_ref` is also true for a
    # loop that HAS been armed but couldn't freeze a baseline at arm time
    # (arm() sets status="running" unconditionally, but only writes
    # contract.mutation_set.baseline.ref when _is_git_work_tree(cwd) holds
    # AND rev-parse HEAD resolves AND the arm-time set is not itself
    # degraded) — such a loop must still fail closed under --dry-run, exactly
    # like the non-dry-run path below. `budget.spent.first_armed_at` is the
    # T19 write-once marker `arm()` stamps and never clears, so it is the
    # correct ever-armed signal, computed None-safely against a state that
    # may be missing `budget`/`spent` entirely.
    ever_armed = bool(((state.get("budget") or {}).get("spent") or {}).get("first_armed_at"))
    if dry_run and not ever_armed:
        print("scope enforcement deferred until --arm freezes a baseline (dry-run)",
              file=sys.stderr)
        return None
    pre_dirty = baseline.get("pre_dirty") or []
    # The trace path is resolved through the ONE canonical helper, ref=None →
    # the hook's own active-context.json derivation. NOT state.target.ref: the
    # hook keys the trace filename off active-context, never off loop-state, so
    # keying attribution off target.ref reads a different file and loses every
    # agent to "unknown". Resolved against `trace_root` (ALWAYS state_root,
    # H1/F34) — never `cwd`/work_dir: the hook writes the trace file under
    # state_root's `.fairmind/trace/` and nowhere else, so under a worktree
    # loop the trace FILE stays put while the git diff (below) follows the
    # worktree.
    trace_file = trace_path(trace_root if trace_root is not None else cwd)

    mutation_set = compute_mutation_set(cwd, arm_ref, pre_dirty, trace_file)

    if mutation_set.get("degraded"):
        # AC5 / T18 consumer rule: a degraded set is UNKNOWN, never "nothing
        # mutated" — fail closed rather than risk an undetected out-of-scope
        # mutation slipping through as if the set were empty.
        reason = mutation_set["degraded"]
        detail = f": {mutation_set['error']}" if mutation_set.get("error") else ""
        feedback = (
            "⛔ LOOP STOPPED — blocked_scope: a scope is declared "
            "(contract.scope.allowed_paths) but this run's mutation set could not "
            f"be determined (compute_mutation_set degraded: {reason}{detail}). The "
            "set is UNKNOWN, not empty — failing closed rather than allowing a "
            "possible out-of-scope mutation to go undetected. A human must resolve "
            "the git condition (or re-arm) before this loop can proceed."
        )
        # A1 (amendment): the reason must be readable from the PERSISTED
        # audit entry alone (iterations[], no stdout needed) — naming it only
        # in `feedback` (ephemeral stdout) left a real violation with zero
        # paths indistinguishable from a degraded/UNKNOWN set. The marker is
        # stamped ONLY on this degraded path — never on a real (non-degraded)
        # violation's entry (A1b guard) — so its mere presence means "the set
        # was unknown", never "the set was known and happened to be empty".
        audit_entry = {"event": "scope_violation", "at": iso(now_utc()),
                       "paths": [], "agents": [], "degraded": reason}
        if mutation_set.get("error"):
            audit_entry["error"] = mutation_set["error"]
        return audit_entry, feedback

    violations = [
        p for p in mutation_set["paths"]
        if not p["pre_existing"] and not any(fnmatch.fnmatch(p["path"], g) for g in allowed_paths)
    ]
    if not violations:
        return None  # AC4: every mutated path is in scope

    paths = [v["path"] for v in violations]
    agents = [v["agent"] for v in violations]
    lines = [
        f"⛔ LOOP STOPPED — blocked_scope: {len(violations)} mutated path(s) outside "
        f"the declared scope {allowed_paths!r}:",
    ]
    lines.extend(f"  - {p} (agent: {a})" for p, a in zip(paths, agents))
    lines.append(
        "This is a TERMINAL hard stop — never counted against budget, never an "
        "ordinary check failure. A human must reconcile the scope declaration or the "
        "mutation before this loop can proceed (re-arm after resolving)."
    )
    audit_entry = {"event": "scope_violation", "at": iso(now_utc()), "paths": paths, "agents": agents}
    return audit_entry, "\n".join(lines)


# --- degraded scope self-heal (F28) -----------------------------------------
# A `blocked_scope` stop is TERMINAL by construction (main's `status != "running"`
# guard silent-no-ops every subsequent Stop) — correct for a REAL violation,
# which only a human `--arm` should resolve. But a DEGRADED stop (the mutation
# set itself was UNKNOWN — a git hiccup, a worktree momentarily unavailable,
# the pre-arm no-baseline-ref condition on an already-armed loop, ...) may be
# purely transient: the very next Stop could find the set resolvable again with
# nothing to enforce. The persisted `scope_violation` entry's `"degraded"` key
# (present ONLY on the fail-closed path, A1a/A1b — see `evaluate_scope`) is the
# one signal already on disk that tells the two apart, so self-heal keys off it
# rather than off `blocked_scope` status alone.

# Bounds how many CONSECUTIVE degraded re-evaluations a loop will attempt on
# its own before handing back to a human `--arm`, exactly like a real
# violation — a transient that never clears must not retry forever.
DEGRADED_SCOPE_RETRY_CAP = 3


def _trailing_degraded_scope_run(state):
    """Walk `iterations[]` from the end. Returns `(latest, deg)`: `latest` is
    the most recent entry IF it is a `scope_violation` (else None), and `deg`
    is how many CONSECUTIVE trailing `scope_violation` entries carry the
    `"degraded"` key. The walk stops at the first entry that is either not a
    `scope_violation` or a `scope_violation` with NO `"degraded"` key (a REAL
    violation) — a real violation is itself always terminal (no further
    `scope_violation` entries are ever appended after one, since it is never
    auto-recovered), so in practice this never has to break mid-run except at
    exactly that boundary."""
    latest = None
    deg = 0
    for it in reversed(state.get("iterations", [])):
        if it.get("event") != "scope_violation":
            break
        if latest is None:
            latest = it
        if "degraded" not in it:
            break
        deg += 1
    return latest, deg


def _degraded_scope_recoverable(state):
    """True when a `blocked_scope` loop is eligible for a self-heal
    re-evaluation on the NEXT Stop (F28/AC2): the most recent scope_violation
    entry must carry a `"degraded"` key (a REAL violation, carrying none,
    NEVER auto-recovers — AC2(b)) AND the cap must not be reached yet
    (AC2(c)): `deg < DEGRADED_SCOPE_RETRY_CAP`."""
    latest, deg = _trailing_degraded_scope_run(state)
    return latest is not None and "degraded" in latest and deg < DEGRADED_SCOPE_RETRY_CAP


# --- no-work re-evaluation signal (H3/F21+F33) -------------------------------
# Today `run_gate`'s not-green branch advances `budget.spent.iterations` and
# every admitted check's `consecutive_failures` on EVERY turn-ending
# evaluation, even when nobody did any work since the previous evaluation — a
# background maker whose Stop hook fires twice against the same half-written
# tree burns two budget iterations and can trip a spurious STRATEGY TURN
# (`commitment_boundaries`, at consecutive_failures == 2) on a check that is
# not genuinely failing twice in a row. `_no_work_signature` answers "did work
# happen since the immediately preceding results-bearing evaluation?" so
# `run_gate` can freeze that accounting on a no-work re-evaluation instead of
# blindly advancing it.

def _no_work_signature(state, work_dir, trace_root):
    """A content-sensitive fingerprint of the loop's current mutation
    footprint, used by `run_gate` to tell a genuine re-evaluation (real work
    landed) apart from a no-work re-evaluation (the tree is byte-identical to
    what the immediately preceding evaluation already looked at).

    Computed UNCONDITIONALLY by `run_gate` — independent of `contract.scope`.
    `evaluate_scope` is the only OTHER caller of `compute_mutation_set` and it
    early-returns whenever no scope is declared (T8, AC3), so a scope-less
    loop (this loop family declares none on purpose — the gate itself is the
    subject under change) would never otherwise compute a mutation set at all.

    Reuses `compute_mutation_set` for membership (the same git-grounded path
    set the scope boundary trusts) but membership alone is NOT sufficient: the
    same file edited twice in a row has an IDENTICAL path set but DIFFERENT
    content — real work a path-only signature would misread as no-work. Each
    member path is therefore re-hashed via `_working_tree_sha` (the same
    byte-identity primitive `pre_dirty_anchors` uses to anchor arm-time dirty
    files), and the signature is the `[[path, sha], ...]` list, sorted by path
    — `compute_mutation_set` already sorts `paths`, but sorting here too keeps
    this function's own equality contract explicit and independent of that
    detail ever changing.

    `work_dir` is the tree to diff (H1/F34: the worktree when the state
    records a valid one, else state_root — the SAME tree `run_gate` evaluates
    checks against). `trace_root` is ALWAYS state_root (the trace FILE never
    moves with a worktree), mirroring `evaluate_scope`'s `trace_root` split.

    Returns `(signature, degraded)`:
      - `degraded is None` and `signature` a (possibly empty) list when the
        mutation set was computed cleanly. The empty list IS a valid,
        comparable signature — "nothing has been touched since arm at all".
      - `degraded` is `compute_mutation_set`'s degraded marker and
        `signature is None` when "did work happen?" is UNANSWERABLE. The
        caller MUST fail closed on this (count the evaluation exactly as
        pre-H3) — never read "unknown" as "no work". Three
        `compute_mutation_set` degraded markers reach here:
          - `no-baseline-ref` — the loop was never armed inside a git work
            tree (or the arm-time freeze itself failed), so
            `contract.mutation_set.baseline.ref` is absent.
          - `no-git-work-tree` — `work_dir` is not a git work tree at
            evaluation time (arm-time git-ness can drift from eval-time).
          - `git-query-failed` — a git query needed to build the set failed
            (e.g. `baseline.ref` no longer resolves — a corrupted/rewritten
            object).
    """
    baseline = _mutation_baseline(state)
    arm_ref = baseline.get("ref")
    pre_dirty = baseline.get("pre_dirty") or []
    tfile = trace_path(trace_root)
    mutation_set = compute_mutation_set(work_dir, arm_ref, pre_dirty, tfile)
    if mutation_set.get("degraded"):
        return None, mutation_set["degraded"]
    signature = sorted(
        [p["path"], _working_tree_sha(work_dir, p["path"])]
        for p in mutation_set["paths"]
    )
    return signature, None


def _signature_members(signature):
    """The member PATHS out of a `_no_work_signature` result (`[[path, sha],
    ...]`), in exactly the shape `_numstat`'s `members` takes — including the
    degraded case, where a None signature projects to a None member list and
    `_numstat` walks the tree itself.

    ONE writer for that projection, because both `_numstat` call sites make it
    and getting it wrong is silent in both directions: `[]` where None was meant
    reports `{"files": 0, ...}` — "this loop changed nothing", a real and very
    different answer from "we could not tell" — and None where `[]` was meant
    pays the whole redundant walk back."""
    return None if signature is None else [p[0] for p in signature]


# H8 (PCF-8/PCF-5): the settle signal — "is the tree still being written?"
#
# `_no_work_signature` (H3) freezes accounting when the tree is byte-IDENTICAL
# to the previous evaluation ("no work happened since"). It cannot help the
# OTHER half of the async-maker problem: a background maker that HAS written
# since the last evaluation but is NOT YET DONE. The signature moved, so H3
# counts the evaluation, and the gate draws a conclusion from a half-written
# tree — charging budget, advancing consecutive_failures, and firing a STRATEGY
# TURN against work that is merely incomplete (PCF-5, live), or — worse —
# advancing the confirmation streak on a FALSE green when the RED-making test
# simply has not been written yet (PCF-8 case 3; a false GREEN is the one error
# class the whole confirmation design exists to prevent, K≥3 notwithstanding).
#
# `_settle_age` reads the append-only trace and returns how many seconds ago the
# most recent WORK-PRODUCT `mutate` op landed (a maker actively typing). Within
# the settle window `run_gate` treats the evaluation as in-flight and freezes
# the SAME accounting a no-work re-evaluation freezes (budget /
# consecutive_failures / STRATEGY TURN) AND freezes the confirmation streak
# (mirroring an H4 --hold), so neither a red nor a green reading of a
# half-written tree can move the loop. It never changes a verdict.
#
# Fail toward COUNTING on every uncertainty (no trace, no repo root to classify
# targets, no work-product mutate, an unparseable ts): a missing signal must
# degrade to today's behavior, never freeze — else a maker that writes
# continuously without going green could dodge the budget cap forever. Targets
# under `.fairmind/` are skipped (journal/state writes are bookkeeping, not work
# in flight), mirroring `compute_mutation_set`'s workspace drop. The wall-clock
# `timeout_min` guard remains the backstop against a tree that never settles.
SETTLE_WINDOW_S = 45.0

# The settle window may DEFER a not-green BUDGET charge, but never PREVENT it
# indefinitely: after this many CONSECUTIVE in-flight budget-freezes the next
# not-green evaluation charges normally. Without this cap a maker that writes
# within the window before every turn-end would freeze the budget forever, making
# `max_iterations` — an UNCONDITIONAL backstop pre-H8 — depend on a `timeout_min`
# the engine does not require. Bounds only the IN-FLIGHT path: the H3 `no_work`
# path is uncapped by design (a byte-identical tree can only waste compute, never
# false-close). The consecutive-failure freeze and the green streak freeze are
# BOTH deliberately uncapped — an in-flight eval is never a completed attempt (so
# it must never advance `consecutive_failures`, H8-F-A) and a stalled green never
# closes falsely and burns no budget; the wall-clock timeout resolves either.
SETTLE_MAX_CONSECUTIVE = 3


def _settle_window_s():
    """The settle window in seconds. `FAIRMIND_GATE_SETTLE_S` overrides the
    default (a test/tuning seam, same idiom as `FAIRMIND_GATE_DEADLINE_S`); a
    non-positive value disables the settle signal entirely."""
    env = os.environ.get("FAIRMIND_GATE_SETTLE_S")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return SETTLE_WINDOW_S


def _settle_max_consecutive():
    """How many consecutive in-flight freezes the not-green accounting tolerates
    before it must charge again. `FAIRMIND_GATE_SETTLE_MAX` overrides the default
    (a test/tuning seam); a value < 1 is floored to 1 (at least one charge is
    always eventually forced, so the iteration cap can never be starved)."""
    env = os.environ.get("FAIRMIND_GATE_SETTLE_MAX")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return SETTLE_MAX_CONSECUTIVE


def _settle_age(work_dir, trace_root, now):
    """Seconds since the most recent WORK-PRODUCT `mutate` op in the trace, or
    None when that is unknown/absent — no trace file, an unresolvable repo root
    (so targets cannot be classified), no such op, or an unparseable ts. Never
    raises. A target under `.fairmind/` (a journal or state write) is skipped:
    it is bookkeeping, not a maker still writing code. A target that cannot be
    normalized (truncated / escaping the tree) is also skipped — an
    unclassifiable op must not freeze the loop (fail toward counting).

    `work_dir`/`trace_root` mirror `_no_work_signature`'s split (H1/F34): the
    trace FILE never moves with a worktree, so it is read from `trace_root`
    (always state_root), while targets are normalized against `work_dir` (the
    worktree when one is recorded — the tree the mutations actually land in)."""
    tfile = trace_path(trace_root)
    if not tfile or not os.path.isfile(tfile):
        return None
    repo_root = _resolve_repo_root(work_dir)
    if repo_root is None:
        return None  # cannot tell work product from bookkeeping — fail toward counting
    latest = None
    try:
        with open(tfile, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    op = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(op, dict):
                    continue  # a bare JSON scalar/array is not an op — never raise on .get()
                if op.get("kind") != "mutate":
                    continue
                normalized = _normalize_trace_target(op.get("target"), repo_root)
                if normalized is None or _is_loop_workspace_path(normalized):
                    continue  # unclassifiable, or bookkeeping (.fairmind/…) — not work in flight
                ts = _parse_iso(op.get("ts"))
                if ts is None:
                    continue
                if latest is None or ts > latest:
                    latest = ts
    except OSError:
        return None
    if latest is None:
        return None
    return (now - latest).total_seconds()
