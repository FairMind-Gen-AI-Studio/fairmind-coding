#!/usr/bin/env python3
"""
harness_audit.py — audit a repo against a criteria catalog and emit a
deterministic pass/fail assessment plus a per-pillar level summary. Standard
library only; portable across customer OSes.

It implements a fixed contract: the catalog format, the three primitives, the
assessment.jsonl / summary.json shapes, the >=80%-consecutive-tier ladder rule
and the determinism requirements.

An optional `dimensions` block on the catalog (five fixed Loop Readiness
dimensions — see FIXED_DIMENSION_IDS below), computed either by folding
already-evaluated criterion verdicts or, for `test-determinism`, by probing a
test command `k` times and comparing exit codes. `dimensions` is additive and
optional — a catalog without it is audited on its criteria alone.

Every criterion carries a mandatory `title`/`remediation` (a hard
catalog error if either is missing/empty — validated in `validate_catalog`
alongside the existing structural rules), and each `assessment.jsonl` record
gains `title` (copied from the catalog) and `expected` (the concrete target
the primitive evaluated, a deterministic string derived from `params` —
`_expected_string`). `summary.json` does not carry either.

Usage:
  harness_audit.py --repo <path> [--catalog <path>] [--out <dir>]
                    [--test-command <shell command>] [--probe-k <int>]

File discovery is `git ls-files` run inside --repo — only tracked paths count.
Exit code 0 only on a clean run. Any error (unreadable/invalid catalog, an
unknown primitive, a --repo that is not a git work tree) exits non-zero and
writes nothing partial that could read as a pass: the catalog is fully
validated and every criterion evaluated in memory before either output file
is written, so a hard error never leaves a stale/half-written summary.json.
"""

import argparse
import json
import os
import re
import subprocess
import sys

# PCF-28: one writer for the consumer-repo ignore entry, asked by every site
# that can create `.fairmind/`. A stdlib-only LEAF module — importing it
# costs nothing else — reached the way `_loop_ledger` and `ambient_digest`
# already make their own dir importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _fm_ignore import makedirs_ignored  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG_NAME = "harness_audit_criteria.json"

ALLOWED_PRIMITIVES = {"file-exists", "content-regex", "manifest-token"}
REQUIRED_PARAMS = {
    "file-exists": ("path",),
    "content-regex": ("path", "regex"),
    "manifest-token": ("manifest", "token"),
}

PASS_RATE_THRESHOLD = 0.8

# ---------------------------------------------------------------------------
# T6 Loop Readiness dimensions — fixed set, fixed order (contract PART 1).
# `dimensions` on the catalog is OPTIONAL (Amendment 1); when present it must
# declare exactly these five ids. Output is always emitted in this canonical
# order regardless of the order the catalog happens to list them in.
# ---------------------------------------------------------------------------

FIXED_DIMENSION_IDS = (
    "oracle-coverage", "test-determinism", "signal-quality", "ci-gates", "traceability",
)
FIXED_DIMENSION_ID_SET = set(FIXED_DIMENSION_IDS)
ALLOWED_DIMENSION_KINDS = {"fold", "probe"}
ALLOWED_PROBES = {"test-command-k-run"}
PROBE_TIMEOUT_S = 60
DEFAULT_PROBE_K = 3


class CatalogError(Exception):
    """Raised for any structural problem with the criteria catalog, including
    an unknown `primitive` (AC2). Always a hard error — never a partial run."""


# ---------------------------------------------------------------------------
# Glob matching: fnmatch-like over the full tracked path, where `**` matches
# across directory separators and a bare `*` stays within one path segment.
# Python's stdlib fnmatch does not have this two-tier semantic (its `*`
# already crosses `/`), so we translate the pattern to a regex ourselves.
#
# The syntax also supports brace alternation, `{a,b}` -> `(?:a|b)`, because a
# criterion is a statement about a FACT ("the repo has CI workflows"), not
# about a spelling. Without it, one glob can only name one spelling of a path,
# and every ecosystem that accepts several — GitHub reads both
# `.github/workflows/*.yml` and `*.yaml` — turns a cosmetic choice into an
# unconditional fail for every criterion pinned to the other extension, which
# then scores its whole dimension "absent". Alternatives are themselves globs
# (translated recursively), so `*.{yml,yaml}` and `{src,lib}/**/*.py` both
# work; an unmatched `{` is a literal brace.
# ---------------------------------------------------------------------------

