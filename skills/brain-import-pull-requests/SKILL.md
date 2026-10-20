---
name: brain-import-pull-requests
description: Use only when a person asks to bring a repository's pull requests into the company brain - it reads the merged and closed pull requests of the checkout's GitHub repository with the person's own gh credential, collects each one's description, comments and review conversations, shows how many will be sent and whose words travel, and after a yes imports them, one call per pull request or several for a large one, so a later read of a file returns the review conversations written on it. Also explains how a project admin removes one person's conversations. Also use when asked to "import the pull requests", "put our PR reviews in the brain", or "backfill the review history".
---

# Pull requests into the brain

## Overview

The review conversations of a pull request are where a team says why code is the way it is, and
they sit in GitHub where nothing else can read them. This skill brings them into the company
brain: one `pull_request` card per pull request, and one `review_thread` card per conversation
anchored to a file, tied to the file and line it was written on. Once they are in, a read at that
file (`mcp__Fairmind__Brain_context`) returns the conversations written on it.

**Cards are recorded confirmed, with the agent as their author.** An import asserts only that a
conversation happened, so nothing waits for review — which is why the skill asks before it sends,
and why the removal path below exists.

The skill makes one call per pull request, or several for a large one — it never drops a
conversation to fit. Each call spends one unit of the tenant's daily brain write budget. An
unchanged re-import by the same person writes nothing.

**Announce at start:** "I'm using the brain-import-pull-requests skill to import this
repository's pull requests and their review conversations into the company brain."

**What leaves your machine when this skill writes.** For each call,
`mcp__Fairmind__Brain_import_pull_request` sends the pull request's title, **whole description**,
state, number, URL, permanent ids and commit ids; **every comment and every non-empty review
summary, whole, as written on GitHub**; each review conversation's file path, line, commit and
resolved or outdated state; the **repo-relative path of every file the pull request changed**, with
the lines added and removed in each (at most 300, and the real number when there are more); and the **GitHub login of every person and bot who wrote any of it**.
With them go the repository's catalog id, the project id and the checkout's remote URL. It is read
from GitHub with the person's own `gh` credential, and nothing is posted to GitHub. This is the
**brain write-back**, and the plugin README's *What leaves your machine* describes it in full.

## When to Use

Use this skill when a person asks for it:

- Backfill the review history of a repository the brain has already ingested.
- Bring newly merged pull requests in. A rerun is cheap: unchanged ones answer `written: 0`.

Do **not** use it to:

- Import open pull requests unless the person asks for them.
- Post anything to GitHub. This skill only reads.
- Erase someone's words because they asked for it themselves. A person does not remove their own
  comments; see the last section.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the `fairmind`
   field set to `"configured"`, the `project_id` and the `repository_id` that `/fairmind-connect`
   writes. **Never write those keys yourself.** If the file is missing or a key is absent:
   **stop** and tell the user to run `/fairmind-connect` once for this checkout. A bound
   repository is what lets a read of a file find the conversations.
2. Confirm the `mcp__Fairmind__Brain_*` tools are in the tool list. They arrive with the
   platform's **Brain-1** release. If they are absent, say which release is needed and stop.
3. Confirm `mcp__Fairmind__Brain_import_pull_request` is in the tool list by name. It arrives with
   a later platform release than the other `mcp__Fairmind__Brain_*` tools. If it is absent, say so
   and **stop** — never record a pull request through another door.
4. `gh auth status` must succeed, and `git remote get-url origin` must be a GitHub URL. Otherwise
   say which of the two failed and stop.

## Step 1: Choose the pull requests

1. Run, from the repository root:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py list --limit 50
   ```

   It lists merged and closed pull requests, newest first. Open ones only when the person asks
   (`--states OPEN`, and `--allow-open` on collect). `--since YYYY-MM-DD` narrows by date.
2. Ask which: all listed, the last N, a date range, or specific numbers.

## Step 2: Collect, then show what is about to leave and ask

1. For every chosen number, run (GitHub reads only; nothing is sent anywhere yet):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py collect --number <n> --out-dir .fairmind/brain-import
   ```

   It writes one file per call under `.fairmind/brain-import/<n>/` and prints a report: `parts`,
   `files`, `logins`, `conversations_total`, `skipped_too_large`, `pr_turns_total`,
   `pr_turns_sent`, `changed_files` and `changed_files_sent`. A pull request whose list was cut
   sends the real number as `file_count`. On exit 2, 3, 4 or 5, record the pull request as not collected, with the script's
   message, and continue. On exit 127 or a `gh` authentication failure, stop: every later one
   fails the same way.
2. **Before the first call, show what is about to leave and ask.** Show:
   - the number of pull requests and the total number of calls (the sum of `parts`), noting that
     each call spends one unit of the tenant's daily brain write budget;
   - that the path of every file a pull request changed travels with the lines added and removed
     in each, up to 300 files, and the real number when a list was cut;
   - that whole descriptions and comments travel, with the GitHub login of every author: list the
     combined `logins`, bots included and `ghost` for deleted accounts;
   - which pull requests go in several calls and which are partial (pull-request turns above 99,
     or `skipped_too_large`);
   - that the cards land confirmed and are readable by everyone on the project, and the project
     they go to.

   One yes covers the batch. No yes: nothing is sent. What is sent is exactly the files shown —
   never collect again between the yes and the send.

## Step 3: Import, one call per file

For each pull request, in order, for each of its `files` in order (`part-1`, `part-2`, …):

