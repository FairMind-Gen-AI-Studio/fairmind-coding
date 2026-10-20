---
name: brain-extract-components
description: Use only when a person asks to turn a blueprint into the architecture model the company brain can reason with - it finds the project's blueprint documents in the brain, reads them, proposes the components they name (name, responsibility, layer, technology and the directories that implement each) with their containment and dependency links, shows the draft and waits for a yes, then records it through the component door as proposals a human confirms. Also use when asked to "extract the components from the blueprint", "build the architecture model", or "which components does our design name".
---

# Components from a blueprint

## Overview

A component is a named part of the architecture — what the blueprint calls "the billing
service" or "the ingestion pipeline" — anchored to the directories that implement it. Once a
person confirms one, a read at any file inside those directories returns the component, what
it depends on and what depends on it, and the decisions attached to it. Without components,
the answer to "what does this change touch" stops at a list of files, and a rule like "the API
layer never talks to the database directly" has nowhere to attach.

**The blueprint is the only source this skill reads.** It does not guess an architecture from
the directory tree, a README or the code: a component invented that way is a claim nobody
made, and it would arrive looking exactly like one a blueprint states. A project with no
blueprint filed in the brain gets a clear answer and a stop, not a guess.

Everything this skill writes lands as `proposed`. A human confirms.

**Announce at start:** "I'm using the brain-extract-components skill to propose the components
the blueprint names, for a person to confirm."

**Dependencies:** requires a connected Fairmind workspace — the same resolution chain
`fairmind-context` uses. The door's full parameter and response shapes, the refusals and a
worked example live in `references/playbook.md`.

**What leaves your machine when this skill writes.**
`mcp__Fairmind__Brain_record_component` sends, for each component: its name and, when it
is already recorded, its key; the responsibility and description the agent wrote from the
blueprint (free prose), the
repo-relative **directory paths** that implement it, its subtype, layer, technology, owning
team and status when the blueprint states them, and the names, keys or node ids of the
components it belongs to and depends on. With them go the **repository's catalog id**, the
project id, the blueprint's node id and an agent label — to the Fairmind platform your
project's MCP entry points at. It confirms nothing. This is the **brain write-back**, and the
plugin README's *What leaves your machine* describes it in full under that name — alongside
every other route by which anything leaves.

## When to Use

Use this skill when a person asks for it:

- A blueprint or architecture document is filed in the brain and the components it names
  should become records the brain can answer questions about
- The blueprint changed and its components should be brought up to date — a rerun lands on
  the records the last run wrote
- Someone asks which components the design names, and where each one lives in the code

Do **not** use it to:

- **File the blueprint itself** — that is `brain-add-document`, with `doc_kind: blueprint`.
  This skill reads a document the brain already holds.
- **Record a decision about a component** — a decision has its own door, and names the
  component it concerns there.
- **Recover requirements** from the code — that is `brain-rebuild-requirements`.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the
   `fairmind` field set to `"configured"`, the `project_id` and the `repository_id` that
   `/fairmind-connect` writes. **Never write those keys yourself**, and never set `fairmind`
   to `"configured"` on their behalf.
2. If the file is missing, `fairmind` is `"none"`, or `project_id` or `repository_id` is
   absent: **stop** and tell the user to run `/fairmind-connect` once for this checkout. The
   component door takes exactly one repository and resolves every directory inside it, so a
   run with no bound repository has nowhere to anchor anything.
3. Confirm the `mcp__Fairmind__Brain_*` tools are in the tool list. They arrive with the
   platform's **Brain-1** release; an older server answers `mcp__Fairmind__Studio_*` and
   `mcp__Fairmind__Insights_*` normally and exposes no `mcp__Fairmind__Brain_*` tool at all.
   If they are absent, say which release is needed and stop.
4. Confirm `mcp__Fairmind__Brain_record_component` is in the tool list **by name**. It
   arrives with a later platform release than the other `mcp__Fairmind__Brain_*` tools, so a
   connected server can offer those and not this one. If it is absent, say so and **stop** —
   do not reach for a neighbouring door instead: a component recorded as a requirement or a
   decision is a claim nobody made.

## Step 1: Find the blueprint

