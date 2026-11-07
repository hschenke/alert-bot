"""Thread-sicherer lokaler SQLite-Executor (WAL-Modus) mit Path-Traversal-Schutz."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.config.settings import PROJECT_ROOT
from src.domain.exceptions import StorageConnectionError, StorageError
from src.security.sanitization import resolve_safe_path
from src.storage.executor import StatementResult


class SqliteExecutor:
    """Lokaler SQLite-Adapter für Entwicklung, Tests und Offline-Fallback."""

    def __init__(self, db_path: str | Path, base_dir: Path = PROJECT_ROOT) -> None:
        self._is_memory = str(db_path).strip() == ":memory:"
        if self._is_memory:
            target_str = ":memory:"
        else:
            raw_path = Path(db_path)
            safe_path = (
                raw_path.resolve()
                if raw_path.is_absolute()
                else resolve_safe_path(base_dir, str(db_path))
            )
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            target_str = str(safe_path)

        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(target_str, check_same_thread=False, timeout=15.0)
            self._conn.row_factory = sqlite3.Row
            if not self._is_memory:
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
        except sqlite3.Error as exc:
            raise StorageConnectionError(f"SQLite-Initialisierung fehlgeschlagen: {exc}") from exc

    @property
    def backend_name(self) -> str:
        return "SQLite (In-Memory)" if self._is_memory else "SQLite Lokal (WAL)"

    def execute(self, sql: str, params: Sequence[Any] = ()) -> StatementResult:
        return self.execute_batch([(sql, params)])[0]

    def execute_batch(
        self,
        statements: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[StatementResult]:
        if not statements:
            return []

        results: list[StatementResult] = []
        with self._lock:
            try:
                cursor = self._conn.cursor()
                for sql, params in statements:
                    normalized_params = [
                        int(p) if isinstance(p, bool) else p for p in params
                    ]
                    cursor.execute(sql, normalized_params)
                    fetched = cursor.fetchall() if cursor.description else []
                    rows = [dict(row) for row in fetched]
                    affected = max(0, cursor.rowcount) if cursor.rowcount is not None else 0
                    results.append(StatementResult(rows=rows, affected_rows=affected))
                self._conn.commit()
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError(f"SQLite-Abfragefehler: {exc}") from exc
        return results

    def close(self) -> None:
        with self._lock:
            self._conn.close()
