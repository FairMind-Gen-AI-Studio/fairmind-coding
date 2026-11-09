---
name: brain-import-pull-requests
description: Use only when a person asks to bring a repository's pull requests into the company brain - it reads the merged and closed pull requests of the checkout's GitHub repository with the person's own gh credential, asks the brain whether each one changed, writes a short summary and a few rationale points for the ones that did, shows how many will be sent and who took part, and after a yes imports one reference card per pull request, so a later read of a file returns the reasons written about it. Also explains how a project admin removes one person from the brain. Also use when asked to "import the pull requests", "put our PR reviews in the brain", or "backfill the review history".
---

# Pull requests into the brain

## Overview

The reasons behind a change are written in the discussion of its pull request, and they sit in
GitHub where nothing else can read them. This skill brings each pull request into the company
brain as one **reference card**: the pull request's facts (title, state, branches, commits, the
files it changed, who took part), a short **summary** and up to five **rationale points** that
you write after reading the discussion, and the **version** of what people wrote in it. The
description and the comments themselves stay on this machine. Once a card is in, an answer to a `mcp__Fairmind__Brain_ask` question about a file cites the
pull request and the rationale points written about it.

**Cards are recorded confirmed, with the agent as their author.** An import asserts only that a
pull request happened and what it was about, so nothing waits for review - which is why the skill
asks before it sends, and why the removal path below exists.

**The brain decides whether to write.** Before anything is read or summarised, a free check call
carries the pull request's version. If the brain already holds that version, the pull request is
skipped: nothing is summarised, written or charged. Only a changed pull request costs a write,
one unit of the tenant's daily brain write budget. When the check also says that a card of the same
pull request, in this project or another, already holds a summary of exactly this version
(`summary_needed: false`), the pull request is not read or summarised either: the card is sent
without a summary and the brain fills it from that card.

**Announce at start:** "I'm using the brain-import-pull-requests skill to import this
repository's pull requests into the company brain as reference cards."

**What leaves your machine when this skill writes.** For each changed pull request,
`mcp__Fairmind__Brain_import_pull_request` sends a card: the title, state, number, URL and
permanent id, the head and base branch names and the head and merge commit ids, the
**repo-relative path of every file the pull request changed** (at most 300, with the real number
when there are more), the **GitHub login of everyone who took part**, bots included, the
repository's visibility, the version, and the **summary and rationale points you wrote**, which
are free prose. The description and the comments are not sent; the prose is written from them,
so it says whatever you chose to say. A pull request whose summary the brain already holds is sent
with no summary and no rationale points, and the brain copies both from the card that holds them.
The tool refuses a summary or a point that repeats twelve
or more words in a row from them, or a whole short paragraph or comment of them, and one that looks like a credential. With the card go the
repository's catalog id, the project id, the checkout's remote URL and the agent name. It is read
from GitHub with the person's own `gh` credential, and nothing is posted to GitHub. This is the
**brain write-back**, and the plugin README's *What leaves your machine* describes it in full.

## When to Use

Use this skill when a person asks for it:

- Backfill the history of a repository the brain has already ingested.
- Bring newly merged pull requests in. A rerun is cheap: an unchanged pull request is skipped by
  its check call.

Do **not** use it to:

- Import open pull requests unless the person asks for them.
- Post anything to GitHub. This skill only reads.
- Erase someone's words because they asked for it themselves. A person does not remove their own
  words; see the last section.

## Step 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. This skill needs the `fairmind`
   field set to `"configured"`, the `project_id` and the `repository_id` that `/fairmind-connect`
   writes. **Never write those keys yourself.** If the file is missing or a key is absent:
   **stop** and tell the user to run `/fairmind-connect` once for this checkout. A bound
   repository is what lets a read of a file find the cards.
2. Confirm the `mcp__Fairmind__Brain_*` tools are in the tool list. They arrive with the
   platform's **Brain-1** release. If they are absent, say which release is needed and stop.
3. Confirm `mcp__Fairmind__Brain_import_pull_request` is in the tool list by name. It arrives with
   a later platform release than the other `mcp__Fairmind__Brain_*` tools. If it is absent, say so
   and **stop** - never record a pull request through another door.
