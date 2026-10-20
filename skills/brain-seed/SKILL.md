---
name: brain-seed
description: Use only when a person asks to seed or backfill a project's company brain - it brings in the documents they name (as links with a summary the agent writes), the tasks of one tracker project updated in the last 90 days, and the merged and closed pull requests of the same window, read through the connections already in the session. It lists first (titles, links, ids and each task's own text; no document bodies, no comments), shows the plan, the budget units and what leaves the machine, asks for one yes before it collects anything or writes to the brain, then sends in a fixed order (documents, tasks, pull requests), stops cleanly when the daily budget runs out, and resumes later without sending twice. Also use when asked to "seed the brain", "fill the brain with our docs and tickets", or "backfill the brain".
---

# Seed a project's brain

## Overview

A new project's brain is empty, so the first question an agent asks it comes back with nothing.
This skill fills it from what the team already has: the documents a person names, the tasks of one
tracker project **updated in the last N days**, and the pull requests of the last N days (90 by default).
A task last touched before the window is not sent, however many the tracker holds. Sub-agents on a small
model list and summarise; scripts do the mechanical work and never call the brain; **this
session sends every payload itself**, through three doors, in this order: documents, then tasks,
then pull requests.

**Results.** Tasks and pull requests are recorded **confirmed**, with the agent as their author.
Documents land as **proposals** that wait in the Review Queue until a person confirms them.

**Units.** Each call spends one unit, taken from an open setup window if there is one and from
the tenant's daily brain write budget otherwise. A pull request that needs several calls spends
one unit per call.

**What leaves your machine when this skill writes.** Tasks and pull requests travel whole: the
description, every comment as written, and the account id or login of everyone who wrote any of
it. A document travels as its link, its title and a summary written here, except a local file
with no address, whose whole body travels. The project id and the checkout's remote URL go with
them; the remote URL is sent without any credential in front of the host. This is the **brain write-back**, and the plugin README's *What leaves your machine*
describes it in full.

**Announce at start:** "I'll fill this project's FairMind brain from your documents, tasks and
pull requests. A few quick questions first; nothing is sent until you approve."

## When to Use

Use this skill only when a person asks for it.

Do **not** use it to:

- Import open pull requests, unless the person asks for them.
- File a document for the whole company. Everything this skill sends is for one project.
- Run on a checkout that is not connected to a Fairmind project (Phase 0 stops).
- Seed several repositories at once. One run covers the checkout's own repository (its origin).

It must run in the main session: the sub-agents it starts cannot start sub-agents of their own.

## What the person sees

The person has never seen this skill and is not reading it. The rest of this file is written for
you and keeps its internal names; what reaches the person is fixed here. By default they see only:

- the opener above;
- two screens, each one `AskUserQuestion` call: **screen 1** in Phase 1 (what to learn from) and
  **screen 2** in Phase 3 (the plan and the yes);
- one fixed line at the start of Phases 2 and 4;
- the `text` that `seed_ledger.py summary` prints, shown **exactly as it comes**, never reworded,
  shortened or extended: the estimate inside screen 2 (Phase 3), what is about to be sent at the
  start of Phase 5, and the end of the run in Phase 6. In an English conversation show it
  verbatim. In any other language translate it line by line, keeping every number and title and
  adding and dropping nothing.

**Screens.** There are two main screens. Besides them there is only: one follow-up when an answer
leaves the scope open (the tracker project, the document space or folder, the numbered pick after
`Let me choose`); the re-ask of Phase 4 step 5; and screen 2 asked again when the answer was not
`Proceed`.

Say nothing between tool calls. Speak mid-run only when something needs the person.

**Never shown by default:** a key, a digest or `sha256`, the ledger, a part, a payload, a
fingerprint, an outcome name, an error code, a tool name, an id, a `<work>` path. Anything technical
goes to the report file (`.fairmind/brain-seed/report-<run>.json`). When the person asks for
details, read that file and explain each item in one or two plain sentences: what happened,
whether anything was stored, what happens next, what the person can do. Show a raw field only when
they ask for it by name.

**Every `AskUserQuestion` explains itself.** The question text says plainly what is being asked
and why, and every option says what choosing it does, for someone who has never seen this skill.
Ask in the person's language (the drafts below stay in English). Follow the idiom of
`/fairmind-coding`: the tool adds its own "Other" free-text entry, so never invent one; mark the
choice you recommend with the `(Recommended)` suffix; headers are 12 characters or fewer; 2 to 4
options; every option has a description. Leave out a question whose source is skipped.

