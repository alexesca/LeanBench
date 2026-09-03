"""Renderer seam.

The canonical model stays renderer-independent. Swapping `CompactRenderer` for a
format experiment must not touch extraction, storage, or the goldens.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..budget import BudgetReport
from ..views import FileView


class Renderer(Protocol):
    name: str

    def render_file(self, view: FileView, budget: int, cfg: Any) -> tuple[str, BudgetReport]: ...


from .compact import CompactRenderer
from .debug import DebugRenderer
from .json_renderer import JsonRenderer

RENDERERS: dict[str, Any] = {
    "compact": CompactRenderer(),
    "debug": DebugRenderer(),
    "json": JsonRenderer(),
}

__all__ = [
    "RENDERERS",
    "CompactRenderer",
    "DebugRenderer",
    "JsonRenderer",
    "Renderer",
    "get_renderer",
]


def get_renderer(name: str) -> Any:
    try:
        return RENDERERS[name]
    except KeyError:
        raise ValueError(
            f"unknown renderer: {name!r} (have: {', '.join(sorted(RENDERERS))})"
        ) from None
