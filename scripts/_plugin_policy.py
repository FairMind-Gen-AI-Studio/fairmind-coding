#!/usr/bin/env python3
"""
_plugin_policy.py — the ONE shared precedence implementation for the central
per-project plugin policy (judge on/off, ambient_capture on/off).

Two consumers read this module and must never disagree: the ambient gate
(`_insights_session.evaluate_gate`'s central step) and the judge Stop hook
(the review hook). It therefore does NOT import `_insights_session` — the
judge hook imports this module on every Stop and must stay light; pulling the
whole session module in behind it would hand every Stop that module's import
cost for a three-key cache read.

The contract, in one sentence: `resolve_central` answers "on", "off", or None
(= unset) for a feature, and it answers None for EVERY ambiguous shape — a
missing cache, a stale cache, an unknown version, a value a typo produced.
Unset is not "off": it means the platform said nothing, so the local layer
(repo file, env) decides. The fail direction is deliberate and asymmetric with
the repo file's fail-closed `_config_disables`: this cache is plugin-owned
state, not company-authored config, and a corrupt plugin cache must not mute
(or force) anything a human can be held to.

Stdlib only, no network. All I/O lives in `read_cache`/`write_cache`;
`resolve_central` is a pure function over the cache dict so the whole truth
table is testable without a filesystem.
"""

import base64
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone

#: Version tag of the payload the server serves (GET /insights/v1/client-policy).
PAYLOAD_VERSION = "fm-plugin-policy/1"

#: Version tag of the on-disk cache envelope this module writes around it.
CACHE_VERSION = "fm-plugin-policy-cache/1"

#: 7 days. This is the OFFLINE-DEGRADATION window — how long a last-known
#: central force keeps binding while the platform is unreachable — NOT
#: convergence latency: every session start attempts a refresh, so a reachable
#: platform converges in one session regardless of this number.
POLICY_TTL_S = 604800

#: How far a cache's `fetched_at` may sit AHEAD of the reader's clock before
#: the envelope is "unreadable" rather than "fresh". A few minutes of skew
#: between the machine that stamped the cache and the one reading it is
#: ordinary; a timestamp further ahead than that is not a fetch that happened.
#: The bound matters because the arithmetic below is one-sided: a future
#: `fetched_at` makes the age NEGATIVE, so `age > ttl` answers "fresh" for as
#: long as the timestamp is ahead — an IMMORTAL force, the same failure a
#: non-finite `ttl_s` produces from the other end.
_MAX_CLOCK_SKEW_S = 300

#: The closed feature perimeter. A feature name outside this set resolves to
#: None like any other ambiguous input — extending the perimeter is an edit
#: here, never an inference from whatever keys a payload happens to carry.
#:
#: The last two are JC39's purpose ladder, and they are TWO BOOLEANS rather than
#: one three-valued `content_purpose` key on purpose: `POLICY_VALUES` below is
#: FEATURE-GLOBAL, and the two switch readers deliberately treat every non-None
#: answer as a force. A third value added there would reach `judge` and
#: `ambient_capture` too, which is a behaviour change to two shipped gates in
#: exchange for a tidier spelling of a third. Two booleans buy the same ladder
#: and touch neither.
FEATURES = frozenset({"judge", "ambient_capture", "brain",
                      "purpose_customer_only", "purpose_fairmind_training"})

#: The closed value vocabulary, matched EXACTLY. "ON", "true", 1 and True are
#: all None: a value a typo produced is not a decision anyone made.
POLICY_VALUES = frozenset({"on", "off"})

#: The one spelling of the repo-root config file, shared by every reader.
INSIGHTS_CONFIG_BASENAME = ".fairmind-insights.json"


def _data_dir():
    """The writable per-user data dir.

    A DELIBERATE HAND-MIRROR of `_insights_session.data_dir` (env
    `FAIRMIND_INSIGHTS_HOME` override, else ~/.fairmind) — importing it would
    drag the whole session module into the judge Stop hook, which this module
    exists to avoid. `tests/test_plugin_policy.py::
    test_the_data_dir_mirror_matches_the_session_modules` pins that the two
    resolve identically (house precedent:
    test_the_loop_state_lookup_matches_the_gate_engines)."""
    override = os.environ.get("FAIRMIND_INSIGHTS_HOME")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".fairmind")


