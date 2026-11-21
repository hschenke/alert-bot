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


def test_price_detector_grounding_guard_overrides_hallucinated_llm_value() -> None:
    from src.domain.schemas import AiMetricExtraction

    class HallucinatingLLMGateway:
        def generate_structured(self, prompt: str, system_instruction: str, schema_cls: type) -> AiMetricExtraction:
            # KI halluziniert 0.44 % aus Vorwissen, obwohl Snippets 2.18 % angeben
            return AiMetricExtraction(
                value_found=True,
                numeric_value=0.44,
                unit="%",
                context_summary="Zusatzbeitragssatz der BKK firmus im aktuellen Jahr",
            )

    html_snippets = """
    <html><body>
    <div class="result">
        <a class="result__a" href="https://krankenkassen.example.com/bkk-firmus">BKK firmus Zusatzbeitrag</a>
        <div class="result__snippet">Die BKK firmus hält den Satz stabil. Aktuell beträgt der Zusatzbeitrag nur 2,18 %.</div>
    </div>
    </body></html>
    """
    fetcher = DummySearchHttpFetcher({"duckduckgo.com": html_snippets})
    detector = PriceThresholdDetector(
        http_fetcher=fetcher,
        llm_gateway=HallucinatingLLMGateway(),  # type: ignore[arg-type]
    )
    rule = AlertRule(
        id="bkk_grounding_rule",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="Alarmiere, wenn sich der Zusatzbeitragssatz von BKK Firmus ändert",
        detector_type=DetectorType.PRICE_THRESHOLD,
        search_query="BKK firmus Zusatzbeitragssatz aktuell",
        threshold=ThresholdSpec(target_value=None, unit="%"),
        last_value_text="",
        notify_on_first_match=False,
    )
    finding = detector.evaluate(rule)
    # Grounding-Guard muss die Halluzination (0.44) abwehren und den echten Web-Wert (2.18) nehmen!
    assert finding.measured_value == 2.18
    assert "2,18 %" in finding.measured_display


