#!/usr/bin/env python3
"""PL-A1b — the ambient-telemetry DIGESTER's pure transform.

This module holds the whole PL-A1b `digest()` contract: turn one or more parsed
transcript record lists (a main session transcript plus zero+ subagent SIDECAR
transcripts) into a privacy-scrubbed, per-(session, agentRole, model) token
rollup (T2-C1 added the agentRole dimension; it was per-(session, model)).
It is a peer of PL-A0's `_usage_dedup.py` and PL-A1a's `_insights_session.py`,
and is invoked from `_insights_session.run_sweep` (the SessionStart-driven
lifecycle sweep) — never on the request/response hot path.

Design, matching the frozen PL-A1b interface contract:

  * `digest(record_sets, meta) -> dict` is a PURE function: no filesystem, no
    clock, no env reads beyond the one-time sibling `.claude-plugin/plugin.json` version probe.
    It never raises on malformed input — a schema-drifted record degrades the
    result (`parserDegraded: True`, no fabricated numbers) rather than crashing
    the caller (a SessionStart-spawned background sweep must never wedge).
  * Summing is delegated ENTIRELY to `_usage_dedup` (PL-A0) — this module never
    re-sums usage itself, nor re-implements the "may this record be suppressed
    as a repeat" rule, so the two can't drift apart. Where the SUPPRESSION
    happens is worth stating exactly, because it moved: the cross-bucket guard
    below claims each id at most once per MODEL *before* bucketing, asking
    `_usage_dedup.dedup_key` for the key rather than re-deriving it. Every
    record that reaches a bucket therefore already carries an id unique within
    its model, so `deduped_usage_totals`' OWN dedup pass can never suppress
    anything when called from here — it is a no-op on this path, not a second
    line of defence. It stays load-bearing for its OTHER caller,
    `hooks/scripts/capture-subagent-tokens.sh`, which sums a single transcript
    and knows nothing of models or buckets: that is where PCF-15's ~2.7x
    streamed-block over-count is still actively suppressed. Do not "optimize
    away" that branch on the strength of the ambient tests — they would all
    stay green while the hook regressed. See `digest`'s own comment for why the
    scope is the model and not the `(agent_role, model)` bucket.
  * Privacy: a rollup carries only {model, agent_role, the 4 token ints}
    (round 2: skills/entry_source moved to the row level, see below; T2-C1
    added `agent_role`, and NOTHING else from the sidecar's metadata — see
    the `MAIN_ROLE`/`UNATTRIBUTED_ROLE` note below). No raw cwd/gitBranch/
    message content/id ever reaches the returned dict.

PL-A2a extended this SAME contract (converging the spool row onto the shipped
project-context wire schema, see `ambient_outbox.build_wire_payload`) with
session-LEVEL fields on the returned dict, alongside the pre-existing
`session_id`/`tenancy`/`pluginVersion`/`parserDegraded`/`rollups`:

  * `started_at` / `ended_at`: carried through VERBATIM from `meta` (the
    caller — `_insights_session._digest_one_session` — sources them from the
    session's own registry row and copies them into `meta`; this module never
    re-derives or reformats them, it only echoes what it was handed, exactly
    like it already does for `session_id`/`tenancy`/`entry_source`).
  * `entry_source`: HOISTED to this top (row) level — one value per session.
  * `skills`: HOISTED to this top (row) level too (PL-A2a round 2, D1) — the
    sorted union of every `attributionSkill`/`Skill` tool_use seen across the
    whole session. Round 1 stamped this into every per-MODEL rollup instead,
    which silently lost the skill entirely for a session whose only
    Skill-invoking record carried no `usage` block (such a record never
    produces a rollup at all, since rollups are keyed off usage-bearing
    records — see `groups.setdefault` below). `tool_counts` has never had
    this problem (it was already row-level, independent of `usage`), so
    `skills` now follows the SAME altitude — one writer per fact.
  * `schema`: an explicit, versioned stamp (`SCHEMA_VERSION`, PL-A2a round 2
    D4) on every row this function produces — the outbox
    (`ambient_outbox._unsendable_reason`) uses this to detect an
    unrecognized/future row VERSION rather than sniffing for the mere
    absence of `started_at`/`ended_at`, which a row with a different, still
    incompatible field set could otherwise slip past.
  * `tool_counts`: `{tool_name: count}`, aggregated across EVERY record in
    EVERY record_set (main transcript + subagent sidecars alike) via the SAME
    single content-block walk `_record_skills` already performs (no second
    walk) — see `_record_tool_names` below. A `<synthetic>` record's own
    tool_use blocks are excluded, mirroring the token-accounting exclusion of
    that model exactly (`model != _SYNTHETIC_MODEL`).

A rollup itself carries ONLY `{model, agent_role, the 4 token ints}` — a
CLOSED set (see test_ambient_digest.py's own pinned key-set assertion).
`skills` and `entry_source` are not per-rollup fields; a rollup is a
per-(agentRole, model) token aggregate, and neither fact is one of those, so
each has exactly ONE writer at the row level instead of a copy re-stamped
into every rollup.

T2-C1 — WHY `agent_role` is per-rollup and everything else about the sidecar
is not. The sweep already discovers each subagent sidecar transcript; its
sibling `agent-*.meta.json` carries the `agentType` that says WHICH subagent
produced those tokens. Without it every sub-agent's usage merged anonymously
into the main thread's per-model sum, so no token figure in the ambient plane
was attributable. `digest()` therefore groups on `(agent_role, model)`. Only
`agentType` is ever read from that metadata: `description` is free text and
`toolUseId` an internal handle, and the wire's `agents[]` key set stays
CLOSED (`ambient_outbox._agents_from_rollups`).

stdlib only.
"""

import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _usage_dedup  # noqa: E402  (PL-A0 shared dedup helper — the single sum oracle)

# The literal now lives with the predicate that uses it — `_usage_dedup.
# accounting_model`, which BOTH this digester and `loop_tokens` ask, so
# "is this record in the token accounting" has one definition. Aliased here
# rather than respelled: several comments below name it.
_SYNTHETIC_MODEL = _usage_dedup.SYNTHETIC_MODEL

# PL-A2a round 2 (D4): the explicit, versioned schema stamp `digest()` puts on
# every row it produces. A single, PUBLIC (no leading underscore) constant so
# `ambient_outbox._unsendable_reason` can import and compare against the SAME
# value rather than duplicating the literal — one source of truth for
# producer (this module) and validator (ambient_outbox.py) alike, the same
# idiom `insights_flush_payload.py`'s `*_CONTRACT_VERSION` constants use.
SCHEMA_VERSION = "fm-ambient.session/1"

# T2-C3: the version of the ORDERED, CONTENT-FREE EVENT SKELETON
# (`build_event_skeleton`), versioned separately from `SCHEMA_VERSION` because
# the two travel through DIFFERENT DOORS and can therefore move independently:
# the session rollup goes to the REST session-activity door, the skeleton to its
# own collection behind its own door (measured 2026-07-27: 68 of the 265 real
# sessions with a non-empty skeleton, 25.7%, exceed that REST door's
# 262144-byte cap with events inlined, and the client maps its 413 to
# `dead_letter` — which is never retried and whose bytes `_compact_spool`
# reclaims — so inlining would permanently destroy the WHOLE session record for
# a quarter of sessions. ONE number, one derivation, cited identically here, in
# `ambient_outbox.build_events_payload` and in the server's own schema comment;
# see that function for how it is computed).
#
# PUBLIC for the same reason `SCHEMA_VERSION` is: the stage that builds the
# events door imports THIS constant instead of duplicating the literal. It is
# deliberately NOT stamped onto the row `digest()` returns — that row is the
# session-activity payload, not the events payload, and widening it is the
# door-building stage's decision to take, not this one's.
EVENT_SCHEMA_VERSION = "fm-ambient.events/1"

# The EXACT set of top-level keys `digest(events=True)` adds to its row, and
# nothing else. Declared here (the producer owns the declaration) and imported
# by `ambient_outbox._raw_digest`, which must EXCLUDE every one of them.
#
# Why this is a named tuple rather than two string literals in `_raw_digest`:
# `_raw_digest` hashes the WHOLE row, and `rawDigest` ships on the wire while an
# unknown `events` key is silently dropped by the closed wire shape. Attaching
# events to the row therefore changes `rawDigest` for EVERY session while the
# payload looks unchanged — measured 2026-07-26: injecting
# `events=[]` alone moved the baseline harness sha from 15874f3c… to 02d1ddce…,
# i.e. even the emptiest possible events value moves it. One declaration keeps
# the producer and the hasher from disagreeing about which keys those are, and
# `test_event_skeleton.py` asserts BOTH halves: that each name here is inert for
# `_raw_digest`, and that this tuple equals exactly the key-set difference
# `digest(events=True)` minus `digest(events=False)` — so a future third events
# key that is added but not declared here goes red instead of silently moving
# every session's fingerprint.
EVENT_ROW_KEYS = ("events", "lanes")

# JC5 — the FOUR flat keys that carry a row's consent stamp, frozen at
# COLLECTION time and echoed unchanged from here to the wire (§F.3).
#
# THE DECLARATION LIVES IN THIS MODULE FOR AN IMPORT-GRAPH REASON, not because
# consent is a digester concern: `_insights_session` imports THIS module and
# `ambient_outbox` imports both, so this is the only one of the three that every
# other can name without a cycle. The POLICY — which classes a config grants, on
# what basis, what a live revoke does — stays entirely in `_insights_session`;
# what lives here is the row VOCABULARY and the projector below, so that the
# registry-row writer, `_digest_one_session`'s `meta`, this row and
# `ambient_outbox`'s two payload builders cannot spell the same four facts four
# ways. Same idiom as `SCHEMA_VERSION`: the lowest module owns the literal.
#
# `classes_applied` is deliberately NOT one of them. It is the LIVE half —
# `classes_at_collection ∩ <live resolution>` — computed at drain against the
# config as it reads THEN, so storing it would freeze the one value that must not
# be frozen (§F.5).
CONSENT_ROW_KEYS = ("consent_classes", "consent_version", "consent_basis",
                    "consent_content_mode")


def consent_row_fields(source):
    """The consent stamp carried by `source` (a registry row, or the `meta` a
    digest is built from), as the mapping a caller merges into the row it is
    writing. `{}` when `source` carries no stamp.

    OMIT, NEVER FABRICATE — the same discipline
    `_insights_session._provenance_fields` applies to the two provenance keys,
    and for the same reason. A row collected before this machine existed has no
    stamp, and inventing `["A","B","C"]` here would assert a grant that was never
    resolved. The inference for such a row happens ONCE, at the drain
    (`ambient_outbox._consent_object`), where it can be labelled
    `basis: "pre_consent"` and told apart from a resolved grant. Fabricating it
    here would erase that distinction three hops before anyone could use it.

    PARTIAL STAMPS ARE COPIED AS FOUND rather than repaired. `_provenance_fields`
    writes all four or none, so a partial stamp means a hand-edited or truncated
    row; the honest move is to carry what is there and let the drain's own
    completeness check decide, not to guess the missing half here."""
    if not isinstance(source, dict):
        return {}
    return {k: source[k] for k in CONSENT_ROW_KEYS if k in source}


# T2-C1: the two RESERVED agent-role values, PUBLIC so the discoverer
# (`_insights_session._sidecar_role`) and this grouper share one definition
# rather than two copies of a literal — the same idiom as SCHEMA_VERSION.
#
# `MAIN_ROLE` is a LITERAL, never None. It is a true statement about
# provenance (the orchestrating thread really is a distinct producer) rather
# than a fabricated sub-agent role; it makes "group by role" answerable across
# a fleet without a "null means orchestrator" convention every reader has to
# know; and the group key is SORTED — `None < "main"` raises TypeError, and
# rollup ORDER is load-bearing (`ambient_outbox._raw_digest` hashes the whole
# row, `rollups` is a list), so the key must stay TOTALLY ordered.
MAIN_ROLE = "main"