## Phase 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. It needs `fairmind` set to
   `"configured"`, plus `project_id`, `repository_id` and `base_path`. **Never write those keys
   yourself.** If the file is missing or a key is absent, **stop** and offer `/fairmind-connect`.
2. The tools are named exactly `mcp__Fairmind__<Name>`. Check each door by name in the tool list:
   - `mcp__Fairmind__Brain_add_document`: absent, the documents are skipped.
   - `mcp__Fairmind__Brain_import_tracker_item`: absent, the **task phase is skipped**. It arrives with a later platform release than the other brain tools. Never
     record a task through another door.
   - `mcp__Fairmind__Brain_import_pull_request`: absent, the pull requests are skipped.
   - All three absent: **stop**.
3. Read the parameters of `mcp__Fairmind__Brain_add_document`. The `source` parameter is checked:
   without it, documents are sent without `source`, and each card records its link's system as
   `web`; pass `--note no_source` to the `done` summary.
   Read the parameters of `mcp__Fairmind__Brain_import_tracker_item` and
   `mcp__Fairmind__Brain_import_pull_request` too. Pass `expected_sha256` only when the door lists
   it (Phase 5): the schema accepts no argument it does not list, so an older server fails the
   call before it runs. Without it, behave as before and verify after the answer, and pass
   `--note no_expected_sha256` to the `done` summary.
4. For the pull request phase only: `gh auth status` succeeds, and
   `python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py remote` exits 0 (the origin is a GitHub
   URL). It prints `git_remote`, the origin URL with any `user:token@` removed: keep it for Phase 5
   and never send the raw `git remote get-url origin`. Otherwise say which failed and skip that phase.
5. Set `<work>` to `<base_path>/brain-seed/run/`. Sub-agents write there, and the path guard admits
   writes only under `base_path`. The ledger stays at `.fairmind/brain-seed/ledger.json`, outside
   `<work>`.
6. Run every command from the repository root: the ledger's default path and the part paths in
   `collected.json` are relative to it.

**What to say.** When everything above is in place, say nothing. A missing check gets one plain
line, in the person's language, that says what to do:

- not connected: "This folder isn't connected to a FairMind project yet. Run /fairmind-connect,
  then ask again." (stop)
- all three doors absent: "Your FairMind server doesn't offer brain imports yet. Ask your FairMind
  admin for the release that includes them." (stop)
- task door absent: "Tasks will be skipped: your FairMind server doesn't accept them yet."
- `gh` failing: "Pull requests will be skipped: the GitHub command line isn't signed in. Run gh
  auth login and ask again to include them."
- origin not on GitHub: "Pull requests will be skipped: this repository isn't on GitHub."

An absent `source` or `expected_sha256` is silent.

## Phase 1: Sources

1. Detect which connections the session has, by server family and without naming tools: Notion,
   Confluence and Jira (Atlassian), Google Drive, ClickUp, Linear, Azure DevOps.
2. Ask **screen 1**, one `AskUserQuestion` call with the four questions below. Build the options
   from the connections found in step 1. The window applies to tasks (by their last update) and to pull requests.

   - **Q1** — header `Documents`, single select:
     - Question: "Which documents should the brain learn from? A source is a place where your team keeps written knowledge: manuals, architecture pages, specifications. I'll only list titles and links there; no document is opened or sent until you approve the plan. To give exact pages, choose Other and paste their links, one per line."
     - `None` — "Skip documents. The brain learns only from your tasks and code changes."
     - `<Connection> <items>` (one option for each document connection found, at most 2; `<items>` is what that connection holds: `pages` for Notion and Confluence, `files` for Google Drive) — "I'll list the <items> in the space or folder of <connection> that you name next. You'll pick which ones to send on the next screen."
     - `Files in this repo` — "Documents kept as files in this repository, such as a docs folder. Name the folder next."
   - **Q2** — header `Tasks`, single select:
     - Question: "Which task tracker should the brain learn from? A tracker is where your team files tickets. I'll bring in the tasks of one project there: each task's description, its comments, and the tracker account ids of the people who wrote them (never their names)."
     - `None` — "Skip tasks."
     - `<Tracker>` (ClickUp, Linear, Jira or Azure DevOps; at most 3 found) — "Use your <tracker> connection. Next I'll look up the names of its projects, names only, so you can pick one."
   - **Q3** — header `Window`, single select:
     - Question: "How far back should I go? Only tasks updated, and pull requests closed, within this window are brought in; older ones are left out. A longer window gives the brain more history but costs more of your daily allowance."
     - `Last 90 days (Recommended)` — "About a quarter of history. A good start for most teams."
     - `Last 30 days` — "Only recent work. Faster and cheaper."
     - Any other number of days comes through the tool's own Other entry.
   - **Q4** — header `Code changes`, single select:
     - Question: "Should the brain also learn from this repository's pull requests, the code changes your team merged or closed in the window? Each one is sent with its description, review comments, and the list of files it changed."
     - `Yes (Recommended)` — "Include merged and closed pull requests."
     - `No` — "Skip pull requests."

   **Follow-up.** At most one more question, and only when an answer leaves the scope open. For
   the tracker project, read the names of the projects or lists from the tracker's hierarchy
   (names only, nothing else) and ask one `AskUserQuestion` with up to 3 of them as options, each
   described as "Bring in the tasks of this project", plus the tool's own Other entry. A space
   name or a folder has no closed form: ask it as one plain sentence.
