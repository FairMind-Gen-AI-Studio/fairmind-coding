#!/usr/bin/env python3
"""
admit_check.py — graduated admission: "verify the verifier" before a check is
allowed to contribute to the stop condition.

Mandatory, portable gates (no OS/sandbox dependency):
  - maker_checker : source.authored_by present and != owner (structural
                    maker != checker; the engine also enforces this at runtime)
  - clean_signal  : signal.on_missing is "error"/"fail" (absence never a pass)
                    and a predicate with a known operator is present
  - red_first     : the live probe is the source of truth, always. A recorded
                    source.red_first_proof.red_value is OPTIONAL INPUT: when
                    absent, a live-RED result is admitted and the engine WRITES
                    red_first_proof (red_value + commit) into the descriptor —
                    nobody else owns authoring it up front (the Technical Lead
                    specifies the check before it exists; the QA Engineer owns
                    tests, not gate artifacts). When present, it is still
                    validated: a recorded value that satisfies the predicate is
                    rejected regardless of the live value (anti-tautology,
                    unchanged), and a live-GREEN check is rejected regardless of
                    any recorded proof (unchanged).
  - determinism   : run the check probe_k times now; identical signal each time

Recommended (needs per-check fixtures → protects adoption, not mandatory):
  - sensitivity   : positive control GREEN, negative control RED (only run when
                    admission.controls provides them; otherwise "unverified")

A check that fails a MANDATORY gate is quarantined (surfaced to the human) and
never contributes to the stop condition. Reuses run_gate_checks so the
admission probe evaluates the signal exactly as the gate will.

Exit code: counted over the checks this run actually EVALUATED — a check
skipped as already-admitted (below) was not evaluated and counts for neither
outcome. 0 when at least one evaluated check was admitted (a partial quarantine
still exits 0 — the quarantined ids are printed to stderr and excluded from the
stop condition), and 0 when every considered check was skipped (nothing new to
admit). Non-zero (1) when EVERY evaluated check ended up quarantined, even if
other checks were skipped, or when zero checks were considered at all (an empty
`checks` list, or `--id` matching nothing) — an admission run that evaluates
checks and admits none of them is never a success.

A check whose `admission.status` is already "passed" for its EXACT current
descriptor (a matching `source.admitted_hash`) is SKIPPED on a bare run —
left untouched, not re-probed — rather than re-admitted from scratch. This is
what makes a re-arm (new checks added, admit_check.py run again with no
`--id`) safe: without it, a live re-probe of an already-correct check can
only find it GREEN, which `red_first` reads as "not RED-first" and
quarantines. `--force` re-probes it live anyway, for a deliberate re-admit.

Usage:
  admit_check.py --state <loop-state.json> [--cwd <repo>] [--id <check-id>] [--force]
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_gate_checks as roc  # noqa: E402


def probe_signal(check, cwd, k):
    """Run the check k times, returning (values, error). error is set when a
    run yields no signal (which itself fails admission — a clean signal must be
    present to certify determinism)."""
    values = []
    for _ in range(max(1, k)):
        run = roc.run_command(check, cwd, tier="B")  # admission is Tier-B/portable
        if run["timed_out"]:
            return values, "check timed out during admission probe"
        try:
            values.append(roc.extract_signal(check, run))
        except roc.MissingSignal as exc:
            return values, f"missing signal during admission probe: {exc}"
        except (ValueError, TypeError):
            # Defence in depth, not the primary path: `gate_clean_signal` refuses
            # an unrecognised `value_type` before this runs. What survives to here
            # is a signal that IS present and does not coerce (a `count` check
            # whose stdout matched non-numeric text) — a quarantine reason, never
            # a traceback out of admission.
            #
            # 🔴 THE REASON COMES FROM `_uncoercible_reason`, NOT FROM `{exc}`, and
            # this line is the third instance of the same defect in this repo: the
            # fix for the engine's copy landed and this one, one module over,
            # kept interpolating. Python's ValueError QUOTES the offending input,
            # which here is the check's own stdout, and an admission reason is
            # persisted into `loop-state.json` — which the internal harvest copies
            # whole off the machine. Fix the predicate, not the site.
            return values, roc._uncoercible_reason(check, "signal during admission probe")
    return values, None


def _unevaluated(gates):
    """Mark every EXECUTION-dependent gate as not passed when admission stops
    early. False, never absent: a gate missing from the map reads as "not
    applicable", and "we never ran it" must not be indistinguishable from that."""
    for name in ("determinism", "red_first", "green_at_spec"):
        gates.setdefault(name, False)
    return gates


