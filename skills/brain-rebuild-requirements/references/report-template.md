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
- **It must be readable without the brain.** Every claim carries its node id *and* enough words
  to be understood by someone who cannot open that node.

---

```markdown
# Requirements reconstruction — {repository_name}

Generated {YYYY-MM-DD} from the code graph and the company brain.
**Status: proposed — awaiting human review.** Nothing here is confirmed knowledge; every draft
listed below exists in the review queue as a proposal until a person confirms or rejects it.

## Scope and freshness

| | |
|---|---|
| Repository | {repository_name} (catalog id `{repository_id}`) |
| Branch | {repository_branch} |
| Project | {project_name} (`{project_id}`) |
| Modules | {n} |
| Files inventoried | {n} |
| Brain last synced | {last_synced_at} |
| Sync state | {sync_state} |
| Code graph | {available / unavailable} |

> {One sentence on what the freshness figures mean for this report — e.g. "the graph was last
> synced 11 days ago, so anchors into code added since then resolve as unresolved."}

## How to read this

**Confidence** is about the *anchor* — how firmly a draft is tied to code:

- **Anchored** — the draft points at code that exists in the graph today.
- **Semantic** — the brain record was found by meaning, not by an anchor. Weakest tier; treat
  as a lead.
- **Inferred** — derived from the code with no brain record behind it. Not knowledge yet.

**Review state** is a different axis, about *trust*, and it is printed beside every precedent:

- **confirmed** — a person agreed with this record. It is knowledge.
- **proposed, not yet confirmed** — a draft nobody has confirmed. On any run after the first,
  some of these are **this report's own predecessors**: a precedent marked that way means the
  draft above it rests on an earlier guess, not on a decision anyone made.

---

## {Module name}

`{path/}` · {n} files · {n} brain items

### Functional requirements

#### FR-{n} · {title}
{The requirement, in one or two sentences.}

- **Anchors:** `{path}` · `{symbol}` ({FUNCTION|CLASS|FILE})
- **Precedents:** `{node_id}` ({confirmed | **proposed, not yet confirmed**}) — {what that record is}
- **Confidence:** anchored | semantic | inferred
- **Natural key:** `{module}:{slug}`

### Technical requirements

#### TR-{n} · {title}
{The requirement.}

- **Anchors:** `{path}`
- **Precedents:** `{node_id}` ({confirmed | **proposed, not yet confirmed**})
- **Confidence:** {…}
- **Natural key:** `{module}:{slug}`

### Contradictions — listed, not resolved

- The brain records {X} (`{node_id}`, {status}, {date}). The code does {Y} (`{path}:{symbol}`).
  **Unresolved: a person who knows why has to decide.**

### Retired or superseded — do not re-specify

- **{title}** (`{node_id}`) — {retired|superseded} {date}. Reason as recorded: "{reason}".
  {Decision behind it, when there is one: `{node_id}`.}

### Open questions

1. {Question} — blocks {which draft}.

---

## Coverage

### Files nothing explains ({n})

Measured with `include_proposed: false`, so this counts what **human-confirmed** knowledge
explains, not what this skill has already proposed about the repository. State it that way
whenever the number is quoted; a coverage figure without its setting cannot be compared with
the next run's.

| pass | `uncovered[]` |
|---|---|
| coverage (`include_proposed: false`) | {n} |
| evidence (`include_proposed: true`) | {n} |

{On a rerun, two identical counts mean the filter did not reach `uncovered[]` — the coverage
number is counting this skill's own drafts, and must not be read as human knowledge. On a
first run they agree trivially, because there are no proposals yet.}

| File | Module |
|---|---|
| `{path}` | {module} |

{When the list is long, give the count per module here and the full list in the appendix.}

### Requirements whose anchors resolve to nothing ({n})

| Requirement | Node id | Anchor that no longer resolves |
|---|---|---|
| {title} | `{node_id}` | `{path}` |

These are requirements about code that moved, was renamed, or was removed. Each one is either a
requirement to retire or an anchor to repair — a person decides which.

### Resolution tiers

| extracted | inferred | ambiguous | unresolved | semantic |
|---|---|---|---|---|
| {n} | {n} | {n} | {n} | {n} |

---

## Yield

| | |
|---|---|
| Drafts recorded | {n} — functional {n}, technical {n}; every one `proposed` |
| Already confirmed by a human | {n} — `review_state: confirmed` among what the context calls returned; 0 on a first run |
| With ≥1 resolving code anchor | {n} / {n} |
| With ≥1 brain precedent | {n} / {n} — of which unconfirmed proposals {n} |
| Revision proposals filed | {n} — the natural key already belonged to a confirmed record |
| Not written (refused or failed) | {n} |
| Modules covered | {n} / {n} |

**Hand check:** would a reviewer accept this as the requirement of that module?

Confirm or reject each draft in the review queue. Nothing in this report is confirmed knowledge
and no part of the run confirmed anything.

## Reproducing this run

- Natural keys are `{module}:{slug of the title}` — rerunning with the same module names
  updates these drafts instead of creating new ones.
- Module names used: {list}.
- Scope: {whole repository | the subtree `{path}`}.
```
