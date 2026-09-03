"""Phase 2 gate: mock implementations of every port compose into a complete run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from leanbench.artifacts import ARTIFACT_NAMES, RunWriter
from leanbench.kernel.bus import EventBus
from leanbench.kernel.capabilities import assert_capabilities
from leanbench.kernel.context import RunContext
from leanbench.kernel.errors import IncompatibleCandidate
from leanbench.kernel.ids import is_run_id, new_run_id, run_id_from_seed
from leanbench.kernel.registry import REGISTRY, register
from leanbench.ports import (
    CandidatePort,
    GraderPort,
    HarnessPort,
    MetricPort,
    MutationPort,
    ReporterPort,
    RepositoryPort,
)


class MockRepository:
    root = Path()

    def list_files(self, subpath: str | None = None) -> list[str]:
        return ["a.py"]

    def read(self, path: str) -> str:
        return "x = 1\n"

    def read_range(self, path: str, start: int, end: int) -> str:
        return "x = 1"

    def stat(self, path: str) -> dict:
        return {"path": path, "bytes": 6, "lines": 1, "is_dir": False, "exists": True}

    def search(self, pattern: str, limit: int) -> list[dict]:
        return []

    def exists(self, path: str) -> bool:
        return True


class MockCandidate:
    name = "mock"

    def start(self) -> None:
        return None

    def prepare(self, path: str, commit: str) -> dict:
        return {"indexed": True}

    def call(self, op: str, args: dict, *, task_id: str | None = None) -> dict:
        return {"hits": []}

    def declared_capabilities(self) -> frozenset:
        return frozenset({"search"})

    def digests(self):
        return None

    def resources(self):
        return None

    def shutdown(self) -> None:
        return None


class MockHarness:
    track = "mock"

    def run_task(self, task: Any) -> dict:
        return {"task_id": "t1", "probes": []}


class MockGrader:
    def grade(self, task: Any, observations: dict) -> dict:
        return {"correctness": 1.0}


class MockMetric:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    def observe(self, event: Any) -> None:
        self.seen.append(event)

    def aggregate(self, *args: Any, **kwargs: Any) -> dict:
        return {"observed": len(self.seen)}


class MockReporter:
    def render(self, result: Any) -> str:
        return "ok"


class MockMutation:
    def apply(self, repo: Any, seed: int) -> Any:
        return {"seed": seed}

    def revert(self, repo: Any) -> None:
        return None


PORTS = [
    (RepositoryPort, MockRepository),
    (CandidatePort, MockCandidate),
    (HarnessPort, MockHarness),
    (GraderPort, MockGrader),
    (MetricPort, MockMetric),
    (ReporterPort, MockReporter),
    (MutationPort, MockMutation),
]


@pytest.mark.parametrize(("port", "impl"), PORTS, ids=[p.__name__ for p, _ in PORTS])
def test_mock_structurally_satisfies_its_port(port: type, impl: type) -> None:
    """Protocols are structural (ADR-003): conformance needs no inheritance, and a
    missing method is a type error rather than a runtime surprise."""
    instance = impl()
    for name in dir(port):
        if name.startswith("_"):
            continue
        assert hasattr(instance, name), f"{impl.__name__} lacks {name}"


def test_registry_is_a_plain_dict_with_no_version_ceremony() -> None:
    register("harness", "mock", MockHarness)
    assert REGISTRY["harness"]["mock"] is MockHarness


def test_capability_assertion_is_set_subtraction() -> None:
    assert_capabilities({"search"}, {"search", "docs"}, candidate="mock")
    with pytest.raises(IncompatibleCandidate) as excinfo:
        assert_capabilities({"search", "incremental"}, {"search"}, candidate="mock")
    assert "incremental" in str(excinfo.value)


def test_run_ids_are_well_formed_and_seedable() -> None:
    assert is_run_id(new_run_id())
    assert run_id_from_seed("x") == run_id_from_seed("x")
    assert run_id_from_seed("x") != run_id_from_seed("y")


def test_ports_compose_into_a_run_producing_valid_artifacts(tmp_path: Path, config) -> None:
    run_id = run_id_from_seed("compose")
    bus = EventBus(run_id)
    metric = MockMetric()
    bus.subscribe("metric", metric.observe)
    context = RunContext(run_id=run_id, config=config, bus=bus, run_dir=tmp_path / run_id)

    bus.emit("tool_completed", "harness", task_id="t1", tokens=10)
    grade = MockGrader().grade(None, None)

    writer = RunWriter(tmp_path / run_id)
    for name in ARTIFACT_NAMES:
        if name.endswith(".jsonl"):
            writer.write_jsonl(name, [{"kind": "mock"}])
        else:
            writer.write_json(name, {"run_id": run_id, "grade": grade})
    assert writer.missing_artifacts() == []
    assert metric.seen, "the metric port never observed the emitted event"
    assert context.infrastructure_failures == 0
