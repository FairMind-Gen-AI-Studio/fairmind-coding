#!/usr/bin/env python3
"""JC6 — capture the diff of an iteration the gate REJECTED, and this harness's
own account of why, into the local content compartment.

WHY IT EXISTS AT ALL. The bytes of a red iteration are in NO COMMIT, EVER. They
live in the working tree for the minutes between one evaluation and the next
attempt that overwrites them, and then they are gone — for us, for a partner,
for the customer who might one day want them back. `run_gate_checks
._working_tree_sha` already records a sha256 of those raw bytes, so even the
fingerprint the gate keeps is of content nothing stores. There is no retroactive
path. Capture at the instant of the verdict or not at all.

WHAT MAKES IT SAFE TO EXIST is not this module: it is the two locks in
`_insights_session.config_content_mode`, which this module asks and never
re-implements. A repository that has not written BOTH the opt-in content value
AND an explicit `true` on a content class gets exactly today's behaviour, byte
for byte, and this module writes nothing anywhere.

WHERE IT RUNS, and why not where the design said. The design put the capture
inside `run_gate` immediately before `state["iterations"].append(iteration)` —
the last instant at which the verdict and the mutation set are both in scope.
Three independent reviews accepted that; the third refuted it, and it is right:

  * that point is BEFORE `save_state`, and a durable append takes a lock. A
    capture that waits on a contended lock past the Stop hook's 600 s timeout
    gets the process killed before the gate has persisted anything — losing the
    verdicts, the budget charge, the session claim and the very red iteration
    the capture existed to keep. A telemetry side-effect that can eat the gate's
    own record is a defect in the gate.
  * `run_gate` runs under `--dry-run` too, and `dry_run` is threaded only as far
    as `evaluate_scope`. The pre-PR ceremony in this repo runs `--dry-run` twice
    per loop; a capture there would write rows for evaluations that were never
    charged and never persisted.

⚠️ THE AMENDMENT NAMED A LINE THAT DOES NOT EXIST: "in `main()` after
`save_state`". `main()` holds neither `state` nor `decision` — it parses args,
resolves the state path, calls `_dispatch` and returns — and from its return
code a red iteration is indistinguishable from a terminal pass (`EXIT_ITERATE`
is both). The gate path's only `save_state` is inside `_dispatch`, and that is
where the hook went: after the persist, under `not args.dry_run`, shaped like
the `loop_ledger.record_terminal` call that already sits eight lines below it.

WHAT IS AND IS NOT CAPTURED, in one place because the answer is a policy and not
an implementation detail:

  * class B — the unified diff of the mutation set against the commit the loop
    armed on. Per-path, git-produced, never assembled here.
  * class C — check ids, verdicts, measurement values and a CLOSED VOCABULARY of
    failure categories. NOT the `reason` strings themselves, NOT the feedback
    text, NOT command output, NOT check command lines. See `_reason_category`.
  * neither — the transcript. Model output and customer prompts are a different
    product decision with a different consent key, and folding them into
    "generation context" would have been that decision taken by an implementer.
"""

import json
import os
import subprocess
import time

# The gate engine is the importer, so its own helpers are importable by name.
# Everything from `_insights_session` is reached LAZILY inside the granted
# branch: a repository that has not opted in must not pay a module import, and
# the non-granting cost is a measured property with a test behind it.
import run_gate_checks as _gate


#: Per-PATH ceiling on captured diff bytes. It is a LOCAL DISK bound and not a
#: door's payload limit — no door reads this file — so it is denominated in the
#: bytes git produces rather than in what some future JSON envelope would weigh.
#:
#: 🔴 PER PATH, NOT PER ROW, and that ordering is the whole value of the number.
#: `compute_mutation_set` membership is CUMULATIVE against the frozen arm ref, so
#: one 296 KB `package-lock.json` touched once in iteration 2 is a member of
#: every iteration after it. A row-level cap would let that single file blind
#: the entire remaining loop; a path-level cap drops the lockfile and keeps the
#: source change, which is the part anybody wanted.
DIFF_PATH_CAP_BYTES = 262_144

#: Ceiling on the whole row's diff text, applied after per-path selection. It
#: exists so a mutation set of hundreds of small files cannot multiply into an
#: unbounded row: the per-path cap bounds one member, this bounds their sum.
DIFF_ROW_CAP_BYTES = 1_048_576

