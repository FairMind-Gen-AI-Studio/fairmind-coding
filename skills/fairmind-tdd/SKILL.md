---
name: fairmind-tdd
description: Use when implementing features with Fairmind acceptance criteria - TDD workflow aligned with Fairmind test plans, implementation plans, and journal-based traceability
---

# Fairmind Test-Driven Development

## Overview

Test-Driven Development workflow aligned with Fairmind test plans and acceptance criteria. This skill ensures tests are **aligned with Fairmind acceptance criteria** (not invented) and implementation follows **plans from get_task** (not free-form).

**Announce at start:** "I'm using the fairmind-tdd skill to implement this feature with test-driven development."

**Dependencies:** Requires `fairmind-context` skill as foundation

## When to Use

Use this skill when:
- Implementing features with Fairmind acceptance criteria
- You have an implementation plan from Atlas or Fairmind
- You want to ensure test coverage matches expectations
- You need traceability from plan to implementation

## TDD Process with Fairmind

### SETUP PHASE

**Step 1: Gather context**

1. **REQUIRED SUB-SKILL:** Use `fairmind-context` skill to gather full context
2. Verify you have:
   - Implementation plan (from `Studio_get_task`)
   - Acceptance criteria (from user story)
   - Test expectations (from `Studio_list_tests_by_userstory`)

**Step 2: Understand what to build**

1. Read implementation plan carefully
2. Identify the **first** small unit of functionality
3. Check acceptance criteria alignment
4. Check test expectations from Fairmind

### RED PHASE

**Step 3: Write the failing test**

Write a test that:
- ✓ Aligns with Fairmind acceptance criteria (not invented)
- ✓ Follows implementation plan scope (no scope creep)
- ✓ Matches expected test coverage from `list_tests_by_userstory`
- ✓ Tests one small behavior (not multiple things)

Example:
```python
def test_user_can_login_with_valid_credentials():
    # Acceptance criterion: "Users can log in with email and password"
    result = login("user@example.com", "password123")
    assert result.success == True
    assert result.user_id is not None
```

**Step 4: Run test to verify it fails**

Run the test and verify:
- ✗ Test fails (no false positives)
- Failure message is clear (e.g., "function login not defined")

Document in terminal:
```
FAILED: test_user_can_login_with_valid_credentials
Expected failure: login function not yet implemented
```

### GREEN PHASE

**Step 5: Implement minimal code**

Write the **minimal** code to make the test pass:
- Follow the implementation plan
- Don't add extra features
- Don't over-engineer
- Just make the test green

Example:
```python
def login(email, password):
    # Minimal implementation to pass test
    if email and password:
        return LoginResult(success=True, user_id=1)
    return LoginResult(success=False, user_id=None)
```

**Step 6: Run test to verify it passes**

Run the test and verify:
- ✓ Test passes
- No other tests broken

Document in terminal:
```
PASSED: test_user_can_login_with_valid_credentials
All tests passing: 1/1
```

### REFACTOR PHASE

**Step 7: Clean up code**

Improve code quality while maintaining test passage:
- Remove duplication (DRY principle)
- Improve naming
- Extract functions/classes as needed
- Improve error handling
- **Keep tests green**

**Step 8: Run tests again**

Verify all tests still pass after refactoring.

### DOCUMENTATION PHASE

**Step 9: Update journal**

Update `fairmind/journals/{role}/{task_id}_*_journal.md` with:

```markdown
### {Timestamp} - Implemented {Feature Name}

**Implementation Plan Item**: {Which item from plan was completed}

**Test Added**:
- File: `{test_file_path}`
- Test name: `{test_name}`
- Aligned with acceptance criterion: "{criterion text}"

**Code Added**:
- File: `{implementation_file_path}`
- Function/class: `{name}`
- Lines: {start_line}-{end_line}

**Test Coverage**:
- ✓ Acceptance criterion X covered
- Test result: PASSED

**Next Steps**: {What's next from the plan}
```

**Step 10: Validate against Fairmind expectations**

Cross-check:
- ✓ Test aligns with acceptance criteria from user story?
- ✓ Implementation follows plan from `get_task`?
- ✓ Test coverage matches expectations from `list_tests_by_userstory`?
- ✓ Journal provides traceability (plan → test → code)?

### REPEAT FOR NEXT FEATURE

**Step 11: Continue with next unit**

Return to RED PHASE (Step 3) for the next small unit of functionality from the implementation plan.

## Key Differences from Generic TDD

| Generic TDD | Fairmind TDD |
|-------------|--------------|
| Invent test scenarios | Align with Fairmind acceptance criteria |
| Free-form implementation | Follow plan from `get_task` |
| No traceability requirement | Journal provides plan→test→code traceability |
| Test coverage subjective | Test coverage from `list_tests_by_userstory` |

## Completion Checklist

Before marking work complete, verify:

- [ ] All acceptance criteria have tests
- [ ] All tests pass
- [ ] Implementation follows plan
- [ ] Journal documents: plan items → tests → code
- [ ] Test coverage matches Fairmind expectations
- [ ] Code is refactored and clean
- [ ] No scope creep (only planned features implemented)

## Error Scenarios

**If acceptance criteria unclear:**
- Use `fairmind-context` to gather more details
- Ask Atlas for clarification
- Document ambiguity in journal

**If implementation plan missing:**
- Check work package from Atlas
- Ask Atlas to provide adapted plan
- Don't proceed without plan (prevents scope creep)

**If test expectations unavailable:**
- Use acceptance criteria as test guide
- Document assumption in journal
- Proceed with standard test coverage practices