4. `gh auth status` must succeed, and `git remote get-url origin` must be a GitHub URL. Otherwise
   say which of the two failed and stop.
5. Get the remote the door may receive:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py remote
   ```

   It prints `repository` and `git_remote`, the origin URL with any `user:token@` removed. Keep
   `git_remote` for Step 3 and never send the raw `git remote get-url origin`.

## Step 1: Choose the pull requests

1. Run, from the repository root:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py list --limit 50
   ```

   It lists merged and closed pull requests, newest first. Open ones only when the person asks
   (`--states OPEN`, and `--allow-open` on collect). `--since YYYY-MM-DD` narrows by date.
2. Ask which: all listed, the last N, a date range, or specific numbers. When `/fairmind-connect`
   started this skill after the person accepted its offer of the last 50, that is the answer:
   take every listed pull request and do not ask again.

## Step 2: Collect, then show what is about to leave and ask

1. For every chosen number, run (GitHub reads only; nothing is sent anywhere yet):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py collect --number <n> --out-dir .fairmind/brain-import
   ```

   It writes `card.json` (the facts, no text) and `read.md` (the description and the discussion,
   for you to read; it is never sent) under `.fairmind/brain-import/<n>/`, and prints a report:
   `card`, `read`, `source_version`, `source_visibility`, `changed_files`, `changed_files_sent`,
   `human_texts` and `logins`. On exit 2, 3, 4 or 5, record the pull request as not collected,
   with the script's message, and continue. On exit 127 or a `gh` authentication failure, stop:
   every later one fails the same way.
2. **Before the first call, show what is about to leave and ask.** Show:
   - the number of pull requests, noting that each one the brain does not already hold spends one
     unit of the tenant's daily brain write budget and that the check calls spend none;
   - that each card carries the path of every file the pull request changed, up to 300 files, and
     the real number when a list was cut;
   - that the GitHub login of everyone who took part travels: list the combined `logins`, bots
     included;
   - that a summary and up to five rationale points, written by you from the description and the
     discussion, travel, and that the description and the comments do not (a pull request whose
     summary the brain already holds travels without them: the brain copies them);
   - that the cards land confirmed and are readable by everyone on the project, and the project
     they go to.

   One yes covers the batch. No yes: nothing is sent. What is sent is built from exactly the
   files collected - never collect again between the yes and the send.

## Step 3: Check, write the words, send

For each pull request, in order, with `<dir>` = `.fairmind/brain-import/<n>`:

1. **Check.** Call `mcp__Fairmind__Brain_import_pull_request` with exactly these arguments and no
   others, the three values of `pull_request` read from `<dir>/card.json`:

   ```
   repository:   <repository_id from active-context>
   git_remote:   <git_remote from Step 0>
   project:      <project_id from active-context>
   agent:        "brain-import-pull-requests"
   pull_request: {"source": "github", "id": <the card's id>, "source_version": <the card's source_version>}
   ```

   A check has no `summary`. The brain answers `{"status": "ok", "updated": false,
   "source_changed", "exists", "summary_needed"?, "erased_participants"?, "erased_participants_truncated"?}` and
   writes and charges nothing. `erased_participants` lists every login erased in the project on
   this platform, not only this pull request's. An answer
   that refuses the call as malformed, or a message saying it does not know a `pull_request` with
   no summary, comes from a server older than the card release: **stop** and say which release is
   needed. `summary_needed` appears only on a changed pull request. Only `summary_needed: false`,
   explicitly false, means a card of this pull request already holds a summary of exactly this
   `source_version` that this project may take. An answer without the key (an older server) or
   with `true` means the pull request is read and summarised, as below.
2. **Unchanged.** `source_changed: false`: the pull request is done, report it as `unchanged`,
   and go to the next one. Nothing is read or summarised.
3. **Changed.** When the answer carries `erased_participants_truncated: true`, the list of
   erased people is cut short and some of them are unknown to you: **do not summarise** this pull
   request, record it as failed with the reason "the brain's list of erased people is cut short",
   and go to the next one. Otherwise run this check for every changed pull request, whether or
   not the answer carries `erased_participants` (the server leaves the key out when nobody is
   erased, and the check is also what compares a staged card's project, repository and remote
   with the current ones). It finds which of the listed logins took part in this pull request:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/reference_card.py erased --card <dir>/card.json --staged <dir>/write.json --project <project_id> --repository <repository_id> --git-remote <git_remote> [--erased <login> ...]
   ```

   (one `--erased` per login of `erased_participants`, none when the answer carries none; leave
   `--staged` out when there is no `<dir>/write.json`). It prints
   `{"affects": [...], "stale": true|false}`, compared without regard to case. `affects` is the
   erased logins that took part and that `read.md` has not been redacted for yet; `stale` is
   whether the staged card was made before `read.md` was redacted for all of them, or was staged
   for another project, repository or remote than the ones you passed. **A non-zero exit means the
   erased people are unknown: send nothing for this pull request, record it as failed with the
   script's message, and go to the next one** - never read a failure as an empty `affects`.
   Only these logins matter
   below; a pull request none of them took part in is handled as if the answer named nobody.
   If `<dir>/write.json` exists and its `arguments.pull_request.source_version` is
   the `source_version` of `<dir>/card.json`, and `affects` is empty and `stale` is false,
   a card was already staged for this version of the pull request: go straight to the send below
   and do not write the words again, unless the staged card has no `summary` (a copy, below) and
   this check did not answer `summary_needed: false`: that copy cannot be sent, so write the words
   and stage with `--restage`. A file for another version is stale and is never sent. A
   staged file is stale as well when `affects` is not empty or `stale` is true, since it was
   written from words that may include theirs or for another project, repository or remote:
   redact (a login already redacted is not redacted again), write the words again and stage
   with `--restage`.
   Otherwise, unless the copy form below applies:
   1. When `affects` is not empty, remove what those people wrote from the
      reading file **before you read it**:

      ```bash
      python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py redact --read <dir>/read.md --erased <login> [--erased <login> ...]
      ```

      (the logins of `affects`; the match ignores case). The redaction is kept on record beside
      `read.md`, which is how the next `erased` knows they are done.

   2. Read `<dir>/read.md` and write `<dir>/words.json`:

      ```
      {"summary": "<1 to 1000 characters>",
       "rationale_points": [{"text": "<1 to 300 characters>", "file_path": "<optional>"}]}
      ```

      The summary says what the change does and why, and how the discussion ended. A rationale
      point is one reason, objection or agreement a later reader of the code would want to
      know; at most five. Give a point a `file_path` - exactly as the changed-files line of
      `read.md` spells it, or as the heading of a review thread names it without its line number -
      when it is about that file; any other path, one with a directory added or dropped included,
      is refused. Write in your own words: do not quote,
      refer to people by role ("a reviewer", "the author") rather than by login or name, and
      leave out secrets, tokens and personal data. Leave `implements` and `resolves` out unless
      the person named the requirement or issue they refer to. Add nothing else to the file.
   3. Stage the card:

      ```bash
      python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/reference_card.py stage --card <dir>/card.json --input <dir>/words.json --out <dir>/write.json --agent brain-import-pull-requests --project <project_id> --repository <repository_id> --git-remote <git_remote> [--restage]
      ```

      Add `--restage` when `<dir>/write.json` already holds a card for this very version and binding
      that is stale because `affects` named erased people: without it the stager exits 4. A card
      staged under another project, repository or remote (a rebind) is replaced without the flag. It joins your words to the facts, so you never re-type a field; `--repository` is the
      catalog id the door resolves, and the card keeps its own `owner/name`. Exit 2 lists every problem
      (a text over its limit, a credential-like string, a point naming a file `read.md` does not
      show, words copied from `read.md` - twelve in a row, or a whole short paragraph of it); fix `words.json` and run
      it again; the same exit also says when `<dir>/read.md` is missing, which no change to
      `words.json` clears: record the pull request as failed (not collected) and go to the next one, never collect it again between the yes and the send; nothing was written, so no flag is needed (`--restage` replaces a card that was
      already staged and is stale, as after an erasure or a rebind). Its output carries `expected_sha256` and `card_bytes`; a
      `warning` says the card is over the door's size limit and will be refused.
   **Copy form.** When the answer said `summary_needed: false` and `affects` is empty, do not read
   `read.md` and do not write words. Stage the card without them, in place of the numbered steps above:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/reference_card.py stage --card <dir>/card.json --copy --out <dir>/write.json --agent brain-import-pull-requests --project <project_id> --repository <repository_id> --git-remote <git_remote>
   ```

   Its output carries `"copy": true`. When `affects` is not empty the copy form is not used: the
   erased person's words may sit in the summary the brain would copy, so read, redact and write the
   words as above.
4. **Ask again, then send.** An erasure can land while the words were being written. Unless the
   check of step 1 was answered with nothing summarised or staged since (a staged card sent
   straight from it) and this is the first send of the card, repeat the check of step 1 and read it as step 3 does, `reference_card.py
   erased --staged <dir>/write.json` included, with the same `--project`, `--repository` and `--git-remote`. A failed call, a truncated list or a failed
   `erased` means: send nothing, record the pull request as failed. When `affects` is not empty
   or `stale` is true, redact, write the words again, stage with `--restage` and ask again, at
   most twice more; then record it as failed. Only an answer with `affects` empty and `stale`
   false permits the send. Every send of a card goes through this step, a retry after a failure
   included. Read the repeated answer's `summary_needed` too: a staged copy is sent only while it is
   `false`; otherwise write the words and stage with `--restage`.

   Read `<dir>/write.json` and call `mcp__Fairmind__Brain_import_pull_request` with its
   `arguments` exactly as they are, and `expected_sha256` from the file beside them. Never
   shorten, reorder or re-spell anything in it: spelling is identity, and an admin erases by it.
   Send `agent_accounts` only when the person names the logins the company runs through an agent
   - `collect` takes them as `--agent-account` and the card file then carries them.
5. Read the answer:
   Read `erased_participants` first, before anything is recorded; the bullet that names it takes precedence over every status below.
   - An answer whose `erased_participants` holds a login that the last check did not list: an
     erasure landed after it, and the card was written from words that may include that person's.
     Record the pull request as failed, never `imported`, and tell the person to ask
     someone with the editor role on the project to remove that card by hand (a platform action; this plugin has no door that removes a card): an erasure by login
     can no longer find it (it no longer names that person) and a later check will call it
     unchanged. This is the window that remains after the check was repeated just before the
     send; it cannot be closed from here. A deleted account (`ghost`) is never listed in that
     answer, so its words in a card written inside that window cannot be detected afterwards.
   - `status` `ok`: imported. Record `written` and `unchanged`. Check that `received_sha256`
     equals `expected_sha256`; if it does not, the arguments were altered on the way: send the
     file once more (after repeating step 4), and if it differs again, record the pull request as failed and say so.
   - `status` `partial`: the card was written, but review-thread cards of the earlier contract are
     still waiting (`legacy_pending`, beside `erased_legacy`, the ones erased by this call). Record
     the pull request as `imported` with that note; importing it again retries the erasure and
     spends one unit.
   - **`{'status': 'throttled', ...}`:** the tenant's daily brain write budget is spent. **Stop
     the batch immediately** and report which pull requests were imported and which were not, so
     the next run resumes there. Never retry: every attempt is refused and counted against the
     tenant.
   - `retryable: true` (an erasure in flight, a store blip, an access check that could not run):
     wait a few seconds, go back to the check of step 1 and follow it from the top - never send the
     same staged file again unchecked, since an erasure in flight is the usual cause - then write
     once more; a second `retryable` is recorded and the batch continues.
   - `{'status': 'refused', 'code': 'summary_required', ...}` to a card staged as a copy: no card
     holds the summary any more. Nothing was written or charged and it is not a refusal to record.
     Read `read.md` (redact first when `affects` asks for it), write the words, stage with
     `--restage`, repeat step 4 and send the full card.
   - `{'status': 'refused', 'errors': [...]}`, or a message starting "Brain pull request import
     refused": nothing was written or spent. Record the errors and continue with the next pull
     request. Do not edit a file by hand to make it pass. A code of `payload_digest_mismatch`
     means the call was altered on the way: repeat step 4, then send the file once more.
   - "Access denied" without `retryable`: the person lacks the editor role on the project. Stop
     the batch.
   - `{'message': 'Brain pull request import failed'}` alone: a server fault. Wait a few seconds
     and retry **once** after repeating step 4; then record it and continue.
   - `{'message': …, 'key': 'brain_write_quota_daily'}`: the tenant's budget is not configured.
     Stop and tell the person to ask their admin.
6. In the first answer, read `repository.repo_ref_scheme`. When it is not `code-ingestion`, warn
   once: the cards are stored, but an answer to a `mcp__Fairmind__Brain_ask` question about a file
   will not cite these pull requests.

## Step 4: Report

One table row per chosen pull request: `#number`, title, outcome (`imported`, `unchanged`,
`refused: <first error>`, `failed`, `not collected: <reason>`, `not sent: budget spent`),
`written`, and the card's size. Then the totals and the budget units spent.