def _translate_glob(pattern):
    """Translate a glob into a regex BODY (no anchors) — recursive, because a
    brace alternative is itself a glob."""
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            star_count = j - i
            if star_count >= 2:
                # `**` (or more) crosses directory separators. If immediately
                # followed by '/', fold that separator into the group so
                # "**/x" also matches "x" at the root (zero segments).
                if j < n and pattern[j] == "/":
                    out.append("(?:.*/)?")
                    j += 1
                else:
                    out.append(".*")
            else:
                out.append("[^/]*")
            i = j
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "{":
            close = pattern.find("}", i + 1)
            if close == -1:
                # No closing brace: a literal `{`, never a silent half-parse
                # that would quietly match nothing.
                out.append(re.escape(c))
                i += 1
                continue
            alternatives = pattern[i + 1:close].split(",")
            out.append("(?:" + "|".join(_translate_glob(a) for a in alternatives) + ")")
            i = close + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _glob_to_regex(pattern):
    return re.compile("^" + _translate_glob(pattern) + "$")


def _compile_glob(pattern):
    return _glob_to_regex(pattern)


# ---------------------------------------------------------------------------
# Catalog validation — runs fully before any evaluation so an unknown
# primitive (or any other structural defect) is caught before touching disk.
# ---------------------------------------------------------------------------

def validate_catalog(catalog):
    if not isinstance(catalog, dict):
        raise CatalogError("catalog root must be a JSON object")
    if "criteria_version" not in catalog:
        raise CatalogError("catalog missing 'criteria_version'")
    pillars = catalog.get("pillars")
    if not isinstance(pillars, list) or not pillars:
        raise CatalogError("catalog 'pillars' must be a non-empty list")

    seen_pillar_ids = set()
    seen_criterion_ids = set()
    for p_idx, pillar in enumerate(pillars):
        if not isinstance(pillar, dict):
            raise CatalogError(f"pillars[{p_idx}] must be a JSON object")
        pid = pillar.get("id")
        if not isinstance(pid, str) or not pid:
            raise CatalogError(f"pillars[{p_idx}] missing a non-empty string 'id'")
        if pid in seen_pillar_ids:
            raise CatalogError(f"duplicate pillar id {pid!r}")
        seen_pillar_ids.add(pid)
        if not isinstance(pillar.get("name"), str) or not pillar.get("name"):
            raise CatalogError(f"pillar {pid!r} missing a non-empty string 'name'")

        criteria = pillar.get("criteria")
        if not isinstance(criteria, list):
            raise CatalogError(f"pillar {pid!r}: 'criteria' must be a list")

        for c_idx, crit in enumerate(criteria):
            if not isinstance(crit, dict):
                raise CatalogError(f"pillar {pid!r} criteria[{c_idx}] must be a JSON object")
            cid = crit.get("id")
            if not isinstance(cid, str) or not cid:
                raise CatalogError(f"pillar {pid!r} criteria[{c_idx}] missing a non-empty string 'id'")
            if cid in seen_criterion_ids:
                raise CatalogError(f"duplicate criterion id {cid!r}")
            seen_criterion_ids.add(cid)

            level = crit.get("level")
            if not isinstance(level, int) or isinstance(level, bool) or level < 1:
                raise CatalogError(f"criterion {cid!r}: 'level' must be an int >= 1")

            primitive = crit.get("primitive")
            if primitive not in ALLOWED_PRIMITIVES:
                # AC2: unknown primitive is a hard error, caught here — before
                # any file is discovered or written.
                raise CatalogError(f"criterion {cid!r}: unknown primitive {primitive!r}")

            params = crit.get("params")
            if not isinstance(params, dict):
                raise CatalogError(f"criterion {cid!r}: 'params' must be a JSON object")
            for req in REQUIRED_PARAMS[primitive]:
                if req not in params:
                    raise CatalogError(f"criterion {cid!r}: params missing {req!r}")

            # T15: title/remediation are mandatory, hard-error like every other
            # structural rule above — never a silent pass. Checked here, before
            # any file is discovered or written, same discipline as the rest of
            # validate_catalog.
            title = crit.get("title")
            if not isinstance(title, str) or not title.strip():
                raise CatalogError(f"criterion {cid!r}: missing a non-empty string 'title'")
            remediation = crit.get("remediation")
            if not isinstance(remediation, str) or not remediation.strip():
                raise CatalogError(f"criterion {cid!r}: missing a non-empty string 'remediation'")

    # T6 (Amendment 1): `dimensions` is OPTIONAL. Absence is a legitimate,
    # explicit choice (every T4-era catalog, third-party catalogs) and exits
    # 0 with nothing computed. Presence is validated in full below — a
    # half-declared block is a typo, never a silent partial pass. We check
    # membership with `in` (not `.get()`) so an explicit `"dimensions": null`
    # is treated as *present* (and thus fails "must be a list"), not as
    # absent.
    if "dimensions" in catalog:
        _validate_dimensions_block(catalog["dimensions"], seen_criterion_ids)