def gate_clean_signal(check):
    """A clean signal is a PRESENT one, a COMPARABLE one, and — since JC12 — a
    MEASURED one.

    The third condition is the one that was missing. `value_type` was read here
    only to coerce a recorded `red_value`; it was never validated, so a check
    declaring `value_type: "string"` passed all four admission gates and then
    shipped its command's raw stdout as `iterations[].results[].value`. The
    vocabulary was already closed in the reference docs
    (`count`/`number`/`duration_ms`/`bool`) and open only in the code.

    ⚠️ A FAILURE HERE SHORT-CIRCUITS ADMISSION; ORDERING ALONE WAS NOT ENOUGH.
    Both call sites used to record the failure and probe anyway, and the probe
    path reaches a THIRD `roc.extract_signal` (in `gate_sensitivity`) that
    catches only `MissingSignal` — so this guard CRASHED admission on exactly
    the descriptor it exists to refuse. "It runs before the probe" was true of
    the call ORDER and false of the PROTECTION. Validating here needs no probe:
    it reads the descriptor alone."""
    on_missing = check.get("signal", {}).get("on_missing")
    if on_missing not in ("error", "fail"):
        return False, "signal.on_missing must be 'error' or 'fail' (absence must never pass)"
    value_type = roc.declared_value_type(check)
    if value_type not in roc.VALUE_TYPES:
        return False, (f"signal.value_type {value_type!r} is not one of "
                       f"{', '.join(roc.VALUE_TYPES)} — a gate signal is a measurement, "
                       "and an unrecognised type used to carry the check's raw stdout")
    op = check.get("predicate", {}).get("operator")
    if op not in roc._OPERATORS:
        return False, f"predicate.operator {op!r} is not a known comparator"
    return True, "on_missing is strict; value_type is a measurement; predicate operator known"


def _current_commit(cwd):
    """HEAD sha of `cwd`, or None when it cannot be read (not a git tree, git
    missing, etc.). A missing commit is provenance, not an admission failure —
    the RED evidence is the live value; `commit` merely anchors it in time."""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def gate_red_first_live(check, values, cwd):
    """RED-first, verified live — the probe result is the sole source of
    truth. A recorded source.red_first_proof.red_value is OPTIONAL INPUT:

      - absent  : a live-RED result admits and the engine WRITES red_value
                  (the observed live value) + commit provenance into the
                  descriptor (mandatory output — nobody else can author it: the
                  check doesn't exist yet when the Technical Lead specifies it,
                  and the QA Engineer owns tests, not gate artifacts). A
                  live-GREEN result is still rejected (unchanged anti-tautology
                  guard) and nothing is written.
      - present : still validated, never blindly trusted. A recorded value
                  that satisfies the predicate is rejected regardless of the
                  live value (AC3, unchanged) — a fabricated/stale "RED" proof
                  can't launder a check that isn't actually RED. A recorded
                  value that isn't coercible to the signal's value_type is
                  also rejected. Otherwise the live probe still has the final
                  say: a valid recorded RED proof does not admit a check that
                  is live-GREEN right now. A human-recorded proof is never
                  overwritten.
    """
    proof = check.get("source", {}).get("red_first_proof") or {}
    predicate = check.get("predicate", {})
    try:
        live_passes = roc.eval_predicate(values[0], predicate)
    except (ValueError, TypeError) as exc:
        return False, f"cannot evaluate live value against predicate: {exc}"

    if "red_value" in proof:
        vt = roc.declared_value_type(check)
        try:
            recorded_passes = roc.eval_predicate(roc.coerce_value(proof["red_value"], vt), predicate)
        except (ValueError, TypeError) as exc:
            return False, f"cannot evaluate red_value against predicate: {exc}"
        if recorded_passes:
            return False, "recorded red_value satisfies the predicate — not a RED proof"
        if live_passes:
            return False, (f"check is already GREEN on current code (value={values[0]}) — RED-first "
                           "not satisfied (tautological, or the code already passes)")
        return True, f"RED on current code (value={values[0]}); recorded red_value={proof['red_value']}"

    # No recorded proof: absent is legal — the live probe alone decides, and a
    # live-RED result is admitted with the engine recording what it observed.
    if live_passes:
        return False, (f"check is already GREEN on current code (value={values[0]}) — RED-first "
                       "not satisfied (tautological, or the code already passes)")
    check.setdefault("source", {})["red_first_proof"] = {
        "commit": _current_commit(cwd),
        "red_value": values[0],
    }
    return True, f"RED on current code (value={values[0]}); recorded by the engine at admission"