# The converse is NOT true and both external reviewers flagged it: `MAIN_ROLE`
# is a plain string in the SAME namespace as user-authored `agentType` values,
# so a sub-agent literally typed `"main"` lands in the orchestrator's bucket
# and its tokens are attributed to the main thread. `"fork"` is reserved by
# behaviour the same way. This costs ATTRIBUTION only — conservation is
# unaffected, because the claim is keyed on the model, not the role — and no
# such agentType exists in the 646-sidecar corpus here. Fixing it properly
# means an internally-tagged role key rather than a raw string, which changes
# the wire's `agentRole` values; deliberately not done under T2-C1.
#
# `UNATTRIBUTED_ROLE` is the EMPTY STRING, for that same sort-safety reason
# plus one more: a real `agentType` is always a NON-EMPTY string, so "" is the
# one sentinel a user-authored role can never collide with (roles are
# unbounded and user-authored — `general-purpose`, `impl-A1`, `qa-graph`,
# `fairmind-coding:QA Engineer`). It maps to `agent_role: None` on the rollup:
# an unreadable/absent meta file is a fact about the FILESYSTEM, not an agent
# role, so the rollup declines to name one rather than inventing a pseudo-role
# that a later "which agents burn tokens" query would count as real.
UNATTRIBUTED_ROLE = ""


