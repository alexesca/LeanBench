"""Kernel: identity, config plumbing, registry, event bus, capability algebra.

Hard rule (tested in tests/test_architecture.py): the kernel imports no Git, no Docker,
and nothing named `leanvfs`. It depends only on `leanbench.schemas` and the stdlib.
"""

from leanbench.kernel.bus import EventBus
from leanbench.kernel.capabilities import assert_capabilities, missing_capabilities
from leanbench.kernel.context import RunContext
from leanbench.kernel.counters import Counters
from leanbench.kernel.errors import (
    BenchmarkInfrastructureError,
    CandidateCrash,
    CandidateProtocolError,
    CandidateTimeout,
    ConfigError,
    IncompatibleCandidate,
    InvalidResponse,
    LeanBenchError,
    ProtocolError,
    UnsupportedCapability,
)
from leanbench.kernel.ids import is_run_id, new_run_id, run_id_from_seed
from leanbench.kernel.logging import configure_logging, get_logger
from leanbench.kernel.registry import REGISTRY, lookup, names, register

__all__ = [
    "REGISTRY",
    "BenchmarkInfrastructureError",
    "CandidateCrash",
    "CandidateProtocolError",
    "CandidateTimeout",
    "ConfigError",
    "Counters",
    "EventBus",
    "IncompatibleCandidate",
    "InvalidResponse",
    "LeanBenchError",
    "ProtocolError",
    "RunContext",
    "UnsupportedCapability",
    "assert_capabilities",
    "configure_logging",
    "get_logger",
    "is_run_id",
    "lookup",
    "missing_capabilities",
    "names",
    "new_run_id",
    "register",
    "run_id_from_seed",
]
