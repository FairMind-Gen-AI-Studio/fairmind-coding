---
name: custom-check-authoring
description: Use when a loop-mode acceptance criterion is not covered by the five built-in check types and you need a custom check - interviews the scenario, emits an open-contract descriptor, and runs the admission self-test that verifies the verifier before the check can gate
---

# Custom Check Authoring

## Overview

The five built-in check types (functional, metric, performance, static, evidence) cover most criteria. When one doesn't fit, a **custom** check uses the *same open descriptor contract* — so it is a first-class citizen, not a second-class escape hatch. This skill (and the `/fairmind-add-check` command) turns a scenario into an admitted descriptor.

**Announce at start:** "I'm using the custom-check-authoring skill to author a custom check."

The point of this skill is **not** to re-teach test craft — it composes `fairmind-tdd` for that. Its real value is the **admission self-test that verifies the verifier**: a check that asserts nothing (or that can never go red) is worse than no check, because it ships a false "proven". There is **no `--force` / trust-by-assertion path**.

## Steps

1. **Interview the scenario.** Capture, in the user's words:
   - What must be true for this to pass (the predicate, in plain language)?
   - What observable signal reflects it (a command's exit code, a JSON field, a metric)?
   - Who is the maker (fixes it) and who is the checker (authors it)? They must differ.
   - Should it touch the network? (default `forbidden`).

2. **Emit the descriptor** (`type: "custom"`) into `${FAIRMIND_BASE}/loop-state.json` following `fairmind-gate/references/check-contract.md`. Set `on_missing: "error"`, a concrete `predicate`, and `source.authored_by` ≠ `owner`. Reference any plugin script in `exec.command` by a **literal absolute path**, never a variable reference of any kind: `loop-state.json` is a data file, never filled in the way this skill's own body is, so a variable written into `exec.command` yields no signal there and the check is quarantined. Copy the absolute root from step 5 below — Claude Code will have already filled it in for you by the time you read that line.

3. **Prove RED-first — live.** Run the check against the current (pre-fix) code; it must fail. Admission re-runs it and settles this from its own probe: a check that is already GREEN is rejected, and `source.red_first_proof` (`{commit, red_value}`) is **written by `admit_check.py`**, not by you. Do not hand-author it; a recorded proof is validated, never trusted.

4. **Provide sensitivity controls (strongly recommended).** Supply `admission.controls.positive` (a state/command that makes it GREEN) and `admission.controls.negative` (one that makes it RED). This is the highest-value gate — pure logic, zero OS dependency — and it is what catches a check that can't actually distinguish pass from fail. See `references/admission-self-test.md`.

5. **Run the admission self-test:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/admit_check.py --state "${FAIRMIND_BASE}/loop-state.json" --id <check-id>
   ```
   Mandatory gates: maker≠checker, clean-signal, live RED-first, determinism probe. A failed mandatory gate → the check is quarantined (surfaced to the human) and never contributes to the stop condition. A non-zero exit means nothing was admitted in that run — including `--id` matching no check. Fix the descriptor and re-run until `admission.status == "passed"`.

6. **Register.** Once admitted, the check participates in the gate exactly like a built-in — same confirmation-gated stop, same budget accounting.

## Anti-patterns (the self-test exists to catch these)

- **Tautology:** predicate that is always true (e.g. `>= 0` on a count). Negative control will not go RED → quarantined.
- **Silent absence:** `on_missing` set to a passing literal → clean-signal gate fails.
- **Self-authored:** `authored_by == owner` → maker≠checker gate fails.
- **Flaky signal:** value varies across the determinism probe → quarantined as non-deterministic.
