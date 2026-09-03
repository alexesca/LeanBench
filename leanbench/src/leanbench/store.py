"""SQLite index over runs, tasks and summary metrics.

The run directory is the source of truth; this is a queryable mirror. Writing is
idempotent per run_id so re-indexing an existing run never duplicates rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    suite TEXT NOT NULL,
    candidate TEXT NOT NULL,
    track TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    tokenizer TEXT NOT NULL,
    tokenizer_approximate INTEGER NOT NULL,
    degraded INTEGER NOT NULL,
    run_dir TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    track TEXT NOT NULL,
    failed INTEGER NOT NULL,
    repository_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, task_id, track)
);
CREATE TABLE IF NOT EXISTS summary_metrics (
    run_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (run_id, scope, metric)
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_metrics_run ON summary_metrics(run_id);
"""


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> RunStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_run(self, row: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO runs
               (run_id, suite, candidate, track, status, started_at, finished_at,
                tokenizer, tokenizer_approximate, degraded, run_dir)
               VALUES (:run_id, :suite, :candidate, :track, :status, :started_at,
                       :finished_at, :tokenizer, :tokenizer_approximate, :degraded, :run_dir)""",
            row,
        )
        self._conn.commit()

    def record_tasks(self, run_id: str, rows: Iterable[dict[str, Any]]) -> None:
        self._conn.executemany(
            """INSERT OR REPLACE INTO tasks
               (run_id, task_id, track, failed, repository_tokens)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (run_id, r["task_id"], r["track"], int(bool(r.get("failed"))),
                 int(r.get("repository_tokens", 0)))
                for r in rows
            ],
        )
        self._conn.commit()

    def record_metrics(self, run_id: str, scope: str, metrics: dict[str, float]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO summary_metrics (run_id, scope, metric, value) VALUES (?,?,?,?)",
            [(run_id, scope, key, float(value)) for key, value in sorted(metrics.items())],
        )
        self._conn.commit()

    def runs(self) -> list[dict[str, Any]]:
        cursor = self._conn.execute("SELECT * FROM runs ORDER BY run_id")
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def metrics_for(self, run_id: str, scope: str) -> dict[str, float]:
        cursor = self._conn.execute(
            "SELECT metric, value FROM summary_metrics WHERE run_id=? AND scope=? ORDER BY metric",
            (run_id, scope),
        )
        return dict(cursor.fetchall())
