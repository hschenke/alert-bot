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
from src.domain.models import AlertRule
from src.security.sanitization import escape_html, sanitize_display_url

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


def build_kpi_grid_html(
    active_count: int,
    total_count: int,
    total_hits: int,
    recent_events_count: int,
) -> str:
    """Erstellt kompaktes, XSS-sicheres HTML für die 4er-KPI-Leiste."""
    kpis = (
        ("Active", active_count, "Currently active alerts"),
        ("Total", total_count, "All configured alerts"),
        ("Triggers", total_hits, "Total alerts triggered"),
        ("Events", recent_events_count, "Recent triggered events (max 20)"),
    )
    cards = "".join(
        f"<div class='ab-kpi-card' title='{escape_html(hint)}'>"
        f"<div class='ab-kpi-label'>{escape_html(label)}</div>"
        f"<div class='ab-kpi-value'>{int(val)}</div>"
        "</div>"
        for label, val, hint in kpis
    )
    return f"<div class='ab-kpi-grid'>{cards}</div>"


def build_rule_info_html(rule: AlertRule) -> str:
    """Formatiert Ziel-URL/Query und Schwellenwert einer Regel XSS-sicher als HTML."""
    items: list[str] = []
    safe_url = sanitize_display_url(rule.target_url) if rule.target_url else "#"
    if safe_url != "#":
        short_url = rule.target_url[:50] + ("…" if len(rule.target_url) > 50 else "")
        items.append(
            f"🔗 <a href='{escape_html(safe_url)}' target='_blank' style='color:#60a5fa;'>{escape_html(short_url)}</a>"
        )
    elif rule.search_query:
        items.append(f"🔍 Query: <code>{escape_html(rule.search_query)}</code>")
    if rule.threshold.target_value is not None:
        op_txt = escape_html(rule.threshold.operator.value)
        unit_txt = escape_html(rule.threshold.unit)
        items.append(f"🎯 Target: <b>{op_txt} {rule.threshold.target_value:g} {unit_txt}</b>")
    elif rule.trigger_mode == TriggerMode.ON_STATE_CHANGE:
        items.append("🎯 Trigger: <i>On Value Change</i>")
    return " &nbsp;•&nbsp; ".join(items)
