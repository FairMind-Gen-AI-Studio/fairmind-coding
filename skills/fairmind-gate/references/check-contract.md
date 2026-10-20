# Check descriptor contract

Every check — built-in or custom — is one JSON object in `loop-state.json.checks`. The contract is **open**: any check whose descriptor satisfies these rules is a first-class citizen. This is what makes `custom` checks equal to built-ins.

## Required shape

```json
{
  "id": "<stable-unique-id>",
  "kind": "machine" | "evidence" | "guard",
  "type": "functional|metric|performance|static|evidence|custom",
  "owner": "<maker role that fixes a RED, e.g. software-engineer>",
  "source": {
    "authored_by": "<checker role, MUST differ from owner>"
  },
  "exec": { "command": "<shell string>" | "argv": ["..."],
            "timeout_s": <int>, "network": "forbidden|allowed" },
  "signal": { "from": "exit_code|file_json|stdout_json|stdout_regex",
              "file": "<path for file_json>", "selector": "$.a.b",
              "value_type": "count|number|duration_ms|bool",
              "on_missing": "error|fail" },
  "predicate": { "operator": "==|!=|<|<=|>|>=", "value": <target> },
  "determinism": { "runs": 1, "probe_k": 3, "confirmation_k": 3 },
  "admission": { "status": "pending" }
}
```

`signal.value_type` is a **closed set the admission gate enforces**: a gate signal is a *measurement*, so there is no string type and a check cannot be authored to extract one. A descriptor declaring anything else is quarantined by `admit_check.py`, and the engine refuses to coerce it rather than passing the raw value through — which is what used to put a check's own stdout on the insights wire as `results[].value`.

