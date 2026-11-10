"""Einheitlicher Dispatcher für Sofort-Checks und Hintergrund-Scheduling (Cloud vs. Lokal)."""

from __future__ import annotations

import logging

from src.config.settings import AppSettings
from src.domain.enums import WorkerMode
from src.domain.exceptions import DispatchError
from src.domain.models import EvaluationOutcome
from src.storage.repository import SqlAlertRepository
from src.worker.github_dispatcher import GitHubActionsDispatcher
from src.worker.local_scheduler import LocalAlertRunner

logger = logging.getLogger(__name__)


class AlertWorkDispatcher:
    """Wählt intelligent zwischen Cloud-Worker (GitHub Actions + Turso) und lokalem Runner."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._gh = GitHubActionsDispatcher(settings)
        self._local = LocalAlertRunner(settings)

    @property
    def uses_cloud_worker(self) -> bool:
        """True, wenn GitHub Actions + Turso als primärer Hintergrund-Worker aktiv sind."""
        if self._settings.worker_mode == WorkerMode.LOCAL:
            return False
        if self._settings.worker_mode == WorkerMode.GITHUB:
            return True
        return self._settings.has_github_worker_configured and self._settings.has_turso_configured

    def ensure_scheduler_active(self) -> None:
        """Aktiviert den eingebetteten Hintergrund-Scheduler für fällige Wächter."""
        if self._settings.worker_mode != WorkerMode.GITHUB:
            self._local.ensure_background_scheduler_started()

    def check_rule_now(
        self,
        rule_id: str,
        repository: SqlAlertRepository | None = None,
        prefer_cloud_async: bool = False,
    ) -> tuple[EvaluationOutcome | None, str]:
        """
        Führt einen Sofort-Check aus.
        Standardmäßig direkt synchron mit sofortigem Ergebnis-Feedback; optional via GitHub Actions.
        """
        if prefer_cloud_async and self.uses_cloud_worker:
            try:
                self._gh.trigger_check(rule_id=rule_id, force=True)
                return None, "Cloud-Prüfung auf GitHub Actions wurde gestartet."
            except DispatchError as exc:
                logger.warning("Cloud-Dispatch fehlgeschlagen (%s) – prüfe direkt.", exc)

        outcome = self._local.run_rule_sync(rule_id=rule_id, repository=repository)
        if outcome is None:
            return None, "Regel wurde nicht gefunden."
        return outcome, f"Prüfung abgeschlossen ({outcome.outcome.value})."
