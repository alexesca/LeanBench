"""The fact-kind registry.

Extractors emit facts *through* the registry. An unregistered kind is a hard error,
not a warning: the registry is the contract between extraction and every consumer
(budget, renderer, search, protocol). Priorities live here as CONFIG.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .model import Fact, SourceRange


class FactKindError(ValueError):
    """Raised when an extractor emits a kind the registry does not declare."""


@dataclass(frozen=True)
class FactKind:
    name: str
    grammar: str          # human-readable value grammar
    pattern: str          # regex the value must match ("" = free text)
    cardinality: str      # "one_per_symbol" | "many" | "one_per_file"
    default_priority: int
    description: str


#: Value grammars. Deliberately permissive on free text, strict on structured kinds.
_ID = r"[A-Za-z_][\w.]*"

_KINDS: tuple[FactKind, ...] = (
    FactKind("signature", "<name>(<params>)[->ret]", r"^.+\(.*\)", "one_per_symbol", 0,
             "Normalized callable signature."),
    FactKind("parameter", "<name>[:<type>][=]", r"^[\w*]+(:.*)?(=)?$", "many", 0,
             "One declared parameter; '=' suffix marks default-value presence."),
    FactKind("return_type", "<type>", r"^.+$", "one_per_symbol", 0,
             "Declared return annotation."),
    FactKind("return_shape", "ret=<shape>|yield=<shape>", r"^(ret|yield)=.+$", "one_per_symbol", 0,
             "Cheap syntactic return shape when no annotation exists."),
    FactKind("keyword", "<term>", r"^[\w.:-]+$", "many", 2,
             "Scored search term."),
    FactKind("call", "<target>", r"^.+$", "many", 1,
             "High-value call target."),
    FactKind("type_use", "<type>", r"^.+$", "many", 2,
             "A type referenced in an annotation."),
    FactKind("side_effect", "<vocabulary term>", r"^[a-z]+(:[a-z]+)?$", "many", 1,
             "Side effect from the configurable pattern table."),
    FactKind("exception", "<ExceptionName>", r"^.+$", "many", 1,
             "Exception raised or expected."),
    FactKind("route", "<method>=<path>", r"^.+$", "many", 1,
             "HTTP route registration."),
    FactKind("config_key", "<key>=<value>", r"^.+$", "many", 1,
             "Configuration key with a safe short value."),
    FactKind("resource", "<kind>=<name>", r"^.+$", "many", 2,
             "External resource (env var, file, service)."),
    FactKind("purpose", "<free text>", "", "one_per_symbol", 1,
             "What this file or symbol is for."),
    FactKind("invariant", "<free text>", "", "many", 0,
             "Something that must always/never hold."),
    FactKind("warning", "<free text>", "", "many", 2,
             "A caveat a caller must know."),
    FactKind("rationale", "<free text>", "", "many", 2,
             "Why the code is the way it is."),
    FactKind("performance_note", "<free text>", "", "many", 2,
             "Performance-relevant remark."),
    FactKind("security_note", "<free text>", "", "many", 0,
             "Security-relevant remark."),
    FactKind("test_expectation", "<free text>", "", "many", 0,
             "What a test asserts."),
    FactKind("test_fixture", "<name>", r"^[\w.]+$", "many", 2,
             "Fixture a test consumes."),
    FactKind("test_mock", "<target>", r"^.+$", "many", 2,
             "Symbol a test mocks or patches."),
    FactKind("test_case", "<free text>", "", "many", 2,
             "A parameterized case."),
    FactKind("documentation", "<free text>", "", "many", 2,
             "Documentation excerpt or concept."),
    FactKind("architecture_decision", "<free text>", "", "many", 1,
             "A recorded design decision."),
    FactKind("framework_metadata", "<key>=<value>", r"^.+$", "many", 3,
             "Decorator/attribute/framework annotation."),
)

KIND_NAMES: tuple[str, ...] = tuple(k.name for k in _KINDS)


class FactRegistry:
    """The only legal way to construct a Fact."""

    def __init__(self, cfg: Any) -> None:
        self.kinds: dict[str, FactKind] = {}
        self.priorities: dict[str, int] = {}
        self._patterns: dict[str, re.Pattern[str] | None] = {}
        for kind in _KINDS:
            self.kinds[kind.name] = kind
            self.priorities[kind.name] = int(
                cfg.get(f"priorities.{kind.name}", kind.default_priority)
            )
            self._patterns[kind.name] = re.compile(kind.pattern) if kind.pattern else None
        self.validate_values = True

    def __contains__(self, kind: str) -> bool:
        return kind in self.kinds

    def priority(self, kind: str) -> int:
        try:
            return self.priorities[kind]
        except KeyError:
            raise FactKindError(f"unregistered fact kind: {kind!r}") from None

    def make(
        self,
        kind: str,
        value: str,
        *,
        file_path: str,
        symbol_key: str | None = None,
        confidence: float = 1.0,
        provenance: str = "ast",
        priority: int | None = None,
        range: SourceRange | None = None,
    ) -> Fact:
        spec = self.kinds.get(kind)
        if spec is None:
            raise FactKindError(
                f"unregistered fact kind: {kind!r} (registered: {', '.join(KIND_NAMES)})"
            )
        value = value.strip()
        if not value:
            raise FactKindError(f"empty value for fact kind {kind!r}")
        pattern = self._patterns[kind]
        if self.validate_values and pattern is not None and not pattern.match(value):
            raise FactKindError(
                f"value {value!r} violates grammar {spec.grammar!r} for kind {kind!r}"
            )
        return Fact(
            kind=kind,
            value=value,
            priority=self.priorities[kind] if priority is None else priority,
            confidence=confidence,
            provenance=provenance,
            symbol_key=symbol_key,
            file_path=file_path,
            range=range or SourceRange(),
        )

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": k.name,
                "grammar": k.grammar,
                "pattern": k.pattern,
                "cardinality": k.cardinality,
                "priority": self.priorities[k.name],
                "default_priority": k.default_priority,
                "description": k.description,
            }
            for k in _KINDS
        ]
