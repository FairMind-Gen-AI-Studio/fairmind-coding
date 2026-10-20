# design-brief.md — the design decisions a contract cannot hold

## Why this exists

A compiled contract is a list of *checkable claims*. It is not a design. And when a ticket
arrives with no acceptance criteria, extracting them **is** the design step — while
`task-compilation` is deliberately forbidden to design, because a skill that invents
thresholds ships checks asserting the wrong thing. So with no artifact in between, the
design gets made implicitly, by whoever writes the criteria, against the ticket's own
wording, and nobody ever reviews it as a design.

Measured 2026-08-24, on a card whose prose said *the route accepts it and validates it*:
the compiled contract pinned the rule at the HTTP boundary and left the store accepting
anything. Every check went green on the **first** evaluation, the loop spent 0 of 8
iterations, and the append-only log could still be handed an invalid row by any non-HTTP
caller. The gap report read `coverage: 1.0`.

## When

Every loop, before any criterion is classified. **Sized to the task**: on a change with one
obvious layer and no new way to fail, five lines is a complete brief. A brief that is short
because the questions genuinely have one answer is correct — a brief that is short because
the questions were skipped is the failure this exists to stop.

## Where

`${FAIRMIND_BASE}/design/<ref>.md`. Under `.fairmind/`, so it is structurally exempt from
`blocked_scope` (`_is_loop_workspace_path`) and never lands in the diff a customer reviews.

## It is a PLAN, never a REPORT

Written **before** implementation, about what will be built and why. Never a retrospective
of what was done, what was learned, or what the numbers came out to — that is the journal's
job. A brief rewritten afterwards to match the code has destroyed the one property it had.

## The sections

### 1. The problem, in the code's own terms

Name the function, module or contract that is wrong today, and what a caller cannot do
because of it. Not a restatement of the ticket.

### 2. Where each invariant lives — *the load-bearing section*

For every rule this task introduces, or must preserve, answer three questions **in writing**:

1. **Which layer owns it**, and **which existing precedent in this codebase puts it there.**
   Name the precedent by symbol, not by description.
2. **Which other callers reach the same state?** Enumerate them: another route, a CLI, a
   script, a test helper, a consumer that does not exist yet.
3. **Can any of them bypass the rule as placed?** If yes, it is at the wrong layer, or it
   needs a second placement. Say which, and place it.

⚠️ **A layer named in the ticket is where the reporter noticed the problem — not a finding
about where the rule belongs.** Ticket prose says "the route validates it" because the route
is what the reporter was looking at. Re-derive the placement against the code. If you land
back on the ticket's layer, say so and name the reason: agreeing with the ticket after
checking is a different act from transcribing it, and only one of them is reviewable.

Special force for anything **append-only, immutable, or published**: a row admitted by
mistake cannot be corrected later without breaking the contract that made it valuable. For
those, the rule belongs at the narrowest point every writer must pass through.

### 3. Refusal and error semantics

For anything this task makes reject: which existing error class it uses, and **why not the
neighbouring one** the codebase already uses for an adjacent failure. State whether the
message carries caller-supplied input verbatim, and if so why that is safe here.
Skip this section only when the task adds no new way to fail.

### 4. The precedent, and where it stops

What existing pattern this copies — **and the part of the analogy that does not transfer.**
An unqualified precedent gets over-applied by whoever implements it.

### 5. Non-happy inputs

Per invariant: absent, malformed, belonging to another tenant or owner, right shape and
wrong category, at a boundary. These become criteria. The ones you decide not to handle are
named here, with the reason.

### 6. Out of scope

What this task will not do, and why — the neighbouring card, the deliberate deferral.

## What the brief feeds

- **The acceptance criteria are derived from the brief's decisions** — written into the
  `TaskDraft`'s `acceptance_criteria` **before** classification, never patched into
  `contract.criteria[]` after it: `loop_import.py` requires the classification map's
  decision id-set to equal the draft's AC id-set exactly, so a late edit is refused.
- **The human sees the decisions the ticket did not state**, as decisions, alongside the gap
  report, before anything arms. `coverage: 1.0` says every criterion has a check; it cannot
  say the criteria describe the right design.
- **The completeness reviewer reads it at the exit gate.** The question there — *does the
  diff implement every decision in this brief, at the layer it named* — is one no check in
  the contract can ask, because the contract is what the brief was written to correct.
