#!/usr/bin/env python3
"""judge_stop_lane.py — ONE durable row per Stop-hook firing, and its delivery.

WHAT THE CARD IS FOR, in one measurement. Over 404 firings of the Stop hook on
one developer, only 23.5% became judge calls: dedup 162, call 95,
digest_unavailable 74, stop_hook_active 62, clean_tree 10, not_a_work_tree 1.
THE NON-CALLS ARE THREE QUARTERS OF THE SIGNAL and only the hook can see them.
A call count without that denominator cannot be read: "the judge ran 95 times"
and "the judge ran 95 times out of 404 opportunities" are different facts, and
nothing outside the hook can tell them apart. So this lane writes a row for
EVERY firing inside its population, and the seven non-call outcomes are the
point rather than an afterthought.

THE POPULATION IS "STOPS WHERE THE JUDGE IS INSTALLED AND ENABLED", and that
sentence has to travel with every rate computed from these rows. Three gates in
the review hook's entry point sit ABOVE the emission point and are deliberately
silent: the `FM_JUDGE_HOOK` off switch, an absent `fm-judge-review` client, and
a `cwd` that is not a directory. The install gate is additionally enforced
OUTSIDE python — the review hook's wrapper does `command -v
fm-judge-review || { cat >/dev/null; exit 0; }` before the interpreter starts —
so an in-process producer STRUCTURALLY cannot narrate a machine without the
client. A rate quoted against "all stops" rather than against this population is
wrong, and it is wrong in the flattering direction.

--- THE FOUR PROPERTIES THIS FILE MUST NOT LOSE ----------------------------

1. **It never changes how a turn ends.** the review hook exits 0 on every
   path and prints at most one JSON object, as the last statement on its path.
   A telemetry append that raised would be caught by that module's last-resort
   `except Exception` — which exits 0, yes, but SKIPS THE REST OF `main()`, so a
   spool failure before the client runs would silently suppress a real review.
   That is the one shape the hook forbids. So `append` NEVER RAISES: every
   failure inside it costs a row and nothing else. The guard lives here rather
   than at the call sites so it cannot be forgotten at the next one.

2. **Append at Stop, deliver later — never a synchronous POST.** A judge call
   takes 5-194 s (measured); a POST on top of it would be latency the developer
   pays at the end of every turn. Rows go to local disk here and are delivered
   by `_insights_session.run_drain`, inside the detached, niced sweep that
   SessionStart already spawns. CONSEQUENCE WORTH STATING: `SessionEnd` does not
   drain, so a developer's last session's stops sit on disk until the next
   session opens in that checkout.

3. **It is a stdlib-only leaf.** the review hook imports `_binding`,
   `_plugin_policy` and `audit_run_meta` and nothing else, precisely so a stop
   costs ONE process start on every turn of every repository with the plugin
   installed. Importing `_insights_session` (6,000 lines) or `ambient_outbox`
   (3,400, which imports `_insights_session` at its top) here would spend that
   budget on every turn. So the CAPTURE half below is stdlib plus
   `_plugin_policy`, which the hook already pays for, and the DELIVERY half
   imports `ambient_outbox` LAZILY, inside `drain()`, which only ever runs in
   the sweep. That is the same lazy-import seam `run_drain` already uses.

4. **Metadata only.** No file path, no branch name, no digest, no diff, no
   prose. The wire carries an opaque repository key, one bounded categorical,
   two integers and three opaque ids. Every categorical this module can emit is
   asserted against `^[a-z_]{1,64}$` by its own suite, because a 422 is a
   PERMANENT dead-letter in the outbox classifier — the row is destroyed, not
   retried — and a value with a digit, a hyphen or a dot is a 422.

--- WHY THIS IS NOT THE AMBIENT OUTBOX ------------------------------------

Every durable structure in `ambient_outbox` is keyed by `session_id`:
`state["delivered"]` is a list of session-id strings, `_pending_rows` skips
`sid in delivered`, `drain`'s collapse keeps `latest_row[sid]`,
`_merge_events_entries` dedups BY SESSION by design. ONE ROW PER STOP IS N ROWS
PER SESSION. Reusing any of it would collapse a session's stops to one and
destroy exactly the denominator this lane exists to supply — and the failure is
SILENT: the drain reports `acked=1` and logs nothing.

Putting the row on the ambient SPOOL is worse still. `_unsendable_reason`'s
third branch turns any row whose `schema` is not `ambient_digest.SCHEMA_VERSION`
into `"held"` — never sent, never terminal, never reclaimed by `_compact_spool`,
i.e. stranded forever; and bumping that constant to fix it strands every
backlogged ambient row instead. So: own spool, own state, own schema constant.
What IS reused, unchanged, is the transport and the status machinery — see
`drain`.

--- WHAT THE ROW IDENTIFIES A REPOSITORY BY, AND THE COST OF IT ------------

`repoRef` is the code-ingestion catalog id the checkout is bound to, under
scheme `code-ingestion`, whenever `/fairmind-connect` has run: that is the ONE
identity two developers of one repository share, and it is the same value the
judge is given as `--repository-ref`, so the rubric and the telemetry name the
same repository. An UNBOUND checkout — the ordinary state of a repository that
has not run the command — falls back to the opaque tenancy, `opaque-tenancy`,
the same one-way hash the ambient lane already wire-binds.

⚠️ THE HONEST COST: the tenancy hashes an ABSOLUTE LOCAL PATH, so two
developers on one unbound repository are two repositories, and one repository
that later gets bound changes identity. Cross-developer aggregation therefore
joins only over the bound population until the brain resolves the two schemes
to each other. The alternative — emitting nothing for unbound checkouts — would
leave most checkouts out of the denominator silently, which is the defect this
card exists to remove. Neither half of that trade may be dropped quietly.

`repoRef` is NEVER the memo key (the review hook's state-path idiom's
`sha256(realpath(repo))[:32]`) and never a path or a folder name.

--- THE ROW'S OWN CLASS ---------------------------------------------------

Judge-stop rows are consent class **C**, `generation_context`, and the lane
declares its own gate: `class_consent_state(toplevel, "C")`, read at DELIVERY.
It deliberately does NOT borrow the event-skeleton switch — `run_drain`'s
`events_consent_for` ANDs that switch with class C, and borrowing it would make
a judge row's fate depend on a decision about session transcripts.

WHY THE GATE IS AT DELIVERY AND NOT AT CAPTURE. `class_consent_state` lives in
`_insights_session`, which property 3 forbids the hook from importing, and a
second implementation of a three-valued revoke-first consent read is the drift
failure this repository keeps paying for. Gating at delivery is also STRICTLY
STRONGER than gating at capture: REVOKED both refuses to send and DISCARDS rows
already spooled, which capture-gating alone cannot do for a revocation that
arrives after the fact. Capture is to local disk under the developer's own
home, and nothing leaves the machine until the gate is read.

The spool is keyed PER CHECKOUT (the git toplevel), not per tenancy, and that
is load-bearing rather than incidental: the ambient lane shipped and then fixed
a bug (OPEN-1 F4) where one launcher's consent answer was applied to a
tenancy-shared spool and destroyed a GRANTING sibling worktree's rows. A
per-checkout spool cannot express that mistake — the rows and the
`.fairmind-insights.json` that governs them come from the same toplevel.

--- THE FIELD DEFINITIONS THAT LOOK LIKE BUGS FROM A DASHBOARD -------------

`changedFiles` / `changedLines` measure THIS SESSION'S CHANGE — since
2026-09-24 the files the session wrote, diffed from the commit it started on,
which is exactly what the judge is offered; before that date they measured the
whole working tree, and a series that spans the date changes meaning there.
They are derived for free from the git output `change_digest` already read, so
this lane adds NO subprocess to the hook's budget. One consequence: an
untracked file contributes to `changedFiles` but not to `changedLines` —
counting its lines means READING it on every stop, the cost
`untracked_fingerprint` already caps for the same reason. A change the session
COMMITTED is counted like one it did not, since the diff runs from before it.

Both are ABSENT rather than zero when a git call failed or the tree was never
probed: four of the outcomes return before any probe happens, and a zero there
would read as `clean_tree`, which is a different fact.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _plugin_policy  # noqa: E402  (stdlib-only leaf; the hook already pays for it)

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None


# --------------------------------------------------------------------------- #
# The contract, transcribed. NOTHING HERE IS THIS CLIENT'S TO INVENT.
# --------------------------------------------------------------------------- #

#: The door, as a PATH on the Fairmind MCP's own origin — the same mechanism
#: `_insights_session._ACTIVITY_ENDPOINT_PATH` uses, and for the same reason:
#: deriving the url from the MCP url (rather than accepting a configurable
#: endpoint) is what keeps the bearer structurally on the host that issued it.
ENDPOINT_PATH = "/insights/v1/judge-stop"

#: The SERVER's contract literal, transcribed from the brain, NEVER this
#: plugin's own stamp. That
#: distinction is a drafting error this programme has already made once: a
#: version invented client-side made every stored row claim a contract the
#: server had never published. The server WARNS on an unrecognised value and
#: stores the row, so a stale literal here degrades to a warning rather than to
#: a dead-letter — but it is still a false claim, so it is transcribed.
CONTRACT_VERSION = "fm-insights.judge-stop/1"

#: ⚠️ EVERY CONSTANT ABOVE AND BELOW IS TRANSCRIBED, WHICH IS EXACTLY WHERE A
#: 422 COMES FROM, so it was checked rather than proof-read. On 2026-08-31 all
#: 44 payload shapes this module can build — every outcome including the three
#: the enum does not name, both `repoRefScheme` values, maximal and minimal —
#: were constructed by `build_wire` and validated against the brain's REAL
#: request schema, with a negative control confirming the door refuses a
#: categorical this module also refuses. That check cannot live in this suite:
#: the brain is not a dependency of this repository, and making it one to test a
#: wire is the coupling the byte-pinned fixtures exist to avoid. Re-run it by
#: hand when a constant here moves.

#: The SPOOL ROW's own schema, which is a LOCAL disk format and not the wire.
#: Its own constant, and deliberately not a bump of `ambient_digest.
#: SCHEMA_VERSION`: a judge row on the ambient spool is stranded forever by
#: `_unsendable_reason`, and bumping that constant to admit it would strand
#: every backlogged ambient row instead.
SPOOL_SCHEMA = "fm-judge-stop.row/1"

#: What a 200 from THIS door must carry before a row is treated as delivered.
#:
#: A 200 proves the request was WELL-FORMED, never that anything was stored:
#: the server accepts, DROPS and 200s identically for a payload it did not
#: understand. And the endpoint is
#: DERIVED from whatever url the project config names, so a proxy, a captive
#: portal or a typo'd host answering 200 would settle every row and reclaim the
#: bytes. The keys are transcribed from THIS door rather than copied from
#: `ambient_outbox.SESSION_ACK_KEYS`; the RULE is shared, the keys are not.
ACK_KEYS = ("id",)

#: The consent class every field of this lane that describes the work belongs
#: to, per the canonical `consent_class_map.json`.
CONSENT_CLASS = "C"

#: The scheme the brain accepts for the catalog id `/fairmind-connect` binds a
#: checkout to. NOT pattern-bounded server-side (only `outcome` is), which is
#: why a hyphen is safe HERE and fatal there.
SCHEME_CATALOG = "code-ingestion"
#: The ambient lane's own scheme for the opaque tenancy, reused verbatim so the
#: brain — which resolves a project only for `opaque-tenancy` — can reach a
#: binding for an unbound checkout too.
SCHEME_TENANCY = "opaque-tenancy"


# --- the outcome vocabulary ------------------------------------------------
#
# THE EIGHT are the brain's outcome vocabulary, transcribed. THE EXTRAS
# are terminal states of the hook's ladder that the vocabulary does not name;
# they are emitted anyway because the card's rule is ONE ROW PER STOP and a
# missing row is unrecoverable, while an outcome the dashboard does not render
# is a reporting-layer fix. The door's `outcome` is `Union[Enum, str]` bounded
# to `^[a-z_]+$`, so a well-shaped unknown is STORED and visible in any
# breakdown over the collection — that tolerance is what makes emitting them
# safe. Every one of them is asserted against the pattern by this lane's own
# suite.

OUTCOME_CALL = "call"
OUTCOME_CLEAN_TREE = "clean_tree"
OUTCOME_DEDUP = "dedup"
OUTCOME_SILENCED = "silenced"
OUTCOME_LOOP_RUNNING = "loop_running"
OUTCOME_DIGEST_UNAVAILABLE = "digest_unavailable"
OUTCOME_STOP_HOOK_ACTIVE = "stop_hook_active"
OUTCOME_NOT_A_WORK_TREE = "not_a_work_tree"

#: The eight the brain's enum renders.
KNOWN_OUTCOMES = (
    OUTCOME_CALL, OUTCOME_CLEAN_TREE, OUTCOME_DEDUP, OUTCOME_SILENCED,
    OUTCOME_LOOP_RUNNING, OUTCOME_DIGEST_UNAVAILABLE, OUTCOME_STOP_HOOK_ACTIVE,
    OUTCOME_NOT_A_WORK_TREE,
)

#: No base branch resolved, so there was nothing to measure the change against.
OUTCOME_NO_BASE = "no_base"
#: This session already learned the repository has no compiled rubric.
OUTCOME_NO_RUBRIC_SESSION = "no_rubric_session"
#: This session already asked about this exact tree and got no verdict.
OUTCOME_NO_VERDICT_SESSION = "no_verdict_session"
#: RETIRED 2026-09-24 and no longer emitted: it named the rung where a
#: checkout-wide baseline sat at HEAD over a clean tree, and that baseline is
#: gone — the change is measured from the session's own anchor, and "the
#: session's files hold no change" is `clean_tree`. Kept in the vocabulary
#: because rows spooled before that date still carry it.
OUTCOME_EMPTY_DELTA = "empty_delta"
#: This session wrote no file in this repository, so it gets no review (owner,
#: 2026-09-20): the working tree may be dirty with other sessions' work, and
#: none of it is this session's to be told about.
OUTCOME_NOTHING_WRITTEN = "nothing_written"
#: The transcript could not be read, so which files are this session's is
#: unknown — told apart from `nothing_written`, because unknown is not none.
OUTCOME_ATTRIBUTION_UNAVAILABLE = "attribution_unavailable"
#: The client cannot restrict the review to this session's files (it predates
#: `--paths-from`, or the names could not be written down) and the tree holds
#: other work, so no call was made rather than a review of someone else's.
OUTCOME_CANNOT_RESTRICT = "cannot_restrict"

EXTRA_OUTCOMES = (
    OUTCOME_NO_BASE, OUTCOME_NO_RUBRIC_SESSION, OUTCOME_NO_VERDICT_SESSION,
    OUTCOME_EMPTY_DELTA, OUTCOME_NOTHING_WRITTEN, OUTCOME_ATTRIBUTION_UNAVAILABLE,
    OUTCOME_CANNOT_RESTRICT,
)

OUTCOMES = KNOWN_OUTCOMES + EXTRA_OUTCOMES

#: The server's own bound on every categorical on this lane. A value outside
#: it is a 422, and a 422 is a PERMANENT dead-letter — the row is destroyed,
#: never retried.
CATEGORICAL_RE = re.compile(r"^[a-z_]{1,64}$")

#: The two bounds the brain puts on the identity keys. `stopId` is composed
#: server-side into a colon-delimited telemetry record id, so a colon in it
#: mints extra namespace segments the receiver's own guard exists to refuse.
ID_MAX_LENGTH = 256

#: ⚠️ THE ONE PRODUCER FIELD WHOSE VALUE COMES FROM A FILE THIS CODE DOES NOT
#: WRITE, and therefore the only one that needs a shape rather than a bound.
#: `repoRef` is either the opaque tenancy (minted here) or the catalog `_id`
#: read verbatim out of `.fairmind/active-context.json` — a repo-root file a
#: developer, an agent or a future writer can put anything in. `_binding.
#: repository_id` validates only that it is a non-empty string.
#:
#: BOTH SIDES ASSUMED THE OTHER BOUNDED IT. The brain's request schema claims
#: no field could carry a file path, yet declares `repoRef: str` with no
#: pattern, and it already treats a non-`opaque-tenancy` scheme as untrusted
#: free text. Measured 2026-08-31 through the real
#: hook: an absolute local path, a git remote url and a prose string carrying
#: an email address each reached the wire verbatim. This lane is what puts the
#: value on the wire, so the guard belongs here.
#:
#: The shape admits both legitimate spellings — a 24-hex catalog ObjectId and
#: `fm-` + 16 hex — and refuses every separator a path, a url, an address or a
#: header injection needs: `/`, `:`, `@`, whitespace and newlines.
REPO_REF_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


# --------------------------------------------------------------------------- #
# Tunables. Deliberately the ambient outbox's own numbers, because this lane
# talks to the same service under the same PC-A2 status contract; they are
# restated rather than imported so the capture half stays a leaf.
# --------------------------------------------------------------------------- #

_BACKOFF_BASE_SECONDS = 60
_BACKOFF_MAX_SECONDS = 3600
_MUTE_401_REASON = "auth_failed"
_MUTE_401_DURATION = timedelta(hours=1)
_MUTE_403_REASON = "ambient_disabled"
_MUTE_403_DURATION = timedelta(hours=24)

#: How many terminal records the state file keeps per compartment. The state
#: file is rewritten whole, so it must not grow without bound; a settled row is
#: gone from the spool, so what is kept here is only a diagnostic tail.
_DEAD_LETTER_KEEP = 100


# --------------------------------------------------------------------------- #
# Where the lane's two files live.
# --------------------------------------------------------------------------- #

def checkout_key(toplevel):
    """The 32-hex name this checkout's spool and state are filed under.

    THE SAME IDIOM as the review hook's state-path idiom and
    `_plugin_policy.cache_path` — sha256 of the realpath, first 32 chars — so
    two paths to one checkout (a symlink, /tmp vs /private/tmp on macOS) share
    one spool instead of two. A ONE-WAY hash of a LOCAL PATH: correct as a
    file name on this machine, and never a value that reaches the wire. None
    for a falsy toplevel, so a caller cannot file rows under the empty string.
    """
    if not toplevel:
        return None
    try:
        real = os.path.realpath(toplevel)
    except OSError:
        real = toplevel
    return hashlib.sha256(real.encode("utf-8", "replace")).hexdigest()[:32]


def canonical_common(cwd, common):
    """The git common dir as a canonical ABSOLUTE path, or None.

    `git rev-parse --git-common-dir` answers RELATIVELY (`.`) when git ran
    inside the git dir itself — which is every bare repository — and every
    caller passes `git -C cwd`, so the answer is relative to `cwd`. Resolving
    it in ONE place is what keeps the tenancy and the spool's file name naming
    the same directory: they were derived separately once, and the raw `.` went
    to the file name, which then followed the READER's working directory
    instead of the repository's."""
    if not common:
        return None
    if not os.path.isabs(common):
        if not cwd:
            return None
        common = os.path.join(cwd, common)
    try:
        return os.path.realpath(common)
    except OSError:
        return None


