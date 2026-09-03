"""Exception hierarchy. Every raised error carries a PROTOCOL.md §7 classification."""

from __future__ import annotations


class LeanBenchError(Exception):
    """Base. `classification` is the PROTOCOL.md §7 bucket this failure reports as."""

    classification: str = "benchmark_infrastructure_error"

    def __init__(self, message: str, *, task_id: str | None = None, op: str | None = None):
        super().__init__(message)
        self.message = message
        self.task_id = task_id
        self.op = op


class BenchmarkInfrastructureError(LeanBenchError):
    classification = "benchmark_infrastructure_error"


class ConfigError(BenchmarkInfrastructureError):
    """A config key is missing, mistyped, or a layer file is unreadable."""


class TaskValidationError(BenchmarkInfrastructureError):
    """A task file does not satisfy TASKS.md §1."""


class CandidateCrash(LeanBenchError):
    classification = "candidate_crash"

    def __init__(self, message: str, *, exit_code: int | None = None, **kwargs: object):
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.exit_code = exit_code


class CandidateTimeout(LeanBenchError):
    classification = "candidate_timeout"


class ProtocolError(LeanBenchError):
    """Non-JSON on stdout, or an unknown protocol_version."""

    classification = "protocol_error"


class InvalidResponse(LeanBenchError):
    """Valid JSON that fails the response schema, or an unknown `status`."""

    classification = "invalid_response"


class CandidateProtocolError(LeanBenchError):
    """`unsupported_op` for a capability the candidate declared."""

    classification = "candidate_protocol_error"


class UnsupportedCapability(LeanBenchError):
    """An op was used that the candidate never declared."""

    classification = "unsupported_capability"


class IncompatibleCandidate(BenchmarkInfrastructureError):
    """Suite requires capabilities the candidate does not declare (PROTOCOL.md §5)."""

    def __init__(self, missing: frozenset[str] | set[str], candidate: str = ""):
        self.missing = frozenset(missing)
        listed = ", ".join(sorted(self.missing))
        super().__init__(f"candidate {candidate!r} is missing required capabilities: {listed}")


class GatewayError(BenchmarkInfrastructureError):
    """Tool gateway rejected a call (unknown tool, bad args, path escape, budget)."""


#: Map classification -> exception type, for constructing from a classification string.
ERROR_FOR_CLASSIFICATION: dict[str, type[LeanBenchError]] = {
    "candidate_crash": CandidateCrash,
    "candidate_timeout": CandidateTimeout,
    "protocol_error": ProtocolError,
    "invalid_response": InvalidResponse,
    "candidate_protocol_error": CandidateProtocolError,
    "unsupported_capability": UnsupportedCapability,
    "benchmark_infrastructure_error": BenchmarkInfrastructureError,
}
