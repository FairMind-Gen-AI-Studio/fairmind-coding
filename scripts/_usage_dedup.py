#!/usr/bin/env python3
"""Single source of truth for per-message.id token-usage dedup (PCF-15).

A streamed assistant message repeats its `usage` block once per content block,
every repeat carrying the SAME `message.id`, so a naive per-record sum
over-counts (~2.7x seen in the wild). Deduping by message.id before summing is
the fix.

Both the SubagentStop capture hook (`hooks/scripts/capture-subagent-tokens.sh`)
and PL-A1's Python digester import THIS helper, so the two can never drift — a
bash-only inline dedup could not be imported, which would make that "can't
drift" guarantee a fiction.

stdlib only: importable with no third-party dependency.
"""

# The four canonical Anthropic usage fields we sum. A missing field counts as 0.
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


# The model Claude Code stamps on records it synthesizes rather than receives
# from the API. They are EXCLUDED from token accounting entirely — even when
# they carry non-zero usage — because they are not a billed exchange.
SYNTHETIC_MODEL = "<synthetic>"


def accounting_model(rec):
    """The model `rec`'s tokens are accounted under, or None when `rec` is OUT
    of token accounting altogether.

    THE ONE DEFINITION OF "IS THIS RECORD IN THE TOKEN ACCOUNTING", asked by
    every reader that sums or buckets usage: `ambient_digest.digest` (fleet-
    facing) and `loop_tokens._deduped_per_model` (developer-facing). It sits
    beside `dedup_key` because the two questions are decided together and a
    reader that disagrees about MEMBERSHIP produces a different number even when
    it agrees about repeats.

    IT WAS TWO DEFINITIONS UNTIL 2026-07-28, and the split is why it is here.
    PCF-27 routed the developer-facing sum through this module's dedup and left
    the membership rule behind: the fleet reader skipped `<synthetic>` and
    model-less records, the developer reader did not, so the same session
    produced two numbers again — one skip away from the fix for exactly that.
    Measured on the corpus that day: 53 `<synthetic>` usage-bearing records
    across 30 of 266 transcripts, all four token fields zero on every one, and
    none sharing a `message.id` with a real-model record. So the divergence was
    LATENT rather than live — and `ambient_digest`'s own comment says these are
    excluded "even non-zero usage", i.e. the shape it guards against is one the
    corpus simply has not produced yet.

    None for: a non-dict record, no `message` dict, no `usage` DICT, the
    synthetic model, or a missing/non-string/empty model. Never raises."""
    if not isinstance(rec, dict):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
        return None
    model = msg.get("model")
    if not isinstance(model, str) or not model or model == SYNTHETIC_MODEL:
        return None
    return model