Optional: `baseline` (number, for reduce/improve), `regression_guard` (list of `{operator, value|"baseline"}`), `admission.controls` (positive/negative sensitivity fixtures, each a mapping merged over the check's `exec`, e.g. `"controls": {"positive": {"command": "<makes it GREEN>"}, "negative": {"command": "<makes it RED>"}}`). For `evidence`, `source.evidence_hash` is required instead of a predicate (see `check-types.md`).

**`kind` selects the admission path**, never the evaluation path (`run_gate` routes everything that is not `evidence` to `evaluate_check`):

- `machine` — an executed check, admitted RED-first (it must actually fail on the code as it stands).
- `evidence` — settled by a verdict artifact written by a non-maker; admitted with a freshness anchor, RED-first on the verdict.
- `guard` — a check that asserts "this existing behaviour still works", so it is **GREEN at specification time by construction**. It cannot pass RED-first, so admission replaces `red_first` with `green_at_spec` (it must be live GREEN now; an already-RED guard is quarantined naming that gate) and **promotes `sensitivity` from advisory to mandatory** (`admission.controls` must supply a `positive`→GREEN and a `negative`→RED control, and the negative must actually turn the signal RED). `source.red_first_proof` is not written for a guard. `kind: "guard"` is unrelated to the `regression_guard` **field** above — the field is the baseline-predicate list; the kind is the admission path.

  **Control-error convention (`gate_sensitivity()` in `admit_check.py`).** A `positive`/`negative` control command signals a harness/control error — as opposed to a genuine, clean predicate-failing RED — by exiting with the **reserved code 2**, typically paired with a `"CONTROL ERROR"` marker on stderr (the same convention `tests/fixtures/guard_gate_eval_control.sh`'s own `CONTROL ERROR` paths already use). `gate_sensitivity` inspects a control's raw exit code directly and fails CLOSED (`"failed"`, never `"passed"`) the instant it recognizes the exit code as a HARNESS crash, independent of whatever `eval_predicate` would have made of it — a crashed control proves nothing about the check's real sensitivity, and read through the predicate alone a crash is indistinguishable from a control that cleanly failed it.

  A control does not have to crash *voluntarily* (the authored `exit 2` convention) to be caught: `gate_sensitivity` (via `_control_crash_reason()`) also fails closed on **involuntary** crash shapes — a shell exec failure (**126** "found but not executable", **127** "command not found": the control's command never even started) or a **signal kill** (`returncode < 0`, how Python's `subprocess` reports a direct child killed by a signal on POSIX, e.g. -11 for SIGSEGV; or `returncode >= 128`, the alternate `128 + signal` convention some shells/wrappers use for a killed descendant). Any one of these — reserved 2, 126/127, or a signal kill — fails the gate closed with a reason naming which shape matched.

  A genuinely-RED negative control (a clean predicate failure, no crash) is unaffected and still certifies `"passed"` alongside a GREEN positive control. Author a custom control command with this in mind: exit 2 is reserved for "the control itself broke," never for "the check under test is legitimately red."

  **Residual limit.** None of this is a substitute for a control that crashes by exiting **exactly 1** — e.g. an uncaught exception under a wrapper that maps any exception to exit 1. That shape is indistinguishable from a clean, semantically-RED predicate failure by exit code alone (both simply fail a `== 0`-shaped predicate the same way), and no exit-code-only detection can close that gap; it would need a distinct signal from the control itself (a reserved marker/exit convention of its own).

**Written by the engine, not by the author.** `admit_check.py` fills two `source` fields on a passing admission and they are not part of what you specify:

- `source.red_first_proof` — `{commit, red_value}`, recorded from the **live** admission probe (`gate_red_first_live()` in `admit_check.py`). See rule 5.
- `source.admitted_hash` — the descriptor-integrity stamp (`_finalize()` in `admit_check.py`). See rule 7.

`exec.command` runs in the repo root under the *inherited* environment (`run_command()` in `run_gate_checks.py`); it is a JSON field, never substituted the way a command/SKILL.md/agent body is — write the plugin's scripts as a resolved absolute path (`<plugin root>/scripts/<x>.py`), never as any variable form (`check-types.md`).

## Contract rules the engine enforces

1. **maker ≠ checker.** Both `owner` and `source.authored_by` must be present and differ. A missing role or `authored_by == owner` → ERROR (never green). For `evidence`, the verdict's `verifier` is required and must differ from `owner`. Admission also rejects violations.
2. **Clean signal.** `signal.on_missing` must be `"error"` or `"fail"` — admission rejects any other value, including a literal that would be read as a passing signal (`gate_clean_signal()` in `admit_check.py`). If the signal cannot be located (missing file, absent selector, empty stdout, timeout, or a *stale* result file this run did not produce), the verdict is ERROR/RED — never green.
3. **Result freshness.** For `file_json`, the file is read only if it was written at/after this run started; a stale artifact is treated as a missing signal.
4. **Determinism.** With `determinism.runs > 1`, differing signals across runs ⇒ `inconclusive` (not green).
5. **Admission required, and RED-first is proven live.** Only `admission.status == "passed"` (and not quarantined) contributes to the stop condition. A missing/failed admission or a quarantined id is excluded and, if it is the only kind of check present, the loop stops `blocked_no_checks` for the human — it never silently passes.

   The RED-first gate is settled by the **live probe**, which is the sole source of truth (`gate_red_first_live()` in `admit_check.py`): the check must actually fail the predicate on the code as it stands when `admit_check.py` runs it. A live-GREEN check is rejected — tautological, or the code already passes — no matter what the descriptor claims.

   `source.red_first_proof` is therefore an **engine output, not an authored input**: when it is absent (the normal case — the check does not exist yet when the Technical Lead specifies the descriptor), a live-RED result admits the check and the engine writes `{commit, red_value}` from what it observed. Do not hand-author it. When one *is* present it is validated, never trusted: a recorded `red_value` that satisfies the predicate is rejected outright (a fabricated or stale "RED" proof cannot launder a check), and the live probe still has the final say.
6. **Confirmation floor.** `determinism.confirmation_k` is floored at 3 by the engine.
7. **Descriptor integrity.** Admission stamps `source.admitted_hash` over the contract fields. The engine recomputes it every evaluation; if the descriptor (predicate, exec, signal, …) changed since admission → ERROR. The maker cannot relax a check to force a pass. (Immutability of the external test artifact + a git-identity firewall are in the hardening backlog.)

## Emitted contract shape (`loop_import.py --emit`)

`scripts/loop_import.py --emit` compiles a `task-compilation`-skill classification map into a
fresh `loop-state.json` mechanically (no judgment — see `task-compilation/SKILL.md`). Its
`contract.criteria[]` entries use a **compact** disposition that names the covering
descriptor's `id` directly, one level more specific than the `loop-state.md` reference's
general `checked:<id>` / `evidence:<id>` / `quarantined:<id>` / `unverifiable` vocabulary:

| Classification decision | Emitted `contract.criteria[]` entry |
|---|---|
| `disposition: "checked"` | `{ id, text, "disposition": "checked:<descriptor.id>", "hard": true }` |
| `disposition: "evidence"` | `{ id, text, "disposition": "evidence:<descriptor.id>", "hard": true }` |
| `disposition: "unverifiable"` | `{ id, text, "disposition": "unverifiable", "hard": false }` |

Every `checked`/`evidence` decision's `descriptor` is copied verbatim into `checks[]`, so the
disposition's referenced id always exists there by construction — `--emit` refuses to write
a `checked`/`evidence` decision that has no `descriptor` at all (a hard criterion with
nothing covering it must never be silently emitted). `--emit` also writes a **reusable
copy** of the contract to `<contracts-dir>/<task-ref>.json` (default
`.fairmind/contracts`), whose parsed content deep-equals `loop-state.json["contract"]` — a
re-import round-trips. `--emit` runs admission on the written checks but never arms the loop
(`status` stays `"specified"`); a quarantined check from that admission pass still shows up
here with its `checked:`/`evidence:` disposition — quarantine is read from `quarantine[]`,
not from `contract.criteria[]`'s disposition string.

## Authoring checklist

- [ ] `id` stable and unique
- [ ] `owner` = the maker; `source.authored_by` = a different (checker) agent
- [ ] `exec` produces a signal deterministically; plugin scripts referenced by absolute path; `network: "forbidden"` when it should not touch the network
- [ ] `signal.on_missing: "error"`; selector points at the real signal
- [ ] `predicate` encodes the acceptance threshold
- [ ] the check is **actually RED right now** — admitted while the code still fails, before the maker fixes anything (the engine records `source.red_first_proof` itself; you do not write it)
- [ ] runs through `admit_check.py` with `admission.status == "passed"` (a non-zero exit means nothing was admitted — the loop cannot be armed on it)