3. **Nothing outside the named spaces is read.** A document that a task cites outside them is
   listed in Phase 3 and never opened unless the person ticks it.
4. `agent_accounts`: ask for them only if the person says the company runs work through an agent
   account. Otherwise leave the field out.

## Phase 2: Inventory, reads only

This phase reads before the yes, and it says so when it starts: for documents their titles, links,
ids and edit markers, for tasks their fields and **their own text** (to find the documents they
cite). **Document bodies and comments are not read until the yes.** The person sees this one
line, in their language: "Listing what's in those sources: titles and links, each task's own
description, and the names of tracker projects. No document is opened, no comment is read and
nothing is sent yet."

Two inventory sub-agents (`model: "haiku"`, general-purpose, briefs in
`references/subagent-briefs.md`) write `<work>/inventory-documents.json` and
`<work>/inventory-tasks.json`. They list titles, links, ids, edit markers and cited links; `cited_links` comes from a task's
description.
**They do not read document bodies.** Per-source notes: `references/document-sources.md`; per
tracker: `references/tracker-jira.md`, `references/tracker-clickup.md`,
`references/tracker-linear.md`, `references/tracker-azure-devops.md`.

Then run the following. Each script creates the directory it writes into; leave out `--documents`
or `--tasks` when that phase is skipped.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/link_normalize.py --in <work>/inventory-documents.json --out <work>/documents.json
```

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py list --days <N> --out <work>/pulls.json
```

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py plan --project <project_id> --work <work> --documents <work>/documents.json --tasks <work>/inventory-tasks.json --pull-list <work>/pulls.json --out <work>/estimate.json
```

A row in `documents.json` with `needs_id` set has a short or display link whose page id is not
known. Ask its sub-agent for the id, or put the row in the refused list: a link spelled two ways
makes two cards.

## Phase 3: Plan, and one yes

**Propose** the documents: the stable ones (manuals, architecture, specifications) and every
document a task cites inside the named spaces. Leave out minutes and drafts. Cited documents
outside the named spaces are listed unticked and are never part of "All proposed": only the
person's explicit pick adds one. Print the proposed documents as a short numbered list of titles.

The estimate is computed with every proposed document ticked, so the yes covers the largest set
and choosing fewer only lowers the cost. When the person chooses fewer on screen 2, write the
ticked file again with only those rows and run the two commands below again before Phase 4.

Write **only the ticked rows** to `<work>/ticked-documents.json`: the ticked rows of the inventory
as they were, a ticked document that a task cites (`cited_links` in `inventory-tasks.json`) as a row
`{url, title}`, and a file with no address (a local PDF) as a row `{local_path, title}` with a path
inside the repository (the script gives it the link `local:<path>`, `source: "local"` and
`local: true`, and refuses an absolute, home, drive or `..` path, because the link is stored: ask the
person for the repository path instead). Then rebuild
`documents.json` from that file, and from no other:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/link_normalize.py --in <work>/ticked-documents.json --out <work>/documents.json
```

Every link is spelled by the script and a page listed twice is one card; never write a link into
`documents.json` by hand. `link_normalize.py` is **not** run again over
`inventory-documents.json`: that file lists every document found, and the output would send the
unticked ones. A document that is not in `ticked-documents.json` is not in `documents.json`, so no
later plan can send it.

