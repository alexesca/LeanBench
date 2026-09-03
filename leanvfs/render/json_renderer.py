"""JSON renderer. Same canonical model, different serialization."""

from __future__ import annotations

import json
from typing import Any

from ..budget import BudgetItem, BudgetReport, TokenCounter, admit, per_kind_caps
from ..views import FileView


class JsonRenderer:
    name = "json"

    def render_file(self, view: FileView, budget: int, cfg: Any) -> tuple[str, BudgetReport]:
        counter = TokenCounter(cfg)
        items: list[BudgetItem] = []
        for sym in view.symbols:
            payload = {
                "symbol": sym.qualified_name,
                "kind": sym.kind,
                "signature": sym.signature,
                "return_type": sym.return_type,
                "visibility": sym.visibility,
                "line_start": sym.line_start,
                "line_end": sym.line_end,
                "async": sym.is_async,
                "keywords": [t for t, _ in sym.keywords],
                "facts": {k: sorted({v for v, *_ in vs}) for k, vs in sorted(sym.facts.items())},
            }
            text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            items.append(BudgetItem("symbol", 0, 1.0, sym.key, counter.count(text), payload))
        admitted, report = admit(items, budget, per_kind_caps(cfg), counter)
        doc = {
            "file": {
                "path": view.path,
                "language": view.language,
                "class": view.file_class,
                "lines": view.line_count,
                "parse_state": view.parse_state,
                "role": view.role,
                "imports_local": view.imports_local,
                "imports_ext": view.imports_ext,
                "exports": view.exports,
                "keywords": [t for t, _ in view.keywords],
            },
            "symbols": sorted((i.payload for i in admitted), key=lambda p: p["symbol"]),
        }
        out = json.dumps(doc, sort_keys=True, indent=None, separators=(",", ":")) + "\n"
        report.tokens_approx = counter.count(out)
        return out, report
