---
name: brain-record-decision
description: Use only when a person asks for it - by name, or in so many words - to record in the company brain a decision taken in this session, an issue found and not fixed, or the replacement of a decision the brain already holds. "Record this decision in the brain", "file this as an issue for later", "this replaces what we decided about X". It searches the brain for the same or a competing decision first, drafts the record, shows it and asks before anything is sent, and records it as a proposal a person confirms. It never starts on its own initiative and never writes the local decision log.
---

# Record a decision, an issue or a replacement in the brain

## Overview

A decision taken with the main agent in an interactive session lives in the transcript and
nowhere else. `/fairmind-loop` proposes the architecture decisions its own agents logged —
at its human gate, or later through `/fairmind-sync-insights` — and nothing proposes the ones
a person takes in conversation. The same holds for the bug noticed on the way and
deliberately left alone, and for the decision that quietly replaced one the brain still
calls current. This skill is the door for those three.

It is deliberately narrow. **It runs because a person asked for it, never on its own
initiative, and it does not capture automatically.** The brain is not a transcript: a
decision nobody could have inferred from the code is worth a record, a restatement of what
the code already says is noise in every later read, and a session that recorded everything
it did would bury the few decisions that matter.

Everything it writes lands as `proposed`. A person confirms or rejects it in the review
queue.

**What leaves your machine when this skill writes.**
`mcp__Fairmind__Brain_record_decision`, `mcp__Fairmind__Brain_record_issue` and
`mcp__Fairmind__Brain_supersede_decision` send what you drafted and the person approved: a
decision's title, rationale, the options it rejected and why, its consequences, repo-relative
**file paths** and **function names**, and the node ids of the requirements and documents it
rests on; an issue's title, body, anchors and quoted evidence; a supersession's reason — to
the Fairmind platform your project's MCP entry points at. This is the **brain write-back**,
and the plugin README's *What leaves your machine* describes it in full under that name —
alongside every other route by which anything leaves.

**Announce at start:** "I'm using the brain-record-decision skill to record <what> in the
brain."

**Dependencies:** a connected Fairmind workspace. The payload shapes, the id rules and a
worked example live in `references/playbook.md`.

## When to Use

Use this skill when a person asks to keep, in the company brain:

- **A decision** taken in this session — a choice between real alternatives that the code
  does not explain on its own: a library, a schema shape, a trade-off accepted.
- **An issue** found and not fixed now — a latent bug, missing coverage, a piece of debt, a
  question only a person can answer.
- **A replacement** — a decision the brain holds is no longer what the team does, and the
  new one should say so rather than sit beside it.

Do **not** use it on your own because a decision just happened. If one looks worth keeping,
say so in one line and let the person ask.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the
   `fairmind` field set to `"configured"` and the `project_id` that `/fairmind-connect`
   writes. `repository_id` is optional: when present it is the `repository` every decision
   is recorded against and the `repo_ref` of every issue anchor. **Never write those keys
   yourself**, and never set `fairmind` to `"configured"` on the user's behalf.
2. If the file is missing, `fairmind` is `"none"`, or `project_id` is absent: **stop** and
   tell the user to run `/fairmind-connect` once for this checkout.
3. Confirm these tools are in the tool list, by name: `mcp__Fairmind__Brain_search`,
   `mcp__Fairmind__Brain_context`, `mcp__Fairmind__Brain_record_decision`,
   `mcp__Fairmind__Brain_record_issue` and `mcp__Fairmind__Brain_supersede_decision`. They
   arrive with the platform's **Brain-1** release; an older server answers
   `mcp__Fairmind__Studio_*` and `mcp__Fairmind__Insights_*` normally and exposes no
   `mcp__Fairmind__Brain_*` tool at all. If they are absent, say which release is needed and
   **stop**.

**Never degrade.** Without the brain there is nowhere this record belongs: do not write it
to a local file as a stand-in, and do not send it through another door. A decision filed
somewhere nobody reads is worse than one the person knows was not filed.

## Step 1: Decide what it is

| What the person wants kept | Where it goes |
|---|---|
| A choice between real alternatives that the code does not explain | this skill — a **decision** |
| A bug, gap, piece of debt or open question, found and not fixed now | this skill — an **issue** |
| A decision the brain holds that is no longer what the team does | this skill — a **supersession** |
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

## Step 2: Look for precedents first

### For a decision, or a replacement

1. `mcp__Fairmind__Brain_search` with the decision's own words, `kinds: "decision"`,
   `include_invalidated: true` and `project`, `k` small. `include_invalidated` is what brings
   back the decisions already superseded or rejected, each with its recorded reason. Rephrase
   it two or three times: the words a decision was recorded in are rarely today's.
