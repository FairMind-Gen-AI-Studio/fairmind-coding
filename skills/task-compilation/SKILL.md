---
name: task-compilation
description: Turn a TaskDraft's acceptance criteria into a classification map for loop-mode intake - extract every criterion, classify each into a gate-check type or flag it unverifiable with a machine-checkable rewrite, resolving ambiguity by interview rather than guessing, then compile the map with loop_import.py
---

# Task Compilation

## Overview

Loop mode (`fairmind-gate`) needs a machine-checkable stop condition before it can arm. This
skill is the **judgment half** that gets it there from an external ticket: it turns a
`TaskDraft`'s prose acceptance criteria into a **classification map** — a structured decision
per criterion — that `scripts/loop_import.py` then compiles **deterministically** into a gap
report (`--gap-report`, read-only sanity check) or a full loop-mode contract (`--emit`,
`loop-state.json` + a reusable `.fairmind/contracts/<ref>.json` copy).

The split is deliberate and load-bearing: **this skill is judgment, `loop_import.py` is
mechanism.** Same classification map + same TaskDraft → `loop_import.py` always produces the
byte-identical gap report / contract; nothing in the compiler second-guesses the
classification. Everything a human should double-check — is this really unverifiable? is
`functional` the right type here? does the descriptor actually assert the criterion? — lives
in this skill, not in the deterministic script.

**Announce at start:** "I'm using the task-compilation skill to classify this task's
acceptance criteria."

Use this skill any time you have (or can produce) a `TaskDraft` — see
`references/taskdraft.md` — and need a `loop-state.json` contract for `/fairmind-loop`, or
just want a coverage read on how automatable a ticket's stated criteria are. See
`references/gap-report.md` for the full classification-map and gap-report schemas this skill
targets, and `fairmind-gate/references/check-contract.md` / `check-types.md` for the check
descriptor shape a `checked`/`evidence` decision's `descriptor` follows.

## Non-negotiable principles

