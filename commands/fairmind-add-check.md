---
description: Author a custom loop-mode check for a criterion the five built-in types do not cover - interviews the scenario, emits an open-contract descriptor, and runs the admission self-test that verifies the verifier before the check can gate
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/admit_check.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/run_gate_checks.py:*), Read, Write, Edit, Grep, Glob
---

# fairmind-add-check

Add a **custom** check to the active loop for an acceptance criterion the five built-in types (functional, metric, performance, static, evidence) don't cover. The custom check uses the *same open descriptor contract*, so it gates exactly like a built-in — after it clears admission.

## Usage

```bash
/fairmind-add-check
```

Requires an active loop (`${FAIRMIND_BASE}/loop-state.json`). If loop mode isn't set up yet, run `/fairmind-loop` first.

## What it does

Loads the **`custom-check-authoring`** skill and:

1. **Interviews the scenario** — the plain-language predicate, the observable signal, the maker (owner) and the checker (author, must differ), and whether it may touch the network.
2. **Emits the descriptor** (`type: "custom"`) into `loop-state.json`, following `fairmind-gate/references/check-contract.md` — `on_missing: "error"`, a concrete predicate, `source.authored_by ≠ owner`.
3. **Proves RED-first** against the current code and records `source.red_first_proof`.
4. **Adds sensitivity controls** (positive → GREEN, negative → RED) when possible — the highest-value gate.
5. **Runs the admission self-test:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/admit_check.py --state "${FAIRMIND_BASE}/loop-state.json" --id <check-id>
   ```
   Iterate on the descriptor until `admission.status == "passed"`. A failed mandatory gate quarantines the check (surfaced to the human) — there is **no `--force` bypass**.

## Guarantee

A custom check clears the same admission bar as a built-in: maker≠checker, clean-signal, RED-first, and a determinism probe — plus change-sensitivity when controls are supplied. A check that can't go red when it should never contributes to the stop condition.
