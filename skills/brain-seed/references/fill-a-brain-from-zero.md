# Fill a brain from zero

A new project's brain is empty, so the first question an agent asks it
comes back with nothing. The `brain-seed` skill of the `fairmind-coding` plugin fills it from
what the team already has, in one run a person asks for:

- the **documents** you name (pages in a document space, files in Drive) and the documentation
  files kept in the project's repositories;
- the **tasks** of the tracker projects and lists you pick, updated inside a window (90
  days by default), and the epics you pick, whole whatever their dates;
- the **merged and closed pull requests** of the same window, from every repository of the
  project that you select.

Everything goes to one project. Nothing is filed for the whole company.

## Before you start

### 1. Hold the editor role on the project in FairMind Studio

Seeding writes to the project's brain, so the person who runs it needs the editor role
on that project.

### 2. Create the project in FairMind Studio and associate its repositories

The repositories `brain-seed` reads come from the **project in Studio**, wherever the
checkout you run it from lives. A project with no associated repository still gets its
documents and tasks, but no pull requests and no repository files.

### 3. Install the plugin

```text
/plugin marketplace add FairMind-Gen-AI-Studio/fairmind-plugins-public
/plugin install fairmind-coding@fairmind-plugins
```

### 4. Connect the Fairmind MCP for this project

Configure the Fairmind MCP entry **for this project** (an entry configured for your user
account does not count). Name the entry exactly `Fairmind`: the skill finds its tools
under that name (`mcp__Fairmind__*`). The key must be able to write: generate it in
Studio on the Developer page with the project selected. A key without write scope reads
but cannot seed.

### 5. Run /fairmind-connect in a checkout of the project

```text
/fairmind-connect
```
It verifies the MCP entry and the key and binds the checkout to the project in
`.fairmind/active-context.json`. `brain-seed` stops without that binding and tells you to
run this. The binding is per machine: each teammate runs it in their own clone.

### 6. Optional: sign in to GitHub

Pull requests and repository files need the GitHub command line signed in
(`gh auth login`). Without it they are skipped and the skill says so; documents and tasks
still go ahead. It reads with your own credential, read-only, and posts nothing back.

### 7. Optional: set a ClickUp token

When ClickUp is your tracker you can read it with your own API token instead of the hosted
connector, which has a daily call allowance. Export `CLICKUP_API_TOKEN` in the environment
of the session before it starts. The skill reads it from there and from nowhere else.
Without it, ClickUp is read through the connector as for any other tracker.