- **Every criterion gets a decision — none silently dropped, none silently invented.**
  `loop_import.py` enforces this mechanically (the classification map's decision id-set must
  equal the draft's AC id-set exactly), but the discipline starts here: extract every
  distinct, testable claim from the ticket, and account for every one of them in the map.
- **Ambiguity is resolved by interview, never by silent inference.** When it is unclear
  which type a criterion belongs to, whether it is checkable at all, or what threshold it
  implies, stop and ask — see "Resolving ambiguity" below. A guessed classification that
  turns out wrong either ships a check that asserts the wrong thing (worse than no check) or
  wrongly writes off a checkable criterion as `unverifiable`.
- **`unverifiable` requires a real rewrite, not a shrug.** A criterion classified
  `unverifiable` must carry a concrete, machine-checkable restatement in `rewrite` — a
  sentence a checker could turn into a descriptor tomorrow. `"make it clean"` classified
  `unverifiable` with `rewrite: "clean up the code"` has restated nothing; `rewrite: "the
  diff touches only <path> and its regression test"` is checkable.
- **Assert the artifact, not the mention.** Same rule `fairmind-gate` states for check
  authoring, applied one step earlier: when drafting the `descriptor` for a `checked`/
  `evidence` decision, make sure its predicate would actually catch the criterion failing —
  not just that some string related to it appears. A rename criterion's descriptor asserts
  the renamed value is used correctly, not merely that the new name appears somewhere.
- **Classify the THRESHOLD the ticket states, not one you assume.** A vague criterion
  (`"the fix should be clean"`) is `unverifiable` as *written* even if you can imagine a
  stricter version — don't silently upgrade it to `checked` by inventing a threshold the
  ticket never stated; write the threshold into `rewrite` instead and let a human confirm it.
  ⚠️ **This rule governs thresholds and scope. It does NOT govern where in the code a rule
  lives, or which error class a refusal uses** — see the next principle, which is the one
  that decides those.
- **A criterion that names a LAYER is a design decision, not a transcription.** Where an
  invariant belongs and how a refusal is classified are settled against the codebase, in the
  design brief (`fairmind-gate/references/design-brief.md`), *before* any of this — never by
  copying the layer the ticket happens to mention. `fairmind-gate` states the same rule for
  check authoring and it applies here first: **the task doc is a *source*, not a constraint**;
  re-derive each criterion against the code as it is now.

  Measured 2026-08-24, the whole failure in one row. A card said *"the route accepts it and
  validates it"*. The compiled AC read *"POST /judge/{id}/outcome accepts a criterion_id only
  when it is among that verdict's citations"*, and its own recorded `reason` said the
  validation sits at the HTTP boundary *"per the card's own wording"*. Nothing pinned the
  store. Every check went green on the FIRST evaluation, `coverage` reported **1.0**, and the
  shipped code let any non-HTTP caller write an invalid row into an **append-only** log —
  the one kind of mistake that cannot be corrected afterwards. The compiler *saw* the layering
  question and deferred to prose instead of answering it.

  So: a layer named in a ticket is where the reporter noticed the problem. Ask who else
  reaches the same state, and whether any of them bypasses the rule as placed. Landing back
  on the ticket's layer is a fine answer — but say why, because agreeing after checking and
  transcribing without checking produce the same words and only one of them is reviewable.

## The five check types (+ unverifiable)

Same catalog `fairmind-gate` uses for check authoring — reuse it here for classification,
since the classification map's `type` field is exactly the descriptor's own `type`:

| Type | Asserts | Typical signal | Descriptor author (`source.authored_by`) |
|---|---|---|---|
| `functional` | a behavior/AC holds | test runner → exit code or JSON count | `qa-engineer` |
| `metric` | a number crosses a threshold | `measure_metric.py` → number | `code-reviewer` |
| `performance` | a latency statistic under a bound | `bench_runner.js` → statistic | `code-reviewer` |
| `static` | analyzer clean | `sonar_gate.py` → count | `code-reviewer` |
| `custom` | anything the five don't cover | same open descriptor contract | via `/fairmind-add-check` |
| `evidence` | a non-automatable judgement | verdict artifact from a verifier ≠ maker | `qa-engineer` |
| — `unverifiable` | genuinely not machine-checkable as written | — (no descriptor) | — |

`owner` on every descriptor is the maker (`software-engineer`) — see `check-contract.md` for
the full field-by-field contract and worked examples per type in `check-types.md`.

## Workflow

1. **Get a validated TaskDraft.** From a deterministic adapter (`loop_import.py --adapter
   gh|pasted|clickup`), or supplied directly — e.g. an LLM normalizing an MCP tracker's payload
   (Jira/Linear/…) straight to `TaskDraft`, which is the default for MCP sources with no shipped
   adapter (`references/adapters.md`). Either way, confirm it validates first:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --validate-draft --input draft.json
   ```

2. **Extract acceptance criteria, if the draft's `acceptance_criteria` is empty.** An
   adapter's job is fetch → normalize only (`references/taskdraft.md`); it never extracts.

   🔴 **An empty `acceptance_criteria` is DESIGN MODE, not a mechanical fallback.** With no
   criteria to classify, *this extraction becomes the design step* — and everything
   downstream is bounded by it for the rest of the run. So the design brief
   (`fairmind-gate/references/design-brief.md`) is written **first**, and the criteria are
   derived from its decisions rather than from the ticket's sentences. That is the whole
   difference between a contract that says what the work is and one that repeats what the
   reporter happened to look at.

   ⚠️ **Order is mechanical, not stylistic: the brief feeds the DRAFT's
   `acceptance_criteria`, before classification — never `contract.criteria[]` afterwards.**
   `loop_import.py` requires the classification map's decision id-set to equal the draft's AC
   id-set exactly, so a criterion added after the fact is refused, and the tempting repair —
   loosening that check — is the one thing holding up "none silently dropped, none silently
   invented".

   Then read `title` + `body` and pull out every distinct, testable claim as one entry:
   `{ "id": "AC<n>", "text": "<short criterion statement>", "span": "<quoted excerpt from body, or null>" }`.
   - One criterion per **distinct testable claim**, not per sentence or per bullet — a
     ticket often states the same criterion twice (a "steps to reproduce" and an "expected"
     section restating it); merge those into one AC rather than double-counting.
   - `span` should quote the ticket's own words when a claim traces to a specific passage;
     `null` is legitimate for a criterion synthesized from context that has no single
     quotable span (e.g. an implied "and don't break existing behavior").
   - If the draft **already** has a populated `acceptance_criteria`, use it as-is — do not
     re-extract or renumber; the ids in the classification map must match exactly.

3. **Classify each criterion.** For every AC, in order, decide one of three outcomes:
   - **`checked`** — pick the type from the table above and draft a full descriptor per
     `check-contract.md` (id, `kind`, `type`, `owner: "software-engineer"`,
     `source.authored_by`, `exec`, `signal`, `predicate`, `determinism`,
     `admission: {"status": "pending"}`). The predicate must encode the criterion's actual
     acceptance threshold — see "Assert the artifact, not the mention" above.
   - **`evidence`** — the criterion is a genuine judgement call (visual match, code-shape
     review, "does this feel right") that a human/verifier settles, not a predicate. Draft
     the `evidence`-kind descriptor (`source.evidence_hash`, `exec.verdict_file`,
     `signal.file` — see `check-types.md`'s evidence example) and name the verifier role in
     `reason` if it isn't obviously `qa-engineer`.
   - **`unverifiable`** — the criterion, as written, has no observable threshold at all
     (subjective adjectives with no metric behind them, a goal stated at the wrong altitude
     to test directly). Write the `rewrite` — see the non-negotiable principle above — and a
     `reason` naming what's missing (a threshold, a concrete artifact to inspect, a
     reproducible setup).

   Record every decision in the classification-map shape (`references/gap-report.md`):
   `{ "id", "disposition", "type", "rewrite"?, "reason"?, "descriptor"? }`.

4. **Resolving ambiguity — interview, don't guess.** When a criterion could plausibly go
   more than one way (checked vs. evidence, which of two thresholds, whether it's really
   unverifiable), stop and put a **structured decision brief** to the user rather than
   picking silently:
   - State the criterion (quote it).
   - Give **2–4 bounded options**, each with what it would concretely mean for the
     descriptor/rewrite (not open-ended "what do you think?").
   - State your **recommendation** and why, so the user is confirming or correcting a
     concrete default, not starting from a blank page.
   - Never proceed on that criterion until the user answers; never silently pick the
     recommendation as a fallback.

5. **Sanity-check coverage before compiling the contract.** Run the read-only compile:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --gap-report --draft draft.json --classification .fairmind/import/classification.json
   ```
   A non-zero exit names exactly which rule the map violates (id-set mismatch, missing
   `rewrite`, missing/mismatched `descriptor`) — fix the map and re-run; `loop_import.py`
   never emits a partial report. On success, read `coverage` **alongside** `counts` (a
   low `coverage` from a high `evidence` count is fine — deliberately human-judged criteria
   — a low `coverage` from a high `unverifiable` count means more classification work is
   needed before this task is loop-ready; see "Reading the number" in
   `references/gap-report.md`).

6. **Compile the contract.** Once the gap report looks right, emit the loop-mode contract:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --emit --draft draft.json --classification .fairmind/import/classification.json \
       --task-ref <ref> --state "${FAIRMIND_BASE}/loop-state.json" --contracts-dir .fairmind/contracts
   ```
   This writes `loop-state.json` (`status: "specified"`) with `checks[]` = every
   `checked`/`evidence` descriptor and `contract.criteria[]` covering every AC, plus a
   reusable `.fairmind/contracts/<ref>.json` contract copy and a persisted
   `.fairmind/contracts/<ref>.gap.json` gap report (byte-identical to what `--gap-report`
   prints to stdout, kept as a durable artifact instead of an ephemeral one — see
   `/loop-import` step 4, the intake's coaching moment), then runs admission automatically.
   `--emit` exits `0` on **mechanical** success regardless of admission verdicts — a
   partially-quarantined admission is a normal outcome, not a failure; read `quarantine[]`
   in the written `loop-state.json` and re-author any quarantined check (`admit_check.py`,
   named `gate_failed` reason) before arming. `--emit` never arms the loop — arming is
   `/fairmind-loop`'s own step (`run_gate_checks.py --arm`), which additionally refuses any
   HARD criterion left uncovered.

## Anti-patterns

- **Classifying by vibes instead of by the criterion's actual words.** If the ticket doesn't
  state a threshold, don't invent one silently and call it `checked` — either interview for
  the threshold or classify `unverifiable` with the threshold proposal living in `rewrite`
  for a human to confirm.
- **Padding `unverifiable.rewrite` with a restatement of the same vagueness.** `"the fix
  should be clean"` → `rewrite: "the fix should be clean and well-tested"` restates nothing;
  it must name an observable check (a path scope, a diff size, a specific regression test).
- **Descriptor mismatch.** A `checked` decision whose `descriptor.predicate` tests something
  adjacent to the criterion (e.g. "the function was called" instead of "the function
  returned the right value") passes `loop_import.py`'s structural validation — it has a
  descriptor and a matching `type` — but ships a check that asserts the mention, not the
  artifact. Re-read the criterion against the drafted predicate before moving on.
- **Silent re-classification after a rejection.** If admission quarantines a check or the
  human rejects a classification, re-derive the decision from the criterion again — don't
  patch the descriptor just enough to clear the specific gate that failed (that is
  the fixture/check equivalent of solving the test instead of the problem).
