---
name: brain-rebuild-requirements
description: Use when the requirements of a system that already exists have to be recovered rather than written - rebuilding a legacy service on a modern stack, taking over an undocumented codebase, or answering "what is this module actually supposed to do, and why". Reads the ingested code and the company brain module by module, derives functional and technical requirement drafts each carrying at least one code anchor, reports what is uncovered, and records the drafts as proposals for a human to confirm. Also use when asked to "reconstruct the requirements", "document what this service does", or "get the spec out of the code".
---

# Rebuild requirements from what exists

## Overview

A system that runs is a system whose requirements were written down once, partly, somewhere
— or never. This skill reconstructs them: it walks the repository module by module, pulls
the **why** the company brain already holds for each one (requirements, tasks, issues,
decisions, including the ones that were retired and the reason they were), derives
functional and technical requirement **drafts** with evidence attached, and records them
back as proposals.

Two properties make the output usable rather than plausible:

- **Every draft carries evidence.** At least one code anchor — the file, and where it is
  known the symbol — and, where the brain already holds something behind it, the node id of
  that record. A draft with no anchor is reported as unanchored, not shipped as if it were
  grounded.
- **Nothing is confirmed by this skill.** Everything it writes lands as `proposed`. A human
  confirms in the review queue. That is not a policy this skill can relax.

**Announce at start:** "I'm using the brain-rebuild-requirements skill to reconstruct the
requirements of this repository from the code and the brain."

**Dependencies:** requires a connected Fairmind workspace — see `fairmind-context` for the
resolution chain this skill reuses in Step 0. The step-by-step detail, the module heuristics
and the exact call shapes live in `references/playbook.md`; the report layout in
`references/report-template.md`.

**What leaves your machine when this skill writes.**
`mcp__Fairmind__Brain_record_requirement` sends the requirement title and text as drafted,
its anchors (repo-relative **file paths**, symbol names, line hints, and the **repository's
catalog id**), the node ids cited as precedents, and an agent label — to the Fairmind
platform your project's MCP entry points at. It confirms nothing and changes no existing
record. This is the **brain write-back**, and the plugin README's *What leaves your machine*
describes it in full under that name — alongside every other route by which anything leaves.

## When to Use

Use this skill when:

- A system is to be rebuilt, replatformed or replaced and its requirements have to exist
  first
- A team inherits a codebase whose specification was never written, or drifted away from it
- Someone asks what a module is *for*, not what it does line by line
- Requirement coverage is the question: which parts of this repository nothing explains

Do **not** use it to write a *new* requirement from an idea — that is
`brain-new-requirement`.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the
   `fairmind` field set to `"configured"`, plus the identity keys `project_id` and
   `repository_id` that `/fairmind-connect` writes. **Never write those keys yourself**, and
   never set `fairmind` to `"configured"` on their behalf — an unverified binding files this
   repository's work under a different repository.
2. If the file is missing, `fairmind` is `"none"`, or `repository_id` is absent: **stop**
   and tell the user to run `/fairmind-connect` once for this checkout. Unlike most of the
   plugin, standalone is not a degraded mode here — with no brain there is nothing to read
   the *why* from, and a "reconstruction" from code alone is a summary of the code, not
   requirements. (`mcp__Fairmind__Insights_bind_repository` exists as a platform door; this
   skill does not call it.)
3. Confirm the ten `mcp__Fairmind__Brain_*` tools are in the tool list. They arrive with the
   platform's **Brain-1** release; an older server answers `mcp__Fairmind__Studio_*` and
   `mcp__Fairmind__Insights_*` normally and exposes no `mcp__Fairmind__Brain_*` tool at all.
   If they are absent, say which release is needed and stop.
4. Ask the user for the scope if it is not obvious: whole repository, or a named subtree.

## Step 1: Bind and inventory

1. Take `project_id` and `repository_id` from `active-context.json` — the platform matches
   the repository by its catalog id, so pass that rather than this directory's name.
2. Inventory the code through the brain's view of it, not the local disk:
   `mcp__Fairmind__Code_tree` for the structure, `mcp__Fairmind__Code_search` for entry
   points and boundaries, `mcp__Fairmind__Code_analyze_repo` for the repository's maturity
   read (`output_format: "summary"`) when the user wants it framed.
