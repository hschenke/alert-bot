"""Einheitliches Repository (100 % parametrisiertes SQL) für SQLite und Turso Cloud."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from src.config.settings import AppSettings
from src.domain.enums import RuleStatus
from src.domain.models import AlertEvent, AlertRule, CheckLogEntry, DiscoveredItem
from src.storage.executor import SqlExecutor
from src.storage.row_mappers import (
    map_row_to_check_log,
    map_row_to_event,
    map_row_to_rule,
    serialize_channels,
    serialize_items,
    serialize_threshold,
)
from src.storage.schema import DDL_STATEMENTS
from src.storage.sqlite_executor import SqliteExecutor
from src.storage.turso_executor import TursoExecutor

logger = logging.getLogger(__name__)


class SqlAlertRepository:
    """Persistenz-Fassade ohne dupliziertes SQL für Regeln, Treffer-Historie und Deduplizierung."""

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor
        self.initialize_schema()

    @property
    def backend_name(self) -> str:
        return self._executor.backend_name

    def initialize_schema(self) -> None:
        """Führt alle DDL-Migrationen idempotent aus."""
        self._executor.execute_batch([(ddl, ()) for ddl in DDL_STATEMENTS])

    def upsert_rule(self, rule: AlertRule) -> None:
        """Erstellt oder aktualisiert eine Alert-Regel vollständig parametrisiert."""
        sql = """
        INSERT INTO alert_rules (
            id, title, user_prompt, detector_type, status,
            search_query, target_url, condition_description, threshold_json,
            trigger_mode, check_interval_minutes, cooldown_minutes,
            notify_on_first_match, notify_email, channels_json,
            last_checked_at, next_check_at, last_triggered_at,
            last_condition_met, last_value_text, last_content_hash,
            last_error, consecutive_failures, total_triggers, baseline_seeded,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            user_prompt = excluded.user_prompt,
            detector_type = excluded.detector_type,
            status = excluded.status,
            search_query = excluded.search_query,
            target_url = excluded.target_url,
            condition_description = excluded.condition_description,
            threshold_json = excluded.threshold_json,
            trigger_mode = excluded.trigger_mode,
            check_interval_minutes = excluded.check_interval_minutes,
            cooldown_minutes = excluded.cooldown_minutes,
            notify_on_first_match = excluded.notify_on_first_match,
            notify_email = excluded.notify_email,
            channels_json = excluded.channels_json,
            last_checked_at = excluded.last_checked_at,
            next_check_at = excluded.next_check_at,
            last_triggered_at = excluded.last_triggered_at,
            last_condition_met = excluded.last_condition_met,
            last_value_text = excluded.last_value_text,
            last_content_hash = excluded.last_content_hash,
            last_error = excluded.last_error,
            consecutive_failures = excluded.consecutive_failures,
            total_triggers = excluded.total_triggers,
            baseline_seeded = excluded.baseline_seeded,
            updated_at = excluded.updated_at
        """
        params = (
            rule.id,
            rule.title,
            rule.user_prompt,
            rule.detector_type.value,
            rule.status.value,
            rule.search_query,
            rule.target_url,
            rule.condition_description,
            serialize_threshold(rule.threshold),
            rule.trigger_mode.value,
            rule.check_interval_minutes,
            rule.cooldown_minutes,
            int(rule.notify_on_first_match),
            rule.notify_email,
            serialize_channels(rule.channels),
            rule.last_checked_at,
            rule.next_check_at,
            rule.last_triggered_at,
            int(rule.last_condition_met),
            rule.last_value_text,
            rule.last_content_hash,
            rule.last_error,
            rule.consecutive_failures,
            rule.total_triggers,
            int(rule.baseline_seeded),
            rule.created_at,
            rule.updated_at,
        )
        self._executor.execute(sql, params)

    def get_rule(self, rule_id: str) -> AlertRule | None:
        """Lädt eine einzelne Alert-Regel anhand ihrer ID."""
        res = self._executor.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,))
        return map_row_to_rule(res.rows[0]) if res.rows else None

    def list_rules(self, status: RuleStatus | None = None) -> list[AlertRule]:
        """Listet alle Regeln (optional nach Status gefiltert), neueste zuerst."""
        if status is not None:
            res = self._executor.execute(
                "SELECT * FROM alert_rules WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            )
        else:
            res = self._executor.execute("SELECT * FROM alert_rules ORDER BY created_at DESC")
        return [map_row_to_rule(row) for row in res.rows]

    def delete_rule(self, rule_id: str) -> bool:
        """Löscht eine Regel inklusive gemerkter Seen-Items und Prüf-Logs."""
        results = self._executor.execute_batch(
            [
                ("DELETE FROM alert_seen_items WHERE rule_id = ?", (rule_id,)),
                ("DELETE FROM alert_check_logs WHERE rule_id = ?", (rule_id,)),
                ("DELETE FROM alert_rules WHERE id = ?", (rule_id,)),
            ]
        )
        return results[-1].affected_rows > 0

    def set_rule_status(self, rule_id: str, status: RuleStatus, updated_at: str) -> None:
        """Aktualisiert den Status (`active`, `paused`, `error`) einer Regel."""
        self._executor.execute(
            "UPDATE alert_rules SET status = ?, updated_at = ?, locked_until = '' WHERE id = ?",
            (status.value, updated_at, rule_id),
        )

    def claim_due_rules(
        self,
        now_iso: str,
        lock_until_iso: str,
        worker_id: str,
        limit: int = 25,
    ) -> list[AlertRule]:
        """Reserviert atomar alle fälligen aktiven Regeln gegen parallele Doppelausführung."""
        candidates = self._executor.execute(
            """
            SELECT id FROM alert_rules
            WHERE status = 'active'
              AND (next_check_at = '' OR next_check_at <= ?)
              AND (locked_until = '' OR locked_until <= ?)
            ORDER BY next_check_at ASC
            LIMIT ?
            """,
            (now_iso, now_iso, limit),
        )
        claimed: list[AlertRule] = []
        for row in candidates.rows:
            rule = self.claim_specific_rule(str(row["id"]), now_iso, lock_until_iso, worker_id)
            if rule is not None:
                claimed.append(rule)
        return claimed

    def claim_specific_rule(
        self,
        rule_id: str,
        now_iso: str,
        lock_until_iso: str,
        worker_id: str,
    ) -> AlertRule | None:
        """Reserviert gezielt eine einzelne Regel atomar über `locked_until`."""
        res = self._executor.execute(
            """
            UPDATE alert_rules
            SET locked_until = ?, locked_by = ?
            WHERE id = ? AND (locked_until = '' OR locked_until <= ?)
            """,
            (lock_until_iso, worker_id, rule_id, now_iso),
        )
        if res.affected_rows <= 0:
            return None
        return self.get_rule(rule_id)

    def release_rule_lock(self, rule_id: str) -> None:
        """Gibt die Worker-Reservierung nach Abschluss der Prüfung wieder frei."""
        self._executor.execute(
            "UPDATE alert_rules SET locked_until = '', locked_by = '' WHERE id = ?",
            (rule_id,),
        )

    def get_seen_fingerprints(self, rule_id: str) -> set[str]:
        """Liefert alle bereits bekannten Treffer-Fingerprints einer Regel."""
        res = self._executor.execute(
            "SELECT fingerprint FROM alert_seen_items WHERE rule_id = ?",
            (rule_id,),
        )
        return {str(row["fingerprint"]) for row in res.rows}

    def record_seen_items(
        self,
        rule_id: str,
        items: Sequence[DiscoveredItem],
        seen_at_iso: str,
    ) -> None:
        """Speichert neue Treffer-Fingerprints idempotent ab."""
        if not items:
            return
        stmts = [
            (
                """
                INSERT OR IGNORE INTO alert_seen_items
                (rule_id, fingerprint, title, url, first_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rule_id, item.fingerprint, item.title[:200], item.url[:500], seen_at_iso),
            )
            for item in items
        ]
        self._executor.execute_batch(stmts)

    def save_event(self, event: AlertEvent) -> None:
        """Persistiert ein ausgelöstes Alarm-Ereignis."""
        self._executor.execute(
            """
            INSERT INTO alert_events (
                id, rule_id, rule_title, detector_type, headline, summary,
                measured_display, source_url, items_json,
                notified_channels_json, notification_succeeded, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.rule_id,
                event.rule_title,
                event.detector_type.value,
                event.headline,
                event.summary,
                event.measured_display,
                event.source_url,
                serialize_items(event.items),
                json.dumps(list(event.notified_channels)),
                int(event.notification_succeeded),
                event.created_at,
            ),
        )

    def list_events(self, rule_id: str | None = None, limit: int = 50) -> list[AlertEvent]:
        """Lädt die neuesten Alarm-Ereignisse chronologisch absteigend."""
        if rule_id:
            res = self._executor.execute(
                "SELECT * FROM alert_events WHERE rule_id = ? ORDER BY created_at DESC LIMIT ?",
                (rule_id, limit),
            )
        else:
            res = self._executor.execute(
                "SELECT * FROM alert_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [map_row_to_event(row) for row in res.rows]

    def get_event(self, event_id: str) -> AlertEvent | None:
        """Lädt ein einzelnes Alarm-Ereignis anhand seiner ID."""
        res = self._executor.execute("SELECT * FROM alert_events WHERE id = ?", (event_id,))
        return map_row_to_event(res.rows[0]) if res.rows else None

    def append_check_log(self, entry: CheckLogEntry) -> None:
        """Schreibt einen Prüf-Log-Eintrag und begrenzt alte Einträge pro Regel schlank."""
        self._executor.execute_batch(
            [
                (
                    """
                    INSERT INTO alert_check_logs
                    (id, rule_id, outcome, message, measured_display, duration_ms, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.rule_id,
                        entry.outcome.value,
                        entry.message[:500],
                        entry.measured_display[:120],
                        entry.duration_ms,
                        entry.created_at,
                    ),
                ),
                (
                    """
                    DELETE FROM alert_check_logs
                    WHERE rule_id = ? AND id NOT IN (
                        SELECT id FROM alert_check_logs
                        WHERE rule_id = ?
                        ORDER BY created_at DESC
                        LIMIT 50
                    )
                    """,
                    (entry.rule_id, entry.rule_id),
                ),
            ]
        )

    def list_check_logs(self, rule_id: str | None = None, limit: int = 40) -> list[CheckLogEntry]:
        """Listet die letzten Prüf-Durchläufe auf."""
        if rule_id:
            res = self._executor.execute(
                "SELECT * FROM alert_check_logs WHERE rule_id = ? ORDER BY created_at DESC LIMIT ?",
                (rule_id, limit),
            )
        else:
            res = self._executor.execute(
                "SELECT * FROM alert_check_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [map_row_to_check_log(row) for row in res.rows]

    def list_recent_check_logs_by_rule(self, per_rule_limit: int = 8) -> dict[str, list[CheckLogEntry]]:
        """Lädt bis zu `per_rule_limit` aktuelle Prüf-Logs je Regel in einer einzigen Abfrage."""
        res = self._executor.execute(
            """
            SELECT id, rule_id, outcome, message, measured_display, duration_ms, created_at
            FROM (
                SELECT id, rule_id, outcome, message, measured_display, duration_ms, created_at,
                       ROW_NUMBER() OVER (PARTITION BY rule_id ORDER BY created_at DESC) AS rn
                FROM alert_check_logs
            )
            WHERE rn <= ?
            ORDER BY created_at DESC
            """,
            (per_rule_limit,),
        )
        grouped: dict[str, list[CheckLogEntry]] = {}
        for row in res.rows:
            entry = map_row_to_check_log(row)
            grouped.setdefault(entry.rule_id, []).append(entry)
        return grouped

    def close(self) -> None:
        self._executor.close()


def create_alert_repository(settings: AppSettings) -> SqlAlertRepository:
    """Erstellt ein `SqlAlertRepository` für Turso Cloud (falls konfiguriert) oder lokales SQLite."""
    if settings.has_turso_configured:
        executor: SqlExecutor = TursoExecutor(
            database_url=settings.turso_url,
            auth_token=settings.turso_auth_token,
        )
        logger.info("Speicher-Backend initialisiert: Turso Cloud.")
    else:
        executor = SqliteExecutor(settings.sqlite_path)
        logger.info("Speicher-Backend initialisiert: %s.", executor.backend_name)
    return SqlAlertRepository(executor)
