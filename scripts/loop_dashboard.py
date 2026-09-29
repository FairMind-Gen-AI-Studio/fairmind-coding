#!/usr/bin/env python3
"""
loop_dashboard.py — the loop transactional dashboard, printed deterministically.

One consolidated view: a title, a header cockpit for the ACTIVE loop (budget / tier /
K / confirmations), and one row per loop (history + active) with results and stats —
tool calls by kind + dispatches (from the trace ledger), duration, and best-effort
token totals (from loop_tokens). A totals footer aggregates the run.

Printed as tool output at the loop opening, at each loop's start, and in the closing
report — the model shows this stdout, it does not re-emit it (deterministic + cheap).
Robust data (trace / loop-state) always renders; token columns show `n/a` when the
best-effort source is unavailable. Stdlib only; reuses loop_ledger + loop_tokens.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_ledger as L  # noqa: E402
import loop_tokens as TK  # noqa: E402

_KINDS = ("mutate", "exec", "read", "dispatch", "other")


def _h(n):
    """Human-compact token count: 4740931 -> '4.7M', 81525 -> '82k'."""
    if n is None:
        return "n/a"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1000)}k"
    return str(n)


def _dur(start_iso, end_iso):
    a = TK._parse_iso(start_iso)
    b = TK._parse_iso(end_iso) or datetime.now(timezone.utc)
    if a is None:
        return "—"
    secs = max(0, int((b - a).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def _started_at(row):
    """History rows encode started_at in loop_id, which loop_ledger._loop_id builds as
    `<ref>@<started_at>`. Split from the RIGHT: an ISO timestamp contains no "@", but a
    task ref may (a scoped/service-qualified ref), and splitting from the left would hand
    the window an unparseable start — which silently disables it, so the row's trace stats
    would count every op ever recorded and its duration would render as a dash."""
    lid = row.get("loop_id", "")
    return lid.rsplit("@", 1)[1] if "@" in lid else None


def _trace_stats(cwd, task_ref, start_iso, end_iso):
    """Count trace ops for a loop window, by kind. Returns a dict (zeros if no file).

    `end_iso=None` — an ACTIVE, still-running loop with no `closed_at` yet — means no
    upper bound is enforced: a trace op is always appended AFTER it happens, never
    pre-dated, so there is no legitimate "future" op to exclude for a loop that hasn't
    closed. (T19 deviation from contract.arming C9's "zero dashboard change" default:
    `_start` now resolves to the arm-time stamp rather than the first-evaluation one,
    so the active window opens earlier and the writer/renderer can race within the
    same second — clamping the upper end at a freshly-read `datetime.now()` bought
    nothing but the risk of clipping a genuinely-recorded, just-written op on sub-
    second clock skew. A CLOSED loop's row always carries a real `closed_at` and
    keeps its exact upper bound, unchanged.)
    """
    stats = {"total": 0, **{k: 0 for k in _KINDS}}
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(task_ref or "session")) or "session"
    path = os.path.join(cwd, ".fairmind", "trace", safe + ".jsonl")
    if not os.path.isfile(path):
        return stats
    start = TK._parse_iso(start_iso)
    end = TK._parse_iso(end_iso)  # None only for an active/ongoing loop — see above
    try:
        for line in open(path, encoding="utf-8"):
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = TK._parse_iso(o.get("ts"))
            if start is not None and (ts is None or ts < start or (end is not None and ts > end)):
                continue
            stats["total"] += 1
            k = o.get("kind", "other")
            stats[k if k in _KINDS else "other"] += 1
    except OSError:
        pass
    return stats


def _tokens_cell(cwd, base, start_iso, end_iso):
    """(up_str, down_str, up_int, down_int) combining orchestrator + sub-agent,
    best-effort. up = input + cache_creation, down = output. 'n/a' when no source."""
    tk = TK.loop_tokens(cwd, base, start_iso, end_iso)
    up = down = 0
    any_src = False
    for src in (tk.get("orchestrator"), tk.get("subagent")):
        if src:
            any_src = True
            up += src.get("in", 0) + src.get("cache_creation", 0)
            down += src.get("out", 0)
    if not any_src:
        return ("n/a", "", None, None)
    return (f"{_h(up)}↑", f"{_h(down)}↓", up, down)


def _row_cells(cwd, base, row, active=False):
    """Display cells + stat ints for one loop row — shared by both renderers."""
    checks = f"{row.get('checks_passed', 0)}/{row.get('checks_total', 0)}"
    itmax = row.get("iter_max")
    itr = f"{row.get('iter_spent', 0)}/{itmax if itmax is not None else '—'}"
    start_iso = row.get("_start")
    end_iso = None if active else row.get("closed_at")
    st = _trace_stats(cwd, row.get("task"), start_iso, end_iso)
    tools = f"{st['total']}({st['mutate']}/{st['exec']}/{st['read']}/{st['dispatch']})"
    # A closed loop carries its token totals frozen in the ledger row (recorded at close
    # with its own base_path/window) — use them so history is stable and never recomputed
    # under the current active loop's base. The active row (and legacy rows with no frozen
    # value) compute live, best-effort.
    if not active and row.get("tokens_up") is not None:
        up_i, down_i = int(row.get("tokens_up")), int(row.get("tokens_down") or 0)
        up, down = f"{_h(up_i)}↑", f"{_h(down_i)}↓"
    else:
        up, down, up_i, down_i = _tokens_cell(cwd, base, start_iso, end_iso)
    return {
        "glyph": L._glyph(row.get("status")),
        "task": str(row.get("task", "—")),
        "status": L._short_status(row.get("status")),
        "checks": checks,
        "iter": itr,
        "tier": str(row.get("tier", "—")),
        "tools": tools,
        "dur": _dur(start_iso, end_iso),
        "tok": (up + " " + down).strip(),
        "st": st, "up_i": up_i, "down_i": down_i,
    }


def _fmt_row(cwd, base, row, active=False):
    c = _row_cells(cwd, base, row, active)
    line = (f" {c['glyph']}  {c['task'][:12]:<12} {c['status'][:14]:<14} {c['checks']:<5} "
            f"{c['iter']:<6} {c['tier']:<4} {c['tools']:<20} {c['dur']:<5} {c['tok']}")
    return line, c["st"], c["up_i"], c["down_i"]


def _active_row(state):
    """Synthesize the active loop's row + resolve its started_at."""
    if not state or state.get("status") not in ("running", "arming"):
        return None
    last = next((it for it in reversed(state.get("iterations", [])) if "results" in it), None)
    row = L.build_row(state, last["results"] if last else [], closed_at="—")
    row["_start"] = (state.get("budget", {}).get("spent", {}) or {}).get("started_at")
    return row