#: Wall-clock ceiling on ONE git subprocess the capture starts.
#:
#: 🔴 IT EXISTS BECAUSE THE LOCK BOUND ALONE WAS NOT THE HAZARD. The append was
#: given a bounded acquisition so a contended compartment could not wedge the
#: Stop hook; git can wedge it just as thoroughly and was left unbounded. `git
#: diff` blocks on `index.lock` while any other git process in the repository
#: holds it — an IDE indexing, a `git gc`, the developer's own commit — and the
#: capture runs on the Stop-hook path AFTER the state is persisted but BEFORE
#: the gate's feedback is printed, so a wedge there costs the agent the very
#: verdict it is waiting for. Every subprocess this module starts carries it.
#:
#: Five seconds is ~800x the measured cost of the largest real diff in the
#: corpus (6.2 ms over 13 paths / 92 331 B), so it can only fire on a genuine
#: block.
#:
#: ⚠️ IT COVERS THE SUBPROCESSES THIS MODULE STARTS ITSELF — the diffs and the
#: `ls-files`. It does NOT cover the three it reaches through the gate engine
#: (`_head_sha`, `_settle_age`'s repo-root query, and `resolve_work_dir`'s
#: work-tree probe), which are unbounded there and shared with the gate's own
#: paths. Saying "every subprocess" would have been the easier sentence and the
#: false one; bounding those is a change to `run_gate_checks`' own helpers,
#: which is a separate decision from this feature.
GIT_TIMEOUT_S = 5.0

#: Wall-clock ceiling on the WHOLE diff loop, however many members it holds.
#: `GIT_TIMEOUT_S` bounds one subprocess; this bounds their product, which is
#: the quantity the Stop hook actually feels. Thirty seconds is ~5000x the
#: measured cost of the largest real capture in the corpus and two orders below
#: the hook's own deadline.
CAPTURE_BUDGET_S = 30.0

#: The closed vocabulary of failure CATEGORIES. The key is a prefix of the
#: `reason` the gate built; the value is all that is ever written.
#:
#: 🔴 THE CATEGORY IS EMITTED, NEVER THE REASON. Matching a prefix and then
#: shipping the string would be no guard at all — the interpolated tail is
#: exactly the part that carries content. `int("SECRET")` raises a ValueError
#: whose message QUOTES its input, and on a `stdout_regex` signal that input is
#: the check's own command output; `run_gate_checks._uncoercible_reason` was
#: written for that single case and this table generalises its discipline to
#: every reason the engine can produce.
#:
#: Census taken 2026-08-21 over every `reason` handed to `_result`: four
#: f-strings interpolate an exception (`missing signal`, `verdict artifact
#: unreadable`, `evaluation crashed`), one interpolates the differing per-run
#: VALUES of a non-deterministic signal, and TWO — the evidence pass/fail pair —
#: read `data.get("notes")` straight out of a verdict artifact an agent wrote,
#: which is unbounded customer text. Only the last pair is invisible to a prefix
#: table, and it is exactly why the default is `other` rather than the string.
_REASON_CATEGORIES = (
    ("predicate satisfied", "predicate_satisfied"),
    ("predicate not satisfied", "predicate_unsatisfied"),
    ("gate deadline exceeded", "gate_deadline_exceeded"),
    ("check not admitted", "check_not_admitted"),
    ("evidence check has no verdict_file", "evidence_no_verdict_file"),
    ("verdict artifact unreadable", "evidence_artifact_unreadable"),
    ("uncoercible ", "uncoercible_signal"),
    ("missing signal (treated as fail)", "missing_signal_failed"),
    ("missing signal", "missing_signal"),
    ("non-deterministic signal", "non_deterministic_signal"),
    ("evaluation crashed", "evaluation_crashed"),
)

#: What a reason this table does not recognise becomes. Never the reason.
_REASON_OTHER = "other"


def _reason_category(reason):
    """One closed category for one `reason`, or `other`.

    Longest-prefix ordering matters in exactly one pair — "missing signal
    (treated as fail)" must be tested before "missing signal" — so the table is
    an ordered tuple rather than a dict, and the pair sits adjacent in it."""
    if not isinstance(reason, str):
        return _REASON_OTHER
    for prefix, category in _REASON_CATEGORIES:
        if reason.startswith(prefix):
            return category
    return _REASON_OTHER


def _banner_kinds(row):
    """The KINDS of commitment banner this iteration carried — never their text.

    The banners are harness-authored, but they interpolate a check's `owner`
    (`authored_by`, a free string from a descriptor) and its id, so the text is
    not closed even though its template is. The kinds are, and they carry the
    fact a reader of the corpus wants: that a strategy turn or a contract
    conflict fired here."""
    kinds = []
    if row.get("strategy_turn"):
        kinds.append("strategy_turn")
    if row.get("in_flight"):
        kinds.append("settle")
    return kinds


