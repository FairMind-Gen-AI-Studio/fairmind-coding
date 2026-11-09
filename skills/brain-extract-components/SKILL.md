---
name: brain-extract-components
description: Use only when a person asks to turn a blueprint into the architecture model the company brain can reason with - it reads the blueprint from the checkout or from a link the person gives, proposes the components it names (name, responsibility, layer, technology and the directories that implement each) together with what each belongs to and depends on, shows the draft and waits for a yes, then tells the brain one component at a time, with the blueprint as the document each rests on, as proposals a human confirms. Also use when asked to "extract the components from the blueprint", "build the architecture model", or "which components does our design name".
---

# Components from a blueprint

## Overview

A component is a named part of the architecture — what the blueprint calls "the billing
service" or "the ingestion pipeline" — tied to the directories that implement it. Once a
person confirms one, the brain can answer what a change to those directories touches, and a
rule like "the API layer never talks to the database directly" has somewhere to attach.

**The blueprint is the only source this skill reads.** It does not guess an architecture from
the directory tree, a README or the code: a component invented that way is a claim nobody
made, and it would arrive looking exactly like one a blueprint states. A run with no
blueprint, in the checkout or at a link the person gives, gets a clear answer and a stop, not
a guess. The blueprint's text is read from the checkout or from the link and never from the
brain.

Everything this skill tells lands as a proposal. A human confirms.

**Announce at start:** "I'm using the brain-extract-components skill to propose the components
the blueprint names, for a person to confirm."

**Dependencies:** requires a connected Fairmind workspace — the same resolution chain
`fairmind-context` uses. The call shapes, the answers and a worked example live in
`references/playbook.md`.

**What leaves your machine when this skill works.**
`mcp__Fairmind__Brain_tell` sends, for each component: its name, the responsibility the agent
wrote from the blueprint (free prose, with its layer, technology, owning team, what it belongs
to and what it depends on, in words), the repo-relative **directory paths** that implement it,
an agent label, this session's Claude Code session id, and the title, link and summary of the
blueprint it rests on — to the Fairmind platform your project's MCP entry points at; the
blueprint is first told once as a document of its own (its title, a summary the agent wrote,
its link, and for a checkout file the blob id of the file; its text is not sent).
`mcp__Fairmind__Brain_overview` and `mcp__Fairmind__Brain_ask` send the project id and, for
each directory the draft names, a question naming the **directory path** and the component.
It confirms nothing. This is the **brain write-back**, and the plugin README's *What leaves
your machine* describes it in full under that name — alongside every other route by which
anything leaves.

## When to Use

Use this skill when a person asks for it:

- A blueprint or architecture document exists, in the checkout or at a link, and the
  components it names should become records the brain can answer questions about
- The blueprint changed and its components should be brought up to date — a rerun tells the
  same titles again and the brain recognises its own
- Someone asks which components the design names, and where each one lives in the code

Do **not** use it to:

- **File the blueprint itself** — that is `brain-add-document`. This skill files it only as
  the document its components rest on.
- **Record a decision about a component** — that is `brain-record-decision`.
- **Recover requirements** from the code — that is `brain-rebuild-requirements`.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the
   `fairmind` field set to `"configured"`, the `project_id` and the `repository_id` that
   `/fairmind-connect` writes. **Never write those keys yourself**, and never set `fairmind`
   to `"configured"` on their behalf.
2. If the file is missing, `fairmind` is `"none"`, or `project_id` or `repository_id` is
   absent: **stop** and tell the user to run `/fairmind-connect` once for this checkout. A run
   with no bound repository has nowhere to tie the directories to.
3. Confirm these tools are in the tool list, by name: `mcp__Fairmind__Brain_tell`,
   `mcp__Fairmind__Brain_ask` and `mcp__Fairmind__Brain_overview`. A server that predates the
   platform release serving them answers `mcp__Fairmind__Studio_*` and
   `mcp__Fairmind__Insights_*` normally and exposes none of them. **Without `Brain_tell`, say
   which release is needed and stop.** Without `Brain_ask`, go on and write the draft, and say
   in the report "precedents not checked: the brain's ask tool is absent". Without
   `Brain_overview`, or with an answer whose `status` is not `ok`, go on and say "existing
   components not checked".

## Step 1: Get the blueprint and its link

Ask the person which blueprint to read if they have not said: a file in this checkout, or a
link. The brain is not searched for it.

