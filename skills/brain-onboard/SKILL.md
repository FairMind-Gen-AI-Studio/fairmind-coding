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
catalog id and, for each file you look into, a one-line question naming its **repo-relative
path** — `mcp__Fairmind__Brain_overview` and `mcp__Fairmind__Brain_ask` are reads. Generation
mode also calls `mcp__Fairmind__General_list_projects`, which sends nothing beyond the call
itself, to learn the project's name for the brief's title; and it writes:
`mcp__Fairmind__Brain_tell` sends the brief's **whole body** — prose you wrote from what the
brain returned, with node ids cited in it — plus a title, a summary, the repository and an
agent label, for one project or, with `project` set to `*` and a key that names no project,
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
3. Confirm these tools are in the tool list, **by name**: `mcp__Fairmind__Brain_overview` for
   both modes, `mcp__Fairmind__Brain_ask` for reading mode's per-file step, and, for
   generation mode, `mcp__Fairmind__Brain_tell`. They arrive with a later platform release than
   the earlier brain tools, so a connected server can offer some and not others. If the
   overview is absent, say so and **stop** — do not rebuild the brief out of searches: that
   answer is unreviewed, unfiltered and not the brief anybody confirmed. Without `Brain_ask`,
   say the per-file half is unavailable and go on with the brief. Without `Brain_tell`, say
   generation mode cannot write, and stop.
4. **The scope is what the platform resolves, not what you pass.** `project` is the
   `project_id` from step 1, left out only for the company brief. On a **read** the platform
   takes the project from the key first: a key minted for one project answers for that project
   whatever `project` says, and even when it is left out. So read `scope` (`project` or
   `company`) and `project_id` off every overview response, and name what you actually read.
   A **write** is stricter: the platform refuses a `project` that names another project than
   the key's, and refuses a company brief written with a key minted for a project. Only a key
   that names no project writes the company brief. To READ the company brief with any key, pass
   `scope="company"`: a key issued for a project is then served it only once a person
   confirmed it, with no pending revision. A platform whose overview has no `scope` parameter
   refuses the argument; then the company brief — once a person confirmed it — is one of that
   project's `key_documents`, and R1 says how to find it.
5. **Choose the mode.** Reading mode, unless a person asked for a brief to be written or
   regenerated.

## Reading mode

### R1 · The brief, in one call

```
mcp__Fairmind__Brain_overview(
    project=<project_id>,             # left out for the company brief
    scope="company",                  # only for the company brief; left out for a project's
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
- `truncated: true` — `content` is the beginning of a longer brief. Read the whole page with
  one more `mcp__Fairmind__Brain_overview` call, `detail_level="brief"` — it returns the brief
  whole, never cut, and no sections — only when the question needs it.

**Asked for the company brief, answered for a project.** `scope` came back `project` although
you passed `scope="company"`, or the call was refused for the `scope` argument: the platform
predates the selector, and the key was minted for a project, so the brief you hold is that
project's — say so. The company brief,
once a person confirmed it, is a document among that project's `key_documents`, but a
`minimal` read lists three items per section and says nothing of their kind. Read the overview
again at `detail_level="standard"`, take the `key_documents` items whose `subtype` is `brief`
— a title tells the company's apart from a copy someone filed — and ask about the one you want
with `mcp__Fairmind__Brain_ask`, `question_type="precedent"`, its title as the `question`. If
none is listed and `omitted` is zero, this key sees no confirmed company brief; say that.

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

An item whose summary is not enough to say what it is, you ask about with
`mcp__Fairmind__Brain_ask`, `question_type="precedent"`, its title as the `question` and
`project` set — at most five such questions in a session.

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
mcp__Fairmind__Brain_ask(
    project=<project_id>,
    question_type="file",
    question=<one line naming the path and the task>,
    subject={"file": {"repository": <repository_id>, "path": <repo-relative path>}},
)
```

The platform refuses a file question without a repository: when no `repository_id` is bound,
say the per-file half is unavailable until `/fairmind-connect` binds one, and stop there. Once
per file, not once per edit, and at most ten files in a session — every call sends that file's
path, and a file past the tenth is named as "not read". After an answer with
`reason: cap_reached`, send no further `Brain_ask` in this session. Read the answer in this
order:

1. **`composed: false`.** The brain listed what it found without composing an answer: say
   "listed, not composed", with the `reason`. For `nothing_found`, quote the `coverage`
   sentence: it says "could not resolve this", never "nothing is known".
