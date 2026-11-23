"""Typisierte Exception-Hierarchie für präzise Fehlerbehandlung ohne Silent Fails."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.models import CheckDiagnostics


class AlertBotError(Exception):
    """Basisklasse für alle fachlichen und technischen Fehler im Alert-Bot."""


class InputValidationError(AlertBotError):
    """Benutzer- oder Konfigurationseingabe verletzt eine Whitelist-Regel."""


class SSRFBlockedError(AlertBotError):
    """Ausgehende URL-Anfrage wurde durch den SSRF-/DNS-Rebinding-Schutz blockiert."""


class StorageError(AlertBotError):
    """Fehler bei einer Datenbankabfrage oder Migration."""


class StorageConnectionError(StorageError):
    """Verbindung zu SQLite oder Turso Cloud fehlgeschlagen."""


class LLMProviderError(AlertBotError):
    """Fehler oder Zeitüberschreitung bei der Kommunikation mit Google Gemini."""


class DetectorExecutionError(AlertBotError):
    """Fehler während der Ausführung eines spezifischen Alert-Wächters."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: CheckDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class NotificationError(AlertBotError):
    """Fehler beim Versand einer Alarm-Benachrichtigung."""


class DispatchError(AlertBotError):
    """Fehler beim Auslösen eines Hintergrund-Workers (z. B. GitHub Actions API)."""