Explain the readings:

- `unchanged` means the brain already holds this version of the pull request: nothing was read,
  summarised or charged.
- A pull request imported with no summary of yours (`summary_needed: false`) took its summary and
  rationale points from a card the brain already held; say so in the table. A write answer may carry
  `copies: {updated, skipped?}`, the cards of other projects the brain brought up to date from a
  summary this run wrote; mention it, there is nothing to do.
  A check also answers `source_changed: true` for a version the card holds when the card still
  lists verbatim cards it could not erase; the write that follows retries the erasure.
- A pull request first imported in an earlier shape is rewritten in place, and the cards of its
  review conversations are removed by the same import: the old text leaves the brain at that
  moment.
- A rerun by a different person does not rewrite a card: the version is what people wrote, not
  who imported it.

The files under `.fairmind/brain-import/` hold the collected text (`read.md`). Say so, and that
they can be deleted.

## Removing a person from the brain - project admins only

- The cards belong to the company, not to whoever took part: **a person does not erase
  themselves**. A project **admin or owner** can, per project, with
  `mcp__Fairmind__Brain_erase_login`; for anyone else the platform answers "Access denied". This
  skill never offers it on its own initiative and never runs it as part of an import.
- Only when a person explicitly asks: show the login exactly as it will be sent and the project,
  say that it deletes **whole cards** - every pull request card that login took part in, in this
  project - and that it is irreversible. Ask. Then call with `login: "<login>"`,
  `project: <project_id>`, `confirm: true`.
