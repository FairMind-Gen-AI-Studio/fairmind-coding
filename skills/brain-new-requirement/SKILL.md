---
name: brain-new-requirement
description: Use when a new feature, need or requirement is about to be written and the question "did we already do this, and what happened to it?" has not been answered - it asks the company brain for precedents, including the ones that were removed or replaced, quotes why when the brain says, sketches which modules the change would touch, then drafts the need, its user stories and the functional and technical requirements citing those precedents, recording them as proposals for a human to confirm. Also use when asked to "check if this existed before", "why was this removed", or "turn this idea into a spec".
---

# New requirement from what exists

## Overview

Most "new" requirements are not new. Something like them was specified before, built, and
then changed, retired or replaced — usually for a reason that still holds. This skill starts
from the idea and works outward through the company brain: precedents first, **what happened
to them** second, the modules they touched third, and only then a draft.

The order is the point. A feature removed on purpose, re-specified because nobody remembered
why it went away, is the most expensive requirement a team can write — it is paid for twice
and then removed a second time. So the retirement reasons come **before** the drafting,
never after.

Everything this skill writes lands as `proposed`. A human confirms.

**Announce at start:** "I'm using the brain-new-requirement skill to look for precedents in
the brain before drafting this requirement."

**Dependencies:** requires a connected Fairmind workspace — see `fairmind-context` for the
resolution chain this skill reuses in Step 0. Search phrasing, the report shapes and the
worked example live in `references/playbook.md`.

**What leaves your machine when this skill works.**
`mcp__Fairmind__Brain_tell` sends each drafted item's title and text, the repo-relative
**file paths** and **function names** it touches, the title, link and summary of a document
it rests on, the ticket reference and the status the person stated when there is one, an
agent label and this session's Claude Code session id — to the Fairmind platform your
project's MCP entry points at. It confirms nothing and changes no existing record.
`mcp__Fairmind__Brain_ask` sends the idea in the person's words. This is the **brain
write-back**, and the plugin README's *What leaves your machine* describes it in full under
that name — alongside every other route by which anything leaves.

## When to Use

Use this skill when:

- An idea, a feature request or a need has to become a specification
- Someone asks whether something already exists, existed once, or was deliberately dropped
- A requirement is being written and the decisions that constrain it are not all known
- A precedent's history matters: when it changed, why, and what replaced it

Do **not** use it to reconstruct the requirements of a whole existing system — that is
`brain-rebuild-requirements`.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the
   `fairmind` field set to `"configured"` and the `project_id` that `/fairmind-connect`
   writes. `repository_id` is optional — needed for `brain-impact` in Step 4, and, when
   present, the `repository` every item is filed under. **Never write those keys yourself**,
   and never set `fairmind` to `"configured"` on their behalf.
2. If the file is missing, `fairmind` is `"none"`, or `project_id` is absent: **stop** and
   tell the user to run `/fairmind-connect` once for this checkout. There is no standalone
   version of this skill — with no brain there are no precedents, and drafting without them
   is exactly the failure it exists to prevent. (`mcp__Fairmind__Insights_bind_repository`
   exists as a platform door; this skill does not call it.)
3. Confirm these tools are in the tool list, by name: `mcp__Fairmind__Brain_tell` and
   `mcp__Fairmind__Brain_ask`. A server that predates the platform release serving them
   answers `mcp__Fairmind__Studio_*` and `mcp__Fairmind__Insights_*` normally and exposes
   neither. **Without `Brain_tell`, say which release is needed and stop.** Without
   `Brain_ask`, go on and draft, and say in the report, and beside every item, "precedents
   not checked: the brain's ask tool is absent".

## Step 1: Take the input

1. Get the **idea** in the user's own words — a sentence or a paragraph, natural language.
   Do not reshape it into requirement grammar yet; the raw phrasing is what the search
   needs.
2. Confirm the **project** (from `project_id`) and, when the idea is code-shaped, the
   **repository**. A repository is optional: an idea can be searched company-wide.
3. Company-wide knowledge is in scope by default. Records the company holds outside this
   project are precedents too, and the most useful ones often are.
