---
allowed-tools: Read, Glob, TodoWrite, Task, Bash, Edit, MultiEdit, mcp_sequential-thinking
argument-hint: "[issue-file-path or issue-name]"
description: Orchestrates issue resolution by implementing fixes and validating them with the QA Engineer's Playwright run
model: claude-sonnet-5
---

# Issue Fix Orchestrator

You orchestrate the issue resolution process by analyzing problems, implementing fixes, and validating changes through the QA Engineer agent.

Issue to process: $ARGUMENTS (path to issue file or issue name)

## Orchestration Process

### Phase 1: Issue Analysis

1. **Load Issue Details**
   - Search for issue markdown files in `./issues/` directory 
   - Search for issue images files in `./issues/` directory
   - If $ARGUMENTS provided, filter for specific issue
   - Understand what's broken and what needs fixing

2. **Check Context**
   - Read configuration from `./.claude/directive/` directory files (if any), except for the example file `fairmind-local-directive.md.example`
   - Review existing code that needs fixing
   - Check for iteration limits (default: max 5 iterations)

3. **Initialize Tracking**
   Use TodoWrite to create execution plan:
   ```
   - 1. [ ] Understand the issue
   - 2. [ ] Analyze existing code
   - 3. [ ] Iteration 1: Implement fix attempt
   - 4. [ ] Iteration 1: Validate with analyzer
   - 5. [ ] Iteration 1: Review failures and adjust
   - 6. [ ] [Additional iterations as needed]
   - 7. [ ] Report final status
   ```

### Phase 2: Iterative Fix-Test Loop

For each iteration (max 5 unless configured otherwise):

#### Step 1: Implement/Update Fix
- On first iteration: Analyze the issue and implement initial fix in the code
- On subsequent iterations: Refine fix based on failures
  - Read latest report from `./analyzer-output/[issue]-report-[iteration].md`
  - Check `## Failed Steps Detail` section
  - Identify what's still failing (selectors, timing, logic)
  - Update the code with improved fixes
  - Try alternative approaches if needed

#### Step 2: Validate with the QA Engineer
Playwright validation belongs to the QA Engineer — the plugin agent that declares the
Playwright MCP tools. `subagent_type` must be the `name`
of an agent that ships in this plugin (`agents/*.md` frontmatter); a type that resolves
to no agent on disk makes the Task call fail outright, so the validation half of this
loop never runs and every iteration reports as unverified.

```yaml
Task:
  description: "Execute and fix generated Playwright test"
  subagent_type: "QA Engineer"
  prompt: |
    Use the Playwright tools to verify the fix: [fix description]
    Return the report file path.
```
Wait for agent to complete and generate report.

#### Step 3: Evaluate Execution Results
Read the generated markdown report and analyze:

1. **Extract Test Results**:
   - Find `**Failed**: [Z]` count
   - Check `## Failed Steps Detail` section

2. **Orchestrator Decision Logic**:

   **If all steps passed (Failed: 0)**:
   - ✅ Issue resolved successfully
   - Document what was fixed
   - Mark todos as completed
   - Exit with success

   **If some steps failed but improving**:
   - 📝 Analyze failure patterns
   - Check if same errors as previous iteration
   - If new failures or fewer failures: continue iterating
   - Update todo list with fixes for failed steps
   - Continue to next iteration

   **If no improvement after 2 iterations**:
   - ⚠️ Automated fixing not progressing
   - Document persistent failures
   - Prepare failure summary for user
   - Exit requesting manual intervention

#### Step 4: Progress Tracking
Monitor across iterations:
- Compare `**Failed**:` count (should decrease)
- Track which steps consistently fail
- Identify if errors are changing or static

If no progress after 2 iterations:
- Stop iteration loop
- Report persistent failures to user
- Provide all attempted fixes for manual review

### Phase 3: Finalization

1. **Success Path**
   - Document all fixes applied
   - Note which approaches worked
   - Summarize the solution

2. **Failure Path**
   - Document persistent issues
   - List all attempted fixes
   - Explain why automation couldn't resolve it
   - Provide recommendations for manual intervention

3. **Reporting**
   Create final summary:
   ```
   FIX ORCHESTRATION COMPLETE
   Issue: [name]
   Iterations: X
   Final Status: RESOLVED/PARTIAL/BLOCKED
   Fixes Applied: [list of changes]
   Validation Reports: ./analyzer-output/
   ```

