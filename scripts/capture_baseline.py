#!/usr/bin/env python3
"""
capture_baseline.py — capture a frozen baseline for reduce/improve goals
(metric / performance) on a *clean committed ref*, so the baseline reflects
committed code, not the maker's dirty working tree.

It runs the measurement inside an isolated `git worktree` checked out at the
ref (default HEAD), reads the produced number, then removes the worktree — the
maker's working tree is never disturbed. If git or worktrees are unavailable it
degrades to measuring in place and marks the baseline `clean: false` (honest
Tier-B behaviour), never silently pretending it was clean.

The measurement command must, inside the checkout, write a JSON file (at the
path given by --measure-out, relative to the checkout) containing a numeric
value at --selector (default `$.value`) — e.g. invoke measure_metric.py.

A baseline is frozen for the whole run and every reduce/improve goal is gated
against it, so it may only ever come from a measurement *this* run made: a
measurement that fails (or exits 0 without writing a number) produces NO
baseline and a non-zero exit, never a leftover file promoted to a number nobody
measured.

Exit codes: 0 baseline written · 3 measurement failed (no baseline written).

Usage:
  capture_baseline.py --ref HEAD \
      --command 'python3 "$SCRIPTS"/measure_metric.py --kind loc --paths src --out .baseline.json' \
      --measure-out .baseline.json --selector '$.value' --out baseline.json
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

_SELECTOR_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def resolve_selector(obj, selector):
    if selector in (None, "", "$"):
        return obj
    path = selector[2:] if selector.startswith("$.") else selector.lstrip("$")
    cur = obj
    for key, idx in _SELECTOR_TOKEN.findall(path):
        cur = cur[int(idx)] if idx != "" else cur[key]
    return cur


def sh(argv, cwd=None, check=True):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {proc.stderr.strip()}")
    return proc


def git_available():
    return shutil.which("git") is not None


def resolve_sha(ref):
    try:
        return sh(["git", "rev-parse", ref]).stdout.strip()
    except RuntimeError:
        return None


class MeasurementError(RuntimeError):
    """The measurement did not produce a number this run can vouch for."""


def stat_sig(path):
    """Identity + mtime signature, used to prove the measurement rewrote the file
    on this run. None when the path does not exist."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def run_measure(command, cwd):
    argv = ["/bin/sh", "-c", command] if os.name != "nt" else [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=1200, check=False)
    except subprocess.TimeoutExpired:
        raise MeasurementError("measurement command timed out after 1200s") from None
    # A failed measurement must never yield a baseline. Discarding the exit status
    # here would leave whatever file already sits at --measure-out to be frozen as
    # the baseline, and every reduce/improve goal would then be gated against a
    # number nobody measured.
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise MeasurementError(f"measurement command exited {proc.returncode}: {detail}")
    return proc


def read_value(measure_out_path, selector, before_sig=None):
    """The measured number, refusing anything this run did not produce."""
    sig = stat_sig(measure_out_path)
    if sig is None:
        raise MeasurementError(f"measurement wrote no file at {measure_out_path}")
    # Exit 0 but the file is byte-for-byte the one that was already there: the
    # command measured nothing (wrong output path, no-op script) and this is a
    # leftover from an earlier, unrelated run — stale, not a measurement.
    if before_sig is not None and sig == before_sig:
        raise MeasurementError(f"{measure_out_path} was not rewritten by this run (stale file, not a measurement)")
    try:
        with open(measure_out_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise MeasurementError(f"{measure_out_path} is not readable JSON: {exc}") from None
    try:
        value = float(resolve_selector(payload, selector))
    except (KeyError, IndexError, TypeError, ValueError):
        raise MeasurementError(f"no number at selector {selector} in {measure_out_path}") from None
    # json.load accepts NaN/Infinity: a non-finite value compares False against
    # every predicate and slips through every regression guard.
    if not math.isfinite(value):
        raise MeasurementError(f"measured value is not finite: {value}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Capture a frozen baseline on a clean ref.")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--command", required=True, help="Measurement command, run inside the checkout.")
    parser.add_argument("--measure-out", required=True, help="Path (relative to checkout) the command writes.")
    parser.add_argument("--selector", default="$.value")
    parser.add_argument("--out", required=True, help="Where to write the baseline JSON.")
    args = parser.parse_args(argv)

    clean = False
    sha = None
    value = None

    try:
        if git_available() and resolve_sha(args.ref):
            sha = resolve_sha(args.ref)
            worktree = tempfile.mkdtemp(prefix="fairmind-baseline-")
            try:
                sh(["git", "worktree", "add", "--detach", worktree, sha])
                # The checkout can already carry a committed file at --measure-out;
                # snapshot it so only a rewrite by this run counts as measured.
                measure_out = os.path.join(worktree, args.measure_out)
                before = stat_sig(measure_out)
                run_measure(args.command, cwd=worktree)
                value = read_value(measure_out, args.selector, before_sig=before)
                clean = True
            finally:
                sh(["git", "worktree", "remove", "--force", worktree], check=False)
                if os.path.isdir(worktree):
                    shutil.rmtree(worktree, ignore_errors=True)
        else:
            # Degraded: measure in place, mark not-clean (hermeticity-unverified).
            before = stat_sig(args.measure_out)
            run_measure(args.command, cwd=os.getcwd())
            value = read_value(args.measure_out, args.selector, before_sig=before)
    except MeasurementError as exc:
        # No baseline on a failed measurement — not even a degraded one. A silent
        # freeze here would gate the whole run against an unmeasured number, so
        # the run stops until the measurement command is fixed.
        print(f"capture_baseline: MEASUREMENT FAILED — {exc}", file=sys.stderr)
        print(f"capture_baseline: no baseline written to {args.out}; fix the measurement command and re-run.",
              file=sys.stderr)
        if os.path.exists(args.out):
            print(f"capture_baseline: WARNING {args.out} still holds an EARLIER baseline — it is not this run's.",
                  file=sys.stderr)
        return 3

    # Emit the same object shape a check's `baseline` field accepts
    # ({value, ref, clean}), so the captured provenance — including a dirty-tree
    # `clean: false` — can be pasted straight into loop-state.json and surfaced
    # by the gate as [DEGRADED: baseline dirty-tree].
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"value": value, "ref": sha or args.ref, "clean": clean}, fh, indent=2)
        fh.write("\n")
    note = "clean committed ref" if clean else "IN-PLACE (dirty tree — hermeticity-unverified)"
    print(f"capture_baseline: baseline={value} from {sha or args.ref} [{note}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
