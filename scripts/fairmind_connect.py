#!/usr/bin/env python3
"""
fairmind_connect.py — the engine behind `/fairmind-connect` (PX2).

WHAT IT IS FOR. Until this existed, the only instruction for connecting a
checkout to the platform was PROSE addressed to an agent — "point them to
Studio -> avatar -> Developer page for the project API key + the MCP snippet"
— and the repository identity every lane then sent was a FOLDER NAME. The
server resolves projects by ObjectId or exact name, so a folder called
`payments-api` under a project called "Payments Platform" fell through
silently, every row landed under a key that joins to nothing, and the
link from a decision to the function it concerns was never even
attempted. This command makes the connection a VERIFIED ACT and replaces the
folder name with the code-ingestion catalog id everywhere the plugin
speaks.

WHAT IT DOES NOT DO, and the boundaries are the design:

  * It NEVER writes `~/.claude.json`. The MCP entry carries a bearer token;
    a tool that edits that file on a developer's behalf is a tool that can
    point their credential somewhere. It prints the exact command instead.
  * It resolves NOTHING itself. The repository is matched server-side, on the
    origin url, by the door that owns the catalog. The plugin stays dumb: it
    reports what it has and records what it is told (IP boundary, ONB-1).
  * It invents no wire field. Once bound, the catalog `_id` travels in the
    EXISTING `repository` field of the decisions and audit payloads — the
    server's matcher takes an id or a name on that same parameter — so an
    UNBOUND checkout's payloads stay byte-identical to what they were.

THE SILENT TRAPS IT EXISTS TO NAME (each measured, not assumed):

  1. Arming needs a PER-PROJECT entry whose key matches `^fairmind(?![a-z0-9])`
     (`_insights_session._FAIRMIND_NAME_RE`). A user-global entry is ignored BY
     DESIGN — counting it would make every repository on the machine a tenant —
     and a committed `.mcp.json` needs this project's explicit approval. A
     developer who pasted the snippet at the wrong scope sees a plugin that
     looks configured and captures nothing.
  2. The Developer page names the key after the browser's host — `Fairmind` on
     prod, `Fairmind-dev` on dev — and the plugin's anchored regex arms BOTH.
     A key copied from the wrong Studio therefore arms the wrong backend, in
     silence. This command cannot know which host is "right" (no host table
     ships in a customer-facing plugin, and the token carries no environment
     claim), so it reports what it CAN measure: two armed keys pointing at two
     different hosts, side by side, for a human to settle.
  3. The key expires and cannot be renewed. `exp` is printed on every run and
     an expiry inside 30 days is a warning, because the failure it prevents —
     a lane that silently stops delivering — has no other local symptom.
  4. `--project B` against a key whose own `projectId` claim is A. The server
     enforces per-project write access from the VERIFIED claim, never from
     what the caller named, so a bind under the override records B locally
     while every write this key attempts is checked against A and refused —
     the first symptom used to be a loop's write-back reporting "not synced".
  5. A key with no `write`/`admin` `scope` claim — every key minted before
     the claim existed, or copied with no project selected. Reads still work,
     so this is a warning rather than a trap: the connect is legitimate, only
     the write-back is dead on arrival.

EXIT CODES, which the command body branches on:

    0  bound (or already bound — a re-run is idempotent)
    1  a trap the developer must fix; every trap found is printed, not the first
    2  usage
    3  no project could be resolved: the token carries no `projectId` claim and
       no `--project` was given. The command body answers this one by asking.
    4  retryable: the BIND door answered 503, or the network did not answer at
       all. A tenant-status door that fails is only a warning — it is a
       diagnostic, and the bind below is the real test of whether anything
       works — so it never produces this code on its own.

Stdlib only, like every script in this plugin. Every server call is REST:
the bind door has an MCP twin, but the twin has no status codes — every
refusal arrives as a `message` string — while the traps above are exactly the
distinctions a status code makes.
"""

from __future__ import annotations

import argparse
import json
import time
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _consent_authority as S  # noqa: E402
import _plugin_policy  # noqa: E402
import ambient_outbox  # noqa: E402
import audit_run_meta  # noqa: E402
import _binding  # noqa: E402
from _fm_ignore import makedirs_ignored  # noqa: E402
from loop_open import _atomic_write_json  # noqa: E402

#: The two doors this command speaks to, both REST, both on the SAME origin as
#: the configured MCP url — derived from it by `_derive_insights_endpoint`,
#: never separately configurable, so the bearer can only ever be sent to the
#: host that already holds it.
_BIND_PATH = "/insights/v1/bind-repository"
_TENANT_STATUS_PATH = "/insights/v1/tenant-status"

#: Per-socket timeout and a bounded read, the same idiom (and for the same
#: reasons) as the client-policy fetch one module over: a black-holed endpoint
#: must not pin a command a developer is watching, and a hostile one must not
#: hand it an unbounded body.
_TIMEOUT_S = 15
_MAX_RESPONSE_BYTES = 65536

#: An expiry closer than this is a warning. The number is a month because that
#: is the horizon on which "ask Studio for a new key" is a task rather than an
#: emergency; the key cannot be renewed, so the only cure is a new one.
_EXPIRY_WARNING_DAYS = 30

#: `mode` written when this command has to CREATE `.fairmind/active-context.json`
#: (a checkout connected before it ever ran a loop). It is deliberately
#: `closed` — the one value of the three-word vocabulary that every hook reads
#: as "nothing is live here", which is the only thing a connect can honestly
#: assert about a session it is not part of. `interactive` would be a claim
#: about a session AND would arm the journal gate: `check-journal.sh` demands a
#: journal from every code-mutating sub-agent under that marker, so connecting a
#: repository with a dirty tree would start blocking sub-agents that were
#: finishing cleanly the minute before. A connect changes IDENTITY; it must not
#: change ENFORCEMENT.
_BOOTSTRAP_MODE = "closed"

#: `base_path` written on that same bootstrap. NOT decoration and not a default
#: repeated for tidiness: `check-journal.sh` exits 2 — refusing every sub-agent
#: completion — when active-context.json exists and names no readable
#: `base_path`. Creating the file without one would convert "no journal rule
#: here" into "no sub-agent may ever finish".
_BOOTSTRAP_BASE_PATH = ".fairmind"


