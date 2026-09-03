"""Markdown / documentation extractor.

Docs are compressed into structured facts. We never copy prose wholesale: an excerpt
is bounded by `docs.excerpt_chars` and headings become addressable `section` symbols.
"""

from __future__ import annotations

import re
from typing import Any

from ..model import (
    FileExtraction,
    KeywordCandidate,
    SourceRange,
    Symbol,
    UnresolvedRef,
    make_stable_key,
)
from .base import ExtractionContext
from .tokens import structure_hash_generic

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_LINK = re.compile(r"\[([^\]]{1,80})\]\(([^)]{1,200})\)")
_CODE_FENCE = re.compile(r"^```")
_INLINE_CODE = re.compile(r"`([^`\n]{2,80})`")
_SYMBOLISH = re.compile(r"^[A-Za-z_][\w.]*(\.[A-Za-z_]\w*)+$|^[A-Z][A-Za-z0-9]+$")
_FILEISH = re.compile(r"^[\w./-]+\.(py|md|toml|yml|yaml|json|cfg|ini|txt|rst)$")
_ENVVAR = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
_COMMANDISH = re.compile(r"^\s*(\$|>)\s*(\S.*)$")


class MarkdownAdapter:
    language = "markdown"

    def extract(self, ctx: ExtractionContext) -> FileExtraction:
        ext = FileExtraction(file=ctx.file)
        text = ctx.text
        ctx.file.structure_hash = structure_hash_generic(text)
        reg = ctx.registry
        path = ctx.rel_path
        max_headings = int(ctx.p("docs.max_headings", 40))
        max_concepts = int(ctx.p("docs.max_concepts", 20))
        max_links = int(ctx.p("docs.max_links", 20))
        excerpt_chars = int(ctx.p("docs.excerpt_chars", 200))
        elevated = _is_elevated(path, ctx)
        base_priority = 1 if elevated else 2

        lines = text.splitlines()
        doc_key = make_stable_key("markdown", path, "module", path)
        title = ""
        for line in lines:
            m = _HEADING.match(line)
            if m:
                title = m.group(2).strip()
                break
        module_sym = Symbol(
            stable_key=doc_key,
            name=path.rsplit("/", 1)[-1],
            qualified_name=title or path,
            kind="module",
            file_path=path,
            visibility="public",
            signature=title,
            range=SourceRange(1, max(1, len(lines)), 0, len(ctx.source)),
            doc=title,
            is_exported=True,
        )
        ext.symbols.append(module_sym)
        ctx.file.role = title[:120]

        def fact(kind: str, value: str, *, symbol_key: str, priority: int | None = None,
                 provenance: str = "markdown", confidence: float = 0.8,
                 line: int = 0) -> None:
            try:
                ext.facts.append(
                    reg.make(kind, value, file_path=path, symbol_key=symbol_key,
                             provenance=provenance, confidence=confidence,
                             priority=priority, range=SourceRange(line, line, 0, 0))
                )
            except Exception as exc:
                ext.diagnostics.append(f"fact-rejected:{kind}:{exc}")

        if title:
            fact("purpose", title[:200], symbol_key=doc_key, priority=base_priority)
            for w in _WORD.findall(title.lower()):
                ext.keyword_candidates.append(
                    KeywordCandidate(w, "heading", doc_key, path)
                )

        is_adr = _is_adr(path, ctx)
        in_fence = False
        heading_stack: list[tuple[int, str, str]] = []
        current_key = doc_key
        section_lines: list[str] = []
        headings_seen = 0
        links = 0
        concepts = 0
        sections: list[tuple[str, str, int]] = []

        def flush(key: str, buf: list[str], line_no: int) -> None:
            body = " ".join(b.strip() for b in buf if b.strip())
            if not body:
                return
            fact("documentation", body[:excerpt_chars], symbol_key=key,
                 priority=base_priority + 1, line=line_no)
            low = body.lower()
            if any(w in low for w in ("must not", "never ", "must always", "always ")):
                fact("invariant", body[:excerpt_chars], symbol_key=key,
                     priority=base_priority - 1 if base_priority else 0, line=line_no)
            if any(w in low for w in ("warning", "caution", "note that", "be careful")):
                fact("warning", body[:excerpt_chars], symbol_key=key, line=line_no)
            if is_adr and any(w in low for w in ("decision", "we will", "chosen", "accepted")):
                fact("architecture_decision", body[:excerpt_chars], symbol_key=key,
                     priority=1, line=line_no)

        for idx, line in enumerate(lines, start=1):
            if _CODE_FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                cmd = _COMMANDISH.match(line)
                if cmd:
                    fact("resource", f"command={cmd.group(2)[:80]}", symbol_key=current_key,
                         line=idx)
                for env in set(_ENVVAR.findall(line)):
                    fact("config_key", f"{env}=<code>", symbol_key=current_key, line=idx)
                continue
            m = _HEADING.match(line)
            if m and headings_seen < max_headings:
                flush(current_key, section_lines, idx)
                section_lines = []
                level = len(m.group(1))
                heading = m.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                qual = " > ".join([h[1] for h in heading_stack] + [heading])
                key = make_stable_key("markdown", path, "section", qual)
                heading_stack.append((level, heading, key))
                ext.symbols.append(
                    Symbol(
                        stable_key=key,
                        name=heading,
                        qualified_name=qual,
                        kind="section",
                        file_path=path,
                        visibility="public",
                        signature=heading,
                        parent_key=doc_key,
                        range=SourceRange(idx, idx, 0, 0),
                        is_exported=True,
                    )
                )
                sections.append((key, heading, idx))
                current_key = key
                headings_seen += 1
                for w in _WORD.findall(heading.lower()):
                    ext.keyword_candidates.append(KeywordCandidate(w, "heading", key, path))
                continue
            section_lines.append(line)
            for text_, target in _LINK.findall(line):
                if links >= max_links:
                    break
                links += 1
                fact("documentation", f"link={text_[:40]}->{target[:80]}",
                     symbol_key=current_key, line=idx, confidence=0.6)
                if _FILEISH.match(target):
                    ext.refs.append(
                        UnresolvedRef(name=target, kind="DOCUMENTS",
                                      source_symbol_key=current_key, source_file=path, line=idx)
                    )
            for code in _INLINE_CODE.findall(line):
                token = code.strip()
                if _SYMBOLISH.match(token) and concepts < max_concepts:
                    concepts += 1
                    ext.refs.append(
                        UnresolvedRef(name=token.split(".")[-1], kind="MENTIONS",
                                      source_symbol_key=current_key, source_file=path,
                                      line=idx, receiver=token)
                    )
                    for w in _WORD.findall(token):
                        ext.keyword_candidates.append(
                            KeywordCandidate(w.lower(), "docstring", current_key, path)
                        )
                elif _FILEISH.match(token):
                    ext.refs.append(
                        UnresolvedRef(name=token, kind="DOCUMENTS",
                                      source_symbol_key=current_key, source_file=path, line=idx)
                    )
            for w in _WORD.findall(line.lower())[:12]:
                ext.keyword_candidates.append(
                    KeywordCandidate(w, "docstring", current_key, path)
                )

        flush(current_key, section_lines, len(lines))

        for sym in ext.symbols:
            sym.interface_hash = _h(["iface", sym.name, sym.qualified_name])
            sym.behavior_hash = _h(["body", ctx.file.structure_hash, sym.qualified_name])
            sym.doc_hash = _h(["doc", sym.signature])
            sym.metadata_hash = _h(["meta", str(elevated)])
        ext.canonicalize()
        return ext


def _h(parts: list[str]) -> str:
    from ..hashing import digest_parts

    return digest_parts(parts)


def _is_elevated(path: str, ctx: ExtractionContext) -> bool:
    from ..globs import any_match

    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()
    if stem in {n.upper() for n in ctx.p("docs.elevated_names", []) or []}:
        return True
    return any_match(path, list(ctx.p("docs.elevated_globs", []) or []))


def _is_adr(path: str, ctx: ExtractionContext) -> bool:
    name = path.rsplit("/", 1)[-1]
    for pat in ctx.p("docs.adr_patterns", []) or []:
        if re.match(pat, name, re.IGNORECASE):
            return True
    return "/adr/" in path


def _unused(_: Any) -> None:  # pragma: no cover
    return None