def _validate_dimensions_block(dimensions, known_criterion_ids):
    """Validate a PRESENT `dimensions` block against the T6 contract's fixed
    set of five ids/order. `known_criterion_ids` is the full set of criterion
    ids collected across all pillars, used to catch dangling fold references."""
    if not isinstance(dimensions, list) or len(dimensions) != 5:
        raise CatalogError(
            "catalog 'dimensions', when present, must be a list of exactly 5 entries"
        )

    seen_dimension_ids = set()
    for d_idx, dim in enumerate(dimensions):
        if not isinstance(dim, dict):
            raise CatalogError(f"dimensions[{d_idx}] must be a JSON object")

        did = dim.get("id")
        if not isinstance(did, str) or not did:
            raise CatalogError(f"dimensions[{d_idx}] missing a non-empty string 'id'")
        if did not in FIXED_DIMENSION_ID_SET:
            raise CatalogError(
                f"dimension {did!r}: not one of the fixed five dimension ids "
                f"{sorted(FIXED_DIMENSION_ID_SET)}"
            )
        if did in seen_dimension_ids:
            raise CatalogError(f"duplicate dimension id {did!r}")
        seen_dimension_ids.add(did)

        if not isinstance(dim.get("name"), str) or not dim.get("name"):
            raise CatalogError(f"dimension {did!r} missing a non-empty string 'name'")

        kind = dim.get("kind")
        if kind not in ALLOWED_DIMENSION_KINDS:
            raise CatalogError(f"dimension {did!r}: unknown kind {kind!r}")

        if kind == "fold":
            criteria = dim.get("criteria")
            if not isinstance(criteria, list) or not criteria:
                raise CatalogError(
                    f"dimension {did!r}: 'criteria' must be a non-empty list for a fold dimension"
                )
            for cid in criteria:
                if cid not in known_criterion_ids:
                    raise CatalogError(
                        f"dimension {did!r}: 'criteria' references unknown criterion id {cid!r}"
                    )
        else:  # kind == "probe"
            probe = dim.get("probe")
            if probe not in ALLOWED_PROBES:
                raise CatalogError(f"dimension {did!r}: unknown probe {probe!r}")

    # `len(dimensions) == 5` plus "no duplicates, all in the fixed set" (both
    # enforced above) together already imply seen_dimension_ids == the fixed
    # set — this is a defensive belt-and-braces check, not reachable dead code
    # under normal validation paths, but keeps the invariant explicit.
    if seen_dimension_ids != FIXED_DIMENSION_ID_SET:
        raise CatalogError(
            f"dimensions must declare exactly the fixed five ids "
            f"{sorted(FIXED_DIMENSION_ID_SET)}, got {sorted(seen_dimension_ids)}"
        )


# ---------------------------------------------------------------------------
# Primitive evaluation
# ---------------------------------------------------------------------------

def _read_text_or_none(full_path):
    """Read a file as UTF-8 text; return None (never raise) if it can't be
    read or decoded — the contract requires unreadable/binary files to be
    skipped, not to crash the run."""
    try:
        with open(full_path, "rb") as fh:
            data = fh.read()
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _eval_file_exists(params, repo, tracked_paths, tracked_set):
    matcher = _compile_glob(params["path"])
    count = sum(1 for p in tracked_paths if matcher.match(p))
    verdict = "pass" if count > 0 else "fail"
    return verdict, f"matched {count} path(s)"


