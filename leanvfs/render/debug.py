"""Debug renderer: compact plus provenance and confidence on every fact."""

from __future__ import annotations

from typing import Any

from ..budget import BudgetItem, BudgetReport, TokenCounter, admit
from ..views import FileView


class DebugRenderer:
    name = "debug"

    def render_file(self, view: FileView, budget: int, cfg: Any) -> tuple[str, BudgetReport]:
        counter = TokenCounter(cfg)
        lines = [
            "@lvfs 1 debug",
            f"@file {view.path}",
            f"lang={view.language} class={view.file_class} lines={view.line_count} "
            f"parse={view.parse_state}",
        ]
        items: list[BudgetItem] = []
        for kind in sorted(view.file_facts):
            for value, conf, prov, prio in view.file_facts[kind]:
                text = f"file.{kind}={value} @{prov} c={conf:.2f} p={prio}"
                items.append(BudgetItem(kind, prio, conf, text, counter.count(text), text))
        for sym in view.symbols:
            head = (
                f"{sym.kind} {sym.qualified_name} L{sym.line_start}-{sym.line_end} "
                f"vis={sym.visibility} sig={sym.signature}"
            )
            items.append(BudgetItem("symbol", 0, 1.0, sym.key, counter.count(head), head))
            for kind in sorted(sym.facts):
                for value, conf, prov, prio in sym.facts[kind]:
                    text = f"  {kind}={value} @{prov} c={conf:.2f} p={prio}"
                    items.append(
                        BudgetItem(kind, prio, conf, sym.key + text, counter.count(text), text)
                    )
            for term, score in sym.keywords:
                text = f"  keyword={term} score={score:.4f}"
                items.append(
                    BudgetItem("keyword", 2, 1.0, sym.key + text, counter.count(text), text)
                )
        admitted, report = admit(items, budget, None, counter)
        order = {id(i): n for n, i in enumerate(items)}
        admitted.sort(key=lambda i: order[id(i)])
        out = "\n".join(lines + [str(i.payload) for i in admitted]) + "\n"
        report.tokens_approx = counter.count(out)
        return out, report
