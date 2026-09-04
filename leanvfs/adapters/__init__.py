"""Language adapters.

The seam exists from day one. Adapters return canonical records and depend on
nothing but the canonical model, the fact registry, and an injected policy mapping.
They must NOT import the store, the renderer, or the config loader — enforced by
``tests/test_module_boundaries.py``.

Fallback ladder: rich adapter -> generic tree-sitter symbols -> text/document
extractor -> metadata only.
"""

from __future__ import annotations

from .base import ExtractionContext, LanguageAdapter, MetadataOnlyAdapter
from .config_files import ConfigAdapter
from .generic import GenericTextAdapter
from .markdown import MarkdownAdapter
from .python_adapter import PythonAdapter

__all__ = [
    "ADAPTERS",
    "ConfigAdapter",
    "ExtractionContext",
    "GenericTextAdapter",
    "LanguageAdapter",
    "MarkdownAdapter",
    "MetadataOnlyAdapter",
    "PythonAdapter",
    "select_adapter",
]

ADAPTERS: dict[str, LanguageAdapter] = {}


def _register(adapter: LanguageAdapter) -> None:
    ADAPTERS[adapter.language] = adapter


_register(PythonAdapter())
_register(MarkdownAdapter())
_register(ConfigAdapter())
_register(GenericTextAdapter())
_METADATA_ONLY = MetadataOnlyAdapter()


def select_adapter(language: str, file_class: str) -> LanguageAdapter:
    """The fallback ladder, resolved to a single adapter."""
    if file_class in ("binary", "generated"):
        return _METADATA_ONLY
    if language in ADAPTERS:
        return ADAPTERS[language]
    if language in ("toml", "yaml", "json", "ini"):
        return ADAPTERS["config"]
    if language in ("markdown",):
        return ADAPTERS["markdown"]
    if language == "binary":
        return _METADATA_ONLY
    # Rung three catches everything else, including languages with no rich adapter.
    # The generic adapter reads declaration patterns from config, so a Go or TypeScript
    # file yields real symbols rather than a single metadata stub; a language with no
    # patterns still yields file metadata and keywords, which beats nothing.
    return ADAPTERS["text"]
