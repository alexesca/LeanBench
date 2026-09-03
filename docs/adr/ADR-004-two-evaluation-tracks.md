# ADR-004 — Two evaluation tracks: deterministic retrieval, stochastic agent

**Status:** Accepted (2026-09-02)

## Context

The obvious design is one evaluation loop: put an LLM agent in front of the candidate, give it
a task, see if it succeeds and how many tokens it burned. That single loop is both the
expensive one and the noisy one, which makes it a terrible inner development loop — and using
it as the only loop is why benchmarks in this space tend to produce mush.

## Decision

Two tracks, reported separately, plus their correlation.

**Retrieval Track** — no LLM. Each task's fixed probe set is issued directly to the candidate
through the protocol; results are graded against structured gold. Deterministic, milliseconds
per task, zero API cost, identical probes for every candidate, runnable in CI on every commit.
This is the **primary optimization loop**: a candidate developer runs it and gets a full answer
in under 10 seconds, free.

**Agent Track** — full LLM agent loop through the tool gateway. Measures whether retrieval
quality actually converts into an agent reaching a correct answer with less context. Run on
demand and nightly, never on every commit.

## Why both, and why not merged

They answer different questions. The retrieval track answers *"is the index good?"*. The agent
track answers *"does a good index help?"*. Conflating them makes the fast loop slow and the
trustworthy loop untrustworthy.

**Their correlation is itself a reported metric.** If retrieval-track gains stop predicting
agent-track gains, we are overfitting to retrieval metrics, and the report must warn about it
rather than quietly continuing to optimize a proxy. The Phase 9 gate requires Spearman ≥ 0.6
between the tracks across the baselines; if the tracks disagree about which baseline is better,
one of them is measuring the wrong thing and the build stops until we know which.

## Consequences

- Retrieval-track noise must be **exactly zero**. Not "low" — zero. Any variance is
  nondeterminism in our own code, and an assertion test hunts it.
- The retrieval track's known weakness is overfitting to probe phrasing, mitigated by ≥3
  paraphrases per task with mean *and worst-case* reporting (see `docs/limitations.md` §2).
- Two graders, two report sections, and a correlation to maintain. Accepted: the alternative is
  a single loop that is too slow to iterate on and too noisy to trust.
