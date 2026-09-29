# Playbook — a new requirement from what exists

On-demand detail for the `brain-new-requirement` skill: how to search so precedents actually
surface, how to read a timeline, the reference shapes the write door takes, and a worked
example end to end.

## The tool surface

| Tool | Used for | Used here |
|---|---|---|
| `mcp__Fairmind__Brain_search` | text and meaning search across the brain | yes — Step 2 |
| `mcp__Fairmind__Brain_timeline` | a record's history: status changes, retirement, reason, the decision behind it | yes — Step 3 |
| `mcp__Fairmind__Brain_context` | knowledge anchored on a file, symbol or directory | yes — Step 4, the light sketch when no repository is bound |
| `mcp__Fairmind__Brain_get` | one record's body, in chunks | on demand only |
| `mcp__Fairmind__Brain_expand` | the chunks around one already fetched | on demand only |
| `mcp__Fairmind__Brain_record_requirement` | record a draft | **yes — the only write** |
| `mcp__Fairmind__Brain_record_issue` | record an issue draft | no |
| `mcp__Fairmind__Brain_record_decision` | record an architectural decision | no |
| `mcp__Fairmind__Brain_supersede_decision` | replace a decision with a newer one | no |
| `mcp__Fairmind__Brain_set_status` | change a record's status | **never** |

🔴 **`mcp__Fairmind__Brain_set_status` is not on this skill's menu**, including for the drafts
it just wrote. Confirmation is a human act performed in the review queue.

## Searching so precedents actually surface

`mcp__Fairmind__Brain_search` takes `query`, `project`, `kinds`, `status`, `review_state`,
`include_invalidated`, `date_from`, `date_to`, `time_cutoff`, `k` and `cursor`. It pages: the
response carries `result_count`, `results_omitted` and `next_cursor`, and a `next_cursor` that
is not null is the next page of the same request — sent back as `cursor`, with the same
filters, until it comes back null. A precedent search stops after five pages and says so when
a cursor was still there.

**`kinds`, `status` and `review_state` each take one value or a list.** The removal states are
one call, `status: ["retired", "superseded"]`, and a terminal status in the filter brings back
the records that left the current view.

A search that finds nothing is usually a search phrased in today's words:

1. **The user's words, verbatim.** "let customers pause a subscription".
2. **The domain noun on its own.** "subscription pause", "dunning", "grace period".
3. **The behaviour, not the feature name.** Feature names change; behaviours do not. "billing
   stops but the account stays open".
4. **The failure it prevents.** Many requirements are recorded as the incident that produced
   them: "customers cancelled instead of pausing".
5. **The adjacent noun.** A precedent often lives one concept away — "trial extension",
   "account hold", "suspension".

Then, for each phrasing that returned anything, run it again for the removal states —
`status: ["retired", "superseded"]`, in one call. Those results carry the recorded `reason`.

Widen `kinds` beyond requirements. Tasks say what was actually built, issues say what broke,
decisions say why it changed — and a feature's real history is usually in the last two.

## Reading a timeline

`mcp__Fairmind__Brain_timeline` takes a `knowledge_id` (or an anchor) and returns
`{'events': [...], 'chains': [...], 'evidence': {...}}`. What to pull out:

- **`created`** — when the requirement first existed.
- **Each status change** — the path from draft to whatever it is now.
- **`retired`** — the date, the **`reason`** as recorded, and the decision reference behind it.
- **The supersession chain** — what replaced it, and what replaced that. Superseded records are
  labelled, never hidden, so a chain three links long is readable end to end.

Report the reason **as a quote**. "Removed because per-seat pausing made the invoice
reconciliation ambiguous" is actionable; "removed for billing reasons" is a paraphrase that
loses the very thing the reader needed.

**When a timeline records a retirement with no reason**, say exactly that: "retired on
{date}, no reason recorded". Inventing a plausible reason is the single worst failure this
skill can produce — it manufactures a decision nobody made and it is indistinguishable from a
real one afterwards.

## The impact step: `brain-impact` first, the sketch only without a repository

With a bound `repository_id`, Step 4 is `brain-impact`. Hand it the idea and the precedents
Step 2 found — node ids plus both states — and it skips its own search. What comes back is a
dependency-aware answer in its usages-only mode: the precedents' anchors with their resolved
tiers, the dependents of each symbol from the code graph, the architecture components the
files sit in and the components that depend on those, and the decisions, incidents and
criteria recorded at each place. It records nothing; its findings reach the brain through
this skill's own write step, as the drafts' anchors and evidence.

With no repository bound, `brain-impact` cannot run, and Step 4 falls back to a sketch. Take
the anchors of the closest precedents, group them by directory, and call
`mcp__Fairmind__Brain_context` per directory with `granularity: "file"` and
`detail_level: "minimal"`. That gives, per module: what is anchored there now, and
`uncovered[]` for the files nothing explains.

State the limit in the output. The sketch:

- **does** show where the previous attempt lived and what knowledge sits there today;
- **does not** follow call graphs, imports or runtime dependencies;
- **does not** claim to be complete.

Name in the report which of the two produced the impact section. Two analyses of different
strengths presented in the same voice is how a sketch gets read as a guarantee.

## Reference shapes on the write door

`mcp__Fairmind__Brain_record_requirement` takes `project`, `req_type`, `title`, `text`,
`natural_key`, `refines`, `anchors`, `precedent_refs`, `status`, `evidence`, `agent`,
`session_ref`, `task_ref`.

