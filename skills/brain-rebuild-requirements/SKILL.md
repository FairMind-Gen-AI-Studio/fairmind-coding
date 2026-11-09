---
name: brain-rebuild-requirements
description: Use when the requirements of a system that already exists have to be recovered rather than written - rebuilding a legacy service on a modern stack, taking over an undocumented codebase, or answering "what is this module actually supposed to do, and why". Reads the ingested code and asks the company brain about it module by module, derives functional and technical requirement drafts each naming at least one file or function, reports what the brain holds and what it covers, and records the drafts as proposals for a human to confirm. Also use when asked to "reconstruct the requirements", "document what this service does", or "get the spec out of the code".
---

# Rebuild requirements from what exists

## Overview

A system that runs is a system whose requirements were written down once, partly, somewhere
— or never. This skill reconstructs them: it walks the repository module by module, asks the
**why** the company brain already holds for each one (requirements, tasks, issues,
decisions, including the ones that were removed and the reason they were), derives
functional and technical requirement **drafts** with evidence attached, and records them
back as proposals.

Two properties make the output usable rather than plausible:

- **Every draft carries evidence.** At least one file — and where it is known the function —
  and, where the brain already holds something behind it, that record, cited by title and id.
  A draft with no file or function is reported as unanchored, not shipped as if it were
  grounded.
- **Nothing is confirmed by this skill.** Everything it writes lands as `proposed`. A human
  confirms in the review queue. That is not a policy this skill can relax.

**Announce at start:** "I'm using the brain-rebuild-requirements skill to reconstruct the
requirements of this repository from the code and the brain."

**Dependencies:** requires a connected Fairmind workspace — see `fairmind-context` for the
resolution chain this skill reuses in Step 0. The step-by-step detail, the module heuristics
and the exact call shapes live in `references/playbook.md`; the report layout in
`references/report-template.md`.

**What leaves your machine when this skill works.**
`mcp__Fairmind__Brain_tell` sends each requirement's title and text as drafted, the
repo-relative **file paths** and **function names** it is about, the title, link and summary
of a document it rests on, an agent label and this session's Claude Code session id — to the
Fairmind platform your project's MCP entry points at. It confirms nothing and changes no
existing record. `mcp__Fairmind__Brain_ask` sends one question per module, naming the module's
**directory path**, and a few questions in the words of the module's domain. This is the
**brain write-back**, and the plugin README's *What leaves your machine* describes it in full
under that name — alongside every other route by which anything leaves.

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
3. Confirm these tools are in the tool list, by name: `mcp__Fairmind__Brain_tell` and
   `mcp__Fairmind__Brain_ask`. A server that predates the platform release serving them
   answers `mcp__Fairmind__Studio_*` and `mcp__Fairmind__Insights_*` normally and exposes
   neither. **Without `Brain_tell`, say which release is needed and stop.** Without
   `Brain_ask`, go on and draft from the code, mark every item inferred, and say in the
   report "precedents not checked: the brain's ask tool is absent".
4. Ask the user for the scope if it is not obvious: whole repository, or a named subtree.

## Step 1: Bind and inventory

1. Take `project_id` and `repository_id` from `active-context.json` — the platform matches
   the repository by its catalog id, so pass that rather than this directory's name.
2. Inventory the code through the brain's view of it, not the local disk:
   `mcp__Fairmind__Code_tree` for the structure, `mcp__Fairmind__Code_search` for entry
   points and boundaries, `mcp__Fairmind__Code_analyze_repo` for the repository's maturity
   read (`output_format: "summary"`) when the user wants it framed.
3. Group files into **modules**. Prefer the repository's own boundaries — top-level source
   directories, packages, deployable services. When the brain's answers already cluster on a
   directory under a name, reuse that name: the module name is part of every draft's title,
   and a renamed module produces duplicate drafts on the next run.
4. Print the module list and the file counts, and let the user correct it **before** any
   derivation. A wrong grouping is cheap to fix here and expensive to fix afterwards.

## Step 2: Ask the brain for the "why" of each module

Every `mcp__Fairmind__Brain_ask` call carries a `question` in plain words, `project` (the
bound `project_id`) and a `question_type`. What comes back is a short list of
**considerations** — each a sentence the brain composed, with whether a person confirmed what
it rests on and the references behind it (cards, documents, files, tickets, pull requests) —
plus **further reading**: records found and not cited, and a `coverage` sentence saying how
much of the question the brain could answer.

Per module, in this order:

1. `question_type: "file"` with `subject: {"file": {"repository": <the bound repository_id>,
   "path": <the module directory, ending in "/">}}` and a `question` naming the module and
   asking what the brain holds about it and why. A path that ends in `/` asks about the whole
   directory. One such question per module.
2. `question_type: "precedent"` for what the module's directory cannot reach: the module name
   and its domain vocabulary. Behaviour that was removed deliberately must not be re-specified
   by accident — that is the single most expensive mistake this whole exercise can make. At
   most three `precedent` questions in a run; a module past them is named as "not asked".
