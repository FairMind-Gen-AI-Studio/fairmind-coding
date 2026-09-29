# `loop-state.json` — schema reference

`${FAIRMIND_BASE}/loop-state.json` is the **authoritative "loop active" signal**. Its presence with `status: "running"` is what makes the Stop hook (`loop-check.sh`) evaluate the gate; without it, or with any other status, the hook is a silent no-op and the interactive workflow is untouched.

The Technical Lead *specifies* the descriptors in this file during Phase 0b. The checker-side agents author the checks the descriptors point at. The engine (`run_gate_checks.py`) reads and mutates the runtime fields (`status`, `confirmations`, `budget.spent`, per-check `consecutive_failures`, `iterations`).

Every `python3 "<plugin root>"/scripts/…` line below is a placeholder, not a runnable command: this file is read raw, never substituted the way a command/SKILL.md/agent body is. `<plugin root>` is the same absolute path the invoking command or `SKILL.md` shows once Claude Code substitutes its own `${CLAUDE_PLUGIN_ROOT}` references — copy that literal value in before running any line shown here.

## Top-level fields

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `schema` | string | Technical Lead | Always `"fairmind-loop/1"`. |
| `target` | object | Technical Lead | `{ "level": "story"\|"task", "ref": "US-142" }` — what the loop is closing. |
| `status` | string | engine | `running` → gate active. Terminal (`run_gate_checks.TERMINAL_STATUSES`): `"passed_pending_human"` \| `"blocked_budget"` \| `"blocked_failures"` \| `"blocked_timeout"` \| `"blocked_no_checks"` (no admitted check to gate on → human) \| `"blocked_scope"` (T8 — a mutated path outside a declared `contract.scope` → human) \| `"blocked_recovered"` (W2.1 — a human forced `--recover` on a loop wedged in `running` whose owning session was gone; a `blocked_*` status, so `--arm`/`--extend-budget` can resume it) \| `"blocked_worktree"` (H1/F34 — a recorded `worktree.path` that does not resolve to a real, registered worktree of this repo's own git history; fails the WHOLE evaluation closed before any check runs — see `worktree` below). |
| `hermeticity_tier` | string | Technical Lead / engine | `"A"` (enforced via `srt`) or `"B"` (detected). Downgraded to `B` at run time if `srt` is absent. |
| `runner` | object | Technical Lead | Detected toolchain, e.g. `{ "test": "playwright", "pkg": "npm" }`. Advisory. |
| `budget` | object | Technical Lead + engine | Caps and spend — see below. |
| `confirmations` | int | engine | Consecutive all-green evaluations so far (runtime). **Engine-owned**: the orchestrator must not pre-seed it — a fresh loop's streak is forced to 0 on its first evaluation regardless of any persisted value (T11). Frozen (never incremented) on every all-green evaluation while `hold` (below) is set — see "Hold / release" below (H4/F24). |
| `hold` | object \| absent | `--hold` / `--release` | **Optional.** `{ "at": <iso>[, "user_confirmed": <str>] }` when a human-approved contract amendment is in flight; **absent** otherwise (`--release` deletes the key, it never writes `null`). Its mere presence is what `run_gate`'s all-green branch reads to suspend the confirmation streak — see "Hold / release" below (H4/F24). |
| `checks` | array | Technical Lead → checker | The measurable predicates — see below. |
| `quarantine` | array | admission | Checks that failed a mandatory admission gate: `{ id, reason, gate_failed }`. Never contribute to stop. |
| `iterations` | array | engine | Append-only log: `{ n, at, results:[{id,verdict,value}], feedback_to, mutation_signature }` (`mutation_signature` — H3/F21+F33, see below), plus audit-only entries with no `n`/`results` key (`arm`, `extend_budget`, `recover`, `hold`, `release`, `scope_violation`, `worktree_degraded`) — every counter that tests `"results" in it` looks past them. |
| `contract` | object | Technical Lead | Optional, per-run frozen contracts. `contract.mutation_set` (below) is a helper the engine exposes but does not itself enforce. `contract.scope` (below, T8) IS enforced — every evaluation checks it before any check verdict. `contract.criteria` (below, T10) IS enforced at **arm time** — `--arm` refuses a contract whose hard criteria are not covered by a live, admitted check. Absent unless a task needs it. |

## `budget`

```json
"budget": {
  "max_iterations": 8,
  "max_consecutive_failures": 3,
  "timeout_min": 120,
  "spent": { "iterations": 3, "started_at": "2026-07-07T21:00:00+00:00" }
}
```

- `max_iterations` — cap on **non-green** evaluations (red/error). Confirmation-green evaluations do **not** count, so a genuinely green run can always reach the confirmation threshold without being starved. Since H3 (F21+F33), a non-green evaluation *also* does not count when it is a **no-work re-evaluation** — see below.
- `max_consecutive_failures` — per-check cap; any check reaching it → `blocked_failures`.
- `timeout_min` — wall-clock cap from `spent.started_at`, covering the whole **arm→close** span (every maker/checker turn between `--arm` and the loop's terminal transition), not a single gate evaluation. This span is **inclusive of idle time**: the clock measures the whole arm→close wall-clock window, work or no work, so a loop left untouched between turns spends its `timeout_min` budget exactly as if it had been actively iterating.
- `spent.started_at` — **engine-owned**, same class as `confirmations`. Since T19, it is stamped by `--arm` (see below) at the moment the loop is armed — the measured window now starts when the work starts, not at the first gate evaluation. `run_gate` keeps a backstop stamp (whichever evaluation first finds it unresolvable — absent, `null`, or garbage — stamps it, then never touches it again) for a hand-armed or legacy loop that skipped `--arm`. A `timeout_min` budget whose `started_at` cannot be resolved fails closed (`blocked_timeout`) rather than silently running unbounded (T16).
- A loop that reaches all-green closes to `passed_pending_human` **without ever consulting `timeout_min`** — `budget_exhausted` (the sole place the timeout is checked) is only called on the not-all-green path. So the idle-inclusive clock only ever bites a loop that is still doing (red) work when it runs out; a green loop can sit idle for any length of time up to the moment of its final confirming evaluation and still close on that evaluation, timeout notwithstanding.
- The Technical Lead **proposes** these per task; the **user confirms** before the loop starts.

## Arming — `run_gate_checks.py --arm`

`--arm` is the **single engine verb** that flips a loop into `running`; the orchestrator never hand-edits `loop-state.json.status` (T19 — see `contract.arming` in a task's own `loop-state.json` for the full ruling, decisions C1-C10). It composes with `--state`/`--cwd` exactly like `--extend-budget`: success exits 0, every refusal exits non-zero and leaves the file byte-untouched, and the human-facing summary goes to stderr.

```bash
python3 "<plugin root>"/scripts/run_gate_checks.py --arm
```

On a successful arm:

- `status` → `"running"`.
- `confirmations` → `0`, always (a fresh arm and a re-arm both reset the streak — a re-armed loop that kept a stale streak would close on a single green evaluation instead of earning K).
- `budget.spent.started_at` — stamped from the engine's own clock on a **fresh** arm, unconditionally overwriting any pre-seeded value; **preserved** on a **re-arm**, since re-stamping would truncate the arm→close window and mint a second run identity in the ledger. Fresh vs re-arm is decided by the durable `budget.spent.first_armed_at` (stamped once, on the first arm, and never cleared), **not** by whether a results-bearing iteration exists — a `blocked_scope` whose only `iterations[]` entry is a `scope_violation` (no `results`) is still a re-arm, and using the results proxy would wrongly re-stamp its window.
- `budget.spent.first_armed_at` — engine-owned, write-once. Stamped equal to `started_at` on the first-ever arm and never overwritten; its mere presence is the "this loop has been armed before" signal that keeps every subsequent `--arm` a re-arm (preserving `started_at` and the ledger identity).
- `owner_session` — stamped with the arming session's id: `--session-id` when given, else `CLAUDE_CODE_SESSION_ID` (set by the host in every Bash subprocess, equal to the `session_id` the Stop hook receives). Claiming at arm time, rather than on the first Stop, is what keeps an unrelated session in the same checkout from taking the loop; a re-arm from a different session (the normal case: work resumes in a new session after a rejection) overwrites the stale id. With neither id, it is cleared, and the first session whose Stop hook drives the running gate claims it.
- One audit entry appended to `iterations[]`: `{"event": "arm", "at": <iso>, "prev_status": <status armed from>, "confirmations_reset_from": <int>, "started_at": <the value now on disk>}` (plus `"user_confirmed"` when `--user-confirmed` was passed). It carries **no `n` key and no `results` key** — the same shape as an `extend_budget` audit entry — so it stays invisible to every consumer that counts evaluations by testing `"results" in it`.

Refused (state untouched) in four cases, checked in this order: the loop is already `running` (arming it again would silently re-stamp a live loop's start instant and reset its streak); there is no admitted check to gate on (`admission.status == "passed"` AND `id` not in `quarantine[]` — the same predicate `run_gate` uses to select checks); the `contract.criteria` coverage contract does not validate (T10 — see below); or **(H7/F6)** an admitted `kind:"guard"` check is **not green at arm time** — its guarded artifact has already regressed, so arming would only flip to `running` and burn the whole budget blocking on a guard that was doomed at t=0. Every other status — `specified`, any `blocked_*`, or an absent/unknown status — is armable. Arming does not run the gate and is never invoked by the gate itself; its **one** deliberate evaluation is the H7/F6 guard-only pre-check — it evaluates admitted **guards only** (a red-first machine check is RED at arm by construction, so evaluating those would refuse every legitimate loop) via the engine's own `evaluate_check`, fail-closed on RED **or** ERROR, solely to refuse an already-broken loop up front. It evaluates them against the **same tree the running gate will** — `work_dir`, resolved by the very `resolve_work_dir(state, cwd)` helper `run_gate` calls (H1/F34) — never `state_root`, so a guard green on the main tree but red in the worktree the gate actually runs against cannot slip through arm; a recorded `worktree.path` that cannot be proven a real, registered worktree of this repo makes `--arm` **refuse, naming the worktree condition**, rather than silently falling back to `state_root` (that silent fallback is F34 recurring). It never confirms, mutates, or persists (`--dry-run` writes nothing on this path either).

## Hold / release — suspending confirmation counting (H4/F24)

Before H4, nothing suspended confirmation counting: a green loop kept incrementing `confirmations` and could stamp `passed_pending_human` at `K` even while a human-approved **contract amendment** was in flight — closing on the very check the amendment exists to replace. `--hold`/`--release` are the fix: two engine verbs, dispatched in `main()` in the same place as `--arm`/`--recover`/`--validate-contract` (before the generic `status != "running"` guard), that bracket the amendment window.

**Both verbs require a `running` loop and refuse otherwise.** The original design let `--hold`/`--release` write to `iterations[]` on *any* status, including pre-arm, on the reasoning that a hold can only *prevent* a close, never *cause* one. That reasoning held in isolation but broke a different invariant: `arm()`'s fresh-vs-re-arm classification relies on "nothing appends to `iterations[]` before the first arm", and a pre-arm hold/release audit entry silently violated it — a genuinely fresh `--arm` was misread as a re-arm and **skipped the arm-time baseline freeze** (`contract.mutation_set.baseline` stayed `None`), which in turn produced a spurious `blocked_scope` on the very first evaluation and defeated the no-work signal below. `hold_verb`/`release_verb` now refuse (non-zero exit, state byte-for-byte untouched, `state["hold"]` never set) on any status other than `running`; arm the loop first (`--arm`), then hold/release.

```bash
python3 "<plugin root>"/scripts/run_gate_checks.py --hold
# ... contract amendment lands (new/updated check admitted, contract.criteria updated) ...
python3 "<plugin root>"/scripts/run_gate_checks.py --release
```

- **`--hold`** sets `state["hold"] = {"at": <iso>[, "user_confirmed": <str>]}`. Refused (state untouched) on any status other than `running`, exactly like `--arm`/`--recover` refuse on their own wrong status — see above. Still conservative in the sense that a *successful* hold can only *prevent* a close, never *cause* one, so **no mandatory `--user-confirmed`** (recorded on the audit entry when supplied, but optional). Idempotent: holding an already-held loop just overwrites the marker with a fresh timestamp.
- **While `state["hold"]` is set**, `run_gate`'s all-green branch checks it *before* the `n == 1` reset and *before* incrementing `confirmations` at all: a held all-green evaluation does **not** advance `confirmations` (frozen exactly where it was — not merely capped below `K`), the loop stays `"running"`, and `status` can **never** reach `passed_pending_human` while the marker is present, no matter how many consecutive green evaluations run. The iteration is still a **real, results-bearing** evaluation — it carries `results` and `mutation_signature` exactly like an ordinary one (H3 stays intact under a hold) — plus `"held": true`, so a human reading `iterations[]` can tell a held green apart from an ordinary one. A held evaluation still returns `DECISION_ITERATE`; only the all-green *close* path is short-circuited. Every other evaluation path (not-all-green, scope, worktree, deadline) is untouched by `hold` — none of them reach the confirmation-increment code a hold guards.
- **`--release`** clears `state["hold"]` (the key is deleted, never set to `null`) and **zeroes `state["confirmations"]` unconditionally** — a streak earned against the superseded check must not carry over once the amendment lands, so counting resumes strictly **from 0** on the next evaluation. Unconditional even if no hold was in force (a no-op release is harmless). Refused (state untouched) on any status other than `running`, mirroring `--hold`'s guard.
- **Audit entries.** Both verbs append one entry to `iterations[]` — `{"event": "hold", "at": <iso>, "prev_hold": <prior state["hold"] or null>[, "user_confirmed"]}` and `{"event": "release", "at": <iso>, "prev_hold": ..., "confirmations_reset_from": <int>[, "user_confirmed"]}` respectively — with **no `n` key and no `results` key**, the same shape as `arm`/`extend_budget`/`recover`, so evaluation numbering and the confirmation streak stay blind to them.
- **Without a hold, behavior is byte-identical to pre-H4** — `state.get("hold")` is falsy on every loop that never calls `--hold`, so the all-green branch's normal `n == 1` reset / increment / `K`-threshold check runs exactly as before.
- Honors `--dry-run` like every other admin verb: prints what WOULD change to stderr and persists nothing.

## `contract.mutation_set`

The loop's mutation set — which paths a run touched — is ground-truthed against **git**, never against the trace alone. `hooks/scripts/trace-op.sh` classifies `Write`/`Edit`/`MultiEdit`/`NotebookEdit` as `kind: "mutate"` and `Bash` as `kind: "exec"`, so a file changed by a *script* invoked through Bash (e.g. a heredoc write) leaves no mutate trace op for that path — proven live in the T14-T15 internal run (finding F12). A trace-only view of "what changed" is therefore a lie by omission: any bulk edit done via a script bypasses it entirely. The rule this contract encodes: **git says *what* changed; the trace only says *who* changed it.**

The Technical Lead freezes the contract at arm time under `contract.mutation_set` in `loop-state.json`:

```json
"contract": {
  "mutation_set": {
    "baseline": {
      "recorded_at_arm": true,
      "ref": "6477c9be4e7f31f0554d94e6b69923c77fc18aa0",
      "pre_dirty": []
    }
  }
}
```

- `baseline.ref` — the **frozen arm-time `HEAD` sha**, captured once when the loop is armed. Never re-resolved to live `HEAD`: this repo's loops commit mid-run (T16 committed while running), and diffing against live `HEAD` would silently erase every already-committed mutation from the set — the exact false-empty failure mode this contract exists to prevent.
- `baseline.pre_dirty` — paths already dirty in the work tree **at arm time**, each **anchored to its arm-time content**: `[{ "path": "<repo-relative>", "sha": "sha256:<hex>" }, …]`, written by `--arm` via `run_gate_checks.pre_dirty_anchors(cwd, paths)`. Git cannot tell *when* a path became dirty, so this list is caller-supplied, not derived. A path is marked `pre_existing: true` **only while it is byte-identical to its anchor** — "pre-existing" means *unchanged since arm*, not merely *named in this list*: a pre-dirty path rewritten during the run becomes an ordinary member, fully subject to scope, so a file dirty at arm can no longer be edited out of scope for free. **Backward compatibility:** a legacy bare-string entry (`pre_dirty: ["path", …]`) is still accepted, but it carries no anchor and therefore **cannot be proven unchanged — it fails closed** to `pre_existing: false` (an ordinary, scoped member). Every member is still reported (marked `pre_existing` true or false), never dropped; policy on them belongs to the consumer (the `contract.scope` hard-stop below), not to this contract.

`run_gate_checks.compute_mutation_set(cwd, arm_ref, pre_dirty=None, trace_path=None)` implements the contract:

- **Membership** — `union(git diff --name-only <arm_ref>, git ls-files --others --exclude-standard)` **minus the loop's own workspace** (`.fairmind/`). `.fairmind/` is dropped **structurally** (`_is_loop_workspace_path`), gitignored or not: the zero-config bootstrap writes `active-context.json` and never touches `.gitignore`, so a consumer repo that never ignored `.fairmind/` would otherwise see the gate's own state file and trace ledger land in the untracked half and trip the scope boundary against the loop itself. `--exclude-standard` is kept, but only for the **consumer's** ignores (build output, `node_modules`, …) — it is *not* what keeps `.fairmind/` out. Membership is never derived from trace `kind == "mutate"` — only from git.
- **Attribution decorates, it does not filter** — the trace JSONL at `trace_path` (one op per line: `{ts, agent, tool, kind, target}`) is read for `kind == "mutate"` lines only; a git-reported path with a matching mutate op is tagged with that op's agent, and any other path is reported with `agent: "unknown"` — never dropped. `"unknown"` must be a **true negative** (the trace genuinely has no mutate op for that path), never an artifact of a format mismatch.
  - **The join normalizes `target` before comparing it to git's paths (contract amendment 2, human-approved at the gate).** `hooks/scripts/trace-op.sh` writes `target` as the tool's raw `tool_input.file_path` — an **absolute** path — while git reports **repo-relative** paths. Comparing the two raw strings never matches. This shipped once as a green-but-wrong check: the checker's original fixture hand-built trace lines with repo-relative `target`s (a shape the hook never emits), so the assertion passed against a fiction; run against this loop's own real trace, every single path came back `"unknown"`, including the ones the trace explicitly attributed. **Rule: any fixture standing in for a real artifact must use that artifact's real format, or the assertion proves nothing about the artifact** (`contract.mutation_set.fixture_fidelity`).
  - Normalization, in order: `repo_root = realpath(git rev-parse --show-toplevel)`; (1) an empty/non-string `target` is unattributable; (2) a `target` ending in `"..."` is the hook's truncation marker — unattributable, **never** prefix-, substring-, or fuzzy-matched (a wrong attribution is worse than `"unknown"`); (3) an absolute `target` is joined via `relpath(realpath(target), repo_root)` — `realpath` **both** sides (a symlinked temp root, e.g. macOS `/tmp` → `/private/tmp`, would not otherwise join) — and if the result escapes the tree (starts with `..`) the op is ignored (the git-reported path, if any, is never dropped because of it); (4) an already-relative `target` is accepted unchanged (liberal in what's accepted — the hook's format may vary by tool/version); (5) separators are normalized to POSIX `/`.
  - **The truncation marker exists because `trace-op.sh` truncates cosmetic fields (a Bash `command`, a Task `prompt`, a `description`/`pattern`/`url`) at 120 chars via `tr(s, n=120)` — but a mutate op's `target` (`Write`/`Edit`/`MultiEdit`/`NotebookEdit`) is never truncated.** A filesystem path is the exact-match join key this whole normalization depends on, so truncating it would silently destroy the join — a live near-miss: the longest real mutate target measured in this repo's own trace was within 2 characters of the old 120-char cutoff before the hook was fixed to exempt mutate targets.
  - **Contested path → last mutate op in trace order wins** (the trace is append-only chronological); `agent` is reported verbatim, never reformatted.
  - **If `git rev-parse --show-toplevel` itself fails**, attribution is unavailable — every path reports `"unknown"` — but membership is untouched and this is explicitly **not** a degraded marker: git already answered "what changed" via the membership queries; only "who" is unresolved.
- **Degradation is explicit, and three markers are distinguished** — checked in this order, `paths` is always `[]` for any of them:
  - `no-baseline-ref` — `arm_ref` is `None`/empty (no frozen arm-time sha to diff *from*). Checked **first, before any git argv is built**, so a `None` ref can never reach `git diff <ref>` as a raw `TypeError` (which escaped `_GitQueryError`, crashed the gate at exit 1, and left `status` stuck on `running` with no saved state — every later Stop re-crashed, and `--arm` refuses a running loop, so the documented re-arm recovery did not exist). The set is UNKNOWN, not empty; the remedy is re-arm / write the sha.
  - `no-git-work-tree` — `cwd` is not a git repo at all (`git rev-parse --is-inside-work-tree` fails or is not `"true"`). Checked second.
  - `git-query-failed` — `cwd` IS a work tree, but a git query used to build the set exited non-zero: `git diff --name-only <arm_ref>` (e.g. `arm_ref` unresolvable after a reset/gc, or a `loop-state.json` copied into another clone) or `git ls-files --others --exclude-standard`. Added by contract amendment 1, human-approved mid-loop, after the maker found that the original implementation let each query independently swallow a non-zero exit to `[]`: with a bad `arm_ref`, the tracked-modified half of the set vanished silently while the untracked half kept landing, so the result looked like a healthy, non-empty set while actually being a wrong, partial one — worse than an empty set, because nothing signals the tracked-modified half is missing. The fix is a **whole-set failure**: if either query fails, the *other* query's result (even if it succeeded) is discarded too, and the result carries `"error"`, a non-empty string naming the failing git subcommand and its stderr, e.g. `"git diff --name-only 000...0 -> exit 128: fatal: bad object 000...0"`.
  - Never a bare empty list from any error path, and never a silent fallback to the trace-only derivation.
- **Consumer rule** — a non-null `degraded` (either marker) means the mutation set is **UNKNOWN, not empty**. A consumer (T8's future scope hard-stop, or any other) must fail closed or surface the condition to a human; it must never read a degraded result as "nothing was mutated" and proceed as if the set were empty.
- Return shape: `{"degraded": None | "no-baseline-ref" | "no-git-work-tree" | "git-query-failed", "paths": [{"path": <repo-relative str>, "agent": <str, "unknown" if unattributed>, "pre_existing": <bool>}, ...], "error": <str, present only when degraded == "git-query-failed">}`, `paths` sorted by path for determinism when not degraded.

`compute_mutation_set` is a standalone helper, independent of check evaluation. Before H3 its only consumer was the `contract.scope` hard stop below (`evaluate_scope`), which the gate's evaluation loop (`run_gate`) calls first, before any check verdict is considered — and `evaluate_scope` itself is a no-op whenever no `contract.scope` is declared, so a scope-less loop never invoked `compute_mutation_set` at all. Since H3, `run_gate` also calls it **unconditionally**, regardless of `contract.scope`, to build the no-work signal below.

## No-work re-evaluation accounting (H3/F21+F33)

Before H3, `run_gate`'s not-green branch advanced `budget.spent.iterations` and every admitted check's `consecutive_failures` on **every** turn-ending evaluation — even when nobody had done any work since the previous evaluation. A background maker whose Stop hook fired twice against the same half-written tree burned two budget iterations and could trip a spurious **STRATEGY TURN** (`commitment_boundaries`, fired when a check's `consecutive_failures` reaches `2`, one below the default cap) on a check that was not genuinely failing twice in a row — the tree just hadn't moved between the two evaluations.

H3 makes such a re-evaluation a **no-work re-evaluation**: it consumes no budget iteration, does not advance any check's `consecutive_failures`, and therefore never raises a spurious STRATEGY TURN. The check's verdict is still recorded (the status board still shows red) — only the *accounting* freezes.

**The signal — `run_gate_checks._no_work_signature(state, work_dir, trace_root)`.** A path-set-only "did the mutation footprint change?" test is not enough: the same file edited twice in a row has an **identical path set but different content** — real work a path-only delta would misread as no-work. The signal is therefore **content-sensitive**: it reuses `compute_mutation_set(work_dir, contract.mutation_set.baseline.ref, contract.mutation_set.baseline.pre_dirty, trace_path)` for membership (the same git-grounded path set `contract.scope` trusts), then re-hashes each member path via `_working_tree_sha` (the same byte-identity primitive that anchors `pre_dirty`). The resulting **mutation signature** is `[[path, sha256-or-null], ...]`, sorted by path — the empty list is itself a valid, comparable signature ("nothing has been touched since arm at all").

**Where it's stored.** Every results-bearing `iterations[]` entry — win, lose, or draw — carries `"mutation_signature"` (the list above, or `null` when degraded) and, only when degraded, `"mutation_signature_degraded"` (one of `compute_mutation_set`'s markers: `no-baseline-ref`, `no-git-work-tree`, `git-query-failed`). `run_gate` compares **this** evaluation's signature to the **immediately preceding results-bearing iteration's** recorded signature (skipping audit-only entries — `arm`, `extend_budget`, `scope_violation`, `worktree_degraded` — exactly like every other "prior evaluation" lookup in this engine).

**Semantics (fail-closed by construction):**

- The **first** evaluation after arm (no predecessor results-bearing iteration) always counts as work — there is nothing to compare against yet.
- A **degraded/unknown** signal on either side of the comparison — this evaluation's own signal, *or* the predecessor's recorded signature (absent because it predates H3, or itself `null` from a degraded evaluation) — makes "did work happen?" **unanswerable**, and the evaluation counts exactly as it did before H3. "Unknown" is never read as "no work."
- Only when **both** signatures are known and **byte-identical** is this a genuine no-work re-evaluation: `spent.iterations` does not increment, no admitted check's `consecutive_failures` advances (a check that just went green still resets to `0` — that reset is idempotent either way), and `commitment_boundaries` therefore never sees a check's `consecutive_failures` reach the STRATEGY TURN threshold on a no-work re-run.
- A **genuine content change that lands inside the git-tracked-content mutation set** — even one that leaves the path set unchanged, e.g. the same tracked (or untracked-but-not-ignored) file edited a second time — still counts as real work and advances the accounting exactly as before H3.

**Limitation, stated plainly (not "any content change counts"): the signal can only see what `compute_mutation_set` can see.** The mutation set's membership is `git diff --name-only <arm_ref>` union `git ls-files --others --exclude-standard` — git-tracked content only — and the signature itself is a **content-only** byte hash (`_working_tree_sha` reads raw bytes, nothing else). A genuine change invisible to that view is therefore **not counted as work** and will freeze `spent.iterations` / `consecutive_failures` exactly as if nothing had happened, even though something genuinely did:

  - a change confined to a **gitignored path** — `--exclude-standard` excludes it from membership entirely, so it is never hashed;
  - a **file-mode-only** change (e.g. `chmod +x` with no byte change) — even on a path that IS a member, the content hash is unchanged, so the signature is unchanged;
  - **out-of-repo state a check reads** — a database row, an environment variable, a remote service response, anything outside this working tree — leaves the mutation set (and therefore the signature) identical between evaluations no matter what changed.

  For a loop whose real work legitimately lives in one of these blind spots, the no-work freeze can persist indefinitely; `budget.timeout_min` (a wall-clock cap, not a signature-gated one) is the intended backstop that still lets such a loop eventually block for a human rather than spin forever.

This is independent of, and does not weaken, `contract.scope` (T8): a scope violation is still a terminal `blocked_scope` stop evaluated *before* the no-work signal is ever computed, and the no-work signature is computed **unconditionally**, whether or not `contract.scope` is declared — unlike `evaluate_scope`'s own `compute_mutation_set` call, which only runs when a scope is.

## `contract.scope` — the scope-boundary hard stop (T8)

A loop may optionally declare an allowed mutation scope. On **every** gate evaluation, before any check is run, the engine cross-checks this run's git-derived mutation set (`compute_mutation_set`, above) against the declaration; any mutated path outside it is a **terminal hard stop** — never an ordinary red, never counted against budget.

```json
"contract": {
  "scope": { "allowed_paths": ["**"] },
  "mutation_set": { "baseline": { "recorded_at_arm": true, "ref": "6477c9b…", "pre_dirty": [] } }
}
```

- **Declaration** — `contract.scope.allowed_paths`, a glob list. **Absent, or an empty/missing list, is a no-op** — identical to pre-T8 behavior. This is the only trigger; the absence of a trace file is never one.
- **Membership** is `compute_mutation_set(cwd, contract.mutation_set.baseline.ref, contract.mutation_set.baseline.pre_dirty, trace_path)` — git, exactly as `contract.mutation_set` defines it above. The trace only decorates the report with *who* mutated a path (`agent`, `"unknown"` if unattributed); it never decides *what* is in scope.
- **`trace_path`** is resolved through the one canonical helper `run_gate_checks.trace_path(cwd, ref=None)`, fixed to `--cwd` and independent of `--state`/`base_path`: `<cwd>/.fairmind/trace/<safe>.jsonl`, `safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or "session"`. With `ref=None` (the scope boundary's case) the ref is read **from `<cwd>/.fairmind/active-context.json`** (`task_ref`, then `taskRef`, else `"session"`) — **exactly as `hooks/scripts/trace-op.sh` derives the filename it writes**. It is **not** derived from loop-state's `target.ref`: the hook never sees loop-state, and a session working a task ref other than the loop's target would otherwise be read from the wrong file, silently losing every attribution to `"unknown"`. (`scripts/loop_dashboard.py` is the same helper's other caller — it passes an explicit per-row historical `ref`.)
- **Glob semantics** — a repo-relative POSIX path is IN-SCOPE iff `fnmatch.fnmatch(path, glob)` is `True` for ≥1 allowed glob (stdlib `fnmatch`: `*` matches `/`). A path reported `pre_existing: true` is exempt from the violation set — but `pre_existing` means **byte-identical to its arm-time anchor** (see `contract.mutation_set.baseline.pre_dirty`), *not* merely "named in `pre_dirty`": a dirty-at-arm path rewritten during the run — or one carried only in the legacy anchor-less shape — is **not** exempt and is enforced like any other mutation.
- **Violation → `blocked_scope`** — any non-`pre_existing` path matching no allowed glob makes the whole evaluation a terminal `blocked_scope` stop. The surfaced report names every violating path and its attributed agent. An audit entry is appended to `iterations[]`: `{"event": "scope_violation", "at": <iso>, "paths": [<violating paths>], "agents": [<attributed agents>]}` — no `"n"` key and no `"results"` key, so it is invisible to every counter that tests `"results" in it` (budget spend, the confirmation streak, per-check `consecutive_failures`): a scope breach is not a failed evaluation attempt, it never happened as far as the checks are concerned.
- **Degraded mutation set → fail closed** — if `compute_mutation_set` reports a non-null `degraded` (`no-baseline-ref`, `no-git-work-tree`, or `git-query-failed`) while a scope is declared, the set is UNKNOWN, never read as "nothing mutated": the evaluation still stops `blocked_scope`, carrying the degraded reason both in the report **and on the persisted `scope_violation` audit entry** (`entry["degraded"] == "<marker>"`, readable from `iterations[]` alone), rather than risk an undetected out-of-scope mutation slipping through as a green close.
- **`blocked_scope` is a `blocked_*` status** like the others: it blocks the stop once on the transition (exit 10, surfacing the report), is `startswith("blocked_")`-armable via `--arm` and extendable via `--extend-budget` exactly like any other block, and the next stop allows (exit 0) once the terminal status is persisted.
- Implementation: `evaluate_scope` (defined in `_gate_mutation.py`, imported back into `run_gate_checks.py`), called as the first statement of `run_gate` — before hermeticity resolution, `admitted_checks`, or any check is run.

## `contract.criteria` — the arm-time coverage contract (T10)

Arming validates the contract itself: every **hard** acceptance criterion must be covered by a **live, admitted** check, or the loop is not armable. `contract.criteria[]` is the classified acceptance criteria the Technical Lead persists in Phase 0 — one entry per criterion — and it is **mandatory for arming, never inferred**: a loop with `contract.criteria` absent, null, or empty is **refused** by `--arm`.

```json
"contract": {
  "criteria": [
    { "id": "<ac-id>", "text": "<the acceptance criterion, verbatim>",
      "disposition": "checked:<id>" | "evidence:<id>" | "quarantined:<id>" | "unverifiable",
      "hard": true }
  ]
}
```

- `id` / `text` — the criterion's stable id and its human-readable statement.
- `hard` — **defaults to `true`** (fail-closed): a criterion is HARD unless it explicitly declares `hard: false`. A hard criterion must be covered by a live, admitted check; only an explicitly advisory (`hard: false`) criterion may go uncovered — and even then it is **named on stdout** so the human signing the arm sees the hole.
- `disposition` — how the criterion is covered, cross-checked against the engine's real check state (`admitted_checks()`), never merely parsed:

| Disposition value | Valid iff |
|---|---|
| `checked:<id>` | `<id>` exists in `checks[]` and is **admitted** (admission passed and not in `quarantine[]`). |
| `evidence:<id>` | as `checked:`, and the named check is evidence-kind/type. |
| `quarantined:<id>` | `<id>` is actually in `quarantine[]`. Valid **only on a `hard: false`** criterion (a quarantined check contributes nothing to the stop condition). |
| `unverifiable` | Valid **only on a `hard: false`** criterion. |

The documented disposition set equals the engine's exported `run_gate_checks.CRITERION_DISPOSITIONS` (`checked` / `evidence` / `quarantined` / `unverifiable`), so the reference and the engine cannot drift.

**Two verbs, one code path** (`validate_contract` in `run_gate_checks.py`):

- `run_gate_checks.py --validate-contract` — **read-only**: prints the coverage report (naming every uncovered hard criterion, and every advisory `hard: false` hole, by id) and exits `0` when armable, non-zero otherwise. Never mutates `loop-state.json`, never evaluates a check.
- `run_gate_checks.py --arm` runs the **same** validation internally, as its **third** refusal gate (after already-`running`, then no-admitted-check), and **refuses** an invalid contract — so the guarantee cannot be bypassed by an orchestrator that forgets the read-only verb. On refusal it names every offender on stderr, recommends running the task in **interactive** mode rather than arming a weak gate, and leaves `loop-state.json` byte-untouched (status not flipped, `started_at` not stamped, no arm audit entry).

Validation is **arm-time / contract-time only** — `run_gate` (the evaluation path) gains no criteria logic (a `g-gate-eval-untouched` guard protects this), so amending `contract.criteria[]` mid-run never changes an in-flight evaluation.

## `worktree` — opt-in maker isolation (T9)

```json
"worktree": { "path": "/abs/path/.fairmind/worktrees/T-142", "branch": "loop/T-142" }
```

Optional and top-level; declaring it is never *required* to arm a loop (AC3 — an absent `worktree` key is a legitimate, backward-compatible no-op). But once declared, **the gate does read it** (H1/F34, below) — it is not an inert, orchestrator-only breadcrumb. It exists so the maker's edits can, by choice, land in a dedicated git worktree instead of the user's own checkout, recorded here for the orchestrator (and the exit report) to find:

- `worktree.path` — the real, on-disk path of the linked worktree.
- `worktree.branch` — always `loop/<taskRef>`, the branch the worktree is checked out on (based on `HEAD` at creation time).

**The gate follows a recorded, valid worktree (H1/F34).** `run_gate`'s first action, on every evaluation, is `resolve_work_dir(state, cwd)`. When `worktree.path` resolves to a real, currently-registered worktree of the same repo as `state_root` (`--cwd`), that path becomes `work_dir` — the tree every check verdict and the `contract.scope` boundary are evaluated against, and the tree every check subprocess actually runs in (so a functional check sees the maker's edits even when they never touched the user's own checkout). Only the trace file stays pinned to `state_root` (`trace_root`, always `--cwd`, never `work_dir`): `hooks/scripts/trace-op.sh` writes `.fairmind/trace/` under the CLAUDE_PROJECT_DIR the hook sees, not inside a worktree — so agent attribution keeps resolving correctly even while checks run against the worktree. Absent a `worktree` key, `work_dir == trace_root == state_root`, identical to pre-T9 behavior.

**Fail-closed: `blocked_worktree`.** A recorded `worktree.path` that is *not* a real, registered worktree of the state's own repo (missing on disk, not a directory, not a git work tree at all, or a git work tree registered to some *other* repo) must never be silently substituted with `state_root` — that silent fallback is exactly F34 (checks quietly evaluated against the main tree while the maker's actual change lived only in the worktree, including the T8 scope boundary passing vacuously on a worktree-only mutation) recurring under a different name. Instead `resolve_work_dir` reports the exact reason, and `run_gate` terminates the WHOLE evaluation — before the scope boundary, before hermeticity resolution, before any check runs — to `status: "blocked_worktree"`, with a `{"event": "worktree_degraded", "at", "reason", "detail"}` entry appended to `iterations[]` (no `"n"`/`"results"` keys, the same family as `scope_violation`, so it never counts as a budget-spending evaluation). A human must resolve it — re-create the worktree (`loop_worktree.py --create`) or clear `state["worktree"]` — before the loop can be re-armed.

Written by `scripts/loop_worktree.py`, a standalone stdlib helper — not by `run_gate_checks.py`:

```bash
python3 "<plugin root>"/scripts/loop_worktree.py --create --task-ref <ref> --state "${FAIRMIND_BASE}/loop-state.json"
python3 "<plugin root>"/scripts/loop_worktree.py --cleanup --task-ref <ref>
```

- `--create` resolves the repo root, then creates (or, if one is already registered for `loop/<ref>` at the same deterministic path, **reuses**) the worktree — idempotent, safe to call again on the same (repo, ref). A `--state` path gets `worktree.{path,branch}` recorded into it, preserving every other field already there. If `loop/<ref>` is already checked out at a *different* location, or `--cwd` is not inside a git work tree at all, it refuses (non-zero exit) with no side effects — `loop-state.json` is left untouched.
- `--cleanup` removes the worktree (`git worktree remove --force`) so it no longer shows up in `git worktree list`, but **never deletes the `loop/<ref>` branch** — the maker's commits stay reachable for the exit report to diff. Cleaning up a ref with no registered worktree is a benign no-op (still exit 0).

## Gate deadline (fail-closed)

Separate from the `budget` (which counts *iterations*), every single gate evaluation has a **wall-clock deadline** enforced inside the engine: `min(Σ(exec.timeout_s × determinism.runs) + 60s, 540s)`. It exists because the gate runs as a Stop hook with a hard `timeout` (600s): if a runaway check let the evaluation overrun that, the hook would be *killed* and the turn could end **unguarded** — a false green, the worst possible failure. So the engine self-limits: each check's subprocess timeout is capped to the remaining budget, and once the budget is spent every unfinished check is recorded `ERROR("gate deadline exceeded")`. ERROR is never green, so a deadline can only ever *block* (exit 10), never pass. Feedback carries a `[DEGRADED: gate deadline]` label so the human sees the gate was truncated. `FAIRMIND_GATE_DEADLINE_S` (seconds) can only *tighten* the deadline — a test/escape hatch, never a way to run longer than the 540s cap.

## A check descriptor

```json
{
  "id": "story-142-guest-checkout-e2e",
  "kind": "machine",
  "type": "functional",
  "owner": "software-engineer",
  "source": {
    "authored_by": "qa-engineer",
    "admitted_hash": "sha256:…",
    "red_first_proof": { "commit": "9f3a1c0", "red_value": 3 }
  },
  "exec": { "command": "npx playwright test guest-checkout --reporter=json",
            "timeout_s": 300, "expects": "file", "network": "forbidden" },
  "signal": { "from": "file_json", "file": "playwright-report.json",
              "selector": "$.stats.unexpected", "value_type": "count", "on_missing": "error" },
  "predicate": { "operator": "==", "value": 0 },
  "baseline": null,
  "regression_guard": [],
  "determinism": { "runs": 1, "probe_k": 3, "confirmation_k": 3 },
  "admission": { "status": "passed",
                 "gates": { "clean_signal": true, "red_first": true, "sensitivity": "unverified" } },
  "consecutive_failures": 0
}
```

| Field | Meaning |
|---|---|
| `id` | Stable, unique per check. |
| `kind` | `"machine"` (executed) or `"evidence"` (verdict artifact). |
| `type` | `functional` \| `metric` \| `performance` \| `static` \| `evidence` \| `custom`. |
| `owner` | The maker who fixes a RED (e.g. `software-engineer`). **Must differ from `source.authored_by`.** |
| `source.authored_by` | The checker who authored the check (e.g. `qa-engineer`, `code-reviewer`). Enforces maker ≠ checker. |
| `source.red_first_proof` | `{ commit, red_value }` — evidence the check failed on pre-fix code. **Written by the engine** (`admit_check.py`) at admission, not authored up front: the live probe is always the source of truth, and a live-RED check with no recorded proof is admitted with the engine recording what it observed (`red_value` + the current `HEAD` sha, or `commit: null` when `--cwd` isn't a git tree). Optional at spec time — nobody needs to pre-populate it (the check doesn't exist yet when the Technical Lead specifies it, and the QA Engineer owns tests, not gate artifacts). A pre-recorded value is still honored, but validated, never trusted blindly: one that already *satisfies* the predicate is rejected regardless of the live value (anti-tautology), and a human-recorded proof is never overwritten. |
| `source.admitted_hash` | Set by `admit_check.py` on a pass — integrity hash over the contract fields. The engine rejects a descriptor changed since admission. |
| `source.evidence_hash` | (evidence only, required) content anchor the verdict file must match, else stale → ERROR. |
| `exec.command` / `exec.argv` | Shell command string (portable, via `/bin/sh -c` or `cmd /c`) **or** an argv list (no shell). |
| `exec.timeout_s` | Per-run timeout; a timeout is an `error` verdict, never a pass. |
| `exec.network` | `"forbidden"` → wrapped in `srt` when Tier A. |
| `signal.from` | `exit_code` \| `file_json` \| `stdout_json` \| `stdout_regex`. |
| `signal.file` | For `file_json`: path (relative to repo root) the command writes. |
| `signal.selector` | Minimal JSONPath subset: `$.a.b[0].c`. |
| `signal.value_type` | `count` \| `number` \| `duration_ms` \| `bool` — a **closed set, enforced at admission**. A gate signal is a *measurement*; there is no string type and a check cannot be authored to extract one. (Until JC12 the set was closed in this table only: an unrecognised type made the engine pass the raw value through, so a check declaring `"string"` shipped its command's stdout as `results[].value`. `admit_check` now quarantines it and `coerce_value` refuses it.) |
| `signal.on_missing` | `"error"` (recommended) \| `"fail"` \| a literal fallback value. **`"error"` guarantees absence never reads as a pass** (clean-signal). |
| `predicate` | `{ operator: ==,!=,<,<=,>,>=, value }` evaluated against the signal. |
| `baseline` | Frozen baseline for reduce/improve goals; `null` otherwise. Either a bare number (back-compat) or a provenance object `{ "value": <n>, "ref": "<sha>", "clean": <bool> }` as emitted by `capture_baseline.py`. When `clean: false` (measured on a dirty tree, not a committed ref) the gate labels the check `[DEGRADED: baseline dirty-tree]` in every report. |
| `regression_guard` | Extra predicates vs the baseline (`value: "baseline"` substitutes the numeric baseline). |
| `determinism.runs` | Times the engine runs the check per evaluation (>1 → differing values ⇒ `inconclusive`). |
| `determinism.probe_k` | Runs used by admission to certify determinism. |
| `determinism.confirmation_k` | Consecutive green evaluations required to stop (default 3; the gate uses the max across checks). |
| `admission.status` | `passed` \| `failed`. `failed` checks are excluded from the stop decision. |
| `consecutive_failures` | Runtime counter, reset to 0 on green; advances by 1 on a genuine non-green evaluation. Since H3 (F21+F33), it does **not** advance on a **no-work re-evaluation** (below) — the check stays exactly where it was, because nothing has actually failed a second time. |

## Verdicts

Each evaluation yields one verdict per check: `green` (predicate satisfied and guards hold), `red` (predicate/guard fails), `error` (missing signal with `on_missing:"error"`, or a timeout — never a pass), or `inconclusive` (non-deterministic across `runs`). The loop closes only on `confirmation_k` consecutive evaluations where **all** active checks are `green`.
