"""Phase 3 gate: every fake-candidate mode yields the CORRECT DISTINCT classification.

The point is not "errors are handled". The point is that LeanBench can tell a crash from
a timeout from a protocol violation from a capability lie — because a benchmark that
collapses those into one bucket cannot report honestly about why a candidate lost.
"""

from __future__ import annotations

import contextlib

import pytest
from lb_paths import FAKE_MANIFESTS, MINI_REPO
from leanbench.candidate.manifest import load_manifest
from leanbench.candidate.runner import SubprocessCandidate
from leanbench.kernel.errors import LeanBenchError

# PROTOCOL.md §7, as an explicit matrix. Each row is a distinct classification.
MATRIX = [
    ("crash", "candidate_crash"),
    ("slow", "candidate_timeout"),
    ("invalid-json", "protocol_error"),
    ("wrong-protocol", "invalid_response"),
    ("false-capability", "candidate_protocol_error"),
]


@pytest.mark.parametrize(("mode", "expected"), MATRIX, ids=[m for m, _ in MATRIX])
def test_failure_mode_is_classified_distinctly(mode: str, expected: str, config) -> None:
    manifest = load_manifest(FAKE_MANIFESTS / f"{mode}.toml")
    candidate = SubprocessCandidate(manifest, config)
    classification = None
    try:
        candidate.start()
        candidate.prepare(str(MINI_REPO), "fixture")
        for _ in range(6):
            # `false-capability` refuses only the op it falsely declared, so the loop
            # exercises both a normal op and the declared-but-unsupported one.
            candidate.call("search", {"query": "money", "limit": 5}, task_id="t")
            candidate.call("get_docs", {"query": "money", "limit": 5}, task_id="t")
    except LeanBenchError as exc:
        classification = exc.classification
    finally:
        with contextlib.suppress(LeanBenchError):
            candidate.shutdown()
    assert classification == expected, f"mode {mode!r} classified as {classification!r}"


def test_every_matrix_row_is_a_distinct_classification() -> None:
    classifications = [c for _, c in MATRIX]
    assert len(set(classifications)) == len(classifications)


def test_normal_mode_produces_no_failure(config) -> None:
    manifest = load_manifest(FAKE_MANIFESTS / "normal.toml")
    candidate = SubprocessCandidate(manifest, config)
    candidate.start()
    try:
        candidate.prepare(str(MINI_REPO), "fixture")
        response = candidate.call("search", {"query": "money", "limit": 5}, task_id="t")
        assert response.status == "ok"
        assert response.result
    finally:
        candidate.shutdown()


def test_undeclared_capability_is_not_a_crash(config) -> None:
    """A capability the candidate never claimed is `unsupported_capability` — a
    statement about fit, not about the candidate being broken."""
    from leanbench.kernel.capabilities import missing_capabilities

    manifest = load_manifest(FAKE_MANIFESTS / "missing-capability.toml")
    # The manifest honestly declares `docs = false`; requiring docs must therefore
    # report a missing capability rather than letting the run start and fail later.
    missing = missing_capabilities({"search", "docs"}, manifest.declared_capabilities)
    assert missing == {"docs"}


def test_bad_protocol_version_manifest_is_rejected() -> None:
    from leanbench.kernel.errors import LeanBenchError

    with pytest.raises(LeanBenchError):
        load_manifest(FAKE_MANIFESTS / "bad-protocol-version.toml")
