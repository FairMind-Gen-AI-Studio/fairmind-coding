---
name: brain-impact
description: Use when a change is about to be made and nobody has worked out what it touches - it takes a requirement, task or draft from the company brain (or an idea in plain words), follows its code anchors to the symbols they name, asks the code graph who uses those symbols, maps the files to the architecture components they sit in and the components that depend on them, and collects the decisions, incidents, tech debt, requirements and test criteria already recorded there, then writes an impact report with a confidence tier on every link and every unknown listed. Read-only - it records nothing in the brain. Also use when asked "what does this change touch", "what depends on this", "blast radius" or "impact analysis".
---

# Impact of a change

## Overview

A requirement says what should change. It rarely says what else moves when it does: the
callers of the function it names, the component that function lives in, the components that
depend on that one, and the decisions somebody already took about all of them. The platform
holds each of those answers in a different place. This skill chains them:

1. **brain → code** — the item's own code anchors, each with the tier the platform resolved
   it to against the live code graph;
2. **code → dependents** — who uses each resolved symbol, from the code graph, to a bounded
   depth;
3. **code → components** — the architecture component each touched file sits in, and the
   components that depend on it;
4. **places and components → constraints** — the decisions, incidents, tech-debt issues,
   requirements and test criteria recorded there.

**The server returns records; the prose is yours.** Every line of the report names the node
id or the call it came from, and every place the chain broke is listed as an unknown rather
than left out. An impact report that looks complete because its gaps were dropped is worse
than none: it gets read as a guarantee.

**One mode today: usages-only.** Dependents come from `mcp__Fairmind__Code_find_usages`, one
symbol name at a time. Each usage it prints carries a relation: `calls` or `imports` is a
**graph usage**, recorded in the code graph; `text_match` is a **text match** — the name appears
in that function's lines, which may be a call, a mention in a comment or a string, or another
symbol that shares the name. The answer spans every repository of the project, so its rows are
kept to the bound repository before they count (Steps 3 and 4). The platform serves no
call-flow tracing tool, so runtime paths, dynamic dispatch, message queues and HTTP calls
between services are not followed. The report says so on its first line, every time. Do not
substitute another tool for the missing one and present the result as a trace.

**Read-only.** This skill records nothing in the brain. Its output is a report file in the
user's repository; see *What this skill does not write* for where an impact result does reach
the brain.

**Announce at start:** "I'm using the brain-impact skill to trace what this change touches,
from the brain to the code and back."

**Dependencies:** requires a connected Fairmind workspace with a bound repository — see
`fairmind-context` for the resolution chain reused in Step 0. The response fields read at each
step, the call shapes and a worked example live in `references/playbook.md`.

**What leaves your machine.** Read calls only, to the Fairmind platform your project's MCP
entry points at: node ids; the item's title, or the idea in the user's own words, as a search
query; repo-relative file paths and directories — a file an anchor names without a symbol
also goes to `mcp__Fairmind__Code_cat`, to read which symbols it defines; and symbol names,
which go to `mcp__Fairmind__Code_find_usages`. Nothing is recorded in the brain; the platform keeps its
own access log of reads. The plugin README's *What leaves your machine* is the full account
of every route.

## When to Use

Use this skill when:

- A requirement, task or draft is about to be implemented and what it reaches is not known
- Someone asks what depends on a function, file or module before it changes
- `brain-new-requirement` reaches its impact step and hands its precedents over
- A review needs the decisions and past incidents that govern the code a change lands in

Do **not** use it to look for precedents and draft a requirement from an idea — that is
`brain-new-requirement`, which calls this skill for its impact step. Do not use it to recover
the requirements of a whole system — that is `brain-rebuild-requirements`.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs `fairmind`
   set to `"configured"` plus the `project_id` and `repository_id` that `/fairmind-connect`
   writes. **Never write those keys yourself**, and never set `fairmind` to `"configured"` on
   their behalf.
2. If the file is missing, `fairmind` is `"none"`, or `repository_id` is absent: **stop** and
   tell the user to run `/fairmind-connect` once for this checkout. An impact analysis with no
   repository has no code to reach; there is no standalone version of this skill.
