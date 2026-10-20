# TaskDraft schema

`TaskDraft` is the normalized output every intake adapter (`scripts/loop_import.py`)
produces from an external ticket. Downstream task-compilation code depends
only on this schema — never on the shape of a specific source (a GitHub issue
JSON payload, a pasted string, a future MCP payload). An adapter's job is
**fetch → normalize**: obtain the raw ticket from its source, then map it onto
the fields below. A source-specific detail that doesn't fit the schema is
carried in `meta` rather than invented as a new top-level field.

## Shape

```json
{
  "source": { "kind": "gh-issue" | "pasted" | "mcp:<name>", "ref": "<string>", "url": "<string|null>" },
  "title": "<string>",
  "body": "<string>",
  "acceptance_criteria": [ { "id": "<string>", "text": "<string>", "span": "<string|null>" } ],
  "meta": { }
}
```

## Fields

### `source`

Identifies where the ticket came from and how to find it again.

- `kind` — one of three forms:
  - `"gh-issue"` — a GitHub issue fetched via `gh issue view --json`.
  - `"pasted"` — raw text pasted directly by a user, with no external system
    of record behind it.
  - `"mcp:<name>"` — a ticket fetched through an MCP tool named `<name>`
    (e.g. `"mcp:jira"`, `"mcp:clickup"`). `<name>` identifies the MCP-reachable
    tracker, not a specific ticket instance. The normalizer that produces it
    is either the LLM (the default for a tracker with no shipped adapter —
    `references/adapters.md` §1) or a shipped deterministic adapter, e.g.
    `--adapter clickup` (`references/adapters.md` §2).
- `ref` — a stable identifier for the ticket within its source. For
  `gh-issue`, the issue number as a string. For `pasted`, any stable
  non-empty string derived from the input (the current adapter uses a SHA-256
  content hash) — there is no external ref to reuse.
- `url` — a dereferenceable link back to the ticket, or `null` when none
  exists (always `null` for `pasted`).

### `title`

Non-empty string. For `gh-issue`, the issue's `title` field verbatim. For
`mcp:clickup`, the task's `name` field verbatim. For `pasted`, the adapter
derives one (the current adapter uses the first non-empty line of the
input).

### `body`

String. The ticket's full body/description, carried byte-for-byte from the
source with no stripping or normalization — later stages need the original
text (formatting, whitespace, code fences, trailing newline) intact.

### `acceptance_criteria`

List of `{ "id": "<string>", "text": "<string>", "span": "<string|null>" }`.
Each entry is a single acceptance criterion extracted from the ticket body.
`id` is a stable per-draft identifier; `text` must be a non-empty string;
`span` optionally locates the criterion within `body` (e.g. a line range or
excerpt marker) and is `null` when extraction did not track one.

An empty list is valid — extraction is a distinct, later concern from
adaptation. Every adapter in this codebase currently emits `[]`; a future
extraction pass fills this in from `body` without touching the adapters
themselves.

### `meta`

Object. Source-specific fields that don't map onto the schema above but that
later stages (or a human) may still need. For `gh-issue` this currently
includes `labels`, `assignees`, `author`, `state`, `createdAt`, `updatedAt`,
and `milestone`, passed through with their original values rather than
dropped. For `mcp:clickup` (the `--adapter clickup` path) this is every
top-level field of the ClickUp task payload except `id`, `url`, `name`, and
the three description variants (`description`, `markdown_description`,
`text_content` — all promoted; the rationale lives on
`_CLICKUP_PROMOTED_FIELDS` in `scripts/loop_import.py`):
`status`, `assignees`, `custom_fields`, `parent`, `list`, `date_created`, and
so on. `{}` is valid when a source has nothing extra to carry (the `pasted`
adapter always emits `{}`).

## Adapter contract

An adapter is a pure function from "raw ticket in its native shape" to
`TaskDraft`: read (or accept) the raw payload, then normalize it onto the
schema above. The task compiler and every stage after it read only
`TaskDraft` — they have no knowledge of `gh-issue`, `pasted`, or any other
source. A new source is added by writing a new adapter that emits this
schema, never by teaching downstream code a new source kind.

## Validation (`--validate-draft`)

`loop_import.py --validate-draft` checks a draft structurally, independent of
source, and is fail-closed — every malformed field is rejected, never
silently accepted:

- `source` is an object with a non-empty string `kind` matching `gh-issue`,
  `pasted`, or `mcp:<name>`.
- `title` is a non-empty string.
- `body` is a string.
- `acceptance_criteria` is a list; if non-empty, every entry is an object
  with a non-empty string `text` and a present `id`. An empty list is valid.
- `meta`, if present, is an object.

On failure the command exits non-zero and prints one reason per violated rule
to stderr, each naming the specific field that failed (e.g. `source.kind`,
`acceptance_criteria[0].text`) — never a generic "invalid draft" message.

## Adding a new adapter

> **First decide *whether* to write one.** For an MCP-backed tracker with no shipped adapter
> (Jira, Linear, …) you usually should **not**: the LLM normalizes the payload to `TaskDraft`
> directly, with no per-tracker code. Write a *deterministic* adapter (the steps below) only when
> you need a byte-reproducible, LLM-free path (CI/offline) — `--adapter clickup` is the worked
> example. See `references/adapters.md` for the decision rule, the LLM-as-adapter flow, and the
> no-fetch/no-credentials constraint that path carries.

1. Pick a `source.kind` (`mcp:<name>` for MCP-backed sources).
2. Implement the fetch/read step for the new source.
3. Map the source's fields onto `title` and `body`; pass through anything not
   otherwise represented into `meta`; leave `acceptance_criteria` as `[]`
   unless the adapter itself performs extraction.
4. Validate the result with `--validate-draft` before treating it as usable
   by the rest of the pipeline.
