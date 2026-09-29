---
name: brain-onboard
description: Use at the start of a session on a project you do not know yet, or when someone is being onboarded onto one - it reads the project's brief from the company brain in one call (what the project is, its current decisions, its conventions, its known traps, its components, where to look) and then the "why" behind each file you open. Also use when asked to "write the project brief", "regenerate the brief" or "write the company brief" - generation mode assembles the brief from what the brain holds, shows what changed since the confirmed version, and records it as a proposal a person confirms. Also use for "brief me on this project" or "onboard me".
---

# Onboard from the project brief

## Overview

A session that knows nothing about a project asks the same questions first — what is this,
what did we decide, what must I not break, where do I look — and without a brief it answers
them with a dozen searches, differently every time. The brief is the one page that answers
them: one per project, one for the whole company, stored in the brain, confirmed by a person.

This skill has two modes:

- **Reading mode** — the default, and read-only. One call returns the brief together with the
  live records it is built from; after that, the "why" of each file the developer opens.
- **Generation mode** — writes the brief. It reads what the brain holds, **you** write the
  prose, and it is recorded as a **proposal** a person confirms in the platform's review
  queue. A regeneration shows what changed against the version a person confirmed.

**The platform runs no model for any of this.** The brief is stored prose, the sections
beside it are fixed reads of confirmed knowledge, and the only interpreter is the agent
running this skill. Nothing is generated when somebody reads the brief.

**Announce at start:** "I'm using the brain-onboard skill to read the brief of <project>" —
or, in generation mode, "to write the brief of <project> as a proposal".

**What leaves your machine.** Reading mode sends queries only: the project, the repository's
catalog id and, for each file you look into, its **repo-relative path** —
`mcp__Fairmind__Brain_overview` and `mcp__Fairmind__Brain_context` are reads. Generation mode
also calls `mcp__Fairmind__General_list_projects`, which sends nothing beyond the call itself,
to learn the project's name for the brief's title; and it writes:
`mcp__Fairmind__Brain_add_document` sends the brief's **whole body** — prose you
wrote from what the brain returned, with node ids cited in it — plus a title, a summary and an
agent label, for one project or, with the project left out and a key that names no project,
for the whole company. A brief is **one page per scope**: writing it again rewrites a draft
nobody has confirmed yet, and against a brief a person confirmed it files a **revision
proposal** that waits for a person. This is the **brain write-back**, and the plugin README's
*What leaves your machine* describes it in full under that name — alongside every other route
by which anything leaves.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the
   `fairmind` field set to `"configured"` and the `project_id` that `/fairmind-connect` writes.
   `repository_id` is optional for the brief and required for reading mode's per-file step.
   **Never write those keys yourself**, and never set `fairmind` to `"configured"` on their
   behalf.
2. If the file is missing, `fairmind` is `"none"`, or `project_id` is absent: **stop** and tell
   the user to run `/fairmind-connect` once for this checkout. There is no standalone version:
   with no brain there is no brief, and a summary of the repository written from the local
   files is not one.
3. Confirm `mcp__Fairmind__Brain_overview` is in the tool list **by name**. It arrives with a
   later platform release than the other `mcp__Fairmind__Brain_*` tools, so a connected server
   can offer those and not this one. For generation mode, confirm
   `mcp__Fairmind__Brain_add_document` the same way. If the one you need is absent, say which
   and **stop** — do not rebuild the brief out of `mcp__Fairmind__Brain_search` calls: that
   answer is unreviewed, unfiltered and not the brief anybody confirmed.
4. **The scope is what the platform resolves, not what you pass.** `project` is the
   `project_id` from step 1, left out only for the company brief. On a **read** the platform
   takes the project from the key first: a key minted for one project answers for that project
   whatever `project` says, and even when it is left out. So read `scope` (`project` or
   `company`) and `project_id` off every overview response, and name what you actually read.
   A **write** is stricter: the platform refuses a `project` that names another project than
   the key's, and refuses a company brief written with a key minted for a project. Only a key
   that names no project writes the company brief or reads it through the overview. With a
   project's key, the company brief — once a person confirmed it — is one of that project's
   `key_documents`; R1 says how to find it.
