# Classification map & gap report — schema reference

`loop_import.py` consumes a **classification map** (the output of the `task-compilation`
skill's judgment) and produces either a **gap report** (`--gap-report`, read-only) or a
compiled **loop-mode contract** (`--emit`, see `fairmind-gate/references/check-contract.md`
for the emitted-contract shape). Both modes share one loader/validator
(`validate_classification` in `loop_import.py`), so the rules below hold identically for
either invocation.

## classification-map schema (INPUT)

```json
{
  "task_ref": "<string>",
  "scope": { "allowed_paths": ["<glob>", "..."] },
  "decisions": [
    { "id": "<AC id matching a TaskDraft acceptance_criteria id>",
      "disposition": "checked" | "evidence" | "unverifiable",
      "type": "functional|metric|performance|static|custom|evidence|null",
      "rewrite": "<string — REQUIRED & non-empty IFF disposition=='unverifiable'>",
      "reason": "<string — optional>",
      "descriptor": { "<a full check descriptor per check-contract.md — REQUIRED IFF "
                       "disposition in {checked,evidence}; its id present & unique>" } }
  ]
}
```

- `task_ref` — carried straight into the gap report's own `task_ref`; on `--emit` the
  target ref instead comes from the CLI's `--task-ref` (they are conventionally the same
  string, but `loop_import.py` never cross-checks them — `--task-ref` is authoritative for
  everything `--emit` writes).
- `scope` — **optional**. When present, passed through verbatim into `contract.scope` on
  `--emit` (see `check-contract.md` / `loop-state.md`'s `contract.scope` section for what the
  gate does with it). Omitted entirely on `--gap-report`'s output (the gap report has no
  `contract`) and omitted from `contract` on `--emit` when absent from the map — never
  written as `null`.
- `decisions` — **exactly one entry per TaskDraft `acceptance_criteria[]` id, no more, no
  less.** The decision id-set must equal the draft's AC id-set exactly: a missing id, an
  extra id naming an AC the draft doesn't have, or a duplicated id are all fail-closed
  errors on both `--gap-report` and `--emit` — this is the no-silent-drops guarantee,
  enforced mechanically by `loop_import.py`, not trusted from the classifier's judgment.

### Per-decision rules (enforced by `validate_classification`)

| `disposition` | `type` | `rewrite` | `descriptor` |
|---|---|---|---|
| `checked` | one of `functional`/`metric`/`performance`/`static`/`custom` | must be absent | **required**; `descriptor.id` non-empty and unique across the whole map |
| `evidence` | must be `"evidence"` | must be absent | **required**; `descriptor.id` non-empty and unique across the whole map |
| `unverifiable` | must be `null`/absent | **required**, non-empty string | must be absent |

A violation of any cell above fails the whole map closed — `loop_import.py` prints one
named reason per violation to stderr and exits non-zero; it never emits a partial report or
a partial contract. `reason` is optional on every disposition (a human-readable note on why
the classifier chose it — carried through into the gap report, not otherwise validated).

## gap-report schema (OUTPUT of `--gap-report`)

```json
{
  "task_ref": "<string>",
  "coverage": 0.5,
  "criteria": [
    { "id": "AC1", "text": "<from the TaskDraft, verbatim>",
      "disposition": "checked:functional",
      "type": "functional",
      "rewrite": null,
      "reason": "<string | null>" },
    { "id": "AC4", "text": "<from the TaskDraft, verbatim>",
      "disposition": "unverifiable",
      "type": null,
      "rewrite": "<a concrete, machine-checkable restatement>",
      "reason": "<string | null>" }
  ],
  "counts": { "total": 4, "machine_checked": 2, "evidence": 1, "unverifiable": 1 }
}
```

- `criteria` follows the **draft's own acceptance_criteria order** (not the classification
  map's decision order), and `text` is copied verbatim from the draft — the gap report is a
  read-only compile, it never edits or reflows the criterion's wording.
- `disposition` is `"checked:<type>"` for **both** `checked` and `evidence` decisions — e.g.
  `"checked:functional"`, `"checked:evidence"` — or `"unverifiable"`. The report's own
  vocabulary distinguishes coverage *kind* by the `type` field, not by a second disposition
  prefix. (`--emit`'s `contract.criteria[]` uses a different, `evidence:<descriptor-id>`
  disposition shape for a different job — naming the covering check, not the coverage kind —
  see `check-contract.md`.)
- `rewrite` is non-empty **iff** the criterion is unverifiable; `null` otherwise.

### Coverage — the definition, precisely

```
coverage == counts.machine_checked / counts.total     (exact float; 0.0 when total == 0)
```

- `counts.total` — always `len(criteria)`, one per draft AC.
- `counts.machine_checked` — decisions whose `type` is one of the five *machine* types
  (`functional`/`metric`/`performance`/`static`/`custom`). **This is the coverage
  numerator.**
- `counts.evidence` — decisions with `type == "evidence"`. **Outside the coverage
  numerator** — an evidence check is still *covered* (settled by a verifier's verdict at
  gate time), just not by a machine predicate, so it does not count toward `coverage`.
- `counts.unverifiable` — decisions with `disposition == "unverifiable"`. Outside the
  coverage numerator and a genuine gap: nothing gates this criterion until it is rewritten
  into something checkable and re-classified.
- The three counts always sum to `total`; `machine_checked + evidence + unverifiable ==
  total` is an invariant the gap-report tests assert as a VALUE, not merely a present key.

**Reading the number.** `coverage` alone conflates "genuinely can't be automated" with
"nobody has written the check yet" — a low coverage with a high `evidence` count is a
different situation (deliberately human-judged criteria) than a low coverage with a high
`unverifiable` count (criteria still needing a rewrite + re-classification pass). Read
`counts` alongside `coverage`, never the ratio alone.
