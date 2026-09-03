"""Shared vocabularies. Every literal here is normative (PROTOCOL.md / TASKS.md)."""

from __future__ import annotations

from typing import Literal

# --- PROTOCOL.md §4 operations -------------------------------------------------
CandidateOp = Literal[
    "prepare_repository",
    "search",
    "get_symbol",
    "get_context",
    "get_dependencies",
    "get_references",
    "get_tests",
    "get_docs",
    "update_repository",
    "get_stats",
    "shutdown",
]

# --- PROTOCOL.md §5 capability keys -------------------------------------------
Capability = Literal[
    "search",
    "symbols",
    "context",
    "dependencies",
    "references",
    "tests",
    "docs",
    "incremental",
]

#: Capability key -> the op it gates (PROTOCOL.md §5 "Capability keys map to ops").
OP_FOR_CAPABILITY: dict[str, str] = {
    "search": "search",
    "symbols": "get_symbol",
    "context": "get_context",
    "dependencies": "get_dependencies",
    "references": "get_references",
    "tests": "get_tests",
    "docs": "get_docs",
    "incremental": "update_repository",
}

#: Inverse of OP_FOR_CAPABILITY; ops absent here need no declared capability.
CAPABILITY_FOR_OP: dict[str, str] = {op: cap for cap, op in OP_FOR_CAPABILITY.items()}

#: Ops every candidate must implement regardless of declared capabilities.
UNGATED_OPS: frozenset[str] = frozenset({"prepare_repository", "get_stats", "shutdown"})

# --- PROTOCOL.md §3 error codes -----------------------------------------------
ErrorCode = Literal[
    "unsupported_op",
    "invalid_args",
    "not_found",
    "not_prepared",
    "index_error",
    "timeout",
    "internal",
]

IndexState = Literal["ok", "stale", "partial", "failed"]

# --- PROTOCOL.md §7 failure classification ------------------------------------
Classification = Literal[
    "candidate_crash",
    "candidate_timeout",
    "protocol_error",
    "invalid_response",
    "candidate_protocol_error",
    "unsupported_capability",
    "benchmark_infrastructure_error",
]

CLASSIFICATIONS: tuple[str, ...] = (
    "candidate_crash",
    "candidate_timeout",
    "protocol_error",
    "invalid_response",
    "candidate_protocol_error",
    "unsupported_capability",
    "benchmark_infrastructure_error",
)

#: Classifications that are LeanBench's fault and are never scored against a candidate.
INFRASTRUCTURE_CLASSIFICATIONS: frozenset[str] = frozenset({"benchmark_infrastructure_error"})

# --- TASKS.md §2 / §3 ----------------------------------------------------------
CATEGORIES: tuple[str, ...] = (
    "symbol_location",
    "api_contract",
    "architecture",
    "behavior",
    "tests",
    "documentation",
    "comments",
    "configuration",
    "change_impact",
    "incremental",
)

DIFFICULTIES: tuple[str, ...] = ("L1", "L2", "L3", "L4", "L5")

PARAPHRASE_IDS: tuple[str, ...] = ("literal", "intent", "symptom")

Track = Literal["retrieval", "agent", "both"]

#: TASKS.md §5 triage flags.
TRIAGE_FLAGS: tuple[str, ...] = ("ceiling", "floor", "unstable", "stale_gold")

#: build spec §8.1 gateway tool names.
GATEWAY_TOOLS: tuple[str, ...] = (
    "candidate.search",
    "candidate.symbol",
    "candidate.context",
    "candidate.dependencies",
    "candidate.references",
    "candidate.tests",
    "candidate.docs",
    "repo.list",
    "repo.search",
    "repo.read",
    "repo.read_range",
    "repo.stat",
    "repo.patch",
)