def _eval_content_regex(params, repo, tracked_paths, tracked_set):
    matcher = _compile_glob(params["path"])
    try:
        line_re = re.compile(params["regex"])
    except re.error as e:
        raise CatalogError(f"invalid regex {params['regex']!r}: {e}")

    candidates = sorted(p for p in tracked_paths if matcher.match(p))
    matched_files = 0
    for rel in candidates:
        text = _read_text_or_none(os.path.join(repo, rel))
        if text is None:
            continue  # unreadable/binary — skipped, never crashes the run
        for line in text.splitlines():
            if line_re.search(line):
                matched_files += 1
                break
    verdict = "pass" if matched_files > 0 else "fail"
    return verdict, f"matched {matched_files} file(s)"


def _eval_manifest_token(params, repo, tracked_paths, tracked_set):
    manifest = params["manifest"]
    token = params["token"]
    if manifest not in tracked_set:
        return "fail", "manifest not tracked"
    text = _read_text_or_none(os.path.join(repo, manifest))
    if text is None:
        return "fail", "manifest unreadable"
    if token in text:
        return "pass", "token found in manifest"
    return "fail", "token not found in manifest"


_EVALUATORS = {
    "file-exists": _eval_file_exists,
    "content-regex": _eval_content_regex,
    "manifest-token": _eval_manifest_token,
}


def evaluate_criterion(primitive, params, repo, tracked_paths, tracked_set):
    return _EVALUATORS[primitive](params, repo, tracked_paths, tracked_set)


def _expected_string(primitive, params):
    """T15: the concrete target a primitive evaluated, as a deterministic
    string — `"<key>=<value> ..."` over REQUIRED_PARAMS[primitive], in that
    declared order, values inserted raw (no quoting/escaping). Pinned exactly
    by the contract; `detail` stays unchanged (the verdict's evidence), this
    is what it always lacked (the target)."""
    return " ".join(f"{k}={params[k]}" for k in REQUIRED_PARAMS[primitive])


# ---------------------------------------------------------------------------
# The >=80%-consecutive-tier ladder rule
# ---------------------------------------------------------------------------

def compute_ladder_level(tiers):
    """`tiers`: {level:int -> (total, passed)} for criteria actually defined
    in the pillar. Walk tiers 1..max consecutively; a tier with zero defined
    criteria is vacuously satisfied and does not stop the ladder."""
    if not tiers:
        return 0
    max_level = max(tiers)
    achieved = 0
    for t in range(1, max_level + 1):
        if t not in tiers:
            achieved = t
            continue
        total, passed = tiers[t]
        if total == 0:
            achieved = t
            continue
        if (passed / total) >= PASS_RATE_THRESHOLD:
            achieved = t
        else:
            break
    return achieved


# ---------------------------------------------------------------------------
# Full catalog evaluation -> (assessment records, pillar summaries, totals)
# ---------------------------------------------------------------------------

def evaluate_catalog(catalog, repo, tracked_paths, tracked_set):
    records = []
    pillar_summaries = []
    total_criteria = 0
    total_passed = 0

    for pillar in catalog["pillars"]:
        pid = pillar["id"]
        pname = pillar["name"]
        tiers = {}  # level -> [total, passed]
        pillar_total = 0
        pillar_passed = 0

        for crit in pillar["criteria"]:
            cid = crit["id"]
            level = crit["level"]
            primitive = crit["primitive"]
            params = crit["params"]

            verdict, detail = evaluate_criterion(primitive, params, repo, tracked_paths, tracked_set)

            records.append({
                "criterion_id": cid,
                "pillar_id": pid,
                "level": level,
                "primitive": primitive,
                "verdict": verdict,
                "detail": detail,
                "title": crit["title"],
                "expected": _expected_string(primitive, params),
            })

            pillar_total += 1
            if verdict == "pass":
                pillar_passed += 1

            tier = tiers.setdefault(level, [0, 0])
            tier[0] += 1
            if verdict == "pass":
                tier[1] += 1

        achieved_level = compute_ladder_level(tiers)
        levels_out = {}
        for t in sorted(tiers):
            levels_out[str(t)] = {"total": tiers[t][0], "passed": tiers[t][1]}

        pillar_summaries.append({
            "id": pid,
            "name": pname,
            "level": achieved_level,
            "criteria_total": pillar_total,
            "criteria_passed": pillar_passed,
            "levels": levels_out,
        })

        total_criteria += pillar_total
        total_passed += pillar_passed

    totals = {"criteria": total_criteria, "passed": total_passed}
    return records, pillar_summaries, totals


