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
    DetectorType.WEATHER_THRESHOLD: "🌡️ Wetter- & Temperatur-Schwellenwert",
    DetectorType.WEB_SEARCH: "🌍 KI-Websuche & Neue Angebote (z. B. Mietwohnungen / News)",
    DetectorType.PRICE_THRESHOLD: "💰 Preis-, Finanz- & Kennzahlen-Wächter",
    DetectorType.PAGE_CHANGE: "📄 Website-Änderungen & Verfügbarkeit",
    DetectorType.HTTP_API: "🩺 Uptime-, HTTP-Status- & JSON-API-Check",
}

TRIGGER_LABELS: dict[TriggerMode, str] = {
    TriggerMode.ON_STATE_CHANGE: "🔄 Bei Zustandswechsel (kein Dauer-Spam solange aktiv)",
    TriggerMode.EVERY_NEW_ITEM: "🆕 Bei jedem neuen Treffer / Inserat (mit Duplikat-Gedächtnis)",
    TriggerMode.COOLDOWN: "⏱️ Wiederholt nach Cooldown-Pause",
    TriggerMode.ONCE: "1️⃣ Einmalig melden & danach automatisch pausieren",
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
