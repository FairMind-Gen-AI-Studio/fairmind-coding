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
from urllib.parse import urlsplit

# Anchored Fairmind-key match: a genuine key is exactly "fairmind" or begins with
# a "fairmind" SEGMENT (followed by a non-alphanumeric boundary or end of string),
# case-insensitive. So "Fairmind", "Fairmind-dev", "fairmind_local" arm, but a key
# that merely CONTAINS the substring ("not-fairmind-proxy", "fairmindish") does
# NOT false-positive-arm.
_FAIRMIND_NAME_RE = re.compile(r"^fairmind(?![a-z0-9])", re.IGNORECASE)


def _is_fairmind_name(key):
    """True iff `key` names the Fairmind MCP (anchored, not a bare substring)."""
    return isinstance(key, str) and _FAIRMIND_NAME_RE.match(key) is not None


def _fairmind_keys(mapping):
    """The anchored Fairmind key names in `mapping` (empty list if it is not a
    dict or has no Fairmind key). Returns the names, not a bool, so the caller can
    cross-check them against a disabled-servers guard."""
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


def _project_entry(cwd, toplevel):
    """The `~/.claude.json` project entry for `cwd`, else for the git toplevel
    (a session launched in a subdirectory), else None.

    The exact `cwd` string comes first because that is how the harness keys a
    project."""
    projects = _projects()
    proj = projects.get(cwd)
    if not isinstance(proj, dict):
        proj = projects.get(toplevel) if toplevel else None
        if not isinstance(proj, dict):
            proj = None
    return proj


def _prefer(keys):
    """The key exactly `"Fairmind"` when present, else the first in order."""
    if "Fairmind" in keys:
        return "Fairmind"
    return keys[0] if keys else None


def _select_entry(proj, toplevel):
    """The `(key, value)` of the Fairmind MCP entry the resolved project entry
    `proj` (None when there is none) arms, or None.

    ONE SELECTION behind both questions the plugin asks — "is one configured"
    (`fairmind_configured`) and "where is it" (`fairmind_server`) — so a lane
    can never find a server that the consent predicate said was absent.

    PRIMARY: this project's own `mcpServers` carries an anchored Fairmind key
    that is NOT in `disabledMcpServers`. A guard that is present but not a list
    cannot be verified, so it fails closed and the primary path does not arm;
    the secondary path is still tried.
    SECONDARY: the repo-root committed `.mcp.json`, but ONLY for a key this
    project entry explicitly approved. No project entry means not approved.
    Several enabled keys: the one named exactly `Fairmind`, else the first in
    mapping order. The value is returned whatever its type; the callers decide
    what a malformed one means."""
    if isinstance(proj, dict):
        servers = proj.get("mcpServers")
        matched = _fairmind_keys(servers)
        if matched:
            disabled = proj.get("disabledMcpServers")
            enabled = None
            if disabled is None:
                enabled = matched
            elif isinstance(disabled, list):
                enabled = [k for k in matched if k not in disabled]
            if enabled:
                key = _prefer(enabled)
                return key, servers[key]

    if not isinstance(proj, dict) or not toplevel:
        return None
    mcp = _read_json(os.path.join(toplevel, ".mcp.json"))
    servers = mcp.get("mcpServers") if isinstance(mcp, dict) else None
    if not isinstance(servers, dict):
        return None
    enable_all = proj.get("enableAllProjectMcpServers") is True
    enabled_list = proj.get("enabledMcpjsonServers")
    enabled_list = enabled_list if isinstance(enabled_list, list) else []
    disabled = proj.get("disabledMcpjsonServers")
    disabled = disabled if isinstance(disabled, list) else []
    approved = [key for key in servers
                if _is_fairmind_name(key) and key not in disabled
                and (enable_all or key in enabled_list)]
    if not approved:
        return None
    key = _prefer(approved)
    return key, servers[key]


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
    tenant."""
    return _entry_configured(_project_entry(cwd, toplevel), toplevel)


def _entry_configured(proj, toplevel):
    """Whether the resolved project entry `proj` (None when there is none) arms,
    per the two signals `fairmind_configured` documents."""
    return _select_entry(proj, toplevel) is not None


_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(text):
    """`text` with `${VAR}` and `${VAR:-default}` expanded from the environment,
    or None when a reference cannot be expanded. A default applies when the
    variable is unset or empty."""
    failed = []

    def sub(match):
        name, default = match.group(1), match.group(2)
        value = os.environ.get(name)
        if default is not None:
            return value if value else default
        if value is None:
            failed.append(name)
            return ""
        return value

    out = _ENV_REF_RE.sub(sub, text)
    return None if failed else out


def _url_allowed(url):
    """https anywhere; plain http only to the machine's own loopback names."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme == "https":
        return bool(host)
    return parts.scheme == "http" and host in ("localhost", "127.0.0.1")


def fairmind_server(cwd, toplevel):
    """The reachable Fairmind MCP entry for `cwd` as
    `{"key": str, "url": str, "headers": {str: str}}`, or None.

    The selection is `fairmind_configured`'s, entry for entry. None as well when
    the selected entry is not something a hook can POST to: no `url` (a stdio
    server), a URL that is neither https nor loopback http, headers that are not
    a str-to-str mapping, or a `${VAR}` reference that cannot be expanded.

    The headers carry the credential: callers pass them on and never log,
    print, store or put them in an argument vector. Never raises."""
    try:
        selected = _select_entry(_project_entry(cwd, toplevel), toplevel)
        if selected is None:
            return None
        key, value = selected
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            return None
        url = _expand(value["url"])
        if url is None or not _url_allowed(url):
            return None
        raw_headers = value.get("headers")
        headers = {}
        if raw_headers is not None:
            if not isinstance(raw_headers, dict):
                return None
            for name, header in raw_headers.items():
                if not isinstance(name, str) or not isinstance(header, str):
                    return None
                expanded = _expand(header)
                if expanded is None:
                    return None
                headers[name] = expanded
        return {"key": key, "url": url, "headers": headers}
    except Exception:  # noqa: BLE001 - an unreadable config is no server
        return None


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
