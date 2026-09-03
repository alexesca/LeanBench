"""Adapter seam contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..model import FileExtraction, FileRecord, make_stable_key
from ..registry import FactRegistry


@dataclass
class ExtractionContext:
    """Everything an adapter is allowed to see.

    Note the absence of a `Config` object: adapters receive an already-resolved
    ``policy`` mapping. That keeps them free of config-resolution semantics and
    makes them trivially testable and portable.
    """

    rel_path: str
    source: bytes
    text: str
    file: FileRecord
    registry: FactRegistry
    policy: dict[str, Any] = field(default_factory=dict)

    def p(self, key: str, default: Any = None) -> Any:
        return self.policy.get(key, default)


class LanguageAdapter(Protocol):
    language: str

    def extract(self, ctx: ExtractionContext) -> FileExtraction:  # pragma: no cover
        ...


class MetadataOnlyAdapter:
    """Bottom of the fallback ladder: the file exists, has a class and a size."""

    language = "metadata"

    def extract(self, ctx: ExtractionContext) -> FileExtraction:
        ext = FileExtraction(file=ctx.file)
        key = make_stable_key("meta", ctx.rel_path, "module", ctx.rel_path)
        ext.symbols.append(
            _file_symbol(key, ctx.rel_path, ctx.file.line_count)
        )
        ext.canonicalize()
        return ext


def _file_symbol(key: str, rel_path: str, lines: int):
    from ..model import SourceRange, Symbol

    return Symbol(
        stable_key=key,
        name=rel_path.rsplit("/", 1)[-1],
        qualified_name=rel_path,
        kind="module",
        file_path=rel_path,
        visibility="public",
        range=SourceRange(1, max(1, lines), 0, 0),
    )
