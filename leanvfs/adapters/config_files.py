"""Configuration, schema and build-file extractor.

Emits key hierarchy, value types, safe short values and environment variable names.
Build files additionally yield project name, dependencies, scripts and entrypoints.
"""

from __future__ import annotations

import configparser
import io
import json
import re
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from ..model import (
    FileExtraction,
    KeywordCandidate,
    SourceRange,
    Symbol,
    make_stable_key,
)
from .base import ExtractionContext
from .tokens import structure_hash_generic

_ENVVAR = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_YAML_KEY = re.compile(r"^(\s*)([A-Za-z_][\w.-]*)\s*:(.*)$")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")


class ConfigAdapter:
    language = "config"

    def extract(self, ctx: ExtractionContext) -> FileExtraction:
        ext = FileExtraction(file=ctx.file)
        ctx.file.structure_hash = structure_hash_generic(ctx.text)
        path = ctx.rel_path
        reg = ctx.registry
        max_keys = int(ctx.p("config_extract.max_keys", 120))
        max_chars = int(ctx.p("config_extract.max_value_chars", 60))
        max_depth = int(ctx.p("config_extract.max_depth", 6))

        root_key = make_stable_key("config", path, "module", path)
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

        pairs = _parse(ctx, max_depth)
        if pairs is None:
            ctx.file.parse_state = "partial"
            pairs = []

        emitted = 0
        for key, value, kind in pairs:
            if emitted >= max_keys:
                break
            emitted += 1
            short = _short(value, max_chars)
            try:
                ext.facts.append(
                    reg.make("config_key", f"{key}={short}", file_path=path,
                             symbol_key=root_key, provenance="configuration",
                             confidence=0.9)
                )
            except Exception as exc:
                ext.diagnostics.append(f"fact-rejected:config_key:{exc}")
            leaf = key.rsplit(".", 1)[-1]
            if _ENVVAR.match(leaf):
                _safe(ext, reg, path, root_key, "resource", f"env={leaf}")
            for w in _WORD.findall(leaf):
                ext.keyword_candidates.append(
                    KeywordCandidate(w.lower(), "parameter", root_key, path)
                )
            skey = make_stable_key("config", path, "config_key", key)
            if len(ext.symbols) < max_keys:
                ext.symbols.append(
                    Symbol(
                        stable_key=skey,
                        name=leaf,
                        qualified_name=key,
                        kind="config_key",
                        file_path=path,
                        visibility="public",
                        signature=f"{key}: {kind}",
                        return_type=kind,
                        parent_key=root_key,
                        range=SourceRange(1, 1, 0, 0),
                        is_exported=True,
                    )
                )

        if ctx.file.file_class == "build":
            self._build_facts(ctx, ext, root_key, pairs)

        for sym in ext.symbols:
            sym.interface_hash = _h(["iface", sym.qualified_name, sym.return_type])
            sym.behavior_hash = _h(["body", sym.signature])
            sym.doc_hash = _h(["doc", ""])
            sym.metadata_hash = _h(["meta", ctx.file.file_class])
        ext.canonicalize()
        return ext

    def _build_facts(self, ctx: ExtractionContext, ext: FileExtraction, root_key: str,
                     pairs: list[tuple[str, Any, str]]) -> None:
        reg = ctx.registry
        path = ctx.rel_path
        for key, value, _kind in pairs:
            low = key.lower()
            if low.endswith(("project.name", "name")) and isinstance(value, str) and "." not in low[:-4]:
                ctx.file.role = f"project {value}"[:120]
                _safe(ext, reg, path, root_key, "purpose", f"project {value}"[:120])
            if "dependencies" in low or low.endswith("requires"):
                if isinstance(value, str):
                    _safe(ext, reg, path, root_key, "resource", f"dependency={value[:60]}")
            if "scripts" in low or "entry_points" in low or "console_scripts" in low:
                _safe(ext, reg, path, root_key, "resource", f"entrypoint={key}")


def _safe(ext: FileExtraction, reg: Any, path: str, key: str, kind: str, value: str) -> None:
    try:
        ext.facts.append(
            reg.make(kind, value, file_path=path, symbol_key=key,
                     provenance="configuration", confidence=0.8)
        )
    except Exception as exc:
        ext.diagnostics.append(f"fact-rejected:{kind}:{exc}")


def _h(parts: list[str]) -> str:
    from ..hashing import digest_parts

    return digest_parts(parts)


def _short(value: Any, limit: int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit]
    return text or "<empty>"


def _flatten(obj: Any, prefix: str, depth: int, max_depth: int,
             out: list[tuple[str, Any, str]]) -> None:
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k in sorted(obj, key=str):
            _flatten(obj[k], f"{prefix}.{k}" if prefix else str(k), depth + 1, max_depth, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            if isinstance(v, (dict, list)):
                _flatten(v, f"{prefix}[{i}]", depth + 1, max_depth, out)
            else:
                out.append((prefix, v, type(v).__name__))
    else:
        out.append((prefix, obj, type(obj).__name__))


def _parse(ctx: ExtractionContext, max_depth: int) -> list[tuple[str, Any, str]] | None:
    path = ctx.rel_path.lower()
    text = ctx.text
    out: list[tuple[str, Any, str]] = []
    try:
        if path.endswith(".toml"):
            _flatten(tomllib.loads(text), "", 0, max_depth, out)
        elif path.endswith(".json"):
            _flatten(json.loads(text), "", 0, max_depth, out)
        elif path.endswith((".ini", ".cfg")):
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            cp.read_file(io.StringIO(text))
            for section in cp.sections():
                for k, v in cp.items(section):
                    out.append((f"{section}.{k}", v, "str"))
        elif path.endswith((".yaml", ".yml")):
            out.extend(_yaml_keys(text, max_depth))
        elif path.endswith(".env.example") or path.endswith(".env"):
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out.append((k.strip(), v.strip(), "str"))
        elif path.endswith(".txt"):
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(("requirement", line, "str"))
        else:
            return []
    except Exception:
        return None
    return out


def _yaml_keys(text: str, max_depth: int) -> list[tuple[str, Any, str]]:
    """Indentation-based key extraction. No YAML dependency, deliberately shallow."""
    out: list[tuple[str, Any, str]] = []
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _YAML_KEY.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2)
        rest = m.group(3).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        qual = ".".join([s[1] for s in stack] + [key])
        if len(stack) >= max_depth:
            continue
        stack.append((indent, key))
        if rest and not rest.startswith("#"):
            out.append((qual, rest, "str"))
        else:
            out.append((qual, "", "table"))
    return out
