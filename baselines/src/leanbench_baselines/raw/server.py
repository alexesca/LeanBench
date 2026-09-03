"""RawRepository -- the bottom rung.

Capability ceiling (deliberate): **no index of any kind**. Every op is a fresh linear
scan over file contents, exactly like a human with ``ls``, hand-rolled ``grep`` and
``cat``. Matching is naive case-insensitive substring matching, so ``send`` also matches
``sender`` -- that noise is a property of the rung, not a bug. ``get_context`` returns a
raw chunk of source around the definition, which is why this rung is token-expensive.

Not implemented, and not declared: dependencies, tests, docs, incremental.
"""

from __future__ import annotations

import re
from typing import Any

from leanbench_baselines.common.payload import Item, Payload
from leanbench_baselines.common.repo import Repository
from leanbench_baselines.common.server import BaseServer, not_found
from leanbench_baselines.common.text import (
    block_end,
    clean_line,
    dotted_parts,
    indent_of,
    query_terms,
    read_signature,
)

#: How many lines of context a raw read shows around a match.
SNIPPET_RADIUS = 3
#: How many distinct match regions a single file hit shows.
MAX_REGIONS_PER_FILE = 3
#: Default cap on the raw chunk returned by get_context when no budget is given.
DEFAULT_CONTEXT_LINES = 80

_DEF_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _def_regex(name: str) -> re.Pattern[str]:
    cached = _DEF_RE_CACHE.get(name)
    if cached is None:
        cached = re.compile(
            r"^\s*(?:async\s+)?(?:def|class)\s+" + re.escape(name) + r"\s*[\(:\[]"
        )
        _DEF_RE_CACHE[name] = cached
    return cached


