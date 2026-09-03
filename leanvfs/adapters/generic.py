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
_SHELL_FN = re.compile(r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{")
_ENVVAR = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")


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
        reg = ctx.registry
        envs: set[str] = set()
        for idx, line in enumerate(ctx.text.splitlines()[:2000], start=1):
            m = _SHELL_FN.match(line)
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