def gate_green_at_spec(check, values):
    """The guard-side replacement for RED-first (T10/AC7/R4.1). A `kind: "guard"`
    check asserts "this existing behaviour still works", so it is GREEN at
    specification time BY CONSTRUCTION — a live-RED probe (what `red_first`
    demands) is structurally impossible for it. This gate inverts the polarity:
    the guard must be live GREEN now. A guard that is ALREADY RED at admission is
    quarantined naming this gate — it can never distinguish "the maker regressed
    it" from "it was broken before anyone touched it" (F6's residue).

    Returns (ok, reason). `source.red_first_proof` is NEVER written here: there is
    no RED to record, so T14's proof-writer must not fire on this path."""
    predicate = check.get("predicate", {})
    try:
        live_passes = roc.eval_predicate(values[0], predicate)
    except (ValueError, TypeError, IndexError) as exc:
        return False, f"cannot evaluate live value against predicate: {exc}"
    if not live_passes:
        return False, (f"guard is live RED on current code (value={values[0]}) — a guard must "
                       "be GREEN at specification (green_at_spec): an already-RED guard cannot "
                       "distinguish a maker regression from a pre-existing break, so one contract "
                       "criterion is unsatisfiable from the start")
    return True, f"guard is live GREEN on current code (value={values[0]})"


# H5-AC3: the reserved exit code a CONTROL uses to signal a harness/control
# error — as opposed to a genuine, clean predicate-failing RED — never a real
# outcome of the check under test. Shared convention with authored controls
# (guard_gate_eval_control.sh's own `CONTROL ERROR ... exit 2` paths already
# use it); documented in check-contract.md alongside `admission.controls`.
CONTROL_ERROR_EXIT_CODE = 2


def _control_crash_reason(returncode):
    """Classify a control's raw exit code as a HARNESS crash (as opposed to a
    genuine, clean predicate-failing RED) and return a human-readable reason,
    or `None` when the exit code is not a recognized crash shape.

    H5-AC3 (adversarial-pass amendment): the original fix here only caught
    the reserved `CONTROL_ERROR_EXIT_CODE` (2). That is too narrow — a
    negative control can also crash INVOLUNTARILY, never even reaching the
    "CONTROL ERROR, exit 2" convention an author writes by hand. Recognized
    crash shapes (any ONE is sufficient to fail closed):

      - the reserved `CONTROL_ERROR_EXIT_CODE` (2) — an authored control's
        own "CONTROL ERROR" convention (see guard_gate_eval_control.sh).
      - 126 / 127 — POSIX shell exec-failure codes ("found but not
        executable" / "command not found"): the control's command never
        even started, let alone ran the intended assertion.
      - `returncode < 0` — Python's `subprocess` reports a direct child
        killed by a signal this way on POSIX (e.g. -11 for a self-inflicted
        SIGSEGV).
      - `returncode >= 128` — the alternate `128 + signal` convention some
        shells/wrappers use when a signal kills a DESCENDANT rather than the
        immediate child (e.g. a shell that propagates a killed subprocess's
        status instead of dying itself).

    RESIDUAL LIMIT (deliberately not caught here, and cannot be by exit code
    alone): a control that crashes by exiting exactly 1 — e.g. an uncaught
    exception under a wrapper that maps any exception to exit 1 — is
    indistinguishable from a clean, semantically-RED predicate failure. Both
    simply fail a `== 0`-shaped predicate the same way; telling them apart
    would need a distinct signal from the control itself (a reserved
    marker/exit convention), which is out of scope for exit-code-only
    detection."""
    if returncode == CONTROL_ERROR_EXIT_CODE:
        return (f"exited with the reserved control-error code {CONTROL_ERROR_EXIT_CODE} "
                "(a harness/control crash, not a genuine predicate outcome)")
    if returncode in (126, 127):
        which = "found but not executable" if returncode == 126 else "command not found"
        return (f"exited {returncode} (shell exec failure — {which} — the control never "
                "ran the intended assertion at all)")
    if isinstance(returncode, int) and (returncode < 0 or returncode >= 128):
        return f"was killed by a signal (returncode={returncode}), not a clean predicate outcome"
    return None