# --------------------------------------------------------------------------- #
# Reporting. One printer so the transcript is deterministic and the command
# body can relay stdout verbatim instead of re-typing it (a re-typed report is
# a report that can drift from what the engine actually did).
# --------------------------------------------------------------------------- #

class Report:
    """Collects lines and traps. `trap()` never raises: every trap found in a
    step is reported, because a developer who fixes one and re-runs to find the
    next one has been made to pay for the tool's convenience."""

    def __init__(self):
        self.lines = []
        self.traps = []

    def ok(self, text):
        self.lines.append(f"  [ok]   {text}")

    def warn(self, text):
        self.lines.append(f"  [warn] {text}")

    def info(self, text):
        self.lines.append(f"         {text}")

    def head(self, text):
        self.lines.append(text)

    def trap(self, text):
        self.lines.append(f"  [FIX]  {text}")
        self.traps.append(text)

    def flush(self):
        sys.stdout.write("\n".join(self.lines) + "\n")
        sys.stdout.flush()


# --------------------------------------------------------------------------- #
# Step 1 — the configuration, read the way the plugin itself reads it.
# --------------------------------------------------------------------------- #

def _claude_json():
    return S._read_json(os.path.join(os.path.expanduser("~"), ".claude.json"))


def _project_entry(config, root):
    """The per-project entry that arms this checkout, or None.

    ONE KEY, not the two-rung ladder `fairmind_configured` and
    `fairmind_delivery_target` walk. Those two accept `projects[cwd]` first and
    fall back to `projects[toplevel]` because a session can open in a
    subdirectory; this command resolves the toplevel before anything else and
    works only there, so a second lookup on the same string would be a dead
    branch dressed as a fallback."""
    projects = config.get("projects") if isinstance(config, dict) else None
    projects = projects if isinstance(projects, dict) else {}
    entry = projects.get(root)
    return entry if isinstance(entry, dict) else None


def _armed_keys(entry):
    """`[(key, url)]` for every Fairmind-named server in `entry` that is not
    disabled — the set whose SIZE is trap 2. One armed key is the ordinary
    case; two pointing at different hosts means the sweep will deliver under
    whichever one `fairmind_delivery_target` reaches first, and that is not a
    choice anyone made."""
    servers = entry.get("mcpServers") if isinstance(entry, dict) else None
    if not isinstance(servers, dict):
        return []
    disabled = entry.get("disabledMcpServers")
    if disabled is not None and not isinstance(disabled, list):
        # FAIL CLOSED, matching `fairmind_configured` (N1) rather than
        # `fairmind_delivery_target`. The two shipped resolvers differ here and
        # this command reports what the GATE does — so a malformed guard, under
        # which the gate captures nothing, must not be reported as armed. That
        # is precisely the silent misconfiguration this command exists to name.
        return []
    disabled = disabled or []
    armed = []
    for key in S._fairmind_keys(servers):
        if key in disabled:
            continue
        srv = servers.get(key)
        url = srv.get("url") if isinstance(srv, dict) else None
        armed.append((key, url if isinstance(url, str) else None))
    return armed


def _identities_differ(entry, armed):
    """Whether the armed keys are scoped to different tenants or projects.

    Unverified decode, display-and-diagnosis only, exactly like every other
    claim this command reads: what is being asked is not "is this token
    genuine" but "do these two tokens disagree about who they are for", and a
    forged claim can only make this command MORE cautious."""
    servers = entry.get("mcpServers") if isinstance(entry, dict) else {}
    servers = servers if isinstance(servers, dict) else {}
    identities = set()
    for key, _url in armed:
        srv = servers.get(key)
        headers = srv.get("headers") if isinstance(srv, dict) else None
        auth = headers.get("Authorization") if isinstance(headers, dict) else None
        claims = _plugin_policy.claims_from_bearer(auth)
        identities.add((claims.get("company"),
                        claims.get("projectId") or claims.get("project_id")))
    return len(identities) > 1


def _host_of(url):
    # The try/except earns its keep — `urlsplit` raises on a malformed IPv6
    # literal or a non-numeric port — but the import does not belong inside it.
    try:
        return urlsplit(url).netloc or None
    except ValueError:
        return None


def _user_global_fairmind(config):
    """Fairmind-named keys in the USER-GLOBAL `mcpServers`. They do not arm and
    must not — a global entry would make every repository on this machine a
    tenant of one company (plan V7) — but a developer who pasted the snippet
    there sees a working MCP in chat and a plugin that captures nothing, so the
    absence of an explanation is the whole defect."""
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    return S._fairmind_keys(servers)


def _committed_mcp_json(toplevel):
    """Fairmind-named keys in a team-committed `.mcp.json`. Reported, never
    counted: `fairmind_configured`'s secondary path arms one only when THIS
    project entry approved it explicitly, so a fresh clone never starts
    capturing on someone else's committed key."""
    if not toplevel:
        return []
    mcp = S._read_json(os.path.join(toplevel, ".mcp.json"))
    servers = mcp.get("mcpServers") if isinstance(mcp, dict) else None
    return S._fairmind_keys(servers)


def _write_scopes(claims):
    """The `scope` claim, normalized into a list — mirroring, shape for shape,
    the server's own `enforce_write_scope` (the project-context service
    `app/mcp/utils.py`): a str is space-split, a real collection is taken
    as-is, and anything else (a stray non-string/non-collection claim, or no
    claim at all — every key minted before the scope claim existed) is empty
    scope. A key this command calls write-capable must be one the write-back
    door also accepts, so the two checks have to fail closed the same way."""
    raw = claims.get("scope")
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
    return []