3. **Cite each consideration with its state.** One whose `confirmed` is false is a draft
   nobody has confirmed — quite possibly one this very skill wrote on an earlier run — and is
   cited "(proposed, not yet confirmed)", never bare; an entry of `further_reading` is listed
   as "found, not cited". A consideration is said to cite knowledge recorded **at a file**
   only when it comes from a `file` question and one of its `file` references has the bound
   repository and that path. On any other question a `file` reference carries the catalog's
   own spelling of the repository: quote its path as a pointer and make no claim about where
   anything was recorded.
4. Keep the `coverage` sentence of each module's answer. It is the brain's own account of what
   it holds about that directory; it is quoted, never recomputed here, and never pooled with
   another module's.

How to read what `Brain_ask` answers, for this skill, which writes:

| Answer | What to do |
|---|---|
| `configuration_unset` or `disabled` (with a `key`) | say "precedents not checked: `<key>`", then draft and write |
| `configuration_invalid` (with a `key`) | the same, "misconfigured" |
| `retryable: True` (`retrieval_failed`, `catalog_unavailable`) | retry once, then "precedents not checked" |
| access denied | "precedents not checked", then write; `Brain_tell` decides whether this key may |
| `project_unresolved` | stop before the draft and route the person to `/fairmind-connect` |
| `invalid_parameter` (with a `parameter`) | fix that parameter once and ask again, then "precedents not checked" |
| `composed: false` | say "listed, not composed", with the `reason`; for `nothing_found`, quote the `coverage` |
| `throttled` | stop the reading, never retry, and write |

After an answer with `reason: cap_reached`, send no further `Brain_ask` in this run; the
modules left are named as "not asked".

## Step 3: Derive requirements per module

For each module produce two lists:

- **Functional requirements** — behaviour visible to a user or another system.
- **Technical requirements** — integrations, data, constraints, and non-functional
  properties (performance, security, availability) that the code demonstrably imposes.

Rules that hold for every derived item:

- **At least one file or function**, and it names a file that exists in the inventory. Where
  the symbol is known, name it.
- **Cite the records behind the item** when they exist — an existing requirement, the
  decision that constrains it, the issue that produced it — by title and id, from the
  references of Step 2.
- **A `proposed` precedent is cited with its state in the same line, or not at all.** The
  honest form is `precedent: {title} ({id}) (proposed, not yet confirmed)`. This matters most
  on a rerun, when the brain holds this skill's own earlier drafts: a requirement grounded in
  an unconfirmed draft is a requirement grounded in a previous guess, and nothing in the id
  says so. Prefer a confirmed precedent where one exists; where none does, say the grounding
  is unconfirmed rather than presenting an id as evidence.
- **Contradictions are listed, never resolved.** When the brain says one thing and the code
  does another, write both, name both sources, and leave it for the human. Silently
  preferring the code turns a genuine decision into an invisible one.
- **State what is inferred.** A requirement no record supports and no file proves is an
  inference; mark it, and prefer an open question over a confident sentence.

## Step 4: Report what the brain holds and covers

The report states, per module and as **conclusions with references**, what the brain holds
about that code: the considerations that cite it, each with its confirmed or proposed state
and the cards, files and documents behind it. The brain's answer is a set of conclusions with
references, not a graph and not a file inventory, and the report is no more than that.

Two things come from the answers and are not recomputed:

