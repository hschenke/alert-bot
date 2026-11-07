"""Abstrakte SQL-Ausführungsschnittstelle für SQLite (lokal) und Turso LibSQL (Cloud)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StatementResult:
    """Normiertes Ergebnis einer einzelnen parametrisierten SQL-Anweisung."""

    rows: list[dict[str, Any]]
    affected_rows: int = 0


class SqlExecutor(Protocol):
    """Einheitliches Protokoll für lokale SQLite- und Cloud-Turso-Datenbanken."""

    @property
    def backend_name(self) -> str: ...

    def execute(self, sql: str, params: Sequence[Any] = ()) -> StatementResult: ...

    def execute_batch(
        self,
        statements: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[StatementResult]: ...

    def close(self) -> None: ...
