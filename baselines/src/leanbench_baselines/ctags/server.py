"""CTags baseline -- metadata beyond text, and nothing more.

Capability ceiling (deliberate): a **tag table**. Names, kinds, scopes, signatures,
line spans -- the fields a ``tags`` file carries. Everything is answered from that table:

* ``search`` matches the *tag table only* (names, scopes, signatures). It never scans file
  contents -- content search is the ripgrep rung's technique;
* ``get_context`` returns the tag record plus the definition line (for a class, its member
  tags). It is metadata lookup, not semantic assembly;
* ``get_references`` returns what a tags file can honestly offer: other definitions of the
  same name and the import tags that name it.

Not implemented, and not declared: dependencies, tests, docs.

The tag table is produced by :mod:`leanbench_baselines.ctags.tagger`, a documented
line-oriented reimplementation of universal-ctags' Python parser (see README).
"""

from __future__ import annotations

from typing import Any

from leanbench_baselines.common.payload import Item, Payload
from leanbench_baselines.common.repo import Repository
from leanbench_baselines.common.server import BaseServer, not_found
from leanbench_baselines.common.text import clean_line, query_terms, split_identifier, stems
from leanbench_baselines.ctags.tagger import Tag, generate_tags

KIND_WEIGHT: dict[str, float] = {
    "c": 1.0,
    "f": 0.95,
    "m": 0.95,
    "v": 0.6,
    "i": 0.25,
    "I": 0.25,
    "x": 0.3,
}