1. Call `mcp__Fairmind__Brain_import_pull_request` with exactly these arguments:

   ```
   repository:         <repository_id from active-context>
   git_remote:         <git remote get-url origin>
   project:            <project_id from active-context>
   pull_request:       <the file's pull_request, verbatim>
   conversations:      <the file's conversations, verbatim>
   conversation_count: <the file's conversation_count — leave it out only when null>
   ```

   Pass the file's JSON **verbatim**: never shorten, summarise, reorder or re-spell a body or a
   login. Spelling is identity, and an admin erases by it. Never drop `conversation_count` from a
   part that carries it: a part sent without it would retire every conversation the other parts
   wrote. Do not send `agent`, `session_ref` or `task_ref`: the door has no such arguments.
   Send `agent_accounts` only when the person names the logins the company runs through an agent.
2. Read the answer:
   - `status` `ok` or `partial` means imported. Record `written`, `unchanged`, `partial_import`
     and `conversations.discarded_erased`. Check that `conversations.received` equals the number
     of conversations in the file; a mismatch means the arguments were altered on the way, so say
     so.
   - `status: "partial"` means a thread write failed (`threads[i].status`; `fenced` is retryable)
     or a gone conversation could not be retired yet. List it for a later rerun.
   - **`{'status': 'throttled', ...}`:** the tenant's daily brain write budget is spent. **Stop
     the batch immediately** and report which files were imported and which were not, so the next
     run resumes there. Never retry: every attempt is refused and counted against the tenant.
   - `retryable: true` (an erasure in flight, a store blip, an access check that could not run):
     wait a few seconds and retry that file **once**; then record it and continue.
   - `{'status': 'refused', 'errors': [...]}`, or a message starting "Brain pull request import
     refused": nothing was written or spent. Record the errors, skip that pull request's remaining
     parts, and continue with the next pull request. Do not edit a file by hand to make it pass.
   - "Access denied" without `retryable`: the person lacks the editor role on the project. Stop
     the batch.
   - `{'message': 'Brain pull request import failed'}` alone: a server fault. Wait a few seconds
     and retry that file **once**; then record it and continue.
   - `{'message': …, 'key': 'brain_write_quota_daily'}`: the tenant's budget is not configured.
     Stop and tell the person to ask their admin.
3. In the first answer, read `repository.repo_ref_scheme`. When it is not `code-ingestion`, warn
   once: the cards are stored, but a read of a file (`mcp__Fairmind__Brain_context`) will not find
   these conversations.
4. In the first answer, read `files`. **An answer with no `files` key comes from a server that does
   not store changed files yet**: say so once; the import still counts as done.

## Step 4: Report

One table row per chosen pull request: `#number`, title, outcome (`imported`, `partial`,
`refused: <first error>`, `not collected: <reason>`, `not sent: budget spent`), calls made out of
parts, `written`, `unchanged` (summed over parts), `discarded_erased`, and what stayed out
(`pr_turns_total - pr_turns_sent`, `skipped_too_large`). Then the totals and the budget units
spent.

Explain the readings:

- `written: 0` on a rerun means nothing changed.
- A rerun by a different person shows `written` above 0, because the importing person is part of
  each card.
- A pull request sent in several calls keeps a partial-import notice on its card, never retires a
  conversation that was deleted on GitHub, and shows `written` above 0 on a rerun. That is the
  price of not dropping any conversation.

The files under `.fairmind/brain-import/` hold the collected text. Say so, and that they can be
deleted.

## Removing a person's conversations — project admins only

- The conversations belong to the company, not to whoever wrote them: **a person does not erase
  their own comments**. A project **admin or owner** can, per project, with
  `mcp__Fairmind__Brain_erase_login`; for anyone else the platform answers "Access denied". This
  skill never offers it on its own initiative and never runs it as part of an import.
- Only when a person explicitly asks: show the login exactly as it will be sent and the project,
  say that it deletes **whole cards** — every pull request and review conversation card that
  login spoke in, in this project, including other people's turns on those cards — and that it is
  irreversible. Ask. Then call with `login: "<login>"`, `project: <project_id>`, `confirm: true`.
- Logins are spelled as the collector sends them: a bot with its `[bot]` suffix
  (`coverage[bot]`), a deleted account as `ghost` (every deleted account shares that one login).
  Case does not matter.
- Read the answer: `erased` (with `found` and `erased` node ids), `not_found` (still recorded:
  future imports skip the login), `partial` (run it again), `error` ("Access denied" means the
  person is not an admin of the project, so stop; another erasure of that login in flight or an
  erasure record that could not be written deleted nothing, so retry later). A message starting
  "Brain login erasure refused" means the request itself was malformed (no confirmation, a bad
  login, no project id): fix it and ask again.
- **Then re-import.** Erasure removes whole cards, so the other people's words on them are gone
  too until the same pull requests are imported again. Offer to run Steps 1–4 over the same
  selection. The re-import brings back everybody else's turns and never the erased login's; a pull
  request that login opened comes back with its title and the other people's turns, without its
  description and its author. Each call spends one budget unit
  again.

## Error Handling

**`{'status': 'throttled', ...}`:** the daily brain write budget is spent. **Stop** the batch,
report what was imported and what was not, and **never retry**.

**`retryable: true`:** wait a few seconds and retry that file once; a second refusal is recorded
and the batch continues.

**`refused`:** nothing was written or spent. Record the errors, skip that pull request's other
parts, continue with the next one.

**"Access denied" without `retryable`:** the person lacks the editor role on the project. Stop.

**`{'message': 'Brain pull request import failed'}`:** a server fault. Retry that file once, then
record it and continue.

**Collector exits:** 2 (usage, an open pull request without the flag, not found), 3 (`gh` failed),
4 (too large for one call) and 5 (the part files cannot be written) skip that pull request; 127 (`gh` missing) stops everything.
