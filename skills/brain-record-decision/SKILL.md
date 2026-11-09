---
name: brain-record-decision
description: Use only when a person asks for it - by name, or in so many words - to record in the company brain a decision taken in this session, an issue found and not fixed, or the replacement of a decision the brain already holds. "Record this decision in the brain", "file this as an issue for later", "this replaces what we decided about X". It asks the brain for the same or a competing decision first, drafts the record, shows it and asks before anything is sent, and records a decision as taken and an issue as a proposal a person confirms. It never starts on its own initiative and never writes the local decision log.
---

# Record a decision, an issue or a replacement in the brain

## Overview

A decision taken with the main agent in an interactive session lives in the transcript and
nowhere else. `/fairmind-loop` sends the decisions its own agents logged —
at its human gate, or later through `/fairmind-sync-insights` — and nothing sends the ones
a person takes in conversation. The same holds for the bug noticed on the way and
deliberately left alone, and for the decision that quietly replaced one the brain still
calls current. This skill is the door for those three.

It is deliberately narrow. **It runs because a person asked for it, never on its own
initiative, and it does not capture automatically.** The brain is not a transcript: a
decision nobody could have inferred from the code is worth a record, a restatement of what
the code already says is noise in every later read, and a session that recorded everything
it did would bury the few decisions that matter.

A decision is recorded where it was taken, so:
a decision lands `confirmed` with status `taken`; an issue lands `proposed`.
A person confirms or rejects an issue in the review queue. A taken decision changes only by
replacement: the brain refuses a `revises` of it.

**What leaves your machine when this skill works.**
`mcp__Fairmind__Brain_tell` sends what you drafted and the person approved: a decision's
title, its rationale and consequences, the options it rejected and why, repo-relative **file
paths** and **function names**, commit ids, the question it answers and who took it; an
issue's title and description; a replacement's reason; the title, link and summary of a
document the record rests on; the ticket reference and the status the person stated, when
there is one; and, with each, an agent label and this session's Claude Code session id — to
the Fairmind platform your project's MCP entry points at. This is the **brain write-back**,
and the plugin README's *What leaves your machine* describes it in full under that name —
alongside every other route by which anything leaves. `mcp__Fairmind__Brain_ask` sends the
question you put to the brain, in the person's words, and the paths of the files the record
is about.

**Announce at start:** "I'm using the brain-record-decision skill to record <what> in the
brain."

**Dependencies:** a connected Fairmind workspace. The report shapes and a worked example live
in `references/playbook.md`.

## When to Use

Use this skill when a person asks to keep, in the company brain:

- **A decision** taken in this session — a choice between real alternatives that the code
  does not explain on its own: a library, a schema shape, a trade-off accepted.
- **An issue** found and not fixed now — a latent bug, missing coverage, a piece of debt, a
  question only a person can answer.
- **A replacement** — a decision the brain holds is no longer what the team does, and the
  new one should say so rather than sit beside it.
- **An overrule** — the person changes their mind about an answer the company brain gave in
  this session (a question it answered before the person was asked). That is a replacement of
  the decision that answered. When answer before asking is on, do not use this skill for it:
  ask that question again with `AskUserQuestion`, with the same text and options, and the
  hooks record the person's pick as the replacement — writing one here as well would leave
  two decisions answering one question. Use the skill for an overrule only when the person
  asks for the replacement by name. Step 4's yes is still required before anything is sent.

Do **not** use it on your own because a decision just happened. If one looks worth keeping,
say so in one line and let the person ask.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the
   `fairmind` field set to `"configured"` and the `project_id` that `/fairmind-connect`
   writes. `repository_id` is optional: when present it is the `repository` every record is
   filed under and the repository of every `file` question. **Never write those keys
   yourself**, and never set `fairmind` to `"configured"` on the user's behalf.
2. If the file is missing, `fairmind` is `"none"`, or `project_id` is absent: **stop** and
   tell the user to run `/fairmind-connect` once for this checkout.
3. Confirm these tools are in the tool list, by name: `mcp__Fairmind__Brain_tell` and
   `mcp__Fairmind__Brain_ask`. A server that predates the platform release serving them
   answers `mcp__Fairmind__Studio_*` and `mcp__Fairmind__Insights_*` normally and exposes
   neither. **Without `Brain_tell`, say which release is needed and stop.** Without
   `Brain_ask`, go on and write, and say in the report "precedents not checked: the brain's
   ask tool is absent".

**Never degrade.** Without the brain there is nowhere this record belongs: do not write it
to a local file as a stand-in, and do not send it through another door. A decision filed
somewhere nobody reads is worse than one the person knows was not filed.

