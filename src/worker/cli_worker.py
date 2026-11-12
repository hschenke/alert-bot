"""CLI-Worker für GitHub Actions (`check_alerts.yaml`), Cronjobs und Kommandozeile."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

from src.config.settings import load_app_settings
from src.detectors.engine import AlertEvaluationEngine
from src.domain.enums import CheckOutcome
from src.notifications.channels import create_notification_router
from src.storage.repository import create_alert_repository


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alert-Bot Hintergrund-Worker")
    parser.add_argument("--rule-id", default="", help="Optionale ID einer einzelnen Regel")
    parser.add_argument("--max-rules", type=int, default=25, help="Maximale Anzahl fälliger Regeln")
    parser.add_argument("--force", action="store_true", help="Ignoriere next_check_at Fälligkeit")
    return parser.parse_args(argv)


def run_cli_worker(argv: list[str] | None = None) -> int:
    """Führt alle fälligen (oder eine gezielt angegebene) Alert-Regeln aus."""
    _configure_logging()
    logger = logging.getLogger("alert_bot.worker")
    args = _parse_args(argv)

    settings = load_app_settings()
    worker_id = os.getenv("WORKER_ID", "cli-worker").strip() or "cli-worker"
    repo = create_alert_repository(settings)

    try:
        now_dt = datetime.now(UTC).replace(microsecond=0)
        now_iso = now_dt.isoformat()
        lock_until = (now_dt + timedelta(seconds=settings.rule_lock_seconds)).isoformat()

        if args.rule_id.strip():
            rule = repo.claim_specific_rule(args.rule_id.strip(), now_iso, lock_until, worker_id)
            rules = [rule] if rule is not None else []
        elif args.force:
            all_active = repo.list_rules()
            rules = [
                claimed
                for candidate in all_active[: max(1, args.max_rules)]
                if candidate.status.value == "active"
                and (claimed := repo.claim_specific_rule(candidate.id, now_iso, lock_until, worker_id)) is not None
            ]
        else:
            rules = repo.claim_due_rules(
                now_iso=now_iso,
                lock_until_iso=lock_until,
                worker_id=worker_id,
                limit=max(1, args.max_rules),
            )

        if not rules:
            logger.info("Keine fälligen Alert-Regeln zur Prüfung vorhanden.")
            return 0

        logger.info("Starte Prüfung von %d Alert-Regel(n)...", len(rules))
        engine = AlertEvaluationEngine(
            settings=settings,
            repository=repo,
            notifier=create_notification_router(settings),
        )

        triggered_count = 0
        error_count = 0
        for rule in rules:
            result = engine.evaluate_rule(rule, now=now_dt)
            if result.outcome == CheckOutcome.TRIGGERED:
                triggered_count += 1
            elif result.outcome == CheckOutcome.ERROR:
                error_count += 1
            summary_txt = result.finding.summary if result.finding else ""
            source_txt = result.finding.source_url if result.finding else ""
            logger.info(
                "Regel '%s' (%s) -> %s (%s) [Query: '%s', URL: '%s', Source: '%s', Summary: '%s']",
                rule.title,
                rule.id,
                result.outcome.value,
                result.finding.measured_display if result.finding else result.error_message,
                rule.search_query,
                rule.target_url,
                source_txt,
                summary_txt,
            )

        logger.info(
            "Worker-Lauf beendet: %d geprüft, %d ausgelöst, %d Fehler.",
            len(rules),
            triggered_count,
            error_count,
        )
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(run_cli_worker())
