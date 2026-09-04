"""SQLite + FTS5 storage.

The schema is deliberately plain: every statement here is valid `rusqlite` verbatim.
No ORM, no Python-only types, no JSON columns carrying semantics.

Product invariant 5 is enforced structurally: :meth:`Store._write_facts` is private and
:meth:`Store.insert_facts` accepts only :class:`~leanvfs.redact.Redacted`. There is no
second path from an extractor to the `facts` table.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .model import (
    Fact,
    FileRecord,
    ImportRecord,
    Relationship,
    ScoredKeyword,
    SourceRange,
    Symbol,
    UnresolvedRef,
)
from .redact import Redacted

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  id             INTEGER PRIMARY KEY,
  path           TEXT NOT NULL UNIQUE,
  language       TEXT NOT NULL,
  file_class     TEXT NOT NULL,
  byte_size      INTEGER NOT NULL,
  line_count     INTEGER NOT NULL,
  source_hash    TEXT NOT NULL,
  structure_hash TEXT NOT NULL,
  parse_state    TEXT NOT NULL,
  role           TEXT NOT NULL DEFAULT '',
  generation     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_class ON files(file_class);

CREATE TABLE IF NOT EXISTS symbols (
  id               INTEGER PRIMARY KEY,
  stable_key       TEXT NOT NULL UNIQUE,
  file_id          INTEGER NOT NULL,
  parent_symbol_id INTEGER,
  kind             TEXT NOT NULL,
  name             TEXT NOT NULL,
  qualified_name   TEXT NOT NULL,
  visibility       TEXT NOT NULL,
  signature        TEXT NOT NULL DEFAULT '',
  return_type      TEXT NOT NULL DEFAULT '',
  doc              TEXT NOT NULL DEFAULT '',
  is_async         INTEGER NOT NULL DEFAULT 0,
  is_exported      INTEGER NOT NULL DEFAULT 0,
  line_start       INTEGER NOT NULL DEFAULT 0,
  line_end         INTEGER NOT NULL DEFAULT 0,
  byte_start       INTEGER NOT NULL DEFAULT 0,
  byte_end         INTEGER NOT NULL DEFAULT 0,
  interface_hash   TEXT NOT NULL DEFAULT '',
  behavior_hash    TEXT NOT NULL DEFAULT '',
  doc_hash         TEXT NOT NULL DEFAULT '',
  metadata_hash    TEXT NOT NULL DEFAULT '',
  decorators       TEXT NOT NULL DEFAULT '',
  generation       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qual ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);

CREATE TABLE IF NOT EXISTS facts (
  id         INTEGER PRIMARY KEY,
  file_id    INTEGER NOT NULL,
  symbol_id  INTEGER,
  kind       TEXT NOT NULL,
  value      TEXT NOT NULL,
  priority   INTEGER NOT NULL,
  confidence REAL NOT NULL,
  provenance TEXT NOT NULL,
  line_start INTEGER NOT NULL DEFAULT 0,
  line_end   INTEGER NOT NULL DEFAULT 0,
  generation INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_symbol ON facts(symbol_id, kind);
CREATE INDEX IF NOT EXISTS idx_facts_file ON facts(file_id, kind);
CREATE INDEX IF NOT EXISTS idx_facts_kind ON facts(kind);

CREATE TABLE IF NOT EXISTS relationships (
  id               INTEGER PRIMARY KEY,
  kind             TEXT NOT NULL,
  source_symbol_id INTEGER NOT NULL,
  target_symbol_id INTEGER,
  target_external  TEXT NOT NULL DEFAULT '',
  confidence       REAL NOT NULL,
  tier             TEXT NOT NULL,
  source_file_id   INTEGER NOT NULL,
  line             INTEGER NOT NULL DEFAULT 0,
  generation       INTEGER NOT NULL DEFAULT 0
);
-- Forward edges only. Reverse lookups go through this index; nothing is materialized.
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_symbol_id);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_symbol_id, kind);
CREATE INDEX IF NOT EXISTS idx_rel_kind ON relationships(kind);
CREATE INDEX IF NOT EXISTS idx_rel_file ON relationships(source_file_id);

CREATE TABLE IF NOT EXISTS unresolved_refs (
  id               INTEGER PRIMARY KEY,
  name             TEXT NOT NULL,
  kind             TEXT NOT NULL,
  source_symbol_id INTEGER NOT NULL,
  source_file_id   INTEGER NOT NULL,
  line             INTEGER NOT NULL DEFAULT 0,
  receiver         TEXT NOT NULL DEFAULT '',
  alias_module     TEXT NOT NULL DEFAULT '',
  arity            INTEGER NOT NULL DEFAULT -1,
  generation       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_unres_name ON unresolved_refs(name);
CREATE INDEX IF NOT EXISTS idx_unres_source ON unresolved_refs(source_symbol_id);
CREATE INDEX IF NOT EXISTS idx_unres_file ON unresolved_refs(source_file_id);

CREATE TABLE IF NOT EXISTS keywords (
  id         INTEGER PRIMARY KEY,
  file_id    INTEGER NOT NULL,
  symbol_id  INTEGER,
  term       TEXT NOT NULL,
  score      REAL NOT NULL,
  source     TEXT NOT NULL,
  generation INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_kw_term ON keywords(term);
CREATE INDEX IF NOT EXISTS idx_kw_symbol ON keywords(symbol_id);
CREATE INDEX IF NOT EXISTS idx_kw_file ON keywords(file_id);

-- The frozen IDF snapshot. Written ONLY by a full sync. Incremental updates read it.
CREATE TABLE IF NOT EXISTS idf (
  term           TEXT NOT NULL,
  df             INTEGER NOT NULL,
  idf            REAL NOT NULL,
  idf_generation INTEGER NOT NULL,
  PRIMARY KEY (term, idf_generation)
);

CREATE TABLE IF NOT EXISTS imports (
  id              INTEGER PRIMARY KEY,
  file_id         INTEGER NOT NULL,
  module          TEXT NOT NULL,
  alias           TEXT NOT NULL DEFAULT '',
  names           TEXT NOT NULL DEFAULT '',
  is_relative     INTEGER NOT NULL DEFAULT 0,
  level           INTEGER NOT NULL DEFAULT 0,
  line            INTEGER NOT NULL DEFAULT 0,
  resolved_file_id INTEGER,
  is_local        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports(module);

CREATE TABLE IF NOT EXISTS counters (
  name  TEXT PRIMARY KEY,
  value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS durations (
  name    TEXT PRIMARY KEY,
  seconds REAL NOT NULL DEFAULT 0.0
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
  path, symbol, qualified, signature, keywords, facts, role, klass,
  doc_kind UNINDEXED, doc_id UNINDEXED,
  tokenize = 'unicode61 remove_diacritics 2'
);
"""


