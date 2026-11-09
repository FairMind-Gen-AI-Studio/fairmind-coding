---
name: brain-impact
description: Use when a change is about to be made and nobody has worked out what it touches - it takes the files, directories or symbols the change will land in (or a ticket, a pull request, or an idea in plain words), asks the company brain what it holds about those places, asks the code graph who uses the symbols in them, and writes an impact report of conclusions with references, with every unknown listed. Read-only - it records nothing in the brain. Also use when asked "what does this change touch", "what depends on this", "blast radius" or "impact analysis".
---

# Impact of a change

## Overview

A change request says what should move. It rarely says what else moves with it: the callers of
the function it names, and the decisions somebody already took about all of them. The platform
holds each of those answers in a different place. This skill chains them:

1. **input → places** — the files, directories and symbols the change lands in;
2. **places → what the company recorded** — the brain's own answer for each place, as
   conclusions with references (cards, files, documents, tickets, pull requests), each with
   whether a person confirmed what it rests on;
3. **places → dependents** — who uses each symbol in them, from the code graph, to a bounded
   depth.

**The server returns records; the prose is yours.** Every line of the report names the card or
the call it came from, and every place the chain broke is listed as an unknown rather than left
out. An impact report that looks complete because its gaps were dropped is worse than none: it
gets read as a guarantee.

**One mode today: usages-only.** Dependents come from `mcp__Fairmind__Code_find_usages`, one
symbol name at a time. Each usage it prints carries a relation: `calls` or `imports` is a
**graph usage**, recorded in the code graph; `text_match` is a **text match** — the name appears
in that function's lines, which may be a call, a mention in a comment or a string, or another
symbol that shares the name. The answer spans every repository of the project, so its rows are
kept to the bound repository before they count (Step 3). The platform serves no call-flow
tracing tool, so runtime paths, dynamic dispatch, message queues and HTTP calls between
services are not followed. The report says so on its first line, every time. Do not substitute
another tool for the missing one and present the result as a trace.

**Read-only.** This skill records nothing in the brain. Its output is a report file in the
user's repository; see *What this skill does not write* for where an impact result does reach
the brain.

**Announce at start:** "I'm using the brain-impact skill to trace what this change touches,
from the places it lands in to what the brain holds about them and who uses them."

**Dependencies:** requires a connected Fairmind workspace with a bound repository — see
`fairmind-context` for the resolution chain reused in Step 0. The call shapes, the fields read
at each step and a worked example live in `references/playbook.md`.

**What leaves your machine.** Read calls only, to the Fairmind platform your project's MCP
entry points at: one question per place, naming a repo-relative file path or a directory; the
idea in the user's own words, a requirement's text, or a ticket's or pull request's link or id,
when the input is one; and symbol names, which go to `mcp__Fairmind__Code_find_usages` — a
file that names no symbol is also read through `mcp__Fairmind__Code_cat`, to learn which
symbols it defines. Nothing is recorded in the brain; the platform keeps its own access log of
reads. The plugin README's *What leaves your machine* is the full account of every route.

## When to Use

Use this skill when:

- A change is about to be made and what it reaches is not known
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
3. Confirm these tools are in the tool list, by name: `mcp__Fairmind__Brain_ask` and
   `mcp__Fairmind__Code_find_usages`. A server that predates the platform release serving the
   brain's ask tool answers `mcp__Fairmind__Studio_*` and `mcp__Fairmind__Code_*` normally and
   offers no `mcp__Fairmind__Brain_*` tool at all. **If either is missing, say which and
   stop.** `mcp__Fairmind__Code_cat` is needed only for a file that names no symbol (Step 3.5)
   and for the repository check of Step 3.6; without it, such a file goes to *Open unknowns*
   untraced and the run continues.

## Step 1: Take the input and name the places

A **place** is a repo-relative file, a directory (its path ends in `/`), or a symbol inside a
file. Every form below ends in a list of places.