def gate_sensitivity(check, cwd):
    """Optional: only runs when the descriptor supplies control fixtures.
    controls = { "positive": {command}, "negative": {command} } where positive
    should make the check GREEN and negative should make it RED.

    A control's raw exit code is inspected directly (not only the value
    `eval_predicate` derives from it) so a CRASHED control is never certified
    sensitive. `_control_crash_reason` (see its docstring for the full list
    and rationale) recognizes: the reserved `CONTROL_ERROR_EXIT_CODE` (2); a
    shell exec failure (126/127, "not executable" / "command not found");
    or a signal kill (`returncode < 0` or `>= 128`, covering both
    conventions a subprocess can report one under). Read through
    `eval_predicate` alone, ANY of these shapes is indistinguishable from a
    control that cleanly failed a `== 0`-shaped predicate: both simply fail
    to satisfy it, so a naive read certifies "passed" either way. But a
    crash proves nothing about whether the check under test is actually
    sensitive to the mutation the control was meant to apply — it must fail
    this gate CLOSED, never "passed", while a genuinely-RED negative control
    (a clean predicate failure, no crash) still passes.

    RESIDUAL LIMIT: see `_control_crash_reason`'s docstring — a control that
    crashes by exiting exactly 1 cannot be told apart from a clean RED by
    exit code alone; no exit-code-only fix can close that gap.

    HARDENING (PCF-23): `admission`, `admission.controls`, and each control
    value are author-supplied INPUT that can be present but the WRONG type
    rather than merely absent (e.g. a string/int/list standing in for a
    mapping); each such SUPPLIED-but-malformed shape is rejected `"failed"`
    with a reason naming what was expected, mirroring `_finalize`'s F29
    None-safe acquisition of `admission` rather than crashing on `**ctl` or
    `.get()`, while a non-dict `admission` itself (nothing supplied to call
    malformed) still folds to the pre-existing `"unverified"` path.

    ROUND 2 (QW-1, adversarial break-pass) fixed two remaining holes in the
    above:

      - a FALSY-but-wrong-typed `admission.controls` (`0`/`False`/`""`/`[]`)
        used to trip `if not controls` BEFORE the `isinstance(controls, dict)`
        check ever ran, misclassifying a genuinely SUPPLIED-but-malformed
        shape as `"unverified"` ("nothing provided"). The acquisition below
        checks `controls is None` first (the only "nothing supplied at all"
        case), then the type, then falls through to `"unverified"` only for
        an actually-EMPTY mapping `{}` — the one falsy shape with nothing
        supplied IN SUBSTANCE.
      - a control mapping whose OWN shape is fine (passes
        `isinstance(ctl, dict)`) but whose `"command"` VALUE is not a runnable
        string (`None`/int/bool/dict, or a string with an embedded NUL byte)
        used to crash `run_command`'s `subprocess.run` call UNCAUGHT one level
        deeper (`TypeError`/`ValueError`) — the same total-batch-abort defect
        this function exists to close, just nested inside a shape that
        already looked well-formed. The merged exec's `"command"` value is
        now validated BEFORE `run_command` is ever called, so no subprocess
        exception can escape this function."""
    admission = check.get("admission")
    controls = admission.get("controls") if isinstance(admission, dict) else None
    if controls is None:
        return "unverified", "no positive/negative controls provided"
    if not isinstance(controls, dict):
        return "failed", (
            "malformed admission.controls: expected a mapping with "
            f"'positive'/'negative' control mappings, got {type(controls).__name__}")
    if not controls:
        return "unverified", "no positive/negative controls provided"
    outcomes = {}
    for label in ("positive", "negative"):
        # ROUND 3 (QW-1, Codex review finding C1): the SAME absence-vs-malformed
        # ladder as the outer `admission.controls` acquisition above, one level
        # deeper. `if not ctl` used to trip on ANY falsy value regardless of
        # type, misclassifying a SUPPLIED-but-wrong-typed per-label control
        # (0/False/""/[]) as "unverified"/"missing" instead of "failed"/
        # malformed. The ladder below tells "genuinely nothing supplied"
        # (absent key, explicit null, or an actually-empty {} mapping) apart
        # from "something was supplied, just the wrong type": absence checks
        # first (key missing or None), then the type check (any non-dict —
        # including a falsy scalar — is SUPPLIED but malformed), then the
        # empty-mapping check last (a dict that made it past isinstance but
        # is empty is nothing supplied in substance).
        if label not in controls or controls[label] is None:
            return "unverified", f"missing {label} control"
        ctl = controls[label]
        if not isinstance(ctl, dict):
            return "failed", (
                f"malformed {label} control: expected a mapping like "
                f'{{"command": "..."}} merged over exec, got {type(ctl).__name__}')
        if not ctl:
            return "unverified", f"missing {label} control"
        merged_exec = {**check.get("exec", {}), **ctl}
        cmd = merged_exec.get("command")
        if not isinstance(cmd, str):
            return "failed", (
                f"malformed {label} control command: expected a shell command string, "
                f"got {type(cmd).__name__}")
        if "\x00" in cmd:
            return "failed", (
                f"malformed {label} control command: expected a shell command string, "
                f"got embedded NUL byte")
        probe = json.loads(json.dumps(check))  # deep copy
        probe["exec"] = merged_exec
        run = roc.run_command(probe, cwd, tier="B")
        if run["timed_out"]:
            return "failed", f"{label} control timed out during sensitivity probe"
        crash_reason = _control_crash_reason(run.get("returncode"))
        if crash_reason is not None:
            return "failed", (
                f"{label} control {crash_reason} — sensitivity cannot be certified: "
                f"stderr={run.get('stderr', '')[:300]!r}")
        try:
            value = roc.extract_signal(probe, run)
        except (ValueError, TypeError) as exc:
            # The third `extract_signal` in this module, and the one that made a
            # coercion failure fatal to admission. Caught here too so the guard
            # can never be the thing that crashes.
            return "failed", roc._uncoercible_reason(probe, "control signal")
        except roc.MissingSignal as exc:
            return "failed", f"{label} control produced no signal: {exc}"
        outcomes[label] = roc.eval_predicate(value, check.get("predicate", {}))
    if outcomes.get("positive") and not outcomes.get("negative"):
        return "passed", "positive control GREEN, negative control RED"
    return "failed", f"controls did not move the signal as expected: {outcomes}"


