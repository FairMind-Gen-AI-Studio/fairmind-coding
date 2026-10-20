---
name: fairmind-code-review
description: Use when reviewing implementation work - systematic code review with plan→journal→code traceability, requirements verification, and cross-repository integration checks
---

# Fairmind Code Review

## Overview

Systematic code review with three-layer verification: plan→journal→code traceability, requirements compliance, and cross-repository integration validation.

**Announce at start:** "I'm using the fairmind-code-review skill to review this implementation."

**Dependencies:** Requires `fairmind-context` skill as foundation

## When to Use

Use this skill when:
- Reviewing implementation work by development agents
- Verifying work aligns with Fairmind plans and requirements
- Checking for scope creep or missing features
- Validating cross-repository integrations

## Three-Layer Review Process

### LAYER 1: Plan Verification

**Step 1: Retrieve implementation plan**

1. Use `mcp__Fairmind__Studio_get_task` to get the original implementation plan
2. Note: If Atlas adapted the plan, also check `fairmind/work_packages/{role}/{task_id}_*_workpackage.md`

**Step 2: Read agent journal**

1. Read `fairmind/journals/{role}/{task_id}_*_journal.md`
2. Extract:
   - What was implemented
   - Which plan items were addressed
   - What files were modified
   - What decisions were made

**Step 3: Compare plan vs journal**

Create comparison checklist:

```markdown
## Plan Compliance

### Planned Features
- ✓ Feature X from plan → Journal entry: "Implemented X in file.ts:42"
- ✓ Feature Y from plan → Journal entry: "Completed Y with tests in test.ts:15"
- ✗ Feature Z from plan → **Missing from journal** (not implemented)
- ⚠ Feature W in journal → **Not in original plan** (scope creep?)

### Assessment
- Planned items completed: X/Y (percentage)
- Missing items: {list}
- Extra items (scope creep): {list}
```

**If discrepancies found:**
- Flag missing planned items
- Question extra unplanned items
- Ask agent or Atlas for clarification

### LAYER 2: Requirements Verification

**Step 4: Gather requirements and acceptance criteria**

1. **REQUIRED SUB-SKILL:** Use `fairmind-context` to gather:
   - User story acceptance criteria
   - Functional requirements
   - Technical requirements
   - Test coverage expectations

**Step 5: Build verification checklist**

Create checklist from gathered context:

```markdown
## Requirements Compliance

### User Story Acceptance Criteria
- ✓ AC-1: Users can log in with email and password
  - Verified in: `auth.ts:login()` + test `test_login.ts`
- ✗ AC-2: Users receive error message for invalid credentials
  - **Missing**: No error handling implemented
- ✓ AC-3: Users are redirected to dashboard after login
  - Verified in: `auth.ts:handleLoginSuccess()`

### Functional Requirements
- ✓ FR-123: Session timeout after 30 minutes
  - Verified in: `session.ts:startTimeout()`
- ✗ FR-456: Input validation for email format
  - **Missing**: No email validation found

### Technical Requirements
- ✓ TR-789: Use bcrypt for password hashing
  - Verified in: `auth.ts:hashPassword()`
- ⚠ TR-012: Log authentication attempts
  - **Partially implemented**: Logging only for failures, not successes

### Test Coverage
- ✓ Test scenarios from `list_tests_by_userstory` covered
- Test count: 8/10 expected tests present
- **Missing tests**: Invalid email format, session timeout edge cases
```

**Step 6: Review code against checklist**

For each checklist item:
1. Find corresponding code implementation
2. Verify it meets the requirement
3. Check for edge cases and error handling
4. Mark as ✓ (complete), ✗ (missing), or ⚠ (partial)

### LAYER 3: Cross-Repository Verification

**Step 7: Identify integration points**

From implementation plan and journal:
1. Identify services/repositories being integrated
2. List API endpoints being called
3. Note data contracts being used

**If no integrations:** Skip to Step 11

**Step 8: List target repositories**

Use `mcp__Fairmind__Code_list_repositories` to identify target services

**Step 9: Verify API contracts**

