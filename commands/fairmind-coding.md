---
description: The Fairmind coding front desk — pick a job (loop mode, develop with the team, import a ticket, harness audit, requirements from the company brain) and it launches the matching command or skill; type anything else to reach the rest of the toolkit.
allowed-tools: AskUserQuestion, Read, Skill
---

# fairmind-coding

The front desk for the `fairmind-coding` toolkit. It offers the headline jobs as a menu
and launches the one you pick by running its command in this same session. Every command
listed below is still invocable directly (`/fairmind-loop`, `/harness-audit`, …) — the front
desk is a convenience door, not a replacement.

The banner is printed for you by the plugin's `UserPromptExpansion` hook the moment this
command is invoked (it renders as a `systemMessage`; a banner run from the command body as a
Bash tool call would be collapsed to "Ran 1 shell command" and never seen). So **do not print
a banner** — your first action is the routing decision below.

## Usage

```bash
/fairmind-coding                 # menu, pick a job
/fairmind-coding US-142          # skip the menu: a bare task/story ref goes to loop mode
/fairmind-coding audit           # skip the menu: name a job directly
```

## The front desk

### 1. If the request already names a job, skip the menu

If `$ARGUMENTS` is non-empty, route straight to the matching job (see the catalog in
step 3) with the remainder as its argument, and do not draw the menu:

- a recognised job word (`loop`, `develop`, `import`, `audit`, `connect`,
  `sync`/`insights`, `report`, `fix`, `check`, `sonar`, …) picks that job;
- anything else that looks like a task or story reference (e.g. `US-142`, `TASK-871`, an
  issue number or URL, a sentence describing work to build) goes to **loop mode**, passed
  through as its argument. If it is ambiguous whether the user wants loop mode or develop,
  ask that one question before launching.

### 2. Otherwise, draw the menu

Call `AskUserQuestion` once — single select, `header: "Job"`, `question: "What would you
like to do?"` — offering exactly these five options, in this order (the tool adds its own
free-text and chat entries; do not invent more):

1. **Loop mode (Recommended)** — Build under a machine-checkable gate: implement → verify →
   iterate until the checks pass and you approve. One task wide.
2. **Develop with the team** — Implement a story or task with the full team, task by task,
   driven by you. Needs a Fairmind workspace.
3. **Import a ticket** — Turn an external ticket (a GitHub issue or pasted text) into a
   loop-ready contract, then hand off to loop mode.
4. **Harness audit** — Score this repo's Loop Readiness against the 81-criteria catalog and
   render a self-contained HTML report.
5. **Requirements from the brain** — Ask the company brain what it already knows: recover a
   system's requirements from its code, or check an idea against the precedents (including
   the ones that were retired, and why) before specifying it. Needs a Fairmind workspace.

The banner's last line already tells the user the off-menu jobs are reachable by typing, so
if they choose the free-text entry, map what they type with the catalog in step 3.

### 3. Launch the chosen job

The moment the job is known — picked on the menu, named in `$ARGUMENTS`, or typed as free
text — do two things, in order:

1. **Emit the auto-mode note once**, worded exactly: "Fairmind's jobs run best in auto mode —
   press Shift+Tab until the status bar shows auto mode, or restart with
   `claude --permission-mode auto`, to skip the per-step confirmations." Say it once; never
   reword or resize it, and do not diagnose the user's current mode.
2. **Launch the chosen job.** Invoke it with the `Skill` tool, by the name in whichever
   catalog below lists it (`fairmind-coding:<name>`), forwarding whatever argument applies —
   a **command** and a **skill** launch the same way (see "Why the Skill tool, not a file
   read" below).
   Execute its instructions — do not summarise it back to the user. Its own opening (banner,
   contract, questions) runs as part of it; that job-specific opening is expected and is the
   "you are now in <job>" signal.

Catalog 1 (job → command, launched with the `Skill` tool):

| Job | Skill |
|---|---|
| Connect this checkout to its Fairmind project | `fairmind-coding:fairmind-connect` |
| Loop mode | `fairmind-coding:fairmind-loop` |
| Develop with the team | `fairmind-coding:fairmind-develop` |
| Import a ticket | `fairmind-coding:loop-import` |
| Harness audit | `fairmind-coding:harness-audit` |
| Flush / sync insights | `fairmind-coding:fairmind-sync-insights` |
| Task report | `fairmind-coding:report` |
| Fix an issue | `fairmind-coding:fix-issue` |
| Fix a frontend issue | `fairmind-coding:fix-frontend-issue` |
| Add a custom check | `fairmind-coding:fairmind-add-check` |
| SonarQube fix | `fairmind-coding:sonarqube-fix` |
| Make tests | `fairmind-coding:make-tests` |
| De-slop | `fairmind-coding:de-slop` |
| Review a PR | `fairmind-coding:gh-review-pr` |
| Commit | `fairmind-coding:gh-commit` |
| Fix CI | `fairmind-coding:gh-fix-ci` |
| Address PR comments | `fairmind-coding:gh-address-pr-comments` |

Catalog 2 (job → skill, invoked with the `Skill` tool). Both need a connected Fairmind
workspace; when `.fairmind/active-context.json` says `fairmind: "none"`, say so and offer
`/fairmind-connect` rather than launching one:

| Job | Skill |
|---|---|
| Recover the requirements of a system that already exists | `fairmind-coding:brain-rebuild-requirements` |
| Check an idea against the brain's precedents, then draft it | `fairmind-coding:brain-new-requirement` |

"Requirements from the brain" on the menu covers both: ask which of the two the user wants
— recovering what exists, or specifying something new — and invoke that one.

If the free text is not a job but a question about the toolkit, or the user chose "Chat
about this", just answer in this session — no job is launched.

## Why the Skill tool, not a file read

The `Skill` tool loads a command the same way typing the slash command does — its own
substitution and `allowed-tools` apply — so it is what dispatches a sibling command from
inside this one; reading the target's file with `Read` would not, since a file opened that
way is read raw and carries none of the target's own grants. The loop's gate is driven by
the `loop-check.sh` Stop hook keyed on `loop-state.json`, not by this command's frontmatter,
so every job's own machinery still engages regardless of how it was launched.
