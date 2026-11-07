"""Zentrale Auswertungs-Engine mit Anti-Spam-Zustandsautomat, Deduplizierung & Cooldown."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from src.config.constants import MAX_CONSECUTIVE_FAILURES_BEFORE_ERROR
from src.config.settings import AppSettings
from src.detectors.http_api_detector import HttpApiDetector
from src.detectors.http_client import SafeHttpFetcher
from src.detectors.llm_gateway import AlertLLMGateway, GeminiAlertGateway
from src.detectors.page_change_detector import PageChangeDetector
from src.detectors.price_threshold_detector import PriceThresholdDetector
from src.detectors.weather_detector import WeatherThresholdDetector
from src.detectors.web_search_detector import WebSearchDetector
from src.domain.enums import CheckOutcome, DetectorType, RuleStatus, TriggerMode
from src.domain.exceptions import AlertBotError
from src.domain.models import (
    AlertEvent,
    AlertRule,
    CheckLogEntry,
    DetectorFinding,
    DiscoveredItem,
    EvaluationOutcome,
)
from src.storage.repository import SqlAlertRepository

logger = logging.getLogger(__name__)


class AlertNotificationSink(Protocol):
    """Entkoppelte Benachrichtigungs-Schnittstelle (Dependency Inversion)."""

    def dispatch_event(
        self,
        rule: AlertRule,
        event: AlertEvent,
    ) -> tuple[tuple[str, ...], bool]: ...


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


class AlertEvaluationEngine:
    """Führt Detektoren aus, filtert Duplikate/Spam und persistiert Ereignisse & Status."""

    def __init__(
        self,
        settings: AppSettings,
        repository: SqlAlertRepository,
        notifier: AlertNotificationSink,
        http_fetcher: SafeHttpFetcher | None = None,
        llm_gateway: AlertLLMGateway | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._notifier = notifier
        self._http = http_fetcher or SafeHttpFetcher()
        self._llm = llm_gateway or (
            GeminiAlertGateway(settings) if settings.has_gemini_configured else None
        )
        prompts = settings.prompts
        self._weather = WeatherThresholdDetector(self._http, self._llm, prompts.metric_extract_system_prompt)
        self._web_search = WebSearchDetector(self._http, self._llm, prompts.web_search_system_prompt)
        self._price = PriceThresholdDetector(self._http, self._llm, prompts.metric_extract_system_prompt)
        self._page_change = PageChangeDetector(self._http, self._llm, prompts.page_change_system_prompt)
        self._http_api = HttpApiDetector(self._http)

    def evaluate_rule(self, rule: AlertRule, now: datetime | None = None) -> EvaluationOutcome:
        """Prüft eine einzelne Regel, löst bei Bedarf Benachrichtigungen aus und speichert das Ergebnis."""
        started = time.monotonic()
        current_dt = now or datetime.now(UTC).replace(microsecond=0)
        now_iso = current_dt.isoformat()
        next_iso = (current_dt + timedelta(minutes=rule.check_interval_minutes)).isoformat()

        try:
            raw_finding = self._run_detector(rule)
            finding, should_fire, suppress_reason = self._apply_trigger_policy(rule, raw_finding, current_dt, now_iso)
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._finalize_success(
                rule=rule,
                finding=finding,
                should_fire=should_fire,
                suppress_reason=suppress_reason,
                now_iso=now_iso,
                next_iso=next_iso,
                duration_ms=duration_ms,
            )
        except (AlertBotError, ValueError) as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._finalize_error(rule, str(exc), now_iso, next_iso, duration_ms)
        finally:
            self._repo.release_rule_lock(rule.id)

    def _run_detector(self, rule: AlertRule) -> DetectorFinding:
        if rule.detector_type == DetectorType.WEATHER_THRESHOLD:
            return self._weather.evaluate(rule)
        if rule.detector_type == DetectorType.WEB_SEARCH:
            return self._web_search.evaluate(rule)
        if rule.detector_type == DetectorType.PRICE_THRESHOLD:
            return self._price.evaluate(rule)
        if rule.detector_type == DetectorType.PAGE_CHANGE:
            return self._page_change.evaluate(rule)
        return self._http_api.evaluate(rule)

    def _apply_trigger_policy(
        self,
        rule: AlertRule,
        finding: DetectorFinding,
        current_dt: datetime,
        now_iso: str,
    ) -> tuple[DetectorFinding, bool, str]:
        """Filtert bereits gesehene Inserate heraus und prüft State-Change / Cooldown / Baseline."""
        if rule.trigger_mode == TriggerMode.EVERY_NEW_ITEM and finding.items:
            return self._evaluate_new_items_policy(rule, finding, now_iso)

        if not finding.condition_met:
            return finding, False, ""

        if rule.trigger_mode == TriggerMode.ON_STATE_CHANGE and rule.last_condition_met:
            return finding, False, "Bedingung weiterhin aktiv (bereits beim Eintritt gemeldet)."

        if rule.trigger_mode == TriggerMode.COOLDOWN and rule.last_triggered_at:
            last_dt = _parse_iso(rule.last_triggered_at)
            if last_dt and (current_dt - last_dt) < timedelta(minutes=rule.cooldown_minutes):
                return finding, False, f"Cooldown ({rule.cooldown_minutes} Min.) läuft noch."

        return finding, True, ""

    def _evaluate_new_items_policy(
        self,
        rule: AlertRule,
        finding: DetectorFinding,
        now_iso: str,
    ) -> tuple[DetectorFinding, bool, str]:
        seen_fps = self._repo.get_seen_fingerprints(rule.id)
        unseen: tuple[DiscoveredItem, ...] = tuple(
            item for item in finding.items if item.fingerprint not in seen_fps
        )
        if unseen:
            self._repo.record_seen_items(rule.id, unseen, now_iso)

        if not unseen:
            updated = replace(
                finding,
                condition_met=False,
                measured_display="0 neue Treffer",
                items=(),
            )
            return updated, False, "Alle gefundenen Einträge wurden bereits gemeldet."

        updated_finding = replace(
            finding,
            condition_met=True,
            measured_display=f"{len(unseen)} neue Treffer",
            items=unseen,
            source_url=unseen[0].url or finding.source_url,
        )
        if not rule.baseline_seeded and not rule.notify_on_first_match:
            return (
                updated_finding,
                False,
                f"{len(unseen)} bestehende Einträge als Baseline gemerkt – künftige Neuerscheinungen lösen Alarm aus.",
            )
        return updated_finding, True, ""

    def _finalize_success(
        self,
        rule: AlertRule,
        finding: DetectorFinding,
        should_fire: bool,
        suppress_reason: str,
        now_iso: str,
        next_iso: str,
        duration_ms: int,
    ) -> EvaluationOutcome:
        event: AlertEvent | None = None
        new_status = RuleStatus.ACTIVE if rule.status == RuleStatus.ERROR else rule.status
        triggered_at = rule.last_triggered_at
        total_triggers = rule.total_triggers

        if should_fire:
            event = self._build_and_send_event(rule, finding, now_iso)
            triggered_at = now_iso
            total_triggers += 1
            if rule.trigger_mode == TriggerMode.ONCE:
                new_status = RuleStatus.PAUSED
            outcome = CheckOutcome.TRIGGERED
            log_msg = finding.summary
        elif finding.condition_met or suppress_reason:
            outcome = CheckOutcome.SUPPRESSED if finding.condition_met else CheckOutcome.OK_NO_MATCH
            log_msg = suppress_reason or finding.summary
        else:
            outcome = CheckOutcome.OK_NO_MATCH
            log_msg = finding.summary

        updated_rule = replace(
            rule,
            status=new_status,
            last_checked_at=now_iso,
            next_check_at=next_iso,
            last_triggered_at=triggered_at,
            last_condition_met=finding.condition_met,
            last_value_text=finding.measured_display or rule.last_value_text,
            last_content_hash=finding.content_hash or rule.last_content_hash,
            last_error="",
            consecutive_failures=0,
            total_triggers=total_triggers,
            baseline_seeded=True,
            updated_at=now_iso,
        )
        self._repo.upsert_rule(updated_rule)
        self._repo.append_check_log(
            CheckLogEntry(
                id=uuid.uuid4().hex[:12],
                rule_id=rule.id,
                outcome=outcome,
                message=log_msg,
                measured_display=finding.measured_display,
                duration_ms=duration_ms,
                created_at=now_iso,
            )
        )
        return EvaluationOutcome(rule=updated_rule, outcome=outcome, finding=finding, event=event)

    def _build_and_send_event(
        self,
        rule: AlertRule,
        finding: DetectorFinding,
        now_iso: str,
    ) -> AlertEvent:
        headline = (
            f"{rule.title} ({finding.measured_display})"
            if finding.measured_display
            else rule.title
        )
        draft_event = AlertEvent(
            id=uuid.uuid4().hex[:12],
            rule_id=rule.id,
            rule_title=rule.title,
            detector_type=rule.detector_type,
            headline=headline[:180],
            summary=finding.summary,
            measured_display=finding.measured_display,
            source_url=finding.source_url or rule.target_url,
            items=finding.items,
            created_at=now_iso,
        )
        channels_sent, succeeded = self._notifier.dispatch_event(rule, draft_event)
        final_event = replace(
            draft_event,
            notified_channels=channels_sent,
            notification_succeeded=succeeded,
        )
        self._repo.save_event(final_event)
        return final_event

    def _finalize_error(
        self,
        rule: AlertRule,
        error_msg: str,
        now_iso: str,
        next_iso: str,
        duration_ms: int,
    ) -> EvaluationOutcome:
        failures = rule.consecutive_failures + 1
        status = (
            RuleStatus.ERROR
            if failures >= MAX_CONSECUTIVE_FAILURES_BEFORE_ERROR
            else rule.status
        )
        updated_rule = replace(
            rule,
            status=status,
            last_checked_at=now_iso,
            next_check_at=next_iso,
            last_error=error_msg[:300],
            consecutive_failures=failures,
            updated_at=now_iso,
        )
        self._repo.upsert_rule(updated_rule)
        self._repo.append_check_log(
            CheckLogEntry(
                id=uuid.uuid4().hex[:12],
                rule_id=rule.id,
                outcome=CheckOutcome.ERROR,
                message=error_msg,
                duration_ms=duration_ms,
                created_at=now_iso,
            )
        )
        logger.warning("Prüfung für Regel '%s' fehlgeschlagen (%d): %s", rule.id, failures, error_msg)
        return EvaluationOutcome(
            rule=updated_rule,
            outcome=CheckOutcome.ERROR,
            error_message=error_msg,
        )
