---
name: fairmind-gate
description: Use in loop mode to design the machine-checkable stop condition - classify each acceptance criterion into a check type, specify descriptors into loop-state.json, author checks RED-first (maker != checker), run admission, and understand how the executed gate evaluates them
---

# Fairmind Gate Layer

> Not Fairmind's Code Oracle (Curator, docs⇄code): the gate verifies code against checks.

## Overview

Loop mode replaces "prompt every turn" with a **machine-verifiable stop condition**. A **check** is a *measurable predicate* the harness can evaluate — not "a QA test". This skill is how the Technical Lead (specify) and the checker-side agents (author) build that stop condition, and how the executed gate (`run_gate_checks.py`, fired by the `loop-check.sh` Stop hook) evaluates it.

In Fairmind's stacked-loop model the executed gate is the task loop's **binary oracle** — it answers *does it work?* The loop's other exit question is **completeness** — *is this the right work?* — and since 2026-08-24 the engine gates on that too: `green × K` no longer closes a loop on its own. It leaves the loop `running` until a reviewer that owns no check in the contract records a verdict (`run_gate_checks.py --record-completeness`), and `--arm` refuses a loop with no design brief for that reviewer to read.

🔴 **Be exact about what each half is, because overclaiming here is the error this paragraph exists to prevent.** The gate is executed code: it measures predicates. The completeness verdict is a **judgement** — the engine proves that a verdict was recorded, by a non-maker role, against a harness-written attestation, for the tree that is actually there; it cannot prove the reviewer reasoned well, and the attestation is a file under `.fairmind/` a session could write. Likewise `--arm`'s brief check proves a brief exists and carries prose, never that it answered anything. And `check-journal.sh` still only requires that a journal was *written* (any recent `.md` in the scoped directory), never that its contents match a task, a story or an architecture — `run_gate_checks.py` does not read a journal at all. So the **final human gate stays load-bearing rather than ceremonial**. Use both names when explaining the contract, and say who owns what: every terminal state other than `passed_pending_human` is a `blocked_*` stop, and the journal hook refuses a code-mutating sub-agent that wrote none *during* the loop, not at its close.

**Why the gate cannot ask the second question itself.** The checks are compiled from the task's criteria, so when the *criteria* are wrong the checks are wrong with them — in the same direction, and green. Measured on a real card: the contract pinned an invariant at the HTTP boundary because the ticket's prose mentioned the route, every check passed on the **first** evaluation, `coverage` read 1.0, and any non-HTTP caller could still write an invalid row into an append-only log. A loop whose only completeness signal is its own checks rewards writing nothing more.

So the reviewer's input is the **design brief** (`references/design-brief.md`), not the contract, and the question is *does the diff implement every decision in that brief, at the layer it named* — one no check in the contract can ask.

**Announce at start:** "I'm using the fairmind-gate skill to define the loop stop condition."

Load this skill in loop mode only. Interactive mode is unchanged.

This skill covers the checked surface — classifying, specifying, authoring, admitting. The one human channel *outside* it, the mid-loop steering file the maker reads each iteration, is documented separately: `references/steering.md`.

## Non-negotiable principles

- **maker ≠ checker.** The check for a criterion is authored by an agent *other than* the maker who implements/fixes it (`source.authored_by` ≠ `owner`). The engine enforces this structurally — a self-authored check yields ERROR, never green.
- **RED-first, proven live.** Every authored check must actually fail on the current (pre-fix) code *at the moment admission runs it* — so author and admit while the code is still red, before the maker fixes anything. The proof is the live probe, never a claim: `admit_check.py` writes `source.red_first_proof` (`{commit, red_value}`) itself from what it observed. Nobody hand-authors it.
- **Clean signal.** `signal.on_missing` must be `"error"` (or `"fail"`) — the absence of a result is **never** read as a pass. A network blip, a crashed runner, a stale report → ERROR, not green.
- **Assert the artifact, not the mention.** A check must verify the thing the criterion is *about* is actually right — never that a string naming it is merely present. A content-grep for a label, field name, or flag passes on code that *mentions* it while doing the wrong thing: a rename check that greps for the new name, a telemetry check that greps for a word, a "the two strings differ" check — each stays green while the value behind the label is still wrong (a counter whose name says "budget" but whose number is an evaluation count; an emitted field present but carrying the wrong content). For any naming / telemetry / format / doc criterion, assert the **values** agree with the label — the number the name promises, the content the field must carry — not that the label exists.
- **Confirmation-gated stop.** The loop closes only after **K ≥ 3 consecutive green evaluations** (a false GREEN would ship "proven"; a false RED only wastes one iteration). The engine floors K at 3.
- **Portable by construction.** No sandbox is required. Tier A (`srt`) is used if present; otherwise Tier B (determinism probe) applies and checks are reported `hermeticity-unverified`.

