# ADR-010 — LLM replay cache as the basis for post-hoc reproducibility

**Status:** Accepted (2026-09-02)

## Context

Two problems share one solution.

**Cost.** 50 tasks × 5 candidates × 3 repetitions is 750 agent runs of real API spend, per
experiment. Iterating on a grader or a scoring formula under that cost model means not
iterating.

**Reproducibility.** The provider is not deterministic. An agent-track run cannot be reproduced
by re-running it, so a published agent-track number is, by default, unauditable.

## Decision

Cache every model call inside the run directory (`llm-cache/`).

- **Key:** `sha256(model || model_settings || full serialized message list || tool schema)`.
  Keying on the full message list means the cache is only hit when the agent is genuinely in the
  same state, so a replay that diverges misses the cache and is visibly not a replay.
- **Value:** the full response.

This yields:

```bash
leanbench rescore RUN_ID --scoring scoring-v2.toml   # new formula, old run, no model calls
leanbench replay  RUN_ID --graders graders-v2        # re-run agent track from cache, free
```

## Consequences

- Grader changes, scoring changes, and newly invented metrics become testable against historical
  runs at **zero cost**. This is what makes the optimization loop affordable enough to actually
  run.
- Agent runs become reproducible **after the fact** even though the provider is not
  deterministic — the strongest available answer to the auditability problem short of a
  deterministic model.
- Build it **early**, not as a later optimization: every scoring iteration performed before it
  exists costs a full re-run, and those costs are unrecoverable.
- The cache is bound to a specific model version string. Per `PROTOCOL.md` and the run manifest,
  a run whose model version differs from another's is **not comparable**, and `compare` marks
  such a pairing `CROSS-MODEL — INTERPRET WITH CAUTION`.
- Cache hits are recorded in the cost ledger (`cache_hit`), so a report can always distinguish
  spend from replay.
