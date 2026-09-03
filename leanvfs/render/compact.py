"""The default compact representation.

Deterministic, grep-friendly, line-oriented, low-punctuation. Not JSON: JSON spends
a third of its tokens on structural characters that a model does not need.
"""

from __future__ import annotations

from typing import Any

from ..budget import BudgetItem, BudgetReport, TokenCounter, admit, per_kind_caps
from ..views import FileView, SymbolView

FORMAT_VERSION = 1

_NOTE_KINDS = (
    ("invariant", "invariant"),
    ("security_note", "note[security]"),
    ("warning", "note[warning]"),
    ("rationale", "note[why]"),
    ("performance_note", "note[perf]"),
    ("architecture_decision", "decision"),
)


class CompactRenderer:
    name = "compact"

    def render_file(self, view: FileView, budget: int, cfg: Any) -> tuple[str, BudgetReport]:
        counter = TokenCounter(cfg)
        indent = str(cfg.get("render.indent", "  "))
        show_kw = bool(cfg.get("render.show_keywords", True))
        show_calls = bool(cfg.get("render.show_calls", True))
        show_fx = bool(cfg.get("render.show_effects", True))
        show_notes = bool(cfg.get("render.show_notes", True))

        header = [
            f"@lvfs {FORMAT_VERSION}",
            f"@file {view.path}",
            f"lang={view.language} class={view.file_class} lines={view.line_count}",
        ]
        if view.parse_state != "ok":
            header.append(f"parse={view.parse_state}")
        if view.role:
            header.append(f"role={view.role}")

        items: list[BudgetItem] = []

        def add(kind: str, priority: int, confidence: float, value: str, payload: Any) -> None:
            items.append(
                BudgetItem(
                    kind=kind,
                    priority=priority,
                    confidence=confidence,
                    value=value,
                    tokens=counter.count(payload if isinstance(payload, str) else value),
                    payload=payload,
                )
            )

        if view.imports_local:
            add("import", 2, 1.0, "imports.local", f"imports.local={','.join(view.imports_local)}")
        if view.imports_ext:
            add("import", 2, 1.0, "imports.ext", f"imports.ext={','.join(view.imports_ext)}")
        if view.exports:
            add("export", 0, 1.0, "exports", f"exports={','.join(view.exports)}")
        if view.env:
            add("resource", 1, 1.0, "env", f"env={','.join(view.env)}")

        doc_lines: list[str] = []
        for value in sorted(view.values("purpose"))[:2]:
            doc_lines.append(f"{indent}purpose={value}")
        for value in sorted(view.values("invariant"))[:3]:
            doc_lines.append(f"{indent}invariant={value}")
        for value in sorted(view.values("architecture_decision"))[:3]:
            doc_lines.append(f"{indent}decision={value}")
        for value in sorted(view.values("documentation"))[:4]:
            doc_lines.append(f"{indent}doc={value}")
        if doc_lines:
            add("documentation", 1, 1.0, "doc", "doc:\n" + "\n".join(doc_lines))
        if show_kw and view.keywords:
            terms = ",".join(t for t, _ in view.keywords)
            add("keyword", 2, 1.0, "file.kw", f"kw={terms}")

        by_key = {s.key: s for s in view.symbols}
        for sym in view.symbols:
            if sym.kind == "module":
                continue
            depth = _depth(sym, by_key)
            block = self._symbol_block(sym, indent, depth, show_kw, show_calls, show_fx, show_notes)
            if not block:
                continue
            priority = (
                0
                if sym.kind in ("class", "function", "method", "property", "test_class", "test")
                else 1
            )
            add(
                "symbol",
                priority,
                1.0 if sym.visibility == "public" else 0.8,
                sym.key,
                "\n".join(block),
            )

        admitted, report = admit(items, budget, per_kind_caps(cfg), counter)
        order = {id(i): n for n, i in enumerate(items)}
        admitted.sort(key=lambda i: order[id(i)])

        body: list[str] = []
        prev_symbol = False
        for item in admitted:
            text = item.payload if isinstance(item.payload, str) else item.value
            if item.kind == "symbol":
                if (body and not prev_symbol) or prev_symbol:
                    body.append("")
                prev_symbol = True
            else:
                prev_symbol = False
            body.append(text)
        out = "\n".join(header + ([""] if body else []) + body) + "\n"
        report.tokens_approx = counter.count(out)
        return out, report

    def _symbol_block(
        self,
        sym: SymbolView,
        indent: str,
        depth: int,
        show_kw: bool,
        show_calls: bool,
        show_fx: bool,
        show_notes: bool,
    ) -> list[str]:
        pad = indent * depth
        head = _symbol_head(sym)
        if not head:
            return []
        lines = [pad + head]
        inner = pad + indent
        if show_kw and sym.keywords:
            lines.append(inner + "kw=" + ",".join(t for t, _ in sym.keywords))
        shape = sym.values("return_shape")
        if shape and not sym.return_type:
            lines.append(inner + shape[0])
        if show_calls:
            calls = sorted(set(sym.values("call")))
            if calls:
                lines.append(inner + "call=" + ",".join(calls))
        if show_fx:
            fx = sorted(set(sym.values("side_effect")))
            if fx:
                lines.append(inner + "fx=" + ",".join(fx))
        throws = sorted(set(sym.values("exception")))
        if throws:
            lines.append(inner + "throws=" + ",".join(throws))
        tests = sorted(set(sym.values("test_expectation")))[:3]
        for t in tests:
            lines.append(inner + "expects=" + t)
        fixtures = sorted(set(sym.values("test_fixture")))
        if fixtures:
            lines.append(inner + "fixtures=" + ",".join(fixtures))
        mocks = sorted(set(sym.values("test_mock")))
        if mocks:
            lines.append(inner + "mocks=" + ",".join(mocks))
        if show_notes:
            for kind, label in _NOTE_KINDS:
                for value in sorted(set(sym.values(kind)))[:2]:
                    lines.append(f"{inner}{label}={value}")
        purpose = sym.values("purpose")
        if purpose:
            lines.append(inner + "purpose=" + purpose[0])
        return lines


