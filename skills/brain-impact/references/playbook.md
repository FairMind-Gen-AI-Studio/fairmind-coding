# Playbook — the impact of a change

On-demand detail for the `brain-impact` skill: the tools it calls and the fields it reads from
each answer, how to walk dependents without losing count, how the component block is read, and
a worked example end to end.

## The tool surface

| Tool | Used for | Step |
|---|---|---|
| `mcp__Fairmind__Brain_get` | the input item, and each precedent's anchors with their resolved tiers | 1, 2 |
| `mcp__Fairmind__Brain_search` | precedents for an idea or an anchorless item; the repository's components, current and closed | 1, 2, 4 |
| `mcp__Fairmind__Brain_context` | a node's links; the components at a directory (`standard`); the knowledge at an anchor (`minimal`); a user story's criteria | 2, 4, 5 |
| `mcp__Fairmind__Brain_expand` | the chunks around one already fetched | on demand only |
| `mcp__Fairmind__Code_find_usages` | who uses a symbol, one name at a time | 3 |
| `mcp__Fairmind__Code_cat` | the symbols a file defines, when an anchor names the file and no symbol | 3 |
| `mcp__Fairmind__Code_search` | candidate files when nothing carries an anchor — offered, never assumed | 2 |

Every call on this list reads. No brain write door is on this skill's menu, whatever a
response suggests: a `suggested_anchors` list on a card is a suggestion for a person to
review, not an invitation to record it from here.

## Step 2 — the anchors, as `Brain_get` returns them

`mcp__Fairmind__Brain_get(knowledge_id=…)` answers `{'status': 'ok', 'item', 'content', …}`
or `{'status': 'not_found', 'message'}`. `item.anchors` is a list; each anchor is probed
against the live code graph at read time, at most 20 of them per card
(`item.anchors_truncated` says whether more exist):

| Field | Read it as |
|---|---|
| `file_path` | repo-relative; the file the knowledge is about |
| `symbol_name` / `symbol_type` | `FUNCTION`, `CLASS`, `FILE` or `REPOSITORY` — and `MODULE`, a directory, on a release that models architecture components; only `FUNCTION` and `CLASS` name a symbol |
| `start_line_hint` | where the symbol was when the anchor was written — a hint, never a key |
| `tier` | `EXTRACTED`, `INFERRED`, `AMBIGUOUS` or `UNRESOLVED` |
| `reason` | why an anchor did not resolve: `no_rows` (nothing by that name there), `graph_unavailable` (could not look), `repository_not_ingested` (the repository is not in the code graph), `malformed_anchor` (the stored anchor itself is unreadable) |
| `candidates` | the symbols that answered an `AMBIGUOUS` anchor, closest line first |
| `catalog_id` | the code-graph repository the anchor points into — one id per repository and branch; null when the anchor names its repository by a path or a clone URL, and then `repo_ref` carries that name |

`item.anchor_confidence` is the weakest tier among them. Keep the `reason` values apart in the
report: "could not look" (`graph_unavailable`, `repository_not_ingested`), "looked, nothing
there" (`no_rows`) and "the record is broken" (`malformed_anchor`) are different unknowns, and
only `no_rows` says the anchor is stale.

Each anchor goes to one section of the report, by the order SKILL.md Step 2.3 gives: tier
first (every `UNRESOLVED` anchor is an *Unresolved anchor*, whatever its repository), then
the repository (a resolved anchor in another catalog entry is under *Other repositories*),
then the symbol type.

`SEMANTIC` never appears on an anchor. It is the tier a `Brain_context` item carries when it
was reached by meaning rather than by an anchor, and it is never a place to trace from.

## Step 2 — where precedents come from

