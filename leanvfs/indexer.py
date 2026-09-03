"""The pipeline: discover -> classify -> hash -> extract -> canonical model ->
resolve -> keywords/IDF -> budget -> SQLite + FTS5.

Ordering discipline (product invariant 7): files may be *parsed* in parallel, but every
ordering-sensitive step is sequential and index-ordered. Extractions are buffered per
file and merged in sorted path order; ids are assigned only after the merge. No step
below iterates a dict or a set in a way that reaches output without an explicit sort.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import ExtractionContext, select_adapter
from .adapters.tokens import structure_hash_generic
from .discover import Discovered, discover, line_count, read_source
from .hashing import digest_bytes_
from .keywords import (
    GlobalStatsPermit,
    IdfSnapshot,
    compute_idf,
    default_idf,
    file_level_candidates,
    normalize_terms,
)
from .keywords import score as score_keywords
from .model import FileExtraction, FileRecord, KeywordCandidate
from .redact import Redactor
from .registry import FactRegistry
from .resolve import Resolver, tier_rates
from .state import IndexState, save_state, write_resolved_config
from .store import Store
from .symbolindex import SqlSymbolIndex
from .telemetry import Telemetry


@dataclass
class IndexResult:
    files: int = 0
    symbols: int = 0
    facts: int = 0
    relationships: int = 0
    unresolved: int = 0
    duration_s: float = 0.0
    generation: int = 0
    idf_generation: int = 0
    source_bytes: int = 0
    skipped: int = 0
    reparsed: int = 0
    tier_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "symbols": self.symbols,
            "facts": self.facts,
            "relationships": self.relationships,
            "unresolved": self.unresolved,
            "duration_s": round(self.duration_s, 4),
            "generation": self.generation,
            "idf_generation": self.idf_generation,
            "source_bytes": self.source_bytes,
            "skipped": self.skipped,
            "reparsed": self.reparsed,
            "resolution_rate": tier_rates(self.tier_counts),
        }


class Indexer:
    """Owns the write path. The query side never goes through here."""

    def __init__(self, repo_root: Path, store: Store, cfg: Any, *, state_dir: Path | None = None):
        self.repo_root = Path(repo_root).resolve()
        self.store = store
        self.cfg = cfg
        self.state_dir = state_dir
        self.registry = FactRegistry(cfg)
        self.redactor = Redactor(cfg)
        self.telemetry = Telemetry()

    # -- extraction ---------------------------------------------------------------

    def _extract_one(self, disc: Discovered) -> FileExtraction | None:
        """Pure per-file work. Safe to run in parallel: touches no shared mutable state
        and never writes to the store."""
        max_bytes = int(self.cfg.get("discovery.max_file_bytes", 2 << 20))
        try:
            raw, text, encoding = read_source(disc.abs_path, max_bytes)
        except OSError as exc:
            # A file that vanished or became unreadable mid-parse is a real, expected
            # event on a live tree — it is recorded, never swallowed.
            rec = FileRecord(
                path=disc.rel_path,
                language=disc.language,
                file_class=disc.file_class,
                parse_state="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            ext = FileExtraction(file=rec)
            ext.diagnostics.append(f"read_error: {exc}")
            self.telemetry.incr("parse_failed")
            return ext

        rec = FileRecord(
            path=disc.rel_path,
            language=disc.language,
            file_class=disc.file_class,
            byte_size=len(raw),
            line_count=line_count(text),
            source_hash=digest_bytes_(raw),
            encoding=encoding,
        )
        if encoding == "oversized" or disc.is_binary:
            rec.parse_state = "ok"
            rec.structure_hash = digest_bytes_(raw)
            ext = FileExtraction(file=rec)
            return ext

        policy = dict(self.cfg.for_path(disc.rel_path).section("extraction") or {})
        ctx = ExtractionContext(
            rel_path=disc.rel_path,
            source=raw,
            text=text,
            file=rec,
            registry=self.registry,
            policy=policy,
        )
        adapter = select_adapter(disc.language, disc.file_class)
        try:
            ext = adapter.extract(ctx)
        except (ValueError, RuntimeError, AttributeError, IndexError, KeyError) as exc:
            # Extraction failure keeps the file in the index with honest metadata and a
            # `failed` parse state, which propagates to `index_state` on every response
            # derived from it (product invariant 10). It is never silently dropped.
            rec.parse_state = "failed"
            rec.error = f"{type(exc).__name__}: {exc}"
            ext = FileExtraction(file=rec)
            ext.diagnostics.append(f"extract_error: {exc}")
            self.telemetry.incr("parse_failed")
            return ext
        if not rec.structure_hash:
            rec.structure_hash = structure_hash_generic(text)
        self.telemetry.incr("parse_full")
        return ext

    def _extract_all(self, discovered: list[Discovered]) -> list[FileExtraction]:
        workers = max(1, int(self.cfg.get("general.workers", 4)))
        if workers == 1:
            results = [self._extract_one(d) for d in discovered]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(self._extract_one, discovered))
        # The merge point: sorted by path, so thread scheduling cannot reach output.
        out = [r for r in results if r is not None]
        out.sort(key=lambda e: e.file.path)
        for ext in out:
            ext.canonicalize()
        return out

    # -- full sync ----------------------------------------------------------------

    def full_sync(self) -> IndexResult:
        """Clean index of the whole repository.

        This is the ONLY place a `GlobalStatsPermit` is minted: IDF is a corpus-wide
        statistic, so it may only be computed here and is thereafter frozen under an
        `idf_generation`. Incremental updates read the snapshot and can never recompute
        it — which is what makes "incremental converges to clean" testable rather than
        aspirational (docs/equivalence.md, ADR on the IDF fix).
        """
        started = time.perf_counter()
        result = IndexResult()

        with self.telemetry.timer("discovery"):
            discovered = discover(self.repo_root, self.cfg)
        self.telemetry.incr("files_discovered", len(discovered))

        with self.telemetry.timer("parsing"):
            extractions = self._extract_all(discovered)

        result.source_bytes = sum(e.file.byte_size for e in extractions)

        # --- IDF: computed once, here, under a permit -----------------------------
        with self.telemetry.timer("keywords"):
            doc_term_sets: list[set[str]] = []
            for ext in extractions:
                terms = normalize_terms((c.term for c in ext.keyword_candidates), self.cfg)
                doc_term_sets.append(set(terms))
            permit = GlobalStatsPermit("full_sync")
            entries, doc_count = compute_idf(doc_term_sets, permit)
            idf_generation = self.store.idf_generation() + 1
            self.store.write_idf(entries, idf_generation)
            snapshot = IdfSnapshot(
                generation=idf_generation,
                doc_count=doc_count,
                values={term: value for term, _df, value in entries},
                default=default_idf(doc_count),
            )

        generation = self.store.generation() + 1
        self.store.begin()
        try:
            self._write_files(extractions, snapshot, generation, result)
            self._resolve_and_write(extractions, generation, result)
            self.store.set_meta("idf_generation", str(idf_generation))
            self.store.set_meta("generation", str(generation))
            self.store.set_meta("doc_count", str(doc_count))
            self.store.set_index_state("ok")
            self.telemetry.flush(self.store)
            self.store.commit()
        except Exception:
            self.store.rollback()
            raise

        result.generation = generation
        result.idf_generation = idf_generation
        result.duration_s = time.perf_counter() - started

        if self.state_dir is not None:
            save_state(
                self.state_dir,
                IndexState(
                    repo_root=str(self.repo_root),
                    generation=generation,
                    idf_generation=idf_generation,
                    idf_doc_count=doc_count,
                    files_added_since_idf=0,
                    files_removed_since_idf=0,
                    config_digest=self.cfg.digest(),
                    last_sync_kind="full",
                    file_count=result.files,
                    symbol_count=result.symbols,
                    source_bytes=result.source_bytes,
                    cold_index_ms=round(result.duration_s * 1000.0, 3),
                ),
            )
            write_resolved_config(self.state_dir, self.cfg)
        return result

    # -- write path ---------------------------------------------------------------

    def _write_files(
        self,
        extractions: list[FileExtraction],
        snapshot: IdfSnapshot,
        generation: int,
        result: IndexResult,
    ) -> None:
        for ext in extractions:
            file_id = self.store.upsert_file(ext.file, generation)
            ext.file.id = file_id
            symbol_ids = self.store.insert_symbols(file_id, ext.symbols, generation)

            # Facts reach SQLite through exactly one choke point (product invariant 5).
            redacted = self.redactor.redact_all(ext.facts)
            secrets = sum(1 for r in redacted if r.was_redacted)
            if secrets:
                self.telemetry.incr("secrets_redacted", secrets)
            result.facts += self.store.insert_facts(redacted, file_id, symbol_ids, generation)

            if ext.keyword_candidates:
                candidates = list(ext.keyword_candidates)
                candidates.extend(file_level_candidates(candidates))
                normalized: list[KeywordCandidate] = []
                for cand in candidates:
                    for term in normalize_terms([cand.term], self.cfg):
                        normalized.append(
                            KeywordCandidate(
                                term=term,
                                source=cand.source,
                                symbol_key=cand.symbol_key,
                                file_path=cand.file_path or ext.file.path,
                            )
                        )
                scored = score_keywords(normalized, snapshot, self.cfg)
                self.store.insert_keywords(file_id, scored, symbol_ids, generation)
                ext.scored_keywords = scored  # type: ignore[attr-defined]

            if ext.imports:
                self.store.insert_imports(file_id, ext.imports)

            result.files += 1
            result.symbols += len(ext.symbols)

    def _resolve_and_write(
        self, extractions: list[FileExtraction], generation: int, result: IndexResult
    ) -> None:
        """Phase B: resolve every reference against the *complete* symbol table."""
        with self.telemetry.timer("resolution"):
            index = SqlSymbolIndex(self.store.conn)
            resolver = Resolver(index, self.cfg)

            key_to_id = self.store.symbol_ids_by_key(
                [s.stable_key for e in extractions for s in e.symbols]
            )
            file_ids = {e.file.path: e.file.id for e in extractions}

            batch: list[tuple[Any, int, int]] = []
            for ext in extractions:
                fid = file_ids.get(ext.file.path, 0)
                for ref in ext.refs:
                    sid = key_to_id.get(ref.source_symbol_key, 0)
                    if sid:
                        batch.append((ref, sid, fid))

            edges, pending = resolver.resolve_batch(batch)

            self.store.insert_relationships(
                [
                    (
                        e.kind,
                        e.source_symbol_id,
                        e.target_symbol_id,
                        e.target_external,
                        e.confidence,
                        e.tier,
                        e.source_file_id,
                        e.line,
                        generation,
                    )
                    for e in edges
                ]
            )
            # R4 references are still emitted as edges above; `unresolved_refs` is the
            # name-indexed table that makes incremental re-resolution bounded (§9.3).
            self.store.insert_unresolved(
                [
                    (
                        ref.name,
                        ref.kind,
                        sid,
                        fid,
                        ref.line,
                        ref.receiver,
                        ref.alias_module,
                        ref.arity,
                        generation,
                    )
                    for ref, sid, fid in pending
                ]
            )
            result.relationships = len(edges)
            result.unresolved = len(pending)
            counts: dict[str, int] = {}
            for edge in edges:
                counts[edge.tier] = counts.get(edge.tier, 0) + 1
            result.tier_counts = counts
            for tier, count in sorted(counts.items()):
                self.telemetry.incr(f"relationships_by_tier_{tier}", count)
            self.telemetry.incr("relationships_created", len(edges))
            self.telemetry.incr("unresolved_refs_pending", len(pending))

        with self.telemetry.timer("fts"):
            self._index_search_docs(extractions)

    def _index_search_docs(self, extractions: list[FileExtraction]) -> None:
        """Build the FTS5 documents. One per file, one per symbol."""
        for ext in extractions:
            fid = ext.file.id
            scored = getattr(ext, "scored_keywords", [])
            file_kw = " ".join(sorted({k.term for k in scored if k.symbol_key is None}))
            file_facts = " ".join(sorted({f.value for f in ext.facts if f.symbol_key is None}))
            self.store.index_search_doc(
                "file",
                fid,
                ext.file.path,
                "",
                "",
                "",
                file_kw,
                file_facts,
                ext.file.role,
                ext.file.file_class,
            )
            by_symbol: dict[str, list[str]] = {}
            for k in scored:
                if k.symbol_key:
                    by_symbol.setdefault(k.symbol_key, []).append(k.term)
            facts_by_symbol: dict[str, list[str]] = {}
            for f in ext.facts:
                if f.symbol_key:
                    facts_by_symbol.setdefault(f.symbol_key, []).append(f.value)
            ids = self.store.symbol_ids_by_key([s.stable_key for s in ext.symbols])
            for sym in ext.symbols:
                sid = ids.get(sym.stable_key)
                if not sid:
                    continue
                self.store.index_search_doc(
                    "symbol",
                    sid,
                    ext.file.path,
                    sym.name,
                    sym.qualified_name,
                    sym.signature,
                    " ".join(sorted(set(by_symbol.get(sym.stable_key, [])))),
                    " ".join(sorted(set(facts_by_symbol.get(sym.stable_key, [])))),
                    ext.file.role,
                    ext.file.file_class,
                )