3. Confirm the `mcp__Fairmind__Brain_*` tools and `mcp__Fairmind__Code_find_usages` are in the
   tool list. The brain tools arrive with the platform's **Brain-1** release; an older server
   answers `mcp__Fairmind__Studio_*` and `mcp__Fairmind__Code_*` normally and exposes no
   `mcp__Fairmind__Brain_*` tool at all. If either is missing, say which and stop.
   `mcp__Fairmind__Code_cat` is needed only for a file-level anchor (Step 3.5); without it,
   such an anchor goes to *Open unknowns* untraced and the run continues.

## Step 1: Take the input

**Which calls carry the binding.** `mcp__Fairmind__Code_find_usages`,
`mcp__Fairmind__Code_cat` and `mcp__Fairmind__Code_search` take the bound `project_id` as
`project` and the bound `repository_id` as `repository`. So does every
`mcp__Fairmind__Brain_context` read of a place — `file_path` or `directory` — and the component
browses of Step 4. **A read of one node is different: `mcp__Fairmind__Brain_context` with
`knowledge_id` alone**, plus `project` and `detail_level`, and no `repository`, `file_path` or
`directory`. `repository` on its own is a place — the whole repository — and a node read that
also names a place is refused by the platform, or, on an older server, answered for the place
with the node dropped without saying so, and those items would be taken for the node's links.
Either way the node is not read. The precedent
searches — for an idea below, and on an item's title in Step 2 — take their parameters from
`brain-new-requirement`'s Step 2, not from this rule.

The input comes in one of three forms:

1. **A node id** (32 lowercase hex characters) of a requirement, task or draft:
   `mcp__Fairmind__Brain_get` with `knowledge_id`. Check for `{'status': 'not_found'}` before
   reading `item`. Note the item's `kind`, `subtype`, `title`, `status` and `review_state`.
2. **An idea in plain words:** run the precedent search of `brain-new-requirement` — its
   Step 2 only: the search, its removal-state pass, and a `review_state` label on every
   result. Not its drafting and not its write step. Restate the idea in one line and get the
   user's agreement before searching.
3. **Handed over by `brain-new-requirement`:** the idea plus the precedents it already found,
   with their node ids and both of their states. Do not search again.

## Step 2: Anchors → code

1. For a node, the anchors are `item.anchors[]` from `mcp__Fairmind__Brain_get`. Each carries
   `file_path`, `symbol_name`, `symbol_type`, `start_line_hint`, the `tier` the platform
   resolved it to, the `reason` when it could not (`no_rows`, `graph_unavailable`,
   `repository_not_ingested`, `malformed_anchor`), `candidates` when several symbols answered,
   and `catalog_id`.
   `anchors_truncated: true` means more than 20 exist and only the first 20 were probed — say
   so in the report.
2. Treat each anchor by its tier:

   | Tier | What it means | What this skill does |
   |---|---|---|
   | `EXTRACTED` | the symbol is exactly where the anchor said | trace it |
   | `INFERRED` | the symbol exists, at another line — it moved. **On a file-level anchor** (`symbol_type: FILE`, or no `symbol_name`) it means only that the file exists: a file node carries no line of its own, so a file anchor never reaches `EXTRACTED`, and `INFERRED` there does not say it moved | trace it — a file-level anchor through the symbols its file defines (Step 3.5) |
   | `AMBIGUOUS` | several symbols answer | trace up to 3 of its `candidates`, each labelled ambiguous |
   | `UNRESOLVED` | nothing answers | do not trace; list it under *Unresolved anchors* with its `reason` |
   | `SEMANTIC` | reached by meaning, not by an anchor | never traced as if it were an anchor |

