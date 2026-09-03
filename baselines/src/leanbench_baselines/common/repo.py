"""Repository walking and file access shared by all four baselines.

Everything here is *content-agnostic*: listing, ignore rules, binary detection, line
access. No baseline-specific intelligence lives in this module, so a lower rung cannot
accidentally inherit a higher rung's technique from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Files larger than this are listed but never read (protects against vendored blobs).
MAX_READ_BYTES = 2_000_000

#: Extensions we treat as source for scanning/searching purposes.
SOURCE_SUFFIXES = frozenset({".py", ".pyi"})
DOC_SUFFIXES = frozenset({".md", ".rst", ".txt"})

_BINARY_SNIFF_BYTES = 8192


class RepositoryError(Exception):
    """Raised when the repository cannot be walked or read."""


@dataclass(frozen=True)
class FileRecord:
    path: str  # repository-relative, POSIX separators
    size: int
    is_source: bool
    is_doc: bool
    is_test: bool


def is_test_path(rel_path: str) -> bool:
    parts = rel_path.split("/")
    if any(part in {"tests", "test", "testing"} for part in parts[:-1]):
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def is_doc_path(rel_path: str) -> bool:
    suffix = Path(rel_path).suffix.lower()
    if suffix in DOC_SUFFIXES:
        return True
    return rel_path.split("/")[0] in {"docs", "doc"}


class Repository:
    """A prepared working copy. Paths in and out are repository-relative POSIX."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise RepositoryError(f"not a directory: {root}")
        from leanbench_baselines.common.ignores import IgnoreIndex

        self._ignores = IgnoreIndex.for_repo(self.root)
        self._files: list[FileRecord] = []
        self._by_path: dict[str, FileRecord] = {}
        self._text_cache: dict[str, str | None] = {}
        self._lines_cache: dict[str, tuple[str, ...]] = {}
        self._scan()

    # -- listing ---------------------------------------------------------------

    def _scan(self) -> None:
        records: list[FileRecord] = []
        stack: list[Path] = [self.root]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(directory.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                raise RepositoryError(f"cannot list {directory}: {exc}") from exc
            for entry in entries:
                rel = entry.relative_to(self.root).as_posix()
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if not self._ignores.is_ignored(rel, is_dir=True):
                        stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
                if self._ignores.is_ignored(rel, is_dir=False):
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                records.append(
                    FileRecord(
                        path=rel,
                        size=size,
                        is_source=entry.suffix.lower() in SOURCE_SUFFIXES,
                        is_doc=is_doc_path(rel),
                        is_test=is_test_path(rel),
                    )
                )
        records.sort(key=lambda r: r.path)
        self._files = records
        self._by_path = {r.path: r for r in records}

    @property
    def files(self) -> list[FileRecord]:
        """All non-ignored files, sorted by path."""
        return self._files

    def source_files(self) -> list[FileRecord]:
        return [r for r in self._files if r.is_source]

    def has(self, rel_path: str) -> bool:
        return rel_path in self._by_path

    def record(self, rel_path: str) -> FileRecord | None:
        return self._by_path.get(rel_path)

    @property
    def source_bytes(self) -> int:
        return sum(r.size for r in self._files)

    # -- reading ---------------------------------------------------------------

    def read_text(self, rel_path: str) -> str | None:
        """Decoded text, or ``None`` for binary / oversized / unreadable files."""
        if rel_path in self._text_cache:
            return self._text_cache[rel_path]
        record = self._by_path.get(rel_path)
        text: str | None = None
        if record is not None and record.size <= MAX_READ_BYTES:
            path = self.root / rel_path
            try:
                data = path.read_bytes()
            except OSError:
                data = None
            if data is not None and b"\x00" not in data[:_BINARY_SNIFF_BYTES]:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
        self._text_cache[rel_path] = text
        return text

    def lines(self, rel_path: str) -> tuple[str, ...]:
        """1-based line access helper: ``lines(p)[n - 1]`` is line ``n``."""
        cached = self._lines_cache.get(rel_path)
        if cached is not None:
            return cached
        text = self.read_text(rel_path)
        result = tuple(text.splitlines()) if text is not None else ()
        self._lines_cache[rel_path] = result
        return result

    def line(self, rel_path: str, number: int) -> str:
        rows = self.lines(rel_path)
        if 1 <= number <= len(rows):
            return rows[number - 1]
        return ""

    def snippet(self, rel_path: str, start: int, end: int) -> list[str]:
        """Inclusive 1-based line range, clamped to the file."""
        rows = self.lines(rel_path)
        lo = max(1, start)
        hi = min(len(rows), end)
        if lo > hi:
            return []
        return list(rows[lo - 1 : hi])

    def invalidate(self, rel_path: str) -> None:
        self._text_cache.pop(rel_path, None)
        self._lines_cache.pop(rel_path, None)

    def rescan(self) -> None:
        self._text_cache.clear()
        self._lines_cache.clear()
        self._scan()