1. **Places the person names**, or "this change": the files the checkout reports as changed
   (`git status --short`, and the diff against the branch the work will merge into). Show the
   list and let the person correct it before anything is asked.
2. **A ticket or a pull request:** one `mcp__Fairmind__Brain_ask` with
   `question_type: "context"` and `subject: {"ticket": <its link, id or node id>}` or
   `{"pull_request": ...}`. Its `file` references are the places the work touched; they join
   the list of form 1.
3. **An idea in plain words:** restate it in one line and get the person's agreement, then ask
   with `question_type: "precedent"`, the idea as the `question`. A requirement's text goes in
   `subject: {"requirement": <text>}` instead. The `file` references of its considerations are
   where earlier attempts lived: they become places, each named with the consideration it came
   from. A requirement known only by its card id cannot be read from here; ask for its text.
4. **Handed over by `brain-new-requirement`:** the idea plus the precedents it already found,
   with their card ids and both of their states. Do not ask the `precedent` question again; the
   places are the `file` references of those precedents.

**An input with no place at all** — no file named, no reference in any answer — is still never
an empty report: say so first under *Open unknowns*, and offer up to five files from
`mcp__Fairmind__Code_search` on the input's own vocabulary as `suggested — from code search,
not from the brain`. They are asked about only if the person picks them.

## Step 2: Ask the brain about each place

Every `mcp__Fairmind__Brain_ask` call carries a `question` in plain words, `project` (the bound
`project_id`) and a `question_type`. What comes back is a short list of **considerations** —
each a sentence the brain composed, with whether a person confirmed what it rests on and the
references behind it — plus **further reading**: records found and not cited, and a `coverage`
sentence saying how much of the question the brain could answer.

1. For each place, at most **ten** in the run, ask `question_type: "file"` with
   `subject: {"file": {"repository": <the bound repository_id>, "path": <the place>}}` — a
   directory as its path ending in `/`, a symbol with `"symbol"` beside the path — and a
   `question` of one line naming the path and the change. A place past the tenth is named as
   "not read"; after an answer with `reason: cap_reached`, send no further `Brain_ask` in this
   run. Places are asked in the order of Step 1, the files the change names first.
2. **Cite each consideration with its state.** One whose `confirmed` is false is a draft nobody
   has confirmed and is cited "(proposed, not yet confirmed)", never bare; an entry of
   `further_reading` is listed as "found, not cited". A consideration is said to cite knowledge
   recorded **at a file** only when it comes from a `file` question and one of its `file`
   references has the bound repository and that path. On any other question a `file` reference
   carries the catalog's own spelling of the repository: quote its path as a pointer and make
   no claim about where anything was recorded.
3. Keep the `coverage` sentence of each answer. It is the brain's own account of what it holds
   about that place; it is quoted, never recomputed here, and never pooled with another
   place's. No considerations is "the brain holds nothing on this that this key may read", which
   is not proof that nobody considered it.

How to read what `Brain_ask` answers, for this skill, which only reads:

| Answer | What to do |
|---|---|
| `configuration_unset` or `disabled` (with a `key`) | quote the key and stop the brain step |
| `configuration_invalid` (with a `key`) | quote the key and stop |
| `retryable: True` (`retrieval_failed`, `catalog_unavailable`) | retry once, then say the brain could not be read — never "nothing is known" |
| access denied | say this key cannot read the brain |
| `project_unresolved` | route the person to `/fairmind-connect` |
| `invalid_parameter` (with a `parameter`) | fix that parameter once and ask again, then name it and stop |
| `composed: false` | say "listed, not composed", with the `reason`; for `nothing_found`, quote the `coverage` |
| `throttled` | stop, and never retry |

Stopping the brain step does not stop the run: the dependents of Step 3 still come from the
code graph, and the report says the brain was not read and why.

## Step 3: Places → dependents (usages-only)

For every traced symbol — the symbols the person named first, then the symbols of files that
name none (item 5):

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
     prints no repository per usage — item 6 settles it.
2. **Check the definition first.** The lookup is by name alone, so check each answer against
   the file the symbol is known to live in: the place's file at depth 1, and at depth 2 the
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
   *Open unknowns*. Never expand a caller twice — call graphs have cycles.
4. **`## No usages found` is an answer, not a failure:** nothing uses the name, in the graph or
   in the text. It can be an entry point, a handler registered by name, or dead code; say which
   only if the code shows it.