def _finalize(check, gates, failed):
    """Record admission result; stamp the integrity hash on a pass so the engine
    can detect any later descriptor tampering.

    `status` and `gates` are the only admission.* OUTPUTS — recomputed on every
    run — so they are written directly. Everything else already under
    `admission` is author-supplied INPUT (notably `admission.controls`, the
    positive/negative sensitivity fixtures a `kind: "guard"` descriptor needs
    to be re-admissible) and must be PRESERVED, never discarded (F29): a full
    overwrite (`check["admission"] = {...}`) would silently erase `controls`
    on every admission run, making the quarantine -> re-admit lifecycle a
    one-way door for any check whose sensitivity gate depends on them.
    `admission` is excluded from `_CONTRACT_FIELDS`/`descriptor_hash`
    (run_gate_checks.py), so merging instead of overwriting has zero impact on
    the integrity hash.

    F29 adversarial-review hole: `check.setdefault("admission", {})` only
    supplies its default when the KEY IS ABSENT. When `admission` is PRESENT
    but explicitly non-dict (e.g. `null`, as a JSON round-trip produces for
    an unset optional field), `setdefault` returns that existing non-dict
    value unchanged, and the following item assignment raises `TypeError`,
    uncaught, aborting admit_check.py's entire run BEFORE `save_state` — the
    whole batch is left un-admitted, not just this one check. The pre-F29
    unconditional overwrite self-healed this shape for free. Acquire the
    admission dict None-safely instead: a present-but-non-dict value is
    replaced (self-healed), while a genuine dict's author-supplied inputs
    (notably `admission.controls`) are preserved exactly as before."""
    status = "failed" if failed else "passed"
    admission = check.get("admission")
    if not isinstance(admission, dict):
        admission = {}
        check["admission"] = admission
    admission["status"] = status
    admission["gates"] = gates
    if status == "passed":
        check.setdefault("source", {})["admitted_hash"] = roc.descriptor_hash(check)
    quar = None
    if failed:
        quar = {"id": check.get("id"), "reason": failed[1], "gate_failed": failed[0]}
    return status, quar


