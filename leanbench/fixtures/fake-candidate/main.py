#!/usr/bin/env python3
"""Fake candidate — a standalone PROTOCOL.md v1 server used to exercise LeanBench.

Standalone on purpose: it imports nothing from `leanbench`, so it tests the wire, not
our own objects.

    --mode      normal | slow | crash | invalid-json | wrong-protocol | false-capability
    --strategy  symbol | text     (retrieval quality: symbol-aware vs plain text match)

Mode semantics (each maps to exactly one PROTOCOL.md §7 classification):

    normal            well-behaved                       -> (no failure)
    slow              never answers in time              -> candidate_timeout
    crash             exits non-zero mid-conversation    -> candidate_crash
    invalid-json      writes a banner line on stdout     -> protocol_error
    wrong-protocol    JSON with an unknown `status`      -> invalid_response
    false-capability  `unsupported_op` for a DECLARED cap-> candidate_protocol_error

`unsupported_capability` and the manifest-level `protocol_error` are produced by the
`missing-capability` and `bad-protocol-version` manifests, which need no code path here.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from pathlib import Path

PROTOCOL_VERSION = 1
STOPWORDS = frozenset(
    """a an the of to for in on with and or is are be does do how what where when which that this
    it its as by from at into over after before than then use used using can could should""".split()
)
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
SLOW_SLEEP_S = 3600.0
CRASH_EXIT_CODE = 3
TEXT_EXTENSIONS = (".py", ".md", ".toml", ".txt", ".cfg", ".ini", ".rst")
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
NAME_WEIGHT = 3.0
PATH_WEIGHT = 1.5
DOC_WEIGHT = 1.0
FILE_HIT_WEIGHT = 0.5
DEFAULT_LIMIT = 10


def tokenize(text: str) -> list[str]:
    parts: list[str] = []
    for chunk in SPLIT_RE.split(text or ""):
        if not chunk:
            continue
        # split snake_case and CamelCase
        for piece in chunk.split("_"):
            if not piece:
                continue
            for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", piece):
                parts.append(word.lower())
    return [p for p in parts if p and p not in STOPWORDS]


class Index:
    """A tiny AST index: files, symbols, docs, tests, imports, references."""

    def __init__(self) -> None:
        self.root: Path | None = None
        self.files: list[str] = []
        self.file_text: dict[str, str] = {}
        self.symbols: list[dict] = []
        self.docs: list[dict] = []
        self.tests: list[dict] = []
        self.imports: dict[str, dict[str, list[str]]] = {}
        self.references: list[dict] = []
        self.cold_index_ms = 0.0

    # -- build ---------------------------------------------------------------

    def build(self, root: Path) -> None:
        started = time.perf_counter()
        self.__init__()  # idempotent for the same path
        self.root = root
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
                continue
            rel = path.relative_to(root).as_posix()
            if any(part in SKIP_DIRS for part in rel.split("/")):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            self.files.append(rel)
            self.file_text[rel] = text
            if path.suffix == ".py":
                self._index_python(rel, text)
            elif path.suffix in (".md", ".rst"):
                self._index_doc(rel, text)
        self.files.sort()
        self.symbols.sort(key=lambda s: (s["path"], s["line_start"], s["symbol"]))
        self.docs.sort(key=lambda d: (d["path"], d["line_start"]))
        self.tests.sort(key=lambda t: (t["path"], t["line_start"]))
        self.references.sort(key=lambda r: (r["path"], r["line"], r["symbol"]))
        self.cold_index_ms = (time.perf_counter() - started) * 1000.0

    def _index_python(self, rel: str, text: str) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return
        is_test = rel.startswith("tests/") or Path(rel).name.startswith("test_")
        local: list[str] = []
        external: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    (local if self._is_local(alias.name) else external).append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                (local if node.level or self._is_local(node.module) else external).append(
                    node.module
                )
        self.imports[rel] = {
            "imports_local": sorted(set(local)),
            "imports_external": sorted(set(external)),
        }
        self._walk_body(tree.body, rel, prefix="", is_test=is_test)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.references.append(
                    {"path": rel, "symbol": node.id, "line": node.lineno,
                     "kind": "USES_TYPE" if node.id[:1].isupper() else "CALLS",
                     "confidence": 0.9 if node.id[:1].isupper() else 0.7}
                )
            elif isinstance(node, ast.Attribute):
                self.references.append(
                    {"path": rel, "symbol": node.attr, "line": node.lineno,
                     "kind": "CALLS", "confidence": 0.6}
                )

    @staticmethod
    def _is_local(module: str) -> bool:
        return module.split(".")[0] in {"src", "shopcart", "tests"}

    def _walk_body(self, body: list, rel: str, *, prefix: str, is_test: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                name = f"{prefix}{node.name}"
                self._add_symbol(rel, name, "class", node)
                self._walk_body(node.body, rel, prefix=f"{name}.", is_test=is_test)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{node.name}"
                kind = "method" if prefix else "function"
                self._add_symbol(rel, name, kind, node)
                if is_test and node.name.startswith("test"):
                    doc = ast.get_docstring(node) or ""
                    self.tests.append(
                        {"path": rel, "symbol": name, "line_start": node.lineno,
                         "scenario": doc.strip().splitlines()[0] if doc.strip() else node.name,
                         "expects": doc.strip()}
                    )

    def _add_symbol(self, rel: str, name: str, kind: str, node: ast.AST) -> None:
        doc = ast.get_docstring(node) or ""
        signature = ""
        raises: list[str] = []
        calls: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            signature = f"{name.split('.')[-1]}({', '.join(args)})"
            for sub in ast.walk(node):
                if isinstance(sub, ast.Raise) and sub.exc is not None:
                    target = sub.exc.func if isinstance(sub.exc, ast.Call) else sub.exc
                    if isinstance(target, ast.Name):
                        raises.append(target.id)
                    elif isinstance(target, ast.Attribute):
                        raises.append(target.attr)
                elif isinstance(sub, ast.Call):
                    func = sub.func
                    if isinstance(func, ast.Attribute):
                        calls.append(func.attr)
                    elif isinstance(func, ast.Name):
                        calls.append(func.id)
        self.symbols.append(
            {
                "path": rel,
                "symbol": name,
                "kind": kind,
                "signature": signature,
                "return_type": None,
                "line_start": getattr(node, "lineno", 1),
                "line_end": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                "visibility": "private" if name.split(".")[-1].startswith("_") else "public",
                "doc": doc.strip(),
                "raises": sorted(set(raises)),
                "calls": sorted(set(calls)),
            }
        )

    def _index_doc(self, rel: str, text: str) -> None:
        heading = ""
        buffer: list[str] = []
        start = 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.startswith("#"):
                if heading or buffer:
                    self.docs.append(
                        {"path": rel, "heading": heading, "line_start": start,
                         "excerpt": " ".join(buffer)[:300]}
                    )
                heading = line.lstrip("#").strip()
                buffer = []
                start = lineno
            else:
                if line.strip():
                    buffer.append(line.strip())
        self.docs.append(
            {"path": rel, "heading": heading, "line_start": start,
             "excerpt": " ".join(buffer)[:300]}
        )

    # -- query ---------------------------------------------------------------

    def search_symbol_aware(self, query: str, limit: int) -> list[dict]:
        wanted = set(tokenize(query))
        scored: list[tuple[float, str, str, dict]] = []
        for sym in self.symbols:
            name_tokens = set(tokenize(sym["symbol"]))
            doc_tokens = set(tokenize(sym["doc"]))
            path_tokens = set(tokenize(sym["path"]))
            score = (
                NAME_WEIGHT * len(wanted & name_tokens)
                + DOC_WEIGHT * len(wanted & doc_tokens)
                + PATH_WEIGHT * len(wanted & path_tokens)
            )
            if score <= 0:
                continue
            scored.append((score, sym["path"], sym["symbol"], sym))
        hits = []
        for score, path, symbol, sym in sorted(scored, key=lambda r: (-r[0], r[1], r[2])):
            hits.append(
                {"path": path, "symbol": symbol, "kind": sym["kind"],
                 "line_start": sym["line_start"], "line_end": sym["line_end"],
                 "score": round(score, 4), "snippet": (sym["doc"] or sym["signature"])[:160]}
            )
        return hits[:limit]

    def search_text(self, query: str, limit: int) -> list[dict]:
        """Plain substring frequency over whole files. No symbols, no structure."""
        wanted = [t for t in tokenize(query)]
        scored: list[tuple[float, str]] = []
        for rel in self.files:
            lowered = self.file_text[rel].lower()
            score = 0.0
            for token in wanted:
                count = lowered.count(token)
                if count:
                    score += FILE_HIT_WEIGHT + min(count, 20) * 0.05
            if score <= 0:
                continue
            scored.append((score, rel))
        hits = []
        for score, rel in sorted(scored, key=lambda r: (-r[0], r[1])):
            hits.append(
                {"path": rel, "symbol": None, "kind": "file", "line_start": 1,
                 "line_end": len(self.file_text[rel].splitlines()) or 1,
                 "score": round(score, 4), "snippet": self.file_text[rel][:160]}
            )
        return hits[:limit]

    def find_symbols(self, name: str) -> list[dict]:
        matches = [
            s for s in self.symbols
            if s["symbol"] == name or s["symbol"].endswith("." + name)
            or s["symbol"].split(".")[-1] == name
        ]
        return sorted(matches, key=lambda s: (s["path"], s["line_start"]))


def compact_render(op: str, result: dict) -> str:
    """Token-efficient rendering used for `format: "compact"`."""
    lines: list[str] = []
    if op == "search":
        for hit in result.get("hits", []):
            label = hit["symbol"] or hit["path"]
            lines.append(f"{label} {hit['path']}:{hit['line_start']}-{hit['line_end']}")
    elif op == "get_symbol":
        for sym in result.get("symbols", []):
            lines.append(f"{sym['symbol']} {sym['path']}:{sym['line_start']} {sym['signature']}")
    elif op == "get_tests":
        for test in result.get("tests", []):
            lines.append(f"{test['path']}::{test['symbol']} {test['scenario']}")
    elif op == "get_docs":
        for doc in result.get("docs", []):
            lines.append(f"{doc['path']}#{doc['heading']}")
    elif op == "get_references":
        for ref in result.get("references", []):
            lines.append(f"{ref['symbol']} {ref['path']}:{ref['line']} {ref['kind']}")
    else:
        lines.append(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines)


class Server:
    def __init__(self, mode: str, strategy: str) -> None:
        self.mode = mode
        self.strategy = strategy
        self.index = Index()
        self.prepared = False
        self.generation = 0

    def run(self) -> int:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps({"id": "0", "status": "error", "code": "invalid_args",
                                  "message": "unparseable request"}), flush=True)
                continue
            if self.handle(request):
                return 0
        return 0

    def handle(self, request: dict) -> bool:
        req_id = str(request.get("id", "0"))
        op = request.get("op", "")
        args = request.get("args") or {}
        fmt = request.get("format", "compact")

        if op == "shutdown":
            self.emit_ok(req_id, {"ok": True}, fmt, op)
            return True

        if op != "get_stats":
            if self.mode == "slow":
                time.sleep(SLOW_SLEEP_S)
            elif self.mode == "crash":
                sys.stderr.write("fake-candidate: simulated crash\n")
                sys.stderr.flush()
                os._exit(CRASH_EXIT_CODE)
            elif self.mode == "invalid-json":
                print("fake-candidate v0 ready >>>", flush=True)
                return False
            elif self.mode == "wrong-protocol":
                print(json.dumps({"id": req_id, "status": "acknowledged", "payload": []}),
                      flush=True)
                return False
            elif self.mode == "false-capability" and op == "get_docs":
                self.emit_error(req_id, "unsupported_op", "docs are declared but not implemented")
                return False

        try:
            result = self.dispatch(op, args)
        except KeyError as exc:
            self.emit_error(req_id, "not_found", str(exc))
            return False
        except ValueError as exc:
            self.emit_error(req_id, "invalid_args", str(exc))
            return False
        if result is None:
            self.emit_error(req_id, "unsupported_op", f"op {op!r} is not implemented")
            return False
        self.emit_ok(req_id, result, fmt, op)
        return False

    def dispatch(self, op: str, args: dict):
        if op == "prepare_repository":
            path = Path(args.get("path", "")).resolve()
            if not path.is_dir():
                raise ValueError(f"no such repository: {path}")
            self.index.build(path)
            self.prepared = True
            self.generation += 1
            return {
                "indexed": True,
                "files": len(self.index.files),
                "symbols": len(self.index.symbols),
                "cold_index_ms": round(self.index.cold_index_ms, 3),
                "index_bytes": sum(len(t.encode("utf-8")) for t in self.index.file_text.values()),
            }
        if op == "get_stats":
            return {
                "files": len(self.index.files),
                "symbols": len(self.index.symbols),
                "facts": len(self.index.references),
                "relationships": len(self.index.references),
                "index_bytes": 0,
                "source_bytes": sum(len(t) for t in self.index.file_text.values()),
                "cold_index_ms": round(self.index.cold_index_ms, 3),
                "resolution_rate": {"exact": 1.0},
                "counters": {"generation": self.generation},
                "config_resolved": {"mode": self.mode, "strategy": self.strategy},
            }
        if not self.prepared:
            raise KeyError("repository not prepared")
        if op == "search":
            query = str(args.get("query", ""))
            limit = int(args.get("limit", DEFAULT_LIMIT))
            hits = (
                self.index.search_symbol_aware(query, limit)
                if self.strategy == "symbol"
                else self.index.search_text(query, limit)
            )
            return {"hits": hits}
        if op == "get_symbol":
            found = self.index.find_symbols(str(args.get("name", "")))
            return {"symbols": [
                {k: s[k] for k in ("path", "symbol", "kind", "signature", "return_type",
                                   "line_start", "line_end", "visibility", "doc")}
                for s in found
            ]}
        if op == "get_context":
            name = str(args.get("symbol", ""))
            found = self.index.find_symbols(name)
            if not found:
                raise KeyError(f"symbol {name!r} not found")
            sym = found[0]
            tests = [f"{t['path']}::{t['symbol']}" for t in self.index.tests
                     if name.split(".")[-1].lower() in t["symbol"].lower()]
            return {
                "symbol": sym["symbol"], "path": sym["path"], "line_start": sym["line_start"],
                "line_end": sym["line_end"], "signature": sym["signature"],
                "return_type": sym["return_type"], "raises": sym["raises"], "effects": [],
                "calls": sym["calls"], "notes": [], "tests": sorted(tests),
                "keywords": sorted(set(tokenize(sym["symbol"]) + tokenize(sym["doc"])))[:12],
                "budget_report": {"admitted": len(sym["calls"]), "dropped": {},
                                  "tokens_approx": 0},
            }
        if op == "get_dependencies":
            rel = str(args.get("path", ""))
            entry = self.index.imports.get(rel, {"imports_local": [], "imports_external": []})
            imported_by = sorted(
                other for other, imp in self.index.imports.items()
                if any(rel.removesuffix(".py").replace("/", ".").endswith(m)
                       for m in imp["imports_local"])
            )
            return {**entry, "imported_by": imported_by}
        if op == "get_references":
            name = str(args.get("symbol", "")).split(".")[-1]
            limit = int(args.get("limit", 50))
            refs = [r for r in self.index.references if r["symbol"] == name]
            return {"references": refs[:limit]}
        if op == "get_tests":
            name = str(args.get("symbol", "")).split(".")[-1].lower()
            tests = [t for t in self.index.tests
                     if name in t["symbol"].lower() or name in (t["expects"] or "").lower()]
            return {"tests": tests}
        if op == "get_docs":
            wanted = set(tokenize(str(args.get("query", ""))))
            limit = int(args.get("limit", 5))
            scored = []
            for doc in self.index.docs:
                tokens = set(tokenize(doc["heading"] + " " + doc["excerpt"]))
                score = len(wanted & tokens)
                if score:
                    scored.append((score, doc["path"], doc["line_start"], doc))
            ordered = [d for _s, _p, _l, d in sorted(scored, key=lambda r: (-r[0], r[1], r[2]))]
            return {"docs": ordered[:limit]}
        if op == "update_repository":
            assert self.index.root is not None
            self.index.build(self.index.root)
            self.generation += 1
            return {"updated": True, "update_ms": round(self.index.cold_index_ms, 3),
                    "files_reparsed": len(self.index.files), "generation": self.generation}
        return None

    def emit_ok(self, req_id: str, result: dict, fmt: str, op: str) -> None:
        payload = result if fmt == "json" else {"text": compact_render(op, result)}
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        print(json.dumps({
            "id": req_id, "status": "ok", "result": payload,
            "meta": {"tokens_approx": max(1, len(body) // 4), "truncated": False,
                     "index_state": "ok", "generation": self.generation, "elapsed_ms": 0.0},
        }, sort_keys=True, separators=(",", ":")), flush=True)

    def emit_error(self, req_id: str, code: str, message: str) -> None:
        print(json.dumps({"id": req_id, "status": "error", "code": code,
                          "message": message, "retryable": False},
                         sort_keys=True, separators=(",", ":")), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="LeanBench fake candidate")
    parser.add_argument(
        "--mode",
        default="normal",
        choices=["normal", "slow", "crash", "invalid-json", "wrong-protocol",
                 "missing-capability", "false-capability"],
    )
    parser.add_argument("--strategy", default="symbol", choices=["symbol", "text"])
    parser.add_argument("--protocol-version", type=int, default=PROTOCOL_VERSION)
    args = parser.parse_args()
    return Server(args.mode, args.strategy).run()


if __name__ == "__main__":
    sys.exit(main())
