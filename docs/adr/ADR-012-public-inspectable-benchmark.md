# ADR-012 — Public benchmark fully inspectable; contamination documented, not hidden

**Status:** Accepted (2026-09-02)

## Context

Publishing gold data has a real, permanent cost: it enters future model training corpora, after
which agent-track scores on those tasks may reflect memorization rather than candidate quality.
Withholding it has a different cost: an unauditable benchmark is an assertion, not a measurement,
and nobody can check whether its gold is correct or its scoring is fair.

## Decision

Publish everything — gold, probes, weights, normalization, task justifications, baselines, and
the raw run artifacts — and make contamination **detectable** rather than pretending it is
avoided.

- Every task records `authored_at`.
- A jump in agent-track performance on **old** tasks not reproduced on newly authored ones is
  reported as a **contamination signal, not a candidate win**.
- `docs/limitations.md` states this in the open, along with the benchmark's other weaknesses.
- The retrieval track is structurally more resistant: it measures what an index returns for a
  fixed probe, so a model having memorized the corpus does not help a candidate.

## Consequences

- The benchmark can be audited, reproduced, and disagreed with on specifics — the necessary
  condition for anyone to trust a number they did not produce themselves.
- Scores on the public suite will decay in meaningfulness over time. The mitigation is task
  provenance plus authoring fresh tasks, not secrecy.
- We accept that a sufficiently determined optimizer can overfit to published gold. The task
  quality gates, the paraphrase requirement, and the separation matrix make that overfitting
  visible rather than free.
