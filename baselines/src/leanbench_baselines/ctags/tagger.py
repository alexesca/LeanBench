"""A universal-ctags *equivalent* tag generator for Python, in pure Python.

``universal-ctags`` is not installed in this environment and cannot be installed without
root, so this module reimplements what ``ctags --fields=+neKSt --extras=+q`` emits for
Python. Like the real thing, it is a **line-oriented scanner**: it tracks indentation and
triple-quoted string state and never builds a syntax tree. That matters for the benchmark
ladder -- the CTags rung must stay strictly below the AST rung.

Emitted kinds (identical letters and names to universal-ctags' Python parser):

======  ===========  ===============================================================
letter  name         meaning
======  ===========  ===============================================================
``c``   class        class definitions
``f``   function     module-level (or nested-in-function) ``def``
``m``   member       ``def`` whose immediate scope is a class
``v``   variable     module-level or class-level assignment
``i``   module       ``import x`` / the module part of ``from x import y``
``I``   namespace    ``import x as y`` -- the local alias
``x``   unknown      ``from x import y`` -- the imported name
======  ===========  ===============================================================

Kinds ``z`` (parameter) and ``l`` (local) are off by default in universal-ctags and are
not emitted here either. Fields produced per tag: ``name``, ``path``, ``line``, ``end``,
``kind``, ``scope`` (``class:Outer.Inner``), ``signature`` (``(self, url, *, x=None)``)
and ``typeref`` (``typename:Response``) where the source states one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from leanbench_baselines.common.text import block_end, read_signature, return_annotation

KIND_NAMES: dict[str, str] = {
    "c": "class",
    "f": "function",
    "m": "member",
    "v": "variable",
    "i": "module",
    "I": "namespace",
    "x": "unknown",
}

#: Protocol-facing `kind` string for each ctags kind letter.
PROTOCOL_KIND: dict[str, str] = {
    "c": "class",
    "f": "function",
    "m": "method",
    "v": "variable",
    "i": "import",
    "I": "import",
    "x": "import",
}

_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:async[ \t]+)?(?P<kind>def|class)[ \t]+(?P<name>[A-Za-z_]\w*)"
)
_ASSIGN_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<name>[A-Za-z_]\w*)"
    r"(?:[ \t]*:[ \t]*(?P<type>[^=]+?))?[ \t]*=(?!=)"
)
_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+(?P<body>.+)$")
_FROM_RE = re.compile(r"^[ \t]*from[ \t]+(?P<module>[.\w]+)[ \t]+import[ \t]+(?P<body>.+)$")
_TRIPLE_RE = re.compile(r'"""|\'\'\'')


@dataclass(frozen=True)
class Tag:
    """One ctags tag record."""

    name: str
    path: str
    line: int
    end: int
    kind: str  # ctags kind letter
    scope: str | None  # dotted scope path, e.g. "Client"
    scope_kind: str | None  # "class" | "function" | None
    signature: str | None
    typeref: str | None

    @property
    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, "unknown")

    @property
    def protocol_kind(self) -> str:
        return PROTOCOL_KIND.get(self.kind, "symbol")

    @property
    def qualified(self) -> str:
        return f"{self.scope}.{self.name}" if self.scope else self.name

    @property
    def is_definition(self) -> bool:
        return self.kind in ("c", "f", "m", "v")

    def as_ctags_line(self) -> str:
        """The tag file line the real binary would write (used for index sizing)."""
        fields = [self.name, self.path, f"{self.line};\"", self.kind_name]
        fields.append(f"line:{self.line}")
        fields.append(f"end:{self.end}")
        if self.scope and self.scope_kind:
            fields.append(f"{self.scope_kind}:{self.scope}")
        if self.signature:
            fields.append(f"signature:{self.signature}")
        if self.typeref:
            fields.append(f"typeref:typename:{self.typeref}")
        return "\t".join(fields)


def _expand_indent(text: str) -> int:
    return len(text.expandtabs(4))