- **`refines`** points at the parent: a user story refines its need, a functional requirement
  refines its story, a technical requirement refines the functional one it constrains. Send it
  as `[{"node_id": <id>}]` — **the JSON key is always `node_id`**, never `knowledge_id`, never
  the natural key. The `<id>` value is what the parent's own write response returned:
  `knowledge_id` on a `{'status': 'ok', ...}` response, or `target` when the parent's write
  came back `revision_proposed` instead (that response carries no `knowledge_id`). The
  platform stores a transformed form of the natural key you sent, so a raw natural-key
  reference silently derives the wrong node id and the edge is never built; nothing about the
  response says so. This is why the need is recorded **first**: no id to refine by exists
  until its write responds, and every story needs that value.
- **`precedent_refs`** are node ids of what the draft rests on, including retired records. A
  retired precedent is often the most informative thing on the draft. The field carries node
  ids only — it has no room for a review state, which is why the state belongs in the draft's
  own `text` and in the report: `precedent: {node_id} (proposed, not yet confirmed)`. A
  reviewer reading the recorded draft has nothing else to tell a confirmed record from an
  agent's guess.
- **`anchors`** carry `file_path` plus `repo_ref` and `repo_ref_scheme` (the bound
  `repository_id` and the literal `"code-ingestion"` — this write door does not resolve a
  repository name into an id itself) and, where known, `symbol_name`, `symbol_type`
  (`FUNCTION`, `CLASS`, `FILE`) and `start_line_hint`. Only anchor what the sketch actually
  found; a new requirement legitimately has none. When `repository_id` is not bound, omit the
  anchor rather than send one without `repo_ref`, and say so explicitly in the report next to
  that draft — distinct from "no anchor found," which is a different case.
- **`evidence`** holds the short quotes the draft rests on — a retirement reason, a line from a
  decision. Quote, do not paraphrase.
- **`natural_key`** is required and client-supplied; the platform never synthesizes one.

## The natural key

```
natural_key = "<module-or-feature>:<slug of the title>"
slug        = title lowercased, every run of non-alphanumerics collapsed to "-", trimmed
```

Pick the prefix once per idea and reuse it for every draft in the set — the need, its stories,
and the requirements under them. Write every key into the report. That is what makes the second
run on the same idea update these drafts instead of minting a parallel set, and it is what lets
a different person reproduce the run.

## Response shapes

| Response | Meaning | Do |
|---|---|---|
| `{'status': 'ok', 'knowledge_id': …, …}` | the draft landed | count it, carry `knowledge_id` and the natural key into the report — and **capture `knowledge_id`**, it is the value the next draft's `refines` needs. There is no `review_state` on this response; do not wait for one |
| `{'status': 'revision_proposed', 'proposal_id': …, 'target': …}` | the key matches a record a human confirmed; the record was **not** touched | report the proposal id, continue — and if this item has children, **capture `target`** as the id they refine by (this response has no `knowledge_id`) |
| `{'status': 'throttled', 'quota': N}` | the tenant's daily write quota is spent | **stop**, report which drafts landed and which did not |
| `{'retryable': True}` | the quota service was unreachable | one retry, then stop and report |
| a refusal naming a configuration key | the tenant is not provisioned for brain writes | quote the key, stop |

A partial write is a normal outcome and it must be reported as one: "the need and two stories
landed, the four requirements did not — resume at `subscription-pause:…`" is usable. "Some
drafts were recorded" is not.

## Worked example

> **Idea:** "let customers pause a subscription instead of cancelling."

1. **Search.** "pause subscription" (2 hits), "subscription hold" (1), "billing stops account
   open" (0), then the removal pass, `status: ["retired", "superseded"]`: it returns
   *Subscription pause (self-service)*, retired, and nothing superseded. Of the three live hits, two
   carry `review_state: confirmed` and one is an agent's `proposed` draft — noted as such, and
   it will be cited as such if the draft rests on it.
2. **Timeline** on that record: created 2024-03, live 2024-06, retired 2025-01. Reason as
   recorded: *"paused seats stayed counted in the monthly invoice, so reconciliation could not
   tell a pause from a downgrade"*. Decision behind it: *Invoices are reconciled per seat-day*,
   `review_state: confirmed` — so the constraint is a decision somebody made, not a draft.
3. **Ask.** That reason is about how invoices are reconciled, and that has not changed. Surface
   it and ask the user before drafting: does the new idea address it, or does it inherit it?
   The answer becomes an open question on the draft either way.
4. **Impact.** The repository is bound, so `brain-impact` runs with the three precedents
   handed over. The retired record anchored on `app/billing/subscription.py` and
   `app/billing/invoice.py`; the code graph finds seven usages of the pause path across three
   files, all inside the *Billing* component, which *Invoicing* depends on. The
   reconciliation requirement and its decision are recorded there; eleven files carry
   nothing.
5. **Draft.** Need *Customers can suspend billing without losing their account*; two stories;
   three functional requirements; two technical ones, one of which explicitly states how a
   paused seat is counted — because that is the reason the previous attempt was removed.
6. **Write.** Prefix `subscription-pause`; the need first, capture its `knowledge_id` from the
   write response, then the stories naming that `knowledge_id` in `refines`. Every draft
   carries the retired record in `precedent_refs` and the reconciliation decision in its text
   — the one `proposed` precedent is cited in that text with its state spelled out, so nobody
   reads it as settled. All land as `proposed`; the report says so, prints the natural keys for
   readability, and prints how many precedents were themselves unconfirmed.
