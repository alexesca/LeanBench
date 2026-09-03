"""Symbol lookup backed by SQLite indexes.

Deliberately *not* an in-memory table built per update: product invariant 4 says work
per edit is O(changed file + resolution fan-in), and a full in-memory rebuild would be
O(repo). Every lookup here is an indexed query, and `module -> path` is computed from
the path convention rather than by scanning.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .resolve import SymInfo, module_name_for


class SqlSymbolIndex:
    def __init__(self, conn: sqlite3.Connection, preload: bool = False) -> None:
        self.conn = conn
        self._name_cache: dict[str, list[SymInfo]] = {}
        self._qual_cache: dict[tuple[str, str], SymInfo | None] = {}
        self._name_file_cache: dict[tuple[str, str], list[SymInfo]] = {}
        self._scope_cache: dict[str, dict[str, tuple[str, str]]] = {}
        self._module_symbol_cache: dict[str, SymInfo | None] = {}
        self._module_path_cache: dict[str, str | None] = {}
        self._key_cache: dict[str, SymInfo | None] = {}
        self._paths: set[str] | None = None
        self.preloaded = False
        if preload:
            self._preload()

    # -- construction ----------------------------------------------------
    def _row(self, r: Any) -> SymInfo:
        return SymInfo(
            id=int(r["id"]),
            key=r["stable_key"],
            name=r["name"],
            qualified_name=r["qualified_name"],
            path=r["path"],
            kind=r["kind"],
            file_class=r["file_class"],
            is_exported=bool(r["is_exported"]),
        )

    def _preload(self) -> None:
        rows = self.conn.execute(
            "SELECT s.id, s.stable_key, s.name, s.qualified_name, s.kind, s.is_exported, "
            "f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id ORDER BY s.stable_key"
        )
        for r in rows:
            sym = self._row(r)
            self._name_cache.setdefault(sym.name, []).append(sym)
            self._qual_cache[(sym.path, sym.qualified_name)] = sym
            self._name_file_cache.setdefault((sym.path, sym.name), []).append(sym)
            self._key_cache[sym.key] = sym
            if sym.kind == "module":
                self._module_symbol_cache[sym.path] = sym
        for bucket in self._name_cache.values():
            bucket.sort(key=SymInfo.rank)
        for bucket in self._name_file_cache.values():
            bucket.sort(key=SymInfo.rank)
        self.preloaded = True

    def invalidate(self) -> None:
        self._name_cache.clear()
        self._qual_cache.clear()
        self._name_file_cache.clear()
        self._scope_cache.clear()
        self._module_symbol_cache.clear()
        self._module_path_cache.clear()
        self._key_cache.clear()
        self._paths = None
        self.preloaded = False

    # -- lookups ----------------------------------------------------------
    def lookup_name(self, name: str) -> list[SymInfo]:
        hit = self._name_cache.get(name)
        if hit is not None:
            return hit
        if self.preloaded:
            return []
        rows = self.conn.execute(
            "SELECT s.id, s.stable_key, s.name, s.qualified_name, s.kind, s.is_exported, "
            "f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.name=?",
            (name,),
        )
        out = sorted((self._row(r) for r in rows), key=SymInfo.rank)
        self._name_cache[name] = out
        return out

    def lookup_qual_in_file(self, path: str, qual: str) -> SymInfo | None:
        key = (path, qual)
        if key in self._qual_cache:
            return self._qual_cache[key]
        if self.preloaded:
            return None
        row = self.conn.execute(
            "SELECT s.id, s.stable_key, s.name, s.qualified_name, s.kind, s.is_exported, "
            "f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE f.path=? AND s.qualified_name=? ORDER BY s.stable_key LIMIT 1",
            (path, qual),
        ).fetchone()
        out = self._row(row) if row else None
        self._qual_cache[key] = out
        return out

    def lookup_name_in_file(self, path: str, name: str) -> list[SymInfo]:
        key = (path, name)
        hit = self._name_file_cache.get(key)
        if hit is not None:
            return hit
        if self.preloaded:
            return []
        rows = self.conn.execute(
            "SELECT s.id, s.stable_key, s.name, s.qualified_name, s.kind, s.is_exported, "
            "f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE f.path=? AND s.name=?",
            (path, name),
        )
        out = sorted((self._row(r) for r in rows), key=SymInfo.rank)
        self._name_file_cache[key] = out
        return out

    def symbol_by_key(self, key: str) -> SymInfo | None:
        if key in self._key_cache:
            return self._key_cache[key]
        row = self.conn.execute(
            "SELECT s.id, s.stable_key, s.name, s.qualified_name, s.kind, s.is_exported, "
            "f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.stable_key=?",
            (key,),
        ).fetchone()
        out = self._row(row) if row else None
        self._key_cache[key] = out
        return out

    def module_path(self, module: str) -> str | None:
        """Convention-driven, so it costs one indexed lookup rather than a scan."""
        if module in self._module_path_cache:
            return self._module_path_cache[module]
        out: str | None = None
        if module:
            base = module.replace(".", "/")
            for candidate in (f"{base}.py", f"{base}/__init__.py", f"{base}.pyi"):
                if self._path_exists(candidate):
                    out = candidate
                    break
            if out is None:
                # a package rooted below the repo root, e.g. src/pkg/mod.py
                row = self.conn.execute(
                    "SELECT path FROM files WHERE path LIKE ? ORDER BY LENGTH(path), path LIMIT 1",
                    (f"%/{base}.py",),
                ).fetchone()
                if row is not None:
                    out = row["path"]
                else:
                    row = self.conn.execute(
                        "SELECT path FROM files WHERE path LIKE ? "
                        "ORDER BY LENGTH(path), path LIMIT 1",
                        (f"%/{base}/__init__.py",),
                    ).fetchone()
                    out = row["path"] if row is not None else None
        self._module_path_cache[module] = out
        return out

    def _path_exists(self, path: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM files WHERE path=? LIMIT 1", (path,)).fetchone()
        return row is not None

    def module_symbol(self, path: str) -> SymInfo | None:
        if path in self._module_symbol_cache:
            return self._module_symbol_cache[path]
        row = self.conn.execute(
            "SELECT s.id, s.stable_key, s.name, s.qualified_name, s.kind, s.is_exported, "
            "f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE f.path=? AND s.kind='module' ORDER BY s.stable_key LIMIT 1",
            (path,),
        ).fetchone()
        out = self._row(row) if row else None
        self._module_symbol_cache[path] = out
        return out

    def module_symbol_for_module(self, module: str) -> SymInfo | None:
        path = self.module_path(module)
        return self.module_symbol(path) if path else None

    def resolve_path_like(self, raw: str) -> SymInfo | None:
        raw = raw.lstrip("./")
        sym = self.module_symbol(raw)
        if sym is not None:
            return sym
        row = self.conn.execute(
            "SELECT path FROM files WHERE path LIKE ? ORDER BY LENGTH(path), path LIMIT 1",
            (f"%/{raw}",),
        ).fetchone()
        return self.module_symbol(row["path"]) if row is not None else None

    def import_scope(self, path: str) -> dict[str, tuple[str, str]]:
        if path in self._scope_cache:
            return self._scope_cache[path]
        rows = self.conn.execute(
            "SELECT i.module, i.alias, i.names, i.is_relative, i.level FROM imports i "
            "JOIN files f ON f.id = i.file_id WHERE f.path=? ORDER BY i.id",
            (path,),
        )
        from .resolve import build_import_scope

        scope = build_import_scope(
            path,
            [(r["module"], r["alias"], r["names"], int(r["is_relative"]), int(r["level"]))
             for r in rows],
        )
        self._scope_cache[path] = scope
        return scope

    def module_name_for(self, path: str) -> str:
        return module_name_for(path)