class Store:
    def __init__(self, db_path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        if not read_only:
            self.conn.executescript(SCHEMA)
            self.conn.executescript(FTS_SCHEMA)
            self.set_meta("schema_version", str(SCHEMA_VERSION))

    # -- lifecycle ------------------------------------------------------
    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):  # closing twice must not raise
            self.conn.close()

    def begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.conn.execute("COMMIT")
        self.checkpoint()

    def checkpoint(self) -> None:
        """Fold the write-ahead log back into the database file.

        WAL mode is right for us -- readers never block the indexer -- but an
        un-checkpointed WAL retains every page written during the session. On a small
        repository that made the reported index 4x the actual data, which is both wasted
        disk and a misleading number.
        """
        with contextlib.suppress(sqlite3.Error):
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def rollback(self) -> None:
        with contextlib.suppress(sqlite3.Error):  # no active transaction is fine
            self.conn.execute("ROLLBACK")

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    # -- meta -----------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def generation(self) -> int:
        return int(self.get_meta("generation", "0") or 0)

    def bump_generation(self) -> int:
        gen = self.generation() + 1
        self.set_meta("generation", str(gen))
        return gen

    def idf_generation(self) -> int:
        return int(self.get_meta("idf_generation", "0") or 0)

    def index_state(self) -> str:
        return self.get_meta("index_state", "ok") or "ok"

    def set_index_state(self, state: str) -> None:
        self.set_meta("index_state", state)

    # -- counters -------------------------------------------------------
    def incr(self, name: str, amount: int = 1) -> None:
        self.conn.execute(
            "INSERT INTO counters(name, value) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
            (name, amount),
        )

    def add_duration(self, name: str, seconds: float) -> None:
        self.conn.execute(
            "INSERT INTO durations(name, seconds) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET seconds=seconds+excluded.seconds",
            (name, seconds),
        )

    def counters(self) -> dict[str, int]:
        return {
            r["name"]: r["value"]
            for r in self.conn.execute("SELECT name, value FROM counters ORDER BY name")
        }

    def durations(self) -> dict[str, float]:
        return {
            r["name"]: round(r["seconds"], 6)
            for r in self.conn.execute("SELECT name, seconds FROM durations ORDER BY name")
        }

    # -- files ----------------------------------------------------------
    def upsert_file(self, rec: FileRecord, generation: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO files(path, language, file_class, byte_size, line_count, "
            "source_hash, structure_hash, parse_state, role, generation) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET language=excluded.language, "
            "file_class=excluded.file_class, byte_size=excluded.byte_size, "
            "line_count=excluded.line_count, source_hash=excluded.source_hash, "
            "structure_hash=excluded.structure_hash, parse_state=excluded.parse_state, "
            "role=excluded.role, generation=excluded.generation",
            (
                rec.path,
                rec.language,
                rec.file_class,
                rec.byte_size,
                rec.line_count,
                rec.source_hash,
                rec.structure_hash,
                rec.parse_state,
                rec.role,
                generation,
            ),
        )
        if cur.lastrowid:
            row = self.conn.execute("SELECT id FROM files WHERE path=?", (rec.path,)).fetchone()
            return int(row["id"])
        row = self.conn.execute("SELECT id FROM files WHERE path=?", (rec.path,)).fetchone()
        return int(row["id"])

    def file_by_path(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def all_files(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM files ORDER BY path"))

    def file_hashes(self) -> dict[str, tuple[int, str, str]]:
        return {
            r["path"]: (r["id"], r["source_hash"], r["structure_hash"])
            for r in self.conn.execute("SELECT id, path, source_hash, structure_hash FROM files")
        }

    def delete_file(self, file_id: int) -> None:
        # Drop the file's symbol documents from FTS *before* the symbol rows go, while
        # their ids are still readable. Omitting this left a deleted file's symbols
        # searchable: the query returned hits pointing at a path that no longer exists,
        # and an agent would follow them into a failed read.
        for row in self.conn.execute("SELECT id FROM symbols WHERE file_id=?", (file_id,)):
            self.conn.execute(
                "DELETE FROM search_fts WHERE doc_kind='symbol' AND doc_id=?", (int(row["id"]),)
            )
        # Edges pointing INTO this file's symbols would otherwise dangle.
        self.conn.execute(
            "DELETE FROM relationships WHERE target_symbol_id IN "
            "(SELECT id FROM symbols WHERE file_id=?)",
            (file_id,),
        )
        for table in ("symbols", "facts", "keywords", "imports"):
            self.conn.execute(f"DELETE FROM {table} WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM relationships WHERE source_file_id=?", (file_id,))
        self.conn.execute("DELETE FROM unresolved_refs WHERE source_file_id=?", (file_id,))
        self.conn.execute("DELETE FROM search_fts WHERE doc_kind='file' AND doc_id=?", (file_id,))
        self.conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    def clear_file_derived_keep_symbols(self, file_id: int) -> None:
        """Drop a file's derived rows but KEEP its symbol rows.

        Symbol identity is `stable_key`, which is deliberately not line-dependent, and
        `insert_symbols` upserts on it. Deleting the symbol rows first would hand every
        re-parsed symbol a fresh id while edges *from other files* still referenced the
        old one -- so a one-file edit would silently dangle inbound relationships across
        the repository. Keeping the rows lets the upsert preserve ids, and symbols that
        genuinely disappeared are removed afterwards by `prune_symbols`.
        """
        for table in ("facts", "keywords", "imports"):
            self.conn.execute(f"DELETE FROM {table} WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM relationships WHERE source_file_id=?", (file_id,))
        self.conn.execute("DELETE FROM unresolved_refs WHERE source_file_id=?", (file_id,))
        rows = self.conn.execute("SELECT id FROM symbols WHERE file_id=?", (file_id,)).fetchall()
        for r in rows:
            self.conn.execute(
                "DELETE FROM search_fts WHERE doc_kind='symbol' AND doc_id=?", (int(r["id"]),)
            )
        self.conn.execute("DELETE FROM search_fts WHERE doc_kind='file' AND doc_id=?", (file_id,))

    def files_referencing(self, file_id: int) -> list[str]:
        """Paths whose relationships point INTO this file.

        This is the "resolution fan-in" the bounded-work invariant explicitly allows: a
        deleted file dangles references held by other files, and those files -- and only
        those -- must be re-resolved. It is a bounded index lookup, never a repo scan.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT f.path AS path FROM relationships r "
            "JOIN symbols t ON t.id = r.target_symbol_id "
            "JOIN files f ON f.id = r.source_file_id "
            "WHERE t.file_id = ? AND r.source_file_id != ? ORDER BY f.path",
            (file_id, file_id),
        )
        return [r["path"] for r in rows]

    def prune_symbols(self, file_id: int, keep_keys: Sequence[str]) -> list[int]:
        """Delete symbols of a file that the new parse no longer produces.

        Inbound edges to a removed symbol are dropped rather than left pointing at a row
        that no longer exists; they are re-resolved from `unresolved_refs` on a later
        sync if a matching symbol reappears.
        """
        keep = set(keep_keys)
        removed: list[int] = []
        rows = self.conn.execute(
            "SELECT id, stable_key FROM symbols WHERE file_id=?", (file_id,)
        ).fetchall()
        for row in rows:
            if row["stable_key"] in keep:
                continue
            sid = int(row["id"])
            removed.append(sid)
            self.conn.execute("DELETE FROM relationships WHERE target_symbol_id=?", (sid,))
            self.conn.execute("DELETE FROM facts WHERE symbol_id=?", (sid,))
            self.conn.execute("DELETE FROM keywords WHERE symbol_id=?", (sid,))
            self.conn.execute("DELETE FROM search_fts WHERE doc_kind='symbol' AND doc_id=?", (sid,))
            self.conn.execute("DELETE FROM symbols WHERE id=?", (sid,))
        return removed

    def clear_file_derived(self, file_id: int) -> None:
        """Drop everything derived from one file, keeping the file row itself."""
        rows = self.conn.execute("SELECT id FROM symbols WHERE file_id=?", (file_id,)).fetchall()
        sym_ids = [int(r["id"]) for r in rows]
        for table in ("facts", "keywords", "imports"):
            self.conn.execute(f"DELETE FROM {table} WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM relationships WHERE source_file_id=?", (file_id,))
        self.conn.execute("DELETE FROM unresolved_refs WHERE source_file_id=?", (file_id,))
        self.conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
        for sid in sym_ids:
            self.conn.execute("DELETE FROM search_fts WHERE doc_kind='symbol' AND doc_id=?", (sid,))
        self.conn.execute("DELETE FROM search_fts WHERE doc_kind='file' AND doc_id=?", (file_id,))

    # -- symbols --------------------------------------------------------
    def insert_symbols(
        self, file_id: int, symbols: Sequence[Symbol], generation: int
    ) -> dict[str, int]:
        out: dict[str, int] = {}
        for sym in symbols:
            cur = self.conn.execute(
                "INSERT INTO symbols(stable_key, file_id, parent_symbol_id, kind, name, "
                "qualified_name, visibility, signature, return_type, doc, is_async, "
                "is_exported, line_start, line_end, byte_start, byte_end, interface_hash, "
                "behavior_hash, doc_hash, metadata_hash, decorators, generation) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(stable_key) DO UPDATE SET file_id=excluded.file_id, "
                "parent_symbol_id=excluded.parent_symbol_id, kind=excluded.kind, "
                "name=excluded.name, qualified_name=excluded.qualified_name, "
                "visibility=excluded.visibility, signature=excluded.signature, "
                "return_type=excluded.return_type, doc=excluded.doc, "
                "is_async=excluded.is_async, is_exported=excluded.is_exported, "
                "line_start=excluded.line_start, line_end=excluded.line_end, "
                "byte_start=excluded.byte_start, byte_end=excluded.byte_end, "
                "interface_hash=excluded.interface_hash, behavior_hash=excluded.behavior_hash, "
                "doc_hash=excluded.doc_hash, metadata_hash=excluded.metadata_hash, "
                "decorators=excluded.decorators, generation=excluded.generation",
                (
                    sym.stable_key,
                    file_id,
                    None,
                    sym.kind,
                    sym.name,
                    sym.qualified_name,
                    sym.visibility,
                    sym.signature,
                    sym.return_type,
                    sym.doc,
                    1 if sym.is_async else 0,
                    1 if sym.is_exported else 0,
                    sym.range.line_start,
                    sym.range.line_end,
                    sym.range.byte_start,
                    sym.range.byte_end,
                    sym.interface_hash,
                    sym.behavior_hash,
                    sym.doc_hash,
                    sym.metadata_hash,
                    ",".join(sym.decorators),
                    generation,
                ),
            )
            del cur
            row = self.conn.execute(
                "SELECT id FROM symbols WHERE stable_key=?", (sym.stable_key,)
            ).fetchone()
            out[sym.stable_key] = int(row["id"])
        # second pass: parent links, now that all ids exist
        for sym in symbols:
            if sym.parent_key and sym.parent_key in out:
                self.conn.execute(
                    "UPDATE symbols SET parent_symbol_id=? WHERE id=?",
                    (out[sym.parent_key], out[sym.stable_key]),
                )
        return out

    def symbol_ids_by_key(self, keys: Iterable[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        keys = list(keys)
        for i in range(0, len(keys), 400):
            chunk = keys[i : i + 400]
            q = ",".join("?" * len(chunk))
            for r in self.conn.execute(
                f"SELECT id, stable_key FROM symbols WHERE stable_key IN ({q})", chunk
            ):
                out[r["stable_key"]] = int(r["id"])
        return out

    def all_symbols(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT s.*, f.path AS path, f.file_class AS file_class "
                "FROM symbols s JOIN files f ON f.id = s.file_id ORDER BY s.stable_key"
            )
        )

    def symbol_by_key(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT s.*, f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.stable_key=?",
            (key,),
        ).fetchone()

    def symbol_by_id(self, sid: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT s.*, f.path AS path, f.file_class AS file_class "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.id=?",
            (sid,),
        ).fetchone()

    def symbols_by_name(self, name: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT s.*, f.path AS path, f.file_class AS file_class "
                "FROM symbols s JOIN files f ON f.id = s.file_id "
                "WHERE s.name=? OR s.qualified_name=? ORDER BY s.stable_key",
                (name, name),
            )
        )

    # -- facts (private write path) --------------------------------------
    def _write_facts(self, rows: Sequence[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO facts(file_id, symbol_id, kind, value, priority, confidence, "
            "provenance, line_start, line_end, generation) VALUES(?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

    def insert_facts(
        self,
        items: Sequence[Redacted],
        file_id: int,
        symbol_ids: dict[str, int],
        generation: int,
    ) -> int:
        """The ONLY public fact-insert path. It accepts nothing but `Redacted`."""
        rows = []
        for item in items:
            if not isinstance(item, Redacted):
                raise TypeError(
                    "insert_facts requires Redacted values; route facts through Redactor.redact()"
                )
            f: Fact = item.fact
            rows.append(
                (
                    file_id,
                    symbol_ids.get(f.symbol_key or "") if f.symbol_key else None,
                    f.kind,
                    f.value,
                    f.priority,
                    f.confidence,
                    f.provenance,
                    f.range.line_start,
                    f.range.line_end,
                    generation,
                )
            )
        self._write_facts(rows)
        return len(rows)

    def facts_for_symbol(self, symbol_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM facts WHERE symbol_id=? ORDER BY kind, value", (symbol_id,)
            )
        )

    def facts_for_file(self, file_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM facts WHERE file_id=? ORDER BY COALESCE(symbol_id,0), kind, value",
                (file_id,),
            )
        )

    # -- keywords -------------------------------------------------------
    def insert_keywords(
        self,
        file_id: int,
        items: Sequence[ScoredKeyword],
        symbol_ids: dict[str, int],
        generation: int,
    ) -> None:
        self.conn.executemany(
            "INSERT INTO keywords(file_id, symbol_id, term, score, source, generation) "
            "VALUES(?,?,?,?,?,?)",
            [
                (
                    file_id,
                    symbol_ids.get(k.symbol_key or "") if k.symbol_key else None,
                    k.term,
                    k.score,
                    k.source,
                    generation,
                )
                for k in items
            ],
        )

    def keywords_for_symbol(self, symbol_id: int, limit: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT term, score FROM keywords WHERE symbol_id=? "
                "ORDER BY score DESC, term ASC LIMIT ?",
                (symbol_id, limit),
            )
        )

    def keywords_for_file(self, file_id: int, limit: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT term, score FROM keywords WHERE file_id=? AND symbol_id IS NULL "
                "ORDER BY score DESC, term ASC LIMIT ?",
                (file_id, limit),
            )
        )

    # -- idf ------------------------------------------------------------
    def write_idf(self, entries: Sequence[tuple[str, int, float]], idf_generation: int) -> None:
        self.conn.execute("DELETE FROM idf WHERE idf_generation=?", (idf_generation,))
        self.conn.executemany(
            "INSERT INTO idf(term, df, idf, idf_generation) VALUES(?,?,?,?)",
            [(t, df, v, idf_generation) for t, df, v in entries],
        )

    def read_idf(self, idf_generation: int) -> dict[str, float]:
        return {
            r["term"]: r["idf"]
            for r in self.conn.execute(
                "SELECT term, idf FROM idf WHERE idf_generation=?", (idf_generation,)
            )
        }

    # -- imports --------------------------------------------------------
    def insert_imports(self, file_id: int, imports: Sequence[ImportRecord]) -> None:
        self.conn.executemany(
            "INSERT INTO imports(file_id, module, alias, names, is_relative, level, line, "
            "resolved_file_id, is_local) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    file_id,
                    i.module,
                    i.alias,
                    ",".join(i.names),
                    1 if i.is_relative else 0,
                    i.level,
                    i.line,
                    None,
                    1 if i.is_local else 0,
                )
                for i in imports
            ],
        )

    def imports_for_file(self, file_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM imports WHERE file_id=? ORDER BY module, alias, line", (file_id,)
            )
        )

    # -- relationships ---------------------------------------------------
    def insert_relationships(self, rows: Sequence[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO relationships(kind, source_symbol_id, target_symbol_id, "
            "target_external, confidence, tier, source_file_id, line, generation) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            rows,
        )

    def insert_unresolved(self, rows: Sequence[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO unresolved_refs(name, kind, source_symbol_id, source_file_id, "
            "line, receiver, alias_module, arity, generation) VALUES(?,?,?,?,?,?,?,?,?)",
            rows,
        )

    def unresolved_by_name(self, names: Sequence[str]) -> list[sqlite3.Row]:
        """Fan-in bounded by the name index — never a repo scan."""
        out: list[sqlite3.Row] = []
        names = list(dict.fromkeys(names))
        for i in range(0, len(names), 400):
            chunk = names[i : i + 400]
            q = ",".join("?" * len(chunk))
            out.extend(
                self.conn.execute(
                    f"SELECT * FROM unresolved_refs WHERE name IN ({q}) ORDER BY id", chunk
                )
            )
        return out

    def relationships_to(self, target_symbol_id: int, kinds: Sequence[str]) -> list[sqlite3.Row]:
        q = ",".join("?" * len(kinds))
        return list(
            self.conn.execute(
                f"SELECT r.*, s.stable_key AS source_key, s.name AS source_name, "
                f"s.qualified_name AS source_qual, s.kind AS source_kind, f.path AS source_path, "
                f"f.file_class AS source_class "
                f"FROM relationships r JOIN symbols s ON s.id = r.source_symbol_id "
                f"JOIN files f ON f.id = s.file_id "
                f"WHERE r.target_symbol_id=? AND r.kind IN ({q}) "
                f"ORDER BY s.stable_key, r.kind, r.line",
                [target_symbol_id, *kinds],
            )
        )

    def relationships_from(
        self, source_symbol_id: int, kinds: Sequence[str] | None = None
    ) -> list[sqlite3.Row]:
        if kinds:
            q = ",".join("?" * len(kinds))
            sql = (
                f"SELECT * FROM relationships WHERE source_symbol_id=? AND kind IN ({q}) "
                f"ORDER BY kind, COALESCE(target_symbol_id, 0), target_external"
            )
            return list(self.conn.execute(sql, [source_symbol_id, *kinds]))
        return list(
            self.conn.execute(
                "SELECT * FROM relationships WHERE source_symbol_id=? "
                "ORDER BY kind, COALESCE(target_symbol_id, 0), target_external",
                (source_symbol_id,),
            )
        )

    def tier_counts(self, kind: str | None = None) -> dict[str, int]:
        if kind:
            rows = self.conn.execute(
                "SELECT tier, COUNT(*) c FROM relationships WHERE kind=? GROUP BY tier", (kind,)
            )
        else:
            rows = self.conn.execute("SELECT tier, COUNT(*) c FROM relationships GROUP BY tier")
        return {r["tier"]: int(r["c"]) for r in rows}

    # -- fts ------------------------------------------------------------
    def index_search_doc(
        self,
        doc_kind: str,
        doc_id: int,
        path: str,
        symbol: str,
        qualified: str,
        signature: str,
        keywords: str,
        facts: str,
        role: str,
        klass: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO search_fts(path, symbol, qualified, signature, keywords, facts, "
            "role, klass, doc_kind, doc_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (path, symbol, qualified, signature, keywords, facts, role, klass, doc_kind, doc_id),
        )

    def fts_search(self, match: str, limit: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT doc_kind, doc_id, path, symbol, qualified, signature, keywords, "
                "role, klass, bm25(search_fts) AS rank FROM search_fts "
                "WHERE search_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            )
        )

    # -- aggregate counts -------------------------------------------------
    def count(self, table: str) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()
        return int(row["c"])

    def text_columns(self) -> list[tuple[str, str]]:
        """Every (table, column) that can hold text — used by the secrets test."""
        out: list[tuple[str, str]] = []
        for t in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ):
            table = t["name"]
            if table.startswith(("sqlite_", "search_fts_")):
                continue
            try:
                cols = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            except sqlite3.Error:
                continue
            for c in cols:
                out.append((table, c["name"]))
        return out


def source_range_from_row(row: Any) -> SourceRange:
    return SourceRange(row["line_start"], row["line_end"], row["byte_start"], row["byte_end"])


def row_to_relationship(row: Any) -> Relationship:
    return Relationship(
        kind=row["kind"],
        source_symbol_key=str(row["source_symbol_id"]),
        target_symbol_key=str(row["target_symbol_id"]) if row["target_symbol_id"] else None,
        target_external=row["target_external"],
        confidence=row["confidence"],
        tier=row["tier"],
        line=row["line"],
    )


def row_to_unresolved(row: Any) -> UnresolvedRef:
    return UnresolvedRef(
        name=row["name"],
        kind=row["kind"],
        source_symbol_key=str(row["source_symbol_id"]),
        source_file="",
        line=row["line"],
        receiver=row["receiver"],
        alias_module=row["alias_module"],
        arity=row["arity"],
    )
