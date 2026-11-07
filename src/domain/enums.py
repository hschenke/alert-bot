"""Fachliche Aufzählungstypen (Enums) für Wächter, Operatoren, Trigger und Kanäle."""

from __future__ import annotations

from enum import StrEnum


class DetectorType(StrEnum):
    """Unterstützte Überwachungs-Strategien des Alert-Bots."""

    WEATHER_THRESHOLD = "weather_threshold"
    WEB_SEARCH = "web_search"
    PRICE_THRESHOLD = "price_threshold"
    PAGE_CHANGE = "page_change"
    HTTP_API = "http_api"


class WeatherMetric(StrEnum):
    """Messgrößen des integrierten Open-Meteo Wetter-Wächters."""

    TEMPERATURE_C = "temperature_c"
    APPARENT_TEMPERATURE_C = "apparent_temperature_c"
    WIND_SPEED_KMH = "wind_speed_kmh"
    PRECIPITATION_MM = "precipitation_mm"


class ComparisonOperator(StrEnum):
    """Vergleichsoperatoren für numerische und textbasierte Bedingungen."""

    GTE = ">="
    GT = ">"
    LTE = "<="
    LT = "<"
    EQ = "=="
    NEQ = "!="
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"


class TriggerMode(StrEnum):
    """Steuert, wann bei erfüllter Bedingung tatsächlich eine Benachrichtigung rausgeht."""

    ON_STATE_CHANGE = "on_state_change"
    EVERY_NEW_ITEM = "every_new_item"
    COOLDOWN = "cooldown"
    ONCE = "once"


class RuleStatus(StrEnum):
    """Lebenszyklus-Status einer Alert-Regel."""

    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class CheckOutcome(StrEnum):
    """Ergebnis eines einzelnen Prüfdurchlaufs."""

    TRIGGERED = "triggered"
    SUPPRESSED = "suppressed"
    OK_NO_MATCH = "ok_no_match"
    ERROR = "error"


class ChannelType(StrEnum):
    """Zustellkanäle (E-Mail aktiv in v0.1.0; ntfy, Telegram & Webhook vorbereitet)."""

    EMAIL = "email"
    NTFY = "ntfy"
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"


class WorkerMode(StrEnum):
    """Ausführungsmodus für Hintergrund-Prüfungen."""

    AUTO = "auto"
    GITHUB = "github"
    LOCAL = "local"


class AuthRole(StrEnum):
    """Zugriffsrolle innerhalb der Streamlit Web-App."""

    ADMIN = "admin"
    READONLY = "readonly"
    GUEST = "guest"
