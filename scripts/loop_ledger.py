#!/usr/bin/env python3
"""
loop_ledger.py — the loop run ledger: one row per loop, at a glance.

`.fairmind/loop-ledger.jsonl` (per-repo) records exactly ONE row per loop, written when
that loop reaches a terminal state (passed_pending_human / blocked_*) — the history of
closed loops. A loop revived by `--extend-budget` reaches a second terminal state under
the same `loop_id`; its row is then SUPERSEDED IN PLACE (it keeps its slot in history)
so the ledger says where the loop ended, never where it merely passed through. The
current loop, still running, is not in the file; the renderer synthesizes its live row
from `.fairmind/active-context.json` → `loop-state.json` so the table always shows
"what happened + what's happening now".

One row per loop is a load-bearing invariant, not tidiness: the dashboard's totals
(loops, passed/blocked, tool calls, tokens) are aggregated from these rows, so a
second row for one loop double-counts it and a dropped row loses it.

Two entry points:
  - record_terminal(cwd, state, results): the engine calls this once, on the
    terminal transition, to append the closed loop's row.
  - render(cwd) / `loop_ledger.py --render --cwd <repo>`: the /fairmind-loop command
    prints the table verbatim at the opening and in the closing report.

Deterministic by construction: timestamps are stored and rendered verbatim (never
"2m ago"), so the same ledger renders identically every time. Stdlib only, like the
gate engine. A ledger failure is never allowed to break the gate — the engine wraps
its call so a cosmetic ledger error can never fail-close a check.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# PCF-28: one writer for the consumer-repo ignore entry, asked by every site
# that can create `.fairmind/`. A stdlib-only LEAF module — importing it
# costs nothing else — reached the way `_loop_ledger` and `ambient_digest`
# already make their own dir importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _fm_ignore import makedirs_ignored  # noqa: E402

LEDGER_REL = os.path.join(".fairmind", "loop-ledger.jsonl")

_STATUS_GLYPH = {
    "passed_pending_human": "🟢",
    "running": "🟡",
    "arming": "⚪",
}


def _glyph(status):
    if not status:
        return "⚪"
    if status.startswith("blocked_"):
        return "🔴"
    return _STATUS_GLYPH.get(status, "⚪")


def _short_status(status):
    """Compact label for the table cell."""
    if status == "passed_pending_human":
        return "passed→human"
    return status or "—"


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ledger_path(cwd):
    return os.path.join(cwd, LEDGER_REL)


# --- writing ----------------------------------------------------------------

def _loop_id(state):
    """Stable per-run id: the task ref + the run's start instant. `started_at` is set
    by the engine on the first evaluation, so it is present by the terminal one."""
    ref = (state.get("target") or {}).get("ref") or "loop"
    started = ((state.get("budget") or {}).get("spent") or {}).get("started_at") or ""
    return f"{ref}@{started}" if started else ref


def _counts(results):
    """(green, total) over the evaluated checks in a results list."""
    total = len(results or [])
    green = sum(1 for r in (results or []) if r.get("verdict") == "green")
    return green, total


def build_row(state, results, closed_at=None):
    budget = state.get("budget") or {}
    spent = budget.get("spent") or {}
    green, total = _counts(results)
    if total == 0:
        # No evaluation results yet (a freshly-armed active loop): show the count of
        # admitted checks so the row reads e.g. 0/1 rather than a misleading 0/0.
        total = sum(1 for c in state.get("checks", [])
                    if (c.get("admission", {}) or {}).get("status") == "passed")
    return {
        "loop_id": _loop_id(state),
        "task": (state.get("target") or {}).get("ref") or "loop",
        "status": state.get("status"),
        "checks_passed": green,
        "checks_total": total,
        "iter_spent": spent.get("iterations", 0),
        "iter_max": budget.get("max_iterations"),
        "tier": state.get("hermeticity_tier", "B"),
        "closed_at": closed_at or _iso_now(),
    }


def _freeze_tokens(cwd, state, row):
    """Best-effort: record the loop's token totals into its ledger row at close.

    A closed loop's tokens are FROZEN here, computed once with the loop's own base_path
    and window, so the dashboard renders a stable, correct value for history. Without
    this the dashboard recomputes every row's tokens under whatever loop is active NOW —
    a history row then reads the wrong sub-agent file and its numbers drift between
    renders. Any failure simply omits the fields (the dashboard falls back to a live,
    best-effort compute); never fatal — the engine also wraps this whole call.
    """
    try:
        import loop_tokens as TK  # lazy: a token-source error must never break the ledger
        base = ""
        ctx = os.path.join(cwd, ".fairmind", "active-context.json")
        if os.path.isfile(ctx):
            with open(ctx, encoding="utf-8") as fh:
                base = json.load(fh).get("base_path") or ""
        started = ((state.get("budget") or {}).get("spent") or {}).get("started_at")
        tk = TK.loop_tokens(cwd, base, started, row.get("closed_at"))
        up = down = 0
        any_src = False
        for src in (tk.get("orchestrator"), tk.get("subagent")):
            if src:
                any_src = True
                up += src.get("in", 0) + src.get("cache_creation", 0)
                down += src.get("out", 0)
        if any_src:
            row["tokens_up"] = up
            row["tokens_down"] = down
    except Exception:
        pass


def _write_rows(path, rows):
    """Rewrite the ledger atomically (temp file in the same dir, then os.replace), the
    way the engine writes loop-state.json: superseding a row must never be able to leave
    the run history truncated or half-written."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".loop-ledger.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# The accounting a terminal decision is identified by. Two records of the SAME close
