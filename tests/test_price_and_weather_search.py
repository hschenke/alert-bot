"""Tests für PriceThresholdDetector und WeatherThresholdDetector mit FreeWebSearchProvider."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from src.detectors.http_client import SafeHttpFetcher, SafeHttpResponse
from src.detectors.price_threshold_detector import PriceThresholdDetector
from src.detectors.weather_detector import WeatherThresholdDetector
from src.domain.enums import ComparisonOperator, DetectorType, TriggerMode, WeatherMetric
from src.domain.exceptions import LLMProviderError
from src.domain.models import AlertRule, ThresholdSpec

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_SAMPLE_BKK_HTML = """
<html><body>
<div class="result">
    <a class="result__a" href="https://krankenkassen.example.com/bkk-firmus">BKK firmus Zusatzbeitrag 2026</a>
    <div class="result__snippet">Der Zusatzbeitragssatz der BKK firmus beträgt aktuell 1,84 % (Gesamtbeitrag 16,44 %).</div>
</div>
</body></html>
"""

_SAMPLE_WEATHER_HTML = """
<html><body>
<div class="result">
    <a class="result__a" href="https://wetter.example.com/berlin">Wetter Berlin</a>
    <div class="result__snippet">Aktuell in Berlin: -1,5 °C bei leichtem Frost und schwachem Wind.</div>
</div>
</body></html>
"""


class DummySearchHttpFetcher(SafeHttpFetcher):
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        for key, body in self._responses.items():
            if key in url:
                return SafeHttpResponse(url, 200, "text/html", body, 15)
        return SafeHttpResponse(url, 404, "text/plain", "Not Found", 10)

    def post(
        self,
        url: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> SafeHttpResponse:
        return self.fetch(url, headers=headers)


class ErrorProneLLMGateway:
    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        raise LLMProviderError("429 Quota Exceeded")

    def generate_grounded_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        raise LLMProviderError("429 Quota Exceeded")


def test_price_detector_change_monitoring_via_free_search() -> None:
    fetcher = DummySearchHttpFetcher({"duckduckgo.com": _SAMPLE_BKK_HTML})
    detector = PriceThresholdDetector(
        http_fetcher=fetcher,
        llm_gateway=ErrorProneLLMGateway(),
    )

    rule = AlertRule(
        id="bkk_firmus_rule",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="Alarmiere, wenn sich der Zusatzbeitragssatz von BKK Firmus ändert",
        detector_type=DetectorType.PRICE_THRESHOLD,
        search_query="BKK firmus Zusatzbeitrag aktuell",
        threshold=ThresholdSpec(target_value=None, unit="%"),
        last_value_text="1,84 %",
        notify_on_first_match=False,
    )

    finding = detector.evaluate(rule)
    assert finding.measured_value == 1.84
    assert finding.condition_met is False  # 1.84 == 1.84, unverändert
    assert "Wert unverändert" in finding.summary


def test_price_detector_detects_value_change_via_free_search() -> None:
    fetcher = DummySearchHttpFetcher({"duckduckgo.com": _SAMPLE_BKK_HTML})
    detector = PriceThresholdDetector(
        http_fetcher=fetcher,
        llm_gateway=None,
    )

    rule = AlertRule(
        id="bkk_firmus_rule",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="Alarmiere, wenn sich der Zusatzbeitragssatz von BKK Firmus ändert",
        detector_type=DetectorType.PRICE_THRESHOLD,
        search_query="BKK firmus Zusatzbeitrag aktuell",
        threshold=ThresholdSpec(target_value=None, unit="%"),
        last_value_text="1,50 %",  # Vorher 1.50%, jetzt 1.84%
        notify_on_first_match=False,
    )

    finding = detector.evaluate(rule)
    assert finding.measured_value == 1.84
    assert finding.condition_met is True
    assert "Wert geändert (vorher: 1,5 %)" in finding.summary


def test_weather_detector_falls_back_to_free_search_on_open_meteo_failure() -> None:
    fetcher = DummySearchHttpFetcher({"duckduckgo.com": _SAMPLE_WEATHER_HTML})
    detector = WeatherThresholdDetector(
        http_fetcher=fetcher,
        llm_gateway=ErrorProneLLMGateway(),
    )

    rule = AlertRule(
        id="weather_berlin_frost",
        title="Temperaturwarnung Berlin Frost",
        user_prompt="Alarmiere, wenn Temperatur in Berlin <= 0 Grad fällt",
        detector_type=DetectorType.WEATHER_THRESHOLD,
        search_query="Berlin",
        threshold=ThresholdSpec(
            operator=ComparisonOperator.LTE,
            target_value=0.0,
            weather_metric=WeatherMetric.TEMPERATURE_C,
            location_name="Berlin",
            unit="°C",
        ),
        trigger_mode=TriggerMode.ON_STATE_CHANGE,
    )

    finding = detector.evaluate(rule)
    assert finding.measured_value == -1.5
    assert finding.condition_met is True
    assert "erfüllt" in finding.summary


def test_price_detector_ignores_outdated_historical_year_rates() -> None:
    html_with_old_and_new = """
    <html><body>
    <div class="result">
        <a class="result__a" href="https://krankenkassen.example.com/bkk-firmus">BKK firmus Zusatzbeitrag</a>
        <div class="result__snippet">Historie: 2020 lag der Zusatzbeitrag bei 0,44 %. Aktuell beträgt er 2,18 %.</div>
    </div>
    </body></html>
    """
    fetcher = DummySearchHttpFetcher({"duckduckgo.com": html_with_old_and_new})
    detector = PriceThresholdDetector(
        http_fetcher=fetcher,
        llm_gateway=None,
    )
    rule = AlertRule(
        id="bkk_recency_rule",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="Alarmiere, wenn sich der Zusatzbeitragssatz von BKK Firmus ändert",
        detector_type=DetectorType.PRICE_THRESHOLD,
        search_query="BKK firmus Zusatzbeitrag",
        threshold=ThresholdSpec(target_value=None, unit="%"),
        last_value_text="",
        notify_on_first_match=False,
    )
    finding = detector.evaluate(rule)
    # Muss 2.18 % wählen und nicht den veralteten 2020-Wert von 0.44 %
    assert finding.measured_value == 2.18
    assert "2,18 %" in finding.measured_display