5. **Choose the mode.** Reading mode, unless a person asked for a brief to be written or
   regenerated.

## Reading mode

### R1 · The brief, in one call

```
mcp__Fairmind__Brain_overview(
    project=<project_id>,             # left out only for the company brief
    repository=<repository_id>,       # when one is bound; left out for the company brief
    detail_level="minimal",
)
```

Leave `repository` out of the company brief's read as well. At company scope a named
repository is listed as if it were the company's, and the conventions section then answers
for that one repository instead of saying whether the company as a whole has any.

At `minimal` the whole answer — brief included — is held to about two thousand tokens, which is
why this is the one call a session starts with. It returns `brief`, nine `sections` and an
`evidence` envelope. Read `brief.state` first:

| `brief.state` | What you tell the developer |
|---|---|
| `confirmed` | The brief, as confirmed knowledge, and `valid_at` — when it was confirmed |
| `proposed` | The brief, flagged **(proposed, not yet confirmed)**: nobody has reviewed it, so it is a draft to read with care |
| `absent` | That no brief exists yet — quote its `absence` verbatim — then the sections, which are what one would be built from. Offer generation mode; never start it unasked |
| `unavailable` | Its `absence` verbatim, then the sections, which were read separately |

The flag is not optional: an unreviewed page presented as the project's own summary is the
worst way to onboard somebody, so it travels with every quote of a `proposed` brief.

- `pending_revision` set on a confirmed brief — a regenerated version is waiting in the review
  queue. Say so; what you read is still the confirmed one.
- `truncated: true` — `content` is the beginning of a longer brief. Read the rest with
  `mcp__Fairmind__Brain_get(knowledge_id=<brief.node_id>)` only when the question needs it.

**Asked for the company brief, answered for a project.** `scope` came back `project`: the key
was minted for a project, so the brief you hold is that project's — say so. The company brief,
once a person confirmed it, is a document among that project's `key_documents`, but a
`minimal` read lists three items per section and says nothing of their kind. Read the overview
again at `detail_level="standard"`, take the `key_documents` items whose `subtype` is `brief`
— a title tells the company's apart from a copy someone filed — and open the one you want
with `mcp__Fairmind__Brain_get(knowledge_id=<its node_id>)`. If none is listed and `omitted`
is zero, this key sees no confirmed company brief; say that.

### R2 · The sections beside it

All nine, always, in this order: `purpose`, `components`, `decisions`, `conventions`,
`known_traps`, `incidents`, `retired_features`, `repositories`, `key_documents`. Each is
`{state, items, omitted, absence}`:

- `ok` — list the items by title with their `node_id` (a repository by `name` and `repo_ref`),
  plus `status`, `reason` on a retired feature, `repository` on a convention. `omitted` above
  zero is "and N more".
- `empty`, `not_built` or `unavailable` — print the section anyway, with its `absence`
  verbatim. **A section is never left out:** an empty section with nothing beside it reads as
  "the project has none", which is the misreading this mode exists to prevent.

`key_documents` never lists this scope's own brief: that one is `brief`, above. A brief listed
there is a document like the others — at project scope the company's, once confirmed, or a
brief someone filed as a link or an uploaded file — and never the brief of the scope you read.

The sections list confirmed knowledge only and are read live, while the brief was written at
some earlier point. Where the two disagree — a decision the brief cites is no longer listed, a
component it never mentions is — say so: that is drift, and it is information.

`evidence.degraded` true is the one failure signal: `evidence.unreadable_planes` names what
could not be read. Say so, and do not present what came back as complete. `evidence.caveats`
is not a failure — a healthy read carries caveats too, such as a thin record or a cut made to
fit the budget — so pass them on as caveats.

### R3 · The why of each file