def cache_path(toplevel):
    """Where the policy cache for the checkout rooted at `toplevel` lives.

    Same hashing idiom as the review hook's state-path idiom: sha256 of the
    realpath, first 32 hex chars, so two paths to one checkout share a cache
    and no customer path leaks into a filename."""
    key = hashlib.sha256(
        os.path.realpath(toplevel).encode("utf-8", "replace")).hexdigest()
    return os.path.join(_data_dir(), "insights", "policy", key[:32] + ".json")


def read_cache(path):
    """The cache dict at `path`, or None. Tolerant on purpose: a missing,
    unparseable, or non-dict file is the same answer as no cache at all —
    `resolve_central(feature, None, now)` is None (unset)."""
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _atomic_write_json(path, obj, *, prefix=".fm-json-", mode=0o600,
                       preserve_mode=False, indent=None, sort_keys=False,
                       ensure_makedirs=False, trailing_newline=False):
    """Write `obj` to `path` as JSON, atomically: mkstemp in the SAME directory
    + `os.replace`, so a concurrent reader never sees a truncated file and a
    crash leaves the previous contents standing (last-known-wins).

    ONE PARAMETRIZED WRITER for the two JSON files this feature owns — the
    plugin-owned policy cache here (`write_cache`) and the committable
    repo-root config over in `_insights_session._write_consent_config`. They
    differ only in formatting, temp-file prefix and mode; a second hand-rolled
    copy of the mkstemp/replace/cleanup dance is another place the atomicity
    can quietly stop holding, which is the whole reason this family
    (`loop_import._atomic_write_json`, `run_gate_checks._atomic_write_json`,
    `_loop_ledger._atomic_write_lines`) exists at all.

    `prefix` names the temp file, and it is a parameter rather than a constant
    because one of these directories is a customer's REPO ROOT: a leftover
    temp file there shows up in `git status` under whatever name it was given.

    `mode` is applied to the temp file before the replace, because mkstemp
    lands 0600 and a plain replace would silently carry that onto the target.
    `preserve_mode=True` keeps an EXISTING file's own mode instead, falling
    back to `mode` when there is no file to read one from — what a committable,
    team-readable file needs, and what an int alone cannot express."""
    directory = os.path.dirname(os.path.abspath(path))
    if ensure_makedirs:
        os.makedirs(directory, exist_ok=True)
    if preserve_mode:
        try:
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            pass  # no file yet: `mode` stays the caller's fallback
    fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=indent, sort_keys=sort_keys)
            if trailing_newline:
                fh.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_cache(path, payload_dict, project_id, now_iso, origin=None):
    """Wrap `payload_dict` in the cache envelope and write it atomically.

    mkstemp in the same directory + `os.replace`, 0600 — the shape the
    plugin's other atomic writers agreed on (`_loop_ledger._atomic_write_lines`,
    the review hook's state writer): a concurrent reader never sees a
    truncated file, and a crash leaves the previous cache intact
    (last-known-wins). Returns the envelope it wrote.

    `origin` IS THE URL THIS PAYLOAD CAME FROM, and it is recorded because the
    cache is keyed by CHECKOUT PATH alone. Re-point a checkout at a different
    Fairmind project or company — a new MCP entry, a new bearer — and the path,
    and therefore the cache file, is the same one; without this field the old
    company's force would keep applying to the new one until a fetch SUCCEEDED.
    The field is what lets `_insights_session.run_policy_refresh` tell "the
    platform I am talking to went down" (keep the cache: last-known-wins) from
    "this checkout now answers to somebody else" (drop it: a force must not
    outlive the credential that granted it). ALWAYS WRITTEN, null when the
    caller names no origin — an absent field and a foreign one are treated
    alike downstream, so a cache whose provenance is unknown is one that does
    not survive a failure."""
    envelope = {
        "version": CACHE_VERSION,
        "fetched_at": now_iso,
        "ttl_s": POLICY_TTL_S,
        "project_id": project_id if isinstance(project_id, str) else None,
        "origin": origin if isinstance(origin, str) and origin else None,
        "payload": payload_dict,
    }
    _atomic_write_json(path, envelope, prefix=".fm-policy-", mode=0o600,
                       sort_keys=True, ensure_makedirs=True,
                       trailing_newline=True)
    return envelope