2. `mcp__Fairmind__Brain_context` on each file the decision is about — `repository` (the
   bound `repository_id`, else the repository's name), `file_path`, `kinds: "decision"`,
   `detail_level: "minimal"`. A decision anchored to the
   file you are changing is the one most likely to compete with this one.
3. **Read `status` and `review_state` on every result and carry both with it.** `status` says
   whether the decision still holds (`taken`, `superseded`, `rejected`, …); `review_state` says
   whether a **person ever agreed with the record** (`confirmed`, `proposed`, `rejected`). A
   `proposed` decision is somebody's unconfirmed draft — possibly an agent's — and it is
   always cited as `{title} ({node_id}) (proposed, not yet confirmed)`, never bare.
4. **For a decision, the card's `natural_key` IS its `decisionId`.** That is the id a
   supersession and an issue's `caused_by` take. `node_id` is not.
5. **No read door shows you that id, and none says who first recorded a card.** Search,
   context and get return a card's `node_id`, never its `natural_key`. `provenance.agent` on a
   card, like a timeline event's `by`, names whoever wrote the record **last** — a status
   change carries the subject of whoever made it, and a later write by another agent replaces
   the name — so neither says whether this skill recorded a decision. What the skill can rely
   on is the id rule it applies itself (Step 3):
   - **The id is in hand** — printed by the report of the run that recorded the decision, or
     supplied by the person. Use it as given.
   - **Otherwise it is rebuilt, on the person's word** that this skill recorded that decision:
     apply Step 3's id rule to the **full** title `mcp__Fairmind__Brain_get` returns — a search
     result's title is cut short and mints the wrong id — show the rebuilt id, and ask. When
     the brain holds more than one decision of that title, the current one may carry a
     `-2`/`-3` suffix no title can tell you: ask the person for the id rather than guess.
   - **A decision another producer recorded** — a loop's, whose id begins `sha256:`, or another
     agent's — cannot be named from here unless the person supplies its id. Cite it by title
     and `node_id` and say so. A guessed `old_decision_id` records the replacement and leaves
     the old decision current; a guessed `caused_by` links nothing.

Then one of four answers:

- **The same decision is already current.** Stop and cite it — title, `node_id`, both
  states. Nothing is written: the brain already has it.
- **A different current decision covers the same ground.** Name it, and ask whether the new
  one replaces it. If it does, this is a **supersession**, and it needs the person's
  **reason** — the sentence a later reader will actually read — and the old decision's id,
  found as in item 5. No reason, no supersession; no id, no supersession either: record the
  new decision only if the person wants it, name the old one in its rationale, and say the
  old one stays current. If the person says the two coexist, record the new one and say in
  its rationale how they divide the ground.
- **A superseded or rejected decision covers the same ground.** Surface it with its recorded
  reason **before** drafting. A decision that revives something deliberately dropped is a
  stop-and-ask: does that reason still hold?
- **Nothing.** A real answer. Say which phrasings were tried.

Keep what the search found that the decision rests on: requirement node ids become
`requirement_refs`, document node ids become `document_refs`. If it rests on a document nobody
has filed, offer `brain-add-document` first and reference the document once it is filed.

### For an issue

1. `mcp__Fairmind__Brain_search` with the issue's own words and `kinds: "issue"`, plus
   `mcp__Fairmind__Brain_context` on the files it is about.
2. **The same issue is already recorded:** stop and cite it, by title and `node_id`, with both
   states. When the person has something to add, the **key** decides whether it may be
   written from here, never the card: `provenance.agent` and a timeline event's `by` name the
   last writer, so they cannot tell this skill's issue from another producer's, and the
   platform keeps every agent's issue keys in one namespace (`agent:<slug>`) — a write lands
   on whichever issue holds its key.
   - **Only under a key this skill minted** can an issue be updated — the key printed in the
     report of the run that filed it, or one the person supplies, or confirms after you rebuild
     it, only when the module-or-area prefix is certain; the rest is the slug of the stored
     title — and **an update replaces the whole record**: the title, body, anchors, evidence and
     `relates` stored afterwards are exactly what the new call carries, and nothing keeps the
     old ones. So draft the whole issue, not the addition. Take the full title, body and
     anchors from `mcp__Fairmind__Brain_get`, every chunk of the body. No read door returns an
     issue's evidence or its `relates`, so ask the person to supply again what should stay, or
     to drop it knowingly. A key that differs files a second issue instead of updating the
     first.
   - **Without such a key, it is another producer's issue** — a loop's included — and it is
     never written from here. The addition belongs on that card, in the review queue; say so
     and stop.
3. **A decision caused it:** carry that decision's `decisionId`, found as in item 5 of the
   decision search above, into `relates.caused_by`. When no id can be found, name the
   decision in the body instead.

## Step 3: Draft

**A decision** is one `fm-insights.decision/2` row. The fields and a full example are in
`references/playbook.md`; in short:

| field | value |
|---|---|
| `decisionId` | **`decision:<project_id>:<slug of the title>`** — the slug lowercase, non-alphanumerics to `-`, under 80 characters |
| `ts` | now, ISO-8601 UTC |
| `agent` | `brain-record-decision` |
| `kind` | `architecture`, `implementation`, `dependency`, `testing` or `process` — or a truer word when none fits |
| `title` / `rationale` | what was decided, and why — in the person's terms, not a paraphrase that softens it |
| `options` | `[{"title": ..., "rejected_because": ...}]` — the alternatives considered |
| `consequences` | what follows from it, including what it costs |
| `files` / `functions` | repo-relative paths, and `{"file_path", "name"}` per symbol — only what the decision is actually about |
| `requirement_refs` / `document_refs` | node ids from Step 2, when there are any |

**The slug is taken from the exact title you send.** When it would run past 80 characters,
shorten the title itself, not the slug: a recorded title that does not slugify to its own id
is a decision no later run can name.

**The `decisionId` is what makes a re-run converge.** The same decision drafted twice mints
the same id, and the platform counts the second as a duplicate instead of filing it again.
That is also the limit: **a recorded decision is never edited by sending it again.** A
corrected rationale under the same id is dropped with a warning naming the id; the way to
change what a recorded decision says is a supersession. A replacement never reuses its
predecessor's id — when the slug would match, add `-2`, `-3`, the first suffix not already
taken.

**An issue** is one `mcp__Fairmind__Brain_record_issue` call:

| field | value |
|---|---|
| `project` | the bound `project_id` |
| `issue_kind` | `bug`, `feature`, `question` or `tech_debt` |
| `title` / `body` | what was found, and how it was found |
| `natural_key` | **`<module-or-area>:<slug of the title>`** — under 80 characters; the same issue run twice authors the same key |
| `anchors` | `file_path` plus `repo_ref` / `repo_ref_scheme` / `symbol_name` / `symbol_type` / `start_line_hint`; omitted entirely when `repository_id` is not bound |
| `evidence` | `[{"kind": ..., "text": ..., "source": ...}]` — what the finding rests on; `kind` is `quote`, `log_excerpt` or `link` |
| `relates` | `{"caused_by": [<decisionId>]}` when a decision caused it |
| `agent` | `brain-record-decision` |

**Anchors need a bound `repository_id`:** `repo_ref` is that id and `repo_ref_scheme` is the
literal `"code-ingestion"`. With no repository bound, omit the anchors and say so next to the
issue in the report — "anchor omitted — no repository bound" is a different fact from "no
anchor found", and must read as one.

**A supersession** is the old decision's `decisionId`, the person's reason, and the
replacement drafted as a full decision row.

## Step 4: Show what is about to leave, and ask

Show, per record, in a few lines: its **kind**, the **title**, the **rationale** or body, its
**anchors** — files and functions — and the **target**: the repository and project it is
recorded against. For a supersession, also which decision it replaces and the reason — and
say that the replaced decision **stops being current the moment the call lands**, while its
replacement waits for a person as a proposal: that half does not wait for anybody. For an
update of an issue already recorded, show the whole record that will replace the stored one,
and name what the stored one had that the new one drops. Then wait for a yes.

Asking for this skill is consent to draft, not to send what you drafted: the words are yours
until the person has read them. If they change a word, the change is theirs to make and yours
to apply — then show it again.

## Step 5: Write

- **Decisions:** one `mcp__Fairmind__Brain_record_decision` call carrying every decision of
  this run in `decisions` (at most 50), with `repository` (the bound `repository_id`, else the
  repository's name), `project` and `contract_version: "fm-insights.decision/2"`.
- **Issues:** one `mcp__Fairmind__Brain_record_issue` per issue. When an issue's `caused_by`
  names a decision recorded in this run, record the decision first.
- **Supersessions:** one `mcp__Fairmind__Brain_supersede_decision` per replacement, with
  `old_decision_id`, `reason`, `new_decision` (the row), `repository` and `project`.

**Read what comes back.** Each response names what happened; report that, not what you meant
to happen:

- a decision batch answers `inserted`, `duplicates`, `warnings` and `edges`. A row counted
  under `duplicates`, with a warning naming its id, was **already recorded and nothing
  changed** — report it that way, never as recorded. An `edges` entry marked `target_missing`
  is a ref the brain could not find: name it;
- an issue answers `{'status': 'ok', 'knowledge_id': ...}`. **When a response carries
  `possible_duplicates`**, the platform found records that look like this one: list each —
  its id and score — next to the record in the report, and never drop them. The person
  deciding in the review queue needs them. **An empty list is "none" only when the check
  ran**: `detection.duplicates.status` `disabled` — or `embedding.duplicate_detection.status`
  `disabled` — is "not checked — duplicate detection is off"; a `detection` with no
  `duplicates` entry, or any status but `complete` or `replayed`, is "not checked — {status or
  reason}";
- a supersession answers `{'status': 'ok', 'superseded': ..., 'superseded_by': ...,
  'decisions': {...}}`, and that `ok` says only that the call went through. Read `decisions`,
  which is the decision batch's own answer:
  - the replacement counted under `inserted` was recorded; counted under `duplicates`, it
    was **not** written — a decision with that id already existed — and the old decision was
    marked superseded all the same, by that earlier row. Report that, never
    `supersession · proposed`;
  - a warning saying the old decision could not be superseded — no decision with that id, or
    a project you may not write to — means the old decision **is still current**. Report
    exactly that.

**When the response is `revision_proposed`** (`{'status': 'revision_proposed',
'proposal_id': …, 'target': …}`), the issue's natural key hit a record a person already
confirmed. The record was left untouched and a revision proposal was filed instead. Report
the `proposal_id` and move on — do not retry, and do not route around it with a different
key.

**Three hard rules, no exceptions:**

- **Never call `mcp__Fairmind__Brain_set_status`**, to confirm anything or for any other
  reason, and never present a record as confirmed. The recorded
  `review_state` is always `proposed`, whatever else the call says — confirmation happens in
  the review queue and is a person's act.
- **Never append to `.fairmind/insights/decisions.jsonl`.** That file is the log the agents of
  a loop keep and `/fairmind-sync-insights` flushes. The brain door already stores the
  decision where Agentic Insights reads decisions from — `Insights_get_decisions` returns it
  like any other — so a row put in that log would only be sent again by the next sync,
  through the insights lane and, for an architecture decision, to the brain, and come back a
  duplicate both times. What the stored decision lacks is a session: this skill sends no
  session reference, so it is tied to no session's activity. Say so if asked.
- **Never write a local stand-in** for a record the platform refused — see Step 0.

## Step 6: Report

One line per record, never the payload. Print it in the shape below.

## Output Structure

```markdown
**Status: proposed — awaiting human review.** Nothing below is confirmed knowledge.

- decision · {title} · {repository} / {project} · proposed · `{decisionId}`
- issue · {title} · {project} · proposed · key `{natural_key}` · possible duplicates: {ids and scores | none | not checked — duplicate detection is off | not checked — {status or reason}}
- supersession · {old title} (`{old decisionId}`) → {new title} (`{new decisionId}`) · proposed · reason: "{reason}"
- not recorded · {title} · {why: already current as `{node_id}` | duplicate, nothing changed | replacement already existed, old decision superseded by it | no id for the decision it replaces | refused: {message}}

Confirm or reject each record in the review queue — this skill confirms nothing.
```

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `project_id`:** stop and point the user
at `/fairmind-connect`. Say plainly that the record was not made.

**No `mcp__Fairmind__Brain_*` tools in the tool list:** the connected server predates the
**Brain-1** release. Name that release, say the `mcp__Fairmind__Studio_*` and
`mcp__Fairmind__Insights_*` tools are unaffected, and stop.

**`{'status': 'throttled', ...}`:** the tenant's daily brain-write quota is spent. **Stop
immediately.** Report which records were written, which were not, and their ids and keys, so
the next run resumes there. Never retry in a loop — every attempt is refused and the
refusals are counted against the tenant.

**`{'retryable': True}`:** the write could not be completed right now — an erasure in
flight, or the store unreachable. One retry is reasonable; a second refusal is an outage —
report it and stop.

**A refusal naming an unset configuration key:** provisioning, not a bug in the run. Quote
the key name verbatim and stop; someone with platform access has to set it for this tenant.

**An access refusal:** the key, or the person behind it, may not write to this project. Say
so and stop; retrying changes nothing.

**A refusal naming a field or a shape** — an anchor missing `repo_ref`, a batch over 50, a
supersession with neither or both of `new_decision` and `new_decision_id`: fix the draft and
show it again before resending. Never resend an unchanged call.

**`revision_proposed`:** expected, not an error — see Step 5. Count it, name the
`proposal_id`, continue.
