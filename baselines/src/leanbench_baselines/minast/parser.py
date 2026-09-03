"""tree-sitter extraction for the MinimalAST baseline.

Extracted, and nothing more: functions, classes, methods, their parameter names and
annotations, return annotations, decorators, source locations, the module/class nesting
hierarchy, imports, and raw identifier occurrences.

Explicitly *not* extracted (this is the headroom a full semantic system must earn):
call-graph resolution, exception propagation, side effects, test linkage, documentation
intelligence, keyword scoring, or any budget engine beyond flat prefix truncation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

_LANGUAGE = Language(tspython.language())

_DEF_TYPES = frozenset({"function_definition", "class_definition"})
_SCOPE_TYPES = frozenset({"function_definition", "class_definition", "decorated_definition"})


@dataclass(frozen=True)
class Param:
    name: str
    annotation: str | None
    default: str | None

    def render(self) -> str:
        text = self.name
        if self.annotation:
            text += f": {self.annotation}"
        if self.default is not None:
            text += f" = {self.default}" if self.annotation else f"={self.default}"
        return text


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    qualified: str
    kind: str  # "class" | "function" | "method"
    line_start: int
    line_end: int
    params: tuple[Param, ...]
    return_type: str | None
    decorators: tuple[str, ...]
    is_async: bool
    bases: tuple[str, ...]
    doc: str | None
    scope: str | None

    @property
    def visibility(self) -> str:
        return "private" if self.name.startswith("_") else "public"

    def signature(self) -> str:
        if self.kind == "class":
            bases = ", ".join(self.bases)
            return f"class {self.name}({bases})" if bases else f"class {self.name}"
        params = ", ".join(param.render() for param in self.params)
        prefix = "async " if self.is_async else ""
        text = f"{prefix}{self.name}({params})"
        if self.return_type:
            text += f" -> {self.return_type}"
        return text


@dataclass(frozen=True)
class ImportRecord:
    line: int
    module: str  # dotted module text as written ("." prefixes kept for relative)
    name: str | None  # imported name for `from x import y`
    alias: str | None
    level: int  # number of leading dots (0 for absolute)

    def render(self) -> str:
        if self.name is None:
            return f"import {self.module}" + (f" as {self.alias}" if self.alias else "")
        text = f"from {self.module} import {self.name}"
        return text + (f" as {self.alias}" if self.alias else "")


@dataclass
class FileAst:
    path: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    occurrences: list[tuple[str, int]] = field(default_factory=list)
    ok: bool = True


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _params(node: Node | None, source: bytes) -> tuple[Param, ...]:
    if node is None:
        return ()
    out: list[Param] = []
    for child in node.named_children:
        kind = child.type
        if kind == "identifier":
            out.append(Param(_text(child, source), None, None))
        elif kind == "typed_parameter":
            name = _text(child.named_children[0], source) if child.named_children else ""
            out.append(Param(name, _flat(_text(child.child_by_field_name("type"), source)), None))
        elif kind == "default_parameter":
            out.append(
                Param(
                    _text(child.child_by_field_name("name"), source),
                    None,
                    _flat(_text(child.child_by_field_name("value"), source)),
                )
            )
        elif kind == "typed_default_parameter":
            out.append(
                Param(
                    _text(child.child_by_field_name("name"), source),
                    _flat(_text(child.child_by_field_name("type"), source)),
                    _flat(_text(child.child_by_field_name("value"), source)),
                )
            )
        elif kind == "list_splat_pattern":
            out.append(Param("*" + _text(child.named_children[0], source), None, None))
        elif kind == "dictionary_splat_pattern":
            out.append(Param("**" + _text(child.named_children[0], source), None, None))
        elif kind == "keyword_separator":
            out.append(Param("*", None, None))
    return tuple(out)


def _docstring(body: Node | None, source: bytes) -> str | None:
    if body is None:
        return None
    for child in body.named_children:
        if child.type != "expression_statement":
            return None
        if not child.named_children or child.named_children[0].type != "string":
            return None
        raw = _text(child.named_children[0], source)
        stripped = raw.strip().strip("rbfu").strip()
        for quote in ('"""', "'''", '"', "'"):
            if stripped.startswith(quote):
                stripped = stripped[len(quote) :]
                if stripped.endswith(quote):
                    stripped = stripped[: -len(quote)]
                break
        for line in stripped.splitlines():
            if line.strip():
                return _flat(line.strip())[:200]
        return None
    return None


def _bases(node: Node, source: bytes) -> tuple[str, ...]:
    arguments = node.child_by_field_name("superclasses")
    if arguments is None:
        return ()
    return tuple(_flat(_text(child, source)) for child in arguments.named_children)