3. **Every anchor lands in exactly one place**, decided in this order:
   - Tier `UNRESOLVED` → *Unresolved anchors*, whichever repository it names. A null
     `catalog_id` means the anchor names its repository by a path or a clone URL, which the
     code graph does not key on: print its `repo_ref` beside the reason.
   - Any other tier, with a `catalog_id` that is not the bound `repository_id` → *Other
     repositories*, with its tier; not traced in this run. The catalog keeps one id per
     repository **and branch**, so another id can also be this repository on another branch,
     and its tier was read there: say both are possible, and list it under *Open unknowns*.
   - `symbol_type: REPOSITORY` with no `file_path` → *Input*: it is about the repository as a
     whole and points at no code.
   - `symbol_type: MODULE`, on a platform release that models architecture components → it
     names a directory (its `file_path` ends in `/`) and no symbol, so it is not traced
     (Step 3.5); its directory counts as touched, and its Step 4 and Step 5 reads pass
     `directory` with no symbol fields.
   - Everything else is traced, by the table above.
4. **An item with no anchors of its own falls back to its precedents' anchors, and the
   report says so.** The precedents of a node are the records it links to —
   `mcp__Fairmind__Brain_context` with `knowledge_id` alone and `detail_level: "standard"`,
   whose `items` other than the node itself carry `edges` naming the relation — plus a
   precedent search on its title, as in Step 1. An idea never has anchors of its own, so it
   always takes this path, and its precedents are the search results or the ones handed over.
   Take the anchors of the closest five, each by `mcp__Fairmind__Brain_get`, and open the
   report's anchor section with the line *No anchors of its own — using the anchors of {n}
   precedents*.
   A precedent that is itself a `proposed` record is cited `{node_id} (proposed, not yet
   confirmed)`, never as a bare node id.
5. **No anchor anywhere — the item's or its precedents' — is still never an empty report.**
   Put "no code anchor on the item or its precedents" first under *Open unknowns*, run Step 5
   on what the brain does hold (the precedents and the decisions they link to), and offer up
   to five files from `mcp__Fairmind__Code_search` on the item's own vocabulary as
   `candidate — from code search, not an anchor`. They carry no tier, and they are traced
   only if the user picks them.

## Step 3: Code → dependents (usages-only)

For every traced symbol — the symbol anchors first, then the symbols of file-level anchors
(item 5):

1. `mcp__Fairmind__Code_find_usages` with `entity_name` (the symbol name), `project` and
   `repository`. It answers in markdown: `**Definition:**` the file, `**Found:** N usages`,
   then one `### i. <file> (n usages)` heading per file with a line per usage naming the
   **enclosing function** and the **relation**: ``- **`pause()`** calls `settle_invoice` (line 88)``.
   - **Read the relation on every line.** `calls` and `imports` are graph usages, recorded in
     the code graph. `text_match` is a text match: the name appears in that function's lines,
     and the graph holds no call or import behind it — a call the graph missed, a mention in a
     comment or a string, or another symbol with the same name. Keep the two apart in every
     count and every line of the report, and never present a text match as a call. Any other
     relation is printed as the answer gave it.
   - **The answer spans every repository of the project**, graph usages included. When a
     usage line or its file heading names the repository it sits in, keep the rows of the bound
     `repository_id` and count the others as left out. When it does not — the answer today
     prints no repository per usage — Step 4's read of that file's directory settles it
     (Step 4.3); until then the rows count provisionally.
2. **Check the definition first.** The lookup is by name alone, so check each answer against
   the file the symbol is known to live in: the anchor's file at depth 1, and at depth 2 the
   file whose `### <file>` heading the caller appeared under at depth 1. When
   `**Definition:**` names a different file, the usages may belong to another symbol that
   shares the name: report `name collision — usages unverified` and keep those counts out of
   the dependents totals. An answer with no `**Definition:**` line — the code graph holds no
   file for the name — cannot be checked at all: report `no definition line — usages
   unverified` and keep its counts out the same way. Short, common names (`__init__`, `get`,
   `handle`) collide most, and mostly at depth 2.
3. **Depth 2, bounded.** Depth 1 is the symbol's direct users. Depth 2 is the users of each
   distinct depth-1 calling function, by the same call. Stop at depth 2 unless the user asks
   for 3. Spend at most **30 `Code_find_usages` calls** in one run, the lookups of item 5
   included; when the budget runs out, list the callers and symbols left unexpanded under
   *Open unknowns*. Never expand a caller twice — the graph has cycles.