4. Restate the idea in one line and get the user's agreement before searching. A search
   built on a misread idea returns confident, irrelevant precedents.

## Step 2: Find the precedents

Every `mcp__Fairmind__Brain_ask` call carries a `question` in plain words, `project` (the
bound `project_id`) and a `question_type`. What comes back is a short list of
**considerations** — each a sentence the brain composed, with whether a person confirmed what
it rests on and the references behind it (cards, documents, files, tickets, pull requests) —
plus **further reading**: records found and not cited.

1. `question_type: "precedent"`, the `question` being the idea in the user's vocabulary. It
   searches by text and by meaning, so ask the *concept*, not only the exact words the user
   used.
2. Ask again with other phrasings, up to four `precedent` questions in a run: the behaviour
   rather than the feature name, the failure it prevents, the adjacent noun. The language a
   feature was specified in years ago is rarely today's. After an answer with
   `reason: cap_reached`, send no further `Brain_ask` in this run.
3. Rank what came back into a plain list of *this exists / this existed*, each with what the
   brain says happened to it and its references. Say which are live and which are gone — in
   the same list, never two lists a reader has to join.
4. **Cite each consideration with its state.** One whose `confirmed` is false is somebody's —
   quite possibly an agent's — unconfirmed draft: it is a useful lead, it is **not** a
   precedent in the sense a reviewer will read it as, and it is cited "(proposed, not yet
   confirmed)", never bare. An entry of `further_reading` is listed as "found, not cited".
   A consideration is said to cite knowledge recorded **at a file** only when it comes from a
   `file` question and one of its `file` references has the bound repository and that path.
   On any other question a `file` reference carries the catalog's own spelling of the
   repository: quote its path as a pointer and make no claim about where anything was
   recorded.

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

## Step 3: Explain what happened to each precedent

For every precedent the brain says was retired, removed, replaced or superseded, report, in
this order:

1. **What it was** — one line.
2. **What happened to it** — retired or replaced, and when, if the answer says.
3. **Why** — the reason as the consideration states it, quoted, not paraphrased into
   something softer.
4. **What it rests on** — the references of the consideration: the decision, document or
   ticket behind the change, by title and id.

**When the answer does not say why a precedent was removed or replaced, ask the person**
before drafting: whether it still holds, and what they know. Do not invent a reason — "no
reason recorded" is the truth, and a plausible reason nobody gave is the one thing this skill
must never produce.

🔴 **A precedent removed for a reason that still holds is a stop-and-ask, not a footnote.**
Surface it, say which reason and which record, and ask the user whether it still applies
**before** drafting anything. If the answer is that circumstances changed, record that
answer as an open question on the draft — it is the thing a reviewer will want to see.

## Step 4: Sketch the impact

**With a bound `repository_id`, load `brain-impact`** and hand it the idea together with the
precedents from Step 2 — their card ids and both of their states — so it does not ask the
`precedent` question again. It takes the files the precedents point to, asks the brain what
it holds about each, asks the code graph who uses the symbols in them, and reports the
conclusions with their references. It records nothing in the brain; it does write its own
report, `docs/reports/brain-impact-<YYYY-MM-DD>.md`, into the user's repository, and this
skill's output names that file beside its own. Take the drafts' files (Step 6) from the
places it traced, and their constraints (Step 5) from the conclusions it reported; say in the
output that `brain-impact` produced the impact answer, and quote its mode line.

**With no repository bound, `brain-impact` cannot run** — it needs one. Sketch instead:

1. Take the file references of the closest precedents — the paths the considerations point
   at.
2. Group them by directory.
3. Produce a short list of modules the change would plausibly touch, each with why it is on
   the list (a precedent that points there, a decision that governs it).

**Keep the sketch light and say that it is.** It is built from where the precedents point,
not a dependency analysis: it does not follow call graphs and it does not claim completeness.
The output names which of the two produced the impact section — `brain-impact` or this sketch.

## Step 5: Draft

Produce, in this order:

- **One need** — the problem in the user's terms, not the solution.
- **User stories** under it — each one a role, a capability and a reason.
- **Functional requirements** — behaviour visible to a user or another system.
- **Technical requirements** — integrations, data, constraints, non-functional properties.