Then run the `plan` command of Phase 2 again, so `estimate.json` holds exactly the ticked
documents: the counts below and the summarisers of Phase 4 read that file. Then print the
estimate and keep its `text` for screen 2:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py summary --plan <work>/estimate.json --stage estimate
```

**What screen 2 must convey:**

- The counts, as the estimate text carries them: to send, already in the brain, refused earlier.
  The tasks to send are **at most** N ("up to N"): the estimate cannot see the pull request links
  yet, so on a re-run it over-counts tasks. Add no count of your own.
- The budget units: documents to send, plus tasks to send, plus at least one per pull request in
  the window (`summary.pull_request.units_at_least`). The daily allowance cannot be read before a
  call. When the total is large, say the two ways through: a FairMind operator opens a setup window
  for the tenant, or the run proceeds over several days and resumes where it stopped. **This skill
  never opens a setup window.**
- What reading the tasks costs on the tracker's own connection: about two calls per task to send
  (the task and its comments), plus the listing. A hosted tracker connector can have a daily call
  allowance, so a large task phase may be better split across days.
- That the yes covers this estimate: if Phase 4 ends with more units than shown, it asks again.
  It also covers the re-sends of Phase 5: a payload the brain received different from its file is
  sent once more, which can spend one more unit each (the altered version may already have been
  written). They cannot be counted in advance; Phase 6 reports them.
- What leaves, as in the Overview: for tasks and pull requests the whole text and the account ids
  or logins of everyone who wrote it (the tracker's account ids and GitHub logins, never display
  names), the commit ids, the changed files with their line counts, and this repository's remote
  URL without credentials; for documents only link, title and summary, except the whole body of a
  local file with no address.
- The project the cards go to, and that the cards land readable by everyone on the project.

**Screen 2**, one `AskUserQuestion` call. The `text` from `summary --stage estimate` goes in the
question as `<estimate text>`; use the project's name, never its id.

- **Q1** — header `Documents`, only when documents were proposed:
  - Question: "I found N documents worth keeping, listed above. Which should go to the brain? Each one becomes a proposal that waits for someone on the project to confirm it in Studio."
  - With exactly 1 document, two options:
  - `Include` — "Send this document to the brain."
  - `Skip` — "Send no documents this time."
  - With 2 to 4 documents, a multi-select with one option for each: `<document title>` — "<one plain sentence on what the document covers>"
  - With more than 4, these three options:
  - `All N proposed (Recommended)` — "Send every document listed above."
  - `None` — "Send no documents this time."
  - `Let me choose` — "I'll show a numbered list; reply with the numbers to keep."
- **Q2** — header `Plan`:
  - Question: "<estimate text> Send this to the FairMind brain of <project name>? A unit is one write, taken from your workspace's daily allowance. If the allowance runs out, the run pauses and continues from the same point next time.[ Only when the total is large: For a run this size your FairMind contact can open a setup window instead.] A send that arrives altered is sent once more, which can cost one more unit each, and this yes covers that. If the final count comes out higher than shown, I'll ask again. Reading the tasks also uses about two calls per task on your tracker connection, which may have its own daily limit. What leaves this computer: each task's and pull request's full text and comments, the tracker account ids and GitHub logins of the people who wrote them (never display names), commit ids, the files each pull request changed with their line counts, and this repository's remote address without credentials; for documents, only the link, the title and a short summary written here (a local file goes whole). Nothing has left this computer yet."
  - `Proceed` — "Collect the full text and send it now. Everyone on the project can read what is stored."
  - `Change` — "Go back and choose different sources or a different window. Nothing is sent."
  - `Cancel` — "Stop here. Nothing is collected or sent; the list I made stays on this computer only."

**Only `Proceed` on this screen is the yes.** A yes typed earlier, or in Other text, is not a yes:
ask again. The screens allowed besides these two are listed in What the person sees.

**One yes covers the plan. It comes before any document body or comment is collected and before
any write to the brain; with no yes, nothing more is collected and nothing is sent.** What the
inventory read in Phase 2 is the only thing read without it.

## Phase 4: Collect

The person sees one line: "Collecting the full text you approved…"

1. **Documents.** Summariser sub-agents (`model: "haiku"`) take batches of about 10 to-send rows
   (`new`, `changed` or `retry`) from `estimate.json`, which Phase 3 rebuilt from the ticked
   documents. Each writes the row's `file` as `{link, source, title, doc_kind, summary}`.
   - `doc_kind` is one of `spec|design|blueprint|manual|wiki|note|adr_export`, never `brief`.
   - A row with `local: true` also gets `content`, the whole text of the file, never shortened.
   - The summary is two or three sentences on what the document is evidence for. As a
     precaution, it describes a relative path or a local address in words rather than writing
     it out: the hosting edge in front of the platform refuses some text patterns, and its rules
     can change.
2. **Pull requests.** The collector works for about seven minutes per call: set the Bash tool's
   timeout to its maximum. Run the same command again until its summary says `"complete": true`.
   `--resume` continues an unfinished collect of the same repository and window (a `--days` window
   that began the day before keeps its first day); the first call of a new run starts over, so a
   pull request changed since the last run is collected again. A `gh` failure for one pull request,
   a 403 on it included, is retried on later calls (three attempts in all) and then listed in
   `not_collected`; an expired login or a rate limit ends the call with exit 3, and `gh` gone with exit 127 (either costs the pull request in
   flight no attempt) and the phase stops:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py collect --days <N> --out-dir <work>/pull-requests --resume
   ```