When the developer opens a file, and before you change code you did not write:

```
mcp__Fairmind__Brain_context(
    project=<project_id>,
    repository=<repository_id>,
    file_path=<repo-relative path>,
    detail_level="minimal",
)
```

The platform refuses a file read without `repository`: when no `repository_id` is bound, say
the per-file half is unavailable until `/fairmind-connect` binds one, and stop there. Once per
file, not once per edit — every call sends that file's path. Read the answer in this order:

1. **`unresolved`.** An entry for the file means the file itself is not resolved in the code
   index, whatever `items` holds: `no_rows` — the index has no such file in this repository (a
   file newer than the last ingestion, or on another branch); `repository_not_ingested` — the
   repository is not in the index at all; `graph_unavailable` — the index could not be asked.
   Say so first, with the reason.
2. **`anchor_confidence` on each item** splits the items in two. `EXTRACTED`, `INFERRED`,
   `AMBIGUOUS` and `UNRESOLVED` are **recorded at this file** — an anchor of the item names it —
   and they are the file's why. `SEMANTIC` is **related by meaning, not recorded at this
   file**: found by similarity of text, anchored somewhere else or nowhere. List those apart,
   under that label, and never present them as the reason the file is the way it is. When every
   item is `SEMANTIC`, nothing is recorded at this file; say that in those words.
3. **`components.containing`** — the architecture component whose directory holds the file,
   deepest first. It is the file's owner even when every item is only related by meaning, so
   name it: its title, `node_id` and `review_state`. An empty list, with the block present,
   means no component covers the file's directory.
4. **`review_state`** on every item and component — cite a `proposed` one as
   **(proposed, not yet confirmed)**.

An empty `items` with a caveat means "could not resolve this", never "nothing is known".

## Generation mode

It runs **only when a person asked** for a brief to be written or regenerated. The company
brief — `project` left out, visible to the **whole company** — is written only when that is
what they asked for by name. Reading mode that finds no brief offers this mode; it never
starts it.

### G1 · Read what the brief is built from

```
mcp__Fairmind__Brain_overview(
    project=<project_id>,             # left out for the company brief
    repository=<repository_id>,       # when one is bound; left out for the company brief
    detail_level="standard",
)
```

`standard` carries up to twenty items per section with their summaries. A company brief read
with a repository would store that repository as one of the company's, and conventions scoped
to it as the company's, so `repository` is left out for it here too. Then check, in order:

1. **The scope.** Asked for the company brief and `scope` is `project`: the key was minted for
   a project, and the platform refuses a company brief written with it. Say so and stop.
2. **The project.** `scope` is `project` and the returned `project_id` is not the checkout's
   `project_id` (compare them ignoring case): the key was minted for another project than the
   one this checkout is connected to. The sections you just read are that other project's,
   and the platform refuses a write that names this one. Name both projects, point at
   `/fairmind-connect` or at the key issued for this project, and stop.
3. **A failed read is not a fact.** Any section `unavailable`, `brief.state` `unavailable`,
   `evidence.degraded` true, or a caveat saying the repository catalog could not be read — then
   `repositories` holds only the repository you named and `conventions` may miss some, while
   both still read `ok`: retry the read once; if it persists, stop. Reading mode passes that
   caveat on; here it is a failure, because a brief written over a failed read records an
   outage as a fact about the project, in a page a person is about to confirm.
4. **An absence is a fact.** A section `empty` or `not_built` is a durable answer: carry its
   `absence` verbatim into the brief under that section's heading. The company brief's
   conventions, for one, read empty until conventions are recorded for the company as a whole,
   and the brief says so rather than leaving the heading out.

**The project's name.** The overview returns the project's id and no name, and the brief's
heading and title need one. Call `mcp__Fairmind__General_list_projects` once and take the
`name` of the row whose `id` equals the `project_id` the overview returned, compared ignoring
case. When no row matches, or the answer is not a list, write the `project_id` where the name
goes and say so in the output — never a name taken from the checkout's directory or its
`active-context.json`, which names a repository, not the project. The company brief needs no
name.