1. Browse the project's documents: `mcp__Fairmind__Brain_search` with `kinds: "document"`,
   `project: <project_id>` and **no** `query`. Follow `next_cursor` until it is null —
   including after a page that came back empty, because a browse page can be empty and still
   not be the last one.
2. Keep the results whose `subtype` is `blueprint`: that is the `doc_kind` the document was
   filed with. Company-wide documents come back in the same browse, and a company-wide
   blueprint counts.
3. **No blueprint: say so and stop.** "No blueprint is filed in the brain for this project."
   The way forward is to file the blueprint with `brain-add-document` (`doc_kind: blueprint`)
   and run this skill again — say that, and do not do it on your own. Do not extract from a
   design note, a README or the directory tree instead, and do not tell the person the
   platform will propose components from the code without a blueprint: it proposes none.
4. More than one blueprint: list them — title, status, `review_state`, node id — and read
   all of them unless the person narrows the list. Run Steps 2–6 once per blueprint: the door
   links every component in a call to the one blueprint that call names, so each blueprint is
   drafted and written in calls of its own. A component two blueprints both name is drafted
   once — the person settles its fields — and sent, with those same fields, in each
   blueprint's call. The platform keeps every blueprint a component is described by, so the
   second call only adds its link; a second call with different fields would overwrite the
   first.
5. Carry the blueprint's `review_state`. A blueprint nobody confirmed is cited as
   `blueprint: {node_id} (proposed, not yet confirmed)` in everything this run shows.

## Step 2: Read it

