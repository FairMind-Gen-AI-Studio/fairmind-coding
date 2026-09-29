# Runtime contracts

## Workspace contract

The active context at `.fairmind/active-context.json` identifies the current
project, task and workspace through `base_path`. Commands must repoint that
context before writing another task's state. Plugin scripts are addressed
through `<plugin root>/scripts/` — the absolute root the invoking command or
`SKILL.md` shows once Claude Code substitutes its own `${CLAUDE_PLUGIN_ROOT}`
references; this file is read raw and never substituted itself, so it never
carries a form that looks runnable but is not.

## Artifact roster

| Location | Purpose |
| --- | --- |
| `${base_path}/journals/` | Role-agent journals |
| `${base_path}/loop-state.json` | Selected loop contract, budget and gate state |
| `.fairmind/trace/<sanitized task_ref>.jsonl` | Tool-operation ledger |
| `${base_path}/subagent-tokens-<sanitized task_ref>.jsonl` | Token ledger; absent refs use `session` |
| `.fairmind/no-loop/` and `.fairmind/degraded/` | Routed `trace.jsonl` and `subagent-tokens.jsonl` |
| `.fairmind/contracts/` | Imported contracts and gap reports |
| `.fairmind/worktrees/<sanitized task_ref>` | Optional loop worktree |
| `.fairmind/insights/decisions.jsonl` | Captured decisions |
| `.fairmind/loop-ledger.jsonl` | Closed-loop records |
| `.fairmind/insights-sync.json` | Flush acknowledgements |
| `.fairmind/insights-inflight.json` | `decisionId`s the last `--emit` handed a caller, until a `--commit` spends them — every `--emit` rewrites it, including one run only to look |
| `.fairmind/audit/` | Audit report and structured assessment |

The engine falls back to `.fairmind/loop-state.json` when no context selects a
workspace. Token readers also include the legacy unkeyed `subagent-tokens.jsonl`;
writers do not migrate or delete it. The plugin best-effort adds `.fairmind/` to
the repository's root `.gitignore`.

## Hook roster

`hooks/hooks.json` defines event registration and timeouts.

| Script | Event and role |
| --- | --- |
| `front-desk-banner.sh`, `loop-banner.sh` | UserPromptExpansion: command banners |
| `validate-fairmind-path.sh` | PreToolUse: enforce scoped workspace writes |
| `inject-context.sh` | SubagentStart: pass workspace context into subagents; PreToolUse (`check`): refuse a dispatch whose context cannot be read |
| `trace-op.sh` | PostToolUse: record tool operations |
| `loop-check.sh` | Stop: evaluate the active loop gate |
| `capture-orchestrator-tokens.sh` | Stop: record main-agent usage for the owning armed loop |
| `check-journal.sh` | SubagentStop: require a journal after code mutation |
| `capture-subagent-tokens.sh` | SubagentStop: record subagent usage |
| `record-attestation.sh` | SubagentStop: record independent checker evidence |
| `python-floor.sh` | SessionStart: one line when `python3` is missing, unreadable or below the 3.9 floor; plain bash, writes nothing |
| `session-start-insights.sh` | SessionStart: register the session and maintain pending delivery |
| `session-end-insights.sh` | SessionEnd: mark the session ended and maintain pending delivery |

## Repository identity — bound and unbound

`/fairmind-connect` verifies the per-project MCP configuration and binds this
checkout to a repository returned by the platform catalog. A successful binding
is recorded in `.fairmind/active-context.json`.

## Interactive mode vs loop mode

Interactive work uses the role agents and journal. Loop mode additionally uses
an explicit contract, a maker/checker split, and the gate engine. `running`
means the loop is active; `passed_pending_human` still requires the human gate.
A finished loop's ledgers belong to that loop. Repoint the active context before
starting another task rather than appending new work to the old task.

Use an explicit `--state` path when inspecting a particular loop with
`run_gate_checks.py --dry-run`. Inspection does not change loop state and is
not evidence about a different task or branch.
An inspection with no state file exits nonzero; an ordinary Stop invocation
without an active loop remains a no-op.

## Capture routing

`scripts/_loop_ledger.py:resolve_loop_context` routes trace and subagent-token
rows. `closed` is written by the engine at terminal loop states, including
`passed_pending_human`; agents reopen work through the commands' repoint step.

| Context | Destination |
| --- | --- |
| No active context | No workspace capture |
| `loop`, live state, matching `target.ref` and owning or unclaimed `owner_session` | Loop ledgers |
| `loop`, live state, different task or owning session | `.fairmind/no-loop/`, without `after_loop` |
| `loop`, missing or terminal state | `.fairmind/degraded/`, with `degraded_from` |
| `closed` | `.fairmind/no-loop/`, with `after_loop` |
| `interactive` or absent mode, loop-state in either ledger home | `.fairmind/no-loop/`, without `after_loop` |
| `interactive` or absent mode, no loop-state in either home | Ordinary ledgers |
| Unrecognized mode | `.fairmind/degraded/`, with `degraded_from` |

The ledger homes checked for interactive capture are the resolved token home
and `.fairmind/`. Identity comparisons require nonempty values on both sides;
an undecidable comparison does not divert rows. Main-agent token capture only
writes for an owning live loop after arming. The journal hook stands down on
`closed`, while `interactive` still requires journals after code mutation.

## Consent classes

The [README](README.md) is the authority for capture, delivery, consent classes,
and configuration. Consult its consent section before enabling a lane. The
consent stamp describes the decision at collection time; delivery resolves the
live decision for the originating checkout. Unknown configuration is not an
explicit revocation. Do not infer training permission from a repository file.

The optional `consent` object has `merged_diffs`, `rejected_proposals` and
`generation_context` switches. An absent file or absent block defaults all
three classes on; a present block grants each class only for literal `true`.
Malformed input grants no classes. This narrows an existing lane grant; it does
not enable capture. A file that exists must also carry `"ambient_capture": true`
to enable ambient capture. See the README for each class's fields and content modes.

At delivery, an explicit `false` can discard data governed by that switch;
unreadable or wrong-typed configuration retains pending data unsent. Collection
and delivery stamps are distinct: re-arming intersects the loop's original
grant, and flushing intersects it with the live grant. A withheld class is not
a deletion promise for data already delivered.

## Completeness attestations

A completeness attestation carries no prompt, no code, no file content. It
identifies the independent checker and the tree evaluated; the gate rejects
stale evidence. See the README for the disclosure of each lane.

