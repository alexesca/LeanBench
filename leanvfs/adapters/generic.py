"""Text / shell fallback: rung three of the ladder."""

from __future__ import annotations

import re

from ..model import (
    FileExtraction,
    KeywordCandidate,
    SourceRange,
    Symbol,
    make_stable_key,
)
from ..registry import FactKindError
from .base import ExtractionContext
from .tokens import structure_hash_generic

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_MAX_DECL_LINES = 20000
_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _compiled(pattern: str) -> re.Pattern[str] | None:
    """Compile once and reuse. A bad pattern disables itself rather than killing the
    index -- these come from user-editable config, so one typo must not stop indexing."""
    if pattern not in _PATTERN_CACHE:
        try:
            _PATTERN_CACHE[pattern] = re.compile(pattern)
        except re.error:
            _PATTERN_CACHE[pattern] = None  # type: ignore[assignment]
    return _PATTERN_CACHE[pattern]


_DECL_PREFIX = "languages.declarations."


def declaration_patterns(language: str, policy: dict) -> list[tuple[str, str]]:
    """Resolve a language to its declaration patterns, following one alias hop.

    The injected policy is the *flattened* config, so the patterns arrive as dotted keys
    (`languages.declarations.go`) rather than a nested table. Reading it as if it were
    nested silently returned nothing for every language -- no error, just an index with
    no symbols in it.
    """
    entry = policy.get(_DECL_PREFIX + language)
    if isinstance(entry, str):          # e.g. javascript -> typescript
        entry = policy.get(_DECL_PREFIX + entry)
    if not isinstance(entry, list):
        return []
    return [
        (str(row[0]), str(row[1]))
        for row in entry
        if isinstance(row, (list, tuple)) and len(row) >= 2
    ]


def _block_end(lines: list[str], start_index: int) -> int:
    """Where the declaration's block ends, by indentation.

    Deliberately approximate. The agent uses the range to decide what to READ, so an
    end that is a few lines generous costs a little context; one that is short would hide
    the thing it was asked to find. Erring long is the safer direction.
    """
    opening = lines[start_index]
    indent = len(opening) - len(opening.lstrip())
    for offset in range(start_index + 1, min(len(lines), start_index + 400)):
        line = lines[offset]
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= indent and line.strip() not in ("}", "};", ")", "end"):
            return offset          # 0-based index of the line AFTER the block
    return min(len(lines), start_index + 400)
_SHELL_FN = re.compile(r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{")
_ENVVAR = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")

#: Keywords that regex declaration patterns pick up as if they were names.
_DECL_NOISE = frozenset({
    "if", "for", "while", "switch", "catch", "return", "else", "do", "try",
    "func", "function", "class", "struct", "enum", "interface", "trait", "impl",
    "new", "delete", "typedef", "public", "private", "protected", "static",
})


class GenericTextAdapter:
    language = "text"

    def extract(self, ctx: ExtractionContext) -> FileExtraction:
        ext = FileExtraction(file=ctx.file)
        ctx.file.structure_hash = structure_hash_generic(ctx.text)
        path = ctx.rel_path
        root_key = make_stable_key("text", path, "module", path)
        ext.symbols.append(
            Symbol(
                stable_key=root_key,
                name=path.rsplit("/", 1)[-1],
                qualified_name=path,
                kind="module",
                file_path=path,
                visibility="public",
                range=SourceRange(1, max(1, ctx.file.line_count), 0, len(ctx.source)),
                is_exported=True,
            )
        )
        # --- rung two: declaration symbols for languages with no rich adapter -------
        patterns = declaration_patterns(ctx.file.language, ctx.policy)
        if patterns:
            lines = ctx.text.splitlines()
            seen: set[str] = set()
            for index, line in enumerate(lines[:_MAX_DECL_LINES]):
                if not line.strip() or len(line) > 500:
                    continue
                for raw_pattern, kind in patterns:
                    compiled = _compiled(raw_pattern)
                    if compiled is None:
                        continue
                    match = compiled.match(line)
                    if match is None:
                        continue
                    name = match.group(1)
                    if not name or name in _DECL_NOISE:
                        continue
                    key = make_stable_key(ctx.file.language, path, kind, name)
                    if key in seen:
                        continue
                    seen.add(key)
                    end = _block_end(lines, index)
                    ext.symbols.append(
                        Symbol(
                            stable_key=key,
                            name=name,
                            qualified_name=name,
                            kind=kind,
                            file_path=path,
                            visibility="private" if name.startswith("_") else "public",
                            signature=line.strip()[:200],
                            parent_key=root_key,
                            range=SourceRange(index + 1, max(index + 1, end), 0, 0),
                            is_exported="export" in line or name[:1].isupper(),
                        )
                    )
                    for term in _WORD.findall(name):
                        ext.keyword_candidates.append(
                            KeywordCandidate(
                                term=term, source="symbol_name",
                                symbol_key=key, file_path=path,
                            )
                        )
                    break     # first matching pattern wins; order in config is the rule

        reg = ctx.registry
        envs: set[str] = set()
        shell_like = ctx.file.language in ("shell", "text", "unknown")
        for idx, line in enumerate(ctx.text.splitlines()[:2000], start=1):
            # Only where nothing better applies. `name() {` also matches a TypeScript
            # method, and firing both paths emitted the same symbol twice -- duplicate
            # hits spend the agent's tokens twice for one answer.
            m = _SHELL_FN.match(line) if shell_like else None
            if m:
                name = m.group(1)
                key = make_stable_key("text", path, "function", name, [])
                ext.symbols.append(
                    Symbol(
                        stable_key=key,
                        name=name,
                        qualified_name=name,
                        kind="function",
                        file_path=path,
                        visibility="public",
                        signature=f"{name}()",
                        parent_key=root_key,
                        range=SourceRange(idx, idx, 0, 0),
                        is_exported=True,
                    )
                )
            envs.update(_ENVVAR.findall(line))
            for w in _WORD.findall(line.lower())[:8]:
                ext.keyword_candidates.append(KeywordCandidate(w, "literal", root_key, path))
        for env in sorted(envs)[:20]:
            try:
                ext.facts.append(
                    reg.make(
                        "resource",
                        f"env={env}",
                        file_path=path,
                        symbol_key=root_key,
                        provenance="heuristic",
                        confidence=0.5,
                    )
                )
            except FactKindError as exc:
                ext.diagnostics.append(f"fact-rejected:resource:{exc}")
        for sym in ext.symbols:
            sym.interface_hash = _h(["iface", sym.qualified_name])
            sym.behavior_hash = _h(["body", ctx.file.structure_hash, sym.qualified_name])
            sym.doc_hash = _h(["doc", ""])
            sym.metadata_hash = _h(["meta", ctx.file.file_class])
        ext.canonicalize()
        return ext


def _h(parts: list[str]) -> str:
    from ..hashing import digest_parts

    return digest_parts(parts)