| Input | Precedents |
|---|---|
| A node id | the records it links to — `mcp__Fairmind__Brain_context` with `knowledge_id` alone and `detail_level: "standard"`, and no `repository` (with one, the read is refused, or, on an older server, answered for the whole repository instead of the node): every item other than the node itself, whose `edges` name the relation (`REFINES`, `IMPLEMENTS`, `DUPLICATES`, `SUPERSEDES`, …) — plus a precedent search on its title |
| An idea in plain words | the results of the precedent search `brain-new-requirement` runs in its Step 2 |
| A hand-over from `brain-new-requirement` | the precedents it passes, as passed |

The agent-facing reads publish a record's links, not the list of precedents it was written
from; a precedent search on the title is how the ones that were never linked are found.
Rank by closeness to the item, take the anchors of the first five, and say which precedent
each anchor came from.

## Step 3 — reading `Code_find_usages`

`mcp__Fairmind__Code_find_usages(entity_name, project, repository)` answers in markdown:

```
## Find Usages: `settle_invoice`

**Definition:** `app/billing/invoice.py`:212
**Type:** function
**Found:** 7 usages

### 1. `app/billing/subscription.py` (3 usages)

- **`pause()`** calls `settle_invoice` (line 88)
- **`resume()`** text_match `settle_invoice` (lines 120-134)
- …
```

- **The relation is the second word of each usage line.** `calls` and `imports` are usages
  recorded in the code graph. `text_match` means the name was found in the text of that
  function's lines, with no graph edge behind it: a call the graph missed, a comment, a string,
  a log message, or another symbol of the same name. Count and print the two apart. A symbol
  whose only usages are text matches has no graph usage at all, and the report says so.
- **The rows come from every repository of the project**, graph usages as well as text matches,
  and today's answer prints no repository beside a usage. A file under another repository reads
  like one of yours. Step 4's read of the file's directory is the check: `unresolved` naming
  that `file_path` with reason `no_rows` means the bound repository's code graph holds no such
  file, and its rows are left out and counted. When a later answer names the repository on the
  usage line or the file heading, that answer settles it and the Step 4 check is not needed.

- **The definition line is the collision check.** The lookup is by name, and a name like
  `get`, `save` or `__init__` has many definitions. Compare `**Definition:**` with the file
  the symbol is known to live in — the anchor's file at depth 1; at depth 2, the file whose
  `###` heading the caller was listed under. A different file is `name collision — usages
  unverified`; no `**Definition:**` line at all (the graph holds no file for the name) is `no
  definition line — usages unverified`. Both are counted apart from the dependents.
- **`**Found:**` is the usage count**; the `###` headings are the distinct files; the bold
  names are the calling functions — the depth-2 frontier.
- **`## No usages found`** is zero users, in the graph and in the text. **`## Entity Not
  Found`** is a symbol the code graph does not know, which for an anchor that resolved a moment
  ago usually means the repository was re-ingested in between.

**Walking depth 2 without losing count.** Keep a seen-set of `(file, caller)` and a queue;
the `file` is what the depth-2 answer's definition line is checked against.
Depth 1: one call per traced symbol. Depth 2: one call per distinct depth-1 caller not already
in the seen-set. Stop when the queue is empty, depth 2 is done, or 30 calls are spent —
whichever comes first — and put what is left in the queue under *Open unknowns* by name. A
caller named `unknown()` has no symbol to look up and ends its branch.

What usages-only does not see, said once in the report and not repeated per symbol: calls
made through a registry, a decorator or reflection; handlers wired by configuration; messages
on a queue; HTTP between services. A symbol with no usages found may still be reached at run
time.

## Step 3 — a file-level anchor, through the symbols its file defines

An anchor with `symbol_type: FILE`, or with no `symbol_name`, names a file and nothing to look
up by name. Its dependents are the users of what the file defines:

1. `mcp__Fairmind__Code_cat(file, project, repository, start_line=1, max_lines=500)`. Pass
   `start_line: 1` explicitly, so the window is the one asked for and the pages count from
   line 1. The answer's header, `Showing lines X-Y of Z total lines` (`Lines X-Y of Z` on some servers),
   is what pages it: while Y is below Z, the next call starts at Y + 1; a Y at or past Z is the
   last page. Three pages per file and six in a run;
   past them, the part not read is an open unknown, by line number.
