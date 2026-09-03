"""Capability assertion — set subtraction and nothing else (PROTOCOL.md §5)."""

from __future__ import annotations

from collections.abc import Iterable

from leanbench.kernel.errors import IncompatibleCandidate
from leanbench.schemas.common import CAPABILITY_FOR_OP, OP_FOR_CAPABILITY, UNGATED_OPS


def missing_capabilities(required: Iterable[str], declared: Iterable[str]) -> frozenset[str]:
    return frozenset(set(required) - set(declared))


def assert_capabilities(
    required: Iterable[str], declared: Iterable[str], *, candidate: str = ""
) -> None:
    missing = missing_capabilities(required, declared)
    if missing:
        raise IncompatibleCandidate(missing, candidate)


def required_for_probes(ops: Iterable[str]) -> frozenset[str]:
    """The capabilities a *retrieval* run will actually exercise.

    A suite's `required_capabilities` describes what solving its tasks needs in general
    (the agent track may call `get_tests`, `get_references`, ...). The retrieval track
    only ever issues the ops named by the probes, so gating a retrieval run on the wider
    set would exclude every deliberately capability-limited baseline — and the baseline
    ladder is the instrument the whole benchmark is calibrated against. Assert against
    what will actually be called.
    """
    capabilities = {CAPABILITY_FOR_OP[op] for op in ops if op in CAPABILITY_FOR_OP}
    return frozenset(capabilities)


def capability_for_op(op: str) -> str | None:
    """The capability key that gates `op`, or None if the op is always required."""
    if op in UNGATED_OPS:
        return None
    return CAPABILITY_FOR_OP.get(op)


def op_declared(op: str, declared: Iterable[str]) -> bool:
    capability = capability_for_op(op)
    return True if capability is None else capability in set(declared)


def ops_for_capabilities(capabilities: Iterable[str]) -> frozenset[str]:
    return (
        frozenset(OP_FOR_CAPABILITY[c] for c in capabilities if c in OP_FOR_CAPABILITY)
        | UNGATED_OPS
    )
