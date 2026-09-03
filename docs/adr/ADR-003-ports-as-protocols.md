# ADR-003 — Ports as protocols; plugin machinery deferred

**Status:** Accepted (2026-09-02)

## Context

The original design specified nine plugin categories, API versioning, capability negotiation,
a contract-test framework, and WASM plugins — while also stating "do not build a large framework
unnecessarily." Those two things cannot both be honoured.

Plugin architecture designed before a second implementation exists is speculative generality.
The cost is not just the code: it is the versioning ceremony, the negotiation protocol, the
contract-test harness, and the API-stability obligation, all maintained against exactly zero
external implementers.

## Decision

Keep the **seams**; drop the **machinery**.

- Ports are plain `typing.Protocol` classes: `RepositoryPort`, `CandidatePort`, `HarnessPort`,
  `GraderPort`, `MetricPort`, `ReporterPort`, `MutationPort`. No ABCs, no inheritance
  hierarchies, no base classes to subclass.
- Registration is a dict populated at import time: `REGISTRY: dict[str, dict[str, type]]` with a
  `register(kind, name, impl)` function.
- Capability checking is a set-subset assertion at suite start, not a negotiation protocol:
  `missing = suite.required_capabilities - candidate.capabilities` → raise `IncompatibleCandidate`.

Deferred with explicit re-entry criteria (build spec §17): plugin API versioning, capability
negotiation, contract-test framework, and `leanbench plugin test` re-enter when **a third party
outside the repo has written a plugin**. WASM/external-executable plugins re-enter when **two
external plugin authors exist**.

## Consequences

- Adding an implementation is writing a class that structurally matches a Protocol and calling
  `register`. No inheritance, no registration ceremony, no version declaration.
- Structural typing means violations are caught by the type checker rather than at runtime, and
  no runtime `isinstance` machinery is needed.
- If a real third-party plugin ecosystem appears, we will have to add versioning under
  compatibility pressure, which is harder than adding it now. Accepted deliberately: the odds of
  guessing the right API shape before any external implementer exists are poor, and a wrong
  guess frozen by a version promise is worse than a late-but-informed design.

Note that the **candidate** boundary is a genuine exception and *is* versioned — see
`PROTOCOL.md`. That boundary already has multiple implementations (four baselines plus LeanVFS)
and crosses a process boundary, which is precisely the condition that justifies the ceremony.