Open a record with `mcp__Fairmind__Brain_get(knowledge_id=<node_id>)` only when its summary is
not enough to say what it is. Do not widen a section with `mcp__Fairmind__Brain_search`: the
sections are already held to current, confirmed records, and a search returns retired,
superseded and unreviewed ones unless told otherwise — "current decisions" would quietly
absorb retired ones. If a search is genuinely needed, pass `review_state: "confirmed"`, drop
every result whose `status` is `retired` or `superseded`, and cite only what survives.

### G2 · What the write will be compared against

| `brief.state` | What the write does | The diff you show |
|---|---|---|
| `absent` | The first version — or, when `absence` says the last one was rejected or archived, its replacement; it lands `proposed` in the review queue | None — say it is the first version, or that it replaces a rejected one |
| `proposed` | Rewrites that draft in place; still `proposed` | Against the stored text, labelled **against an unconfirmed draft** — nobody confirmed what it replaces |
| `confirmed` | Files a revision proposal; the confirmed text keeps being served until a person accepts the new one | Against the whole confirmed text |

**The diff is against the whole text, never the served cut.** `brief.content` is the whole
brief only when `truncated` is false. When it is true, read the stored text with
`mcp__Fairmind__Brain_get(knowledge_id=<brief.node_id>, max_tokens=8000)` and walk it by chunk
index. `truncated` alone cannot drive the walk: the platform sets it both when chunks remain
after the answer and when a single chunk was cut to fit `max_tokens`, and a `section` past the
last chunk is clamped to the last chunk rather than refused — so a loop on `truncated` can drop
a chunk's tail, or never end. Instead:

1. Each answer holds the chunks `chunk_range.from` to `chunk_range.to`, of `chunks_available`.
   When it holds more than one, every one of them is whole: keep them, and continue from
   `chunk_range.to + 1`.
2. When it holds exactly one chunk and says `truncated`, that chunk may have been cut. Read it
   alone — `section={"chunk_index": <that index>}` — with `max_tokens` doubled, up to the
   platform's ceiling of 100000, until the answer is not `truncated`; that text is the whole
   chunk. Still `truncated` at the ceiling: stop and say the brief could not be read whole —
   a diff against part of it is not shown.
3. Stop once chunk `chunks_available - 1` is in hand, whatever `truncated` says. The walk
   never asks for a chunk index past that one.

Join the chunks in index order with nothing between them. A diff against the cut shows the
whole tail as deleted.

`pending_revision` set means a regeneration is already waiting in the review queue since its
`created_at`. Say so, and ask before filing another: two proposals against one brief leave the
reviewer to guess which one is meant.

### G3 · Write the brief

- **Only what the overview returned.** Every record is cited by title and `node_id`, and every
  node id is one the overview gave you — never one remembered from an earlier session or
  inferred from a title. A gap is a gap: write the absence, not a plausible filler.
- **Lead first.** The first paragraph says what the project is and what a newcomer must not
  break. A `minimal` read serves the brief cut to its first 600 tokens, and to 250 when the
  bundle is tight, so that paragraph may be all a session sees.
- **One heading per section, in the overview's order.** A section in state `empty` or
  `not_built` keeps its heading and one line: its `absence`, verbatim.
- **A brief among the key documents is cited, not copied.** The company's brief, or one
  somebody filed, is listed under Key documents by title and `node_id` like any document; its
  text belongs to its own scope and stays out of this brief.
- **The records' own words.** No evaluation ("a robust service"), nothing the records do not
  say, no reading of why they exist.
- **Keep `...`, `../` and `<script>` out** of the content, the title and the summary. The
  platform's edge answers any of them with a bare 403 and nothing is written — model prose
  reaches for an ellipsis often. Write a range as "to", and paths repo-relative.
- **Size.** About six thousand tokens at most, the most a `standard` read serves back.