Every drafted item carries:

- **The precedents it rests on**, by title and card id, with **both** of their states: live or
  gone, *and* confirmed or proposed. A precedent that is itself an unconfirmed proposal is
  cited as `precedent: {title} ({id}) (proposed, not yet confirmed)` — never as a bare id.
  Grounding a new requirement in a draft nobody has agreed with is legitimate; presenting that
  grounding as settled is not, and the id alone does not tell them apart.
- **The decisions that constrain it**, by title and id — a draft that contradicts a recorded
  decision says so in its own text rather than leaving a reviewer to discover it.
- **Files**, where Step 4 identified real ones.
- **Open questions where the brain is silent.** This is a first-class output, not an
  apology. Anything invented to fill a gap becomes an open question instead of a confident
  sentence, and a draft with no open questions on an idea the brain barely covers is a
  warning sign about the draft.

## Step 6: Write the drafts back

For every drafted item, in order — the need first, then its stories, then the functional
requirements, then the technical ones — call `mcp__Fairmind__Brain_tell` with
`intent: "need"` and:

| field | value |
|---|---|
| `repository` | the bound `repository_id`, else the repository's name |
| `project` | the bound `project_id` |
| `title` | the item as drafted, on one line, short. The brain identifies a record by its title: the same idea run twice finds its own drafts instead of minting a second set |
| `summary` | what the item is — a need, a user story, a functional or a technical requirement — the text as drafted, the title of the item it refines, and the precedents it rests on with their states. At most 1000 characters; the door refuses a longer text and never cuts it, so an item that needs more is two items |
| `files` / `functions` | repo-relative paths, and `"<path>::<name>"` per symbol — only where Step 4 found real ones; a new requirement legitimately has none |
| `documents` | `[{"url": ..., "title": ..., "summary": ...}]` for a document it rests on and has a link — at most ten related items in all |
| `task_ref` | the ticket the item belongs to, **only when the person named it in this conversation** |
| `task_ref_status` | a status the person stated about that ticket in this conversation, never inferred, and only with `task_ref` |
| `session_ref` | `"${CLAUDE_SESSION_ID}"` |
| `agent` | `brain-new-requirement` |

Do not send a requirement type, a key, a parent reference, an anchor or a precedent list: the
brain decides what kind of record each item is and links the items to each other and to code
when it places them, and a report that carries a field it does not take is refused. The
report shows the chain as drafted and says the brain links the items when it places them.

A status rides on the report that names its ticket. **The status change itself waits for no
approval**; the report that carries it may sit in the review queue for its own reasons.
`cancelled` and `retired` wait for a person; `rejected` is recorded and never applied. A
status told again with the same report changes nothing: to apply it again after the card
moved back, send a new report, whose title says so, such as `Done again: <ticket>
(<task_ref>)`.

**Read what comes back.** Report the `state` as the answer gives it, never as you expect it,
with the `node_id`:

- `proposed` — recorded, and waiting for a person in the review queue. `confirmed` is not
  what a need lands as; if an answer says it, report it as the answer says.
- `duplicate` — the same item was already there and **nothing changed**. Report it that way.
- `conflict` — an item with this title exists and says something different. **Nothing was
  overwritten.** Show the person the difference; on a yes the item is sent again, whole, with
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

- **Never send a status, a `retract` or a `same_as` to confirm anything**, and never present a
  draft as confirmed. The recorded `review_state` is always `proposed`, whatever else the call
  says — a human confirms in the review queue.
- **A partial write is a normal outcome and is reported as one:** "the need and two stories
  landed, the four requirements did not" is usable; "some drafts were recorded" is not. A
  child is told only after its parent was.

Then write the markdown copy to **`docs/reports/brain-new-requirement-<YYYY-MM-DD>.md`** in
the user's repository (add the idea's slug to the filename when more than one idea is
drafted on the same day). Say that you created it and let the user decide whether to commit
it.

## Output Structure

