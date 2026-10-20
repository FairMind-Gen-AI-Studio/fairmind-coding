# The admission self-test — verifying the verifier

A custom check is only as trustworthy as its ability to go **red when it should**. Admission is the gate that proves that *before* the check can ever contribute to closing the loop. `admit_check.py` runs it.

## Mandatory gates (portable, always run)

| Gate | Question | How it is checked | Fail → |
|---|---|---|---|
| **maker_checker** | Did a non-maker author it? | `owner` and `source.authored_by` both present and different | quarantine |
| **clean_signal** | Can absence ever pass? | `signal.on_missing ∈ {error, fail}` and a known `predicate.operator` | quarantine |
| **red_first** | Is it actually RED right now? | the check is **run live** and must fail its predicate; an already-green check cannot pass (tautology, or the code already passes) | quarantine |
| **determinism** | Is the signal stable? | run `probe_k` times; all signals identical | quarantine |

**`red_first_proof` is an output of this gate, not an input to it.** The live probe is the sole source of truth: when the descriptor carries no proof (the normal case — the check did not exist when it was specified), a live-RED result admits it and `admit_check.py` writes `source.red_first_proof = {commit, red_value}` from the value it just observed (`gate_red_first_live()`). Nobody pre-writes it. A proof that *is* present is validated, never trusted — a `red_value` that satisfies the predicate is rejected outright (a fabricated or stale "RED" cannot launder a check), and the live probe still decides.

On a pass, admission also stamps `source.admitted_hash` over the contract fields; the engine rejects any later descriptor change (a maker cannot relax the check to force a pass). Evidence checks are admitted on a parallel path: maker≠checker, a required `source.evidence_hash` anchor (an authored input — it anchors the verdict artifact), and a verdict that is not already GREEN.

A check that fails any mandatory gate is added to `loop-state.json.quarantine` with the `gate_failed` name and is **excluded from the stop condition**. It is surfaced to the human, never silently dropped.

## Recommended gate — change-sensitivity

The highest-value gate, and the one built-in types often can't run without fixtures: prove the check **moves** with the thing it measures.

```json
"admission": {
  "status": "pending",
  "controls": {
    "positive": { "command": "<put the system in a PASSING state, then run the check>" },
    "negative": { "command": "<put the system in a FAILING state, then run the check>" }
  }
}
```

`admit_check.py` runs the check under each control and requires: **positive → GREEN** and **negative → RED**. If the negative control does not go red, the check cannot distinguish pass from fail (a tautology) and is reported `sensitivity: failed`. Without controls it is reported `sensitivity: unverified` — honest, and a prompt to add them.

Sensitivity is *recommended, not mandatory* in v1 (it needs per-check fixtures, and mandating it would hurt adoption). But for a custom check it is the difference between a real check and a rubber stamp — provide the controls whenever you can.

## Re-run until green

```bash
python3 "<plugin root>"/scripts/admit_check.py --state "${FAIRMIND_BASE}/loop-state.json" --id <check-id>
```

`<plugin root>` is the plugin's absolute install root — the same absolute path the invoking `SKILL.md` or command shows once Claude Code substitutes its own `${CLAUDE_PLUGIN_ROOT}` references. Paste that literal path here: this file is read raw, never substituted the way a command/SKILL.md/agent body is, and the same applies inside a descriptor's `exec.command` — a check that reaches a plugin script through any variable form (braced or not) measures nothing there and is quarantined for a missing signal.

Inspect `checks[].admission.gates` and `quarantine[]`. Iterate on the descriptor — never bypass a gate — until `admission.status == "passed"`. A non-zero exit means the run admitted nothing at all (every considered check quarantined, or `--id` matched none) — the loop cannot be armed on it.

When authoring is delegated to a checker-side agent (rather than done inline), the dispatch must be **stateless and self-contained** — see `fairmind-gate/references/authoring-brief.md`. Re-authoring a quarantined check is a **fresh** Task dispatch quoting `quarantine[].gate_failed` verbatim, never a continuation of the failed attempt.