# ---------------------------------------------------------------------------
# T6 — Loop Readiness dimensions: scoring, status, and the determinism probe
# ---------------------------------------------------------------------------

def _dimension_status(score):
    """score -> status per the contract's fixed table. `score is None` means
    not computable (probe never ran); a fold dimension's score is never null
    because validation guarantees a non-empty `criteria` list."""
    if score is None:
        return "not-probed"
    if score >= PASS_RATE_THRESHOLD:
        return "clean"
    if score > 0.0:
        return "weak"
    return "absent"


def _resolve_test_command(cli_value, repo, tracked_set):
    """Strict precedence: --test-command (non-empty) > package.json
    scripts.test (verbatim, non-empty, tracked, valid JSON) > None (not
    probed). A blank --test-command is treated as "not given", falling
    through to the package.json check, same as an absent flag."""
    if cli_value:
        return cli_value

    if "package.json" not in tracked_set:
        return None
    text = _read_text_or_none(os.path.join(repo, "package.json"))
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return None
    test_cmd = scripts.get("test")
    if isinstance(test_cmd, str) and test_cmd:
        return test_cmd
    return None


def _shell_argv(command):
    """Build the argv to run `command` through the platform shell explicitly
    (POSIX `/bin/sh -c` / Windows `cmd /c`) rather than passing `shell=True`
    to subprocess.run — same convention as `run_gate_checks.py:build_argv`
    for the identical need (a config-supplied command string: here the
    probed test command, there a check's `exec.command`; both are trusted
    in-repo config, not untrusted external input)."""
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
    return ["/bin/sh", "-c", command]


def _probe_test_determinism(test_command, repo, probe_k):
    """Run `test_command` `probe_k` times via the shell (cwd=repo), comparing
    ONLY the exit code across runs (stdout is deliberately not compared —
    contract: 'timings/paths make it noisy'). Score is binary: 1.0 iff every
    run's exit code agrees, 0.0 otherwise. A command that consistently fails
    is still deterministic (score 1.0) — determinism != passing. A timed-out
    run (60s) is recorded as exit code -1. Returns (score, detail); score is
    None (not-probed) when there is no command to run or it cannot be
    launched at all."""
    if not test_command:
        return None, "no test command configured (no --test-command, no package.json scripts.test)"

    exit_codes = []
    for _ in range(probe_k):
        try:
            proc = subprocess.run(
                _shell_argv(test_command), cwd=repo,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT_S,
            )
            exit_codes.append(proc.returncode)
        except subprocess.TimeoutExpired:
            exit_codes.append(-1)
        except OSError:
            # The command could not be executed at all (contract: "treat as
            # not-probed") — e.g. an empty or otherwise unlaunchable command.
            return None, "test command could not be executed"

    distinct = sorted(set(exit_codes))
    if len(distinct) == 1:
        return 1.0, f"{probe_k}/{probe_k} runs agreed on exit code {distinct[0]}"
    return 0.0, f"{probe_k} runs disagreed on exit code (observed: {', '.join(str(c) for c in distinct)})"


