# Playbook — recording a decision, an issue or a replacement

On-demand detail for the `brain-record-decision` skill: the tool surface, the id rules, the
payload each write door takes, how to read what comes back, and a worked example end to end.
Read the parts a run actually needs.

## The tool surface

| Tool | Used for | Used here |
|---|---|---|
| `mcp__Fairmind__Brain_search` | text and meaning search across the brain | yes — Step 2, with `include_invalidated: true` |
| `mcp__Fairmind__Brain_context` | knowledge anchored on a file or a symbol | yes — Step 2, on the files the record is about |
| `mcp__Fairmind__Brain_get` | one record's body, in chunks | Step 2 — a precedent's full title, to rebuild its id; the whole of an issue about to be updated |
| `mcp__Fairmind__Brain_timeline` | a record's history: what replaced what, when, and why | when a precedent's history matters |
| `mcp__Fairmind__Brain_record_decision` | record decisions, as one batch | **yes — a write** |
| `mcp__Fairmind__Brain_record_issue` | record one issue | **yes — a write** |
| `mcp__Fairmind__Brain_supersede_decision` | replace one decision with a newer one | **yes — a write** |
| `mcp__Fairmind__Brain_record_requirement` | record a requirement draft | no — that is `brain-new-requirement` |
| `mcp__Fairmind__Brain_set_status` | change a record's status | **never** |

🔴 **`mcp__Fairmind__Brain_set_status` is not on this skill's menu**, including for the
records it just wrote. Confirmation is a human act performed in the review queue.

## Searching for a competing decision

`mcp__Fairmind__Brain_search` takes `query`, `project`, `kinds`, `status`, `review_state`,
`include_invalidated`, `date_from`, `date_to`, `time_cutoff` and `k`.

- **`include_invalidated: true`** is what makes the search historical. Without it only
  current knowledge comes back, and the decision that was already tried and dropped — the
  one most worth knowing about — stays hidden.
- **`kinds: "decision"`** for a decision or a replacement, **`kinds: "issue"`** for an issue.
  `kinds` takes a list as well, when a decision might have been recorded as something else.
- **`status` takes one value or several.** `status: "superseded"` shows only the replaced
  decisions; naming `rejected` beside it in the same call brings back both kinds of dropped
  decision at once.

Search the decision the way it will be asked about later, not the way it was phrased today:
the component's name, the alternative that lost, the failure that prompted it.

## Two axes on every result

`status` is the decision's lifecycle — does it still hold. `review_state` is whether a
**person** ever agreed with the record — is it knowledge or somebody's draft. A superseded
decision can be confirmed knowledge; a current-looking one can be an agent's proposal from
last week. Carry both, and cite a proposal as `(proposed, not yet confirmed)`.

## Ids

**`decisionId` — minted by this skill:**

```
decisionId = "decision:<project_id>:<slug of the title>"
```

- `<project_id>` is the value in `.fairmind/active-context.json`. It keeps two projects'
  decisions of the same title apart: the platform treats `decisionId` as unique across the
  whole company, not per repository.
- `<slug of the title>` is the exact title sent, lowercased, every run of non-alphanumerics
  folded to one `-`, trimmed of `-` at both ends. It stays under 80 characters by shortening
  the title itself before it is recorded, never by cutting the slug: the recorded title must
  slugify to its own id, or a later run cannot rebuild the id from it.
- Never begin it `sha256:` — that shape belongs to the decision log the loop flushes, and a
  collision with it would drop one of the two.
- A replacement takes a new id. If its slug matches its predecessor's, add `-2`, `-3`, the
  first suffix no decision of that title already holds — the precedent search, run with
  `include_invalidated: true`, lists every one of them, current or not. An id that is
  already held is not written, and the supersession still ends the old decision.

**For a decision already in the brain, the card's `natural_key` is its `decisionId`.** That is
the value `old_decision_id` and `relates.caused_by` take. No read door publishes it: a search
result, a context bundle and a `mcp__Fairmind__Brain_get` card carry the `node_id`. Nor does a
read say who first recorded a card: `provenance.agent`, and a timeline event's `by`, name the
**last** writer — a status change carries the subject of whoever made it, and a later write by
another agent replaces the name. The id is therefore taken from what this skill controls: the
`decisionId` its own report printed when it recorded the decision, one the person supplies, or
one rebuilt with the rule above from the full title `mcp__Fairmind__Brain_get` returns (a search
title is cut short) — rebuilt only on the person's word that this skill recorded it, and shown
to them before it is sent. Any other decision — a loop's `sha256:` id included — can only be
named with an id the person supplies.

