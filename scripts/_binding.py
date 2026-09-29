#!/usr/bin/env python3
"""
_binding.py — ONE definition of "what repository is this checkout bound to".

WHY IT IS ITS OWN MODULE. Three places have to answer that question and none
of them may import the others: the payload builders
(`insights_flush_payload`, 3,600 lines), the judge Stop hook
(the review hook, which deliberately imports neither of the big modules so
a stop costs one process start), and `--insights-status`
(`_insights_session`, 6,000 lines, on the SessionStart fast path). The first
round of PX2 gave each of them its own reader, and they diverged inside one
change: the payload builders refused a binding whose origin had moved while the
judge hook kept sending its id, so the two lanes disagreed about which
repository the same commit belonged to.

A stdlib-only LEAF, like `_fm_ignore` and `_plugin_policy` beside it — importing
it costs nothing else, which is what makes it reachable from the Stop hook.

THE STALENESS RULE, stated once here because it is the part that diverged. A
binding names ONE origin, it is recorded on disk, and the origin can be
re-pointed afterwards (a fork promoted to upstream, an organisation renamed).
The catalog id is matched EXACTLY and ahead of everything else server-side, so
a stale one does not degrade into a near miss — it files this repository's work
under a different repository. So a binding applies only while the origin it was
made for is still this checkout's origin, and both sides of that comparison go
through the same normalizer: the stored url is the CATALOG's own spelling,
which is not ours to assume.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import audit_run_meta  # noqa: E402  (stdlib-only leaf; `normalize_git_remote`)

#: The keys `/fairmind-connect` writes. Named here so the command that writes
#: them and the three readers that consume them cannot drift apart on a
#: spelling — the failure that has no symptom until a lane silently stops
#: recognising a binding it wrote itself.
REPOSITORY_ID = "repository_id"
REPOSITORY_NAME = "repository_name"
REPOSITORY_URL = "repository_url"
REPOSITORY_BRANCH = "repository_branch"
PROJECT_ID = "project_id"
BOUND_AT = "bound_at"


def context_path(root):
    """`.fairmind/active-context.json` under `root`, which is the git toplevel.

    The marker lives at the repository root by definition — every writer puts
    it there — so a reader anchored anywhere else answers "unbound" for a
    perfectly bound checkout whenever it is reached from a subdirectory."""
    return os.path.join(root, ".fairmind", "active-context.json")


def read_context(root):
    """The marker as a dict, or `{}`.

    Never raises. A missing, unreadable, malformed or non-object marker is an
    unbound checkout, which is the state every repository was in before
    `/fairmind-connect` existed and the state every caller already handles."""
    try:
        with open(context_path(root), encoding="utf-8") as handle:
            ctx = json.load(handle)
    except (OSError, ValueError):
        return {}
    return ctx if isinstance(ctx, dict) else {}


def is_bound(ctx):
    """Whether the marker records a binding at all.

    Its own predicate so a caller can decide whether the staleness check is
    worth a subprocess: an UNBOUND checkout — which is every checkout that has
    not run the command — must not pay `git remote get-url` to be told it is
    still unbound."""
    value = ctx.get(REPOSITORY_ID)
    return isinstance(value, str) and bool(value.strip())


def repository_id(ctx, current_remote=None):
    """The catalog `_id` this checkout is bound to, or None.

    `current_remote` is this checkout's origin, ALREADY NORMALIZED by
    `audit_run_meta.normalize_git_remote`, or None when the caller has not
    resolved one. Passing None skips the staleness comparison rather than
    failing it: "I did not look" and "it moved" are different answers, and only
    the second one may discard a binding the developer established.

    A PARAMETER RATHER THAN A GIT CALL IN HERE, so this stays pure: the payload
    builders already hold the value, and the Stop hook must not pay a
    subprocess for it on every turn end."""
    value = ctx.get(REPOSITORY_ID)
    value = value.strip() if isinstance(value, str) else None
    if not value:
        return None
    if current_remote and not origin_matches(ctx, current_remote):
        return None
    return value


def origin_matches(ctx, current_remote):
    """Whether the binding still describes the checkout whose origin is
    `current_remote`. True when the binding records no url — an older binding
    that predates the field is not evidence of a move."""
    bound = ctx.get(REPOSITORY_URL)
    if not isinstance(bound, str) or not bound.strip():
        return True
    normalized = audit_run_meta.normalize_git_remote(bound.strip())
    if not normalized:
        return True
    return normalized == current_remote
