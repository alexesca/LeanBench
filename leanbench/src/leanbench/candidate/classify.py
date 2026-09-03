"""PROTOCOL.md §7 failure classification, as a table. Pure lookup — the runner detects
situations, this module names them. Keeping the mapping here means the normative matrix
is one grep away from the spec.
"""

from __future__ import annotations

from leanbench.kernel.errors import ERROR_FOR_CLASSIFICATION, LeanBenchError

#: Situation (left column of PROTOCOL.md §7) -> classification (right column).
CLASSIFICATION_MATRIX: dict[str, str] = {
    # Process exits non-zero, or dies, before `shutdown`
    "process_died_before_shutdown": "candidate_crash",
    # No terminal response within the op/startup timeout
    "no_terminal_response": "candidate_timeout",
    # Non-JSON line on stdout, or unknown protocol_version
    "non_json_stdout": "protocol_error",
    "unknown_protocol_version": "protocol_error",
    # JSON that fails the response schema, or unknown `status`
    "response_schema_violation": "invalid_response",
    "unknown_status": "invalid_response",
    # `unsupported_op` for a declared capability
    "unsupported_op_for_declared_capability": "candidate_protocol_error",
    # Op used that the candidate never declared
    "op_not_declared": "unsupported_capability",
    # LeanBench's own fault
    "benchmark_fault": "benchmark_infrastructure_error",
}

#: PROTOCOL.md §3: an error response is a well-behaved outcome. `not_found` on a
#: genuinely absent symbol is a normal empty answer and is NOT a failure at all.
WELL_BEHAVED_ERROR_CODES: frozenset[str] = frozenset(
    {"not_found", "invalid_args", "not_prepared", "index_error", "timeout", "internal"}
)


def classify(situation: str) -> str:
    if situation not in CLASSIFICATION_MATRIX:
        return "benchmark_infrastructure_error"
    return CLASSIFICATION_MATRIX[situation]


def error_for(situation: str, message: str, **kwargs: object) -> LeanBenchError:
    classification = classify(situation)
    exc_type = ERROR_FOR_CLASSIFICATION[classification]
    return exc_type(message, **kwargs)  # type: ignore[arg-type]


def classify_error_response(code: str, *, capability_declared: bool) -> str | None:
    """Classification for a well-formed `status: "error"` response, or None when the
    response is simply a negative answer rather than a failure."""
    if code == "unsupported_op":
        return "candidate_protocol_error" if capability_declared else "unsupported_capability"
    if code in WELL_BEHAVED_ERROR_CODES:
        return None
    return "invalid_response"