4. **`## No usages found` is an answer, not a failure:** nothing uses the name, in the graph or
   in the text. It can be an entry point, a handler registered by name, or dead code; say which
   only if the code shows it.
5. **A file-level anchor** (`symbol_type: FILE`, or no `symbol_name`) names no symbol to look
   up, so it is traced through the symbols its file defines:
   - Read the file with `mcp__Fairmind__Code_cat` (`file`, `project`, `repository`), passing
     `start_line: 1` and `max_lines: 500`. The header `Showing lines X-Y of Z total lines` (on
     some servers `Lines X-Y of Z`) says how much came back; while Y is below Z, read on with
     `start_line` set to Y + 1. At most
     **3 pages per file and 6 in the run**; a file read only in part goes to *Open unknowns* as
     `file read to line Y of Z — symbols past it not traced`.
   - From the text, take the file's **top-level definitions** — the classes and functions
     defined at the top level of the file, not the methods inside a class — public names (no
     leading underscore) first, in file order, **at most 5 per file**.
   - Trace each as a symbol (items 1–4), against the same 30 calls: its depth-1
     `**Definition:**` must name the anchor's file.
   - The file defines no top-level symbol → *Open unknowns*, `file-level anchor — no top-level
     symbol to trace`. `## File Not Found` → `file-level anchor — file not in the code graph`.
   - The report says which method ran for every anchor, in the `Traced` column: `symbol`,
     `file-level — through {the names traced}`, or the reason nothing was traced.

   Its file counts as touched in Step 4 whatever came of this. A `MODULE` anchor names a
   directory and no file, and is not traced: `directory anchor — dependents not traced`.
6. Keep per traced symbol: graph usages and text matches, distinct files and distinct callers
   at each depth, the file-level anchor it came from when it did, and the calls spent.

## Step 4: Code → components

1. **The touched files** are the files of every resolved anchor — traced, file-level, or a
   candidate traced for an ambiguous one — and of every depth-1 dependent, plus the directory
   of every resolved `MODULE` anchor.
2. Every file in one directory sits in the same components — a component is a set of
   directories — so **one read per distinct directory** is enough, the anchors' directories
   included: `mcp__Fairmind__Brain_context` with `repository`, `file_path` set to one file of
   that directory, a dependent's file when it has one (`directory` instead, for a `MODULE`
   anchor), `detail_level: "standard"` and `max_items: 1` — **at most 15** such
   reads per run; the directories left over go to *Open unknowns*. A file at the repository
   root sits in no component's directory, so it is unmodeled by construction.
3. **The same read settles which repository a dependent sits in**, at no extra call, when the
   usage answer did not print it (Step 3.1). A file that is not in the bound repository's code
   graph answers with an `unresolved` entry for that `file_path`, reason `no_rows`: drop the
   usages listed under that file, count them by relation — "{n} graph usages and {n} text
   matches outside the bound repository left out" — and read the directory again with its next
   dependent file, against the same 15. A directory whose read resolved its file keeps its
   dependents as the bound repository's; the check is per directory, so a directory of the same
   path in another repository is not told apart. Dependents whose directory was never read stay
   counted and are marked `repository not checked`; an `unresolved` reason other than
   `no_rows` (`repository_not_ingested`, `graph_unavailable`) is the same: not checked, never
   dropped. A directory none of whose dependent files resolves is not a touched directory of
   this repository, and is neither read for components nor listed as unmodeled.
4. **Why `standard`, and why `max_items: 1`.** At the default `minimal` level the
   `components` block is a pointer: at most two containing components, four neighbour names
   each way, and no `via`, `dependency_kind`, `knowledge` or `within`. It cannot say which
   touched component a dependent hangs off, how it depends on it, or what was recorded against
   it — the whole second-order section. A `standard` answer is not held to the read budget,
   so `max_items: 1` keeps its items small: this read is for the block. The knowledge at each
   anchor is Step 5's own read, and that read's `minimal` answer never stands in for this one.
