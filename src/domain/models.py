"""Unveränderliche Fachmodelle (`@dataclass(frozen=True)`) für Regeln, Funde und Ereignisse."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.enums import (
    ChannelType,
    CheckOutcome,
    ComparisonOperator,
    DetectorType,
    RuleStatus,
    TriggerMode,
    WeatherMetric,
)


@dataclass(frozen=True)
class DiscoveredItem:
    """Ein einzelner verifizierter Treffer (z. B. Wohnungsangebot, Artikel, Produkt)."""

    fingerprint: str
    title: str
    url: str = ""
    summary: str = ""
    price_or_value: str = ""


@dataclass(frozen=True)
class ThresholdSpec:
    """Strukturierte Prüfparameter für Schwellenwert-, Selektor- und API-Wächter."""

    location_name: str = ""
    weather_metric: WeatherMetric = WeatherMetric.TEMPERATURE_C
    operator: ComparisonOperator = ComparisonOperator.GTE
    target_value: float | None = None
    target_text: str = ""
    unit: str = ""
    css_selector: str = ""
    json_path: str = ""
    expected_status_code: int = 200


@dataclass(frozen=True)
class AlertRule:
    """Vollständige Definition und Laufzeit-Zustand eines Überwachungsauftrags."""

    id: str
    title: str
    user_prompt: str
    detector_type: DetectorType
    status: RuleStatus = RuleStatus.ACTIVE
    search_query: str = ""
    target_url: str = ""
    condition_description: str = ""
    threshold: ThresholdSpec = field(default_factory=ThresholdSpec)
    trigger_mode: TriggerMode = TriggerMode.ON_STATE_CHANGE
    check_interval_minutes: int = 30
    cooldown_minutes: int = 180
    notify_on_first_match: bool = True
    notify_email: str = ""
    channels: tuple[ChannelType, ...] = (ChannelType.EMAIL,)
    last_checked_at: str = ""
    next_check_at: str = ""
    last_triggered_at: str = ""
    last_condition_met: bool = False
    last_value_text: str = ""
    last_content_hash: str = ""
    last_error: str = ""
    consecutive_failures: int = 0
    total_triggers: int = 0
    baseline_seeded: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class DetectorFinding:
    """Normiertes Prüfergebnis eines einzelnen Detektors vor der Anti-Spam-Bewertung."""

    condition_met: bool
    summary: str
    measured_value: float | None = None
    measured_display: str = ""
    content_hash: str = ""
    items: tuple[DiscoveredItem, ...] = ()
    source_url: str = ""


@dataclass(frozen=True)
class AlertEvent:
    """Persistierter Treffer-Eintrag, für den ein Alarm ausgelöst wurde."""

    id: str
    rule_id: str
    rule_title: str
    detector_type: DetectorType
    headline: str
    summary: str
    measured_display: str = ""
    source_url: str = ""
    items: tuple[DiscoveredItem, ...] = ()
    notified_channels: tuple[str, ...] = ()
    notification_succeeded: bool = False
    created_at: str = ""


@dataclass(frozen=True)
class CheckLogEntry:
    """Kompakter Protokolleintrag eines ausgeführten Prüf-Laufs."""

    id: str
    rule_id: str
    outcome: CheckOutcome
    message: str
    measured_display: str = ""
    duration_ms: int = 0
    created_at: str = ""


@dataclass(frozen=True)
class EvaluationOutcome:
    """Gesamtergebnis von `AlertEvaluationEngine.evaluate_rule`."""

    rule: AlertRule
    outcome: CheckOutcome
    finding: DetectorFinding | None = None
    event: AlertEvent | None = None
    error_message: str = ""