def claims_from_bearer(authorization):
    """The claim dict of a JWT Authorization value, or `{}`.

    SELECTION AND DIAGNOSIS ONLY, NEVER AUTHORIZATION: the payload segment is
    base64url-decoded WITHOUT any signature verification. Two callers, one
    reason each. `project_id_from_bearer` picks which entry of an
    already-served policy map applies to this checkout — the server scopes what
    it serves by the VERIFIED token, so nothing a forged claim selects here
    grants anything the server did not already hand this company. And
    `/fairmind-connect` SHOWS `company` and `exp` to the developer holding the
    key, where the alternative to an unverified read is not a safer answer but
    no answer at all: an expired key is refused by every door with a 401, and
    "401" is not a sentence anyone can act on.

    `{}` — never None — for every malformed shape: non-string, no
    dot-separated segments, bad base64, non-JSON, non-dict claims. An empty
    dict keeps every caller on one `.get`, so a malformed token reads as a
    token that claims nothing rather than as a second failure mode. Accepts the
    raw token or the full "Bearer …" value."""
    if not isinstance(authorization, str):
        return {}
    token = authorization.strip()
    if token[:7].lower() == "bearer ":
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1]
    padding = "=" * (-len(segment) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(segment + padding))
    except ValueError:  # binascii.Error and UnicodeDecodeError are ValueErrors
        return {}
    if not isinstance(claims, dict):
        return {}
    return claims


def project_id_from_bearer(authorization):
    """The `projectId` claim of a JWT Authorization value, or None.

    A thin reading of `claims_from_bearer` — see it for why an unverified
    decode is the right primitive here. Its own contract is unchanged and
    pinned: every malformed shape, and an absent/non-string/empty claim, are
    all None."""
    project_id = claims_from_bearer(authorization).get("projectId")
    if isinstance(project_id, str) and project_id:
        return project_id
    return None


def _parse_iso_utc(value):
    """An aware UTC datetime from an ISO string, or None. Mirrors the idiom of
    `run_gate_checks` / `loop_tokens._parse_iso`: a trailing "Z" is normalized
    so it parses on every supported Python, and a naive result is pinned to
    UTC — subtracting a naive from an aware datetime raises, and a malformed
    timestamp must degrade to "unset", never to a crash in a Stop hook."""
    if not isinstance(value, str) or not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cache_freshness(cache, now):
    """Whether the cache envelope is usable, in ONE WORD: "none",
    "unreadable", "stale" or "fresh".

    THE ONE FRESHNESS RULE. `resolve_central` obeys it and
    `_insights_session._policy_status_lines` prints it, and they must never
    disagree: a hand-copied second implementation of this arithmetic is what
    made a `ttl_s: Infinity` cache print "(fresh)" while resolving unset.

      "none"       — no cache at all (`read_cache` answers None for a missing,
                     unparseable or non-dict file)
      "unreadable" — a non-dict, an envelope `version` this module does not
                     know, a missing/malformed `fetched_at`, a `ttl_s` that
                     is not a finite number (`json.load` parses a bare
                     `Infinity`, and an immortal force is not a decision
                     anyone made), or a `fetched_at` more than
                     `_MAX_CLOCK_SKEW_S` in the FUTURE
      "stale"      — age STRICTLY greater than the EFFECTIVE ttl, which is
                     `min(ttl_s, POLICY_TTL_S)`
      "fresh"      — everything else; age EXACTLY the effective ttl is still
                     fresh

    Pure — no I/O, and `now` is an argument — so the boundary is testable
    without a filesystem or a wall clock. A naive `now` is read as UTC, for
    the same reason `_parse_iso_utc` pins one: the subtraction would raise."""
    if cache is None:
        return "none"
    if not isinstance(cache, dict):
        return "unreadable"
    if cache.get("version") != CACHE_VERSION:
        return "unreadable"
    fetched = _parse_iso_utc(cache.get("fetched_at"))
    if fetched is None:
        return "unreadable"
    ttl = cache.get("ttl_s")
    if (isinstance(ttl, bool) or not isinstance(ttl, (int, float))
            or not math.isfinite(ttl)):
        return "unreadable"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = (now - fetched).total_seconds()
    if age < -_MAX_CLOCK_SKEW_S:
        # A stamp from the future is not a fetch that happened. Unreadable
        # rather than stale, because the file is not an expired answer — it is
        # an answer this module cannot place in time at all, and the whole
        # point of the freshness rule is that an unplaceable force does not
        # bind. See `_MAX_CLOCK_SKEW_S` for why trusting it would be immortal.
        return "unreadable"
    # THE TTL IS CLAMPED, NOT TRUSTED. `write_cache` stamps `POLICY_TTL_S` into
    # every envelope this plugin writes, so a LARGER `ttl_s` on disk cannot have
    # come from the plugin: it is hand-made, and honouring it would extend the
    # offline window past the one the product documents. Clamping rather than
    # rejecting keeps a SMALLER value honoured, which is the only direction
    # that shortens the time a last-known force keeps binding.
    return "stale" if age > min(ttl, POLICY_TTL_S) else "fresh"


