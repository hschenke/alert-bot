"""Wetter- & Temperatur-Wächter via Open-Meteo API (kostenlos, ohne Key) + KI-Websuche-Fallback."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from src.detectors.comparators import evaluate_numeric_operator, format_number_de
from src.detectors.http_client import SafeHttpFetcher
from src.detectors.llm_gateway import AlertLLMGateway
from src.domain.enums import WeatherMetric
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding
from src.domain.schemas import AiMetricExtraction

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_METRIC_API_FIELDS: dict[WeatherMetric, tuple[str, str, str]] = {
    WeatherMetric.TEMPERATURE_C: ("temperature_2m", "°C", "Temperatur"),
    WeatherMetric.APPARENT_TEMPERATURE_C: ("apparent_temperature", "°C", "Gefühlte Temperatur"),
    WeatherMetric.WIND_SPEED_KMH: ("wind_speed_10m", "km/h", "Windgeschwindigkeit"),
    WeatherMetric.PRECIPITATION_MM: ("precipitation", "mm", "Niederschlag"),
}


class WeatherThresholdDetector:
    """Prüft Live-Wetterdaten an jedem Ort weltweit gegen einen numerischen Schwellenwert."""

    def __init__(
        self,
        http_fetcher: SafeHttpFetcher,
        llm_gateway: AlertLLMGateway | None = None,
        metric_prompt: str = "",
    ) -> None:
        self._http = http_fetcher
        self._llm = llm_gateway
        self._metric_prompt = metric_prompt

    def evaluate(self, rule: AlertRule) -> DetectorFinding:
        """Misst aktuellen Wetterwert am Zielort und gleicht ihn mit dem Schwellenwert ab."""
        location = (rule.threshold.location_name or rule.search_query).strip()
        if not location:
            raise DetectorExecutionError("Kein Ort für den Wetter-Wächter angegeben.")
        if rule.threshold.target_value is None:
            raise DetectorExecutionError("Kein numerischer Schwellenwert für den Wetter-Wächter gesetzt.")

        metric = rule.threshold.weather_metric
        api_field, unit, label = _METRIC_API_FIELDS[metric]
        target = rule.threshold.target_value
        op = rule.threshold.operator

        try:
            measured, resolved_place = self._fetch_open_meteo(location, api_field)
            source_url = "https://open-meteo.com/"
        except DetectorExecutionError as exc:
            if self._llm is None:
                raise
            logger.warning("Open-Meteo nicht verfügbar (%s) – nutze Gemini Web-Suche als Fallback.", exc)
            measured, resolved_place, source_url = self._fetch_via_llm_fallback(location, label, unit)

        met = evaluate_numeric_operator(measured, op, target)
        measured_str = f"{format_number_de(measured, 1)} {unit}"
        target_str = f"{op.value} {format_number_de(target, 1)} {unit}"
        status_word = "erfüllt" if met else "noch nicht erreicht"
        summary = (
            f"{label} in {resolved_place}: aktuell {measured_str} "
            f"(Zielbedingung {target_str} {status_word})."
        )
        return DetectorFinding(
            condition_met=met,
            summary=summary,
            measured_value=measured,
            measured_display=f"{measured_str} ({resolved_place})",
            source_url=source_url,
        )

    def _fetch_open_meteo(self, location: str, api_field: str) -> tuple[float, str]:
        geo_query = f"{_GEOCODING_URL}?name={quote(location)}&count=1&language=de&format=json"
        geo_resp = self._http.fetch(geo_query)
        if geo_resp.status_code != 200:
            raise DetectorExecutionError(f"Geocoding HTTP {geo_resp.status_code}")

        try:
            geo_data = json.loads(geo_resp.text)
        except json.JSONDecodeError as exc:
            raise DetectorExecutionError("Ungültige Geocoding-Antwort.") from exc

        results = geo_data.get("results") if isinstance(geo_data, dict) else None
        if not results or not isinstance(results[0], dict):
            raise DetectorExecutionError(f"Ort '{location}' wurde nicht gefunden.")

        first = results[0]
        lat = float(first["latitude"])
        lon = float(first["longitude"])
        city = str(first.get("name", location))
        country = str(first.get("country", "")).strip()
        resolved_place = f"{city}, {country}" if country else city

        wx_url = (
            f"{_FORECAST_URL}?latitude={lat}&longitude={lon}"
            f"&current={api_field}&timezone=auto"
        )
        wx_resp = self._http.fetch(wx_url)
        if wx_resp.status_code != 200:
            raise DetectorExecutionError(f"Wetter-API HTTP {wx_resp.status_code}")

        try:
            wx_data = json.loads(wx_resp.text)
            current = wx_data.get("current", {})
            raw_val = current.get(api_field)
            if raw_val is None:
                raise KeyError(api_field)
            return float(raw_val), resolved_place
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise DetectorExecutionError("Aktueller Wetter-Messwert konnte nicht gelesen werden.") from exc

    def _fetch_via_llm_fallback(
        self,
        location: str,
        label: str,
        unit: str,
    ) -> tuple[float, str, str]:
        if self._llm is None:
            raise DetectorExecutionError("Kein KI-Fallback konfiguriert.")
        prompt = (
            f"Wie hoch ist aktuell die {label} in {location} (in {unit})? "
            "Nutze aktuelle Wetterdaten aus der Web-Suche."
        )
        extracted = self._llm.generate_grounded_structured(
            prompt=prompt,
            system_instruction=self._metric_prompt or "Extrahiere den aktuellen Wetter-Messwert präzise.",
            schema_cls=AiMetricExtraction,
        )
        if not extracted.value_found or extracted.numeric_value is None:
            raise DetectorExecutionError(f"Kein aktueller Wetterwert für '{location}' ermittelbar.")
        return float(extracted.numeric_value), location, extracted.source_url