class AstExtractor:
    """Parses one file at a time. Instances are cheap and stateless between calls."""

    def __init__(self) -> None:
        self._parser = Parser(_LANGUAGE)

    def parse_file(self, path: str, text: str) -> FileAst:
        source = text.encode("utf-8")
        tree = self._parser.parse(source)
        result = FileAst(path=path, ok=not tree.root_node.has_error)
        self._walk(tree.root_node, source, path, [], result)
        result.symbols.sort(key=lambda symbol: (symbol.line_start, symbol.qualified))
        result.imports.sort(key=lambda record: (record.line, record.render()))
        result.occurrences.sort()
        return result

    def _walk(
        self,
        node: Node,
        source: bytes,
        path: str,
        scope: list[str],
        result: FileAst,
        decorators: tuple[str, ...] = (),
    ) -> None:
        for child in node.named_children:
            kind = child.type
            if kind == "decorated_definition":
                names = tuple(
                    _flat(_text(part, source)).lstrip("@")
                    for part in child.named_children
                    if part.type == "decorator"
                )
                definition = child.child_by_field_name("definition")
                if definition is not None:
                    self._walk_definition(definition, source, path, scope, result, names)
                continue
            if kind in _DEF_TYPES:
                self._walk_definition(child, source, path, scope, result, decorators)
                continue
            if kind == "import_statement":
                self._import_statement(child, source, result)
                continue
            if kind in ("import_from_statement", "future_import_statement"):
                self._import_from_statement(child, source, result)
                continue
            if kind == "identifier":
                result.occurrences.append((_text(child, source), child.start_point[0] + 1))
            elif kind == "attribute":
                attribute = child.child_by_field_name("attribute")
                if attribute is not None:
                    result.occurrences.append(
                        (_text(attribute, source), attribute.start_point[0] + 1)
                    )
            self._walk(child, source, path, scope, result)

    def _walk_definition(
        self,
        node: Node,
        source: bytes,
        path: str,
        scope: list[str],
        result: FileAst,
        decorators: tuple[str, ...],
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        if not name:
            return
        is_class = node.type == "class_definition"
        parent_kind = None
        if scope:
            parent_kind = "class" if scope[-1].startswith("c:") else "function"
        qualified_scope = ".".join(part[2:] for part in scope) or None
        if is_class:
            kind = "class"
        elif parent_kind == "class":
            kind = "method"
        else:
            kind = "function"
        body = node.child_by_field_name("body")
        symbol = Symbol(
            path=path,
            name=name,
            qualified=f"{qualified_scope}.{name}" if qualified_scope else name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            params=() if is_class else _params(node.child_by_field_name("parameters"), source),
            return_type=(
                None
                if is_class
                else (_flat(_text(node.child_by_field_name("return_type"), source)) or None)
            ),
            decorators=decorators,
            is_async=any(child.type == "async" for child in node.children),
            bases=_bases(node, source) if is_class else (),
            doc=_docstring(body, source),
            scope=qualified_scope,
        )
        result.symbols.append(symbol)
        result.occurrences.append((name, symbol.line_start))
        marker = ("c:" if is_class else "f:") + name
        if body is not None:
            self._walk(body, source, path, [*scope, marker], result)

    def _import_statement(self, node: Node, source: bytes, result: FileAst) -> None:
        line = node.start_point[0] + 1
        for child in node.named_children:
            if child.type == "aliased_import":
                module = _flat(_text(child.child_by_field_name("name"), source))
                alias = _flat(_text(child.child_by_field_name("alias"), source))
                result.imports.append(ImportRecord(line, module, None, alias or None, 0))
            elif child.type in ("dotted_name", "identifier"):
                result.imports.append(
                    ImportRecord(line, _flat(_text(child, source)), None, None, 0)
                )

    def _import_from_statement(self, node: Node, source: bytes, result: FileAst) -> None:
        line = node.start_point[0] + 1
        module_node = node.child_by_field_name("module_name")
        module_text = _flat(_text(module_node, source)) if module_node is not None else ""
        level = len(module_text) - len(module_text.lstrip("."))
        module = module_text.lstrip(".")
        module_end = module_node.end_byte if module_node is not None else node.start_byte
        for child in node.named_children:
            if child.start_byte < module_end:
                continue
            if child.type == "aliased_import":
                name = _flat(_text(child.child_by_field_name("name"), source))
                alias = _flat(_text(child.child_by_field_name("alias"), source))
                result.imports.append(ImportRecord(line, module, name, alias or None, level))
            elif child.type in ("dotted_name", "identifier", "wildcard_import"):
                result.imports.append(
                    ImportRecord(line, module, _flat(_text(child, source)), None, level)
                )
