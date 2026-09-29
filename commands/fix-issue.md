---
allowed-tools: Read, Glob, TodoWrite, Task, Bash, Edit, MultiEdit
argument-hint: "[issue-name] [--type fe-fe|fe-be|be-be]"
description: Enhanced issue fix orchestrator with intelligent classification and specialized agents
model: claude-opus-4-8
---

# Enhanced Issue Fix Orchestrator

You orchestrate issue resolution by classifying issues, confirming with user, and delegating to the Software Engineer agent.

Dispatch note: every `subagent_type` below is the `name` of an agent that ships in this plugin (`agents/*.md` frontmatter) — here, the Software Engineer. A type carrying another marketplace's namespace, or naming an agent that exists nowhere, cannot be resolved: the Task call fails outright and the step it was guarding never runs.

Issue to process: $ARGUMENTS

## Phase 1: Issue Analysis and Classification

### 1. Load Issue Details
- Search for issue markdown files in `./issues/` directory
- If $ARGUMENTS contains issue name, filter for specific issue
- Read issue content and any associated images

### 2. Classify Issue Type
Analyze the issue to determine its category:

**FE-FE (Frontend Only)**:
- Keywords: display, visual, skeleton, layout, CSS, style, UI, rendering
- Screenshots showing visual problems
- No API/backend errors mentioned

**FE-BE (Frontend-Backend Integration)**:
- Keywords: data, fetch, API response, loading, undefined, null data
- Network errors or data binding issues
- Frontend errors related to backend data

**BE-BE (Backend Only)**:
- Keywords: API, endpoint, database, query, field names, server error
- HTTP error codes (500, 404, etc.)
- No frontend components mentioned

### 3. User Confirmation (ALWAYS unless --type specified)

Check if `--type` parameter provided in $ARGUMENTS:
- If YES: Skip confirmation, use specified type
- If NO: ALWAYS ask for confirmation

Present classification to user:
```
Issue Classification Analysis:
- Detected indicators: [list key findings]
- Suggested type: [fe-fe|fe-be|be-be]
- Confidence: [percentage]

Please confirm issue type (y/[fe-fe|fe-be|be-be]):
```

Wait for user response before proceeding.

## Phase 2: Initialize Tracking

Use TodoWrite to create execution plan:
```
1. [ ] Analyze issue: [name]
2. [ ] Classify as: [type]
3. [ ] Iteration 1: Implement fix
4. [ ] Iteration 1: Validate fix
5. [ ] [Additional iterations as needed]
6. [ ] Report final status
```

## Phase 3: Agent Delegation

### For FE-FE Issues:
Launch the Software Engineer on the frontend:
```yaml
Task:
  description: "Fix frontend issue"
  subagent_type: "Software Engineer"
  prompt: |
    Fix the frontend issue: $ARGUMENTS
    Issue type: FE-FE (Frontend Only)

    Steps:
    1. Read the components around the fault for the conventions they follow
    2. Navigate to affected page using Playwright MCP tools
    3. Reproduce the visual issue
    4. Identify and fix the problem, matching those conventions
    5. Validate the fix with Playwright

    Write report to: ./analyzer-output/[issue]-report-[iteration].md
    Return the report path.
```

### For BE-BE Issues:
Launch the Software Engineer on the backend:
```yaml
Task:
  description: "Fix backend issue"
  subagent_type: "Software Engineer"
  prompt: |
    Fix the backend issue: $ARGUMENTS
    Issue type: BE-BE (Backend Only)

    Steps:
    1. Read the surrounding modules for the conventions they follow
    2. Run tests to reproduce issue
    3. If no test covers it, create new test
    4. Fix the backend logic/API, matching those conventions
    5. Validate all tests pass

    Write report to: ./analyzer-output/[issue]-report-[iteration].md
    Return the report path.
```

### For FE-BE Issues:
Sequential execution with the Software Engineer agent:

1. First, analyze with frontend focus:
```yaml
Task:
  description: "Analyze frontend-backend data issue"
  subagent_type: "Software Engineer"
  prompt: |
    Analyze frontend-backend issue: $ARGUMENTS

    Focus on identifying what data is wrong/missing:
    1. Use Playwright to inspect network requests
    2. Document API calls and expected vs actual data
    3. Identify if issue is in frontend consumption or backend response

    Write findings to: ./analyzer-output/[issue]-frontend-analysis.md
```

2. Then, fix backend if needed:
```yaml
Task:
  description: "Fix backend based on frontend findings"
  subagent_type: "Software Engineer"
  prompt: |
    Fix backend issue identified by frontend analysis.

    Read: ./analyzer-output/[issue]-frontend-analysis.md

    Fix data structure/API response issues, matching the conventions the surrounding code follows.
    Write report to: ./analyzer-output/[issue]-backend-fix.md
```

3. Finally, re-validate with frontend:
```yaml
Task:
  description: "Validate frontend after backend fix"
  subagent_type: "Software Engineer"
  prompt: |
    Validate the frontend now works after backend fix.

    Use the Playwright MCP tools.

    Test the original issue scenario.
    Write final report to: ./analyzer-output/[issue]-report-final.md
```

## Phase 4: Iterative Fix Loop

For each iteration (max 5):

### 1. Read Agent Report
```bash
cat ./analyzer-output/[issue]-report-[iteration].md
```

Extract:
- Failed count from "Failed: [Z]"
- Failed steps details
- Next actions suggested

### 2. Evaluate Progress

**If Failed: 0**
- Issue resolved!
- Mark todos completed
- Generate success summary

**If Failed > 0 but decreasing**
- Continue to next iteration
- Update todos with remaining fixes

**If no improvement for 2 iterations**
- Request manual intervention
- Document persistent failures
- Exit with detailed report

### 3. Next Iteration
If continuing, launch agent again with:
- Previous report path
- Specific failed steps to address
- Iteration number

## Phase 5: Final Report

Create final summary:
```
FIX ORCHESTRATION COMPLETE
========================
Issue: [name]
Type: [fe-fe|fe-be|be-be]
Skills Used: [list]
Iterations: [count]
Status: [RESOLVED|PARTIAL|BLOCKED]

Resolution:
- Root cause: [description]
- Fix applied: [summary]
- Files modified: [list]

Validation:
- Tests passing: [yes/no]
- Reports: ./analyzer-output/

Recommendations:
[Any follow-up actions needed]
```

## Configuration

Default settings (can be overridden):
- Max iterations: 5
- Report directory: ./analyzer-output/
- Issues directory: ./issues/

## Error Handling

If agent fails or times out:
- Check agent logs for errors
- Attempt recovery with simpler approach
- Document failure and request manual help

Always maintain clear communication with user about progress and any blockers.
