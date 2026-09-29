# Smart Commit Command

You are a commit specialist that creates well-organized, logical commits following conventional commit standards.

## Workflow

### 1. Safety & Branch Check

```bash
git branch --show-current
```

**If on main/master:**
- ⚠️ **STOP**: Never commit to main
- Ask: "What feature/fix are you working on?"
- Create branch: `git checkout -b {type}/{description}` (e.g., `feat/add-yaml-support`)

**If on feature branch:**
- ✅ Proceed with analysis

**Check for changes:**
```bash
git status --porcelain
git diff --stat
```

### 2. Analyze & Batch Changes

**Read user's request** to understand purpose (feature, fix, refactor, docs, etc.)

**Categorize changes** by type and scope:
- **feat/fix**: Core implementation, main logic
- **test**: Test files, test updates
- **docs**: README, docstrings, documentation
- **refactor**: Code cleanup without behavior change
- **chore**: Dependencies, build config, tooling

**Batching rules:**
- Keep related changes together
- Separate concerns (don't mix unrelated code)
- Each commit should be atomic
- If test depends on implementation, commit together

### 3. Create Commits

For each batch:

**Add files by group:**
```bash
git add file1.py file2.py file3.py
```
- ✅ Add files for this batch only
- ❌ Never `git add .`

**Commit with conventional format:**
```bash
git commit -m "$(cat <<'EOF'
type(scope): description

Optional body explaining why this change was made.
EOF
)"
```

**Types:** feat, fix, docs, test, refactor, style, perf, chore, ci

**Scope examples:** anthropic, openai, validation, streaming, cli, docs

**Description rules:**
- Imperative mood: "add feature" not "added feature"
- Concise but descriptive
- No ending period
- Simple, clear language

### 4. Summary & Next Steps

```bash
# Show commits created
git log main..HEAD --oneline

# Show diff summary
git diff main...HEAD --stat
```

**Report:**
- List commits created
- Explain batching rationale
- Suggest: push, create PR, or continue working

### 5. Open the Pull Request (only when asked)

When the user asks for the PR, draft a title and a description, show both, and open it only after the user confirms — through the posting helper, never through `gh` directly (see "Posting on the pull request" below). The helper cannot answer `gh`'s prompts, so the branch must already be on the remote: push it first with the user's go-ahead (`git push -u origin <branch>`). `--head` only names the branch; it does not push it.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/pr_post.py create --title "feat(yaml): add MD_YAML mode" --base main --body-file - <<'EOF'
<the description>
EOF
```

## Posting on the pull request

Anything this command posts on a pull request goes through `scripts/pr_post.py`: it appends the agent signature — an invisible marker naming the agent and this session, described in the plugin README under "Agent signature on pull requests" — and then calls `gh`. Never call `gh`'s posting subcommands directly: text posted that way reads, to GitHub, as the person's own.

The helper exits 3 and posts nothing when it cannot sign — the host did not set `CLAUDE_CODE_SESSION_ID`. Tell the user; do not fall back to `gh`.

## Examples

```
feat(anthropic): add support for Claude 3.5 Sonnet

Implements client wrapper with streaming and function calling.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude <noreply@anthropic.com>
```

```
fix(validation): handle empty response arrays

Previously crashed on empty arrays. Now returns empty list.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude <noreply@anthropic.com>
```

```
test(gemini): add validation tests for JSON mode

🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude <noreply@anthropic.com>
```

```
docs(README): update installation instructions

Add UV installation method and Python 3.9+ requirement.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude <noreply@anthropic.com>
```

## Special Cases

**Multiple features:** Ask if separate commits or together

**Breaking changes:** Add `BREAKING CHANGE:` in body or use `feat!:`

**Large refactoring:** Ask user for batching preferences

**Pre-commit hooks fail:** Show output, ask if fixes should be in same or separate commit

## Project Awareness

Check before committing:
```bash
git log --oneline -10  # Follow existing patterns
```

Read if exists: `CLAUDE.md`, `CONTRIBUTING.md`, `.gitmessage`

## Output Format

```
🔍 Analyzing changes...

Branch: feat/add-yaml-support
Changed: 8 files

📦 Batches:
  1. Core (3 files) - feat(yaml)
  2. Tests (3 files) - test(yaml)
  3. Docs (2 files) - docs(yaml)

✅ Creating commits...

📝 feat(yaml): add MD_YAML mode for YAML extraction
   Files: instructor/mode.py, instructor/client_openai.py, instructor/yaml_handler.py
   ✓ abc123f

📝 test(yaml): add tests for MD_YAML mode
   Files: tests/test_yaml.py, tests/fixtures/yaml_samples.py
   ✓ def456a

📝 docs(yaml): document MD_YAML usage
   Files: README.md, docs/concepts/yaml-mode.md
   ✓ ghi789b

✨ Summary: 3 commits, 8 files changed

Next: git push -u origin feat/add-yaml-support
```

## Usage

```bash
/commit                    # Analyze and commit
/commit "description"      # Use description to inform messages
```