def _signature_params(signature: str) -> str | None:
    start = signature.find("(")
    if start == -1:
        return None
    depth = 0
    params = signature[start:]
    for index in range(start, len(signature)):
        char = signature[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                params = signature[start : index + 1]
                break
    params = re.sub(r"\(\s+", "(", params)
    params = re.sub(r",?\s+\)", ")", params)
    return params


def _split_import_names(body: str) -> list[tuple[str, str | None]]:
    """``a.b as c, d`` -> ``[('a.b', 'c'), ('d', None)]``."""
    out: list[tuple[str, str | None]] = []
    cleaned = body.split("#", 1)[0].strip().strip("()")
    for chunk in cleaned.split(","):
        piece = chunk.strip()
        if not piece or piece == "*":
            continue
        parts = piece.split()
        if len(parts) == 3 and parts[1] == "as":
            out.append((parts[0], parts[2]))
        elif len(parts) == 1:
            out.append((parts[0], None))
    return out


def _header_end(lines: tuple[str, ...], start: int) -> int:
    """Last line of a (possibly multi-line) ``def``/``class`` header."""
    depth = 0
    for number in range(start, min(len(lines), start + 60) + 1):
        row = lines[number - 1]
        depth += row.count("(") + row.count("[") - row.count(")") - row.count("]")
        if depth <= 0 and row.rstrip().endswith(":"):
            return number
    return start


def generate_tags(path: str, lines: tuple[str, ...]) -> list[Tag]:
    """Scan one Python file and return its tags, sorted by (line, name)."""
    tags: list[Tag] = []
    stack: list[tuple[int, str, str]] = []  # (indent, kind_letter, name)
    in_string: str | None = None
    skip_until = 0  # continuation lines of a multi-line definition header

    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if number <= skip_until:
            continue

        # --- triple-quoted string state (line-oriented, like the real parser) ---
        if in_string is not None:
            if in_string in line:
                in_string = None
            continue
        stripped = line.strip()
        # Blank and comment lines carry no indentation information: touching the scope
        # stack here would drop every enclosing class at the first empty line.
        if not stripped or stripped.startswith("#"):
            continue
        delimiters = _TRIPLE_RE.findall(line)
        if delimiters:
            counts = {'"""': line.count('"""'), "'''": line.count("'''")}
            for delimiter, count in counts.items():
                if count % 2 == 1:
                    in_string = delimiter
                    break
            if in_string is not None and _DEF_RE.match(line) is None:
                continue

        match = _DEF_RE.match(line)
        if match is not None:
            indent = _expand_indent(match.group("indent"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1] if stack else None
            scope = ".".join(name for _, _, name in stack) or None
            scope_kind = None
            if parent is not None:
                scope_kind = "class" if parent[1] == "c" else "function"
            is_class = match.group("kind") == "class"
            if is_class:
                kind = "c"
            elif parent is not None and parent[1] == "c":
                kind = "m"
            else:
                kind = "f"
            signature_text = read_signature(lines, number)
            tags.append(
                Tag(
                    name=match.group("name"),
                    path=path,
                    line=number,
                    end=block_end(lines, number),
                    kind=kind,
                    scope=scope,
                    scope_kind=scope_kind,
                    signature=None if is_class else _signature_params(signature_text),
                    typeref=return_annotation(signature_text),
                )
            )
            stack.append((indent, kind, match.group("name")))
            skip_until = _header_end(lines, number)
            continue

        indent = _expand_indent(line[: len(line) - len(line.lstrip())])
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1] if stack else None

        from_match = _FROM_RE.match(line)
        if from_match is not None:
            module = from_match.group("module")
            tags.append(
                Tag(module, path, number, number, "i", None, None, None, None)
            )
            for name, alias in _split_import_names(from_match.group("body")):
                tags.append(
                    Tag(alias or name, path, number, number, "x", module, "module", None, None)
                )
            continue

        import_match = _IMPORT_RE.match(line)
        if import_match is not None:
            for name, alias in _split_import_names(import_match.group("body")):
                if alias:
                    tags.append(Tag(alias, path, number, number, "I", None, None, None, name))
                else:
                    tags.append(Tag(name, path, number, number, "i", None, None, None, None))
            continue

        # Variables: module-level or class-level only (ctags' `l` kind is off by default).
        if parent is not None and parent[1] != "c":
            continue
        if parent is None and indent != 0:
            # A continuation line of a multi-line expression, not a declaration.
            continue
        assign = _ASSIGN_RE.match(line)
        if assign is not None:
            scope = ".".join(name for _, _, name in stack) or None
            scope_kind = "class" if parent is not None else None
            declared = assign.group("type")
            tags.append(
                Tag(
                    name=assign.group("name"),
                    path=path,
                    line=number,
                    end=number,
                    kind="v",
                    scope=scope,
                    scope_kind=scope_kind,
                    signature=None,
                    typeref=declared.strip() if declared else None,
                )
            )

    tags.sort(key=lambda tag: (tag.line, tag.kind, tag.name))
    return tags