5. Read the `components` block of each answer:
   - `containing` — the components whose directory holds the file, deepest first. The first
     is the file's own component; the others contain it. Each carries its `natural_key` —
     `component:<projectId>:<slug>`, or `*` for a company-wide one — the name a person uses
     for it, which survives a change of title.
   - `within` — on a `directory` read only: the components under that directory. A change to
     the directory reaches them, so they count as touched. The neighbours below are read
     around the `containing` components alone, so a `within` component's dependents are not in
     this answer: list it under *Open unknowns* as `dependents not read`.
   - `dependents` — components that depend on a containing one, each with `via` (which one),
     `dependency_kind` (`calls`, `reads`, `publishes`) and its `natural_key`. **These are the
     second-order impact**, and the report lists them as such.
   - `depends_on` — what the containing components rely on, the other direction. Listed, and
     not counted as impact.
   - `knowledge` — decisions, requirements and incidents recorded against a containing
     component, each with `via`. Step 5 uses them.
   - `graph_available: false` — the neighbours could not be read. The component itself is
     still listed; its neighbours go to *Open unknowns*.
   - `truncated: true` — a cap was hit (five containing, five within, ten per neighbour list).
     Say so.
   - `anchor_confidence` on each component: `EXTRACTED` (its directory exists), `UNRESOLVED`
     (the directory is gone and the model is stale there), or null with `anchor_reason` when
     the code graph could not say.
   - `review_state` on each component and neighbour: a `proposed` component is an extraction
     no person has confirmed, cited `(proposed, not yet confirmed)`.
   - `status` — `planned`, `active` or `deprecated`. A change landing in a `deprecated`
     component gets a line of its own.
6. **Unmodeled files are never skipped.** Each goes to the report's *Unmodeled* bucket with
   the reason that applies:
   - `containing` is empty — no component covers this directory;
   - the `components` block is absent from a file read — the platform has **no architecture
     component model** yet, so every touched file is unmodeled; say it once, at the top of the
     bucket. (The block is absent by design on a repository-only or a `knowledge_id` read, so
     only a `file_path` or `directory` read can tell you this.)
   - the file sits at the repository root.
7. Browse the repository's components: `mcp__Fairmind__Brain_search` with
   `kinds: ["component"]`, `repository` and no `query`, `k: 50`. A browse page can come back
   short, or empty, with more components behind it: follow `next_cursor` — the same call with
   `cursor` set to it — until it is null, 50 rows are in hand, or **10 pages** are spent,
   whichever comes first. Only a null `next_cursor` makes the count final; when one is still
   there, print `at least {n}`. The count is what tells "this file is outside the model" apart
   from "this repository has no model".
8. **Closed components are relayed, never guessed at.** `retired` and `rejected` are the
   component's closing statuses: a closed component has left the model, no `components` block
   serves it, and the files it covered read as unmodeled. One more browse lists them —
   `mcp__Fairmind__Brain_search` with `kinds: ["component"]`, `status: ["retired",
   "rejected"]`, `repository`, no `query`, `k: 50`, paged the same way — each with its
   `natural_key`, `status`, `review_state` and the `reason` recorded when it was closed. Print
   them under *Closed components*, quoting the reason as recorded and never supplying one. An
   empty list is "none found" only when its `next_cursor` came back null; otherwise it is
   `at least 0`, and a person's rejection may sit on a page not read.
   - A `review_state` of `rejected` is a person's rejection from the review queue: cite that
     row "rejected by a person". Any other closed row is cited by its `status` alone, without
     saying who closed it — the read does not.
   - A component a person rejected or retired is not re-proposed when an extraction runs
     again: bringing it back is a person's act, so a gap such a verdict left in the model
     stays until a person does it. Say so rather than presenting the gap as an area nobody
     has modelled yet.
   - Browse rows carry no directories, so do not guess which closed component covered which
     unmodeled file. Open one with `mcp__Fairmind__Brain_get` only when the answer turns on
     it, counted against the 15 reads above: its `item.anchors` of `symbol_type: MODULE` are
     its directories.

## Step 5: Constraints that apply