## Step 1: Decide what it is

| What the person wants kept | Where it goes |
|---|---|
| A choice between real alternatives that the code does not explain | this skill — a **decision** |
| A bug, gap, piece of debt or open question, found and not fixed now | this skill — an **issue** |
| A decision the brain holds that is no longer what the team does | this skill — a **replacement** |
| What the system must do — a need, a story, a requirement | `brain-new-requirement` — name it and stop |
| A document — a standard, a design, a specification, a brief | `brain-add-document` — name it and stop |
| A rule reviews should apply, a criterion for the judge | nowhere on this machine — see below |
| What the code already says | refused — see below |

**A rule or a review criterion is never authored here.** The judge's criteria are served from
what the brain holds; a rule written on this machine is a second source that nobody else
reads. If a decision stands behind the rule, record that decision; the rule is not drafted.

**A restatement is refused, and the reason is given.** "We use `requests` for HTTP" when every
module imports it, "the service returns JSON" — the code already says it, and a second copy in
the brain can only drift from the first. Say that, in those terms, and write nothing. What is
worth keeping is what the code cannot tell a reader: the alternative that was rejected and
why.

A component, a glossary term or an incident is none of the three: say so and stop rather than
reshape it into a decision or an issue.

## Step 2: Ask the brain for precedents first

Every `mcp__Fairmind__Brain_ask` call carries a `question` in plain words, `project` (the
bound `project_id`) and a `question_type`. What comes back is a short list of
**considerations** — each a sentence the brain composed, with whether a person confirmed what
it rests on and the references behind it — plus **further reading**: records found and not
cited.

1. `question_type: "precedent"`, the `question` being the decision's own words. Ask it two or
   three times with different phrasings: the words a decision was recorded in are rarely
   today's.
2. `question_type: "contraindications"`, the `question` naming the approach chosen: what the
   brain holds that speaks against it.
3. For each file the decision is about, at most three in a run: `question_type: "file"`,
   `subject: {"file": {"repository": <the bound repository_id>, "path": <the file>}}`, and a
   `question` of one line naming the path and the decision. With no bound `repository_id`,
   send no `file` question and say "files not read — no repository bound". A file past the
   third is named as "not read"; after an answer with `reason: cap_reached`, send no further
   `Brain_ask` in this run.
4. **Cite each consideration with its state.** One whose `confirmed` is false is cited
   "(proposed, not yet confirmed)", never bare; an entry of `further_reading` is listed as
   "found, not cited". A consideration is said to cite knowledge recorded **at a file** only
   when it comes from a `file` question and one of its `file` references has the bound
   repository and that path. On any other question a `file` reference carries the catalog's
   own spelling of the repository: quote its path as a pointer and make no claim about where
   anything was recorded.
5. **The node id of a decision comes from the answer, never from guessing.** A `card`
   reference carries the card's `id`; that 32-character id is what a replacement names. A
   company-brain answer of this session names the decision it used with its `node_id` too.
   Use an id from one of those, or one the person supplies.

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

Then one of four outcomes:

- **The same decision is already current.** Stop and cite it — title, node id, confirmed or
  not. Nothing is written: the brain already has it.
- **A different decision covers the same ground.** Name it, and ask whether the new one
  replaces it. If it does, this is a **replacement**, and it needs the person's **reason** —
  the sentence a later reader will actually read — and the old decision's node id. No reason,
  no replacement; no id, no replacement either: record the new decision only if the person
  wants it, name the old one in its description, and say the old one stays current. If the
  person says the two coexist, record the new one and say in its description how they divide
  the ground.
- **A cited decision is not clearly current.** The answer does not say whether a decision is
  still in force. When a consideration on the same ground reads as dropped, replaced or
  disputed, or you cannot tell, name it — title, node id, confirmed or not — and ask the
  person whether the reason it was dropped still holds **before** drafting. A decision that
  revives something deliberately dropped is a stop-and-ask.
- **Nothing.** A real answer. Say which phrasings were tried.

Keep what the answers found that the decision rests on: a document the brain holds, with its
link, goes in the report's `documents` (Step 3). If it rests on a document nobody has filed,
offer `brain-add-document` first.

For an **issue**, ask `question_type: "precedent"` with the issue's own words and `file`
questions on the files it is about, as above. **The same issue is already recorded:** stop
and cite it. When the person has something to add, a report whose title is the recorded
issue's title answers `conflict` and names that record's `node_id`: show the person what
would change and, on a yes, send the whole issue again with `revises` set to that node id
(Step 3). A node id that the answer to this run's own earlier `Brain_tell` returned, or that
the person supplies, serves the same way. A record found only through `Brain_ask` is never
revised from here: the addition belongs on that card, in the review queue; say so and stop.

