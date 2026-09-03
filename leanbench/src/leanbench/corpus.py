"""Corpus manifest loader — resolves a task's `repository`/`commit` to a checkout."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib
from dataclasses import dataclass
from pathlib import Path

from leanbench.kernel.errors import BenchmarkInfrastructureError

MANIFEST_NAME = "corpus-manifest.toml"


@dataclass(frozen=True)
class RepositoryEntry:
    id: str
    name: str
    remote: str
    commit: str
    language: str
    role: str
    license: str
    root: str
    description: str = ""

    def path(self, repo_root: Path) -> Path:
        return (repo_root / self.root).resolve()


@dataclass(frozen=True)
class Corpus:
    entries: dict[str, RepositoryEntry]
    manifest_path: Path
    repo_root: Path

    def get(self, repo_id: str) -> RepositoryEntry:
        if repo_id not in self.entries:
            known = ", ".join(sorted(self.entries)) or "<none>"
            raise BenchmarkInfrastructureError(
                f"repository {repo_id!r} is not in the corpus manifest; known: {known}"
            )
        return self.entries[repo_id]

    def ids(self) -> list[str]:
        return sorted(self.entries)

    def commits(self) -> list[tuple[str, str]]:
        return sorted((e.id, e.commit) for e in self.entries.values())

    def resolve_path(self, repo_id: str) -> Path:
        path = self.get(repo_id).path(self.repo_root)
        if not path.is_dir():
            raise BenchmarkInfrastructureError(
                f"repository {repo_id!r} points at {path}, which does not exist"
            )
        return path


def default_manifest_path() -> Path:
    """`leanbench/corpus-manifest.toml`, found relative to this installed package."""
    return package_root() / MANIFEST_NAME


def package_root() -> Path:
    """The `leanbench/` directory that owns the fixtures and the corpus manifest."""
    return Path(__file__).resolve().parents[2]


def repository_root() -> Path:
    """The LeanBench git repository root (parent of `leanbench/`)."""
    return package_root().parent


def load_corpus(path: Path | None = None) -> Corpus:
    manifest_path = Path(path) if path is not None else default_manifest_path()
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BenchmarkInfrastructureError(f"cannot read corpus manifest {manifest_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BenchmarkInfrastructureError(f"malformed corpus manifest {manifest_path}: {exc}") from exc

    entries: dict[str, RepositoryEntry] = {}
    for raw in data.get("repositories", []):
        try:
            entry = RepositoryEntry(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                remote=raw.get("remote", ""),
                commit=raw["commit"],
                language=raw.get("language", "python"),
                role=raw.get("role", "fixture"),
                license=raw.get("license", ""),
                root=raw["root"],
                description=raw.get("description", ""),
            )
        except KeyError as exc:
            raise BenchmarkInfrastructureError(
                f"corpus manifest entry missing required key {exc}"
            ) from exc
        if entry.id in entries:
            raise BenchmarkInfrastructureError(f"duplicate corpus repository id {entry.id!r}")
        entries[entry.id] = entry
    return Corpus(
        entries=entries, manifest_path=manifest_path, repo_root=repository_root()
    )