def _tree_moved_during_checks(work_dir, trace_root, started_at):
    """True when a work-product write landed AFTER the checks started, None when
    that is unknowable.

    THE ARITHMETIC IS `_settle_age`'S, RUN AGAINST A DIFFERENT CLOCK. That
    function returns `now - latest_work_product_mutation`; handing it the
    instant the checks began instead of the present returns a NEGATIVE number
    exactly when a maker wrote during the run. No new probe, no new file format,
    one trace read.

    ⚠️ IT MATTERS BECAUSE THE OTHER THREE SIGNALS ARE ALL BLIND TO IT. The
    mutation signature is computed AFTER every check has run, so a write that
    landed mid-run is already folded into it; the in-flight probe only sees the
    last 45 seconds; and the stability re-read compares two post-check reads of
    the same bytes. A maker writing 300 seconds into a 540-second check run is
    invisible to all three, and the diff would then be labelled with verdicts
    computed on different bytes.

    UNKNOWN IS NOT A REFUSAL. `_settle_age` answers None for no trace file, an
    unresolvable repo root or an unparseable timestamp — ordinary states, not
    suspicious ones — and the gate's own rule for this signal is to fail toward
    counting. A capture that refused on None would silently never fire on any
    repository without a trace."""
    if started_at is None:
        return None
    age = _gate._settle_age(work_dir, trace_root, started_at)
    if age is None:
        return None
    return age < 0


def _stable_members(work_dir, signature):
    """`(stable, moved, expected)` — the signature's members split by RE-READING
    each path's bytes and comparing to the sha the evaluation recorded, plus the
    sha each stable path is expected to still carry, which `_capture_diff` uses
    to re-check it AFTER its diff has been produced.

    THE STABLE SET IS COMPUTED FIRST AND THE PATHSPEC IS BUILT FROM IT, rather
    than diffing everything and filtering afterwards. Omission then has no
    parser behind it: a path whose bytes moved is never handed to git, so no
    byte of it can appear in the output by way of a hunk header, a rename
    detection or a context line. The alternative ordering has to find and remove
    a file's hunks from a text that already contains them, which is a leak
    surface where this is an argument list."""
    moved, expected = [], {}
    for entry in signature or ():
        if not (isinstance(entry, list) and len(entry) == 2):
            continue
        path, recorded = entry
        if not isinstance(path, str):
            continue
        # 🔴 `recorded is None` IS A RECORDED VALUE, NOT A MALFORMED ONE. A
        # mutation-set member the iteration DELETED is hashed by
        # `_working_tree_sha`, which answers None for a path that no longer
        # exists — so the signature carries `[path, None]`. Filtering that out
        # here dropped the deletion from the diff with no marker, and when the
        # deletion was the iteration's ONLY change the whole row was suppressed
        # (`if not expected: return`) with nothing recorded anywhere. Measured:
        # a red iteration deleting one tracked file captured zero rows. It also
        # made the one-time notice false — that notice tells the customer a
        # deletion IS captured, which was true of a deleted LINE and not of a
        # deleted FILE. Compared like any other value now: both None means the
        # file was gone at the verdict and is still gone, which is stable.
        if not isinstance(recorded, (str, type(None))):
            continue
        current = _sha(work_dir, path)
        if current == recorded:
            expected[path] = recorded
        else:
            moved.append(path)
    # `(expected, moved)`, not `(stable, moved, expected)`: `stable` was exactly
    # `list(expected)` — one fact returned twice and then threaded through
    # `_capture_diff` as two arguments whose agreement nothing checked. The dict
    # is insertion-ordered, so iterating it preserves the signature's order.
    return expected, moved


def _sha(work_dir, path):
    """One member's current content hash, or None. Thin, because the post-diff
    re-read wants the same answer `_stable_members` computed and nothing else."""
    try:
        return _gate._working_tree_sha(work_dir, path)
    except Exception:  # noqa: BLE001
        return None


