---
description: Connect this checkout to its Fairmind project — verify the per-project MCP entry and the key, pre-flight the tenant, bind the repository to the one the platform ingested using its catalog identity where the installed client supports it, offer the git pre-push check that keeps working artifacts out of a public repository, and offer to import the repository's recent pull requests so the first brain answer about its busiest file comes in the same session
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/fairmind_connect.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/artifact_cards.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/reference_card.py:*), Bash(gh auth status), Edit(.fairmind/brain-import/**), Read, AskUserQuestion, Skill(fairmind-coding:brain-import-pull-requests), mcp__Fairmind__General_list_projects, mcp__Fairmind__Brain_ask, mcp__Fairmind__Brain_import_pull_request
---

# /fairmind-connect

Run this once per checkout, per machine, before the first `/fairmind-loop` or
`/fairmind-develop`. It replaces the paragraph that used to tell a human to go
to Studio, copy a key and hope — and records the repository identity minted by the platform catalog. The judge
lane adopts that identity when the installed client supports it; older clients
continue to use the directory name.

**It never writes `~/.claude.json`.** That file holds a bearer token; a command
that edits it on someone's behalf is a command that can point a credential
somewhere. When something is missing it prints the exact line to run.

**The binding is per machine, not per repository.** It lands in
`.fairmind/active-context.json`, which is gitignored, so each teammate runs this
in their own clone.

## Step 1 — run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/fairmind_connect.py
```

The script prints the whole report. **Show that output; do not re-type it, do
not summarise it into a sentence of your own.** It is the record of what was
checked and what will now leave the machine, and a paraphrase of it is a record
that can drift from what actually happened.

## Step 2 — branch on the exit code

| Exit | Meaning | What you do |
| --- | --- | --- |
| `0` | Bound. | Relay the report, then go to step 5. |
| `1` | One or more traps, each printed with the fix. | Relay the report. Do not attempt the fixes yourself — every one of them is either a Studio action or an edit to the user's own MCP config. |
| `3` | The key is not scoped to a project and none was named. | Go to step 3. |
| `4` | The platform did not answer (503, network, VPN). | Say so and offer to re-run. Nothing was changed. |

## Step 3 — only on exit `3`: ask which project

The key carries no `projectId` claim, so the project has to be named once.

1. Call `mcp__Fairmind__General_list_projects`. Each row carries `id` and
   `name` — **`id`, not `_id` and not `project_id`**.
2. Ask the user with `AskUserQuestion`, one question, the project names as the
   options. Do not guess from the directory name: that a folder is called
   `payments-api` is exactly the signal this command exists to stop trusting.
3. Resolve the selected name to the `id` from that same list, then re-run with
   that ID. If names are duplicated, ask which project ID they intend. Never pass
   the display name as the `--project` value:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/fairmind_connect.py --project <id>
```

If the tool answers something that is not a list (a `message` dict is its
failure shape), report that and stop — do not fall back to a name you inferred.

## Step 4 — only on a `409`: the branch is ambiguous

A catalog row is one `(url, branch)` pair, so a repository ingested on several
branches has several rows. When none of them is on the current branch the door
refuses to guess — binding the wrong row looks exactly like not being bound at
all — and names the candidates. The report already prints the exact re-run line
per candidate; relay it and let the user pick.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/fairmind_connect.py --branch <name>
```

## Step 5 — after exit `0`: the artifact push check

Agents write working documents (design notes, reviews, reports) into the repository's
artifacts path. A git `pre-push` hook keeps them from reaching a **public** repository; in a
private one it stays silent. It works for any agent and for a push from a terminal, because git
runs it. Ask once, then leave it.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/artifact_cards.py install-git-hook --status
```

| The `state` it reports | What you do |
| --- | --- |
| **`installed`** | Say it is installed. Nothing else. |
| **`outdated`** | Run it again with `--refresh` and relay what it prints: the person already chose it. |
| **`absent`** | Ask with `AskUserQuestion`, one question: install the pre-push check that keeps working artifacts out of a public repository (silent in a private one)? Options: install, not now. On install, run it without `--status` and relay what it prints. On not now, say that `/fairmind-connect` offers it again. |
| `managed` or `foreign`, with `copy` set | The check is set up here already. Show `line` once as a reminder of where it goes (below), and do not ask. |
| `managed` or `foreign`, `copy` null | Another tool owns this repository's hooks (`why` says which), or someone else's pre-push hook is already there. Ask the same question. On install, run it without `--status`: it writes nothing among their hooks and prints `line`. Show that line exactly and say where it goes: at the end of their pre-push hook (`.husky/pre-push` for husky); for lefthook, in a pre-push `scripts:` entry with `use_stdin: true`. Never edit their hook yourself. The line does nothing for a teammate who has not installed the check. |

How the hook stays current, and how to remove it, is in the plugin README under `artifact-cards`.

Then go to step 6.

## Step 6 — after step 5: the first answer from the brain

A newly bound repository leaves the brain with nothing to say about its code. The
reasons behind that code sit in its pull requests, so this step offers to import
the recent ones and then asks the brain about the file they touched most: the
person sees an answer about their own code before the session ends.

Offer it only when all of these hold, and otherwise skip it without a word:

1. No `.fairmind/brain-import/<n>/write.json` exists at the repository root. A
   staged card means an import already went past its question about what
   leaves; a collect the person then declined leaves none, so the offer comes
   back. So does a run in which every pull request was `unchanged`: the brain
   already held them, and a second run only spends its free checks.
2. `mcp__Fairmind__Brain_import_pull_request` and `mcp__Fairmind__Brain_ask` are
   in the tool list, by name.
3. The origin is on GitHub:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py remote
   ```

   exits 0. Then `gh auth status` must succeed; when it does not, say once that
   `gh auth login` lets `/fairmind-connect` import the pull requests, and stop.

Then:

1. Ask with `AskUserQuestion`, one question: import the 50 merged and closed
   pull requests of `<repository>` updated most recently into the brain, so that a question
   about a file is answered with the reasons written in its reviews? Say that
   they are read from GitHub with the person's own `gh` login, that nothing is
   sent before a second question showing exactly what leaves, and that each
   pull request sent spends one unit of the daily brain write budget. Options:
   import, not now. On not now, say that `/fairmind-connect` offers it again,
   and stop.
2. On import, run the `brain-import-pull-requests` skill. The yes is the
   person's request that skill needs, and it is also the choice its Step 1
   asks for: the last 50, every pull request `list --limit 50` returns. Do not
   ask which again. Every other step of the skill applies as written, its
   Step 2 question included, so a no there still sends nothing.
3. After the skill's report, when at least one pull request is `imported` or
   `unchanged`, find the file to ask about, with one `--number <n>` per such
   pull request:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_collect.py busiest --out-dir .fairmind/brain-import --number <n>
   ```

   When none is `imported` or `unchanged` (the budget was spent, the person
   said no, the import stopped) or `path` is null, stop here. Say that asking
   to "import the pull requests" runs the `brain-import-pull-requests` skill
   again and continues where this one ended, and that `/fairmind-connect`
   does not offer it again once a card was staged.
4. Ask the brain about `path` without asking the person first: the call only
   reads. Ask one question with `mcp__Fairmind__Brain_ask`, by the rules of the
   `brain-ask` skill (the one repeat its Step 3 table allows for a failed call
   included): `question_type: "file"`, `question: "Why is <path> the
   way it is, and what was decided about it?"`, `subject: {"file":
   {"repository": <repository_id>, "path": <path>}}` and `project:
   <project_id>`, both ids from `.fairmind/active-context.json`. Say first that
   `<path>` is the file `pull_requests` of the imported pull requests changed.
   Present the answer as `brain-ask` Step 5 does, and read every refusal as its
   Step 3 table says. End with one line: from now on, a question about why the
   code is the way it is goes to the brain first.

## What this does not do

- It does not resolve the repository itself. The origin url goes to the door
  that owns the catalog and the server answers; no matching logic lives here.
- It does not change what the ambient session digest sends. That payload was,
  and stays, an opaque one-way hash of this checkout's location. What the
  binding adds is the platform's ability to join that hash to the repository —
  a consequence of an act the developer performed deliberately, which is why it
  is this command and not a default.
- It does not turn capture on or off. That is `/fairmind-config`.
- It does not install the push check without asking, and it never edits a hook another tool owns.
- It does not import a pull request without two yeses: the offer in step 6,
  then the import skill's own question showing what leaves. The question to
  the brain that follows reads only.