1. **A file in the checkout.** Read it with the Read tool. Then write a title and a summary of
   two or three sentences — what the document is and what it covers, in its own vocabulary —
   to a scratch file, and run, with `{"agent": "brain-extract-components", "project":
   <project_id>}` on stdin:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/artifact_cards.py blueprint <path> --title <title> --summary-file <scratch file>
   ```

   It prints `{"tool", "arguments", "item"}`: `arguments` is the report that files the
   blueprint as a document by its link, and `item` is the related document every component
   report carries. The link names the last commit that changed the file, so an unchanged
   blueprint gives the same link on every run. **Send neither object changed, and never build
   the link yourself.** The verb sends nothing. It refuses, with one reason on stderr and exit
   code 3, a file that is untracked, edited since its last commit, on no branch of `origin`,
   or whose `origin` gives no link: say which, and ask the person for a link instead, or to
   commit and push the file.
2. **A link the person gives.** Read it through a connection the session already has, or from
   text the person pastes; this skill fetches nothing on its own. A link that cannot be read
   is a stop: say so. **Never extract from a title.** Write the title and summary the same way;
   the filing report is `{"intent": "document", "repository": <repository_id>, "project":
   <project_id>, "agent": ..., "title": ..., "summary": ..., "source": {"url": <the link>}}` and
   the item is `{"url": <the link>, "title": ..., "summary": ...}`, both with the same title
   and summary and no version.
3. **No blueprint at all: say so and stop.** "No blueprint was given." Do not extract from a
   design note, a README or the directory tree instead.
4. One blueprint per run. For a second blueprint, run the skill again; a component both name
   is drafted once, with the same text in each run, and a different text in the second run
   answers `conflict` (Step 6).

## Step 2: Read it

Take out only what the blueprint states:

- each component it names, in its own words;
- what each one is responsible for;
- its layer, technology and owning team, where the blueprint gives them;
- the directories or modules it points at;
- which component contains which;
- which depends on which, and how — calls it, reads from it, or publishes to it.

Whatever the blueprint does not say stays empty. A technology inferred from a file extension
is a guess the reader cannot tell from a statement.

## Step 3: Check what the brain and the code already hold

1. **Components already recorded.** Call `mcp__Fairmind__Brain_overview` with
   `detail_level: "standard"`, `project` (the bound `project_id`) and `repository` (the bound
   `repository_id`). Read its `components` section: the confirmed, current components, at most
   twenty, each with its `node_id` and `title`; `omitted` counts the rest. A drafted
   component whose title folds to the same text as a listed one — lowercase, every run of
   other characters as `-` — is shown as **already recorded as {title} ({node_id})**, and the
   person chooses: skip it, or send it as drafted. An overview item does not say whether a
   component belongs to the project or to the whole company, so this skill never sends
   `revises` against a listed node; `revises` goes only to the node a `conflict` answer
   returned (Step 6). The list is for the person, who can spot a renamed component; the brain
   answers `duplicate` or `conflict` to a same-title one anyway. A component proposed but not
   yet confirmed, one past the twentieth and one a person closed are not in this list.
2. **What the brain holds about the code.** For each distinct directory the draft names, at
   most **ten** in the run, call `mcp__Fairmind__Brain_ask` with `question_type: "file"`,
   `project`, `subject: {"file": {"repository": <the bound repository_id>, "path": <the
   directory, ending in "/">}}` and a `question` of one line naming the directory and the
   component. The answer is the decisions and requirements recorded under that code. Use it
   in the draft and report it per component as conclusions with references ("What the brain
   holds about this code"). **This answer never names a component card**, so it is not the
   duplicate check of item 1. A directory past the tenth is named as "not read"; after an
   answer with `reason: cap_reached`, send no further `Brain_ask` in this run.
3. **Cite each consideration with its state.** One whose `confirmed` is false is a draft nobody
   has confirmed — quite possibly one this very skill told on an earlier run — and is cited
   "(proposed, not yet confirmed)", never bare; an entry of `further_reading` is listed as
   "found, not cited". A consideration is said to cite knowledge recorded **at a file** only
   when it comes from a `file` question and one of its `file` references has the bound
   repository and that path. On any other question a `file` reference carries the catalog's
   own spelling of the repository: quote its path as a pointer and make no claim about where
   anything was recorded. Keep the `coverage` sentence of each answer; it is quoted, never
   recomputed.

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

4. Match every directory against the ingested repository with `mcp__Fairmind__Code_tree`
   (`project`, `repository: <repository_id>`, `start_line: 1`, `max_lines: 200`). The tree
   comes in pages, and a page says so only when it is not the last: under the repository's
   name it carries `(showing lines X-Y of Z)` — some servers print `Showing lines X-Y of Z
   total lines` instead. While such a line is there with Y below Z, call again with
   `start_line` set to Y + 1 (lines count from 1 and Y was already shown). A page with no such
   line, or with Y equal to Z, is the last. A page shorter than asked is not a stop signal: the
   server caps `max_lines` on its own. A directory is marked missing only against the whole
   tree. The blueprint's module names become repo-relative directory paths that exist in the
   tree. A directory the blueprint names and the tree does not have is marked **not in the
   ingested tree** in the draft; it is sent only if the person keeps it.

## Step 4: Draft

One entry per component the blueprint names, using only what `Brain_tell` takes:

| field | value |
|---|---|
| `title` | required — the component's name as the blueprint writes it, on one line. The brain recognises a component by its title: the same name finds its own record on a rerun, and a renamed component is a new record unless the person says otherwise |
| `summary` | one or two sentences of responsibility in the blueprint's own vocabulary, then — when the blueprint says them — its layer, technology, owning team, whether it is planned or deprecated, what it belongs to and what it depends on and how (calls, reads, publishes), all in words. At most 1000 characters; the door refuses a longer text and never cuts it |
| `files` | the directories that implement it, at most 10, as paths from the tree |
| `documents` | the blueprint's `item`, as Step 1 made it; the same object in every component report |

The component's layer, technology, owning team, parent and dependencies have no field of
their own: they are part of the `summary`, and the brain links the components when it places
them. The report carries no kind, key, status, anchor or link field, and a report that carries
one is refused.

## Step 5: Show what is about to leave, and ask

Being asked to extract the components is not a yes for this draft — the draft is your reading
of the blueprint, and the person has not seen it. Show, in a table: each component's title,
summary, directories (with the ones not in the ingested tree marked), each component
**already recorded as** another with the person's answer (skipped, or sent as drafted), and for
each distinct directory what the brain holds about that code (Step 3.2). Then say:

- **where it will be visible** — this project, by name;
- **what it will be linked to** — the blueprint, by title and link, which is told first as a
  document of its own;
- **what travels** — the fields above and the project id.

Then **wait for a yes.** An edit from the person is applied to the draft, and the changed
draft is shown again before it is sent.

**This skill tells components into the project, and offers no company-wide scope.** Every
call names `project`, the bound `project_id`.

## Step 6: Tell the components

**First the blueprint.** Send the filing report of Step 1 with `mcp__Fairmind__Brain_tell`. When
it answers `confirmed`, `proposed` or `duplicate`, the blueprint is a document the components
can rest on. Any other answer — a refusal such as `node_unavailable`, `conflict`, `retry` —
means **no component is told**: say why, and ask for a link or for a commit and push.

**Then one `mcp__Fairmind__Brain_tell` per component**, with `intent: "component"`,
`repository` (the bound `repository_id`), `project`, `title`, `summary`, `files`,
`documents: [<the blueprint's item>]`, `session_ref: "${CLAUDE_SESSION_ID}"` and `agent:
"brain-extract-components"`, each sent only after its predecessor was answered.

**Read what comes back.** Report the `state` as the answer gives it, never as you expect it,
with the `node_id`:

- `proposed` — recorded, and waiting for a person in the review queue.
- `duplicate` — the same component was already there and **nothing changed**. Report it that
  way, never as written.
- `conflict` — a record with this title exists and says something different. **Nothing was
  overwritten.** Show the person the difference; on a yes the component is sent again, whole,
  with `revises` set to the `node_id` the answer returned.
- The blueprint's `related` entry has its own `state`: `created`, `updated`, `unchanged` or
  `proposed` is linked. `unavailable` (the blueprint's card was rejected or closed since the
  filing), `retry`, `over_cap` or **no entry at all** is not: **stop the remaining components**
  and report which components were told, which one's blueprint was not linked and its state. A
  rerun tells them again (`duplicate`) and carries the blueprint item again.

A report the brain **refused** (`{'status': 'refused', 'reason': ..., 'message': ...}`) has
words the brain did not accept — an empty title, a title or text over its cap, a path that is
not a directory. Fix the draft, show what changed, and send it once more; a component refused
twice is reported and skipped. "No such document" on the blueprint item means the brain cannot
use that card for this key: say so and stop. A refusal that names a field of the report's
**shape** is different: say "the plugin and the brain disagree on the report's shape — update
the plugin", and do not send it again.

**Two hard rules, no exceptions:**

- **Never send a status, a `retract` or a `same_as` to confirm anything**, and never present a
  component as confirmed. The recorded state is a proposal; a human confirms in the review
  queue.
- **Never merge a duplicate.** A same-title record is the brain's to decide; this skill
  reports it and sends no second call that folds one component into another.

## Step 7: Report

One line per component, in the order told: the `state`, the title, the `node_id`, each
directory (marked if it was not in the ingested tree), the blueprint's `related` state, and
the `reason` or the refusal `message` where there is one. A partial run is reported as one:
"told 6 of 9, stopped at *Payment Connector*: the blueprint was not linked (`retry`)".

Then, per directory asked, what the brain holds about that code as **conclusions with
references**: the considerations that cite it with their confirmed or proposed state and the
cards, files and documents behind them, and the `coverage` sentence. Directories not read, and
answers the brain could not compose, are listed with the reason. Components listed as already
recorded are named with the person's answer.

## Output Structure

```markdown
# Components from the blueprint ({date})

**Status: proposed — awaiting human review.** Nothing below is confirmed architecture.

Told for: {project name} · repository {repository name} · blueprint: {title} ({link}) — {filed: confirmed | proposed | duplicate}

## Components
| State | Component | Node id | Directories | Blueprint link |
|---|---|---|---|---|
| proposed | {title} | {node_id} | {path}/ | {created | updated | unchanged | proposed} |
| duplicate | {title} | {node_id} | {path}/ (not in the ingested tree) | unchanged |
| conflict | {title} | {node_id} | {path}/ | — sent again with revises after a yes / left as it is |
| refused | {title} | — | — | — {message} |
| not told | {title} | — | — | stopped: blueprint {state | no entry} |

### Already recorded
- {draft component} — already recorded as {title} ({node_id}): {skipped | sent as drafted}

## What the brain holds about this code
### {directory}/
**Coverage:** {the answer's coverage sentence, verbatim | listed, not composed: {reason}}
- {conclusion} ({confirmed | proposed, not yet confirmed}) — references: {title and id of each card, file, document}
- Directories not read: {list or none}

### Caveats
- Precedents: {checked | not checked: {why}} · existing components: {checked | not checked: {why}}

## Yield
- Components named by the blueprint: {n} · told: {n} · not told: {n}
- proposed {n} · duplicate {n} · conflict {n} · refused {n}
- Directories in the ingested tree: {n} / {n} · asked of the brain: {n} of 10

Confirm or reject each component in the review queue — this skill confirms nothing.
```

## Next Steps

- **Confirm or reject** in the platform's review queue. Until a human does, these are
  proposals, and nothing downstream should treat them as the architecture.
- **When a blueprint changes**, run the skill again: a changed blueprint file has a new link
  and is filed as a new document; the components are told again under their titles, and the
  brain answers `duplicate` or `conflict` to each.
- **For a decision about one component**, load `brain-record-decision`.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id` or `repository_id`:** stop
and point the user at `/fairmind-connect`. There is nowhere to tie the directories.

**No `mcp__Fairmind__Brain_tell` in the tool list:** the connected server predates the
platform release that serves it. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**An MCP error result on `Brain_tell`:** the brain may not offer `Brain_tell` yet, or the
plugin and the brain disagree on the report's shape. Say so and stop; sending it again
changes nothing.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report which components were told, which were not, so the next run resumes
there. Never retry in a loop — every attempt is refused and the refusals are counted against
the tenant.

**`{'retryable': True}`:** the write could not be completed right now — an erasure in
flight, or the store unreachable. One retry is reasonable; a second refusal is an outage —
report it and stop.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote the
key name verbatim and stop; someone with platform access has to set it for this tenant.

**`{'status': 'error', 'message': 'Access denied: …'}`:** the key, or the person behind it,
may not write to this project, or the key was issued for another project. Nothing was written.
Quote it verbatim — it says where the right key is issued — and stop; retrying changes
nothing.

**No blueprint:** a real and reportable answer, not a failed search — see Step 1. Say it and
stop.