1. **At each resolved anchor:** `mcp__Fairmind__Brain_context` with `repository`,
   `file_path`, `symbol_name`, `start_line` set to the anchor's `start_line_hint`, and
   `detail_level: "minimal"` — for a `MODULE` anchor, `directory` in place of the other three.
   Its `items` come most grounded first and confirmed before proposed, each with its own
   `anchor_confidence`, and **that field splits them in two**:
   - **Recorded at this place** — `EXTRACTED`, `INFERRED`, `AMBIGUOUS` or `UNRESOLVED`: an
     anchor of the item names this place (`UNRESOLVED` — it names it, and the code graph cannot
     place it now). Only these rows are constraints of the place.
   - **Related by meaning** — `SEMANTIC`: found by similarity of text, with no anchor at this
     place. They are leads, listed apart, and never presented as recorded here.

   Its `components` key is the `minimal` pointer; the report's components come from Step 4's
   `standard` reads.
2. **At each touched component:** the `knowledge` rows from Step 4's `standard` read. When
   they came back truncated, read the component itself — `mcp__Fairmind__Brain_context` with
   its node id as `knowledge_id` alone.
3. Sort what came back into the report's sections. The sections below take the rows recorded
   at a touched place and the `knowledge` rows of a touched component; every `SEMANTIC` row
   goes to *Related by meaning* instead, whatever its kind — a `SEMANTIC` incident or bug is
   never listed under *what broke here*.
   - **Decisions** — kind `decision`. Current first (status `taken`), confirmed before
     proposed. A `superseded` or `rejected` decision goes under *History*.
   - **History** — a `superseded` or `rejected` decision, a `retired` requirement: it
     explains the code, it no longer constrains it. A `minimal` item carries no reason, so
     open each with `mcp__Fairmind__Brain_get` and a small `max_tokens` (200 — only `item` is
     needed, not the body), and quote its `item.reason` — and `item.superseded_by` for a
     superseded decision. A record with no reason recorded is reported as such, never given
     one; a superseded decision whose `superseded_by` is empty is printed `successor not
     recorded`, never left without a successor line.
   - **Incidents — what broke here before** — kind `incident`, and issues of subtype `bug`.
     Say which of the two the brain returned; a platform release without the incident kind
     answers with the second only.
   - **Tech debt** — issues of subtype `tech_debt`.
   - **Requirements already covering the area** — kind `requirement`, other than the input.
   - **Criteria that verify those requirements** — kind `criterion`, reached through the
     `VERIFIES` edge a test case carries to its user story: for each user story above, read it
     with `mcp__Fairmind__Brain_context` and its node id as `knowledge_id` alone, and keep the
     linked items of kind `criterion`. No edge runs from a criterion to a file or a component,
     so a criterion is always cited *via* its story.
   - **Related by meaning — not recorded at the touched places** — every `SEMANTIC` row, with
     its kind and the place whose read returned it. It tells the reader where to look further;
     it constrains nothing, and no row of it is counted among the constraints.
4. **Label every row with its `review_state`.** It is a second axis beside `status`: `status`
   says whether the thing is still wanted, `review_state` whether a person ever agreed with the
   record. A `proposed` decision is a lead, not a constraint, and is cited `{node_id}
   (proposed, not yet confirmed)`.
5. **Read the envelope before trusting the rows:** `staleness.graph_available`,
   `staleness.repo.sync_state`, `staleness.repo.last_synced_at`, `staleness.resolution`,
   `unresolved`, and the `evidence` caveats. A stale or ungraphed repository changes what the
   report may claim, so the figures go into it. An empty `items` beside a caveat means "could
   not look", never "nothing is known".

**Token discipline.** `detail_level: "minimal"` on every context call in this step and
`max_items` at its default. **At most 20** reads in this step, the *History* opens included;
what is left over goes to *Open unknowns*. Beyond *History*, open a record with
`mcp__Fairmind__Brain_get` only when its body changes the answer, and use
`mcp__Fairmind__Brain_expand` for neighbouring chunks rather than re-fetching it.

## Step 6: Write the report

Print the report, then write the same markdown to **`docs/reports/brain-impact-<YYYY-MM-DD>.md`**
in the user's repository (add the item's slug to the filename when there is more than one on the
same day). Say that you created it and let the user decide whether to commit it.

Every section of the template stays in the report even when it is empty — write "none found"
under it. A missing section and an empty one read differently, and only the second is an
answer.

