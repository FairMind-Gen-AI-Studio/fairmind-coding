#!/usr/bin/env python3
"""
loop_import.py — turn an external ticket into a `TaskDraft`, then compile a
classified TaskDraft into a gap report or a loop-mode contract.

Six modes, stdlib-only, invoked as a CLI (never imported):

  --adapter gh --input <path>       read a `gh issue view --json` payload
                                     (JSON), write a TaskDraft to stdout.
  --adapter pasted --input <path>   read a raw ticket string (arbitrary
                                     text), write a TaskDraft to stdout.
  --adapter clickup --input <path>  read a ClickUp `GET /v2/task/{id}`
                                     public REST task payload (JSON), write
                                     a TaskDraft to stdout.
  --validate-draft --input <path>   read a TaskDraft JSON, exit 0 if valid,
                                     else exit non-zero with a stderr reason
                                     that names the offending field.
  --gap-report --draft <p> --classification <p>
                                     compile a TaskDraft + a classification
                                     map (see `skills/task-compilation/
                                     SKILL.md`) into a gap report, written to
                                     stdout.
  --emit --draft <p> --classification <p> --task-ref <ref> --state <out>
         [--contracts-dir <dir>]
                                     compile the same inputs into a
                                     `loop-state.json` (status "specified")
                                     plus a reusable `<contracts-dir>/<ref>
                                     .json` copy of its contract and a
                                     persisted `<contracts-dir>/<ref>.gap.json`
                                     gap report (byte-identical to what
                                     `--gap-report` prints to stdout), then
                                     run admission on the emitted checks.

`--input` is optional for the first four modes; omitting it reads from
stdin. Input is always read as raw bytes and decoded as UTF-8 (never through
Python's text mode) so that a body/ticket string round-trips byte-for-byte —
no newline translation, no stripping. `--gap-report`/`--emit` read `--draft`/
`--classification` the same way (see `_read_json_file`).

This file is the **deterministic** half of task compilation — same inputs
produce byte-identical outputs, no judgment. The judgment half (turning a
TaskDraft's prose acceptance criteria into a classification map) is
`skills/task-compilation/SKILL.md`, the classifier the agent follows; this
script only ever consumes the classification map it produces, never
performs the classification itself.

See `skills/task-compilation/references/taskdraft.md` for the full TaskDraft
schema and the adapter contract: fetch -> normalize, downstream code depends
on this schema, never on a specific source's shape. See
`skills/task-compilation/references/gap-report.md` for the classification-map
and gap-report schemas, and `skills/fairmind-gate/references/check-contract.md`
for the check descriptor shape a classification decision's `descriptor`
follows.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

# PCF-28: one writer for the consumer-repo ignore entry, asked by every site
# that can create `.fairmind/`. A stdlib-only LEAF module — importing it
# costs nothing else — reached the way `_loop_ledger` and `ambient_digest`
# already make their own dir importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _fm_ignore import makedirs_ignored  # noqa: E402

# Fields promoted onto the top-level TaskDraft by the gh adapter (source.ref
# derives from "number", source.url from "url", plus "title"/"body"
# themselves). Every other field in the gh payload passes through into
# `meta` unchanged, so a field the gh CLI adds later is carried automatically
# rather than silently dropped.
_GH_PROMOTED_FIELDS = ("number", "url", "title", "body")

# Fields promoted onto the top-level TaskDraft by the clickup adapter. All
# three description variants ("description", "markdown_description",
# "text_content") are promoted even though only one is ever chosen for
# `body`, so `meta` never carries a stale/duplicate copy of the body under
# another key.
_CLICKUP_PROMOTED_FIELDS = ("id", "url", "name", "description", "markdown_description", "text_content")

_VALID_SOURCE_KINDS = ("gh-issue", "pasted")


def read_input(input_path):
    """Read raw bytes from `input_path`, or stdin when it is None, and
    decode as UTF-8. Binary mode throughout so no universal-newline
    translation or stripping ever touches the content — callers that need a
    byte-for-byte round-trip (the pasted adapter's body) depend on this."""
    if input_path:
        with open(input_path, "rb") as fh:
            raw = fh.read()
    else:
        raw = sys.stdin.buffer.read()
    return raw.decode("utf-8")


def gh_adapter(raw_text):
    """Map a `gh issue view --json` payload onto a TaskDraft. `body` is
    carried unaltered from the payload; `meta` is a passthrough of every
    payload field not promoted onto the top level (labels, assignees,
    author, state, createdAt, updatedAt, milestone, ...)."""
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise KeyError(
            "issue payload must be a JSON object, got " + type(payload).__name__
        )
    if payload.get("number") in (None, ""):
        raise KeyError("number")
    meta = {k: v for k, v in payload.items() if k not in _GH_PROMOTED_FIELDS}
    return {
        "source": {
            "kind": "gh-issue",
            "ref": str(payload["number"]),
            "url": payload.get("url"),
        },
        "title": payload["title"],
        "body": payload["body"],
        "acceptance_criteria": [],
        "meta": meta,
    }


def clickup_adapter(raw_text):
    """Map a ClickUp `GET /v2/task/{id}` REST payload onto a TaskDraft. `body`
    prefers `markdown_description` when the key is present and a string
    (checked by presence + type, never truthiness, so a present-but-empty
    markdown string stays `""` rather than falling back); otherwise it falls
    back to `description`. `meta` is a passthrough of every payload field not
    promoted onto the top level (status, assignees, custom fields, parent,
    ...)."""
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise KeyError(
            "task payload must be a JSON object, got " + type(payload).__name__
        )
    if payload.get("id") in (None, ""):
        raise KeyError("id")
    if isinstance(payload.get("markdown_description"), str):
        body = payload["markdown_description"]
    else:
        body = payload["description"]
    meta = {k: v for k, v in payload.items() if k not in _CLICKUP_PROMOTED_FIELDS}
    return {
        "source": {
            "kind": "mcp:clickup",
            "ref": str(payload["id"]),
            "url": payload.get("url"),
        },
        "title": payload["name"],
        "body": body,
        "acceptance_criteria": [],
        "meta": meta,
    }


def _derive_title(raw_text):
    """First non-empty (stripped) line of the input, or a fallback when the
    entire input is blank — title must be non-empty for the draft to
    validate."""
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "Untitled"


def pasted_adapter(raw_text):
    """Map a raw pasted ticket string onto a TaskDraft. `body` is the input
    exactly as received (no stripping/normalization); `ref` is a stable
    content hash since a pasted ticket has no external identifier to reuse."""
    ref = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    return {
        "source": {
            "kind": "pasted",
            "ref": ref,
            "url": None,
        },
        "title": _derive_title(raw_text),
        "body": raw_text,
        "acceptance_criteria": [],
        "meta": {},
    }


def _valid_source_kind(kind):
    if not isinstance(kind, str) or kind == "":
        return False
    if kind in _VALID_SOURCE_KINDS:
        return True
    return kind.startswith("mcp:") and len(kind) > len("mcp:")


def validate_draft(draft):
    """Return a list of human-readable error strings (empty == valid).
    Fail-closed: every branch that could be malformed is enumerated and
    named explicitly, never collapsed into a generic "invalid" message."""
    errors = []

    if not isinstance(draft, dict):
        return ["draft must be a JSON object, got " + type(draft).__name__]

    source = draft.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
    elif not _valid_source_kind(source.get("kind")):
        errors.append(
            "source.kind is missing/empty or not one of "
            "'gh-issue' / 'pasted' / 'mcp:<name>' "
            f"(got {source.get('kind')!r})"
        )

    title = draft.get("title")
    if not isinstance(title, str) or title == "":
        errors.append("title must be a non-empty string")

    body = draft.get("body")
    if not isinstance(body, str):
        errors.append("body must be a string")

    ac_list = draft.get("acceptance_criteria")
    if not isinstance(ac_list, list):
        errors.append("acceptance_criteria must be a list")
    else:
        for idx, entry in enumerate(ac_list):
            if not isinstance(entry, dict):
                errors.append(f"acceptance_criteria[{idx}] must be an object")
                continue
            text = entry.get("text")
            if not isinstance(text, str) or text == "":
                errors.append(
                    f"acceptance_criteria[{idx}].text is missing or empty "
                    "(every acceptance_criteria entry needs a non-empty text)"
                )
            if "id" not in entry or entry.get("id") is None:
                errors.append(f"acceptance_criteria[{idx}].id is missing")

    meta = draft.get("meta")
    if meta is not None and not isinstance(meta, dict):
        errors.append("meta must be an object when present")

    return errors


class _LoopImportInputError(Exception):
    """Raised by `_read_json_file` on any read/parse failure. Caught in
    `main()` and reported as a named stderr reason, never a raw traceback —
    the same fail-closed discipline `validate_draft`'s callers already
    follow."""


def _read_json_file(path, what):
    """Read `path` as UTF-8 JSON, same raw-bytes discipline as `read_input`
    (no universal-newline translation). `what` names the artifact in any
    error message (e.g. "TaskDraft", "classification map") so a failure
    points at the offending file, not a generic "invalid input"."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise _LoopImportInputError(f"could not read {what} at {path!r}: {exc}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _LoopImportInputError(f"{what} at {path!r} is not valid UTF-8: {exc}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _LoopImportInputError(f"{what} at {path!r} is not valid JSON: {exc}")


def _atomic_write_json(path, data):
    """Write `data` as JSON to `path` atomically: serialize to a temp file in
    the SAME directory, then os.replace it over the target — a crash
    mid-write leaves either the old file or the new one, never a
    half-written loop-state.json / contract copy (same idiom as
    `loop_open.py`'s `_atomic_write_json` and `run_gate_checks.save_state`)."""
    directory = os.path.dirname(os.path.abspath(path))
    makedirs_ignored(directory)
    fd, tmp = tempfile.mkstemp(prefix=".loop-import.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# The five check types a classification decision's `type` may name when
# `disposition == "checked"` (an `evidence` decision is validated separately,
# below — it is not one more member of this set, since coverage buckets it
# apart from the machine-checked five per CONTRACT-SPEC).
_CHECKED_TYPES = ("functional", "metric", "performance", "static", "custom")

# Mirrors `run_gate_checks.py`'s own fallback defaults (`budget.get(
# "max_iterations", 8)`, `budget.get("max_consecutive_failures", 3)`) and
# `loop-state.md`'s worked `timeout_min` example — a human confirms the real
# values in Phase 0 (`fairmind-loop.md` step 6); this skeleton exists so
# `--emit` never writes a loop-state.json with no budget at all.
_DEFAULT_BUDGET = {
    "max_iterations": 8,
    "max_consecutive_failures": 3,
    "timeout_min": 120,
    "spent": {"iterations": 0, "started_at": None},
}

DEFAULT_CONTRACTS_DIR = os.path.join(".fairmind", "contracts")


def validate_classification(classification, draft):
    """Return a list of human-readable error strings (empty == valid).
    Shared by `--gap-report` and `--emit` so both modes apply the exact same
    rules — see `skills/task-compilation/references/gap-report.md`.

    Cross-checks the classification map's decision id-set against the
    draft's own acceptance_criteria id-set: missing, extra, or duplicated
    ids are all rejected. This IS the no-silent-drops guarantee, enforced
    mechanically rather than trusted from the classifier's judgment. Then
    validates every decision's shape against its `disposition`."""
    errors = []

    if not isinstance(classification, dict):
        return ["classification must be a JSON object, got " + type(classification).__name__]

    decisions = classification.get("decisions")
    if not isinstance(decisions, list):
        return ["classification.decisions must be a list"]

    draft_id_set = {ac.get("id") for ac in draft.get("acceptance_criteria", [])
                     if isinstance(ac, dict)}
    decision_ids = [d.get("id") if isinstance(d, dict) else None for d in decisions]
    decision_id_set = set(decision_ids)

    missing = draft_id_set - decision_id_set
    extra = decision_id_set - draft_id_set
    seen = set()
    dups = set()
    for did in decision_ids:
        if did in seen:
            dups.add(did)
        seen.add(did)

    if missing:
        errors.append(
            "classification.decisions is missing a decision for draft acceptance_criteria "
            "id(s): " + ", ".join(sorted(str(m) for m in missing))
        )
    if extra:
        errors.append(
            "classification.decisions has decision id(s) not present in the draft's "
            "acceptance_criteria: " + ", ".join(sorted(str(e) for e in extra))
        )
    if dups:
        errors.append(
            "classification.decisions has duplicated decision id(s): "
            + ", ".join(sorted(str(d) for d in dups))
        )

    descriptor_ids = []
    for idx, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f"decisions[{idx}] must be an object")
            continue
        did = decision.get("id")
        disposition = decision.get("disposition")
        dtype = decision.get("type")
        descriptor = decision.get("descriptor")
        rewrite = decision.get("rewrite")

        if disposition not in ("checked", "evidence", "unverifiable"):
            errors.append(
                f"decisions[{idx}] (id={did!r}).disposition must be one of "
                f"'checked'/'evidence'/'unverifiable', got {disposition!r}"
            )
            continue

        if disposition == "unverifiable":
            if not isinstance(rewrite, str) or rewrite.strip() == "":
                errors.append(
                    f"decisions[{idx}] (id={did!r}): disposition=='unverifiable' requires a "
                    "non-empty 'rewrite' string"
                )
            if dtype is not None:
                errors.append(
                    f"decisions[{idx}] (id={did!r}): disposition=='unverifiable' requires "
                    f"'type' to be null/absent, got {dtype!r}"
                )
            if descriptor is not None:
                errors.append(
                    f"decisions[{idx}] (id={did!r}): disposition=='unverifiable' must not "
                    "carry a 'descriptor'"
                )
            continue

        # checked / evidence: both require a descriptor, differing only in
        # the type(s) each disposition accepts.
        wanted_types = _CHECKED_TYPES if disposition == "checked" else ("evidence",)
        if not isinstance(descriptor, dict):
            errors.append(
                f"decisions[{idx}] (id={did!r}): disposition=={disposition!r} requires a "
                "'descriptor' object"
            )
        else:
            d_id = descriptor.get("id")
            if not isinstance(d_id, str) or d_id == "":
                errors.append(
                    f"decisions[{idx}] (id={did!r}): descriptor.id must be a non-empty string"
                )
            else:
                descriptor_ids.append(d_id)
        if dtype not in wanted_types:
            errors.append(
                f"decisions[{idx}] (id={did!r}): disposition=={disposition!r} requires 'type' "
                f"to be one of {wanted_types}, got {dtype!r}"
            )

    dup_descriptor_ids = {d for d in descriptor_ids if descriptor_ids.count(d) > 1}
    if dup_descriptor_ids:
        errors.append(
            "classification.decisions has duplicated descriptor id(s): "
            + ", ".join(sorted(dup_descriptor_ids))
        )

    return errors


def build_gap_report(draft, classification):
    """Compile a validated draft + classification map into the gap-report
    shape (`skills/task-compilation/references/gap-report.md`). Caller must
    have already run `validate_draft`/`validate_classification` — this
    function assumes both are clean and does no further validation.

    `criteria` follows the draft's own acceptance_criteria order (not the
    classification map's decision order) and copies `text` verbatim from
    the draft. `disposition` is `"checked:<type>"` for BOTH `checked` and
    `evidence` decisions (e.g. "checked:functional", "checked:evidence") —
    the report's own vocabulary distinguishes coverage kind by `type`, not
    by a second disposition prefix; that "evidence:<id>" distinction is
    `--emit`'s `contract.criteria` shape, a different structure with a
    different job (naming the covering check id, not the coverage kind)."""
    criteria = []
    machine_checked = 0
    evidence = 0
    unverifiable = 0

    decisions_by_id = {d["id"]: d for d in classification["decisions"]}
    for ac in draft["acceptance_criteria"]:
        decision = decisions_by_id[ac["id"]]
        disposition = decision["disposition"]
        if disposition == "unverifiable":
            criteria.append({
                "id": ac["id"],
                "text": ac["text"],
                "disposition": "unverifiable",
                "type": None,
                "rewrite": decision.get("rewrite"),
                "reason": decision.get("reason"),
            })
            unverifiable += 1
            continue

        dtype = decision["type"]
        criteria.append({
            "id": ac["id"],
            "text": ac["text"],
            "disposition": f"checked:{dtype}",
            "type": dtype,
            "rewrite": None,
            "reason": decision.get("reason"),
        })
        if dtype == "evidence":
            evidence += 1
        else:
            machine_checked += 1

    total = len(criteria)
    coverage = (machine_checked / total) if total else 0.0

    return {
        "task_ref": classification.get("task_ref"),
        "coverage": coverage,
        "criteria": criteria,
        "counts": {
            "total": total,
            "machine_checked": machine_checked,
            "evidence": evidence,
            "unverifiable": unverifiable,
        },
    }


def build_loop_state(draft, classification, task_ref):
    """Compile a validated draft + classification map into a fresh
    loop-state.json body (status "specified") — complete-by-construction:
    `contract.criteria` has exactly one entry per draft AC (the id-set is
    the draft's own, so nothing can be dropped), and every `checked`/
    `evidence` entry's disposition names a descriptor id that is, by
    construction, present in `checks[]` (the same decision contributed
    both). Caller must have already run `validate_draft`/
    `validate_classification`.

    `checks[]` carries every `checked`/`evidence` decision's `descriptor`
    verbatim; an `unverifiable` decision contributes no check. Admission
    (invoked by the `--emit` caller, not here) is what turns these
    `admission.status: "pending"` skeletons into `passed`/quarantined."""
    checks = []
    criteria = []

    decisions_by_id = {d["id"]: d for d in classification["decisions"]}
    for ac in draft["acceptance_criteria"]:
        decision = decisions_by_id[ac["id"]]
        disposition = decision["disposition"]
        if disposition == "unverifiable":
            criteria.append({
                "id": ac["id"],
                "text": ac["text"],
                "disposition": "unverifiable",
                "hard": False,
            })
            continue

        descriptor = decision["descriptor"]
        checks.append(descriptor)
        criteria.append({
            "id": ac["id"],
            "text": ac["text"],
            "disposition": f"{disposition}:{descriptor['id']}",
            "hard": True,
        })

    contract = {"criteria": criteria}
    scope = classification.get("scope")
    if scope is not None:
        contract["scope"] = scope

    return {
        "schema": "fairmind-loop/1",
        "target": {"level": "task", "ref": task_ref},
        "status": "specified",
        "runner": {},
        "budget": json.loads(json.dumps(_DEFAULT_BUDGET)),  # fresh copy per call
        "confirmations": 0,
        "checks": checks,
        "quarantine": [],
        "iterations": [],
        "contract": contract,
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Normalize an external ticket into a TaskDraft, validate "
        "a TaskDraft, or compile a classified TaskDraft into a gap report / "
        "loop-mode contract."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--adapter",
        choices=["gh", "pasted", "clickup"],
        help="run the named adapter (gh issue payload, pasted text, or a "
        "ClickUp task REST payload), reading its native ticket format",
    )
    mode.add_argument(
        "--validate-draft",
        action="store_true",
        help="validate a TaskDraft JSON document",
    )
    mode.add_argument(
        "--gap-report",
        action="store_true",
        help="compile --draft + --classification into a gap report (stdout)",
    )
    mode.add_argument(
        "--emit",
        action="store_true",
        help="compile --draft + --classification into loop-state.json + a "
        "reusable contract copy, then run admission",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="(--adapter / --validate-draft) path to read input from; omit "
        "to read from stdin",
    )
    parser.add_argument(
        "--draft",
        default=None,
        help="(--gap-report / --emit) path to a TaskDraft JSON document",
    )
    parser.add_argument(
        "--classification",
        default=None,
        help="(--gap-report / --emit) path to a classification map JSON document",
    )
    parser.add_argument(
        "--task-ref",
        default=None,
        help="(--emit) the target ref written into loop-state.json and used "
        "as the contract copy's filename",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="(--emit) output path for the emitted loop-state.json",
    )
    parser.add_argument(
        "--contracts-dir",
        default=DEFAULT_CONTRACTS_DIR,
        help=f"(--emit) directory for the reusable contract copy "
        f"(default: {DEFAULT_CONTRACTS_DIR})",
    )
    return parser


def _run_gap_report_or_emit(args):
    """Shared load+validate for `--gap-report`/`--emit`, then dispatch to the
    mode-specific compile. Returns the process exit code."""
    if not args.draft or not args.classification:
        print("--draft and --classification are required", file=sys.stderr)
        return 1

    try:
        draft = _read_json_file(args.draft, "TaskDraft")
        classification = _read_json_file(args.classification, "classification map")
    except _LoopImportInputError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    draft_errors = validate_draft(draft)
    if draft_errors:
        print("invalid TaskDraft:", file=sys.stderr)
        for err in draft_errors:
            print(f"- {err}", file=sys.stderr)
        return 1

    classification_errors = validate_classification(classification, draft)
    if classification_errors:
        print("invalid classification map:", file=sys.stderr)
        for err in classification_errors:
            print(f"- {err}", file=sys.stderr)
        return 1

    if args.gap_report:
        report = build_gap_report(draft, classification)
        print(json.dumps(report))
        return 0

    return _emit(args, draft, classification)


def _emit(args, draft, classification):
    """`--emit`: write loop-state.json + the reusable contracts-dir copy +
    the persisted gap report, then invoke admission as a subprocess. Exits 0
    on MECHANICAL success (all three files written and admission invoked)
    regardless of the admission verdicts it produces — a partially/fully
    quarantined admission is a valid outcome the human resolves later;
    `--emit` never arms the loop."""
    if not args.task_ref or not args.state:
        print("--emit requires --task-ref and --state", file=sys.stderr)
        return 1

    state = build_loop_state(draft, classification, args.task_ref)
    gap_report = build_gap_report(draft, classification)

    try:
        _atomic_write_json(args.state, state)
        contract_copy_path = os.path.join(args.contracts_dir, f"{args.task_ref}.json")
        _atomic_write_json(contract_copy_path, state["contract"])
        # Persisted next to the contract copy so the gap report the human
        # sees at intake time is a durable, re-presentable artifact rather
        # than something only ever printed once to stdout by --gap-report.
        gap_report_path = os.path.join(args.contracts_dir, f"{args.task_ref}.gap.json")
        _atomic_write_json(gap_report_path, gap_report)
    except OSError as exc:
        print(f"--emit: could not write output: {exc}", file=sys.stderr)
        return 1

    admit_check_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admit_check.py")
    try:
        subprocess.run(
            [sys.executable, admit_check_path, "--state", args.state],
            check=False,
        )
    except OSError as exc:
        print(f"--emit: could not invoke admit_check.py: {exc}", file=sys.stderr)
        return 1

    return 0


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.gap_report or args.emit:
        return _run_gap_report_or_emit(args)

    try:
        raw_text = read_input(args.input)
    except OSError as exc:
        print(f"could not read input: {exc}", file=sys.stderr)
        return 1

    if args.validate_draft:
        try:
            draft = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            print(f"invalid TaskDraft: input is not valid JSON: {exc}", file=sys.stderr)
            return 1

        errors = validate_draft(draft)
        if errors:
            print("invalid TaskDraft:", file=sys.stderr)
            for err in errors:
                print(f"- {err}", file=sys.stderr)
            return 1

        return 0

    try:
        if args.adapter == "gh":
            draft = gh_adapter(raw_text)
        elif args.adapter == "clickup":
            draft = clickup_adapter(raw_text)
        else:
            draft = pasted_adapter(raw_text)
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"{args.adapter} adapter failed to parse input: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(draft))
    return 0


if __name__ == "__main__":
    sys.exit(main())
