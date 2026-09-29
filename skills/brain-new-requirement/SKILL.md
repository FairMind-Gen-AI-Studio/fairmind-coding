---
name: brain-new-requirement
description: Use when a new feature, need or requirement is about to be written and the question "did we already do this, and what happened to it?" has not been answered - it searches the company brain for precedents including the ones that were retired or superseded, explains why each was removed and which decision removed it, sketches which modules the change would touch, then drafts the need, its user stories and the functional and technical requirements citing those precedents, recording them as proposals for a human to confirm. Also use when asked to "check if this existed before", "why was this removed", or "turn this idea into a spec".
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
resolution chain this skill reuses in Step 0. Search phrasing, the reference shapes and the
worked example live in `references/playbook.md`.

**What leaves your machine when this skill writes.**
`mcp__Fairmind__Brain_record_requirement` sends the drafted title and text, its anchors
(repo-relative **file paths**, symbol names, line hints, and — when a repository is bound —
the **repository's catalog id**), the node ids cited as precedents, and an agent label — to
the Fairmind platform your project's MCP entry points at. It confirms nothing and changes no
existing record. This is the **brain write-back**, and the plugin README's *What leaves your
machine* describes it in full under that name — alongside every other route by which anything
leaves.

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
   present, sent on every anchor in Step 6. **Never write those keys yourself**, and never set
   `fairmind` to `"configured"` on their behalf.
2. If the file is missing, `fairmind` is `"none"`, or `project_id` is absent: **stop** and
   tell the user to run `/fairmind-connect` once for this checkout. There is no standalone
   version of this skill — with no brain there are no precedents, and drafting without them
   is exactly the failure it exists to prevent. (`mcp__Fairmind__Insights_bind_repository`
   exists as a platform door; this skill does not call it.)
3. Confirm the ten `mcp__Fairmind__Brain_*` tools are in the tool list. They arrive with the
   platform's **Brain-1** release; an older server answers `mcp__Fairmind__Studio_*` and
   `mcp__Fairmind__Insights_*` normally and exposes no `mcp__Fairmind__Brain_*` tool at all.
   If they are absent, say which release is needed and stop.

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

1. `mcp__Fairmind__Brain_search` with the idea's own vocabulary, `k` small. It matches both
   by text and by meaning, so search the *concept*, not only the exact words the user used.
2. Run it again for the removal states: `status: ["retired", "superseded"]`. `status` takes
   one value or a list, and naming a terminal status brings back the records that left the
   current view without `include_invalidated`. Their results carry the `reason` the record was
   retired. This second pass is not optional. Vary the phrasing two or three times: the
   language a feature was specified in years ago is rarely today's.
3. Widen the `kinds` beyond requirements: tasks, issues and decisions are where the real
   history of a feature usually sits.
4. Rank what came back into a plain list of *this exists / this existed*, each with its
   status, its dates and its node id. Say which are live and which are gone — in the same
   list, never two lists a reader has to join.
5. **Read the `review_state` on every result and carry it with the result.** It is a second
   axis, independent of `status`: `status` says whether the behaviour is still wanted (live,
   retired, superseded), `review_state` says whether a **person ever agreed with the record**
   (`confirmed`, `proposed`, `rejected`). A `proposed` record is somebody's — quite possibly
   an agent's — unconfirmed draft. It can be a useful lead and it is **not** a precedent in
   the sense a reviewer will read it as, so it never appears without its state attached.
   `mcp__Fairmind__Brain_search` takes `review_state` as a filter when you want one axis
   alone.

**Token discipline.** Read the search results as they come; open a record with
`mcp__Fairmind__Brain_get` only when a precedent is close enough that its body matters, and
use `mcp__Fairmind__Brain_expand` for neighbouring chunks rather than re-fetching the
record. `mcp__Fairmind__Brain_search` pages: `k` is the page size (at most 50), and a
`next_cursor` that is not null means more results stand behind the page — send the same call
again with `cursor` set to it, and the same filters, until `next_cursor` comes back null. A
precedent search follows it for at most five pages; stopped with a cursor still there, what it
holds is the closest matches rather than all of them, and the output says so.

## Step 3: Explain what happened to each precedent

For every precedent that is retired, superseded, or otherwise not live, call
`mcp__Fairmind__Brain_timeline` with its `knowledge_id`. It returns the record's history:
when it was created, each status change, when it was retired and the **reason**, what
superseded it, and the decision or incident behind the change.

Report per precedent, in this order:

1. **What it was** — one line.
2. **What happened to it** — retired or superseded, when.
3. **Why** — the reason as recorded, quoted, not paraphrased into something softer.
4. **Which decision** — the decision or incident linked to the change, by node id.

🔴 **A precedent removed for a reason that still holds is a stop-and-ask, not a footnote.**
Surface it, say which reason and which decision, and ask the user whether it still applies
**before** drafting anything. If the answer is that circumstances changed, record that
answer as an open question on the draft — it is the thing a reviewer will want to see.

## Step 4: Sketch the impact

**With a bound `repository_id`, load `brain-impact`** and hand it the idea together with the
precedents from Step 2 — their node ids and both of their states — so it does not search
again. It follows the precedents' anchors to the code, asks the code graph who uses those
symbols, maps the files to the architecture components they sit in and the components that
depend on them, and collects the decisions, incidents and criteria recorded there. It records
nothing in the brain; it does write its own report, `docs/reports/brain-impact-<YYYY-MM-DD>.md`,
into the user's repository, and this skill's output names that file beside its own. Take the
drafts' anchors (Step 6) from the files it resolved, and their constraints (Step 5) from the
decisions it found; say in the output that `brain-impact` produced the impact answer, and quote
its mode line.

**With no repository bound, `brain-impact` cannot run** — it needs one. Sketch instead:

1. Take the anchors of the closest precedents — the files and symbols they pointed at.
2. For each distinct directory among them, call `mcp__Fairmind__Brain_context` with
   `directory`, `granularity: "file"`, `detail_level: "minimal"` to see which modules carry
   knowledge today and what else is anchored there.
3. Produce a short list of modules the change would plausibly touch, each with why it is on
   the list (a precedent anchored there, a decision that governs it).

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

- **The precedents it rests on**, by node id, with **both** of their states: live / retired /
  superseded, *and* confirmed / proposed. A precedent that is itself an unconfirmed proposal
  is cited as `precedent: {node_id} (proposed, not yet confirmed)` — never as a bare node id.
  Grounding a new requirement in a draft nobody has agreed with is legitimate; presenting that
  grounding as settled is not, and the node id alone does not tell them apart.
- **The decisions that constrain it**, by node id — a draft that contradicts a recorded
  decision says so in its own text rather than leaving a reviewer to discover it.
- **Anchors**, where Step 4 identified real files.
- **Open questions where the brain is silent.** This is a first-class output, not an
  apology. Anything invented to fill a gap becomes an open question instead of a confident
  sentence, and a draft with no open questions on an idea the brain barely covers is a
  warning sign about the draft.

## Step 6: Write the drafts back

For every drafted item, call `mcp__Fairmind__Brain_record_requirement` with:

| field | value |
|---|---|
| `project` | the bound `project_id` |
| `req_type` | `need`, `user_story`, `functional` or `technical` |
| `title` / `text` | the item as drafted |
| `natural_key` | **`<module-or-feature>:<slug of the title>`** — lowercase, non-alphanumerics to `-` |
| `refines` | `[{"node_id": <knowledge_id>}]` — the parent's reference: story → need, functional → story, technical → functional |
| `precedent_refs` | node ids of the precedents found in Step 2 |
| `anchors` | `file_path` plus `repo_ref` / `repo_ref_scheme` / `symbol_name` / `symbol_type` / `start_line_hint` where the sketch found them; omitted entirely when `repository_id` is not bound (see below) |
| `evidence` | the quoted retirement reasons and decision extracts the draft rests on |
| `agent` | the label identifying this run |

**`refines` is always `[{"node_id": <id>}]` — the JSON key is `node_id`, never `knowledge_id`,
never the natural key.** The `<id>` value is what the *parent's own write response* returned:
`knowledge_id` on an ordinary `{'status': 'ok', ...}` response, or `target` when the parent's
write came back `revision_proposed` (see below — that response carries no `knowledge_id` at
all). Every item drafted in this step is written in the same run as its parent (need, then its
stories, then the requirements under each story), so capture that id from the parent's
response before writing its children. A raw natural-key reference does not raise an error; the
platform stores a transformed form of the key, so the reference silently derives the wrong
node id and the edge is never built — sending `{"knowledge_id": ...}` instead of
`{"node_id": ...}` fails exactly the same way, silently. Record the need first — not because
its natural key exists sooner (it does, but that is not why), but because no id to refine by
exists until its own write responds, and every story needs that value.

**`repo_ref`/`repo_ref_scheme` require a bound `repository_id`.** Set from Step 0 when
present, as `repo_ref` (the id) and `repo_ref_scheme: "code-ingestion"`. When no repository is
bound — this skill's precondition allows a company-wide idea with no `repository_id` — omit
the anchor rather than send one missing `repo_ref`, and say so explicitly next to that draft
in the report: "anchor omitted — no repository bound to this run." This is a different case
from "no anchor found" and must read as one; folding the two into one sentence hides which
happened.

**The natural key is supplied by the client and is what makes a rerun converge** — the
platform never synthesizes one. Choose the module-or-feature prefix once, derive the slug
deterministically from the title, and write both into the report: the same idea run twice
updates its own drafts instead of minting a second set.

**What a write response actually looks like.** An ordinary success is `{'status': 'ok',
'knowledge_id': <id>, 'doc_id': …, 'anchors': …, …}` — it carries no `review_state` field; the
draft's `review_state` is `proposed` on the platform, but that is not a key you will see come
back from this call. Do not wait to see `review_state` before trusting a `status: 'ok'`
response.

**Two hard rules, no exceptions:**

- **Never call `mcp__Fairmind__Brain_set_status` to confirm anything**, and never present a
  draft as confirmed. The recorded `review_state` is always `proposed`, whatever else the call
  says — a human confirms in the review queue.
- **When the response is `revision_proposed`** (`{'status': 'revision_proposed',
  'proposal_id': …, 'target': …}`), the natural key hit a record a human already confirmed.
  The record was left untouched and a revision proposal was filed instead. Report the
  `proposal_id` and move on — do not retry, and do not route around it with a different key.
  **If this item has children, capture `target` for their `refines`** — a child left without
  it cannot be linked and must be reported as such, never sent with no `refines` or a guessed
  key.

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

Two independent columns: **Status** is the record's lifecycle, **Review** is whether a person
ever confirmed it. A retired record can be confirmed knowledge; a live-looking one can be an
unconfirmed draft.

| What | Status | Review | When | Node id |
|---|---|---|---|---|
| {title} | live / retired / superseded | confirmed / **proposed, not yet confirmed** | {date} | {node_id} |

### What happened to them
- **{title}** — {retired|superseded} on {date}. Reason (as recorded): "{reason}".
  Decision behind it: {title} ({node_id}). **Still applies? {answer or OPEN}**

## Impact — {from `brain-impact` (usages-only), see its report | light sketch — from where the precedents point, not a dependency analysis}
- {module or component} — {why it is on the list: a precedent anchored there, a dependent in the code graph, a decision that governs it}

## Draft

The lines below print each item next to its **parent's** natural key, for readability. The
actual `refines` call never uses that key — it uses the parent's id as captured from the
parent's own write response (`knowledge_id`, or `target` on a `revision_proposed` parent); see
Step 6.

### Need — {title}
{text} · precedents: {node_ids} · constrained by: {decision node_ids}

### User stories
- {title} — refines {need natural_key} · precedents: {node_ids}

### Functional requirements
- {title} — refines {story natural_key} · anchors: {path}:{symbol}

### Technical requirements
- {title} — refines {functional natural_key} · anchors: {path}

## Open questions (the brain is silent on these)
1. {question} — needed before {which draft} can be confirmed

## Yield
- Precedents surfaced: {n} (retired or superseded: {n}; unconfirmed proposals: {n})
- Retirement reasons explained with a linked decision: {n} / {n}
- Drafts recorded (all `proposed`): need {n}, stories {n}, functional {n}, technical {n}
- Drafts carrying ≥1 precedent: {n} / {n} — of which resting only on unconfirmed proposals: {n}
- Revision proposals filed (key already confirmed by a human): {n}
- Not written (refused / failed): {n}
- Natural keys used: {list} — a rerun of the same idea reuses these
- Report written to: docs/reports/brain-new-requirement-{date}.md
- Impact report: {docs/reports/brain-impact-{date}.md, written by `brain-impact` | none — light sketch, no repository bound}

**Hand check:** would the team accept this as the starting spec?
Confirm or reject each draft in the review queue — this skill confirms nothing.
```

## Next Steps

- **Answer the open questions**, then rerun: the natural keys make the second pass update
  the same drafts rather than duplicate them.
- **Confirm or reject** in the platform's review queue. Until a human does, these are
  proposals and nothing downstream should treat them as the specification.
- **For implementation** once confirmed, load `fairmind-context`, then `fairmind-tdd`.
- **For the wider picture** — the requirements of the whole existing system rather than one
  new one — load `brain-rebuild-requirements`.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id`:** stop and point the
user at `/fairmind-connect`. Do not draft from the idea alone and present it as grounded —
say plainly that the precedents live in the brain and the brain is not reachable.

**No `mcp__Fairmind__Brain_*` tools in the tool list:** the connected server predates the
**Brain-1** release. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report which drafts were written, which were not, and their natural keys, so
the next run resumes there. Never retry in a loop — every attempt is refused and the
refusals are counted against the tenant.

**`{'retryable': True}`:** the quota service itself was unreachable. One retry is
reasonable; a second refusal is an outage — report it and stop.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote
the key name verbatim and stop; someone with platform access has to set it for this tenant.

**`revision_proposed`:** expected, not an error — see Step 6. Count it, name the
`proposal_id`, capture `target` if this item has children to refine, continue with the next
draft.

**No precedents found at all:** a real and reportable answer, not a failed search. Say which
phrasings were tried, say the brain holds nothing on this, and draft with every item marked
as having no precedent — a reviewer reads that differently, and should.

**A precedent whose timeline has no reason recorded:** report the gap as a gap. "Retired, no
reason recorded" is the truth; inventing a plausible reason is the one thing this skill must
never do.
