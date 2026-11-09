# Playbook — recording a decision, an issue or a replacement

On-demand detail for the `brain-record-decision` skill: the tool surface, what the brain reads
from a report, the report each kind of record takes, how to read what comes back, and a worked
example end to end. Read the parts a run actually needs.

## The tool surface

| Tool | Used for | Used here |
|---|---|---|
| `mcp__Fairmind__Brain_ask` | what the brain holds on a question, a file or a ticket, as short considerations with their references | yes — Step 2 |
| `mcp__Fairmind__Brain_tell` | report a decision, an issue or a replacement | **yes — a write** |
| `mcp__Fairmind__Brain_answer` | the company brain's answer to a design question, with the decision it used | only to take a node id from an answer this session already holds |

No other brain tool is on this skill's menu. A record's status and review state are decided by
the brain from the report and from who sends it; confirming is a human act performed in the
review queue.

## Asking for precedents

`mcp__Fairmind__Brain_ask` takes `question` (one to two thousand characters), `question_type`
(`file`, `precedent`, `contraindications` or `context`), `project` and, for a file,
`subject: {"file": {"repository": ..., "path": ..., "symbol": ...}}`. A path that ends in `/`
asks about a directory.

- `precedent` — what the brain holds on the idea, in the decision's own words. Ask it the way
  the decision will be asked about later, not the way it was phrased today: the component's
  name, the alternative that lost, the failure that prompted it.
- `contraindications` — what the brain holds against the approach chosen.
- `file` — what the brain holds on a file, in one line naming the path and the decision.

The answer is `considerations` (each a `text`, whether a person confirmed what it rests on,
and its `references`), `further_reading` (records found and not cited), `composed`, `coverage`
and, when nothing could be composed, a `reason`. A reference is a `card`, a `document`, a
`file` (repository and path), a `ticket`, a `pull_request` or a `commit`. On a `file` question a
reference to the asked repository comes back spelled as the `repository` the question sent; on
every other question it carries the catalog's own spelling, so its path is a pointer and
nothing more.

A consideration that is not confirmed is somebody's unconfirmed draft — possibly an agent's —
and is always cited as `{text} (proposed, not yet confirmed)`.

## What the brain does with a report

The brain identifies a decision, an issue or a need by its **title**: the same title sent again
is the same record, so a retry converges; a changed title is a new record. A retell with the
same content answers `duplicate`; with different content, `conflict`, and nothing is
overwritten. To change what a recorded decision says, replace it. The brain chooses the kind
of issue, the links to code and to other records, and the review state; the report carries
none of them.

Text limits the door enforces and never repairs: a title is one line of at most 256
characters; a `summary`, a `question`, an option's `rejected_because` and a reason are at most
1000 characters; at most twelve options, a hundred files, a hundred functions, twenty commit
ids, and ten related items in all (`tickets`, `pull_requests`, `documents` together). A
control character or a line break in a one-line field is a refusal.

## Reports

**A decision** — `intent: "decision"`:

```json
{
  "intent": "decision",
  "repository": "payments-service",
  "project": "6a8c08657fdafcc39df4e48c",
  "origin": "taken",
  "decided_by": "human",
  "title": "Retry payment webhooks with exponential backoff and jitter",
  "summary": "The provider rate-limits bursts; a fixed delay re-synchronised retries after an outage and tripped the limit again. A webhook can now arrive up to 20 minutes late, so consumers must stay idempotent on the event id.",
  "question": "How should failed payment webhooks be retried?",
  "options": [
    {"title": "Exponential backoff with jitter", "chosen": true},
    {"title": "Fixed 30-second delay", "chosen": false, "rejected_because": "retries from every tenant land together after an outage"},
    {"title": "Queue with a dead-letter only", "chosen": false, "rejected_because": "a transient failure would need a person to replay it"}
  ],
  "files": ["app/webhooks/payments.py"],
  "functions": ["app/webhooks/payments.py::deliver_webhook"],
  "documents": [
    {"url": "https://example.com/docs/webhook-delivery-design", "title": "Webhook delivery design", "summary": "The design note the retry policy rests on."}
  ],
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-record-decision"
}
```

Leave out any field you have nothing true to put in. `gate_approved: true` is added only when
the person approved an agent's choice at Step 4; the brain stores it as a record that a person
approved and does not need it to confirm a decision taken here. `task_ref` (the ticket the
decision belongs to) and `task_ref_status` (`done`, `in_progress`, `implemented` or `verified`)
are added only with the person's own words in this conversation. Do not send `status`,
`review_state`, a category, an id or an anchor: the brain sets them, and a report that carries
`status` or `state` at any depth is refused.

**An issue** — `intent: "problem"`:

```json
{
  "intent": "problem",
  "repository": "payments-service",
  "project": "6a8c08657fdafcc39df4e48c",
  "title": "Webhook retries ignore the provider's Retry-After header",
  "summary": "Found while choosing the backoff: deliver_webhook computes its own delay even when the 429 response names one.",
  "files": ["app/webhooks/payments.py"],
  "functions": ["app/webhooks/payments.py::deliver_webhook"],
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-record-decision"
}
```