5. **A file that names no symbol** (the place is a file, not a symbol) is traced through the
   symbols it defines:
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
     `**Definition:**` must name the place's file.
   - The file defines no top-level symbol → *Open unknowns*, `file-level place — no top-level
     symbol to trace`. `## File Not Found` → `file-level place — file not in the code graph`.
   - The report says which method ran for every place, in the `Traced` column: `symbol`,
     `file-level — through {the names traced}`, or the reason nothing was traced.

   A directory names no file and no symbol and is not traced: `directory — dependents not
   traced`.
6. **Which repository a dependent sits in.** When the usage answer did not print it, read one
   file of each distinct directory of the dependents with `mcp__Fairmind__Code_cat`, passing
   `start_line: 1` and `max_lines: 1` — **at most 15** such reads per run. A file that is not
   in the bound repository's code graph answers `## File Not Found`: drop the usages listed
   under that file, count them by relation — "{n} graph usages and {n} text matches outside the
   bound repository left out" — and read the directory again with its next dependent file,
   against the same 15. A directory whose read found its file keeps its dependents as the bound
   repository's; the check is per directory, so a directory of the same path in another
   repository is not told apart. Dependents whose directory was never read stay counted and are
   marked `repository not checked`, and so do those a failed read could not settle: never
   dropped on a guess.
7. Keep per traced symbol: graph usages and text matches, distinct files and distinct callers
   at each depth, the file-level place it came from when it did, and the calls spent.

## Step 4: Write the report

The report is **conclusions with references**: what the brain holds about each place, as the
considerations that cite it with their confirmed or proposed state and the cards, files,
documents, tickets and pull requests behind them; and what the code graph shows about who uses
the symbols. It is no more than that. A place the brain was not asked about, or could not
answer, is listed with the reason, never left out.

Print the report, then write the same markdown to **`docs/reports/brain-impact-<YYYY-MM-DD>.md`**
in the user's repository (add the item's slug to the filename when there is more than one on the
same day). Say that you created it and let the user decide whether to commit it.

Every section of the template stays in the report even when it is empty — write "none found"
under it. A missing section and an empty one read differently, and only the second is an
answer.

## What this skill does not write

Nothing goes into the brain from here: not the report, not the touched files, not a new
requirement.

- **Run from `brain-new-requirement`**, the findings reach the brain through that skill's own
  write step: the files the change touches become its drafts' files and the conclusions their
  grounding.
- **Run on its own**, the report file is the output. Attaching it to an existing requirement
  would mean rewriting that requirement from a report; say so if the user asks for it, and
  leave the attaching to a person.

## Output Structure

