"""Tests für Such-Eskalation, Low-Signal-Fallback, Fokus-Auszug, Schema-Migration & Check-Diagnose."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, TypeVar

import ai_brain
import pytest
from ai_brain import (
    AppBrainPolicy,
    BrainCitation,
    BrainFetchedPage,
    BrainSearchResult,
    BrainTier,
)
from pydantic import BaseModel
from src.config.settings import AppSettings
from src.detectors.diagnostics import (
    deserialize_diagnostics,
    format_diagnostics_one_liner,
    serialize_diagnostics,
)
from src.detectors.engine import AlertEvaluationEngine
from src.detectors.free_search_parsers import parse_yahoo_html
from src.detectors.http_client import SafeHttpFetcher, SafeHttpResponse
from src.detectors.metric_grounding import (
    build_relaxed_metric_query,
    extract_focused_page_excerpt,
    rank_relevant_hits,
)
from src.detectors.price_threshold_detector import PriceThresholdDetector
from src.detectors.search_provider import BrainWebSearchProvider, FreeWebSearchProvider
from src.domain.enums import CheckOutcome, DetectorType, RuleStatus, TriggerMode
from src.domain.models import (
    AlertEvent,
    AlertRule,
    CheckDiagnostics,
    CheckLogEntry,
    DiscoveredItem,
    ThresholdSpec,
)
from src.domain.schemas import AiMetricExtraction
from src.storage.repository import SqlAlertRepository
from src.storage.sqlite_executor import SqliteExecutor
from streamlit.testing.v1 import AppTest

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ENTRYPOINT = Path(__file__).resolve().parent.parent / "streamlit_app.py"


class _StubHttp(SafeHttpFetcher):
    def __init__(self, responses: dict[str, tuple[int, str]]) -> None:
        self._responses = responses

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        for key, (code, body) in self._responses.items():
            if key in url:
                return SafeHttpResponse(url, code, "text/html", body, 12)
        return SafeHttpResponse(url, 404, "text/plain", "Not Found", 5)

    def post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> SafeHttpResponse:
        return self.fetch(url, headers=headers)


class _NoopNotifier:
    def dispatch_event(self, rule: AlertRule, event: AlertEvent) -> tuple[tuple[str, ...], bool]:
        return (), True


class _FakeBrainMultiPass:
    """Simuliert `ai_brain.connect()` mit Tier-spezifischen Suchergebnissen und optionalem fetch_page."""

    def __init__(
        self,
        *,
        fast_result: BrainSearchResult,
        standard_result: BrainSearchResult | None = None,
        fetched_page: BrainFetchedPage | None = None,
        policy: AppBrainPolicy | None = None,
    ) -> None:
        self.fast_result = fast_result
        self.standard_result = standard_result
        self.fetched_page = fetched_page
        self.policy = policy or AppBrainPolicy(
            llm_tier=BrainTier.FAST,
            search_tier=BrainTier.FAST,
            maps_mode="auto",
            allow_paid=False,
        )
        self.search_calls: list[dict[str, Any]] = []
        self.fetch_calls: list[tuple[str, bool]] = []

    def __enter__(self) -> _FakeBrainMultiPass:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_app_policy(self, app_name: str = "") -> AppBrainPolicy:
        return self.policy

    def search(
        self,
        query: str,
        *,
        tier: BrainTier | str = "auto",
        max_results: int = 8,
        allow_paid: bool | None = None,
    ) -> BrainSearchResult:
        self.search_calls.append({"query": query, "tier": tier, "allow_paid": allow_paid})
        if tier in {BrainTier.STANDARD, "standard"} and self.standard_result is not None:
            return self.standard_result
        return self.fast_result

    def fetch_page(self, url: str, *, allow_paid: bool = False) -> BrainFetchedPage:
        self.fetch_calls.append((url, allow_paid))
        assert self.fetched_page is not None
        return self.fetched_page


def _bkk_rule(target_url: str = "") -> AlertRule:
    return AlertRule(
        id="bkk_prod",
        title="BKK Firmus Zusatzbeitragssatz überwachen",
        user_prompt="Alarmiere, wenn sich der Zusatzbeitragssatz von BKK Firmus ändert.",
        detector_type=DetectorType.PRICE_THRESHOLD,
        status=RuleStatus.ACTIVE,
        search_query="BKK Firmus Zusatzbeitragssatz aktuell",
        target_url=target_url,
        threshold=ThresholdSpec(target_value=None, unit="%"),
        trigger_mode=TriggerMode.ON_STATE_CHANGE,
        last_value_text="2,18 %",
        created_at="2026-11-20T10:00:00+00:00",
        updated_at="2026-11-22T10:00:00+00:00",
    )


class TestQueryRelaxationAndFocusedExcerpt:
    def test_build_relaxed_metric_query_simplifies_german_compound_and_noise(self) -> None:
        assert build_relaxed_metric_query("BKK Firmus Zusatzbeitragssatz aktuell") == "BKK Firmus Zusatzbeitrag"
        assert build_relaxed_metric_query("Goldpreis EUR") == ""

    def test_extract_focused_page_excerpt_captures_late_table_match(self) -> None:
        filler = "Navigation Menü Datenschutz Impressum Kontakt " * 250  # > 11.000 Zeichen
        late_section = "Tarifübersicht: Der aktuelle Zusatzbeitragssatz der BKK firmus liegt bei 2,18 %."
        full_text = f"Startseite Krankenkassen\n{filler}\n{late_section}\n{filler[:1200]}"
        excerpt = extract_focused_page_excerpt(full_text, "BKK Firmus Zusatzbeitragssatz", max_chars=4000)
        assert len(excerpt) <= 4000
        assert "2,18 %" in excerpt
        assert "BKK firmus" in excerpt

    def test_rank_relevant_hits_prioritizes_direct_web_and_keeps_gemini_grounding(self) -> None:
        hits = [
            DiscoveredItem(
                "fp_news",
                "BKK Firmus: So hoch ist der Zusatzbeitrag 2027 - Lausitzer Rundschau",
                "https://news.google.com/rss/articles/abc",
                "BKK Firmus: So hoch ist der Zusatzbeitrag 2027",
                source_tool="google_news_rss",
            ),
            DiscoveredItem(
                "fp_wiki",
                "Wikipedia: Zusatzbeitrag",
                "https://de.wikipedia.org/wiki/Zusatzbeitrag",
                "Unter den bundesweit geöffneten Krankenkassen erhebt 2026 die TUI BKK mit 2",
                source_tool="wikipedia_api",
            ),
            DiscoveredItem(
                "fp_gem",
                "krankenkassen.de",
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/xyz",
                "",
                source_tool="gemini_google_search",
            ),
            DiscoveredItem(
                "fp_official",
                "Beitragssätze 2026",
                "https://www.bkk-firmus.de/beitragssaetze",
                "Aktueller Zusatzbeitrag: 2,18 %.",
                source_tool="yahoo_search",
            ),
        ]
        ranked = rank_relevant_hits(hits, "BKK Firmus Zusatzbeitragssatz aktuell", explicit_unit="%")
        urls = [it.url for it, _ in ranked]
        assert urls[0] == "https://www.bkk-firmus.de/beitragssaetze"
        assert "https://vertexaisearch.cloud.google.com/grounding-api-redirect/xyz" in urls
        assert urls.index("https://vertexaisearch.cloud.google.com/grounding-api-redirect/xyz") < urls.index(
            "https://de.wikipedia.org/wiki/Zusatzbeitrag"
        )


class TestLowSignalFallbackAndEscalation:
    def test_brain_search_supplements_with_free_fallback_when_only_news_and_wiki_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fast_only_low_signal = BrainSearchResult(
            query="BKK Firmus Zusatzbeitragssatz aktuell",
            tier=BrainTier.FAST,
            tools_executed=["yahoo_search", "searxng_public", "google_news_rss", "wikipedia_api"],
            tool_errors={"searxng_public": "timeout"},
            citations=[
                BrainCitation(
                    url="https://news.google.com/rss/articles/lr123",
                    title="BKK Firmus: So hoch ist der Zusatzbeitrag 2027 - Lausitzer Rundschau",
                    snippet="BKK Firmus: So hoch ist der Zusatzbeitrag 2027",
                    source_tool="google_news_rss",
                ),
                BrainCitation(
                    url="https://de.wikipedia.org/wiki/Zusatzbeitrag",
                    title="Wikipedia: Zusatzbeitrag",
                    snippet="Unter den Kassen erhebt 2026 die TUI BKK mit 2",
                    source_tool="wikipedia_api",
                ),
            ],
            latency_ms=3200,
        )
        fake_brain = _FakeBrainMultiPass(fast_result=fast_only_low_signal)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        ddg_html = """
        <html><body><div class="result">
            <a class="result__a" href="https://www.bkk-firmus.de/beitraege">BKK firmus Beitragssatz</a>
            <div class="result__snippet">Der Zusatzbeitragssatz der BKK firmus beträgt aktuell 2,18 %.</div>
        </div></body></html>
        """
        http = _StubHttp({"duckduckgo.com/html": (200, ddg_html)})
        provider = BrainWebSearchProvider(http_fetcher=http)
        items = provider.search("BKK Firmus Zusatzbeitragssatz aktuell", max_results=6)

        assert items[0].url == "https://www.bkk-firmus.de/beitraege"
        diag = provider.get_last_search_diagnostics()
        assert diag is not None
        assert diag.fallback_used is True
        assert diag.provider == "ai_brain+free"
        assert "yahoo_search" in diag.tools_executed
        assert "duckduckgo_html" in diag.tools_executed
        assert any("yahoo_search: 0 Treffer" in err for err in diag.tool_errors)
        assert any("searxng_public: timeout" in err for err in diag.tool_errors)

    def test_price_detector_escalates_tier_and_extracts_from_gemini_grounding_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fast_low_signal = BrainSearchResult(
            query="BKK Firmus Zusatzbeitragssatz aktuell",
            tier=BrainTier.FAST,
            tools_executed=["yahoo_search", "google_news_rss", "wikipedia_api"],
            citations=[
                BrainCitation(
                    url="https://news.google.com/rss/articles/lr123",
                    title="BKK Firmus: So hoch ist der Zusatzbeitrag 2027 - Lausitzer Rundschau",
                    snippet="BKK Firmus: So hoch ist der Zusatzbeitrag 2027",
                    source_tool="google_news_rss",
                ),
                BrainCitation(
                    url="https://de.wikipedia.org/wiki/Zusatzbeitrag",
                    title="Wikipedia: Zusatzbeitrag",
                    snippet="Allgemeine Infos ohne BKK Firmus Satz.",
                    source_tool="wikipedia_api",
                ),
            ],
        )
        standard_grounded = BrainSearchResult(
            query="BKK Firmus Zusatzbeitrag",
            tier=BrainTier.STANDARD,
            tools_executed=["yahoo_search", "gemini_google_search"],
            citations=[
                BrainCitation(
                    url="https://www.bkk-firmus.de/beitraege",
                    title="bkk-firmus.de",
                    snippet="",
                    source_tool="gemini_google_search",
                )
            ],
        )
        fake_brain = _FakeBrainMultiPass(
            fast_result=fast_low_signal,
            standard_result=standard_grounded,
        )
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        long_nav = "Menü Punkt Service Leistungen Karriere " * 220
        official_html = (
            f"<html><body><nav>{long_nav}</nav>"
            "<main><h1>Beiträge</h1><p>Der aktuelle Zusatzbeitragssatz der BKK firmus beträgt 2,18 %.</p></main>"
            "</body></html>"
        )
        wiki_html = "<html><body><h1>Zusatzbeitrag</h1><p>TUI BKK erhebt einen Zusatzbeitrag.</p></body></html>"
        http = _StubHttp(
            {
                "duckduckgo.com": (202, "<html>Anti-Bot</html>"),
                "search.yahoo.com": (200, "<html>Keine Ergebnisse</html>"),
                "de.wikipedia.org": (200, wiki_html),
                "www.bkk-firmus.de/beitraege": (200, official_html),
            }
        )

        class _PageOnlyLLM:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            def generate_structured(
                self, prompt: str, system_instruction: str, schema_cls: type[SchemaT]
            ) -> SchemaT:
                self.prompts.append(prompt)
                if "2,18 %" in prompt:
                    return AiMetricExtraction(  # type: ignore[return-value]
                        value_found=True,
                        numeric_value=2.18,
                        unit="%",
                        context_summary="Offizielle BKK firmus Seite",
                    )
                return AiMetricExtraction(value_found=False, numeric_value=None, unit="%")  # type: ignore[return-value]

        llm = _PageOnlyLLM()
        provider = BrainWebSearchProvider(http_fetcher=http)
        detector = PriceThresholdDetector(http_fetcher=http, llm_gateway=llm, search_provider=provider)  # type: ignore[arg-type]

        finding = detector.evaluate(_bkk_rule())
        assert finding.measured_value == 2.18
        assert finding.source_url == "https://www.bkk-firmus.de/beitraege"
        assert finding.diagnostics is not None
        assert "standard" in finding.diagnostics.provider
        assert fake_brain.search_calls[1]["query"] == "BKK Firmus Zusatzbeitrag"
        assert fake_brain.search_calls[1]["tier"] == BrainTier.STANDARD
        assert fake_brain.search_calls[1]["allow_paid"] is False

    def test_price_detector_uses_ai_brain_fetch_page_fallback_on_http_403(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_brain = _FakeBrainMultiPass(
            fast_result=BrainSearchResult(query="q", tier=BrainTier.FAST),
            fetched_page=BrainFetchedPage(
                url="https://www.bkk-firmus.de/beitraege",
                title="[Jina Reader] bkk-firmus.de",
                text_content="BKK firmus: Der Zusatzbeitragssatz beträgt aktuell 2,18 %.",
                extractor_used="jina_reader",
                latency_ms=250,
            ),
        )
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        http = _StubHttp({"www.bkk-firmus.de/beitraege": (403, "Forbidden")})
        provider = BrainWebSearchProvider(http_fetcher=http)
        detector = PriceThresholdDetector(http_fetcher=http, llm_gateway=None, search_provider=provider)

        finding = detector.evaluate(_bkk_rule(target_url="https://www.bkk-firmus.de/beitraege"))
        assert finding.measured_value == 2.18
        assert fake_brain.fetch_calls == [("https://www.bkk-firmus.de/beitraege", False)]
        assert finding.diagnostics is not None
        assert "jina_reader" in finding.diagnostics.tools_used


class TestDiagnosticsPersistenceMigrationAndUI:
    def test_engine_persists_rich_error_diagnostics_in_check_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        low_signal = BrainSearchResult(
            query="BKK Firmus Zusatzbeitragssatz aktuell",
            tier=BrainTier.FAST,
            tools_executed=["yahoo_search", "google_news_rss", "wikipedia_api"],
            citations=[
                BrainCitation(
                    url="https://news.google.com/rss/articles/lr123",
                    title="BKK Firmus: So hoch ist der Zusatzbeitrag 2027 - Lausitzer Rundschau",
                    snippet="BKK Firmus: So hoch ist der Zusatzbeitrag 2027",
                    source_tool="google_news_rss",
                ),
            ],
        )
        fake_brain = _FakeBrainMultiPass(fast_result=low_signal, standard_result=low_signal)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        http = _StubHttp({"duckduckgo.com": (202, ""), "search.yahoo.com": (429, "")})
        repo = SqlAlertRepository(SqliteExecutor(":memory:"))
        try:
            rule = _bkk_rule()
            repo.upsert_rule(rule)
            provider = BrainWebSearchProvider(http_fetcher=http)
            engine = AlertEvaluationEngine(
                AppSettings(), repo, _NoopNotifier(), http_fetcher=http, llm_gateway=None, search_provider=provider
            )
            outcome = engine.evaluate_rule(rule)

            assert outcome.outcome == CheckOutcome.ERROR
            assert "nur News-/Wikipedia-Treffer" in outcome.error_message
            assert outcome.diagnostics is not None
            assert outcome.diagnostics.hint != ""
            assert "yahoo_search" in outcome.diagnostics.tools_used
            assert "google_news_rss" in outcome.diagnostics.tools_used

            logs = repo.list_recent_check_logs_by_rule(per_rule_limit=5)[rule.id]
            assert len(logs) == 1
            assert logs[0].details_json != ""
            restored = deserialize_diagnostics(logs[0].details_json)
            assert restored is not None
            assert restored.raw_hits >= 1
            assert "Direkte Ziel-URL" in restored.hint
            one_liner = format_diagnostics_one_liner(restored)
            assert "Engine:" in one_liner and "Tools:" in one_liner
        finally:
            repo.close()

    def test_legacy_sqlite_table_auto_migrates_details_json_column(self, tmp_path: Path) -> None:
        db_file = tmp_path / "legacy.db"
        with sqlite3.connect(db_file) as conn:
            conn.execute(
                """
                CREATE TABLE alert_check_logs (
                    id TEXT PRIMARY KEY,
                    rule_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    message TEXT NOT NULL,
                    measured_display TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO alert_check_logs (id, rule_id, outcome, message, measured_display, duration_ms, created_at)
                VALUES ('old1', 'r1', 'error', 'Alter Fehler', '', 500, '2026-11-22T03:29:00+00:00')
                """
            )

        repo = SqlAlertRepository(SqliteExecutor(db_file))
        try:
            old_logs = repo.list_check_logs("r1")
            assert len(old_logs) == 1
            assert old_logs[0].id == "old1"
            assert old_logs[0].details_json == ""

            diag_json = serialize_diagnostics(
                CheckDiagnostics(stage="web_search", provider="ai_brain (fast)", tools_used=("yahoo_search",))
            )
            repo.append_check_log(
                CheckLogEntry(
                    id="new2",
                    rule_id="r1",
                    outcome=CheckOutcome.ERROR,
                    message="Neuer Fehler",
                    duration_ms=420,
                    created_at="2026-11-23T15:14:00+00:00",
                    details_json=diag_json,
                )
            )
            grouped = repo.list_recent_check_logs_by_rule(per_rule_limit=5)["r1"]
            assert grouped[0].id == "new2"
            assert deserialize_diagnostics(grouped[0].details_json) is not None
        finally:
            repo.close()

    def test_ui_renders_last_error_diagnostics_and_detailed_trace_toggle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_file = tmp_path / "ui_diag.db"
        repo = SqlAlertRepository(SqliteExecutor(db_file))
        try:
            rule = _bkk_rule()
            rule = AlertRule(
                **{
                    **rule.__dict__,
                    "last_error": "Kein aktueller %-Wert in 2/7 Treffern (nur News-/Wikipedia-Treffer ohne Live-Zahl).",
                }
            )
            repo.upsert_rule(rule)
            diag_json = serialize_diagnostics(
                CheckDiagnostics(
                    stage="web_search",
                    provider="ai_brain+free (fast)",
                    query="BKK Firmus Zusatzbeitragssatz aktuell",
                    tools_used=("yahoo_search", "google_news_rss", "wikipedia_api"),
                    tool_errors=("yahoo_search: 0 Treffer",),
                    raw_hits=7,
                    relevant_hits=2,
                    sample_sources=("Wikipedia: Zusatzbeitrag (de.wikipedia.org · wikipedia_api)",),
                    llm_status="kein Wert in Snippets (value_found=false)",
                    steps=("Erstsuche: 7 Treffer", "KI-Snippet-Analyse: kein belegter Wert"),
                    hint="Direkte Ziel-URL unter Edit hinterlegen.",
                )
            )
            repo.append_check_log(
                CheckLogEntry(
                    id="log_err_1",
                    rule_id=rule.id,
                    outcome=CheckOutcome.ERROR,
                    message=rule.last_error,
                    duration_ms=14881,
                    created_at="2026-11-23T03:29:00+00:00",
                    details_json=diag_json,
                )
            )
        finally:
            repo.close()

        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", "")
        monkeypatch.setenv("WORKER_MODE", "github")

        at = AppTest.from_file(ENTRYPOINT).run()
        assert not at.exception
        assert any("nur News-/Wikipedia-Treffer" in w.value for w in at.warning)
        assert any("ai_brain+free (fast)" in c.value for c in at.caption)
        assert any("Direkte Ziel-URL unter Edit hinterlegen." in c.value for c in at.caption)

        at = at.checkbox(key="diag_trace_bkk_prod").check().run()
        assert not at.exception
        assert any("yahoo_search: 0 Treffer" in c.value for c in at.caption)
        assert any("Erstsuche: 7 Treffer" in m.value for m in at.markdown)

    def test_free_search_dual_dom_yahoo_and_diagnostics(self) -> None:
        modern_yahoo = """
        <html><body>
        <div class="algo">
            <div class="compTitle">
                <a aria-label="BKK firmus Beitragssätze 2026"
                   href="https://r.search.yahoo.com/_ylt=1/RU=https%3A%2F%2Fwww.bkk-firmus.de%2Fbeitraege/RK=2">
                   <h3>ignored inner</h3>
                </a>
            </div>
            <div class="compText"><p>Zusatzbeitrag aktuell 2,18 %.</p></div>
        </div>
        </body></html>
        """
        parsed = parse_yahoo_html(modern_yahoo, limit=5)
        assert len(parsed) == 1
        assert parsed[0].title == "BKK firmus Beitragssätze 2026"
        assert parsed[0].url == "https://www.bkk-firmus.de/beitraege"
        assert parsed[0].source_tool == "yahoo_search"

        http = _StubHttp({"duckduckgo.com": (202, ""), "search.yahoo.com": (200, modern_yahoo)})
        provider = FreeWebSearchProvider(http_fetcher=http)
        items = provider.search("BKK firmus Zusatzbeitrag")
        assert len(items) == 1
        diag = provider.get_last_search_diagnostics()
        assert diag is not None
        assert "duckduckgo_html: HTTP 202 (Anti-Bot)" in diag.tool_errors
        assert "yahoo_search" in diag.tools_executed
