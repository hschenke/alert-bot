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

PAGE_ACTIVE_ALERTS = "📡 Active Alerts"
PAGE_NEW_ALERT = "➕ New Alert"
PAGE_ALERT_HISTORY = ":material/notifications: Alert History"
PAGE_SETTINGS = "⚙️ Settings"

DETECTOR_LABELS: dict[DetectorType, str] = {
    DetectorType.WEATHER_THRESHOLD: "🌡️ Weather",
    DetectorType.WEB_SEARCH: "🌍 Web Search",
    DetectorType.PRICE_THRESHOLD: "💰 Price & Metric",
    DetectorType.PAGE_CHANGE: "📄 Page Change",
    DetectorType.HTTP_API: "🩺 HTTP / API",
}

TRIGGER_LABELS: dict[TriggerMode, str] = {
    TriggerMode.ON_STATE_CHANGE: "🔄 On State Change",
    TriggerMode.EVERY_NEW_ITEM: "🆕 Every New Item",
    TriggerMode.COOLDOWN: "⏱️ Cooldown",
    TriggerMode.ONCE: "1️⃣ Once",
}

WEATHER_METRIC_LABELS: dict[WeatherMetric, str] = {
    WeatherMetric.TEMPERATURE_C: "Temperature (°C)",
    WeatherMetric.APPARENT_TEMPERATURE_C: "Feels Like (°C)",
    WeatherMetric.WIND_SPEED_KMH: "Wind Speed (km/h)",
    WeatherMetric.PRECIPITATION_MM: "Precipitation (mm)",
}

OPERATOR_LABELS: dict[ComparisonOperator, str] = {
    ComparisonOperator.GTE: ">= (at least)",
    ComparisonOperator.GT: "> (greater than)",
    ComparisonOperator.LTE: "<= (at most)",
    ComparisonOperator.LT: "< (less than)",
    ComparisonOperator.EQ: "== (equal)",
    ComparisonOperator.NEQ: "!= (not equal)",
    ComparisonOperator.CONTAINS: "contains",
    ComparisonOperator.NOT_CONTAINS: "not contains",
}

STATUS_BADGES: dict[RuleStatus, str] = {
    RuleStatus.ACTIVE: "🟢 Active",
    RuleStatus.PAUSED: "⏸️ Paused",
    RuleStatus.ERROR: "🔴 Error",
}

OUTCOME_BADGES: dict[CheckOutcome, str] = {
    CheckOutcome.TRIGGERED: ":material/notifications_active: Triggered",
    CheckOutcome.SUPPRESSED: "🛡️ Suppressed (Active / Cooldown)",
    CheckOutcome.OK_NO_MATCH: "✅ OK (No Match)",
    CheckOutcome.ERROR: "⚠️ Check Error",
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
    """Formatiert Minuten-Intervalle kompakt (z. B. `15m`, `6h` oder `1d`)."""
    if minutes < 60:
        return f"{minutes}m"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"