## Step 3: Draft

Every record is one `mcp__Fairmind__Brain_tell` report. The fields and a full example are in
`references/playbook.md`; in short:

**A decision** — `intent: "decision"`:

| field | value |
|---|---|
| `repository` | the bound `repository_id`, else the repository's name |
| `project` | the bound `project_id` |
| `title` | what was decided, on one line, short. The brain identifies a record by its title: the same title sent again is the same decision, a changed title is a new one |
| `summary` | why it was decided and what follows from it, including what it costs — in the person's terms, not a paraphrase that softens it. At most 1000 characters; the door refuses a longer text and never cuts it, so shorten it yourself |
| `origin` | `"taken"`, always: the decision was taken where it is recorded, because the person stated it |
| `decided_by` | `"human"` when the person stated the decision; `"agent"` when the report records a choice the agent made on its own. It says who took the decision and confirms nothing |
| `gate_approved` | `true` only when the person approved an agent's choice at Step 4. The brain stores it as a record that a person approved; it is not what makes a decision confirmed |
| `question` | the question the decision answers, in the words it was asked in this session. Only when it answers one; otherwise left out |
| `options` | `[{"title": ..., "chosen": true or false, "rejected_because": ...}]` — every alternative considered, the chosen one included, with `chosen` always present |
| `files` / `functions` | repo-relative paths, and `"<path>::<name>"` per symbol — only what the decision is actually about |
| `commit_shas` | the commits that carry it, when there are any |
| `documents` | `[{"url": ..., "title": ..., "summary": ...}]` for a document it rests on and has a link, `{"document_id": ..., "summary": ...}` for an uploaded one — at most ten related items in all |
| `task_ref` | the ticket the decision belongs to, **only when the person named it in this conversation** |
| `task_ref_status` | a status the person stated about that ticket in this conversation (`done`, `in_progress`, `implemented`, `verified`), never inferred, and only with `task_ref` |
| `session_ref` | `"${CLAUDE_SESSION_ID}"` |
| `agent` | `brain-record-decision` |

A status rides on the report that names its ticket. **The status change itself waits for no
approval**; the report that carries it may sit in the review queue for its own reasons.
`cancelled` and `retired` wait for a person; `rejected` is recorded and never applied. A
status told again with the same report changes nothing: to apply it again after the card
moved back, send a new report — a decision of its own, whose title says it, such as
`Done again: <ticket> (<task_ref>)`.

**An issue** — `intent: "problem"`: `repository`, `project`, `title`, `summary` (what was
found, and how it was found, at most 1000 characters), `files` and `functions` when it is
about code, `documents` for a link that shows it, `session_ref`, `agent`. Nothing beyond
those fields is sent: the brain decides what kind of issue it is.

**A replacement** is a decision report as above with `replaces: {"node_id": <the old
decision's 32-character id>, "reason": <the person's reason, at most 1000 characters>}`. It
needs an id that Step 2 item 5 names. **Only a decision the person took replaces
directly.** An agent's replacement (`decided_by: "agent"`) lands the new decision and files
the replacement as a proposal that a person confirms: the old decision stays in force until
then.

**A change to an issue this skill filed in this run** is the same report sent again with
`revises` set to that record's `node_id`. **A decision this skill filed is never revised**:
it is taken, so it lands confirmed, and the brain refuses a `revises` of it with
`revise_not_allowed`. Changing it is a replacement, as above, naming that `node_id` in
`replaces`.

**Do not send** a category, an id, a slug, an anchor, a status, a review state or a
requirement reference: the brain decides all of them from the report, and a report that
carries a field it does not take is refused.

## Step 4: Show what is about to leave, and ask

Show, per record, in a few lines: its **kind**, the **title**, the **description**, its files
and functions, and the **target**: the repository and project it is recorded against. For a
decision, also that it will be recorded as taken, who decided it, and the question it answers
when it has one. For a replacement, also which decision it replaces and the reason — and say
that a person's replacement ends the old decision **the moment the call lands**, while an
agent's waits in the review queue. For a change to a record already filed, show the whole
record that will replace the stored one, and name what the stored one had that the new one
drops. Then wait for a yes.

Asking for this skill is consent to draft, not to send what you drafted: the words are yours
until the person has read them. If they change a word, the change is theirs to make and yours
to apply — then show it again.

## Step 5: Write

One `mcp__Fairmind__Brain_tell` call per record, in this order: a decision before an issue
that names it. Every call carries `session_ref: "${CLAUDE_SESSION_ID}"` and `agent`.

