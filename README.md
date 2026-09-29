# fairmind-coding

Coding workflow plugin for Claude Code. Six role-based agents, plus skills, hooks and commands rostered by name in the tables below rather than counted here — among them a front desk (`/fairmind-coding`) that menus the toolkit, a human-driven team mode (`/fairmind-develop`), and an opt-in **loop mode** (`/fairmind-loop`) with a machine-checkable stop condition. **Runs standalone on any repo, zero-config**; the Fairmind MCP and the `.fairmind/` session workspace produced by Fairmind AI Studio are optional and only enable connected mode.

## What's included

### Agents

| Agent | Role |
|---|---|
| `Technical Lead / Architect` | Bootstraps `.fairmind/<project>/<session>/`, pulls the work and the roadmap, writes the work packages, and returns the ordered plan for the command to dispatch — a sub-agent has no `Task` tool of its own. **Never implements code.** |
| `Software Engineer` | Versatile implementer (frontend / backend / AI). Takes its conventions from the work package and the surrounding code. |
| `Code Reviewer` | Post-implementation review against plan, journal, and code standards. |
| `QA Engineer` | Test execution and validation. Playwright by default. |
| `Debugging Specialist` | Methodical, hypothesis-driven root-cause investigation. |
| `Security Engineer` | Web security review — OWASP, STRIDE, CVSS-classified findings. |

### Skills

| Skill | When to load |
|---|---|
| `fairmind-context` | Pulling project / session / user-story / requirements / test context from the Fairmind platform |
| `fairmind-tdd` | Implementing features against Fairmind acceptance criteria with journal traceability |
| `fairmind-code-review` | Reviewing implementation work — plan→journal→code traceability |
| `fairmind-gate` | Designing a loop-mode stop condition — the five check types, RED-first authoring, admission |
| `custom-check-authoring` | Authoring a custom loop check with the admission self-test (verify the verifier) |
| `task-compilation` | Classifying an external ticket's acceptance criteria into check types, for `/loop-import` |
| `brain-rebuild-requirements` | Recovering the functional and technical requirements of a system that already exists — module by module, from the ingested code and the company brain, each draft carrying a code anchor. Connected mode only; drafts are recorded as proposals a human confirms |
| `brain-new-requirement` | Turning an idea into a need, stories and requirements **after** checking the brain for precedents — including the ones that were retired, and why. Connected mode only; drafts are recorded as proposals a human confirms |
| `brain-add-document` | Filing a document the brain can cite — a company-wide standard, a design given as a link, a specification written in the session. The summary is the agent's: this door runs no model, and a document nobody summarised is findable only by its first 500 characters |
| `brain-record-decision` | Recording, when a person asks for it, a decision taken in the session, an issue found and not fixed, or the replacement of a decision the brain already holds — after searching the brain for the same or a competing one, and showing the draft before anything is sent. Connected mode only; records are proposals a human confirms, and nothing is added to the local decision log |
| `brain-onboard` | Starting on a project you do not know: its brief in one call — what it is, what was decided, what not to break, where to look — then the why behind each file you open. Asked to write or regenerate the brief, it assembles one from what the brain holds, shows the diff against the confirmed version, and records it as a proposal a human confirms. Connected mode only |
| `brain-impact` | Working out what a change touches before it is made — from a requirement, task or idea to the code it anchors on, the code graph's users of those symbols, the architecture components the files sit in and the ones that depend on them where the platform models components, and the decisions, incidents and criteria recorded there. Usages-only: it follows the usages the code tools find — graph usages and text matches of a name — not call flows. Connected mode only; read-only — it records nothing in the brain |
| `brain-extract-components` | Turning a blueprint the brain already holds into the components it names — each with its responsibility, layer, technology and the directories that implement it, plus what it belongs to and depends on — when a person asks. It reads a filed blueprint and nothing else, and stops when the project has none; it shows the draft and asks before sending. Connected mode only; components are recorded as proposals a human confirms |

### Hooks

