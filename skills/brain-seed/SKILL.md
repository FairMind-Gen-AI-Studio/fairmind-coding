---
name: brain-seed
description: Use only when a person asks to seed or backfill a project's company brain - it brings in the documents they name (as links with a summary the agent writes), documents kept as files in the project's repositories (a card with a summary written here, never the file's text), the tasks of the tracker projects and lists a person picks updated in the last 90 days, plus the whole of any epic they pick whatever its dates, and the merged and closed pull requests of the same window from every repository of the project, tasks and pull requests as short reference cards (facts, a summary written here and a few rationale points, never the original text), read through the connections already in the session. It lists first (titles, links, ids and each task's own text; no document bodies, no comments), shows the plan, the budget units and what leaves the machine, asks for one yes before it collects anything or writes to the brain, asks the brain whether each task and pull request changed before it reads or summarises it, then sends in a fixed order (documents, tasks, pull requests), stops cleanly when the daily budget runs out, and resumes later without sending twice. Also use when asked to "seed the brain", "fill the brain with our docs and tickets", or "backfill the brain".
---

# Seed a project's brain

## Overview

A new project's brain is empty, so the first question an agent asks it comes back with nothing.
This skill fills it from what the team already has: the documents a person names or that are kept
in the project's repositories, the tasks of the tracker projects and lists a person picks, **updated in the last N days**, the whole of every epic they pick (whatever its dates), and
the pull requests of the last N days (90 by default) from every repository of the project that the
person selects. The repositories come from the project in FairMind Studio, never from the checkout
the skill runs in. A task of a project or list last touched before the window is not sent, however many the tracker holds; an epic the person picks is not cut by the window, and neither is anything under it. Sub-agents on a small
model list, summarise and read; scripts do the mechanical work and only the sender calls the brain.
Documents are sent by **this session itself**. Tasks and pull requests are sent by
`seed_send.py`, a script on this computer, through the project's FairMind MCP connection (the same address
and key this session uses), and by this session itself when the script cannot use that connection.
The order is fixed: documents, then tasks, then pull requests.

**Reference cards.** A task and a pull request travel as a *card*, never as their text: the facts
a script collects (ids, state, dates, branches, commits, changed files, the people who took part,
the version of what was written in it), plus a short summary and up to five rationale points
written here after reading the source. The text stays on this machine. Before a task or a pull
request is summarised the brain is asked, in a free **check** call that carries only its version,
whether it already holds that version; if it does, nothing more is read, written or charged.

**Results.** Tasks, pull requests and repository files are recorded **confirmed**, with the agent as
their author; the change to a repository file whose card a person had confirmed waits for review
instead. Pages land as **proposals** (a page is a document from Notion, Confluence, Drive or the web) that
wait in the Review Queue until a person confirms them.

**Units.** Each write spends one unit, taken from this project's open setup mode if there is one and from
the tenant's daily brain write budget otherwise. A check call spends none, and neither does a task
or a pull request the brain already holds.

**The full recipe.** When the person asks how to prepare for a run, what to ask FairMind for, or
how a large run is set up, point them to `references/fill-a-brain-from-zero.md` beside this skill: the
customer's recipe from an empty brain, what leaves the machine, and what to ask FairMind for.

**What leaves your machine when this skill writes.** A task or a pull request travels as a card:
its title, state, ids, dates, links, the account id or login of everyone who took part, the
commit ids and changed file paths of a pull request, the source's own word for who can see it,
and the summary and rationale points written here, which are free prose. The description and the
comments are not sent; the prose is written from them, so it says whatever was chosen to say, and
the tool that builds the card refuses a text that repeats twelve or more words in a row from them, or a whole short paragraph or comment of them,
and one that looks like a credential. A page travels as its link, its title and a summary
written here, except a local file with no address, whose whole body travels. A file kept in a
repository travels as a card: its title, repository, path, commit, branch and blob id, the
repository's visibility, the credential-free remote and a summary written here, never its text. The project id and
the remote URL of each repository of the project go with their cards; a remote URL is sent without
any credential in front of the host. This is the **brain write-back**, and the plugin README's *What leaves your machine*
describes it in full.

**Announce at start:** "I'll fill this project's FairMind brain from your documents, tasks and
pull requests. A few quick questions first; nothing is sent until you approve."

## When to Use

Use this skill only when a person asks for it.

Do **not** use it to:

- Import open pull requests, unless the person asks for them.
- File a document for the whole company. Everything this skill sends is for one project.
- Run on a checkout that is not connected to a Fairmind project (Phase 0 stops).

It runs from any checkout of the project: the repositories come from the project, so every checkout
of it lists the same repositories (the ledger and the saved selection live in the checkout).

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

**Screens.** There are two main screens. Besides them there is only: one follow-up for each answer
that leaves a scope open (the tracker projects, lists or epics; the document space or folder; the
numbered pick after `Let me choose`); the one-time question about the accounts of ClickUp integrations
(Phase 2, ClickUp token path only); the re-ask of Phase 4 step 6; the separate setup mode question of Phase 3 (only when the run is
large and the setup mode door is present); the offer of Phase 6 to close a setup mode this run opened (closed only after a yes); and screen 2 asked again when the answer was not
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
options; every option has a description. Leave out a question, or an option, whose source is skipped.

## Phase 0: Preconditions

1. Read `.fairmind/active-context.json` at the repository root. It needs `fairmind` set to
   `"configured"`, plus `project_id`, `repository_id` and `base_path`. `repository_id` is used only when no
   `repositories.json` exists: the plan then binds page rows to it (Phase 2). **Never write those keys
   yourself.** If the file is missing or a key is absent, **stop** and offer `/fairmind-connect`.
2. Set `<work>` to `<base_path>/brain-seed/run/`. Sub-agents write there, and the path guard admits
   writes only under `base_path`. The ledger stays at `.fairmind/brain-seed/ledger.json`, outside
   `<work>`.
3. The tools are named exactly `mcp__Fairmind__<Name>`. Check each door by name in the tool list:
   - `mcp__Fairmind__Brain_tell`: absent, the documents are skipped. That includes repository files.
   - `mcp__Fairmind__Brain_import_tracker_item`: absent, the **task phase is skipped**. It arrives with a later platform release than the other brain tools. Never
     record a task through another door.
   - `mcp__Fairmind__Brain_import_pull_request`: absent, the pull requests are skipped.
   - All three absent: **stop**.
   - `mcp__Fairmind__Brain_setup_mode`: absent, the plan screen keeps the old wording (a large run
     can ask FairMind to open a setup window for the tenant, or continue over several days) and
     this skill never opens anything. Its absence stops nothing else.
4. A document needs no probe. `mcp__Fairmind__Brain_tell` takes its link, the system it lives in
   and who may open it as `source.system` and `source.visibility`, and its check is made at
   send time (Phase 5).
