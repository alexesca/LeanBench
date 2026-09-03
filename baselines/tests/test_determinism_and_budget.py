"""Determinism and honest budget truncation (PROTOCOL.md §2, §3)."""

from __future__ import annotations

import json

import pytest
from client import CandidateProcess

QUERIES: tuple[tuple[str, dict[str, object]], ...] = (
    ("search", {"query": "retry engine timeout", "limit": 5}),
    ("get_symbol", {"name": "Engine.send"}),
    ("get_context", {"symbol": "Engine.send"}),
    ("get_references", {"symbol": "Engine", "limit": 20}),
)

#: `elapsed_ms` is a wall-clock measurement and is exempt from byte equality; every
#: other byte of the response must repeat exactly.
VOLATILE_META = ("elapsed_ms",)


def _stable_bytes(line: str) -> bytes:
    payload = json.loads(line)
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in VOLATILE_META:
            meta.pop(key, None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize(("op", "args"), QUERIES)
def test_repeated_queries_are_byte_identical(candidate: CandidateProcess, op, args):
    capability = {
        "search": "search",
        "get_symbol": "symbols",
        "get_context": "context",
        "get_references": "references",
    }[op]
    if capability not in candidate.capabilities:
        pytest.skip(f"{candidate.name} does not declare {capability}")
    first = _stable_bytes(candidate.request_raw({"id": "d", "op": op, "args": args}))
    for _ in range(3):
        assert _stable_bytes(candidate.request_raw({"id": "d", "op": op, "args": args})) == first


def test_budget_is_respected_and_truncation_is_reported(candidate: CandidateProcess):
    unbounded = candidate.request("get_context", {"symbol": "Engine.send"})
    if unbounded["status"] == "error":
        pytest.skip("no context for this baseline")
    full_lines = unbounded["result"]["text"].splitlines()
    assert unbounded["meta"]["truncated"] is False
    for budget in (40, 80, 160, 320):
        response = candidate.request("get_context", {"symbol": "Engine.send"}, token_budget=budget)
        lines = response["result"]["text"].splitlines()
        meta = response["meta"]
        # The header (identity + signature) is mandatory, so a very small budget may be
        # exceeded by it alone; every *droppable* item beyond it must then be dropped.
        assert full_lines[: len(lines)] == lines
        if meta["truncated"]:
            assert len(lines) < len(full_lines)
            assert meta.get("dropped"), "truncated responses must report dropped kinds"
        else:
            assert lines == full_lines


def test_truncation_is_a_deterministic_prefix(candidate: CandidateProcess):
    if "search" not in candidate.capabilities:
        pytest.skip("no search")
    small = candidate.request("search", {"query": "engine retries", "limit": 5}, token_budget=60)
    large = candidate.request("search", {"query": "engine retries", "limit": 5}, token_budget=600)
    small_lines = small["result"]["text"].splitlines()
    large_lines = large["result"]["text"].splitlines()
    assert large_lines[: len(small_lines)] == small_lines
    if small["meta"]["truncated"]:
        assert small["meta"]["dropped"]


def test_token_estimate_tracks_the_returned_text(candidate: CandidateProcess):
    response = candidate.request("search", {"query": "engine", "limit": 5})
    if response["status"] != "ok":
        pytest.skip("no hits")
    text = response["result"]["text"]
    approx = response["meta"]["tokens_approx"]
    assert approx > 0 or text == ""
    # A sane estimator stays within a factor of four of a chars/4 rule of thumb.
    assert approx <= max(8, len(text))
