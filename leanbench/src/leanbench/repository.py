"""`RepositoryPort` over a checked-out working copy. Read-only, path-escape proof.

Deliberately knows nothing about git: LeanBench checks the tree out, this class reads it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from leanbench.kernel.errors import GatewayError
from leanbench.kernel.registry import register
from leanbench.schemas.config import ResolvedConfig
from leanbench.scoring.normalize import normalize_path


class LocalRepository:
    """A pinned working copy on local disk."""

    def __init__(self, root: Path, config: ResolvedConfig) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise GatewayError(f"repository root {self.root} is not a directory")
        self.config = config
        self._excluded = set(config.get_list("gateway.excluded_dirs"))
        self._text_extensions = set(config.get_list("gateway.text_extensions"))
        self._cache: dict[str, str] = {}

    # --- path safety ----------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        rel = normalize_path(path)
        if not rel:
            return self.root
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise GatewayError(f"path {path!r} escapes the repository root")
        return target

    def _is_excluded(self, rel: str) -> bool:
        return any(part in self._excluded for part in rel.split("/"))

    # --- RepositoryPort -------------------------------------------------------

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def list_files(self, subpath: str | None = None) -> list[str]:
        base = self._resolve(subpath or "")
        if not base.exists():
            raise GatewayError(f"no such path: {subpath!r}")
        if base.is_file():
            return [base.relative_to(self.root).as_posix()]
        out: list[str] = []
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if self._is_excluded(rel):
                continue
            out.append(rel)
        return sorted(out)

    def read(self, path: str) -> str:
        rel = normalize_path(path)
        if rel in self._cache:
            return self._cache[rel]
        target = self._resolve(path)
        if not target.is_file():
            raise GatewayError(f"no such file: {path!r}")
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise GatewayError(f"cannot read {path!r}: {exc}") from exc
        self._cache[rel] = text
        return text

    def read_range(self, path: str, start: int, end: int) -> str:
        if start < 1 or end < start:
            raise GatewayError(f"invalid line range {start}-{end} for {path!r}")
        lines = self.read(path).splitlines()
        return "\n".join(lines[start - 1 : end])

    def stat(self, path: str) -> dict[str, Any]:
        rel = normalize_path(path)
        target = self._resolve(path)
        if not target.exists():
            return {"path": rel, "exists": False, "is_dir": False, "bytes": 0, "lines": 0}
        if target.is_dir():
            return {"path": rel, "exists": True, "is_dir": True, "bytes": 0, "lines": 0}
        raw = target.read_bytes()
        return {
            "path": rel,
            "exists": True,
            "is_dir": False,
            "bytes": len(raw),
            "lines": raw.count(b"\n") + (0 if raw.endswith(b"\n") or not raw else 1),
        }

    def search(self, pattern: str, limit: int) -> list[dict[str, Any]]:
        """Literal-or-regex text search. Ordered by (path, line) — never by walk order."""
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))
        matches: list[dict[str, Any]] = []
        for rel in self.list_files():
            if Path(rel).suffix not in self._text_extensions:
                continue
            try:
                text = self.read(rel)
            except GatewayError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append({"path": rel, "line": lineno, "text": line.strip()})
        matches.sort(key=lambda m: (m["path"], m["line"]))
        return matches[:limit]


register("repository", "local", LocalRepository)
