---
name: fairmind-context
description: Use when starting any Fairmind development work - gathers full context including project, session, user story, task implementation plan, requirements, test expectations, and relevant documentation
---

# Fairmind Context Gathering

## Overview

Reusable context gathering for any Fairmind development work. This skill is the **foundation** for all other Fairmind skills.

**Announce at start:** "I'm using the fairmind-context skill to gather full context for this work."

🔴 **Every `mcp__Fairmind__*` step below is the ORCHESTRATOR's to run, not a sub-agent's.** A skill carries no tool grant — it instructs whoever invoked it — and since 2026-09-12 no agent in this plugin holds a Fairmind tool: the server key is per-checkout (`^fairmind`, so `Fairmind` or `Fairmind-dev`) and a sub-agent's `tools:` line is a literal roster that cannot follow it. So run these steps yourself and hand the results down as files under `${FAIRMIND_BASE}` or inline in the brief. Use whichever Fairmind mount this session actually has; the names below are written in the common `mcp__Fairmind__` spelling.

## When to Use

Use this skill when:
- Starting any development task linked to Fairmind
- You have a task_id, user_story_id, or project_id
- You need to understand requirements and acceptance criteria
- You're preparing for implementation or code review

## Context Gathering Process

### Step 0: Resolution chain (`active-context.json` → bootstrap)

Context resolves in a fixed order so the plugin runs on any repo, connected or not:

1. Read `.fairmind/active-context.json` at the repo root. It carries `mode` (`interactive` | `loop` | `closed`), a `project` name, and a **`fairmind`** field recording whether a Fairmind workspace is connected: `"configured"` (use the `mcp__Fairmind__*` tools in the steps below) or `"none"` (standalone — skip the MCP steps and take context from the user or local `.fairmind/` files).
2. If `active-context.json` is **absent**, bootstrap a minimal one — `{"mode":"loop","fairmind":"none","project":"<repo-name>"}` — and ask the user **once** whether a Fairmind workspace exists, recording the answer in `fairmind` and never re-asking while it stands (the Technical Lead owns this in `/fairmind-loop` Phase 0).

**The identity keys are written by `/fairmind-connect`, never by you.** A connected
checkout also carries `project_id`, `repository_id`, `repository_name`,
`repository_branch`, `repository_url` and `bound_at`. Those are the values the
capture lanes and the judge hook actually send — `project` beside them is a human
label, and the platform can rarely resolve it (it keys projects by ObjectId, so a
directory called `payments-api` never matches a project called "Payments Platform").
Read them; do not invent them, and do not set `fairmind: "configured"` on their behalf:
an unverified `"configured"` is exactly the state where every row is delivered and keys
to nothing. `repository_branch` is not decoration — a catalog id is a `(url, branch)`
row, so it is the only local record of WHICH row was bound.

