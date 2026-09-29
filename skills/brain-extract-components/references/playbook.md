# Playbook — components from a blueprint

On-demand detail for the `brain-extract-components` skill: the tools it uses and the ones it
never does, the component door's parameters and answers as this skill relies on them, how a
rerun converges, and a worked example end to end.

## The tool surface

| Tool | Used for | Used here |
|---|---|---|
| `mcp__Fairmind__Brain_search` | browse documents, and components with their `natural_key` — the rejected and retired ones included — by kind | yes — Steps 1 and 3 |
| `mcp__Fairmind__Brain_get` | the blueprint's body, chunk by chunk | yes — Step 2 |
| `mcp__Fairmind__Code_tree` | the ingested repository's directories, page by page | yes — Step 3 |
| `mcp__Fairmind__Brain_context` | which component already claims a directory | yes — Steps 3 and 7, only when components exist |
| `mcp__Fairmind__Brain_record_component` | record the approved components | **yes — the only write** |
| `mcp__Fairmind__Brain_add_document` | file a document | no — filing the blueprint is `brain-add-document`'s job |
| `mcp__Fairmind__Brain_record_decision` | record a decision | no |
| `mcp__Fairmind__Brain_set_status` | change a record's status | **never** |

🔴 **`mcp__Fairmind__Brain_set_status` is not on this skill's menu**, including for the
components it just wrote. Confirmation is a human act performed in the review queue.

## The component door, as this skill relies on it

This is the contract the skill is written against. When the platform's answer disagrees with
it, report what the platform said, verbatim — do not reshape the answer to fit this page.

**Parameters.**

| parameter | this skill sends |
|---|---|
| `repository` | required — the bound `repository_id`; every directory of every component is resolved in it |
| `components` | required — 1 to 20 component objects |
| `agent` | required — the label identifying this run |
| `project` | the bound `project_id`, always. The platform takes the repository's binding first, then the key's own project, and this parameter last; a component is company-wide only when all three are absent, so leaving it out does not make one company-wide. Sent, it is also checked: a project other than the one the key was issued for is refused before anything runs |
| `blueprint` | the blueprint's `node_id` (32 lowercase hex characters) — each component is linked to it as the document that describes it |
| `git_remote` | **never** |
| `session_ref`, `task_ref` | not sent |

**The component object is a closed set.** `name` (required), `key`, `responsibility`,
`description`, `directories`, `subtype`, `layer`, `technology`, `owner_team`, `status`,
`part_of`, `depends_on`. Any other key refuses the whole call.

- `key` — a slug, and the component's identity; by default the name's slug. Sending the
  recorded one lands a renamed component on its own record.
- `directories` — at most 10 repo-relative paths. Each is stored as a directory prefix
  (`app/billing` is stored as `app/billing/`).
- `subtype` — `service`, `module`, `library`, `data_store`, `frontend`, `integration`.
  Advisory.
- `status` — `planned`, `active` (the default) or `deprecated`.
- `responsibility` — becomes the summary, capped at 1000 characters, and the body too when
  there is no `description`.
- `depends_on` — at most 30 entries. Each is a reference, or `{"component": <reference>,
  "kind": "calls" | "reads" | "publishes"}`. `dependency_kind` is accepted as another spelling
  of `kind`; this skill writes `kind`.

**A reference** is one of three spellings:

- 32 lowercase hex characters — a node id, as a Brain read returned it;
- `component:<projectId or *>:<slug>` — a full key, for a component recorded under another
  scope (`*` is the whole company);
- anything else — a name or slug, read as a component of **this call's** scope. This is what
  lets `depends_on: ["Billing"]` name a component written in the same call or an earlier one.
  It is turned into a slug, never looked up by title: a component whose key is not its
  name's slug is named by that key.

**The answer.**

```
{'status': 'ok', 'received': n, 'recorded': n,
 'components': [<one line per component sent, in the order sent>],
 'referrers_rearmed': n,
 'possible_duplicates': [<a duplicate candidate, plus the 'component' it concerns>],
 'suggested_anchors': [<an unclaimed directory the blueprint mentions>],
 'blueprint': None | {'knowledge_id', 'scan': {'status', 'candidates', 'truncated', 'reason'?}},
 'warnings': [str]}
```

