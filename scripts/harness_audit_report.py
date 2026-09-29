#!/usr/bin/env python3
"""
harness_audit_report.py — render a single self-contained HTML report from a
`harness_audit.py` summary.json. Standard library only; portable across
customer OSes; no network access is required to view the output (see
"Self-containment" below).

Visual quality is a HUMAN judgement — no test asserts that the report looks
good; only self-containment and the presence of every pillar/dimension name
are machine-checked.

An optional per-criterion drill-down, sourced from a
`harness_audit.py` `assessment.jsonl` (`--assessment`, or its default sibling
next to `--summary`). When available, every pillar card expands to list each
of its criteria (id, title, verdict); a FAILED criterion additionally shows
its `expected` target (from the assessment record) and its `remediation`
text (looked up from the criteria catalog — `assessment.jsonl` deliberately
does not carry `remediation`, only `title`/`expected`, so the report loads
the catalog itself, the same default-resolution convention as
`harness_audit.py`'s own `--catalog`). Absence of an assessment (no flag, no
sibling file) stays legal — pillar aggregates and
dimension pills only, plus an explicit note that no per-criterion assessment
was available.

Usage:
  harness_audit_report.py --summary <path/to/summary.json> [--out <path>]
                           [--assessment <path/to/assessment.jsonl>]
                           [--catalog <path/to/criteria.json>]

--summary (required): path to a summary.json as produced by harness_audit.py.
--out (optional): output HTML path. Default: `.fairmind/audit/report.html`
  relative to the summary's own directory (its sibling). Parent dirs are
  created if absent.
--assessment (optional): path to an assessment.jsonl (T15 per-criterion
  drill-down). Default: `assessment.jsonl` in the same directory as
  --summary, used only if it exists there — that absence is legal (T7
  back-compat). Given explicitly, a missing/unreadable/malformed file is a
  hard error (an explicitly-requested signal is never a silent pass).
--catalog (optional): criteria catalog JSON, consulted only to source
  `remediation` text for FAILED criteria when a per-criterion assessment is
  in play. Default: `harness_audit_criteria.json` next to this script (the
  shipped catalog) — the same resolution `harness_audit.py --catalog`
  defaults to.

Exit code 0 only on a clean run. A missing/unreadable/invalid (non-JSON)
summary, or a summary missing 'pillars', exits non-zero and writes no
partial file: the HTML is built fully in memory before anything is written
to disk — the same hard-error discipline as harness_audit.py itself. A
summary missing the optional 'dimensions' key (legal — see T6 Amendment 1)
is NOT an error: the report renders the pillars and simply shows no
dimension pills. Likewise a summary/assessment pair with no per-criterion
data at all is legal; a pair where an assessment record's `pillar_id` does
not match any pillar in the summary is NOT — a mismatched pair is a hard
error, never silently dropped.
"""

import argparse
import html
import json
import os
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
DEFAULT_OUT_RELATIVE = os.path.join(".fairmind", "audit", "report.html")
DEFAULT_CATALOG_NAME = "harness_audit_criteria.json"

# Dimension status -> CSS class / label. Mirrors the T6 contract's status
# table (score is null -> not-probed; >=0.8 -> clean; (0, 0.8) -> weak;
# ==0 -> absent). A null score must NEVER render as clean/green — the
# not-probed branch is handled separately in _render_dimension_pill and
# never reuses the "clean" class.
_STATUS_LABELS = {
    "clean": "Clean",
    "weak": "Weak",
    "absent": "Absent",
    "not-probed": "Not probed",
}


class ReportError(Exception):
    """Raised for any structural problem with the input summary. Always a
    hard error — never a partial report.html."""


# ---------------------------------------------------------------------------
# Styling — a STATIC SNAPSHOT of the `:root` design-token values of a real
# design system, taken on 2026-07-12. The source is a stylesheet outside this
# repository, so it is dated rather than cited: a reader here can only check
# what the snapshot IS, never what it was copied from, and a path that names
# somebody's checkout answers neither question. Only the value tokens (colors,
# spacing, radii, shadows, motion) are copied; the @font-face rules for
# Geist / JetBrains Mono are deliberately NOT copied — those load webfont
# files via a CSS url function, which self-containment forbids (no external
# fetch, no giant embedded blob). The report uses a system font stack instead
# (FONT_STACK below); `--font-sans`/`--font-mono` are correspondingly redefined
# to system stacks rather than copied verbatim, per the contract: "Named
# font-families are fine; fetching them is not" — we simply don't reference the
# unfetched family names at all, to keep the snapshot honest about what's
# available.
# ---------------------------------------------------------------------------