```markdown
# {Project name, from G1 — or the project_id when no name was found} — project brief

{What this project is, who it serves, and the one or two things a newcomer must not break,
citing the node ids they rest on.}

## Purpose
- {title} ({node_id}): {one line, in the record's own words}

## Components
- {title} ({node_id}): {what it is responsible for}

## Current decisions
- {title} ({node_id}): {what was decided}

## Conventions
- {title} ({node_id}): applies to {repository}

## Known traps
- {title} ({node_id}): {status}

## Incidents
- {title} ({node_id}): {status}

## Retired features, and why
- {title} ({node_id}): retired because {reason, as recorded}

## Where to look
### Repositories
- {name} ({repo_ref})
### Key documents
- {title} ({node_id})
```

Under any heading whose section had nothing to show, the only line is that section's
`absence`, verbatim.

### G4 · Show the diff, and ask

1. For a regeneration, write the version you are compared against and the new brief to two
   files in a fresh temporary directory outside the repository (`mktemp -d`), and show the
   output of `diff -u` over them. Show the diff as computed — a prose summary of a diff is the
   drift this step exists to expose, retold by the party that caused it.
2. Then show: the title, the summary, **where it will be visible** — the named project, or the
   whole company — that the **whole brief** travels, and that it lands as a proposal a person
   confirms or rejects in the review queue. The review queue shows the old and the new body and
   title side by side; a change to `summary` appears only here.
3. **Wait for a yes.** Being asked to write the brief is the yes for the brief, not for its
   scope: a company brief is asked about by name.

### G5 · Write it

```
mcp__Fairmind__Brain_add_document(
    project=<the project_id Brain_overview returned; left out only when its scope was company>,
    doc_kind="brief",
    title=<one line: the project's name from G1 (or its project_id) and that this is its brief>,
    content=<the brief, exactly as shown>,
    summary=<one or two sentences: what the project is>,
    agent=<which agent you are>,
)
```

Nothing else. A link or an uploaded file's id instead of `content` files an ordinary document
keyed by that link, which appears under the key documents and never becomes the brief. Node
ids are cited inside the content, not passed as evidence. And `project` is the `project_id`
the overview returned — after G1, the checkout's own — never a name: the brief's identity is
derived from it on both sides, so a different spelling of the same project reads a brief the
write never made.

### G6 · Read what comes back

- `{'status': 'ok', 'created': True}` — the first version, `proposed`, now in the review queue.
- `{'status': 'ok', 'created': False, 'updated': True}` — the unconfirmed draft was rewritten
  in place and is still `proposed`.
- `{'status': 'ok', 'updated': False}` — identical to what is stored; nothing changed.
- `{'status': 'revision_proposed', 'proposal_id': <id>, 'target': <node_id>}` — the confirmed
  brief was left untouched and **keeps being served** until a person accepts the proposal.
  Report the `proposal_id`: it is not a failure and it is not done either. Sending the same text
  again returns the same `proposal_id` rather than a second proposal. Do not retry, and do not
  route around it.
- A refusal — see Error Handling. None of them is retried by repeating the call unchanged.
- Anything else — a `status` neither this list nor Error Handling names, or a `reason` saying
  a person rejected or archived the brief — means nothing was written. Quote the `reason`
  verbatim and stop. Never route around it by filing the brief as a link or a file: bringing
  back what a person rejected is that person's act.

**A write that stores the text also answers `suggested_links` and `suggested_anchors`** — a
first version, a draft rewritten in place, an unchanged resend. The platform scans the stored
text for mentions — a tracker key, a path-like fragment — and proposes a
link or an anchor for each, each with a `review_state` of `pending`. They are pattern matches,
not reading: a key document's title cited in the brief can come back as a fragment that is not
a path at all. Report them, one line each with the text that matched, and tell the person that
confirming the brief will offer them in the review queue, to leave unselected unless they are
right. Never act on one from here.