3. Group files into **modules**. Prefer the repository's own boundaries — top-level source
   directories, packages, deployable services. When existing brain items already cluster on
   a directory under a name, reuse that name: module names are half of the natural key, and
   a renamed module produces duplicate drafts on the next run.
4. Print the module list and the file counts, and let the user correct it **before** any
   derivation. A wrong grouping is cheap to fix here and expensive to fix afterwards.

## Step 2: Collect the "why" for each module

Per module, in this order:

1. `mcp__Fairmind__Brain_context` with `directory: "<module path>"`, `granularity: "file"`,
   `detail_level: "minimal"`. It returns the items anchored under that directory, per-file
   counts, and — the part this skill is built around — **`uncovered[]`**, the files with
   nothing behind them. Make the call **twice**, and never pool the two answers:

   | pass | `include_proposed` | what it is for |
   |---|---|---|
   | evidence | `true` | everything anchored there, **drafts included** — the widest view, for deriving |
   | coverage | `false` | only what a human has confirmed — this is the `uncovered[]` the report quotes |

   **The coverage numbers come from the second call.** On a first run the two agree. On a
   rerun they do not, and the reason is this skill: run 1's own proposals are anchored under
   that directory now, so the `include_proposed: true` view shows a module as explained when
   the only thing explaining it is what this skill said last time. Coverage measured that way
   improves because the skill wrote to itself.

   Write into the report **which pass produced the coverage figures**. And report the two
   `uncovered[]` counts side by side. On a run where the module holds proposals — any rerun of
   this skill — two *identical* counts mean the filter did not reach `uncovered[]`, so even
   the coverage pass is counting drafts; say so rather than quoting the number as if it were
   human knowledge. (On a first run the two agree trivially, because there are no proposals
   yet, and that tells you nothing.) The platform contract does not settle which behaviour to
   expect, which is exactly why both numbers are printed instead of one.
2. `mcp__Fairmind__Brain_search` for what the anchors cannot reach: the module name and its
   domain vocabulary, `k` small. Run it **twice** per phrasing: once unfiltered, and once for
   the removal states, `status: ["retired", "superseded"]` — `status` takes one value or a
   list. Their results carry the `reason` the record was retired. Behaviour that was removed deliberately
   must not be re-specified by accident — that is the single most expensive mistake this
   whole exercise can make.
3. Read the staleness envelope the context call returns (`repo.last_synced_at`,
   `sync_state`, `graph_available`, and the `resolution` tiers). A stale or ungraphed
   repository does not invalidate the run; it changes what the report may claim, so carry
   those numbers into it.
4. Note the `resolution` counts: items reached `SEMANTIC` are the weakest tier and are
   labelled as such in the report, never mixed in with anchored evidence.
5. **Label every item that comes back with its `review_state`** — `proposed`, `confirmed` or
   `rejected` — and keep that label attached wherever the item is used afterwards. It is a
   different axis from `status` (`status` is the record's *lifecycle*: live, retired,
   superseded; `review_state` is whether a **human has ever agreed with it**), and a different
   axis again from `anchor_confidence`. A `proposed` item is a draft nobody has confirmed —
   quite possibly one this very skill wrote on an earlier run.

**Token discipline.** `detail_level: "minimal"` on every context call, `max_items` at the
default. Open a record with `mcp__Fairmind__Brain_get` only when a draft actually depends on
its body, and use `mcp__Fairmind__Brain_expand` for neighbouring chunks rather than
re-fetching. `mcp__Fairmind__Brain_search` pages: a `next_cursor` that is not null is the
next page, fetched by sending the same call with `cursor` set to it, until it comes back null.
Follow it for at most five pages per search, and say in the report when a search stopped with
a cursor still there.

## Step 3: Derive requirements per module

For each module produce two lists:

- **Functional requirements** — behaviour visible to a user or another system, written in
  the platform's requirement vocabulary (need / user story / functional).
- **Technical requirements** — integrations, data, constraints, and non-functional
  properties (performance, security, availability) that the code demonstrably imposes.

Rules that hold for every derived item:

