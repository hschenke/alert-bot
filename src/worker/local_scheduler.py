"""Lokaler Hintergrund-Scheduler & Ad-hoc-Runner für den Betrieb ohne GitHub Actions."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from src.config.settings import AppSettings
from src.detectors.engine import AlertEvaluationEngine
from src.domain.models import EvaluationOutcome
from src.notifications.channels import create_notification_router
from src.storage.repository import SqlAlertRepository, create_alert_repository

logger = logging.getLogger(__name__)


class LocalAlertRunner:
    """Führt Prüfungen lokal im Hintergrund oder synchron auf Knopfdruck aus."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, settings.max_concurrent_local_checks),
            thread_name_prefix="alert-local",
        )
        self._stop_event = threading.Event()
        self._daemon_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def run_rule_sync(
        self,
        rule_id: str,
        repository: SqlAlertRepository | None = None,
    ) -> EvaluationOutcome | None:
        """Prüft eine einzelne Regel synchron (ideal für sofortiges Feedback in der Web-UI)."""
        owns_repo = repository is None
        repo = repository or create_alert_repository(self._settings)
        try:
            now_dt = datetime.now(UTC).replace(microsecond=0)
            lock_until = (now_dt + timedelta(seconds=self._settings.rule_lock_seconds)).isoformat()
            claimed = repo.claim_specific_rule(rule_id, now_dt.isoformat(), lock_until, "ui-sync")
            if claimed is None:
                existing = repo.get_rule(rule_id)
                if existing is None:
                    return None
                claimed = existing
            engine = AlertEvaluationEngine(
                settings=self._settings,
                repository=repo,
                notifier=create_notification_router(self._settings),
            )
            return engine.evaluate_rule(claimed, now=now_dt)
        finally:
            if owns_repo:
                repo.close()

    def submit_rule_async(self, rule_id: str) -> Future[EvaluationOutcome | None]:
        """Startet die Prüfung einer Regel asynchron im lokalen ThreadPool."""
        return self._pool.submit(self.run_rule_sync, rule_id, None)

    def ensure_background_scheduler_started(self) -> None:
        """Startet einmalig einen schlanken Daemon-Thread für fällige lokale Regeln."""
        with self._lock:
            if self._daemon_thread is not None and self._daemon_thread.is_alive():
                return
            self._stop_event.clear()
            self._daemon_thread = threading.Thread(
                target=self._scheduler_loop,
                name="alert-local-scheduler",
                daemon=True,
            )
            self._daemon_thread.start()
            logger.info(
                "Lokaler Alert-Scheduler gestartet (Poll-Intervall: %ds).",
                self._settings.local_scheduler_poll_seconds,
            )

    def _scheduler_loop(self) -> None:
        poll_sec = max(10, self._settings.local_scheduler_poll_seconds)
        while not self._stop_event.wait(poll_sec):
            try:
                self._run_due_cycle()
            except Exception as exc:
                logger.warning("Fehler im lokalen Scheduler-Zyklus: %s", exc)

    def _run_due_cycle(self) -> None:
        repo = create_alert_repository(self._settings)
        try:
            now_dt = datetime.now(UTC).replace(microsecond=0)
            lock_until = (now_dt + timedelta(seconds=self._settings.rule_lock_seconds)).isoformat()
            due_rules = repo.claim_due_rules(
                now_iso=now_dt.isoformat(),
                lock_until_iso=lock_until,
                worker_id="local-daemon",
                limit=5,
            )
            if not due_rules:
                return
            engine = AlertEvaluationEngine(
                settings=self._settings,
                repository=repo,
                notifier=create_notification_router(self._settings),
            )
            for rule in due_rules:
                engine.evaluate_rule(rule, now=now_dt)
        finally:
            repo.close()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._pool.shutdown(wait=False)