| Hook | Event | Purpose |
|---|---|---|
| `validate-fairmind-path` | `PreToolUse` on `Write\|Edit` | Blocks writes to `.fairmind/` paths outside the scope active-context.json's declared `base_path` names, and any path carrying a `..` segment |
| `inject-context` | `PreToolUse` on `Task` | Surfaces `FAIRMIND_BASE`, `project_id`, `session_mindstreamId` to subagents |
| `check-journal` | `SubagentStop` | Refuses a sub-agent's completion if it mutated non-`.fairmind/` code and wrote no journal — enforced for any code-mutating sub-agent, not a fixed role allowlist |
| `record-attestation` | `SubagentStop` | Writes `${FAIRMIND_BASE}/attest/<task_ref>-<agent>-<n>.json` — the sub-agent's harness-supplied `agent_type`, normalized the same way `check-journal` normalizes it, and the instant it finished — but only when **this session is the one driving a live loop**, resolved through the same `_loop_ledger` authority every sibling capture hook uses (`active-context.json` and `loop-state.json` are repo-global, so mode+status alone would let an unrelated session's sub-agent mint one by accident). An undecidable identity records nothing. It is what makes the loop's completeness verdict evidence rather than a role the caller typed about itself: `--record-completeness` refuses a verdict whose attester owns a check in the contract, and spends each attestation on exactly one verdict. ⚠️ It raises the cost of faking maker≠checker; it does not make it impossible — the file lands under `.fairmind/`, which the session can write. Records only a role name, a timestamp and the minting session's id (the same one the token ledgers already carry): no prompt, no code, no file content, no path. Best-effort and never blocking |
| `loop-check` | `Stop` | Loop-mode gate — runs the checks and blocks the turn until the stop condition holds. Silent no-op unless a loop is active |
| `trace-op` | `PostToolUse` on all tools | Appends the mechanical *what* of each tool call to `.fairmind/trace/<taskRef>.jsonl` (`kind`: mutate/exec/dispatch/read/other). Silent no-op outside a Fairmind workspace |
| `capture-subagent-tokens` | `SubagentStop` | Sums a finished sub-agent's token usage from its transcript and appends one row to the **ref-keyed** `${FAIRMIND_BASE}/subagent-tokens-<sanitized task_ref>.jsonl` — the name `_loop_ledger.loop_ledger_path` builds; a context carrying no `task_ref` writes the fixed `subagent-tokens-session.jsonl` — so the loop dashboard has per-loop token stats. The legacy unkeyed `subagent-tokens.jsonl` is still **read** and is never written, migrated or deleted, so rows captured before the key changed still count (`_loop_ledger.loop_ledger_paths` owns that rule). A session that is **not** the loop those ledgers belong to has its rows routed to a directory of their own instead; `INTERNALS.md` holds that table. Best-effort and never blocking; silent no-op outside a Fairmind workspace |
| `python-floor` | `SessionStart` | Says in one line when `python3` is missing, does not report its version, or is older than 3.9 (`◆ Fairmind Python: …`). The judge, criteria and insights hooks are `python3` programs whose wrappers swallow errors so they never block a session, so an interpreter too old to run them would otherwise leave them silently off. Plain bash, silent when the interpreter is recent enough, bounds its own probe of `python3` to two seconds, sends nothing anywhere, exits 0 on every path |
| `session-start-insights` | `SessionStart` | The ambient capture gate, re-evaluated fresh every session: it fails closed unless this repo has a per-project Fairmind MCP configured, then consults the central plugin policy, then `.fairmind-insights.json`. On capture it registers the session and shows the one-time notice. It then spawns one detached, niced background pass that digests and delivers what is spooled and **refreshes the central plugin-policy cache** (the only outbound call for that policy — see [Central policy](#central-policy)). Fail-open, 5 s timeout; a virgin, non-Fairmind or opted-out session writes nothing |
| `session-end-insights` | `SessionEnd` | Stamps the end marker on this session's registry row; a no-op for a session that never captured |
| `capture-orchestrator-tokens` | `Stop` | The main thread's counterpart to `capture-subagent-tokens` — the orchestrator never fires `SubagentStop`, so its own token usage would otherwise go uncounted. Best-effort and never blocking |

### Commands

| Command | What it does |
|---|---|
| `/fairmind-coding [job\|ref]` | Front desk: prints the banner, offers the headline jobs (loop mode, develop, import a ticket, harness audit) as a menu, and launches the one you pick by running its command in this session; type anything else to reach the rest of the toolkit. A named job or a bare task ref skips the menu |
| `/fairmind-loop [ref]` | Run a task/story in loop mode: the Technical Lead builds a machine-checkable stop condition, then the executed gate drives implement→verify→iterate under budget until it passes and a human approves |
| `/fairmind-develop <US-\|TASK-ref>` | Implement a story (every task under it, in roadmap order) or a single task with the full team, human-driven: the Technical Lead plans, you confirm the order, then engineer → QA → review run task by task. Connected mode only; no executed gate — `/fairmind-loop` is the gated twin |
| `/loop-import [gh-issue\|ticket-file\|clickup-task.json]` | Turn an external ticket into a compiled loop-mode contract (adapter → `task-compilation` classification → `loop_import.py --emit`), present the gap report, then hand off to `/fairmind-loop` to arm |
| `/fairmind-add-check` | Author a custom loop-mode check (open descriptor contract + admission self-test) |
| `/fairmind-config [judge\|ambient on\|off\|unset]` | Show or change this repo's plugin policy — the judge Stop-hook review and ambient session capture. No argument reports the effective state per feature and which layer decided it (centrally forced / repo file / default) plus the policy cache's freshness; a feature and a value writes the local `.fairmind-insights.json`, always spelling `ambient_capture` out. Refuses — leaving the file byte-identical — a centrally forced feature, a file that is not a JSON object, and a `judge` write over a non-boolean `ambient_capture` |
| `/harness-audit [--test-command "<cmd>"]` | Audit the repo against the Loop Readiness criteria catalog (81 criteria / 9 pillars / 5 dimensions) and render a self-contained HTML report under `.fairmind/audit/` |
| `/fix-issue [issue-name] [--type fe-fe\|fe-be\|be-be]` | Classify an issue, confirm with user, dispatch the Software Engineer |
| `/fix-frontend-issue [issue-file]` | Frontend fix loop with Playwright validation, max 5 iterations |
| `/sonarqube-fix` | Pull PR-scoped SonarCloud issues, fix BLOCKER → INFO, run tests, commit |
| `/report` | Task report — executive / sprint / standup |
| `/make-tests` | Coverage-driven test scaffolding (pytest by default) |
| `/de-slop` | Strip AI-generated artifacts (NOTES.md, redundant comments, etc.) before PR |
| `/gh-commit` | Conventional commits with branch safety; opens the pull request when asked, [signed](#agent-signature-on-pull-requests) |
| `/gh-fix-ci` | Diagnose and fix CI failures |
| `/gh-review-pr` | Structured PR review; posts it when asked, [signed](#agent-signature-on-pull-requests) |
| `/gh-address-pr-comments` | Walk PR comments and apply fixes; replies to the addressed ones when asked, [signed](#agent-signature-on-pull-requests) |

## How Fairmind stacks the loops

Fairmind's take on loop engineering is a foundation plus four concentric loops, each with its own exit condition and time scale. This plugin operates the two innermost loops (runtime in Claude Code); the outer two and the foundation live on the Fairmind platform (design-time):

```text
0 · FOUNDATION   Evidence Collection: code · logs · DB · UI  →  Project Context
                 runs once, up front — feeds every loop below

┌─ 4 · OPTIMIZE ── Conductor · Optimize ───────────────────────────────────────────────┐
│                                         exit: agent-ready codebase · every N sprints │
│ ┌─ 3 · SPRINT ── Agile Studio → Working Session ───────────────────────────────────┐ │
│ │                                            exit: Working Session closed · ≈ days │ │
│ │ ┌─ 2 · TASK ── Claude Code + verifier agents ──────────────────────────────────┐ │ │
│ │ │ ◀ you are here: /fairmind-loop       exit: gate green, you approve · ≈ hours │ │ │
│ │ │ ┌─ 1 · AGENT TURN ── the harness ──────────────────────────────────────────┐ │ │ │
│ │ │ │ ◀ hooks · skills · subagents             exit: turn complete · ≈ minutes │ │ │ │
│ │ │ │ [implement] → [hooks + skills guide the turn] → [second opinion] → close │ │ │ │
│ │ │ └──────────────────────────────────────────────────────────────────────────┘ │ │ │
│ │ └──────────────────────────────────────────────────────────────────────────────┘ │ │
│ └──────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘

● design-time on FairMind: 0 · 3 · 4            ● runtime in Claude Code: 1 · 2
```

- **0 · Foundation** — Evidence Collection (code, logs, DB, UI) builds the **Project Context** once, up front: a loop iterating on the wrong context converges on the wrong answer. Connected mode pulls it through the Fairmind MCP (`fairmind-context` skill); standalone mode approximates it from the local repo.
- **1 · Agent turn (≈ minutes)** — the harness. The plugin configures it: hooks fire at key lifecycle points, skills put written conventions in front of the agent on every run, and a subagent gives a second opinion — the writer never approves their own work.
- **2 · Task (≈ hours)** — the loop `/fairmind-loop` drives. Exit turns on **two questions**, neither of them the maker's to answer, and **since 2026-08-24 the engine gates on both**:
  - the **binary oracle** — the executed gate (`run_gate_checks.py`): acceptance checks pass or fail with no interpretation, and stay valid across refactors because they never look at the implementation. It answers: *does it work?*
  - the **completeness check** — *is this the right work?* `green × K` no longer closes a loop on its own: it holds at `running` until a reviewer that owns no check in the contract records a verdict (`--record-completeness`), read against the **design brief** rather than the contract, and `--arm` refuses a loop that has no brief for that reviewer to read.

  What the engine proves about the second is narrower than that it happened well: a verdict was recorded, by a non-maker role, against a harness-written attestation, for the tree that is actually there. It cannot prove the reviewer reasoned well — so the **final human gate stays load-bearing**. The `fairmind-gate` skill states the residuals; do not restate them here.
- **3 · Sprint (≈ days)** — Agile Studio designs the sprint (needs, stories, tasks, acceptance tests) into a Working Session; closing it is the exit.
- **4 · Optimization (every N sprints)** — Conductor's Optimize measures how agent-ready the codebase is and turns its recommendations into next-sprint tasks: the outer loop feeds the inner ones.

Every loop, at every scale, is built from the same six pieces: a **trigger** (the beat), a **worktree** (isolation), **skills** (written context), **MCP connectors** (reach), a **second opinion** (the verifier), and **state on disk** (memory between runs — `loop-state.json`, journals, trace).

## Loop mode

Interactive mode (the default) is human-driven. **Loop mode** (opt-in via `/fairmind-loop`, recorded as `active-context.json.mode = "loop"`) adds an objective, machine-verifiable stop condition and lets an *executed* gate drive the loop:

- **Contract up front.** The Technical Lead classifies each acceptance criterion into one of five **check** types — `functional`, `metric`, `performance`, `static`, `evidence` (plus `custom`) — and specifies descriptors into `${FAIRMIND_BASE}/loop-state.json`. The Technical Lead never writes check code.
- **maker ≠ checker.** Checks are authored RED-first by an agent other than the maker (the QA Engineer for functional/evidence, the Code Reviewer for metric/performance). The engine enforces this structurally.
- **Admission ("verify the verifier").** `admit_check.py` runs mandatory portable gates — maker≠checker, clean-signal, RED-first, determinism probe — before a check can gate. Failures are quarantined and excluded from the stop condition.
- **Executed gate.** The `loop-check` Stop hook runs `run_gate_checks.py` on every stop: not green + budget left → blocks the turn with routed feedback; all green for **K ≥ 3 consecutive** evaluations → `passed_pending_human`.
- **Portable, no sandbox required.** Hermeticity is tiered: Tier A wraps checks in `srt` (network-denied) when present; otherwise Tier B uses a determinism probe and marks checks `hermeticity-unverified`.
- **Two human touchpoints.** The Technical Lead proposes a budget (iterations / consecutive-failure cap / timeout) that the user confirms before the loop arms; at the end a **final human gate** reviews the report — no auto-merge/deploy.

See the `fairmind-gate` skill for the check types and descriptor contract, and `/fairmind-add-check` for custom checks.

## Standalone vs connected

The plugin installs and runs with **zero MCP servers**. In **standalone mode** (the default when no Fairmind workspace is present), the Technical Lead bootstraps a minimal `.fairmind/active-context.json`, asks once whether a Fairmind workspace exists, and drives loop mode entirely from local `.fairmind/` files (contracts, `loop-state.json`, journals) — the executed gate, admission, budget, and human gate all work with nothing but `git`, `python3`, and `bash`. In **connected mode** (`fairmind: "configured"`, which `/fairmind-connect` establishes and verifies rather than asserting), the Fairmind MCP adds platform context (projects, stories, requirements, tests, RAG), while Playwright and MongoDB MCP enable the QA/frontend and MongoDB-stack workflows. Every agent degrades gracefully: absent MCP tools mean it reads the local equivalents and operates standalone — absence of Fairmind is a mode, not an error. Connected mode is not only inbound: records also go **out**, by more than one route — *What leaves your machine* below describes each route and states how that list was built.

## Agent signature on pull requests

An agent that posts on a pull request with your `gh` credential is, to GitHub, you. So everything this plugin posts there — a pull request's description, a comment, a review, a reply to a review comment — ends in a marker saying an agent wrote it. The marker is an HTML comment: GitHub displays nothing for it, and its API returns it inside the text.

**Where it is applied.** In one place, `scripts/pr_post.py`, which signs the text and then calls `gh`. Three commands post through it, each only after showing you the text and getting your confirmation: `/gh-commit` when you ask it to open the pull request, `/gh-review-pr` when you ask it to post the review, and `/gh-address-pr-comments` when you ask it to reply to what it fixed. In the plugin's source repository a test scans every file the plugin ships and fails on any other way of posting on a pull request that it knows the shape of — `gh`'s posting subcommands, `gh api` and HTTP writes to a pull request or its threads, GraphQL mutations, GitHub MCP write tools.

**The format**, version 1:

```text
<!-- fairmind-agent v=1 agent=<name> session=<sessionRef> -->
```

| Field | Meaning |
|---|---|
| `v` | The format version, an integer. It changes whenever a field is added, removed or changes meaning |
| `agent` | Who wrote the text: `claude-code` unless the caller names a plugin agent's role. Letters, digits, `.`, `_` and `-`, at most 64 characters |
| `session` | The Claude Code session id, read from `CLAUDE_CODE_SESSION_ID` — the host sets it in every shell the agent runs, equal to the `session_id` its hooks receive. It is the same id the insights records carry as `sessionId`, `owner_session` and `session_ref`, so a signed post joins the decisions its session recorded |

It carries nothing else: no user name, no account, no tenant, no token. It is always the final line of the text, at the start of the line and after a blank line, which ends any list or quote before it. A fenced code block the text leaves open outside every list and quote is closed first, so the marker is not rendered as code. Signing a text again — a description being edited — replaces its final marker instead of adding a second one.

**Parsing it.** The regex, in Python syntax:

```text
<!--\s*fairmind-agent\s+v=(?P<v>\d+)\s+agent=(?P<agent>[^\s>]+)\s+session=(?P<session>[^\s>]+)\s*-->
```

A text is signed only by its **final non-blank line**, and only when that line, without its trailing whitespace, is one whole match of this regex with `v` equal to `1`. A marker anywhere else signs nothing: a reply quoting a signed comment, a description documenting this format, a marker a person typed more text after, a marker indented into a code block. This regex matches only a marker carrying exactly these three fields in this order, so a later version that adds a field comes with a regex of its own, and to a parser of this version it reads as no signature at all. `scripts/pr_post.py parse` applies this rule.

**What it does not prove.**

- It says that the text was posted through this plugin from an agent session — a claim the helper writes, not a proof. It is **not a cryptographic signature**: anyone can type the same line by hand, and a person can edit the text above it after the agent posted it and leave the marker in place.
- Its **absence** proves nothing. Text an agent posts some other way — the host's own pull-request flow, a `gh` call made outside these commands — carries no marker, and on a host that does not set `CLAUDE_CODE_SESSION_ID` the helper refuses to post rather than post unsigned. Read unmarked text as of unknown authorship, never as proven human.
- The session id is opaque. Tying it to the decisions and records of its session needs access to the Fairmind records that carry the same id.

Anyone who can read the pull request can read the marker — on a public repository, everyone. What that discloses is under *What leaves your machine* below.

## What leaves your machine

Records leave this machine by more than one route, and the routes are separate in every way that matters: different payloads, different doors, different preconditions, different controls. Two of them are **capture lanes** feeding Agentic Insights — the **loop lane**, flushed from what a run leaves under `.fairmind/`, and the **ambient lane**, a per-session summary — and a single loop run is captured by both. Beside them sit three routes that are not capture lanes at all: the **Studio write-back**, a per-task push of the run's journals that a human asks for; the **brain write-back**, what the brain skills record when somebody runs one — requirement drafts from two of them, a document from a third, decisions, issues and replacements of decisions from a fourth, a project brief from a fifth, and proposed components from a sixth; and the **agent signature**, a marker carrying the session id at the end of what three GitHub commands post on a pull request, which goes to GitHub rather than to Fairmind. They are described here one at a time, because nothing said about one holds for another.

**How this list was built — stated rather than left implicit, because a closed count is the failure mode a page like this has.** It comes from reading every `mcp__*` tool this plugin *declares* — the `allowed-tools:` of `commands/*.md` and the `tools:` of `agents/*.md` — on 2026-08-17, re-read on 2026-09-12 after the agent grants were cleaned up, and keeping the ones whose **payload carries repository-derived content outward**. Fairmind's `Code_*`, `Studio_get_*`/`Studio_list_*` and `General_*` tools are inbound reads and are not here. Neither are the declared MongoDB tools — `find`, `aggregate`, `count`, `explain`, the `list-*`/`*-schema`/`*-indexes`/`*-stats` inspectors and `mongodb-logs`, among which no insert, update or delete appears — This is a survey of what the plugin **declares**, not proof that nothing else sends. ⚠️ **That rule reaches what a command or an agent declares; it does not reach a call a *skill* instructs.** A skill carries no tool grant, so `Brain_record_requirement`, `Brain_add_document`, `Brain_supersede_decision` and `Brain_record_component` — the brain write-back below — sit in no `allowed-tools:` and no `tools:` list, and the grep this paragraph describes would never have surfaced them; `Brain_record_decision` and `Brain_record_issue` appear in one only because `/fairmind-loop` — and, for decisions, `/fairmind-sync-insights` — declare them for the loop half of that route, and the grep would have credited them to that half alone. It is written up here on the same footing as the Playwright and `Bash` capabilities named next: outside the rule, inside the heading. The agent signature is outside the rule for a different reason: it leaves through `gh`, not through any `mcp__*` tool, so no survey of declared tools could have found it either.

🔴 **The Playwright tools are the exception that has to be stated, not dismissed** — an earlier draft of this paragraph waved them away as merely driving a browser, and that was false. `qa-engineer` declares `mcp__plugin_playwright_playwright__browser_file_upload` in its `tools:` frontmatter (`agents/qa-engineer.md`), alongside `browser_evaluate`, which runs arbitrary script in the page under test. What that upload tool does with the paths it is handed is documented by the Playwright MCP server, not by this plugin — read it there rather than taking this sentence for it. Those can put local file bytes, or anything a script can read, onto a site the agent navigated to. (The Puppeteer grants this paragraph used to name beside them were removed on 2026-09-12: no instruction in this plugin ever called one. The Playwright ones stay, because the QA Engineer really does drive a browser.)

One declared capability **used to be** named here without being described as a route: four agents — `software-engineer`, `tech-lead`, `qa-engineer`, `code-reviewer` — carried `mcp__memory__create_entities`, `create_relations` and `add_observations` in their frontmatter `tools:` list, pointed at whatever `memory` MCP server **you** configured rather than at a Fairmind door. No instruction anywhere in this plugin ever told an agent to call one, and on 2026-09-12 the grants were removed along with the other 118 that no instruction used. It is recorded here rather than quietly deleted, because a customer who read the old paragraph was told a capability existed and is owed the sentence saying it no longer does.

### Lane 1 — the loop lane: three Insights doors

**What it sends.** A flush step assembles payloads out of what has been left under `.fairmind/` — a loop's record, the agents' decision log, a `/harness-audit` run — and each goes through its own Fairmind MCP tool. The content is **derived from your repository** and is not anonymized — paths and prose travel as they were written:

| Door | What it carries |
|---|---|
| `Insights_record_loop_stats` | The **repo-relative path of every file the loop changed**, each with a rework count; the diff's shape (files, insertions, deletions, a size bucket, and a histogram of source-file extensions); a per-iteration timeline of check ids, verdicts, **the values a check measured**, timestamps and **commit shas**; the agents that ran, with model id, token counts and tool-call counts; the loop's lifecycle transitions and the human verdicts recorded on it; and your own identifiers in clear — `task_ref`, `target_ref`, `project_id`, `loop_id`, `owner_session`. ⚠️ One field in that timeline, `iterations[].event`, is an **open free-string channel**: the engine writes a set of known names but none is enforced, so a hand-edited `loop-state.json` sends whatever it says (`_iteration_wire` in `scripts/insights_flush_payload.py`). No file contents and no diff text: divergence between two trees travels as a pair of sha256 digests and a changed-path *count*, never as a path list or a patch |
| `Insights_record_agent_decisions` | Your **repository name** and your **`origin` remote URL** in plaintext — credentials are stripped out of the URL; the host, the organization and the repository name are not — and, per decision, **the free prose an agent wrote**: a title and a rationale, in whatever words it chose, alongside repo-relative **file paths** and **function names with their line numbers** |
| `Insights_record_harness_audit` | A `/harness-audit` run: repository name, commit sha, `origin` remote URL, and the per-criterion verdicts |

⚠️ **Only one of those three doors is tied to a loop, and the lane's name is misleading about the other two.** `Insights_record_loop_stats` carries a loop's record. The other two need no loop to exist: `/harness-audit` has **no loop precondition at all** — its flush is gated only on the Fairmind MCP being reachable — and the decision log the agents append to is a top-level convention in `agents/software-engineer.md`, `agents/tech-lead.md` and `agents/qa-engineer.md`, a *sibling* of their loop-mode sections rather than something nested inside them, which `/fairmind-sync-insights` describes in its own frontmatter as running *independent of a loop close*. `/fairmind-develop`, which runs no gate and closes no loop, declares `Insights_record_agent_decisions` too. So a repository that never runs loop mode still has two of these three doors open to it.

**When.** The flush is a step somebody runs, not something the plugin does on a timer or a schedule: it is the last step of `/fairmind-loop`'s Exit, the last step of a `/harness-audit` run, and the whole of `/fairmind-sync-insights`, which you can run at any time.

⚠️ **A running loop's record is not held back until it closes.** `/fairmind-sync-insights` applies **no status filter** — it emits whatever is on disk. Measured on the shipped CLI against a repo whose `loop-state.json` still read `"status": "running"`: `insights_flush_payload.py --emit loop` returned a payload carrying that status verbatim, alongside `loop_id`, `target_ref`, `task_ref`, `project_id`, `owner_session`, `started_at`, the check count and the iteration count. So a sync run mid-loop sends the loop as it stands, and so does one run over the residue a crash, a `kill` or a hand close left behind — which is the ordinary way a loop ends up non-terminal.

**Precondition.** A Fairmind MCP tool reachable in that session — and that is the whole precondition. The plugin's own script opens no network connection: it builds the payload, writes it to a file, and the *model* makes the MCP call. With no Fairmind MCP connected the payloads stay on disk unflushed, and the next `/fairmind-sync-insights` sends them. This lane applies no scope check of its own, so unlike the ambient lane below it does not distinguish a Fairmind entry configured for *this project* from one configured for your user account.

### Lane 2 — ambient session records

**What it sends.** Independently of loop mode, a background process summarizes each finished Claude Code session in this repo and posts it to the Fairmind door derived from this project's own MCP URL, under that entry's credential. Two doors:

- **Session activity** — the session id; a **one-way-hashed repository id** (`repoRefScheme: "opaque-tenancy"`, and read the residual below before treating "opaque" as the end of the sentence); how the session was entered; the plugin version; start and end times; the **names of the skills** used and a **count per tool name**; one row per (agent role, model) carrying token counts; a `decisionsCount` field that is currently always the literal `0` (`build_wire_payload` in `scripts/ambient_outbox.py` — the ambient lane does not count decisions, and the field is a placeholder rather than a measurement); a flag saying whether the transcript parsed cleanly; a fingerprint of the record; and three delivery counters.
- **The event skeleton** — **off unless your company enables it**, a separate decision from the one above. One row per assistant turn, tool call and tool result: kind, actor, lane, the transcript's own row identifiers, the position in the sequence, token counts, a model id, a tool name and an error flag. Rows the *agent* authored carry a timestamp; a row originating from a person — a prompt, an interrupt, an approval, a denial, an answer — carries its position and no clock.

**How opaque the repository id actually is.** `repoRef` is `"fm-" + sha256(realpath(<git common dir>))[:16]` — unsalted, truncated, and a **pure function of that absolute path**, with no host, user or `$HOME` component mixed in. Two consequences follow, and a page that said only "opaque" would be claiming more privacy than the code delivers. Two checkouts sitting at the same absolute path produce the **same** id — a devcontainer's `/workspaces/<repo>/.git` is identical for every developer on that repo, as is CI's `/home/runner/work/<repo>/<repo>/.git`. And anyone holding the delivered rows plus a *guess* at the path can **confirm** a repository by recomputing the hash. It withholds the name; it does not resist a guess.

**What it does not send.** No prompt or reply text, no tool inputs, no file paths, no command text, no diffs, no branch name, and not your repository's name or path. What actually checks which part of that, cited precisely rather than pooled — because two of these are different kinds of guarantee and one part is not machine-checked at all:

- **Prompt and reply text, tool inputs, file paths, command text** — `tests/test_events_conformance.py`, on the event-skeleton payload, searching the delivered bytes for needles taken from **that fixture's own generating transcript**: the file path it read, the command it ran, the prompt a person typed, the plan body, the tool-result body, and the question the agent asked together with the answers the person gave.
- **The branch name and your repository's raw path** — `tests/test_ambient_outbox.py`, which drains a live fixture repo and searches the sent bytes for that repo's **actual** toplevel path and its **actual** branch name, both read out of `git` at test time rather than written by hand.
- **Everything else in the list — including diffs and your repository's bare directory name — is carried by the payload's SHAPE, not by a search.** Both payloads are closed key sets pinned against `tests/fixtures/wire/session_activity_conformance.json` and `session_events_conformance.json`, so there is no field for a diff or a name to travel in, and the event rows are pinned to a closed per-row key set on top of that. That is a real guarantee and a *different* one from a needle search, so it is stated as its own kind. **Declared, because it is the hole:** no test plants a diff, or your repository's bare directory name, into the ambient lane's inputs and then looks for it on the wire. And `session_activity_conformance.json` is **not** evidence for any member of this list — its own negative-space test bans a hand-written list of generic tokens (`/Users`, `/home`, `userId`, `company`, `Bearer `, `gitBranch`, …), and that fixture's input row contains no prompt, tool input, path, command, diff, branch or repository name to search for in the first place.

One thing it *does* name: **tool names ship whole**, so an MCP tool arrives spelled `mcp__<server>__<tool>` — any MCP server whose tools were actually called is named by the rows (a configured-but-unused server is not).

**When.** At session start, in a detached background process, which delivers the sessions that have already **ended**. In practice a session is sent when the next one opens, not while it runs.

**Precondition.** A Fairmind MCP entry configured **for this project** — an entry configured for your user account does not count — and no opt-out in the repo-root `.fairmind-insights.json`. The gate is re-evaluated every session, so a change to that file takes effect on the next one.

⚠️ **Two things that "for this project" does not promise, both verified in the code.** First, the switch is evaluated when a session *opens*: a session that had already registered when you flip it is still swept, spooled and delivered afterwards — flipping it stops the next session, not the one in flight. Second, **linked git worktrees share one delivery queue but not one destination**: the queue is keyed on the repository's common directory, which every linked worktree shares, while the endpoint and credential are resolved from whichever checkout happens to run the delivery (`run_drain` against `fairmind_delivery_target`, both in `scripts/_insights_session.py`). So a session recorded in one worktree can be delivered under another worktree's Fairmind entry. If your worktrees are configured against different Fairmind projects or different credentials, treat that as a real crossing rather than a theoretical one.

### The Studio write-back — not a capture lane

**What it sends.** Two Fairmind MCP calls, attempted independently so a failure in one never masks the other:

- `Studio_bulk_update_status(ids=[<task_id>], status="passed", entity_type="task")` — the Studio task id and a status word. Nothing derived from your code.
- `Studio_process_journal(task_id=…, journal_content=…, project_id=…)` — and `journal_content` is **the journals' file contents**, not a path. Verbatim from `commands/fairmind-loop.md`: *"read every `${FAIRMIND_BASE}/journals/<taskRef>_*.md` matching this task ref (concatenating them when more than one agent journaled) and pass the combined text"*. The journals are the narrative **why** — free prose an agent wrote about your codebase, its problems and its decisions. What matters about this call is a property rather than a ranking, and the property is that the text is **unbounded and verbatim**: no schema constrains what a journal may contain, so whatever an agent wrote about your code is what gets sent. `/fairmind-loop` and `/fairmind-develop` are the two commands that make it.

**When.** Only after a **human approves** — never on a green gate. `/fairmind-loop`'s Exit fires it once you approve a `passed_pending_human` loop; `/fairmind-develop`'s Exit asks for it as a **second, separate request** after you approve the run. It is per **task** and driven by a person, not per session and not on a schedule, and the model makes the calls.

**Precondition.** The Fairmind MCP connected with both `Studio_*` tools present, plus `editor+` on the project. A missing tool or a denied write does not fail the close — each is reported as *not synced* in the Exit report.

**No on-disk queue, and that cuts both ways.** Unlike the insights flush there is no payload written to disk and no cursor: nothing sits on your machine waiting to be sent, and a call that never happened is never picked up later. The only recovery named is re-running the loop to its approved Exit.

### The brain write-back — not a capture lane

**What it sends.** Fairmind MCP calls, opened by seven different things. `Brain_record_requirement`, made only by the two requirement skills — `brain-rebuild-requirements` and `brain-new-requirement` — and only when a person runs one of them. `Brain_add_document`, made by the `brain-add-document` skill and on the same terms; its payload is not a draft, so it has its own paragraph below. The same door is opened by `brain-onboard` when a person asks it to write a project brief — a document of one particular kind, whose different bound gets a paragraph of its own. `Brain_record_decision`, `Brain_record_issue` and `Brain_supersede_decision`, made by the `brain-record-decision` skill when a person asks for it — the only caller of the third — with a paragraph of their own below. `Brain_record_component`, made only by the `brain-extract-components` skill when a person asks for it, with a paragraph of its own below. And, since 2026-09-12, `Brain_record_decision` and `Brain_record_issue`, made by `/fairmind-loop` at its final human gate, **after** you approve a `passed_pending_human` loop and never on a green gate alone. The seventh is `/fairmind-sync-insights`, which makes `Brain_record_decision` whenever somebody runs it, in any session and with no loop: it proposes every decision the agents logged on disk and typed `architecture` that no earlier run proposed — the loop half's rows, through the same door and with the same payload — and it asks for **no approval** before it sends; once somebody runs it with the Fairmind MCP connected, the `brain` switch is the only thing that stops the send. Each requirement call carries a requirement **as the agent drafted it**, plus the evidence under it:

| What travels | Detail |
|---|---|
| The draft itself | A title and a body of **free prose an agent wrote about your system** — the same property that matters on the Studio write-back matters here: no schema constrains what a drafted requirement may say, so whatever the agent wrote is what is sent |
| Its anchors | Repo-relative **file paths**, **symbol names** (a function or a class), a **line hint**, and — when a repository is bound — the **repository's catalog id** (`repository_id`, sent so the platform can resolve the anchor against the code graph), naming the code the draft is derived from |
| Its grounding | The **node ids** of the platform records the draft rests on, and short **quoted extracts** from them — a retirement reason, a line of a decision |
| Its identity | A client-supplied key spelling out the **module name and the requirement's title**, and an agent label naming the run |

The same skills read the brain first, through `Brain_context`, `Brain_search`, `Brain_get`, `Brain_expand` and `Brain_timeline`. Those are inbound queries and are not a route of their own — but the **query strings** go out with them, and a query is a directory of yours, a module name, or the idea in the words the user typed. `brain-onboard` reads through `Brain_overview` and `Brain_context` and, in its reading mode, writes nothing at all — but its per-file read carries the **repo-relative path** of each file the developer opens. Asked to write a brief, it also calls `General_list_projects`, which sends nothing beyond the call itself, to learn the project's name for the brief's title. **`brain-impact` makes the same kind of reads and writes nothing** — no requirement, decision, issue or document — so it opens no route of this section. Its queries reach further, though: repo-relative **file paths** and directories, a requirement's title or the idea as the user typed it, and **symbol names**, which it sends to `Code_find_usages` to ask the code graph who uses them. When an anchor names a file and no symbol, it reads that file through `Code_cat` — the path goes out, the file's text comes back — to find the symbols it defines, and sends their names the same way.

**What the loop half sends.** The decisions the run's own agents recorded and typed
`kind: "architecture"` — the same `fm-insights.decision/2` rows the insights lane carries,
filtered to that one kind, so the title, the free-prose rationale, the repo-relative paths
and the symbol names described for the Studio write-back apply here too. Plus one issue per
finding the loop surfaced and did not fix: an advisory or quarantined check still red at
close, or a completeness review that recorded gaps.

**What the document door sends.** One document per call. Its **kind**, a one-line **title**, and a
**summary the agent wrote** — free prose, on the same terms as the drafts above. Then
exactly one of three things: the document's **whole body** — whatever the agent puts there, written in the
session or read into it; a **link** to where it lives — the link is what is sent, and the skill does not
read what is behind it; or the **id** of a file already uploaded to the project. With
them go the ids of the records the document is evidence for, and an agent label.
**The body is what sets this door apart:** every other call in this section carries prose
*about* your system, and this one can carry a document of yours verbatim. **And the
project is optional:** the skill leaves it out for a document meant for the whole company
rather than one project, and makes that choice per document. **Before it sends, the skill
shows what is about to leave — title, summary, where it will be visible, and whether the
body travels — and asks.** That is an instruction the skill gives the agent, not a lock:
nothing on this machine enforces it.

**What the decision skill sends.** `brain-record-decision` sends records the agent drafted
in the session, one call per kind. A **decision** travels as the same `fm-insights.decision/2`
row the loop half sends: a title, a free-prose rationale, the **options considered** and why
each was rejected, its **consequences**, repo-relative **file paths** and **function names**,
the node ids of the requirements and documents it rests on, the repository and project it is
recorded against, and an id the skill builds from the project id and the title. An **issue**:
its kind, a title and a free-prose body, anchors — repo-relative paths, symbol names and a
line hint, with the repository's catalog id when one is bound — **quoted evidence**, the id of
a decision that caused it, and a key the skill builds from a module name and the title so
that a second run replaces the issue the first one filed instead of filing another. A **supersession**: the id of
the decision it replaces, the **reason** in the person's words, and the replacement as a full
decision row. **Before it sends, the skill shows each record — title, rationale or body,
anchors, and where it will be recorded — and asks.** Like the document skill's question,
that is an instruction to the agent, not a lock. **Nothing it records is written to
`.fairmind/insights/decisions.jsonl`**, the local log the loop lane flushes, and a decision
needs no row there to be counted: the brain door stores it in the same decision store
Agentic Insights reads, and the insights decision read returns it beside the loop's own.
**What it does not carry is a session:** the skill sends no session id and no task ref, so
none of its records is keyed to a session the way the loop lane's decisions are.

**What the brief sends.** `brain-onboard`, asked to write or regenerate a brief, makes one
document call with the kind `brief`, for one project or — with the project left out — for the
whole company; the platform accepts a company brief only from a key that names no project. The
brief's **whole body** travels: prose the agent wrote about your project from
what the brain returned, citing the **node ids** of the records it rests on, with a title, a
summary the agent wrote and an agent label. Before it sends, the skill shows the diff against
the version the brief replaces, where it will be visible, and that the whole brief travels, and
asks — the same instruction-not-a-lock as above.

**What the component skill sends.** `brain-extract-components` reads the blueprints already
filed in the brain and sends the components the agent read out of them, up to twenty per call.
For each: its **name** and, when it is already recorded, its **key**; the **responsibility**
and description the agent wrote from the
blueprint — free prose about your system, on the same terms as the drafts above; the
repo-relative **directory paths** that implement it; its subtype, layer, **technology**,
**owning team** and status (planned or deprecated) where the blueprint names them; and the
names, keys or node ids of the components it belongs to and depends on, with how it depends —
calls, reads or publishes. With
them go the **repository's catalog id**, the project id, the **blueprint's node id** and an
agent label. The skill always names the project and offers **no company-wide scope**. Its reads
before that — the blueprint through the brain, the directory layout through the code graph —
send filters, node ids and directory paths, and no query text. **Before it sends, the skill
shows every component and where it will be visible, and asks.** Like the document skill's
question, that is an instruction to the agent, not a lock. Every component lands as a
**proposal**; one the platform finds close to a component already recorded is queued for a
person to decide, and never merged by the skill.

**When.** For the skills: only on a run of one of them — the two requirement skills at the
write step near their end, `brain-add-document` once for the document it was asked to file,
`brain-record-decision` once the person has approved the records it drafted,
`brain-extract-components` once the person has approved the components it drafted,
`brain-onboard` once per brief, and only when a person asked for the brief to be written.
For the loop: only after a human approves a `passed_pending_human` loop, in the same block
as the Studio write-back and on the same terms. For `/fairmind-sync-insights`: whenever somebody runs it, with no approval asked. Every requirement, decision, issue and component this route writes is recorded as a **proposal** for a person to confirm or reject on the platform; the requirement skills, `brain-record-decision` and `brain-extract-components` confirm nothing themselves and each says so in the output it prints. **A document is the exception: it is filed, not proposed.** Only a change to a card a person had already confirmed comes back as a proposal. **A supersession is half an exception:** the replacement is recorded as a proposal, but the decision it replaces stops being current the moment the call lands — marked superseded, still readable in its history — without waiting for anybody. **A brief is one document per scope** — one per project, one for the company — so writing it again rewrites a draft nobody has confirmed yet, and a regenerated brief a person had already confirmed arrives as a revision proposal carrying the old and the new text, while the confirmed one keeps being served.

**Precondition.** The Fairmind MCP connected for this project with the `Brain_*` tools present — a server older than the platform release that serves them exposes no such tool, and the skills stop rather than degrade — plus a tenant provisioned for brain writes. **No switch reaches the skills half of this route, and the reason it needs none is that its trigger is a person:** the two lanes above fire on a session opening or a flush somebody runs for other reasons, while this door opens only because the skill was asked for by name. Not running the skills is the whole control. **Two things differ for `brain-add-document`.** Its tool, `Brain_add_document`, arrives with a later platform release than the other `Brain_*` tools, so it checks for that one by name and stops without it. And it is the one skill here an agent may start on its own, when a document comes up in a session — which is why it asks before it sends, as described under the controls below. `brain-onboard` checks for its own read door, `Brain_overview`, the same way — it too arrives with a later release — and for `Brain_add_document` before it writes. **`brain-extract-components` shares the first of those:** `Brain_record_component` also arrives with a later platform release, so the skill checks for it by name and stops without it. It also needs the repository bound to the checkout by `/fairmind-connect`, because every directory it sends is resolved in that one repository.

### The agent signature on pull requests — not a capture lane

**What it sends.** Nothing to Fairmind. It adds two values — an agent name and **the Claude Code session id** — to the text a person asks one of the three posting commands to put on a pull request. The format, and what the marker does not prove, are under [Agent signature on pull requests](#agent-signature-on-pull-requests).

**Where it goes.** To GitHub, inside the posted text, under your own `gh` credential. GitHub does not display it, but anyone who can read the pull request can read it through the API, which returns the raw text — on a public repository, the public.

**What it joins.** That session id is the one that keys the ambient record, the loop record and the decisions and harness-audit payloads, so whoever holds those Fairmind records can tie a signed post to the session that wrote it and to what that session decided. A reader of the pull request who does not hold them learns that an agent session wrote the text, and that session's opaque id.

**When.** Only when a person asks `/gh-commit`, `/gh-review-pr` or `/gh-address-pr-comments` to post, and confirms the text.

**Precondition.** `gh` authenticated for the repository, and a host that sets `CLAUDE_CODE_SESSION_ID`; without it nothing is posted. No switch reaches it, because an agent's post without the marker is indistinguishable from the person's own writing: the control is not asking a command to post.

### Which route fires in which mode

The loop lane's three doors are listed separately here, because they do **not** share an answer:

| | Interactive session | Loop run |
|---|---|---|
| **Ambient session records** | sent, on the preconditions above | sent, on the same terms — a loop session is also a session |
| `Insights_record_loop_stats` | whatever loop record is on disk to flush, if any — see below | sent at close, and by any sync run before it |
| `Insights_record_agent_decisions` | **sent — no loop is needed**, and `/fairmind-develop` declares this door too | sent |
| `Insights_record_harness_audit` | **sent after a `/harness-audit` run — no loop is needed** | the same; `/harness-audit` is its own command, not a loop step |
| **The Studio write-back** | not fired on its own; `/fairmind-develop`'s Exit asks for it after you approve that run | after you approve a `passed_pending_human` loop |
| **The brain write-back** | **sent when you run `brain-rebuild-requirements`, `brain-new-requirement`, `brain-add-document`, `brain-record-decision` or `brain-extract-components`, or ask `brain-onboard` to write a brief** — no loop is needed for that half of the route — and the architecture decisions the agents logged go out **when you run `/fairmind-sync-insights`**, with no approval asked | the same, **plus** the decisions and issues `/fairmind-loop` proposes after you approve a `passed_pending_human` loop. The brain skills are still not loop steps and a loop does not run them; the loop opens the door itself, with its own payload |

**On that second row, measured rather than reasoned.** Reproduced through the shipped CLI on a repo that had **never run a loop** — `mode: "interactive"`, no `loop-state.json` anywhere on disk: `insights_flush_payload.py --emit all` returned a **non-null decisions payload** carrying the repository name, the `origin` URL in plaintext, an agent's free-prose decision title and rationale, a repo-relative file path, and a function name with its line number. The same call also returned a **non-null `loop` payload** — on that particular repo a shell whose `status` and `target_ref` are null, but which still carries the `task_ref` and `project_id` read out of `active-context.json`. (Do not read a null field as a promise of one: on a repo whose `loop-state.json` *is* present but still running, the same call filled `status`, `target_ref` and `closed_at` in, per the ⚠️ under **Lane 1**.) Nothing is sent without somebody running a flush and a Fairmind MCP being reachable, but "no loop ever closed here" is not what stops it.

The routes are separate requests to separate doors, and none is derived from another. **They are still joinable, and by more than the two records the join is easiest to notice on.** The same Claude Code session id keys the ambient record (`sessionId`), the loop record (`owner_session`) **and** the decisions and harness-audit payloads, which carry it as `session_ref` (`build_decisions_batches` and `build_audit_payload` in `scripts/insights_flush_payload.py`); those two additionally carry your `project`, and the decisions payload your `task_ref` too. The decisions stamped this way are the ones recorded since the current loop or develop run opened; any earlier pending decision leaves in a separate batch that carries neither, and so does every pending decision when `active-context.json` holds no `decisions_start_line` to say where the run opened. So a holder of the delivered records can align an ambient session with the loop record, the decisions and the audit run it produced.

### Switching each route off

**The ambient lane has a switch, and since 2026-08-21 it is the SECOND of three layers.** Above it sits a per-Fairmind-project **central policy** the platform pushes (see [Central policy](#central-policy) below): where it speaks it forces ambient capture on or off for that project and beats everything local, in both directions. Where it is silent — no policy for this project, no cached answer yet, a cached answer past its offline window, or a value outside the closed `on`/`off` vocabulary — the answer is **unset, not off**, and the local switch below decides exactly as it always has. Where both are silent, ambient capture is on wherever a per-project Fairmind MCP is configured. The local switch is a committable `.fairmind-insights.json` at your repository root, and that scope is deliberate: no per-user setting can change the outcome in either direction — nothing under the developer's own `~/.fairmind/` is read at all. You can flip it by hand or with `/fairmind-config ambient on|off|unset`, which writes that same file and is the safer of the two (see the trap below). ⚠️ **The guarantee is narrower than "the developer cannot change it", and stating it wider would be false:** these reads take the **file**, never git, so an *untracked* `.fairmind-insights.json` written straight into the work tree is indistinguishable from the one the company committed (reproduced: `git ls-files` does not list it, `git status` reports `??`, and the switch still reads as off). What is enforced is that the switch is a *repository* file rather than a personal setting; a central force sits above that file, but it does not make the file unforgeable, and nothing here is enforcement in any case — client-side policy is configuration this plugin obeys, not a control it can impose, and enforcement is platform-side absence detection. That is why this file was called an interim proxy for a platform decision, and it remains the fallback now that the platform can speak. ⚠️ **It stops the NEXT session, not one already in flight** — a session registered before you flip it is still digested and delivered (see the precondition above). A **central** flip binds one step later still: the next session **after a successful fetch**, since the plugin reads its cached copy rather than the platform. This turns the ambient lane off for sessions opening after it, and withholds all three consent classes:

```json
{
  "ambient_capture": false,
  "event_skeleton": false,
  "consent": {
    "merged_diffs": false,
    "rejected_proposals": false,
    "generation_context": false,
    "content": "references"
  }
}
```

⚠️ **The trap: a `.fairmind-insights.json` that exists without `"ambient_capture": true` reads as an opt-out.** Only an absent file, or the explicit boolean `true`, leaves the ambient lane on. So a repo that adds a `consent` block to *narrow* what it sends switches the ambient lane off entirely unless it carries `"ambient_capture": true` alongside — run against the shipped resolver, `{"consent": {"merged_diffs": true, "rejected_proposals": true, "generation_context": true}}` grants all three classes **and** opts the repo out of ambient capture. **`/fairmind-config` is the safe writer for that file**, because it never leaves `ambient_capture` to be inferred: every write spells it out as a boolean. `ambient unset` writes `true` and says so, because a file that exists cannot express "no opinion" — the fail-closed rule above is what makes silence a decision. Every other key keeps its value and its order (the file is re-emitted with two-space indent, so hand formatting is normalized), and the command refuses rather than guesses: a feature the platform has centrally forced, a file that does not parse as a JSON object are each refused with the file left byte-identical. To narrow while staying on, write both:

```json
{
  "ambient_capture": true,
  "consent": {"merged_diffs": true, "rejected_proposals": false, "generation_context": true}
}
```

🔴 **The loop lane has no switch.** There is no key in that file, or anywhere else, that stops it. `"ambient_capture": false` does not reach it: measured on the shipped resolver, that file silences the ambient lane while leaving all three consent classes granted and the loop lane sending. The `consent` block below narrows *what* the loop record carries; it never switches a lane off, and it does not reach the decisions or the harness-audit door at all. The controls that exist today are operational, not configuration: **do not connect a Fairmind MCP in the session**, and **do not run the flush step** — the flush at the end of `/fairmind-loop` and `/harness-audit`, and `/fairmind-sync-insights`. Both are actions a person takes or omits in a session; neither is a decision the repository can record the way `.fairmind-insights.json` records one.

🔴 **The Studio write-back has no switch either, and its control is a person saying no.** There is no key in `.fairmind-insights.json` — or anywhere else — that reaches it, and the `consent` classes below do not touch it. What stands between your journals and the platform is a human approval — but **the two commands differ on how much of a decision that is, and the loop-mode answer is the weaker one**. `/fairmind-develop` puts the write-back behind a request of its own, made after you have already approved the work, so it can be declined on its own terms. `/fairmind-loop` does not: approving a `passed_pending_human` loop is itself the trigger, so in loop mode there is no separate point at which to say no — declining the write-back means declining the approval of the work. Not connecting a Fairmind MCP stops it either way, the same way it stops the loop lane. Stated plainly, in the same terms as the paragraph above: that is a **procedure, not a configuration**. Nothing in the repository records the decision, nothing carries it from one run to the next, and nothing prevents a later run being approved. Its own asymmetry is worth knowing in both directions — with no on-disk queue, a write-back you decline leaves nothing behind that a later sync could pick up.

🔴 **The brain write-back has two halves and they do not share a control.** What they share is what the platform does with a requirement, a decision, an issue or a component: each lands as a **proposal** for a person to confirm or reject — though a supersession's replacement does while the decision it replaces stops being current at once. **A document does not** — `brain-add-document` files it, so that sentence stops at the four record kinds and a document's only human check is the one the skill asks for before it sends. `brain-onboard` writes one document, its scope's brief, and a brief a person had confirmed is never overwritten by a regeneration: the new text waits for a person as a revision proposal. And not connecting a Fairmind MCP stops both, the same way it stops the loop lane and the Studio write-back.

**The skills half's control is the narrowest of any route: nobody ran the skill.** No key in `.fairmind-insights.json` reaches it, the `consent` classes below do not touch it, and the two requirement skills ask no approval **before** they send — they write at the end of a run somebody asked for by name. Their gate sits on the far side of the send rather than in front of it, which is a real control and a different one from the Studio write-back's pre-send approval: the two lanes fire on a session opening or on a flush run for other reasons, whereas nothing opens the requirement door unless `brain-rebuild-requirements` or `brain-new-requirement` was invoked. **`brain-add-document` is the other way round:** nothing on the far side holds a newly filed document for anybody's confirmation — only a change to a card a person had already confirmed comes back as a proposal — so its check sits in front of the send, where the skill asks. It can also be started by the agent rather than by name, when a document comes up in a session; the question before the send is what stands between that and a document leaving. **`brain-record-decision` carries both checks:** it runs only when a person asks for it, and it shows each record and asks again before it sends. **`brain-onboard` has a check on both sides:** it writes only when a person asked for a brief, asks again before the send, and what it sends waits on the platform for a person to confirm. The half an agent may start on its own — reading a brief at the beginning of a session — writes nothing. **`brain-extract-components` carries both checks:** it runs only when a person asks for it, and it shows the components it drafted and asks again before it sends — and what it sends still lands as a proposal. Like the others, this is a **procedure, not a configuration** — the repository records no decision about it.

**The loop half's control is a repository switch and an approval, and the approval is the weaker of the two.** `brain` in `.fairmind-insights.json` — written by `/fairmind-config brain on|off|unset`, with the platform's central policy above it — is a decision the repository records and carries from one run to the next, which is what the skills half has never had. **It binds in the producer**, not only in the command that reads it: the script that builds the payload consults the switch itself, so a caller that never read the instruction cannot send the rows either, and a value present but not a boolean reads as off rather than as consent. Beneath it sits the human gate, and there the loop-mode answer is the same weak one the Studio write-back has: **approving a `passed_pending_human` loop is itself the trigger**, so there is no separate point at which to decline the send without also declining the approval of the work. The command names what it sent, record by record, in the Exit report; that is disclosure after the fact, not consent before it. **The approval covers the loop's own send and nothing else:** `/fairmind-sync-insights` proposes the same rows whenever somebody runs it and asks nobody first, so for that opener the switch is the only control the repository records.

⚠️ **`brain off` is not "these decisions stay on this machine", and it must not be sold as one.** The architecture rows it stops here are the same rows the **loop lane** carries as `decisions`, and 🔴 the loop lane has no switch. Turning `brain` off narrows which doors a decision goes through; it does not keep it on your machine. Nothing short of not connecting a Fairmind MCP does that. **Nor does `brain off` reach `brain-record-decision`:** the switch governs what the loop and `/fairmind-sync-insights` propose, and a decision or an issue a person asks that skill to record goes to the brain whatever the switch says.

### Central policy

The layer above `.fairmind-insights.json`, added 2026-08-21. Four features are in
its perimeter and no others:
`brain` (the brain write-back at a loop's human gate),
`ambient_capture` (the ambient capture lane), and the two grants that raise the
`purpose` of captured content above `local_only` —
`purpose_customer_only` and `purpose_fairmind_training`. For each, a Fairmind
project can be set to `on` or `off` on the platform; anything else — including a
project with no entry at all — is **unset**.

⚠️ **What "unset" then means splits the perimeter in two, so read the row you
care about.** For the two **switches** (`brain`, `ambient_capture`) unset hands
the decision to your repository file, exactly as described above. For the two
**`purpose` grants** there is **no local fallback at all**: your repository file
cannot raise a rung, deliberately, because a contractual use right should not be
something anyone who can edit a file in the repository can grant themselves.
`/fairmind-config` does not write them for the same reason, and unset simply
means ungranted. The full rule, with the cases it covers, is stated once where a
customer asks the question — [What it may be used for](#what-leaves-your-machine),
in the content-capture section — and deliberately not restated here, because two
copies of a consent rule drift and the drifting half is the one someone reads.

**It is one more outbound request, so it belongs on this page.** At most once per
session — and not at all where no per-project Fairmind MCP credential resolves —
the detached background process the plugin already spawns at session start issues
a single `GET /insights/v1/client-policy` against the door **derived from this
project's own Fairmind MCP url**, carrying that entry's own bearer and nothing
else. The session-start hook is its only caller, so it is one request per session
opened, never one per turn or per tool call. **Beyond that bearer, nothing of
yours goes out on it:** no session row, no digest, no file path, no repository
name, no branch — it is a read, and its request body is empty. The bearer and
the host ARE the request; everything else about your machine stays on it. It is
also, deliberately, **not** gated by the ambient off switch: a read is not
capture, and a central force has to be able to reach a repository whose ambient
lane is muted. It is the only place the plugin ever calls that endpoint; the
session-start gate reads the cached answer alone, so it costs you no network
round trip.

**Which project's policy applies is resolved on your machine**, in this order:
the `projectId` claim carried by the MCP entry's own bearer, then the project
named in `.fairmind/active-context.json` at your repository root. Where neither
names a project, the only entry that can apply is the payload's company-wide
default, so such a checkout is governed by the company default and by nothing
project-specific — and that default is consulted per feature, so a project that
sets one feature and not the other still takes the company answer for the one it
left alone. The claim is read for **selection only and never for
authorization** — the plugin decodes it without verifying any signature, because
all it does with it is pick which entry of an already-served policy map is yours.

**The answer is cached per checkout** at
`<data_dir>/insights/policy/<sha256 of the checkout's real path, truncated>.json`
— `~/.fairmind` by default, moved by `FAIRMIND_INSIGHTS_HOME`, and named by a
hash so no path of yours appears in a filename. The cache carries a **7-day
TTL**, and that number is the **offline window, not a convergence delay**: every
session start attempts a refresh, so a reachable platform takes effect on the
next session. Offline, the last successfully fetched answer keeps binding until
it is older than the TTL, after which the local file decides again. Any failure —
no credential, a connection error, a non-200, an unparseable or over-long body, a
200 that is not the policy document (a gateway's maintenance page, a version this
plugin does not speak), or any phase of the request — connect, headers or body —
still under way when the bounded wall-clock budget expires (the network half runs
on a worker the sweep abandons at the deadline) — leaves the previous cache
byte-for-byte untouched. So does a
cache stamped in the future or carrying a hand-made TTL longer than the shipped
one: neither can extend the offline window, because a force nobody can place in
time is treated as no force at all.

**The one exception, and it is the one to know if you re-point a checkout.** The
cache is named after the checkout's path, so pointing the same working copy at a
different Fairmind project or company — a new MCP entry, a new bearer — lands on
the same cache file. Each cache therefore records the door it was fetched from,
and a failure against a **different** door deletes it rather than keeping it: a
force must not outlive the credential that granted it, so the new company's own
answer (or, until it is reachable, your local file) decides instead of the
previous one's. ⚠️ **The residual is one session.** Both readers consult the
cache at the start of a session, before that session's refresh has run, so the
first session after such a swap can still apply the previous project's answer;
from the next session on it cannot.

**What a force does, and what it does not.** A central `off` on `ambient` stops
**new** capture for sessions opening after it is read; it is not the explicit
local `false` that discards already-captured, unsent data, and it leaves anything
already queued where it is. A central `on` runs the feature and
the local file is not even consulted. None of it is enforcement: this is
configuration the plugin obeys, and a developer who disables the plugin is
outside its reach either way. The ambient lane has no environment escape.

**To see what is in force here**, run `/fairmind-config`:
it prints the effective state of each feature and which layer decided it —
centrally forced, naming the project; repo file; or default — alongside whether
the cached policy is fresh, stale, or has never been fetched for this checkout.

### The consent classes

The same file carries an optional `consent` block, sorting what may leave into three classes granted independently:

| Key | What it covers |
|---|---|
| `merged_diffs` | What the work changed: the artifacts a loop produced, and the size and shape of its diff |
| `rejected_proposals` | The work that did *not* pass: the iterations the gate failed, and the human verdicts recorded on them |
| `generation_context` | How the work was produced: which agents ran, the cadence and lifecycle of the iterations, and — on the ambient lane — tool counts, skills and the event skeleton |

**The block narrows a grant that already exists — it is not an opt-in.** Read backwards it would look like a way to enable something; it is the opposite. The defaults, measured on the shipped resolver:

| The config | What is granted |
|---|---|
| no file at all | all three classes |
| a file with no `consent` block | all three classes, recorded as *defaulted* rather than *decided* |
| inside a present `consent` block | each class is off unless its value is the literal boolean `true` — `1`, `"true"`, `"yes"` are all off |
| an unparseable file, or a `consent` that is not an object | no class at all |

**`content` is a separate switch, and the only one in this plugin that reaches the text of your code.** It is stated as three promises rather than one, because collecting, sending and using are three different things — and one sentence about all three is how the previous version of this paragraph came to be untrue.

**What is collected.** `"references"` is the default and is what every config that does not name the other value resolves to: an absent file, a file with no `consent` block, an unparseable one, a block without the key, and a block that sets it to anything unrecognised. Under it no diff, no snippet and no file content is recorded by this plugin anywhere. The one other accepted value is `"failed_iterations"`, and it is an opt-in **in two parts**: it does nothing unless `rejected_proposals` or `generation_context` is *also* set to the literal boolean `true` in the same block. What it then authorizes is bounded to one thing — when the loop gate **rejects** an iteration, the diff of the files that iteration changed, measured against the commit the loop armed on, plus this plugin's own account of why it was rejected, as check ids, verdicts and a fixed vocabulary of failure categories. Not your conversation with the model, not your prompts, not the text of your check commands, and not their output. **A file git is not tracking IS captured** if the iteration created it — a new source file is exactly the work the gate refused — so the exclusion that holds is *git-ignored*, not *untracked*.

**What is sent: nothing.** This build has **no route that delivers captured content** — it is written under `~/.fairmind` on the developer's machine and stays there. Two consequences follow, and both are deliberate. It **outlives the repository**: `~/.fairmind` is outside your checkout, so `git clean`, deleting the branch and deleting the whole working copy all leave it in place. And it **grows**: nothing drains or trims it. `python3 scripts/_insights_session.py --insights-status`, run from the repository, reports how much is there.

**What it may be used for — three rungs, and you get the narrowest unless a contract says otherwise.** Every captured record is stamped with a `purpose`, frozen at capture time so a record collected under one purpose can never be quietly re-read as authorizing another. The vocabulary is closed:

| `purpose` | how far the record may travel | who it may serve |
| --- | --- | --- |
| `local_only` | it never leaves this machine | the developer who ran the loop |
| `customer_only` | it may leave the machine, but never leaves your tenant | your organisation, all of its developers |
| `fairmind_training` | it may enter the FairMind training corpus | every customer, via the shared base model |

⚠️ **`local_only` says *machine*, not *checkout*, and the difference is deliberate.** The record is written under `~/.fairmind`, which is outside your working copy — so deleting the branch, running `git clean`, or deleting the whole checkout all leave it in place, exactly as the paragraph above says. `local_only` is a statement about *use and travel*, not about where the bytes sit.

**Opting into capture grants `local_only` and nothing more.** The two wider rungs are granted only through your [central policy](#central-policy) — a contractual decision, deliberately not something a file in your repository can turn on.

**Only the literal `"on"` raises a rung. Every other answer leaves the record at `local_only`**, and that is the whole list: the feature set to `"off"`, your project carrying no entry, no policy for your organisation at all, no cached answer yet, a cached answer past its offline window, a cache the plugin could not read, and any value outside the `on`/`off` vocabulary — `"ON"`, `true` and `1` included. This reads the opposite way to the perimeter's *switch* (`ambient_capture`), where an answer the plugin cannot read is treated as a force: for a switch that means less review or less capture, and for a grant the same instinct means *no* grant.

⚠️ **A grant survives an outage; it does not need the platform to be reachable at capture time.** The plugin reads a cached answer and never calls out on this path, so once your policy has been fetched, a rung keeps applying for as long as that cache is inside its offline window even with the platform completely unreachable. ⚠️ **That window is measured from the FETCH, not from the outage:** it is 7 days old at most, so a cache fetched six days before the platform went down keeps granting for one more day, not seven. "Unreachable" is therefore not on the list above. What ends a grant is the cache going stale, being replaced by a fetch that no longer carries it, or being dropped because the checkout now answers to a different Fairmind project.

*(Until 2026-08-21 this paragraph said every record was stamped `purpose: "training"` and that the build wrote no other value. That was true of the code and wrong as a promise — it labelled every record at the widest rung of a ladder that did not exist yet, and the stamp does not get relabelled later. The sentence is replaced rather than patched.)*

**The residual, stated rather than scrubbed.** A unified diff carries the lines a change **removed** as well as the ones it added, so a secret that was committed in a tracked file and gets deleted during a loop is captured *by* the deletion. There is no secret scanner on this path and this page will not imply one: a filter that catches some secrets and is described as protection converts "nobody checked" into "checked and fine". The exclusions that do hold are per-path and verifiable — a file git IGNORES is never part of a mutation set at all, and a path marked `-diff` in `.gitattributes` contributes no bytes. An untracked file is not one of them: if the iteration created it, it is captured. Setting `rejected_proposals` and `generation_context` to the literal `false` deletes what was already captured — at the next REFUSED iteration, or the next session start, whichever comes first. Both are named because neither alone is enough: a green evaluation does not reach the reclaimer, so a loop that goes green and never opens another session would otherwise wait on a refusal that never comes. Granting one class and not the other gets exactly one of the two halves: `rejected_proposals` alone captures the diff and no failure account, `generation_context` alone captures the account and **no diff and no file paths**.

Withholding a class **empties its own fields**, and the payload carries both the classes granted when the data was collected and the classes actually applied — so a field emptied by a withheld class stays distinguishable from one that was genuinely empty.

**What the classes do not cover**, stated because reading them as *the* instrument would be wrong in the direction that matters:

- **Not the loop record's remainder.** Measured by running the shipped builder with all three classes withheld: the loop door still sends `task_ref`, `target_ref`, `project_id`, `loop_id`, `owner_session`, `status`, `tier`, the check count, the iteration count, both timestamps, and the structural rows of the iteration timeline. Your ticket identifiers and the kind of work travel under no class.
- **Not two of the three loop doors.** The decision log and the harness-audit payload carry no consent stamp, and their builders take no class list. The repository name, the `origin` URL, the agents' free prose, the file paths and the function names sit outside this instrument entirely.
- **Not the Studio write-back.** It is not built by either wire builder, takes no class list and carries no stamp, so no `consent` value narrows the journal text it sends. Its only control is the human approval described above.
- **Not a deletion promise.** A class decides which bytes leave this machine. Revoking one stops future sending; it does not reach back into what was already delivered. What happens to a record after it arrives — retention, scanning, deletion — is decided elsewhere and is not claimed here.
- **Not enforcement.** A client-side switch cannot stop someone disabling the plugin; it expresses a decision, it does not police one.

The fuller mechanics — the three-state read, and what an explicit `false` discards versus what an unreadable file retains unsent — are in `INTERNALS.md`, installed alongside this file in the plugin directory.

## Workspace contract

The plugin assumes a Fairmind session workspace rooted at:

```
.fairmind/
  active-context.json             { mode, fairmind, project, base_path, task_ref }
                                  + decisions_start_line, the line count of
                                    insights/decisions.jsonl when the run opened
                                    (0 when empty); absent = flush unattributed
                                  + once /fairmind-connect has run: project_id,
                                    repository_id, repository_name,
                                    repository_branch, repository_url, bound_at
  <project-slug>/<session-slug>/
    work_packages/
      ai/      backend/      frontend/      qa/
    journals/
```

The Technical Lead creates this on first run from a Fairmind work package. The other agents read `active-context.json` to resolve `FAIRMIND_BASE` and only write to scoped subpaths — the `validate-fairmind-path` hook will refuse anything outside that scope.

## Prerequisites

Required for standalone (loop mode) use:

- **`python3` 3.9 or newer**, **`git`**, **`bash`** on `$PATH` — the gate engine, its test suite, and the hooks are stdlib-only, no third-party dependencies. The `python3` macOS ships is enough. An older one, or none, is said at session start (`◆ Fairmind Python: …`) instead of leaving the hooks to fail silently.

Optional — only enable **connected mode** and the corresponding workflows:

- **Fairmind MCP** — `mcp__Fairmind__*` — platform context (projects, stories, requirements, tests, RAG). Absent → agents read local `.fairmind/` and operate standalone.
- **Playwright MCP** — the `QA Engineer` and `/fix-frontend-issue` browser workflows.
- **MongoDB MCP** — `Software Engineer` / `Code Reviewer` for NextJS/MongoDB stacks.
- **`gh` CLI** on `$PATH` — the GitHub commands.
- For `/sonarqube-fix`: `SONAR_TOKEN` env var and a `sonar-project.properties` file in the project root.

An absent **MCP server** degrades gracefully to standalone behavior, and absent `gh` or `SONAR_TOKEN` costs you only the commands that need them.

🔴 **`jq` is the exception, and it is not on either list above.** Outside a Fairmind
workspace nothing here needs it. Inside one — the moment `.fairmind/active-context.json`
exists — **three hooks refuse rather than skip** when it is missing, each with `exit 2`,
which is the code that blocks:

| Hook | Event | What stops |
|---|---|---|
| `validate-fairmind-path` | `PreToolUse` on `Write`/`Edit` | **every write** — *"an unchecked write into an active Fairmind workspace is refused"* |
| `check-journal` | `SubagentStop` | the sub-agent cannot finish — *"Refusing to let the sub-agent finish rather than skipping the journal rule"* |
| `inject-context` | `PreToolUse` on `Task` | the dispatch — *"dispatching a sub-agent that does not know the scoped path is refused"* |

Each prints that line on stderr and names the install command. The refusals are deliberate:
a write nobody checked against the scope, or a silently skipped journal rule, is worse than
a stop. But the consequence is that **`jq` is required once a workspace exists** — the path
guard makes it the point at which editing stops, not a degraded mode. `check-journal` needs
`find`, `git`, `sed`, `grep`, `head` and `tr` on the same terms.

## Install

From git:

```text
/plugin marketplace add FairMind-Gen-AI-Studio/fairmind-plugins-public
/plugin install fairmind-coding@fairmind-plugins
```

Verify:

```text
/agents     # Technical Lead / Architect, Software Engineer, Code Reviewer, QA Engineer, Debugging Specialist, Security Engineer listed
/help       # /fairmind-loop, /fairmind-add-check, /harness-audit, /fix-issue, /sonarqube-fix, /gh-* listed
```

## Examples

### Start a Fairmind task

Engage the tech lead first — the Technical Lead pulls the work package from Fairmind and bootstraps `.fairmind/`:

```text
> Technical Lead, prepare the work package for user story FM-1234
```

### Fix an issue with intelligent classification

```text
/fix-issue auth-login-broken
```

The orchestrator inspects the issue file under `./issues/`, classifies it (FE-FE / FE-BE / BE-BE), confirms with you, then dispatches the Software Engineer.

### Clean up SonarCloud issues for the current PR

```text
/sonarqube-fix
```

Runs `analyze_sonarqube.py` from the plugin's own `scripts/` directory, fixes issues by severity, runs `poetry run pytest`, and commits the result.

## Configuration

The plugin reads project-level configuration from:

- `.fairmind/active-context.json` — created by the Technical Lead, consumed by every other agent and by the hooks
- `./issues/*.md` (and image attachments) — input for `/fix-issue` and `/fix-frontend-issue`
- `./.claude/directive/*.md` — optional project-specific directives consumed by `/fix-frontend-issue` (the `*.example` file is ignored)
- `sonar-project.properties` + `SONAR_TOKEN` — required by `/sonarqube-fix`
- `.fairmind-insights.json` at the repository root — the committable file that governs the ambient capture lane and the consent classes. `/fairmind-config` is the safe writer for all of it; a central per-project policy can override the feature from the platform. See [What leaves your machine](#what-leaves-your-machine) and [Central policy](#central-policy)

## License

MIT