```markdown
# Impact of a change — {item title or idea, one line} ({date})

**Mode: usages-only** — dependents from `Code_find_usages` to depth {d}: graph usages (`calls`, `imports`) and text matches of the name, kept to the bound repository. No call-flow tracing: runtime paths, dynamic dispatch, queues and calls between services are not followed.
**Nothing here was recorded in the brain.** A conclusion marked *proposed, not yet confirmed* rests on knowledge no person has checked.

## Input
- {files and directories named | ticket / pull request {link or id} | idea: "{one line}" | handed over by brain-new-requirement}
- Project {project_id} · repository {repository_id} · places: {n} ({n} asked, {n} not read)

## What the brain holds about the places
### {path or directory/}{ — from {ticket | pull request | the consideration it was found in}}
**Coverage:** {the answer's coverage sentence, verbatim | listed, not composed: {reason}}
- {conclusion} ({confirmed | proposed, not yet confirmed}) — references: {title and id of each card, file, document, ticket, pull request}
- Found, not cited: {title} ({confirmed | proposed, not yet confirmed})

### Places not read
- {path} — {past the cap | no answer: {why}}

## Places → dependents (usages-only)
| Place | Symbol | Traced |
|---|---|---|
| {path} | {symbol | —} | symbol / file-level — through {symbols traced} / directory — dependents not traced / {why nothing was traced} |

### `{symbol}` ({path}){ — from the file-level place on {path}, when it came from one}
- Depth 1: {n} graph usages and {n} text matches in {f} files, {c} callers — {caller} ({file}, {calls | imports | text match}), …
- Depth 2: {n} graph usages and {n} text matches in {f} files, through {c} callers
- Outside the bound repository, left out: {n} graph usages, {n} text matches · repository not checked: {n}
- {name collision — usages unverified | no definition line — usages unverified | no usages found}

## Open unknowns
1. {a place not read, with the reason · a file or directory not traced · a file read only in part · a name collision or a missing definition line · callers left unexpanded by the budget · dependents whose repository was not checked · an answer the brain could not compose}

## Yield
- Places: {n} · asked of the brain: {n} of 10 · answered {n}, listed not composed {n}, refused {n}
- Conclusions: {n} (confirmed {n}, proposed {n}) · found, not cited: {n}
- Symbols traced: {n}, of which {n} through file-level places · `Code_find_usages` calls: {n} of 30 · `Code_cat` pages: {n} of 6 · repository checks: {n} of 15 · depth reached: {d}
- Dependents: {n} graph usages and {n} text matches in {n} files at depth 1; {n} graph usages and {n} text matches in {n} files at depth 2; unverified (name collision or no definition line) {n}; outside the bound repository, left out {n}; repository not checked {n}
- Recorded in the brain: 0 — this skill is read-only
- Report written to: docs/reports/brain-impact-{date}.md
```

## Next Steps

- **Before implementing**, read the conclusions in the report that bind the change, and resolve
  the open unknowns that matter to it.
- **For a new requirement** built on this analysis, load `brain-new-requirement` — it calls
  this skill for its impact step and tells the drafts with the files it found.
- **For implementation**, load `fairmind-context`, then `fairmind-tdd`.
- **An empty answer from the brain is a gap in what the brain was given**, not evidence that
  nothing was decided there. Say so when it covers most of the touched files.

## Error Handling

**No Fairmind MCP connected / `fairmind: "none"` / no `repository_id`:** stop and point the
user at `/fairmind-connect`. Do not fall back to grepping the local tree and calling the
result an impact analysis.

**No `mcp__Fairmind__Brain_ask` in the tool list:** the connected server predates the platform
release that serves it. Name that release, say the `mcp__Fairmind__Code_*` tools are
unaffected, and stop.

**An MCP error result on `Brain_ask`:** the call's shape is wrong, or the server does not offer
the tool. Name it and stop the brain step; the same call again is refused again.

**`## Entity Not Found` from `mcp__Fairmind__Code_find_usages`:** the code graph does not
know the symbol — the name is stale, or the repository was re-ingested since the place was
written. Report it next to that place. **`## Service Unavailable`:** one retry, then report the
dependents of that symbol as not read. **`## Error: Repository Not Found`:** the binding is
wrong for this checkout — stop and point at `/fairmind-connect`.

**`## File Not Found` from `mcp__Fairmind__Code_cat`:** the code graph holds no such file in
the bound repository. A file-level place is not traced; it goes to *Open unknowns* with that
reason.

**A repository whose code graph is stale or not ingested:** proceed, and say so at the top of
the report. An impact read from a stale graph is the impact on an older system.