2. **The considerations.** Each is a sentence the brain composed with the references behind
   it. One whose `confirmed` is false is cited **(proposed, not yet confirmed)**, never bare.
   A consideration is said to cite knowledge recorded **at a file** only when it comes from a
   `file` question and one of its `file` references has the bound repository and that path.
   On any other question a `file` reference carries the catalog's own spelling of the
   repository: quote its path as a pointer and make no claim about where anything was recorded.
3. **`further_reading`** — records found and not cited — is listed as "found, not cited".

How to read what `Brain_ask` answers, for this skill, which only reads:

| Answer | What to do |
|---|---|
| `configuration_unset` or `disabled` (with a `key`) | quote the key and stop the brain step |
| `configuration_invalid` (with a `key`) | quote the key and stop |
| `retryable: True` (`retrieval_failed`, `catalog_unavailable`) | retry once, then say the brain could not be read — never "nothing is known" |
| access denied | say this key cannot read the brain |
| `project_unresolved` | route the person to `/fairmind-connect` |
| `invalid_parameter` (with a `parameter`) | fix that parameter once and ask again, then name it and stop |
| `composed: false` | say "listed, not composed", with the `reason`; for `nothing_found`, quote the `coverage` |
| `throttled` | stop, and never retry |

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
to it as the company's, so `repository` is left out for it here too. Generation reads without
`scope`: the scope the platform answers tells whether this key may write the company brief at
all. Then check, in order:

1. **The scope.** Asked for the company brief and `scope` is `project`: the key was minted for
   a project, and the platform refuses a company brief written with it. Say so and stop.
2. **The project.** `scope` is `project` and the returned `project_id` is not the checkout's
   `project_id` (compare them ignoring case): the key was minted for another project than the
   one this checkout is connected to. The sections you just read are that other project's,
   and the platform refuses a write that names this one. Name both projects, point at
   `/fairmind-connect` or at the key issued for this project, and stop.
3. **A failed read is not a fact.** When the brief itself (`brief.state` `unavailable`) or a
   section it rests on — `decisions`, `components` or `conventions` — is `unavailable`, or a
   caveat says the repository catalog could not be read (then `repositories` holds only the
   repository you named and `conventions` may miss some, while both still read `ok`): retry the
   read once; if it persists, stop. Any other section `unavailable` — `evidence.degraded` is
   then true, and that alone is no reason to stop — keeps its heading with the one line "Not
   read: the store was unavailable when this brief was written.", never its `absence` as a
   fact, and is listed in the output as not read. A brief written over a failed read of what
   it rests on records an outage as a fact about the project, in a page a person is about to
   confirm.
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

Ask about a record with `mcp__Fairmind__Brain_ask`, `question_type="precedent"`, its title as
the `question`, only when its summary is not enough to say what it is — at most five such
questions in a run. Do not widen a section with a question: the sections are already held to
current, confirmed records, and an answer is considerations a model composed, not a list —
"current decisions" would quietly absorb what the brain only mentions. A brief states what
the sections hold and nothing else.

### G2 · What the write will be compared against

| `brief.state` | What the write does | The diff you show |
|---|---|---|
| `absent` | The first version — or, when `absence` says the last one was rejected or archived, its replacement; it lands `proposed` in the review queue | None — say it is the first version, or that it replaces a rejected one |
| `proposed` | Rewrites that draft in place; still `proposed` | Against the stored text, labelled **against an unconfirmed draft** — nobody confirmed what it replaces |
| `confirmed` | Files a revision proposal; the confirmed text keeps being served until a person accepts the new one | Against the whole confirmed text |

**The diff is against the whole text, never the served cut.** `brief.content` is the whole
brief only when `truncated` is false. When it is true, read the whole page with one more
`mcp__Fairmind__Brain_overview` call, `detail_level="brief"`, with the same `project`,
`scope` and `repository`: it returns the brief whole, never cut, and no sections. A diff
against the cut shows the whole tail as deleted.

`pending_revision` set means a regeneration is already waiting in the review queue since its
`created_at`. Say so, and ask before filing another: two proposals against one brief leave the
reviewer to guess which one is meant. Filing one supersedes the older pending revisions of this
brief; the review queue keeps only the newest.

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
- **Paths and addresses in words.** In the content, the title and the summary, write a path
  repo-relative and describe a local address in words rather than writing it out. A precaution:
  the hosting edge in front of the platform refuses some text patterns before they reach it, and
  its rules can change.
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
   confirms or rejects in the review queue. The review queue shows the old and the new body, title
   and summary side by side.
3. **Wait for a yes.** Being asked to write the brief is the yes for the brief, not for its
   scope: a company brief is asked about by name.

