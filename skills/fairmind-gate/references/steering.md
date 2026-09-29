# steering.md — the human→maker channel

A loop's checks are a machine-checkable stop condition; `steering.md` is the one human channel *into* a running loop that is not a check. This reference is its contract.

## What it is, where it lives

`${FAIRMIND_BASE}/steering.md` — a plain-text/Markdown file, always under `.fairmind/` (per the workspace contract in this plugin's `CLAUDE.md` → **Workspace contract**: `.fairmind/<project-slug>/<session-slug>/`). It carries whatever a human wants to say to the maker mid-loop, in the human's own words: a redirect ("stop chasing check X, look at Y instead"), a do-not-repeat ("the last three iterations tried the same fix — don't try it again"), or a context drop (a fact, a link, a constraint the human knows and the loop doesn't).

## Who writes it, who reads it

**The human writes it, by hand.** No script, hook, or agent ever emits `steering.md` — that would make it just another machine artifact, and the whole point is that it carries a voice the engine cannot generate. It may be absent for an entire run; that is the default, healthy state.

**The maker (`software-engineer`) reads it — at the start of every iteration**, before reading the prior gate feedback and before touching any code (`agents/software-engineer.md` → "Loop mode: the maker" → step 1). Reading it first means a human course-correction takes effect on the very next turn, not after the maker has already re-walked the same dead end the steering note was written to stop. This protocol step is the *primary* mechanism — it holds even if the hook below were absent or misconfigured.

Mechanically, `hooks/scripts/inject-context.sh` (PreToolUse on `Task`) reinforces it, belt-and-suspenders: it rewrites the dispatched sub-agent's own Task `prompt` via `hookSpecificOutput.updatedInput`, prepending the file's content alongside the existing `FAIRMIND_BASE=…` context line before the Task runs. That is a deliberate choice, not the obvious one — a PreToolUse hook's plain stdout on exit 0 is written to the debug log only and is never added to any model's context (the Claude Code hooks docs name `UserPromptSubmit`/`UserPromptExpansion`/`SessionStart` as the only stdout-to-context events), and a Task sub-agent's context is built solely from the Task tool's `prompt` argument plus its own agent-definition file. Printing the text would have been silently inert; rewriting the prompt via `updatedInput` is what actually lands it in front of the sub-agent. A missing or empty `steering.md` is a silent skip in that hook, never an error: the channel is optional by design.

## The hard boundary: outside the checked surface

`steering.md` is advisory, never a check. It must never become a check descriptor, and reading it must never lead the maker to edit one — that would let a human note silently redefine the stop condition, which is exactly what admission and maker≠checker exist to prevent. If steering ever contradicts what a check asserts, that is a **rebuttal to the checker** (the same apply-or-rebut channel the maker already uses for gate feedback), never a reason to touch the descriptor. The maker is read-only on gate artifacts regardless of what steering.md says.

It is also outside the loop's mutation set and scope boundary — structurally, not by convention. Because it lives under `.fairmind/`, `compute_mutation_set` drops it before returning `paths` via `_is_loop_workspace_path` (defined in `scripts/_gate_mutation.py`, imported back into `run_gate_checks.py`), the same blanket rule that already excludes `loop-state.json`, the trace ledger, and journals. Editing `steering.md`:

- never registers as a code mutation,
- never trips `blocked_scope` even when `contract.scope.allowed_paths` is declared over code paths,
- never satisfies or breaks any check.

(`i5-ac1-steering-exempt` is the guard that asserts this explicitly, rather than leaving it merely assumed — see `tests/test_steering_channel.py`.)

## How it relates to the gate's own signals

The gate already has two *engine-initiated* stall signals (see `commands/fairmind-loop.md` → "Commitment boundaries"): **STRATEGY TURN**, raised when a check is 2 consecutive failures from its cap and routed to the checker, and **CONTRACT CONFLICT**, raised when two checks are strictly anti-correlated and routed to the Technical Lead. Both fire *from inside the loop*, on iteration history the engine observes.

`steering.md` is the *human-initiated* counterpart: a person watching the run can redirect it at any point, for any reason the engine has no way to detect — not only the two stall shapes the commitment-boundary heuristics happen to catch. The two mechanisms compose: a STRATEGY TURN can prompt a human to *write* a steering note in response, but the note itself carries no engine trigger of its own — it is read, not evaluated.

## The durable rule

A human channel into a loop must sit **outside the checked surface**. This is the same reason journals, loop-state, and the trace ledger all live under `.fairmind/` and never gate: anything that can influence a check's own descriptor or verdict corrupts the stop condition it exists to keep honest. `steering.md` extends that rule to a channel humans write, rather than one the engine writes — same boundary, opposite author.