FONT_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
MONO_FONT_STACK = '"SF Mono", "Cascadia Code", Consolas, monospace'

STYLE_TOKENS = f"""
/* Static snapshot of :root design tokens, taken from a design system on
   2026-07-12 (values only — no @font-face, no CSS url function, no network). */
:root {{
  --bg:               #181C22;
  --surface:          #1D2128;
  --surface-raised:   #242A33;
  --border:           #1D2A3B;
  --border-hover:     #2A3A50;

  --text-primary:     #F8FAFC;
  --text-secondary:   #94A3B5;
  --text-tertiary:    #5A6578;

  --primary:          #0B774F;
  --primary-light:    #16B27A;
  --primary-dark:     #095D3E;

  --accent-blue:       #29B6F6;
  --accent-purple:     #a78bfa;

  --success:          #10B981;
  --warning:          #F59E0B;
  --error:            #EF4444;
  --info:             #29B6F6;

  --font-sans: {FONT_STACK};
  --font-mono: {MONO_FONT_STACK};

  --fs-11: 11px;
  --fs-12: 12px;
  --fs-13: 13px;
  --fs-16: 16px;
  --fs-20: 20px;
  --fs-24: 24px;

  --radius-sm:   4px;
  --radius-md:   6px;
  --radius-lg:   8px;
  --radius-full: 9999px;

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;

  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.2);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.35);
  --shadow-glow-teal: 0 0 24px rgba(22, 178, 122, 0.35);

  --ease-out: cubic-bezier(0.4, 0, 0.2, 1);
}}
"""

BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: var(--font-sans);
  background: var(--bg);
  color: var(--text-primary);
  font-size: var(--fs-13);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

.wrap { max-width: 1040px; margin: 0 auto; padding: var(--space-8) var(--space-6); }

header { margin-bottom: var(--space-8); }
header h1 { font-size: var(--fs-24); font-weight: 700; letter-spacing: -0.01em; }
header .meta {
  margin-top: var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--fs-11);
  color: var(--text-tertiary);
}

section { margin-bottom: var(--space-8); }
section h2 {
  font-size: var(--fs-11);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-tertiary);
  margin-bottom: var(--space-4);
}

.gauge {
  display: flex;
  align-items: center;
  gap: var(--space-6);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
}
.gauge-dial {
  width: 96px;
  height: 96px;
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  background: conic-gradient(var(--primary-light) calc(var(--pct) * 1%), var(--surface-raised) 0);
  box-shadow: var(--shadow-glow-teal);
  flex: none;
}
.gauge-dial-inner {
  width: 72px;
  height: 72px;
  border-radius: var(--radius-full);
  background: var(--bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono);
  font-size: var(--fs-16);
  font-weight: 700;
}
.gauge-label { font-size: var(--fs-13); color: var(--text-secondary); }
.gauge-label strong { color: var(--text-primary); }

.pillar-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}
.pillar-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-4);
}
.pillar-card .pillar-level {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: var(--fs-11);
  font-weight: 600;
  color: var(--primary-light);
  border: 1px solid var(--border-hover);
  border-radius: var(--radius-full);
  padding: 2px var(--space-2);
  margin-bottom: var(--space-2);
}
.pillar-card .pillar-name {
  font-size: var(--fs-13);
  font-weight: 600;
}
.pillar-card .pillar-sub {
  margin-top: var(--space-1);
  font-size: var(--fs-11);
  color: var(--text-tertiary);
}

.pillar-criteria {
  margin-top: var(--space-3);
  border-top: 1px solid var(--border);
  padding-top: var(--space-2);
}
.pillar-criteria summary {
  cursor: pointer;
  font-size: var(--fs-11);
  color: var(--text-secondary);
}
.criterion {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-2);
  margin-top: var(--space-2);
  font-size: var(--fs-11);
}
.criterion-verdict {
  font-family: var(--font-mono);
  font-weight: 700;
  text-transform: uppercase;
}
.criterion-pass .criterion-verdict { color: var(--success); }
.criterion-fail .criterion-verdict { color: var(--error); }
.criterion-id { font-family: var(--font-mono); color: var(--text-tertiary); }
.criterion-title { color: var(--text-primary); }
.criterion-detail {
  flex-basis: 100%;
  margin-top: var(--space-1);
  color: var(--text-secondary);
}
.criterion-detail span { display: block; }