3. **Match, then plan.** Pull requests that name a ticket's key become links on that task:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_links.py match --tasks <work>/inventory-tasks.json --collected <work>/pull-requests/collected.json --out <work>/tasks.json
   ```

   Exit 3 with an `errors` list in its summary still wrote `tasks.json`: a pull request whose
   description file could not be read matched on its title and branch only. Say it in one plain
   sentence. A pull request the collector could not read reaches the person through `--collected`
   in Phase 6.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py plan --project <project_id> --work <work> --documents <work>/documents.json --tasks <work>/tasks.json --collected <work>/pull-requests/collected.json
   ```

4. **Tasks.** Shaper sub-agents (`model: "haiku"`, batches of about 20 to-send task rows) read each
   ticket and its comments through the tracker's own connection. Each writes the row's `file` as the
   door's `item`, following `references/tracker-<tracker>.md` and the brief in
   `references/subagent-briefs.md`; give the sub-agent the absolute path of this skill's directory,
   since its working directory is the repository root. Then add the matched links:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_links.py merge --tasks <work>/tasks.json --dir <work>/tasks
   ```

   Exit 3 lists in `errors` the shaped files it could not read or left as they were (a
   `pull_request_urls` that is not a list); the others are merged. Say it in one plain sentence.

5. Read `plan.json`'s `summary` again. If the final units exceed the estimate the yes covered, show
   the new number and ask again with one `AskUserQuestion`, `Proceed` or `Cancel`, each described
   as on screen 2; otherwise go on. **Never collect or reshape between that point and
   the send.**

## Phase 5: Send

Order: documents, then tasks, then pull requests. A pull request's own links reach a task only
once the task is in.

Start by printing the `send` summary's `text`. A stop reaches the person only through the `done`
summary of Phase 6.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py summary --plan <work>/plan.json --stage send
```

For each kind, in a loop:

1. `python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py next --plan <work>/plan.json --kind document`
   (then `task`, then `pull_request`). It returns up to 20 rows and `stopped`.
2. Send each row's `file` as below. A pull request row lists its `parts`: send `parts[resume_part:]`
   in order (`resume_part` is 0 unless an earlier attempt at this same content already got parts
   accepted).
