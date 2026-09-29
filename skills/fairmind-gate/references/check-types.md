# Check types — worked descriptors

One worked descriptor per type. All obey the `check-contract.md` rules.

Two rules every `exec.command` below follows:

- **Repo-relative paths for the project's own files.** The gate and admission both execute the command with `cwd` = the repo root (`run_command()` in `run_gate_checks.py`).
- **An absolute path for the plugin's scripts — never any variable form, braced or not.** The descriptor's command is executed with the *inherited* environment (`env = dict(os.environ)` in `run_command()`), and `CLAUDE_PLUGIN_ROOT` is not set as a real environment variable in the shell where admission runs (`admit_check.py` is invoked from the command body, not from a hook — see `commands/fairmind-loop.md`). A descriptor is a JSON field in `loop-state.json`, never substituted the way a command/SKILL.md/agent body is, so a variable reference written there — braced or not — therefore expands to nothing (or stays literal), writes no result file, and is quarantined at admission with `missing signal … result file missing or not JSON` — even though the *same* descriptor evaluates fine at gate time, where the Stop hook does set the variable as a real environment variable. That asymmetry is what makes the trap silent, so paste a literal absolute path into the descriptor instead: copy it from the same `${CLAUDE_PLUGIN_ROOT}` invocation the dispatching skill or command already shows you, substituted.

  The descriptors below write that resolved path as `<plugin root>`; substitute the absolute path (the one shown above) before admitting. A placeholder left in place fails loudly (no such file); a variable reference fails quietly (it expands to nothing, or never expands at all) — that is the whole reason the placeholder is spelled this way.

`source` carries only `authored_by` at authoring time: `red_first_proof` and `admitted_hash` are **written by `admit_check.py`** on a live-RED admission (`gate_red_first_live()` / `_finalize()`), so the examples do not pre-write them. See `check-contract.md`.

## functional — a behavior / acceptance criterion holds

Test runner → JSON count → predicate. Author: the QA Engineer. Owner (maker): the Software Engineer.

```json
{
  "id": "story-142-guest-checkout-e2e", "kind": "machine", "type": "functional", "owner": "software-engineer",
  "source": { "authored_by": "qa-engineer" },
  "exec": { "command": "npx playwright test guest-checkout --reporter=json > pw.json",
            "timeout_s": 300, "network": "forbidden" },
  "signal": { "from": "file_json", "file": "pw.json", "selector": "$.stats.unexpected",
              "value_type": "count", "on_missing": "error" },
  "predicate": { "operator": "==", "value": 0 },
  "determinism": { "runs": 1, "probe_k": 3, "confirmation_k": 3 },
  "admission": { "status": "pending" }
}
```

Alternative signal for a runner that only sets an exit code: `"signal": { "from": "exit_code", "value_type": "count", "on_missing": "error" }`, `"predicate": { "operator": "==", "value": 0 }`.

