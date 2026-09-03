"""MinimalAST baseline -- the discrimination gate's reference point.

Capability ceiling (deliberate): a **tree-sitter symbol table**. Functions, classes,
methods, parameter names and annotations, return annotations, decorators, source spans,
the class/module nesting hierarchy, imports (resolved to repository paths where the
target is local), and raw identifier occurrences.

Deliberately absent, because a full semantic system must earn them: documentation
intelligence, test intelligence, call/exception/effect resolution, keyword scoring over
file contents, and any budget engine cleverer than deterministic prefix truncation.
``search`` therefore ranks over the symbol table (names, scopes, signatures) only, and
``get_context`` returns identity + signature + location + the file's immediate imports.

Not implemented, and not declared: tests, docs.
"""

from __future__ import annotations

from typing import Any

from leanbench_baselines.common.payload import Item, Payload
from leanbench_baselines.common.repo import Repository
from leanbench_baselines.common.server import BaseServer, not_found
from leanbench_baselines.common.text import (
    dotted_parts,
    query_symbol_candidates,
    query_terms,
    split_identifier,
    stems,
)
from leanbench_baselines.common.tokens import canonical_json
from leanbench_baselines.minast.parser import AstExtractor, FileAst, ImportRecord, Symbol

KIND_WEIGHT: dict[str, float] = {"class": 1.0, "method": 0.95, "function": 0.95}


