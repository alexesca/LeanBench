# ADR-006 — Pi as default harness, never a kernel dependency

**Status:** Accepted (2026-09-02)

## Context

The agent track needs an agent harness. A specific harness will always be the practical default,
and the risk is that "the default" silently becomes "the assumption" — its message format, its
tool-call convention, and its quirks leaking into the kernel until the benchmark can only measure
agents that look like that one.

## Decision

Three harnesses, all behind `HarnessPort`:

- **`MockHarness`** — mandatory, no LLM, drives all CI. Deterministic tool-call sequences.
- **`PythonLoopHarness`** — minimal reference implementation, for debugging and for harness-to-harness comparison.
- **`PiHarness`** — default for real evaluation, version-pinned.

The kernel must not import Pi, or any harness implementation. A test enforces it.

The same task must run through all three, and a test asserts the tool-call sequence is identical
between `MockHarness` runs. Harness identity and version are recorded in the run manifest, and a
comparison across differing harnesses must say so.

## Consequences

- Harness-specific behaviour is measurable rather than invisible: if `PiHarness` and
  `PythonLoopHarness` disagree about a candidate's ranking, that is a finding about the harness,
  which is exactly the kind of confound a benchmark should be able to detect.
- CI never needs an API key or a network, because `MockHarness` drives the end-to-end path.
- Cost: three implementations to maintain against one port. Accepted — `MockHarness` is the CI
  backbone and `PythonLoopHarness` is small, so the real maintenance is one harness plus two
  cheap ones.
