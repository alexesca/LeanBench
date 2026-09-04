"""The one rich adapter.

Emits canonical records only. Never presentation (product invariant 9).
Return typing prefers the declared annotation; absent one it falls back to a cheap
*syntactic* shape. There is deliberately no whole-program inference.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, ClassVar

from ..model import (
    FileExtraction,
    ImportRecord,
    KeywordCandidate,
    SourceRange,
    Symbol,
    UnresolvedRef,
    disambiguate_overload,
    make_stable_key,
)
from ..registry import FactKindError
from .base import ExtractionContext
from .tokens import structure_hash_from_tree, ts_parser

_CAP = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DUNDER = re.compile(r"^__\w+__$")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")


def _text(src: bytes, node: Any) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _range(node: Any) -> SourceRange:
    return SourceRange(
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        byte_start=node.start_byte,
        byte_end=node.end_byte,
    )


def module_name_for(rel_path: str) -> str:
    stem = rel_path.removesuffix(".py")
    stem = stem.removesuffix(".pyi")
    parts = [p for p in stem.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def visibility_of(name: str) -> str:
    if _DUNDER.match(name):
        return "public"
    if name.startswith("__"):
        return "private"
    if name.startswith("_"):
        return "protected"
    return "public"


def split_identifier(name: str) -> list[str]:
    """camelCase / PascalCase / snake_case / kebab-case -> constituent terms."""
    parts: list[str] = []
    for chunk in re.split(r"[_\-.\s/]+", name):
        if not chunk:
            continue
        parts.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk))
    return [p for p in parts if p]


class PythonAdapter:
    language = "python"

    # -- entry point ----------------------------------------------------
    def extract(self, ctx: ExtractionContext) -> FileExtraction:
        ext = FileExtraction(file=ctx.file)
        parser = ts_parser()
        tree = parser.parse(ctx.source)
        root = tree.root_node
        ctx.file.structure_hash = structure_hash_from_tree(ctx.source, root)
        ctx.file.parse_state = "partial" if root.has_error else "ok"
        state = _State(ctx, ext)
        state.run(root)
        ext.canonicalize()
        return ext


class _State:
    def __init__(self, ctx: ExtractionContext, ext: FileExtraction) -> None:
        self.ctx = ctx
        self.ext = ext
        self.src = ctx.source
        self.path = ctx.rel_path
        self.reg = ctx.registry
        self.module = module_name_for(ctx.rel_path)
        self.is_test = ctx.file.file_class.startswith("test.")
        self.exports: set[str] = set()
        self.used_keys: set[str] = set()
        self.comments: list[tuple[int, int, str]] = []
        self.symbol_spans: list[tuple[int, int, str]] = []
        self.max_calls = int(ctx.p("calls.max_per_symbol", 10))
        self.record_builtins = bool(ctx.p("calls.record_builtins", False))
        self.trivial = frozenset(ctx.p("calls.trivial_builtins", []) or [])
        self.max_refs = int(ctx.p("resolution.max_refs_per_symbol", 200))
        self.fx_patterns = [
            (re.compile(pat, re.IGNORECASE), eff)
            for pat, eff in (ctx.p("side_effects.patterns", []) or [])
        ]
        self.comment_patterns = {
            klass: [re.compile(p, re.IGNORECASE) for p in pats]
            for klass, pats in (ctx.p("comments.patterns", {}) or {}).items()
        }
        self.keep_comment_classes = frozenset(ctx.p("comments.keep_classes", []) or [])
        self.comment_min = int(ctx.p("comments.min_length", 12))
        self.comment_max = int(ctx.p("comments.max_length", 240))
        self.comment_cap = int(ctx.p("comments.max_per_symbol", 4))

    # -- helpers --------------------------------------------------------
    def fact(self, kind: str, value: str, **kw: Any) -> None:
        try:
            self.ext.facts.append(self.reg.make(kind, value, file_path=self.path, **kw))
        except FactKindError as exc:  # value violated the registry grammar
            self.ext.diagnostics.append(f"fact-rejected:{kind}:{exc}")

    def kw(self, term: str, source: str, symbol_key: str | None) -> None:
        self.ext.keyword_candidates.append(
            KeywordCandidate(term=term, source=source, symbol_key=symbol_key, file_path=self.path)
        )

    def kw_split(self, raw: str, source: str, symbol_key: str | None) -> None:
        for part in split_identifier(raw):
            self.kw(part, source, symbol_key)

    def ref(self, ref: UnresolvedRef) -> None:
        self.ext.refs.append(ref)

    def key(self, kind: str, qual: str, params: list[str] | None, types: list[str] | None) -> str:
        base = make_stable_key("python", self.path, kind, qual, params)
        if base not in self.used_keys:
            self.used_keys.add(base)
            return base
        cand = disambiguate_overload(base, len(params or []), types or [])
        n = 2
        while cand in self.used_keys:
            cand = f"{base}#{n}"
            n += 1
        self.used_keys.add(cand)
        return cand

    # -- driver ---------------------------------------------------------
    def run(self, root: Any) -> None:
        self.collect_comments(root)
        self.collect_exports(root)

        mod_key = self.key("module", self.module or self.path, None, None)
        doc, _doc_node = self.docstring(root)
        mod_sym = Symbol(
            stable_key=mod_key,
            name=self.module.rsplit(".", 1)[-1] if self.module else self.path,
            qualified_name=self.module or self.path,
            kind="module",
            file_path=self.path,
            visibility="public",
            range=_range(root),
            doc=doc,
            is_exported=True,
        )
        self.ext.symbols.append(mod_sym)
        self.symbol_spans.append((root.start_byte, root.end_byte, mod_key))
        if doc:
            self.emit_doc_facts(doc, mod_key, elevated=True)
        self.kw_split(
            self.module.rsplit(".", 1)[-1] if self.module else self.path, "module_name", mod_key
        )
        if self.exports:
            for name in sorted(self.exports):
                self.fact("framework_metadata", f"export={name}", symbol_key=mod_key)

        self.walk_body(root, parent_key=mod_key, qual_prefix="", class_name=None)

        mod_sym.interface_hash = _hash_iface(mod_sym.name, "public", [], [], "", True)
        mod_sym.behavior_hash = self.ctx.file.structure_hash
        mod_sym.doc_hash = _hash_doc(doc, self.comment_texts_in(root.start_byte, root.end_byte))
        mod_sym.metadata_hash = _hash_meta(sorted(self.exports))

    # -- comments -------------------------------------------------------
    def collect_comments(self, root: Any) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "comment":
                self.comments.append(
                    (node.start_byte, node.start_point[0] + 1, _text(self.src, node))
                )
            stack.extend(node.children)
        self.comments.sort()

    def comment_texts_in(self, lo: int, hi: int) -> list[str]:
        return [t for off, _line, t in self.comments if lo <= off < hi]

    def classify_comment(self, raw: str) -> str:
        body = raw.lstrip("#").strip()
        low = body.lower()
        for klass in (
            "security",
            "invariant",
            "warning",
            "performance",
            "business_rule",
            "rationale",
            "workaround",
            "fixme",
            "todo",
        ):
            for pat in self.comment_patterns.get(klass, []):
                if pat.search(low):
                    return klass
        return "ordinary"

    _COMMENT_FACT_KIND: ClassVar[dict[str, str]] = {
        "rationale": "rationale",
        "invariant": "invariant",
        "warning": "warning",
        "security": "security_note",
        "performance": "performance_note",
        "business_rule": "invariant",
    }

    def emit_comment_facts(self, lo: int, hi: int, symbol_key: str) -> None:
        kept = 0
        for off, line, raw in self.comments:
            if not (lo <= off < hi):
                continue
            body = raw.lstrip("#").strip()
            if len(body) < self.comment_min:
                continue
            klass = self.classify_comment(raw)
            if klass not in self.keep_comment_classes:
                continue
            kind = self._COMMENT_FACT_KIND.get(klass)
            if not kind:
                continue
            self.fact(
                kind,
                body[: self.comment_max],
                symbol_key=symbol_key,
                provenance="comment",
                confidence=0.7,
                range=SourceRange(line, line, off, off),
            )
            kept += 1
            if kept >= self.comment_cap:
                break

    # -- exports --------------------------------------------------------
    def collect_exports(self, root: Any) -> None:
        for child in root.children:
            if child.type != "expression_statement":
                continue
            for a in child.children:
                if a.type != "assignment":
                    continue
                target = a.children[0] if a.children else None
                if target is None or _text(self.src, target) != "__all__":
                    continue
                for node in _descend(a):
                    if node.type == "string":
                        self.exports.add(_string_value(self.src, node))

    # -- docstrings -----------------------------------------------------
    def docstring(self, node: Any) -> tuple[str, Any]:
        body = node if node.type == "module" else node.child_by_field_name("body")
        if body is None:
            return "", None
        for child in body.children:
            if child.type == "comment":
                continue
            if child.type == "expression_statement" and child.children:
                s = child.children[0]
                if s.type == "string":
                    return _string_value(self.src, s).strip(), child
            return "", None
        return "", None

    def emit_doc_facts(self, doc: str, symbol_key: str, elevated: bool = False) -> None:
        """Module docs are compressed to structured facts, never copied wholesale."""
        lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
        if not lines:
            return
        summary = lines[0]
        self.fact(
            "purpose",
            summary[:240],
            symbol_key=symbol_key,
            provenance="docstring",
            confidence=0.9,
            priority=0 if elevated else None,
        )
        for line in lines[1:]:
            low = line.lower()
            if low.startswith(("raises", "raise ")):
                self.fact(
                    "exception",
                    line[:120],
                    symbol_key=symbol_key,
                    provenance="docstring",
                    confidence=0.6,
                )
            elif any(w in low for w in ("must never", "must always", "never ", "always ")):
                self.fact(
                    "invariant",
                    line[:240],
                    symbol_key=symbol_key,
                    provenance="docstring",
                    confidence=0.6,
                )
            elif any(w in low for w in ("security", "credential", "unsafe")):
                self.fact(
                    "security_note",
                    line[:240],
                    symbol_key=symbol_key,
                    provenance="docstring",
                    confidence=0.6,
                )
        for word in _WORD.findall(doc.lower())[:60]:
            self.kw(word, "docstring", symbol_key)

    # -- body walk ------------------------------------------------------
    def walk_body(
        self, node: Any, parent_key: str, qual_prefix: str, class_name: str | None
    ) -> None:
        body = node if node.type == "module" else node.child_by_field_name("body")
        if body is None:
            return
        for child in body.children:
            self.handle_statement(child, parent_key, qual_prefix, class_name, ())

    def handle_statement(
        self,
        child: Any,
        parent_key: str,
        qual_prefix: str,
        class_name: str | None,
        decorators: tuple[str, ...],
    ) -> None:
        t = child.type
        if t == "decorated_definition":
            decs: list[str] = []
            inner = None
            for c in child.children:
                if c.type == "decorator":
                    decs.append(_text(self.src, c).lstrip("@").strip())
                elif c.type in ("function_definition", "class_definition"):
                    inner = c
            if inner is not None:
                self.handle_statement(inner, parent_key, qual_prefix, class_name, tuple(decs))
            return
        if t == "class_definition":
            self.handle_class(child, parent_key, qual_prefix, decorators)
        elif t == "function_definition":
            self.handle_function(child, parent_key, qual_prefix, class_name, decorators, child)
        elif t in ("import_statement", "import_from_statement", "future_import_statement"):
            self.handle_import(child, parent_key)
        elif t == "expression_statement":
            self.handle_assignment(child, parent_key, qual_prefix, class_name)
        elif t in (
            "if_statement",
            "try_statement",
            "with_statement",
            "for_statement",
            "while_statement",
        ):
            for c in child.children:
                if c.type == "block":
                    for g in c.children:
                        self.handle_statement(g, parent_key, qual_prefix, class_name, ())

    # -- imports --------------------------------------------------------
    def handle_import(self, node: Any, mod_key: str) -> None:
        line = node.start_point[0] + 1
        if node.type == "import_statement":
            for c in node.children:
                if c.type == "dotted_name":
                    mod = _text(self.src, c)
                    self.ext.imports.append(ImportRecord(mod, "", (), False, 0, line))
                elif c.type == "aliased_import":
                    mod_node = c.child_by_field_name("name") or c.children[0]
                    alias_node = c.child_by_field_name("alias") or c.children[-1]
                    self.ext.imports.append(
                        ImportRecord(
                            _text(self.src, mod_node),
                            _text(self.src, alias_node),
                            (),
                            False,
                            0,
                            line,
                        )
                    )
        else:
            level = 0
            mod = ""
            names: list[tuple[str, str]] = []
            after_import = False
            for c in node.children:
                if c.type == "import":
                    after_import = True
                    continue
                if c.type == "relative_import":
                    txt = _text(self.src, c)
                    level = len(txt) - len(txt.lstrip("."))
                    mod = txt.lstrip(".")
                elif c.type == "dotted_name" and not after_import:
                    mod = _text(self.src, c)
                elif c.type == "__future__":
                    mod = "__future__"
                elif after_import and c.type == "dotted_name":
                    names.append((_text(self.src, c), ""))
                elif after_import and c.type == "aliased_import":
                    n = c.child_by_field_name("name") or c.children[0]
                    a = c.child_by_field_name("alias") or c.children[-1]
                    names.append((_text(self.src, n), _text(self.src, a)))
                elif after_import and c.type == "wildcard_import":
                    names.append(("*", ""))
            self.ext.imports.append(
                ImportRecord(
                    mod,
                    "",
                    tuple(f"{n}|{a}" for n, a in names),
                    level > 0,
                    level,
                    line,
                )
            )
        for imp in self.ext.imports[-1:]:
            target = imp.module or ("." * imp.level)
            if target:
                self.ref(
                    UnresolvedRef(
                        name=target,
                        kind="IMPORTS",
                        source_symbol_key=mod_key,
                        source_file=self.path,
                        line=line,
                        alias_module=imp.alias,
                        arity=imp.level,
                    )
                )

    # -- assignments ----------------------------------------------------
    def handle_assignment(
        self, stmt: Any, parent_key: str, qual_prefix: str, class_name: str | None
    ) -> None:
        for a in stmt.children:
            if a.type not in ("assignment", "augmented_assignment"):
                continue
            target = a.child_by_field_name("left") or (a.children[0] if a.children else None)
            if target is None or target.type != "identifier":
                continue
            name = _text(self.src, target)
            if name == "__all__":
                continue
            ann = a.child_by_field_name("type")
            ann_text = _text(self.src, ann) if ann is not None else ""
            if class_name is not None:
                kind = "attribute"
            elif _ALLCAPS.match(name) or _DUNDER.match(name):
                kind = "constant"
            else:
                return
            qual = f"{qual_prefix}{name}" if qual_prefix else name
            key = self.key(kind, qual, None, None)
            sym = Symbol(
                stable_key=key,
                name=name,
                qualified_name=qual,
                kind=kind,
                file_path=self.path,
                visibility=visibility_of(name),
                parent_key=parent_key,
                signature=f"{name}: {ann_text}" if ann_text else name,
                return_type=ann_text,
                range=_range(a),
                is_exported=name in self.exports,
                interface_hash=_hash_iface(
                    name, visibility_of(name), [], [], ann_text, name in self.exports
                ),
                behavior_hash=_hash_body(self.normalized_tokens(a)),
                doc_hash=_hash_doc("", []),
                metadata_hash=_hash_meta([ann_text] if ann_text else []),
            )
            self.ext.symbols.append(sym)
            self.symbol_spans.append((a.start_byte, a.end_byte, key))
            self.kw_split(name, "symbol_name", key)
            if ann_text:
                self.fact("type_use", ann_text[:80], symbol_key=key)
                self.kw_split(ann_text, "type_use", key)
                self.emit_type_refs(ann_text, key, a.start_point[0] + 1)

    # -- classes --------------------------------------------------------
    def handle_class(
        self, node: Any, parent_key: str, qual_prefix: str, decorators: tuple[str, ...]
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(self.src, name_node)
        qual = f"{qual_prefix}{name}" if qual_prefix else name
        key = self.key("class", qual, None, None)
        bases: list[str] = []
        arg_list = node.child_by_field_name("superclasses")
        if arg_list is not None:
            for c in arg_list.named_children:
                if c.type == "keyword_argument":
                    continue
                bases.append(_text(self.src, c))
        doc, doc_node = self.docstring(node)
        line = node.start_point[0] + 1
        is_test_class = self.is_test and name.startswith("Test")
        sym = Symbol(
            stable_key=key,
            name=name,
            qualified_name=qual,
            kind="test_class" if is_test_class else "class",
            file_path=self.path,
            visibility=visibility_of(name),
            signature=f"{name}({','.join(bases)})" if bases else f"{name}()",
            parent_key=parent_key,
            range=_range(node),
            doc=doc,
            decorators=decorators,
            is_exported=name in self.exports or not self.exports,
            interface_hash=_hash_iface(
                name, visibility_of(name), bases, [], "", name in self.exports
            ),
            behavior_hash=_hash_body(self.normalized_tokens(node, skip=doc_node)),
            doc_hash=_hash_doc(doc, self.comment_texts_in(node.start_byte, node.end_byte)),
            metadata_hash=_hash_meta(list(decorators) + bases),
        )
        self.ext.symbols.append(sym)
        self.symbol_spans.append((node.start_byte, node.end_byte, key))
        self.kw_split(name, "class_name", key)
        self.fact("signature", sym.signature, symbol_key=key)
        for base in bases:
            self.ref(
                UnresolvedRef(
                    name=base.split(".")[-1],
                    kind="EXTENDS",
                    source_symbol_key=key,
                    source_file=self.path,
                    line=line,
                    receiver=base,
                )
            )
            self.kw_split(base, "type_use", key)
        for dec in decorators:
            self.fact("framework_metadata", f"decorator={dec[:60]}", symbol_key=key)
            self.kw_split(dec.split("(")[0], "decorator", key)
        if doc:
            self.emit_doc_facts(doc, key)
        self.emit_comment_facts(node.start_byte, node.end_byte, key)
        self.walk_body(node, parent_key=key, qual_prefix=f"{qual}.", class_name=qual)

    # -- functions ------------------------------------------------------
    def handle_function(
        self,
        node: Any,
        parent_key: str,
        qual_prefix: str,
        class_name: str | None,
        decorators: tuple[str, ...],
        outer: Any,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(self.src, name_node)
        qual = f"{qual_prefix}{name}" if qual_prefix else name
        params = self.parse_params(node)
        param_names = [p[0] for p in params]
        param_types = [p[1] for p in params]
        is_async = any(c.type == "async" for c in node.children)
        ret_node = node.child_by_field_name("return_type")
        ret = _text(self.src, ret_node) if ret_node is not None else ""

        if self.is_test and name.startswith("test"):
            kind = "test"
        elif any(d.split("(")[0] in ("property", "cached_property") for d in decorators):
            kind = "property"
        elif class_name is not None:
            kind = "method"
        else:
            kind = "function"

        key = self.key(kind, qual, param_names, param_types)
        doc, doc_node = self.docstring(node)
        sig = _signature(name, params, ret)
        line = node.start_point[0] + 1
        sym = Symbol(
            stable_key=key,
            name=name,
            qualified_name=qual,
            kind=kind,
            file_path=self.path,
            visibility=visibility_of(name),
            signature=sig,
            return_type=ret,
            parent_key=parent_key,
            range=_range(outer),
            doc=doc,
            is_async=is_async,
            decorators=decorators,
            is_exported=name in self.exports or (not self.exports and not name.startswith("_")),
            interface_hash=_hash_iface(
                name, visibility_of(name), param_names, param_types, ret, name in self.exports
            ),
            behavior_hash=_hash_body(self.normalized_tokens(node, skip=doc_node)),
            doc_hash=_hash_doc(doc, self.comment_texts_in(node.start_byte, node.end_byte)),
            metadata_hash=_hash_meta(list(decorators)),
        )
        self.ext.symbols.append(sym)
        self.symbol_spans.append((node.start_byte, node.end_byte, key))

        self.fact("signature", sig, symbol_key=key)
        self.kw_split(name, "symbol_name", key)
        if qual != name:
            self.kw_split(qual.split(".")[0], "qualified_name", key)
        for pname, ptype, has_default in params:
            if pname in ("self", "cls"):
                continue
            value = pname + (f":{ptype}" if ptype else "") + ("=" if has_default else "")
            self.fact("parameter", value, symbol_key=key)
            self.kw_split(pname, "parameter", key)
            if ptype:
                self.fact("type_use", ptype[:80], symbol_key=key)
                self.kw_split(ptype, "type_use", key)
                self.emit_type_refs(ptype, key, line)
        if ret:
            self.fact("return_type", ret[:80], symbol_key=key)
            self.kw_split(ret, "return_type", key)
            self.emit_type_refs(ret, key, line)
        for dec in decorators:
            self.fact("framework_metadata", f"decorator={dec[:60]}", symbol_key=key)
            self.kw_split(dec.split("(")[0], "decorator", key)
        if doc:
            self.emit_doc_facts(doc, key)
        self.emit_comment_facts(node.start_byte, node.end_byte, key)

        body = node.child_by_field_name("body")
        if body is not None:
            self.analyze_body(body, key, ret, doc_node, kind)
            # nested functions and classes
            for c in body.children:
                if c.type in ("function_definition", "class_definition", "decorated_definition"):
                    self.handle_statement(c, key, f"{qual}.", class_name, ())

    def parse_params(self, node: Any) -> list[tuple[str, str, bool]]:
        params_node = node.child_by_field_name("parameters")
        out: list[tuple[str, str, bool]] = []
        if params_node is None:
            return out
        for c in params_node.named_children:
            t = c.type
            if t == "identifier":
                out.append((_text(self.src, c), "", False))
            elif t == "typed_parameter":
                ident = next(
                    (
                        g
                        for g in c.children
                        if g.type
                        in ("identifier", "list_splat_pattern", "dictionary_splat_pattern")
                    ),
                    None,
                )
                ty = c.child_by_field_name("type")
                out.append(
                    (
                        _text(self.src, ident) if ident is not None else "",
                        _text(self.src, ty) if ty is not None else "",
                        False,
                    )
                )
            elif t == "default_parameter":
                n = c.child_by_field_name("name")
                out.append((_text(self.src, n) if n is not None else "", "", True))
            elif t == "typed_default_parameter":
                n = c.child_by_field_name("name")
                ty = c.child_by_field_name("type")
                out.append(
                    (
                        _text(self.src, n) if n is not None else "",
                        _text(self.src, ty) if ty is not None else "",
                        True,
                    )
                )
            elif t == "list_splat_pattern" or t == "dictionary_splat_pattern":
                out.append((_text(self.src, c), "", False))
            elif t == "keyword_separator":
                out.append(("*", "", False))
        return [(n, ty, d) for n, ty, d in out if n]

    # -- body analysis --------------------------------------------------
    def analyze_body(
        self, body: Any, key: str, declared_ret: str, doc_node: Any, kind: str
    ) -> None:
        calls: list[tuple[str, str, int, int]] = []  # (raw, receiver, arity, line)
        raises: list[str] = []
        yields: list[str] = []
        returns: list[str] = []
        effects: set[str] = set()
        members: set[str] = set()
        literals: list[str] = []
        mocks: list[str] = []
        expectations: list[tuple[str, int]] = []
        cases: list[str] = []
        expected_exc: list[str] = []

        for node in _descend(body):
            t = node.type
            if t == "call":
                fn = node.child_by_field_name("function")
                args = node.child_by_field_name("arguments")
                arity = len(args.named_children) if args is not None else 0
                if fn is None:
                    continue
                raw = _text(self.src, fn)
                receiver = ""
                simple = raw
                if fn.type == "attribute":
                    obj = fn.child_by_field_name("object")
                    attr = fn.child_by_field_name("attribute")
                    receiver = _text(self.src, obj) if obj is not None else ""
                    simple = _text(self.src, attr) if attr is not None else raw
                    members.add(raw)
                calls.append((raw, receiver, arity, node.start_point[0] + 1))
                if raw.endswith("pytest.raises") or simple == "raises":
                    for a in args.named_children if args is not None else []:
                        expected_exc.append(_text(self.src, a))
                if simple in ("patch", "object", "MagicMock", "Mock", "AsyncMock", "mock_open"):
                    mocks.append(raw)
            elif t == "raise_statement":
                target = next(iter(node.named_children), None)
                if target is not None:
                    txt = _text(self.src, target)
                    raises.append(txt.split("(")[0].split(".")[-1])
            elif t == "yield":
                parent = node.parent
                shape = ""
                if parent is not None:
                    kids = [c for c in parent.named_children if c is not node]
                    if kids:
                        shape = _shape_of(self.src, kids[0])
                yields.append(shape or "any")
            elif t == "return_statement":
                kids = node.named_children
                if kids:
                    returns.append(_shape_of(self.src, kids[0]))
            elif t == "assert_statement":
                expectations.append(
                    (_text(self.src, node)[: self.comment_max], node.start_point[0] + 1)
                )
            elif (
                t == "string"
                and node.parent is not None
                and node.parent.type in ("argument_list", "assignment", "list", "tuple")
            ):
                val = _string_value(self.src, node)
                if 3 <= len(val) <= 60:
                    literals.append(val)

        # calls -> facts + refs
        emitted = 0
        seen_call: set[str] = set()
        for raw, receiver, arity, line in calls:
            simple = raw.rsplit(".", 1)[-1]
            if not self.record_builtins and (simple in self.trivial or raw in self.trivial):
                continue
            if not _CALL_OK.match(raw):
                continue
            for pat, eff in self.fx_patterns:
                if pat.search(raw):
                    effects.add(eff)
            if raw not in seen_call and emitted < self.max_calls:
                seen_call.add(raw)
                self.fact("call", raw[:80], symbol_key=key, confidence=0.8)
                self.kw_split(simple, "call_target", key)
                emitted += 1
            if len(self.ext.refs) < self.max_refs * 8:
                self.ref(
                    UnresolvedRef(
                        name=simple,
                        kind="CALLS",
                        source_symbol_key=key,
                        source_file=self.path,
                        line=line,
                        receiver=receiver,
                        arity=arity,
                    )
                )
                if kind == "test":
                    self.ref(
                        UnresolvedRef(
                            name=simple,
                            kind="TESTS",
                            source_symbol_key=key,
                            source_file=self.path,
                            line=line,
                            receiver=receiver,
                            arity=arity,
                        )
                    )

        for eff in sorted(effects):
            self.fact("side_effect", eff, symbol_key=key, confidence=0.6, provenance="heuristic")
        for exc in sorted(set(raises)):
            if exc:
                self.fact("exception", exc[:60], symbol_key=key)
                self.kw_split(exc, "exception", key)
                self.ref(
                    UnresolvedRef(
                        name=exc,
                        kind="REFERENCES",
                        source_symbol_key=key,
                        source_file=self.path,
                        line=0,
                    )
                )
        for member in sorted(members)[:20]:
            self.kw_split(member.rsplit(".", 1)[-1], "member_access", key)
        for lit in literals[:10]:
            for term in split_identifier(lit):
                self.kw(term, "literal", key)

        if not declared_ret:
            shapes = sorted({s for s in returns if s and s != "None"})
            if yields:
                yshapes = sorted({s for s in yields if s})
                if yshapes:
                    self.fact(
                        "return_shape",
                        "yield=" + "|".join(yshapes[:3]),
                        symbol_key=key,
                        confidence=0.5,
                        provenance="heuristic",
                    )
            elif shapes:
                self.fact(
                    "return_shape",
                    "ret=" + "|".join(shapes[:3]),
                    symbol_key=key,
                    confidence=0.5,
                    provenance="heuristic",
                )

        if kind == "test":
            self.emit_test_facts(key, expectations, mocks, expected_exc, cases)

    def emit_test_facts(
        self,
        key: str,
        expectations: list[tuple[str, int]],
        mocks: list[str],
        expected_exc: list[str],
        cases: list[str],
    ) -> None:
        sym = next((s for s in self.ext.symbols if s.stable_key == key), None)
        if sym is not None:
            scenario = " ".join(split_identifier(sym.name)[1:]) or sym.name
            if scenario:
                self.fact("test_case", scenario[:120], symbol_key=key, provenance="test")
            for pname, _ptype, _d in [(p, "", False) for p in _sig_params(sym.signature)]:
                if pname and pname not in ("self", "cls"):
                    self.fact("test_fixture", pname, symbol_key=key, provenance="test")
                    self.ref(
                        UnresolvedRef(
                            name=pname,
                            kind="USES_FIXTURE",
                            source_symbol_key=key,
                            source_file=self.path,
                        )
                    )
            for dec in sym.decorators:
                if "parametrize" in dec:
                    self.fact("test_case", dec[:160], symbol_key=key, provenance="test")
        for text, line in expectations[:8]:
            self.fact(
                "test_expectation",
                _normalize_ws(text)[:200],
                symbol_key=key,
                provenance="test",
                confidence=0.9,
                range=SourceRange(line, line, 0, 0),
            )
        for m in sorted(set(mocks))[:6]:
            self.fact("test_mock", m[:80], symbol_key=key, provenance="test")
            self.ref(
                UnresolvedRef(
                    name=m.rsplit(".", 1)[-1],
                    kind="MOCKS",
                    source_symbol_key=key,
                    source_file=self.path,
                )
            )
        for exc in sorted(set(expected_exc))[:4]:
            clean = exc.split("(")[0].split(".")[-1]
            if clean and _CAP.match(clean):
                self.fact("exception", clean, symbol_key=key, provenance="test", confidence=0.9)

    # -- types ----------------------------------------------------------
    def emit_type_refs(self, annotation: str, key: str, line: int) -> None:
        for name in sorted(set(re.findall(r"[A-Za-z_][\w.]*", annotation))):
            base = name.split(".")[-1]
            if base in _TYPING_NOISE or not _CAP.match(base):
                continue
            # `receiver` is the dotted prefix, if there is one. Passing the whole
            # dotted name made a bare annotation resolve against itself and surface as
            # `QueryParamTypes.QueryParamTypes` in the unresolved-edge table.
            receiver = name[: -(len(base) + 1)] if name.endswith("." + base) else ""
            self.ref(
                UnresolvedRef(
                    name=base,
                    kind="USES_TYPE",
                    source_symbol_key=key,
                    source_file=self.path,
                    line=line,
                    receiver=receiver,
                )
            )

    # -- token normalization --------------------------------------------
    def normalized_tokens(self, node: Any, skip: Any = None) -> list[str]:
        """Whitespace/comments stripped, literals preserved."""
        out: list[str] = []
        skip_range = (skip.start_byte, skip.end_byte) if skip is not None else None
        for leaf in _leaves(node):
            if leaf.type == "comment":
                continue
            if skip_range and skip_range[0] <= leaf.start_byte < skip_range[1]:
                continue
            out.append(_text(self.src, leaf))
        return out


# --------------------------------------------------------------------------
# module-level helpers
# --------------------------------------------------------------------------

_CALL_OK = re.compile(r"^[A-Za-z_][\w.]*$")
_TYPING_NOISE = frozenset(
    {
        "Optional",
        "Union",
        "List",
        "Dict",
        "Set",
        "Tuple",
        "Any",
        "Callable",
        "Iterable",
        "Iterator",
        "Sequence",
        "Mapping",
        "Type",
        "None",
        "AsyncIterator",
        "Awaitable",
        "Literal",
        "Final",
        "ClassVar",
        "Generator",
        "AsyncGenerator",
        "TypeVar",
        "Self",
    }
)


def _descend(node: Any) -> Iterable[Any]:
    stack = list(node.children)
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)


def _leaves(node: Any) -> Iterable[Any]:
    stack = [node]
    order: list[Any] = []
    while stack:
        n = stack.pop()
        if not n.children:
            order.append(n)
        else:
            stack.extend(reversed(n.children))
    return order


def _string_value(src: bytes, node: Any) -> str:
    parts = [
        src[c.start_byte : c.end_byte].decode("utf-8", "replace")
        for c in node.children
        if c.type == "string_content"
    ]
    if parts:
        return "".join(parts)
    raw = src[node.start_byte : node.end_byte].decode("utf-8", "replace")
    return raw.strip("\"'")


def _shape_of(src: bytes, node: Any) -> str:
    """Cheap syntactic return shape. Never whole-program inference."""
    t = node.type
    if t == "call":
        fn = node.child_by_field_name("function")
        if fn is not None:
            name = src[fn.start_byte : fn.end_byte].decode("utf-8", "replace")
            base = name.rsplit(".", 1)[-1]
            if _CAP.match(base):
                return base
            return base
    if t == "dictionary":
        keys = []
        for pair in node.named_children:
            if pair.type == "pair":
                k = pair.child_by_field_name("key")
                if k is not None and k.type == "string":
                    keys.append(_string_value(src, k))
        if keys:
            return "{" + ",".join(sorted(keys)[:8]) + "}"
        return "dict"
    if t == "none":
        return "None"
    if t in ("true", "false"):
        return "bool"
    if t == "string":
        return "str"
    if t == "integer":
        return "int"
    if t == "float":
        return "float"
    if t == "list":
        return "list"
    if t == "tuple":
        return "tuple"
    if t == "identifier":
        return src[node.start_byte : node.end_byte].decode("utf-8", "replace")
    if t == "attribute":
        return src[node.start_byte : node.end_byte].decode("utf-8", "replace").rsplit(".", 1)[-1]
    if t == "conditional_expression":
        kids = node.named_children
        if kids:
            return _shape_of(src, kids[0])
    return ""


def _signature(name: str, params: list[tuple[str, str, bool]], ret: str) -> str:
    rendered = []
    for pname, ptype, default in params:
        if pname in ("self", "cls"):
            continue
        piece = pname
        if ptype:
            piece += f":{_normalize_ws(ptype)}"
        if default:
            piece += "="
        rendered.append(piece)
    sig = f"{name}({','.join(rendered)})"
    if ret:
        sig += f"->{_normalize_ws(ret)}"
    return sig


def _sig_params(signature: str) -> list[str]:
    inner = signature[signature.find("(") + 1 :]
    inner = inner[: inner.rfind(")")] if ")" in inner else inner
    out = []
    depth = 0
    cur = ""
    for ch in inner:
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return [p.split(":")[0].split("=")[0].strip() for p in out if p.strip()]


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _hash_iface(
    name: str,
    visibility: str,
    param_names: list[str],
    param_types: list[str],
    ret: str,
    exported: bool,
) -> str:
    from ..hashing import digest_parts

    return digest_parts(
        ["iface", name, visibility, *param_names, "|", *param_types, "|", ret, str(exported)]
    )


def _hash_body(tokens: list[str]) -> str:
    from ..hashing import digest_parts

    return digest_parts(["body", *tokens])


def _hash_doc(doc: str, comments: list[str]) -> str:
    from ..hashing import digest_parts

    return digest_parts(["doc", doc, *sorted(comments)])


def _hash_meta(items: list[str]) -> str:
    from ..hashing import digest_parts

    return digest_parts(["meta", *sorted(items)])
