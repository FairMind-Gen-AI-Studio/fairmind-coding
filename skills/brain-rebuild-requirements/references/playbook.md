# Playbook — rebuilding requirements from what exists

On-demand detail for the `brain-rebuild-requirements` skill: the tool surface, the module
heuristics, the title rule, the report shape and what each response means. Read the parts a
run actually needs.

## The tool surface

| Tool | Used for | Used here |
|---|---|---|
| `mcp__Fairmind__Brain_ask` | what the brain holds about a directory, a precedent or a ticket, as short considerations with their references | yes — Step 2 |
| `mcp__Fairmind__Brain_tell` | report a requirement | **yes — the only write** |

Plus the code-reading tools the plugin already uses: `mcp__Fairmind__Code_tree`,
`mcp__Fairmind__Code_search`, `mcp__Fairmind__Code_grep`, `mcp__Fairmind__Code_cat`,
`mcp__Fairmind__Code_find_usages`, `mcp__Fairmind__Code_analyze_repo`, and
`mcp__Fairmind__Code_list_repositories`.

A reconstruction has no standing to change the status of anything, including its own drafts.
Confirmation is a human act performed in the review queue, and no report this skill sends
carries a status.

## Parameters worth knowing

**`mcp__Fairmind__Brain_ask`** — `question` (one to two thousand characters), `question_type`
(`file`, `precedent`, `contraindications` or `context`), `project` and, for a file or a
directory, `subject: {"file": {"repository": ..., "path": ..., "symbol": ...}}`. A path that
ends in `/` asks about a directory: the brain reads the items with a file under that prefix and
the repository's decisions, never caches the answer and takes no `symbol`.

The answer is `considerations` (each a `text`, whether a person confirmed what it rests on, and
its `references`), `further_reading` (records found and not cited), `composed`, `coverage` (one
sentence on how much of the question the brain could answer), `derived_by`, `model` and, when
nothing could be composed, a `reason` (`nothing_found`, `not_approved`, `model_unavailable`,
`cap_reached` or `no_supported_consideration`). A reference is a `card`, a `document`, a `file`
(repository and path), a `ticket`, a `pull_request` or a `commit`.

On a `file` question a reference to the asked repository comes back spelled as the
`repository` the question sent; on every other question it carries the catalog's own spelling,
so its path is a pointer and nothing more.

**Two things, and mixing them up is the expensive mistake.** What a consideration says about
whether a behaviour is still wanted is one; whether a **person** ever agreed with what it rests
on (`confirmed`) is the other. A removed requirement can be confirmed knowledge; a live-looking
one can be an agent's unconfirmed proposal from last week. The report keeps them apart.

**`mcp__Fairmind__Brain_tell`** — one report per requirement, with `intent: "need"`; see
*The report* below.

## Files and functions

A file or a function is what makes a draft evidence rather than an assertion. Supply:

- `files` — repo-relative paths, exactly as the code tools spell them.
- `functions` — `"<path>::<name>"` per symbol, when the requirement is about a specific symbol
  rather than a whole file.

The brain resolves them against the code it has ingested. Do not invent a path to satisfy the
"at least one" rule — an item with no honest file belongs in the unanchored list.

## Grouping files into modules

In order of preference:

1. **Deployable or packaged boundaries** — a service, a package, a published library.
2. **The names the brain already uses.** If the answers cluster on `app/billing/` and call it
   *Billing*, the module is `billing`. Matching the existing name is what keeps titles stable
   across runs, and stable titles are the whole convergence guarantee.
3. **Top-level source directories** — the fallback, and a perfectly good one.

Two anti-patterns:

- **One module per directory, all the way down.** Fifty modules produce fifty thin reports and
  fifty directory questions. Aim for a list a person can hold in their head.
- **Renaming a module between runs.** The module name opens every title; renaming it makes the
  second run tell a parallel set of drafts instead of finding the first.

## The report

```json
{
  "intent": "need",
  "repository": "6a8c08657fdafcc39df4e48d",
  "project": "6a8c08657fdafcc39df4e48c",
  "title": "billing · Invoices are issued monthly",
  "summary": "Functional requirement. Each active customer is invoiced once per calendar month, on the first business day, for the seat-days of the month before. Anchored to code: the scheduler and the invoice builder. Rests on: Invoices are reconciled per seat-day (3f2a9c4e5b6d7a8b9c0d1e2f3a4b5c6d, confirmed).",
  "files": ["app/billing/invoice.py"],
  "functions": ["app/billing/invoice.py::issue_monthly_invoices"],
  "session_ref": "0b0e8d3c-1b6f-4d54-9a39-5f2f4b7d8c11",
  "agent": "brain-rebuild-requirements"
}
```

