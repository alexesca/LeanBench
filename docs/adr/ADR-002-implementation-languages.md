# ADR-002 — Implementation languages: Python for both, Rust deferred

**Status:** Accepted (2026-09-02)
**Supersedes:** the language split implied by LeanBench spec v2 §3.1 and LeanVFS spec v2 §3.

## Context

LeanBench spec v2 §17 already cuts the Rust CLI, with the re-entry criterion "a non-Python
user needs distribution *and* Python packaging has been tried and failed." That decision
stands unchanged.

LeanVFS spec v2 §0 argues the opposite for the indexer: "Rust is kept... this is a
long-running indexer where parse throughput, memory, and cold-start matter." That argument
is sound *in principle*. Two facts about the actual build environment override it in practice:

1. **There is no Rust toolchain on the build machine** and no `~/.cargo`. Installing one is
   possible but adds a multi-minute, ~1 GB dependency and a second CI surface before a single
   measurement has been taken.
2. **Nothing about token efficiency is language-dependent.** The product thesis — "maximize
   software-engineering usefulness per repository token consumed" — is decided entirely by
   the canonical model, the extraction quality, the resolution accuracy, the priority ladder,
   the budget admission algorithm, and the renderer format. All of that is language-neutral.
   Rust buys latency and memory, which LeanVFS spec §16 correctly calls correctness
   properties — but they are *secondary* correctness properties that only matter once the
   primary ones are demonstrated.

Building the Rust version first means paying the toolchain cost, the borrow-checker cost, and
the second-CI cost *before* learning whether the design produces any token savings at all.
That inverts the ground rule both specs share: measurement before breadth.

## Decision

**Build LeanVFS in Python**, in this repository, as a package that is a peer of `leanbench`
and shares nothing with it but the wire protocol in `PROTOCOL.md`.

Design the implementation so a Rust port is a *substitution at the protocol boundary*, not a
rewrite of the concept:

- The canonical model, the fact-kind registry, the priority ladder, and the budget admission
  algorithm are specified as data and pure functions, not as Python idioms.
- The storage layer is SQLite with FTS5 — identical schema, portable to `rusqlite` verbatim.
- Parsing goes through `tree-sitter`, which has first-class bindings in both languages, with
  the same grammar and the same node-type names.
- The candidate protocol server is a thin shell over the query engine, so a Rust
  reimplementation swaps one manifest line and passes the same conformance suite.

## Rust re-entry criterion

Port to Rust when **either** holds:

- LeanVFS passes the LeanBench Phase 9 discrimination gate on HTTPX *and* fails the §16
  latency budgets (`search` p95 25 ms, `get_context` p95 40 ms, cold-index ≥ 400 KB/s/core)
  by a margin that profiling shows is interpreter-bound rather than algorithmic; **or**
- a target repository is large enough that Python cold-index time exceeds the harness startup
  timeout even with the incremental path warm.

Until one of those is *measured*, the port is speculative optimization. The whole point of
building LeanBench first is that we will be able to tell.

## Consequences

- One toolchain, one test runner, one CI pipeline, one packaging story.
- The symbiotic loop (LeanBench measures LeanVFS; LeanVFS's results shape LeanBench) closes
  in hours instead of days.
- We accept that the first published latency numbers will be Python numbers, and the report
  must label them as such rather than presenting them as the design's floor.
- Risk accepted: if the design turns out to be latency-bound in a way Python cannot fix, the
  port is a real cost paid later. Mitigated by keeping the hot path (SQLite/FTS5) out of
  Python loops and by holding the canonical model to a portable shape.
