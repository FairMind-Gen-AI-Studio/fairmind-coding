# Playbook — rebuilding requirements from what exists

On-demand detail for the `brain-rebuild-requirements` skill: the tool surface, the module
heuristics, the natural-key rule, and what each response shape means. Read the parts a run
actually needs.

## The tool surface

Ten brain tools exist. This skill uses five of them, and **writes with exactly one**.

| Tool | Used for | Used here |
|---|---|---|
| `mcp__Fairmind__Brain_context` | knowledge anchored on a file, a symbol or a directory | yes — Step 2, the coverage engine |
| `mcp__Fairmind__Brain_search` | text and meaning search across the brain | yes — Step 2, the unanchored tier |
| `mcp__Fairmind__Brain_get` | one record's body, in chunks | on demand only |
| `mcp__Fairmind__Brain_expand` | the chunks around one already fetched | on demand only |
| `mcp__Fairmind__Brain_timeline` | a record's history: status changes, retirement, reasons | when a precedent's history matters |
| `mcp__Fairmind__Brain_record_requirement` | record a requirement draft | **yes — the only write** |
| `mcp__Fairmind__Brain_record_issue` | record an issue draft | no |
| `mcp__Fairmind__Brain_record_decision` | record an architectural decision | no |
| `mcp__Fairmind__Brain_supersede_decision` | replace a decision with a newer one | no |
| `mcp__Fairmind__Brain_set_status` | change a record's status | **never — see below** |

Plus the code-reading tools the plugin already uses: `mcp__Fairmind__Code_tree`,
`mcp__Fairmind__Code_search`, `mcp__Fairmind__Code_grep`, `mcp__Fairmind__Code_cat`,
`mcp__Fairmind__Code_find_usages`, `mcp__Fairmind__Code_analyze_repo`, and
`mcp__Fairmind__Code_list_repositories`.

🔴 **`mcp__Fairmind__Brain_set_status` is not on this skill's menu.** It is how a record's
status changes, and a reconstruction has no standing to change the status of anything —
including its own drafts. Confirmation is a human act performed in the review queue.

## Parameters worth knowing

**`mcp__Fairmind__Brain_context`** — one call answers either a *file/symbol* question or a
*directory* question:

- File or symbol: `file_path`, optionally `symbol_name`, `symbol_type`, `start_line`.
- Directory (what this skill uses): `directory`, `granularity: "file"` (or `"symbol"` to group
  within each file).
- Always: `project`, `repository`, `detail_level: "minimal"`, `max_items`, `include_proposed`,
  `kinds`, `as_of`.

It returns items carrying `node_id`, `kind`, `subtype`, `title`, `summary`, `status`,
`review_state`, `anchor_confidence`, `valid_at` and `provenance`, plus `unresolved[]`, an
`omitted` count, and a staleness envelope: `repo.catalog_id`, `repo.last_synced_at`, `repo.sync_state`,
`graph_available`, and `resolution` counts across the tiers `extracted`, `inferred`,
`ambiguous`, `unresolved`, `semantic`. On a directory call it also returns the per-file counts
and **`uncovered[]`**.

**`mcp__Fairmind__Brain_search`** — `query`, `project`, `kinds`, `status`, `review_state`,
`date_from`, `date_to`, `time_cutoff`, `k`, `cursor`. `status` takes one lifecycle value or a
list — `["retired", "superseded"]` is one call — and results matching a terminal status carry
the recorded `reason`. `kinds` and `review_state` take one value or a list as well;
`review_state` filters the other axis: `proposed`, `confirmed` or `rejected`. The response
reports `result_count`, `results_omitted` and `next_cursor`: a `next_cursor` that is not null is
the next page, sent back as `cursor` with the same filters until it comes back null.

**Two axes, and mixing them up is the expensive mistake.** `status` is the record's lifecycle
— is this behaviour still wanted. `review_state` is whether a **person** ever agreed with the
record — is this knowledge or somebody's draft. A retired requirement can be confirmed
knowledge; a live-looking one can be an agent's unconfirmed proposal from last week. The
report has to keep them in separate columns.

**`mcp__Fairmind__Brain_record_requirement`** — `project`, `req_type`, `title`, `text`,
`natural_key`, `refines`, `anchors`, `precedent_refs`, `status`, `evidence`, `agent`,
`session_ref`, `task_ref`. `natural_key` is **required and never synthesized by the platform**.
The recorded `review_state` is always `proposed`, whatever else the call says.

## Anchors

An anchor is what makes a draft evidence rather than an assertion. Supply:

- `repo_ref` and `repo_ref_scheme` — the bound `repository_id` from Step 0, and the literal
  string `"code-ingestion"`. This write door does not resolve a repository name into an id the
  way `Brain_record_decision` does; send the id yourself. **Missing either field is refused**
  — the whole write fails with the quota already spent, not a silent degradation. (A *wrong*
  `repo_ref_scheme` value — anything other than `"code-ingestion"` — is the one that is
  accepted but never probed against the code graph; that is a different mistake from omitting
  the field.)
- `file_path` — repo-relative, exactly as the code tools spell it.
- `symbol_name` and `symbol_type` (`FUNCTION`, `CLASS` or `FILE`) when the requirement is about
  a specific symbol rather than a whole file.
- `start_line_hint` when known — a line that has moved degrades the anchor's confidence rather
  than breaking it, which is why the hint is worth sending even if it may be stale.