def _symbol_head(sym: SymbolView) -> str:
    loc = f"L{sym.line_start}-{sym.line_end}"
    if sym.kind in ("class", "test_class"):
        return f"class {sym.signature or sym.name} {loc}"
    if sym.kind in ("function", "method", "test"):
        prefix = "test" if sym.kind == "test" else "fn"
        suffix = " async" if sym.is_async else ""
        return f"{prefix} {sym.signature or sym.name}{suffix} {loc}"
    if sym.kind == "property":
        return f"prop {sym.signature or sym.name} {loc}"
    if sym.kind in ("constant", "attribute"):
        return f"var {sym.signature or sym.name} {loc}"
    if sym.kind == "section":
        return f"section {sym.qualified_name} {loc}"
    if sym.kind == "config_key":
        return f"key {sym.signature or sym.name} {loc}"
    return ""


def _depth(sym: SymbolView, by_key: dict[str, SymbolView]) -> int:
    depth = 0
    cur = sym
    seen: set[str] = set()
    while cur.parent_key and cur.parent_key in by_key and cur.parent_key not in seen:
        seen.add(cur.parent_key)
        parent = by_key[cur.parent_key]
        if parent.kind == "module":
            break
        depth += 1
        cur = parent
    return depth


# --- protocol payload renderers -------------------------------------------------
# The compact format is the token-efficient path and is what the benchmark's headline
# metric measures. It is line-oriented, low-punctuation and grep-friendly: no JSON
# braces, no quoting, no repeated field names. A JSON envelope for the same content
# costs roughly twice the tokens, almost all of it structural noise.


def render_hits(payload: dict) -> str:
    """`search` results. One line per hit, plus an indented locator line."""
    hits = payload.get("hits") or []
    if not hits:
        return "no matches"
    lines = []
    for hit in hits:
        score = hit.get("score") or 0.0
        lines.append(f"{score:.2f} {hit.get('path', '')}")
        symbol = hit.get("symbol")
        if symbol:
            start = hit.get("line_start") or 0
            end = hit.get("line_end") or 0
            span = f" L{start}-{end}" if start else ""
            lines.append(f"     {hit.get('kind', '')} {symbol}{span}")
    return "\n".join(lines)


def render_context(payload: dict) -> str:
    """`get_context`. Identity first, then whatever the budget admitted."""
    if not payload:
        return "not found"
    head = payload.get("symbol", "")
    path = payload.get("path", "")
    start = payload.get("line_start") or 0
    end = payload.get("line_end") or 0
    lines = [f"@symbol {head}", f"at={path} L{start}-{end}"]
    signature = payload.get("signature")
    if signature:
        lines.append(f"sig={signature}")
    ret = payload.get("return_type")
    if ret:
        lines.append(f"ret={ret}")
    visibility = payload.get("visibility")
    if visibility and visibility != "public":
        lines.append(f"vis={visibility}")

    skip = {
        "symbol",
        "path",
        "line_start",
        "line_end",
        "signature",
        "return_type",
        "visibility",
        "kind",
        "budget_report",
    }
    for key in sorted(k for k in payload if k not in skip):
        value = payload[key]
        if isinstance(value, list):
            if value:
                lines.append(f"{key}={','.join(str(v) for v in value)}")
        elif value:
            lines.append(f"{key}={value}")

    report = payload.get("budget_report") or {}
    if report.get("truncated"):
        dropped = report.get("dropped") or {}
        summary = ",".join(f"{k}:{v}" for k, v in sorted(dropped.items()))
        # Truncation is always announced: an agent that cannot tell it got a partial
        # answer will not know to ask for more, which is a correctness bug.
        lines.append(f"truncated dropped={summary}")
    return "\n".join(lines)
