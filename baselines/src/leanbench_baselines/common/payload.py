"""Budget-aware result assembly (PROTOCOL.md §2, §3).

A handler produces a :class:`Payload`: a mandatory header plus an ordered list of
:class:`Item` s. The renderer admits items in order while the approximate token count
stays at or below ``token_budget`` and reports every drop through ``meta.truncated`` and
``meta.dropped``. Truncation is *prefix* truncation over a deterministically ordered
list, so the same query at the same budget always drops exactly the same content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from leanbench_baselines.common.tokens import approx_tokens, approx_tokens_json


@dataclass(frozen=True)
class Item:
    """One droppable unit of content."""

    field: str  # json result key this item appends to
    kind: str  # bucket name reported in meta.dropped
    data: Any  # json form
    text: str  # compact form (one line, no embedded newline)


@dataclass
class Payload:
    """Header is always emitted; items are admitted while budget allows."""

    header: dict[str, Any] = field(default_factory=dict)
    header_text: str = ""
    items: list[Item] = field(default_factory=list)
    #: json keys that must exist (as empty lists) even when nothing is admitted.
    list_fields: tuple[str, ...] = ()
    #: Structural results (prepare_repository, update_repository, get_stats, shutdown)
    #: carry their machine-readable fields under `compact` too: LeanBench reads
    #: `files` / `symbols` / `index_bytes` from them, and a prose rendering would lose
    #: information the protocol reserves. The compact text is added alongside.
    structural: bool = False


@dataclass(frozen=True)
class Rendered:
    result: dict[str, Any]
    tokens_approx: int
    truncated: bool
    dropped: dict[str, int]


def render(payload: Payload, fmt: str, budget: int | None) -> Rendered:
    """Serialize *payload* under *budget* for ``format`` = ``compact`` or ``json``."""
    if fmt == "json":
        base: dict[str, Any] = dict(payload.header)
        for key in payload.list_fields:
            base.setdefault(key, [])
        used = approx_tokens_json(base)
    else:
        base = {}
        used = approx_tokens(payload.header_text)

    admitted: list[Item] = []
    dropped: dict[str, int] = {}
    stopped = False
    for item in payload.items:
        if stopped:
            dropped[item.kind] = dropped.get(item.kind, 0) + 1
            continue
        cost = (
            approx_tokens_json(item.data) + 2 if fmt == "json" else approx_tokens(item.text) + 1
        )
        if budget is not None and used + cost > budget:
            stopped = True
            dropped[item.kind] = dropped.get(item.kind, 0) + 1
            continue
        used += cost
        admitted.append(item)

    if fmt == "json":
        result = base
        for item in admitted:
            result.setdefault(item.field, [])
            if isinstance(result[item.field], list):
                result[item.field].append(item.data)
        tokens = approx_tokens_json(result)
    else:
        lines: list[str] = []
        if payload.header_text:
            lines.append(payload.header_text)
        lines.extend(item.text for item in admitted)
        text = "\n".join(lines)
        if payload.structural:
            result = dict(payload.header)
            result["text"] = text
        else:
            result = {"text": text}
        tokens = approx_tokens(text)

    return Rendered(
        result=result,
        tokens_approx=tokens,
        truncated=bool(dropped),
        dropped={key: dropped[key] for key in sorted(dropped)},
    )
