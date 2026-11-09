# Playbook — a new requirement from what exists

On-demand detail for the `brain-new-requirement` skill: how to ask so precedents actually
surface, how to read an answer, the report shape the write door takes, and a worked example end
to end.

## The tool surface

| Tool | Used for | Used here |
|---|---|---|
| `mcp__Fairmind__Brain_ask` | what the brain holds on a question, as short considerations with their references | yes — Step 2 |
| `mcp__Fairmind__Brain_tell` | report a need, a story or a requirement | **yes — the only write** |

No other brain tool is on this skill's menu. A record's status and review state are decided by
the brain from the report and from who sends it; confirmation is a human act performed in the
review queue.

## Asking so precedents actually surface

`mcp__Fairmind__Brain_ask` takes `question` (one to two thousand characters), `question_type`
(`file`, `precedent`, `contraindications` or `context`), `project` and, for a file,
`subject: {"file": {"repository": ..., "path": ..., "symbol": ...}}`. Here the type is
`precedent`: what the brain holds on the idea.

The answer is `considerations` (each a `text`, whether a person confirmed what it rests on, and
its `references`), `further_reading` (records found and not cited), `composed`, `coverage` and,
when nothing could be composed, a `reason`. A reference is a `card`, a `document`, a `file`
(repository and path), a `ticket`, a `pull_request` or a `commit`.

A question that finds nothing is usually a question phrased in today's words:

1. **The user's words, verbatim.** "let customers pause a subscription".
2. **The domain noun on its own.** "subscription pause", "dunning", "grace period".
3. **The behaviour, not the feature name.** Feature names change; behaviours do not. "billing
   stops but the account stays open".
4. **The failure it prevents.** Many requirements are recorded as the incident that produced
   them: "customers cancelled instead of pausing".
5. **The adjacent noun.** A precedent often lives one concept away — "trial extension",
   "account hold", "suspension".

At most four `precedent` questions in a run. Put the phrasings that returned something first.

A consideration that is not confirmed is somebody's unconfirmed draft — possibly an agent's —
and is always cited as `{text} (proposed, not yet confirmed)`.

## Reading what happened to a precedent

An answer says what the brain holds; when a precedent was removed or replaced, the
consideration says so and the reference beside it names the record behind the change. Pull out:

- **What it was**, and **when it first existed**, if the answer says.
- **What happened to it** — removed, retired, replaced — and when.
- **The reason**, as a quote. "Removed because per-seat pausing made the invoice reconciliation
  ambiguous" is actionable; "removed for billing reasons" is a paraphrase that loses the very
  thing the reader needed.
- **The record behind the change**, by title and id, from the references.

**When the answer records a removal with no reason**, say exactly that: "removed, no reason
recorded", and ask the person what they know. Inventing a plausible reason is the single worst
failure this skill can produce — it manufactures a decision nobody made and it is
indistinguishable from a real one afterwards.

## The impact step: `brain-impact` first, the sketch only without a repository

With a bound `repository_id`, Step 4 is `brain-impact`. Hand it the idea and the precedents
Step 2 found — card ids plus both states — and it skips its own questions. What comes back is
a dependency-aware answer in its usages-only mode: the files the precedents point to, what the
brain holds about each as conclusions with references, and the dependents of each symbol from
the code graph. It records nothing; its findings reach the brain through this skill's own write
step, as the drafts' files and their cited constraints.

With no repository bound, `brain-impact` cannot run, and Step 4 falls back to a sketch. Take
the file references of the closest precedents and group them by directory. State the limit in
the output. The sketch:

- **does** show where the previous attempt lived;
- **does not** follow call graphs, imports or runtime dependencies;
- **does not** claim to be complete.

Name in the report which of the two produced the impact section. Two analyses of different
strengths presented in the same voice is how a sketch gets read as a guarantee.

## The report on the write door

Every drafted item is a `Brain_tell` report with `intent: "need"`:

```json
{
  "intent": "need",
  "repository": "billing-service",
  "project": "6a8c08657fdafcc39df4e48c",
  "title": "Customers can suspend billing without losing their account",
  "summary": "Need. Customers who do not want to pay for a quiet month should pause instead of cancelling and keep their account and history. Rests on: Subscription pause, self-service (removed 2025, reason: paused seats stayed counted in the monthly invoice) (3f2a9c4e5b6d7a8b9c0d1e2f3a4b5c6d, confirmed). Constrained by: Invoices are reconciled per seat-day (confirmed).",
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-new-requirement"
}
```

A child names its parent in its `summary`, by the parent's title; nothing in the report links
the two. The brain links the items to each other, to the code and to what they rest on when it
places them.

