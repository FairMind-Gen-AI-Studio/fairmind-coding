#!/usr/bin/env python3
"""
measure_metric.py — `metric` check command: produce a single number a loop
check compares against a threshold (reduce LOC / complexity, raise coverage,
shrink bundle, …). Standard library only; portable across customer OSes.

It only *measures* — the threshold and direction live in the descriptor's
`predicate` / `regression_guard`. Output is a small JSON the loop engine reads:

  { "value": <number>, "kind": "<kind>", "unit": "<unit>" }

Kinds:
  loc         count non-blank, non-comment-only lines across --paths (recursive)
  file_size   byte size of --path (e.g. a built bundle)
  json_number extract a number from --file at --selector ($.a.b[0].c)
  command     run --command; parse a number from its stdout via --pattern (regex,
              first capture group) or the whole trimmed stdout

Usage examples:
  measure_metric.py --kind loc --paths src lib --ext .ts .tsx --out r.json
  measure_metric.py --kind file_size --path dist/main.js --out r.json
  measure_metric.py --kind json_number --file coverage-summary.json \
      --selector '$.total.lines.pct' --out r.json
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys

_SELECTOR_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def resolve_selector(obj, selector):
    if selector in (None, "", "$"):
        return obj
    path = selector[2:] if selector.startswith("$.") else selector.lstrip("$")
    cur = obj
    for key, idx in _SELECTOR_TOKEN.findall(path):
        cur = cur[int(idx)] if idx != "" else cur[key]
    return cur


def iter_files(paths, exts):
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                # skip common vendored / VCS dirs
                if any(seg in root for seg in (os.sep + "node_modules", os.sep + ".git")):
                    continue
                for f in files:
                    if not exts or os.path.splitext(f)[1] in exts:
                        yield os.path.join(root, f)


def measure_loc(paths, exts):
    total = 0
    seen = 0
    for fpath in iter_files(paths, exts):
        seen += 1
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    s = line.strip()
                    if s and not s.startswith(("//", "#", "*", "/*")):
                        total += 1
        except OSError:
            continue
    # No files matched almost always means a wrong path — reporting 0 would be a
    # false "reduced to zero". Treat it as an error, not a passing measurement.
    if seen == 0:
        raise ValueError(f"no files matched under {paths} (ext={exts})")
    return total


def measure_command(command, pattern):
    argv = ["/bin/sh", "-c", command] if os.name != "nt" else [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    # A failed measurement command must not yield a passing number.
    if proc.returncode != 0:
        raise RuntimeError(f"measurement command exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    out = proc.stdout.strip()
    if pattern:
        m = re.search(pattern, out)
        if not m:
            raise ValueError(f"pattern {pattern!r} did not match command stdout")
        out = m.group(1) if m.groups() else m.group(0)
    return float(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure a single metric value.")
    parser.add_argument("--kind", required=True, choices=["loc", "file_size", "json_number", "command"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--paths", nargs="*", default=[])
    parser.add_argument("--ext", nargs="*", default=[], dest="exts")
    parser.add_argument("--path")
    parser.add_argument("--file")
    parser.add_argument("--selector")
    parser.add_argument("--command")
    parser.add_argument("--pattern")
    args = parser.parse_args(argv)

    unit = ""
    try:
        if args.kind == "loc":
            value = measure_loc(args.paths, args.exts)
            unit = "lines"
        elif args.kind == "file_size":
            value = os.path.getsize(args.path)
            unit = "bytes"
        elif args.kind == "json_number":
            with open(args.file, encoding="utf-8") as fh:
                value = float(resolve_selector(json.load(fh), args.selector))
        elif args.kind == "command":
            value = measure_command(args.command, args.pattern)
        else:  # pragma: no cover - argparse guards
            raise SystemExit(2)
        # json.load accepts NaN/Infinity, and a non-finite value compares False
        # against every predicate and every regression guard — a silent pass
        # dressed as a measurement. It is an error, not a number.
        if not math.isfinite(value):
            raise ValueError(f"measured value is not finite: {value}")
    except Exception as exc:  # noqa: BLE001 — any failure is an error, never a passing value
        # Strict: write an error result with NO value so the loop engine's
        # clean-signal rule turns it into an ERROR verdict, never a green.
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"status": "error", "kind": args.kind, "error": str(exc)}, fh, indent=2)
            fh.write("\n")
        print(f"measure_metric: error: {exc}", file=sys.stderr)
        return 3

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"value": value, "kind": args.kind, "unit": unit}, fh, indent=2)
        fh.write("\n")
    print(f"measure_metric: {args.kind}={value}{(' ' + unit) if unit else ''}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