> **`pytest -k` cannot discriminate while any sibling test file fails to import (PCF-2).** A common pattern is one `functional` check per criterion, each `pytest -k "<selector>"` on `exit_code` — the appeal is granular per-criterion feedback. But pytest **collects the whole suite before applying `-k`**, so a `ModuleNotFoundError`/`ImportError` in *any* selected-or-deselected sibling file errors the entire run (`exit 2`, "N errors during collection") regardless of the filter. Until every imported module exists, all such checks return the identical `exit 2` and are perfectly **correlated** — the per-criterion feedback the contract pays for does not exist, and correlated checks are exactly the shape that trips the `commitment_boundaries` heuristics (see the loop's H8/PCF-5 history). RED-first admission is unaffected (they are genuinely red, for a real reason). Prefer per-file selectors scoped so a sibling's import error cannot bleed in — e.g. point each check at its own file (`pytest tests/test_x.py`) or use `--deselect` on the noisy siblings — or accept that discrimination only begins once all imports resolve and failures become assertion failures (`exit 1`), not collection errors (`exit 2`). Note: a repo whose collection is expensive or crashes on a single file may still force a full-collection `-k` run — record the trade-off in the contract.

## metric — a number crosses a threshold (reduce/improve)

`measure_metric.py` → number vs threshold + baseline regression guard. Author: the Code Reviewer.

```json
{
  "id": "task-88-reduce-loc", "kind": "machine", "type": "metric", "owner": "software-engineer",
  "source": { "authored_by": "code-reviewer" },
  "exec": { "command": "python3 \"<plugin root>\"/scripts/measure_metric.py --kind loc --paths src --ext .ts .tsx --out loc.json",
            "timeout_s": 120, "network": "forbidden" },
  "signal": { "from": "file_json", "file": "loc.json", "selector": "$.value",
              "value_type": "count", "on_missing": "error" },
  "predicate": { "operator": "<=", "value": 1200 },
  "baseline": 1400,
  "regression_guard": [ { "operator": "<=", "value": "baseline" } ],
  "determinism": { "runs": 1, "probe_k": 3, "confirmation_k": 3 },
  "admission": { "status": "pending" }
}
```

Capture the baseline first: `capture_baseline.py --ref HEAD --command '<measure_metric.py … --out .b.json>' --measure-out .b.json --out baseline.json` (same rule: the command it runs carries the resolved absolute path).

## performance — a latency statistic under a bound

`bench_runner.js` → p95 → predicate. Author: the Code Reviewer. Give it a higher `timeout_s` and expect a wider budget.

```json
{
  "id": "api-health-p95", "kind": "machine", "type": "performance", "owner": "software-engineer",
  "source": { "authored_by": "code-reviewer" },
  "exec": { "command": "node \"<plugin root>\"/scripts/bench_runner.js --url http://localhost:3000/api/health --runs 20 --warmup 3 --percentile 95 --out perf.json",
            "timeout_s": 600, "network": "allowed" },
  "signal": { "from": "file_json", "file": "perf.json", "selector": "$.value",
              "value_type": "duration_ms", "on_missing": "error" },
  "predicate": { "operator": "<", "value": 200 },
  "baseline": 380,
  "determinism": { "runs": 1, "probe_k": 3, "confirmation_k": 3 },
  "admission": { "status": "pending" }
}
```

Performance signals are noisy: keep `runs: 1` in the gate (the harness already averages internally) and lean on `confirmation_k` for stability. Statistical rigor (bootstrap-CI, held-out seeds) is a hardening-backlog upgrade.

## static — analyzer clean

`sonar_gate.py` → issue count. Strict: a fetch fault writes `status:"error"` with **no** `total_issues`, so a network blip is ERROR, never a false clean.

```json
{
  "id": "sonar-pr-clean", "kind": "machine", "type": "static", "owner": "software-engineer",
  "source": { "authored_by": "code-reviewer" },
  "exec": { "command": "python3 \"<plugin root>\"/scripts/sonar_gate.py --out sonar.json",
            "timeout_s": 120, "network": "allowed" },
  "signal": { "from": "file_json", "file": "sonar.json", "selector": "$.total_issues",
              "value_type": "count", "on_missing": "error" },
  "predicate": { "operator": "==", "value": 0 },
  "determinism": { "runs": 1, "probe_k": 1, "confirmation_k": 3 },
  "admission": { "status": "pending" }
}
```

## evidence — a non-automatable judgement (verifier ≠ maker)

No machine predicate; an agent other than the maker inspects the artifact (screenshot/DOM) and writes a verdict file. The engine recomputes the AND and rejects a verdict authored by the maker.

```json
{
  "id": "checkout-layout-matches-mockup", "kind": "evidence", "type": "evidence", "owner": "software-engineer",
  "source": { "authored_by": "qa-engineer", "evidence_hash": "sha256:…required content anchor…" },
  "exec": { "verdict_file": "evidence/checkout-layout.json" },
  "signal": { "file": "evidence/checkout-layout.json" },
  "determinism": { "confirmation_k": 3 },
  "admission": { "status": "pending" }
}
```

The verdict file (written by the QA Engineer, not the Software Engineer):

```json
{ "verdict": "pass", "verifier": "qa-engineer", "evidence_hash": "sha256:…", "notes": "matches mockup at 1440px and 375px" }
```

`verdict` in `{pass, green, true}` → GREEN; anything else → RED. `verifier` is **required** and must differ from `owner` (else ERROR). `source.evidence_hash` is **required** (a content anchor — admission quarantines an evidence check without one) and must match the verdict file's `evidence_hash`, else the verdict is treated as stale (ERROR). Fuller evidence-freshness (DOM vs pixel anchoring) is a hardening-backlog item the human gate still owns.

## custom — anything the five don't cover

Use `/fairmind-add-check`. It interviews the scenario, emits a descriptor with `type: "custom"`, and runs `admit_check.py` so the custom check clears the same admission bar as a built-in. Optionally supply sensitivity controls to certify change-sensitivity:

```json
"admission": { "status": "pending",
  "controls": { "positive": { "command": "<makes the check GREEN>" },
                "negative": { "command": "<makes the check RED>" } } }
```