1. `mcp__Fairmind__Brain_get` with `knowledge_id: <the blueprint's node_id>` and a generous
   `max_tokens`, then walk it by chunk index until chunk `chunks_available - 1` is read. An
   answer holding several chunks holds them whole: continue with `section: {"from":
   <chunk_range.to + 1>, "to": <chunks_available - 1>}`. An answer holding one chunk and
   saying `truncated` may hold it cut: read it alone, `section: {"chunk_index": <i>}`, with a
   larger `max_tokens` (the platform's ceiling is 100000) until it is not `truncated`.
   `truncated` alone never drives the walk — it is set on a cut chunk too, and a range past
   the last chunk is clamped to it rather than refused, so a loop on it can drop a chunk's
   tail or never end. A component named on page thirty is still a component.
2. **A card with no body cannot be read.** A blueprint filed as a link only comes back with
   empty `content`, and this skill does not fetch links. Say so and stop: the blueprint has to
   be filed with its content before anything can be extracted from it. Never extract from a
   title.
3. Take out only what the blueprint states:
   - each component it names, in its own words;
   - what each one is responsible for;
   - its layer, technology and owning team, where the blueprint gives them;
   - the directories or modules it points at;
   - which component contains which;
   - which depends on which, and how — calls it, reads from it, or publishes to it.

   Whatever the blueprint does not say stays empty. A technology inferred from a file
   extension is a guess the reader cannot tell from a statement.

## Step 3: Check what the brain and the code already hold

1. Browse the components already recorded: `mcp__Fairmind__Brain_search` with
   `kinds: "component"`, `project: <project_id>`, `include_invalidated: true` and no `query`,
   following `next_cursor` to the end. Send the same filters with every cursor: a cursor is
   valid only for the request that produced it. List each one with its title, its
   **`natural_key`** (`component:<projectId or *>:<slug>`), status, **`review_state`** and
   node id. `include_invalidated` brings back the components that were **rejected** or
   **retired**, which the default browse hides. Their keys belong in the list too: a
   component renamed onto its old key and then closed must be sent with that key, or its new
   name mints a fresh key that the closing does not cover.
2. **Reuse, do not re-mint.** A component's identity is its key, not its title. Match each
   component the blueprint names against the list by the slug in the recorded `natural_key`
   first and the title second. Compare them the way a key is made — lowercase, every run of
   other characters as `-` — so "Billing service" and "Billing Service" are one component.
   On a match whose key starts `component:<project_id>:`, the part after that prefix is the
   recorded slug: send the recorded slug as `key`, and the component lands on its own record
   whatever the blueprint now calls it. A match on a `component:*:` key is the whole
   company's: do not send it. This skill writes into the project, so sending it would mint a
   project copy. Name it by that full key wherever another component belongs to it or
   depends on it. When a name matches both, the project's record is the one to send.
3. **A component the blueprint has renamed** is a question for the person the first time:
   keep the old record (send its recorded slug as `key`, with the new name) or start a new
   one. On the next run the read returns the new title and the old key on the same row, so
   the match needs no question.
4. **A component a person closed stays closed, and the platform keeps it that way.** A
   recorded component whose status is `rejected` or `retired` was closed by someone, and you
   cannot tell from a read who closed it; the platform can. Draft it like any other
   component the blueprint names, and mark it **closed** in the draft. If a person closed it,
   the platform writes nothing and answers that line `not_reproposed` with a `reason`
   (Step 6). If an agent retired it, the platform records it again. Reopening what a person
   closed is that person's act on the platform: no call this skill makes does it.
5. **Read `review_state` on what comes back.** A `proposed` component is an unconfirmed draft
   — quite possibly this skill's own, from an earlier run — and it is cited as
   `{node_id} (proposed, not yet confirmed)`, never as settled architecture.
6. Match every directory against the ingested repository with `mcp__Fairmind__Code_tree`
   (`project`, `repository: <repository_id>`, `start_line: 1`, `max_lines: 200`). The tree
   comes in pages, and a page says so only when it is not the last: under the repository's
   name it carries `(showing lines X-Y of Z)` — some servers print `Showing lines X-Y of Z
   total lines` instead. While such a line is there with Y below Z, call again with
   `start_line` set to Y + 1 (lines count from 1 and Y was already shown). A page with no such
   line, or with Y equal to Z, is the last. A page shorter than asked is not a stop signal: the
   server caps `max_lines` on its own. A directory is marked missing only against the whole
   tree. The blueprint's module names become repo-relative directory
   paths that exist in the tree. A directory the blueprint names and the tree does not have
   is marked **not in the ingested tree** in the draft; it is sent only if the person keeps
   it, and it comes back unverified. This is a check before sending: the `anchor_state` the
   door returns (Step 7) is the answer that counts.
7. When existing components were found in 1, check each directory the draft anchors with
   `mcp__Fairmind__Brain_context` (`repository: <repository_id>`, `directory: <path>`,
   `detail_level: "minimal"`). Its `components.containing` lists the live components — the
   `proposed` ones included — whose directories hold that directory, deepest first, each with
   its `natural_key`, `review_state` and the deepest of its directories that holds this one.
   One read per directory, never per file.
   - **A component whose directory is this one already models it** — the same component under
     another name. Mark the draft's component **already modelled as {title} ({node_id})**, with
     that component's `natural_key` and `review_state`, and ask the person which it is:
     **reuse** it — send the draft's component with that record's slug as `key`, so it lands on
     that record and takes the blueprint's name and fields — or **skip** it. A company-wide one
     (`component:*:`) is never sent (item 2): skipping it and naming it by its full key where
     others depend on it is the reuse. Never send it under a fresh key beside the recorded one
     without the person saying so: that is a second component for the same code.
   - **A component whose directory holds this one from above** is the one it sits inside — a
     `part_of` candidate, when the blueprint says so.

## Step 4: Draft

One entry per component the blueprint names, using only the fields the door accepts:

| field | value |
|---|---|
| `name` | required — the component's name as the blueprint writes it |
| `key` | the recorded slug, for a component already recorded in this project (Step 3); leave it out for a new one, whose key is its name's slug |
| `responsibility` | one or two sentences in the blueprint's own vocabulary — it becomes the summary a search matches on |
| `description` | a longer passage, when the blueprint gives one worth keeping |
| `directories` | repo-relative paths from the tree, at most 10 |
| `subtype` | `service`, `module`, `library`, `data_store`, `frontend` or `integration`, only when the blueprint makes it plain |
| `layer` | the blueprint's layer name |
| `technology` | as the blueprint states it |
| `owner_team` | as the blueprint states it |
| `status` | leave it out for a built component; `planned` or `deprecated` when the blueprint says so |
| `part_of` | the enclosing component, as a reference (below) |
| `depends_on` | a list; each entry `{"component": <reference>, "kind": "calls"}` (`calls`, `reads` or `publishes`), or a bare reference when the blueprint does not say how |

**No other key.** The door refuses an unknown key rather than dropping it, and it refuses the
whole call with it.

**A reference is read as a key, never as a title.** The platform turns whatever you write in
`part_of` or `depends_on` into a slug in this project; it does not look a title up. So name a
component by the `key` it is sent with or recorded under — inside the same call too — and a
company-wide one by its full `component:*:<slug>` key. A new component, whose key is its
name's slug, can be named by its name.

At most **20 components per call**. A blueprint that names more is written in several calls,
the components that others depend on or belong to first: a reference to a component written
in an earlier call resolves by its key.

## Step 5: Show what is about to leave, and ask

Being asked to extract the components is not a yes for this draft — the draft is your reading
of the blueprint, and the person has not seen it. Show, in a table: each component's name,
responsibility, layer, technology, directories (with the ones not in the ingested tree
marked), what it belongs to and what it depends on and how, and its recorded key when it has
one — with the components that are **closed** marked, and a line saying the platform leaves
alone what a person closed, and each component **already modelled as** another marked with
the person's answer: reused under that record's key, or skipped. Then say:

- **where it will be visible** — this project, by name;
- **what it will be linked to** — the blueprint it was read from, by title, as the document
  each component is described by;
- **what travels** — the fields above, the repository's catalog id and the project id.

Then **wait for a yes.** An edit from the person is applied to the draft, and the changed
draft is shown again before it is sent.

**This skill records components in the project, and offers no company-wide scope.** The
platform picks the project from the repository's binding first, then from the key's own
project, and only then from `project`; a component is company-wide only when all three are
absent. So leaving `project` out would not make the components company-wide for a bound
repository or a project key, and a promise that it would is one the platform does not keep.
Sending `project` still does a job: when it names a project other than the one the key was
issued for, the platform refuses the whole call before anything runs. Without it, a key
issued for another project could write the components into that project instead.

## Step 6: Write

```
mcp__Fairmind__Brain_record_component(
    repository=<repository_id>,
    project=<project_id>,
    blueprint=<the blueprint's node_id>,
    components=[...],        # the approved draft, at most 20
    agent=<the label identifying this run>,
)
```

Always pass `project`. Do not send `git_remote` — the bound `repository_id` already names the
repository, and the remote URL is a disclosure this skill has no use for. Components whose code lives in another
repository go in a call of their own, with that repository as the person names it — never
guessed.

**Two hard rules, no exceptions:**

- **Never call `mcp__Fairmind__Brain_set_status` to confirm anything**, and never present a
  component as confirmed. The recorded `review_state` is always `proposed`, whatever else the
  call says — a human confirms in the review queue.
- **Never merge a duplicate.** A `possible_duplicates` entry is a candidate the platform
  queued for a person to decide on; it is reported, never acted on — no second call that
  folds one component into the other, no status change.

**What a response looks like.** `{'status': 'ok', 'received', 'recorded', 'components', …}`
with **one line per component you sent, in the order you sent them.** Each line's `outcome`
is `created`, `updated`, `unchanged`, `revision_proposed`, `not_reproposed` or `refused`:

- `revision_proposed` — the component's `natural_key` belongs to a record a human already
  confirmed. It was left untouched and a revision proposal was filed; the line carries its
  `proposal_id`. Report it and move on — do not retry, and do not route around it with
  another key.
- `not_reproposed` — a person rejected or retired this component. Nothing was written and
  nothing was filed; the line carries a `reason` such as "rejected by a person — not
  re-proposed". Relay the `reason` verbatim. It is not a failure, and not something to retry
  or to route around with another key. Its `directories` are the stored ones, read back, not
  checked by this call, and it is not counted in `recorded`.
- `refused` — the line carries a `message` and, when trying again can help, `retryable`.
  A refused line does not undo the others. The exception is a refusal that names an erasure:
  every line after it is refused the same way, because the erasure began while the call was
  being written and nothing after that point landed.
- `edges_deferred: true` — a dependency inside a cycle; it lands on the platform's next pass.
  Not a failure. Neither is a non-zero `referrers_rearmed`.

## Step 7: Report

One line per component, in the response's order: the outcome, the name, the `natural_key`,
the `knowledge_id`, each directory with its `anchor_state` (`verified` — a file exists under
it; `unverified`; or unknown), the anchor tier counts, its duplicate candidates as the
duplicate check answered them (below), and the `proposal_id`, the `reason` or the refusal
`message` where there is one.

**Read every line's `graph` — the links are reported there, not refused.** A `depends_on` or
`part_of` reference is turned into a key and never looked up by title, so a reference that
names no recorded component does not refuse the call: the line still reads `created`,
`updated` or `unchanged`, and the link is simply not recorded.

- `graph.edges` — one entry per link, `{edge, target, status}`. `merged` is recorded.
  `target_missing` means the target is not recorded, and the link was not written. On a
  `DEPENDS_ON` or `PART_OF` link it is a reference that names no recorded component: report
  each one as "dependency on {the reference as you sent it} not recorded: no such component".
  `target` is a node id, so name the reference by matching it against the node ids you hold —
  this call's `knowledge_id`s and the Step 3 browse; when none matches, print "a {edge}
  reference in the draft names no recorded component (target {target})". The link lands later
  only if a component with that key is recorded. On any other kind of link, report its kind
  and target as they came back.
- `graph.edges_pruned` — how many links of these kinds the component had and this call no
  longer names: they were removed. Report it whenever it is above zero, even when every edge
  merged — a link the draft dropped, or one whose reference now names nothing, disappears
  here and nowhere else says so.
- `graph` null — a `not_reproposed` line — or a `graph.status` other than `projected`: the
  links were not written on this call. Say so.

**Duplicates: say whether anyone looked.** Each line's `detection.duplicates.status` says
whether the duplicate check ran. `disabled` — there or in `embedding.duplicate_detection.status`
— is "not checked — duplicate detection is off", never "none". A `detection` with no
`duplicates` entry, or any other status but `complete` or `replayed`, is "not checked —
{status or reason}". Only a `complete` or `replayed` check with an empty list is "none".

**Report the scope the platform used, not the one you asked for.** It is in each
`natural_key`: `component:<projectId>:…` is the project, `component:*:…` is the whole company.

Then, from the top level of the response:

- **`suggested_anchors`** — directories the blueprint mentions that exist in the repository
  and that no live component of this project and repository claims: neither one of this call
  nor one recorded earlier, the `proposed` ones included; a rejected or retired component's
  directory can be suggested again. A platform that checks only the call's own components can
  still suggest a directory an earlier component holds, so when Step 3 found components, read
  each suggested directory as in Step 3.7 — at most 10 reads — and list the ones a live
  component already claims as **already modelled as {title} ({node_id})**, not as leads; any
  past the tenth are printed "not checked against the components already recorded". Leads for
  the person, not records: a rerun can add one to a component if the person says it belongs
  there.
- **`possible_duplicates`** — each with the `component` it concerns. The platform has queued
  each one for a person to decide whether the two are the same thing; report every one, and
  never drop them from the report.
- **`blueprint.scan`** — `complete` means the blueprint's text was checked against the
  repository. `incomplete` or `not_consulted`, with its `reason`, means the suggestions above
  are partial or absent **because the check could not run** — say that, and never report it
  as "no suggestions". `claims_unavailable` is one such reason: the components already
  recorded could not be read, so nothing was suggested at all.
- **`warnings`**, verbatim.

An anchor tier of `UNRESOLVED` means the code graph answered and has no file under that
directory — the directory is gone or was never there. `DEGRADED` means the code graph could
not answer at all; that is a caveat about this run, not a finding about the component.

## Output Structure

```markdown
# Components from the blueprint ({date})

**Status: proposed — awaiting human review.** Nothing below is confirmed architecture.

Recorded for: {project name, or "the whole company" when the natural keys say so} · repository {repository name}

One section like the next per blueprint read.

## {blueprint title} — {node_id} {(proposed, not yet confirmed) when it is}

| Outcome | Component | Natural key | Knowledge id | Directories | Anchors | Duplicates |
|---|---|---|---|---|---|---|
| created | {name} | {natural_key} | {knowledge_id} | {path} verified | EXTRACTED {n} | {n | none | not checked — duplicate detection is off | not checked — {status or reason}} |
| revision_proposed | {name} | {natural_key} | — | {path} verified | EXTRACTED {n} | {as above} — proposal {proposal_id} |
| not_reproposed | {name} | {natural_key} | {knowledge_id} | {path} (stored, not checked) | — | — {reason} |
| refused | {name} | {natural_key} | — | — | — | — {message} |

Also named by: {other blueprint title}, for {component} — sent with the same fields in that blueprint's call too

### Links not recorded
- {component}: dependency on {reference as sent} not recorded: no such component{ — or: a {edge} reference in the draft names no recorded component (target {target})}
- {component}: {n} link(s) it had before were removed by this call (`edges_pruned`)
- {component}: links not written on this call — {graph status | no graph answer}

### Possible duplicates (queued for a person — nothing was merged)
- {component} may be the same as {target node_id} (score {score}, {relationship})
- {when the check did not run: duplicates not checked — {duplicate detection is off | status or reason}}

### Already modelled under another name
- {draft component} — already modelled as {title} ({node_id}, `{natural_key}`, {review state}): {reused under its key | skipped}

### Suggested directories (not recorded — leads only)
- {file_path} — mentioned in the blueprint's {field}: "{mention text}"{ — not checked against the components already recorded, past the tenth}
- {file_path} — already modelled as {title} ({node_id}), not a lead

### Caveats
- Blueprint scan: {complete | incomplete | not_consulted} {reason}
- {each warning, verbatim}

## Yield
- Blueprints read: {n}
- Components named by the blueprints: {n} · sent: {n} · recorded: {n}
- created {n} · updated {n} · unchanged {n} · revision proposals {n} · not re-proposed, a person's verdict {n} · refused {n}
- Directories verified in the ingested tree, on the lines this call wrote: {n} / {n}
- Natural keys used: {list} — a rerun of the same blueprints lands on these

Confirm or reject each component in the review queue — this skill confirms nothing.
```

## Next Steps

- **Confirm or reject** in the platform's review queue. Until a human does, these are
  proposals, and nothing downstream should treat them as the architecture.
- **When a blueprint changes**, run the skill again: each recorded component lands on its
  own record by its key, even after a rename, and one a person rejected or retired stays
  closed — the platform says so on its line.
- **For a decision about one component**, record it through the decision door and name the
  component it concerns.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id` or `repository_id`:** stop
and point the user at `/fairmind-connect`. There is nothing to read from and nowhere to anchor.

**No `mcp__Fairmind__Brain_*` tools in the tool list:** the connected server predates the
**Brain-1** release. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**No `mcp__Fairmind__Brain_record_component` while the other brain tools are there:** the
server predates the release that serves the component door. Say so and stop.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report which components were written, which were not, and the natural keys
used, so the next run resumes there. Never retry in a loop — every attempt is refused and the
refusals are counted against the tenant.

**`{'retryable': True}`:** the quota service or the store was briefly unreachable, or the
tenant is under an erasure that has not finished. One retry is reasonable; a second refusal is
an outage — report it and stop.

**A refusal naming an unset configuration key** (`'key': 'brain_write_quota_daily'`):
provisioning, not a bug in the run. Quote the key name verbatim and stop; someone with
platform access has to set it for this tenant.

**`{'message': 'Brain component recording refused: …'}`:** the draft broke a rule of the door,
and the message names the component and the field — an unknown field, too many directories or
dependencies, a component that names itself, two components with the same key, more than 20
components, or no such blueprint document. Fix that one thing, show the person what changed,
and ask again. Never drop a field silently to get past a refusal. A reference that names no
recorded component is **not** refused: it is accepted and reported on its line under
`graph.edges` (Step 7).

**`{'status': 'error', 'message': 'Access denied: this key is bound to a different project. …'}`:**
the key in use was issued
for a project other than the one this checkout is connected to, or the repository is bound to
another project. Nothing was written. Quote it verbatim — it says where the right key is
issued — and stop; do not retry without `project` to get past it.

**Any other `{'status': 'error', 'message': 'Access denied: …'}`:** the key's holder has no role
that may write to this project. Nothing was written. Quote it and stop.

**"Brain component recording failed":** the platform could not complete the call. Report it
and stop; nothing tells you which components landed, so a rerun is the way to find out — it
converges on whatever did.

**No blueprint in the project:** a real and reportable answer, not a failed search — see
Step 1. Say it and stop.