class MinimalAstServer(BaseServer):
    NAME = "MinimalAST"
    VERSION = "0.1.0"
    CAPABILITIES = frozenset(
        {"search", "symbols", "context", "dependencies", "references", "incremental"}
    )

    def __init__(self) -> None:
        super().__init__()
        self.extractor = AstExtractor()
        self.asts: dict[str, FileAst] = {}
        self.by_qualified: dict[str, list[Symbol]] = {}
        self.by_name: dict[str, list[Symbol]] = {}
        self.module_to_path: dict[str, str] = {}
        self.local_imports: dict[str, list[str]] = {}
        self.external_imports: dict[str, list[str]] = {}
        self.imported_by: dict[str, list[str]] = {}
        self.index_bytes = 0
        self.parse_failures = 0

    # -- index -----------------------------------------------------------------

    def build_index(self, repo: Repository) -> dict[str, Any]:
        self.asts = {}
        self.parse_failures = 0
        for record in repo.source_files():
            text = repo.read_text(record.path)
            if text is None:
                continue
            file_ast = self.extractor.parse_file(record.path, text)
            if not file_ast.ok:
                self.parse_failures += 1
            self.asts[record.path] = file_ast
        self._rebuild()
        self.index_state = "partial" if self.parse_failures else "ok"
        return {
            "indexed": True,
            "symbols": sum(len(ast.symbols) for ast in self.asts.values()),
            "index_bytes": self.index_bytes,
            "files": len(repo.files),
            "parsed_files": len(self.asts),
            "parse_failures": self.parse_failures,
        }

    def reindex_paths(self, paths: list[str]) -> int:
        repo = self.require_repo()
        targets = paths or [record.path for record in repo.source_files()]
        reparsed = 0
        for path in sorted(set(targets)):
            record = repo.record(path)
            if record is None or not record.is_source:
                self.asts.pop(path, None)
                continue
            repo.invalidate(path)
            text = repo.read_text(path)
            if text is None:
                self.asts.pop(path, None)
                continue
            self.asts[path] = self.extractor.parse_file(path, text)
            reparsed += 1
        self._rebuild()
        return reparsed

    def _rebuild(self) -> None:
        by_qualified: dict[str, list[Symbol]] = {}
        by_name: dict[str, list[Symbol]] = {}
        module_to_path: dict[str, str] = {}
        for path in sorted(self.asts):
            for symbol in self.asts[path].symbols:
                by_qualified.setdefault(symbol.qualified, []).append(symbol)
                by_name.setdefault(symbol.name, []).append(symbol)
            for module in self._modules_for_path(path):
                module_to_path.setdefault(module, path)
        for bucket in (by_qualified, by_name):
            for entries in bucket.values():
                entries.sort(key=lambda symbol: (symbol.path, symbol.line_start))
        self.by_qualified = by_qualified
        self.by_name = by_name
        self.module_to_path = module_to_path
        self._resolve_imports()
        self.index_bytes = sum(
            len(canonical_json(self._symbol_record(symbol)).encode("utf-8"))
            for symbols in by_qualified.values()
            for symbol in symbols
        ) + sum(
            len(record.render().encode("utf-8")) + 16
            for ast in self.asts.values()
            for record in ast.imports
        )

    @staticmethod
    def _modules_for_path(path: str) -> list[str]:
        parts = path.split("/")
        candidates: list[list[str]] = [parts]
        if parts[0] in ("src", "lib"):
            candidates.append(parts[1:])
        modules: list[str] = []
        for candidate in candidates:
            if not candidate:
                continue
            trimmed = list(candidate)
            trimmed[-1] = trimmed[-1].removesuffix(".pyi").removesuffix(".py")
            if trimmed[-1] == "__init__":
                trimmed = trimmed[:-1]
            if trimmed:
                modules.append(".".join(trimmed))
        return modules

    def _resolve_module(self, record: ImportRecord, from_path: str) -> str | None:
        """Resolve an import to a repository path, or ``None`` if it is external."""
        if record.level:
            package = from_path.split("/")[:-1]
            if record.level > 1:
                package = package[: -(record.level - 1)] if record.level - 1 <= len(package) else []
            base = ".".join([*package, *([record.module] if record.module else [])])
        else:
            base = record.module
        if not base:
            return None
        for candidate in [f"{base}.{record.name}", base] if record.name else [base]:
            path = self.module_to_path.get(candidate)
            if path is not None:
                return path
        return None

    def _resolve_imports(self) -> None:
        local: dict[str, list[str]] = {}
        external: dict[str, list[str]] = {}
        imported_by: dict[str, set[str]] = {}
        for path in sorted(self.asts):
            local_paths: set[str] = set()
            external_modules: set[str] = set()
            for record in self.asts[path].imports:
                target = self._resolve_module(record, path)
                if target is not None and target != path:
                    local_paths.add(target)
                    imported_by.setdefault(target, set()).add(path)
                elif target is None and not record.level:
                    external_modules.add(record.module)
            local[path] = sorted(local_paths)
            external[path] = sorted(external_modules)
        self.local_imports = local
        self.external_imports = external
        self.imported_by = {key: sorted(value) for key, value in sorted(imported_by.items())}

    def stats(self) -> dict[str, Any]:
        return {
            "symbols": sum(len(ast.symbols) for ast in self.asts.values()),
            "facts": 0,
            "relationships": 0,
            "index_bytes": self.index_bytes,
            "parsed_files": len(self.asts),
            "parse_failures": self.parse_failures,
        }

    # -- rendering -------------------------------------------------------------

    def _symbol_record(self, symbol: Symbol) -> dict[str, Any]:
        return {
            "path": symbol.path,
            "symbol": symbol.qualified,
            "kind": symbol.kind,
            "signature": symbol.signature(),
            "return_type": symbol.return_type,
            "line_start": symbol.line_start,
            "line_end": symbol.line_end,
            "visibility": symbol.visibility,
            "decorators": list(symbol.decorators),
            "doc": symbol.doc,
        }

    @staticmethod
    def _symbol_text(record: dict[str, Any]) -> str:
        text = (
            f"{record['path']}:{record['line_start']}-{record['line_end']} "
            f"{record['kind']} {record['symbol']}\n  {record['signature']}"
        )
        if record["decorators"]:
            text += "\n  @" + " @".join(record["decorators"])
        if record["doc"]:
            text += f"\n  doc: {record['doc']}"
        return text

    def _all_symbols(self) -> list[Symbol]:
        out: list[Symbol] = []
        for path in sorted(self.asts):
            out.extend(self.asts[path].symbols)
        return out

    def _lookup(self, name: str) -> list[Symbol]:
        symbols = list(self.by_qualified.get(name, ()))
        if symbols:
            return symbols
        scope, leaf = dotted_parts(name)
        candidates = list(self.by_name.get(leaf, ()))
        if scope:
            wanted = scope.rsplit(".", 1)[-1]
            scoped = [
                symbol
                for symbol in candidates
                if symbol.scope and symbol.scope.split(".")[-1] == wanted
            ]
            if scoped:
                return scoped
        return candidates

    # -- ops -------------------------------------------------------------------

    def op_search(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        query = self.arg_str(args, "query")
        limit = self.arg_int(args, "limit", 10)
        keys = stems(query_terms(query) or [query.strip().lower()])
        literals = {candidate.lower() for candidate in query_symbol_candidates(query)}
        literals.add(query.strip().lower())

        scored: list[tuple[tuple[float, str, int], Symbol]] = []
        for symbol in self._all_symbols():
            name_stems = stems(split_identifier(symbol.qualified))
            covered = {key for key in keys if key in name_stems}
            partial = {
                key
                for key in keys
                if key not in covered and any(word.startswith(key) for word in name_stems)
            }
            signature_words = set()
            for param in symbol.params:
                signature_words.update(stems(split_identifier(param.name)))
                if param.annotation:
                    signature_words.update(stems(split_identifier(param.annotation)))
            if symbol.return_type:
                signature_words.update(stems(split_identifier(symbol.return_type)))
            for base in symbol.bases:
                signature_words.update(stems(split_identifier(base)))
            signature_covered = {key for key in keys if key in signature_words}
            exact = (
                1.0
                if symbol.name.lower() in literals or symbol.qualified.lower() in literals
                else 0.0
            )
            if not covered and not partial and not signature_covered and not exact:
                continue
            coverage = (len(covered) + 0.5 * len(partial)) / len(keys)
            signature_score = len(signature_covered) / len(keys)
            record = repo.record(symbol.path)
            class_weight = 0.6 if (record is not None and record.is_test) else 1.0
            score = round(
                0.50 * coverage
                + 0.15 * signature_score
                + 0.15 * KIND_WEIGHT.get(symbol.kind, 0.8) * class_weight
                + 0.30 * exact,
                4,
            )
            scored.append(((-score, symbol.path, symbol.line_start), symbol))

        scored.sort(key=lambda entry: entry[0])
        items: list[Item] = []
        for (neg_score, _, _), symbol in scored[:limit]:
            hit = {
                "path": symbol.path,
                "symbol": symbol.qualified,
                "kind": symbol.kind,
                "line_start": symbol.line_start,
                "line_end": symbol.line_end,
                "score": round(-neg_score, 4),
                "snippet": symbol.signature(),
            }
            items.append(
                Item(
                    field="hits",
                    kind="hit",
                    data=hit,
                    text=(
                        f"{symbol.path}:{symbol.line_start} {symbol.kind} {symbol.qualified} "
                        f"score={hit['score']}\n  {symbol.signature()}"
                    ),
                )
            )
        return Payload(
            header={"hits": []},
            header_text=f"ast {query!r}: {len(scored)} symbol(s) matched",
            items=items,
            list_fields=("hits",),
        )

    def op_get_symbol(self, args: dict[str, Any]) -> Payload:
        name = self.arg_str(args, "name")
        symbols = self._lookup(name)
        if not symbols:
            raise not_found(f"no symbol named '{name}' in the AST index")
        items = [
            Item(
                field="symbols",
                kind="symbol",
                data=record,
                text=self._symbol_text(record),
            )
            for record in (self._symbol_record(symbol) for symbol in symbols)
        ]
        return Payload(
            header={"symbols": []},
            header_text=f"get_symbol {name}: {len(items)} symbol(s)",
            items=items,
            list_fields=("symbols",),
        )

    def op_get_context(self, args: dict[str, Any]) -> Payload:
        name = self.arg_str(args, "symbol")
        symbols = self._lookup(name)
        if not symbols:
            raise not_found(f"no symbol named '{name}' in the AST index")
        symbol = symbols[0]
        header = {
            "symbol": symbol.qualified,
            "path": symbol.path,
            "line_start": symbol.line_start,
            "line_end": symbol.line_end,
            "signature": symbol.signature(),
            "return_type": symbol.return_type,
            "kind": symbol.kind,
            "scope": symbol.scope,
            "decorators": list(symbol.decorators),
            "params": [
                {"name": param.name, "annotation": param.annotation, "default": param.default}
                for param in symbol.params
            ],
            "imports": [],
        }
        header_text = (
            f"{symbol.qualified} [{symbol.kind}] {symbol.path}:"
            f"{symbol.line_start}-{symbol.line_end}\n  {symbol.signature()}"
        )
        if symbol.decorators:
            header_text += "\n  @" + " @".join(symbol.decorators)

        ast = self.asts.get(symbol.path)
        items: list[Item] = []
        if ast is not None:
            local = set(self.local_imports.get(symbol.path, ()))
            seen: set[str] = set()
            rendered: list[tuple[int, str, str]] = []
            for record in ast.imports:
                statement = record.render()
                if statement in seen:
                    continue
                seen.add(statement)
                target = self._resolve_module(record, symbol.path)
                origin = target if target in local else "external"
                rendered.append((0 if target in local else 1, statement, origin))
            rendered.sort()
            for _, text, origin in rendered:
                items.append(
                    Item(
                        field="imports",
                        kind="import",
                        data={"statement": text, "origin": origin},
                        text=f"  import: {text}"
                        + (f"  -> {origin}" if origin != "external" else ""),
                    )
                )
        return Payload(
            header=header, header_text=header_text, items=items, list_fields=("imports",)
        )

    def op_get_dependencies(self, args: dict[str, Any]) -> Payload:
        path = self.arg_str(args, "path")
        if path not in self.asts:
            raise not_found(f"'{path}' is not an indexed Python file")
        header = {
            "path": path,
            "imports_local": [],
            "imports_external": [],
            "imported_by": [],
        }
        items: list[Item] = []
        for target in self.local_imports.get(path, ()):
            items.append(
                Item(
                    field="imports_local",
                    kind="import_local",
                    data=target,
                    text=f"  imports_local: {target}",
                )
            )
        for module in self.external_imports.get(path, ()):
            items.append(
                Item(
                    field="imports_external",
                    kind="import_external",
                    data=module,
                    text=f"  imports_external: {module}",
                )
            )
        for source in self.imported_by.get(path, ()):
            items.append(
                Item(
                    field="imported_by",
                    kind="imported_by",
                    data=source,
                    text=f"  imported_by: {source}",
                )
            )
        return Payload(
            header=header,
            header_text=f"dependencies {path}",
            items=items,
            list_fields=("imports_local", "imports_external", "imported_by"),
        )

    def op_get_references(self, args: dict[str, Any]) -> Payload:
        name = self.arg_str(args, "symbol")
        limit = self.arg_int(args, "limit", 50)
        _, leaf = dotted_parts(name)
        definition_lines = {
            (symbol.path, symbol.line_start) for symbol in self.by_name.get(leaf, ())
        }
        references: list[dict[str, Any]] = []
        for path in sorted(self.asts):
            for occurrence_name, line in self.asts[path].occurrences:
                if occurrence_name != leaf:
                    continue
                is_definition = (path, line) in definition_lines
                references.append(
                    {
                        "path": path,
                        "symbol": None,
                        "line": line,
                        "kind": "DEFINES" if is_definition else "MENTIONS",
                        "confidence": 0.9 if is_definition else 0.5,
                    }
                )
        if not references:
            raise not_found(f"no identifier '{leaf}' occurs in the AST index")
        references.sort(
            key=lambda ref: (-float(ref["confidence"]), str(ref["path"]), int(ref["line"]))
        )
        items = [
            Item(
                field="references",
                kind="reference",
                data=reference,
                text=f"{reference['path']}:{reference['line']} {reference['kind']}",
            )
            for reference in references[:limit]
        ]
        return Payload(
            header={"references": []},
            header_text=f"get_references {name}: {len(references)} identifier occurrence(s)",
            items=items,
            list_fields=("references",),
        )