```json
{
  "intent": "need",
  "repository": "billing-service",
  "project": "6a8c08657fdafcc39df4e48c",
  "title": "A paused seat is not counted in the monthly invoice",
  "summary": "Technical requirement. Refines the functional requirement Pause stops billing for the seat. A paused seat is excluded from the seat-day count that reconciliation uses, so a pause cannot be read as a downgrade. Rests on: Invoices are reconciled per seat-day (confirmed).",
  "files": ["app/billing/invoice.py"],
  "functions": ["app/billing/invoice.py::count_seat_days"],
  "documents": [
    {"url": "https://example.com/docs/invoice-reconciliation", "title": "Invoice reconciliation rules", "summary": "How seat-days are counted and reconciled each month."}
  ],
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-new-requirement"
}
```

Leave out any field you have nothing true to put in. `task_ref` (the ticket the item belongs to)
and `task_ref_status` (`todo`, `in_progress`, `done`, `implemented` or `verified`) are added only
with the person's own words in this conversation. Do not send a requirement type, a key, a
parent reference, an anchor, a precedent list, `status` or `state`: a report that carries one
is refused.

Text limits the door enforces and never repairs: a title is one line of at most 256
characters; a `summary` is at most 1000 characters; at most a hundred files and functions and
ten related items in all. A control character or a line break in a one-line field is a refusal.

A **change** to a draft this run already told is the report again, in full, with `revises` set
to that draft's `node_id` (taken from the answer that told it, or from a `conflict` answer):

```json
{
  "intent": "need",
  "repository": "billing-service",
  "project": "6a8c08657fdafcc39df4e48c",
  "title": "A paused seat is not counted in the monthly invoice",
  "summary": "Technical requirement. Refines the functional requirement Pause stops billing for the seat. A paused seat is excluded from the seat-day count, and the exclusion is logged on the invoice line so support can explain it.",
  "revises": "0123456789abcdef0123456789abcdef",
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-new-requirement"
}
```

## Response shapes

| Response | Meaning | Do |
|---|---|---|
| `state: proposed` (with `node_id`) | the draft landed and waits for a person | count it, carry the `node_id` into the report |
| `state: duplicate` | the same draft was already there; nothing changed | report it that way, never as written |
| `state: conflict` (with `node_id`) | a record under this title says something different; nothing was overwritten | show the difference; after the person's yes, send it again with `revises` set to that `node_id` |
| `related[]` | one entry per related document: `created`, `updated`, `unchanged` or `proposed` is linked; `unavailable`, `retry` and `over_cap` are not | name the document and its state |
| `{'status': 'refused', 'reason': ..., 'message': ...}` | words the brain did not accept (or, for a shape reason, a disagreement with the plugin) | fix the draft and show it again; for a shape reason update the plugin |
| `{'status': 'throttled', ...}` | the tenant's daily write quota is spent | **stop**, report which drafts landed and which did not |
| `{'retryable': True}` | not written now | one retry, then stop and report |
| a refusal naming a configuration key | the tenant is not provisioned for brain writes | quote the key, stop |

A partial write is a normal outcome and it must be reported as one: "the need and two stories
landed, the four requirements did not — resume at *Pause stops billing for the seat*" is usable.
"Some drafts were recorded" is not.

## Worked example

> **Idea:** "let customers pause a subscription instead of cancelling."

1. **Ask.** "pause subscription" returns three considerations, two confirmed and one an
   agent's unconfirmed draft — noted as such, and cited as such if the draft rests on it. A
   second phrasing, "billing stops but the account stays open", adds a fourth. One consideration
   says *Subscription pause (self-service)* was removed in January 2025 because paused seats
   stayed counted in the monthly invoice, and its references name the decision *Invoices are
   reconciled per seat-day*, confirmed.
2. **What happened.** The reason is quoted as recorded. The decision behind it is confirmed, so
   the constraint is a decision somebody made, not a draft.
3. **Ask the person.** That reason is about how invoices are reconciled, and that has not
   changed. Surface it and ask before drafting: does the new idea address it, or does it inherit
   it? The answer becomes an open question on the draft either way.
4. **Impact.** The repository is bound, so `brain-impact` runs with the precedents handed over.
   The removed feature pointed at `app/billing/subscription.py` and `app/billing/invoice.py`;
   the code graph finds seven usages of the pause path across three files, and the brain holds
   the reconciliation decision about the first of them.
5. **Draft.** Need *Customers can suspend billing without losing their account*; two stories;
   three functional requirements; two technical ones, one of which explicitly states how a
   paused seat is counted — because that is the reason the previous attempt was removed.
6. **Write.** The need first, then its stories, each naming its parent by title in its
   `summary`. Every draft cites the removed feature and the reconciliation decision; the one
   unconfirmed precedent is cited with its state spelled out, so nobody reads it as settled. All
   land as `proposed`; the report says so and prints how many precedents were themselves
   unconfirmed.
