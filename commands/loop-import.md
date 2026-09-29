---
description: Turn an external ticket into a compiled loop-mode contract - detect the input form, classify acceptance criteria with task-compilation, compile+emit via loop_import.py, present the gap report, then hand off to /fairmind-loop to arm
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/run_gate_checks.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/admit_check.py:*), Bash(gh issue view:*), Read, Edit(.fairmind/**), Grep, Glob, Task
---

# loop-import

`/loop-import` is F3's **daily verb**: turn an external ticket — a gh issue, a pasted ticket, a ClickUp task payload, or another MCP source — into a compiled loop-mode contract, ready for `/fairmind-loop` to pick up. Run it whenever a new ticket needs to become loop-ready.

The split is the same one `task-compilation` documents: `scripts/loop_import.py` is the **deterministic** half — an adapter, a validator, a compiler — and never classifies anything itself. The **judgment** half — turning prose acceptance criteria into a classification map — is this command's job, following the `task-compilation` skill. `/loop-import` is the pipeline that wires the two together against a real ticket and puts the result in front of the human before anything gets armed.

## Usage

```bash
/loop-import 13906                 # gh issue number (a full issue URL also works)
/loop-import path/to/ticket.txt    # pasted/raw ticket text
/loop-import path/to/task.json     # a ClickUp task REST payload
/loop-import                       # no argument: ask the user to paste a ticket or name an issue
```

## What it does

Loads the **`task-compilation`** skill (the judgment half) and, at the arm handoff, the same **`fairmind-gate`** skill `/fairmind-loop` uses for check authoring.

1. **Detect the input form, route to an adapter.**
   - A gh issue number/URL → fetch, then normalize:
     ```bash
     gh issue view <n> --json number,url,title,body,labels,assignees,author,state,createdAt,updatedAt,milestone > issue.json
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --adapter gh --input issue.json > draft.json
     ```
   - A ClickUp task payload (a JSON file): obtained by the **user's own credentials/tooling** — this command never fetches it — from `GET https://api.clickup.com/api/v2/task/<id>?include_markdown_description=true`, then normalize:
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --adapter clickup --input task.json > draft.json
     ```
   - Pasted ticket text (no external identifier to reuse):
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --adapter pasted --input ticket.txt > draft.json
     ```
   - Detecting which form an argument is: a `.json` file whose top-level object has `id` + `name` + a description field (`markdown_description` or `description`) → clickup adapter (`url` is optional — the adapter carries it when present, and a permalink heuristic would misroute a URL-less payload to `pasted`); `number` + `title` → gh; anything else → pasted. A ClickUp URL or bare task id given as the argument → ask the user for the payload file instead of fetching it — this command must not fetch, since the plugin ships to customers and holds no tracker credentials. A card with **no description at all** (`description: null`, no markdown) maps to `body: null` and is refused by `--validate-draft` naming `body` — title-only cards need a description first, or the pasted form.
   - Other MCP-backed trackers (Linear, Jira, …) normalize via LLM-as-adapter per `skills/task-compilation/references/adapters.md` — same `TaskDraft` shape, a different `source.kind` (`mcp:<name>`).
   - Confirm the draft is well-formed before proceeding:
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --validate-draft --input draft.json
     ```

2. **Classify the acceptance criteria** — the judgment half, per the `task-compilation` skill. Classify each criterion into `checked:<type>` / `evidence` / `unverifiable`, writing a classification map (`.fairmind/import/classification.json`) per `skills/task-compilation/references/gap-report.md`. **Ambiguity is resolved by interview, never by silent inference** — when a criterion could plausibly go more than one way, stop and put a bounded decision brief (2–4 concrete options + a recommendation) to the user rather than guessing a type or a threshold.

   🔴 **A ticket with NO acceptance criteria is design mode, not a mechanical fallback.** With nothing to classify, the extraction *becomes* the design step — and `task-compilation` is deliberately barred from designing, so the layering and error-semantics decisions get made by transcription and nobody reviews them. Write the design brief first (`fairmind-gate/references/design-brief.md`, at `${FAIRMIND_BASE}/design/<ref>.md`), then derive the criteria from **its** decisions and write them into the **draft's** `acceptance_criteria` before classifying. Not afterwards into `contract.criteria[]`: `loop_import.py` requires the classification map's id-set to equal the draft's exactly, and loosening that check to fit a late edit would undo "none silently dropped, none silently invented".

   Measured 2026-08-24 on a card with no criteria: the extracted AC put an invariant at the HTTP boundary *"per the card's own wording"*, no criterion pinned the store, and the shipped code let any other caller write an invalid row into an append-only log — with `coverage` at 1.0.

3. **Compile + emit.** Once the map looks right:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/loop_import.py --emit \
     --draft draft.json --classification .fairmind/import/classification.json \
     --task-ref <ref> --state "${FAIRMIND_BASE}/loop-state.json" \
     --contracts-dir .fairmind/contracts
   ```
   This writes `loop-state.json` (`status: "specified"`) + the reusable `.fairmind/contracts/<ref>.json` contract copy + a persisted `.fairmind/contracts/<ref>.gap.json` gap report, and runs admission on every emitted check — quarantining any weak descriptor before the loop ever arms. `--emit` exits 0 on mechanical success regardless of admission verdicts; a partial quarantine is a normal outcome, surfaced in the next step, not a failure of this command.

   `${FAIRMIND_BASE}` needs a bootstrapped workspace to resolve into — if `.fairmind/active-context.json` doesn't exist yet, dispatch the **Technical Lead / Architect** (`Task`) to bootstrap it first, the same repoint step `/fairmind-loop` Phase 0 runs as its own first mutation.

4. **Present the gap report as a first-class artifact — this is the coaching moment, not an error dump.** Read the persisted `.fairmind/contracts/<ref>.gap.json` and show the human:
   - the **coverage** number **alongside `counts`** — a low coverage from a high `evidence` count is fine (deliberately human-judged criteria); a low coverage from a high `unverifiable` count means more classification work is still needed;
   - a readable **per-criterion breakdown**: each criterion's `id`, its disposition (`checked:<type>` / `checked:evidence` / `unverifiable`), and for every `unverifiable` entry its suggested `rewrite`;
   - any descriptor names in the emitted `loop-state.json`'s `quarantine[]`, with its reason;
   - **the design decisions the ticket never stated** — where each invariant was placed and why, which error class a refusal uses, where a copied precedent stops — presented *as decisions to confirm or correct*, not as background. ⚠️ **`coverage` cannot see any of them.** It measures criteria→checks, so a contract that pins an invariant at the wrong layer scores **1.0** and reads as loop-ready. That number is the reason this step needs the brief beside it.

   Never skip or shortcut this step to reach arming faster — it is the reason `/loop-import` exists as its own command instead of folding straight into `/fairmind-loop`.

5. **Only after the gap report has been shown, offer to continue toward arming.** `/loop-import` never arms a loop itself. Hand off to `/fairmind-loop <task-ref>` — its Phase 0 already knows to reuse a pre-compiled contract (`checks[]` + `contract.criteria[]` already populated) instead of classifying from scratch, and its Phase 0b still runs the RED-first checker-side authoring pass on any check not yet admitted, budget confirmation, and the arm-time smoke before `run_gate_checks.py --arm`. You may run the read-only sanity check first to set expectations:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/run_gate_checks.py --validate-contract
   ```
   **This will very likely fail right after `--emit`, and that is expected** — a freshly-compiled contract's checks have only cleared `admit_check.py`'s own admission pass, not the RED-first checker-side authoring `/fairmind-loop` Phase 0 still runs on the checker side. Do not promise a contract that just came out of `--emit` arms immediately; report what `--validate-contract` actually says and point the user at `/fairmind-loop` to finish the job.

## Guarantee

A ticket that comes through `/loop-import` never reaches `/fairmind-loop --arm` un-triaged: every acceptance criterion has a recorded disposition (none silently dropped, none silently invented — enforced mechanically by `loop_import.py`'s id-set cross-check), every `unverifiable` one carries a concrete rewrite, and the gap report — not a vibes-based "looks loop-ready" — is what the human reviews before a single check gets authored.