def _describe_expiry(exp, report):
    """Print `exp` and classify it. Returns True when the key is still valid.

    An expired key is a trap rather than a warning because every door below
    answers 401 for it, and "401" is not a sentence a developer can act on."""
    if not isinstance(exp, (int, float)):
        report.warn("the key carries no expiry claim — cannot say when it stops working")
        return True
    when = datetime.fromtimestamp(exp, tz=timezone.utc)
    stamp = when.strftime("%Y-%m-%d")
    remaining = (when - datetime.now(timezone.utc)).days
    if remaining < 0:
        report.trap(
            f"the project key EXPIRED on {stamp}. It cannot be renewed: mint a new "
            "one in Studio -> your avatar -> Developer, and replace the entry")
        return False
    if remaining <= _EXPIRY_WARNING_DAYS:
        report.warn(f"the project key expires on {stamp} ({remaining} days) and cannot "
                    "be renewed — mint a replacement before it does")
    else:
        report.ok(f"key valid until {stamp} ({remaining} days)")
    return True


def _how_to_configure(report, config=None, cwd=None):
    """The four things to do, in order. Printed instead of performed — see the
    module docstring on why this command never writes `~/.claude.json`."""
    report.info("")
    projects = config.get("projects") if isinstance(config, dict) else None
    shown = 0
    if isinstance(projects, dict):
        for root, entry in sorted(projects.items()):
            if root == cwd or not isinstance(entry, dict):
                continue
            for key, url in _armed_keys(entry):
                srv = entry["mcpServers"][key]
                if not isinstance(srv, dict):
                    continue
                headers = srv.get("headers") or {}
                claims = _plugin_policy.claims_from_bearer(
                    headers.get("Authorization") if isinstance(headers, dict) else None)
                exp = claims.get("exp")
                try:
                    parsed = urlsplit(url or "")
                    usable = (parsed.scheme == "https" and parsed.hostname and
                              not parsed.username and not parsed.password)
                except ValueError:
                    usable = False
                if (not usable or not isinstance(claims.get("company"), str)
                        or not isinstance(exp, (int, float)) or isinstance(exp, bool)
                        or not (time.time() < exp < 253402300800)):
                    continue
                project = claims.get("projectId") or claims.get("project_id")
                scope = f"project {project}" if project else "company-scoped; select a project at bind time"
                report.info(f"Existing configuration candidate: projects[{root!r}].mcpServers[{key!r}]")
                report.info(f"  Claims company {claims['company']}; {scope}; host {parsed.hostname}.")
                report.info("  Verify that company, environment and project scope match this checkout. "
                            "Claims are not authentication proof. Copy that entry locally if appropriate; "
                            "do not print its Authorization header or use a global scope.")
                shown += 1
                if shown >= 5:
                    break
            if shown >= 5:
                break
    report.info("  Studio fallback (first checkout, or no matching configuration):")
    report.info("  To connect this checkout:")
    report.info("    1. In Studio, open your avatar -> Developer, and select this project.")
    report.info("    2. Copy the project API key and the Claude Code MCP snippet.")
    report.info("    3. From THIS directory (the entry must be per-project, not global):")
    report.info("         claude mcp add-json --scope local Fairmind '<the snippet>'")
    report.info("       `--scope local` writes projects[<this repo>] in ~/.claude.json.")
    report.info("       `--scope project` would commit the key into .mcp.json — do not.")
    report.info("    4. Re-run /fairmind-connect.")


def step_config(cwd, toplevel, report):
    """Returns `(endpoint, token, claims)`; `endpoint` is None when nothing armed."""
    report.head("Configuration")
    config = _claude_json()
    entry = _project_entry(config, cwd)
    globals_ = _user_global_fairmind(config)

    if entry is None:
        report.trap("no per-project entry for this checkout in ~/.claude.json — the "
                    "plugin is not connected to any Fairmind project here")
        if globals_:
            report.info(f"a USER-GLOBAL Fairmind entry exists ({', '.join(globals_)}). It is "
                        "ignored by design: a global entry would make every repository on "
                        "this machine a tenant of one company. The entry must be per-project.")
        _how_to_configure(report, config, cwd)
        return None, None, {}

    armed = _armed_keys(entry)
    if not armed:
        committed = _committed_mcp_json(toplevel)
        guard = entry.get("disabledMcpServers")
        if guard is not None and not isinstance(guard, list):
            report.trap(
                "this checkout's `disabledMcpServers` is not a list, so nothing can verify "
                "which servers are enabled — capture fails closed and nothing is collected "
                "here. Fix that key in ~/.claude.json (it is a list of server names) and "
                "re-run.")
        else:
            report.trap("this checkout has a project entry, but no enabled Fairmind MCP "
                        "server in it (a `fairmind…`-named key that is not in "
                        "disabledMcpServers)")
        if committed:
            report.info(f".mcp.json in this repository declares {', '.join(committed)}, but a "
                        "committed key arms only once THIS project approves it explicitly "
                        "(enabledMcpjsonServers / enableAllProjectMcpServers) — a fresh "
                        "clone must never start capturing unapproved.")
        if globals_:
            report.info(f"a USER-GLOBAL Fairmind entry exists ({', '.join(globals_)}) and is "
                        "ignored by design — the entry must be per-project.")
        _how_to_configure(report, config, cwd)
        return None, None, {}

    report.ok("per-project entry found for this repository")
    hosts = {_host_of(url) for _key, url in armed if url}
    if len(armed) > 1 and len(hosts) > 1:
        listing = ", ".join(f"{key} -> {_host_of(url) or 'no url'}" for key, url in armed)
        report.trap(
            "two or more Fairmind keys are armed for this checkout and they point at "
            f"DIFFERENT hosts ({listing}). Both arm — the plugin's key match is anchored "
            "on the name, not the host — so which backend receives this repository's rows "
            "is decided by ordering, not by you. Studio names the key after the host it "
            "was minted on (Fairmind on prod, Fairmind-dev on dev): remove the one that "
            "does not belong to this project.")
    elif len(armed) > 1 and _identities_differ(entry, armed):
        # SAME HOST IS NOT THE SAME ANSWER. `fairmind_delivery_target` takes the
        # first key that resolves, in dictionary order, so two keys minted for
        # different projects — or different companies — on one host leave the
        # choice to whichever was written first. That is the two-host trap with
        # the host removed, and it is not a warning.
        report.trap(
            f"{len(armed)} Fairmind keys are armed for this checkout on the same host "
            f"({', '.join(k for k, _ in armed)}) and their keys are scoped to different "
            "projects or companies. Which one this repository's rows are attributed to is "
            "decided by their order in the file, not by you: remove the one that does not "
            "belong to this project.")
    elif len(armed) > 1:
        report.warn(f"{len(armed)} Fairmind keys are armed ({', '.join(k for k, _ in armed)}), "
                    "all pointing at the same host and scoped alike")
    else:
        report.ok(f"one Fairmind MCP armed: {armed[0][0]} -> {_host_of(armed[0][1]) or 'no url'}")
    if globals_:
        report.info(f"(a user-global Fairmind entry also exists: {', '.join(globals_)}. It is "
                    "ignored by design and is not what arms this checkout.)")

    endpoint, token = S.fairmind_delivery_target(cwd, toplevel)
    if not endpoint or not token:
        report.trap("the armed Fairmind entry carries no usable url + Authorization header, "
                    "so no door can be reached. Re-copy the snippet from Studio -> your "
                    "avatar -> Developer.")
        return None, None, {}

    claims = _plugin_policy.claims_from_bearer(token)
    company = claims.get("company")
    project_claim = claims.get("projectId") or claims.get("project_id")
    report.ok(f"company {company}" if company else "the key carries no company claim")
    if project_claim:
        report.ok(f"key scoped to project {project_claim}")
    else:
        report.info("the key carries no projectId claim — the project must be named "
                    "(--project <id>)")
    scopes = _write_scopes(claims)
    if "write" not in scopes and "admin" not in scopes:
        # A WARNING, not a trap: reads still work with this key, and the
        # command exists to say what will not — not to refuse a connect that
        # is legitimate for read-only use.
        report.warn("this key carries no write/admin scope — Studio write-back will "
                    "be refused (403: Access denied: write scope required). "
                    "Regenerate it in Studio -> your avatar -> Developer with a "
                    "project selected.")
    _describe_expiry(claims.get("exp"), report)
    return endpoint, token, claims