# agree on all of it; a second close of a REVIVED loop cannot (extending a budget moves
# iter_max, and reaching a terminal state again moves iter_spent and/or status).
_CLOSE_FIELDS = ("status", "checks_passed", "checks_total", "iter_spent", "iter_max", "tier")


def _same_close(a, b):
    return all(a.get(f) == b.get(f) for f in _CLOSE_FIELDS)


def record_terminal(cwd, state, results):
    """Record the closed loop's row — one row per LOOP, carrying the terminal state the
    loop ended on.

    The row is keyed on `loop_id` alone. A revived loop keeps its id (`<ref>@<started_at>`,
    and `--extend-budget` never re-stamps `started_at`), so its second close SUPERSEDES the
    first row in place. Keying on the (loop_id, status) PAIR instead is what breaks the
    invariant: a revived loop that ends on a new status gains a second row, and one that
    re-blocks on the same status keeps the stale one — and the dashboard, which aggregates
    its totals from these rows, inherits both errors.

    Idempotent: re-recording the SAME close (identical terminal accounting) is a no-op, so
    the row keeps its original `closed_at` and its frozen token totals instead of drifting
    to a fresh timestamp on every call.
    """
    path = _ledger_path(cwd)
    makedirs_ignored(os.path.dirname(path))
    row = build_row(state, results)
    existing = _read_rows(cwd)
    prior = next((i for i, r in enumerate(existing) if r.get("loop_id") == row["loop_id"]), None)
    if prior is not None and _same_close(existing[prior], row):
        return  # this very close is already on the record
    _freeze_tokens(cwd, state, row)  # after the no-op guard: freezing costs IO
    if prior is None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return
    existing[prior] = row  # the loop keeps its place in history; only its outcome moves
    _write_rows(path, existing)


# --- reading / rendering ----------------------------------------------------

def _read_rows(cwd):
    path = _ledger_path(cwd)
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # a corrupt line never breaks the render
    return rows


def _active_state(cwd):
    """Resolve the current loop-state.json via active-context.base_path, if any."""
    ctx = os.path.join(cwd, ".fairmind", "active-context.json")
    if not os.path.isfile(ctx):
        return None
    try:
        with open(ctx, encoding="utf-8") as fh:
            base = json.load(fh).get("base_path")
    except (OSError, ValueError):
        return None
    if not base:
        return None
    sp = os.path.join(cwd, base, "loop-state.json")
    if not os.path.isfile(sp):
        return None
    try:
        with open(sp, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _live_row(state):
    """Synthesize the row for the active loop from its live state (latest evaluation's
    counts), so the table shows the loop in progress before it is written to history."""
    if not state:
        return None
    if state.get("status") not in ("running", "arming"):
        return None  # terminal → already in history, don't double-count
    last = next((it for it in reversed(state.get("iterations", [])) if "results" in it), None)
    results = last["results"] if last else []
    return build_row(state, results, closed_at="—")


def _fmt_row(r):
    task = str(r.get("task", "—"))[:14]
    status = _short_status(r.get("status"))[:14]
    checks = f"{r.get('checks_passed', 0)}/{r.get('checks_total', 0)}"
    itmax = r.get("iter_max")
    itr = f"{r.get('iter_spent', 0)}/{itmax if itmax is not None else '—'}"
    tier = str(r.get("tier", "—"))
    when = str(r.get("closed_at", "—"))
    if when != "—" and "T" in when:
        when = when.split("T", 1)[0]  # date only, keeps it compact + deterministic
    return f"  {_glyph(r.get('status'))}  {task:<14} {status:<14} {checks:<6} {itr:<7} {tier:<4} {when}"


def render(cwd, fenced=False):
    """Return the ledger table: closed loops (history) + the active loop as a live
    row. Deterministic for a given ledger + state. Plain text by default (printed as
    tool output at the loop opening, where the model does not re-emit it); pass
    fenced=True to wrap it in a code block for relaying inside a prose report."""
    history = _read_rows(cwd)
    live = _live_row(_active_state(cwd))
    body = ["  ●  Task           Status         Check  Budget  Tier When"]
    if not history and not live:
        body.append("  —  (no loops recorded yet)")
    else:
        for r in history:
            body.append(_fmt_row(r))
        if live is not None:
            body.append(_fmt_row(live))
    text = "\n".join(body)
    return f"```text\n{text}\n```" if fenced else text


def main(argv=None):
    parser = argparse.ArgumentParser(description="Loop run ledger (render / append).")
    parser.add_argument("--cwd", default=None, help="Repo root (defaults to process cwd).")
    parser.add_argument("--render", action="store_true", help="Print the ledger table.")
    args = parser.parse_args(argv)
    cwd = args.cwd or os.getcwd()
    if args.render:
        print(render(cwd, fenced=True))  # fenced for relaying inside the Exit report
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
