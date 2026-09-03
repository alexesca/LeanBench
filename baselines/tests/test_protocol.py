"""PROTOCOL.md conformance for all four baselines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from client import BASELINES, CandidateProcess
from conftest import spawn

CAPABILITY_PROBE: dict[str, tuple[str, dict[str, object]]] = {
    "search": ("search", {"query": "retries engine", "limit": 5}),
    "symbols": ("get_symbol", {"name": "Engine.send"}),
    "context": ("get_context", {"symbol": "Engine.send"}),
    "dependencies": ("get_dependencies", {"path": "pkg/core.py"}),
    "references": ("get_references", {"symbol": "Engine", "limit": 10}),
    "tests": ("get_tests", {"symbol": "Engine"}),
    "docs": ("get_docs", {"query": "retries", "limit": 3}),
    "incremental": ("update_repository", {"changed": ["pkg/core.py"]}),
}

META_REQUIRED = ("tokens_approx", "truncated", "index_state")


def _assert_ok_envelope(response: dict, req_id: str) -> None:
    assert response["id"] == req_id
    assert response["status"] == "ok"
    assert isinstance(response["result"], dict)
    meta = response["meta"]
    for key in META_REQUIRED:
        assert key in meta, f"meta.{key} missing"
    assert isinstance(meta["tokens_approx"], int)
    assert isinstance(meta["truncated"], bool)
    assert meta["index_state"] in ("ok", "stale", "partial", "failed")


def test_prepare_is_idempotent_and_reports_counts(candidate: CandidateProcess, mini_repo: Path):
    first = candidate.request("prepare_repository", {"path": str(mini_repo)}, fmt="json")
    second = candidate.request("prepare_repository", {"path": str(mini_repo)}, fmt="json")
    _assert_ok_envelope(first, first["id"])
    assert first["result"]["files"] == second["result"]["files"]
    assert first["result"]["files"] > 0


def test_declared_capabilities_never_return_unsupported_op(candidate: CandidateProcess):
    for capability in sorted(candidate.capabilities):
        op, args = CAPABILITY_PROBE[capability]
        response = candidate.request(op, args)
        if response["status"] == "error":
            assert response["code"] != "unsupported_op", (
                f"{candidate.name} declared '{capability}' but returned unsupported_op"
            )
            assert response["code"] == "not_found"
        else:
            _assert_ok_envelope(response, response["id"])


def test_undeclared_capabilities_return_unsupported_op(candidate: CandidateProcess):
    for capability, (op, args) in CAPABILITY_PROBE.items():
        if capability in candidate.capabilities:
            continue
        response = candidate.request(op, args)
        assert response["status"] == "error"
        assert response["code"] == "unsupported_op"


def test_unknown_op_is_unsupported(candidate: CandidateProcess):
    response = candidate.request("teleport", {})
    assert response["status"] == "error"
    assert response["code"] == "unsupported_op"


def test_invalid_args_are_classified(candidate: CandidateProcess):
    response = candidate.request("search", {"limit": 5})
    assert response["status"] == "error"
    assert response["code"] == "invalid_args"


def test_malformed_json_line_gets_an_error_response(candidate: CandidateProcess):
    assert candidate.process.stdin is not None
    candidate.process.stdin.write("{not json}\n")
    candidate.process.stdin.flush()
    assert candidate.process.stdout is not None
    response = json.loads(candidate.process.stdout.readline())
    assert response["status"] == "error"
    assert response["code"] == "invalid_args"


def test_both_formats_are_supported(candidate: CandidateProcess):
    compact = candidate.request("search", {"query": "engine", "limit": 3}, fmt="compact")
    structured = candidate.request("search", {"query": "engine", "limit": 3}, fmt="json")
    assert set(compact["result"]) == {"text"}
    assert "hits" in structured["result"]
    assert isinstance(structured["result"]["hits"], list)


def test_paths_are_repository_relative_posix(candidate: CandidateProcess, mini_repo: Path):
    response = candidate.request("search", {"query": "engine retries", "limit": 5}, fmt="json")
    if response["status"] != "ok":
        pytest.skip("no hits for this baseline")
    for hit in response["result"]["hits"]:
        path = hit["path"]
        assert not path.startswith("/")
        assert "\\" not in path
        assert (mini_repo / path).is_file()


def test_ignored_files_are_invisible(candidate: CandidateProcess):
    response = candidate.request("search", {"query": "SHOULD_NOT_BE_INDEXED"}, fmt="json")
    if response["status"] == "error":
        assert response["code"] == "not_found"
        return
    paths = [hit["path"] for hit in response["result"].get("hits", [])]
    assert not any(path.startswith("build/") for path in paths)


@pytest.mark.parametrize("name", BASELINES)
def test_not_prepared_before_prepare(name: str):
    process = spawn(name)
    try:
        response = process.request("search", {"query": "engine"})
        assert response["status"] == "error"
        assert response["code"] == "not_prepared"
    finally:
        process.close()


@pytest.mark.parametrize("name", BASELINES)
def test_shutdown_exits_zero_and_stdout_is_protocol_only(name: str, mini_repo: Path):
    process = spawn(name)
    process.prepare(mini_repo)
    lines: list[str] = []
    lines.append(process.request_raw({"id": "s1", "op": "get_stats", "args": {}}))
    lines.append(process.request_raw({"id": "s2", "op": "shutdown", "args": {}}))
    assert process.process.wait(timeout=5.0) == 0
    for line in lines:
        parsed = json.loads(line)
        assert parsed["status"] in ("ok", "error", "indexing")
    remainder = process.process.stdout.read() if process.process.stdout else ""
    assert remainder.strip() == ""