.dimension-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}
.dim-pill {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-full);
  padding: var(--space-2) var(--space-4);
}
.dim-pill .dim-name { font-size: var(--fs-12); color: var(--text-secondary); }
.dim-pill .dim-score {
  font-family: var(--font-mono);
  font-size: var(--fs-12);
  font-weight: 700;
  border-radius: var(--radius-sm);
  padding: 1px var(--space-2);
}
.dim-clean .dim-score      { color: var(--success); }
.dim-weak .dim-score       { color: var(--warning); }
.dim-absent .dim-score     { color: var(--error); }
.dim-not-probed .dim-score { color: var(--text-tertiary); }
.dim-not-probed { border-style: dashed; }

.empty-note { color: var(--text-tertiary); font-size: var(--fs-12); }

footer {
  margin-top: var(--space-8);
  padding-top: var(--space-4);
  border-top: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: var(--fs-11);
  color: var(--text-tertiary);
}
"""


# ---------------------------------------------------------------------------
# Rendering helpers — every value interpolated from the summary is
# HTML-escaped; a Python `None` is always substituted with a printable
# default before escaping so a raw "None" can never leak into the output.
# ---------------------------------------------------------------------------

def _esc(value, default=""):
    if value is None:
        value = default
    return html.escape(str(value), quote=True)


def _pillar_level_css_class(level):
    if isinstance(level, int) and not isinstance(level, bool) and 0 <= level <= 5:
        return f"level-{level}"
    return "level-unknown"


def _render_criterion_row(record, remediation_by_id):
    """T15: one criterion within a pillar's drill-down — id, title, verdict
    always; for a FAILED criterion, additionally its 'expected' target (from
    the assessment record itself) and its 'remediation' text (looked up by id
    in the catalog, since assessment.jsonl deliberately does not carry it)."""
    cid = record.get("criterion_id")
    cid_label = _esc(cid, "(unknown)")
    title = _esc(record.get("title"), "")
    verdict = record.get("verdict")
    verdict_class = verdict if verdict in ("pass", "fail") else "unknown"
    verdict_label = _esc(verdict, "unknown")

    detail = ""
    if verdict == "fail":
        expected = _esc(record.get("expected"), "")
        remediation = _esc(remediation_by_id.get(cid), "")
        detail = (
            f'<div class="criterion-detail">'
            f'<span class="criterion-expected">Expected: {expected}</span>'
            f'<span class="criterion-remediation">Remediation: {remediation}</span>'
            f'</div>'
        )

    return (
        f'<div class="criterion criterion-{verdict_class}">'
        f'<span class="criterion-verdict">{verdict_label}</span>'
        f'<span class="criterion-id">{cid_label}</span>'
        f'<span class="criterion-title">{title}</span>'
        f'{detail}'
        f'</div>'
    )


def _render_pillar_card(pillar, criteria_records=None, remediation_by_id=None):
    name = _esc(pillar.get("name"), "(unnamed pillar)")
    level = pillar.get("level")
    level_label = _esc(level, "—")
    total = pillar.get("criteria_total")
    passed = pillar.get("criteria_passed")
    sub = ""
    if isinstance(total, int) and isinstance(passed, int):
        sub = f'<div class="pillar-sub">{_esc(passed)}/{_esc(total)} criteria passed</div>'

    drilldown = ""
    if criteria_records:
        rows = "\n".join(
            _render_criterion_row(r, remediation_by_id or {}) for r in criteria_records
        )
        drilldown = (
            f'<details class="pillar-criteria">'
            f'<summary>Criteria ({_esc(len(criteria_records))})</summary>'
            f'{rows}'
            f'</details>'
        )

    return (
        f'<div class="pillar-card {_pillar_level_css_class(level)}">'
        f'<span class="pillar-level">L{level_label}</span>'
        f'<div class="pillar-name">{name}</div>'
        f'{sub}'
        f'{drilldown}'
        f'</div>'
    )


def _render_dimension_pill(dim):
    name = _esc(dim.get("name"), "(unnamed dimension)")
    detail = _esc(dim.get("detail"), "no detail")
    score = dim.get("score")

    if score is None:
        # A null score is NEVER a clean/green pill (T6/T7 contract) — always
        # the dedicated not-probed rendering, regardless of what `status`
        # the summary happens to carry.
        status_class = "dim-not-probed"
        score_label = "Not probed"
    else:
        status = dim.get("status")
        status_key = status if status in _STATUS_LABELS else "not-probed"
        status_class = f"dim-{status_key}"
        try:
            score_label = f"{float(score) * 100:.0f}%"
        except (TypeError, ValueError):
            score_label = _esc(score)

    return (
        f'<div class="dim-pill {status_class}" title="{detail}">'
        f'<span class="dim-name">{name}</span>'
        f'<span class="dim-score">{score_label}</span>'
        f'</div>'
    )


def _compute_gauge(summary):
    """Overall readiness figure derived from totals (passed/criteria) plus
    the pillar levels — never hardcoded. Returns (pct:int 0-100, avg_level:
    float or None)."""
    totals = summary.get("totals")
    pct = 0
    if isinstance(totals, dict):
        criteria = totals.get("criteria")
        passed = totals.get("passed")
        if isinstance(criteria, int) and isinstance(passed, int) and criteria > 0:
            pct = round(max(0, min(passed, criteria)) / criteria * 100)

    levels = [
        p.get("level") for p in summary.get("pillars", [])
        if isinstance(p.get("level"), int) and not isinstance(p.get("level"), bool)
    ]
    avg_level = round(sum(levels) / len(levels), 1) if levels else None
    return pct, avg_level


def _group_records_by_pillar(records):
    """Group assessment records by pillar_id, preserving assessment.jsonl
    order (= catalog order) within each pillar — the order records were
    appended to the list, never re-sorted."""
    grouped = {}
    for r in records:
        grouped.setdefault(r.get("pillar_id"), []).append(r)
    return grouped


def render_report(summary, assessment_records=None, remediation_by_id=None):
    """Build the full HTML document as a string, purely from `summary`
    (already validated to have a 'pillars' list) plus the OPTIONAL T15
    per-criterion `assessment_records` / `remediation_by_id`. Never touches
    disk."""
    pillars = summary.get("pillars", [])
    dimensions = summary.get("dimensions")  # optional (T6 Amendment 1)

    pct, avg_level = _compute_gauge(summary)
    avg_level_label = _esc(avg_level, "n/a")

    grouped = _group_records_by_pillar(assessment_records) if assessment_records else {}
    remediation_by_id = remediation_by_id or {}

    pillar_cards = "\n".join(
        _render_pillar_card(p, grouped.get(p.get("id")), remediation_by_id) for p in pillars
    ) or ('<p class="empty-note">No pillars in this summary.</p>')

    # T15: no assessment available at all (legal — T7 back-compat) gets an
    # explicit note so the absence is visible rather than silently omitted.
    assessment_note = ""
    if not assessment_records:
        assessment_note = (
            '<p class="empty-note">No per-criterion assessment was available for this run.</p>'
        )

    if dimensions:
        dimension_pills = "\n".join(_render_dimension_pill(d) for d in dimensions)
        dimensions_body = f'<div class="dimension-grid">\n{dimension_pills}\n</div>'
    else:
        # Amendment 1: no 'dimensions' key is legal (the catalog declared
        # none) — render the section with an explicit empty note, never an
        # error and never a silently-omitted heading.
        dimensions_body = '<p class="empty-note">No Loop Readiness dimensions declared by the audit catalog.</p>'

    criteria_version = _esc(summary.get("criteria_version"), "n/a")
    source = _esc(summary.get("source"), "n/a")
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    totals_label = f"{_esc(totals.get('passed'), '?')}/{_esc(totals.get('criteria'), '?')} criteria passed"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loop Readiness Report</title>
<style>
{STYLE_TOKENS}
{BASE_CSS}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Loop Readiness Report</h1>
    <div class="meta">criteria_version {criteria_version} &middot; source: {source}</div>
  </header>

  <section class="gauge">
    <div class="gauge-dial" style="--pct: {pct};">
      <div class="gauge-dial-inner">{pct}%</div>
    </div>
    <div class="gauge-label">
      Overall readiness &mdash; <strong>{totals_label}</strong><br>
      Average pillar level: <strong>{avg_level_label}</strong>
    </div>
  </section>

  <section class="pillars">
    <h2>Pillars</h2>
    {assessment_note}
    <div class="pillar-grid">
      {pillar_cards}
    </div>
  </section>

  <section class="dimensions">
    <h2>Loop Readiness Dimensions</h2>
    {dimensions_body}
  </section>

  <footer>harness_audit_report.py &middot; self-contained, no network required to view</footer>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _load_summary(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as e:
        raise ReportError(f"cannot read summary {path!r}: {e}")
    except json.JSONDecodeError as e:
        raise ReportError(f"invalid JSON in summary {path!r}: {e}")

    if not isinstance(data, dict):
        raise ReportError(f"summary {path!r}: root must be a JSON object")
    if not isinstance(data.get("pillars"), list):
        raise ReportError(f"summary {path!r}: missing a 'pillars' list")
    return data


def _load_assessment(path):
    """T15: read an assessment.jsonl (one JSON object per non-blank line).
    Any read/parse failure is a hard error — callers decide whether a
    *missing* file is legal (default, no sibling) or not (explicit --assessment)
    before calling this; once we're reading a file that exists, every line
    in it must parse cleanly."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as e:
        raise ReportError(f"cannot read assessment {path!r}: {e}")

    records = []
    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise ReportError(f"invalid JSON on line {lineno} of assessment {path!r}: {e}")
        if not isinstance(rec, dict):
            raise ReportError(f"assessment {path!r} line {lineno}: record must be a JSON object")
        records.append(rec)
    return records