def _plugin_version():
    """Best-effort read of the sibling ../.claude-plugin/plugin.json version (this
    module lives in <plugin>/scripts/, mirroring `_insights_session.plugin_version()`).
    Returns None on any failure — never raises."""
    try:
        with open(os.path.join(_HERE, "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except Exception:
        return None


def _is_drifted_assistant(rec):
    """True iff `rec` is a schema-drifted assistant record: `type == "assistant"`
    and either `message` is not a dict, or `message` carries a `usage` key whose
    value is present but not a dict. A record with no `usage` key at all is
    normal (not every assistant record carries usage), not drift."""
    if not isinstance(rec, dict) or rec.get("type") != "assistant":
        return False
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return True
    if "usage" in msg and not isinstance(msg.get("usage"), dict):
        return True
    return False


def _record_signals(rec):
    """Both content-derived signals this ONE record contributes, from ONE walk
    over `message.content`: `(skills, tool_names)`.

    - `skills` (a set) comes from BOTH sources — a non-empty top-level
      `attributionSkill`, and any `Skill` tool_use block's `input.skill`.
    - `tool_uses` (a LIST, not a set) is every `tool_use` block as a
      `(tool_use_id, name)` pair: the same record can invoke one tool
      repeatedly and each invocation is its own count, so the caller must be
      able to distinguish "invoked twice" (two ids) from "the same invocation
      observed twice" (one id, re-emitted by a fork sidecar). `tool_use_id` is
      None when the block carries no usable id, which means "never suppress
      this one".

    A `Skill` block contributes to both, which is exactly why this is one
    function: two helpers walking the same content list drifted from their own
    docstring, which claimed a single walk while performing two."""
    skills = set()
    names = []
    if not isinstance(rec, dict):
        return skills, names
    top = rec.get("attributionSkill")
    if isinstance(top, str) and top:
        skills.add(top)
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return skills, names
    content = msg.get("content")
    if not isinstance(content, list):
        return skills, names
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if isinstance(name, str) and name:
            # `(tool_use.id, name)` — the id travels so the caller can tell a
            # REPEATED INVOCATION from the SAME invocation seen twice. A fork
            # sidecar re-emits the fork-point record whole, tool_use blocks
            # included, carrying the SAME `tool_use.id`; without the id both
            # copies read as two calls. Measured on this machine's corpus: all
            # 6 fork-bearing sessions inflate, 10 echoed blocks, and 10 of 10
            # carry an id identical to main's — so the id is the right key and
            # it is stable across the echo. `id` is present on 208 of 208 real
            # tool_use blocks; a missing one falls back to counting (never
            # suppress on an absent key, the same rule `_usage_dedup.dedup_key`
            # applies to `message.id`).
            tool_id = block.get("id")
            names.append((tool_id if isinstance(tool_id, str) and tool_id else None, name))
        if name != "Skill":
            continue
        inp = block.get("input")
        if not isinstance(inp, dict):
            continue
        skill = inp.get("skill")
        if isinstance(skill, str) and skill:
            skills.add(skill)
    return skills, names


def _role_at(roles, index):
    """The agent role `record_sets[index]`'s records are grouped under.

    `roles` is OPTIONAL and positionally parallel to `record_sets`. When it is
    absent (the `digest(record_sets, meta)` 2-argument form — no role channel
    at all), or when the supplied entry is not a usable non-empty string, the
    DEFAULT applies: index 0 (the main transcript) is `MAIN_ROLE`, every
    sidecar is `UNATTRIBUTED_ROLE`.

    The default deliberately does NOT put sidecars under `MAIN_ROLE`: that
    would relabel sub-agent tokens as the orchestrator's — a wrong answer that
    looks right. Unknown is recorded as unknown.

    Always returns a string, so the `(role, model)` group key stays totally
    ordered for `sorted()` whatever a caller hands in."""
    default = MAIN_ROLE if index == 0 else UNATTRIBUTED_ROLE
    if isinstance(roles, (list, tuple)) and index < len(roles):
        role = roles[index]
        if isinstance(role, str) and role:
            return role
    return default


def _as_sequence(value):
    """`value` if it is a list/tuple, else an empty tuple.

    Guards the three `for x in <param> or ()` loops below. `or ()` rescues only
    None and other FALSY values: a non-iterable truthy one (`sidecars=1`) still
    reaches the `for` and raises TypeError — out of a module whose contract is
    "never raises", into `_digest_one_session`'s `except Exception`, which
    converts it into `digest([], meta)`. The WHOLE session's tokens zeroed,
    `parserDegraded` set, and nothing red anywhere: the exact silent
    catastrophe the surrounding docstrings keep citing as the thing to avoid.

    Not reachable from production today (`_discover_sidecars` returns a list),
    which is precisely why it went unnoticed — a contract only holds where
    something enforces it."""
    return value if isinstance(value, (list, tuple)) else ()


def digest(record_sets, meta, roles=None, *, events=False):
    """Turn `record_sets` (record_sets[0] = main transcript, record_sets[1:] =
    zero+ subagent sidecars) into the PL-A1b/PL-A2a rollup dict. See module
    docstring and the PL-A1b/PL-A2a dispatches for the exact contract. Never
    raises.

    `roles` (T2-C1) is an OPTIONAL sequence positionally parallel to
    `record_sets`, carrying each set's agent role — see `_role_at` for the
    defaults that keep the 2-argument form (`digest([], meta)`, the degraded
    path in `_insights_session._digest_one_session`) working unchanged.

    `events` (T2-C3) is KEYWORD-ONLY and OFF by default. When it is off this
    function's output is BYTE-IDENTICAL to the pre-T2-C3 one and the
    `EVENT_ROW_KEYS` are ABSENT rather than empty — not as a matter of
    reasoning ("an absent key cannot matter") but proved by execution over a
    pinned 12-session real corpus, whose sha256 the T2-C3 baseline harness
    reproduced unchanged at 74ddd0a9…3f2bb on 2026-07-26.

    KEYWORD-ONLY because `roles` in front of it is positional-capable, and BOTH
    ways of confusing the two are SILENT: a roles list landing in an `events`
    slot is simply a truthy flag, and a bool landing in the `roles` slot is
    silently ignored by `_role_at` (which only accepts a list/tuple) — every
    sidecar would fall back to `UNATTRIBUTED_ROLE` and nothing would raise. The
    `sidecars=`-style keyword discipline the callers already use
    (`_insights_session._digest_one_session`) is not enough on its own: it is a
    convention at the call site, where this is enforced by the signature."""
    session_id = meta.get("session_id") if isinstance(meta, dict) else None
    tenancy = meta.get("tenancy") if isinstance(meta, dict) else None
    entry_source = meta.get("entry_source") if isinstance(meta, dict) else None
    started_at = meta.get("started_at") if isinstance(meta, dict) else None
    ended_at = meta.get("ended_at") if isinstance(meta, dict) else None

    parser_degraded = False
    skills = set()
    tool_counts = {}
    groups = {}  # (agent_role, model) -> [usage-bearing, non-synthetic records]
    # T2-C1 amendment — WHY THE DEDUP SCOPE IS THE MODEL, NOT THE (role, model)
    # BUCKET. Roles split the bucket for ATTRIBUTION; they must not split it for
    # DEDUP. A fork sidecar re-emits its parent's fork-point message — same
    # `message.id`, byte-identical usage, only `isSidechain` differs — so the
    # same id legitimately appears in two record sets. Before T2-C1 one
    # per-model bucket absorbed that silently; splitting by role put the two
    # copies in DIFFERENT buckets, and `_usage_dedup` only ever dedups WITHIN
    # the list it is handed, so the message was summed twice.
    #
    # T2-C1 addressed that by labelling forks `MAIN_ROLE` in
    # `_insights_session._sidecar_role`, which works only while the sibling
    # `agent-*.meta.json` READ SUCCEEDS: a missing/unreadable/unparseable meta,
    # or a future `agentType` spelling, falls through to `UNATTRIBUTED_ROLE` and
    # the duplicate is back — a FAILED METADATA READ INVENTING TOKENS (measured
    # +33% on a 2-message fixture). Conservation must not depend on correctly
    # LABELLING a fork.
    #
    # So an id is claimed at most once per MODEL, first-seen wins, and
    # `record_sets[0]` is the main transcript — which is why main legitimately
    # owns the fork-point message. Keyed by MODEL, not one flat global set, for
    # a precise reason: the model IS the pre-T2-C1 dedup scope, so per-model
    # totals are now identical to pre-T2-C1 BY CONSTRUCTION. A flat set would
    # additionally dedup across DIFFERENT models — a behaviour change nobody
    # asked for. The fork->main mapping stays (it is correct attribution), but
    # it is now attribution ONLY: mislabelling a fork costs a role name, never a
    # number.
    seen_by_model = {}  # model -> {message.id already claimed under that model}

    # The SAME shape, one level down, for the tool census. A fork sidecar
    # re-emits the fork-point record whole — `tool_use` blocks included, with
    # their original `tool_use.id` — so a session that invoked Bash ONCE
    # reported it TWICE. Distinct from the token claim in two ways that both
    # follow from what the metric means: `tool_counts` is a SESSION-WIDE fact
    # (not per model, so one flat set), and it counts INVOCATIONS, so the key
    # is `tool_use.id` and never `message.id` — one message can legitimately
    # carry several calls to the same tool, and those must all count.
    # Measured before changing anything: all 6 fork-bearing sessions on this
    # machine's corpus inflate, 10 echoed blocks in total, 10 of 10 carrying
    # an id identical to main's, and 0 of 208 real blocks missing an id.
    seen_tool_uses = set()

    for index, record_set in enumerate(_as_sequence(record_sets)):
        if not isinstance(record_set, list):
            continue
        role = _role_at(roles, index)
        for rec in record_set:
            if _is_drifted_assistant(rec):
                parser_degraded = True
            rec_skills, rec_tool_uses = _record_signals(rec)
            skills |= rec_skills
            if not isinstance(rec, dict):
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            model = msg.get("model")
            # PL-A2a tool_counts: the SAME synthetic-model exclusion token
            # accounting already applies below (`isinstance(model, str) and
            # model and model != _SYNTHETIC_MODEL`) — a <synthetic> record's
            # own tool_use blocks must never be counted, even though this
            # gate runs independently of whether `usage` is present (a
            # tool_use record with no usage block would otherwise contribute
            # no tokens but should still contribute its tool call).
            # NOT `accounting_model` — deliberately. That predicate requires a
            # usage DICT, and this gate must run independently of usage: a
            # tool_use record with no usage block contributes no tokens and must
            # still contribute its tool call. Same model exclusion, different
            # question, stated so the next reader does not "unify" them and
            # silently drop tool counts.
            if isinstance(model, str) and model and model != _SYNTHETIC_MODEL:
                for tool_id, name in rec_tool_uses:
                    # An id-less block always counts (never suppress on an
                    # absent key); an id already seen is the SAME invocation
                    # re-emitted, not a second one.
                    if tool_id is not None:
                        if tool_id in seen_tool_uses:
                            continue
                        seen_tool_uses.add(tool_id)
                    tool_counts[name] = tool_counts.get(name, 0) + 1
            # THE ACCOUNTING-MEMBERSHIP GATE, asked of the shared oracle rather
            # than re-derived here. It answers None for exactly the three shapes
            # this branch used to test one by one — a non-dict `usage` (the
            # record contributes no tokens; never fabricate a rollup for it), the
            # synthetic model (excluded entirely, even non-zero usage), and a
            # missing/non-string/empty model. `loop_tokens._deduped_per_model`
            # asks the SAME function, which is what stops the developer-facing
            # and fleet-facing figures disagreeing about membership after PCF-27
            # made them agree about repeats.
            if _usage_dedup.accounting_model(rec) is None:
                continue
            usage = msg.get("usage")
            # An id is marked seen ONLY for a record that actually enters a
            # bucket — i.e. AFTER the synthetic-model skip, the no-usage skip
            # and the no-model skip above. That placement is what makes "no
            # behaviour change except the cross-bucket duplicate" a true
            # statement rather than a hope. It also sits BELOW the
            # `tool_counts` accumulation on purpose — tool counts have never
            # been deduped by message.id, and this change must not silently
            # start doing so.
            #
            # Precisely what the placement buys, since the obvious phrasing —
            # "a record that contributed no tokens cannot claim an id" — is
            # FALSE and was caught here by external review: a record whose
            # `usage` is an EMPTY dict passes every gate above, claims its id,
            # and suppresses a later sibling carrying the same id and real
            # numbers. What the placement actually guarantees is narrower: a
            # record that is structurally OUT of accounting (synthetic model,
            # no usage key at all, no model) cannot claim. The empty-usage case
            # is `_usage_dedup`'s own long-standing first-usage-bearing-
            # occurrence-wins rule, identical on both paths and pinned as a
            # KNOWN residual by
            # `test_the_claim_and_the_oracle_agree_on_the_subtle_shapes` —
            # changing it would move the bash hook too, so it is a decision,
            # not a cleanup.
            mid = _usage_dedup.dedup_key(rec)
            if mid is not None:
                claimed = seen_by_model.setdefault(model, set())
                if mid in claimed:
                    continue  # already counted under this model, in an earlier set
                claimed.add(mid)
            groups.setdefault((role, model), []).append(rec)

    # PL-A2a round 2 (D1): `skills` is a ROW-level fact (one writer), not a
    # per-rollup copy — a rollup only exists for a model with a usage-bearing
    # record, but a Skill invocation's own record may carry NO usage block at
    # all (see `_record_skills`/`tool_counts` above, which is unconditional).
    # Stamping skills into every rollup means a session with zero rollups
    # (e.g. exactly this no-usage case) would silently lose every skill name;
    # hoisting it here, alongside `tool_counts`, fixes that at the source.
    sorted_skills = sorted(skills)
    rollups = []
    # `sorted(groups)` over the (role, model) TUPLE — role first, then model.
    # Every key component is a string (see `_role_at`), so the order is total
    # and deterministic; `_raw_digest` hashes the resulting LIST, so a
    # non-deterministic order here would make the same session hash
    # differently on two runs.
    for role, model in sorted(groups):
        totals = _usage_dedup.deduped_usage_totals(groups[(role, model)])
        # UNATTRIBUTED_ROLE ("") is a GROUPING sentinel only; it reaches the
        # rollup — and hence the wire — as an explicit null, because "no
        # readable metadata" is not a role name.
        #
        # Compared to the CONSTANT, not tested for falsiness (`role or None`).
        # The truthiness form makes this line silently depend on
        # UNATTRIBUTED_ROLE being the empty string — a dependency stated
        # nowhere, including in the constant's own comment, which justifies ""
        # on sort-safety grounds alone. Respell the sentinel as, say,
        # "<unattributed>" for readability and `role or None` KEEPS it: the
        # sentinel then ships as a real role name and a "which agents burn
        # tokens" query counts an agent that does not exist — the precise
        # outcome the constant exists to prevent. Naming it costs nothing.
        rollup = {"model": model,
                  "agent_role": None if role == UNATTRIBUTED_ROLE else role}
        rollup.update(totals)
        rollups.append(rollup)

    row = {
        "session_id": session_id,
        "tenancy": tenancy,
        "pluginVersion": _plugin_version(),
        "parserDegraded": parser_degraded,
        "started_at": started_at,
        "ended_at": ended_at,
        "entry_source": entry_source,
        "schema": SCHEMA_VERSION,
        "skills": sorted_skills,
        "tool_counts": tool_counts,
        "rollups": rollups,
    }
    # JC5 — the consent stamp is ECHOED from `meta`, exactly the way
    # `started_at`/`ended_at`/`entry_source` already are, and for the same reason:
    # it was resolved at COLLECTION time (SessionStart, against the checkout the
    # transcript belongs to) while this function runs in a detached background
    # sweep on a later session, often days later. Reading a config here would
    # retro-label the row with whatever the file says now, which is the exact
    # thing §F.3 exists to forbid — and it would also cost this function its
    # purity, which the whole test suite leans on.
    #
    # `_insights_session._degraded_rollup` reaches this line too (it is
    # `digest([], meta)` with `parserDegraded` flipped), so a transcript that was
    # missing or unparseable spools a STAMPED row. Degraded parsing says nothing
    # about consent, and an unstamped degraded row would be indistinguishable at
    # the drain from a genuinely pre-consent one.
    row.update(consent_row_fields(meta))
    if events:
        # The ONE place the two `EVENT_ROW_KEYS` are written, and they are
        # written together or not at all: `rows` without `lanes` is a projection
        # whose `lane` ints resolve to nothing.
        row["events"], row["lanes"] = build_event_skeleton(record_sets, roles=roles)
    return row


# --------------------------------------------------------------------------- #
# T2-C3 — the ORDERED, CONTENT-FREE EVENT SKELETON.
#
# `digest()` above answers "how many tokens, by whom, on which model". It cannot
# answer "in what ORDER did the episode happen" — a rollup is an aggregate and
# has thrown the sequence away. The skeleton is that missing axis and NOTHING
# else: one row per emitted event, carrying structure (kind/actor/uuid/parent/
# lane/seq), the four token ints on an assistant turn, and a tool NAME. No text,
# no tool INPUT, no file path, no diff — the classifier below reads content to
# recognize a marker, and a row never carries it (see `_body_text`).
#
# This is a PORT of the T2-C3 REFERENCE PROJECTOR, revision 2 (2026-07-26) —
# the single definition the design survey converged on after running TWO
# divergent projectors, whose revision 1 was then audited by an adversarial
# verifier that reproduced five defects against the real corpus. The
# CLASSIFICATION RULES, the ORDER they are evaluated in, and the EMITTED FIELD
# SET are the contract and match it exactly;
# structure and naming were adapted to this module (it reuses `_as_sequence` and
# `_role_at` rather than re-deriving them — see `build_event_skeleton`).
# Verified by execution 2026-07-26: over the ~/.claude/projects corpus (273
# sessions, main + shallow sidecars, 171,335 rows) this port and the reference
# emit BYTE-IDENTICAL `(rows, lanes)` for EVERY session — 0 mismatches — and the
# port re-derives the published figures: rows p90=1740 max=4801, bytes
# p90=429273 p99=993882 max=1277025, human-clock leaks 0, duplicate uuids 0.
# (The p50s read 332 rows / 84006 bytes against 336 / 82270 published earlier
# the same day. That is CORPUS DRIFT, not a port difference: the corpus is live
# — sessions were being written while both ran — and the reference re-run
# alongside this port reports the same 332 / 84006. Which is the reason a
# survey figure is dated here and a PARITY run, not a remembered number, is what
# the port is checked against.)
#
# DECISION D2, and it is the whole reason the classifier is shaped this way:
# agent-authored records (assistant turn, tool invocation, tool result) carry a
# TIMESTAMP; records originating from the HUMAN (prompt, interrupt, approval,
# denial, answer) carry their POSITION IN THE SEQUENCE and NO CLOCK. A human row
# has no `ts` KEY AT ALL — absent, never null. `is_agent_authored` is the one
# function the row builder consults about clocks.
#
# NO `durationMs` FIELD, deliberately: `system/turn_duration.durationMs` is a
# HUMAN-latency measurement, not an agent one (measured 2026-07-26, 220:1
# evidence that it spans from prompt submission, and on one real record
# 150,411,019 of 150,417,783 ms — 100.0% — was the person deciding on a
# permission prompt). Duration is a `ts` delta between AGENT rows.
#
# The row set is deliberately NARROW: assistant turns, tool invocations, tool
# results, and the human boundary events D2 names. `system`, `attachment`
# (except a queued human prompt), `queue-operation`, `mode` and `file-history-*`
# are NOT emitted at all — they are the shapes carrying the wrongly-clocked
# hazards, and not emitting a row is stronger than classifying it correctly.
#
# KNOWN AND ACCEPTED, carried over from the reference so nobody "discovers" it:
#   * A sidecar's root row has NO parentUuid (650 orphan roots, one per sidecar
#     file) and 613 sidecar rows point at a uuid outside the row set, so a lane
#     cannot be attached to the `tool_use` that dispatched it. The fix would be
#     reading `toolUseId` from the sibling `agent-*.meta.json`, which T2-C1
#     deliberately declined to read (see the T2-C1 note in the module
#     docstring); that is a decision to take explicitly, not a gap to close
#     silently here.
#   * The interrupt-marker test is a SUBSTRING test and is therefore spoofable
#     by any transcript that QUOTES the marker — including a report about this
#     feature. That direction is deliberate: a false positive costs a clock, a
#     false negative leaks one.
#   * An ORDINARY `tool_result` whose `tool_use` was never walked KEEPS ITS
#     CLOCK: the shape rule does not require the join, and that is a decision,
#     not an oversight. Measured 2026-07-27 over the corpus named at
#     `_HUMAN_RESULT_KEYS`, 0 of 37,583 `tool_result` blocks are unjoined — so
#     failing them closed would cost nothing today and everything on the day the
#     harness changes how a `tool_use` id is written: 37,583 rows (23% of every
#     row emitted) would quietly become clockless human/prompt, and durations are
#     the analysis the skeleton exists for. The classes that must NOT depend on
#     the join — the results a PERSON authored — get a second, join-free signal
#     instead (`_human_result_kind`).
#     WHAT THIS RESIDUAL DOES NOT COVER, learned the expensive way: a fallback
#     keyed on a tool the join rule never NAMED protects nothing. AskUserQuestion
#     had a perfectly complete join table for all 300 of its leaked rows; the
#     rule simply did not list the tool. Membership is the guarantee, the
#     fallback is only its redundancy.
#   * A `user` record carrying MORE THAN ONE `tool_result` block emits one row
#     per block, and every one of them takes the RECORD's uuid — so the uuid
#     claim in `build_event_skeleton` keeps the first and silently drops the
#     rest. Not fixed, because fixing it means minting a synthetic row identity
#     and the reference projector is the contract; recorded with its measurement
#     instead: 0 of 273 real sessions contain such a record (Claude Code emits
#     one `tool_result` per user record), so the path is latent, not live. It
#     becomes live the day that changes, and the symptom would be missing
#     tool_result rows rather than anything red.
# --------------------------------------------------------------------------- #

# The D2 human-origin markers, measured over 277 main + 650 sidecar transcripts
# (246,653 records) on 2026-07-26.

# STRUCTURAL, spoof-proof. Absent before Claude Code ~2.1.198, which is why the
# TEXT markers below still have to exist: 18 of 62 real denials carry only them.
# Only "user-rejected" is the HUMAN value — `automode-blocked`,
# `permission-rule` and `automode-unavailable` are the MACHINE refusing, and
# those keep their clock.
_DENIAL_KIND = "user-rejected"

_DENIAL_TEXT = ("The user doesn't want to proceed with this tool use",)
_DENIAL_RESULT = ("User rejected tool use",)
# Covers BOTH spellings ("[Request interrupted by user]" 56x and
# "[Request interrupted by user for tool use]" 21x) and every position the
# marker occupies: plain content, a text block, or a tool_result body.
_INTERRUPT_TEXT = "[Request interrupted by user"
# TWO spellings, and the text test finds only 4 of the 6 real ones — which is
# why the `tool_use_id` join below is the PRIMARY rule and this is the fallback.
_APPROVAL_TEXT = ("User has approved your plan", "User has approved exiting plan mod")

# THE TOOLS WHOSE RESULT IS AUTHORED BY THE PERSON rather than by the machine,
# joined by `tool_use_id` — structural, spoof-proof — mapped to the D2 `kind`
# each one's result carries.
#
# THE MEMBERSHIP RULE, written down because this was a ONE-ELEMENT tuple and the
# missing member was a live D2 leak the entire time: a tool belongs here iff THE
# PERSON PRODUCES THE RESULT CONTENT, not iff a person is merely involved. A
# permission denial is NOT a member — a person is very much involved, but the
# result is the harness refusing on their behalf, and `_DENIAL_KIND` /
# `_DENIAL_TEXT` / `_DENIAL_RESULT` already classify that above.
#
# CENSUSED 2026-07-27 over every transcript under `~/.claude/projects/*/*.jsonl`
# plus each session's `<sid>/subagents/agent-*.jsonl` — 269 sessions, 603
# sidecars, 37,583 `tool_result` rows spread over 87 distinct `tool_use` names,
# every name inspected. EXACTLY TWO satisfy the rule.
#
# `AskUserQuestion` was the leak, and it was the larger class by fifty to one.
# Its result is a person answering a question — the same act as an
# `ExitPlanMode` approval — but it arrives as an ordinary `tool_result` block,
# so `_authorship`'s shape rule reached `("agent", "tool_result")` and the row
# was CLOCKED WITH THE PERSON'S OWN TIMESTAMP: 300 rows across 123 sessions,
# against 6 for the class that WAS handled. Re-measured after the fix on the
# same corpus: of 351 AskUserQuestion result rows, 351 classify human and 0
# carry a clock. The other 51 were ALREADY human — a person who cancels the
# prompt instead of answering — and they keep the `denial` label they had, which
# is what `denial-via-answer-join` in test_event_skeleton.py exists to hold.
#
# The closest MISS is worth naming so it is not re-litigated: `EnterPlanMode`
# results are the harness's own `{"message": "Entered plan mode…"}` boilerplate
# (4 rows, 4 sessions) — authored by nobody, so they keep their clock. Every
# other name in the census returns machine content.
_HUMAN_RESULT_TOOLS = {"ExitPlanMode": "approval", "AskUserQuestion": "answer"}

# THE SAME VERDICTS, RECOGNIZED WITHOUT THE JOIN — because the join table is
# built DURING THE WALK, so a result whose `tool_use` was never walked has no
# entry in it (a resumed or compacted transcript that starts after the plan was
# proposed; a record set assembled from anything but a whole session). The rule
# above then yields silently to the `tool_result` shape rule, which says AGENT
# and CLOCKS the row. That made D2's guarantee for these verdicts conditional on
# a join table being complete, while this module's stated discipline is that an
# unrecognized shape falls back to HUMAN. Both tools' results identify themselves
# on their OWN record: `toolUseResult` is a dict carrying what the person
# produced — the plan they ruled on, or the answers they gave.
#
# Measured 2026-07-27 over every transcript under `~/.claude/projects/*/*.jsonl`
# plus each session's `<sid>/subagents/agent-*.jsonl` (269 sessions, 603
# sidecars, 37,583 `tool_result` rows): `plan` appears on 5 blocks, ALL of them
# ExitPlanMode results; `answers` appears on 300 blocks, ALL of them
# AskUserQuestion results. Neither key appears on any other tool, so each is
# tool-exclusive on this corpus. The same run measured 0 `tool_result` blocks
# whose `tool_use_id` is unjoined, so the class each key covers is a RESIDUAL
# and not an observed leak — the observed leak was the MISSING TOOL above, which
# no fallback could have caught because the join rule itself did not name it.
#
# `answers` AND NOT `questions`, although the same 300 dicts carry both: the
# person authored the answers and the AGENT authored the questions. Keying the
# rule on the agent's half would classify a result human on the strength of
# something the agent wrote, which is the one direction a spoof-proof signal
# must not take.
#
# THE PREDICATE IS LOOSER THAN THE SIGNATURES IT WAS MEASURED AGAINST, on
# purpose. All 5 plan payloads carry `filePath`+`hasTaskTool`+`isAgent`+`plan`
# (3 of them `planWasEdited` too) and 267 of the 300 answer payloads carry
# `annotations` alongside `answers`+`questions`; this test asks for one key.
# Requiring the full signature would buy precision this rule does not need and
# cost the thing it exists for: a harness that renames ONE of those keys
# re-opens the leak, silently, for exactly the transcripts where the join is
# also missing. A tool that happens to return a dict with a `plan` or `answers`
# key gets its result classified human and loses a clock — the cheap direction,
# and the same asymmetry the interrupt substring test is argued from above.
#
# ONE KEY WINS AT MOST ONE KIND. The two are disjoint on this corpus (5 and 300,
# no overlap), so iteration order decides nothing today; it is declared in a
# dict rather than a tuple of pairs so a third member cannot be added without
# naming the kind it maps to.
_HUMAN_RESULT_KEYS = {"plan": "approval", "answers": "answer"}

# The three ways a `type == "user"` record can be the HARNESS talking rather
# than the person. Stripping their clock would cost data (they are genuinely
# agent-authored), which is why the inverse control in test_event_skeleton.py
# asserts they KEEP it.
_HARNESS_PROMPT_SOURCES = ("system",)
_HARNESS_ORIGIN_KINDS = ("task-notification", "coordinator", "peer")
_HARNESS_PREFIXES = ("<local-command-stdout>", "<local-command-caveat>",
                     "<system-reminder>")


def _text_of(block):
    """The `text` of one content block, or "" for any other shape."""
    if not isinstance(block, dict):
        return ""
    text = block.get("text")
    return text if isinstance(text, str) else ""


def _body_text(rec, block):
    """Every text position this record/block exposes, concatenated, for MARKER
    MATCHING ONLY.

    NEVER emitted: the classifier may read content, a row may not carry it. That
    asymmetry is the whole privacy claim of the skeleton, so the one function
    that reads content is also the one that returns nothing to the row."""
    parts = []
    if isinstance(block, dict):
        content = block.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(_text_of(b) for b in content)
        parts.append(_text_of(block))
    msg = rec.get("message") if isinstance(rec, dict) else None
    if not isinstance(msg, dict):
        msg = {}
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        parts.extend(_text_of(b) for b in content)
    tool_use_result = rec.get("toolUseResult") if isinstance(rec, dict) else None
    if isinstance(tool_use_result, str):
        parts.append(tool_use_result)
    return "\n".join(p for p in parts if p)


def _human_result_kind(rec, block, tool_names):
    """The D2 `kind` for a row that is the RESULT of a tool the PERSON answered
    — `"approval"` for an ExitPlanMode ruling, `"answer"` for an AskUserQuestion
    reply — or None when this row is not one.

    ONE FUNCTION, TWO TOOLS, and that is the shape of the fix rather than an
    incidental refactor. `AskUserQuestion` leaked for as long as it did because
    "which tools' results are human-authored" was a ONE-ELEMENT tuple consulted
    here while the shape rule in `_authorship` decided everything else; a second
    list of human-result tools kept anywhere else would drift from this one the
    same way. So membership is `_HUMAN_RESULT_TOOLS` and nothing reads a tool
    name for authorship anywhere but here.

    Recognized by EITHER of two independent signals:

      * the `tool_use_id` JOIN, which is exact but needs the dispatching
        `tool_use` to have been walked into `tool_names`;
      * the RESULT SHAPE on the record itself, which needs nothing else read.

    Either is sufficient, so neither is load-bearing alone — see
    `_HUMAN_RESULT_KEYS` for why that matters and for the measurement of the
    shape signals' precision. The plan and the answers are read by nothing: this
    is a KEY-PRESENCE test, and the row it classifies carries no content either
    way.

    `toolUseResult.isAgent` would distinguish a sub-agent's own exit from a
    person's, and this rule deliberately does not read it — the join rule never
    did either, so both signals classify that class the same way, and the cost
    of being wrong about it is a lost clock rather than a leaked one."""
    if not isinstance(block, dict):
        return None
    tool_use_id = block.get("tool_use_id")
    named = ((tool_names or {}).get(tool_use_id)
             if isinstance(tool_use_id, str) else None)
    if isinstance(named, str) and named in _HUMAN_RESULT_TOOLS:
        return _HUMAN_RESULT_TOOLS[named]
    # The shape signal is a rule ABOUT A TOOL RESULT and its guard says so —
    # but NOT LOAD-BEARING TODAY, and that is the honest description of it.
    # This function has ONE caller (`_human_origin`, reached from `_authorship`),
    # and `_event_rows_for` is the only thing that ever supplies a `block`: it
    # filters a `user` record's content to `type == "tool_result"` before
    # emitting a row, and every other row it emits — an assistant turn, a
    # `tool_use`, a string-content prompt, an attachment — passes `block=None`.
    # So a dict block arriving here is a `tool_result` BY CONSTRUCTION, and a
    # `block=None` has already returned at this function's `isinstance` guard
    # above. Measured 2026-07-27 by instrumenting the real projector over 269
    # sessions under `~/.claude/projects/*/*.jsonl` plus their
    # `<sid>/subagents/agent-*.jsonl` (239,924 records, 164,765 emitted rows):
    # 329,304 calls, 254,054 of them `block=None` and 75,250 a `tool_result`
    # block. THIS BRANCH WAS REACHED ZERO TIMES.
    #
    # An earlier version of this comment justified the line by a reproduction
    # that cannot happen — that without it the signal "would also reach rows
    # classified with `block=None`" and relabel a prompt as an approval. Those
    # rows return above; the line has never protected them. The measurement it
    # cited (0 records carrying the marker without also emitting a `tool_result`
    # block) is a true fact supporting a DIFFERENT claim — that deleting the
    # line would change no row on this corpus — which is the same thing the
    # instrumented count says directly and more narrowly.
    #
    # KEPT ANYWAY, as a PRECONDITION RESTATED AT THE RULE rather than as a
    # defence against anything that happens now, and the difference is worth a
    # reader's attention: the marker lives on the RECORD while the verdict is
    # attached to a BLOCK, and one record can emit several rows. A future call
    # site that widened what it passes — a record's content blocks unfiltered,
    # say — would otherwise hand a marked record's verdict to a block that is
    # not the tool's result at all. One dict lookup, on a path that already does
    # several.
    if block.get("type") != "tool_result":
        return None
    tool_use_result = rec.get("toolUseResult") if isinstance(rec, dict) else None
    if not isinstance(tool_use_result, dict):
        return None
    for key, kind in _HUMAN_RESULT_KEYS.items():
        if key in tool_use_result:
            return kind
    return None


def _human_origin(rec, block, tool_names):
    """The D2 human kind this record/block originates from, or None.

    Evaluated FIRST and UNCONDITIONALLY by `_authorship`, before any lane or
    shape rule. The reference projector's revision 1 ran the LANE rule first and
    leaked 10 real human interrupts as `{"kind":"dispatch","actor":"agent",…}`
    (measured 2026-07-26: 77 records whose entire stripped text is one of the
    two interrupt spellings, 67 classified human, 10 classified agent, across 5
    sessions — a person pressing Esc INSIDE a sub-agent). ORDERING IS THE
    GUARANTEE HERE, not an optimization, which is why
    test_event_skeleton.py drives an interrupt inside a sidechain lane
    specifically and why moving this call below the lane rule must go red."""
    body = _body_text(rec, block)
    # Interrupt first: it is the one kind that can appear in ANY lane and in any
    # text position, which is exactly how revision 1 lost it.
    if _INTERRUPT_TEXT in body:
        return "interrupt"
    if isinstance(rec, dict) and rec.get("toolDenialKind") == _DENIAL_KIND:
        return "denial"
    verdict = _human_result_kind(rec, block, tool_names)
    if verdict is not None:
        # A human-answered tool's result is human-authored EITHER WAY — the
        # person ruled/answered, or refused to — and the text says which. Both
        # lose the clock; only `kind` distinguishes them, which is why the suite
        # asserts the denial spelling of this rule too.
        # LABEL-SAFE FOR THE TOOL THIS RULE JUST GAINED, measured 2026-07-27:
        # of 351 real AskUserQuestion results, the 51 the person refused are
        # already classified `denial` before or inside this branch — 37 by
        # `toolDenialKind` (evaluated above) and 14 by this `startswith`, 0 by
        # `_DENIAL_RESULT`. So widening `_HUMAN_RESULT_TOOLS` relabels no
        # existing human row; it moves the OTHER 300, which were `agent` /
        # `tool_result` / CLOCKED, to human `answer` with no clock at all.
        return "denial" if body.startswith(_DENIAL_TEXT) else verdict
    if body.startswith(_DENIAL_TEXT):
        return "denial"
    tool_use_result = rec.get("toolUseResult") if isinstance(rec, dict) else None
    if isinstance(tool_use_result, str) and tool_use_result.startswith(_DENIAL_RESULT):
        return "denial"
    if body.startswith(_APPROVAL_TEXT):
        return "approval"
    return None


def _authorship(rec, block=None, tool_names=None):
    """THE single decision point: `(actor, kind)` for one emitted row.

    The ORDER is load-bearing and is the whole of D2's enforcement:
      1. human-origin markers — any lane, any position;
      2. shape rules;
      3. DEFAULT HUMAN. An unrecognized/future record shape never gets a clock:
         the allowlist is POSITIVE, so the failure mode of drift is a lost
         clock, never a fabricated one."""
    kind = _human_origin(rec, block, tool_names)
    if kind is not None:
        return "human", kind

    rec_type = rec.get("type") if isinstance(rec, dict) else None
    if rec_type == "assistant":
        return "agent", "assistant"
    if isinstance(block, dict) and block.get("type") == "tool_result":
        return "agent", "tool_result"
    if rec_type == "attachment":
        return "human", "prompt"        # only queued human prompts are emitted
    if rec_type == "user":
        if rec.get("isMeta") is True:
            return "agent", "context"   # harness-injected, e.g. the caveat
        if rec.get("isSidechain") and rec.get("agentId"):
            return "agent", "dispatch"  # orchestrator's prompt to a sub-agent
        if rec.get("promptSource") in _HARNESS_PROMPT_SOURCES:
            return "agent", "context"
        origin = rec.get("origin")
        if isinstance(origin, dict) and origin.get("kind") in _HARNESS_ORIGIN_KINDS:
            return "agent", "context"
        if _body_text(rec, block).lstrip().startswith(_HARNESS_PREFIXES):
            return "agent", "context"
    return "human", "prompt"


def is_agent_authored(rec, block=None, tool_names=None):
    """D2's ONE function: may the row this record/block produces carry a clock?

    `_event_row` consults NOTHING ELSE about clocks — that is the property that
    matters, and it is the one stated here. It is defined in terms of the SAME
    `_authorship` rules that produce `(actor, kind)`, so the predicate and the
    label can never disagree; `_event_row` calling both is deliberate rather
    than an oversight, because the alternative (writing the clock on
    `actor == "agent"` at the write site) states the D2 rule in a second place.
    The reference projector's revision-1 docstring named a chokepoint function
    that DID NOT EXIST — authorship was decided in three places — and that
    absence is what made the 10-interrupt leak possible; a docstring asserting a
    property nothing enforces is the failure this track has now hit three times.

    Cost of the second evaluation, measured 2026-07-26 on the largest real
    session (4,801 rows, one call per row): 18.3 ms for the whole projection
    against 14.8 ms with this pass stubbed out — 3.5 ms, inside a detached
    background sweep. The question is closed: that is not worth a second
    expression of D2."""
    return _authorship(rec, block, tool_names)[0] == "agent"


def build_event_skeleton(record_sets, roles=None):
    """The T2-C3 projection: `(rows, lanes)` for `record_sets`.

    `record_sets[0]` is the MAIN transcript, `[1:]` the subagent SIDECARS, in
    the same order and with the same optional positionally-parallel `roles`
    sequence `digest()` receives them — so one call site can feed both.

    PURE and NEVER RAISES ON ANY INPUT, the same discipline `digest()` holds to
    and for the same reason: this runs inside the SessionStart-spawned detached
    background sweep, which must never wedge. It is defensive PER FIELD (via
    `_as_sequence`, `_string_field`, `_int_or_zero` and the `isinstance` guards
    throughout) rather than wrapped in a blanket `except` — a swallowed
    exception would produce a session with rollups and no events,
    indistinguishable from a session that legitimately had none, which is a new
    silent-loss class in a module whose comments are a catalogue of silent-loss
    classes.

    THAT PROMISE IS NOW TESTED RATHER THAN ASSERTED. It was false when it was
    written: the uuid claim below did `uid in seen_uuids` with no hashability
    guard, so one drifted record with a dict/list `uuid` raised TypeError into
    `_insights_session._digest_one_session`'s `except Exception` and zeroed the
    whole session's tokens — an opt-in destroying accounting that works without
    it (see `_string_field` for the reproduction). `test_event_skeleton.py`'s
    fuzz corpus now drives this function, `digest(events=True)` and
    `ambient_outbox.build_events_payload`+`json.dumps` over generated drift
    (non-dict records and messages, non-list content, non-string and
    non-hashable uuid/parentUuid/timestamp/model/tool name, deep nesting, huge
    ints) and asserts each RETURNS. A docstring claiming a property nothing
    exercises is exactly what this track keeps shipping.

    `seq` is a PER-ROW monotonic index, NOT the transcript line index: one
    assistant record with N `tool_use` blocks emits 1+N rows, so a line index
    COLLIDES (measured 2026-07-26: a 57-way collision on one real session) and
    could not be part of a storage key.

    `seq` IS NOT A CLOCK ACROSS LANES. Measured 2026-07-26: 106 of 109 sessions
    with sidecars jump BACKWARD in time along seq order, worst case 431,733 s
    (5.0 days) at a main->sidecar boundary, because sidecars are concatenated
    after main rather than interleaved. So `seq` is a stable total order for
    storage and for within-lane sequence; any CROSS-LANE timing question must
    use `ts` on agent rows. Stated here because a consumer reading `seq` as a
    clock gets a five-day error.

    `lanes` hoists the per-lane ROLE out of the rows: a value identical on every
    row of a lane belongs to the container, not the row. The reference
    projector's revision 1 read `agentId` to classify and never emitted it, so
    all 62,215 sidecar rows carried an identical `isSidechain: true` and nothing
    else, and per-agent timelines were unreconstructable across the 88 sessions
    that have more than one sub-agent lane."""
    rows = []
    lanes = []
    tool_names = {}   # tool_use.id -> tool name, for the ExitPlanMode join
    seen_uuids = set()
    seq = 0
    for lane, record_set in enumerate(_as_sequence(record_sets)):
        # `_role_at` is the SINGLE writer of "what role does record set i have",
        # shared with `digest()`'s rollups on purpose: the rollup's `agent_role`
        # and this lane's `agentRole` describe the same record set, and two
        # copies of that rule would eventually disagree about it. The
        # UNATTRIBUTED_ROLE -> None mapping is the same one the rollup applies,
        # for the same reason: an unreadable/absent sidecar meta file is a fact
        # about the FILESYSTEM, not an agent role, so the lane declines to name
        # one rather than inventing a pseudo-role.
        role = _role_at(roles, lane)
        lanes.append({"lane": lane,
                      "agentRole": None if role == UNATTRIBUTED_ROLE else role})
        if not isinstance(record_set, list):
            continue
        for rec in record_set:
            if not isinstance(rec, dict):
                continue
            for row in _event_rows_for(rec, tool_names, lane):
                # A uuid is claimed AT MOST ONCE, first-seen wins, and lane 0 is
                # the main transcript — so main legitimately owns a block a
                # sidecar merely echoes. Claude Code copies the dispatching
                # `tool_use` block into the sub-agent's own transcript, so
                # reading main and sidecars additively double-counted it
                # (measured 2026-07-26: 10 duplicate-uuid groups, every one
                # `toolName: "Agent"`). This is deliberately the SAME
                # first-seen-wins claim `digest()` applies to `message.id` per
                # model, pointed at here so the two cannot drift into different
                # answers about what "the same event" means.
                #
                # ONLY A NON-EMPTY STRING IS AN IDENTITY, and the test is made
                # HERE rather than trusted from `_event_row` on purpose: `in`
                # and `.add` on a set REQUIRE a hashable operand, so the
                # precondition belongs to the set operation, not to the field.
                # `_string_field` already guarantees it — this line is what
                # keeps the NEVER-RAISES promise from depending on a guarantee
                # made in another function, which is the shape of the three
                # failures this track's own comments catalogue. It costs one
                # isinstance per row (measured: 4,801 rows, no change beyond
                # noise on the 18.3 ms projection).
                uid = row.get("uuid")
                if isinstance(uid, str) and uid:
                    if uid in seen_uuids:
                        continue
                    seen_uuids.add(uid)
                row["seq"] = seq
                seq += 1
                rows.append(row)
    return rows, lanes


def _event_rows_for(rec, tool_names, lane):
    """Every skeleton row ONE record produces, in order. `tool_names` is
    mutated: a `tool_use` block registers its name so a later `tool_result` can
    be joined back to it by `tool_use_id` (that join is what catches 6 of 6 real
    plan verdicts, where the text marker catches only 4)."""
    rec_type = rec.get("type")
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    content = msg.get("content")
    uuid = rec.get("uuid")
    parent = rec.get("parentUuid")
    sidechain = bool(rec.get("isSidechain"))
    ts = rec.get("timestamp")

    if rec_type == "assistant":
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        out = [_event_row(rec, None, tool_names, "assistant", uuid, parent,
                          sidechain, ts, lane,
                          modelId=_string_field(msg.get("model")),
                          inputTokens=_int_or_zero(usage.get("input_tokens")),
                          outputTokens=_int_or_zero(usage.get("output_tokens")),
                          cacheReadTokens=_int_or_zero(
                              usage.get("cache_read_input_tokens")),
                          cacheCreationTokens=_int_or_zero(
                              usage.get("cache_creation_input_tokens")))]
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id, name = block.get("id"), block.get("name")
                if isinstance(tool_id, str) and isinstance(name, str):
                    tool_names[tool_id] = name
                # The tool_use row's own identity is the TOOL_USE id and its
                # parent is the assistant record — that is what makes a call and
                # its result joinable without either carrying the tool INPUT.
                out.append(_event_row(rec, None, tool_names, "tool_use", tool_id,
                                      uuid, sidechain, ts, lane,
                                      toolName=_string_field(name)))
        return out

    if rec_type == "user":
        if isinstance(content, list):
            out = [_event_row(rec, block, tool_names, None, uuid, parent,
                              sidechain, ts, lane,
                              toolUseId=_string_field(block.get("tool_use_id")),
                              isError=bool(block.get("is_error")))
                   for block in content
                   if isinstance(block, dict) and block.get("type") == "tool_result"]
            if out:
                return out
            return [_event_row(rec, None, tool_names, None, uuid, parent,
                               sidechain, ts, lane)]
        if isinstance(content, str):
            return [_event_row(rec, None, tool_names, None, uuid, parent,
                               sidechain, ts, lane)]
        return []

    if rec_type == "attachment":
        attachment = rec.get("attachment")
        if (isinstance(attachment, dict)
                and attachment.get("type") == "queued_command"
                and attachment.get("commandMode") == "prompt"):
            # A human prompt that arrived QUEUED (142 measured rows). Emitted so
            # the sequence keeps its boundary; human, so it never carries the
            # clock it does in fact have on disk.
            return [_event_row(rec, None, tool_names, None, uuid, parent,
                               sidechain, ts, lane)]
        return []

    return []


def _is_real_int(value):
    """A real `int`: an `int` that is not a `bool`.

    Extracted 2026-08-20 so "a real int" has ONE definition shared by the
    write-side normalizer (`_int_or_zero`) and the emission-side admission rule
    (`_admits_int`) — the mirror of what `_is_real_number` does for the reward
    slot. Until then the predicate was inlined in both, and only the emission
    copy had a red case, so an edit to the WRITE copy shipped green. There is
    now ONE copy to edit and it IS red: verified 2026-08-20 by dropping the
    bool exclusion here — `test_event_skeleton.py` fails on
    `test_a_bool_is_never_admitted_as_a_token_count`, while
    `test_ambient_digest.py` and `test_pla2a_wire_contract.py` (the two
    files that exercise the write-side spelling) stay GREEN, which is the
    measurement behind the sentence above.

    `bool` is excluded because `True == 1`: a bool reaching a token count ships
    as a 1 that someone can SUM, and in the store it is indistinguishable from a
    measured token. Same reason `_int_or_zero` and `_is_real_number` exclude it.

    ⚠️ THE DEPENDENCY POINTS THIS WAY ROUND ON PURPOSE, and the other way round
    is the tempting mistake: `_int_or_zero` calls this LEAF, never `_admits_int`.
    The write side must not depend on an emission contract, or tightening the
    wire silently moves producer bytes — the split `_is_real_number`'s own
    docstring refuses for the reward rule, for the same reason."""
    return isinstance(value, int) and not isinstance(value, bool)


def _int_or_zero(value):
    """`value` if it is a real int (bools are not — `_is_real_int` is the shared
    definition), else 0 — and 0 is dropped by `_event_row`'s falsy filter, so a
    drifted usage number is ABSENT rather than fabricated as a zero someone
    could sum."""
    return value if _is_real_int(value) else 0


def _string_field(value):
    """`value` if it is a NON-EMPTY string, else None — and None is dropped by
    `_event_row`'s filter, so a drifted value is ABSENT rather than emitted.

    THE SINGLE NORMALIZER FOR EVERY STRING-TYPED FIELD A ROW CARRIES FROM THE
    TRANSCRIPT (`uuid`, `parentUuid`, `modelId`, `toolName`, `toolUseId`, `ts`),
    and it is the exact rule `_usage_dedup.dedup_key` already states in words for
    `message.id`: "list/dict are unhashable (a set lookup RAISES), and 1 == True
    collide across genuinely distinct values". That rule was written for the
    token path and simply never applied to the skeleton's own identity claim —
    which is how `build_event_skeleton`, whose docstring promises it NEVER
    RAISES, came to do `uid in seen_uuids` on whatever the transcript put there.
    A dict/list `uuid` raised TypeError out of the projector, into
    `_insights_session._digest_one_session`'s `except Exception`, and ZEROED THE
    WHOLE SESSION: reproduced through the real sweep on one drifted record —
    `events=False` gave `rollups=1 tokens={'input_tokens': 1200,
    'output_tokens': 1010} parserDegraded=False`, `events=True` gave `rollups=0
    tokens={} parserDegraded=True`. Enabling an OPT-IN destroyed the token
    accounting of a session that digests perfectly without it.

    THREE separate properties, all of them load-bearing, which is why one helper
    owns all six fields instead of a guard being added where the crash was:

      * HASHABILITY — `seen_uuids` is a set and `tool_names` is a dict; an
        unhashable member/key raises. This is the reported defect.
      * NO UNBOUNDED CONTENT ON THE WIRE — a row is structure only. An
        unvalidated transcript value flowing into `uuid`/`modelId`/`toolUseId`
        carries whatever a drifted (or hostile) producer put there into a
        payload whose whole claim is that it carries no content, and
        `_events_wire_bytes` would then `json.dumps` an arbitrarily nested
        object inside `drain`'s send loop.
        THE CLAIM IS BOUNDED, NOT ABSOLUTE, AND THE FIRST VERSION OVERSTATED IT.
        Rejecting non-strings alone left an arbitrary-length STRING flowing
        straight through: review demonstrated a 281-character secret planted in
        `message.model` and `tool_use.name` arriving verbatim in the POSTed
        body. So the rule is also a LENGTH bound, `_MAX_FIELD_LEN`. What that
        buys is honest and narrow — a field cannot become a smuggling channel
        for a payload of arbitrary size — and what it does NOT buy is any
        guarantee about a SHORT hostile string, which is indistinguishable from
        a legitimate model or tool name and is accepted. The real defence for
        that is upstream: these six fields are harness-authored identifiers and
        a harness-authored clock, not user text.
        The bound is 200, DERIVED not guessed: measured 2026-07-27 over the
        164,365 rows the shipped projector emits from this machine's whole
        corpus (269 sessions under `~/.claude/projects/*/*.jsonl` plus their
        `<sid>/subagents/agent-*.jsonl` sidecars), the longest real value of any
        of the six is `toolName` at 56 characters (uuid/parentUuid 36, toolUseId
        30, modelId 26, `ts` 24 on all 162,008 clocked rows). 200 leaves 3.5x
        headroom over the longest real value and matches
        `_insights_session._MAX_SESSION_ID_LEN`, the in-repo precedent for
        bounding a harness-supplied identifier. Over-length DROPS the field
        rather than truncating it — a truncated identifier is a wrong
        identifier, and this module never silently shortens a value it is
        unwilling to carry whole. For `ts` that direction is a LOST CLOCK, never
        an unbounded one, which is the same way every other D2 failure in this
        module is pointed.
      * "" IS NOT AN IDENTITY — the emit filter drops None/False/0 but KEEPS an
        empty string, so a transcript with `"uuid": ""` on many records had
        every one of them claim the same key and all but the FIRST were silently
        dropped. Non-empty-only makes them unclaimable, so they are all kept.

    Real transcripts are unaffected: over the 273-session corpus every one of
    these six fields is either a non-empty string or absent, so the emitted
    bytes do not move (the events conformance fixture is the pinned proof)."""
    if not isinstance(value, str) or not value:
        return None
    return value if len(value) <= _MAX_FIELD_LEN else None


# See `_string_field`'s NO UNBOUNDED CONTENT clause for the derivation and the
# measurement date. Public-adjacent on purpose: the test that pins the bound
# imports this rather than repeating 200, so the constant and its guard cannot
# drift into disagreeing about what the bound is.
_MAX_FIELD_LEN = 200


# RESERVED (JC5). Nothing writes `reward` today. The exemption exists NOW
# because the falsy filter would eat a 0 reward, and "this step earned nothing"
# must never be byte-identical to "no reward assigned" — the exact distinction a
# training corpus is bought for. The filter is a byte saving, not a boundary (see
# its own comment at the end of `_event_row`), so one named exemption costs
# nothing. The event-skeleton row is the only per-STEP shape that exists — a
# rollup is an aggregate and has thrown the sequence away — so this is where a
# per-step reward can go at all.
#
# THE TYPE GUARD IS NOT DECORATION. An untyped exemption passes ANY value under
# this key straight onto the wire without going through `_string_field`, which is
# exactly how "content-free by shape rather than by inspection" in `_event_row`
# becomes false without anyone editing the notice. A reward is a number or it is
# not a reward. `bool` is excluded explicitly for the reason `_int_or_zero`
# excludes it: `True == 1`, so a bool slipping in would ship as a reward of 1.
_RESERVED_REWARD_KEYS = ("reward",)


def _is_real_number(v):
    """A real number: an `int` or a `float`, and NOT a `bool`.

    Extracted so "a reward is a number" has ONE definition shared by the
    write-side falsy-filter exemption (`_is_reserved_numeric`) and the
    emission-side admission rule (`EVENT_FIELD_RULES["reward"]`). `bool` is
    excluded for the reason `_int_or_zero` excludes it: `True == 1`, so a bool
    slipping in would ship as a reward of 1.

    ⚠️ NON-FINITE FLOATS ARE ADMITTED HERE ON PURPOSE, and the emission rule is
    where they are closed instead. `float("nan")`/`float("inf")` satisfy this
    predicate and `json.dumps` emits them as bare `NaN`/`Infinity`, which is not
    valid JSON. Adding `math.isfinite` HERE would change `_is_reserved_numeric`,
    i.e. the WRITE side, which `test_a_zero_reward_survives_the_falsy_filter_and_
    prose_does_not` pins with four cases none of which is non-finite — a
    producer-byte change smuggled in as a guard. See `_admits_number`."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_reserved_numeric(k, v):
    """True for a RESERVED key carrying a real number (JC5, §F.6) — the one
    named exemption from `_event_row`'s falsy filter."""
    return k in _RESERVED_REWARD_KEYS and _is_real_number(v)


# --------------------------------------------------------------------------- #
# JC14 — THE EMISSION-SIDE PROJECTION for `events[]`/`lanes[]`.
#
# The per-key guarantees this module makes are all made at RECORDING time, and a
# laptop-local JSONL spool (`~/.fairmind/insights/rollups/<tenancy>.jsonl`) sits
# between recording and emission: `ambient_outbox.build_events_payload` used to
# lift `row["events"]`/`row["lanes"]` off that file behind nothing but an
# `isinstance(..., list)` degrade and POST them. So `_string_field`'s three
# properties, `_int_or_zero`'s type test and `_is_reserved_numeric`'s were
# enforced on the way IN and on nothing on the way OUT. These declarations plus
# the two projectors below re-apply them at the wire.
#
# WHAT THIS IS NOT: `EVENT_ROW_KEYS` (above) is the two SPOOL-ROW keys, `events`
# and `lanes`. `EVENT_FIELD_RULES` is the per-ROW field set INSIDE them. Two
# closed sets one screen apart with near-identical names; they are different
# things and neither may be derived from the other.
#
# THE SPOOL OUTLIVES THE CODE. Retention is unbounded while a skeleton is
# pending — `ambient_outbox._attempt_events`'s cross-drain retry lane keeps the
# row until it resolves — so a row written by an older plugin version is emitted
# by a newer one. Consequence: THIS DECLARATION MAY ONLY EVER GROW. Removing a
# key from it silently strips rows already on disk that no current producer path
# can regenerate. Verified 2026-08-20 that no legacy key exists today: the
# `_event_row` row literal has been unchanged since `ba98ea8`, the commit that
# introduced the skeleton.
#
# THE ONE CLASS OF PRODUCER OUTPUT THIS DELIBERATELY NARROWS, stated rather than
# discovered later: a spool row written BEFORE the 2026-07-27 `ts` bound landed
# can carry an unbounded clock, and now loses it at emission. That is the card's
# intent; it is also the only well-formed producer output the projection reduces.
#
# ⚠️ THAT SENTENCE WAS FALSE ON THE DAY IT WAS WRITTEN AND BECAME TRUE ON
# 2026-08-20. The first draft of this projection ALSO bounded `lanes[].agentRole`
# at 200 characters, which made a second class — every role string longer than
# that, a range `_insights_session._sidecar_role` really produces — and there the
# key was DELETED, not a clock lost. The bound was removed before shipping (see
# `_admits_role` for the reproduction and for the rule that decides the next case
# like it). The count is corrected HERE, at the claim, rather than quietly made
# true elsewhere: a sentence that was wrong when written tells the next reader
# something a sentence that merely reads right today cannot.
#
# CONSEQUENCE FOR THE TABLE, same date: `kind`'s emission rule ADDS a bound
# rather than restoring one — it comes straight from `_authorship` and never
# passes `_string_field`. It stays because it is BYTE-INERT against producer
# output: every value it can hold is a module literal (`assistant`, `context`,
# `dispatch`, `tool_result`, `tool_use`, `prompt` — the longest 11 characters),
# so no well-formed row can reach the bound, while a TAMPERED spool row carrying
# prose under `kind` is exactly what it stops.
#
# ⚠️ NO COUNT IS CLAIMED HERE, AND THE MISSING NUMBER IS THE POINT. An earlier
# draft of this paragraph called `kind` the ONLY such key and closed the list on
# it. That was wrong on the reading the paragraph uses: `actor` sits in the same
# position — `_admits_actor` is `v in EVENT_ACTORS`, a membership rule with no
# write-side counterpart, since `_authorship` performs no membership check
# anywhere (verified 2026-08-20 by reading every `return` in `_authorship` and
# `_human_origin`: all of them are module literals) and `_event_row` writes
# `"kind": kind, "actor": actor` RAW, beside `uuid`/`parentUuid` which do pass
# `_string_field`. Others may sit there too; this comment has not enumerated
# them and will not pretend to. That such keys EXIST is the durable fact — how
# MANY is a set nobody has closed, and a count is what made this sentence wrong
# the first time. Deliberately NOT done: replacing "one" with "two", which is
# the same move one iteration later. A guard proposed for this table earns its
# place with a containment proof — the producer's range inside the predicate,
# as `_admits_role` spells out — never by being absent from a list here.
# --------------------------------------------------------------------------- #

# The CLOSED set `_authorship` returns — every `return` in it and in
# `_human_origin` is `("agent", …)` or `("human", …)`, and the server accepts
# exactly that closed set.
#
# ⚠️ THIS MUST STAY A TUPLE AND MUST NEVER BECOME A SET/FROZENSET. `_admits_actor`
# is `v in EVENT_ACTORS`: against a tuple that is an `==` scan, safe for ANY
# value; against a set it hashes and RAISES `TypeError: unhashable type` on a
# tampered `actor: {"a": 1}`. `build_events_payload` runs inside
# `ambient_outbox.drain`'s send loop, where that exception costs the whole batch
# — the identical defect class `_string_field`'s docstring catalogues (`uid in
# seen_uuids` raising into a blanket except and zeroing a session). Pinned by
# `test_a_drifted_actor_never_reaches_the_wire`, which drives an unhashable
# actor through and asserts the call RETURNS.
#
# What the rule buys, stated correctly because a comment asserting a property
# nothing depends on is its own defect: a closed emitted vocabulary matching the
# server's enum, and for a drifted value only a change in WHICH 422 the door
# returns. It is NOT what makes D2 fail-closed — the `ts` rule below is
# `clean.get("actor") != "agent"`, which drops the clock for any drifted value
# with or without this enum.
EVENT_ACTORS = ("agent", "human")


def _admits_string(v):
    """The emission-side spelling of `_string_field`'s predicate — non-empty
    `str`, at most `_MAX_FIELD_LEN`. Over-length DROPS the key; a truncated
    identifier is a wrong identifier."""
    return _string_field(v) is not None


def _admits_int(v):
    """A real `int`. `bool` excluded: `True` would ship as one token.

    The emission-side spelling of the write side's `_int_or_zero`, and the two
    share `_is_real_int` so they cannot drift into disagreeing about what an
    integer is. They differ only in the FAILURE MODE, which is the whole reason
    both exist: the writer substitutes 0 (then dropped by the falsy filter), the
    wire drops the key."""
    return _is_real_int(v)


def _admits_bool(v):
    """A real `bool`. NOT folded into `_admits_int`: `True == 1`, and an int here
    coerces server-side into a sidechain claim nobody made."""
    return isinstance(v, bool)


def _admits_actor(v):
    return v in EVENT_ACTORS


def _admits_role(v):
    """`None` OR a non-empty string — `_role_at`'s own contract, RESTATED at the
    wire and deliberately not narrowed.

    The `None` half is load-bearing and is what the pinned conformance fixture
    cannot see: `agentRole: None` means "no role was attributable" (the
    `UNATTRIBUTED_ROLE` mapping in `build_event_skeleton`), the server declares
    `Optional[str] = None`, and it occurs on real lanes. A rule that ran the
    bare string predicate here would delete the key from every unattributed lane
    with the fixture still green.

    ⚠️ THIS RULE CARRIED A 200-CHARACTER BOUND FOR ONE ROUND. IT WAS REMOVED
    2026-08-20, BEFORE SHIPPING, ON TWO INDEPENDENT EXTERNAL REVIEWS THAT BOTH
    REFUSED THE DIFF OVER IT. Recorded rather than deleted, because this is the
    rule that decides the next key someone proposes to guard.

    JC14 RE-APPLIES A WRITE-SIDE RULE AT EMISSION; IT DOES NOT INVENT ONE.
      * `ts` HAS a write-side rule — `_string_field`, since 2026-07-27 — so
        bounding it at emission RESTORES that rule, and narrowing a
        pre-2026-07-27 legacy spool row is the card's stated intent (see the
        block comment above). IN SCOPE.
      * `agentRole` has NEVER had one, on either side.
        `_insights_session._sidecar_role` ends `return agent_type` with no
        length test at all, and `_role_at` forwards any non-empty string. So the
        producer's range S is EVERY non-empty string while the predicate P was
        `len <= 200`, and P does not contain S. REPRODUCED 2026-08-20:
        `build_event_skeleton([[], []], ["main", "R"*201])` writes the lane
        `{"lane": 1, "agentRole": "RRR…"}` and the bounded rule emitted
        `{"lane": 1}` — the key DELETED off well-formed producer output. That is
        a contract change wearing a guard's clothes, and JC14 is a
        no-wire-change card. OUT OF SCOPE.
      * The justification for the bound was a corpus MAXIMUM of 33 characters.
        A corpus maximum is a sample of the producer's range, never the range
        itself — the same "sample as S" move that had already been rejected
        twice on the sibling audit-door card.

    WHAT SETTLED IT, because the containment argument alone could have been
    answered by moving the bound upstream: the bound was applied to
    `lanes[].agentRole` and NOT to `agents[].agentRole` on the ACTIVITY door
    (`ambient_outbox._agents_from_rollups`, reached from `build_wire_payload`),
    which carries the same `_sidecar_role` string. A 201-character role was dropped on the events door
    and still POSTed on the other one — incoherent as containment as well as
    byte-moving. Bounding at `_sidecar_role` instead only moves the same byte
    change one module upstream and owes its own containment proof; that is a
    card, not a guard to smuggle in here.

    AND THE BOUND WOULD HAVE FALSIFIED TWO STANDING RECORDS WITHOUT ANYONE
    EDITING THEM: "`lanes[].agentRole` is unbounded" is a DECLARED DEFERRED
    residual of the 2026-07-30 notice round, written down at
    `_insights_session.py` (the `WHERE IT GOES` paragraph, "the seventh …
    stays recorded as deferred") and pinned in
    `tests/test_t2c3_optin_delivery.py` beside the same list. Closing a declared
    residual silently is worse than leaving it open: the register is the thing a
    reader trusts, and a register nobody updates is one nobody can use.

    THE ROLE CORPUS, kept because it is the measurement the removed bound was
    justified by and the next proposal will reach for it. MEASURED 2026-08-20 by
    running the shipped producer over `~/.claude/projects` WITH THE ROLE CHANNEL
    WIRED AS PRODUCTION WIRES IT (`_insights_session._discover_sidecars`), 258
    sessions carrying a skeleton, 507 lane entries: 50 distinct role strings, the
    longest observed 33 characters, and `None` PRESENT. The `None` count is
    deliberately not quoted as a closure — it was 1 on that read and 249 on a
    read of the same corpus with the role channel NOT wired, the corpus is
    written while it is read, and an unreadable/absent `agent-*.meta.json` on
    any machine produces one. `None` is a legitimate wire value that occurs;
    how OFTEN is a property of the machine, not of the rule. Quote the wiring
    with any number taken here, because the wiring is what moved it 250x.

    NOT DONE, deliberately: the predicate is spelled out inline instead of being
    extracted into a helper shared with `_role_at`. `_role_at` is PRODUCER code,
    and an emission rule must never become something the write side imports —
    the same direction `_is_real_int` and `_is_real_number` are careful about.
    What holds the two together is `test_event_skeleton.py`, which plants an
    over-length role through the real builder and asserts it ARRIVES."""
    return v is None or (isinstance(v, str) and v != "")


def _admits_number(v):
    """A real number that `json.dumps` can actually serialize as JSON.

    The reserved `reward` slot's emission rule. It is `_is_real_number` PLUS
    finiteness, and the split is deliberate: the write-side exemption keeps the
    bare predicate (changing it would move producer bytes), while the wire
    refuses `NaN`/`Infinity`, which `json.dumps` emits BARE and which
    `json.loads` parses off a spool JSONL line by default — so a hand-edited
    spool could round-trip a non-finite float into a POSTed body that is not
    JSON. Byte-inert: nothing writes `reward` today."""
    return _is_real_number(v) and (not isinstance(v, float) or math.isfinite(v))


# The per-ROW field set `events[]` admits at emission, and the rule each key is
# admitted by. All 16 keys the producer writes (enumerated from the six
# `_event_row(` call sites plus `build_event_skeleton`'s `seq` write) plus the
# reserved `reward`. See the block comment above for why this may only grow.
EVENT_FIELD_RULES = {
    "kind": _admits_string,
    "actor": _admits_actor,
    "uuid": _admits_string,
    "parentUuid": _admits_string,
    "isSidechain": _admits_bool,
    "lane": _admits_int,
    "ts": _admits_string,
    "seq": _admits_int,
    "modelId": _admits_string,
    "inputTokens": _admits_int,
    "outputTokens": _admits_int,
    "cacheReadTokens": _admits_int,
    "cacheCreationTokens": _admits_int,
    "toolName": _admits_string,
    "toolUseId": _admits_string,
    "isError": _admits_bool,
    "reward": _admits_number,
}

# The per-ENTRY field set `lanes[]` admits. `lane` is NOT falsy-filtered here,
# unlike the row key of the same name — `lane: 0` is the main transcript's lane
# entry and is present on every session. A projection written once and reused
# for both arrays would delete it.
LANE_FIELD_RULES = {"lane": _admits_int, "agentRole": _admits_role}


def _project(item, rules):
    """One spool element -> what the producer could have written.

    ⚠️ ITERATION IS OVER `item.items()`, NEVER OVER `rules`, AND THAT IS
    LOAD-BEARING. `json.dumps` on the wire path takes no `sort_keys`, so key
    ORDER is part of the emitted octets. Driving the comprehension off the rules
    table instead emits `…"ts", "seq", "modelId"` where the producer emits
    `…"ts", "modelId", "seq"` — different bytes, EQUAL dicts — and
    `test_events_conformance.py` compares with `json.dumps(..., sort_keys=True)`
    against a fixture stored key-sorted, so it is BLIND to exactly that. This is
    not a line to "simplify"; `test_the_projection_is_byte_identity_on_producer_
    output` is what holds it."""
    if not isinstance(item, dict):
        return {}
    return {k: v for k, v in item.items() if k in rules and rules[k](v)}


def project_event_rows(events):
    """`events` as the wire may carry it: every key allowlisted by name and
    admitted by its own rule, in the producer's own insertion order.

    THE FAILURE MODE, decided rather than inherited:
      * an UNDECLARED key -> dropped. The row survives.
      * a declared key whose value fails its rule -> the KEY is dropped, never
        truncated, never repaired. The row survives.
      * a non-dict element -> `{}`. The element COUNT is preserved.
      * `ts` on a row whose ADMITTED `actor` is not `"agent"` -> dropped (D2).
      * nothing is ever row-fatal or batch-fatal, and nothing raises.

    WHY NEVER ROW-FATAL, since the alternative is tempting: (a) it is the write
    side's own answer, pinned by `test_an_overlong_string_field_never_reaches_
    the_wire` — "dropping the field is the fix, dropping the row would be a
    quieter version of the same defect"; (b) an all-tampered row would project
    to `events: []`, which `build_events_payload`'s docstring calls "a positive
    claim that the session had no steps … a fabricated measurement, not a
    redaction"; (c) it would mint a second row count, and `_attempt_events`
    compares `len(row["events"])` against `len(payload["events"])` — under
    key-drop those are equal by construction, which the tests assert.

    THE COST, stated: `seq`, `kind`, `actor` and `lanes[].lane` are REQUIRED
    server-side (`lanes[].lane` is `int = Field(ge=0)` with NO default, unlike
    `events[].lane` which defaults to 0), so a tampered row that loses one of
    them 422s, and 422 is a permanent dead-letter for the whole batch. That
    trades a silently STORED prose value for a loud local dead-letter, and it is
    not new damage — such a row 422s today too, with the prose in the request
    body on the way."""
    # THE NON-LIST DEGRADE IS A RELOCATED GUARD, NOT A NEW ONE: it used to read
    # `events if isinstance(events, list) else []` inside
    # `ambient_outbox.build_events_payload` and moved here with the projection,
    # so the module that owns the declaration also owns the degrade. Its red
    # lives in ANOTHER FILE — `test_t2c3_optin_delivery.py`'s
    # `test_response_body_and_row_shapes_are_read_defensively`, which drives
    # `"nope"`, `3`, `{"a": 1}` and `None` through `build_events_payload` and
    # asserts `[]`. Verified 2026-08-20 by returning `events` unchanged here:
    # that case reds, and every case in `test_event_skeleton.py` stays GREEN.
    # Named because a guard whose only red is elsewhere is one a reader working
    # in this file will believe is untested.
    if not isinstance(events, list):
        return []
    out = []
    for row in events:
        clean = _project(row, EVENT_FIELD_RULES)
        # D2 at the emission boundary, and fail-closed by construction: if
        # `actor` was itself rejected the key is absent, `.get` returns None,
        # and the clock goes. The server enforces this by rejecting the WHOLE
        # batch (`_reject_human_clock`), so leaving it to the store would trade
        # a stripped field for a permanent dead-letter.
        if clean.get("actor") != "agent":
            clean.pop("ts", None)
        out.append(clean)
    return out


def project_lanes(lanes):
    """`lanes` as the wire may carry it. Same rules, different table — see
    `LANE_FIELD_RULES` for the `lane: 0` asymmetry that makes reusing the row
    projector here a fixture-reddening mistake."""
    # Same relocated degrade, same holder, verified the same way — see
    # `project_event_rows`.
    if not isinstance(lanes, list):
        return []
    return [_project(lane, LANE_FIELD_RULES) for lane in lanes]


def _event_row(rec, block, tool_names, kind_override, uuid, parent, sidechain,
               ts, lane, **extra):
    """ONE skeleton row. The only place a `ts` is ever written."""
    actor, kind = _authorship(rec, block, tool_names)
    # `kind_override` names the STRUCTURE of the row (an assistant turn, one
    # tool_use block) where the classifier only knows the record. It never
    # overrides the ACTOR, and it never applies to a row the classifier called
    # HUMAN — otherwise a human interrupt inside an assistant record would be
    # relabelled by structure and silently re-clocked, which is the reference
    # projector's defect 1. That composite is a FAIL-SAFE, not an observed case:
    # measured 2026-07-26, 0 of 87,169 real assistant records in the
    # 273-session corpus carry an interrupt marker. It is pinned by
    # test_event_skeleton.py with a synthetic record anyway, because an
    # unexercised fail-safe is indistinguishable from a missing one.
    if kind_override is not None and actor == "agent":
        kind = kind_override
    # EVERY string-typed field is normalized HERE, at the one row constructor,
    # and nowhere else — see `_string_field` for the three properties that
    # depends on. `isSidechain` is `bool(...)` at its source and the token ints
    # go through `_int_or_zero`, so after this line every value in the row is a
    # str, a bool or an int: hashable, JSON-scalar, content-free by shape rather
    # than by inspection.
    #
    # AMENDED (JC5, §F.6), before the slot is used rather than after: the RESERVED
    # `reward` key is the one permitted FLOAT, and it is the only value in a row
    # that does not pass through `_string_field` or `_int_or_zero` — there is no
    # normalizer for a float, so its guard is the type test in
    # `_is_reserved_numeric` at the filter below, which drops anything that is not
    # a real number. Nothing writes it today; the sentence is amended now because
    # a comment that becomes false the moment the slot is used is a comment nobody
    # will re-read then.
    row = {"kind": kind, "actor": actor,
           "uuid": _string_field(uuid), "parentUuid": _string_field(parent),
           "isSidechain": sidechain, "lane": lane}
    # THE D2 ENFORCEMENT POINT, and the only one: a human-authored row has no
    # `ts` key to populate. Not "set to None" — ABSENT, so a later widening of
    # the field set cannot fill it in by accident, and so the JSON bytes carry
    # no evidence that a clock was ever considered.
    #
    # `ts` GOES THROUGH `_string_field` LIKE EVERY OTHER STRING THE ROW CARRIES.
    # It did not until 2026-07-27 — it had its own inline `isinstance(ts, str)
    # and ts`, which is the hashability and the emptiness halves of that helper
    # and NOT the length bound, so `ts` was the one transcript-supplied string on
    # the wire with no bound at all while `_string_field`'s docstring argued at
    # length for why the bound exists. A guarantee reasoned about in one place
    # and skipped at one call site is how the three defects catalogued in this
    # module started. Not a live leak — the real corpus's `ts` is 24 characters
    # on all 162,008 clocked rows, measured 2026-07-27 — so this closes the hole
    # rather than repairing damage.
    if is_agent_authored(rec, block, tool_names):
        clock = _string_field(ts)
        if clock is not None:
            row["ts"] = clock
    row.update({k: v for k, v in extra.items() if v is not None})
    # FALSY VALUES ARE DROPPED, not emitted: `lane: 0` (the main transcript),
    # `isSidechain: False`, a zero token count and an absent parentUuid are all
    # the DEFAULT, and the default is what absence means. Measured 2026-07-26
    # over the 273-session corpus: 44.28 MB of rows filtered against 47.51 MB
    # with every dropped default restored, so the filter is 6.8% of the
    # unfiltered bytes. A byte saving, then, NOT a privacy or correctness
    # boundary — but it IS the emitted field set, and lane 0 is the most common
    # lane in every session, so test_event_skeleton.py asserts the ABSENCE
    # rather than reading `row.get("lane", 0)` back as 0: a future edit that
    # stops filtering changes every row on the wire while every "value is 0"
    # assertion stays green.
    #
    # ONE NAMED EXEMPTION (JC5, §F.6): a RESERVED key is admitted by the TYPE
    # GUARD ALONE, so a numeric reward survives even at 0 — where the default and
    # the measurement are different facts — and a non-numeric one is dropped.
    # Written as an exemption rather than as "stop filtering" for exactly the
    # reason the paragraph above gives: dropping the filter would change EVERY row
    # on the wire, and `test_event_skeleton.py` asserts the ABSENCE of `lane: 0`
    # precisely so that edit goes red.
    #
    # ⚠️ THE GUARD REPLACES THE FALSY TEST FOR A RESERVED KEY; IT DOES NOT SIT
    # BESIDE IT, and the difference is the whole guarantee. Written as
    # `_is_reserved_numeric(k, v) or (<falsy filter>)` — the obvious spelling, and
    # the one this change was drafted with — the guard is pure decoration: a
    # non-empty string and `True` are both non-falsy, so `reward: "some prose"`
    # and `reward: True` sail through the second clause untouched. That is exactly
    # the prose channel the guard exists to close, and it was reproduced here
    # before the branch was rewritten (2026-08-14): `_event_row(..., reward=
    # "prose")` emitted `{'kind': 'assistant', 'actor': 'agent', 'uuid': 'u1',
    # 'reward': 'prose'}`. A reserved key gets ONE admission rule, not two ORed
    # ones. `test_event_skeleton.py` pins all four cases.
    return {k: v for k, v in row.items()
            if (_is_reserved_numeric(k, v) if k in _RESERVED_REWARD_KEYS
                else (v is not None and v is not False and v != 0))}


def _read_jsonl(path):
    """Parsed records from a JSONL file at `path`, skipping blank/unparseable
    lines. A missing file yields an empty list (never raises) — the sweep's
    common "session ended, transcript not yet flushed" case must degrade to an
    empty transcript, not crash."""
    if not path or not os.path.isfile(path):
        return []
    records = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        return []
    return records


def digest_transcript_file(main_path, meta, sidecars=(), *, events=False):
    """Read `main_path` (+ any sidecars) as JSONL, build the `record_sets`
    `digest()` expects, and return its result. A missing/unreadable file (main or
    sidecar) degrades to an empty record_set rather than raising.

    `events` (T2-C3) is KEYWORD-ONLY, OFF by default, and forwarded verbatim to
    `digest()` — this function adds no projection logic of its own, so there is
    exactly one implementation of the skeleton no matter which entry point the
    sweep uses.

    `sidecars` accepts EITHER shape, per entry:

      * a `(path, agent_role)` PAIR, as `_insights_session._discover_sidecars`
        returns them — the production form;
      * a bare path STRING, meaning "no role is known for this one", which is
        grouped under `UNATTRIBUTED_ROLE`.

    T2-C1 briefly carried a second parameter, `sidecar_paths=`, for the
    string-only form. It was removed once review established that it had no
    caller anywhere and that `sidecars=` already accepted everything it did.
    The docstring defending it argued that overloading one parameter would be
    silently catastrophic — a tuple reaching `_read_jsonl`, whose
    `os.path.isfile(tuple)` raises TypeError, which
    `_insights_session._digest_one_session` catches and turns into
    `digest([], meta)`, zeroing the WHOLE session with nothing red anywhere.
    That failure is real, and it is NOT what a second name prevented: what
    prevents it is the defensive parsing below, which one parameter carries
    just as well. Recorded because the reason outlived its own premise, which
    is how a shim survives review.

    Entries are parsed defensively (a non-string path, or a malformed pair, is
    skipped) — this function is on the sweep's never-raise path."""
    record_sets = [_read_jsonl(main_path)]
    roles = [MAIN_ROLE]
    for entry in _as_sequence(sidecars):
        if isinstance(entry, str):
            path, role = entry, UNATTRIBUTED_ROLE
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            path, role = entry[0], entry[1]
        else:
            continue
        if not isinstance(path, str):
            continue
        record_sets.append(_read_jsonl(path))
        # Appended RAW: `_role_at` is the single writer of "what counts as a
        # usable role, and what a missing one falls back to". Normalizing here
        # too would put that rule in two places — the exact split this change
        # removed for the dedup rule by extracting `_usage_dedup.dedup_key`.
        # Equivalent because every entry here lands at index >= 1 (roles[0] is
        # the main transcript's), where `_role_at`'s default IS
        # UNATTRIBUTED_ROLE.
        roles.append(role)
    return digest(record_sets, meta, roles=roles, events=events)