The platform fills in the resolution state and the provenance. Do not invent those fields, and
do not invent an anchor to satisfy the "at least one anchor" rule — an item with no honest
anchor belongs in the unanchored list.

## Refines

`refines` takes the parent's `node_id`, never its natural key — the platform stores a
transformed form of the key you send (a prefix and a re-slugify), and a `refines` reference
resolves by an exact string match against that stored form. A raw natural-key reference does
not raise an error; it silently derives the wrong node id, so the edge is never built and
nothing says so. Every parent this skill can refine — a need or a user story — already carries
its `node_id` from the Step 2 `Brain_context`/`Brain_search` reads; use that value.

## Grouping files into modules

In order of preference:

1. **Deployable or packaged boundaries** — a service, a package, a published library.
2. **The names the brain already uses.** If existing items cluster on `app/billing/` and call
   it *Billing*, the module is `billing`. Matching the existing name is what keeps natural keys
   stable across runs, and stable keys are the whole convergence guarantee.
3. **Top-level source directories** — the fallback, and a perfectly good one.

Two anti-patterns:

- **One module per directory, all the way down.** Fifty modules produce fifty thin reports and
  a coverage number nobody can act on. Aim for a list a person can hold in their head.
- **Renaming a module between runs.** The module name is half of the natural key; renaming it
  makes the second run mint a parallel set of drafts instead of updating the first.

## The natural key

```
natural_key = "<module>:<slug of the title>"
slug        = title lowercased, every run of non-alphanumerics collapsed to "-", trimmed
```

Examples:

| module | title | natural key |
|---|---|---|
| `billing` | Invoices are issued monthly | `billing:invoices-are-issued-monthly` |
| `auth` | Session tokens expire after 30 minutes | `auth:session-tokens-expire-after-30-minutes` |

The platform slugifies what it is given into its own identifier space; the point of deriving
the key deterministically here is that **the same input produces the same key**, so a rerun
updates rather than duplicates. Write every key used into the report — that is what makes the
next run reproducible by a different person, or a different agent.

## Response shapes

| Response | Meaning | Do |
|---|---|---|
| `{'status': 'ok', 'knowledge_id': …, …}` | the draft landed | count it, carry `knowledge_id` and the natural key into the report. There is no `review_state` on this response — the record's stored `review_state` is `proposed`, but that key is not echoed back; do not wait to see it |
| `{'status': 'revision_proposed', 'proposal_id': …, 'target': …}` | the key matches a record a human confirmed; the record was **not** touched | report the proposal id, continue |
| `{'status': 'throttled', 'quota': N}` | the tenant's daily write quota is spent | **stop**, report what was written and what remains |
| `{'retryable': True}` | the quota service was unreachable | one retry, then stop and report |
| a refusal naming a configuration key | the tenant is not provisioned for brain writes | quote the key, stop |
| `{'message': …}` with nothing else | an infrastructure failure behind the door | report it verbatim, do not retry blindly |

**Why the throttle rule is absolute.** Every refused attempt is counted against the tenant. A
retry loop against a spent quota burns the next day's headroom and produces nothing. Stopping
with an accurate "wrote 14 of 37, resume at `billing:…`" is strictly better than a loop.

## Working within a token budget

- `detail_level: "minimal"` is the default for a reason: it is sized so a full bundle stays
  small. `standard` returns full summaries, anchors and edge names — use it for one record, not
  for a sweep.
- Process **one module at a time**, end to end, and write its section of the report before
  moving on. A run that dies half way then leaves a usable half-report.
- The two `mcp__Fairmind__Brain_context` passes per module (`include_proposed` true, then
  false) are the one place this skill deliberately pays twice. At `detail_level: "minimal"`
  the second call is cheap, and it buys the only coverage number a rerun can be compared on.
- Fetch bodies (`mcp__Fairmind__Brain_get`) only for records a draft actually depends on. Most
  drafts need a title, a status and a node id.
- When `results_omitted` is large, the query is too broad. Narrow it — there is nothing to page.

## Reading your own previous output back

From the second run onwards, part of what the brain returns for a module **is this skill's own
earlier output**. Nothing in a node id says so; `review_state` does.

Three places it matters, in the order a run meets them:

1. **Coverage.** A module whose only brain content is run 1's drafts is still uncovered in the
   sense that matters — nobody has confirmed anything about it. That is why the coverage pass
   filters proposals out.
2. **Precedents.** A draft may cite a proposal, but the citation carries `(proposed, not yet
   confirmed)` so a reviewer can see the grounding is a guess. A chain of drafts each citing
   the last one looks, from the outside, exactly like accumulated knowledge.
3. **The yield.** Print the confirmed count beside the drafted count. A second run that
   reports "42 drafts" without saying "0 confirmed" reads as twice the progress of the first.

Natural keys do **not** cover this. They guarantee node *identity* across runs, which is what
stops duplicates; they say nothing about whether a human has looked at what is behind the key.

## Contradictions

A contradiction is: the brain records one behaviour, the code implements another. Both are
evidence, and which one is *right* is a question for a person who knows why.

Write it as a pair, always with both sources:

```
- The brain records that refunds are manual-approval only (req-4711, confirmed 2025-11-02).
  `app/billing/refund.py:auto_approve` approves refunds under 50 EUR without review.
```

Do not resolve it, do not draft a requirement for either side, and do not average them into a
sentence that describes neither. List it and move on — resolving it silently is how a decision
somebody made deliberately disappears.