**A replacement** — a decision report with `replaces`:

```json
{
  "intent": "decision",
  "repository": "payments-service",
  "project": "6a8c08657fdafcc39df4e48c",
  "origin": "taken",
  "decided_by": "human",
  "title": "Retry payment webhooks with exponential backoff and jitter",
  "summary": "The provider rate-limits bursts; a fixed delay re-synchronised retries after an outage and tripped the limit again.",
  "replaces": {
    "node_id": "0123456789abcdef0123456789abcdef",
    "reason": "The fixed delay re-synchronised retries after the March outage and tripped the provider's rate limit again."
  },
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-record-decision"
}
```

`node_id` is the 32-character id of the decision being replaced, taken from a `card` reference
of a `Brain_ask` answer, from a company-brain answer of this session, or from the person. A
guessed id is refused, or replaces nothing.

**A change to a record this skill filed in this run** — the report again, in full, with
`revises` set to that record's `node_id`:

```json
{
  "intent": "problem",
  "repository": "payments-service",
  "project": "6a8c08657fdafcc39df4e48c",
  "title": "Webhook retries ignore the provider's Retry-After header",
  "summary": "deliver_webhook computes its own delay even when the 429 response names one. Seen on the March outage too.",
  "revises": "0123456789abcdef0123456789abcdef",
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-record-decision"
}
```

A revision replaces the stored record whole, so the report carries everything that should
stay, not the addition alone. A decision this skill filed is taken, so it lands confirmed and
is never revised: a `revises` of it is refused with `revise_not_allowed`, and its change is a
replacement (above).

## Reading the responses

| Answer | What it means |
|---|---|
| `state: confirmed` | recorded and confirmed: a decision taken here |
| `state: proposed` | recorded, and waiting for a person in the review queue (an issue; a replacement an agent made) |
| `state: duplicate` | already recorded — **nothing changed**; to change it, replace it |
| `state: conflict` (with a `node_id`) | a record under this title says something different; nothing was overwritten; after the person's yes, revise it with that `node_id` — or replace it, when the `message` offers only `replaces` |
| `replaces_outcome: superseded` | the old decision is no longer current |
| `replaces_outcome: proposed` | a proposal for a person to confirm was filed; the old decision is **still current** |
| `replaces_outcome: kept` | the old decision could not be ended (a person rejected it, a successor already replaced it, or this key has no role on its project); no proposal was filed |
| `replaces_outcome: not_replaced` | the report named no replacement that applied |
| `related[]` | one entry per related document: `created`, `updated`, `unchanged` or `proposed` is linked; `unavailable`, `retry` and `over_cap` are not |
| `{'status': 'refused', 'reason': 'revise_not_allowed', ...}` | the decision named in `revises` is not revised: the change is a replacement |
| `{'status': 'refused', 'reason': ..., 'message': ...}` | words the brain did not accept (or, for a shape reason, a disagreement with the plugin) — Step 5 |
| `{'status': 'throttled', ...}` | the daily quota is spent — stop |
| `{'retryable': True}` | not written now; one retry at most |
| access denied | this key may not write to the project |

## A worked example

The person, after a long thread about webhook delivery: *"Record in the brain that we're going
with exponential backoff and jitter — and file the Retry-After thing as a bug, we're not fixing
it today."*

1. **Preconditions.** `active-context.json` names the project and a bound repository; both
   tools are present.
2. **Route.** One decision, one issue. Neither is a requirement or a document.
3. **Precedents.** A `precedent` question in the decision's words returns a consideration
   saying the retries use a fixed 30-second delay, confirmed, with a `card` reference. That is a
   different decision on the same ground, so the skill asks: *does the new one replace it, and
   why?* The person: yes — the fixed delay tripped the rate limit after the March outage. This is
   now a **replacement**, and the old decision's id is the card reference's `id`, shown to the
   person before it is sent.
4. **Draft.** The replacement report as above; the issue.
5. **Ask.** Two records, their titles, the description, the file and function, the repository
   and project, and the reason for the replacement. The person says yes.
6. **Write.** One `Brain_tell` for the replacement: `state: confirmed`, `replaces_outcome:
   superseded`. One for the issue: `state: proposed`.
7. **Report.**

```markdown
**Decisions: taken — recorded where it was taken.**
**Issues: proposed — awaiting human review.** No issue below is confirmed knowledge.

- replacement · Retry payment webhooks with a fixed delay (`0123456789abcdef0123456789abcdef`) → Retry payment webhooks with exponential backoff and jitter (`fedcba9876543210fedcba9876543210`) · confirmed · superseded · reason: "The fixed delay re-synchronised retries after the March outage and tripped the provider's rate limit again."
- issue · Webhook retries ignore the provider's Retry-After header · 6a8c08657fdafcc39df4e48c · proposed · node `00112233445566778899aabbccddeeff`

Precedents: the retry delay is fixed at 30 seconds (confirmed).

Confirm or reject each issue in the review queue — this skill confirms nothing.
```