def tenancy_from_common(cwd, common):
    """The OPAQUE tenancy id — `fm-` + sha256(realpath(git-common-dir))[:16] —
    or None when it cannot be derived (fail closed).

    A HAND-MIRROR of `_insights_session._tenancy_from_common`, letter for
    letter, for property 3; the stop lane's own suite pins that the two answer
    identically on the same input. The COMMON DIR itself is hashed and not its
    parent and not the toplevel: that collapses all linked worktrees of one
    repository to one tenancy while keeping sibling submodules distinct, each
    of which has its own common dir under `<super>/.git/modules/<name>`.

    `git rev-parse --git-common-dir` may answer RELATIVELY (`.git`), relative
    to the directory git ran in — which is `cwd`, since every caller passes
    `git -C cwd` — so it is joined before it is canonicalized."""
    canonical = canonical_common(cwd, common)
    if not canonical:
        return None
    return "fm-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _lane_dir():
    return os.path.join(_plugin_policy._data_dir(), "insights", "judge-stop")


def spool_path(toplevel):
    """The durable append-only queue of rows captured for this checkout."""
    key = checkout_key(toplevel)
    return os.path.join(_lane_dir(), key + ".jsonl") if key else None


def state_path(toplevel):
    """What has been settled for this checkout: delivered ids, dead letters,
    the mute and the backoff. Keyed by `stopId` throughout — never by session,
    which is the collapse this lane exists to avoid."""
    key = checkout_key(toplevel)
    return os.path.join(_lane_dir(), key + ".state.json") if key else None