- Logins are spelled as the collector sends them: a bot with its `[bot]` suffix
  (`coverage[bot]`), a deleted account as `ghost` (every deleted account shares that one login).
  Case does not matter.
- Read the answer: `erased` (with `found` and `erased` node ids), `not_found` (still recorded:
  future imports skip the login), `partial` (run it again), `error` ("Access denied" means the
  person is not an admin of the project, so stop; another erasure of that login in flight or an
  erasure record that could not be written deleted nothing, so retry later). A message starting
  "Brain login erasure refused" means the request itself was malformed (no confirmation, a bad
  login, no project id): fix it and ask again.
- **Then re-import.** Erasure removes whole cards. Offer to run Steps 1-4 over the same
  selection. The check answer then names the erased login in `erased_participants`: remove what
  they wrote from the reading file before you read it (Step 3, after `reference_card.py erased`), so a new summary does not put
  their words back. A pull request that login took part in comes back without that login among its
  participants. Each write spends one budget unit again.

## Error Handling

**`{'status': 'throttled', ...}`:** the daily brain write budget is spent. **Stop** the batch,
report what was imported and what was not, and **never retry**.

**`retryable: true`:** wait a few seconds, go back to the check (Step 3, point 1), follow Step 3 from
the top and write once more; a second refusal is recorded and the batch continues.

**`refused`:** nothing was written or spent. Record the errors and continue with the next one.

**"Access denied" without `retryable`:** the person lacks the editor role on the project. Stop.

**`{'message': 'Brain pull request import failed'}`:** a server fault. Retry once after repeating step 3 point 4 (the check), then record it
and continue.

**Collector exits:** 2 (usage, an open pull request without the flag, not found), 3 (`gh`
failed), 4 (cannot become a card: an identifier over the door's cap, more than 100 participants)
and 5 (the card files cannot be written) skip that pull request; 127 (`gh` missing) stops
everything.

**Stager exits:** 2 (a card the door would refuse: fix `words.json`), 3 (an input is not
readable JSON), 4 (already staged: when an erased person took part, stage again with `--restage`; otherwise send it), 5 (the output
cannot be written).
