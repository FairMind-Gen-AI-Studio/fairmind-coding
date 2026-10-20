---
description: Fix SonarQube issues for current PR
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/analyze_sonarqube.py), Bash(poetry run pytest:*), Bash(git add:*), Bash(git commit:*), Bash(rm -f sonarqube_report.json), Read, Grep, Glob
---

# sonarqube-fix

Fix all SonarQube issues for the current pull request by analyzing the PR-specific issues report and applying fixes.

## Usage

```bash
/sonarqube-fix
```

## Description

This command will:
1. Detect the pull request associated with your current branch
2. Fetch SonarQube issues specific to your PR from SonarCloud
3. Systematically fix each issue starting with the most critical (BLOCKER → CRITICAL → MAJOR → MINOR → INFO)
4. Ask for user guidance when multiple fix options are available
5. After all issues are resolved, automatically stage and commit the changes

**Note:** This command only works with branches that have an associated pull request. It will not analyze the entire codebase or run local sonar-scanner.

## Prerequisites

Before using this command, ensure you have:

1. **An open pull request** for your current branch. The command will not work without an associated PR.

2. **SONAR_TOKEN environment variable** set with your SonarCloud authentication token:
   ```bash
   export SONAR_TOKEN="your-sonarcloud-token"
   ```

3. **sonar-project.properties file** in your project root with at least:
   ```properties
   sonar.projectKey=your-project-key
   sonar.organization=your-organization
   ```

4. **GitHub CLI (gh)** installed and authenticated to detect the pull request.

The integration automatically connects to SonarCloud (https://sonarcloud.io) and fetches issues specific to your pull request.

## Command Implementation

```claude
First, check if we're in a project with sonar-project.properties. If not, inform the user this command requires a SonarCloud-configured project.

Run the SonarQube analysis script to detect PR and fetch issues:
Execute: `python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/analyze_sonarqube.py`

Check the exit code:
- If exit code is 2: The script already displayed a warning about no PR. Stop execution.
- If exit code is non-zero (other error): Display the error message and stop.
- If exit code is 0: Continue with the analysis.

Parse the JSON output from the script to get the issues report.

If there are issues to fix:
1. Group issues by severity (BLOCKER, CRITICAL, MAJOR, MINOR, INFO)
2. For each severity level (starting with most critical):
   - For each issue in that severity:
     a. Locate the file and line mentioned in the issue
     b. Understand the issue type and rule violation
     c. If the fix is straightforward, apply it directly
     d. If multiple approaches exist, explain options to the user and ask for preference
     e. Apply the chosen fix
     f. Mark the issue as resolved in tracking
3. After fixing all issues:
   - Run any linting/formatting commands if configured
   - Run tests to ensure everything works: `poetry run pytest`
   - If tests fail:
     a. Analyze test failure output
     b. Fix the failing tests
     c. Re-run tests (max 3 attempts)
     d. If tests still fail after 3 attempts, inform the user and ask for guidance
   - If tests pass:
     a. Clean up temporary SonarQube files:
        - Remove `sonarqube_report.json` if it exists
     b. Stage all changes: `git add -A`
     c. Commit with message: "fix: Resolve SonarQube issues for PR #[PR_NUMBER]\n\n- Fixed [X] BLOCKER issues\n- Fixed [Y] CRITICAL issues\n- Fixed [Z] MAJOR issues\n- Fixed [A] MINOR issues\n- Fixed [B] INFO issues\n\nAll tests passing ✅"
4. Show summary of fixes applied and test results

If no issues found:
- The script will have already displayed a success message
- Run tests anyway to ensure codebase health: `poetry run pytest`
- Report test results
```

## Example Output

### When PR exists with issues:

```
🔍 Analyzing SonarQube issues for PR #19...
📊 Found 15 issues to fix for your PR:
  - 2 BLOCKER
  - 3 CRITICAL
  - 5 MAJOR
  - 3 MINOR
  - 2 INFO

Starting fixes...

[BLOCKER] Fixing SQL injection vulnerability in app/api/repositories.py:45
✅ Fixed: Used parameterized queries

[BLOCKER] Fixing hardcoded password in config.py:12
❓ Multiple fix options available:
  1. Move to environment variable
  2. Use secrets management service
  3. Remove the credential entirely
Please choose (1-3): 

...

All issues fixed! 

🧪 Running tests to verify fixes...
Running: poetry run pytest
✅ All tests passed (127 passed, 0 failed)

🧹 Cleaning up temporary SonarQube files...
- Removed .sonarqube_analysis.json
- Removed sonarqube_report.json

📝 Committing changes...
✅ Successfully fixed and committed 15 SonarQube issues for PR #19

Summary:
- Fixed 15 SonarQube issues across all severity levels
- All tests passing
- Code committed successfully
```

### When no PR exists:

```
⚠️  No pull request found for current branch 'feature-branch'.

The sonarqube-fix command only works with branches that have an associated pull request.
Please create a pull request for your branch first, then run this command again.
```

### When PR exists but has no issues:

```
🔍 Analyzing SonarQube issues for PR #19...
✅ No open issues found for PR #19! Your code is clean.
```