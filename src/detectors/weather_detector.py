"""Wetter- & Temperatur-Wächter via Open-Meteo, DWD Bright Sky, wttr.in & gehärtetem Web-Fallback."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import quote, urlparse

from src.config.constants import RETRY_BACKOFF_BASE_SECONDS, WEATHER_API_RETRIES
from src.detectors.comparators import (
    evaluate_numeric_operator,
    format_number_de,
    parse_localized_float,
)
from src.detectors.http_client import SafeHttpFetcher
from src.detectors.llm_gateway import AlertLLMGateway
from src.detectors.search_provider import FreeWebSearchProvider, WebSearchProvider
from src.domain.enums import WeatherMetric
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding, DiscoveredItem
from src.domain.schemas import AiMetricExtraction

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_BRIGHT_SKY_URL = "https://api.brightsky.dev/current_weather"
_WTTR_URL = "https://wttr.in"

_METRIC_API_FIELDS: dict[WeatherMetric, tuple[str, str, str]] = {
    WeatherMetric.TEMPERATURE_C: ("temperature_2m", "°C", "Temperatur"),
    WeatherMetric.APPARENT_TEMPERATURE_C: ("apparent_temperature", "°C", "Gefühlte Temperatur"),
    WeatherMetric.WIND_SPEED_KMH: ("wind_speed_10m", "km/h", "Windgeschwindigkeit"),
    WeatherMetric.PRECIPITATION_MM: ("precipitation", "mm", "Niederschlag"),
}
_BRIGHT_SKY_FIELDS: dict[WeatherMetric, tuple[str, ...]] = {
    WeatherMetric.TEMPERATURE_C: ("temperature",),
    WeatherMetric.WIND_SPEED_KMH: ("wind_speed_10", "wind_speed_30", "wind_speed_60", "wind_speed"),
    WeatherMetric.PRECIPITATION_MM: ("precipitation_60", "precipitation_30", "precipitation_10", "precipitation"),
}
_WTTR_FIELDS: dict[WeatherMetric, str] = {
    WeatherMetric.TEMPERATURE_C: "temp_C",
    WeatherMetric.APPARENT_TEMPERATURE_C: "FeelsLikeC",
    WeatherMetric.WIND_SPEED_KMH: "windspeedKmph",
    WeatherMetric.PRECIPITATION_MM: "precipMM",
}
_NON_LIVE_WEATHER_HOSTS: tuple[str, ...] = (
    "wikipedia.org",
    "wikidata.org",
    "wikisource.org",
    "wiktionary.org",
    "statista.com",
)
_HISTORICAL_WEATHER_TERMS: tuple[str, ...] = (
    "zeitreihe",
    "rekord",
    "hitzerekord",
    "kälterekord",
    "klimadiagramm",
    "klimatabelle",
    "jahresmittel",
    "durchschnittstemperatur",
    "historie",
    "statistik",
)


def _is_plausible_weather_value(metric: WeatherMetric, value: float) -> bool:
    """Prüft physikalische Plausibilität eines Wetter-Messwerts."""
    if metric in {WeatherMetric.TEMPERATURE_C, WeatherMetric.APPARENT_TEMPERATURE_C}:
        return -60.0 <= value <= 55.0
    if metric == WeatherMetric.WIND_SPEED_KMH:
        return 0.0 <= value <= 350.0
    return 0.0 <= value <= 500.0


def _is_live_weather_hit(item: DiscoveredItem) -> bool:
    """Filtert Wikipedia-, Klima-Statistik- und historische Rekord-Seiten aus Wetter-Suchtreffern."""
    host = (urlparse(item.url).hostname or "").lower()
    if any(blocked in host for blocked in _NON_LIVE_WEATHER_HOSTS):
        return False
    combined = f"{item.title} {item.summary} {item.url}".lower()
    return not any(term in combined for term in _HISTORICAL_WEATHER_TERMS)


def _first_numeric_field(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        raw = mapping.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


class WeatherThresholdDetector:
    """Prüft Live-Wetterdaten über Open-Meteo, Bright Sky (DWD), wttr.in & gehärteten Web-Fallback."""

    def __init__(
        self,
        http_fetcher: SafeHttpFetcher,
        llm_gateway: AlertLLMGateway | None = None,
        metric_prompt: str = "",
        search_provider: WebSearchProvider | None = None,
    ) -> None:
        self._http = http_fetcher
        self._llm = llm_gateway
        self._metric_prompt = metric_prompt
        self._search_provider = search_provider or FreeWebSearchProvider(http_fetcher)

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

        measured, resolved_place, source_url = self._resolve_live_measurement(
            location=location,
            metric=metric,
            api_field=api_field,
            label=label,
            unit=unit,
        )

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

    def _resolve_live_measurement(
        self,
        location: str,
        metric: WeatherMetric,
        api_field: str,
        label: str,
        unit: str,
    ) -> tuple[float, str, str]:
        coords = self._try_geocode(location)
        resolved_place = coords[2] if coords else location

        if coords is not None:
            lat, lon, resolved_place = coords
            om_val = self._try_open_meteo_current(lat, lon, api_field, metric)
            if om_val is not None:
                return om_val, resolved_place, "https://open-meteo.com/"

            dwd_val = self._try_bright_sky_current(lat, lon, metric)
            if dwd_val is not None:
                return dwd_val, resolved_place, "https://brightsky.dev/"

        wttr_val = self._try_wttr_current(location, metric)
        if wttr_val is not None:
            return wttr_val, resolved_place, "https://wttr.in/"

        return self._fetch_via_web_search_fallback(resolved_place, metric, label, unit)

    def _fetch_json_resilient(self, url: str, attempts: int = WEATHER_API_RETRIES) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                resp = self._http.fetch(url)
                if 400 <= resp.status_code < 500:
                    raise DetectorExecutionError(f"Wetter-API HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise RuntimeError(f"Wetter-API HTTP {resp.status_code}")
                parsed = json.loads(resp.text)
                if not isinstance(parsed, dict):
                    raise DetectorExecutionError("Ungültige JSON-Wetterantwort.")
                return parsed
            except DetectorExecutionError as exc:
                last_exc = exc
                if "HTTP 4" in str(exc) or "Ungültige JSON" in str(exc):
                    raise
            except (RuntimeError, json.JSONDecodeError) as exc:
                last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(RETRY_BACKOFF_BASE_SECONDS * (attempt + 1))
        raise DetectorExecutionError(f"Wetter-API nicht erreichbar: {last_exc}") from last_exc

    def _try_geocode(self, location: str) -> tuple[float, float, str] | None:
        geo_url = f"{_GEOCODING_URL}?name={quote(location)}&count=1&language=de&format=json"
        try:
            geo_data = self._fetch_json_resilient(geo_url)
            results = geo_data.get("results")
            if not isinstance(results, list) or not results or not isinstance(results[0], dict):
                return None
            first = results[0]
            lat, lon = float(first["latitude"]), float(first["longitude"])
            city = str(first.get("name", location))
            country = str(first.get("country", "")).strip()
            return lat, lon, f"{city}, {country}" if country else city
        except (DetectorExecutionError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Open-Meteo Geocoding fehlgeschlagen (%s).", exc)
            return None

    def _try_open_meteo_current(
        self, lat: float, lon: float, api_field: str, metric: WeatherMetric
    ) -> float | None:
        wx_url = f"{_FORECAST_URL}?latitude={lat}&longitude={lon}&current={api_field}&timezone=auto"
        try:
            wx_data = self._fetch_json_resilient(wx_url)
            current = wx_data.get("current")
            val = _first_numeric_field(current, (api_field,)) if isinstance(current, dict) else None
            if val is not None and _is_plausible_weather_value(metric, val):
                return val
        except DetectorExecutionError as exc:
            logger.warning("Open-Meteo nicht verfügbar (%s) – nutze DWD/Bright-Sky & Wetter-Fallback.", exc)
        return None

    def _try_bright_sky_current(self, lat: float, lon: float, metric: WeatherMetric) -> float | None:
        fields = _BRIGHT_SKY_FIELDS.get(metric)
        if not fields:
            return None
        url = f"{_BRIGHT_SKY_URL}?lat={lat:.4f}&lon={lon:.4f}&units=dwd"
        try:
            data = self._fetch_json_resilient(url, attempts=1)
            weather = data.get("weather")
            val = _first_numeric_field(weather, fields) if isinstance(weather, dict) else None
            if val is not None and _is_plausible_weather_value(metric, val):
                logger.info("DWD Bright Sky Fallback erfolgreich: %s", val)
                return val
        except DetectorExecutionError as exc:
            logger.info("Bright Sky (DWD) nicht verfügbar (%s).", exc)
        return None

    def _try_wttr_current(self, location: str, metric: WeatherMetric) -> float | None:
        field_name = _WTTR_FIELDS.get(metric)
        if not field_name:
            return None
        url = f"{_WTTR_URL}/{quote(location)}?format=j2"
        try:
            data = self._fetch_json_resilient(url, attempts=1)
            conds = data.get("current_condition")
            first = conds[0] if isinstance(conds, list) and conds and isinstance(conds[0], dict) else None
            val = _first_numeric_field(first, (field_name,)) if first else None
            if val is not None and _is_plausible_weather_value(metric, val):
                logger.info("wttr.in Wetter-Fallback erfolgreich: %s", val)
                return val
        except DetectorExecutionError as exc:
            logger.info("wttr.in Wetter-Fallback nicht verfügbar (%s).", exc)
        return None

    def _fetch_via_web_search_fallback(
        self,
        location: str,
        metric: WeatherMetric,
        label: str,
        unit: str,
    ) -> tuple[float, str, str]:
        query = f"Wetter {location} {label} aktuell heute"
        raw_hits = self._search_provider.search(query)
        live_hits = [hit for hit in raw_hits if _is_live_weather_hit(hit)]
        if not live_hits:
            raise DetectorExecutionError(
                f"Keine aktuellen Live-Wetterdaten für '{location}' ermittelbar."
            )

        primary_url = live_hits[0].url or "https://open-meteo.com/"
        snippets = "\n".join(f"- {it.title}: {it.summary}" for it in live_hits)

        if self._llm is not None:
            prompt = (
                f"Gesucht: Aktuelle Live-{label} in {location} (in {unit}).\n\n"
                f"Web-Suchergebnisse:\n{snippets}\n\n"
                "STRIKTE REGEL: Extrahiere NUR einen echten aktuellen Messwert von heute. "
                "Ignoriere historische Rekorde, Klimastatistiken und Durchschnittswerte komplett. "
                "Wenn kein aktueller Messwert genannt wird, setze `value_found` auf false."
            )
            try:
                res = self._llm.generate_structured(
                    prompt=prompt,
                    system_instruction=self._metric_prompt or "Extrahiere den aktuellen Wetter-Messwert präzise.",
                    schema_cls=AiMetricExtraction,
                )
                if res.value_found and res.numeric_value is not None:
                    val = float(res.numeric_value)
                    if _is_plausible_weather_value(metric, val):
                        return val, location, res.source_url or primary_url
            except Exception as exc:
                logger.warning("KI-Wetterextraktion fehlgeschlagen (%s), nutze Heuristik: %s", type(exc).__name__, exc)

        parsed = parse_localized_float(snippets, unit, strict_unit=True)
        if parsed is not None and _is_plausible_weather_value(metric, parsed):
            return parsed, location, primary_url

        raise DetectorExecutionError(f"Kein aktueller Wetterwert für '{location}' ermittelbar.")