Each **line** carries `index`, `name`, `natural_key`, `knowledge_id`, `outcome` (`created`,
`updated`, `unchanged`, `revision_proposed`, `not_reproposed`, `refused`), `directories` (each
`{'file_path', 'anchor_state'}`, the state `verified`, `unverified` or unknown), `anchors` (a
count per tier: `EXTRACTED`, `UNRESOLVED`, `DEGRADED`), `links` (references the platform could
turn into no identity at all), `possible_duplicates`, `suggested_anchors`, `suggested_links`,
`detection`, `graph`, `embedding` and `edges_deferred`; plus `proposal_id` on a
`revision_proposed` line, `reason` on a `not_reproposed` one, and `message` and `retryable` on
a `refused` one. `recorded` counts the `created`, `updated` and `unchanged` lines.

**`graph` is where a link's fate is read.** `{'status': 'projected', 'edges': [...],
'edges_pruned': n}` on a line that wrote the graph. Each edge entry is `{'edge', 'target',
'status'}`: `edge` the link's kind (`DEPENDS_ON`, `PART_OF`, `DESCRIBED_BY`, …), `target` the
node id it points at, `status` `merged` or `target_missing`. A reference is turned into a key
and never looked up, so one that names no recorded component is accepted, and its edge comes
back `target_missing` — the link is not recorded, and the line's outcome does not show it.
`edges_pruned` counts the links of those kinds the component had and this call no longer
names, removed by it: a correct old link replaced by a reference that names nothing is lost
here. `target` is a node id, derived on the platform from the key and the tenant, so the
client cannot compute it from a key; it is matched against the node ids already in hand.

**`detection.duplicates.status`** says whether the duplicate check ran: `complete` (or
`replayed`, answered again from a stored check) is a real answer, and only then is an empty
`possible_duplicates` "none". `disabled` — also visible as
`embedding.duplicate_detection.status` — means the tenant has the check switched off;
`incomplete`, with a `reason`, means it could not finish. Both are "not checked", never
"none". A `detection` that carries no `duplicates` entry, only a `status` and a `reason`, did
not reach the check either.

A **`not_reproposed`** line means a person rejected or retired the component. Its `reason`
reads `'<status> by a person — not re-proposed'` — `'rejected by a person — not
re-proposed'`, for instance — and is relayed as it is. Its `knowledge_id` is the stored node
and its `directories` are the stored ones, read back untouched; `anchors` is empty, `links`
and the suggestion lists are empty, and `detection`, `graph` and `embedding` are null. Nothing
was written and nothing was filed, though the call's quota charge still counts it.

A **duplicate candidate** names its `target` (`node_id`, `kind`), a `score` against a
`threshold`, and a `relationship` — `duplicates` when both were written the same way, `same_as`
when they came from different sources. Each one is queued on the platform for a person.

A **suggested anchor** is `{'repo_ref', 'repo_ref_scheme', 'file_path', 'symbol_type':
'MODULE', 'tier', 'source': 'blueprint', 'derived_by', 'mention': {'text', 'pattern',
'field', 'start'}}` — the `mention` says where in the blueprint the directory was named. It is
not a queue item and has no id to act on: it is a lead, reported to the person. The platform
leaves out the directories this call's components claim and those a live component of the
same project and repository already claims — `proposed` ones included, rejected and retired
ones not. A platform that leaves out only the call's own can suggest a directory an earlier
component holds, which is why the skill reads each suggestion's `components.containing` when
components already exist.

`blueprint.scan.status` is `complete`, `incomplete` or `not_consulted`; its `reason`, when
present, is `graph_unavailable`, `repository_not_ingested` or `claims_unavailable` — the
components already recorded could not be read, so nothing is suggested at all.

**Refusals of the whole call**, in the order the platform checks them — the first that
applies is the one you get, and nothing after it ran. Every one of them comes before
anything is written:

1. the batch's shape: `components` is not a non-empty list of objects, or there are more
   than 20 — `{'message': 'Brain component recording refused: …'}`;
2. `project` names a project other than the one the key was issued for — `{'status':
   'error', 'message': 'Access denied: this key is bound to a different project. Use the key
   issued for the target project (Studio Developer page).'}`;
