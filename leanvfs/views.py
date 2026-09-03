"""Renderer-independent projections of the canonical model.

Renderers consume these; nothing here knows about output format. That decoupling is
what makes a format experiment cheap — and renderer format is one of the highest-value
experiments the benchmark can run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .store import Store


@dataclass
class SymbolView:
    key: str
    id: int
    name: str
    qualified_name: str
    kind: str
    signature: str
    return_type: str
    visibility: str
    line_start: int
    line_end: int
    is_async: bool
    doc: str
    parent_key: str | None
    decorators: list[str] = field(default_factory=list)
    keywords: list[tuple[str, float]] = field(default_factory=list)
    facts: dict[str, list[tuple[str, float, str, int]]] = field(default_factory=dict)

    def values(self, kind: str) -> list[str]:
        return [v for v, _c, _p, _pri in self.facts.get(kind, [])]


@dataclass
class FileView:
    path: str
    id: int
    language: str
    file_class: str
    line_count: int
    parse_state: str
    role: str
    imports_local: list[str] = field(default_factory=list)
    imports_ext: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    file_facts: dict[str, list[tuple[str, float, str, int]]] = field(default_factory=dict)
    keywords: list[tuple[str, float]] = field(default_factory=list)
    symbols: list[SymbolView] = field(default_factory=list)

    def values(self, kind: str) -> list[str]:
        return [v for v, _c, _p, _pri in self.file_facts.get(kind, [])]


def _facts_map(rows: list[Any]) -> dict[str, list[tuple[str, float, str, int]]]:
    out: dict[str, list[tuple[str, float, str, int]]] = {}
    for r in rows:
        out.setdefault(r["kind"], []).append(
            (r["value"], float(r["confidence"]), r["provenance"], int(r["priority"]))
        )
    for bucket in out.values():
        bucket.sort(key=lambda t: t[0])
    return out


def build_file_view(store: Store, path: str, cfg: Any) -> FileView | None:
    frow = store.file_by_path(path)
    if frow is None:
        return None
    fid = int(frow["id"])
    max_symbols = int(cfg.get("render.max_symbols_per_file", 400))
    kw_file = int(cfg.get("keywords.max_per_file", 15))
    kw_sym = int(cfg.get("keywords.max_per_symbol", 8))

    view = FileView(
        path=frow["path"],
        id=fid,
        language=frow["language"],
        file_class=frow["file_class"],
        line_count=int(frow["line_count"]),
        parse_state=frow["parse_state"],
        role=frow["role"] or "",
        keywords=[(r["term"], round(r["score"], 4)) for r in store.keywords_for_file(fid, kw_file)],
    )

    # imports
    local, ext = [], []
    for r in store.imports_for_file(fid):
        module = r["module"]
        if not module:
            continue
        target = ("." * int(r["level"])) + module if r["is_relative"] else module
        if r["is_local"] or r["is_relative"]:
            local.append(target)
        else:
            ext.append(module.split(".")[0])
    view.imports_local = sorted(set(local))
    view.imports_ext = sorted(set(ext))

    rows = list(
        store.conn.execute("SELECT * FROM symbols WHERE file_id=? ORDER BY stable_key", (fid,))
    )
    facts_by_symbol: dict[int, list[Any]] = {}
    file_level: list[Any] = []
    for fr in store.facts_for_file(fid):
        if fr["symbol_id"] is None:
            file_level.append(fr)
        else:
            facts_by_symbol.setdefault(int(fr["symbol_id"]), []).append(fr)
    view.file_facts = _facts_map(file_level)

    id_to_key = {int(r["id"]): r["stable_key"] for r in rows}
    for r in rows[:max_symbols]:
        sid = int(r["id"])
        sv = SymbolView(
            key=r["stable_key"],
            id=sid,
            name=r["name"],
            qualified_name=r["qualified_name"],
            kind=r["kind"],
            signature=r["signature"] or "",
            return_type=r["return_type"] or "",
            visibility=r["visibility"],
            line_start=int(r["line_start"]),
            line_end=int(r["line_end"]),
            is_async=bool(r["is_async"]),
            doc=r["doc"] or "",
            parent_key=id_to_key.get(int(r["parent_symbol_id"]))
            if r["parent_symbol_id"] is not None
            else None,
            decorators=[d for d in (r["decorators"] or "").split(",") if d],
            keywords=[
                (k["term"], round(k["score"], 4)) for k in store.keywords_for_symbol(sid, kw_sym)
            ],
            facts=_facts_map(facts_by_symbol.get(sid, [])),
        )
        view.symbols.append(sv)
        if r["kind"] == "module":
            view.exports = sorted(
                v.split("=", 1)[1]
                for v in sv.values("framework_metadata")
                if v.startswith("export=")
            )
            view.env = sorted(
                v.split("=", 1)[1] for v in sv.values("resource") if v.startswith("env=")
            )
            for kind, items in sv.facts.items():
                view.file_facts.setdefault(kind, []).extend(items)
            for bucket in view.file_facts.values():
                bucket.sort(key=lambda t: t[0])
    view.symbols.sort(key=lambda s: s.key)
    return view


def build_symbol_view(store: Store, row: Any, cfg: Any) -> SymbolView:
    sid = int(row["id"])
    kw_sym = int(cfg.get("keywords.max_per_symbol", 8))
    return SymbolView(
        key=row["stable_key"],
        id=sid,
        name=row["name"],
        qualified_name=row["qualified_name"],
        kind=row["kind"],
        signature=row["signature"] or "",
        return_type=row["return_type"] or "",
        visibility=row["visibility"],
        line_start=int(row["line_start"]),
        line_end=int(row["line_end"]),
        is_async=bool(row["is_async"]),
        doc=row["doc"] or "",
        parent_key=None,
        decorators=[d for d in (row["decorators"] or "").split(",") if d],
        keywords=[
            (k["term"], round(k["score"], 4)) for k in store.keywords_for_symbol(sid, kw_sym)
        ],
        facts=_facts_map(store.facts_for_symbol(sid)),
    )