**`mode` is a THREE-value field, and `closed` is written by the engine, not by you.** `closed` means "a loop ran in this repo and finished; no Fairmind session is active" — `run_gate_checks.py` stamps it when the loop reaches a terminal status, which is what nothing did before (the marker used to keep claiming `loop` over a dead loop indefinitely). **For what YOU do with the context it asks nothing extra of you:** like `interactive`, it is not loop mode, so nothing about the gate applies. That equivalence is yours alone and does not extend to the file — the hooks read the two values differently (`check-journal.sh` stands down on `closed` and engages the journal rule on `interactive`, and the two route capture rows to different ledgers unless a `loop-state.json` sits at `base_path`; `INTERNALS.md`'s **Interactive mode vs loop mode** section has the table). Which is exactly why: **Never normalize it away.** Rewriting it to `interactive` (or to `loop`) destroys the record that a loop ended here, and the next `/fairmind-loop` or `/fairmind-develop` run overwrites it correctly on its own via `loop_open.py --repoint`.

**Standalone is a mode, not an error.** When `fairmind` is `"none"` (or the MCP tools are unavailable), the steps below that call `mcp__Fairmind__*` are simply skipped; everything they would supply comes from the user or from local `.fairmind/` files.

### Step 1: Identify Current Project

**If project_id provided:** Skip to Step 2

**If project_id not provided:**
1. Use `mcp__Fairmind__General_list_projects` to list available projects
2. Ask user which project (if ambiguous) or infer from current work context
3. Store project_id for subsequent calls

### Step 2: Identify Active Session

1. Use `mcp__Fairmind__General_list_work_sessions` with the project_id
2. Filter for active sessions (`is_active: true`)
3. If multiple active sessions, ask user to clarify
4. Store session_id for subsequent calls

### Step 3: Gather Task Context (if task_id available)

1. Use `mcp__Fairmind__Studio_get_task` with task_id
2. Extract:
   - Implementation plan
   - Task status
   - Assigned agent role
   - Dependencies

### Step 4: Gather User Story Context (if user_story_id available)

1. Use `mcp__Fairmind__Studio_get_user_story` with user_story_id
2. Extract:
   - Title and description
   - Acceptance criteria
   - Business requirements
3. Use `mcp__Fairmind__Studio_get_related_user_stories` to identify dependencies

### Step 5: Gather Requirements

1. If user_story_id available:
   - Use `mcp__Fairmind__Studio_list_functional_requirements_by_session`
   - Use `mcp__Fairmind__Studio_list_technical_requirements_by_session`

⚠️ **There is no per-project requirements door.** The two session-scoped calls above are the
whole surface; a `Studio_list_requirements_by_project` does not exist on the server, and a
session with no requirements is an answer, not a reason to reach for a wider call.

### Step 6: Gather Test Expectations (if user_story_id available)

1. Use `mcp__Fairmind__Studio_list_tests_by_userstory`
2. Extract expected test coverage and test scenarios

### Step 7: Gather Documentation

1. Use `mcp__Fairmind__General_rag_retrieve_documents` with relevant queries:
   - Similar implementations
   - Architectural patterns
   - Technology-specific best practices
2. Use `mcp__Fairmind__General_get_document_content` for specific documents if needed

### Step 7b: Ask the brain what is already known about these files

Step 7 asks "what documents exist about this topic". It cannot answer the question that
actually changes an implementation: **what has this company already decided, specified or
hit a problem with, in the files I am about to touch.** `mcp__Fairmind__Brain_context`
answers that one — it takes a path and returns the knowledge anchored under it.

Run it once the work's target paths are known, and skip it entirely when Step 0 resolved
`fairmind: "none"`.

1. For each directory the task will touch, call `mcp__Fairmind__Brain_context` with
   `directory: "<module path>"`, `granularity: "file"`, `detail_level: "minimal"`.
2. Make the call **twice per directory** and never pool the two answers — the same
   two-pass rule the `brain-rebuild-requirements` skill is built on:

   | pass | `include_proposed` | what it is for |
   |---|---|---|
   | evidence | `true` | everything anchored there, drafts included — the widest view |
   | coverage | `false` | only what a human has confirmed — this is the `uncovered[]` to quote |

3. Open a record with `mcp__Fairmind__Brain_get` only when it is close enough that its body
   matters, and use `mcp__Fairmind__Brain_expand` for neighbouring chunks rather than
   re-fetching. When a decision looks like it could forbid the approach, read its history
   with `mcp__Fairmind__Brain_timeline` before designing around it.
4. **Report `review_state` with every result, beside `status`.** They are two independent
   axes: `status` says whether the behaviour is still wanted, `review_state` says whether a
   person ever agreed with the record. A `proposed` record is somebody's unconfirmed draft —
   a useful lead, and not a precedent. It never appears without its state attached.

🔴 **A decision that forbids what the task asks for is a stop-and-ask, not a footnote.**
Surface it with its node id before implementing, and let the human say whether it still
applies. Finding it afterwards is the expensive way.

### Step 8: Cross-Project Context (if applicable)

If the work involves integration with another project:
1. Repeat Steps 1-7 for the target project
2. Use `mcp__Fairmind__Code_list_repositories` to identify target repositories
3. Use `mcp__Fairmind__Code_search` to understand integration points

## Output Structure

Present gathered context in this format:

```markdown
# Context for {Task/User Story}

## Project
- **ID**: {project_id}
- **Name**: {project_name}

## Session
- **ID**: {session_id}
- **Status**: {active/inactive}

## User Story
- **ID**: {user_story_id}
- **Title**: {title}
- **Description**: {description}
- **Acceptance Criteria**:
  1. {criterion_1}
  2. {criterion_2}
- **Related Stories**: {list of related story IDs}

## Task
- **ID**: {task_id}
- **Status**: {status}
- **Implementation Plan**:
  ```
  {plan details}
  ```

## Requirements
### Functional Requirements
- {FR-1}: {description}
- {FR-2}: {description}

### Technical Requirements
- {TR-1}: {description}
- {TR-2}: {description}

## Test Expectations
- {Test scenario 1}
- {Test scenario 2}
- Expected coverage: {percentage or scope}

## Documentation
- {Document 1}: {summary}
- {Document 2}: {summary}

## What the brain already holds on these paths
- {path}: {n} items anchored — {kind}/{title} ({status}, {review_state}, node {id})
- Uncovered (confirmed pass): {files with nothing behind them}
- ⚠️ Blocking decisions found: {decision, its reason, its node id} — or "none"

## Integration Points (if cross-project)
- Target project: {name}
- Repositories: {list}
- API endpoints: {list}
```

## Next Steps

After gathering context:
- **For implementation**: Use `fairmind-tdd` skill
- **For code review**: Use `fairmind-code-review` skill
- **For debugging**: Use gathered context with debugging workflows

## Error Handling

**If project_id not found:**
- Ask user to provide project name or ID
- List available projects for selection

**If session not active:**
- Warn user that session may be ended
- Proceed with project-level context only

**If task_id or user_story_id not found:**
- Gather whatever context is available
- Inform user of missing context elements