### G5 · Write it

```
mcp__Fairmind__Brain_tell(
    intent="document",
    brief=True,
    content=<the brief, exactly as shown, at most 200000 characters>,
    title=<one line: the project's name from G1 (or its project_id) and that this is its brief>,
    summary=<one or two sentences: what the project is, at most 1000 characters>,
    repository=<the bound repository_id, else the repository's name>,
    project=<the project_id Brain_overview returned; "*" only when its scope was company>,
    agent=<which agent you are>,
)
```

Nothing else. A link or an uploaded file's id instead of `content` files an ordinary document
that appears under the key documents and never becomes the brief. Node ids are cited inside
the content. And `project` is the `project_id` the overview returned — after G1, the
checkout's own — never a name: the brief's identity is derived from it on both sides, so a
different spelling of the same project reads a brief the write never made.

### G6 · Read what comes back

Report the `state` as the answer gives it, never as you expect it, with the `node_id`. What
it means depends on what G2 read:

- `proposed` — **the first version** (G2 said `absent`), now in the review queue; **the
  unconfirmed draft rewritten in place** (G2 said `proposed`), still `proposed`; or **a
  revision filed against the confirmed brief** (G2 said `confirmed`), which was left untouched
  and **keeps being served** until a person accepts the proposal. The last is not a failure and
  it is not done either. Sending the same text again changes nothing. Do not retry, and do not
  route around it.
- `duplicate` — identical to what is stored; nothing changed.
- A refusal (`{'status': 'refused', 'reason': ..., 'message': ...}`) — see Error Handling.
  None of them is retried by repeating the call unchanged. One that names a field of the
  report's **shape** means the plugin and the brain disagree on the report's shape: say "the
  plugin and the brain disagree on the report's shape — update the plugin", and do not send it
  again.
- Anything else — an answer neither this list nor Error Handling names — means nothing was
  written. Quote the `message` or `reason` verbatim and stop. Never route around it by filing
  the brief as a link or a file: bringing back what a person rejected is that person's act.

**Never send a status, a `retract` or a `same_as` to confirm anything**, and never present the
brief as confirmed: what this mode writes is `proposed`, and a person confirms it in the
review queue.

## Output Structure

Reading mode:

```markdown
# {Project | Company} brief — read {date}

Scope read: {scope} {project_id}
Brief: {confirmed on {valid_at} | proposed, not yet confirmed | none yet: {absence}}
{A regenerated version is waiting in review since {created_at}; this is the confirmed one.}

{brief content; "(continues: Brain_overview, detail_level brief)" when truncated}

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
- Outcome: {first version | draft rewritten in place | revision proposed against the
  confirmed brief | unchanged}
- Compared against: {nothing (first version) | the confirmed text | an unconfirmed draft}
- Sections carried as absences: {section: absence, for each}
- Sections not read: {section, for each | none}
- Project name: {name, from the project list | not found — the project_id stands in its place}
```

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id`:** stop and point the user
at `/fairmind-connect`.

**`mcp__Fairmind__Brain_overview` not in the tool list:** the connected platform predates the
brief. Say so and stop; the other `mcp__Fairmind__Brain_*` tools are unaffected, and
`mcp__Fairmind__Brain_ask` still answers for a single file when the developer asks.

**`mcp__Fairmind__Brain_tell` not in the tool list:** generation mode cannot write. Stop; do
not record the brief through any other door.

**An MCP error result on `Brain_tell`:** the brain may not offer `Brain_tell` yet, or the
plugin and the brain disagree on the report's shape. Say so and stop; sending it again
changes nothing.

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

**A failed write whose error is an HTML page with `403` and "Request blocked":** the hosting
edge in front of the platform stopped the request before it reached the brain. Nothing was
written, and it is not an authentication failure. The edge refuses some text patterns and its
rules can change, so look in the content, the title and the summary for a relative path or a
local address written out, put it in words, show the corrected brief, and send once more.
Rewording is legitimate here because the brief is text the agent wrote, not content passed on
verbatim from somewhere else. An HTML page with another status (a `502`, `503` or `504`), or a
plain-text `upstream connect error`, is a passing failure, not a refusal: the brief is not
changed, and it gets one retry.

## What this skill does not do

- **It confirms nothing.** A person confirms the brief in the review queue.
- **It keeps no copy in the repository.** The brain holds the brief; a second copy drifts from
  it the day it is confirmed.
- **It does not replace `mcp__Fairmind__Brain_ask` on a file.** The brief says what the
  project is; the per-file read says why one place is the way it is.
