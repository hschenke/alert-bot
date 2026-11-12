"""Tests für den kostenfreien Multi-Engine Web-Such-Provider und 429-Quota-Resilienz."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from src.detectors.http_client import SafeHttpFetcher, SafeHttpResponse
from src.detectors.rule_compiler import AlertRuleCompiler
from src.detectors.search_provider import FreeWebSearchProvider
from src.detectors.web_search_detector import WebSearchDetector
from src.domain.enums import ComparisonOperator, DetectorType, TriggerMode
from src.domain.exceptions import LLMProviderError
from src.domain.models import AlertRule, ThresholdSpec

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_SAMPLE_DDG_HTML = """
<html><body>
<div class="result">
    <a class="result__a" href="https://example.com/l/?uddg=https%3A%2F%2Fimmo.example.com%2Fwohnung1">2-Zimmer-Wohnung in Altona</a>
    <div class="result__snippet">Schöne 55m² Wohnung für 950 Euro warm verfügbar.</div>
</div>
<div class="result">
    <a class="result__a" href="https://immo.example.com/wohnung2">3-Zimmer-Altbau Altona</a>
    <div class="result__snippet">Großzügige 80m² Wohnung für 1400 Euro.</div>
</div>
</body></html>
"""

_SAMPLE_DDG_LITE = """
<html><body>
<table class="result">
    <tr><td><a class="result-link" href="https://immo.example.com/lite1">Helle Wohnung Ottensen</a></td></tr>
    <tr><td class="result-snippet">Ruhige Lage, Balkon, 850 Euro Kaltmiete.</td></tr>
</table>
</body></html>
"""

_SAMPLE_GOOGLE_NEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
    <item>
        <title>Neues Wohnbauprojekt in Hamburg Altona gestartet</title>
        <link>https://news.example.com/altona-wohnbau</link>
        <description>&lt;p&gt;Bis 2027 entstehen 300 geförderte Wohnungen.&lt;/p&gt;</description>
    </item>
</channel>
</rss>
"""


class MockHttpFetcher(SafeHttpFetcher):
    def __init__(self, responses: dict[str, str]) -> None:
        self._mock_responses = responses

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        for key, body in self._mock_responses.items():
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
    """Simuliert einen Gemini-Aufruf, der mit Quota-429 fehlschlägt."""

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        raise LLMProviderError("Error code: 429 - You exceeded your current quota")

    def generate_grounded_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        raise LLMProviderError("Error code: 429 - You exceeded your current quota")


class TestFreeWebSearchProvider:
    def test_parses_ddg_html_and_unquotes_redirects(self) -> None:
        fetcher = MockHttpFetcher({"duckduckgo.com/html": _SAMPLE_DDG_HTML})
        provider = FreeWebSearchProvider(http_fetcher=fetcher)
        items = provider.search("Wohnung Altona")

        assert len(items) >= 2
        assert items[0].title == "2-Zimmer-Wohnung in Altona"
        assert items[0].url == "https://immo.example.com/wohnung1"
        assert "950 Euro" in items[0].summary

    def test_parses_ddg_html_preserves_spaces_between_tags(self) -> None:
        html = """
        <html><body>
        <div class="result">
            <a class="result__a" href="https://example.com/test">Titel <b>Hervorhebung</b> Text</a>
            <div class="result__snippet">Dieser beträgt <b>aktuell</b> nur <b>2,18 %</b> Zusatzbeitrag.</div>
        </div>
        </body></html>
        """
        fetcher = MockHttpFetcher({"duckduckgo.com/html": html})
        provider = FreeWebSearchProvider(http_fetcher=fetcher)
        items = provider.search("test")
        assert len(items) == 1
        assert items[0].title == "Titel Hervorhebung Text"
        assert items[0].summary == "Dieser beträgt aktuell nur 2,18 % Zusatzbeitrag."

    def test_falls_back_to_ddg_lite_when_html_empty(self) -> None:
        fetcher = MockHttpFetcher({"duckduckgo.com/lite": _SAMPLE_DDG_LITE})
        provider = FreeWebSearchProvider(http_fetcher=fetcher)
        items = provider.search("Wohnung Ottensen")

        assert len(items) == 1
        assert items[0].title == "Helle Wohnung Ottensen"
        assert items[0].url == "https://immo.example.com/lite1"

    def test_parses_google_news_rss(self) -> None:
        fetcher = MockHttpFetcher({"news.google.com": _SAMPLE_GOOGLE_NEWS_RSS})
        provider = FreeWebSearchProvider(http_fetcher=fetcher)
        items = provider.search("Wohnbau Hamburg News")

        assert len(items) == 1
        assert "Neues Wohnbauprojekt" in items[0].title
        assert items[0].url == "https://news.example.com/altona-wohnbau"


class TestWebSearchDetectorQuotaResilience:
    def test_detector_falls_back_to_heuristics_on_quota_429(self) -> None:
        fetcher = MockHttpFetcher({"duckduckgo.com": _SAMPLE_DDG_HTML})
        llm_error = ErrorProneLLMGateway()
        detector = WebSearchDetector(fetcher, llm_gateway=llm_error)

        rule = AlertRule(
            id="quota_rule_1",
            title="Wohnungssuche Altona",
            user_prompt="2-Zimmer-Wohnung Hamburg Altona bis 1000 Euro",
            detector_type=DetectorType.WEB_SEARCH,
            search_query="2-Zimmer-Wohnung Altona",
            threshold=ThresholdSpec(
                operator=ComparisonOperator.LTE,
                target_value=1000.0,
                unit="€",
            ),
            trigger_mode=TriggerMode.EVERY_NEW_ITEM,
        )

        finding = detector.evaluate(rule)
        assert finding.condition_met is True
        assert len(finding.items) >= 1
        assert "passende Treffer" in finding.summary
        # Verifiziert, dass die 950€ Wohnung gematcht hat
        assert any("950" in it.summary for it in finding.items)

    def test_detector_works_completely_without_llm(self) -> None:
        fetcher = MockHttpFetcher({"duckduckgo.com": _SAMPLE_DDG_HTML})
        detector = WebSearchDetector(fetcher, llm_gateway=None)

        rule = AlertRule(
            id="no_llm_rule",
            title="Wohnungssuche Altona",
            user_prompt="Wohnungen in Altona finden",
            detector_type=DetectorType.WEB_SEARCH,
            search_query="Altona",
            trigger_mode=TriggerMode.EVERY_NEW_ITEM,
        )

        finding = detector.evaluate(rule)
        assert finding.condition_met is True
        assert len(finding.items) == 2


class TestRuleCompilerQuotaResilience:
    def test_compiler_falls_back_to_heuristic_draft_on_llm_429(self) -> None:
        llm_error = ErrorProneLLMGateway()
        compiler = AlertRuleCompiler(llm_gateway=llm_error)

        draft = compiler.compile_from_prompt(
            "Beobachte die Temperatur in Freiburg sobald sie über 25 Grad steigt"
        )
        assert draft.detector_type == DetectorType.WEATHER_THRESHOLD.value
        assert draft.location_name == "Freiburg"
        assert draft.target_value == 25.0