5. **The project's repositories.** They come from the project, not from this checkout. Pull
   requests and repository files need `gh auth status` to succeed; otherwise say so and skip both,
   and go on to step 6. Ask the project for its repositories (a tool that is absent or a call that
   fails skips both too), first delete `<work>/repositories-answer.md` and `<work>/repositories.json`,
   so that a failed listing never leaves last run's rows to bind a page, then save the answer
   **verbatim** (the text exactly as it came, nothing added, dropped or reordered) to
   `<work>/repositories-answer.md`, and build the list:

   ```
   mcp__Fairmind__Code_list_repositories(project=<project_id>)
   ```

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/project_repos.py build --answer <work>/repositories-answer.md --out <work>/repositories.json
   ```

   `<work>/repositories.json` holds one row per repository of the project, with its `repo`
   (`owner/name`), `repository`, `git_remote` and a `status`: `ok`, `not_github` or `unreadable`.
   Only `ok` rows are seeded. `<repo>` below is a row's `repo`. The summary the script prints lists the others in `skipped`, each with
   its reason. Exit 127 means `gh` is missing: skip pull requests and repository files. Exit 3 with the
   message "the answer lists no repository" (or an answer that is the server's "No repositories
   found" sentence) means the project has none. With no `ok` row, pull requests and repository files are skipped. The remote of every row comes from this
   file, already without any credential: the checkout's own remote is never read or sent.
6. **Probe the two card doors, one check call each** (the pull request door once for every
   `ok` row, below). A task or pull request is a reference card, and
   a server older than the card release cannot take one. A *check* carries only `source`, `id`
   and `source_version`, writes nothing and charges nothing, so a made-up item tells which kind of
   server this is:

   ```
   mcp__Fairmind__Brain_import_tracker_item(
       item={"source": "probe", "id": "probe", "source_version": "<64 zeros>"},
       project=<project_id>,
       agent="brain-seed",
   )
   mcp__Fairmind__Brain_import_pull_request(
       repository=<an ok row's repository>,
       git_remote=<that row's git_remote>,
       project=<project_id>,
       pull_request={"source": "github", "id": "probe", "source_version": "<64 zeros>"},
       agent="brain-seed",
   )
   ```

   A door that takes cards answers `status: "ok"` with `updated: false` and `exists: false`. A
   refusal as malformed, or an error naming an argument or a missing `summary`, is a server that
   predates cards: **that phase is skipped** with the plain line below. Nothing was stored or
   charged, and no payload of the earlier shape is ever sent to it.

   The pull request door is probed **once for every `ok` row**, each with its own `repository` and
   `git_remote`, because the brain decides access per repository. "Access denied" to a row's probe,
   unless it names the write scope (below), is that repository's refusal and says nothing about the server: the repository is left out of
   Q4, of the collection and of the repository files, with a line of its own (below), and the other
   rows go on. The pull request phase is skipped as a whole only when the door is absent or the
   probe of **every** `ok` row says the server predates cards; any other failure of a probe (a
   timeout, an error that names nothing) leaves that repository out with the line "<name> (the
   brain could not be reached for it)" and does not decide for the others. An "Access denied" that
   names the write scope, or another 403 whose body names it, to either probe, or any "Access denied" to the task probe (it carries no
   repository) is about the whole project, not one repository: **stop**, with the plain line below.
   Probe a door only when its tool is present and, for the pull request door, step 5 left an `ok`
   row.
7. Run every command from the repository root: the ledger's default path and the card paths in
   `collected.json` are relative to it.

**What to say.** When everything above is in place, say nothing. A missing check gets one plain
line, in the person's language, that says what to do:

- not connected: "This folder isn't connected to a FairMind project yet. Run /fairmind-connect,
  then ask again." (stop)
- all three doors absent: "Your FairMind server doesn't offer brain imports yet. Ask your FairMind
  admin for the release that includes them." (stop)
- task door absent, or its probe says the server predates cards: "Tasks will be skipped: your FairMind server doesn't accept them yet."
- a probe refused with "Access denied" naming the write scope: "This folder's FairMind key can't
  write to the brain. Regenerate it on the Studio Developer page, run /fairmind-connect with it,
  then ask again." (stop)
- the task probe refused with any other "Access denied": "This session can't write to this
  project's brain: either your account lacks the editor role here (ask a project admin), or this
  folder's FairMind key belongs to another project (run /fairmind-connect with the right project).
  Then ask again." (stop)
- `gh` failing: "Pull requests and repository files will be skipped: the GitHub command line isn't
  signed in or isn't installed. Run gh auth login and ask again to include them."
- the project has no repositories: "This project has no repositories associated in FairMind Studio
  yet, so pull requests and repository files will be skipped. Associate one there and ask again."
- the project's repositories cannot be listed: "Pull requests and repository files will be skipped:
  your FairMind server doesn't list this project's repositories."
- no `ok` row: "Pull requests and repository files will be skipped: none of this project's
  repositories can be read on GitHub."
- `skipped` not empty: one plain line naming each repository left out and why, for example "Left
  out: <name> (not on GitHub), <name> (GitHub can't be reached with your login)." Pull requests and
  files still go ahead for the others.
- pull request door absent, or every probe says the server predates cards: "Pull requests will be skipped: your FairMind server doesn't accept them yet."
- the brain refused a repository's probe ("Access denied" not naming the write scope): "Left out: <name> (the brain refused it
  for this account or key). Ask a project admin to check the access to it." Say it once, naming
  every such repository; the others go ahead. Suggest /fairmind-connect only when **every** `ok`
  row was refused and the task probe did not pass, since then the key may belong to another
  project (the task door refuses a key of another project, so a task probe that passed rules it out).


## Phase 1: Sources

1. Detect which connections the session has, by server family and without naming tools: Notion,
   Confluence and Jira (Atlassian), Google Drive, ClickUp, Linear, Azure DevOps. ClickUp is also
   **found** when the person has set an API token for it, with no ClickUp connection in the session.
   This asks nothing of ClickUp and prints nothing secret; the token is read from the environment
   this session already has (`CLICKUP_API_TOKEN`), and the skill never prints it, never asks for it in
   chat and uses no shell trick to load it:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py check --local
   ```

   It prints `{"token_set": true}` or `{"token_set": false}`. When Phase 0 left
   an `ok` row, also read what was chosen last time, and keep the answer for Q4 and the tracker
   follow-up:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_selection.py show --project <project_id> --repositories <work>/repositories.json
   ```

   It prints `{saved, same_as_last_time, new_repositories, gone_repositories}`: `saved` is the
   earlier selection or null, `same_as_last_time` the `owner/name` of the current repositories it
   chose, `new_repositories` the repositories of the project that selection neither chose nor
   declined, and `gone_repositories` the chosen ones the project no longer lists.
2. Ask **screen 1**, one `AskUserQuestion` call with the four questions below. Build the options
   from the connections found in step 1. The window applies to the tasks of projects, spaces, folders and lists (by their last update) and to pull requests; an epic the person picks comes whole, whatever its dates.

   - **Q1** — header `Documents`, multi-select (pages from a connection and repository files can be picked together):
     - Question: "Which documents should the brain learn from? You can pick more than one. A source is a place where your team keeps written knowledge: manuals, architecture pages, specifications. I'll only list titles and links there; no document is opened or sent until you approve the plan. To give exact pages, choose Other and paste their links, one per line; they are added to what you picked."
     - `None` — "Skip documents. The brain learns only from your tasks and code changes. Choose it on its own."
     - `<Connection> <items>` (one option for each document connection found, at most 2; `<items>` is what that connection holds: `pages` for Notion and Confluence, `files` for Google Drive) — "I'll list the <items> in the space or folder of <connection> that you name next. You'll pick which ones to send on the next screen."
     - `Files in the project's repositories` — "I'll list the documentation files kept in the project's repositories (docs, specs, design folders), for the repositories you choose below, or for all of them when I don't ask. You'll pick which ones to send on the next screen."

   A **page source was asked** when a connection option was picked or links were pasted in Other;
   **repository files were asked** when the files option was picked. `None` picked on its own skips
   both. Repository files also need at least one selected repository in Q4 (or Q4 not asked): with
   Q4 `None` they are skipped too.
   - **Q2** — header `Tasks`, single select:
     - Question: "Which task tracker should the brain learn from? A tracker is where your team files tickets. I'll bring in the tasks of the projects, lists or epics you pick there. I read each task's description and comments here to write a short summary; what goes to the brain is that summary, the task's basic facts and the tracker account ids of the people involved (never their names), not the original text."
     - `None` — "Skip tasks."
     - `<Tracker>` (ClickUp, Linear, Jira, Azure DevOps or a Notion database; at most 3 found; ClickUp is offered when its connection or its token was found) — "Use your <tracker> connection. Next I'll look up the names of its projects, lists and epics, names only, so you can pick which to bring in." For ClickUp with a token and no connection, the description reads "Use your ClickUp API token. Next you'll give me the links of the spaces, lists or epics to bring in."
   - **Q3** — header `Window`, single select:
     - Question: "How far back should I go? Only tasks updated, and pull requests closed, within this window are brought in; older ones are left out. The exception is an epic you pick: it comes whole, whatever its dates. A longer window gives the brain more history but costs more of your daily allowance."
     - `Last 90 days (Recommended)` — "About a quarter of history. A good start for most teams."
     - `Last 30 days` — "Only recent work. Faster and cheaper."
     - Any other number of days comes through the tool's own Other entry.

   With a saved selection, the first option of Q3 reads `Last <days> days (Recommended)` with the
   saved days (the 90 and 30 days options follow, leaving out one that equals it).
   - **Q4** — header `Repositories`, single select, asked only when Phase 0 left an `ok` row whose
     probe passed:
     - Question: "Which of this project's repositories should the brain learn from? For the ones you choose I'll bring in their pull requests, the code changes your team merged or closed in the window, and, if you asked for them above, their documentation files. The repositories are the ones the project has in FairMind Studio, wherever this folder is. Each pull request becomes a short card: a summary I write here, the people involved and the list of files it changed. The description and the review comments themselves are not sent."
     - `All <N> (Recommended)` — "Include every repository listed (N of them)."
     - `Let me choose` — "I'll show a numbered list of the repositories; reply with the numbers to keep."
     - `None` — "Skip pull requests and the documentation files of the repositories."

   Q4 lists only the `ok` rows whose probe passed. It is **not asked** when the pull request door is
   absent or predates cards: `ok` rows exist then, but no repository is selected for pull requests,
   and repository files come from every `ok` row. Nor is it asked when Phase 0 left no `ok` row the
   brain neither refused nor failed to reach: there is then nothing to select. The save differs
   between the two (Phase 3).

   When `seed_selection.py show` returned a saved selection, the first option of Q4 reads
   `Same as last time (Recommended)` and `All <N>` loses its suffix. It selects the repositories
   of `same_as_last_time` (their `owner/name`), never a new one. Its description names the
   repositories of `new_repositories` and says they are **not** included, and that `Let me choose`
   adds them (or says there are none). A saved repository the project no longer lists
   (`gone_repositories`) is dropped silently. A saved repository Q4 does not list this time (its
   probe did not pass) is left out of this run; Phase 0 has said why.

   The repositories Q4 selects are the **selected repositories** from here on: every row Q4 lists for
   `All`, those of `same_as_last_time` that Q4 lists for `Same as last time`, the numbered picks for `Let me
   choose`, none for `None`.

   **ClickUp's token, right after Q2 picks ClickUp** (and never earlier, so that a run that does not
   use ClickUp sends ClickUp nothing). Run `clickup_read.py check` once; it asks ClickUp one free
   question with the token:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py check
   ```

   It prints `{"ready": true}` or `{"ready": false, "reason": "..."}` and always exits 0.
   `ready: true` means ClickUp is read by the scripts of Phase 2 and Phase 4 (the **token path**).
   `ready: false` with a ClickUp connection in the session means the connector path of every other
   tracker, with one plain line that says why (no token is set; ClickUp refused the token; ClickUp could not be reached
   just now), and that setting `CLICKUP_API_TOKEN` in their environment makes it faster (100
   requests a minute, against the connector's daily allowance). `ready: false` with no ClickUp
   connection (only the token was found) means ClickUp cannot be read: say so in one plain line and
   go on as if the person had picked `None` for tasks. Never print the token or ask for it.

   **Exit 4 of any `clickup_read.py` call** (the token is missing, malformed or refused by ClickUp;
   nothing was written, except by `read`) ends the token path: the rest of the ClickUp tasks go
   through the ClickUp connection when the session has one, and are skipped with one plain line
   ("ClickUp refused the token, so its tasks are left out of this run.") when it has none; a
   refusal before the inventory is the same as picking `None`. Every later call says "Exit 4: as
   Phase 1 says" and names only the step of its own that follows.

   **Follow-ups.** One more question for each answer that leaves a scope open, and only then (a
   document space and a tracker root are two). For the tracker, read the names of its projects or
   lists from its hierarchy (names only, nothing else) and ask one `AskUserQuestion` as a
   multi-select with up to 4 of them as options, each described as "Bring in the tasks of this
   one", plus the tool's own Other entry. An epic is a task, not an entry of the hierarchy: an epic
   is given by its id or link in Other, or picked from the epics the project lists, and its whole
   subtree comes with it, whatever the window and whatever the dates of its tasks. With a saved selection, the saved roots come first. On the ClickUp token
   path what the person picked becomes the roots of Phase 2: a space, a folder, a list or an epic
   (a task), each given by its id or link. With no ClickUp connection in the session there is no
   hierarchy to read: ask in one plain sentence for the links (or ids and their kind) of the
   spaces, folders, lists or epics to bring in, and turn each link into a root with `root --link`
   (Phase 2). A space name or a
   folder has no closed form: ask it as one plain sentence. `Let me choose` on Q4 is one numbered
   list of the repositories, answered with numbers.
3. **Nothing outside the named spaces is read.** A document that a task cites outside them is
   listed in Phase 3 and never opened unless the person ticks it.
4. `agent_accounts`: ask for them only if the person says the company runs work through an agent
   account. Otherwise leave the field out. On the ClickUp token path the accounts live in the
   mapping file instead, and are asked once (Phase 2).

## Phase 2: Inventory, reads only

This phase reads before the yes, and it says so when it starts: for documents their titles, links,
ids and edit markers, for tasks their fields and **their own text** (to find the documents they
cite). **Document bodies and comments are not read until the yes.** The person sees this one
line, in their language: "Listing what's in those sources: titles and links, each task's own
description, and the names of tracker projects. No document is opened, no comment is read and
nothing is sent yet."

First delete the inputs a former run derived, so that no plan is made from last week's files (a
row nobody ticked this time would be sent):

```bash
rm -rf <work>/inventory-documents.json <work>/listed-documents.json <work>/inventory-tasks.json <work>/inventory-repo-documents.json <work>/ticked-documents.json <work>/documents.json <work>/repo-documents.json <work>/pulls <work>/estimate.json <work>/pr-refs.json <work>/refs <work>/cited-tasks.json <work>/tasks.json <work>/freeze-summary.json <work>/merge-summary.json <work>/clickup-vocabulary.json <work>/clickup-tasks.raw.json <work>/clickup-answers.json
find <work>/pull-requests -name collected.json -delete 2>/dev/null || true
```

Each input below exists only when this run wrote it, and a command names only the files that
exist: a `--documents`, `--tasks`, `--pull-list` or `--collected` that names a missing file makes
`plan` exit 3. Run a step only for what was asked.

Two inventory sub-agents (`model: "haiku"`, general-purpose, briefs in
`references/subagent-briefs.md`) write `<work>/inventory-documents.json` (only when a page source
was asked) and `<work>/inventory-tasks.json` (only when a tracker was picked). They list titles,
links, ids, edit markers and cited links; `cited_links` comes from a task's description. The task
inventory takes every project, list or epic the person picked, an epic's whole subtree included, whatever its dates (the window does not cut it).
**They do not read document bodies.** Per-source notes: `references/document-sources.md`; per
tracker: `references/tracker-jira.md`, `references/tracker-clickup.md`,
`references/tracker-linear.md`, `references/tracker-azure-devops.md`,
`references/tracker-notion.md`. On the ClickUp token path the task inventory sub-agent does not run (see below).
The documentation files kept in the project's repositories are not
listed by a sub-agent but by a script, only when repository files were asked, with one `--repo`
for each selected repository (none when Q4 was not asked because the pull request door is absent or
predates cards, which lists every `ok` row; when it was not asked because the probe of no `ok` row
passed, skip this step):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/repo_docs.py list --repositories <work>/repositories.json --repo <repo> [--repo <repo> ...] --out <work>/inventory-repo-documents.json
```

