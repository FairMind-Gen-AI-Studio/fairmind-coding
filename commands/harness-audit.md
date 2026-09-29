---
description: Audit the current repo against the Loop Readiness criteria catalog (81 criteria across 9 pillars, 5 Loop Readiness dimensions), render a self-contained HTML report, and flush the run to Agentic Insights when Fairmind is connected
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/audit_run_meta.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/harness_audit.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/harness_audit_report.py:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/insights_flush_payload.py:*), Read, mcp__Fairmind__Insights_record_harness_audit
---

# harness-audit

Capture run-identity metadata, run the harness-readiness audit engine against the
current repo, render its `summary.json` into a single self-contained HTML report,
and — when the Fairmind MCP is connected — flush the run to Agentic Insights.

## Usage

```bash
/harness-audit
/harness-audit --test-command "pytest -q"   # probe the test-determinism dimension
```

## What it does

1. **Captures run-identity metadata**, BEFORE the audit engine runs:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/audit_run_meta.py --repo . --out .fairmind/audit/run-meta.json
   ```
   This writes `.fairmind/audit/run-meta.json` — `repo_name` (basename of the git
   toplevel), `git_remote` (the `origin` remote, normalized, or `null` when there is
   no `origin`), `commit_sha` (`git rev-parse HEAD`), and `executed_at` (ISO-8601 UTC,
   second precision). It fails closed: if `--repo` is not a git work tree it prints an
   error to stderr, exits 1, and writes nothing — treat that as a hard stop for this
   step, same as a non-zero exit from either script below.

2. **Runs the audit engine** against the tracked files of the current repo, using
   the shipped criteria catalog (81 criteria across 9 pillars, plus the 5 Loop
   Readiness dimensions):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/harness_audit.py --repo . --out .fairmind/audit [--test-command "<cmd>"] [--probe-k <int>]
   ```
   This writes `.fairmind/audit/assessment.jsonl` (per-criterion verdicts) and
   `.fairmind/audit/summary.json` (per-pillar levels, per-dimension scores,
   totals). If the caller does not pass `--test-command`, the engine still tries
   a tracked `package.json`'s `scripts.test` before giving up on the
   `test-determinism` dimension (it is then reported as `not-probed`, never as
   clean by default).

3. **Renders the HTML report** from that summary, with an explicit `--out` so
   the report lands next to `summary.json` rather than at the generator's
   default path (which is `.fairmind/audit/report.html` **relative to the
   summary's own directory** — since the engine already writes `summary.json`
   inside a directory named `.fairmind/audit`, the unqualified default would
   nest as `.fairmind/audit/.fairmind/audit/report.html`):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/harness_audit_report.py --summary .fairmind/audit/summary.json --out .fairmind/audit/report.html
   ```
   This writes `.fairmind/audit/report.html` — one file, no network access
   required to open it (no external URLs, no non-`data:` `src=`, no `@import`).

4. **Points the user at the result**: tell them to open `.fairmind/audit/report.html`
   in a browser. Report the top-line numbers (overall criteria passed/total, and
   any pillar or dimension that reads `weak`/`absent`/`not-probed`) directly in
   the conversation so they don't have to open the file just to get a summary.

5. **Flushes the run to Agentic Insights** — a model-side step, AFTER the audit
   engine and the report have both succeeded, delegating to the same shared
   flush CLI `/fairmind-sync-insights` and `/fairmind-loop`'s Exit already use,
   never hand-assembling the payload inline:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/insights_flush_payload.py --emit audit
   ```
   It prints `{"loop": null, "decisions": null, "audit": ..., "brain": null}` on stdout — the
   `loop`/`decisions` categories are always null here (out of scope for this
   command); read only the `audit` key:

   - **Non-null** — pass it VERBATIM, unmodified, as the arguments of
     `mcp__Fairmind__Insights_record_harness_audit`. The payload shape is
     owned by `build_audit_payload` in `insights_flush_payload.py`
     (`contract_version` `fm-insights.audit/1`, built
     deterministically from the three files steps 1-2 wrote — `run-meta.json`
     for repo identity, `summary.json` for the pillar rollups, and
     `assessment.jsonl` for the per-criterion verdicts underneath them); do
     not re-derive or edit its fields here. Only after that MCP call returns success, run:
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/insights_flush_payload.py --commit audit
     ```
     A failed or partial send stays **uncommitted** — do not run `--commit
     audit` in that case; the payload stays pending on disk and
     `/fairmind-sync-insights` retries it later, so `--commit` never marks
     unsent data as sent.
   - **Null, with a stderr advisory naming a missing file** (`run-meta.json`
     or `summary.json`) — that source file is missing or unreadable; report
     it as an error. NEVER hand-assemble or improvise the payload yourself.
   - **Null, with a stderr advisory naming `commit_sha`/`executed_at`** —
     `run-meta.json` is PRESENT and parses fine, but is missing its identity
     keys (`commit_sha` and/or `executed_at`), so the run is unkeyable: it
     cannot be cursored on disk. This is NOT the same observable as "already
     flushed" — nothing was ever sent, and there is no cursor entry to delete
     to recover it. Report the identity gap plainly and re-run `/harness-audit`
     from step 1 so `audit_run_meta.py` regenerates a complete `run-meta.json`.
     NEVER hand-assemble or improvise the payload yourself.
   - **Null, with no advisory** — this run (`commit_sha`@`executed_at`) was
     already flushed; report "already flushed" and skip the MCP call. To
     deliberately re-send, use `/fairmind-sync-insights`'s backfill: delete
     the cursor — the server upserts.

   **When the Fairmind MCP tool is not connected (standalone), the MCP call
   and `--commit audit` are both SKIPPED** — a mode, not an error — and the
   pending payload is picked up later by `/fairmind-sync-insights`. Report
   the skip plainly in the end-of-run summary (e.g.
   "Insights flush skipped: standalone, no Fairmind MCP connection" as an
   explicit line alongside the report path and top-line numbers). The audit
   run and the local HTML report always succeed on their own regardless of
   whether this last step ran.

## Options

- `--test-command "<shell command>"` — forwarded verbatim to the engine's
  `--test-command` flag, to probe the `test-determinism` dimension. Omit it to
  rely on the `package.json` fallback or accept `not-probed`.
- `--probe-k <int>` — forwarded to the engine's `--probe-k` flag (default 3):
  how many times the probed command is run to check exit-code agreement.

## Notes

- `audit_run_meta.py`, `harness_audit.py`, `harness_audit_report.py`, and
  `insights_flush_payload.py` are all stdlib Python 3 only — no install step,
  no network calls; `insights_flush_payload.py` never calls the network
  itself — it only assembles payloads and tracks the flush cursor
  `.fairmind/insights-sync.json`.
- Exit code `0` from `audit_run_meta.py`, `harness_audit.py`, and
  `harness_audit_report.py` (steps 1-3) means a clean run; a non-zero exit
  means a hard error (not a git work tree, invalid catalog, missing/invalid
  summary, etc.) and none of them leaves a partial output file behind — re-run
  after fixing the reported problem. `insights_flush_payload.py` always exits
  `0` and signals a degraded/missing input via `null`/stderr instead.
- A catalog or summary with no `dimensions` block is not an error (T6/T7
  contract, Amendment 1): the report simply renders pillars with no dimension
  pills, and the Insights payload `insights_flush_payload.py --emit audit`
  builds omits `dimensions` the same way.