3. **Verify before recording.** Read each task answer, and each pull request part's answer,
   through the table below. A stop is recorded at once, and is never verified or sent again. Any
   other answer is checked before anything is recorded, a success included. An answer whose `code`
   is `payload_digest_mismatch` or `expected_sha256_malformed` (those two values, not any other
   `code`) goes to `verify --code`; leave `--received` out when the refusal carries none. Any other
   answer whose `received_sha256` is a string goes to `verify --received`, which checks that the
   brain received the file byte for byte:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py verify --plan <work>/plan.json --key <key> --received <received_sha256>
   ```

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py verify --plan <work>/plan.json --key <key> --code <code> [--received <received_sha256>]
   ```

   For a pull request part add `--part <i>`: the part's index in the row's `parts`, counted from 0
   (the first part this run sends is `resume_part`). It writes the check into the ledger and
   prints what it decided, with a `next`. Do what `next` says; the exit code (0 when
   the digests are the same, 1 when they differ) is not for this decision:

   - `record`: the answer stands; record it from the table.
   - `resend`: send the same file (or part) once more, verbatim, and read that answer through the
     table from the top.
   - `record_differs`: the brain's digest differed from the file's the same way twice, which is
     not noise in the relay. Record the answer from the table; the row is marked, it is not sent
     again run after run, and Phase 6 lists it for a person to look at. A pull request part counts
     as accepted, and the next part follows.
   - `record_failed`: it differed again, another way. Record `failed`; a later run sends it again.
     For a pull request this ends it: record `failed` with `--parts-written <i>`, and send none of
     its later parts.
   - `record_digest_refused`: the re-send was refused with a `code` too. Record `digest_refused`
     (a pull request with `--parts-written <i>`). Nothing was stored or charged, and the next run
     sends the row again, even when an earlier run stored the same content. It never ends as
     `failed` or `refused`: the brain's `refused` is not sent again.

   The ledger keeps every check, so the decision survives a compacted context, and it writes the
   mark, the details and the number of re-sends onto the record itself. It refuses `digest_refused`
   unless the latest check said so.

   **An answer without a digest.** A `received_sha256` of `null` (the server could not put that
   payload in canonical form), or none on a refusal or an error answer, leaves that one answer
   unverified: record it from the table. Only a success with no `received_sha256` key says the
   server cannot verify relays yet: pass `--note no_relay_digest` to the `done` summary, and the run
   goes on without verifying. An edge page or a transport error is not the door's answer and says nothing either
   way.

   What verification covers: a task's whole `item`; a pull request part's `pull_request`,
   `conversations` and `conversation_count`. Not `project`, `repository`, `git_remote` or
   `agent_accounts`. Documents are not verified: their door answers with no digest. Verification
   detects a relay that changed the payload and repairs it by sending again; the changed version
   stays stored until that re-send lands, and the re-send can spend one more unit.

   **`expected_sha256`** (Phase 0) makes the door check the digest before it writes, so a relay
   that altered the payload is refused with a `code` and the digest it received. Send the value
   from the `next` output already in hand (a task's `expected_sha256`; a pull request part's entry
   of `parts_expected_sha256`, same index as `parts`) on every call, the re-send included. A row
   whose `next` output carries `no_digest` is **not sent** to such a door: record it `failed` with
   that text as the detail, and the next run tries it again. To a door without the argument it is
   sent as before.