def test_price_detector_ignores_euro_amounts_and_url_digits_for_percent_rule() -> None:
    import pytest
    from src.detectors.comparators import parse_localized_float
    from src.domain.exceptions import DetectorExecutionError

    assert parse_localized_float("Spare bis zu 226 Euro im Jahr", unit="%", strict_unit=True) is None
    assert parse_localized_float("Token CBM226xyz Datum 19.11.2026", unit="%", strict_unit=True) is None
    assert parse_localized_float("Früher 0,44 %, aktuell 2,18 %", unit="%", strict_unit=True) == 2.18
    assert parse_localized_float("0,445 %", unit="%", strict_unit=True) == 0.445
    assert parse_localized_float("1.200.000 €") == 1200000.0

    # Google News liefert nur generische Headline ohne % und mit Base64-URL voller Ziffern (226)
    bogus_news_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
        <item>
            <title>Krankenkassen-Hammer: Mit Wechsel 226 Euro sparen - Börse Online</title>
            <link>https://news.google.com/rss/articles/CBM226xyz999?oc=5</link>
            <pubDate>Thu, 19 Nov 2026 16:00:00 GMT</pubDate>
            <description>Krankenkassen-Hammer: BKK Firmus Vergleich 226 Euro Ersparnis.</description>
        </item>
    </channel></rss>
    """
    fetcher = DummySearchHttpFetcher({
        "duckduckgo.com": "<html><body>Keine Treffer</body></html>",
        "search.yahoo.com": "<html><body>Keine Treffer</body></html>",
        "news.google.com": bogus_news_rss,
    })
    detector = PriceThresholdDetector(http_fetcher=fetcher, llm_gateway=None)
    rule = AlertRule(
        id="bkk_bogus_guard",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="Alarmiere wenn BKK Firmus Zusatzbeitragssatz >= 2,18 %",
        detector_type=DetectorType.PRICE_THRESHOLD,
        search_query="BKK Firmus Zusatzbeitragssatz aktuell",
        threshold=ThresholdSpec(operator=ComparisonOperator.GTE, target_value=2.18, unit="%"),
    )
    with pytest.raises(DetectorExecutionError):
        detector.evaluate(rule)


def test_price_detector_attributes_exact_hit_source_url_and_ignores_irrelevant_first_hit() -> None:
    mixed_html = """
    <html><body>
    <div class="result">
        <a class="result__a" href="https://unrelated.example.com/spam">Krankenkassen-Hammer 226 % Wachstum</a>
        <div class="result__snippet">Allgemeiner Marktbericht über Neukunden ohne konkreten Beitragssatz.</div>
    </div>
    <div class="result">
        <a class="result__a" href="https://www.bkk-firmus.de/beitraege">BKK firmus Beitragssätze</a>
        <div class="result__snippet">Der Zusatzbeitragssatz der BKK firmus beträgt aktuell 2,18 %.</div>
    </div>
    </body></html>
    """
    fetcher = DummySearchHttpFetcher({"duckduckgo.com": mixed_html})
    detector = PriceThresholdDetector(http_fetcher=fetcher, llm_gateway=None)
    rule = AlertRule(
        id="bkk_source_match",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="BKK Firmus Zusatzbeitragssatz >= 2,18 %",
        detector_type=DetectorType.PRICE_THRESHOLD,
        search_query="BKK Firmus Zusatzbeitragssatz aktuell",
        threshold=ThresholdSpec(operator=ComparisonOperator.GTE, target_value=2.18, unit="%"),
    )
    finding = detector.evaluate(rule)
    assert finding.measured_value == 2.18
    assert finding.source_url == "https://www.bkk-firmus.de/beitraege"


def test_price_detector_respects_llm_rejection_of_outlier_and_falls_back_to_direct_page() -> None:
    from src.domain.schemas import AiMetricExtraction

    class RejectingSnippetButPageExtractingLLM:
        def generate_structured(self, prompt: str, system_instruction: str, schema_cls: type) -> AiMetricExtraction:
            if "Aktuelle Web-Suchergebnisse:" in prompt:
                # Erkennt, dass "226 % mehr Neukunden" NICHT der Zusatzbeitragssatz ist!
                return AiMetricExtraction(value_found=False, numeric_value=None, unit="%")
            return AiMetricExtraction(
                value_found=True,
                numeric_value=2.18,
                unit="%",
                context_summary="Offizieller Zusatzbeitragssatz BKK firmus",
            )

    ddg_html = """
    <html><body>
    <div class="result">
        <a class="result__a" href="https://www.bkk-firmus.de/zusatzbeitrag">BKK Firmus News</a>
        <div class="result__snippet">BKK Firmus gewinnt 226 % mehr Neukunden im Quartal.</div>
    </div>
    </body></html>
    """
    direct_page_html = """
    <html><body><h1>BKK firmus Beiträge 2026</h1>
    <p>Der aktuelle Zusatzbeitragssatz beträgt 2,18 %.</p></body></html>
    """
    fetcher = DummySearchHttpFetcher({
        "duckduckgo.com": ddg_html,
        "www.bkk-firmus.de/zusatzbeitrag": direct_page_html,
    })
    detector = PriceThresholdDetector(
        http_fetcher=fetcher,
        llm_gateway=RejectingSnippetButPageExtractingLLM(),  # type: ignore[arg-type]
    )
    rule = AlertRule(
        id="bkk_outlier_reject",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="BKK Firmus Zusatzbeitragssatz >= 2,18 %",
        detector_type=DetectorType.PRICE_THRESHOLD,
        search_query="BKK Firmus Zusatzbeitragssatz aktuell",
        threshold=ThresholdSpec(operator=ComparisonOperator.GTE, target_value=2.18, unit="%"),
    )
    finding = detector.evaluate(rule)
    assert finding.measured_value == 2.18
    assert finding.source_url == "https://www.bkk-firmus.de/zusatzbeitrag"


def test_weather_detector_falls_back_to_bright_sky_dwd_when_open_meteo_times_out(monkeypatch) -> None:
    import json

    from src.detectors import weather_detector
    from src.domain.exceptions import DetectorExecutionError

    monkeypatch.setattr(weather_detector.time, "sleep", lambda _s: None)
    geo_json = json.dumps(
        {"results": [{"name": "Berlin", "country": "Deutschland", "latitude": 52.52, "longitude": 13.405}]}
    )
    bright_sky_json = json.dumps({"weather": {"temperature": 3.4, "wind_speed_10": 14.2, "precipitation_10": 0.0}})

    class TimeoutOpenMeteoFetcher(SafeHttpFetcher):
        def fetch(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
            if "geocoding-api.open-meteo.com" in url:
                return SafeHttpResponse(url, 200, "application/json", geo_json, 10)
            if "api.open-meteo.com/v1/forecast" in url:
                raise DetectorExecutionError("HTTP-Abruf fehlgeschlagen: Read timed out.")
            if "api.brightsky.dev/current_weather" in url:
                return SafeHttpResponse(url, 200, "application/json", bright_sky_json, 15)
            return SafeHttpResponse(url, 404, "text/plain", "Not Found", 5)

    detector = WeatherThresholdDetector(http_fetcher=TimeoutOpenMeteoFetcher(), llm_gateway=None)
    rule = AlertRule(
        id="wx_dwd",
        title="Temperaturwarnung Berlin Frost",
        user_prompt="Temperatur in Berlin <= 0 °C",
        detector_type=DetectorType.WEATHER_THRESHOLD,
        threshold=ThresholdSpec(
            operator=ComparisonOperator.LTE,
            target_value=0.0,
            weather_metric=WeatherMetric.TEMPERATURE_C,
            location_name="Berlin",
            unit="°C",
        ),
    )
    finding = detector.evaluate(rule)
    assert finding.measured_value == 3.4
    assert finding.condition_met is False
    assert finding.source_url == "https://brightsky.dev/"
    assert "Berlin, Deutschland" in finding.measured_display


def test_weather_detector_falls_back_to_wttr_in_for_apparent_temperature() -> None:
    import json

    wttr_json = json.dumps({"current_condition": [{"temp_C": "4", "FeelsLikeC": "-1.5", "windspeedKmph": "18", "precipMM": "0.0"}]})
    fetcher = DummySearchHttpFetcher({"wttr.in/Berlin": wttr_json})
    detector = WeatherThresholdDetector(http_fetcher=fetcher, llm_gateway=None)

    rule = AlertRule(
        id="wx_wttr",
        title="Gefühlte Temperatur Berlin",
        user_prompt="Gefühlte Temperatur Berlin <= 0 °C",
        detector_type=DetectorType.WEATHER_THRESHOLD,
        threshold=ThresholdSpec(
            operator=ComparisonOperator.LTE,
            target_value=0.0,
            weather_metric=WeatherMetric.APPARENT_TEMPERATURE_C,
            location_name="Berlin",
            unit="°C",
        ),
    )
    finding = detector.evaluate(rule)
    assert finding.measured_value == -1.5
    assert finding.condition_met is True
    assert finding.source_url == "https://wttr.in/"


def test_weather_detector_retries_transient_open_meteo_timeout(monkeypatch) -> None:
    import json

    from src.detectors import weather_detector
    from src.domain.exceptions import DetectorExecutionError

    monkeypatch.setattr(weather_detector.time, "sleep", lambda _s: None)
    geo_json = json.dumps(
        {"results": [{"name": "Berlin", "country": "Deutschland", "latitude": 52.52, "longitude": 13.405}]}
    )
    wx_json = json.dumps({"current": {"temperature_2m": 5.2}})
    forecast_attempts = {"count": 0}

    class FlakyOpenMeteoFetcher(SafeHttpFetcher):
        def fetch(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
            if "geocoding-api.open-meteo.com" in url:
                return SafeHttpResponse(url, 200, "application/json", geo_json, 10)
            if "api.open-meteo.com/v1/forecast" in url:
                forecast_attempts["count"] += 1
                if forecast_attempts["count"] == 1:
                    raise DetectorExecutionError("HTTP-Abruf fehlgeschlagen: Read timed out.")
                return SafeHttpResponse(url, 200, "application/json", wx_json, 20)
            return SafeHttpResponse(url, 404, "text/plain", "Not Found", 5)

    detector = WeatherThresholdDetector(http_fetcher=FlakyOpenMeteoFetcher(), llm_gateway=None)
    rule = AlertRule(
        id="wx_retry",
        title="Berlin Frost",
        user_prompt="Berlin <= 0 °C",
        detector_type=DetectorType.WEATHER_THRESHOLD,
        threshold=ThresholdSpec(
            operator=ComparisonOperator.LTE,
            target_value=0.0,
            weather_metric=WeatherMetric.TEMPERATURE_C,
            location_name="Berlin",
            unit="°C",
        ),
    )
    finding = detector.evaluate(rule)
    assert forecast_attempts["count"] == 2
    assert finding.measured_value == 5.2
    assert finding.source_url == "https://open-meteo.com/"


def test_weather_web_fallback_rejects_wikipedia_historical_temperature_records() -> None:
    import pytest
    from src.domain.exceptions import DetectorExecutionError
    from src.domain.models import DiscoveredItem

    class WikiOnlySearchProvider:
        def search(self, query: str, max_results: int = 10) -> list[DiscoveredItem]:
            return [
                DiscoveredItem(
                    fingerprint="wiki_rec",
                    title="Zeitreihe der Lufttemperatur in Deutschland",
                    url="https://de.wikipedia.org/wiki/Zeitreihe_der_Lufttemperatur_in_Deutschland",
                    summary="Der Hitzerekord in Berlin lag bei 39,9 °C.",
                )
            ]

    detector = WeatherThresholdDetector(
        http_fetcher=DummySearchHttpFetcher({}),
        llm_gateway=None,
        search_provider=WikiOnlySearchProvider(),
    )
    rule = AlertRule(
        id="wx_no_wiki",
        title="Temperaturwarnung Berlin Frost",
        user_prompt="Temperatur in Berlin <= 0 °C",
        detector_type=DetectorType.WEATHER_THRESHOLD,
        threshold=ThresholdSpec(
            operator=ComparisonOperator.LTE,
            target_value=0.0,
            weather_metric=WeatherMetric.TEMPERATURE_C,
            location_name="Berlin",
            unit="°C",
        ),
    )
    with pytest.raises(DetectorExecutionError, match="Keine aktuellen Live-Wetterdaten"):
        detector.evaluate(rule)



