# Adapters — bringing a new source into intake

An adapter's only job is **fetch → normalize**: get a ticket from its source and map it onto
the neutral `TaskDraft` (`references/taskdraft.md`). Everything downstream — AC extraction,
classification, `loop_import.py`'s `--gap-report`/`--emit` — reads only `TaskDraft` and never
learns where the ticket came from. That boundary is what keeps intake **independent of the
tracker**: the source washes out at `TaskDraft`, so supporting Jira / Linear / ClickUp / … never
touches the compiler.

There are **two ways** to do that normalization. Pick with the decision rule at the bottom;
most MCP-backed trackers want the first, and it ships **no per-tracker code**.

## 1. LLM-as-adapter — the default for any MCP tracker without a shipped adapter (no per-tracker code)

For a tracker reachable through an MCP connector without a shipped deterministic adapter (Jira,
Linear, …) you do **not** write a per-tracker adapter. The LLM is the adapter. (ClickUp now ships a
deterministic adapter — see §2; the route below is the template for the next tracker.)

1. **Fetch — in the command/orchestrator, not the LLM.** Call the tracker's own MCP tool for the
   task (e.g. `mcp__jira__…get_issue`, `mcp__linear__…get_issue`) and capture the raw payload.
   Keeping the fetch in the command (see `commands/loop-import.md`) leaves it non-hermetic and
   out of the deterministic core.
2. **Normalize — the LLM.** Read the raw payload and produce a `TaskDraft` directly:
   - `source.kind` = `"mcp:<name>"` (e.g. `"mcp:jira"`, `"mcp:clickup"`),
   - `source.ref` = the tracker's own task id, `source.url` = its link (or `null`),
   - `title` from the task's name / summary / title field,
   - `body` from its description / markdown, carried verbatim,
   - **everything else into `meta`** (labels, status, assignee, custom fields, …),
   - `acceptance_criteria: []` — extraction is the next stage's job (`SKILL.md` step 2), never
     the adapter's.
3. **Validate — the deterministic guard-rail (non-negotiable).**
   ```bash
   python3 "<plugin root>"/scripts/loop_import.py --validate-draft --input draft.json
   ```
   `validate_draft` already accepts `source.kind: "mcp:<name>"`, so a well-formed MCP draft
   passes; a malformed one (missing `source.kind`, empty `title`, non-string `body`, an AC with
   no `text`) exits non-zero and **names the offending field**. This is what makes LLM-as-adapter
   safe: the LLM *proposes* the mapping, `--validate-draft` *disposes* — a bad map is caught,
   never silently fed downstream, and the compiler stays source-agnostic. (`<plugin root>` is the
   plugin's absolute install root — the same absolute path the invoking `SKILL.md` or command
   shows once Claude Code substitutes its own `${CLAUDE_PLUGIN_ROOT}` references; this file is
   read raw and never substituted itself, same caveat as every other reference file, see
   `fairmind-gate/references/check-types.md`.)
4. **Hand off to task-compilation** (`SKILL.md`) for AC extraction + classification, exactly as
   for any other draft — from here on nothing knows or cares that the source was ClickUp.

**Adding a new MCP tracker = configure its MCP and import.** Nothing in the plugin changes. This
is deliberate: a per-tracker deterministic adapter would couple the core to a tracker just to do
the trivial `name→title` / `description→body` / `everything-else→meta` map the LLM does for free —
and the hard part (turning prose criteria into checkable classifications) is *already* the LLM
(`SKILL.md`), verified as `evidence`.

### What stays gate-checkable vs what is judgment (no hidden loss)

- **Deterministic / gate-checkable:** the `TaskDraft` *shape* (`--validate-draft`) and every
  compiler invariant after it (`--gap-report`/`--emit`: no AC silently dropped, `coverage` is the
  real ratio, admission actually ran).
- **Judgment / evidence:** whether the LLM mapped and extracted *faithfully* from the source.
  That was always judgment — AC classification is — so nothing that used to be machine-checkable
  becomes a guess. When fidelity matters, verify it as `evidence` (an agent ≠ the maker inspects
  the **real** payload against the produced draft), never as a per-tracker functional check —
  such a check could only re-assert that a field *appears*, the mention-check anti-pattern
  `SKILL.md` and `fairmind-gate` both forbid.

## 2. Deterministic adapter — an optional hermetic / zero-LLM path

Write a per-source adapter in `scripts/loop_import.py` (`--adapter <name>`) **only** when you
specifically want a **byte-reproducible, LLM-free** normalization — a CI/offline pipeline, or a
source whose mapping you want pinned by a regression test. Three adapters are shipped this way:
`--adapter gh` (a `gh issue view --json` payload), `--adapter pasted` (raw text), and
`--adapter clickup` (a ClickUp `GET /v2/task/{id}` public REST task payload, read from disk —
the fetch itself stays outside the adapter and outside the plugin: no ClickUp credentials ever
ship in `loop_import.py`). Their mechanics — pick a `source.kind`, implement the fetch/read, map
onto `title`/`body`/`meta`, leave `acceptance_criteria` `[]`, then `--validate-draft` — are in
`references/taskdraft.md` → "Adding a new adapter".

A deterministic adapter is an **optimization, not the price of supporting a tracker.** If you
don't need hermeticity or byte-reproducibility, use the LLM-as-adapter path (§1) and ship no code.

## Decision rule

| Situation | Use |
|---|---|
| A ClickUp task (REST payload on disk) | shipped `--adapter clickup` |
| MCP-backed tracker with no shipped adapter (Jira, Linear, …) | **LLM-as-adapter** (§1) — no code |
| Raw text a human pasted | shipped `--adapter pasted` (or LLM-as-adapter) |
| A GitHub issue | shipped `--adapter gh` (already hermetic) |
| You need a byte-reproducible / zero-LLM / CI normalization | **deterministic adapter** (§2) |

Either way the invariant holds: **the source's knowledge lives in the LLM plus the `TaskDraft`
schema guard-rail (`--validate-draft`), never in downstream code.** The `TaskDraft` boundary is
the seam; a source that flows through it with the compiler unchanged is the proof the seam is
real.
