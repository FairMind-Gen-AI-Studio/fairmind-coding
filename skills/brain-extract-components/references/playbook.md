# Playbook — components from a blueprint

On-demand detail for the `brain-extract-components` skill: the tools it uses and the ones it
never does, the report it sends for the blueprint and for each component, what comes back, and
a worked example end to end.

## The tool surface

| Tool | Used for | Step |
|---|---|---|
| Read / the session's own connection | the blueprint's text, from the checkout or from the link the person gives | 1 |
| `artifact_cards.py blueprint` | builds the filing report and the related item for a blueprint that is a checkout file; sends nothing | 1 |
| `mcp__Fairmind__Brain_overview` | the components already recorded (`detail_level: "standard"`, its `components` section) | 3 |
| `mcp__Fairmind__Brain_ask` | what the brain holds about each directory the draft names (`file`, a path ending in `/`) | 3 |
| `mcp__Fairmind__Code_tree` | the ingested repository's directories, page by page | 3 |
| `mcp__Fairmind__Brain_tell` | the blueprint as a document, then one report per component | 6 |

No other brain tool is on this skill's menu. Filing the blueprint on its own is
`brain-add-document`'s job.

## The blueprint's two objects

For a checkout file, `artifact_cards.py blueprint <path> --title T --summary-file F` prints:

```json
{"tool": "Brain_tell",
 "arguments": {"intent": "document", "repository": "acme/orders", "git_remote": "https://github.com/acme/orders.git",
               "project": "<project_id>", "agent": "brain-extract-components",
               "title": "Order platform — architecture", "summary": "<yours>",
               "source": {"url": "https://github.com/acme/orders/blob/<commit>/docs/architecture.md",
                          "version": "<blob id>", "system": "git"}},
 "item": {"url": "https://github.com/acme/orders/blob/<commit>/docs/architecture.md",
          "title": "Order platform — architecture", "summary": "<the same summary>"}}
```

`arguments` is sent once, before any component. `item` goes unchanged into `documents` of
every component report: it names the card the filing made, and carries no version, so the
brain finds that card and links it without writing it again. For a link the person gave, the
two objects are built by hand with the same title and summary (SKILL.md Step 1.2).

## The component report

```json
{
  "intent": "component",
  "repository": "<repository_id>",
  "project": "<project_id>",
  "title": "Order Service",
  "summary": "Owns the order lifecycle from checkout to fulfilment. Layer: backend. Technology: Python. Depends on Payment Connector (calls).",
  "files": ["services/orders/"],
  "documents": [{"url": "https://github.com/acme/orders/blob/<commit>/docs/architecture.md",
                 "title": "Order platform — architecture", "summary": "<the same summary>"}],
  "session_ref": "<this session's id>",
  "agent": "brain-extract-components"
}
```

`title` and `summary` are text the brain refuses rather than cuts: one line for the title, at
most 1000 characters for the summary. `files` holds at most 10 directories. Nothing else is
sent: the brain decides what a record is and how it links to the others.

## What comes back

`{'node_id', 'state', 'studio_url', 'related'}`:

- `state`: `proposed` (landed, waiting for a person), `duplicate` (the same report was already
  there), `conflict` (a record with this title says something else; nothing was written; the
  answer's `node_id` is the record to revise), or a refusal.
- `related`: one entry per item in `documents`, `{field, index, key, node_id, state}`, with
  `state` one of `created`, `updated`, `unchanged`, `proposed` (linked) or `unavailable`,
  `retry`, `over_cap` (not linked).

A `Brain_ask` answer is `{'considerations', 'further_reading', 'composed', 'coverage', ...}`:
each consideration is `{'text', 'confirmed', 'references'}`, `further_reading` is what was
found and not cited, and `coverage` is the brain's own sentence about what it holds. It never
names a component card: a component the draft repeats is found by the overview's list or by
the answer to the report.

## Finding the blueprint

The brain is not searched. The person names a file or gives a link; a file under the
repository's artifacts path is also filed by the artifact route at push, as a card pointing at
the file in git, so it can be held by two document cards at different keys. That is not an
error of this run.

## Worked example

A blueprint, `docs/architecture.md`, names four components: a web shop with a checkout inside
it, an order service the shop calls, and a payment connector the order service calls. The tree
has `web/shop/`, `web/shop/checkout/`, `services/orders/` and `services/payments/`.

1. **Blueprint.** The file is committed, pushed and unchanged: the verb prints the link and the
   two objects. The person sees the title, summary and link in the draft.
2. **Overview.** Its `components` section lists *Web Shop* (node `7a…`): the draft's *Web Shop*
   is shown as "already recorded as Web Shop (7a…)" and the person chooses to send it as
   drafted.
3. **Ask.** `Brain_ask` `file` on `services/orders/`: two considerations, one confirmed (a
   decision about order idempotency, cited with its card) and one "(proposed, not yet
   confirmed)". The `coverage` sentence is quoted. Four directories, four asks of ten.
4. **Show, yes, tell.** The blueprint report answers `proposed`. The four component reports
   follow in the blueprint's order: *Web Shop* `duplicate`, the others `proposed`; each
   `related` entry reads `unchanged`.
5. **A failure.** Had *Checkout*'s `related` entry read `retry`, the run would stop there and
   report "told 1 of 4, stopped at Checkout: the blueprint was not linked (`retry`)"; a rerun
   tells the first one again (`duplicate`) and continues.
6. **Report.** One line per component, what the brain holds about each directory as
   conclusions with references, and `Confirm or reject each component in the review queue —
   this skill confirms nothing.`
