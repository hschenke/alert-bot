"""Pydantic-Schemata für typsicheren Structured Output der Gemini Interactions API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompiledAlertDraft(BaseModel):
    """Strukturierter Regel-Entwurf aus einer natürlichsprachlichen Benutzereingabe."""

    title: str = Field(description="Prägnanter Titel des Wächters (max. 90 Zeichen)")
    detector_type: str = Field(
        description=(
            "Einer von: weather_threshold, web_search, price_threshold, page_change, http_api"
        )
    )
    condition_description: str = Field(
        description="Klare Beschreibung, wann genau alarmiert werden soll"
    )
    search_query: str = Field(
        default="",
        description="Optimierte Web-Suchanfrage für web_search oder price_threshold",
    )
    target_url: str = Field(
        default="",
        description="Konkrete Ziel-URL falls vom Nutzer genannt, sonst leer",
    )
    location_name: str = Field(
        default="",
        description="Orts-/Städtename bei weather_threshold (z. B. 'Freiburg' oder 'Berlin')",
    )
    weather_metric: str = Field(
        default="temperature_c",
        description="temperature_c, apparent_temperature_c, wind_speed_kmh oder precipitation_mm",
    )
    operator: str = Field(
        default=">=",
        description="Vergleichsoperator: >=, >, <=, <, ==, !=, contains, not_contains",
    )
    target_value: float | None = Field(
        default=None,
        description="Numerischer Ziel-/Schwellenwert falls zutreffend (z. B. 25.0 oder 1200.0)",
    )
    unit: str = Field(default="", description="Einheit wie °C, €, USD, km/h, %")
    css_selector: str = Field(default="", description="Optionaler CSS-Selektor")
    json_path: str = Field(default="", description="Optionaler JSON-Punktpfad für http_api")
    trigger_mode: str = Field(
        default="on_state_change",
        description="on_state_change, every_new_item, cooldown oder once",
    )
    suggested_interval_minutes: int = Field(
        default=30,
        description="Sinnvolles Prüfintervall in Minuten (zwischen 5 und 1440)",
    )


class AiDiscoveredMatch(BaseModel):
    """Einzelner von der KI gefundener Treffer (z. B. Wohnungsangebot oder Ereignis)."""

    title: str = Field(description="Titel des Angebots oder Ereignisses")
    url: str = Field(default="", description="Direkte Quell-URL (http/https)")
    summary: str = Field(default="", description="Kurzbeschreibung mit Eckdaten (Ort, Zimmer, Details)")
    price_or_value: str = Field(default="", description="Preis, Miete oder Kennzahl falls bekannt")


class AiSearchEvaluation(BaseModel):
    """Strukturierte KI-Bewertung einer Web-Such-Überwachung."""

    condition_met: bool = Field(
        description="True, wenn passende aktuelle Treffer oder das gesuchte Ereignis vorliegen"
    )
    summary: str = Field(description="Kompakte Zusammenfassung der aktuellen Lage auf Deutsch")
    matches: list[AiDiscoveredMatch] = Field(
        default_factory=list,
        description="Verifizierte Einzel-Treffer (max. 8 Einträge)",
    )


class AiMetricExtraction(BaseModel):
    """Extrahierter Zahlenwert aus Webseiten-Text oder Websuche."""

    value_found: bool = Field(description="True, falls ein aktueller Zahlenwert eindeutig gefunden wurde")
    numeric_value: float | None = Field(default=None, description="Reiner Zahlenwert als Dezimalzahl")
    unit: str = Field(default="", description="Einheit wie €, USD, °C, %")
    context_summary: str = Field(default="", description="Kurzer Kontext zum gefundenen Wert")
    source_url: str = Field(default="", description="Verwendete Quelle falls bekannt")


class AiChangeEvaluation(BaseModel):
    """Semantische Bewertung einer erkannten Webseiten-Änderung."""

    relevant_change: bool = Field(
        description="True, wenn die Änderung die vom Nutzer gesuchte Bedingung erfüllt"
    )
    summary: str = Field(description="Kurze Erklärung der relevanten Änderung")
    extracted_status: str = Field(default="", description="Aktueller Status, z. B. 'Verfügbar'")
