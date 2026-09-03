"""`leanbench-candidate.toml` loader (PROTOCOL.md §5)."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib
from pathlib import Path

from leanbench.kernel.errors import BenchmarkInfrastructureError, ProtocolError
from leanbench.schemas.protocol import PROTOCOL_VERSION, CandidateManifest

SUPPORTED_PROTOCOL_VERSIONS: frozenset[int] = frozenset({PROTOCOL_VERSION})
MANIFEST_FILENAME = "leanbench-candidate.toml"


def load_manifest(path: Path) -> CandidateManifest:
    """Read and validate a candidate manifest.

    A missing/unreadable/malformed file is LeanBench's problem to report as
    infrastructure; an *unknown protocol_version* is `protocol_error` (PROTOCOL.md §7).
    """
    path = Path(path)
    if path.is_dir():
        path = path / MANIFEST_FILENAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BenchmarkInfrastructureError(f"cannot read candidate manifest {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise BenchmarkInfrastructureError(f"malformed candidate manifest {path}: {exc}") from exc

    version = data.get("protocol_version")
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ProtocolError(
            f"candidate manifest {path} declares protocol_version {version!r}; "
            f"supported: {sorted(SUPPORTED_PROTOCOL_VERSIONS)}"
        )
    try:
        manifest = CandidateManifest.model_validate({**data, "manifest_path": str(path.resolve())})
    except ValueError as exc:
        raise BenchmarkInfrastructureError(f"invalid candidate manifest {path}: {exc}") from exc
    return manifest


def manifest_bytes(manifest: CandidateManifest) -> bytes:
    if manifest.manifest_path is None:
        raise BenchmarkInfrastructureError("manifest has no source path; cannot digest it")
    return Path(manifest.manifest_path).read_bytes()


def candidate_root(manifest: CandidateManifest) -> Path:
    """Directory the candidate's `cwd` and source-tree digest are relative to."""
    if manifest.manifest_path is None:
        raise BenchmarkInfrastructureError("manifest has no source path")
    base = Path(manifest.manifest_path).parent
    if manifest.runtime.cwd:
        return (base / manifest.runtime.cwd).resolve()
    return base.resolve()