def _git_diff(work_dir, args):
    """One `git diff`, returning its stdout or None. `--no-index` exits 1 by
    design when the two sides differ, so a non-zero code is not an error here;
    an empty stdout on a failure is what distinguishes them.

    `--no-textconv` and `--no-ext-diff` because the ORACLE for this text is
    `git apply`: a repository-configured textconv filter or external differ
    emits a rendering of the file rather than its bytes, which neither
    reconstructs the tree nor applies as a patch, and does it silently under a
    config this code never reads. `.gitattributes -diff` is a different
    mechanism and is deliberately still honoured — it is the customer's own
    per-path exclusion."""
    try:
        proc = subprocess.run(["git", "diff", "--no-color", "--no-renames",
                               "--no-textconv", "--no-ext-diff"] + args,
                              cwd=work_dir, capture_output=True,
                              encoding="utf-8", errors="surrogateescape",
                              timeout=GIT_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return None
    return proc.stdout or ""


def _git_query(work_dir, args):
    """One read-only `git`, returning stdout, or None when git said no. Unlike
    `_git_diff` a non-zero exit here IS a failure: these commands have no
    "difference found" convention to confuse it with."""
    try:
        proc = subprocess.run(["git"] + args, cwd=work_dir, capture_output=True,
                              encoding="utf-8", errors="surrogateescape",
                              timeout=GIT_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return None
    return proc.stdout if proc.returncode == 0 else None


def _capture_diff(work_dir, baseline_ref, expected):
    """The unified diff of `paths` against `baseline_ref`, per path, with the
    caps applied. Returns `(text, over_cap, moved, unreached)`, or
    `(None, [], [], [], 0)` when git could not say which members are tracked.
    The fifth member is the byte size of the text, accumulated as it is built:

      * `over_cap` — `[path, bytes]` pairs refused for SIZE;
      * `moved`    — paths whose bytes changed while the capture was running;
      * `unreached` — paths the whole-loop deadline was hit before reaching.

    Three lists rather than one with sentinel sizes in it: the class map
    documents `paths_over_cap` as "refused for size, and how big they were", so
    a `-1` smuggled in there is a lie to every later reader of the record.

    ONE CALL PER PATH, deliberately, and it is what makes the per-path cap
    possible at all: a single batched diff returns one text in which a
    lockfile's hunks would have to be found and cut out again. It also inherits
    `_numstat`'s standing prohibition verbatim — the measured, working batching
    alternative writes loose objects into the customer's object store via a
    throwaway index, and a gate that mutates the repository it is measuring is a
    gate nobody can trust.

    A TRACKED PATH AND AN UNTRACKED ONE TAKE DIFFERENT COMMANDS. `git diff
    <ref> -- <path>` cannot see a file that is in no tree; `git diff --no-index
    /dev/null <path>` produces its `new file mode` hunk.

    🔴 WHICH ONE A MEMBER NEEDS IS ASKED OF GIT, NEVER INFERRED FROM AN EMPTY
    DIFF. The first version of this function fell back to `--no-index` whenever
    the tracked form returned nothing, which reads as "then it must be a new
    file" and is false: a TRACKED member that is byte-identical to the baseline
    also returns nothing, and the fallback then emits its ENTIRE CONTENTS as a
    fabricated `new file mode` hunk. Measured on a throwaway repo: a tracked,
    unchanged file produced 0 bytes from the first form and its whole text from
    the second. That is the exact opposite of what this feature promises — the
    diff of the files the iteration CHANGED — and it would have captured a file
    the iteration did not touch. One `git ls-files` over the whole member set
    answers it for every path at once, so the fix costs one subprocess and
    removes one per untracked member."""
    listed = _git_query(work_dir, ["ls-files", "-z", "--"] + list(expected))
    if listed is None:
        # 🔴 REFUSE THE WHOLE CAPTURE. Trackedness unknown is not a state with a
        # safe default: treating the members as untracked hands every one of
        # them to `--no-index`, which is the form that FABRICATES a new-file
        # hunk out of a file's entire contents — the exact over-capture this
        # query was added to prevent, reintroduced through the failure path. An
        # earlier version of this branch defaulted to the empty set with a
        # comment claiming the opposite; the comment was wrong about which form
        # is the dangerous one.
        return None, [], [], [], 0
    tracked = set(_gate._split_nul(listed))

    chunks, over_cap, moved, unreached, total = [], [], [], [], 0
    deadline = time.monotonic() + CAPTURE_BUDGET_S
    for path in expected:
        # ONE DEADLINE OVER THE WHOLE LOOP, not just per subprocess. A five-
        # second bound on each `git diff` is no bound at all across a mutation
        # set of two hundred members: the product can outlive the Stop hook the
        # per-call timeout was added to protect. Members not reached are
        # reported as unstable rather than silently missing.
        if time.monotonic() >= deadline:
            unreached.append(path)
            continue
        before = expected[path]
        # `--no-index` is for a file that EXISTS and git does not track. A path
        # that is gone from the working tree takes the ref form whether or not
        # the index still lists it: that is the only form that can render a
        # deletion, and handing a missing path to `--no-index` produces nothing
        # at all.
        if path in tracked or not os.path.exists(os.path.join(work_dir, path)):
            text = _git_diff(work_dir, [baseline_ref, "--", path]) or ""
        else:
            text = _git_diff(work_dir, ["--no-index", "--", os.devnull, path]) or ""
        # 🔴 RE-READ AFTER THE DIFF, NOT ONLY BEFORE IT. The stable set is
        # computed once, and then N subprocesses run — so a maker writing during
        # that window moves a file the pre-check already blessed, and the bytes
        # git produced describe a tree that no longer exists. The docstring on
        # `_stable_members` promised this verification from the first draft and
        # the code did not do it. A path that moved contributes nothing.
        if _sha(work_dir, path) != before:
            moved.append(path)
            continue
        if not text:
            continue
        size = len(text.encode("utf-8"))
        if size > DIFF_PATH_CAP_BYTES or total + size > DIFF_ROW_CAP_BYTES:
            over_cap.append([path, size])
            continue
        chunks.append(text)
        total += size
    # THREE OUTCOMES, THREE NAMES. They were briefly one list with `-1` and `-2`
    # smuggled in where a byte size belongs — and `paths_over_cap` is documented
    # in the class map as "which members were refused for size, and how big they
    # were", so a reader (or a deletion job) had no way to tell a 262 KB
    # lockfile from a file somebody wrote during the capture. `paths_unstable`
    # already existed for the second case.
    return "".join(chunks), over_cap, moved, unreached, total


def _context(results, row):
    """Class C: what this harness itself says about the refusal, as a closed
    vocabulary. See `_reason_category` for why no `reason` string is here, and
    the module docstring for why no transcript is.

    ⚠️ `feedback_to` IS NOT HERE, AND IT WAS UNTIL A REVIEW ASKED WHAT IT
    CONTAINED. It is the check descriptor's `owner` (or a routing override) — a
    FREE STRING a customer's agent authors, in a payload whose whole claim is a
    closed vocabulary. "Which role the feedback went to" is worth little and
    "an arbitrary string from the descriptor" costs the claim, so it is dropped
    rather than bounded: a vocabulary with one open member is not one.

    `id` and `verdict` ride the SAME guards the loop door applies, for the same
    reason `_verdicts` imports its projection: a check id is in-policy but it is
    not unbounded, and a verdict outside the wire vocabulary is a row shape
    nobody validated."""
    from insights_flush_payload import (_project_row,  # lazy: granted only
                                        _ITERATION_RESULT_FIELD_GUARDS,
                                        _ITERATION_RESULT_ROW_GUARDS)
    checks = []
    for result in results:
        if not isinstance(result, dict):
            continue
        # 🔴 THROUGH `_project_row`, NOT THROUGH THE PREDICATES DIRECTLY. The
        # guard maps pair a NORMALIZER with each predicate — `id` is
        # `(_nonempty_str, _looks_like_check_id)` — and calling the predicate
        # alone drops the normalizer: `_nonempty_str` strips, and the id pattern
        # admits no whitespace, so a padded id survived on the `verdicts` side
        # of this same row and vanished here. `verdict` is a ROW guard, so an
        # out-of-vocabulary verdict must drop the whole entry rather than one
        # key. `_project_row`'s own docstring records that it exists BECAUSE it
        # was hand-written copies and that declaring the normalizer beside the
        # predicate is "the fix for a latent trap"; a fourth copy re-armed it.
        entry = _project_row(result, ("id", "verdict"),
                             _ITERATION_RESULT_FIELD_GUARDS,
                             _ITERATION_RESULT_ROW_GUARDS)
        if entry is None:
            continue
        entry["reason_category"] = _reason_category(result.get("reason"))
        checks.append(entry)
    return {"checks": checks, "banner_kinds": _banner_kinds(row)}


def _verdicts(row):
    """The row's own results, projected through the SAME wire guard the loop
    door uses.

    🔴 IT IS NOT A VERBATIM COPY, AND THAT IS A CORRECTION TO THE DESIGN, WHICH
    ASKED FOR ONE. `results[].value` is filled from a check's own signal, and
    JC12's shape guard — the one that refuses anything but a JSON number or
    boolean there — was installed at the WIRE PROJECTION, not at the producer.
    Its own comment says so: "THIS CLOSES THE WIRE, NOT THE DISK ...
    `loop-state.json` still records the evidence artifact's own verdict word in
    `value`", a free string read out of a JSON file an agent wrote, measured
    once shipping a whole prose sentence and once a raw dict.

    So the bytes this module reads off the persisted row are on the disk side of
    that asymmetry: copying them would have re-opened JC12 on a brand new door,
    through `value` rather than through `reason`. The projection is IMPORTED
    rather than re-implemented so it cannot drift from the door's."""
    from insights_flush_payload import _iteration_result_wire  # lazy: granted only
    projected = []
    for result in row.get("results") or ():
        if not isinstance(result, dict):
            continue
        wire = _iteration_result_wire(result)
        if wire is not None:
            projected.append(wire)
    return projected


def _red_row(state):
    """The iteration this evaluation just appended, when it is one the gate
    REFUSED; None otherwise.

    THE PREDICATE IS THE CONSENT MAP'S OWN, READ BACK OFF THE RECORD: a row is a
    rejected proposal when its `results` is a non-empty list and not every entry
    is green. Deriving it from the decision code instead would be wrong twice —
    `DECISION_STOP_BLOCKED` also carries a red iteration (the one that exhausted
    the budget), and a capture whose notion of "rejected" differed from the
    classifier's would file rows under a class they do not belong to.

    The row is found the way `run_gate` finds its own predecessor — the last
    entry carrying a `results` key — never `[-1]`: the array also holds audit
    entries with no results, and `budget_exhausted` runs between the append and
    this hook."""
    rows = [it for it in state.get("iterations", ()) if isinstance(it, dict)
            and "results" in it]
    if not rows:
        return None
    row = rows[-1]
    results = row.get("results")
    if not isinstance(results, list) or not results:
        return None
    if all(isinstance(r, dict) and r.get("verdict") == _gate.GREEN
           for r in results):
        return None
    return row


def _skip_reason(state, row):
    """Why this red iteration must NOT be captured, or None to proceed.

    Every case here is a state in which the bytes would not mean what the row
    would claim they mean, and the answer to all of them is the same: capture
    nothing. Fail-closed governs capturing exactly as it governs sending; it
    never governs destroying, which is why reclamation runs before this is even
    asked."""
    if row.get("mutation_signature_degraded") is not None:
        return "signature_degraded"   # "did work happen" was unanswerable
    if row.get("in_flight"):
        return "in_flight"            # a maker is still writing this tree
    if not row.get("mutation_signature"):
        return "no_signature"
    if not _gate._mutation_baseline(state).get("ref"):
        return "no_baseline"          # nothing to diff against
    rows = [it for it in state.get("iterations", ()) if isinstance(it, dict)
            and "results" in it]
    if len(rows) >= 2:
        prev = rows[-2]
        if (prev.get("mutation_signature") is not None
                and prev.get("mutation_signature") == row.get("mutation_signature")):
            return "no_work"          # the same tree, already captured once
    return None


def capture(state, cwd, decision):
    """The hook. Capture this evaluation's red iteration if everything says to,
    reclaim whatever a revocation says must go, and never raise.

    ORDER IS POLICY. Reclamation is decided FIRST and independently of whether
    anything is captured, because an explicit `false` is a decision about bytes
    that already exist and must not wait on a future red verdict to be honoured.

    COST ON A REPOSITORY THAT HAS NOT OPTED IN, stated as a measurement rather
    than as "nothing", because it is not nothing: a green evaluation costs one
    in-memory predicate and returns; a red one costs ONE `git rev-parse`
    (resolving the toplevel and the git common dir together, which is also the
    tenancy) plus one `os.stat`, and then returns without reading a config. No
    row, no file, no directory, no byte."""
    row = _red_row(state)
    if row is None:
        # A green evaluation ends here, having read nothing and asked git
        # nothing. Reclamation is NOT attempted on this path on purpose: it
        # would need a tenancy, a tenancy needs a `git rev-parse`, and paying
        # that on every green evaluation of every repository to catch a
        # revocation on a loop that never goes red again is the wrong trade.
        # `cmd_session_start` already holds a tenancy and a toplevel and
        # reclaims there, once per session, for free.
        return
    # 🔴 THE TREE THE VERDICT WAS ABOUT, RESOLVED THE WAY THE GATE RESOLVES IT.
    # `resolve_work_dir` returns a PAIR, and its second half is a refusal: when
    # a recorded worktree cannot be matched to a real registered worktree of
    # this repo, `work_dir` is None and the contract is that the caller fails
    # closed — never falling back to the state root, which is the F34 defect the
    # split exists to prevent. A capture is the last place to relax that: it
    # would diff a tree no check ever ran in and file the result under this
    # loop's verdicts.
    work_dir, degraded = _gate.resolve_work_dir(state, cwd)
    if work_dir is None or degraded is not None:
        return

    session = _authority()
    if session is None:
        return
    # ONE subprocess for both facts: the toplevel the consent file sits at, and
    # the git common dir the tenancy is derived from.
    toplevel, common = session._git_rev_parse(work_dir)
    tenancy = session._tenancy_from_common(work_dir, common)
    if not tenancy:
        return

    # Reclamation BEFORE the grant is read, and independently of it: an explicit
    # `false` is a decision about bytes that already exist, and it must not wait
    # on the content mode still being on. Guarded by the compartment's
    # existence, so a repository that never captured pays one stat.
    try:
        session.reclaim_content(tenancy, toplevel)
    except Exception:  # noqa: BLE001 — never fail the gate
        pass

    mode, classes = session.content_mode_granted(toplevel)
    if mode != session.CONSENT_CONTENT_MODE_FAILED_ITERATIONS or not classes:
        return

    skip = _skip_reason(state, row)
    if skip is not None:
        return

    baseline = _gate._mutation_baseline(state).get("ref")
    # 🔴 THE TREE MUST BE THE ONE THE VERDICT WAS ABOUT, and `commit_sha` is the
    # only witness the row carries. (An earlier version of this comment said
    # `resolve_work_dir` falls back to the state root when a worktree vanishes —
    # it does not; it returns None and the caller above fails closed. Two
    # comments in one function arguing opposite premises is how a later reader
    # talks themselves into weakening the guard.) The same read settles the
    # design's open question of whether HEAD can differ from the arm baseline:
    # measured never, across 37 loops and 25 red iterations.
    # The two IN-MEMORY comparisons run first: a row that already fails them
    # cannot be rescued by anything git says, so the subprocess only runs when
    # it can still change the answer.
    if not row.get("commit_sha") or row["commit_sha"] != baseline:
        return
    head = _gate._head_sha(work_dir)
    # ALL THREE MUST AGREE, and the row's own witness is REQUIRED rather than
    # merely respected when present. `commit_sha` is omitted (never null) when
    # HEAD did not resolve during the evaluation — so accepting its absence
    # accepted exactly the case where the gate could not tell which tree it had
    # judged, and then vouched for the tree ourselves from a later read.
    if head != row["commit_sha"]:
        return

    started = _gate._parse_iso(decision.get("checks_started_at")) if decision else None
    moved_during = _tree_moved_during_checks(work_dir, cwd, started)
    if moved_during:
        return

    expected, unstable = _stable_members(work_dir, row.get("mutation_signature"))
    if not expected:
        return

    # OMITTED, NEVER NULL, for every optional field — the discipline the
    # iteration row already applies to `commit_sha`. A null asserts that a fact
    # was looked up and found to be nothing; an absent key says it was not
    # available. A corpus reader can act on the second and can only guess at the
    # first, and the two states are genuinely different for a session id (the
    # gate was driven without one) and for a loop id (no ledger to ask).
    capture_row = {k: v for k, v in {
        "schema": session.CONTENT_SCHEMA,
        "tenancy": tenancy,
        "loop_id": _loop_id(state),
        "iteration_n": row.get("n"),
        "at": row.get("at"),
        "session_id": state.get("owner_session"),
        "baseline_ref": baseline,
        "commit_sha": row.get("commit_sha"),
        # 🔴 WHICH CHECKOUT'S CONFIG AUTHORIZED THIS ROW. The compartment is
        # keyed by TENANCY — the git common dir — which linked worktrees of one
        # repository SHARE while each has its own toplevel and its own
        # `.fairmind-insights.json`. Without this, a `false` written in one
        # worktree would strip that class from rows captured under another
        # worktree's still-granting config: a revocation reaching data its
        # author never spoke for. `consent_classes_resolver` documents the same
        # asymmetry for the ambient lane; this is the loop lane paying it.
        "toplevel": toplevel,
    }.items() if v is not None}
    capture_row.update({
        "classes": list(classes),
        "consent": {"classes": list(classes),
                    "version": session.CONSENT_VERSION,
                    "content_mode": mode},
        # THE PURPOSE STAMP. Collection and USE are two decisions, and until
        # this row carries which one it was taken under, a corpus cannot answer
        # "may we train on this" without re-deriving it from a config that has
        # since changed. The stamp FREEZES here: nothing relabels a row later,
        # which is what makes the vocabulary worth closing before the first row
        # rather than after.
        #
        # JC39 replaced a hardcoded "training" here. That literal claimed the
        # WIDEST rung of a ladder that did not exist yet, on every row any
        # customer might capture, permanently — and the comment beside it
        # justified writing it now by saying retrofitting would be "a
        # migration". Measured 2026-08-21, one day after JC6 shipped: zero rows
        # on this machine and no `.fairmind-insights.json` anywhere under
        # ~/Projects, so there was nothing to migrate and the urgency was
        # pointing at the wrong thing. What is actually irreversible is the
        # freeze above.
        "purpose": session.content_purpose(toplevel),
    })
    if moved_during is None:
        capture_row["tree_stability_unknown"] = True

    if "B" in classes:
        # 🔴 EVERY CLASS-B FIELD IS INSIDE THIS BRANCH, and three of them were
        # outside it until a review said so. `verdicts`, `mutation_signature`
        # and `paths_unstable` were written unconditionally — so a repository
        # granting ONLY `generation_context` received the member PATHS of its
        # own tree and the refused iteration's measured values, neither of which
        # it had granted. The map classifies all three as B; writing them under
        # a C-only grant is the class system failing at the one thing it exists
        # for. The partition test now asserts EXACT key sets per grant rather
        # than the presence of `diff` and `context`, which is what let this
        # stand green.
        if row.get("mutation_signature"):
            capture_row["mutation_signature"] = row["mutation_signature"]
        if unstable:
            capture_row["paths_unstable"] = unstable
        capture_row["verdicts"] = _verdicts(row)
        text, over_cap, moved_now, unreached, diff_bytes = _capture_diff(
            work_dir, baseline, expected)
        if text is None:
            # git could not say which members are tracked. Refusing the ROW
            # rather than writing a B-less one: a row stamped class B with no
            # diff and no reason would read as "the iteration changed nothing".
            # A durable trace, because the alternative is a repository that
            # captures nothing, permanently, and cannot tell that from a loop
            # that simply never went red. `--insights-status` reports the count.
            session._record_content_failure(tenancy, "git_unavailable")
            return
        capture_row["diff_class"] = "B"
        # From the sum `_capture_diff` already accumulated: UTF-8 length is
        # additive over concatenation, so re-encoding the whole diff to measure
        # it allocated a second copy of up to the row cap for a number already
        # in hand.
        capture_row["diff_bytes"] = diff_bytes
        if over_cap:
            capture_row["paths_over_cap"] = over_cap
        if moved_now:
            capture_row["paths_unstable"] = sorted(
                set(capture_row.get("paths_unstable", [])) | set(moved_now))
        if unreached:
            capture_row["paths_unreached"] = unreached
        if text:
            capture_row["diff"] = text
        else:
            # 🔴 EMPTY IS NOT THE SAME FACT AS OMITTED, and it is not a failure.
            # An attempt that was made, seen to fail and REVERTED leaves zero
            # bytes in the working tree — so a red iteration can legitimately
            # produce no diff at all. Recorded as its own marker so a reader
            # never has to infer which of the three happened from an absence.
            # THREE REASONS FOR NO BYTES, AND THEY ARE DIFFERENT FACTS. Until
            # the three outcome lists were split apart this read "over cap or
            # nothing to capture", so a row whose every member moved or timed
            # out was durably labelled too-big — a reader would have concluded
            # the iteration made a huge change when the truth is we could not
            # vouch for any of it.
            capture_row["diff_omitted"] = (
                "all_members_over_cap" if over_cap
                else "all_members_unverifiable" if (moved_now or unreached)
                else "no_surviving_change")
    if "C" in classes:
        capture_row["context_class"] = "C"
        capture_row["context"] = _context(decision.get("results") if decision else (),
                                          row)

    session.content_append(tenancy, capture_row)


def _loop_id(state):
    """The run's id, as the run ledger computes it — IMPORTED, not re-derived.

    ⚠️ A HAND-WRITTEN COPY WAS WRONG WITHIN AN HOUR OF BEING WRITTEN, which is
    the argument for the import rather than a stylistic preference. The copy
    read `state["contract"]["target"]["ref"]`, which is where a reader expects
    the target to live; the ledger reads the TOP-LEVEL `state["target"]["ref"]`,
    falls back to the literal "loop", and drops the `@` entirely when no start
    instant is stamped. Every one of those three differences produces a key that
    joins to nothing. The function is pure — no file is read, no ledger is
    touched — so importing it costs one module load inside the granted branch.

    Lazy for the same reason everything else here is: a repository that captures
    nothing must not pay the import."""
    try:
        import loop_ledger
        return loop_ledger._loop_id(state)
    except Exception:  # noqa: BLE001 — a missing ledger must not fail a capture
        return None


def _authority():
    """`_insights_session`, or None — the consent vocabulary and the compartment
    both live there, and neither is re-spelled here.

    LAZY AND GUARDED for the reason `run_gate_checks._consent_authority` is: a
    module-level import would let a broken capture module take the gate engine
    down at load time, and it is also what keeps the non-granting cost honest —
    a repository that never captures never imports it through this path."""
    try:
        import _insights_session
        return _insights_session
    except Exception:  # noqa: BLE001
        return None