class RawRepositoryServer(BaseServer):
    NAME = "RawRepository"
    VERSION = "0.1.0"
    CAPABILITIES = frozenset({"search", "symbols", "context", "references"})

    def build_index(self, repo: Repository) -> dict[str, Any]:
        # No index. Prepare only enumerates files, which is what `ls -R` costs.
        return {"indexed": False, "symbols": 0, "index_bytes": 0, "files": len(repo.files)}

    def stats(self) -> dict[str, Any]:
        return {"symbols": 0, "facts": 0, "relationships": 0, "index_bytes": 0}

    # -- scanning primitives ---------------------------------------------------

    def _scan_files(self) -> list[str]:
        repo = self.require_repo()
        return [record.path for record in repo.files if record.size <= 2_000_000]

    def _find_definitions(self, name: str) -> list[tuple[str, int]]:
        """Every ``def name`` / ``class name`` line, sorted by (path, line)."""
        repo = self.require_repo()
        pattern = _def_regex(name)
        found: list[tuple[str, int]] = []
        for path in self._scan_files():
            if not path.endswith((".py", ".pyi")):
                continue
            for index, line in enumerate(repo.lines(path), start=1):
                if pattern.match(line):
                    found.append((path, index))
        found.sort()
        return found

    def _enclosing_class(self, path: str, line_number: int) -> str | None:
        repo = self.require_repo()
        lines = repo.lines(path)
        if not (1 <= line_number <= len(lines)):
            return None
        target_indent = indent_of(lines[line_number - 1])
        for number in range(line_number - 1, 0, -1):
            row = lines[number - 1]
            if not row.strip():
                continue
            if indent_of(row) < target_indent:
                match = re.match(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", row)
                if match:
                    return match.group(1)
                target_indent = indent_of(row)
        return None

    def _signature(self, path: str, line_number: int) -> str:
        """The definition line, extended by continuation lines until parens balance."""
        repo = self.require_repo()
        return clean_line(read_signature(repo.lines(path), line_number), limit=300)

    # -- ops -------------------------------------------------------------------

    def op_search(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        query = self.arg_str(args, "query")
        limit = self.arg_int(args, "limit", 10)
        terms = query_terms(query) or [query.strip().lower()]
        phrase = query.strip().lower()

        hits: list[tuple[tuple[float, int, str], dict[str, Any]]] = []
        for path in self._scan_files():
            text = repo.read_text(path)
            if text is None:
                continue
            lowered = text.lower()
            matched_terms = [term for term in terms if term in lowered]
            phrase_hit = phrase in lowered and len(terms) > 1
            if not matched_terms and not phrase_hit:
                continue
            lines = repo.lines(path)
            match_lines: list[int] = []
            occurrences = 0
            for number, line in enumerate(lines, start=1):
                low = line.lower()
                if phrase_hit and phrase in low:
                    match_lines.append(number)
                    occurrences += 1
                    continue
                if any(term in low for term in matched_terms):
                    match_lines.append(number)
                    occurrences += low.count(matched_terms[0])
            if not match_lines:
                continue
            coverage = len(matched_terms) / len(terms)
            density = min(1.0, occurrences / 12.0)
            score = round(0.7 * coverage + 0.2 * density + (0.1 if phrase_hit else 0.0), 4)
            regions = self._regions(match_lines)
            snippet_lines: list[str] = []
            for start, end in regions[:MAX_REGIONS_PER_FILE]:
                for number in range(start, end + 1):
                    snippet_lines.append(f"{number:5d}| {clean_line(repo.line(path, number))}")
            first = regions[0]
            hit = {
                "path": path,
                "symbol": None,
                "kind": "file",
                "line_start": first[0],
                "line_end": regions[min(len(regions), MAX_REGIONS_PER_FILE) - 1][1],
                "score": score,
                "snippet": "\n".join(snippet_lines),
            }
            hits.append(((-score, first[0], path), hit))

        hits.sort(key=lambda entry: entry[0])
        selected = [hit for _, hit in hits[:limit]]
        items = [
            Item(
                field="hits",
                kind="hit",
                data=hit,
                text=(
                    f"{hit['path']}:{hit['line_start']}-{hit['line_end']} "
                    f"score={hit['score']}\n{hit['snippet']}"
                ),
            )
            for hit in selected
        ]
        return Payload(
            header={"hits": []},
            header_text=f"search {query!r}: {len(hits)} files matched",
            items=items,
            list_fields=("hits",),
        )

    @staticmethod
    def _regions(match_lines: list[int]) -> list[tuple[int, int]]:
        regions: list[tuple[int, int]] = []
        for number in match_lines:
            start = max(1, number - SNIPPET_RADIUS)
            end = number + SNIPPET_RADIUS
            if regions and start <= regions[-1][1] + 1:
                regions[-1] = (regions[-1][0], max(regions[-1][1], end))
            else:
                regions.append((start, end))
        return regions

    def op_get_symbol(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        name = self.arg_str(args, "name")
        scope, leaf = dotted_parts(name)
        definitions = self._find_definitions(leaf)
        if scope:
            wanted = scope.rsplit(".", 1)[-1]
            definitions = [
                (path, line)
                for path, line in definitions
                if self._enclosing_class(path, line) == wanted
            ]
        if not definitions:
            raise not_found(f"no textual definition of '{name}' found")

        items: list[Item] = []
        for path, line in definitions:
            lines = repo.lines(path)
            header_line = lines[line - 1].strip()
            is_class = header_line.startswith("class ")
            enclosing = self._enclosing_class(path, line)
            qualified = f"{enclosing}.{leaf}" if enclosing and not is_class else leaf
            kind = "class" if is_class else ("method" if enclosing else "function")
            record = {
                "path": path,
                "symbol": qualified,
                "kind": kind,
                "signature": self._signature(path, line),
                "return_type": None,
                "line_start": line,
                "line_end": block_end(lines, line),
                "visibility": "private" if leaf.startswith("_") else "public",
                "doc": None,
            }
            items.append(
                Item(
                    field="symbols",
                    kind="symbol",
                    data=record,
                    text=(
                        f"{record['path']}:{record['line_start']}-{record['line_end']} "
                        f"{record['kind']} {record['symbol']}\n  {record['signature']}"
                    ),
                )
            )
        return Payload(
            header={"symbols": []},
            header_text=f"get_symbol {name}: {len(items)} textual definition(s)",
            items=items,
            list_fields=("symbols",),
        )

    def op_get_context(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        name = self.arg_str(args, "symbol")
        scope, leaf = dotted_parts(name)
        definitions = self._find_definitions(leaf)
        if scope:
            wanted = scope.rsplit(".", 1)[-1]
            filtered = [
                (path, line)
                for path, line in definitions
                if self._enclosing_class(path, line) == wanted
            ]
            definitions = filtered or definitions
        if not definitions:
            raise not_found(f"no textual definition of '{name}' found")
        path, line = definitions[0]
        lines = repo.lines(path)
        end = block_end(lines, line)
        capped_end = min(end, line + DEFAULT_CONTEXT_LINES - 1)
        header = {
            "symbol": name,
            "path": path,
            "line_start": line,
            "line_end": end,
            "signature": self._signature(path, line),
            "source": [],
        }
        items = [
            Item(
                field="source",
                kind="source_line",
                data=f"{number}| {repo.line(path, number)}",
                text=f"{number:5d}| {clean_line(repo.line(path, number), limit=400)}",
            )
            for number in range(line, capped_end + 1)
        ]
        return Payload(
            header=header,
            header_text=f"{name} -- {path}:{line}-{end} (raw source)",
            items=items,
            list_fields=("source",),
        )

    def op_get_references(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        name = self.arg_str(args, "symbol")
        leaf = name.rsplit(".", 1)[-1].lower()
        limit = self.arg_int(args, "limit", 50)
        references: list[dict[str, Any]] = []
        total = 0
        for path in self._scan_files():
            text = repo.read_text(path)
            if text is None or leaf not in text.lower():
                continue
            for number, line in enumerate(repo.lines(path), start=1):
                if leaf not in line.lower():
                    continue
                total += 1
                if len(references) < limit:
                    references.append(
                        {
                            "path": path,
                            "symbol": None,
                            "line": number,
                            "kind": "MENTIONS",
                            "confidence": 0.3,
                            "text": clean_line(line, limit=160),
                        }
                    )
        items = [
            Item(
                field="references",
                kind="reference",
                data=reference,
                text=f"{reference['path']}:{reference['line']}: {reference['text']}",
            )
            for reference in references
        ]
        return Payload(
            header={"references": []},
            header_text=f"get_references {name}: {total} textual occurrence(s)",
            items=items,
            list_fields=("references",),
        )
