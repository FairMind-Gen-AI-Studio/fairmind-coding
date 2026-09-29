# CLAUDE.md — fairmind-coding plugin

Instructions for Claude Code when this plugin is active. These rules govern when to invoke the plugin's agents and skills, and how the `.fairmind/` workspace is managed.

## What may be done with what is captured — the `purpose` ladder

Customer-facing, and here rather than in the maintainer notes because a grant nobody
can read is a grant nobody can act on. Every captured record carries a `purpose` stamp
from a closed three-rung vocabulary:

| rung | what it permits | who can ever see it |
| --- | --- | --- |
| `local_only` | it never leaves this machine | the developer who ran it |
| `customer_only` | it may leave the machine, never your tenant | your organisation |
| `fairmind_training` | it may enter the FairMind training corpus | every customer, via the shared base model |

**`local_only` is the floor and the default.** A rung above it is granted by contract
through the central per-project policy — features `purpose_customer_only` and
`purpose_fairmind_training` — and **never** by a file inside a repository: a checkout
cannot raise its own rung. `/fairmind-config` reports the effective state.

## Runtime contracts — where the reasoning went

The `.fairmind/` artifact roster, the hook roster, the capture-routing table,
and consent mechanics are in **`INTERNALS.md`**,
installed beside this file. It is documentation, not instructions: nothing auto-loads it,
so read it when a question needs it. It is the authority whenever a command, an agent or
the README defers to it — this page never restates its tables.

## When to invoke which agent

| Situation | Invoke |
|---|---|
| New Fairmind task / unfamiliar repo | `Technical Lead / Architect` first — bootstraps `.fairmind/` and prepares work packages |
| Implementing frontend / backend / AI code | `Software Engineer` |
| Test execution / Playwright automation | `QA Engineer` |
| Post-implementation quality review | `Code Reviewer` |
| Reproducing a hard bug | `Debugging Specialist` — no command reaches it; dispatch it by name |
| Auth, input validation, secrets, OWASP review | `Security Engineer` |

Never let the Technical Lead implement code — it is an orchestrator only.

## Skill selection

| Work | Skill |
|---|---|
| Pulling Fairmind context | `fairmind-context` |
| TDD with Fairmind acceptance criteria | `fairmind-tdd` |
| Plan↔journal↔code review | `fairmind-code-review` |
| Designing a loop-mode stop condition (gate layer) | `fairmind-gate` |
| Authoring a custom loop check | `custom-check-authoring` |
| Compiling an external ticket's criteria into check types | `task-compilation` |
| Recovering the requirements of a system that already exists, from the code and the brain | `brain-rebuild-requirements` |
| Drafting a new requirement after checking the brain for precedents and why they were retired | `brain-new-requirement` |
| Filing a document so the brain can cite it — a standard, a design, a blueprint | `brain-add-document` |
| Recording, when a person asks, a decision taken in the session, an issue left unfixed, or the replacement of a recorded decision | `brain-record-decision` |
| Starting on an unfamiliar project — reading its brief, then the why of each file opened — or writing / regenerating that brief as a proposal | `brain-onboard` |
| Working out what a change touches — code, dependents, components, and the decisions recorded there — before making it | `brain-impact` |
| Turning a filed blueprint into the components it names, their directories and dependencies, when a person asks | `brain-extract-components` |

## Commands

- `/fairmind-coding` — the front desk: banner + a menu of the headline jobs (loop mode, develop, import a ticket, harness audit, requirements from the company brain); it launches the pick by running that command — or, for the two brain jobs, that skill — in this session, and free text reaches the rest of the toolkit. Every command below stays directly invocable
- `/fairmind-connect` — connect this checkout to its Fairmind project: it verifies the per-project MCP entry and the key (scope, host, expiry), pre-flights the tenant, and binds the repository to the one the platform ingested — after which supported lanes use the catalog id; older judge clients may still fall back to the directory name. It never writes `~/.claude.json`: it prints the exact line to run. Run it once per checkout, per machine, before the first loop
- `/fairmind-loop` — run a task/story in loop mode: the Technical Lead builds a machine-checkable stop condition, then the executed gate drives implement→verify→iterate under budget until it passes and a human approves
- `/loop-import` — turn an external ticket (gh issue, pasted text, ClickUp task payload) into a compiled loop-mode contract via `task-compilation` + `loop_import.py`, present the gap report, then hand off to `/fairmind-loop` to arm
- `/fairmind-add-check` — author a custom loop-mode check (open descriptor contract + admission self-test)
- `/harness-audit` — audit this repo against the Loop Readiness criteria catalog (81 criteria / 9 pillars / 5 dimensions) and render an HTML report under `.fairmind/audit/`
- `/fairmind-config` — show or change this repository's plugin policy: `brain` (the decisions and issues a closed loop proposes to the company brain) and `ambient` (background session capture), each `on|off|unset`. No argument reports the effective state per feature and WHICH layer decided it — centrally forced (naming the project), repo file, or default — plus the central cache's freshness. It writes only the local layer, `.fairmind-insights.json` — every other key keeps its value and its order, and `ambient_capture` is always emitted explicitly as a boolean (see the ambient-capture trap below) — and it **refuses** to change a feature the platform has centrally forced, naming the forcing project and saying the change has to happen on the Fairmind platform. It also refuses a file that does not parse as a JSON object, and a `judge` write over a file whose `ambient_capture` is present but not a boolean (freezing that shape to `false` behind a command about the judge would erase the evidence of intent — `/fairmind-config ambient on|off` is the documented way out of it). Refusals leave the file byte-identical and print to stderr; every refusal exits non-zero and nothing was written
- `/fix-issue` — classify an issue (FE/FE-BE/BE) and dispatch the Software Engineer
- `/fix-frontend-issue` — orchestrate frontend fix + Playwright validation loop
- `/sonarqube-fix` — pull PR-scoped SonarCloud issues and apply fixes
- `/report` — task report
- `/make-tests` — coverage-driven test scaffolding
- `/de-slop` — strip AI artifacts before PR
- `/gh-commit`, `/gh-fix-ci`, `/gh-review-pr`, `/gh-address-pr-comments` — GitHub workflow helpers

## Posting on a pull request

What `/gh-commit`, `/gh-review-pr` and `/gh-address-pr-comments` post on a pull request — a description, a review, a comment, a reply to a review comment — goes through `scripts/pr_post.py`, never through `gh`'s posting subcommands directly. The helper appends the agent signature (README → *Agent signature on pull requests*), without which text an agent wrote reads, to GitHub, as the person's own. When it refuses to sign (exit 3: the host set no `CLAUDE_CODE_SESSION_ID`), say so — do not post unsigned.

## Cannot proceed if

- `check-journal.sh` reports a missing journal for a code-mutating sub-agent (blocks that sub-agent's completion, on SubagentStop)
- A `Write|Edit` to `.fairmind/...` was rejected by `validate-fairmind-path.sh` (means the path was unscoped — fix the path, do not bypass the hook)
- (loop mode) `loop-check.sh` reports the gate is not green — fix the code (or the flagged check), never edit a check to force a pass; the maker is read-only on gate artifacts (maker ≠ checker)
- (loop mode) a check failed admission and is quarantined — it is surfaced for the human and excluded from the stop condition; re-author it via `admit_check.py`, do not bypass
