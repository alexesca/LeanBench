"""Run artifact writer — the exact `runs/<run-id>/` layout of build spec §13.1.

    manifest.json  config.json  candidate.json  environment.json
    tasks.jsonl    events.jsonl metrics.json    token-usage.json
    cost-ledger.jsonl           failures.jsonl  summary.json

Immutability: a run directory is written once and then sealed. `RunWriter.seal()` marks
it complete, and every writer refuses to touch a sealed directory.
"""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path
from typing import Any

from leanbench.kernel.errors import BenchmarkInfrastructureError
from leanbench.schemas.run import EnvironmentArtifact

LEANBENCH_VERSION = "0.1.0"
SEAL_NAME = ".sealed"

ARTIFACT_NAMES: tuple[str, ...] = (
    "manifest.json",
    "config.json",
    "candidate.json",
    "environment.json",
    "tasks.jsonl",
    "events.jsonl",
    "metrics.json",
    "token-usage.json",
    "cost-ledger.jsonl",
    "failures.jsonl",
    "summary.json",
)

TRACKED_PACKAGES = ("pydantic", "typer", "psutil", "tiktoken")


def dumps(data: Any) -> str:
    """Canonical JSON: sorted keys, fixed separators, trailing newline. Two identical
    runs must produce byte-identical files, so serialization is never left to chance."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, default=str) + "\n"


def dumps_line(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class RunWriter:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.written: list[str] = []

    @property
    def sealed(self) -> bool:
        return (self.run_dir / SEAL_NAME).exists()

    def _guard(self, name: str) -> Path:
        if self.sealed:
            raise BenchmarkInfrastructureError(
                f"run {self.run_dir.name} is sealed; completed runs are never mutated"
            )
        if name not in ARTIFACT_NAMES:
            raise BenchmarkInfrastructureError(f"{name!r} is not a build-spec §13.1 artifact")
        return self.run_dir / name

    def write_json(self, name: str, data: Any) -> Path:
        path = self._guard(name)
        path.write_text(dumps(data), encoding="utf-8")
        self.written.append(name)
        return path

    def write_jsonl(self, name: str, rows: Iterable[Any]) -> Path:
        path = self._guard(name)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(dumps_line(row) + "\n")
        self.written.append(name)
        return path

    def seal(self) -> None:
        (self.run_dir / SEAL_NAME).write_text("", encoding="utf-8")

    def missing_artifacts(self) -> list[str]:
        return [name for name in ARTIFACT_NAMES if not (self.run_dir / name).exists()]


def read_json(run_dir: Path, name: str) -> Any:
    path = Path(run_dir) / name
    if not path.is_file():
        raise BenchmarkInfrastructureError(f"run artifact {name} missing from {run_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(run_dir: Path, name: str) -> list[Any]:
    path = Path(run_dir) / name
    if not path.is_file():
        raise BenchmarkInfrastructureError(f"run artifact {name} missing from {run_dir}")
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def environment_artifact(
    *, tokenizer: str, approximate: bool, reason: str | None = None
) -> EnvironmentArtifact:
    versions: dict[str, str] = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "absent"
    try:
        import psutil

        total_memory = psutil.virtual_memory().total
        cpu_count = psutil.cpu_count()
    except (ImportError, OSError):
        total_memory, cpu_count = None, None
    return EnvironmentArtifact(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        machine=platform.machine(),
        cpu_count=cpu_count,
        total_memory_bytes=total_memory,
        leanbench_version=LEANBENCH_VERSION,
        package_versions=dict(sorted(versions.items())),
        tokenizer=tokenizer,
        tokenizer_approximate=approximate,
        tokenizer_unavailable_reason=reason,
    )
