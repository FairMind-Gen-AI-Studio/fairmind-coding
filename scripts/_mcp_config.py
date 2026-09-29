"""Is a Fairmind MCP configured for THIS project? One definition, stdlib only.

Both halves of the plugin ask this question and neither may pay for the other's
weight to do it. `_insights_session` is 6,000 lines and imports on the ambient
lane's own path, where that cost is already accounted for; the judge hooks run
at every stop and at every tool batch, and the stop lane's
`test_the_capture_half_imports_no_heavy_module` forbids them that module ON ANY
PATH — not only at module scope, because a lazy import on a gate's common path
is paid at every stop just the same.

The alternative was a fifth hand-mirror of this lookup inside the hook. This
file exists instead: a mirror drifts silently and the drift is a consent
predicate answering differently on two lanes, which is the one shape nobody
would notice. `_insights_session` imports these names back, so there is one
definition and no copy to keep honest.

Every function here fails CLOSED. An unresolvable predicate is not consent.
"""

from __future__ import annotations

import json
import os
import re

# Anchored Fairmind-key match: a genuine key is exactly "fairmind" or begins with
# a "fairmind" SEGMENT (followed by a non-alphanumeric boundary or end of string),
# case-insensitive. So "Fairmind", "Fairmind-dev", "fairmind_local" arm, but a key
# that merely CONTAINS the substring ("not-fairmind-proxy", "fairmindish") does
# NOT false-positive-arm (Grok #7).
_FAIRMIND_NAME_RE = re.compile(r"^fairmind(?![a-z0-9])", re.IGNORECASE)


def _is_fairmind_name(key):
    """True iff `key` names the Fairmind MCP (anchored, not a bare substring)."""
    return isinstance(key, str) and _FAIRMIND_NAME_RE.match(key) is not None


def _fairmind_keys(mapping):
    """The anchored Fairmind key names in `mapping` (empty list if it is not a
    dict or has no Fairmind key). Returns the names, not a bool, so the caller can
    cross-check them against a disabled-servers guard (N1)."""
    if not isinstance(mapping, dict):
        return []
    return [key for key in mapping if _is_fairmind_name(key)]


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _projects():
    """`~/.claude.json`'s `projects` mapping, or `{}`. Never raises."""
    claude_json = _read_json(os.path.join(os.path.expanduser("~"), ".claude.json"))
    projects = claude_json.get("projects") if isinstance(claude_json, dict) else None
    return projects if isinstance(projects, dict) else {}


def fairmind_configured(cwd, toplevel):
    """True iff a PER-PROJECT Fairmind MCP is configured for `cwd`, read from LIVE
    config — never a marker file. Fail-closed on any uncertainty. `toplevel` is
    the pre-resolved git toplevel (None if unresolvable).

    PRIMARY: ~/.claude.json -> projects[<cwd>].mcpServers has an anchored Fairmind
    key that is NOT in projects[<cwd>].disabledMcpServers (a malformed disabled
    guard fails closed) — with a launched-in-subdir fallback to
    projects[<git toplevel>].
    SECONDARY: a repo-root team-committed .mcp.json whose mcpServers has a Fairmind
    key AND that key is EXPLICITLY approved by THIS project entry — listed in
    projects[<proj>].enabledMcpjsonServers (or enableAllProjectMcpServers is True)
    and NOT in disabledMcpjsonServers. Mere presence of a committed key does NOT
    arm (owner-confirmed): a fresh clone must never start capturing unapproved, so
    no project entry => secondary is off.
    MUST NOT COUNT: a user-global Fairmind entry (top-level ~/.claude.json
    mcpServers or ~/.claude/settings.json) — counting it makes every repo a
    tenant (violates V7)."""
    projects = _projects()

    # Resolve the project entry: exact cwd string first (how the harness keys it),
    # then the git toplevel as a launched-in-subdir fallback.
    proj = projects.get(cwd)
    if not isinstance(proj, dict):
        proj = projects.get(toplevel) if toplevel else None
        if not isinstance(proj, dict):
            proj = None
    return _entry_configured(proj, toplevel)


def _entry_configured(proj, toplevel):
    """Whether the resolved project entry `proj` (None when there is none) arms,
    per the two signals `fairmind_configured` documents."""
    # PRIMARY signal: this project's own mcpServers carries a Fairmind key that
    # is NOT explicitly disabled. If the Fairmind server is listed in this
    # project's disabledMcpServers it must NOT arm; if that guard is present but
    # MALFORMED (not a list) we cannot verify the server is enabled, so we fail
    # closed and do NOT arm on the primary path (N1). Either way we fall through
    # to the secondary .mcp.json explicit-approval path rather than returning
    # early — a separate, well-formed secondary approval may still arm.
    if isinstance(proj, dict):
        matched = _fairmind_keys(proj.get("mcpServers"))
        if matched:
            disabled = proj.get("disabledMcpServers")
            if disabled is None:
                return True  # nothing disabled -> the primary key arms
            if isinstance(disabled, list) and any(k not in disabled for k in matched):
                return True  # at least one matched key is enabled

    # SECONDARY: a repo-root committed .mcp.json, but ONLY when THIS project entry
    # explicitly approved the Fairmind key. No project entry => not approved.
    if not isinstance(proj, dict) or not toplevel:
        return False
    mcp = _read_json(os.path.join(toplevel, ".mcp.json"))
    servers = mcp.get("mcpServers") if isinstance(mcp, dict) else None
    if not isinstance(servers, dict):
        return False
    enable_all = proj.get("enableAllProjectMcpServers") is True
    enabled = proj.get("enabledMcpjsonServers")
    enabled = enabled if isinstance(enabled, list) else []
    disabled = proj.get("disabledMcpjsonServers")
    disabled = disabled if isinstance(disabled, list) else []
    for key in servers:
        if _is_fairmind_name(key) and key not in disabled and (enable_all or key in enabled):
            return True
    return False



def fairmind_configured_at(path):
    """`fairmind_configured` for a directory known only by its realpath — the
    checkout a linked work tree was created from, which git names canonically.

    The harness keys a project by the path it was launched from, so a checkout
    opened through a symlink is filed under that spelling and an exact lookup of
    the realpath misses it. Every key naming `path` is tried, the exact one
    first, and `~/.claude.json` is read once. Never raises."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    projects = _projects()

    def spellings():
        if real in projects:
            yield real
        for key in projects:
            try:
                if key != real and isinstance(key, str) and os.path.realpath(key) == real:
                    yield key
            except (OSError, ValueError):
                continue

    return any(_entry_configured(projects[key] if isinstance(projects[key], dict) else None, real)
               for key in spellings())
