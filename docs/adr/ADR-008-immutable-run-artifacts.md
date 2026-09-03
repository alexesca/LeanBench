# ADR-008 — Immutable run artifacts

**Status:** Accepted (2026-09-02)

## Context

A benchmark's artifacts are evidence. Evidence that can be edited after the fact is not
evidence. The failure is rarely malicious — it is a re-run that overwrites half a directory, or
a `rescore` that updates `summary.json` in place, leaving a run whose recorded config no longer
produced its recorded numbers.

## Decision

A completed run is **never modified**. Any change to config, candidate, suite, or code produces
a **new run** with a new ID (`lb_<8 uppercase base32 chars>`).

Derived commands write elsewhere: `leanbench rescore RUN_ID` reads a run and emits a *new*
scoring artifact rather than mutating the original; `compare` writes nothing back.

Each run records enough to reconstruct its own provenance: LeanBench version, suite version,
candidate/config/manifest digests, repository commit, task-set version, harness plugin+version,
provider and exact model version, model settings, tokenizer identity, plugin versions, OS,
CPU/RAM, start/end time, **all seeds**, and the noise-profile key.

## Consequences

- Disk grows with every run. Cheap, and the alternative — losing the ability to answer "what
  actually produced this number" — is not.
- Comparisons across runs are always well-defined, because each side's inputs are fixed and
  recorded. Digest mismatches are detectable, and `compare` must state which side differed.
- Combined with ADR-010's replay cache, an old run remains not merely readable but
  **re-executable** under new graders and new scoring.
- A test asserts that no command mutates an existing run directory.