## What this skill does not write

Nothing goes into the brain from here: not the report, not the touched files as new anchors,
not a new requirement.

- **Run from `brain-new-requirement`**, the findings reach the brain through that skill's own
  write step: the touched files become its drafts' anchors and the constraints their evidence.
- **Run on its own**, the report file is the output. The brain's requirement door records a
  whole requirement under a key the agent owns, and the read doors do not return the key or
  the evidence an existing requirement carries — so attaching this report to one from here
  would either mint a second requirement beside it or overwrite what it holds. Say so if the
  user asks for it, and leave the attaching to a person.

## Output Structure

```markdown
# Impact of a change — {item title or idea, one line} ({date})

**Mode: usages-only** — dependents from `Code_find_usages` to depth {d}: graph usages (`calls`, `imports`) and text matches of the name, kept to the bound repository. No call-flow tracing: runtime paths, dynamic dispatch, queues and calls between services are not followed.
**Nothing here was recorded in the brain.** A row marked *proposed, not yet confirmed* is a draft no person has agreed with.

## Input
- {kind}/{subtype} **{title}** ({node_id}) — status {status}, {confirmed | proposed, not yet confirmed}
- Project {project_id} · repository {repository_id} · last synced {last_synced_at} · sync state {sync_state} · graph {available | unavailable}

## Anchors → code
{only when the item has none: No anchors of its own — using the anchors of {n} precedents: {node_id} ({confirmed | proposed, not yet confirmed}), …}

| File | Symbol | Tier | From | Traced |
|---|---|---|---|---|
| {path} | {symbol | —} | EXTRACTED / INFERRED / AMBIGUOUS | item / precedent {node_id} | symbol / file-level — through {symbols traced} / file-level — {why nothing was traced} |

### Unresolved anchors (not traced)
- {path}:{symbol} — UNRESOLVED ({reason}) — from {item | precedent node_id}{ — repository {repo_ref}, when catalog_id is null}

### Other repositories (not traced)
- {catalog_id} · {path}:{symbol} — {tier} — another repository, or this one on another branch

## Code → dependents (usages-only)
### `{symbol}` ({path}){ — from the file-level anchor on {path}, when it came from one}
- Depth 1: {n} graph usages and {n} text matches in {f} files, {c} callers — {caller} ({file}, {calls | imports | text match}), …
- Depth 2: {n} graph usages and {n} text matches in {f} files, through {c} callers
- Outside the bound repository, left out: {n} graph usages, {n} text matches · repository not checked: {n}
- {name collision — usages unverified | no definition line — usages unverified | no usages found}

## Code → components
Components modelled in this repository: {n | at least {n}} · closed: {n | at least {n}}

| Component | Key | Status | Review | Touched through | Confidence |
|---|---|---|---|---|---|
| {title} ({node_id}) | `{natural_key}` | {planned | active | deprecated} | {confirmed | proposed, not yet confirmed} | {files | under {directory}} | EXTRACTED / UNRESOLVED / unknown ({anchor_reason}) |

### Second-order impact — components that depend on the touched ones
- {dependent} ({node_id}, `{natural_key}`, {review state}) → depends on {touched} ({dependency_kind})

### What the touched components depend on
- {touched} → {dependency} (`{natural_key}`, {dependency_kind})

### Unmodeled — files no component covers
- {path} — {no component covers this directory | the platform has no architecture component model | repository root}

### Closed components — retired or rejected, no longer in the model
- {title} ({node_id}, `{natural_key}`) — {retired | rejected}{, rejected by a person — when its review_state is rejected}: "{reason, as recorded | no reason recorded}"

## Constraints that apply
### Decisions (current first)
- {title} ({node_id}) — {status}, {confirmed | proposed, not yet confirmed} — at {path:symbol | component}

### Incidents — what broke here before
- {title} ({node_id}) — {incident | issue/bug}, {review state} — at {…}

### Tech debt
- {title} ({node_id}) — {review state} — at {…}

### Requirements already covering the area
- {title} ({node_id}) — {status}, {review state} — at {…}

### Criteria that verify those requirements
- {title} ({node_id}) — verifies {story title} ({node_id})

### History — superseded or retired, context rather than constraint
- {title} ({node_id}) — {status}: "{reason, as recorded | no reason recorded}"{ — superseded by {node_id | successor not recorded}, for a superseded decision}

### Related by meaning — not recorded at the touched places
- {title} ({node_id}) — {kind}, {review state} — SEMANTIC, returned by the read of {path | directory}

## Open unknowns
1. {an unresolved anchor · a file-level or directory anchor not traced · a file read only in part · an anchor in another catalog entry · a name collision or a missing definition line · callers left unexpanded by the budget · dependents whose repository was not checked · directories left unread by a cap · neighbours missing because the graph was unavailable · the dependents of a component under a touched directory, not read · a stale sync}

## Yield
- Anchors: {n} from the item, {n} from precedents — extracted {n} | inferred {n} | ambiguous {n} | unresolved {n}
- Symbols traced: {n}, of which {n} through file-level anchors · `Code_find_usages` calls: {n} of 30 · `Code_cat` pages: {n} of 6 · depth reached: {d}
- Dependents: {n} graph usages and {n} text matches in {n} files at depth 1; {n} graph usages and {n} text matches in {n} files at depth 2; unverified (name collision or no definition line) {n}; outside the bound repository, left out {n}; repository not checked {n}
- Components touched: {n} · second-order (dependents): {n} · unmodeled files: {n} · closed in this repository: {n | at least {n}}
- Constraints: decisions {n} (confirmed {n}) · incidents {n} · tech debt {n} · requirements {n} · criteria {n} · related by meaning, not counted: {n}
- Context reads: {n} of 15 for components (standard), {n} of 20 for constraints (minimal)
- Recorded in the brain: 0 — this skill is read-only
- Report written to: docs/reports/brain-impact-{date}.md
```