4. Then record the answer, as step 3 settled it: a success is never recorded before its digest
   matched, or before it turned out that it cannot be verified. Server text goes in on stdin as
   JSON, never inside a quoted shell argument:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py record --plan <work>/plan.json --key <key> --outcome <outcome> --answer-json <<'FM_ANSWER_END'
   {"detail": "<first error>", "budget": <the answer's budget object>, "unresolved": <its unresolved_links, as returned>}
   FM_ANSWER_END
   ```

   Leave out the keys the answer does not have. `unresolved_links` goes in as the door returned it:
   a list of links or of objects with a `url`. Add `--parts-written` on the command line when
   step 3 or the paragraph below calls for it.
5. Repeat until `rows` is empty or `stopped` is set. After a context compaction, `next` recovers
   the position: the ledger, not memory, says what is left.

**At most twice.** A document, a task or a pull request part is sent at most twice in a run,
whatever the reason: a `resend`, a `retryable` answer, an HTML 5xx page, an
`upstream connect error`, a message that says it failed. When the second attempt calls for a
third, record `failed` instead.

The three calls, in this shape:

```
mcp__Fairmind__Brain_add_document(
    project=<project_id>,
    doc_kind=<spec|design|blueprint|manual|wiki|note|adr_export>,
    title=<the document file's title>,
    uri=<its link>,
    source=<notion|confluence|google_drive|github>,
    summary=<its summary>,
    agent="brain-seed",
)
```

Leave `source` out for a web page, or when the door has no `source` parameter. A row with
`local: true` (a file with no address) is the only case that passes `content=<the file's content>`
in place of `uri` and `source`.

```
mcp__Fairmind__Brain_import_tracker_item(
    item=<the task file, verbatim>,
    project=<project_id>,
)
```

```
mcp__Fairmind__Brain_import_pull_request(
    repository=<repository_id>,
    git_remote=<the git_remote printed by pr_collect.py remote>,
    project=<project_id>,
    pull_request=<the part's pull_request, verbatim>,
    conversations=<the part's conversations, verbatim>,
    conversation_count=<the part's conversation_count>,
)
```

Leave `conversation_count` out only when the part's is null. Add `agent_accounts` to the last two
only when the person named them. Add `expected_sha256` to the last two as step 3 says, only when Phase 0 found it on the door.

**Every call names `project`; this skill never files anything company-wide.** Payloads are passed
**verbatim**: never shortened, summarised, reordered or re-spelled. Spelling is identity, and an
admin erases by it. A pull request's parts are sent in order; a refused, edge-refused or digest-refused part stops
that pull request. Record a pull request once, after its last part. If every part
wrote, record `written`. If a part was throttled, refused, edge-refused, digest-refused, failed or stopped the
run, record **that outcome** even when an earlier part wrote, and add `--parts-written <N>`: how
many leading parts the brain has accepted in all (`resume_part` included). Only a row recorded as
a success is treated as sent. A throttled, failed or partial row is sent again on the next run
from its first unwritten part, so no part is sent twice and a stop shows as a stop.
A row recorded `refused` is **not** sent again until its content changes or the person asks for a
plan made with `--all`: the card keeps the parts that wrote, with the partial-import notice. The
same holds for a refused document or task. That hold is for `refused` alone, the brain's own
refusal: an `edge_refused` row never reached the brain, so the next run sends it again and the
refusal clears by itself once the edge lets the request through.

Read the table by precedence. First, whether the door answered at all: a failed call whose error
is an HTML page from the hosting edge is not the door's answer, so a `403` inside it is never a
stop. Then a stop (`status: throttled`, `key: brain_write_quota_daily`, "Access denied", a 403
naming the write scope), then a digest refusal (`code: payload_digest_mismatch` or
`expected_sha256_malformed`, checked with `verify --code`), then a `received_sha256` that differs from the file, then the other
`status` values, then the `retryable` cases, and only last a message that starts "Brain …
refused". The quota answers carry such a message too, and
recording them as `refused` would skip the whole corpus for good.

| Answer | Outcome to record | Next |
|---|---|---|
| `status: ok` with `written > 0` | `written` | continue |
| `status: ok` with `written: 0`, or the door's unchanged answer | `unchanged` | continue |
| `status: partial` | `partial` | continue; listed for a re-run |
| A document filed or updated | `written` | continue |
| `revision_proposed` | `revision_proposed` | continue |
| `not_reproposed` | `not_reproposed` | continue |
| `status: throttled` | `throttled` | **stop the run** with the position recorded; keep the `quota` for the report file; never retry |
| `key: brain_write_quota_daily` | `quota_unset` | **stop**; the `done` summary tells the person |
| `code: payload_digest_mismatch` or `expected_sha256_malformed` | see step 3: do what `verify --code` says | continue |
| `received_sha256` that `verify` finds different from the file | see step 3: do what `next` says | continue |
| `retryable: true` | send again (**at most twice**, above), then `failed` | continue |
| `status: refused`, or (after the stops above) a message starting "Brain … refused" | `refused`, first error as detail | continue; never edit a file to make it pass |
| "Access denied" without `retryable` | `access_denied` | **stop**: the person lacks the editor role on the project, or the key is bound to another project |
| A failed call whose error is the hosting edge's HTML page with `403` and "Request blocked", in place of the door's JSON answer | `edge_refused`, the row's title as detail | an **edge-filter refusal**: nothing reached the brain, and it is never an authentication failure; never reword the payload; continue |
| A failed call whose error is another HTML error page from the edge (a `502`, `503` or `504`), or a plain-text `upstream connect error` | send again (**at most twice**, above), then `failed` | continue |
| A 403 whose body names the missing write scope | `access_denied` | **stop** |
| `{'message': '… failed'}` alone | send again (**at most twice**, above), then `failed` | continue |
| `status: error` without `retryable` (nothing was written) | `failed`, the message as detail | continue |
| A summariser file that holds `error` and no `summary` | not sent: record the document `failed` with that error | continue |

An edge refusal is recognised by its shape, never by guessing at the payload: the call fails,
and its error carries the HTML error page of the hosting edge in front of the platform, with
`403` and "Request blocked", where the door's JSON answer would be. The request never reached
the brain: nothing was written and no unit was spent. It is not an authentication failure, and
the connection stays up, so the next call goes on as usual. The edge refuses some text patterns
and its rules can change; the payload is never reworded to get past it, since spelling is
identity. Record the refusal with the row's title and go on.

From the first pull request answer:

- Read `repository.repo_ref_scheme`. When it is not `code-ingestion`, the cards are stored, but a
  read of a file will not find these pull requests: pass `--note not_code_ingestion` to the `done`
  summary.
- Read `files`. **An answer with no `files` key comes from a server that does not store changed
  files yet**: the import still counts as done: pass `--note no_files` to the `done` summary. Its
  consequence: a re-run treats those pull requests as unchanged, so once the server stores files
  they are sent again only by a plan made with `--all` over the collected pull requests alone
  (`plan --collected <work>/pull-requests/collected.json --all`, with no `--documents` or
  `--tasks`).

## Phase 6: Report and the Review Queue

Run the `done` summary. It writes the run's report file, with the output of `status` in it, and
prints the only report the person sees. Add a `--note` for each observation made in Phases 0 and
5, and leave `--collected` out when the pull request phase was skipped:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py summary --plan <work>/plan.json --stage done --collected <work>/pull-requests/collected.json
```

Show its `text` exactly as it comes, as What the person sees describes. Then stop: the person asks
if they want more.

For details, read the report file. Per source: written, unchanged (this run's `unchanged` and `unchanged_earlier`, what earlier
runs sent that has not changed), refused by the brain (`refused`, with the detail;
`refused_earlier` rows are listed with `earlier: true`), refused by the edge (`edge_refused`, by
title), refused for a digest (`digest_refused`, by title), failed (`failed`, with the detail; the next run sends them
again), and not attempted because the run stopped. The budget shown is the last one this run
saw; an earlier run's is not carried over. Then:

- Task-to-pull-request links still open (`open_links`): the pull request is outside the window or
  not yet imported; a later run resolves them.
- The budget left: the last `budget` seen, or the `quota` from a throttle.
- Whether the server stores changed files (the `files` key from Phase 5).
- Whether the server verifies relays: how many tasks and pull requests were `verified` or
  `unverified`, and how many re-sends a digest difference called for (`resends`, one unit each
  at most).
- The rows whose digest differed from the file's the same way twice (`digest_differs`): they are
  not sent again run after run (a success counts as sent, a refusal is held), so a person looks
  at each one. A `digest_refused` row is not one of them: a refused send costs nothing.
- A pull request card shows only the first of its files in `mcp__Fairmind__Brain_get` and says
  when it cut the list; `mcp__Fairmind__Brain_context` on any of its files still returns the pull
  request. A pull request with more files than the collector sends carries `file_count`, the real
  number.
- A re-run sends only what changed, and what did not get through. A row the brain refused waits
  for its content to change; a row the edge refused is sent again, and so is one the door refused for its digest. To send everything again, the
  person asks for it and the plan is run with `--all`.

Documents land as **proposals**, so the last step is the person's: open the **Review Queue** in
Studio and confirm or reject each document.

## Resuming after a stop

A stop (a throttle, "Access denied", an unset quota) is recorded against the plan's run, so
`next` on that plan returns no rows. To resume, once the cause is gone (the budget renewed, the
role granted, the quota set), run the last `plan` command of Phase 4 again with the same inputs:
it makes a new plan from the ledger, in which what was sent is `unchanged` and what was not is
`new` or `retry` (a pull request resumes after its parts that wrote). Collecting and shaping do not run again; their files are still in `<work>`. Then
repeat Phase 5. After a context compaction instead, `next` on the same plan recovers the position.

## Error Handling

- `throttled`, "Access denied", an unset quota (`brain_write_quota_daily`): stop the run and never
  retry; the `done` summary tells the person. Edge-filter refusals do not stop the run.
- `pr_collect.py`: a failure of one pull request (a usage error, a pull request too large, a part
  file that cannot be written, a 403 on that pull request) is recorded in `not_collected` with the script's message and the
  run continues; a `gh` failure for one is retried on later calls, three attempts in all. A `gh`
  login that expired, a rate limit (exit 3) or `gh` missing (exit 127) stops the pull request
  phase, since every later one fails the same way. Report `not_collected` in Phase 6.
- The other scripts: exit 2 is usage, 3 an unreadable input or ledger (a ledger that does not parse
  is never overwritten: tell the person), 5 a file that could not be written.
- A source that cannot be read (a connection that asks for sign-in): say which, skip it, go on.

## Files this skill keeps

- `.fairmind/brain-seed/ledger.json` is the resume and incremental memory. Deleting it makes the
  next run send everything again.
- `.fairmind/brain-seed/report-<run>.json` is the technical report of one run, kept next to the
  ledger because `<work>` can be deleted.
- `<base_path>/brain-seed/run/` holds the inventories, the collected text and the shaped files. It
  can be deleted after the run, and it never contains the ledger.
- All three are local files that no call sends.