**`natural_key` for an issue — authored by this skill:**

```
natural_key = "<module-or-area>:<slug of the title>"
```

The platform stores it as `agent:<slug>` and derives the node from that, so the same key sent
twice updates one issue. Choose the module-or-area prefix from the names the brain already
uses for that part of the code, and do not rename it between runs: a renamed prefix is a
second issue.

**An update is a replacement, not an append.** The second call's title, body, anchors,
evidence and `relates` become the stored issue, whole; what the first call carried and the
second does not is gone, and no history keeps it. `mcp__Fairmind__Brain_get` gives back the
title, the body and the anchors; the evidence and the `relates` are returned by no read door,
so the person supplies them again or drops them knowingly. An issue is updated from this skill
only under a key it minted — printed by the report of the run that filed it, or supplied or
confirmed by the person. The card cannot say whose it is: `provenance.agent` names its last
writer, and every agent's issue keys share the `agent:` namespace, so a key is the only thing
that keeps a write off another producer's issue.

## Payloads

**`mcp__Fairmind__Brain_record_decision`** — one call per run:

```json
{
  "repository": "<repository_id, or the repository's name>",
  "project": "<project_id>",
  "contract_version": "fm-insights.decision/2",
  "decisions": [
    {
      "decisionId": "decision:<project_id>:retry-payment-webhooks-with-exponential-backoff-and-jitter",
      "ts": "2026-01-15T10:42:00Z",
      "agent": "brain-record-decision",
      "kind": "architecture",
      "title": "Retry payment webhooks with exponential backoff and jitter",
      "rationale": "The provider rate-limits bursts; a fixed delay re-synchronised retries after an outage and tripped the limit again.",
      "options": [
        {"title": "Fixed 30-second delay", "rejected_because": "retries from every tenant land together after an outage"},
        {"title": "Queue with a dead-letter only", "rejected_because": "a transient failure would need a person to replay it"}
      ],
      "consequences": "A webhook can arrive up to 20 minutes late; consumers must stay idempotent on the event id.",
      "files": ["app/webhooks/payments.py"],
      "functions": [{"file_path": "app/webhooks/payments.py", "name": "deliver_webhook"}],
      "requirement_refs": ["<node id of the requirement it serves>"],
      "document_refs": ["<node id of the document it rests on>"]
    }
  ]
}
```

Leave out any field you have nothing true to put in. `start_line` may join a `functions` entry
when you have read the line — a wrong line links nothing, so do not guess one. `valid_at` may
be set when the person says the decision was taken earlier than today; otherwise it is `ts`.
Do not send `review_state`, `status_history` or `actor_kind`: the platform sets them from who
is calling, and a row cannot declare its own approval.

**`mcp__Fairmind__Brain_record_issue`** — one call per issue:

```json
{
  "project": "<project_id>",
  "issue_kind": "bug",
  "title": "Webhook retries ignore the provider's Retry-After header",
  "body": "Found while choosing the backoff: deliver_webhook computes its own delay even when the 429 response names one.",
  "natural_key": "payments-webhooks:webhook-retries-ignore-the-provider-s-retry-after-header",
  "anchors": [
    {"repo_ref": "<repository_id>", "repo_ref_scheme": "code-ingestion",
     "file_path": "app/webhooks/payments.py", "symbol_name": "deliver_webhook",
     "symbol_type": "FUNCTION"}
  ],
  "evidence": [{"kind": "quote", "text": "delay = base * 2 ** attempt", "source": "app/webhooks/payments.py"}],
  "relates": {"caused_by": ["decision:<project_id>:retry-payment-webhooks-with-exponential-backoff-and-jitter"]},
  "agent": "brain-record-decision"
}
```

`relates` also takes `duplicates` and `resolves`, and those two carry **node ids**, not
decision ids — the three are read in different id spaces on purpose.

**`mcp__Fairmind__Brain_supersede_decision`** — one call per replacement:

```json
{
  "old_decision_id": "decision:<project_id>:retry-payment-webhooks-with-a-fixed-delay",
  "reason": "The fixed delay re-synchronised retries after the March outage and tripped the provider's rate limit again.",
  "new_decision": { "...": "a full decision row, as above" },
  "repository": "<repository_id, or the repository's name>",
  "project": "<project_id>"
}
```

