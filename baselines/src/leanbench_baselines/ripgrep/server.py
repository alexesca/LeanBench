"""Ripgrep baseline -- the honest competitor.

Capability ceiling (deliberate): **text only, and no index**. Every answer comes from one
or two live ``rg`` invocations plus reading the matched lines. There is no symbol table,
no parser, no persisted state. Within that ceiling this rung is built to be *strong*:

* multi-pattern queries with stemming, word boundaries and code-shaped spellings
  (``connection pooling`` also searches ``connection_pool`` / ``ConnectionPool``);
* ranking by term coverage, match proximity, density and file class;
* snippets tight around the best match window rather than whole files;
* nearest-preceding ``def``/``class`` lookback so hits carry a symbol name.

Not implemented, and not declared: dependencies, incremental.
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
    identifier_variants,
    indent_of,
    parse_def_line,
    query_symbol_candidates,
    query_terms,
    read_signature,
    return_annotation,
    stems,
)
from leanbench_baselines.ripgrep.rgrunner import RgMatch, rg_version, run_rg

WINDOW = 4  # proximity window, in lines
SNIPPET_RADIUS = 2
MAX_SNIPPET_LINES = 7
CONTEXT_BODY_LINES = 8

DEF_PATTERN = r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+[A-Za-z_]"
DOC_GLOBS = ("docs/**", "*.md", "**/*.md", "README*")
TEST_GLOBS = ("tests/**", "test/**", "**/test_*.py", "**/*_test.py")

_RAISE_RE = re.compile(r"\braise\s+([A-Za-z_][A-Za-z0-9_.]*)")
_IMPORT_RE = re.compile(r"^\s*(?:from\s+\S+\s+)?import\s")


def _file_weight(repo: Repository, path: str) -> float:
    record = repo.record(path)
    if record is None:
        return 0.4
    if record.is_test:
        return 0.6
    if record.is_doc:
        return 0.75
    if record.is_source:
        return 1.0
    return 0.4


class RipgrepServer(BaseServer):
    NAME = "Ripgrep"
    VERSION = "0.1.0"
    CAPABILITIES = frozenset({"search", "symbols", "context", "references", "tests", "docs"})

    def __init__(self) -> None:
        super().__init__()
        self.rg_version = ""

    def build_index(self, repo: Repository) -> dict[str, Any]:
        # No index: rg re-reads the tree on every query. We only note the binary version.
        self.rg_version = rg_version()
        return {
            "indexed": False,
            "symbols": 0,
            "index_bytes": 0,
            "files": len(repo.files),
            "rg_version": self.rg_version,
        }

    def stats(self) -> dict[str, Any]:
        return {
            "symbols": 0,
            "facts": 0,
            "relationships": 0,
            "index_bytes": 0,
            "rg_version": self.rg_version,
        }

    # -- shared helpers --------------------------------------------------------

    def _visible(self, matches: list[RgMatch]) -> list[RgMatch]:
        """Drop anything the repository walker considers ignored, and sort."""
        repo = self.require_repo()
        kept = [match for match in matches if repo.has(match.path)]
        kept.sort(key=lambda m: (m.path, m.line))
        return kept

    def _rg(self, patterns: list[str], **kwargs: Any) -> list[RgMatch]:
        repo = self.require_repo()
        self.bump("rg_invocations")
        return self._visible(run_rg(repo.root, patterns, **kwargs))

    def _nearest_symbol(self, path: str, line: int) -> tuple[str | None, int | None]:
        """Nearest preceding ``def``/``class`` line, with one extra class lookback."""
        repo = self.require_repo()
        lines = repo.lines(path)
        for number in range(min(line, len(lines)), 0, -1):
            parsed = parse_def_line(lines[number - 1])
            if parsed is None:
                continue
            kind, name, indent = parsed
            if kind == "class" or indent == 0:
                return name, number
            for outer in range(number - 1, 0, -1):
                outer_parsed = parse_def_line(lines[outer - 1])
                if outer_parsed is None:
                    continue
                outer_kind, outer_name, outer_indent = outer_parsed
                if outer_indent < indent and outer_kind == "class":
                    return f"{outer_name}.{name}", number
                if outer_indent < indent:
                    break
            return name, number
        return None, None

    def _find_definitions(self, name: str) -> list[RgMatch]:
        pattern = r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+" + re.escape(name) + r"\b"
        return self._rg([pattern], ignore_case=False)

    def _signature_end(self, path: str, def_line: int) -> int:
        """Last line of a (possibly multi-line) definition header."""
        repo = self.require_repo()
        lines = repo.lines(path)
        depth = 0
        for number in range(def_line, min(len(lines), def_line + 40) + 1):
            row = lines[number - 1]
            depth += row.count("(") - row.count(")")
            if depth <= 0 and row.rstrip().endswith(":"):
                return number
        return def_line

    def _body_start(self, path: str, def_line: int) -> int:
        """First statement line after the header and any docstring."""
        repo = self.require_repo()
        lines = repo.lines(path)
        number = self._signature_end(path, def_line) + 1
        while number <= len(lines) and not lines[number - 1].strip():
            number += 1
        if number <= len(lines):
            stripped = lines[number - 1].strip()
            for quote in ('"""', "'''"):
                if stripped.startswith(quote):
                    if stripped.endswith(quote) and len(stripped) > 2 * len(quote) - 1:
                        return number + 1
                    for closing in range(number + 1, len(lines) + 1):
                        if quote in lines[closing - 1]:
                            return closing + 1
                    return number + 1
        return number

    def _docstring(self, path: str, def_line: int) -> list[str]:
        """First lines of the docstring following a definition, textually."""
        repo = self.require_repo()
        lines = repo.lines(path)
        signature_end = self._signature_end(path, def_line)
        out: list[str] = []
        for number in range(signature_end + 1, min(len(lines), signature_end + 6) + 1):
            row = lines[number - 1].strip()
            if not row:
                continue
            if not out and not (row.startswith('"""') or row.startswith("'''")):
                break
            cleaned = row.strip('"').strip("'").strip()
            if cleaned:
                out.append(clean_line(cleaned, limit=160))
            if len(out) >= 2 or (len(out) == 1 and row.endswith(('"""', "'''")) and len(row) > 6):
                break
        return out

    # -- search ----------------------------------------------------------------

    def _query_patterns(self, query: str) -> tuple[list[str], list[str], dict[str, list[str]]]:
        terms = query_terms(query) or [query.strip().lower()]
        keys = stems(terms)
        variants = identifier_variants(terms)
        attribution: dict[str, list[str]] = {}
        patterns: list[str] = []
        # No leading \b: `redirect` must also match inside `_send_handling_redirects`
        # and `TooManyRedirects`, which snake/camel case would otherwise hide.
        for key in keys:
            patterns.append(re.escape(key) + r"\w*")
            attribution[key] = [key]
        for variant in variants:
            lowered = variant.lower()
            patterns.append(re.escape(lowered) + r"\w*")
            attribution[lowered] = [
                stem for stem in keys if stem in lowered.replace("_", " ").split()
            ] or [keys[0]]
        for candidate in query_symbol_candidates(query):
            _, leaf = dotted_parts(candidate)
            lowered = leaf.lower()
            if lowered not in attribution:
                patterns.append(r"\b" + re.escape(lowered) + r"\b")
                attribution[lowered] = [lowered]
                keys.append(lowered)
        # longest first so `connection_pool` is attributed before `connection`
        return patterns, keys, attribution

    @staticmethod
    def _attribute(text: str, attribution: dict[str, list[str]]) -> list[str]:
        lowered = text.lower()
        best: list[str] = []
        best_len = -1
        for key, covered in attribution.items():
            if lowered.startswith(key) and len(key) > best_len:
                best = covered
                best_len = len(key)
        return best

    def op_search(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        query = self.arg_str(args, "query")
        limit = self.arg_int(args, "limit", 10)
        patterns, keys, attribution = self._query_patterns(query)
        matches = self._rg(patterns)

        per_file: dict[str, list[tuple[int, str, frozenset[str]]]] = {}
        for match in matches:
            covered: set[str] = set()
            for text in match.matched:
                covered.update(self._attribute(text, attribution))
            if not covered:
                continue
            per_file.setdefault(match.path, []).append(
                (match.line, match.text, frozenset(covered))
            )

        scored: list[
            tuple[tuple[float, str], str, list[tuple[int, str, frozenset[str]]], int]
        ] = []
        for path in sorted(per_file):
            rows = sorted(per_file[path])
            covered_all: set[str] = set()
            for _, _, covered in rows:
                covered_all |= covered
            coverage = len(covered_all) / len(keys)
            proximity = 0.0
            best_center = rows[0][0]
            for line, _, _ in rows:
                near: set[str] = set()
                for other_line, _, covered in rows:
                    if abs(other_line - line) <= WINDOW:
                        near |= covered
                ratio = len(near) / len(keys)
                if ratio > proximity:
                    proximity = ratio
                    best_center = line
            density = min(1.0, len(rows) / 20.0)
            def_bonus = 0.05 if any(parse_def_line(text) for _, text, _ in rows) else 0.0
            score = round(
                0.55 * coverage
                + 0.20 * proximity
                + 0.10 * density
                + 0.10 * _file_weight(repo, path)
                + def_bonus,
                4,
            )
            scored.append(((-score, path), path, rows, best_center))

        scored.sort(key=lambda entry: entry[0])
        selected = scored[:limit]

        items: list[Item] = []
        for (neg_score, path), _, rows, center in selected:
            symbol, def_line = self._nearest_symbol(path, center)
            start = max(1, center - SNIPPET_RADIUS)
            end = center + SNIPPET_RADIUS
            matched_lines = {line for line, _, _ in rows}
            snippet_rows: list[str] = []
            for number in range(start, end + 1):
                text = repo.line(path, number)
                if not text and number > len(repo.lines(path)):
                    break
                marker = ">" if number in matched_lines else " "
                snippet_rows.append(f"{marker}{number:5d}| {clean_line(text, limit=180)}")
            extra = [line for line in sorted(matched_lines) if not start <= line <= end]
            for number in extra[: MAX_SNIPPET_LINES - len(snippet_rows)]:
                snippet_rows.append(
                    f">{number:5d}| {clean_line(repo.line(path, number), limit=180)}"
                )
            hit = {
                "path": path,
                "symbol": symbol,
                "kind": "symbol" if symbol else "file",
                "line_start": def_line or start,
                "line_end": end,
                "score": round(-neg_score, 4),
                "snippet": "\n".join(snippet_rows),
            }
            label = f" {symbol}" if symbol else ""
            items.append(
                Item(
                    field="hits",
                    kind="hit",
                    data=hit,
                    text=(
                        f"{path}:{hit['line_start']}{label} score={hit['score']}\n"
                        + hit["snippet"]
                    ),
                )
            )

        return Payload(
            header={"hits": []},
            header_text=f"rg {query!r}: {len(scored)} files matched",
            items=items,
            list_fields=("hits",),
        )

    # -- symbols ---------------------------------------------------------------

    def _symbol_record(self, path: str, line: int, requested: str) -> dict[str, Any]:
        repo = self.require_repo()
        lines = repo.lines(path)
        parsed = parse_def_line(lines[line - 1])
        kind = "function"
        name = requested
        indent = 0
        if parsed is not None:
            kind, name, indent = parsed
        qualified, _ = self._nearest_symbol(path, line)
        if kind == "class":
            qualified = name
            symbol_kind = "class"
        else:
            symbol_kind = "method" if indent > 0 else "function"
        signature = read_signature(lines, line)
        doc = self._docstring(path, line)
        return {
            "path": path,
            "symbol": qualified or name,
            "kind": symbol_kind,
            "signature": clean_line(signature, limit=300),
            "return_type": return_annotation(signature),
            "line_start": line,
            "line_end": block_end(lines, line),
            "visibility": "private" if name.startswith("_") else "public",
            "doc": doc[0] if doc else None,
        }

    def op_get_symbol(self, args: dict[str, Any]) -> Payload:
        name = self.arg_str(args, "name")
        scope, leaf = dotted_parts(name)
        definitions = self._find_definitions(leaf)
        records = [self._symbol_record(m.path, m.line, leaf) for m in definitions]
        if scope:
            wanted = scope.rsplit(".", 1)[-1]
            records = [r for r in records if r["symbol"] == f"{wanted}.{leaf}"] or records
        if not records:
            raise not_found(f"no definition of '{name}' matched by rg")
        records.sort(key=lambda r: (r["path"], r["line_start"]))
        items = [
            Item(
                field="symbols",
                kind="symbol",
                data=record,
                text=(
                    f"{record['path']}:{record['line_start']}-{record['line_end']} "
                    f"{record['kind']} {record['symbol']}\n  {record['signature']}"
                    + (f"\n  doc: {record['doc']}" if record["doc"] else "")
                ),
            )
            for record in records
        ]
        return Payload(
            header={"symbols": []},
            header_text=f"get_symbol {name}: {len(items)} definition(s)",
            items=items,
            list_fields=("symbols",),
        )

    # -- context ---------------------------------------------------------------

    def op_get_context(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        name = self.arg_str(args, "symbol")
        scope, leaf = dotted_parts(name)
        definitions = self._find_definitions(leaf)
        records = [self._symbol_record(m.path, m.line, leaf) for m in definitions]
        if scope:
            wanted = scope.rsplit(".", 1)[-1]
            records = [r for r in records if r["symbol"] == f"{wanted}.{leaf}"] or records
        if not records:
            raise not_found(f"no definition of '{name}' matched by rg")
        records.sort(key=lambda r: (r["path"], r["line_start"]))
        record = records[0]
        path = str(record["path"])
        start = int(record["line_start"])
        end = int(record["line_end"])

        header = {
            "symbol": record["symbol"],
            "path": path,
            "line_start": start,
            "line_end": end,
            "signature": record["signature"],
            "return_type": record["return_type"],
            "raises": [],
            "doc": [],
            "source": [],
        }
        header_text = (
            f"{record['symbol']} -- {path}:{start}-{end}\n"
            f"  sig: {record['signature']}"
            + (f"\n  returns: {record['return_type']}" if record["return_type"] else "")
        )

        items: list[Item] = []
        for line in self._docstring(path, start):
            items.append(Item(field="doc", kind="doc", data=line, text=f"  doc: {line}"))

        raised: list[str] = []
        for number in range(start, end + 1):
            for match in _RAISE_RE.finditer(repo.line(path, number)):
                exception = match.group(1)
                # `raise exc` re-raises a local; only class-looking names are informative.
                if not exception.rsplit(".", 1)[-1][:1].isupper():
                    continue
                if exception not in raised:
                    raised.append(exception)
        for exception in raised:
            items.append(
                Item(field="raises", kind="raises", data=exception, text=f"  raises: {exception}")
            )

        body_start = self._body_start(path, start)
        shown = 0
        for number in range(body_start, end + 1):
            if shown >= CONTEXT_BODY_LINES:
                break
            text = repo.line(path, number)
            if not text.strip():
                continue
            shown += 1
            items.append(
                Item(
                    field="source",
                    kind="source_line",
                    data=f"{number}| {text}",
                    text=f"  {number:5d}| {clean_line(text, limit=180)}",
                )
            )
        return Payload(
            header=header, header_text=header_text, items=items, list_fields=("raises", "doc")
        )

    # -- references / tests / docs ---------------------------------------------

    def op_get_references(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        name = self.arg_str(args, "symbol")
        _, leaf = dotted_parts(name)
        limit = self.arg_int(args, "limit", 50)
        matches = self._rg([re.escape(leaf)], ignore_case=False, word=True)
        references: list[dict[str, Any]] = []
        for match in matches:
            parsed = parse_def_line(match.text)
            if parsed is not None and parsed[1] == leaf:
                kind, confidence = "DEFINES", 0.9
            elif _IMPORT_RE.match(match.text):
                kind, confidence = "IMPORTS", 0.7
            else:
                kind, confidence = "MENTIONS", 0.5
            symbol, _ = self._nearest_symbol(match.path, match.line)
            references.append(
                {
                    "path": match.path,
                    "symbol": symbol,
                    "line": match.line,
                    "kind": kind,
                    "confidence": confidence,
                    "text": clean_line(match.text, limit=160),
                }
            )
        references.sort(key=lambda r: (-float(r["confidence"]), str(r["path"]), int(r["line"])))
        items = [
            Item(
                field="references",
                kind="reference",
                data=reference,
                text=(
                    f"{reference['path']}:{reference['line']} {reference['kind']} "
                    f"{reference['symbol'] or '-'}: {reference['text']}"
                ),
            )
            for reference in references[:limit]
        ]
        _ = repo
        return Payload(
            header={"references": []},
            header_text=f"get_references {name}: {len(references)} word-boundary hit(s)",
            items=items,
            list_fields=("references",),
        )

    def op_get_tests(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        name = self.arg_str(args, "symbol")
        _, leaf = dotted_parts(name)
        limit = self.arg_int(args, "limit", 20)
        matches = self._rg([re.escape(leaf)], ignore_case=False, word=True, globs=TEST_GLOBS)
        tests: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for match in matches:
            record = repo.record(match.path)
            if record is None or not record.is_test:
                continue
            test_symbol, def_line = self._nearest_symbol(match.path, match.line)
            key = (match.path, def_line or match.line)
            if key in seen:
                continue
            seen.add(key)
            tests.append(
                {
                    "path": match.path,
                    "symbol": test_symbol,
                    "line_start": def_line or match.line,
                    "scenario": clean_line(match.text, limit=160),
                    "expects": None,
                }
            )
        tests.sort(key=lambda t: (str(t["path"]), int(t["line_start"])))
        items = [
            Item(
                field="tests",
                kind="test",
                data=test,
                text=(
                    f"{test['path']}:{test['line_start']} {test['symbol'] or '-'}\n"
                    f"    {test['scenario']}"
                ),
            )
            for test in tests[:limit]
        ]
        return Payload(
            header={"tests": []},
            header_text=f"get_tests {name}: {len(tests)} test(s) mentioning the symbol",
            items=items,
            list_fields=("tests",),
        )

    def op_get_docs(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        query = self.arg_str(args, "query")
        limit = self.arg_int(args, "limit", 5)
        patterns, keys, attribution = self._query_patterns(query)
        matches = self._rg(patterns, globs=DOC_GLOBS)
        per_file: dict[str, list[tuple[int, str, frozenset[str]]]] = {}
        for match in matches:
            record = repo.record(match.path)
            if record is None or not record.is_doc:
                continue
            covered: set[str] = set()
            for text in match.matched:
                covered.update(self._attribute(text, attribution))
            if not covered:
                continue
            per_file.setdefault(match.path, []).append(
                (match.line, match.text, frozenset(covered))
            )

        docs: list[tuple[tuple[float, str, int], dict[str, Any]]] = []
        for path in sorted(per_file):
            rows = sorted(per_file[path])
            covered_all: set[str] = set()
            for _, _, covered in rows:
                covered_all |= covered
            score = round(
                0.7 * (len(covered_all) / len(keys)) + 0.3 * min(1.0, len(rows) / 8.0), 4
            )
            line = rows[0][0]
            heading = self._heading(path, line)
            excerpt = "\n".join(
                clean_line(text, limit=180)
                for text in repo.snippet(path, line - 1, line + 1)
                if text.strip()
            )
            docs.append(
                (
                    (-score, path, line),
                    {
                        "path": path,
                        "heading": heading,
                        "line_start": line,
                        "excerpt": excerpt,
                        "score": score,
                    },
                )
            )
        docs.sort(key=lambda entry: entry[0])
        items = [
            Item(
                field="docs",
                kind="doc",
                data=doc,
                text=(
                    f"{doc['path']}:{doc['line_start']} "
                    f"[{doc['heading'] or '-'}] score={doc['score']}\n{doc['excerpt']}"
                ),
            )
            for _, doc in docs[:limit]
        ]
        return Payload(
            header={"docs": []},
            header_text=f"get_docs {query!r}: {len(docs)} document(s) matched",
            items=items,
            list_fields=("docs",),
        )

    def _heading(self, path: str, line: int) -> str | None:
        repo = self.require_repo()
        lines = repo.lines(path)
        for number in range(min(line, len(lines)), 0, -1):
            text = lines[number - 1]
            if text.startswith("#"):
                return clean_line(text.lstrip("#").strip(), limit=120)
            if indent_of(text) == 0 and text.strip() and set(text.strip()) <= {"=", "-"}:
                previous = lines[number - 2] if number >= 2 else ""
                if previous.strip():
                    return clean_line(previous.strip(), limit=120)
        return None
