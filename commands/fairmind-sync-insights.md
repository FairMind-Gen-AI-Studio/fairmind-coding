---
description: Flush any unflushed Agentic Insights payloads (loop stats, agent decisions, harness-audit runs) to project-context and any unproposed architecture decisions to the company brain, on demand and independent of a loop close
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/insights_flush_payload.py:*), Read, mcp__Fairmind__Insights_record_harness_audit, mcp__Fairmind__Insights_record_loop_stats, mcp__Fairmind__Insights_record_agent_decisions, mcp__Fairmind__Brain_record_decision
---

# fairmind-sync-insights

`/fairmind-loop`'s Exit gate and `/harness-audit`'s own final step both flush what they
just produced to Agentic Insights automatically — but either one leaves its payload
**unflushed on disk** when the Fairmind MCP was not connected at that moment, or when a
send was attempted and failed partway. `/fairmind-sync-insights` is the catch-up verb:
run it any time (standalone, on a schedule, or right after reconnecting Fairmind) to pick
up everything still pending across **every** category — loop stats, agent
decisions, harness-audit runs, and the architecture decisions not yet proposed to the
brain — and send it now.

## Usage

```bash
/fairmind-sync-insights
```

No arguments: it always reads whatever is on disk for the current repo and flushes
whatever is pending.

## What it does

1. **Emit everything pending, in one call:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/insights_flush_payload.py --emit all --out
   ```
   This writes the payloads to a file **outside the repo** and prints only a short
   summary on stdout:
   ```json
   {"out": "/…/fairmind-insights-flush-XXXX.json", "bytes": 28995,
    "categories": {"loop": 6581, "decisions": 13608, "audit": null, "brain": 412}}
   ```
   `Read` the `out` path to get the payloads: the file holds
   `{"loop": ..., "decisions": ..., "audit": ..., "brain": ...}`, each key either the pending payload
   for that category or JSON `null` when there is nothing new to send (already flushed,
   or the category's source files are missing/degraded — a missing
   `.fairmind/audit/run-meta.json`, for instance, quietly emits `audit: null` under this
   `--emit all` call, with no advisory: a repo that never ran `/harness-audit` is the
   normal case here, so nothing is printed to stderr. The "run /harness-audit first"
   advisory only appears on an explicit `--emit audit` request, not on this catch-up
   command's `--emit all`). In the summary, a non-null `categories` entry is that
   category's byte size — use it to decide which MCP tools step 2 needs to call.

   **Do not drop `--out`.** Without it the payloads are printed to stdout, and a Bash
   tool result is capped at **30,000 bytes** — which a real repo already exceeds
   (measured: 39,688 B for the `fairmind-plugins` `loop-t8` close, 28,995 B for
   `the project-context service`). Past the cap you receive a ~2 KB preview instead of
   the payload, with no error to catch: you would reconstruct a fleet number from a
   truncated preview, skip a category, or fail the run. This is a limit of the channel
   between the script and you — the MCP tools themselves have no body cap.

2. **Send each non-null category through its own MCP tool** — never batch them into one
   call, since a partial failure must be attributable to exactly one category:
   - `loop` → `mcp__Fairmind__Insights_record_loop_stats`
   - `decisions` → `mcp__Fairmind__Insights_record_agent_decisions`
   - `audit` → `mcp__Fairmind__Insights_record_harness_audit`
   - `brain` → `mcp__Fairmind__Brain_record_decision`

   **`decisions` and `brain` are each a list of batches — one call per batch**, with that
   batch's own `repository`, `git_remote`, `session_ref` and `task_ref`. Decisions recorded
   since the last run opened (its `loop_open.py --repoint`) travel under its refs; every
   earlier pending decision travels in a separate batch whose `session_ref`/`task_ref` are
   `null`. When `active-context.json` has no usable `decisions_start_line` (a context from
   before the mark existed, or one the ledger no longer reaches), nothing says which rows
   are that run's, so **every** pending decision travels in the `null` batch until the next
   new-task repoint records a mark. Send each exactly so and never merge them: both doors
   stamp a batch's refs onto every row they store, so one merged call would relabel older
   decisions as that run's.

   ⚠️ **`brain` and `decisions` are the same rows through two different doors, and
   neither is derived from the other.** `brain` carries only the rows the agent typed
   `kind: "architecture"`; `decisions` carries every row. They keep separate cursor maps,
   so one door succeeding never marks the other sent, and a repository that has flushed
   its insights has not therefore told the brain anything. Send both when both are
   non-null.

   **When this repository has turned `brain` off, the category simply will not be there.**
   The producer reads the switch itself — central policy first, then the repo file — so
   `--emit brain` returns `null` and `--commit brain` advances nothing. You do not have to
   remember to skip it, and a caller who never read this paragraph cannot send it either.
   `/fairmind-config` reports the effective state on its `brain write-back` line.

3. **Commit only after that category's send succeeds** — the cursor advance and the MCP
   call are never bundled:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/insights_flush_payload.py --commit <category>
   ```
   🔴 **Never `--commit all`.** The CLI accepts it and it commits every category, including
   the one you may deliberately have skipped: a repository can switch `brain` off, and then
   nothing sent those rows. A cursor that says delivered is never pending again.

   Call `--commit loop`, `--commit decisions`, `--commit audit`, `--commit brain`
   individually, one **only after** its own MCP call above has returned success — for
   `decisions` and `brain`, after **every** batch's call has — never speculatively, never for a
   category whose send errored or did not complete. A failed or partial send leaves that
   category **uncommitted**: it stays on disk exactly as it was and this same command
   picks it up again next run, so nothing is ever silently dropped and nothing is ever
   marked sent that was not.

