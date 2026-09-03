# ADR-011 — SQLite + JSONL; no analytical database until measured need

**Status:** Accepted (2026-09-02)

## Context

Event streams, per-task metrics, cost ledgers, and cross-run queries invite an analytical store —
DuckDB, Parquet, a columnar layout. Each adds a dependency, a schema migration story, and a
second way to represent the same facts, before any query has been shown to be slow.

## Decision

- **SQLite** for runs, tasks, summary metrics, metadata, and the cost ledger — queryable,
  transactional, on every machine, zero setup.
- **JSONL** for event streams — append-only, human-readable, greppable, trivially diffable, and
  recoverable when a run is killed mid-write.

That is the entire storage story for v1.

**Re-entry criterion for DuckDB/Parquet** (build spec §17): JSONL event files exceed ~1 GB, or
analysis queries exceed ~30 s. Until one is *measured*, adding a columnar store is optimizing a
problem we do not have.

## Consequences

- LeanBench runs on one laptop with no services to start. This is a stated project constraint,
  not an accident.
- JSONL costs disk and linear scans. Both are acceptable at the current corpus scale and both are
  measurable, so the re-entry criterion can actually be evaluated rather than argued about.
- Because events are plain JSONL, ADR-007's re-analysis promise needs no special tooling: any
  future metric is a script over a text file.