# --------------------------------------------------------------------------- #
# Transport. One request helper for both doors.
# --------------------------------------------------------------------------- #

class DoorError(Exception):
    """A non-2xx answer, or no answer at all. `status` is 0 when the request
    never reached a server, which is the case a caller must treat as retryable
    rather than as a refusal."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _call(url, token, body=None):
    """GET (body None) or POST `body` as JSON, bounded, refusing redirects.

    The opener is `ambient_outbox._OPENER` — the shipped one that raises on a
    3xx instead of following it. Reused rather than rebuilt because an endpoint
    able to answer 302 could otherwise walk this bearer to a host of its
    choosing, and a second opener would be a second place to get that wrong."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, method=("POST" if data is not None else "GET"), headers=headers)
    try:
        with ambient_outbox._OPENER.open(request, timeout=_TIMEOUT_S) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES)
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read(_MAX_RESPONSE_BYTES)
        raise DoorError(exc.code, _detail_of(raw, exc.code))
    except Exception as exc:  # DNS, refused, timeout, a refused redirect
        # THE EXCEPTION TEXT IS A CONTENT CHANNEL. Verified: a token carrying a
        # stray newline makes http.client raise `Invalid header value b'Bearer
        # <the whole token>'`, so quoting `exc` verbatim prints the credential
        # into a report the command body relays. The type name plus a redacted
        # message is what a caller can act on; the token is never part of that.
        raise DoorError(0, f"{type(exc).__name__}: {_redact(str(exc), token)}")
    if status != 200:
        raise DoorError(status, _detail_of(raw, status))
    try:
        answer = json.loads(raw)
    except ValueError:
        raise DoorError(status, "the door answered something that is not JSON")
    if not isinstance(answer, dict):
        raise DoorError(status, "the door answered JSON that is not an object")
    return answer


def _redact(text, token):
    """`text` with the bearer removed, whatever shape it arrived in.

    THREE PASSES, because a literal `str.replace` of the token is not enough
    and that was measured rather than guessed. `http.client` raises
    `Invalid header value b'Bearer <token>'` — a BYTES REPR, in which the
    newline that caused the error is escaped to a backslash and an `n`, so the
    token as this process holds it does not appear in the message at all. So:
    the `Bearer …` spelling, the token itself, and finally every
    whitespace-separated fragment of it, which survives any escaping scheme
    because the fragments contain no whitespace to escape.

    The invariant is stated as "no part of the Authorization header value
    reaches the output", which is both stronger and easier to check than "no
    secret does" — the header may carry more than the secret, and none of it is
    the caller's to see. Lines are redacted as well as whitespace fragments,
    longest first so a whole line goes before its own pieces stop matching. The
    short floor only keeps a one- or two-character piece from blanking out
    ordinary punctuation; a falsy token short-circuits, since otherwise every
    empty substring would match."""
    if not token:
        return text
    text = text.replace("Bearer " + token, "<redacted>").replace(token, "<redacted>")
    # Longest first, so a whole line is replaced before its own fragments are —
    # otherwise the line no longer matches and the leftovers survive.
    pieces = sorted(set(token.splitlines()) | set(token.split()), key=len, reverse=True)
    for piece in pieces:
        piece = piece.strip()
        if len(piece) >= 3:
            text = text.replace(piece, "<redacted>")
    return text


def _detail_of(raw, status):
    """FastAPI puts the refusal in `detail`; anything else is reported as the
    status alone. The body is NEVER echoed verbatim on a shape we do not
    recognize — an error body is a channel, and a door that answered something
    unexpected is exactly the one whose bytes should not be relayed."""
    try:
        body = json.loads(raw)
    except ValueError:
        return f"HTTP {status}"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return detail
    # A 422 answers with a LIST of per-field errors, not a string — the shape a
    # contract drift actually arrives in, and the one worth naming a field for.
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            where = ".".join(str(x) for x in item.get("loc") or [])
            parts.append(f"{where}: {item.get('msg')}" if where else str(item.get("msg")))
        if parts:
            return "; ".join(parts[:5])
    return f"HTTP {status}"


# --------------------------------------------------------------------------- #
# Step 2 — the tenant pre-flight.
# --------------------------------------------------------------------------- #

