"""Relationship resolution — Phase B.

Resolution is a first-class subsystem, not a side effect of parsing. Phase A (in the
adapters, parallel, per file) emits *unresolved references* carrying raw target text
and syntactic hints. Phase B (here, sequential, after every file has been parsed)
resolves them against the complete symbol table.

Confidence tiers:
  R0 same-file / same-class qualified match      1.00
  R1 resolved through the import graph           0.90
  R2 name unique across the repo symbol table    0.70
  R3 ambiguous, N candidates                     min(0.50, 1/N)
  R4 unresolved -> target_external kept          0.30

R4 edges are still emitted. "Calls something named stripe.charge" is useful; a
reference is never dropped for being unresolvable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .model import UnresolvedRef


@dataclass
class SymInfo:
    id: int
    key: str
    name: str
    qualified_name: str
    path: str
    kind: str
    file_class: str
    is_exported: bool

    @property
    def is_test(self) -> bool:
        return self.file_class.startswith("test.")

    def rank(self) -> tuple[int, int, str]:
        """Deterministic preference: exported first, non-test first, then stable key."""
        return (0 if self.is_exported else 1, 1 if self.is_test else 0, self.key)


@dataclass
class ResolvedEdge:
    kind: str
    source_symbol_id: int
    target_symbol_id: int | None
    target_external: str
    confidence: float
    tier: str
    source_file_id: int
    line: int

    def sort_key(self) -> tuple:
        return (self.source_symbol_id, self.kind, self.target_symbol_id or 0,
                self.target_external, self.line)


def module_name_for(rel_path: str) -> str:
    stem = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    if stem.endswith(".pyi"):
        stem = stem[:-4]
    parts = [p for p in stem.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def build_import_scope(
    file_path: str,
    imports: Iterable[tuple[str, str, str, int, int]],
) -> dict[str, tuple[str, str]]:
    """local name -> (absolute module, original symbol name or '')."""
    package = module_name_for(file_path)
    package_parts = package.split(".") if package else []
    if file_path.endswith("__init__.py"):
        base_parts = package_parts
    else:
        base_parts = package_parts[:-1]
    scope: dict[str, tuple[str, str]] = {}
    for module, alias, names, is_relative, level in imports:
        abs_module = module
        if is_relative:
            up = base_parts[: len(base_parts) - (level - 1)] if level > 1 else base_parts
            abs_module = ".".join([p for p in [*up, module] if p])
        if names:
            for entry in names.split(","):
                if not entry:
                    continue
                orig, _, local = entry.partition("|")
                local = local or orig
                scope[local] = (abs_module, orig)
        else:
            local = alias or abs_module.split(".")[0]
            scope[local] = (abs_module, "")
            scope[abs_module] = (abs_module, "")
    return scope


class Resolver:
    def __init__(self, index: Any, cfg: Any) -> None:
        self.index = index
        tiers = cfg.get("resolution.tier_confidence", {}) or {}
        self.conf = {
            "R0": float(tiers.get("R0", 1.0)),
            "R1": float(tiers.get("R1", 0.9)),
            "R2": float(tiers.get("R2", 0.7)),
            "R3": float(tiers.get("R3", 0.5)),
            "R4": float(tiers.get("R4", 0.3)),
        }
        self.max_ambiguous = int(cfg.get("resolution.max_ambiguous", 3))
        self.emit_unresolved = bool(cfg.get("resolution.emit_unresolved", True))
        self.max_calls = int(cfg.get("calls.max_per_symbol", 10))

    # -- one reference ---------------------------------------------------
    def resolve(self, ref: UnresolvedRef, source_symbol_id: int,
                source_file_id: int) -> tuple[list[ResolvedEdge], bool]:
        """Return (edges, resolved). `resolved` is False for R4."""
        idx = self.index
        path = ref.source_file
        name = ref.name

        if ref.kind == "IMPORTS":
            return self._resolve_import(ref, source_symbol_id, source_file_id)
        if ref.kind in ("DOCUMENTS",):
            target = self._resolve_path_like(name)
            if target is not None:
                return ([self._edge(ref, source_symbol_id, source_file_id, target, "R1")], True)

        # -- R0: same file, and same class when the receiver says so ------
        src = idx.symbol_by_key(ref.source_symbol_key)
        if src is not None:
            class_qual = _class_context(src.qualified_name, src.kind)
            if ref.receiver in ("self", "cls", "") and class_qual:
                cand = idx.lookup_qual_in_file(path, f"{class_qual}.{name}")
                if cand is not None:
                    return ([self._edge(ref, source_symbol_id, source_file_id, cand, "R0")], True)
            if ref.receiver in ("self", "cls") and class_qual:
                # inherited/unknown attribute on self: fall through to weaker tiers
                pass
        same_file = idx.lookup_name_in_file(path, name)
        if len(same_file) == 1:
            return ([self._edge(ref, source_symbol_id, source_file_id, same_file[0], "R0")], True)
        if same_file:
            return ([self._edge(ref, source_symbol_id, source_file_id, same_file[0], "R0")], True)

        # -- R1: through the import graph ---------------------------------
        scope = idx.import_scope(path)
        target = None
        if ref.receiver and ref.receiver in scope:
            module, orig = scope[ref.receiver]
            target = self._lookup_in_module(module, name if not orig else f"{orig}.{name}")
            if target is None:
                target = self._lookup_in_module(module, name)
        if target is None and name in scope:
            module, orig = scope[name]
            target = self._lookup_in_module(module, orig or name)
            if target is None and not orig:
                target = idx.module_symbol_for_module(module)
        if target is None and ref.receiver:
            head = ref.receiver.split(".")[0]
            if head in scope:
                module, orig = scope[head]
                target = self._lookup_in_module(module, name)
        if target is not None:
            return ([self._edge(ref, source_symbol_id, source_file_id, target, "R1")], True)

        # -- R2 / R3: the global name table --------------------------------
        candidates = list(idx.lookup_name(name))
        if ref.kind == "TESTS":
            candidates = [c for c in candidates if not c.is_test]
        if ref.kind in ("EXTENDS", "USES_TYPE"):
            candidates = [c for c in candidates if c.kind in ("class", "test_class")] or candidates
        if len(candidates) == 1:
            return ([self._edge(ref, source_symbol_id, source_file_id, candidates[0], "R2")], True)
        if len(candidates) > 1:
            n = len(candidates)
            conf = min(self.conf["R3"], 1.0 / n)
            edges = [
                self._edge(ref, source_symbol_id, source_file_id, c, "R3", conf)
                for c in candidates[: self.max_ambiguous]
            ]
            return (edges, True)

        # -- R4: unresolved, but still recorded ----------------------------
        if not self.emit_unresolved:
            return ([], False)
        raw = ref.receiver + "." + name if ref.receiver else name
        return (
            [
                ResolvedEdge(
                    kind=ref.kind,
                    source_symbol_id=source_symbol_id,
                    target_symbol_id=None,
                    target_external=raw[:120],
                    confidence=self.conf["R4"],
                    tier="R4",
                    source_file_id=source_file_id,
                    line=ref.line,
                )
            ],
            False,
        )

    # -- helpers ---------------------------------------------------------
    def _edge(self, ref: UnresolvedRef, sid: int, fid: int, target: SymInfo, tier: str,
              conf: float | None = None) -> ResolvedEdge:
        return ResolvedEdge(
            kind=ref.kind,
            source_symbol_id=sid,
            target_symbol_id=target.id,
            target_external="",
            confidence=self.conf[tier] if conf is None else conf,
            tier=tier,
            source_file_id=fid,
            line=ref.line,
        )

    def _lookup_in_module(self, module: str, qual: str) -> SymInfo | None:
        path = self.index.module_path(module)
        if path is None:
            return None
        hit = self.index.lookup_qual_in_file(path, qual)
        if hit is not None:
            return hit
        bucket = self.index.lookup_name_in_file(path, qual.split(".")[-1])
        return bucket[0] if bucket else None

    def _resolve_path_like(self, raw: str) -> SymInfo | None:
        return self.index.resolve_path_like(raw)

    def _resolve_import(self, ref: UnresolvedRef, sid: int,
                        fid: int) -> tuple[list[ResolvedEdge], bool]:
        module = ref.name
        level = ref.arity if ref.arity and ref.arity > 0 else 0
        path = ref.source_file
        if level:
            package = module_name_for(path)
            parts = package.split(".") if package else []
            base = parts if path.endswith("__init__.py") else parts[:-1]
            up = base[: len(base) - (level - 1)] if level > 1 else base
            module = ".".join([p for p in [*up, module] if p])
        target_path = self.index.module_path(module)
        if target_path is None and module and "." in module:
            target_path = self.index.module_path(module.rsplit(".", 1)[0])
        if target_path is not None:
            sym = self.index.module_symbol(target_path)
            if sym is not None:
                return ([self._edge(ref, sid, fid, sym, "R1")], True)
        return (
            [
                ResolvedEdge(
                    kind="IMPORTS",
                    source_symbol_id=sid,
                    target_symbol_id=None,
                    target_external=module[:120],
                    confidence=self.conf["R4"],
                    tier="R4",
                    source_file_id=fid,
                    line=ref.line,
                )
            ],
            False,
        )

    # -- batch -----------------------------------------------------------
    def resolve_batch(
        self,
        refs: Sequence[tuple[UnresolvedRef, int, int]],
    ) -> tuple[list[ResolvedEdge], list[tuple[UnresolvedRef, int, int]]]:
        edges: list[ResolvedEdge] = []
        pending: list[tuple[UnresolvedRef, int, int]] = []
        call_counts: dict[int, int] = {}
        for ref, sid, fid in refs:
            got, ok = self.resolve(ref, sid, fid)
            if ref.kind == "CALLS":
                used = call_counts.get(sid, 0)
                if used >= self.max_calls:
                    if not ok:
                        pending.append((ref, sid, fid))
                    continue
                call_counts[sid] = used + len(got)
            edges.extend(got)
            if not ok:
                pending.append((ref, sid, fid))
        edges.sort(key=ResolvedEdge.sort_key)
        return dedupe(edges), pending


def dedupe(edges: Sequence[ResolvedEdge]) -> list[ResolvedEdge]:
    seen: set[tuple] = set()
    out: list[ResolvedEdge] = []
    for e in edges:
        key = (e.source_symbol_id, e.kind, e.target_symbol_id, e.target_external)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _class_context(qualified_name: str, kind: str) -> str:
    if kind in ("method", "property", "attribute") and "." in qualified_name:
        return qualified_name.rsplit(".", 1)[0]
    if kind in ("class", "test_class"):
        return qualified_name
    return ""


def tier_rates(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values())
    if not total:
        return {t: 0.0 for t in ("R0", "R1", "R2", "R3", "R4")}
    return {t: round(counts.get(t, 0) / total, 4) for t in ("R0", "R1", "R2", "R3", "R4")}