def admit_evidence(check, cwd):
    """Evidence checks have no machine predicate; they are judged from a verdict
    artifact written by a non-maker. Apply the evidence-appropriate gates."""
    gates = {}
    failed = None

    def fail(name, reason):
        nonlocal failed
        if not failed:
            failed = (name, reason)

    mc = roc.maker_checker_error(check)
    gates["maker_checker"] = (mc is None)
    if mc:
        fail("maker_checker", mc)

    # Freshness anchor is mandatory for evidence: without a content hash a stale
    # passing verdict could be re-read forever.
    anchor = check.get("source", {}).get("evidence_hash")
    gates["freshness_anchor"] = bool(anchor)
    if not anchor:
        fail("freshness_anchor", "evidence check needs source.evidence_hash (content anchor)")

    # RED-first for evidence: the verdict must not already be GREEN on current
    # state (an absent/RED/unreadable verdict all satisfy "not yet passing").
    res = roc.evaluate_evidence(check, cwd)
    gates["red_first"] = (res["verdict"] != roc.GREEN)
    if res["verdict"] == roc.GREEN:
        fail("red_first", "evidence verdict is already GREEN on current state; not RED-first")

    return _finalize(check, gates, failed)


def admit_guard(check, cwd):
    """Admission path for a `kind: "guard"` check (T10/AC7/R4.1). A guard is GREEN
    at spec by construction, so it cannot pass the RED-first gate; it is proven
    instead by `green_at_spec` (live GREEN now) plus a MANDATORY sensitivity gate
    (mutate the guarded artifact → the check must go RED, or it gates nothing).

      - maker_checker, clean_signal, determinism : unchanged and MANDATORY.
      - red_first                                : REPLACED by green_at_spec.
      - sensitivity                              : PROMOTED advisory → MANDATORY.
      - source.red_first_proof                   : NEVER written (no RED to record).

    `kind` selects the admission PATH only — the gate evaluates a guard exactly
    like any other machine check (`run_gate` routes everything that is not
    `evidence` to `evaluate_check`)."""
    gates = {}
    failed = None

    def fail(name, reason):
        nonlocal failed
        if not failed:
            failed = (name, reason)

    mc = roc.maker_checker_error(check)
    gates["maker_checker"] = (mc is None)
    if mc:
        fail("maker_checker", mc)

    ok, reason = gate_clean_signal(check)
    gates["clean_signal"] = ok
    if not ok:
        fail("clean_signal", reason)
        # Same short-circuit as the other admission path — see the note there.
        return _finalize(check, _unevaluated(gates), failed)

    # One live probe shared by the determinism and green_at_spec gates.
    k = int(check.get("determinism", {}).get("probe_k", roc.DEFAULT_CONFIRMATION_K))
    values, probe_err = probe_signal(check, cwd, k)
    if probe_err:
        gates["determinism"] = False
        fail("determinism", probe_err)
        gates["green_at_spec"] = False
        fail("green_at_spec", f"cannot verify live GREEN (no signal): {probe_err}")
    else:
        det_ok = len({repr(v) for v in values}) == 1
        gates["determinism"] = det_ok
        if not det_ok:
            fail("determinism", f"non-deterministic signal across {k} runs: {values}")
        green_ok, green_reason = gate_green_at_spec(check, values)
        gates["green_at_spec"] = green_ok
        if not green_ok:
            fail("green_at_spec", green_reason)

    # Sensitivity is MANDATORY for a guard — it is the ONLY substitute proof a
    # green check has for RED-first. `gate_sensitivity` returns "unverified" when
    # controls are absent or incomplete; for a guard that is a hard failure (an
    # unfalsifiable green gates nothing), so it is folded to "failed".
    sens_status, sens_reason = gate_sensitivity(check, cwd)
    if sens_status == "unverified":
        sens_status = "failed"
    gates["sensitivity"] = sens_status
    if sens_status != "passed":
        fail("sensitivity", sens_reason)

    # NB: `_finalize` stamps admitted_hash on a pass and writes NO red_first_proof
    # (this path never calls gate_red_first_live), exactly as AC7 requires.
    return _finalize(check, gates, failed)