1. **The module's `coverage` sentence**, verbatim. A module whose answer was `composed:
   false` is listed as "listed, not composed" with its `reason`, and the records the answer
   listed are named as found, not cited.
2. **Unanchored requirements** the brain holds, as far as the answers show: a consideration
   about a path that is not in this repository's inventory. They are requirements about code
   that moved, was renamed, or was deleted.

Do not build a second coverage engine beside the platform's. For documents, the platform's
own gap analysis is the source — cite it, do not re-derive it.

## Step 5: Write the drafts back

For every derived requirement, call `mcp__Fairmind__Brain_tell` with `intent: "need"` and:

| field | value |
|---|---|
| `repository` | the bound `repository_id` |
| `project` | the bound `project_id` |
| `title` | **`<module> · <the requirement>`**, on one line, short. The brain identifies a record by its title: the same module and wording find their own draft on a rerun, and a renamed module is a new record |
| `summary` | whether it is functional or technical, the requirement as drafted, its confidence (anchored to code, or inferred) and the precedents it rests on with their states. At most 1000 characters; the door refuses a longer text and never cuts it, so a requirement that needs more is two |
| `files` / `functions` | at least one of them: repo-relative paths, and `"<path>::<name>"` per symbol |
| `documents` | `[{"url": ..., "title": ..., "summary": ...}]` for a document it rests on and has a link — at most ten related items in all |
| `session_ref` | `"${CLAUDE_SESSION_ID}"` |
| `agent` | `brain-rebuild-requirements` |

This skill never derives a need or a user story (see Step 3); for one of those, load
`brain-new-requirement` instead. It sends no requirement type, key, parent reference, anchor
or precedent list: the brain decides what kind of record each item is and links it to code
and to what it rests on when it places it, and a report that carries a field it does not take
is refused.

**Read what comes back.** Report the `state` as the answer gives it, never as you expect it,
with the `node_id`:

- `proposed` — recorded, and waiting for a person in the review queue.
- `duplicate` — the same requirement was already there and **nothing changed**. Report it that
  way, never as written.
- `conflict` — a record with this title exists and says something different. **Nothing was
  overwritten.** Show the person the difference; on a yes the draft is sent again, whole, with
  `revises` set to the `node_id` the answer returned.
- With `documents`, each `related` entry has its own `state`: `created`, `updated`,
  `unchanged` or `proposed` is linked; `unavailable`, `retry` and `over_cap` are not — name
  the document and its state.

A report the brain **refused** (`{'status': 'refused', 'reason': ..., 'message': ...}`) has
words the brain did not accept — an empty title, a title or text over its cap. Fix the draft
and send it once more; a draft refused twice is reported and skipped. "No such document" on a
`documents` entry means the id is not one this key can use: send the report once more without
that document and say so in the report. A refusal that names a field of the report's **shape**
is different: say "the plugin and the brain
disagree on the report's shape — update the plugin", and do not send it again.

**Two hard rules, no exceptions:**

- **Never send a status, a `retract` or a `same_as` to confirm anything**, and never present
  a draft as confirmed. The recorded `review_state` is always `proposed`; a human confirms in
  the review queue.
- **A partial write is a normal outcome and is reported as one:** "wrote 14 of 37, resume at
  *billing · Invoices are issued monthly*" is usable; "some drafts were recorded" is not.

Then write the markdown report to **`docs/reports/brain-rebuild-<YYYY-MM-DD>.md`** in the
repository being rebuilt (see `references/report-template.md`). This is a file in the user's
own tree — say so when you create it, and let them decide whether to commit it.

## Step 6: Print the yield

Before anyone treats this as a finished spec, print what it actually produced and ask the
hand-check question. Counts are the honest measure of a synthesis; adjectives are not.

Two of those counts are about **trust**, and they are the ones that stop a second run reading
as progress it did not make:

- **Drafted** — what this run recorded. Every one of them is `proposed`.
- **Already confirmed by a human** — among the considerations the module answers returned, how
  many are `confirmed`. On a first run this is `0`; print the `0` rather than omitting the
  line, because a missing number reads as an unmeasured one.

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
- Precedents: {checked | not checked: {why}} | Modules not asked: {list or none}

## Per module
### {module}
**What the brain holds about this code** — {coverage sentence, verbatim | listed, not composed: {reason}}
- {conclusion} ({confirmed | proposed, not yet confirmed}) — references: {title and id of each card, file, document}

**Functional**
- {FR title} — files: {path}:{symbol} | precedents: {title} ({id}) ({confirmed|proposed, not yet confirmed}) | confidence: {anchored|inferred}

**Technical**
- {TR title} — files: {path} | precedents: {title} ({id}) ({confirmed|proposed, not yet confirmed}) | confidence: {…}

**Contradictions (listed, not resolved)**
- brain says {…} ({id}) — code does {…} ({path}:{symbol})

**Removed or replaced, do not re-specify**
- {title} — {removed|replaced} because {reason, as recorded} ({id})

## Coverage
- Per module, the brain's own coverage sentence, above — never recomputed here.
- Unanchored requirements (about paths that resolve to nothing here): {n} — {list}

## Yield
- Drafts told (all `proposed`): {n} (functional {n}, technical {n})
- Already confirmed by a human (`confirmed` among what the module answers returned): {n} — 0 on a first run
- With ≥1 file or function: {n} / {n}
- With ≥1 brain precedent: {n} / {n} — of which unconfirmed proposals: {n}
- Answered `duplicate`: {n}; answered `conflict`: {n}
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
  same titles make the second pass find the same drafts rather than duplicate them.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `repository_id`:** stop and point the
user at `/fairmind-connect`. Do not fall back to reading local files and calling the result
a reconstruction — say plainly that the brain is where the *why* lives.

**No `mcp__Fairmind__Brain_tell` in the tool list:** the connected server predates the
platform release that serves it. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**An MCP error result on `Brain_tell`:** the brain may not offer `Brain_tell` yet, or the
plugin and the brain disagree on the report's shape. Say so and stop; sending it again
changes nothing.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report how many drafts were written, which module the run reached, and the
titles still pending, so the next run resumes there. Never retry in a loop — every attempt is
refused and the refusals are counted against the tenant.

**`{'retryable': True}`:** the write could not be completed right now — an erasure in
flight, or the store unreachable. One retry is reasonable; a second refusal is an outage —
report it and stop.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote
the key name verbatim and stop; someone with platform access has to set it for this tenant.

**An access refusal:** the key, or the person behind it, may not write to this project. Say
so and stop; retrying changes nothing.

**A module with no brain records at all:** that is a finding, not a failure. It belongs in
the report as a module the brain holds nothing about (`composed: false`, `nothing_found`).
Derive from code if the user asks, and mark every such item as inferred.

**A repository whose code graph is stale:** proceed, and say so at the top of the report. A
reconstruction from a stale graph is a reconstruction of an older system.
