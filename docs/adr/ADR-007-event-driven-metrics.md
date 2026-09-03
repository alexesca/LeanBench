# ADR-007 — Event-driven metrics; raw events retained for re-analysis

**Status:** Accepted (2026-09-02)

## Context

The tempting design is to compute metrics inline in the evaluator: as the run proceeds,
increment counters, and write a summary at the end. It is simpler and it is a trap. Every new
metric then requires re-running the benchmark — which for the agent track means real money and,
because the provider is nondeterministic, means the old runs can never be re-analyzed at all.

## Decision

Metrics are **event subscribers**. Collection is never hard-coded into evaluator flow.

Every interaction emits a structured event to an event bus. Metric implementations subscribe,
`observe(event)` as events arrive, and `finalize(ctx)` at the end. Raw events are persisted to
`events.jsonl` **in addition to** the computed summaries.

The rule this exists to serve: **if a new metric is invented later, old runs must remain
analyzable.** A test asserts total tokens can be reconstructed from `events.jsonl` alone and
matches `token-usage.json` exactly — which simultaneously proves the events are complete and
that the summary is derived rather than independently computed.

Repository access records carry enough detail (`path`, `byte_range`, `line_range`,
`bytes_returned`, `tokens_returned`, `task_id`, `tool`, `timestamp`) to answer questions we have
not thought of yet — "how many irrelevant files did the agent open, and where did context
actually go" — without re-running anything.

## Consequences

- Every published number is traceable to raw artifacts by a documented formula, satisfying the
  project's second ground rule. If it cannot be recomputed from `events.jsonl` + `scoring.toml`,
  it does not ship.
- Event files grow. The storage decision (ADR-011) keeps them as JSONL until measured need
  justifies otherwise.
- Metrics must not import harness implementations — the dependency direction is enforced by
  test. A metric that reached into the harness would couple re-analysis to the harness version
  that produced the run, defeating the purpose.