For each integration point:
1. Use `mcp__Fairmind__Code_search` to find API endpoint definitions
2. Use `mcp__Fairmind__Code_cat` to read API documentation or interface definitions
3. Compare implementation vs contract:
   - Request format matches?
   - Response handling correct?
   - Error handling appropriate?

**Step 10: Check for breaking changes**

1. Use `mcp__Fairmind__Code_find_usages` to see how the API is used elsewhere
2. Verify changes don't break existing integrations
3. Check API versioning if applicable

Example verification:

```markdown
## Integration Check

### Auth Service Integration
- **API endpoint**: `POST /auth/login`
- **Contract location**: `auth-service/src/api/auth.ts:42`
- ✓ Request format matches contract
- ✓ Response handling correct
- ⚠ Error handling missing for 429 (rate limit) response

### Payment Service Integration
- **API endpoint**: `GET /payments/user/{id}`
- ⚠ **New dependency not in original plan**
- Need to verify with Atlas: Was this approved?
- Contract verified: Correctly implemented

### Shared Library
- **Library**: `@company/ui-components`
- ✓ Using correct version (v2.3.1)
- ✓ Component API usage matches documentation
```

## Review Output

**Step 11: Compile review report**

Present findings in structured format:

```markdown
# Code Review: {Task ID}

**Reviewer**: {Your name/agent}
**Date**: {review_date}
**Agent reviewed**: {AI/Backend/Frontend Engineer}
**Status**: {APPROVED / CHANGES REQUESTED / BLOCKED}

---

## Executive Summary
{1-2 sentence overview of review findings}

## Plan Compliance
{From Layer 1: Step 3 output}

## Requirements Compliance
{From Layer 2: Step 5 output}

## Integration Check
{From Layer 3: Step 10 output, if applicable}

---

## Critical Issues (Must Fix)
1. {Missing acceptance criterion AC-2: error handling}
2. {Missing functional requirement FR-456: input validation}
3. {Breaking change in payment service integration}

## Warnings (Should Fix)
1. {Partial technical requirement TR-012: incomplete logging}
2. {Unplanned payment service dependency needs approval}

## Recommendations
1. Implement missing acceptance criteria before merging
2. Add missing functional requirements
3. Clarify payment service integration with Atlas
4. Complete logging implementation per TR-012
5. Add missing test cases for edge scenarios

---

## Approval Status

**Decision**: CHANGES REQUESTED

**Reasoning**:
- Critical acceptance criteria missing (AC-2)
- Functional requirement missing (FR-456)
- Unplanned dependency needs architectural review

**Next Steps**:
1. Agent to address critical issues
2. Atlas to review payment service integration
3. Re-review after changes implemented
```

**Step 12: Communicate findings**

Share review report with:
- The agent who did the work
- Atlas (for architectural concerns)
- Project stakeholders (if critical issues found)

## Key Differences from Generic Code Review

| Generic Code Review | Fairmind Code Review |
|---------------------|---------------------|
| Subjective quality checks | Three-layer verification (plan→journal→code) |
| Generic best practices | Requirements from Fairmind |
| Manual integration checks | Cross-repo verification with Code tools |
| No traceability requirement | Full traceability via journal |

## Review Decision Criteria

**APPROVED:**
- ✓ All planned features implemented
- ✓ All acceptance criteria met
- ✓ All requirements satisfied
- ✓ Test coverage complete
- ✓ No critical issues
- ✓ Integrations verified

**CHANGES REQUESTED:**
- Missing planned features
- Missing acceptance criteria
- Missing requirements
- Incomplete test coverage
- Critical issues found
- Integration issues detected

**BLOCKED:**
- Architectural concerns require Atlas review
- Unplanned major changes need approval
- Breaking changes detected in integrations
- Security vulnerabilities found

## Error Scenarios

**If implementation plan missing:**
- Request plan from Atlas
- Use generic code review practices
- Document lack of plan in review report

**If journal missing or incomplete:**
- Flag to agent: journal is required for traceability
- Request journal update before continuing review
- Cannot verify plan compliance without journal

**If requirements unclear:**
- Use `fairmind-context` to gather more details
- Ask Atlas for requirement clarification
- Document ambiguity in review report