def dedup_key(rec):
    """The key `rec` is deduped under, or None when `rec` must NEVER be deduped.

    This is the ONE definition of "may this record be suppressed as a repeat",
    extracted so `deduped_usage_totals` below and `ambient_digest.digest`'s own
    per-model cross-bucket guard cannot drift apart. They HAVE to agree: two
    copies of the rule would mean one could start suppressing a row the other
    counts.

    WHERE each one actually suppresses, since it is easy to assume both do and
    the assumption is load-bearing in the wrong direction:

      * `ambient_digest.digest` claims an id at most once per MODEL *before*
        bucketing, so it can never span two `(agent_role, model)` buckets and
        be summed twice. Every record it then hands to `deduped_usage_totals`
        already carries an id unique within its model — so that call's own
        dedup pass suppresses NOTHING. It is a no-op there, not a safety net.
      * `deduped_usage_totals`' dedup is still fully live for its OTHER
        caller, `hooks/scripts/capture-subagent-tokens.sh`, which sums a single
        transcript with no notion of models or buckets. That is where PCF-15 —
        a streamed assistant message repeating its `usage` block once per
        content block, ~2.7x over-count — is actually suppressed.

    Consequence worth stating plainly: the ambient test suite cannot protect
    that branch, because the ambient path no longer depends on it. Deleting it
    as "provably dead" would leave every ambient test green and regress the
    hook. `test_usage_dedup.py` and `test_pla0_second_opinion_fixes.py` are
    what guard it.

    SCOPE OF THE CONSERVATION CLAIM, narrowed after external review found
    "structural conservation" overstated. What `digest()`'s per-model claim
    guarantees is: a record carrying a USABLE (non-empty string) `message.id`
    is counted at most once per model, whatever role bucket its copies land
    in. It says nothing about records this function declines to key. Two
    consequences, both pre-existing, both identical on the two paths, and both
    now pinned by `test_the_claim_and_the_oracle_agree_on_the_subtle_shapes`:

      * a fork echoing an ID-LESS record double-counts it (no key, so nothing
        to claim) — and did so before the role split too, inside the single
        per-model list, so it is not a regression;
      * a first record with an EMPTY `usage` dict claims its id and suppresses
        a later sibling of the same id that carries real numbers.

    Neither is fixable here without changing what this function means for the
    bash hook as well. Stated rather than smoothed over: the fix removed
    conservation's dependence on READING A METADATA FILE, which was the live
    defect; it did not make conservation unconditional.

    The rule is deliberately narrow, and the narrowness is load-bearing:

      - a NON-EMPTY STRING `message.id` is the only usable key. A missing,
        empty, or NON-STRING id (list/dict/int/bool) yields None -> the record
        is never deduped and always counts, because such an id is untrustworthy
        as a key: list/dict are unhashable (a set lookup RAISES), and 1 == True
        collide across genuinely distinct values, which would suppress a real
        usage row;
      - a record with no usage block (or a drifted non-dict one) yields None
        too, so it never CLAIMS an id. An id-only record must not shadow a
        later usage-bearing sibling carrying the same id.

    Never raises: any non-dict input yields None.
    """
    if not isinstance(rec, dict):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    if not isinstance(msg.get("usage"), dict):
        return None
    mid = msg.get("id")
    return mid if isinstance(mid, str) and mid else None


def deduped_usage_totals(records):
    """Sum token usage across transcript records, deduped by message.id.

    `records` is an iterable of parsed transcript record dicts, each shaped like
    a real Claude Code transcript line: usage at ``record["message"]["usage"]``,
    the message id at ``record["message"]["id"]``.

    Rules:
      - records that share a NON-EMPTY STRING message.id contribute their usage
        ONCE — the first usage-bearing occurrence wins; the streamed-block repeats
        are dropped;
      - records with no id, an empty id, or a NON-STRING id (list/dict/int/bool)
        EACH contribute (never deduped): a non-string id is untrustworthy as a
        dedup key — it can be unhashable (list/dict raise on the set lookup) or
        collide across distinct values (1 == True), which would suppress a real
        usage row — so such records are treated id-less;
      - records without a usage block are skipped, and an id-only record does
        NOT mark that id as seen — a later usage-bearing sibling of the same id
        still counts once;
      - a missing/None token field counts as 0.

    Returns a dict of the four TOKEN_FIELDS summed. Empty input -> all zero.
    """
    totals = {f: 0 for f in TOKEN_FIELDS}
    seen = set()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        # Dedup ONLY on a non-empty STRING id, and only for a usage-bearing
        # record — see `dedup_key`, which is that rule's single definition and
        # is shared with `ambient_digest.digest`'s cross-bucket guard. None
        # here means "never dedup this one" (count it, never suppress it).
        mid = dedup_key(rec)
        if mid is not None:
            if mid in seen:
                continue
            seen.add(mid)
        for f in TOKEN_FIELDS:
            v = usage.get(f, 0) or 0
            try:
                totals[f] += int(v)
            except (TypeError, ValueError):
                pass
    return totals


if __name__ == "__main__":
    # Tiny CLI: read a transcript (JSONL) from argv[1] or stdin, print deduped
    # totals as JSON. Handy for manual inspection; the hook imports the function.
    import json
    import sys

    src = open(sys.argv[1], encoding="utf-8") if len(sys.argv) > 1 else sys.stdin
    recs = []
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    print(json.dumps(deduped_usage_totals(recs)))