def step_tenant(endpoint, token, company, report):
    """Returns True when the connect may continue.

    This door is deliberately NOT behind the ambient kill switch, server-side,
    and that is the whole reason to call it: a company that was never
    provisioned is refused by every WRITE door with a 503 that names an
    internal key, and this is the one door that can tell it so."""
    report.head("Tenant")
    url = S._derive_insights_endpoint(endpoint, _TENANT_STATUS_PATH)
    try:
        answer = _call(url, token)
    except DoorError as exc:
        if exc.status == 401:
            report.trap(f"the project key was refused ({exc.detail}) — it is expired or "
                        "not valid for this backend")
            return False
        if exc.status == 0:
            report.trap(f"the platform did not answer ({exc.detail}) — network, VPN or the "
                        "service being down. Nothing was changed; re-run when it answers.")
            raise
        if exc.status == 403 and not exc.detail.startswith(("Access denied", "Insufficient permissions. Required:")):
            report.trap("the configured endpoint returned an unrecognized 403; tenant status "
                        "and key validity are unknown. Verify the MCP endpoint before selecting "
                        "a project or changing roles.")
            return False
        report.warn(f"tenant status unavailable ({exc.detail}) — continuing; the bind door "
                    "below is the real test")
        return True

    ambient = answer.get("ambient")
    if ambient == "provisioned":
        report.ok("ambient capture is provisioned for this company")
    elif ambient == "muted":
        report.warn("ambient capture is deliberately MUTED for this company — the binding "
                    "below still applies, but session digests are not being collected")
    elif ambient == "unprovisioned":
        report.trap(
            f"this company ({company or 'unknown'}) has never been provisioned for capture, "
            "so every write door refuses it. Ask your FairMind operator to provision the "
            "tenant; binding before that would record a link nothing can use.")
        return False
    else:
        report.warn("the configuration service did not answer, so the tenant state is "
                    "UNKNOWN. This is NOT a sign that your company was never onboarded — "
                    "do not go and re-provision it. Continuing.")
    graph = answer.get("graph")
    if graph == "ready":
        report.ok("the code graph is reachable")
    elif graph == "unavailable":
        report.warn("the code graph is not configured for this company — decisions will "
                    "still be recorded, but they will draw no edges onto your functions")
    telemetry = answer.get("telemetry")
    if telemetry == "absent":
        report.warn("telemetry is not configured for this company")
    return True


# --------------------------------------------------------------------------- #
# Step 3 — the bind.
# --------------------------------------------------------------------------- #