4. **Report per-category counts.** Tell the user, per category, how many records were
   flushed this run (a loop close, `<n>` decision rows across its batches, an audit run, `<n>`
   architecture decisions proposed to the brain) versus
   how many stayed pending because their send failed or the category was already `null`.
   Do not report a single aggregate number — the per-category breakdown is what lets a
   partial failure be diagnosed and re-run.

   **Name the failed category and say whether it failed the same way last time.** A send
   that errors tells you nothing about *why* on its own — see the section below — so the
   repetition is the signal, and it is only visible if the report states it. When a
   category has now failed twice over an unchanged payload, say so and stop retrying.

## Backfill semantics

The cursor (`.fairmind/insights-sync.json`) is what stands between "already
sent" and "pending" — it is safe to delete. (It is no longer the ONLY thing: a
`--commit decisions`/`brain` also spends the sent-set `.fairmind/insights-inflight.json`
that the `--emit` before it recorded, which BOUNDS what that commit marks rather than
deciding what is pending. Deleting the cursor alone still backfills everything, because
an absent sent-set means an unbounded walk.) **Deleting** the cursor file makes every
category pending again (a full **backfill**): the next `--emit all` re-emits everything
currently on disk, and the next `--commit` re-marks it flushed. This is safe because the
server-side `Insights_record_*` tools **upsert** — re-sending an already-recorded loop
close, decision, or audit run is idempotent, never a duplicate. Use this when the cursor
itself is suspect (corrupted, or you want to re-verify what actually landed server-side)
rather than trying to hand-edit it.

## When a send keeps failing — and why the client cannot tell you why

⚠️ **A rejected `Insights_record_loop_stats` is indistinguishable from a transient
failure.** The MCP tools declare **closed** parameter sets, so a payload carrying a field
the *deployed* project-context does not declare fails **the whole call at the door** — not
that one field. What you see is a failed tool call: the same thing you see when the network
blips or the server restarts. There is no status code to key on and no "unknown field" read
back, so the retry loop cannot self-diagnose a schema mismatch — left alone it will re-send
the same rejected payload forever, politely.

**The discriminator is repetition, not the message.** A transient failure clears on the next
run. A schema mismatch fails **identically on every run over an unchanged payload** — same
loop, same bytes, same error. Two or three consecutive runs failing on the same category
with nothing changed on disk is a **version skew**, not a network problem: the plugin is
emitting fields the deployed server does not accept yet.

**What an operator does about it, in order:**

1. **Nothing on disk is lost — and step 3's rule is exactly what guarantees that.** Never
   `--commit` a category whose send errored. The payload stays byte-identical on disk and
   the next run picks it up again, so a skew can be diagnosed at leisure and delivered
   afterwards with no backfill and no gap.
2. **Compare the plugin version against the server deployment**, and report the mismatch
   rather than retrying. Retrying a rejection is precisely what makes it look transient.
3. **Do not delete the cursor to "retry harder".** A backfill re-emits every category for
   every loop, and a rejected payload is simply rejected again, once per loop. The cursor
   is for a *suspect cursor* (see above), never for a failing send.

**The other lane fails the opposite way, and worse.** The ambient session records travel over
REST, where a field the server does not declare is **accepted, dropped, and answered `200`**.
A clean drain is therefore not evidence that the server stored what was sent, and no doctor
output can reveal it. That one is caught by the two-sided conformance fixtures instead —
this plugin pins the bytes it emits, project-context pins that its schema accepts them with
**zero** dropped keys — which is why those fixtures are re-sealed on both sides in the same
change or not at all.

## Notes

- `insights_flush_payload.py` is stdlib Python 3 only — no install step, no network
  calls; it never calls the network itself, only reads/writes local JSON.
- When the Fairmind MCP is not connected at all, this command has nothing useful to do —
  report that plainly and stop; nothing on disk is touched (no `--commit` without a real
  send), so a later run with Fairmind connected picks up exactly the same pending set.
- This command complements, it does not replace, the terminal flush `/fairmind-loop`
  already runs on close and the flush `/harness-audit` already runs on a successful run —
  those stay the primary path. `/fairmind-sync-insights` exists for what those two leave
  behind: a disconnected MCP at the time, or a send that failed partway.