- **At least one code anchor**, and the anchor names a file that exists in the inventory.
  Where the symbol is known, name it (`symbol_type` is `FUNCTION`, `CLASS` or `FILE`).
- **Cite the brain node ids** behind the item when they exist — an existing requirement, the
  decision that constrains it, the issue that produced it.
- **A `proposed` precedent is cited with its state in the same line, or not at all.** The
  honest form is `precedent: {node_id} (proposed, not yet confirmed)`. This matters most on a
  rerun, when the brain holds this skill's own earlier drafts: a requirement grounded in an
  unconfirmed draft is a requirement grounded in a previous guess, and nothing in the node id
  says so. Prefer a confirmed precedent where one exists; where none does, say the grounding
  is unconfirmed rather than presenting a node id as evidence.
- **Contradictions are listed, never resolved.** When the brain says one thing and the code
  does another, write both, name both sources, and leave it for the human. Silently
  preferring the code turns a genuine decision into an invisible one.
- **State what is inferred.** A requirement no record supports and no anchor proves is an
  inference; mark it, and prefer an open question over a confident sentence.

## Step 4: Coverage report

Two coverage numbers, both taken from what the platform returned rather than recomputed:

1. **Uncovered modules and files** — the `uncovered[]` entries from the **coverage pass**
   (`include_proposed: false`), rolled up per module. These are the parts of the system
   nothing a human has confirmed explains. Name the pass in the report; a coverage figure
   whose `include_proposed` setting is not stated cannot be compared with the next run's.
2. **Unanchored requirements** — items the brain holds whose anchors resolve to nothing in
   this repository (the `unresolved[]` markers). They are requirements about code that
   moved, was renamed, or was deleted.

Do not build a second coverage engine beside the platform's. For documents, the platform's
own gap analysis is the source — cite it, do not re-derive it.

## Step 5: Write the drafts back

For every derived requirement, call `mcp__Fairmind__Brain_record_requirement` with:

| field | value |
|---|---|
| `project` | the bound `project_id` |
| `req_type` | `functional` or `technical` — this skill never derives a `need` or a `user_story` (see Step 3); for one of those, load `brain-new-requirement` instead |
| `title` / `text` | the requirement as drafted |
| `natural_key` | **`<module>:<slug of the title>`** — lowercase, non-alphanumerics to `-` |
| `anchors` | at least one; `file_path` plus `repo_ref` (the bound `repository_id`), `repo_ref_scheme: "code-ingestion"`, `symbol_name` / `symbol_type` / `start_line_hint` where known |
| `precedent_refs` | node ids of the brain records the draft rests on |
| `refines` | `[{"node_id": <node_id>}]` — the parent need or user story's `node_id`, when the item sits under one |
| `evidence` | short quotes or links that justify the wording |
| `agent` | the label identifying this run |

**`refines` takes the parent's `node_id`, never its natural key.** The platform stores a
transformed form of the natural key you send, so a raw natural-key reference silently fails
to resolve — the write reports success but the edge is never built. Every parent this skill
could refine already came back from a Step 2 read with its `node_id` attached; use that value,
and use the natural key only for the report's human-readable text.

**The natural key is what makes a rerun converge**, and it is supplied by the client — the
platform never synthesizes one. Same module, same title, same key, same node: a second run
updates its own drafts instead of minting duplicates. Derive the slug deterministically and
write the key into the report so the next run can reproduce it.

**Two hard rules, no exceptions:**

- **Never call `mcp__Fairmind__Brain_set_status` to confirm anything**, and never present a
  draft as confirmed. `review_state` is always `proposed`; a human confirms in the review
  queue.
- **When the response is `revision_proposed`**, the natural key hit a record a human already
  confirmed. The record was left untouched and a revision proposal was filed. Report the
  `proposal_id` and move on — do not retry, and do not route around it with a different key.

Then write the markdown report to **`docs/reports/brain-rebuild-<YYYY-MM-DD>.md`** in the
repository being rebuilt (see `references/report-template.md`). This is a file in the user's
own tree — say so when you create it, and let them decide whether to commit it.

## Step 6: Print the yield

Before anyone treats this as a finished spec, print what it actually produced and ask the
hand-check question. Counts are the honest measure of a synthesis; adjectives are not.