def resolve_central(feature, cache, now):
    """The central policy's answer for `feature`: "on", "off", or None (unset).

    The thin value-only wrapper over `resolve_central_with_source`, which holds
    the ONE implementation — see there for the full truth table. Two callers
    want the value alone (the ambient gate, the judge Stop hook) and two want
    to know WHICH entry answered (the `--insights-status` block, the
    `--set-policy` refusal); splitting the implementation in two is how those
    four would come to disagree."""
    return resolve_central_with_source(feature, cache, now)[0]


def resolve_central_with_source(feature, cache, now):
    """The central policy's answer for `feature` AND which entry produced it:
    `(value, source)` where value is "on"/"off"/None and source is "project",
    "company_default", or None (unset — nothing answered).

    THE SOURCE IS NOT DERIVABLE FROM THE CACHE by any caller: `project_id` says
    which entry was LOOKED UP, not which one answered, and a project entry that
    omits the feature falls through to `companyDefault` PER FEATURE. A status
    line that reads the id alone therefore says "centrally forced (project X)"
    over a value the company default decided — true-looking and wrong, and the
    reason this pair exists rather than a second copy of the resolution order
    at each call site.

    Pure function over the cache dict — no I/O — so the whole truth table is
    testable without a filesystem. None for EVERY ambiguous shape:

    - `feature` outside the closed perimeter
    - anything `cache_freshness` does not call "fresh": no cache, a non-dict,
      an unknown envelope version, a missing/malformed `fetched_at`, a
      malformed or non-finite `ttl_s`, or an age past `ttl_s`
    - payload missing / non-dict / "version" != PAYLOAD_VERSION
    - no applicable entry: project_id null or not in `policies`, and
      `companyDefault` empty
    - feature key absent from the applicable entry (and from `companyDefault`)
    - value outside POLICY_VALUES, matched exactly

    (Every unset path answers `(None, None)`: no value, and no layer to
    attribute it to.)

    Resolution order: `policies[project_id]` first; a feature the project entry
    does not name falls through to `companyDefault` PER FEATURE — a company
    default is what applies where the project has not decided. A present but
    invalid value does NOT fall through: it is ambiguous, and ambiguity is
    unset, never a different layer's answer (the server skips unknown values on
    the way out, so this path is defense against a hand-edited cache only)."""
    # The perimeter FIRST, and deliberately not folded into the freshness
    # call: a feature nobody allowlisted is unset over a perfectly fresh
    # cache too, and an ordering that let the cache answer first would make
    # the perimeter test pass for the wrong reason.
    if feature not in FEATURES:
        return None, None
    if cache_freshness(cache, now) != "fresh":
        return None, None

    payload = cache.get("payload")
    if not isinstance(payload, dict):
        return None, None
    if payload.get("version") != PAYLOAD_VERSION:
        return None, None

    policies = payload.get("policies")
    policies = policies if isinstance(policies, dict) else {}
    default = payload.get("companyDefault")
    default = default if isinstance(default, dict) else {}

    project_id = cache.get("project_id")
    entry = policies.get(project_id) if isinstance(project_id, str) else None
    entry = entry if isinstance(entry, dict) else None

    if entry is not None and feature in entry:
        value, source = entry[feature], "project"
    elif feature in default:
        value, source = default[feature], "company_default"
    else:
        return None, None
    if isinstance(value, str) and value in POLICY_VALUES:
        return value, source
    return None, None
