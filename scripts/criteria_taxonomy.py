#!/usr/bin/env python3
"""
criteria_taxonomy.py — cross-repo LOCKSTEP hash over the Loop Readiness
criteria catalog (`harness_audit_criteria.json`).

A server-side copy mirrors this exact taxonomy:
the same set of `[pillar_id, criterion_id, level]` triples, under the same
`criteria_version`, keyed by the same pillar-name-to-slug map. The hash this
module computes (`compute_taxonomy_hash`) is how the two sides detect drift
without either one importing the other — if the catalog ever changes, both
hashes move together only if both sides are updated deliberately.

LOCKSTEP RE-PIN PROCEDURE — read this before touching
`harness_audit_criteria.json`'s pillar/criterion ids, levels, or
`criteria_version`. Any such change invalidates the pinned hash in
`tests/test_criteria_taxonomy_parity.py` (`EXPECTED_TAXONOMY_SHA256`) and
requires, in the SAME review cycle:

  1. Re-running `compute_taxonomy_hash()` over the new catalog and re-pinning
     the literal `EXPECTED_TAXONOMY_SHA256` (and `EXPECTED_CRITERIA_VERSION`
     if the version string changed) in that test file.
  2. Bumping `criteria_version` in the catalog itself.
  3. Updating the server-side copy of this taxonomy to match — the two are
     in lockstep and must be re-pinned together, never independently.

Skipping any of the three re-introduces silent taxonomy drift between the two
sides, which is exactly what this hash pin exists to catch.

CLI: `python3 criteria_taxonomy.py` prints the hash of the default (on-disk)
catalog — the value to transcribe when re-pinning per step 1 above.
"""

import hashlib
import json
import os
import sys

# Display name -> slug. Pinned 1:1 with `harness_audit_criteria.json`'s 9
# pillars (see `tests/test_criteria_taxonomy_parity.py`'s
# `test_pillar_name_to_slug_matches_catalog` guard) and folded into the hash so
# a slug rename is itself taxonomy drift, not a silent cosmetic change.
# Deliberately HARDCODED, not derived from the catalog: this map is the portable
# half of the cross-repo contract — the server-side copy reimplements it
# independently with no access to this catalog — while `taxonomy_triples` reads
# the catalog from disk. The `test_pillar_name_to_slug_matches_catalog` guard is
# what keeps the hardcoded half honest against catalog drift.
PILLAR_NAME_TO_SLUG = {
    "Style & Validation": "style-validation",
    "Build System": "build-system",
    "Testing": "testing",
    "Documentation": "documentation",
    "Dev Environment": "dev-environment",
    "Debugging & Observability": "observability",
    "Security": "security",
    "Task Discovery": "task-discovery",
    "Product & Analytics": "product-analytics",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG_PATH = os.path.join(SCRIPT_DIR, "harness_audit_criteria.json")


def _default_catalog():
    with open(DEFAULT_CATALOG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def taxonomy_triples(catalog=None):
    """Sorted `[pillar_id, criterion_id, level]` triples for `catalog` (a
    parsed dict). `catalog=None` resolves the default on-disk catalog next to
    this file."""
    if catalog is None:
        catalog = _default_catalog()
    return sorted(
        [pillar["id"], crit["id"], crit["level"]]
        for pillar in catalog["pillars"]
        for crit in pillar["criteria"]
    )


def compute_taxonomy_hash(catalog=None):
    """The canonical LOCKSTEP hash: sha256 hex over a JSON-canonicalized
    object of `{criteria_version, pillar_name_to_slug, triples}`. Byte-for-
    byte identical output is the whole point — this must match the
    server-side copy's independent computation of the same algorithm."""
    if catalog is None:
        catalog = _default_catalog()
    version = catalog["criteria_version"]
    triples = taxonomy_triples(catalog)
    canonical = {
        "criteria_version": version,
        "pillar_name_to_slug": PILLAR_NAME_TO_SLUG,
        "triples": triples,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv=None):
    print(compute_taxonomy_hash())
    return 0


if __name__ == "__main__":
    sys.exit(main())