**Read what comes back.** Each answer names what happened; report that, not what you meant to
happen. Report the `state` as the answer gives it, never as you expect it:

- `confirmed` — recorded and confirmed: a decision taken here. `proposed` — recorded, and
  waiting for a person in the review queue. Report the `node_id` next to the title.
- `duplicate` — the same record was already there and **nothing changed**. Report it that
  way, never as recorded. To change a recorded decision, replace it.
- `conflict` — a record with this title exists and says something different. **Nothing was
  overwritten.** Show the person the difference; on a yes the report is sent again as the
  answer's `message` says: with `revises` set to the `node_id` the answer returned, or —
  when the message offers only `replaces` — as a replacement naming that `node_id` in
  `replaces`, with the person's reason.
- With `replaces`, `replaces_outcome` says what happened to the old decision: `superseded`
  (it is no longer current), `proposed` (a proposal waits for a person; the old decision is
  still current), `kept` (the old decision could not be ended — a person rejected it, a
  successor already replaced it, or this key has no role on its project — and no proposal was
  filed) or `not_replaced` (the report named no replacement that applied). Report it in
  those words, never as a replacement that happened.
- With `documents`, each `related` entry has its own `state`: `created`, `updated`,
  `unchanged` or `proposed` is linked; `unavailable`, `retry` and `over_cap` are not — name
  the document and its state.

A report the brain **refused** (`{'status': 'refused', 'reason': ..., 'message': ...}`) has
words the brain did not accept — an empty title, a title that is too long, a text over its
cap, a node it cannot find. Fix the draft, show it again, and send it only after a yes. One
refusal is answered differently: "no such document" on a `documents` entry means the id is not
one this key can use, so send the report once more without that document and say so in the
report. `revise_not_allowed` means the decision named in `revises` is not revised: draft the
change as a replacement (Step 3), show it, and send it after a yes. A refusal that names a
field of the report's **shape** is different: say "the plugin and the brain disagree on the
report's shape — update the plugin", and do not send it again.

**Three hard rules, no exceptions:**

- **Never send a status, a `retract` or a `same_as` to confirm anything**, and never present
  an issue as confirmed. Report a decision as `taken` and an issue as `proposed`, whatever
  else the call says — an issue's confirmation happens in the review queue and is a person's
  act. A change of mind about a decision is a replacement.
- **Never append to `.fairmind/insights/decisions.jsonl`.** That file is the log the agents of
  a loop keep and `/fairmind-sync-insights` flushes. The brain door already stores the
  decision where Agentic Insights reads decisions from — `Insights_get_decisions` returns it
  like any other — so a row put in that log would only be sent again by the next sync, and
  come back a duplicate. Each record this skill files carries this session's id as
  `session_ref`, so the records are tied to this session's activity. Say so if asked.
- **Never write a local stand-in** for a record the platform refused — see Step 0.

## Step 6: Report

One line per record, never the payload. Print it in the shape below.

## Output Structure

```markdown
**Decisions: taken — recorded where it was taken.**
**Issues: proposed — awaiting human review.** No issue below is confirmed knowledge.

- decision · {title} · {repository} / {project} · {state} · node `{node_id}`
- issue · {title} · {project} · {state} · node `{node_id}`
- replacement · {old title} (`{old node_id}`) → {new title} (`{new node_id}`) · {state} · {replaces_outcome} · reason: "{reason}"
- not recorded · {title} · {why: already there as `{node_id}` | duplicate, nothing changed | conflict with `{node_id}`, nothing overwritten | no id for the decision it replaces | refused: {message}}

Precedents: {considerations cited, each with its state | not checked: {why}}

Confirm or reject each issue in the review queue — this skill confirms nothing.
```

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id`:** stop and point the user
at `/fairmind-connect`. Say plainly that the record was not made.

**No `mcp__Fairmind__Brain_tell` in the tool list:** the connected server predates the
platform release that serves it. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**An MCP error result on `Brain_tell`:** the brain may not offer `Brain_tell` yet, or the
plugin and the brain disagree on the report's shape. Say so and stop; sending it again
changes nothing.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report which records were written, which were not, and their titles, so the
next run resumes there. Never retry in a loop — every attempt is refused and the refusals are
counted against the tenant.

**`{'retryable': True}`:** the write could not be completed right now — an erasure in
flight, or the store unreachable. One retry is reasonable; a second refusal is an outage —
report it and stop.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote
the key name verbatim and stop; someone with platform access has to set it for this tenant.

**An access refusal:** the key, or the person behind it, may not write to this project. Say
so and stop; retrying changes nothing.

**A refusal of the report's words or shape:** see Step 5. Never send an unchanged report
again.