def already_admitted(check, quarantine):
    """True when `check` is admitted for its EXACT current descriptor and a
    bare re-run may leave it alone.

    Without this, a bare run would re-probe EVERY check — including one already
    genuinely admitted and untouched since. For a check whose code is now
    correctly implemented, a live re-probe can only find it GREEN, so
    `red_first` (which demands a RED result) fails it and it lands in
    quarantine — which a re-arm (add new checks, admit again with no `--id`)
    would do to every check already admitted before it.

    Mirrors run_gate_checks.admitted_checks' one definition of "admitted":
    `admission.status == "passed"` AND `source.admitted_hash` is PRESENT and
    still equals `descriptor_hash(check)` AND `id` is not in `quarantine[]`.
    Anything else — never admitted, a prior failure, a descriptor that changed
    since admission (a maker's edit, caught the same way `integrity_error`
    catches it at evaluation), or a quarantined id the gate would exclude — is
    NOT already-admitted and must still go through a live re-probe, which is
    also the only thing that reconciles its quarantine entry."""
    if check.get("id") in {q.get("id") for q in quarantine}:
        return False
    admission = check.get("admission")
    if not isinstance(admission, dict) or admission.get("status") != "passed":
        return False
    admitted_hash = check.get("source", {}).get("admitted_hash")
    return bool(admitted_hash) and admitted_hash == roc.descriptor_hash(check)