def evaluate_dimensions(dimensions_decl, records, test_command, repo, probe_k):
    """dimensions_decl: the catalog's (already-validated) `dimensions` list.
    records: the criterion verdicts just computed by evaluate_catalog — a
    fold dimension's score is passed/total over its `criteria` ids read from
    these same verdicts (never re-evaluated). Returns the five dimension
    objects in FIXED_DIMENSION_IDS order, regardless of the order the
    catalog happened to declare them in."""
    verdict_by_criterion_id = {r["criterion_id"]: r["verdict"] for r in records}
    dimension_by_id = {d["id"]: d for d in dimensions_decl}

    out = []
    for did in FIXED_DIMENSION_IDS:
        dim = dimension_by_id[did]
        kind = dim["kind"]
        if kind == "fold":
            criteria_ids = dim["criteria"]
            total = len(criteria_ids)
            passed = sum(
                1 for cid in criteria_ids if verdict_by_criterion_id.get(cid) == "pass"
            )
            score = passed / total
            detail = f"{passed}/{total} criteria passed"
        else:  # kind == "probe" (only "test-command-k-run" is a valid probe value)
            score, detail = _probe_test_determinism(test_command, repo, probe_k)

        out.append({
            "id": dim["id"],
            "name": dim["name"],
            "kind": kind,
            "score": score,
            "status": _dimension_status(score),
            "detail": detail,
        })
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _is_git_work_tree(repo):
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True,
        )
    except (OSError, FileNotFoundError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_ls_files(repo):
    proc = subprocess.run(
        ["git", "-C", repo, "ls-files"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise CatalogError(f"git ls-files failed in {repo!r}: {proc.stderr.strip()}")
    return sorted(p for p in proc.stdout.splitlines() if p)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit a repo against a criteria catalog.")
    parser.add_argument("--repo", required=True, help="path to the git repo to audit")
    parser.add_argument("--catalog", default=None,
                         help="criteria catalog JSON (default: harness_audit_criteria.json next to this script)")
    parser.add_argument("--out", default=None,
                         help="output directory (default: <repo>/.fairmind/audit/)")
    parser.add_argument("--test-command", default=None,
                         help="shell command probed for the test-determinism dimension (T6); "
                              "falls back to a tracked package.json's scripts.test when omitted")
    parser.add_argument("--probe-k", type=int, default=DEFAULT_PROBE_K,
                         help=f"number of times to run the probed test command (default {DEFAULT_PROBE_K})")
    args = parser.parse_args(argv)

    repo = args.repo
    catalog_path = args.catalog or os.path.join(SCRIPT_DIR, DEFAULT_CATALOG_NAME)
    out_dir = args.out or os.path.join(repo, ".fairmind", "audit")

    if not _is_git_work_tree(repo):
        print(f"error: --repo {repo!r} is not a git work tree", file=sys.stderr)
        return 1

    try:
        with open(catalog_path, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read catalog {catalog_path!r}: {e}", file=sys.stderr)
        return 1

    try:
        validate_catalog(catalog)
    except CatalogError as e:
        print(f"error: invalid catalog: {e}", file=sys.stderr)
        return 1

    try:
        tracked_paths = _git_ls_files(repo)
    except CatalogError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    tracked_set = set(tracked_paths)

    try:
        records, pillar_summaries, totals = evaluate_catalog(catalog, repo, tracked_paths, tracked_set)
    except CatalogError as e:
        print(f"error: evaluation failed: {e}", file=sys.stderr)
        return 1

    # T6: `dimensions` is OPTIONAL on the catalog (Amendment 1). Presence is
    # checked with `in` (not `.get()`) for the same reason as validation —
    # consistent treatment of an explicit `null` as "present but malformed"
    # would have already been rejected by validate_catalog above, so by this
    # point "dimensions" in catalog implies a well-formed 5-entry list.
    dimensions_out = None
    if "dimensions" in catalog:
        test_command = _resolve_test_command(args.test_command, repo, tracked_set)
        dimensions_out = evaluate_dimensions(
            catalog["dimensions"], records, test_command, repo, args.probe_k
        )

    # Build both output contents fully in memory before writing anything —
    # a hard error above this point never leaves a stale/partial output file.
    assessment_lines = [json.dumps(r, separators=(",", ":")) for r in records]
    assessment_content = "".join(line + "\n" for line in assessment_lines)

    summary = {
        "criteria_version": catalog["criteria_version"],
        "source": "git ls-files",
        "pillars": pillar_summaries,
    }
    if dimensions_out is not None:
        # T4-era output stays byte-identical when the catalog declares no
        # dimensions: the key is OMITTED entirely, never `[]`/`null`.
        summary["dimensions"] = dimensions_out
    summary["totals"] = totals
    summary_content = json.dumps(summary, indent=2, sort_keys=False) + "\n"

    makedirs_ignored(out_dir)
    with open(os.path.join(out_dir, "assessment.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(assessment_content)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        fh.write(summary_content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