**Never call `mcp__Fairmind__Brain_set_status`** to confirm the brief, and never present it as
confirmed: what this mode writes is `proposed`, and a person confirms it in the review queue.

## Output Structure

Reading mode:

```markdown
# {Project | Company} brief — read {date}

Scope read: {scope} {project_id}
Brief: {confirmed on {valid_at} | proposed, not yet confirmed | none yet: {absence}}
{A regenerated version is waiting in review since {created_at}; this is the confirmed one.}

{brief content; "(continues: Brain_get {node_id})" when truncated}

## What the brain holds now
- Purpose: {title} ({node_id}); {and N more} | {absence}
- Components: {items} | {absence}
- Current decisions: {items} | {absence}
- Conventions: {items} | {absence}
- Known traps: {items} | {absence}
- Incidents: {items} | {absence}
- Retired features: {title} ({node_id}), retired because {reason} | {absence}
- Repositories: {name} ({repo_ref}) | {absence}
- Key documents: {items} | {absence}

Drift between the brief and the records: {none seen | what differs}
Could not be read: {evidence.unreadable_planes, or nothing}
Caveats: {evidence.caveats, or none}
```

Generation mode:

```markdown
# {Project | Company} brief — written {date}

**Status: proposed — awaiting human review.** Nothing below is confirmed until a person
confirms it in the review queue.

- Scope written: {scope} {project_id}
- Brief node: {node_id}
- Outcome: {first version | draft rewritten in place | revision proposed: {proposal_id} |
  unchanged}
- Compared against: {nothing (first version) | the confirmed text | an unconfirmed draft}
- Sections carried as absences: {section: absence, for each}
- Project name: {name, from the project list | not found — the project_id stands in its place}
- Suggestions the platform attached ({n} links, {n} anchors — leave them unselected in the
  review queue unless they are right): {each, with the text that matched | none}
```

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id`:** stop and point the user
at `/fairmind-connect`.

**`mcp__Fairmind__Brain_overview` not in the tool list:** the connected platform predates the
brief. Say so and stop; the other `mcp__Fairmind__Brain_*` tools are unaffected, and
`mcp__Fairmind__Brain_context` still answers for a single file when the developer asks.

**`mcp__Fairmind__Brain_add_document` not in the tool list:** generation mode cannot write.
Stop; do not record the brief through any other door.

**`{'message': ...}` from the overview:** a read the key is not allowed, or `Brain overview
read failed` when the platform could not answer. Quote it. The second is worth one retry; the
first is not.

**`{'status': 'throttled', ...}` from the write:** the tenant's daily brain-write quota is
spent. **Stop immediately.** The brief was not written; say so, and keep the text you showed so
the next run can send it. Never retry in a loop — every attempt is refused and the refusals are
counted against the tenant.

**`{'retryable': True}`:** the write did not land — the quota could not be read, the row did
not persist, another write reached it first, or a revision proposal failed to record. One
retry is reasonable; a second refusal is an outage — report it and stop.

**`{'status': 'error', 'message': ...}` from the write:** refused before anything was stored.
Two refusals of the scope are the ones this skill can meet: the key is bound to a different
project than the `project` passed, or a key issued for a project was used to write the
company-wide brief. Quote the message and stop, with no retry. The fix is the key —
`/fairmind-connect`, or the key issued for the scope you meant — never a different `project`.

**A refusal naming `'*'`:** `*` is the company brief's own slot, not a project. Leave `project`
out, with a key that names no project, to write the company brief.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote the
key name verbatim and stop.

**A bare 403 with no body on the write:** one of the substrings the platform's edge refuses got
into the content, the title or the summary. Find it, take it out, show the corrected brief, and
send once more.

## What this skill does not do

- **It confirms nothing.** A person confirms the brief in the review queue.
- **It keeps no copy in the repository.** The brain holds the brief; a second copy drifts from
  it the day it is confirmed.
- **It does not replace `mcp__Fairmind__Brain_context` on a file.** The brief says what the
  project is; the per-file read says why one place is the way it is.
