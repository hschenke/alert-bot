"""Anzeige-Labels und datums-/zeitzonensichere Formatierung für die Streamlit-UI."""

from __future__ import annotations

from datetime import UTC, datetime

from src.domain.enums import (
    CheckOutcome,
    ComparisonOperator,
    DetectorType,
    RuleStatus,
    TriggerMode,
    WeatherMetric,
)

DETECTOR_LABELS: dict[DetectorType, str] = {
    DetectorType.WEATHER_THRESHOLD: "🌡️ Wetter & Temperatur",
    DetectorType.WEB_SEARCH: "🌍 Web-Radar & Angebote",
    DetectorType.PRICE_THRESHOLD: "💰 Kennzahlen & Preise",
    DetectorType.PAGE_CHANGE: "📄 Website-Monitor",
    DetectorType.HTTP_API: "🩺 HTTP & API-Check",
}

TRIGGER_LABELS: dict[TriggerMode, str] = {
    TriggerMode.ON_STATE_CHANGE: "🔄 Bei Wert-/Statusänderung",
    TriggerMode.EVERY_NEW_ITEM: "🆕 Bei neuen Treffern",
    TriggerMode.COOLDOWN: "⏱️ Mit Cooldown-Pause",
    TriggerMode.ONCE: "1️⃣ Einmalig melden",
}

WEATHER_METRIC_LABELS: dict[WeatherMetric, str] = {
    WeatherMetric.TEMPERATURE_C: "Temperatur (°C)",
    WeatherMetric.APPARENT_TEMPERATURE_C: "Gefühlte Temperatur (°C)",
    WeatherMetric.WIND_SPEED_KMH: "Windgeschwindigkeit (km/h)",
    WeatherMetric.PRECIPITATION_MM: "Niederschlag (mm)",
}

OPERATOR_LABELS: dict[ComparisonOperator, str] = {
    ComparisonOperator.GTE: ">= (mindestens / erreicht oder über)",
    ComparisonOperator.GT: "> (größer als)",
    ComparisonOperator.LTE: "<= (höchstens / erreicht oder unter)",
    ComparisonOperator.LT: "< (kleiner als)",
    ComparisonOperator.EQ: "== (genau gleich)",
    ComparisonOperator.NEQ: "!= (ungleich)",
    ComparisonOperator.CONTAINS: "enthält Text",
    ComparisonOperator.NOT_CONTAINS: "enthält Text NICHT",
}

STATUS_BADGES: dict[RuleStatus, str] = {
    RuleStatus.ACTIVE: "🟢 Aktiv",
    RuleStatus.PAUSED: "⏸️ Pausiert",
    RuleStatus.ERROR: "🔴 Fehler",
}

OUTCOME_BADGES: dict[CheckOutcome, str] = {
    CheckOutcome.TRIGGERED: "🔔 Alarm ausgelöst",
    CheckOutcome.SUPPRESSED: "🛡️ Aktiv (kein erneuter Spam)",
    CheckOutcome.OK_NO_MATCH: "✅ Geprüft (noch nicht eingetreten)",
    CheckOutcome.ERROR: "⚠️ Prüffehler",
}


def format_iso_timestamp(iso_str: str) -> str:
    """Formatiert einen ISO-8601-Zeitstempel lesbar in lokaler Systemzeit."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local_dt = dt.astimezone()
        return local_dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso_str[:16]


def format_interval_label(minutes: int) -> str:
    """Formatiert Minuten-Intervalle kompakt (z. B. `15 Min.` oder `6 Std.`)."""
    if minutes < 60:
        return f"{minutes} Min."
    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} Tag" if days == 1 else f"{days} Tage"
    if minutes % 60 == 0:
        return f"{minutes // 60} Std."
    return f"{minutes} Min."
