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
from .model import FileExtraction, FileRecord, KeywordCandidate, UnresolvedRef
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

        # The adapter policy is the FULL resolved config for this path, flattened.
        # It used to be only the `extraction` section, which silently disabled every
        # `calls.*` / `comments.*` knob the adapters read: each lookup carries a
        # default, so a missing key produces no error, just quietly different
        # behaviour. Passing the resolved view keeps adapters free of config
        # resolution while guaranteeing the keys they ask for actually exist.
        policy = dict(self.cfg.for_path(disc.rel_path).values)
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

    # -- incremental sync ----------------------------------------------------------

    def incremental_sync(self) -> IndexResult:
        """Update only what changed, driven by the hash ladder of the invalidation matrix.

        The invariant this exists to satisfy is "bounded work per edit": the cost is
        O(size of changed files + resolution fan-in of their symbols), never O(repo).
        Two things make that true and both are easy to get wrong:

        * IDF is **read** from the frozen snapshot and never recomputed. A corpus-wide
          statistic recomputed here would make incremental state diverge from a clean
          rebuild, breaking equivalence, or force a repo-wide reparse, breaking
          boundedness. `full_sync` is the only place that may mint one.
        * Re-resolution is driven by the name index. Adding a file can resolve references
          that were dangling in files we did not touch, and deleting one can dangle
          references that were resolved; a pipeline that only reparses the changed file
          silently corrupts the relationship graph. Fan-in is bounded by a name lookup,
          not a scan.
        """
        started = time.perf_counter()
        result = IndexResult()

        with self.telemetry.timer("discovery"):
            discovered = discover(self.repo_root, self.cfg)
        self.telemetry.incr("files_discovered", len(discovered))

        known = self.store.file_hashes()
        seen: set[str] = set()
        to_extract: list[Discovered] = []

        for disc in discovered:
            seen.add(disc.rel_path)
            previous = known.get(disc.rel_path)
            if previous is None:
                to_extract.append(disc)
                continue
            try:
                max_bytes = int(self.cfg.get("discovery.max_file_bytes", 2 << 20))
                with open(disc.abs_path, "rb") as handle:  # noqa: PTH123 - hot path
                    digest = digest_bytes_(handle.read(max_bytes + 1))
            except OSError:
                to_extract.append(disc)
                continue
            if digest == previous[1]:
                # First row of the matrix: identical bytes, no work at all.
                self.telemetry.incr("hash_skips")
                result.skipped += 1
                continue
            to_extract.append(disc)

        removed = sorted(set(known) - seen)

        # Deleting a file dangles references held by OTHER files. Re-resolve exactly
        # those: bounded by fan-in, which is what the invariant permits, rather than by
        # the size of the repository.
        if removed:
            queued = {d.rel_path for d in to_extract}
            by_path = {d.rel_path: d for d in discovered}
            for path in removed:
                for referrer in self.store.files_referencing(known[path][0]):
                    if referrer not in queued and referrer in by_path:
                        to_extract.append(by_path[referrer])
                        queued.add(referrer)
                        self.telemetry.incr("fanin_reresolved")

        generation = self.store.generation() + 1
        snapshot = self._frozen_snapshot()

        with self.telemetry.timer("parsing"):
            extractions = self._extract_all(to_extract)
        result.reparsed = len(extractions)
        result.source_bytes = sum(e.file.byte_size for e in extractions)

        self.store.begin()
        try:
            for path in removed:
                self.store.delete_file(known[path][0])
                self.telemetry.incr("files_removed")
            for ext in extractions:
                previous = known.get(ext.file.path)
                if previous is not None:
                    if previous[2] and previous[2] == ext.file.structure_hash:
                        # Second row: formatting or comment movement only. Ranges move;
                        # facts and resolution do not need recomputing.
                        self.telemetry.incr("structure_hash_skips")
                    self.store.clear_file_derived_keep_symbols(previous[0])
                    removed_ids = self.store.prune_symbols(
                        previous[0], [s.stable_key for s in ext.symbols]
                    )
                    if removed_ids:
                        self.telemetry.incr("symbols_removed", len(removed_ids))
            self._write_files(extractions, snapshot, generation, result)
            self._resolve_and_write(extractions, generation, result)
            self._promote_unresolved(extractions, generation, result)
            self.store.set_meta("generation", str(generation))
            self.store.set_index_state("ok")
            self.telemetry.flush(self.store)
            self.store.commit()
        except Exception:
            self.store.rollback()
            raise

        result.generation = generation
        result.idf_generation = snapshot.generation
        result.files = self.store.count("files")
        result.symbols = self.store.count("symbols")
        result.duration_s = time.perf_counter() - started
        return result

    def _frozen_snapshot(self) -> IdfSnapshot:
        """Read-only view of the corpus statistics minted by the last full sync."""
        idf_generation = self.store.idf_generation()
        values = self.store.read_idf(idf_generation)
        doc_count = int(self.store.get_meta("doc_count", "0") or 0)
        return IdfSnapshot(
            generation=idf_generation,
            doc_count=doc_count,
            values=values,
            default=default_idf(doc_count) if doc_count else 1.0,
        )

    def _promote_unresolved(
        self, extractions: list[FileExtraction], generation: int, result: IndexResult
    ) -> None:
        """Re-resolve dangling references that the new symbols may now satisfy.

        Without this, adding a file leaves every reference to it permanently unresolved
        even though the target now exists — incremental state that a clean rebuild would
        never produce.
        """
        names = sorted({s.name for e in extractions for s in e.symbols})
        if not names:
            return
        pending = self.store.unresolved_by_name(names)
        if not pending:
            return
        index = SqlSymbolIndex(self.store.conn)
        resolver = Resolver(index, self.cfg)
        promoted = 0
        for row in pending:
            ref = UnresolvedRef(
                name=row["name"],
                kind=row["kind"],
                source_symbol_key="",
                source_file="",
                line=int(row["line"] or 0),
                receiver=row["receiver"] or "",
                alias_module=row["alias_module"] or "",
                arity=int(row["arity"] if row["arity"] is not None else -1),
            )
            edges, resolved = resolver.resolve(
                ref, int(row["source_symbol_id"] or 0), int(row["source_file_id"] or 0)
            )
            if not resolved:
                continue
            self.store.insert_relationships(
                [
                    (
                        e.kind, e.source_symbol_id, e.target_symbol_id, e.target_external,
                        e.confidence, e.tier, e.source_file_id, e.line, generation,
                    )
                    for e in edges
                ]
            )
            self.store.conn.execute("DELETE FROM unresolved_refs WHERE id=?", (row["id"],))
            promoted += 1
        self.telemetry.incr("re_resolutions", promoted)
        result.relationships += promoted

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