def _git(cwd, *args):
    try:
        proc = subprocess.run(["git", "-C", cwd, *args],
                              capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _current_branch(cwd):
    """The checkout's branch, or None on a detached HEAD. None is honest: a
    detached HEAD is not on a branch, and inventing one would pick a catalog
    row on a guess."""
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return None if branch in (None, "HEAD") else branch


#: Schemes a git remote may carry that name a HOSTED repository. `file:` is
#: deliberately absent: it names a path, and `file://localhost/srv/git/x`
#: normalizes to a plausible-looking `https://localhost/srv/git/x`.
_HOSTED_SCHEMES = ("https://", "http://", "ssh://", "git://", "git+ssh://")


def _is_local_path(raw_remote):
    """Whether `origin` names a path on this machine rather than a hosted
    repository. CLASSIFIED ON THE RAW REMOTE, BEFORE NORMALIZATION, and that is
    the whole correction.

    Inferring locality from the NORMALIZED host cannot work, because
    `normalize_git_remote` prefixes `https://` unconditionally and a path is
    then indistinguishable from a host by shape. Measured, both directions:

        repo.git          -> https://repo.git          "has a dot" => hosted  ✗
        foo.bar/repo.git  -> https://foo.bar/repo      "has a dot" => hosted  ✗
        git@gitlab:g/p    -> https://gitlab/g/p        "no dot"    => local   ✗

    The first two are relative paths that would have been POSTed, carrying this
    machine's directory layout (JC32); the third is an ordinary internal forge
    — a single-label hostname is normal on a company network — that would have
    been refused. A hostname heuristic gets both wrong, so there is none: a
    remote is local when it LOOKS like a path, which git itself decides the
    same way.

    Local: absolute (`/srv/x.git`), explicitly relative (`./x`, `../x`), a home
    path (`~/x`), a `file:` URL, or anything with neither a hosted scheme nor
    scp-like `host:path` syntax — which is what a bare `repo.git` is."""
    raw = (raw_remote or "").strip()
    if not raw:
        return True
    lowered = raw.lower()
    if lowered.startswith("file://"):
        return True
    if raw.startswith(("/", "./", "../", "~")):
        return True
    if lowered.startswith(_HOSTED_SCHEMES):
        return False
    # No scheme: git reads `host:path` as scp-like and everything else as a
    # path. `_SCP_LIKE_RE` is the normalizer's OWN matcher, reused so the two
    # cannot disagree about what scp-like means. A Windows drive letter
    # (`C:\repo`) matches it too, so it is excluded by name.
    match = audit_run_meta._SCP_LIKE_RE.match(raw)
    if not match:
        return True
    host = match.group(1)
    return len(host) == 1 and host.isalpha()


def _has_authority(normalized):
    """Whether the normalized remote ended up with a usable authority.

    A second, independent condition rather than a restatement of the first: it
    catches whatever `_is_local_path` let through that still normalized to
    something with no host to send (`https:///…`, `https://../…`)."""
    host = (_host_of(normalized) or "").split("@")[-1]
    if host.startswith("[") and "]" in host:          # IPv6 literal
        host = host[1:host.index("]")]
    else:
        host = host.split(":")[0]
    return bool(host) and host.strip(".") != "" and not host.startswith(".")


def step_bind(cwd, endpoint, token, project_id, branch, report):
    """POST the binding. Returns the answer dict, or raises DoorError."""
    report.head("Repository")
    raw_remote = _git(cwd, "remote", "get-url", "origin")
    if not raw_remote:
        report.trap("this checkout has no `origin` remote, so there is no url to match "
                    "against the catalog. Add one (git remote add origin <url>) and re-run.")
        return None
    remote = audit_run_meta.normalize_git_remote(raw_remote)
    usable = bool(remote) and _has_authority(remote)
    if _is_local_path(raw_remote) or not usable:
        # THE RAW REMOTE IS NOT ECHOED. It can carry `user:token@`, and this
        # report is relayed into a transcript verbatim — an error message is a
        # content channel like any other. The normalized form has had
        # credentials stripped by `normalize_git_remote`; when even that is
        # unusable, the shape is described instead of quoted.
        shown = remote if usable else "a path on this machine"
        report.trap(
            f"`origin` is a local path ({shown}), not a hosted repository. The "
            "catalog holds the url your code was ingested from; a path would match "
            "nothing and would carry this machine's directory layout to the server. "
            "Point `origin` at the hosted repository and re-run.")
        return None
    report.ok(f"origin {remote}")
    report.ok(f"branch {branch}" if branch else "detached HEAD — no branch to match on")

    tenancy = S.resolve_tenancy(cwd)
    body = {"gitRemote": remote}
    if project_id:
        body["projectId"] = project_id
    if branch:
        body["branch"] = branch
    if tenancy:
        # SENT ON EVERY BIND, and it is the half of the retroactive promise that
        # nothing else can deliver. The ambient lane's rows carry this opaque
        # hash and NOTHING ELSE — no name, no path, no project — so the server
        # can only join an already-captured session to a repository through a
        # binding record keyed on it. Omit it and sessions recorded before the
        # connect stay unjoinable for ever, while audit and decision rows are
        # reconciled; the developer would have been told "connected" and half
        # of it would be false. Nothing new goes on the wire and no stored row
        # is rewritten: the hash was already there, the join is what is new.
        body["tenancy"] = tenancy

    url = S._derive_insights_endpoint(endpoint, _BIND_PATH)
    return _call(url, token, body)


def report_bind_failure(exc, report):
    """Map a refusal onto the one thing the developer can do about it."""
    if exc.status == 404:
        report.trap(
            "this repository is not in the catalog for that project — nothing has "
            "ingested it yet. In Studio, open the project -> repositories and add this "
            "repository (and this branch), then re-run.")
    elif exc.status == 403 and not exc.detail.startswith(("Access denied", "Insufficient permissions. Required:")):
        report.trap("the configured endpoint returned an unrecognized 403. Verify the MCP "
                    "endpoint and authentication; this response does not establish a role refusal.")
    elif exc.status == 403 and "bound to another project" in exc.detail:
        # Refused because the checkout is bound under a DIFFERENT project; the role
        # that matters is on that project, not the one being bound.
        report.trap(
            "this checkout is already bound to another project, and moving it needs the "
            "EDITOR role on that other project — ask an admin of that other project, "
            "not of the one you are binding to.")
    elif exc.status == 403 and "already bound elsewhere" in exc.detail:
        # The server will not move this binding at all, so no role changes the outcome.
        report.trap(
            "this checkout is already bound elsewhere and this bind cannot move it. "
            "There is nothing to change on your side today: no role you hold or request "
            "on the project you are binding to will lift it.")
    elif exc.status == 403:
        report.trap(
            f"you may not bind within that project ({exc.detail}). Binding changes what "
            "every future row in the project is keyed on, so it needs the EDITOR role — "
            "ask a project admin.")
    elif exc.status == 409:
        report.trap(f"{exc.detail}")
        candidates = _candidate_branches(exc.detail)
        if candidates:
            report.info("The catalog holds one row per (url, branch) and none of them is on "
                        "your branch, so the door refuses to guess — binding the wrong row "
                        "looks exactly like not being bound at all. Re-run naming one:")
            for candidate in candidates:
                if candidate == "None":
                    report.info("    (one candidate row carries no branch at all)")
                else:
                    # QUOTED: git allows spaces and shell metacharacters in a
                    # branch name, and an unquoted suggestion is a line that
                    # does something other than what it appears to.
                    report.info(f"    /fairmind-connect --branch {shlex.quote(candidate)}")
    elif exc.status == 400:
        report.trap(exc.detail)
    elif exc.status == 422:
        report.trap(
            f"the door rejected the request shape ({exc.detail}). That is a contract drift "
            "between this plugin and the platform, not something you can fix here — "
            "report it with the plugin version.")
    elif exc.status == 401:
        report.trap(f"the project key was refused ({exc.detail}) — it is expired or not "
                    "valid for this backend")
    else:
        report.trap(f"the bind door refused: {exc.detail}")


def _candidate_branches(detail):
    """The branch names a 409 offers. The door names them in prose — there is
    no structured field — so this parses the one sentence it is documented to
    produce and yields nothing at all when it does not match, rather than
    guessing at a shape."""
    marker = "re-run naming one of: "
    index = detail.find(marker)
    if index < 0:
        return []
    tail = detail[index + len(marker):].strip()
    return [part.strip() for part in tail.split(",") if part.strip()]


# --------------------------------------------------------------------------- #
# Step 4 — record the binding.
# --------------------------------------------------------------------------- #

#: The fields a `fm-insights.repo-binding/1` answer must carry for this command
#: to have achieved anything. Checked because a 200 is not a contract: an empty
#: object would otherwise be written as null ids, reported as "Bound" and
#: exited 0 on, leaving `--insights-status` to say NOT BOUND one command later.
_REQUIRED_BINDING_FIELDS = ("repository_id", "project_id")


def validate_binding(answer, project_id, report):
    """Whether the door's 200 really is a binding. Returns True when it is."""
    missing = [f for f in _REQUIRED_BINDING_FIELDS
               if not isinstance(answer.get(f), str) or not answer[f].strip()]
    if missing:
        report.trap(
            f"the bind door answered 200 but named no {', '.join(missing)}. Nothing was "
            "recorded locally — this is a contract drift between the plugin and the "
            "platform, so report it with the plugin version rather than re-running.")
        return False
    # THE ANSWER MUST BE ABOUT THE PROJECT THAT WAS ASKED FOR. The server
    # resolves the project from the body or the token claim and is the
    # authority on it, so a mismatch is not something to correct silently: it
    # means the id about to be written into every future payload belongs to a
    # project the developer did not name.
    if answer["project_id"] != project_id:
        report.warn(
            f"you asked to bind within {project_id} and the platform bound within "
            f"{answer['project_id']} ({answer.get('project_name') or 'unnamed'}). That is "
            "the project every row from this checkout will now be keyed on.")
    return True


def _load_context(cwd, report, suffix=""):
    """`(ok, ctx)` for `.fairmind/active-context.json` under `cwd`.

    ONE reader for the two moments this command needs it — the pre-network
    refusal check and the read-merge-write after the bind — because they were
    two copies of the same four branches and the second copy had lost the
    `OSError` one, which is exactly the post-bind failure the first exists to
    prevent. `suffix` carries the only thing that legitimately differs: before
    the bind the report can promise that nothing was sent, and after it that
    promise would be false."""
    path = _binding.context_path(cwd)
    if not os.path.isfile(path):
        return True, None
    try:
        with open(path, encoding="utf-8") as handle:
            ctx = json.load(handle)
    except ValueError as exc:
        report.trap(f"{path} is not valid JSON ({exc}); refusing to overwrite it — fix "
                    f"or remove it by hand, then re-run.{suffix}")
        return False, None
    except OSError as exc:
        report.trap(f"{path} cannot be read ({exc.strerror}).{suffix}")
        return False, None
    if not isinstance(ctx, dict):
        report.trap(f"{path} is not a JSON object; refusing to overwrite it.{suffix}")
        return False, None
    return True, ctx


def preflight_context(cwd, report):
    """Whether `.fairmind/active-context.json` can be written to at all.

    RUN BEFORE THE BIND, not after. The bind is a WRITE on the platform — it
    stores a binding record and re-keys rows recorded earlier — so discovering
    at the end that the local half cannot be recorded leaves the two sides
    disagreeing: the server bound this checkout, the developer was told it
    failed, and nothing local says the reconciliation already happened."""
    ok, _ctx = _load_context(cwd, report, suffix=" Nothing was sent.")
    return ok


def write_binding(cwd, answer, report):
    """Read-merge-write `.fairmind/active-context.json`, preserving every key
    it already carries — including the ones this module knows nothing about.

    `project` is NOT touched. It holds the folder name, it is what a developer
    reads in a banner, and after this the lanes no longer send it as identity
    (`_PROJECT_KEYS` prefers the bound `project_id`), so it costs nothing to
    keep and would cost a human label to overwrite.

    The refusal branches below are reachable only when the file changed between
    `preflight_context` and here — a real window, since a bind round trip sits
    between them."""
    ok, ctx = _load_context(cwd, report)
    if not ok:
        return False
    if ctx is None:
        ctx = {
            "mode": _BOOTSTRAP_MODE,
            "project": os.path.basename(os.path.abspath(cwd)),
            "base_path": _BOOTSTRAP_BASE_PATH,
        }

    ctx["fairmind"] = "configured"
    ctx[_binding.PROJECT_ID] = answer.get("project_id")
    ctx[_binding.REPOSITORY_ID] = answer.get("repository_id")
    ctx[_binding.REPOSITORY_NAME] = answer.get("name")
    ctx[_binding.REPOSITORY_URL] = answer.get("url")
    # THE BRANCH IS NOT DECORATION. A catalog `_id` IS a (url, branch) row, so
    # this is the only local record of WHICH row was bound — and the judge's
    # rubric inherits the same axis, meaning a rubric compiled from `main` and
    # a request from `develop` carry different ids. Storing it is what lets a
    # later run notice the checkout moved.
    ctx[_binding.REPOSITORY_BRANCH] = answer.get("branch")
    ctx[_binding.BOUND_AT] = datetime.now(timezone.utc).replace(
        microsecond=0).isoformat()

    makedirs_ignored(os.path.dirname(_binding.context_path(cwd)))
    _atomic_write_json(_binding.context_path(cwd), ctx)
    return True


def report_binding(answer, checkout_branch, report):
    report.head("Bound")
    name = answer.get("name") or "?"
    project_name = answer.get("project_name") or answer.get("project_id") or "?"
    report.ok(f"{project_name} / {name} ({answer.get('repository_id')}) "
              f"on {answer.get('branch')}")
    if answer.get("created") is False:
        report.info("this checkout was already bound; the binding was refreshed in place")
    row_branch = answer.get("branch")
    if checkout_branch and row_branch and row_branch != checkout_branch:
        report.warn(
            f"you are on {checkout_branch} but the catalog row bound is {row_branch} — it is "
            f"the only row ingested for this url. Rows from this checkout will be keyed on "
            f"the {row_branch} row, and a judge rubric compiled for another branch will not "
            "match. Ingest this branch if that matters.")
    if answer.get("bound_tenancy"):
        report.ok("this checkout's session digests are now joinable to the repository "
                  "(the hash on the wire is unchanged and no stored row was rewritten)")
    _report_reconciled(answer.get("reconciled"), report)


def _report_reconciled(reconciled, report):
    """Print what the bind re-keyed.

    `reconciled` IS A UNION of four shapes — the full record, a short-circuit
    with no `sources`, a refusal carrying only `message`, and an empty object —
    so every read here goes through `.get`. Indexing it would turn a healthy
    bind into a traceback on the branch that says "nothing needed re-keying"."""
    if not isinstance(reconciled, dict) or not reconciled:
        return
    if reconciled.get("message"):
        report.warn(f"earlier rows were not re-keyed: {reconciled['message']}")
        return
    migrated = reconciled.get("migrated")
    migrated = migrated if isinstance(migrated, dict) else {}
    audits = migrated.get("auditRuns") or 0
    decisions = migrated.get("decisions") or 0
    if audits or decisions:
        report.ok(f"re-keyed onto the catalog id: {audits} audit run(s), "
                  f"{decisions} decision batch(es) recorded before this connect")
        report.info("Two limits, stated rather than implied: rows recorded from a checkout "
                    "with no origin remote cannot be reached, and re-keying does not draw "
                    "the graph edges those older decisions never got.")
    graph = reconciled.get("graph")
    held_back = reconciled.get("heldBack")
    held_back = held_back if isinstance(held_back, dict) else {}
    held = held_back.get("auditRuns")
    counted = isinstance(held, int) and not isinstance(held, bool) and held >= 0
    held = held if counted else 0
    capped = bool(held_back.get("capped"))
    if not (audits or decisions) and not (graph == "shared" or held):
        report.info("nothing recorded before this connect needed re-keying")
    # `held` is keyed on its own, not on `graph == "shared"`: across several
    # spellings the server reports the FIRST status that is not 'merged', so a
    # shared spelling's count can arrive under 'unverified'.
    if held:
        count = f"at least {held}" if capped else str(held)
        report.warn(
            f"{count} audit run(s) stay on the old address until the operator backfill; "
            "this repository address is shared with another project (or with rows that "
            "name no project), so only this project's own rows were re-keyed")
    elif graph == "shared" and counted:
        report.warn("this repository address is shared with another project, so only this "
                    "project's own rows were re-keyed; none of its audit runs were held back")
    elif graph == "shared":
        report.warn("this repository address is shared with another project, so audit "
                    "runs recorded on it are held back until the operator backfill")
    if graph not in (None, "merged", "skipped", "shared"):
        report.warn(f"the graph anchor reported '{graph}'")


# --------------------------------------------------------------------------- #
# Step 5 — what leaves the machine.
# --------------------------------------------------------------------------- #

def report_lanes(answer, report):
    """The per-lane disclosure, printed on every successful connect.

    It is here rather than in a document because this is the moment the answer
    CHANGES: before the bind the named lanes carried this repository's folder
    name, after it they carry a catalog id, and a developer is owed the new
    sentence at the moment the old one stops being true."""
    repository_id = answer.get("repository_id")
    report.head("What leaves this machine, per lane")
    report.info("  session digests   an opaque one-way hash of this checkout's location, "
                "and nothing else. Unchanged by this connect — what changed is that the "
                "platform can now join that hash to the repository you just bound.")
    report.info(f"  agent decisions   {repository_id}, your origin url, the files and "
                "functions a decision names, and its rationale.")
    report.info(f"  harness audits    {repository_id}, your origin url, the commit sha and "
                "the criteria verdicts.")
    report.info("  loop stats        the project id, task and loop references, token counts "
                "per agent role. No repository name, no origin url.")
    report.info("  judge reviews     the diff under review, and the repository id once the "
                "installed judge client accepts it (until then, this directory's name).")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def run(cwd, project_arg, branch_arg):
    report = Report()
    toplevel, _common = S._git_rev_parse(cwd)
    if not toplevel:
        sys.stderr.write("fairmind_connect: not a git work tree — a Fairmind consumer is "
                         "always a git repository\n")
        return 1
    cwd = toplevel

    endpoint, token, claims = step_config(cwd, toplevel, report)
    # A TRAP ENDS THE RUN, even when the step still produced a usable endpoint.
    # Traps accumulate WITHIN a step — a developer who fixes one and re-runs to
    # discover the next has paid for the tool's convenience — but never ACROSS
    # one: a configuration this command has just called wrong is not a
    # configuration to start binding a repository through. Two keys arming two
    # different backends is exactly that case, and it has a usable endpoint by
    # construction.
    if not endpoint or report.traps:
        report.flush()
        return 1

    try:
        if not step_tenant(endpoint, token, claims.get("company"), report):
            report.flush()
            return 1
    except DoorError:
        report.flush()
        return 4

    claim_project = claims.get("projectId") or claims.get("project_id")
    if project_arg and claim_project and project_arg != claim_project:
        # THE KEY, NOT THE ARGUMENT, DECIDES WHAT CAN BE WRITTEN. A --project
        # override this key is not scoped to is not "bind here instead" — the
        # server enforces per-project write access from the VERIFIED claim,
        # never from what the caller named, so a bind under the override would
        # be a local record for a repository this key cannot write in. Refused
        # here, before the bind, rather than surfaced later as the loop's
        # write-back reporting "not synced" with no visible cause.
        report.head("Project")
        report.trap(
            f"--project {project_arg} does not match this key's own project claim "
            f"({claim_project}). Binding would record project {project_arg} locally "
            "while every Studio write this key attempts is checked against "
            f"{claim_project} and refused — copy the key for {project_arg} from "
            "Studio -> your avatar -> Developer, or drop --project to bind within "
            f"the key's own project ({claim_project}).")
        report.flush()
        return 1

    project_id = project_arg or claim_project
    if not project_id:
        report.head("Project")
        report.info("this key is not scoped to a project, so the project to bind within "
                    "must be named: re-run with --project <id>.")
        report.flush()
        return 3

    # BEFORE THE BIND — see `preflight_context`.
    if not preflight_context(cwd, report):
        report.flush()
        return 1

    # RESOLVED ONCE, then bound and reported from the same value. Two
    # resolutions were two observations of the branch, and the one printed was
    # not necessarily the one sent.
    branch = branch_arg or _current_branch(cwd)

    try:
        answer = step_bind(cwd, endpoint, token, project_id, branch, report)
    except DoorError as exc:
        if exc.status in (0, 503):
            report.trap(f"the platform could not answer right now ({exc.detail}). Nothing "
                        "was changed; re-run in a moment.")
            report.flush()
            return 4
        report_bind_failure(exc, report)
        report.flush()
        return 1
    if answer is None:
        report.flush()
        return 1
    if not validate_binding(answer, project_id, report):
        report.flush()
        return 1

    if not write_binding(cwd, answer, report):
        report.flush()
        return 1
    report_binding(answer, branch, report)
    report_lanes(answer, report)
    report.flush()
    return 1 if report.traps else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fairmind_connect.py",
        description="Verify this checkout's Fairmind configuration and bind it to the "
                    "repository the platform ingested.")
    parser.add_argument("--project", default=None,
                        help="the project id to bind within (defaults to the key's own "
                             "projectId claim when it carries one)")
    parser.add_argument("--branch", default=None,
                        help="which catalog row to bind, when a url has several (defaults "
                             "to this checkout's current branch)")
    parser.add_argument("--cwd", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return run(args.cwd or os.getcwd(), args.project, args.branch)


if __name__ == "__main__":
    sys.exit(main())