def admit_one(check, state_root, work_dir):
    """Dispatch on the same tree split `run_gate` uses: an evidence check's
    verdict artifact is read on `state_root`, every probed check runs on
    `work_dir`."""
    if check.get("type") == "evidence" or check.get("kind") == "evidence":
        return admit_evidence(check, state_root)
    if check.get("kind") == "guard":
        return admit_guard(check, work_dir)

    gates = {}
    failed = None

    def fail(name, reason):
        nonlocal failed
        if not failed:
            failed = (name, reason)

    mc = roc.maker_checker_error(check)
    gates["maker_checker"] = (mc is None)
    if mc:
        fail("maker_checker", mc)

    ok, reason = gate_clean_signal(check)
    gates["clean_signal"] = ok
    if not ok:
        fail("clean_signal", reason)
        # 🔴 SHORT-CIRCUIT, NOT MERELY "ORDERED FIRST". Recording the failure and
        # then probing anyway is what let the JC12 guard CRASH ADMISSION on the
        # exact descriptor it was added to refuse: `gate_sensitivity` reaches a
        # third `roc.extract_signal` that catches only `MissingSignal`, so an
        # unrecognised `value_type` escaped as `InvalidValueType`. Reproduced
        # 2026-08-15 through the shipped `admit_one`. Every remaining gate here
        # depends on RUNNING the check, and running a check whose signal contract
        # is already known bad buys nothing.
        return _finalize(check, _unevaluated(gates), failed)

    # One live probe shared by the determinism and RED-first (live) gates.
    k = int(check.get("determinism", {}).get("probe_k", roc.DEFAULT_CONFIRMATION_K))
    values, probe_err = probe_signal(check, work_dir, k)
    if probe_err:
        gates["determinism"] = False
        fail("determinism", probe_err)
        gates["red_first"] = False
        fail("red_first", f"cannot verify live RED (no signal): {probe_err}")
    else:
        det_ok = len({repr(v) for v in values}) == 1
        gates["determinism"] = det_ok
        if not det_ok:
            fail("determinism", f"non-deterministic signal across {k} runs: {values}")
        rf_ok, rf_reason = gate_red_first_live(check, values, work_dir)
        gates["red_first"] = rf_ok
        if not rf_ok:
            fail("red_first", rf_reason)

    sens_status, _ = gate_sensitivity(check, work_dir)
    gates["sensitivity"] = sens_status  # "passed" | "failed" | "unverified"
    # sensitivity is recommended, not mandatory: a "failed" sensitivity is a
    # strong smell but does not by itself quarantine in v1 (it needs per-check
    # fixtures); a "failed" mandatory gate does. Recorded honestly for the human.

    return _finalize(check, gates, failed)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Admit loop checks (verify the verifier).")
    parser.add_argument("--state", required=True)
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--id", help="Admit only this check id (default: all).")
    parser.add_argument("--force", action="store_true",
                         help="Re-run the full gate sequence even for a check that is "
                              "already admitted and unchanged (default: skip it).")
    args = parser.parse_args(argv)

    state_root = args.cwd or os.getcwd()
    with open(args.state, encoding="utf-8") as fh:
        state = json.load(fh)

    # H1/F34 family: a check must be admitted against the SAME tree the running
    # gate (`run_gate_checks.run_gate`) will evaluate it on, never blindly the
    # main checkout when a `worktree.path` is recorded (`commands/fairmind-loop.md`
    # step 6 invokes this with no `--cwd` at all, so `state_root` alone would be
    # the main checkout even when a worktree is in play). `--cwd` overrides only
    # `state_root` — the git-common-dir comparison inside `resolve_work_dir` and
    # the ledger location — never the tree the probe actually runs the check's
    # command against; that is `work_dir`, resolved by the exact helper `run_gate`
    # and `--arm`'s guard pre-check both use, so admission cannot silently drift
    # from what the gate will do. Evidence checks are the exception, as in
    # `run_gate`: their verdict artifact lives under `state_root`'s `.fairmind/`
    # (a worktree has none), so `admit_one` reads it there, never in `work_dir`.
    # A worktree that cannot be trusted degrades the WHOLE admission run closed
    # (byte-untouched: refuse before any probe runs and before any write),
    # mirroring `--arm`'s guard refusal — a check admitted against the wrong
    # tree would gate on evidence it never actually produced.
    work_dir, wt_degradation = roc.resolve_work_dir(state, state_root)
    if wt_degradation is not None:
        reason, detail = wt_degradation
        print(
            "admit_check: refused — this loop records a worktree.path that could "
            f"not be resolved to a real, registered worktree of this repo ({reason}: "
            f"{detail}). Failing closed rather than admitting checks against the "
            "main checkout — the running gate evaluates the worktree, and a check "
            "admitted against the wrong tree would gate on evidence it never "
            "actually produced. A human must resolve the worktree (re-create it "
            "via loop_worktree.py --create, or clear state['worktree']) before "
            "this loop's checks can be (re-)admitted.",
            file=sys.stderr)
        return 1

    quarantine = state.setdefault("quarantine", [])
    summary = []
    for check in state.get("checks", []):
        if args.id and check.get("id") != args.id:
            continue
        cid = check.get("id")
        # A bare re-run (no --id) must not re-probe a check that is
        # already admitted for its EXACT current descriptor — a live re-probe of
        # a now-correctly-implemented check can only find it GREEN, which
        # red_first can only read as "not RED-first" and quarantine, so every
        # re-arm (new checks added, admit_check.py run again with no --id) would
        # quarantine everything already admitted before it.
        # --force is the deliberate escape hatch back to a real live re-probe, and
        # so is an explicit --id: naming one check is a request to re-probe it (e.g.
        # after adding admission.controls, which descriptor_hash does not cover).
        if not args.force and not args.id and already_admitted(check, quarantine):
            summary.append((cid, "skipped"))
            continue
        status, quar = admit_one(check, state_root, work_dir)
        summary.append((cid, status))
        # Reconcile: a check that now passes is removed from quarantine; one that
        # now fails replaces its prior entry. Quarantine reflects the CURRENT
        # failures only, so a fixed-and-re-admitted check is no longer excluded.
        quarantine[:] = [q for q in quarantine if q.get("id") != cid]
        if quar:
            quarantine.append(quar)

    # loop-state.json is the loop's authoritative record — its checks, their admitted
    # hashes, the budget. Write it through the engine's own atomic writer (staged temp
    # file + os.replace), never in place: a write that dies halfway through an in-place
    # truncation leaves no loop at all, and admission is the write that FIRST populates
    # the file, so there is nothing to recover it from.
    roc.save_state(args.state, state)

    for cid, status in summary:
        print(f"admit_check: {cid} → admission={status}", file=sys.stderr)
    quarantined = [q["id"] for q in quarantine]
    if quarantined:
        print(f"admit_check: quarantined (excluded from stop): {quarantined}", file=sys.stderr)

    # A gate that gates nothing is not a success: an admission run that
    # considered zero checks (empty `checks`, or --id matching nothing), or
    # that quarantined EVERY check it evaluated, exits non-zero. A skipped
    # check was not evaluated and counts for neither outcome: it cannot turn an
    # all-failed run into a success, and a run that skipped everything (nothing
    # new to admit) exits 0. A partial quarantine among the evaluated checks
    # still exits 0 — the quarantined ids are surfaced above and excluded from
    # the stop condition.
    evaluated = [status for _, status in summary if status != "skipped"]
    if not summary or (evaluated and all(status == "failed" for status in evaluated)):
        print("admit_check: no check was admitted in this run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