# --------------------------------------------------------------------------- #
# Private, durable, never-rotating writers.
#
# A HAND-MIRROR of `_insights_session._durable_append` / `_ensure_private_dir`
# / `_ensure_private_file`, for the reason `_plugin_policy._data_dir` is a
# hand-mirror of `_insights_session.data_dir`: importing the original would
# drag a 6,000-line module into a hook that runs at the end of every turn.
# the stop lane's own suite pins that the two behave alike where it can.
#
# THE LOCK IS ON THE FILE'S OWN FD, which is why compaction rewrites the spool
# IN PLACE under the same fd lock rather than via mkstemp + os.replace: a
# rename shares no lock with an fd-flock appender, so a concurrent append would
# land on the dangling old inode and be silently lost.
# --------------------------------------------------------------------------- #

def _ensure_private_dir(directory):
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return
    for d in (directory, os.path.dirname(directory)):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _ensure_private_file(path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
    except OSError:
        pass


def _durable_append(path, line):
    """Append ONE line under a blocking exclusive lock, flushed and fsynced.

    Blocking, never `LOCK_NB`: a concurrent stop in the same checkout must WAIT
    its turn, never skip its own write. N stops in one checkout appending to
    one file is the ordinary case here, not a corner — the whole lane is one
    row per stop."""
    _ensure_private_dir(os.path.dirname(path))
    _ensure_private_file(path)
    fh = open(path, "a", encoding="utf-8")
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
        fh.write(line if line.endswith("\n") else line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    finally:
        fh.close()


def _write_state(path, state):
    """Rewrite the state file atomically, owner-only.

    mkstemp + os.replace is right HERE and wrong for the spool: nothing
    fd-locks the state file for appends, and a torn state file would lose the
    record of what has already been delivered — which is the one thing that
    must not be lost, since losing it re-sends rows the server has stored."""
    directory = os.path.dirname(path)
    _ensure_private_dir(directory)
    fd = None
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".judge-stop-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None
            json.dump(state, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        if fd is not None:
            os.close(fd)


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


# --------------------------------------------------------------------------- #
# CAPTURE — everything below runs inside the Stop hook.
# --------------------------------------------------------------------------- #

def mint_stop_id():
    """The row's identity, minted ONCE per firing and frozen before any early
    return.

    NOT DERIVED FROM THE SESSION ID, and that is measured rather than
    stylistic: 3 of 95 call rows carry no session id at all, so a session-keyed
    id would have no value on those rows. NOT DERIVED FROM THE CLOCK either:
    the hook's wall clock is whole-second, so two stops inside one second would
    collide — and this is the server's UPSERT KEY, so a collision is one row
    overwriting another rather than two rows.

    Hex, so it satisfies the door's colon-free bound by construction."""
    return uuid.uuid4().hex


def plugin_version():
    """The sibling `../.claude-plugin/plugin.json` version, or None. Never raises.

    A hand-mirror of `_insights_session.plugin_version` for property 3; the
    suite pins that the two answer the same."""
    try:
        with open(os.path.join(_HERE, "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            version = json.load(fh).get("version")
    except Exception:  # noqa: BLE001 — a version is never worth a raised stop
        return None
    return version if isinstance(version, str) and version else None


def utc_now():
    """The row's `stoppedAt`, in the ISO-8601 UTC shape every other lane here
    stamps (`_insights_session`'s own `.replace(microsecond=0).isoformat()`).

    ⚠️ NOT THE RETENTION CLOCK. The TTL goes on the server-set `createdAt`; a
    producer clock deciding how long a row lives is a failure the brain's own
    retention contract test pins against."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_wire(*, stop_id, outcome, repo_ref, repo_ref_scheme, stopped_at,
               session_id=None, changed_files=None, changed_lines=None,
               plugin_version_=None, contract_version=CONTRACT_VERSION):
    """The POST body, and ONLY the POST body.

    EXACTLY the ten producer keys `consent_class_map.json`'s `judgeStop` door
    declares — no more, because a key nobody classified is a key no revocation
    can name, and no fewer on the keys that are always present. `id`, `userId`,
    `company` and `projectId` are SERVER-INJECTED and must never be sent: the
    server stamps identity from the credential last, and `projectId` is resolved from
    the binding the `repoRef` names.

    An optional field that has no value is OMITTED rather than sent as null:
    the schema's own comment is that a count is "omitted rather than zeroed
    when the working tree was never probed", and an absent key says that
    without asking the far side to distinguish null from zero."""
    wire = {
        "stopId": stop_id,
        "outcome": outcome,
        "repoRef": repo_ref,
        "repoRefScheme": repo_ref_scheme,
        "stoppedAt": stopped_at,
        "contractVersion": contract_version,
    }
    if session_id:
        wire["sessionId"] = session_id
    if isinstance(changed_files, int):
        wire["changedFiles"] = changed_files
    if isinstance(changed_lines, int):
        wire["changedLines"] = changed_lines
    if plugin_version_:
        wire["pluginVersion"] = plugin_version_
    return wire


def _sendable(wire):
    """Whether this row can be POSTed at all, checked BEFORE it is spooled.

    A row that would 422 is worse than a row that was never written: 422 is a
    PERMANENT dead-letter in the classifier, so the row is destroyed on arrival
    and the denominator loses it either way — but a spooled one also costs a
    request and a state entry to learn that. The three bounds are the server's
    own: the categorical pattern on `outcome`, and colon-free/bounded on the
    two identity keys."""
    stop_id = wire.get("stopId")
    if not isinstance(stop_id, str) or not stop_id or ":" in stop_id \
            or len(stop_id) > ID_MAX_LENGTH:
        return False
    session_id = wire.get("sessionId")
    if session_id is not None and (
            not isinstance(session_id, str) or not session_id
            or ":" in session_id or len(session_id) > ID_MAX_LENGTH):
        return False
    if not CATEGORICAL_RE.match(wire.get("outcome") or ""):
        return False
    # SHAPE, not just presence — see `REPO_REF_RE`. This is the last gate
    # before a value read out of a repo-root file becomes a stored row on a
    # field the consent map classes UNCLASSIFIED, i.e. one no revocation can
    # ever name. A row refused here is lost from the denominator, which is why
    # `StopRow._identity` falls back to the tenancy rather than letting a
    # malformed binding reach this point at all; this stays as the backstop for
    # every other caller.
    if not REPO_REF_RE.match(wire.get("repoRef") or ""):
        return False
    return bool(wire.get("repoRefScheme"))


def append(toplevel, wire, *, captured_at=None):
    """Spool ONE row. Returns True when it landed, False otherwise.

    NEVER RAISES — see property 1 in the module docstring. This is called from
    inside the review hook's entry point, whose last-resort handler exits 0 but
    also SKIPS THE REST OF `main()`; a telemetry failure that reached it would
    suppress a real review, which is the one thing this hook may not do. So
    every failure here is swallowed and costs exactly one row."""
    try:
        path = spool_path(toplevel)
        if not path or not _sendable(wire):
            return False
        row = {
            "schema": SPOOL_SCHEMA,
            "capturedAt": captured_at or utc_now(),
            "wire": wire,
        }
        _durable_append(path, json.dumps(row, sort_keys=True))
        return True
    except Exception:  # noqa: BLE001 — a lost row must never cost a review
        return False


# --------------------------------------------------------------------------- #
# DELIVERY — everything below runs in the detached SessionStart sweep.
# --------------------------------------------------------------------------- #

def _load_state(path):
    state = _read_json(path, {})
    delivered = state.get("delivered")
    dead = state.get("deadLetter")
    return {
        # Both keyed by `stopId`. NEVER by session: N stops share one session,
        # and a session-keyed skip would settle rows that were never sent.
        "delivered": [s for s in delivered if isinstance(s, str)]
        if isinstance(delivered, list) else [],
        "deadLetter": [d for d in dead if isinstance(d, dict)]
        if isinstance(dead, list) else [],
        "discarded": state.get("discarded") if isinstance(
            state.get("discarded"), list) else [],
        "mutedUntil": state.get("mutedUntil"),
        "muteReason": state.get("muteReason"),
        "attempt": state.get("attempt") if isinstance(
            state.get("attempt"), int) else 0,
        "nextAttemptAt": state.get("nextAttemptAt"),
    }


def _read_spool(path):
    """Every well-formed row in the spool, in file order. A blank or
    unparseable line is skipped rather than fatal — the same degrade-graceful
    discipline every other JSONL reader here uses."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("wire"), dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _stop_id_of(row):
    return row.get("wire", {}).get("stopId")


def _settled(row, state):
    stop_id = _stop_id_of(row)
    if not stop_id:
        return True  # unnameable, so unredeliverable: never retried
    if stop_id in state["delivered"]:
        return True
    return any(d.get("stopId") == stop_id for d in state["deadLetter"])


def pending_rows(rows, state):
    """The rows still owed to the door, oldest first, deduped by `stopId`.

    A DUPLICATE `stopId` in the spool is possible — an append that landed and a
    process that died before anything recorded it cannot be told apart — and
    the server upserts on `(company, stopId)`, so sending it twice converges.
    Sending it twice in ONE drain is just a wasted request, so the first
    occurrence wins here."""
    seen, out = set(), []
    for row in rows:
        stop_id = _stop_id_of(row)
        if not stop_id or stop_id in seen or _settled(row, state):
            continue
        seen.add(stop_id)
        out.append(row)
    return out


def _ack_proves_persistence(body):
    """True iff `body` is the shape THIS door declares for success.

    ONE RULE, RESTATED FOR A SECOND DOOR — `ambient_outbox._ack_proves_
    persistence` argues it at length for the session-activity door and says
    explicitly that anyone hardening it must edit both. The rule: a 200 proves
    the request was well-formed; only the door's own declared success shape in
    the BODY proves anything was stored. `body` may be None, a list or a string
    — a misdirected endpoint can return any JSON at all, and `str` in
    particular would satisfy a naive `"id" in body`."""
    if not isinstance(body, dict):
        return False
    return all(body.get(key) for key in ACK_KEYS)


def _compact(path, drop_ids):
    """Remove the rows named by `drop_ids` from the spool, in place, under the
    SAME fd lock an appender takes. True when the rewrite happened.

    IN PLACE, never mkstemp + os.replace: a rename swaps the inode from under a
    concurrent `_durable_append`, whose write then lands on the dangling old
    one and is silently lost.

    ⚠️ IT DROPS A DENY LIST AND NEVER KEEPS AN ALLOW LIST, which is the whole
    reason it re-reads under the lock. A row appended between the caller's
    snapshot and this rewrite has an id the caller has never seen; keeping only
    the ids the caller knows about would DELETE it, unsent, with no symptom.
    Dropping only what is known to be settled leaves an unknown row alone."""
    try:
        with open(path, "r+", encoding="utf-8") as fh:
            if _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
            kept = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and _stop_id_of(row) not in drop_ids:
                    kept.append(json.dumps(row, sort_keys=True) + "\n")
            fh.seek(0)
            fh.truncate()
            fh.writelines(kept)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        return False
    return True


class _drain_lock:
    """A NON-BLOCKING exclusive lock over one checkout's lane, so two sweeps
    that overlap do not both send the same rows.

    Non-blocking on purpose, and the loser returns BEFORE loading any state:
    waiting would serialise two background passes for no gain, and the rows it
    would have sent are still on the spool for the winner or for the next
    sweep. The server upserts on `(company, stopId)`, so a double send would
    converge rather than duplicate — this saves the requests and, more to the
    point, stops two writers racing on the state file where the loser's
    `delivered` list would overwrite the winner's."""

    def __init__(self, root):
        self.path = None
        self.fh = None
        key = checkout_key(root)
        if key:
            self.path = os.path.join(_lane_dir(), key + ".drain.lock")

    def __enter__(self):
        if not self.path or _fcntl is None:
            return True
        try:
            _ensure_private_dir(os.path.dirname(self.path))
            self.fh = open(self.path, "a+", encoding="utf-8")
            _fcntl.flock(self.fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            if self.fh is not None:
                self.fh.close()
                self.fh = None
            return False
        return True

    def __exit__(self, *_exc):
        if self.fh is not None:
            self.fh.close()
            self.fh = None
        return False


def _muted(state, now):
    until = state.get("mutedUntil")
    if not isinstance(until, str):
        return False
    try:
        return datetime.fromisoformat(until) > now
    except ValueError:
        return False


def _backing_off(state, now):
    when = state.get("nextAttemptAt")
    if not isinstance(when, str):
        return False
    try:
        return datetime.fromisoformat(when) > now
    except ValueError:
        return False


def _result(**kw):
    base = {"sent": 0, "acked": 0, "dead_lettered": 0, "retried": 0,
            "discarded": 0, "pending": 0, "muted": False, "mute_reason": None,
            "held_reason": None}
    base.update(kw)
    return base


# The three-valued consent constants, restated so this module's own signature
# is readable without importing the 6,000-line module that defines them. The
# CALLER passes the state it already resolved; the strings are
# `_insights_session.EVENTS_CONSENT_*` verbatim, and the suite pins that.
CONSENT_GRANTED = "granted"
CONSENT_REVOKED = "revoked"
CONSENT_INDETERMINATE = "indeterminate"


def drain(toplevel, transport, *, consent_state, now=None):
    """Deliver this checkout's pending rows. Never raises.

    `transport(payload)` is `ambient_outbox.make_urllib_transport(...)` — the
    POST, the redirect refusal (`_RefuseRedirects`, which closes a real stdlib
    credential leak: CPython rebuilds a 302'd POST with `Authorization`
    intact) and the bounded response read, reused verbatim rather than
    hand-written a second time. A falsy `transport` means no door is
    configured, and the whole call degrades to a spool-only no-op with every
    row still queued.

    `consent_state` is this CHECKOUT's live class-C state, three-valued and
    REVOKE-FIRST, resolved by the caller through `_insights_session.
    class_consent_state`. REVOKED is the ONLY state that discards; INDETERMINATE
    neither sends nor destroys, which is what makes a transiently unreadable
    config cost a delay instead of the rows."""
    try:
        return _drain(toplevel, transport, consent_state, now)
    except Exception:  # noqa: BLE001 — the sweep is fail-open by contract
        return _result()


def _drain(toplevel, transport, consent_state, now):
    now = now or datetime.now(timezone.utc)
    spool = spool_path(toplevel)
    state_file = state_path(toplevel)
    if not spool or not os.path.isfile(spool):
        return _result()
    with _drain_lock(toplevel) as mine:
        if not mine:
            return _result(held_reason="another_drain_holds_the_lane")
        return _drain_locked(spool, state_file, transport, consent_state, now)


def _drain_locked(spool, state_file, transport, consent_state, now):
    state = _load_state(state_file)
    rows = _read_spool(spool)

    # REVOCATION IS DECIDED FIRST, before enablement, before the mute and
    # before the transport — the same order `class_consent_state`'s own
    # docstring argues for. An explicit `false` is a decision the company made,
    # and "turned off" would be false if it applied only to what had not
    # happened yet.
    if consent_state == CONSENT_REVOKED:
        discarded = [_stop_id_of(r) for r in rows if _stop_id_of(r)]
        if discarded:
            state["discarded"] = (state["discarded"] + discarded)[-_DEAD_LETTER_KEEP:]
            _write_state(state_file, state)
        _compact(spool, set(discarded))
        return _result(discarded=len(discarded))

    pending = pending_rows(rows, state)
    if consent_state != CONSENT_GRANTED:
        # INDETERMINATE: never sent, never discarded. The rows wait, and the
        # reason is named rather than reported as an empty successful drain.
        return _result(pending=len(pending), held_reason="consent_indeterminate")

    if not transport:
        return _result(pending=len(pending), held_reason="no_door")
    if _muted(state, now):
        return _result(pending=len(pending), muted=True,
                       mute_reason=state.get("muteReason"))
    if _backing_off(state, now):
        return _result(pending=len(pending), held_reason="backoff")
    if not pending:
        if _settle(spool, rows, state):
            _write_state(state_file, state)
        return _result()

    # Lazy, and this is the seam property 3 turns on: `ambient_outbox` imports
    # `_insights_session` at its top, so importing it at module scope would put
    # 9,500 lines on the Stop hook's per-turn path. Here it is already loaded
    # — the sweep imported it to build the transport it just handed us.
    import ambient_outbox  # noqa: PLC0415

    sent = acked = dead = retried = 0
    unproven = False
    for row in pending:
        stop_id = _stop_id_of(row)
        try:
            response = transport(row["wire"])
        except Exception:  # noqa: BLE001 — a transport that raises is a retry
            response = None
        sent += 1
        status = getattr(response, "status", 0)
        body = getattr(response, "body", None)
        action = ambient_outbox._classify(status, body)
        if action == "ack" and not _ack_proves_persistence(body):
            # A 200 that is not this door's answer settles NOTHING. Treated as
            # a retry so the rows are retained, and NAMED, because the usual
            # cause is the derived endpoint pointing at something that is not
            # the insights service — a proxy, a captive portal, a typo'd host —
            # and an unnamed retry reads as a network blip.
            action = "retry"
            unproven = True
        if action == "ack":
            state["delivered"].append(stop_id)
            acked += 1
        elif action == "dead_letter":
            state["deadLetter"] = (state["deadLetter"] + [{
                "stopId": stop_id, "status": status, "at": now.isoformat(),
            }])[-_DEAD_LETTER_KEEP:]
            dead += 1
        elif action in ("mute_auth", "mute_kill"):
            duration = (_MUTE_401_DURATION if action == "mute_auth"
                        else _MUTE_403_DURATION)
            state["mutedUntil"] = (now + duration).isoformat()
            state["muteReason"] = (_MUTE_401_REASON if action == "mute_auth"
                                   else _MUTE_403_REASON)
            retried += 1
            break  # every further row would earn the same answer
        else:
            retried += 1
            break  # the door is down; the rest keep their place in the queue

    if retried:
        state["attempt"] = state.get("attempt", 0) + 1
        delay = min(_BACKOFF_BASE_SECONDS * (2 ** (state["attempt"] - 1)),
                    _BACKOFF_MAX_SECONDS)
        state["nextAttemptAt"] = (now + timedelta(seconds=delay)).isoformat()
    else:
        state["attempt"] = 0
        state["nextAttemptAt"] = None
    _settle(spool, rows, state)
    _write_state(state_file, state)
    still = len(pending_rows(_read_spool(spool), state))
    return _result(sent=sent, acked=acked, dead_lettered=dead, retried=retried,
                   pending=still, muted=_muted(state, now),
                   held_reason="unproven_ack" if unproven else None,
                   mute_reason=state.get("muteReason") if _muted(state, now) else None)


def _settle(spool, rows, state):
    """Reclaim the bytes of settled rows, then FORGET the ids that named them.

    STATE-DRIVEN, never by age: a row is removed because something recorded its
    fate, not because it got old.

    THE SECOND HALF IS WHY THIS RUNS ON EVERY DRAIN rather than past a cap.
    `delivered` is keyed by `stopId`, and this lane writes ONE ROW PER STOP —
    hundreds a week in a busy checkout — so a list that only ever grew would
    become a JSON file re-read and re-written at every session start, for ever.
    It does not need to grow: the list exists ONLY to skip rows still sitting
    on the spool, so once a row's bytes are gone its id has no work left to do.
    Pruned strictly AFTER a rewrite that actually happened, because forgetting
    an id whose row survived would send it a second time."""
    drop = {_stop_id_of(r) for r in rows if _settled(r, state) and _stop_id_of(r)}
    if not drop or not _compact(spool, drop):
        return False
    state["delivered"] = [s for s in state["delivered"] if s not in drop]
    return True