def _fails(state):
    """(worst, cap) consecutive failures over the loop's checks — the same reading the
    engine's `budget_exhausted` takes: `blocked_failures` trips as soon as ANY check
    reaches the cap, so the check closest to it is the one that decides, and the one the
    cockpit must show. The cockpit's whole job on this line is to say how close the loop
    is to blocking, so this number has to come from the checks, never from a constant."""
    b = state.get("budget", {}) or {}
    worst = 0
    for c in state.get("checks", []):
        try:
            worst = max(worst, int(c.get("consecutive_failures", 0) or 0))
        except (TypeError, ValueError):
            pass
    return worst, b.get("max_consecutive_failures", "—")


def _header(state, base):
    if not state or state.get("status") not in ("running", "arming"):
        return ["Active   —  (no loop running)"]
    b = state.get("budget", {}) or {}
    sp = b.get("spent", {}) or {}
    k = 3
    for c in state.get("checks", []):
        try:
            k = max(k, int((c.get("determinism", {}) or {}).get("confirmation_k", 3)))
        except (TypeError, ValueError):
            pass
    fails, fail_cap = _fails(state)
    checks = state.get("checks", [])
    admitted = [c for c in checks if (c.get("admission", {}) or {}).get("status") == "passed"]
    tier = state.get("hermeticity_tier", "B")
    return [
        f"Active   {(state.get('target') or {}).get('ref', '—')} · {state.get('status')} "
        f"· Tier {tier} · K={k}",
        f"Budget   iter {sp.get('iterations', 0)}/{b.get('max_iterations', '—')} · "
        f"fails {fails}/{fail_cap} · "
        f"timeout {b.get('timeout_min', '—')}m",
        f"State    confirmations {state.get('confirmations', 0)}/{k} · "
        f"admitted checks {len(admitted)}/{len(checks)}",
    ]


def render(cwd):
    state = L._active_state(cwd)
    base = ""
    ctx = os.path.join(cwd, ".fairmind", "active-context.json")
    if os.path.isfile(ctx):
        try:
            base = json.load(open(ctx, encoding="utf-8")).get("base_path") or ""
        except (OSError, ValueError):
            base = ""

    history = L._read_rows(cwd)
    for r in history:
        r["_start"] = _started_at(r)
    active = _active_row(state)

    lines = [f"FAIRMIND LOOP DASHBOARD · {os.path.basename(os.path.abspath(cwd))}"]
    lines += _header(state, base)
    lines.append("")
    lines.append(" ●  Task         Status         Chk   Budget Tier Tools (m/e/r/d)       Dur   Tokens*")

    rows = list(history) + ([active] if active else [])
    if not rows:
        lines.append(" —  (no loops recorded yet)")
    else:
        n_pass = n_block = tools_tot = disp_tot = up_tot = down_tot = 0
        have_tok = False
        for r in rows:
            line, st, up_i, down_i = _fmt_row(cwd, base, r, active=(r is active))
            lines.append(line)
            tools_tot += st["total"]
            disp_tot += st["dispatch"]
            if (r.get("status") or "") == "passed_pending_human":
                n_pass += 1
            elif (r.get("status") or "").startswith("blocked_"):
                n_block += 1
            if up_i is not None:
                have_tok = True
                up_tot += up_i
                down_tot += down_i
        tok = f" · {_h(up_tot)}↑ {_h(down_tot)}↓ tokens" if have_tok else ""
        lines.append("")
        lines.append(f" Totals  {len(rows)} loop(s) · {n_pass} passed / {n_block} blocked · "
                     f"{tools_tot} tool calls · {disp_tot} dispatches{tok}")
    lines.append(" * Budget = budget.spent.iterations / max_iterations — it counts only the "
                 "BUDGET-CONSUMING (red/error) evaluations, not evaluations run: a green "
                 "evaluation consumes none, so `0` means 'right first time', never 'nothing ran'.")
    lines.append(" * tokens best-effort: orchestrator (session transcript) + sub-agent "
                 "(SubagentStop capture); up = input+cache_creation, down = output; n/a if unavailable.")
    return "\n".join(lines)


