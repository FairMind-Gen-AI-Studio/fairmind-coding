# Stateless authoring brief

Every checker-side authoring dispatch (Technical Lead → the QA Engineer / the Code Reviewer, or `/fairmind-add-check` → checker) must be **self-contained**. The authoring agent runs in a fresh Task with no memory of the loop conversation, so the dispatch carries everything it needs to author the check and prove it RED — nothing is assumed from prior context.

A dispatch that omits any section below is defective: the check will bounce at admission or, worse, be authored against a guessed contract.

## What every dispatch must carry

1. **The full descriptor JSON, inline.** The complete `checks[]` entry the Technical Lead specified — `id`, `type`, `owner`, `source.authored_by`, `exec`, `signal`, `predicate`, and (for reduce/improve) `baseline` + `regression_guard`. The authoring agent fills in the *implementation* the descriptor points at; it does not invent the contract. Any plugin script in `exec.command` is carried as a **literal absolute path**, never a variable: a descriptor is a JSON field in `loop-state.json`, never substituted the way a command/SKILL.md/agent body is, so a variable reference there (braced or not) produces no signal and is quarantined (`check-types.md`). Use the absolute root the dispatching command or SKILL.md's own `${CLAUDE_PLUGIN_ROOT}` reference already resolved to.

2. **The four admission gates, as numbered acceptance criteria** the authored check must clear (`admit_check.py` enforces them — see `check-contract.md`):
   1. **maker ≠ checker** — `source.authored_by` is present and ≠ `owner`.
   2. **clean signal** — `signal.on_missing` is `"error"` (or `"fail"`); the predicate operator is known. Absence is never a pass.
   3. **RED-first, live** — the check must be **actually RED on the current code** when `admit_check.py` runs it. The live probe is the only source of truth (`gate_red_first_live()` in `admit_check.py`): a check that is already GREEN is rejected — tautological, or the code already passes. `source.red_first_proof` is **not an authored field**: the engine writes it (`{commit, red_value}`) from the live probe on a passing admission. Do not pre-write it (a recorded value is validated, never trusted — one that satisfies the predicate is rejected outright).
   4. **determinism** — the same signal every run across `determinism.probe_k` probes.

3. **The exact OUTPUT FORMAT.** State precisely what the authoring agent returns:
   - the files to write and where (the test/measurement artifact, any evidence verdict file);
   - the **observed RED value and how it was obtained** — reported back in the dispatch's answer as evidence the check fails on current code, *not* hand-written into the descriptor: `admit_check.py` re-runs the check itself and records `source.red_first_proof` + `source.admitted_hash` when it admits it;
   - optional `admission.controls` (`positive` → GREEN, `negative` → RED) when change-sensitivity can be certified, each a mapping merged over the check's `exec`, e.g. `"controls": {"positive": {"command": "<makes it GREEN>"}, "negative": {"command": "<makes it RED>"}}`.

4. **No scope expansion.** Author exactly the one criterion in the descriptor. Do not add checks, relax the predicate, widen `exec`, or touch code under the maker's ownership. Ownership boundary: the checker writes the check, never the fix.

5. **INPUT GAP rule.** If any input the brief promised is missing, ambiguous, or contradictory — **say so and stop; do not guess.** A guessed contract that happens to pass admission is worse than a bounced dispatch, because it gates on the wrong thing. Report the gap back to the Technical Lead.

6. **Assert the value, not the mention.** When the criterion is about a name, a label, a count, or an emitted/rendered field, the check must assert the **value** behind it — the number the name promises, the content the field carries — never that the string merely appears. A grep proving the label is present is not an acceptable check: it stays green while the value behind the label is still wrong.

7. **Reach the asserted state by driving the real sequence.** Never pre-seed the engine's (or the system-under-test's) internal counters or state as a shortcut to the condition you assert on. A fixture that seeds an internal counter to reach a threshold in one step silently encodes the very behaviour under test — and it clears admission untouched (a seeded fixture is both live-RED and deterministic). Build the state by driving the genuine sequence the code would take.

8. **For a "never silently" criterion, cover every branch.** When the criterion is of the form *"degrades explicitly / fails loud / never silently"*, enumerate **every** early-return and exception-swallowing path in the surface under test and assert on each — not only the one degradation the descriptor happens to name. The branch the ticket forgot is exactly where the silent-false result the criterion exists to prevent will survive.

9. **A fixture standing in for a real artifact must carry that artifact's real format** (`contract.mutation_set.fixture_fidelity`). When the check feeds the code a stand-in for something a real producer emits — a trace line, an API payload, an id, a config record — take the shape **from the producer** and quote the `file:line` it came from in the dispatch's answer. Two ways this goes wrong, both invisible to admission (a fabricated fixture is live-RED *and* deterministic, so it clears every gate):
   - **Invented shape.** A hand-built fixture in a form the producer never emits makes the assertion prove nothing about the artifact — it passes against a fiction while the real input still fails. Ids are the common case: synthetic `id1`/`p1` placeholders where the real ids are hyphenated slugs the code interpolates into a query or an attribute name.
   - **Circular fixture.** A fixture derived from the *expected output* (`FIXTURE = _expected_result()`) can never catch the producer drifting from it — the check asserts the test agrees with itself.

   Because the checker often cannot reach the producing repo, the **dispatch carries a real sample payload inline** whenever a check stands in for a producer's artifact. If the real shape is not in the dispatch and the producer is out of reach, that is an **INPUT GAP** (item 5) — report it and stop; do not invent a shape.

## Re-authoring a quarantined check = a brand-new dispatch

A check that failed admission sits in `quarantine[]` with `{ id, reason, gate_failed }`. Fixing it is **never a continuation** of the prior authoring turn — always a fresh, stateless Task dispatch that:

- quotes `quarantine[].gate_failed` and `reason` **verbatim** as the defect to fix;
- carries the full brief again (all the numbered items above);
- re-proves RED-first live and re-runs `admit_check.py` from scratch.

Re-dispatching fresh (rather than continuing) is what keeps the redispatch honest: the second attempt is judged by admission on its own evidence, not on the momentum of the first.
