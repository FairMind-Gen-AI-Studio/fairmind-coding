# Report template — `docs/reports/brain-rebuild-<YYYY-MM-DD>.md`

The markdown report is written into the repository being reconstructed, at
`docs/reports/brain-rebuild-<YYYY-MM-DD>.md`. It is the developer's copy of what the drafts say
and where they came from; the drafts themselves live in the brain as proposals.

Three things about the file, worth saying out loud when you create it:

- **It lands in the user's own tree**, unlike most of what this plugin produces (which either
  stays in the conversation or goes under the gitignored `.fairmind/` workspace). Tell them it
  is there and let them decide whether to commit it.
- **The date prefix makes it point-in-time.** A second run on another day writes another file
  rather than overwriting this one, which is what allows two reconstructions to be compared.
- **It must be readable without the brain.** Every claim carries its title *and* its id, and
  enough words to be understood by someone who cannot open that record.

The report states conclusions with references. The brain's answer to a directory question is a
set of conclusions and one `coverage` sentence, and the report quotes both; it is not a graph
and not an inventory of files.

---

```markdown
# Requirements reconstruction — {repository_name}

Generated {YYYY-MM-DD} from the code graph and the company brain.
**Status: proposed — awaiting human review.** Nothing here is confirmed knowledge; every draft
listed below exists in the review queue as a proposal until a person confirms or rejects it.

## Scope

| | |
|---|---|
| Repository | {repository_name} (catalog id `{repository_id}`) |
| Branch | {repository_branch} |
| Project | {project_name} (`{project_id}`) |
| Modules | {n} |
| Files inventoried | {n} |
| Precedents | {checked / not checked: {why}} |
| Modules not asked | {list, or none} |

## How to read this

**Confidence** is about how firmly a draft is tied to code:

- **Anchored** — the draft names a file or function that exists in the inventory today.
- **Inferred** — derived from the code with no brain record behind it. Not knowledge yet.

**Review state** is a different axis, about *trust*, and it is printed beside every
consideration and every precedent:

- **confirmed** — a person agreed with the record it rests on. It is knowledge.
- **proposed, not yet confirmed** — a draft nobody has confirmed. On any run after the first,
  some of these are **this report's own predecessors**: a precedent marked that way means the
  draft above it rests on an earlier guess, not on a decision anyone made.

---

## {Module name}

`{path/}` · {n} files

### What the brain holds about this code

{The `coverage` sentence of the module's answer, verbatim — or "listed, not composed: {reason}".}

- {Conclusion, in the brain's words} ({confirmed | **proposed, not yet confirmed**}) —
  references: {title and id of each card, file, document}
- Found, not cited: {title and id, or none}

### Functional requirements

#### FR-{n} · {title}
{The requirement, in one or two sentences.}

- **Files:** `{path}` · `{function}`
- **Precedents:** {title} (`{id}`) ({confirmed | **proposed, not yet confirmed**}) — {what that record is}
- **Confidence:** anchored | inferred
- **Title told:** `{module} · {requirement}`

### Technical requirements

#### TR-{n} · {title}
{The requirement.}

- **Files:** `{path}`
- **Precedents:** {title} (`{id}`) ({confirmed | **proposed, not yet confirmed**})
- **Confidence:** {…}
- **Title told:** `{module} · {requirement}`

### Contradictions — listed, not resolved

- The brain records {X} ({title}, `{id}`, {date}). The code does {Y} (`{path}:{symbol}`).
  **Unresolved: a person who knows why has to decide.**

### Removed or replaced — do not re-specify

- **{title}** (`{id}`) — {removed|replaced} {date}. Reason as recorded: "{reason}".
  {Record behind it, when there is one: {title} (`{id}`).}

### Open questions

1. {Question} — blocks {which draft}.

---

## Coverage

The brain's own account of each module is the `coverage` sentence quoted in its section. It is
not recomputed, and no figure here is comparable across runs except as that sentence.

### Requirements about paths that resolve to nothing ({n})

| Requirement | Id | Path that no longer resolves |
|---|---|---|
| {title} | `{id}` | `{path}` |

These are requirements about code that moved, was renamed, or was removed. Each one is either a
requirement to retire or a path to repair — a person decides which.

---

## Yield

| | |
|---|---|
| Drafts told | {n} — functional {n}, technical {n}; every one `proposed` |
| Already confirmed by a human | {n} — `confirmed` among what the module answers returned; 0 on a first run |
| With ≥1 file or function | {n} / {n} |
| With ≥1 brain precedent | {n} / {n} — of which unconfirmed proposals {n} |
| Answered `duplicate` | {n} — already there, nothing changed |
| Answered `conflict` | {n} — a record under that title says something different; nothing overwritten |
| Not written (refused or failed) | {n} |
| Modules covered | {n} / {n} |

**Hand check:** would a reviewer accept this as the requirement of that module?

Confirm or reject each draft in the review queue. Nothing in this report is confirmed knowledge
and no part of the run confirmed anything.

## Reproducing this run

- Titles are `{module} · {requirement}` — rerunning with the same module names finds these
  drafts instead of creating new ones.
- Module names used: {list}.
- Scope: {whole repository | the subtree `{path}`}.
```