3. the key's holder has no role that may write to the project the platform decided —
   `{'status': 'error', 'message': 'Access denied: …'}`;
4. the repository is bound to a project other than the key's — the same answer as 2;
5. an erasure in progress for the tenant — `{'message', 'retryable': True}`;
6. the blueprint is not a document the caller can see — `'… no such blueprint document'`; or
   the store could not be read to find out — `{'message', 'retryable': True}`;
7. any component breaks a field rule — `'… components[i].<field> …'`: an unknown field, too
   many directories or dependencies, a component that names itself, or two components with the
   same key (give one of them a distinct `key`);
8. the store could not be read for the components already recorded —
   `{'message', 'retryable': True}`;
9. a component the platform's own record check refuses — `'… components[i] (<name>): …'`;
10. the daily quota, charged once for the whole call — `{'message', 'key':
    'brain_write_quota_daily'}` when it is not configured, `{'message', 'status': 'throttled',
    'quota'}` when it is spent, `{'message': 'Brain write quota unavailable', 'retryable':
    True}` when the quota service could not answer.

A repository the platform cannot match is not refused: the components are recorded against
the name you gave, and their directories go unchecked. That is why this skill sends the bound
`repository_id` and nothing else.

Past the quota the components are written one by one, and a failure is a **line**, not a
refusal of the call: that component's `outcome` is `refused`, with its `message` and
`retryable`, and the lines before it stand. An erasure that begins while the call is being
written refuses its line **and every line after it**, each with the same message. And when
the platform cannot complete the call at all, the answer is `{'message': 'Brain component
recording failed'}` — which components landed is then unknown, and a rerun converges on
whatever did.

A key with no write scope never reaches any of these: the call is rejected as unauthorised
before the tool runs.

## Finding and reading the blueprint

`mcp__Fairmind__Brain_search` with no `query` is a **browse**: it pages through what the
filters select, exhaustively, and a page can come back empty with a `next_cursor` that is not
null. Follow the cursor until it is null, sending the same filters each time: a cursor is
bound to the request that produced it, and one sent with other filters is refused as an
invalid cursor. Each result carries `subtype`, `status` and `review_state`, and a component
also its `natural_key`; a document filed with `doc_kind: blueprint` has `subtype: blueprint`.
A browse leaves out what was rejected or retired unless it asks for
`include_invalidated: true`. It says what a record's status is, not who set it.

`mcp__Fairmind__Brain_get` returns `content`, `chunk_range` (`{'from', 'to'}`),
`chunks_available` and `truncated`. The same chunk index names the same passage on the next
call, so a long blueprint is read by chunk index: ask for `{"from": <last to + 1>, "to":
<chunks_available - 1>}` and stop once chunk `chunks_available - 1` is in hand. `truncated` is
set both when chunks remain and when a single chunk was cut to fit `max_tokens`, and a range
past the last chunk is clamped to it — so it cannot end the loop. An answer holding several
chunks holds them whole; an answer holding one chunk and saying `truncated` is read again as
`{"chunk_index": <i>}` with a larger `max_tokens`, up to 100000, until it is not. A `{'status': 'not_found'}` answer means
the node is gone or not readable by this key — say so and stop.

## How a rerun converges

A component's `natural_key` is `component:<projectId>:<slug>` — `*` in place of the project
for a company-wide one. The slug is `key` when you pass one, otherwise the name folded to
lowercase with every run of other characters turned into `-`: `"Billing Service"` →
`billing-service`. Every read of a component returns its `natural_key` — a
`mcp__Fairmind__Brain_search` row, the `mcp__Fairmind__Brain_get` item, and the component
cards of `mcp__Fairmind__Brain_context` — so the skill sends the recorded slug as `key`, and:

- **a recorded component lands on its own record**, renamed or not. A rerun over an
  unchanged blueprint answers `unchanged` or `updated` for every component, never `created`.
  After a rename the person keeps on the old record, the read returns the new title with the
  old key, and the next run needs no question;
- **a renamed component sent without its old key mints a new record** — and the new record
  comes back with the old one among its `possible_duplicates`, queued for a person. Both were
  written by an agent, so the candidate's `relationship` is `duplicates`;