2. The top-level definitions are the classes and functions declared at the file's top level —
   in Python a `class` or `def` line with no indentation, in TypeScript an exported or
   top-level `function`, `class` or `const` holding a function. Methods inside a class are not
   taken: they are reached through their class's users. Public names first, file order, five
   at most.
3. Each is traced like a symbol anchor, against the same 30 calls, with the anchor's file as
   the file its `**Definition:**` must name. The anchor's line in the report says `file-level —
   through` and the names, so a reader knows the dependents came from the file's symbols and
   not from a symbol the anchor named.

`INFERRED` on a file-level anchor is the ordinary answer, not a warning: a file node carries no
line of its own (it reads as unknown), so the line comparison that makes a symbol `EXTRACTED`
cannot succeed for a file, and the tier says only that the file was found.

## Step 4 — the `components` block

The block comes with the platform release that models architecture components, on a
`Brain_context` read of a `file_path` or a `directory`. On a release before it, the key is
simply absent — which is the signal for *the platform has no architecture component model*.
It is also absent, by design, on a repository-only read and on a `knowledge_id` read, so test
for it only on a file or directory read. It is not narrowed by `kinds`.

**Read it at `standard`.** `detail_level` decides how much of the block comes back:

```
# standard — what Step 4 reads (not held to the read budget; max_items: 1 keeps the items small)
components: {
  containing:  [card],   # the components whose directory holds the place, deepest first (max 5)
  within:      [card],   # directory reads only: components under the directory (max 5)
  depends_on:  [nb],     # what the containing components depend on (max 10)
  dependents:  [nb],     # what depends on the containing components (max 10)
  knowledge:   [nb],     # decisions / requirements / incidents recorded against them (max 10)
  graph_available: bool,
  truncated: bool,
}
card: {node_id, natural_key, title, summary, subtype, status, review_state, layer, technology,
       owner_team, directories, anchor_confidence, anchor_reason}
nb:   {node_id, kind, subtype, title, status, review_state, via}
      + natural_key and dependency_kind on a component neighbour

