# ADR-001 — LeanBench is independent from LeanVFS

**Status:** Accepted (2026-09-02)

## Context

LeanBench and LeanVFS are developed together, in one repository, by one author, in a
deliberately symbiotic loop: the benchmark measures the indexer, and the indexer's results shape
what the benchmark measures next. That loop is the fastest way to make both good. It is also the
fastest way to make the benchmark worthless, because a benchmark co-designed with one candidate
will encode that candidate's assumptions and quietly award points for them.

## Decision

LeanBench treats LeanVFS as **just another candidate**, indistinguishable from a third-party
system.

Enforced concretely:

1. **No import may cross the boundary.** The LeanBench kernel must not import LeanVFS, Pi, Git,
   Docker, or any candidate-specific module. A test greps the source tree and fails on violation.
2. **The only channel is `PROTOCOL.md`.** LeanVFS is launched as a subprocess and speaks the
   same JSONL protocol as every baseline. There is no in-process fast path, no shared object
   model, no privileged access.
3. **The baselines use the same path.** Raw, ripgrep, CTags, and MinimalAST are also standalone
   candidate processes. If the benchmark had a special internal route for its own baselines, the
   baselines would not be measuring what candidates experience.
4. **No task may be authored against LeanVFS's behaviour.** Tasks are authored by reading the
   target repository, never by inspecting what the candidate happens to return well.

## Consequences

- LeanVFS pays real subprocess and serialization overhead, exactly as a third party would. This
  is a feature: it is the cost an actual agent integration would pay.
- Some genuinely useful LeanVFS capability may be unexpressible through the protocol. The
  correct response is to extend the protocol *for all candidates*, with a version bump, not to
  add a side channel.
- If LeanVFS loses to CTags, the benchmark must report that. Per the LeanVFS build spec's own
  final gate: that is the finding, and it gets reported honestly rather than tuned away.