- **a confirmed component is never overwritten** — the rerun files a revision proposal;
- **a component a person rejected or retired is left as it is.** Its line answers
  `not_reproposed` with a `reason`, and nothing is written or filed. A component an agent
  retired is not a person's verdict, and a rerun records it again. Reopening what a person
  closed is a person's act on the platform, not something a call from this skill can do;
- **a second blueprint adds, it does not replace.** A component keeps every blueprint it is
  described by, so sending it again from another blueprint links that one too.

Print every `natural_key` in the report: it is what a person uses to name the same component.

## Directories that do not resolve

The platform checks each directory against the ingested repository once, when the component
is written:

| what the code graph says | anchor tier | what to tell the person |
|---|---|---|
| at least one file under the directory | `EXTRACTED` | anchored |
| it answered, and has no file there | `UNRESOLVED` | the directory is not in the ingested tree — gone, renamed, or never there |
| it could not answer | `DEGRADED` | the check could not run this time; nothing is known either way |

A component whose directory later disappears is kept, reported `UNRESOLVED`. It is never
dropped, and this skill never removes it.

## Worked example

A blueprint filed as "Order platform — architecture" names four components: a web shop with a
checkout inside it, an order service the shop calls, and a payment gateway the order service
calls. The tree has `web/shop`, `web/shop/checkout`, `services/orders` and
`services/payments`.

The call the skill makes after the person says yes:

```json
{
  "repository": "<repository_id>",
  "project": "<project_id>",
  "blueprint": "0123456789abcdef0123456789abcdef",
  "agent": "brain-extract-components",
  "components": [
    {
      "name": "Web Shop",
      "responsibility": "The storefront customers browse and order in.",
      "directories": ["web/shop"],
      "subtype": "frontend",
      "layer": "frontend",
      "depends_on": [{"component": "Order Service", "kind": "calls"}]
    },
    {
      "name": "Checkout",
      "responsibility": "Collects the delivery address and the payment method for one order.",
      "directories": ["web/shop/checkout"],
      "subtype": "module",
      "layer": "frontend",
      "part_of": "Web Shop"
    },
    {
      "name": "Order Service",
      "responsibility": "Owns the order lifecycle from checkout to fulfilment.",
      "directories": ["services/orders"],
      "subtype": "service",
      "layer": "backend",
      "technology": "Python",
      "depends_on": [{"component": "Payment Connector", "kind": "calls"}]
    },
    {
      "name": "Payment Connector",
      "responsibility": "Charges and refunds card payments through the external payment provider.",
      "directories": ["services/payments"],
      "subtype": "integration",
      "layer": "integration"
    }
  ]
}
```

Two things in it are deliberate. The components are in the order the blueprint names them, and
`Web Shop` depends on `Order Service`, which comes later: inside one call the platform writes
the components that others point at first, so the order does not matter there — it matters only
across calls, which is why a split batch sends those first. And nothing in it says what the
blueprint did not: no `owner_team`, because the blueprint names no team, and no `technology`
where it names none.

The report, abridged — on a tenant whose duplicate check answered `complete` on every line, so an empty list reads "none":

```markdown
| Outcome | Component | Natural key | Knowledge id | Directories | Anchors | Duplicates |
|---|---|---|---|---|---|---|
| created | Web Shop | component:<project_id>:web-shop | 7a… | web/shop/ verified | EXTRACTED 1 | 1 |
| created | Checkout | component:<project_id>:checkout | 1d… | web/shop/checkout/ verified | EXTRACTED 1 | none |
| created | Order Service | component:<project_id>:order-service | 4c… | services/orders/ verified | EXTRACTED 1 | none |
| created | Payment Connector | component:<project_id>:payment-connector | 9f… | services/payments/ verified | EXTRACTED 1 | none |

## Possible duplicates (queued for a person — nothing was merged)
- Web Shop may be the same as 2e… (score 0.91, same_as)

## Suggested directories (not recorded — leads only)
- services/notifications/ — mentioned in the blueprint's body: "services/notifications"
```

A dependency cycle — two components that each depend on the other — is legal. One of the two
comes back with `edges_deferred: true`: its edge lands on the platform's next pass. Report it,
and do not send the call again.