def _validate_assessment_against_summary(records, summary):
    """A criterion present in the assessment whose pillar_id is not in the
    summary's pillars is a hard error — a mismatched summary/assessment pair,
    never silently dropped."""
    pillar_ids = {p.get("id") for p in summary.get("pillars", [])}
    for r in records:
        pid = r.get("pillar_id")
        if pid not in pillar_ids:
            raise ReportError(
                f"assessment record for criterion {r.get('criterion_id')!r} references "
                f"pillar_id {pid!r}, which is not one of the summary's pillars "
                f"{sorted(p for p in pillar_ids if p)} — mismatched summary/assessment pair"
            )


def _load_catalog(path):
    """T15: the criteria catalog, consulted only to source `remediation` text
    for FAILED criteria (assessment.jsonl deliberately does not carry it —
    see the T15 contract's 'Assessment record' delta). Same hard-error
    discipline as every other input here."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as e:
        raise ReportError(f"cannot read catalog {path!r}: {e}")
    except json.JSONDecodeError as e:
        raise ReportError(f"invalid JSON in catalog {path!r}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("pillars"), list):
        raise ReportError(f"catalog {path!r}: root must be a JSON object with a 'pillars' list")
    return data


def _remediation_map(catalog):
    m = {}
    for pillar in catalog.get("pillars", []):
        for crit in pillar.get("criteria", []):
            cid = crit.get("id")
            if cid:
                m[cid] = crit.get("remediation")
    return m


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a self-contained HTML report from a harness_audit.py summary.json."
    )
    parser.add_argument("--summary", required=True, help="path to summary.json")
    parser.add_argument("--out", default=None,
                         help="output HTML path (default: .fairmind/audit/report.html, "
                              "a sibling of --summary's directory)")
    parser.add_argument("--assessment", default=None,
                         help="path to an assessment.jsonl for the T15 per-criterion drill-down "
                              "(default: assessment.jsonl next to --summary, when present)")
    parser.add_argument("--catalog", default=None,
                         help="criteria catalog JSON, used to source 'remediation' text for "
                              "failed criteria (default: harness_audit_criteria.json next to "
                              "this script)")
    args = parser.parse_args(argv)

    if args.out is not None:
        out_path = args.out
    else:
        summary_dir = os.path.dirname(os.path.abspath(args.summary))
        out_path = os.path.join(summary_dir, DEFAULT_OUT_RELATIVE)

    try:
        summary = _load_summary(args.summary)

        # T15: resolve the (optional) per-criterion assessment source. An
        # explicit --assessment is a real request — missing/malformed is a
        # hard error. The default sibling is only consulted if it actually
        # exists; a genuinely absent default is legal (T7 back-compat, AC3
        # point 5) and leaves assessment_records as None.
        explicit_assessment = args.assessment is not None
        if explicit_assessment:
            assessment_path = args.assessment
        else:
            summary_dir = os.path.dirname(os.path.abspath(args.summary))
            assessment_path = os.path.join(summary_dir, "assessment.jsonl")

        assessment_records = None
        if explicit_assessment or os.path.isfile(assessment_path):
            assessment_records = _load_assessment(assessment_path)
            _validate_assessment_against_summary(assessment_records, summary)

        # The catalog is only needed to look up 'remediation' for FAILED
        # criteria — never loaded when there is no assessment, or when every
        # assessed criterion already passed.
        remediation_by_id = {}
        if assessment_records and any(r.get("verdict") == "fail" for r in assessment_records):
            catalog_path = args.catalog or os.path.join(SCRIPT_DIR, DEFAULT_CATALOG_NAME)
            remediation_by_id = _remediation_map(_load_catalog(catalog_path))

        # Build the full document in memory before touching disk — a hard
        # error above this point never leaves a stale/partial report.html.
        html_content = render_report(summary, assessment_records, remediation_by_id)
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out_dir = os.path.dirname(out_path)
    if out_dir:
        makedirs_ignored(out_dir)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