## Authoring lessons the engine can't enforce

The principles above are enforced by the engine. These are craft rules it *cannot* enforce — each one names a way a fully green gate has still shipped a wrong result. They extend "assert the artifact, not the mention" above; apply them while classifying (Technical Lead) and authoring (checker, via `authoring-brief.md`) every check.

- **Assert it passed for the right reason.** One level below "assert the artifact": verify the *outcome* the criterion is about, not the *mechanism* a fix happened to choose. A hermeticity check asserts *the child resolved its own state*, not *this env var is absent* — a different var can win and the "absent" check still passes. A worktree check asserts *the loop gated the tree the maker wrote*, not *the helper recorded a path*. Asserting the mechanism greenlights a fix that pulled the wrong lever.
- **A guarantee that depends on a step having run is not a guarantee — unless the engine can prove the step ran.** RED-first, maker ≠ checker, and clean-signal hold only because the engine enforces them by construction — RED-first proven live at admission (`admit_check.py`), maker ≠ checker and clean-signal re-checked again at every evaluation (RED-first is admission-only: nothing at evaluation time can notice it never happened, which is exactly why admission must). A discipline that lives only where the engine can't observe it — a hand-written `admission: passed`, a rule recorded only in a log — gates nothing. When a criterion's force depends on a step, make the engine prove the step happened (the pattern already in the codebase: `--arm` enforces `contract.criteria` coverage itself).
- **Drive the real sequence; never pre-seed the system's internal counters.** A fixture that seeds `confirmations = 2` to reach K in one green evaluation — or hand-sets any internal state the check is *about* — silently encodes the defect under test, and admission cannot see it: the seeded fixture is both live-RED and deterministic, so it clears every gate. Reach the asserted state by driving the genuine sequence.
- **A fixture must carry its producer's real format — take the shape from the producer, never from your expectation.** A stand-in for a real artifact (a trace line, a payload, an id, a config record) hand-built in a shape the producer never emits proves nothing about the artifact: the assertion passes against a fiction while the real input still fails. A fixture derived from the *expected output* is the same defect turned inward — the check agrees with itself and can never catch the producer drifting. Admission cannot see either one (a fabricated fixture is both live-RED and deterministic), and no amount of loop iteration will surface it, because the fixture *is* the gate's reality. Since the checker often cannot reach the producing code, the Technical Lead's dispatch carries the real sample payload inline; out of reach and not in the dispatch ⇒ INPUT GAP, not an invented shape.
- **"Degrades explicitly / never silently" ⇒ enumerate every branch, not the one the ticket names.** For any criterion of that shape, cover *every* early-return and exception-swallowing path in the surface under test. Naming only the degradation the spec mentions (e.g. "no work tree") leaves the sibling silent-false branches (a failed `git diff` swallowed to `[]`) green — the exact class the criterion exists to kill, one branch over.
- **Re-derive each criterion against the code as it is now — don't transcribe.** Before classifying, confirm the criterion still describes the mechanism the current code uses. A criterion naming a mechanism a later change replaced (a trace-based membership test after the code moved to a git diff) must be re-derived, not copied. The task doc is a *source*, not a constraint: an "authoritative AC" that has drifted from the design it depends on will ship the very bypass a successor task exists to remove.
- **For a change to the gate's own safety logic, run an adversarial pass on top of the completeness review.** Every loop now answers the completeness check before it can close, and that is the general case. This is the *extra* one: when a loop hardens the gate or engine itself, even a reviewer reading the design brief cannot pre-imagine every hole in code that changes the safety machinery. An independent pass that reads the diff and *tries to break each fix* (revert-on-scratch to prove the check has teeth; drive the real engine on the failing combinations) has repeatedly caught green-but-wrong fixes that a green suite passed. It belongs before the human gate, not after.

## The five check types

A check is a typed measurable predicate. Pick the type by what the criterion asserts (full catalog with worked descriptors in `references/check-types.md`):

| Type | Asserts | Command → signal | Owner / author | Baseline |
|---|---|---|---|---|
| **functional** | a behavior / AC holds | test runner → exit code or JSON count | maker `software-engineer` / author `qa-engineer` | no |
| **metric** | a number crosses a threshold (LOC, complexity, coverage, bundle) | `measure_metric.py` → number | author `code-reviewer` | yes (reduce/improve) |
| **performance** | a latency statistic under a bound (p95 < N ms) | `bench_runner.js` → statistic | author `code-reviewer` | yes |
| **static** | analyzer clean (Sonar issues == 0, lint clean) | `sonar_gate.py` → count | reuse | no |
| **evidence** | a non-automatable judgement (layout matches mockup, UX) | verdict artifact from an agent ≠ maker | verifier ≠ maker | no |
| **custom** | anything the five don't cover | same descriptor contract | `/fairmind-add-check` | optional |

## Workflow