**ClickUp on the token path** (Phase 1's `check` answered `ready: true` and ClickUp was picked).
A script lists the tasks instead of the task inventory sub-agent, and writes the same
`<work>/inventory-tasks.json`, plus the statuses and task types the tasks and their parents use in
`<work>/clickup-vocabulary.json`, and every task it downloaded in `<work>/clickup-tasks.raw.json`
(later calls take it with `--raw` and do not download those tasks again). Each root is the kind of thing the person picked, with its id:
`space:<id>` (the space's folders' lists and its loose lists), `folder:<id>`, `list:<id>`, or
`task:<id>` (an epic, by its permanent id or its custom task id: the task and its subtasks at
every level). Pass one `--root` for each. When the person gives a ClickUp address instead of an id
(typed in Other, or with no connector to browse), turn it into the root; the address may be a
task, list, folder or space link, and nothing is requested:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py root --link <url>
```

It prints `{"root": "<kind>:<id>"}`. Exit 2 means the address does not name its kind (a view or a
doc, say): ask the person for the id and whether it is a space, folder, list or task.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py list --root <space|folder|list|task>:<id> [--root <kind>:<id> ...] --days <N> --out <work>/inventory-tasks.json --vocabulary <work>/clickup-vocabulary.json
```

This call is paced to ClickUp's allowance and reads one task at a time through an epic's tree, so
it can run for minutes. **For every `clickup_read.py` call that lists or reads (`list`, `get`,
`read`) set the Bash tool's timeout to its maximum; if a call is killed before it prints its
summary, run it again** (`list` and `get` write their files only when they finish; `read` has its
own restart rule below).

A script cannot tell what a status name or a task type means, so the agent decides once and the
decision is kept in `.fairmind/brain-seed/clickup-mapping.json` for the project, a local file, so
that it is not judged again in each run. Ask the script which names the mapping lacks:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py map --vocabulary <work>/clickup-vocabulary.json --mapping .fairmind/brain-seed/clickup-mapping.json --project <project_id>
```

It prints `pending` (`statuses`, each `{status, status_type}`, and `types`, each a name or a
`{custom_item_id}`), the `agent_accounts` already kept, `agent_accounts_asked` and `merged`. For
each pending name, decide following `references/tracker-clickup.md` (a status by what the whole
name means, a type by its name), write the decisions to `<work>/clickup-answers.json`:

```json
{"statuses": [{"status": "<name>", "status_type": "<type>", "status_category": "todo|in_progress|done|cancelled"}],
 "types": [{"type": "<name>", "kind": "task|issue|requirement"}]}
```

A type that `pending` shows as `{"custom_item_id": <n>}` is answered with that same number:
`{"custom_item_id": <n>, "kind": "task|issue|requirement"}`. Merge the answers; repeat `map` until
`pending` is empty (an answer for a name that is not pending is refused with exit 2 and nothing
is written):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py map --vocabulary <work>/clickup-vocabulary.json --mapping .fairmind/brain-seed/clickup-mapping.json --project <project_id> --answers <work>/clickup-answers.json
```

**Agent accounts**, only while `map` prints `agent_accounts_asked: false`: ask the person in one
plain question whether their ClickUp workspace has integrations or automations that post comments,
and for the numeric ids of those accounts. Never guess an account. Put the answer in the answers
file as `{"agent_accounts": ["<id>", ...]}`, and as `{"agent_accounts": []}` when they name none:
merging it records that the question was answered, so it is never asked again for this project.

`list` exit codes: 3 with a non-empty `not_read` means the rest was still written; say, in one plain
sentence, which roots or tasks could not be read, and go on. Exit 2 (a bad root, a missing file)
stops the ClickUp tasks: fix the call or say so. Exit 4: as Phase 1 says (the task inventory
sub-agent then reads the ClickUp tasks when the session has a connection).

Then run the following. Each script creates the directory it writes into.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/link_normalize.py --in <work>/inventory-documents.json --out <work>/listed-documents.json
```

(only when a page source was asked; `listed-documents.json` is every page found, which only the
estimate below reads, and the plan that is sent from never does). Pull requests are listed for
every selected repository, in one Bash call that runs `pr_collect.py list` once for each (the file
name joins owner and name with two underscores):

```bash
for repo in <repo> [<repo> ...]; do
  python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py list --repo "$repo" --days <N> --out "<work>/pulls/${repo/\//__}.json"
done
```

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py plan --project <project_id> --work <work> --repositories <work>/repositories.json [--documents <work>/listed-documents.json] [--documents <work>/inventory-repo-documents.json] [--tasks <work>/inventory-tasks.json] [--pull-list <work>/pulls/<owner>__<name>.json ...] --out <work>/estimate.json
```

A row in `listed-documents.json` with `needs_id` set has a short or display link whose page id is not
known. Ask its sub-agent for the id, or put the row in the refused list: a link spelled two ways
makes two cards.

When Phase 0 wrote no `repositories.json` (pull requests and repository files skipped) leave out
`--repositories`, every `--pull-list` and the repository files from every `plan` command, and pass
`--repository <repository_id>` (from `.fairmind/active-context.json`) instead: it binds the page
rows, and only then.

## Phase 3: Plan, and one yes

**Propose** the documents: the stable ones (manuals, architecture, specifications) and every
document a task cites inside the named spaces. Leave out minutes and drafts. Cited documents
outside the named spaces are listed unticked and are never part of "All proposed": only the
person's explicit pick adds one. Propose the files of `inventory-repo-documents.json` the same
way: manuals, architecture, specifications and decision records, not drafts or notes. Print the
proposed documents as a short numbered list of titles, a repository file with its repository and
path.

The estimate is computed with every proposed document ticked, so the yes covers the largest set
and choosing fewer only lowers the cost. When the person chooses fewer on screen 2, write the
ticked files again with only those rows and run the commands below again before Phase 4.

For pages, write **only the ticked rows** to `<work>/ticked-documents.json`: the ticked rows of the inventory
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

The ticked repository files become a file of their own, copied by index so that no row is typed
(the 1-based numbers of the rows of `inventory-repo-documents.json`), and never passed through
`link_normalize.py`, which a repository file does not need. When repository files were asked,
`pick` always runs and its output is always passed: with no `--index` it writes `[]`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/repo_docs.py pick --in <work>/inventory-repo-documents.json --index <N> [--index <N> ...] --out <work>/repo-documents.json
```

A file that is not in `repo-documents.json` cannot be sent by a later plan either. Then run the
plan again with the files that exist as `--documents` (the pages' `documents.json` only when pages
were ticked, `repo-documents.json` whenever repository files were asked), so `estimate.json` holds
exactly the ticked documents:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py plan --project <project_id> --work <work> --repositories <work>/repositories.json [--documents <work>/documents.json] [--documents <work>/repo-documents.json] [--tasks <work>/inventory-tasks.json] [--pull-list <work>/pulls/<owner>__<name>.json ...] --out <work>/estimate.json
```

The counts below and the summarisers of Phase 4 read that file. Then print the
estimate and keep its `text` for screen 2:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py summary --plan <work>/estimate.json --stage estimate
```

**What screen 2 must convey:**

- The counts, as the estimate text carries them: to send, already in the brain, refused earlier.
  Those for tasks and pull requests are a first guess, not a promise: the estimate has not read a
  task's comments and cannot know what the brain already holds, so the number can turn out lower
  or higher, and a task or pull request the ledger holds as sent is assumed unchanged. Say so in
  one plain sentence. Add no count of your own.
- The budget units, as the estimate text carries them ("up to" when pull requests are in the window): at most one per document to send, per task to send and per pull request in
  the window (`summary.pull_request.units_at_most`). Before a task or a pull request is written
  the brain is asked, in a free call, whether it already holds the current version, so on a
  re-run the real number is lower. A task or pull request the ledger holds as sent is assumed
  unchanged, so the estimate does not count a task card the brain no longer holds (removed, or
  deleted by an erasure of a login that took part): the check finds it, and it is summarised and
  written again at one unit each without a new question; one erasure can bring back many. A changed
  task or pull request whose summary the brain already holds from another project is written
  without being read or summarised, at the same one unit; the estimate cannot tell which these are
  and counts every row to send alike. Every
  pull request in the window is counted, as "up to". The daily allowance cannot be read before a call. When the total is large, call `mcp__Fairmind__Brain_setup_mode` with `action: "status"` and the
  project id first, and say what it returned: a setup mode of this project already open (`in_effect`
  true) is used as it is, and the plan says its remaining writes; a mode open for another project
  (`other_project_open`) means this run goes on at the daily budget, so say that a mode is open for
  another project and that the run continues over several days unless an admin of that project closes
  it; an `expired` mode lifts nothing; no mode at all leads to the offer of Phase 3. Without the door
  the old wording applies: a FairMind operator opens a setup window for the tenant, or the run
  proceeds over several days and resumes where it stopped. **Setup mode is opened only after the
  separate yes of Phase 3, never on the plan's `Proceed`.**
- What reading the tasks costs on the tracker's own connection: about two calls per task brought
  in (the task and its comments), plus the listing, because the version of a task covers its
  comments and has to be known before the brain can be asked about it. A hosted tracker connector
  can have a daily call allowance, so a large task phase may be better split across days. On the
  ClickUp token path the same reads are requests to `api.clickup.com` with the person's own token,
  paced to 90 a minute, and have no daily allowance.
- The ticket references: the disclosure sentence of the question below, in the estimate text too. The
  pass writes nothing to the brain and spends no unit, but a cited ticket the plan did not list
  becomes a task to send, and pull requests are not collected yet, so the estimate cannot count
  those: the count is made when the final plan is computed after the collection, and Phase 4 step
  6 asks again if the units come out higher than shown.
- That the yes covers this estimate: if Phase 4 ends with more units than shown, it asks again.
  It also covers the re-sends of Phase 5: a payload the brain received different from its file is
  sent once more, which can spend one more unit each (the altered version may already have been
  written). They cannot be counted in advance; Phase 6 reports them.
- What leaves, as in the Overview: for tasks and pull requests a card, never the text: the facts,
  the account ids or logins of everyone involved (the tracker's account ids and GitHub logins,
  never display names), the commit ids and changed file paths of a pull request, and a summary and
  a few points written here; the remote URL of each repository without credentials; for pages only
  link, title and summary, except the whole body of a local file with no address; for a file kept
  in a repository, what the plan question below says, which is the one complete statement. A test
  call with made-up data went to each card door in Phase 0; it carried the project id and, for the
  pull request door, the first repository's id and remote URL.
- The project the cards go to, and that the cards land readable by everyone on the project.

**Screen 2**, one `AskUserQuestion` call. The `text` from `summary --stage estimate` goes in the
question as `<estimate text>`; use the project's name, never its id.

- **Q1** — header `Documents`, only when documents were proposed:
  - Question: "I found N documents worth keeping, listed above. Which should go to the brain? A page from Notion, Confluence, Drive or the web becomes a proposal that waits for someone on the project to confirm it in Studio; a file kept in one of the project's repositories is stored at once, already confirmed."
  - With exactly 1 document, two options:
  - `Include` — "Send this document to the brain."
  - `Skip` — "Send no documents this time."
  - With 2 to 4 documents, a multi-select with one option for each: `<document title>` — "<one plain sentence on what the document covers>"
  - With more than 4, these three options:
  - `All N proposed (Recommended)` — "Send every document listed above."
  - `None` — "Send no documents this time."
  - `Let me choose` — "I'll show a numbered list; reply with the numbers to keep."
- **Q2** — header `Plan`:
  - Question: "<estimate text> Send this to the FairMind brain of <project name>? A unit is one write, taken from your workspace's daily allowance. If the allowance runs out, the run pauses and continues from the same point next time.[ Only when the total is large: A setup mode can raise the write budget for this project for a few days; I will ask about it separately, and nothing is opened without your yes.] [ Without the setup mode door: For a run this size your FairMind contact can open a setup window instead.] A send that arrives altered is sent once more, which can cost one more unit each, and this yes covers that. The task and pull request cards are sent by a script on this computer, through the same FairMind connection this session uses, or by this session itself when the script cannot use that connection. If the final count comes out higher than shown, I'll ask again. Reading the tasks also uses about two calls per task brought in on your tracker connection, which may have its own daily limit. Tickets that the pull requests cite are read by id through the same connection even when they lie outside the projects you picked, and the text of each new or changed pull request is read on this computer to find those references; a ticket found is read with its comments, together with the tasks or epics above it, and is sent like the others, so the tickets found can add tasks to the count. What leaves this computer: for each task and pull request, a short card with a summary I write here from its text (the description and comments themselves are not sent), the tracker account ids and GitHub logins of the people involved (never display names), commit ids, the paths of the files each pull request changed, and the remote address of each repository without credentials; for pages, only the link, the title and a short summary written here, or only the link and its version when the brain already holds a summary of that version (a local file goes whole); for a file kept in a repository, its title, where it is kept (repository, path, commit and branch), a short code that identifies its content, whether the repository is public, private or internal, and a short summary written here, never its text. Apart from the request for this project's repository list (it carried the project id) and a test call with made-up data, nothing has left this computer yet."
  - `Proceed` — "Read what you approved, write the cards and send them now. Everyone on the project can read what is stored."
  - `Change` — "Go back and choose different sources or a different window. Nothing is sent."
  - `Cancel` — "Stop here. Nothing is collected or sent; the list I made stays on this computer only."

Say in the question which of the ticked documents will wait for review (pages) and which land at
once (repository files); leave out the half that does not apply.

**On the ClickUp token path** the question's wording changes in three places, because ClickUp is read
with the person's own token and not through a connection: "Reading the tasks also uses about two
calls per task brought in on your tracker connection, which may have its own daily limit." becomes
"Reading the ClickUp tasks also takes about two requests per task brought in, sent to ClickUp
with your own API token, which has no daily limit."; "read by id through the same connection" becomes "read by id from ClickUp with the
same token"; and "Apart from the request for this project's repository list (it carried the
project id) and a test call with made-up data" gains "the reads from ClickUp with your token,"
before "and a test call".

**Only `Proceed` on this screen is the yes.** A yes typed earlier, or in Other text, is not a yes:
ask again. The screens allowed besides these two are listed in What the person sees.

**Setup mode offer.** Asked only when the plan is large, the door is present and `status` showed no
mode of this project in effect and none open for another project. It comes after `Proceed` and before
anything is collected, as one `AskUserQuestion` with its own yes:

- Header `Setup mode`; question: "This run is large. Setup mode raises the number of writes this project may make, and the number of placement cards (the links between new cards) it may create, for one project and a fixed number of days. It does not raise model spend. It ends on its own, you can close it at the end, and it needs the admin role on the project. Open it with <days> days, <writes> writes<, <per_day> placement cards a day and <per_month> a month>? The call carries the project id, the days, the writes and the caps."
- `Open it` — "Open setup mode with these numbers, then continue the run."
- `Not now` — "Open nothing. The run continues at the daily budget over several days."

Size the proposal from the plan: `days` from the units over the daily budget when it is known, at
least 1; `writes` the plan's units at most, with room for retries; the placement caps from the cards
the run will write (a document, task, pull request and repository file each become one card), per
day and per month, the month never below the day. Only the exact words `Open it` call
`mcp__Fairmind__Brain_setup_mode` with `action: "open"`, `project`, `days`, `writes`, and the two
caps; an answer in Other text is not a yes. Then call `status` once and keep what it says for
Phase 6. A refusal is said in one plain line and the run goes on at the daily budget:

- `Access denied`: setup mode needs the admin role on the project; ask a project admin to accept it.
- `configuration_unset` or `disabled`: setup mode is not enabled for the workspace; ask FairMind.
- `invalid_request`: the numbers are above the workspace's ceilings; offer smaller ones once.
- `already_open`: a mode is already open; read it with `status`.
- `openings_exhausted`: the workspace has used up its setup mode openings; ask FairMind.
- `other_project`, `operator_window`, `not_open`: the mode belongs to someone else or is not open;
  change nothing.

**After `Proceed`, and only then,** save what was approved, so that the next run can offer it
again. Run this once, with the tracker's name or `none`, one `--root` for each project, list or
epic picked (on the ClickUp token path the same `<kind>:<id>` text that `clickup_read.py list --root`
took), and one `--document` for the link of each ticked page. For the repositories:

- `All`: neither `--repo` nor `--undecided` (every repository chosen).
- `Let me choose`: one `--repo` for each selected repository; the rest are declined.
- `Same as last time`: one `--repo` for each `same_as_last_time` entry and one `--undecided` for
  each `new_repositories` entry, so that a repository the person has not seen yet is offered
  again. When `same_as_last_time` is empty, pass just the `--undecided` ones: nothing is chosen.
- `None`, or Phase 0 left no `ok` row: `--no-repositories`.
- Q4 not asked because the pull request door is absent or predates cards, or because the probe of
  no `ok` row passed: `--keep-repositories`
  when a selection was saved before (`saved` is not null), otherwise one `--undecided` for every
  `ok` row (nothing chosen, nothing declined).

A repository this run could not see keeps its saved state, chosen or declined. When Phase 0 wrote
no `repositories.json` there is nothing to save against: save nothing.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_selection.py save --project <project_id> --repositories <work>/repositories.json [--repo <owner/name> ...] [--undecided <owner/name> ...] [--no-repositories | --keep-repositories] --tracker <name|none> [--root <id> ...] --days <N> [--document <link> ...]
```

Any answer other than `Proceed` saves nothing.

**One yes covers the plan. It comes before any document body or comment is collected and before
any write to the brain; with no yes, nothing more is collected and nothing is sent.** What the
inventory read in Phase 2 is the only thing read without it, and the test calls of Phase 0 carry made-up data.
No repository file is read before it: `repo_docs.py read` runs in Phase 5.

## Phase 4: Collect

The person sees one line: "Reading what you approved…"

1. **Pages.** First, **check every to-send page row that carries a `version`** (`new`, `changed` or
   `retry`, not `git: true` and not `local: true`, from `estimate.json`) before anything reads it:
   the page check of Phase 5 (its call shape with no `title` and no `summary`), with the row's
   `version`. It writes nothing and charges nothing; send several in parallel in one turn. Only
   `summary_needed: false`, explicitly false, beside
   `changed` or `absent`, changes what follows: a card of the same link, in this project or another,
   already holds a summary of this version, so the page is **not read and not summarised**. Write its
   `file` as `{"link": <the row's id>, "source": <the row's source>, "version": <the row's version>,
   "copy": true}` and nothing else; Phase 5 sends it in the copy form. A `duplicate` page is not
   read either, and its `file` is written the same way: a file left from an earlier run holds an
   older version, and Phase 5 would send its words over the newer card. An answer without the key
   (an older server) or with `summary_needed: true`, and a row with no `version`, go to the
   summarisers below. So does a page whose check here fails twice: it is not recorded, and Phase
   5's check of it decides under **At most twice**.

   Summariser sub-agents (`model: "haiku"`) take batches of about 10 of the to-send page rows the
   check did not settle (not `git: true`) from `estimate.json`, which Phase 3 rebuilt from the
   ticked documents. Each writes the row's
   `file` as `{link, source, title, summary}`, and
   `version` (the page's edit marker as the connection reports it) and `source_visibility`
   when the source gives them. A page that is the project's brief is not seeded: it is
   `brain-onboard`'s. A repository file (`git: true`) is left to Phase 5.
   - A row with `local: true` also gets `content`, the whole text of the file, never shortened.
   - The summary is two or three sentences on what the document is evidence for. As a
     precaution, it describes a relative path or a local address in words rather than writing
     it out: the hosting edge in front of the platform refuses some text patterns, and its rules
     can change.
2. **Pull requests.** Collect each selected repository in turn, one call each. The
   collector works for about seven minutes per call: set the Bash tool's
   timeout to its maximum. Run the same command again until its summary says `"complete": true`,
   then go to the next repository.
   `--resume` continues an unfinished collect of the same repository and window (a `--days` window
   that began the day before keeps its first day); the first call of a new run starts over, so a
   pull request changed since the last run is collected again. A `gh` failure for one pull request,
   a 403 on it included, is retried on later calls (three attempts in all) and then listed in
   `not_collected`; an expired login or a rate limit ends the call with exit 3, and `gh` gone with exit 127 (either costs the pull request in
   flight no attempt) and the phase stops. It writes, per pull request, a `card.json` (the facts,
   no text) and a `read.md` (what is read to write the summary; it never leaves the machine):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py collect --repo <repo> --days <N> --out-dir <work>/pull-requests/<owner>__<name> --resume
   ```

3. **References and match.** A pull request that cites a ticket is linked to that ticket's card, and
   its own card names the tickets it cites. An agent finds the references, a script decides which
   stand, and nothing the agent proposes is trusted until a script has found its words in the
   pull request's text and the tracker has confirmed the ticket. A pull request the pass read and found
   citing nothing is sent with an empty list of tickets, which clears the links the brain holds for it;
   one the pass did not read is sent with no list, which keeps them. Run the **reference pass** (a to e)
   only when a tracker was picked and at least one repository's collection report says
   `"complete": true`; the scripts need one such report. With no complete report, skip a to e and
   go to f with `--refs` left out: the tasks get no pull request links from references and the
   pull request cards carry no tickets. With no tracker there is nothing to resolve a reference
   against: skip this whole step and step 4, and the pull request cards carry no tickets. The pass
   reads every `read.md` on this machine, and its output stays in `<work>` and in
   `.fairmind/brain-seed/references.json`, a local file that no call sends. `<tracker>` below is the
   tracker picked in Phase 1, by the name brief 6 lists (`jira`, `clickup`, `linear`,
   `azure_devops` or `notion`): the three scripts that touch the store take it as `--tracker`, and
   what the store holds for another tracker is neither reused nor looked up.
   - a. List the pull requests whose text has no frozen references yet, one `--collected` for
     the report of every selected repository that exists:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_refs.py pending --project <project_id> --tracker <tracker> --collected <work>/pull-requests/<owner>__<name>/collected.json [--collected <work>/pull-requests/<owner>__<name>/collected.json ...] --out <work>/refs/pending.json
     ```

     It prints `pending` and `frozen` counts and `skipped_reports`. A pull request whose
     `read.md` is unchanged since an earlier run, and that was frozen for this tracker, is frozen: its
     stored tickets are reused and no agent reads it.
   - b. Proposer sub-agents (`model: "haiku"`, brief 6 in `references/subagent-briefs.md`), one for
     each batch of `pending.json`, each writes the `proposal` path of every item in its batch. Tell
     each one the project's tracker, the one picked in Phase 1, by the name brief 6 lists. When
     `pending` is 0 there is no batch, but c and e still run, as they do whenever a complete report
     exists: they rebuild `pr-refs.json` from the store, and without it the pull request cards
     would carry no tickets.
   - c. Check the proposals:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_refs.py verify --project <project_id> --tracker <tracker> --collected <work>/pull-requests/<owner>__<name>/collected.json [--collected <work>/pull-requests/<owner>__<name>/collected.json ...] --proposals <work>/refs --tasks <work>/inventory-tasks.json --out-dir <work>/refs
     ```

     It writes `<work>/refs/verified.json` and `<work>/refs/lookup.json`, the ids it could not
     find in the inventory, by tracker. A proposal whose words are only a bare number is refused
     like the other unbacked ones (`span_is_bare_id`).
   - d. When `lookup.json` lists any id, one cited-ticket reader sub-agent (`model: "haiku"`, brief 7)
     reads each of them by id through the tracker's connection, with its parent chain, and writes
     `<work>/cited-tasks.json`; an id the tracker does not hold is simply absent. When it lists
     none, skip d and leave `--cited` out of e and the second `--tasks` out of f.
     **On the ClickUp token path** (the tracker is `clickup` and the token is ready) a script
     replaces brief 7:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py get --lookup <work>/refs/lookup.json --mapping .fairmind/brain-seed/clickup-mapping.json --project <project_id> --out <work>/cited-tasks.json --vocabulary <work>/clickup-vocabulary.json --tasks <work>/inventory-tasks.json --raw <work>/clickup-tasks.raw.json
     ```

     Timeout and rerun as for `list`. It reads each cited id (a permanent id or a custom task id)
     and its parent chain. An id ClickUp does not hold is `absent`, not an error. A parent whose
     type the mapping lacks only leaves its row without a kind (`parents_without_kind`): nothing is
     rerun for it. The statuses and types `get` met join `<work>/clickup-vocabulary.json`: `map`
     them as in Phase 2 before `read`, so that `read` finds them decided. `not_read` entries are
     named in one plain sentence and the run goes on. Exit 4: as Phase 1 says; with a ClickUp
     connection in the session brief 7 reads the ids; without one delete `<work>/cited-tasks.json`
     if it exists and treat the references as not read, as below.
   - e. Freeze what stands:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_refs.py freeze --project <project_id> --tracker <tracker> --verified <work>/refs/verified.json [--cited <work>/cited-tasks.json] --repositories <work>/repositories.json --out <work>/pr-refs.json --summary-out <work>/freeze-summary.json
     ```

   - f. Turn the references into links on the tasks. The call reads `<work>/pr-refs.json`; leave
     `--refs` out when the pass did not run, and name `cited-tasks.json` only when it exists:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_links.py match --tasks <work>/inventory-tasks.json [--tasks <work>/cited-tasks.json] [--refs <work>/pr-refs.json] --out <work>/tasks.json
     ```

   `pending`, `verify` or `freeze` exiting 3 **with a summary on stdout** that lists `errors` still
   wrote everything it could: the pull requests named there keep the tickets they had and are
   looked at again next run. Say, in one plain sentence, "Some pull requests could not be checked
   for ticket references this time; they keep the tickets they had and are checked again next
   run.", and go on. Any other failure of those three (exit 2 or 5, or exit 3 with only a message
   on stderr) wrote nothing you can trust: do **not** stop the run. Delete `<work>/pr-refs.json`
   and `<work>/cited-tasks.json` if they exist, go to f with `--refs` and the second `--tasks`
   left out, and say in one plain sentence "Ticket references could not be read this time, so
   pull requests and tickets are not linked by them in this run." Keep each summary's `dropped`
   counts (references the pass refused) for Phase 6.

4. **Tasks.** First empty `<work>/tasks/parts` (the work folder keeps the files of earlier runs, and
   a reader that does not write a file again would otherwise leave last run's text in its place). Reader sub-agents (`model: "haiku"`, batches of about 20 rows of `tasks.json`) read
   each row of `tasks.json` and its comments through the tracker's own connection, and leave
   what they read, as it is, in `<work>/tasks/parts/<source>-<id>/` (the fields in a `ticket.json`,
   each text in a plain text file of its own) following `references/tracker-<tracker>.md` and the
   brief in `references/subagent-briefs.md`; give the sub-agent the absolute path of this skill's
   directory, since its working directory is the repository root. `tasks.json` holds the inventory rows
   (the tasks in the window and every task of a picked epic) and the cited tickets with their parent chain, whatever their date and wherever they
   live. The readers read every row, with its comments, not only the ones to send, because the brain
   can be asked about a task only once its version, which covers its comments, is known; all of them
   are shaped, checked and sent.
   **On the ClickUp token path** (the tracker is `clickup` and the token is ready) a script reads
   the ClickUp rows instead of the reader sub-agents, from `tasks.json` into the same directories,
   with the text copied exactly as ClickUp returned it (comments with their replies, oldest
   first). Empty `<work>/tasks/parts` first, as above, then:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py read --tasks <work>/tasks.json --mapping .fairmind/brain-seed/clickup-mapping.json --project <project_id> --out-dir <work>/tasks/parts --vocabulary <work>/clickup-vocabulary.json --raw <work>/clickup-tasks.raw.json
   ```

   Timeout as for `list`, also for the `--only` call below. `read` downloads only the tasks the
   raw file lacks and then makes about one request for each (its comments). If a `read` is
   killed before it prints its summary, empty `<work>/tasks/parts` and run the first command
   again for all the rows: never assemble the set it left, because some of its tasks are missing.

   Exit 3 with `unmapped` or `not_read` still wrote every other task: a task whose status or type
   (or its parent's type) the mapping lacks gets no directory. `map` the new names as in Phase 2,
   then run `read` again for the rows `unmapped` listed only, one `--only <id>` for each, and say
   in one plain sentence which tasks remain unread:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/clickup_read.py read --tasks <work>/tasks.json --mapping .fairmind/brain-seed/clickup-mapping.json --project <project_id> --out-dir <work>/tasks/parts --vocabulary <work>/clickup-vocabulary.json --raw <work>/clickup-tasks.raw.json --only <id> [--only <id> ...]
   ```

   Exit 4: as Phase 1 says; here it still prints a summary naming the tickets it wrote
   and the ones it did not reach. Empty `<work>/tasks/parts` again, so that one task is not
   copied by a script and its neighbour by a model, and hand every ClickUp row of `tasks.json` to
   the reader sub-agents when the session has a ClickUp connection; without one, leave the ClickUp
   rows out of this run with one plain sentence (`merge` may then exit 3 for them, which is
   expected). Rows of another tracker in `tasks.json` still go to reader sub-agents. Then pass each
   account in the `agent_accounts` that the last `map` of Phase 2 printed to `shape` below (for
   ClickUp a `<login>` is the numeric account id; with none, `shape` takes no `--agent-account`).

   Then a script writes the raw tickets from those directories, so that no text is typed into
   JSON by a model, a second turns them into card skeletons and reading files, and the matched
   links are added to the skeletons (`merge` also reads the pull request addresses written in each
   ticket's description and comments; it prints an `overflow` list of links cut at the door's limit
   of 50, which Phase 6 reports, and `--summary-out` keeps that summary for the report):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_card.py assemble --in-dir <work>/tasks/parts --out-dir <work>/tasks/raw
   ```

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_card.py shape --in-dir <work>/tasks/raw --out-dir <work>/tasks [--agent-account <login> ...]
   ```

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/ticket_links.py merge --tasks <work>/tasks.json --dir <work>/tasks --summary-out <work>/merge-summary.json [--alias <old owner/name>=<current owner/name> ...]
   ```

   A task that quotes a pull request under a repository's former `owner/name` stays an open link,
   because the address names a different repository from the one imported. When this run imports
   pull requests, after `merge` take the `owner/name` of every
   `https://github.com/<owner>/<name>/pull/<n>` in the `pull_request_urls` of
   `<work>/tasks/*/card.json` that is not the `repo` of a row of `repositories.json` (compared
   without regard to case), and resolve each once with
   `gh api repos/<owner>/<name> --jq .full_name` (GitHub answers a renamed or transferred
   repository with its current name). If any answers a different name, run `merge` again with one
   `--alias <owner/name>=<current owner/name>` for each: it rewrites the links it wrote. A
   repository for which `gh` exits non-zero (not signed in, or a private or deleted repository,
   which answers 404 and prints the error instead of a name) gets no alias, and its links stay
   open. The scripts make no network call of their own. An alias reaches a task only when the task
   is sent in this run (a new task, or one whose text changed): the brain compares a task by its
   text, not by its links, so a task it already holds keeps the links it was sent with.

   A task's version is computed from the text the reader copied. A copy is not guaranteed to be
   identical from one run to the next, and a copy that differs changes the version: the check then
   answers that the task changed, and it is summarised and written again although the tracker did
   not change. Say so in the report when it happens. This holds for a sub-agent's reads through a
   connector only: the ClickUp token path copies the same text every run. A pull request's text comes from `gh` through
   a script and does not have this limit.
   `assemble` exit 3 lists in `not_assembled` the directories it could not read (a text file
   missing, a text typed into the JSON): they are left out. `shape` exit 3 lists in `not_shaped` the tasks that cannot become a card (a list over the
   door's limit, a kind it does not know): they are left out. `merge` exit 3 lists the skeletons it
   could not read or left as they were; the others are merged. Say either in one plain sentence.
5. **Plan.** The plan that is sent from takes the shaped tasks, which carry their versions:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py plan --project <project_id> --work <work> --repositories <work>/repositories.json [--documents <work>/documents.json] [--documents <work>/repo-documents.json] [--tasks <work>/tasks/shaped.json] [--collected <work>/pull-requests/<owner>__<name>/collected.json ...] [--refs <work>/pr-refs.json]
   ```

   Name only what this run wrote: `documents.json` when pages were ticked, `repo-documents.json`
   when repository files were asked, `tasks/shaped.json` when tasks were read, `pr-refs.json` when
   the reference pass wrote it (it puts the tickets each pull request cites into that pull
   request's check, so the brain finds out when only the tickets changed), and one
   `--collected` for the report of every selected repository that exists (Phase 2 deleted the
   reports of earlier runs, so these are this run's; the card folders and staged cards stay).
   When `plan` prints a non-empty `skipped_reports`, say in one plain line, before the
   send: "Pull requests of <repo> were not fully collected and are left out of this run; the next
   run collects them." Steps 3 and 4 do not run when no tracker was picked.

6. Read `plan.json`'s `summary` again. If the final units exceed the estimate the yes covered, show
   the new number and ask again with one `AskUserQuestion`, `Proceed` or `Cancel`, each described
   as on screen 2; otherwise go on. **Never collect or reshape between that point and
   the send** (Phase 5's read of a repository file, and of a page whose copy found no summary in
   the brain, is neither).

## Phase 5: Send

Order: documents, then tasks, then pull requests. A pull request's own links reach a task only
once the task is in.

Start by printing the `send` summary's `text`. A stop reaches the person only through the `done`
summary of Phase 6.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py summary --plan <work>/plan.json --stage send
```

Every pull request call (the check, the write, `reference_card.py erased` and `stage`) takes
`repository` and `git_remote` from the row `next` returned, never from Phase 0: the rows of one plan
belong to different repositories.

**Which route.** Documents (pages and repository files) are sent by this session, one row at a
time, by the loop under "The session route" below: run it for `document` first. Tasks, then pull
requests, are sent by the script route that follows. The session route is also the way to send
them when the script cannot use the project's FairMind connection.

### The script route: tasks, then pull requests

`seed_send.py` does for a task or a pull request everything the loop below does with the brain: the
check, the erasure check, the write with the staged file's arguments exactly as they are and its
digest, the verification, the at-most-two sends (a send that arrived altered is checked again and
sent once more even when the brain then calls that version unchanged: the re-send is the repair),
the stops, and the record in the ledger (the ledger
stays the only thing that decides what is left). It reads the address and key of the project's own
FairMind MCP entry, the way the plugin's hooks do, and never prints, logs or stores them. What it
does not do is write words: a changed item with no staged card is handed back, not recorded, and
card-writer sub-agents write it. Run it once for `task`, and when it is done once for
`pull_request`, in chunks so the person can be told how far it got. A long run goes in the Bash
tool's background mode; its notification carries the summary:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_send.py send --plan <work>/plan.json --kind task --limit 200
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_send.py send --plan <work>/plan.json --kind pull_request --limit 200 [--refs <work>/pr-refs.json]
```

(`--refs` for a pull request only, when `<work>/pr-refs.json` exists.) It prints one JSON summary:
`route`, `kind`, `processed`, `outcomes` (counts), `needs_words`, `needs_out` (the file listing the
items that need words), `stopped`, `attention`, `notes` and `remaining`. Read the exit code first:

- **2**: the command line or the plan was not usable; nothing was called. Fix the call; do not
  fall back.
- **5**: the project has no MCP entry a script can use (no address, or a key it cannot resolve, such
  as a connector that signs in with OAuth), or the platform has no file lock. Nothing was called or
  recorded: send this kind by the session route below, and say so in one line.
- **6**: another sender holds the ledger. Tell the person; start nothing else.
- **3**: the run stopped (`stopped` is the recorded outcome: a throttle, an unset quota or "Access
  denied" are stops that the ledger now holds; `unreachable` means the brain did not answer six
  calls in a row and `ledger` that the ledger could not be written; neither is an outcome, and the
  rows not recorded wait for the next run). Go to Phase 6 and say which in the report.
- **0**: done, or the limit was reached.

Then, for each chunk, until `needs_words` is 0 and `remaining` is 0, or the run stopped:

1. If `needs_words` is not 0, `needs_out` lists the items. For each item whose `read` file exists
   and whose `words` file does not, have a card writer (`model: "haiku"`, brief 5 in
   `references/subagent-briefs.md`) write `words` from `read`; hand it a list of items, each with its
   reading file and its `words.json` path, as the brief says. Then stage them:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_send.py stage --plan <work>/plan.json --needs <needs_out> [--refs <work>/pr-refs.json]
   ```

   It prints `{"staged", "problems", "missing_words", "no_reading"}`. `no_reading` rows are
   recorded `failed` by the script (there is nothing to read). For each entry of `problems` (the same
   refusals the session route's staging command gives: a text over its limit, a
   credential-like string, a point naming a file the reading file does not show, copied words) hand
   the item and its problems back to a card writer, then stage again with the same command; at
   most two rounds per item. An item still in `problems` or in `missing_words` after that is
   recorded `failed` with the problem as its detail, exactly as step 4 of the session route records
   an outcome (`seed_ledger.py record ... --outcome failed --answer-json`, the detail on stdin).

2. Run `send` again for the same kind. The staged items are offered at their check again and sent;
   the script asks the brain again just before each write. The script counts the hand-backs itself
   (per row, in the ledger, for the run): an item it would hand
   back a third time (an erasure that kept landing) it records `failed` with "words could not be
   staged after two rounds". You do not count them.

While a `send` is running, run neither `seed_send.py stage` nor `seed_ledger.py record` (the
ledger has one writer at a time; `stage` exits 6 while a sender holds the lock): wait for the
summary first.

Everything else in the summary reaches Phase 6 as it does from the session route: pass each code
of `notes` as a `--note` of the `done` summary, and say each `attention` entry's `text` as one plain
line after the report (an erasure that landed during a write is the usual one: someone with the
editor role on the project has to check that card and remove it by hand). The script's rows are recorded
exactly as the session route records them, so `summary --stage done` and a later run read them the
same way.

### The session route: documents, and tasks and pull requests when the script cannot use the connection

For each kind, in a loop:

1. `python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py next --plan <work>/plan.json --kind document`
   (then `task`, then `pull_request`). It returns one row, `remaining` (the rows of that kind not
   recorded yet, the one returned included) and `stopped`.
2. A page row: send its `file` as the first call shape below. A repository file row (`git: true`):
   file it as the repository file procedure below says. A task or pull request row is always
   offered at `check`, whether or not a card is staged and whether or not the ledger holds it as
   sent: **its first call in a run is its check, never its write**. A task or a pull request goes
   through up to three steps:
   - **`check`.** Call the door with the row's `check` arguments (a pull request's include
     `tracker_items`, the tickets it cites, when the plan was made with `--refs` and the references hold an entry for it: `[]` when that entry cites none, which clears the links the brain holds; no key when there is no entry, which keeps them),
     `project`, `agent` (always `"brain-seed"`) and, for a pull request, the row's `repository` and
     `git_remote`; no other key and no `summary`. Read the
     answer through the table below. `source_changed: false` means the brain already holds this
     version: **record `unchanged` at once**. Nothing was stored, so there is nothing to verify, and
     nothing is read or summarised. A row that `next` returns with `repair: true` is the exception: an earlier send of this version arrived altered or its outcome was never seen, so the brain's `source_changed: false` is read as changed, the row goes through the erasure gates below and its staged card is sent again, and `unchanged` is never recorded for it. `source_changed: true` means the card has to be written. The
     answer may carry `erased_participants`, every login erased in the project on this platform
     (not only this item's), and `erased_participants_truncated: true` when that list was cut
     short. When it is truncated, some erased people are unknown to you: **do not summarise** the
     item, record it as `failed` with the reason "the brain's list of erased people is cut short",
     and continue. Otherwise run the check below for every changed item, whether or not the
     answer carries `erased_participants` (the server leaves the key out when nobody is erased,
     and the check is also what compares a staged card's project, repository and remote with the
     current ones). It finds which of the listed logins took part in this item:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/reference_card.py erased --card <card> --staged <write> --project <project_id> [--repository <repository> --git-remote <git_remote> [--refs <work>/pr-refs.json]] [--erased <login> ...]
     ```

     (`<card>` and `<write>` are the row's `card` and `write`; one `--erased` per login of
     `erased_participants`, none when the answer carries none; leave `--staged` out when the row
     has no staged card; `--repository`, `--git-remote` and `--refs` are for a pull request, which is
     staged under them). It prints `{"affects": [...], "stale": true|false}` (with `--refs`, also `"stale_reason": null|"redaction"|"binding"|"tickets"`), compared without
     regard to case. `affects` is the erased logins that took part and that `read.md` has not
     been redacted for yet; `stale` is whether the staged card was made before `read.md` was
     redacted for all of them, or was staged for another project, repository or remote, or (with `--refs`) with other tickets than the
     references give now. `stale_reason` says which: `redaction` and `binding` are handled below; `tickets` alone means the words
     are still good: stage again with `--restage` from the existing `words.json`, with no redaction and no new summary.
     **Any non-zero exit of this
     command means the erased people are unknown: do not send, record the item `failed` with the
     script's message, say so in the report, and continue** - never read a failure as an empty
     `affects`. Only when `affects` is not
     empty or `stale` is true with a `stale_reason` other than `tickets`, a staged card is stale whatever the row says: it was written from words that may
     include an erased person's, so treat the row as having no staged card, redact, write the
     words again and stage with `--restage` (next point); a login already redacted is not redacted
     again. An item none of those people took part
     in is handled as if the answer named nobody: if the row says `staged: true`, a card for this
     version already exists, so go to the **write** step and do not summarise again; if it does
     not, write the words and stage the card (next point).
     A check can also answer `source_changed: true` for a version the card already holds, when the
     card still lists verbatim cards of the earlier contract that could not be erased: the write
     that follows retries the erasure and spends one unit.
     A changed item's answer may also carry `summary_needed`. Only `summary_needed: false`,
     explicitly false, changes what follows: a card of this item, in this project or another, already
     holds a summary of exactly this version that this project may take, so the item is **not read
     and not summarised**; it is staged and written in the copy form below, and the brain fills
     `summary` and `rationale_points` itself. An answer without the key (an older server) or with
     `summary_needed: true` is handled as before: the item is read and summarised. The copy form is
     never used for an item an erased login took part in (`affects` is not empty): that item is read,
     redacted and summarised as below.
     A settled row, one the ledger holds as sent, comes back here every run: the check is free of
     charge, and it is how a card removed from the brain is found again. The price is time, not
     quota: every run reads every row of `tasks.json` with its comments to know its version, and
     makes one check call per task and pull request, so a second run over a large window is not
     quick even when nothing changed. The reference pass adds little to a re-run: a pull request whose
     text is unchanged is not read by a model again, but the cited-ticket reader still runs whenever
     stored references point to tickets outside the chosen roots, reading them by id.
   - **Words and staging**, only for a changed item with no staged card, or whose staged card is
     stale because of `affects`, or whose staged card is a copy that may not be sent. An item whose
     check answered `summary_needed: false` takes the copy form below instead. The reading file is
     `read.md` beside the row's `card`. When `affects` is not empty, first
     remove what those people wrote from it, before anything reads it:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py redact --read <dir>/read.md --erased <login> [--erased <login> ...]
     ```

     (a task: `ticket_card.py redact`, with the same arguments). Then read it and write
     `<dir>/words.json` - yourself, or by handing `read.md` (or a list of items, each with its
     `read.md` and its `words.json`) to a card-writer sub-agent
     (`model: "haiku"`, brief 5 in `references/subagent-briefs.md`). Then stage:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/reference_card.py stage --card <card> --input <dir>/words.json --out <write> --agent brain-seed --project <project_id> [--repository <repository> --git-remote <git_remote> [--refs <work>/pr-refs.json]]
     ```

     `<card>` and `<write>` are the row's `card` and `write`; `--repository` and `--git-remote`
     are required for a pull request and have no use for a task; `--refs` is for a pull request
     only, passed when `<work>/pr-refs.json` exists. It joins the words to the facts, so no field is
     re-typed. Exit 2 lists every problem (a text over its limit, a credential-like string, a point
     naming a file the reading file does not show, words copied from the reading file, twelve in a
     row or a whole short paragraph of it): fix
     `words.json` and stage again; the same exit also says when the row's `read.md` is missing,
     which no change to `words.json` clears: record the row `failed`; nothing was written, so no flag is needed (`--restage` is only
     for replacing a card that was already staged and is stale, as after an erasure or a rebind). Its output
     carries `expected_sha256` and `card_bytes`. A `warning` that the card is over the door's size
     limit means the door will refuse it: send it anyway and record the refusal, never trim it by
     hand. **A changed item is summarised once per run**: after staging, a failed or interrupted
     write sends the staged file again, and a later `next` offers the row at `check` with
     `staged: true`, whose answer is read as above (an erasure since then makes the card stale).
   - **Copy form**, only for a changed item whose latest check answered `summary_needed: false`,
     with `affects` empty and no card with words staged. Nothing is read and no words are written; stage:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/reference_card.py stage --card <card> --copy --out <write> --agent brain-seed --project <project_id> [--repository <repository> --git-remote <git_remote> [--refs <work>/pr-refs.json]]
     ```

     The card carries every fact of the item and no `summary` and no `rationale_points`; its output
     carries `"copy": true` with `expected_sha256` and `card_bytes`. A row `next` returns with
     `copy: true` is sent only while the latest check says `summary_needed: false`; when the answer
     is `true` or has no `summary_needed` (the card that held the words is gone, or the server is
     older), write the words as the next point says and stage with `--restage`.
   - **Ask again, just before the write.** An erasure can land while the words were being
     written. Unless the check above was answered with nothing summarised or staged since it
     (a staged card sent straight from it) and this is the first send of the card, call the door again with the row's `check` arguments
     and read the answer as above, `summary_needed` and `reference_card.py erased --staged <write>` included, with the same `--project`, `--repository` and `--git-remote` (and `--refs`, when `stage` got it). A
     failed call, a truncated list or a failed `erased` means: do not send, record `failed`.
     When `affects` is not empty or `stale` is true, redact, write the words again, stage with
     `--restage` and ask again, at most twice more; then record `failed`. Only an answer with
     `affects` empty and `stale` false permits the send. Every send of a staged card goes through
     this step, a re-send or a retry after a failure included (`resend`, `payload_digest_mismatch`,
     a bare `… failed`, an edge `502`, `503` or `504`).
     A write in the copy form that the brain refuses with `code: summary_required` found no card
     holding the summary any more: nothing was stored or charged, and it is neither a failure nor a
     `refused` row. Redact when `affects` asks for it, read the item, write the words, stage with
     `--restage`, ask again and send the full card. That refusal does not count toward the two sends
     below.
   - **`write`.** Read the `write` file and call the door with its `arguments` exactly as they
     are and its `expected_sha256` beside them. The digest is the one of the file just read, or the one
     `stage` printed: after a `--restage` it is never the row's, which was computed from the card
     that has since been replaced.
3. **Verify before recording.** Read each write answer through the table below. A stop is recorded
   at once, and is never verified or sent again. Any other answer to a write is checked before
   anything is recorded, a success included. An answer whose `code`
   is `payload_digest_mismatch` or `expected_sha256_malformed` (those two values, not any other
   `code`) goes to `verify --code`; leave `--received` out when the refusal carries none. Any other
   answer whose `received_sha256` is a string goes to `verify --received`, which checks that the
   brain received the staged card byte for byte:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py verify --plan <work>/plan.json --key <key> --received <received_sha256>
   ```

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py verify --plan <work>/plan.json --key <key> --code <code> [--received <received_sha256>]
   ```

   It writes the check into the ledger and prints what it decided, with a `next`. Do what `next`
   says; the exit code (0 when the digests are the same, 1 when they differ) is not for this
   decision:

   - `record`: the answer stands; record it from the table.
   - `resend`: ask again as above, then send the same staged file once more, verbatim, and read that answer through the
     table from the top.
   - `record_differs`: the brain's digest differed from the file's the same way twice, which is
     not noise in the relay. Record the answer from the table; the row is marked, it is not sent
     again run after run, and Phase 6 lists it for a person to look at.
   - `record_failed`: it differed again, another way. Record `failed`; a later run sends it again.
   - `record_digest_refused`: the re-send was refused with a `code` too. Record `digest_refused`.
     Nothing was stored or charged, and the next run sends the row again, even when an earlier
     run stored the same content. It never ends as `failed` or `refused`: the brain's `refused`
     is not sent again.

   The ledger keeps every check, so the decision survives a compacted context, and it writes the
   mark, the details and the number of re-sends onto the record itself. It refuses `digest_refused`
   unless the latest check said so.

   **An erasure during the write.** After the digest is settled and before anything is recorded, read the
   write answer's `erased_participants`. The brain leaves an erased person out of the card's
   participants and says so there, so a login that the last check of this item did not list is an
   erasure that landed after it: the card was written from words that may include that person's.
   Record the item `failed` with the detail "an erasure landed during the write", never `written`,
   and add one line to the report note for someone with the editor role on the project: the card for this item holds words
   written before the erasure, an erasure by login can no longer find it (it no longer names that
   person) and a later check will call it unchanged, so it has to be removed by hand (a platform action; this plugin has no door that removes a card). This is the
   window that remains once the check was repeated just before the send; the brain cannot be made
   to refuse such a write from here. A deleted account (`ghost`) is never listed in that answer,
   so its words in a card written inside that window cannot be detected afterwards.

   **An answer without a digest.** A `received_sha256` of `null` (the server could not put that
   payload in canonical form), or none on a refusal or an error answer, leaves that one answer
   unverified: record it from the table. An edge page or a transport error is not the door's
   answer and says nothing either way.

   What verification covers: a task's whole `item`; a pull request's whole `pull_request`. Not
   `project`, `repository`, `git_remote`, `agent` or `agent_accounts`. Documents are not verified:
   their door answers with no digest. Verification detects a relay that changed the payload and
   repairs it by sending again; the changed version stays stored until that re-send lands, and
   the re-send can spend one more unit.

   **`expected_sha256`** makes the door check the digest before it writes, so a relay that
   altered the payload is refused with a `code` and the digest it received. Send the staged file's
   value on every write, the re-send included.
4. Then record the answer, as step 3 settled it: a success is never recorded before its digest
   matched, or before it turned out that it cannot be verified. Server text goes in on stdin as
   JSON, never inside a quoted shell argument:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py record --plan <work>/plan.json --key <key> --outcome <outcome> --answer-json <<'FM_ANSWER_END'
   {"detail": "<first error>", "budget": <the answer's budget object>, "unresolved": <its unresolved_links, as returned>}
   FM_ANSWER_END
   ```

   Leave out the keys the answer does not have. `unresolved_links` goes in as the door returned it:
   a list of links or of objects with a `url`.
5. Repeat until `rows` is empty or `stopped` is set. After a context compaction, `next` recovers
   the position: the ledger, not memory, says what is left.

**At most twice.** A document, or the write of a task or a pull request, is sent at most twice in
a run, whatever the reason: a `resend`, a `retryable` answer, an HTML 5xx page, an
`upstream connect error`, a message that says it failed. When the second attempt calls for a
third, record `failed` instead. A check call is not a write: a check that fails is made once more,
and then the row is recorded `failed`. A write in the copy form refused with
`code: summary_required` is not one of the two either: it stored and charged nothing.

The calls, in this shape:

```
mcp__Fairmind__Brain_tell(
    intent="document",
    repository=<the row's repository>,
    git_remote=<the row's git_remote>,
    project=<project_id>,
    title=<the document file's title>,
    source={"url": <its link>, "version": <the document file's version>, "system": <notion|confluence|google_drive|github>, "visibility": <the document file's source_visibility>},
    summary=<its summary>,
    agent="brain-seed",
)
```

A document with a `version` is **checked first**: the same call with no `title` and no `summary`
(a call that carries either is a filing, and one with a title but no summary is refused) answers
`duplicate` (the brain holds this version: record `unchanged`, send nothing more), `changed` or
`absent` (send the call above). Leave `system` out for a web page, and `version` and `visibility`
out when the file has none. A row with `local: true` (a file with no address) is the only case that
passes `content=<the file's content>` in place of `source`, and it has no check.

**A page row `next` returns with `copy: true`** has a `file` with no title and no summary. Its
check answering `duplicate` is `unchanged`; `changed` or `absent` with `summary_needed: false`
sends the **copy form**: the call above with `use_shared_summary=True` in place of `title` and
`summary`, and the brain files the card from the summary it holds. When the check answers
`summary_needed: true` or without the key, or the copy write is refused with
`code: summary_required`, the card that held that summary is gone: have a summariser (brief 3)
write the row's `file` with words now, then send the call above.

**A repository file** (`git: true`) is filed exactly as `brain-add-document` files a working
artifact: as a reference card with a git location. `next` hands out the arguments, so no row, no
digest and no card is typed. Call `Brain_tell` with the row's `check_arguments`, exactly as they
are. It answers `duplicate` (the brain holds this blob: record `unchanged`; the file was never
read), `changed` or `absent` (file it). To file it, first read the file here, at the commit the
row names, into the row's `read_path`. The copy stays on this machine and is never sent, and the
script removes any earlier copy itself. If the read exits non-zero, record the row `failed` and go
on:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/repo_docs.py read --repo <the row's repo> --path <the row's path> --commit <the row's commit> --out <the row's read_path>
```

Then a summariser sub-agent (`model: "haiku"`, brief 3 in `references/subagent-briefs.md`), given
the `read_path` and the row's `file`, writes `{"title", "summary"}` into that `file` and nothing
else; the same rules for the summary apply as for a page. Call `next` again: the row now carries
`write_arguments`. Call `Brain_tell` with those, exactly as they are. If it carries `write_error`
instead, the write form refused the summary: have the same summariser rewrite it once within the
limit the error names, call `next` again, and on a second `write_error` record the row `failed`
with that error.

The card is confirmed on arrival. Record the answer in the table below: `confirmed` is `written`,
`duplicate` is `unchanged`, `proposed` (a person had confirmed the card, so the change waits for
review) is `revision_proposed`. A repository file is never sent with `content`, `source.url` or `source.document_id`, which the
door refuses for a git location.

```
mcp__Fairmind__Brain_import_tracker_item(
    item=<{"source", "id", "source_version"} of the row's check>,
    project=<project_id>,
    agent="brain-seed",
)
```

```
mcp__Fairmind__Brain_import_tracker_item(
    item=<the staged file's item, exactly>,
    project=<project_id>,
    agent="brain-seed",
    expected_sha256=<the staged file's expected_sha256>,
)
```

```
mcp__Fairmind__Brain_import_pull_request(
    repository=<the row's repository>,
    git_remote=<the row's git_remote>,
    project=<project_id>,
    agent="brain-seed",
    pull_request=<{"source", "id", "source_version"} of the row's check>,
)
```

```
mcp__Fairmind__Brain_import_pull_request(
    repository=<the row's repository>,
    git_remote=<the row's git_remote>,
    project=<project_id>,
    agent="brain-seed",
    pull_request=<the staged file's pull_request, exactly>,
    expected_sha256=<the staged file's expected_sha256>,
)
```

The first of each pair is the check, the second the write. Add `agent_accounts` to a write only when
the person named them (the staged file carries them then). **Every call names `project`; this
skill never files anything company-wide.** A staged card is passed **verbatim**: never shortened,
summarised, reordered or re-spelled. Spelling is identity, and an admin erases by it. Only a row
recorded as a success is treated as sent. A throttled, failed or partial row is sent again on the
next run, from its staged card when there is one, so a changed item is never summarised twice. A
row recorded `refused` is **not** sent again until its content changes or the person asks for a
plan made with `--all`. The same holds for a refused document. That hold is for `refused` alone,
the brain's own refusal: an `edge_refused` row never reached the brain, so the next run sends it
again and the refusal clears by itself once the edge lets the request through.

Read the table by precedence. First, whether the door answered at all: a failed call whose error
is an HTML page from the hosting edge is not the door's answer, so a `403` inside it is never a
stop. Then a stop (`status: throttled`, `key: brain_write_quota_daily`, "Access denied" naming
the write scope, "Access denied" to a call with no repository) or else a repository refusal (any
other "Access denied", to a call that carries a `repository`), then a digest refusal (`code: payload_digest_mismatch` or
`expected_sha256_malformed`, checked with `verify --code`), then a `received_sha256` that differs from the file, then
`code: summary_required` (a refusal that is not a `refused` row), then the other
`status` values, then the `retryable` cases, and only last a message that starts "Brain …
refused". The quota answers carry such a message too, and
recording them as `refused` would skip the whole corpus for good.

| Answer | Outcome to record | Next |
|---|---|---|
| A check with `source_changed: false` | `unchanged` | continue: nothing was read, stored or charged |
| A check with `erased_participants_truncated: true` | `failed` (the list of erased people is cut short) | continue; summarise nothing and write nothing |
| A check, or `reference_card.py erased`, that failed | `failed` (the erased people are unknown) | continue; write nothing |
| A check with `summary_needed: false` (it answers `source_changed: true`), no erased login having taken part | none yet | stage the copy form (nothing is read or summarised); ask again just before the write; then write. A row with a staged card with words sends that card |
| A write in the copy form refused with `code: summary_required` | none yet | read the item, write the words, stage with `--restage`, ask again, then write the full card; nothing was stored or charged |
| A page's copy write refused with `code: summary_required` | none yet | its summariser (brief 3) writes the row's `file` with words, then send it; nothing was stored or charged |
| A check with `source_changed: true` | none yet | stage the card if the row has none, or redact and `--restage` when `reference_card.py erased` finds one of `erased_participants` among the participants; ask again just before the write; then write |
| A write answer whose `erased_participants` holds a login the last check did not list | `failed` (an erasure landed during the write) | see step 3: continue, and note for someone with the editor role on the project |
| `status: ok` with `written > 0` | `written` | continue |
| `status: ok` with `written: 0`, or the door's unchanged answer, to a write | `unchanged` | continue |
| `status: partial` | `partial` | continue; listed for a re-run. A ticket answers it with `retirement_pending` when the card of its earlier kind could not be retired yet; a pull request answers it with `legacy_pending` when review-thread cards of the earlier contract could not be erased yet. Writing again retries both |
| `status: ok` to a Notion page with `status_defaulted: true` | `written` | continue; the page's status word did not map, so the card took the brain's default and keeps the word |
| A page told and answered `proposed` (a new card, or a confirmed card whose change waits for a person) | `written` | continue |
| A repository file answered `confirmed` | `written` | continue |
| A repository file answered `proposed` (a person had confirmed the card, so the change waits for review) | `revision_proposed` | continue |
| A document answered `duplicate` to its write, or to its check | `unchanged` | continue |
| A document answered `conflict` (another document holds the title) | `refused` | continue; held until its content changes |
| `revision_proposed` | `revision_proposed` | continue |
| `not_reproposed` | `not_reproposed` | continue |
| `status: throttled` | `throttled` | **stop the run** with the position recorded; keep the `quota` for the report file; never retry |
| `key: brain_write_quota_daily` | `quota_unset` | **stop**; the `done` summary tells the person |
| `code: payload_digest_mismatch` or `expected_sha256_malformed` | see step 3: do what `verify --code` says | continue |
| `received_sha256` that `verify` finds different from the file | see step 3: do what `next` says | continue |
| `retryable: true` to the write of a task or a pull request | go back to the check of that item (an erasure in flight is the usual cause), follow it from the top and write again (**at most twice**, above), then `failed` | continue |
| `retryable: true` to a document | send the document again (**at most twice**, above; a document has no check form to repeat), then `failed` | continue |
| `status: refused`, or (after the stops above) a message starting "Brain … refused" | `refused`, first error as detail | continue; never edit a file to make it pass |
| A refusal whose message says the call "carries fields this contract has not" (a server older than the plugin), to a check carrying `tracker_items` as to a write | `failed`, the message as detail (the ledger turns a `refused` with this message into `failed` so the next plan sends it again; a check refused this way holds nothing) | continue; tell the person to update the server |
| "Access denied" naming the write scope ("write scope required"), or another 403 whose body names the missing write scope, to any call | `access_denied` | **stop**: the key cannot write; it never names one repository |
| Any other "Access denied" without `retryable`, to a call that carries a `repository` (a page, a repository file, a pull request) | `repository_denied` (the ledger works out the repository from the row `next` offered) | continue: the ledger stops offering that repository's rows this run, and offers a page again under a repository that is not refused; the `done` summary names the refused repository. Never `/fairmind-connect` for it, unless the `done` summary suggests it (every repository was refused and no task went through) |
| "Access denied" without `retryable`, to a call with no repository (a task) | `access_denied` | **stop**: the account lacks the editor role on the project, or the key is bound to another project |
| A failed call whose error is the hosting edge's HTML page with `403` and "Request blocked", in place of the door's JSON answer | `edge_refused`, the row's title as detail | an **edge-filter refusal**: nothing reached the brain, and it is never an authentication failure; never reword the payload; continue |
| A failed call whose error is another HTML error page from the edge (a `502`, `503` or `504`), or a plain-text `upstream connect error` | for a task or a pull request, go back to its check and ask again first (step 3); for a document, send it again; **at most twice** in all, above, then `failed` | continue |
| `{'message': '… failed'}` alone | for a task or a pull request, go back to its check and ask again first (step 3); for a document, send it again; **at most twice** in all, above, then `failed` | continue |
| `status: error` without `retryable` (nothing was written) | `failed`, the message as detail | continue |
| A summariser file that holds `error` and no `summary` | not sent: record the document `failed` with that error | continue |

An edge refusal is recognised by its shape, never by guessing at the payload: the call fails,
and its error carries the HTML error page of the hosting edge in front of the platform, with
`403` and "Request blocked", where the door's JSON answer would be. The request never reached
the brain: nothing was written and no unit was spent. It is not an authentication failure, and
the connection stays up, so the next call goes on as usual. The edge refuses some text patterns
and its rules can change; the payload is never reworded to get past it, since spelling is
identity. Record the refusal with the row's title and go on.

From the first pull request answer of each repository, read `repository.repo_ref_scheme`. When it is
not `code-ingestion` for any of them, the cards are stored, but a read of a file will not find these pull requests:
pass `--note not_code_ingestion` to the `done` summary.

## Phase 6: Report and the Review Queue

Run the `done` summary. It writes the run's report file, with the output of `status` in it, and
prints the only report the person sees. Add a `--note` for each observation made in Phases 0 and
5, one `--collected` for each repository collected, and leave `--collected` out when the pull
request phase was skipped:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/seed_ledger.py summary --plan <work>/plan.json --stage done --collected <work>/pull-requests/<owner>__<name>/collected.json [--collected <work>/pull-requests/<owner>__<name>/collected.json ...] [--extra <work>/freeze-summary.json] [--extra <work>/merge-summary.json]
```

Name `--extra` only for the two summaries that exist (the reference pass and `merge` write them); the report file keeps them under `extras`.

Show its `text` exactly as it comes, as What the person sees describes. Then stop: the person asks
if they want more.

For details, read the report file. Per source: written, unchanged (this run's `unchanged` and `unchanged_earlier`, what earlier
runs sent that has not changed), refused by the brain (`refused`, with the detail;
`refused_earlier` rows are listed with `earlier: true`), refused by the edge (`edge_refused`, by
title), refused for a digest (`digest_refused`, by title), failed (`failed`, with the detail; the next run sends them
again), and not attempted because the run stopped. The budget shown is the last one this run
saw; an earlier run's is not carried over. Then:

- Ticket references (the `extras` of the report file): the references the pass refused (the
  `dropped` counts of `verify` and `freeze`, by reason: words not found in the pull request's text, an id not in the quoted words, words that are only a bare number,
  a ticket the tracker does not hold) and the links cut at 50 on a ticket (`overflow` of `merge`,
  by ticket). Say each in plain words, with the counts.
- Task-to-pull-request links still open (`open_links`): the pull request is outside the window or
  not yet imported. A later run resolves a link only when that pull request is imported by then
  (a wider window); a link that quotes a renamed repository's old name stays open until it is
  given as an `--alias` and its task is sent again, which happens only when the task's text
  changes. Say which links are of that kind.
- The budget left: the last `budget` seen, or the `quota` from a throttle.
- Setup mode: whether one was used (opened here or found open for this project), and what its `status`
  call returned at the end (days left, writes left). Only when this run opened it, offer to close it as the
  one allowed extra question of What the person sees; call `mcp__Fairmind__Brain_setup_mode` with
  `action: "close"` only after a yes. A mode found already open is left as it is.
- How many pages, tasks and pull requests were written in the copy form (no model reading); a write
  answer may carry `copies: {updated, skipped?}`, the cards of other projects the brain brought up
  to date from a summary this run wrote. Say it in one plain line; there is nothing to do.
- How many tasks and pull requests were `verified` or `unverified`, and how many re-sends a digest
  difference called for (`resends`, one unit each at most). A card settled by its check call stored
  nothing and counts as neither.
- What `seed_send.py` returned, when the script route sent tasks or pull requests: each code of
  its `notes` goes in as a `--note` of the `done` summary, as the session route's notes do; each
  `attention` entry (an erasure that landed during a write, whose card someone with the editor role
  on the project must remove by hand) is said as one plain line after the report, with its title; a
  `stopped` of `unreachable` or `ledger` is said in one line (the brain stopped answering, or the
  ledger could not be written; the rows not recorded are sent by the next run). The rows the script
  recorded are in the ledger and counted by the summary like any other.
- The rows whose digest differed from the file's the same way twice (`digest_differs`): they are
  not sent again run after run (a success counts as sent, a refusal is held), so a person looks
  at each one. A `digest_refused` row is not one of them: a refused send costs nothing.
- A pull request with more files than the collector sends carries `file_count`, the real number;
  a `mcp__Fairmind__Brain_ask` file question on any file it sent returns the pull request among
  its references, and a `context` question on the pull request itself answers with what is
  linked to it. A card over the door's size limit was refused and is listed with the others.
- A re-run asks the brain about every task and pull request, for free, and writes only what changed
  and what did not get through. A row the brain refused waits for its content to change; a row the
  edge refused is sent again, and so is one the door refused for its digest. To send everything
  again, the person asks for it and the plan is run with `--all`.

Pages land as **proposals**, so the last step is the person's: open the **Review Queue** in
Studio and confirm or reject each page. A repository file is confirmed on arrival; only the change
to one a person had confirmed waits there, counted in the report as a revision. A task or a pull
request does not wait.

## Resuming after a stop

A repository refused this run (`repository_denied`) is not a stop: its rows wait for the next
`plan`, which offers them again. A stop (a throttle, "Access denied" naming the write scope or to a
call with no repository, an unset quota) is recorded against the plan's run, so
`next` on that plan returns no rows. To resume, once the cause is gone (the budget renewed, the
role granted, the quota set), run the last `plan` command of Phase 4 again with the same inputs
(`--repositories`, both `--documents`, `--tasks`, every `--collected` and `--refs`):
it makes a new plan from the ledger, in which what was sent is `unchanged` (it is asked about again,
for free) and what was not is `new` or `retry`. Collecting and shaping do not run again; their
files, the staged cards included, are still in `<work>`. Then repeat Phase 5. After a context
compaction instead, `next` on the same plan recovers the position.

## Error Handling

- `throttled`, "Access denied" naming the write scope or to a call with no repository, an unset
  quota (`brain_write_quota_daily`): stop the run and never retry; the `done` summary tells the person.
  Edge-filter refusals and a repository's "Access denied" (`repository_denied`) do not stop the run.
- `project_repos.py` and `repo_docs.py`: a repository that cannot be read is listed with its reason
  (`skipped`, `failed`, `truncated`) and the others go on; exit 127 means `gh` is missing and the
  pull requests and repository files are skipped. Say it in one plain sentence.
- `pr_collect.py`: a failure of one pull request (a usage error, a pull request that cannot become
  a card, a card file that cannot be written, a 403 on that pull request) is recorded in
  `not_collected` with the script's message and the run continues; a `gh` failure for one is retried
  on later calls, three attempts in all. A `gh` login that expired, a rate limit (exit 3) or `gh`
  missing (exit 127) stops the pull request phase, since every later one fails the same way. Report
  `not_collected` in Phase 6.
- `clickup_read.py` (the ClickUp token path): exit 3 with `not_read` or `unmapped` in the summary
  wrote everything else; exit 3 with only a message is a local file that cannot be read; exit 2
  stops that call; exit 4: as Phase 1 says. Report `not_read` in Phase 6. The token never appears
  in a message.
- `ticket_card.py`: a task that cannot become a card is listed in `not_shaped` and left out; the
  others are shaped.
- `reference_card.py stage`: exit 2 lists every problem with the card; fix the words and stage
  again, except for a missing `read.md`, which words cannot clear (record the row `failed`); nothing was written, so no flag is needed. Exit 4 means a card is already staged for
  this version: when an erased person took part in the item (`reference_card.py erased`), redact, write the words again and stage
  with `--restage`; otherwise send it. `reference_card.py erased` exiting non-zero: send nothing for that
  item, record it `failed`. A `stage` exit 2 whose problem is about `tracker_items` (more than 50 tickets, a ticket with a malformed source
  or id) is not a problem of the words: do not rewrite them, record the row `failed` with that
  problem as the reason.
- The other scripts: exit 2 is usage, 3 an unreadable input or ledger (a ledger that does not parse
  is never overwritten: tell the person), 5 a file that could not be written.
- A source that cannot be read (a connection that asks for sign-in): say which, skip it, go on.

## Files this skill keeps

- `.fairmind/brain-seed/ledger.json` is the resume and incremental memory. Deleting it makes the
  next run send everything again. `ledger.json.lock` beside it is the empty lock `seed_send.py`
  holds while it runs, so two senders never write the ledger together.
- `.fairmind/brain-seed/references.json` holds the tickets each pull request's text was found to
  cite, per project, pull request and tracker, so a pull request whose reading file has not changed
  is not read by a model again. It is local: no call sends it. Deleting it makes the next run read
  every pull request again.
- `.fairmind/brain-seed/report-<run>.json` is the technical report of one run, kept next to the
  ledger because `<work>` can be deleted.
- `.fairmind/brain-seed/selection.json` is the selection a person approved (repositories, tracker
  roots, window, pages), written after `Proceed` and offered again at the next run. Deleting it
  loses the offer, not what the brain holds.
- `.fairmind/brain-seed/clickup-mapping.json` holds, per project, what each ClickUp status name and
  task type was decided to mean and the numeric ids of the accounts that are integrations
  (ClickUp token path only). Deleting it makes the next run decide again.
- `<base_path>/brain-seed/run/` holds the inventories, the raw ClickUp tasks (`clickup-tasks.raw.json`), the reading files (`read.md`, the only copy
  of a task's or pull request's text), the copies of repository files read for their summaries, the card skeletons, the staged cards and the lists `seed_send.py` hands back for words (`send-needs-<kind>.json`). It can be deleted
  after the run, and it never contains the ledger.
- All six are local files that no call sends.