```markdown
# New requirement from the brain — {idea, one line} ({date})

**Status: proposed — awaiting human review.** Nothing below is confirmed knowledge.

## The idea
{the user's own words}

## Precedents found

Two independent things are told about each one: whether it still holds, and whether a person
ever confirmed it. A retired record can be confirmed knowledge; a live-looking one can be an
unconfirmed draft.

| What the brain says | Holds today? | Review | References |
|---|---|---|---|
| {consideration} | live / removed / replaced | confirmed / **proposed, not yet confirmed** | {title and id of each card, document, ticket} |

### What happened to them
- **{title}** — {removed|replaced} {when, if said}. Reason (as recorded): "{reason}" |
  no reason recorded. Record behind it: {title} ({id}). **Still applies? {answer or OPEN}**

## Impact — {from `brain-impact` (usages-only), see its report | light sketch — from where the precedents point, not a dependency analysis}
- {module or component} — {why it is on the list: a precedent pointing there, a dependent in the code graph, a decision that governs it}

## Draft

The brain links the items when it places them; the lines below show the chain as drafted.

### Need — {title}
{text} · precedents: {titles and ids with states} · constrained by: {decision titles and ids}

### User stories
- {title} — refines {need title} · precedents: {titles and ids}

### Functional requirements
- {title} — refines {story title} · files: {path}:{symbol}

### Technical requirements
- {title} — refines {functional title} · files: {path}

## Open questions (the brain is silent on these)
1. {question} — needed before {which draft} can be confirmed

## Yield
- Precedents surfaced: {n} (removed or replaced: {n}; unconfirmed proposals: {n}; found, not cited: {n})
- Removal reasons found in the answer: {n} / {n}
- Drafts told (all `proposed`): need {n}, stories {n}, functional {n}, technical {n}
- Drafts carrying ≥1 precedent: {n} / {n} — of which resting only on unconfirmed proposals: {n}
- Answered `duplicate`: {n}; answered `conflict`: {n}
- Not written (refused / failed): {n}
- Precedents: {checked | not checked: {why}}
- Report written to: docs/reports/brain-new-requirement-{date}.md
- Impact report: {docs/reports/brain-impact-{date}.md, written by `brain-impact` | none — light sketch, no repository bound}

**Hand check:** would the team accept this as the starting spec?
Confirm or reject each draft in the review queue — this skill confirms nothing.
```

## Next Steps

- **Answer the open questions**, then rerun: the same titles make the second pass find the
  same drafts rather than a duplicate set.
- **Confirm or reject** in the platform's review queue. Until a human does, these are
  proposals and nothing downstream should treat them as the specification.
- **For implementation** once confirmed, load `fairmind-context`, then `fairmind-tdd`.
- **For the wider picture** — the requirements of the whole existing system rather than one
  new one — load `brain-rebuild-requirements`.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id`:** stop and point the
user at `/fairmind-connect`. Do not draft from the idea alone and present it as grounded —
say plainly that the precedents live in the brain and the brain is not reachable.

**No `mcp__Fairmind__Brain_tell` in the tool list:** the connected server predates the
platform release that serves it. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**An MCP error result on `Brain_tell`:** the brain may not offer `Brain_tell` yet, or the
plugin and the brain disagree on the report's shape. Say so and stop; sending it again
changes nothing.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report which drafts were written, which were not, and their titles, so the
next run resumes there. Never retry in a loop — every attempt is refused and the refusals are
counted against the tenant.

**`{'retryable': True}`:** the write could not be completed right now — an erasure in
flight, or the store unreachable. One retry is reasonable; a second refusal is an outage —
report it and stop.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote
the key name verbatim and stop; someone with platform access has to set it for this tenant.

**An access refusal:** the key, or the person behind it, may not write to this project. Say
so and stop; retrying changes nothing.

**No precedents found at all:** a real and reportable answer, not a failed search. Say which
phrasings were tried and what `coverage` the answer reports, say the brain holds nothing on
this, and draft with every item marked as having no precedent — a reviewer reads that
differently, and should.

**A precedent with no reason recorded for its removal:** report the gap as a gap. "Removed,
no reason recorded" is the truth; inventing a plausible reason is the one thing this skill
must never do.