## Next Steps

- **Before implementing**, read the decisions in the report in full (`mcp__Fairmind__Brain_get`)
  and resolve the open unknowns that matter to the change.
- **For a new requirement** built on this analysis, load `brain-new-requirement` — it calls
  this skill for its impact step and records the findings with its drafts.
- **For implementation**, load `fairmind-context`, then `fairmind-tdd`.
- **An unmodeled area is a gap in the architecture model**, not evidence that nothing depends
  on it. Say so when it covers most of the touched files.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `repository_id`:** stop and point the
user at `/fairmind-connect`. Do not fall back to grepping the local tree and calling the
result an impact analysis.

**No `mcp__Fairmind__Brain_*` tools in the tool list:** the connected server predates the
**Brain-1** release. Name that release, say the `mcp__Fairmind__Code_*` tools are unaffected,
and stop.

**`{'status': 'not_found'}` from `mcp__Fairmind__Brain_get`:** the node id is wrong or not
readable in this tenant. Say which, and ask for the right one; do not guess a neighbour.

**A read refused by name** (`{'message': 'Brain context refused: …'}`): the call's shape is
wrong — both `file_path` and `directory`, symbol fields on a directory read, a place with no
`repository`, a `knowledge_id` sent together with a place. Fix the call the message names; the
same call again is refused again.

**`{'message': 'Brain … read failed'}`:** the platform could not answer. One retry is
reasonable; a second failure goes to *Open unknowns* as "not read — platform error", and the
run continues with the rest.

**`## Entity Not Found` from `mcp__Fairmind__Code_find_usages`:** the code graph does not
know the symbol — the anchor is stale, or the repository was re-ingested since it was written.
Report it next to that anchor. **`## Service Unavailable`:** one retry, then report the
dependents of that symbol as not read. **`## Error: Repository Not Found`:** the binding is
wrong for this checkout — stop and point at `/fairmind-connect`.

**`## File Not Found` from `mcp__Fairmind__Code_cat`:** the code graph holds no such file in
the bound repository. The file-level anchor is not traced; it goes to *Open unknowns* with that
reason, and its file still counts as touched.

**A repository whose sync state is stale or unknown:** proceed, and put the staleness figures
at the top of the report. An impact read from a stale graph is the impact on an older system.
