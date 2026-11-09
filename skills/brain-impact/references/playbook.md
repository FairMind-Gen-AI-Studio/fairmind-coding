# Playbook — the impact of a change

On-demand detail for the `brain-impact` skill: the tools it calls and the fields it reads from
each answer, how to walk dependents without losing count, and a worked example end to end.

## The tool surface

| Tool | Used for | Step |
|---|---|---|
| `mcp__Fairmind__Brain_ask` | what the brain holds about a place (`file`), a ticket or pull request (`context`), an idea (`precedent`) | 1, 2 |
| `mcp__Fairmind__Code_find_usages` | who uses a symbol, one name at a time | 3 |
| `mcp__Fairmind__Code_cat` | the symbols a file defines, when a place names the file and no symbol; which repository a dependent's file sits in | 3 |
| `mcp__Fairmind__Code_search` | suggested files when nothing names a place — offered, never assumed | 1 |

Every call on this list reads. No brain write door is on this skill's menu, whatever an answer
suggests.

## Step 1 — which question each input takes

| Input | Call | The places are |
|---|---|---|
| Files, directories or symbols the person names | none yet | exactly those |
| "This change" | none; the checkout's changed files (`git status --short`, the diff against the target branch) | the files git reports, shown to the person first |
| A ticket | `Brain_ask`, `question_type: "context"`, `subject: {"ticket": <link, id or node id>}` | the `file` references of the answer |
| A pull request | `Brain_ask`, `question_type: "context"`, `subject: {"pull_request": <link, id or node id>}` | the `file` references of the answer |
| An idea in plain words | `Brain_ask`, `question_type: "precedent"`, the idea as `question` | the `file` references of its considerations, each tied to the consideration it came from |
| A requirement's text | the same, with `subject: {"requirement": <text>}` | the same |
| A hand-over from `brain-new-requirement` | none | the `file` references of the precedents it passes, as passed |

An answer's `file` reference is `{'type': 'file', 'repository', 'path'}`. On a `context` or
`precedent` answer the `repository` is the catalog's own spelling, so a place read off it is a
pointer for the next question, not a claim about where anything was recorded.

## Step 2 — reading a `Brain_ask` answer

`Brain_ask` answers `{'considerations', 'further_reading', 'composed', 'derived_by', 'model',
'prompt_version', 'coverage'}`:

- Each **consideration** is `{'text', 'confirmed', 'references'}`; a reference is a `card`
  (`id`, `title`), a `document` (where the original lives), a `file` (`repository`, `path`), a
  `ticket`, a `pull_request` or a `commit`. `confirmed: false` means the consideration rests on
  knowledge no person has checked: cite it "(proposed, not yet confirmed)".
- **`further_reading`** lists what was found and not cited, each `{'title', 'confirmed',
  'references'}`. List it as "found, not cited"; open it only when the conclusions are not
  enough.
- **`coverage`** is the brain's own sentence about what it has been given for the place. Quote
  it verbatim.
- **`composed: false`** means the answer lists what was found without composing it; `reason`
  says why (`nothing_found` is the empty case: quote `coverage`).

A directory is asked by a path ending in `/`. A `file` answer is the same for every question
about that file, so the `question` only has to name the path and the change.

A refusal is `{'status', 'reason', 'message'}`: `configuration_unset` and `disabled` carry the
`key` to quote, `retryable: True` means ask again once, and `reason: cap_reached` ends the
run's questions.

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
  function's lines, with no graph link behind it: a call the graph missed, a comment, a string,
  a log message, or another symbol of the same name. Count and print the two apart. A symbol
  whose only usages are text matches has no graph usage at all, and the report says so.
- **The rows come from every repository of the project**, graph usages as well as text matches,
  and today's answer prints no repository beside a usage. A file under another repository reads
  like one of yours. The per-directory `Code_cat` read of SKILL.md Step 3.6 is the check: a
  `## File Not Found` for a dependent file means the bound repository's code graph holds no such
  file, and its rows are left out and counted. When a later answer names the repository on the
  usage line or the file heading, that answer settles it and the check is not needed.
- **The definition line is the collision check.** The lookup is by name, and a name like
  `get`, `save` or `__init__` has many definitions. Compare `**Definition:**` with the file
  the symbol is known to live in — the place's file at depth 1; at depth 2, the file whose
  `###` heading the caller was listed under. A different file is `name collision — usages
  unverified`; no `**Definition:**` line at all (the graph holds no file for the name) is `no
  definition line — usages unverified`. Both are counted apart from the dependents.
- **`**Found:**` is the usage count**; the `###` headings are the distinct files; the bold
  names are the calling functions — the depth-2 frontier.
- **`## No usages found`** is zero users, in the graph and in the text. **`## Entity Not
  Found`** is a symbol the code graph does not know, which for a name that resolved a moment
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

## Step 3 — a file that names no symbol, through the symbols it defines

A place that is a file, with no symbol beside it, names nothing to look up by name. Its
dependents are the users of what the file defines:

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
3. Each is traced like a symbol, against the same 30 calls, with the place's file as the file
   its `**Definition:**` must name. The place's line in the report says `file-level — through`
   and the names, so a reader knows the dependents came from the file's symbols and not from a
   symbol the person named.

## Why the report stays a file

The brain's tell door records a whole requirement, decision or issue as the writer drafted it.
A report is neither: it is a reading of the code and of what the brain holds, valid on the day
it was written. Run from `brain-new-requirement`, its findings go in as the drafts' files and
the grounding they cite, in that skill's own write step; run on its own, the file is the whole
output.

## Worked example

> **Input:** the files `app/billing/subscription.py` and `app/api/` — a change that lets
> customers pause a subscription for up to 90 days.

1. **Places.** One file and one directory, as named. Two asks of ten.
2. **The brain.** `Brain_ask` `file` on `app/billing/subscription.py`: three considerations. One
   says the decision *Invoices are reconciled per seat-day* binds the module (confirmed, cites
   the card, the file and a pull request); one says paused seats were double-billed once
   (confirmed, cites an issue card); one rests on a draft nobody confirmed, so it is cited
   "(proposed, not yet confirmed)". `further_reading` lists one more issue, found and not
   cited. Its `coverage` sentence is quoted. `Brain_ask` `file` on `app/api/`: no
   considerations, `composed: false`, `reason: nothing_found` — the report quotes the
   `coverage` and says the brain holds nothing readable there, not that nothing was decided.
3. **Dependents.** The file names no symbol: `Code_cat` shows top-level `pause` and
   `prorate_pause`. `Code_find_usages("pause")`: definition in `app/billing/subscription.py` —
   matches. 5 usages in 4 files: callers `pause_endpoint()` and `bulk_pause()` (`calls`),
   `handle_dunning()` (`text_match` — the name in a log line, reported as a text match), and
   `replay_pause()` (`calls`) in `scripts/replay.py`. Depth 2 on the callers: 6 more usages in
   4 files. 5 calls of 30. The directories `app/billing/`, `app/api/` and `scripts/` get one
   `Code_cat` read each; `scripts/replay.py` answers `## File Not Found`, so its one usage is
   left out and counted — "1 graph usage outside the bound repository left out".
   `app/api/` is a directory place: `directory — dependents not traced`.
4. **Report.** Mode line first; `app/api/` under *Open unknowns*; `Recorded in the brain: 0`;
   written to `docs/reports/brain-impact-<date>.md`.