class CTagsServer(BaseServer):
    NAME = "CTags"
    VERSION = "0.1.0"
    CAPABILITIES = frozenset({"search", "symbols", "context", "references", "incremental"})

    def __init__(self) -> None:
        super().__init__()
        self.tags_by_file: dict[str, list[Tag]] = {}
        self.by_name: dict[str, list[Tag]] = {}
        self.by_qualified: dict[str, list[Tag]] = {}
        self.index_bytes = 0

    # -- index -----------------------------------------------------------------

    def build_index(self, repo: Repository) -> dict[str, Any]:
        self.tags_by_file = {}
        for record in repo.source_files():
            self.tags_by_file[record.path] = generate_tags(record.path, repo.lines(record.path))
        self._rebuild_lookups()
        return {
            "indexed": True,
            "symbols": sum(1 for tag in self._all_tags() if tag.is_definition),
            "index_bytes": self.index_bytes,
            "files": len(repo.files),
            "tags": len(self._all_tags()),
        }

    def reindex_paths(self, paths: list[str]) -> int:
        repo = self.require_repo()
        targets = paths or [record.path for record in repo.source_files()]
        reparsed = 0
        for path in sorted(set(targets)):
            record = repo.record(path)
            if record is None or not record.is_source:
                self.tags_by_file.pop(path, None)
                continue
            repo.invalidate(path)
            self.tags_by_file[path] = generate_tags(path, repo.lines(path))
            reparsed += 1
        self._rebuild_lookups()
        return reparsed

    def _rebuild_lookups(self) -> None:
        by_name: dict[str, list[Tag]] = {}
        by_qualified: dict[str, list[Tag]] = {}
        size = 0
        for path in sorted(self.tags_by_file):
            for tag in self.tags_by_file[path]:
                by_name.setdefault(tag.name, []).append(tag)
                by_qualified.setdefault(tag.qualified, []).append(tag)
                size += len(tag.as_ctags_line().encode("utf-8")) + 1
        for bucket in (by_name, by_qualified):
            for entries in bucket.values():
                entries.sort(key=lambda tag: (tag.path, tag.line, tag.kind))
        self.by_name = by_name
        self.by_qualified = by_qualified
        self.index_bytes = size

    def _all_tags(self) -> list[Tag]:
        out: list[Tag] = []
        for path in sorted(self.tags_by_file):
            out.extend(self.tags_by_file[path])
        return out

    def stats(self) -> dict[str, Any]:
        tags = self._all_tags()
        return {
            "symbols": sum(1 for tag in tags if tag.is_definition),
            "tags": len(tags),
            "facts": 0,
            "relationships": 0,
            "index_bytes": self.index_bytes,
            "tag_kinds": {
                kind: sum(1 for tag in tags if tag.kind == kind)
                for kind in sorted({tag.kind for tag in tags})
            },
            "ctags_implementation": "pure-python-equivalent",
        }

    # -- rendering -------------------------------------------------------------

    def _record(self, tag: Tag) -> dict[str, Any]:
        repo = self.require_repo()
        return {
            "path": tag.path,
            "symbol": tag.qualified,
            "kind": tag.protocol_kind,
            "ctags_kind": tag.kind_name,
            "signature": (tag.name + tag.signature) if tag.signature else None,
            "return_type": tag.typeref,
            "line_start": tag.line,
            "line_end": tag.end,
            "scope": tag.scope,
            "visibility": "private" if tag.name.startswith("_") else "public",
            "doc": None,
            "definition": clean_line(repo.line(tag.path, tag.line), limit=200),
        }

    @staticmethod
    def _record_text(record: dict[str, Any]) -> str:
        head = (
            f"{record['path']}:{record['line_start']}-{record['line_end']} "
            f"{record['ctags_kind']} {record['symbol']}"
        )
        if record["signature"]:
            head += f"\n  {record['signature']}"
            if record["return_type"]:
                head += f" -> {record['return_type']}"
        return head

    # -- ops -------------------------------------------------------------------

    def op_search(self, args: dict[str, Any]) -> Payload:
        query = self.arg_str(args, "query")
        limit = self.arg_int(args, "limit", 10)
        terms = query_terms(query) or [query.strip().lower()]
        keys = stems(terms)
        literal = query.strip().lower()

        scored: list[tuple[tuple[float, str, int], Tag]] = []
        for tag in self._all_tags():
            name_words = split_identifier(tag.qualified)
            word_stems = stems(name_words)
            covered = {key for key in keys if key in word_stems}
            partial = {
                key
                for key in keys
                if key not in covered and any(word.startswith(key) for word in word_stems)
            }
            signature_hits = 0
            if tag.signature or tag.typeref:
                haystack = f"{tag.signature or ''} {tag.typeref or ''}".lower()
                signature_hits = sum(1 for key in keys if key in haystack)
            exact = 1.0 if tag.name.lower() == literal or tag.qualified.lower() == literal else 0.0
            if not covered and not partial and not signature_hits and not exact:
                continue
            coverage = (len(covered) + 0.5 * len(partial)) / len(keys)
            signature_score = min(1.0, signature_hits / len(keys))
            score = round(
                0.55 * coverage
                + 0.15 * signature_score
                + 0.20 * KIND_WEIGHT.get(tag.kind, 0.3)
                + 0.30 * exact,
                4,
            )
            if score <= 0.0:
                continue
            scored.append(((-score, tag.path, tag.line), tag))

        scored.sort(key=lambda entry: entry[0])
        items: list[Item] = []
        for (neg_score, _, _), tag in scored[:limit]:
            record = self._record(tag)
            hit = {
                "path": tag.path,
                "symbol": tag.qualified,
                "kind": tag.protocol_kind,
                "line_start": tag.line,
                "line_end": tag.end,
                "score": round(-neg_score, 4),
                "snippet": record["definition"],
            }
            items.append(
                Item(
                    field="hits",
                    kind="hit",
                    data=hit,
                    text=(
                        f"{tag.path}:{tag.line} {tag.kind_name} {tag.qualified} "
                        f"score={hit['score']}\n  {record['definition']}"
                    ),
                )
            )
        return Payload(
            header={"hits": []},
            header_text=f"tags {query!r}: {len(scored)} tag(s) matched",
            items=items,
            list_fields=("hits",),
        )

    def _lookup(self, name: str) -> list[Tag]:
        tags = list(self.by_qualified.get(name, ()))
        if not tags:
            tags = list(self.by_name.get(name, ()))
        if not tags and "." in name:
            leaf = name.rsplit(".", 1)[1]
            wanted = name.rsplit(".", 1)[0].rsplit(".", 1)[-1]
            tags = [
                tag
                for tag in self.by_name.get(leaf, ())
                if tag.scope and tag.scope.split(".")[-1] == wanted
            ]
        definitions = [tag for tag in tags if tag.is_definition]
        chosen = definitions or tags
        chosen.sort(key=lambda tag: (tag.path, tag.line, tag.kind))
        return chosen

    def op_get_symbol(self, args: dict[str, Any]) -> Payload:
        name = self.arg_str(args, "name")
        tags = self._lookup(name)
        if not tags:
            raise not_found(f"no tag named '{name}'")
        items = [
            Item(
                field="symbols",
                kind="symbol",
                data=record,
                text=self._record_text(record),
            )
            for record in (self._record(tag) for tag in tags)
        ]
        return Payload(
            header={"symbols": []},
            header_text=f"get_symbol {name}: {len(items)} tag(s)",
            items=items,
            list_fields=("symbols",),
        )

    def op_get_context(self, args: dict[str, Any]) -> Payload:
        name = self.arg_str(args, "symbol")
        tags = self._lookup(name)
        if not tags:
            raise not_found(f"no tag named '{name}'")
        tag = tags[0]
        record = self._record(tag)
        header = {
            "symbol": record["symbol"],
            "path": record["path"],
            "line_start": record["line_start"],
            "line_end": record["line_end"],
            "signature": record["signature"],
            "return_type": record["return_type"],
            "kind": record["kind"],
            "scope": record["scope"],
            "definition": record["definition"],
            "members": [],
        }
        header_text = self._record_text(record) + f"\n  def: {record['definition']}"

        items: list[Item] = []
        if tag.kind == "c":
            members = [
                member
                for member in self.tags_by_file.get(tag.path, [])
                if member.scope == tag.qualified and member.is_definition
            ]
            members.sort(key=lambda member: (member.line, member.name))
            for member in members:
                data = {
                    "symbol": member.qualified,
                    "kind": member.protocol_kind,
                    "line_start": member.line,
                    "signature": (member.name + member.signature) if member.signature else None,
                    "return_type": member.typeref,
                }
                text = f"  {member.line:5d} {member.kind_name} " + (
                    data["signature"] or member.name
                )
                if member.typeref:
                    text += f" -> {member.typeref}"
                items.append(Item(field="members", kind="member", data=data, text=text))
        return Payload(
            header=header, header_text=header_text, items=items, list_fields=("members",)
        )

    def op_get_references(self, args: dict[str, Any]) -> Payload:
        name = self.arg_str(args, "symbol")
        limit = self.arg_int(args, "limit", 50)
        leaf = name.rsplit(".", 1)[-1]
        references: list[dict[str, Any]] = []
        for tag in self.by_name.get(leaf, ()):
            if tag.is_definition:
                kind, confidence = "DEFINES", 0.9
            else:
                kind, confidence = "IMPORTS", 0.6
            references.append(
                {
                    "path": tag.path,
                    "symbol": tag.qualified,
                    "line": tag.line,
                    "kind": kind,
                    "confidence": confidence,
                    "ctags_kind": tag.kind_name,
                }
            )
        # Tags whose scope is the requested symbol are references to it, too.
        for tag in self._all_tags():
            if tag.scope and tag.scope.split(".")[-1] == leaf and tag.is_definition:
                references.append(
                    {
                        "path": tag.path,
                        "symbol": tag.qualified,
                        "line": tag.line,
                        "kind": "SCOPED_IN",
                        "confidence": 0.8,
                        "ctags_kind": tag.kind_name,
                    }
                )
        references.sort(
            key=lambda ref: (-float(ref["confidence"]), str(ref["path"]), int(ref["line"]))
        )
        if not references:
            raise not_found(f"no tag references '{name}'")
        items = [
            Item(
                field="references",
                kind="reference",
                data=reference,
                text=(
                    f"{reference['path']}:{reference['line']} {reference['kind']} "
                    f"{reference['symbol']} ({reference['ctags_kind']})"
                ),
            )
            for reference in references[:limit]
        ]
        return Payload(
            header={"references": []},
            header_text=f"get_references {name}: {len(references)} tag reference(s)",
            items=items,
            list_fields=("references",),
        )