> Tell FairMind before the first run that you intend to seed, so the tenant is ready (see
> [What to ask FairMind for](#what-to-ask-fairmind-for)). A run against a tenant that is not
> provisioned for brain writes stops at its first write. For a large run, the budget units
> appear only on the plan screen, where the skill can offer setup mode (see
> [A large run](#a-large-run)).

## Run it

Ask the agent for it: "seed the brain", "fill the brain with our docs and tickets", or
"backfill the brain". Nothing is sent until you approve.

**First screen: what to learn from.** Four questions:

| Question | What you choose |
| --- | --- |
| Documents | None, the pages of a connected space or the files of a Drive folder, and/or the documentation files of the project's repositories. You can also paste links, one per line. |
| Tasks | None, or the tracker to read. Then the projects, lists or epics to bring in (their names are looked up; with ClickUp read through your own token, you give their links instead). |
| Window | Last 90 days (recommended), last 30 days, or any number of days. It applies to projects and lists, not to an epic you pick: that comes whole. |
| Repositories | All of the project's repositories, a numbered pick, or none. |

When a selection was approved on an earlier run, it is offered again as "Same as last time".

**Second screen: the plan and the one yes.** The skill lists first (titles, links, ids and
each task's own text; no document body and no comment) and then shows:

- how many documents, tasks and pull requests it would send, how many the brain already holds
  and how many were refused earlier. Counts for tasks and pull requests are a first guess, not
  a promise;
- the **budget units**: at most one per document, per task brought in and per pull request in the window.
  A unit is one write, taken from the tenant's daily brain write budget. Asking the brain
  whether it already holds an item is free, so a re-run costs fewer;
- what reading the tasks costs on the tracker's own connection (about two calls per task);
- what leaves your machine (below).

Only choosing **Proceed** is the yes. A yes typed earlier, or in free text, is not one, and
`Change` and `Cancel` send nothing. If the final count comes out higher than the plan showed,
the skill asks again.

## What leaves the machine

The skill reads through the connections already in your session and through your own `gh`
credential. It sends to FairMind, through the Fairmind MCP and only after the yes:

| Source | What travels | What does not |
| --- | --- | --- |
| Document (page or Drive file) | Its link, the system it lives in, title, version marker, who can open it, and a summary the agent writes | The document's text. The exception is a local file with no address, whose whole body travels; the plan names it as such. |
| Documentation file in a repository | A card: title, repository, path, commit, branch, blob id, the repository's visibility, the remote URL without credentials, and a summary the agent writes | The file's text |
| Tracker task | A reference card: status, kind and type, ids, parent, commit ids and pull request links, who can see it, the tracker's **account ids** of everyone involved, and a summary plus up to five points the agent writes | The description and the comments (they stay in local files on your machine). Display names are never sent. |
| Pull request | A card: ids, state, dates, branches, commit ids, every changed file path, the **logins** of the people who took part, the tickets it cites, and a summary the agent writes | The description and the review comments |

Two safeguards sit on the summaries: the tool that builds a card refuses a text that repeats
twelve or more words in a row from the source, or that looks like a credential. The summary is
still free prose written by the agent, so it says what the agent chose to say.

The ClickUp token is read from your environment, never written to a file, never in an
argument or an output, and never sent to FairMind: it goes only in the `Authorization` header
of the requests (reads only) that your machine sends to ClickUp.

Before the yes, nothing is sent to FairMind except the request for the project's repository
list (it carries the project id) and one test call to each of the task and pull request
endpoints with made-up data. The listing step reads through the tracker and document
connections of your session (and, on the ClickUp token path, ClickUp's own API with your
token), and `gh` reads each repository of the project with your own credential. The plugin
README's "What leaves your machine" describes the whole route.

A pull request in the window can cite a ticket outside the tracker scope you picked. Such a
ticket is read by id through the tracker connection after the yes, together with the tasks
above it and its comments, and is sent like the others, so it can add tasks to the count.
Each page the skill files carries the remote URL of the project's first repository, with
credentials removed.

## What lands, and where

- **Tasks, pull requests and repository files** are recorded **confirmed**, with the agent as
  their author. They wait for nobody. The change to a repository file whose card a person had
  already confirmed waits for review instead.
- **Pages** (Notion, Confluence, Drive or the web) land as **proposals**. A person on the
  project opens the **Review Queue** in Studio and confirms or rejects each one.
- The skill ends with a short report: what was written, what the brain already held, what was
  refused or failed, and the budget left. It keeps a technical report of the run next to its
  ledger and explains any item when you ask.

## Run it again

A re-run is safe and cheap.

- A local **ledger** in `.fairmind/brain-seed/` records, per item, the version sent and the
  outcome. The skill asks the brain about every task and pull request first, for free, and
  writes only what changed or did not get through.
- The selection you approved is kept in the same folder and offered again, with repositories
  added to the project since flagged as new.
- If the run stopped because the daily budget ran out, run it again once the budget has
  renewed: it resumes from the ledger and does not send twice.
- An item the brain refused waits for its content to change. To send everything again, say so
  and the plan includes those items again.
- Deleting `.fairmind/brain-seed/ledger.json` makes the next run send everything again.

## What to ask FairMind for

Seeding needs one thing from the platform side before the first run, and two more when the project is large.

| Ask for | Why |
| --- | --- |
| **A tenant provisioned for brain writes**, with a daily write budget set | Without a budget the write is refused and the run stops. The server release must also offer the endpoints for tasks and pull requests; on an older one the skill skips that phase and says so. |
| **Setup mode enabled for your tenant**, for a large run | Setup mode is a bounded period, for one project, with a write budget of its own that the writes of documents, tasks and pull requests spend instead of the daily budget, and with higher limits on placement cards for that project. It raises the write budget and the placement card limits only; it does not raise model spend. A project admin opens it, from the plan screen of the skill when the run is large, with numbers that stay within the ceilings FairMind set for your tenant. It ends on its own when its days run out, and can be closed earlier. Without it, the run continues over several days and resumes where it stopped. |
| **A placement pass** | Placement is the step that links the new cards to each other and to existing knowledge in the graph. It runs under per-day, per-month, per-run and cost caps sized for a trickle, so a bulk load can outrun it. FairMind raises the caps for the load, runs the pass over the cards the queue will not reach, and restores the caps. |

FairMind enables setup mode for the tenant and runs its own steps for the placement pass when you ask.

What you do yourself is generate a key with write scope for the project, in Studio, as
above.

### A large run

The budget units are known only at the plan screen, and `Cancel` saves nothing. So the
hand-off has two passes:

1. Make sure setup mode is enabled for your tenant (see above) and that you have the admin role on the project.
2. Run the skill to the plan screen. When the run is large and the tenant has setup mode, choose
   **Proceed**; the skill then asks, as its own question, whether to open setup mode with the
   days, writes and placement caps it proposes from the plan. Nothing is opened without your yes.
3. At the end of the run the report says whether setup mode was used; the skill offers to close it.

If you are not an admin, or setup mode is not enabled, the run continues over several days at the
daily budget and resumes where it stopped.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| "This folder isn't connected to a FairMind project yet" | Run `/fairmind-connect`, then ask again. |
| "Your FairMind server doesn't offer brain imports yet" | Ask FairMind for the release that includes them. |
| "Tasks will be skipped" / "Pull requests will be skipped" | The server predates that endpoint; ask FairMind for the release. |
| "Pull requests and repository files will be skipped: the GitHub command line isn't signed in" | Run `gh auth login` and ask again. |
| "This project has no repositories associated" | Associate them in Studio and ask again. |
| The run stops with a throttle | The daily budget (or the setup mode) is spent. Run again once it renews, or ask FairMind to enable setup mode; it resumes. |
| The run stops with an unset quota | The tenant has no write budget. Ask FairMind to set one, then run again. |
| "Access denied" | You have no editor role on the project, the key was issued for another project, or the key lacks write scope. Fix that and run again; it resumes. |