`title` is the module's name, ` · `, and the requirement, on one line. The brain identifies a
record by its title, so the same module and wording find their own draft on a rerun and a
changed title is a new draft. Examples:

| module | requirement | title |
|---|---|---|
| `billing` | Invoices are issued monthly | `billing · Invoices are issued monthly` |
| `auth` | Session tokens expire after 30 minutes | `auth · Session tokens expire after 30 minutes` |

Text limits the door enforces and never repairs: a title is one line of at most 256
characters; a `summary` is at most 1000 characters; at most a hundred files and functions and
ten related items in all. A control character or a line break in a one-line field is a refusal.

Do not send a requirement type, a key, a parent reference, an anchor, a precedent list,
`status` or `state`: the brain decides what kind of record each item is and links it when it
places it, and a report that carries one of those is refused.

## Response shapes

| Response | Meaning | Do |
|---|---|---|
| `state: proposed` (with `node_id`) | the draft landed and waits for a person | count it, carry the `node_id` into the report |
| `state: duplicate` | the same draft was already there; nothing changed | report it that way, never as written |
| `state: conflict` (with `node_id`) | a record under this title says something different; nothing was overwritten | show the difference; after the person's yes, send it again with `revises` set to that `node_id` |
| `related[]` | one entry per related document: `created`, `updated`, `unchanged` or `proposed` is linked; `unavailable`, `retry` and `over_cap` are not | name the document and its state |
| `{'status': 'refused', 'reason': ..., 'message': ...}` | words the brain did not accept (or, for a shape reason, a disagreement with the plugin) | fix the draft and show it again; for a shape reason update the plugin |
| `{'status': 'throttled', ...}` | the tenant's daily write quota is spent | **stop**, report what was written and what remains |
| `{'retryable': True}` | not written now | one retry, then stop and report |
| a refusal naming a configuration key | the tenant is not provisioned for brain writes | quote the key, stop |

**Why the throttle rule is absolute.** Every refused attempt is counted against the tenant. A
retry loop against a spent quota burns the next day's headroom and produces nothing. Stopping
with an accurate "wrote 14 of 37, resume at `billing · …`" is strictly better than a loop.

## Working within a budget

- A directory question is never cached and is answered by a model: ask one per module, not one
  per file, and keep the module list short.
- Process **one module at a time**, end to end, and write its section of the report before
  moving on. A run that dies half way then leaves a usable half-report.
- Most drafts need a title, a state and an id. Open a record only when a draft depends on its
  body.

## Reading your own previous output back

From the second run onwards, part of what the brain returns for a module **is this skill's own
earlier output**. Nothing in an id says so; the consideration's `confirmed` flag does.

Three places it matters, in the order a run meets them:

1. **What the brain holds.** A module whose only brain content is run 1's drafts is still
   unexplained in the sense that matters — nobody has confirmed anything about it. Report the
   considerations with their state, so a module explained only by proposals reads as such.
2. **Precedents.** A draft may cite a proposal, but the citation carries `(proposed, not yet
   confirmed)` so a reviewer can see the grounding is a guess. A chain of drafts each citing
   the last one looks, from the outside, exactly like accumulated knowledge.
3. **The yield.** Print the confirmed count beside the drafted count. A second run that
   reports "42 drafts" without saying "0 confirmed" reads as twice the progress of the first.

Titles do **not** cover this. They guarantee identity across runs, which is what stops
duplicates; they say nothing about whether a human has looked at what is behind them.

## Contradictions

A contradiction is: the brain records one behaviour, the code implements another. Both are
evidence, and which one is *right* is a question for a person who knows why.

Write it as a pair, always with both sources:

```
- The brain records that refunds are manual-approval only (Refund approval, confirmed 2025-11-02).
  `app/billing/refund.py:auto_approve` approves refunds under 50 EUR without review.
```

Do not resolve it, do not draft a requirement for either side, and do not average them into a
sentence that describes neither. List it and move on — resolving it silently is how a decision
somebody made deliberately disappears.
