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

    def render_file(self, view: FileView, budget: int, cfg: Any) -> tuple[str, BudgetReport]:
        ...


from .compact import CompactRenderer  # noqa: E402
from .debug import DebugRenderer  # noqa: E402
from .json_renderer import JsonRenderer  # noqa: E402

RENDERERS: dict[str, Any] = {
    "compact": CompactRenderer(),
    "debug": DebugRenderer(),
    "json": JsonRenderer(),
}

__all__ = ["Renderer", "CompactRenderer", "DebugRenderer", "JsonRenderer", "RENDERERS",
           "get_renderer"]


def get_renderer(name: str) -> Any:
    try:
        return RENDERERS[name]
    except KeyError:
        raise ValueError(f"unknown renderer: {name!r} (have: {', '.join(sorted(RENDERERS))})") from None