0. **Write the design brief** — `${FAIRMIND_BASE}/design/<ref>.md`, before a single criterion is classified. Where each invariant lives and which precedent puts it there; which error class a refusal uses and why not the neighbouring one; what precedent this copies and where the analogy stops; the non-happy inputs; what is out of scope. Sized to the task — five lines when the questions genuinely have one answer each. The criteria are then derived from **its** decisions, not from the ticket's sentences, and the decisions the ticket never stated go to the human as decisions. Full contract, and the measured failure it exists to stop: `references/design-brief.md`.
1. **Classify** each acceptance criterion / objective into a type above (adaptive stop condition). A criterion that cannot be automated becomes `evidence`.
2. **Specify the descriptor** into `${FAIRMIND_BASE}/loop-state.json` — see `references/loop-state.md` for every field and `references/check-contract.md` for the descriptor contract. The Technical Lead writes the descriptor; it does **not** write the check implementation.
3. **Capture baselines** for reduce/improve goals with `capture_baseline.py` (runs on a clean committed ref via an isolated worktree; frozen for the run as target + regression guard).
4. **Author RED-first**, delegated to the checker-side (the QA Engineer for functional/evidence, the Code Reviewer for metric/performance, `sonar_gate.py` for static) via a **stateless dispatch** (`references/authoring-brief.md`): full descriptor inline, the four admission gates as acceptance criteria, exact output format, no scope expansion, INPUT GAP rule. The check must be red on the pre-fix code — admission re-runs it and records the proof; the author reports the failing value, never writes it into the descriptor. Re-authoring a quarantined check is a **fresh** dispatch quoting `quarantine[].gate_failed` verbatim, never a continuation.
5. **Admit** with `admit_check.py` — runs the mandatory gates (maker≠checker, clean-signal, live RED-first, determinism probe) and the recommended sensitivity control, then stamps `source.red_first_proof` + `source.admitted_hash` on each check it admits. Failures are quarantined and excluded from the stop condition. It **exits non-zero when nothing was admitted** (every check it evaluated quarantined, or zero checks considered) — that exit blocks arming; a partial quarantine exits 0. A check skipped as already admitted for its unchanged descriptor is not evaluated and counts for neither outcome, so a run that skips every check exits 0.
6. **Start gate** (Technical Lead): the loop is armed with the engine's verb, `run_gate_checks.py --arm` — never by hand-editing `status`. Arm only when every hard-gate criterion has a live, admitted check (or is evidence / quarantined → human) **and** the user confirmed the budget. `--arm` flips `status` to `running`, stamps `budget.spent.started_at`, and zeroes `confirmations`; it refuses a loop that is already `running` or that has no admitted check to gate on.

## How the gate evaluates (so you author checks it can read)

`run_gate_checks.py` per evaluation: runs each admitted check `determinism.runs` times (Tier A wrapped in `srt`), extracts the signal (`exit_code` / `file_json` / `stdout_json` / `stdout_regex`), applies the predicate and regression guards, reads evidence verdicts, and yields one verdict per check: `green` / `red` / `error` / `inconclusive`. A check that never cleared admission is evaluated as ERROR, so a forgotten check cannot be silently absent from the stop condition. All green → `confirmations += 1` (a confirmation turn spends no iteration budget — a green run must be able to reach K without being starved); anything not green → `confirmations` resets to 0 and the evaluation consumes one iteration of budget. Only all-green, K-times-consecutive closes the loop to `passed_pending_human`. See `references/loop-state.md` for the state machine and budget accounting.

## Scripts

Plugin scripts live under the plugin's `scripts/` directory. The plugin root is filled in by Claude Code wherever it appears in a command/SKILL.md/agent body, so `python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/<x>.py` already reaches the right script when you run it — invoke plugin scripts that way. A check descriptor's `exec.command`, though, is a JSON field in `loop-state.json` — a data file, never filled in the way this skill's own body is — so reference a plugin script there by a **literal absolute path** instead: copy it from any script invocation elsewhere in this skill or a command body, where Claude Code will already have filled it in for you.

- `run_gate_checks.py` — the executed gate (called by the Stop hook; also `--dry-run` for inspection).
- `admit_check.py` — verify-the-verifier admission; writes `admission` + `quarantine`.
- `measure_metric.py` — metric measurement (loc / file_size / json_number / command).
- `bench_runner.js` — performance percentile harness (url / command).
- `sonar_gate.py` — static gate with strict fetch-error propagation (never a false clean).
- `capture_baseline.py` — freeze a baseline on a clean committed ref.

## Custom checks

The same descriptor contract plus a `custom` type. `/fairmind-add-check` interviews the scenario, emits a descriptor, and runs the admission self-test (the real value: it **verifies the verifier**). It composes `fairmind-tdd` rather than re-teaching test craft; there is no `--force` trust-by-assertion path.