def _header_md(state):
    if not state or state.get("status") not in ("running", "arming"):
        return "_No loop running._"
    b = state.get("budget", {}) or {}
    sp = b.get("spent", {}) or {}
    k = 3
    for c in state.get("checks", []):
        try:
            k = max(k, int((c.get("determinism", {}) or {}).get("confirmation_k", 3)))
        except (TypeError, ValueError):
            pass
    fails, fail_cap = _fails(state)
    ref = (state.get("target") or {}).get("ref", "—")
    return (f"**Active** {ref} · {state.get('status')} · Tier {state.get('hermeticity_tier', 'B')} · K={k} "
            f"— budget iter {sp.get('iterations', 0)}/{b.get('max_iterations', '—')} · "
            f"fails {fails}/{fail_cap} · timeout {b.get('timeout_min', '—')}m · "
            f"confirmations {state.get('confirmations', 0)}/{k}")


def render_md(cwd):
    """Markdown-table form of the dashboard. It renders as a real table only when the
    model relays it into its message (raw tool output would show the pipes literally),
    so this is the form the command includes in the opening and the closing report."""
    state = L._active_state(cwd)
    base = ""
    ctx = os.path.join(cwd, ".fairmind", "active-context.json")
    if os.path.isfile(ctx):
        try:
            base = json.load(open(ctx, encoding="utf-8")).get("base_path") or ""
        except (OSError, ValueError):
            base = ""
    history = L._read_rows(cwd)
    for r in history:
        r["_start"] = _started_at(r)
    active = _active_row(state)

    repo = os.path.basename(os.path.abspath(cwd))
    out = [f"**Fairmind loop dashboard · {repo}**", "", _header_md(state)]
    rows = list(history) + ([active] if active else [])
    if not rows:
        out += ["", "_No loops recorded yet._"]
    else:
        out += ["",
                "| | Task | Status | Chk | Budget | Tier | Tools (m/e/r/d) | Dur | Tokens\\* |",
                "|---|---|---|---|---|---|---|---|---|"]
        n_pass = n_block = tools_tot = disp_tot = up_tot = down_tot = 0
        have_tok = False
        for r in rows:
            c = _row_cells(cwd, base, r, active=(r is active))
            out.append(f"| {c['glyph']} | {c['task']} | {c['status']} | {c['checks']} | {c['iter']} | "
                       f"{c['tier']} | {c['tools']} | {c['dur']} | {c['tok'] or '—'} |")
            tools_tot += c["st"]["total"]
            disp_tot += c["st"]["dispatch"]
            s = r.get("status") or ""
            if s == "passed_pending_human":
                n_pass += 1
            elif s.startswith("blocked_"):
                n_block += 1
            if c["up_i"] is not None:
                have_tok = True
                up_tot += c["up_i"]
                down_tot += c["down_i"]
        tok = f" · {_h(up_tot)}↑ {_h(down_tot)}↓ tokens" if have_tok else ""
        out += ["", f"**Totals** {len(rows)} loop(s) · {n_pass} passed / {n_block} blocked · "
                    f"{tools_tot} tool calls · {disp_tot} dispatches{tok}"]
    out += ["", "**Budget** = `budget.spent.iterations` / `max_iterations` — it counts only the "
                "BUDGET-CONSUMING (red/error) evaluations, **not** evaluations run: a green evaluation "
                "consumes none, so `0` means \"right first time\", never \"nothing ran\".",
                "", "\\* tokens best-effort: orchestrator (session transcript) + sub-agent "
                "(SubagentStop capture); up = input+cache_creation, down = output; n/a if unavailable."]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print the loop transactional dashboard.")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--md", action="store_true", help="Markdown table form (for the model to relay).")
    args = ap.parse_args(argv)
    cwd = args.cwd or os.getcwd()
    print(render_md(cwd) if args.md else render(cwd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
