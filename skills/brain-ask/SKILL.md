---
name: brain-ask
description: Use when a question is about why the code is the way it is, what was decided before, whether an idea was already considered or dropped, what speaks against an approach, or what a ticket or pull request established - ask the company brain with one well-formed Brain_ask call before reading code, GitHub or the tracker, then verify only what the answer leaves open. Also use when asked "was this decided", "why is it done this way", "did we try this before" or "what do we know about ticket X".
---

# Asking the company brain

## Overview

The brain holds the decisions behind the code, with the pull requests and tickets around them,
as short cards. One `mcp__Fairmind__Brain_ask` call usually answers a question that would
otherwise take a dozen reads across the repository, GitHub and the tracker. A malformed call
answers nothing and costs a turn, so build the call by the rules below, read what comes back,
and go to the raw sources only for what the answer leaves open. This skill only reads: it
records nothing in the brain.

## Step 0: Preconditions

Confirm `mcp__Fairmind__Brain_ask` is in the tool list, by name. A server that predates the
platform release serving it offers no `mcp__Fairmind__Brain_*` tool at all. If it is missing,
say so, stop using this skill, and answer from the repository and the other sources, saying
the brain could not be asked.

## Step 1: Pick the question type

| The question is about | `question_type` | `subject` |
|---|---|---|
| why one file or directory is the way it is | `file` | `{"file": {"repository", "path"}}`, a directory path ends in `/` |
| whether something like this was considered before, dropped or replaced | `precedent` | none, or `{"requirement": "<its text>"}` |
| what speaks against an approach you propose | `contraindications` | none, or `{"file": {...}}` when it is about one file |
| what is known about a ticket or a pull request | `context` | `{"ticket": "<link, id or node id>"}` or `{"pull_request": "<link, id or node id>"}` |

When unsure between `precedent` and `contraindications`: "did we already decide this" is
`precedent`; "is this a bad idea" is `contraindications`.

## Step 2: Build a valid call

- Always send `question_type`. A call without it is refused.
- Always send a `question`: one sentence describing the idea or the doubt, 1 to 2000
  characters. Describe what you want to know, not the card you expect to find.
- `subject` holds **at most one** key: `file`, `ticket`, `pull_request` or `requirement`.
  Two keys in one call are refused.
- A `precedent` question takes no `file`, `ticket` or `pull_request` in `subject`. With a file,
  ask a `file` question; with a ticket or a pull request, ask a `context` question.
- When the question names a file, a ticket or a pull request, pass it in `subject`: the brain
  then reads that item and what it links to, instead of only searching by similarity.
- Send `project` (the `project_id` in `.fairmind/active-context.json`) when the checkout has
  one; a key issued for one project reads that project without it.

## Step 3: Read the answer

`considerations` are the brain's answer, each a sentence with whether a person confirmed what
it rests on and the `references` behind it (cards, files, tickets, pull requests, documents):
conclusions with references.

- Name the references you relied on. A consideration whose `confirmed` is false rests on
  knowledge no person has checked yet: say "(proposed, not yet confirmed)" when you use it.
- `further_reading` lists what was found and not cited: open it only when the considerations
  are not enough.
- A consideration is said to cite knowledge recorded **at a file** only when it comes from a
  `file` question and one of its `file` references has the repository and path you asked
  about. On any other question a `file` reference is a pointer: quote its path and make no
  claim about where anything was recorded.
- No considerations (`reason` `nothing_found` or `no_supported_consideration`) means the brain
  holds nothing that answers. It is not proof that nobody considered it.

| Answer | What to do |
|---|---|
| `configuration_unset` or `disabled` (with a `key`) | quote the key; answer from the other sources and say the brain is not set up |
| `configuration_invalid` (with a `key`) | quote the key; answer from the other sources |
| `retryable: True` (`retrieval_failed`, `catalog_unavailable`, `screen_failed`, `screen_malformed`) | ask the same call once more, then say the brain could not be read — never "nothing is known" |
| access denied | say this key cannot read the brain |
| `project_unresolved` | route the person to `/fairmind-connect` |
| `invalid_parameter` (with a `parameter`) | fix that parameter by the rules of Step 2 and ask once more; if it is refused again, name it and continue without the brain |
| `composed: false` | the answer lists what was found without composing it: read the cards, quote the `reason` |
| `throttled` | stop asking, and never retry: the budget is spent |
| `cap_reached` | send no further `Brain_ask` in this session |

## Step 4: When to call again, and when to go to the sources

- One call per question is the norm. Call again only when the answer has no considerations,
  and then change something: the type (`precedent` to `contraindications`, or the reverse), or
  add the subject you now know. Never repeat an identical call.
- Do not call `mcp__Fairmind__Brain_overview` to answer a specific question: it returns the
  project brief, not an answer.
- Read the code, GitHub or the tracker after the brain only for what the answer leaves open:
  the current behaviour of the code, a detail of a pull request it cites, a ticket it does not
  hold. Start from the references the brain gave.
- If the brain found nothing after two different calls, search the sources yourself and say in
  the answer that the brain held nothing on it.

## Step 5: Answer

State the conclusion first, then the decisions, pull requests or tickets it rests on, by their
references. Mark what rests on unconfirmed knowledge, and say plainly when nothing relevant was
found.