Two of those counts are about **trust**, and they are the ones that stop a second run reading
as progress it did not make:

- **Drafted** — what this run recorded. Every one of them is `proposed`.
- **Already confirmed by a human** — among the items the module context calls returned, how
  many carry `review_state: confirmed`. On a first run this is `0`; print the `0` rather than
  omitting the line, because a missing number reads as an unmeasured one.

Print both. "37 drafted, 0 confirmed" is the true state of a first run, and it is a different
sentence from "37 requirements recovered".

## Output Structure

```markdown
# Requirements reconstruction — {repository} ({date})

**Status: proposed — awaiting human review.** Nothing below is confirmed knowledge.

## Scope
- Repository: {repository_name} (catalog id {repository_id}), branch {repository_branch}
- Project: {project_name} ({project_id})
- Modules: {n} | Files inventoried: {n}
- Brain freshness: last synced {last_synced_at} | sync state {sync_state} | graph {available|unavailable}

## Per module
### {module}
**Functional**
- {FR title} — anchors: {path}:{symbol} | precedents: {node_id} ({confirmed|proposed, not yet confirmed}) | confidence: {anchored|semantic|inferred}

**Technical**
- {TR title} — anchors: {path} | precedents: {node_id} ({confirmed|proposed, not yet confirmed}) | confidence: {…}

**Contradictions (listed, not resolved)**
- brain says {…} ({node_id}) — code does {…} ({path}:{symbol})

**Retired or superseded, do not re-specify**
- {title} — {retired|superseded} because {reason} ({node_id})

## Coverage
- Uncovered files, coverage pass (include_proposed: false — human-confirmed knowledge only): {n} — {list or "see appendix"}
- Uncovered files, evidence pass (include_proposed: true — drafts included): {n} {on a rerun, "identical, so the coverage figure counts this run's own drafts"}
- Unanchored requirements (anchors resolve to nothing here): {n} — {list}
- Resolution tiers: extracted {n} | inferred {n} | ambiguous {n} | unresolved {n} | semantic {n}

## Yield
- Drafts recorded (all `proposed`): {n} (functional {n}, technical {n})
- Already confirmed by a human (`review_state: confirmed` among what the module context calls returned): {n} — 0 on a first run
- With ≥1 resolving code anchor: {n} / {n}
- With ≥1 brain precedent: {n} / {n} — of which unconfirmed proposals: {n}
- Revision proposals filed (key already confirmed by a human): {n}
- Not written (refused / failed): {n}
- Report written to: docs/reports/brain-rebuild-{date}.md

**Hand check:** would a reviewer accept this as the requirement of that module?
Confirm or reject each draft in the review queue — this skill confirms nothing.
```

## Next Steps

- **Confirm or reject** the drafts in the platform's review queue. Until a human does, they
  are proposals and nothing downstream should treat them as the specification.
- **For a new requirement built on top of what was found**, load `brain-new-requirement`.
- **For implementation** against the confirmed set, load `fairmind-context`, then
  `fairmind-tdd`.
- **Rerun after confirmation** to pick up the module boundaries the human corrected — the
  natural keys make the second pass converge rather than duplicate.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `repository_id`:** stop and point the
user at `/fairmind-connect`. Do not fall back to reading local files and calling the result
a reconstruction — say plainly that the brain is where the *why* lives.

**No `mcp__Fairmind__Brain_*` tools in the tool list:** the connected server predates the
**Brain-1** release. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report how many drafts were written, which module the run reached, and the
natural keys still pending, so the next run resumes there. Never retry in a loop — every
attempt is refused and the refusals are counted against the tenant.

**`{'retryable': True}`:** the quota service itself was unreachable. One retry is
reasonable; a second refusal is an outage — report it and stop.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote
the key name verbatim and stop; someone with platform access has to set it for this tenant.

**`revision_proposed`:** expected, not an error — see Step 5. Count it, name the
`proposal_id`, continue with the next draft.

**A module with no brain records at all:** that is a finding, not a failure. It belongs in
the uncovered section. Derive from code if the user asks, and mark every such item as
inferred.

**A repository whose sync state is stale:** proceed, and put the staleness figures at the
top of the report. A reconstruction from a stale graph is a reconstruction of an older
system.