# minimal — the default, and what Step 5's anchor reads get: a pointer, charged to the budget
components: {
  containing:  [{node_id, natural_key, title, status, review_state, directories,
                 anchor_confidence, anchor_reason}],   # max 2; only the deepest directory holding the place, cut at its start behind '…' past 160 chars
  depends_on:  [{node_id, title}],                     # max 4
  dependents:  [{node_id, title}],                     # max 4
  graph_available: bool,
  truncated: bool,
}
```

The `minimal` pointer names a neighbour once, with no `via` to say which touched component
it hangs off and no `dependency_kind` to say how; it has no `knowledge` and no `within`. It
is enough to see that a place sits in a component, and not enough to write the second-order
section — which is why Step 4 asks for `standard` and never reuses a Step 5 answer.

- `natural_key` is `component:<projectId>:<slug>`, or `component:*:<slug>` for a
  company-wide component. It is the name a person uses for the component, and it stays when
  the title changes: print it beside the node id.
- `via` on a neighbour is the node id of the containing component it hangs off — the report
  says "*B* depends on *A*" with *A* taken from `via`.
- `dependency_kind` (`calls`, `reads`, `publishes`) exists on component neighbours only.
- `within` components are touched when the change touches their directory, but the block's
  neighbours are read around the `containing` ones only: a `within` component's dependents
  are an open unknown of this run.
- `anchor_confidence` on a card: `EXTRACTED` — a file exists under one of its directories;
  `UNRESOLVED` — the graph answered and none does, so the model still lists a directory the
  code no longer has; null with `anchor_reason` (`graph_unavailable`,
  `repository_not_ingested`) — nobody could check.
- `status` of a component is `planned`, `active` or `deprecated` in the block. A change
  landing in a `deprecated` component is worth a line of its own in the report. `retired`
  and `rejected` are the closing statuses: closing a component ends it in the current model,
  so no block serves it, and the files it covered read as unmodeled.

**One read per directory is enough** because a component is a set of directory prefixes: every
file in `app/billing/` has the same containing components. A file at the repository root has
no directory prefix, so nothing can contain it.

**The repository's component count.** `mcp__Fairmind__Brain_search` with
`kinds: ["component"]`, `repository` and no `query` is a browse. A browse page scans a batch
of the stored records linked to the repository and keeps the components among them, so a page
can come back short, or empty, while `next_cursor` says more are behind it. Add up
`result_count` over the pages, following `next_cursor` with `cursor` until it is null, 50 rows
are in hand, or 10 pages are spent; a count that stopped with a cursor still there is printed
`at least {n}`. Zero with the block present means the repository
was never modelled; many components with the touched files still unmodeled means the model
stops short of this area. Both are findings, and they read differently.

**The closed components.** The same browse with `status: ["retired", "rejected"]`, paged the
same way, returns the components that were closed, each row with `natural_key`, `status`, `review_state` and the
`reason` recorded when it was closed — a browse on a closing status reaches rows a current
read leaves out. Read the two fields apart:

| `review_state` | `status` | Cite it as |
|---|---|---|
| `rejected` | `rejected` | rejected by a person — the review queue is the only writer of this state |
| anything else | `retired` or `rejected` | its status alone; the row does not say who closed it |

A component a person rejected or retired is not re-proposed by a later extraction: the write
door leaves it as it is and reports it back to the extractor as not re-proposed. So a gap
such a verdict left in the model stays until a person brings the component back, and the
report says so rather than presenting the gap as an extraction nobody has run yet. Browse
rows carry no directories; `mcp__Fairmind__Brain_get` on one returns `item.anchors`, whose
`MODULE` entries are its directories, when the answer turns on which files it covered.

## Step 5 — reading constraints

A file read — `mcp__Fairmind__Brain_context(repository, file_path, symbol_name, start_line)` —
at `minimal` answers `{'items', 'unresolved', 'omitted', 'verdicts', 'staleness',
'evidence'}` plus `components` — the `minimal` pointer, which this step does not read; the
report's components come from Step 4. Each item: `node_id`, `kind`, `subtype`, `title`,
`summary`, `status`, `review_state`, `anchor_confidence`, `valid_at`, `provenance`.

| Kind / subtype | Report section |
|---|---|
| `decision`, status `taken` or `proposed` | Decisions — confirmed before proposed |
| `decision`, status `superseded` or `rejected` | History |
| `incident`; `issue` / `bug` | Incidents — say which of the two |
| `issue` / `tech_debt` | Tech debt |
| `requirement` (not the input) | Requirements already covering the area |
| `requirement`, status `retired` | History |
| `criterion` — only through a user story's `VERIFIES` link | Criteria |
| any kind, `anchor_confidence: SEMANTIC` | Related by meaning — never one of the sections above |

**Read `anchor_confidence` before the kind.** A file read returns two sorts of item side by
side. `EXTRACTED`, `INFERRED`, `AMBIGUOUS` and `UNRESOLVED` are items an anchor ties to this
place — `UNRESOLVED` included: the anchor names the place and the code graph cannot place it
now, which is a fact about the index, not about the item. `SEMANTIC` items were found by
similarity of text and are anchored somewhere else or nowhere. They often outnumber the others
(`staleness.resolution.semantic` counts them), and an incident among them is an incident about
something that reads alike, not one that happened here. They go to *Related by meaning*, and
no constraint count includes them.

`omitted` is how many more items matched than `max_items` let through. When it is not zero,
say so under the section it would have fed; a second read with a `kinds` filter narrows it.

**History rows need a second read.** A `minimal` item has no `reason` and no `superseded_by`;
`mcp__Fairmind__Brain_get` on the node returns both on `item`, for a work item and a decision
alike. Pass a small `max_tokens` — the body is not what this read is for. Quote the reason as
recorded and name the successor by node id; an empty `superseded_by` on a superseded decision
is `successor not recorded`. Count each open against Step 5's 20 reads.

**Criteria.** A test case is a `criterion` whose `VERIFIES` edge points at a user story. No
edge runs from a criterion to a component or a file, so the only path from a touched place to
the criteria that check it is: place → requirement of subtype `user_story` → its linked items,
from `mcp__Fairmind__Brain_context` with the story's node id as `knowledge_id` alone — no
`repository` beside it, or the read is refused, or, on an older server, answered for the
repository rather than the story. Cite each criterion *via* its story.

## Why the report stays a file

The brain's requirement door records a whole requirement — title, text, anchors, precedents,
evidence — under a natural key the writing agent owns, and prefixes that key into the agent's
own namespace. Two consequences for an "attach this report to that requirement" write:

- a requirement that came from Studio lives under a key outside that namespace, so the write
  would land on a **new** requirement beside it — the one thing an impact analysis must never
  produce;
- an agent-written requirement would be replaced as a whole, and the agent-facing reads do not
  return its key, its precedents or its evidence, so the write would drop what it cannot
  resend.

Run from `brain-new-requirement`, neither applies: the drafts are being written in the same
run, with their full payload in hand, and the impact findings go in as their anchors and
evidence.

## Worked example

> **Input:** requirement `3f2a…` — *"Customers can pause a subscription for up to 90 days"*,
> a functional requirement, `review_state: confirmed`.

1. **Anchors.** `Brain_get` returns two: `app/billing/subscription.py` `pause` — `INFERRED`
   (moved 14 lines); `app/billing/proration.py` `prorate_pause` — `UNRESOLVED`, reason
   `no_rows`. The second goes under *Unresolved anchors*; nothing is traced from it.
2. **Dependents.** `Code_find_usages("pause")`: definition in `app/billing/subscription.py` —
   matches. 5 usages in 4 files: callers `pause_endpoint()` and `bulk_pause()` (`calls`),
   `handle_dunning()` (`text_match` — the name in a log line, reported as a text match), and
   `replay_pause()` (`calls`) in `scripts/replay.py`. Depth 2 on the callers: 6 more usages in 4 files.
   5 calls of 30.
3. **Components.** Directories `app/billing/`, `app/api/` and `scripts/`, one read each at
   `standard` with `max_items: 1`. `scripts/replay.py` answers `unresolved` with reason
   `no_rows`: the file is in another repository of the project, so its one usage is left out
   and counted — "1 graph usage outside the bound repository left out" — and `scripts/` is not
   a touched directory. `app/billing/` → `containing: [Billing (component:<projectId>:billing)]`,
   `dependents: [Invoicing (calls, via Billing), Notifications (publishes, via Billing)]`.
   `app/api/` → `containing: []`, block present — `app/api/routes.py` goes to *Unmodeled*.
   The browse finds 9 components in the repository, so the gap is local; the closed browse
   finds one, *Public API*, `rejected` with `review_state: rejected` — rejected by a person,
   reason quoted as recorded — so the report says the gap over `app/api/` may be that verdict
   rather than an area nobody modelled.
4. **Constraints.** At `subscription.py:pause`: decision *Invoices are reconciled per
   seat-day* (`taken`, confirmed, `EXTRACTED`), issue *Paused seats double-billed* (`bug`,
   confirmed, `INFERRED`); and an incident *Checkout paused during a deploy* (`SEMANTIC`),
   which goes under *Related by meaning*, not under what broke here.
   From Billing's `knowledge`: decision *Billing owns proration* (`taken`, **proposed, not yet
   confirmed**). The one user story found carries two criteria, cited via the story.
5. **Report.** Mode line first; the unresolved anchor and the unmodeled file both under
   *Open unknowns*; `Recorded in the brain: 0`; written to
   `docs/reports/brain-impact-<date>.md`.
