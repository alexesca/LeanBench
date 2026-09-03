"""The canonical model.

Pure data + pure functions. No SQLite, no renderer, no I/O. This is the portable
core: a rusqlite/serde port is a transliteration, not a redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Vocabularies (closed sets — membership is validated, not assumed)
# --------------------------------------------------------------------------

FILE_CLASSES = (
    "source",
    "test.unit",
    "test.integration",
    "test.e2e",
    "documentation",
    "architecture",
    "configuration",
    "schema",
    "migration",
    "build",
    "deployment",
    "ci",
    "script",
    "generated",
    "binary",
    "data",
    "unknown",
)

TEST_CLASSES = ("test.unit", "test.integration", "test.e2e")
DOC_CLASSES = ("documentation", "architecture")

SYMBOL_KINDS = (
    "module",
    "class",
    "function",
    "method",
    "property",
    "attribute",
    "constant",
    "variable",
    "test",
    "test_class",
    "section",
    "config_key",
    "decision",
)

VISIBILITIES = ("public", "protected", "private", "internal")

RELATIONSHIP_KINDS = (
    "IMPORTS",
    "CALLS",
    "REFERENCES",
    "USES_TYPE",
    "EXTENDS",
    "IMPLEMENTS",
    "TESTS",
    "COVERS",
    "MOCKS",
    "USES_FIXTURE",
    "DOCUMENTS",
    "MENTIONS",
    "EXPLAINS",
    "DECIDES",
    "CONFIGURES",
    "READS",
    "WRITES",
    "PUBLISHES",
    "SUBSCRIBES",
)

PROVENANCES = (
    "ast",
    "comment",
    "docstring",
    "test",
    "markdown",
    "configuration",
    "heuristic",
    "framework",
)

PARSE_STATES = ("ok", "partial", "failed", "skipped")

INDEX_STATES = ("ok", "stale", "partial", "failed")

SIDE_EFFECT_VOCABULARY = (
    "db:r",
    "db:w",
    "fs:r",
    "fs:w",
    "http",
    "queue:publish",
    "queue:consume",
    "cache:r",
    "cache:w",
    "subprocess",
    "crypto",
    "email",
    "env:r",
    "clock",
    "random",
)

RESOLUTION_TIERS = ("R0", "R1", "R2", "R3", "R4")


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class SourceRange:
    """1-based inclusive line span plus byte offsets. Never part of an identity."""

    line_start: int = 0
    line_end: int = 0
    byte_start: int = 0
    byte_end: int = 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.line_start, self.line_end, self.byte_start, self.byte_end)


@dataclass
class FileRecord:
    path: str
    language: str = "unknown"
    file_class: str = "unknown"
    byte_size: int = 0
    line_count: int = 0
    source_hash: str = ""
    structure_hash: str = ""
    parse_state: str = "ok"
    generation: int = 0
    id: int = 0
    role: str = ""
    encoding: str = "utf-8"
    error: str = ""

    def sort_key(self) -> str:
        return self.path


@dataclass
class Symbol:
    stable_key: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    visibility: str = "public"
    signature: str = ""
    return_type: str = ""
    parent_key: str | None = None
    range: SourceRange = field(default_factory=SourceRange)
    interface_hash: str = ""
    behavior_hash: str = ""
    doc_hash: str = ""
    metadata_hash: str = ""
    doc: str = ""
    is_async: bool = False
    is_exported: bool = False
    decorators: tuple[str, ...] = ()
    id: int = 0
    file_id: int = 0
    parent_symbol_id: int | None = None
    generation: int = 0

    def sort_key(self) -> str:
        return self.stable_key


@dataclass
class Fact:
    kind: str
    value: str
    priority: int = 2
    confidence: float = 1.0
    provenance: str = "ast"
    symbol_key: str | None = None
    file_path: str = ""
    range: SourceRange = field(default_factory=SourceRange)
    id: int = 0
    file_id: int = 0
    symbol_id: int | None = None
    generation: int = 0

    def sort_key(self) -> tuple[str, str, str]:
        return (self.symbol_key or "", self.kind, self.value)


@dataclass
class Relationship:
    kind: str
    source_symbol_key: str
    target_symbol_key: str | None
    target_external: str
    confidence: float
    tier: str
    source_file: str = ""
    target_file: str = ""
    line: int = 0
    id: int = 0
    generation: int = 0

    def sort_key(self) -> tuple[str, str, str]:
        return (
            self.source_symbol_key,
            self.kind,
            self.target_symbol_key or self.target_external,
        )


@dataclass
class UnresolvedRef:
    """Phase-A output: a raw textual reference awaiting the complete symbol table."""

    name: str
    kind: str
    source_symbol_key: str
    source_file: str
    line: int = 0
    receiver: str = ""
    alias_module: str = ""
    arity: int = -1
    generation: int = 0

    def sort_key(self) -> tuple[str, str, str, int]:
        return (self.source_symbol_key, self.kind, self.name, self.line)


@dataclass
class KeywordCandidate:
    term: str
    source: str
    symbol_key: str | None = None
    file_path: str = ""


@dataclass
class ScoredKeyword:
    term: str
    score: float
    source: str
    symbol_key: str | None = None
    file_path: str = ""

    def sort_key(self) -> tuple[float, str]:
        return (-self.score, self.term)


@dataclass
class ImportRecord:
    module: str
    alias: str
    names: tuple[str, ...]
    is_relative: bool
    level: int
    line: int
    resolved_path: str = ""
    is_local: bool = False


@dataclass
class FileExtraction:
    """Everything one file contributes. Buffered per file, merged in sorted path order."""

    file: FileRecord
    symbols: list[Symbol] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    refs: list[UnresolvedRef] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    keyword_candidates: list[KeywordCandidate] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    def canonicalize(self) -> None:
        """Impose the total ordering. Called before anything downstream sees it."""
        self.symbols.sort(key=Symbol.sort_key)
        self.facts.sort(key=Fact.sort_key)
        self.refs.sort(key=UnresolvedRef.sort_key)
        self.imports.sort(key=lambda i: (i.module, i.alias, i.names, i.line))
        self.keyword_candidates.sort(key=lambda k: (k.symbol_key or "", k.source, k.term))
        self.diagnostics.sort()

    def to_canonical(self) -> dict[str, Any]:
        """Renderer-independent JSON view. This is what goldens assert on."""
        self.canonicalize()
        return {
            "file": {
                "path": self.file.path,
                "language": self.file.language,
                "file_class": self.file.file_class,
                "line_count": self.file.line_count,
                "parse_state": self.file.parse_state,
                "structure_hash": self.file.structure_hash,
                "role": self.file.role,
            },
            "symbols": [
                {
                    "stable_key": s.stable_key,
                    "name": s.name,
                    "qualified_name": s.qualified_name,
                    "kind": s.kind,
                    "visibility": s.visibility,
                    "signature": s.signature,
                    "return_type": s.return_type,
                    "parent_key": s.parent_key,
                    "is_async": s.is_async,
                    "is_exported": s.is_exported,
                    "decorators": list(s.decorators),
                    "interface_hash": s.interface_hash,
                    "behavior_hash": s.behavior_hash,
                    "doc_hash": s.doc_hash,
                    "metadata_hash": s.metadata_hash,
                    "line_start": s.range.line_start,
                    "line_end": s.range.line_end,
                }
                for s in self.symbols
            ],
            "facts": [
                {
                    "symbol_key": f.symbol_key,
                    "kind": f.kind,
                    "value": f.value,
                    "priority": f.priority,
                    "confidence": round(f.confidence, 4),
                    "provenance": f.provenance,
                }
                for f in self.facts
            ],
            "refs": [
                {
                    "name": r.name,
                    "kind": r.kind,
                    "source_symbol_key": r.source_symbol_key,
                    "receiver": r.receiver,
                    "alias_module": r.alias_module,
                    "arity": r.arity,
                }
                for r in self.refs
            ],
            "imports": [
                {
                    "module": i.module,
                    "alias": i.alias,
                    "names": list(i.names),
                    "is_relative": i.is_relative,
                    "level": i.level,
                }
                for i in self.imports
            ],
            "keyword_candidates": [
                {"term": k.term, "source": k.source, "symbol_key": k.symbol_key}
                for k in self.keyword_candidates
            ],
        }


# --------------------------------------------------------------------------
# Stable identity — never line-dependent
# --------------------------------------------------------------------------


def normalize_param_discriminator(param_names: list[str]) -> str:
    """Parameter NAMES only, so a type-annotation change never reassigns identity."""
    return ",".join(param_names)


def make_stable_key(
    language: str,
    path: str,
    kind: str,
    qualified_name: str,
    param_names: list[str] | None = None,
) -> str:
    disc = normalize_param_discriminator(param_names or []) if param_names is not None else ""
    if param_names is None:
        return f"{language}:{path}:{kind}:{qualified_name}"
    return f"{language}:{path}:{kind}:{qualified_name}({disc})"


def disambiguate_overload(base_key: str, arity: int, param_types: list[str]) -> str:
    """Overloads disambiguate by arity, then by ordered parameter types."""
    types = ",".join(param_types)
    return f"{base_key}#{arity}#{types}"