`new_decision_id` replaces `new_decision` when the replacement is already recorded; pass
exactly one of the two.

## Reading the responses

| Door | Answer | What it means |
|---|---|---|
| record decision | `inserted: 1` | recorded, as `proposed` |
| record decision | `duplicates: 1` and a warning naming the id | already recorded — **nothing changed**; to change it, supersede it |
| record decision | an `edges` entry marked `target_missing` | a `requirement_refs` or `document_refs` id the brain could not find |
| record issue | `{'status': 'ok', 'knowledge_id': ...}` | recorded — or, for a key already used, the stored issue **replaced** by this call's title, body, anchors, evidence and `relates` |
| record issue | `possible_duplicates: [{knowledge_id, score, threshold}]` | records that read like this one — list them in the report |
| record issue | `detection.duplicates.status: disabled` (also `embedding.duplicate_detection.status: disabled`) | the duplicate check is switched off for the tenant — report "not checked — duplicate detection is off", never "none" |
| record issue | `detection` with no `duplicates` entry, or a duplicates status other than `complete` or `replayed` | the check did not finish — "not checked", with the status or reason |
| record issue | `{'status': 'revision_proposed', ...}` | the key belongs to an issue a person confirmed; a proposal was filed instead |
| supersede | `{'status': 'ok', 'superseded': ..., 'superseded_by': ..., 'decisions': {...}}` with `inserted: 1` inside `decisions` | the replacement is recorded; the old decision is invalidated, not removed, and stays readable in its history |
| supersede | `duplicates: 1` inside `decisions` | the replacement was **not** written — a decision with its id already existed — and the old decision was invalidated all the same, by that earlier row |
| supersede | a warning inside `decisions` saying the old decision could not be superseded | the replacement is recorded and the old decision **is still current** — its id matched nothing, or it belongs to a project the caller may not write to |
| any | `{'status': 'throttled', ...}` | the daily quota is spent — stop |
| any | `{'retryable': True}` | not written now; one retry at most |

## A worked example

The person, after a long thread about webhook delivery: *"Record in the brain that we're going
with exponential backoff and jitter — and file the Retry-After thing as a bug, we're not fixing
it today."*

1. **Preconditions.** `active-context.json` names the project and a bound repository; the five
   tools are present.
2. **Route.** One decision, one issue. Neither is a requirement or a document.
3. **Precedents.** A search for "webhook retry" with `kinds: "decision"` and
   `include_invalidated: true` returns *"Retry payment webhooks with a fixed delay"*, status
   `taken`, review state `confirmed`. That is a different current decision on the same
   ground, so the skill asks: *does the new one replace it, and why?* The person: yes — the
   fixed delay tripped the rate limit after the March outage. This is now a **supersession**,
   and it needs the old decision's id. The person says this skill recorded it, so the id is
   rebuilt from the full title `mcp__Fairmind__Brain_get` returns and shown to them before it
   is sent: `decision:<project_id>:retry-payment-webhooks-with-a-fixed-delay`. The card's
   `provenance.agent` is not the evidence — it names the last writer, not the first.
4. **Draft.** The replacement row as above; the issue with `caused_by` pointing at the new
   decision's id.
5. **Ask.** Two records, their titles, the rationale, the file and function, the repository
   and project, and the reason for the replacement. The person says yes.
6. **Write.** One `mcp__Fairmind__Brain_supersede_decision` carrying the new row, then one
   `mcp__Fairmind__Brain_record_issue`, whose answer carries an empty `possible_duplicates`
   and `detection.duplicates.status: complete` — the check ran and found nothing.
7. **Report.**

```markdown
**Status: proposed — awaiting human review.** Nothing below is confirmed knowledge.

- supersession · Retry payment webhooks with a fixed delay (`decision:<project_id>:retry-payment-webhooks-with-a-fixed-delay`) → Retry payment webhooks with exponential backoff and jitter (`decision:<project_id>:retry-payment-webhooks-with-exponential-backoff-and-jitter`) · proposed · reason: "The fixed delay re-synchronised retries after the March outage and tripped the provider's rate limit again."
- issue · Webhook retries ignore the provider's Retry-After header · <project> · proposed · key `payments-webhooks:webhook-retries-ignore-the-provider-s-retry-after-header` · possible duplicates: none

Confirm or reject each record in the review queue — this skill confirms nothing.
```
