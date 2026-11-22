"""Gemockte Unit-Tests für die `ai_brain`-Adapter, `tier="auto"`-Routing, Fallbacks & Settings-UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import ai_brain
import pytest
from ai_brain import (
    AppBrainPolicy,
    BrainAuthError,
    BrainCitation,
    BrainProviderError,
    BrainSearchResult,
    BrainStorageError,
    BrainTier,
)
from pydantic import BaseModel
from src.config.settings import AppSettings
from src.detectors.engine import AlertEvaluationEngine
from src.detectors.http_client import SafeHttpFetcher, SafeHttpResponse
from src.detectors.llm_gateway import (
    BrainAlertGateway,
    GeminiAlertGateway,
    create_alert_llm_gateway,
)
from src.detectors.search_provider import (
    BrainWebSearchProvider,
    FreeWebSearchProvider,
    create_web_search_provider,
)
from src.domain.exceptions import LLMProviderError
from src.domain.models import AlertEvent, AlertRule, DiscoveredItem
from src.domain.schemas import (
    AiChangeEvaluation,
    AiMetricExtraction,
    AiSearchEvaluation,
    CompiledAlertDraft,
)
from src.security.sanitization import compute_item_fingerprint
from src.storage.repository import SqlAlertRepository
from src.storage.sqlite_executor import SqliteExecutor
from src.ui.views.new_alert_view import _build_compiler
from streamlit.testing.v1 import AppTest

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ENTRYPOINT = Path(__file__).resolve().parent.parent / "streamlit_app.py"


class _FakeBrainSession:
    """Kontextmanager-Stub für `ai_brain.connect()` mit Aufzeichnung aller Aufrufe."""

    def __init__(
        self,
        *,
        structured_result: BaseModel | None = None,
        structured_error: Exception | None = None,
        search_result: BrainSearchResult | None = None,
        search_error: Exception | None = None,
        policy: AppBrainPolicy | None = None,
        policy_load_error: Exception | None = None,
        policy_save_error: Exception | None = None,
    ) -> None:
        self.structured_result = structured_result
        self.structured_error = structured_error
        self.search_result = search_result
        self.search_error = search_error
        self.policy = policy or AppBrainPolicy(
            llm_tier=BrainTier.FAST,
            search_tier=BrainTier.FAST,
            maps_mode="auto",
            allow_paid=False,
        )
        self.policy_load_error = policy_load_error
        self.policy_save_error = policy_save_error
        self.entered = False
        self.exited = False
        self.structured_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.saved_policies: list[AppBrainPolicy] = []

    def __enter__(self) -> _FakeBrainSession:
        self.entered = True
        return self

    def __exit__(self, *args: object) -> None:
        self.exited = True

    def get_app_policy(self, app_name: str = "") -> AppBrainPolicy:
        if self.policy_load_error is not None:
            raise self.policy_load_error
        return self.policy

    def set_app_policy(self, policy: AppBrainPolicy, app_name: str = "") -> AppBrainPolicy:
        if self.policy_save_error is not None:
            raise self.policy_save_error
        self.policy = policy
        self.saved_policies.append(policy)
        return policy

    def generate_structured(
        self,
        prompt: str,
        schema_cls: type[SchemaT],
        *,
        tier: str = "fast",
        system_instruction: str = "",
        allow_paid: bool | None = None,
    ) -> tuple[SchemaT, str]:
        self.structured_calls.append(
            {
                "prompt": prompt,
                "schema_cls": schema_cls,
                "tier": tier,
                "system_instruction": system_instruction,
                "allow_paid": allow_paid,
            }
        )
        if self.structured_error is not None:
            raise self.structured_error
        assert self.structured_result is not None
        return self.structured_result, "fake-free-model"  # type: ignore[return-value]

    def search(
        self,
        query: str,
        *,
        tier: str = "standard",
        max_results: int = 8,
        allow_paid: bool | None = None,
    ) -> BrainSearchResult:
        self.search_calls.append(
            {
                "query": query,
                "tier": tier,
                "max_results": max_results,
                "allow_paid": allow_paid,
            }
        )
        if self.search_error is not None:
            raise self.search_error
        assert self.search_result is not None
        return self.search_result


class _FakeFallbackGateway:
    """Stub für Gemini-Fallback bei `BrainError`."""

    def __init__(
        self,
        *,
        structured_result: BaseModel | None = None,
        grounded_result: BaseModel | None = None,
    ) -> None:
        self.structured_result = structured_result
        self.grounded_result = grounded_result
        self.structured_calls: list[tuple[str, str, type]] = []
        self.grounded_calls: list[tuple[str, str, type]] = []

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        self.structured_calls.append((prompt, system_instruction, schema_cls))
        assert self.structured_result is not None
        return self.structured_result  # type: ignore[return-value]

    def generate_grounded_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        self.grounded_calls.append((prompt, system_instruction, schema_cls))
        assert self.grounded_result is not None
        return self.grounded_result  # type: ignore[return-value]


class _NoopNotifier:
    def dispatch_event(self, rule: AlertRule, event: AlertEvent) -> tuple[tuple[str, ...], bool]:
        return (), True


class _StubHttpFetcher(SafeHttpFetcher):
    def __init__(self, body: str = "") -> None:
        self._body = body

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> SafeHttpResponse:
        return SafeHttpResponse(url, 200, "text/html", self._body, 5)

    def post(
        self,
        url: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> SafeHttpResponse:
        return self.fetch(url, headers=headers)


class TestBrainAlertGateway:
    def test_generate_structured_uses_auto_tier_and_policy_allow_paid_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = CompiledAlertDraft(
            title="Goldpreis unter 2400 €",
            detector_type="price_threshold",
            condition_description="Goldpreis <= 2400 €",
        )
        fake_brain = _FakeBrainSession(structured_result=expected)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        result = BrainAlertGateway().generate_structured(
            prompt="Überwache Goldpreis",
            system_instruction="Kompiliere präzise.",
            schema_cls=CompiledAlertDraft,
        )

        assert result == expected
        assert fake_brain.entered and fake_brain.exited
        assert len(fake_brain.structured_calls) == 1
        call = fake_brain.structured_calls[0]
        assert call["prompt"] == "Überwache Goldpreis"
        assert call["schema_cls"] is CompiledAlertDraft
        assert call["tier"] == "auto"
        assert call["system_instruction"] == "Kompiliere präzise."
        assert call["allow_paid"] is None

    def test_generate_structured_falls_back_to_gemini_on_brain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = CompiledAlertDraft(
            title="Fallback-Regel",
            detector_type="web_search",
            condition_description="Gemini Fallback",
        )
        fake_brain = _FakeBrainSession(structured_error=BrainProviderError("all models busy"))
        fallback = _FakeFallbackGateway(structured_result=expected)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        result = BrainAlertGateway(fallback_gateway=fallback).generate_structured(
            prompt="Überwache Wohnungen",
            system_instruction="Kompiliere.",
            schema_cls=CompiledAlertDraft,
        )

        assert result == expected
        assert len(fallback.structured_calls) == 1

    def test_generate_structured_wraps_brain_error_without_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_brain = _FakeBrainSession(structured_error=BrainProviderError("all free models busy"))
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        with pytest.raises(LLMProviderError, match="AI-Brain Strukturanalyse fehlgeschlagen"):
            BrainAlertGateway().generate_structured("prompt", "sys", CompiledAlertDraft)

    def test_generate_grounded_structured_uses_auto_tier_for_search_and_synthesis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        search_res = BrainSearchResult(
            query="Goldpreis Euro Feinunze",
            tier=BrainTier.FAST,
            citations=[
                BrainCitation(
                    url="https://finance.example.com/gold",
                    title="Goldpreis aktuell",
                    snippet="Aktueller Goldpreis liegt bei 2.350,50 € je Feinunze.",
                ),
                BrainCitation(
                    url="javascript:alert(1)",
                    title="XSS Versuch",
                    snippet="Bösartiger Link",
                ),
            ],
        )
        expected = AiMetricExtraction(
            value_found=True,
            numeric_value=2350.5,
            unit="€",
            context_summary="Goldpreis aktuell",
            source_url="https://finance.example.com/gold",
        )
        fake_brain = _FakeBrainSession(search_result=search_res, structured_result=expected)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        result = BrainAlertGateway().generate_grounded_structured(
            prompt="Kennzahl / Asset: Goldpreis Euro Feinunze\nZiel: <= 2400 €",
            system_instruction="Extrahiere exakt.",
            schema_cls=AiMetricExtraction,
        )

        assert result == expected
        assert fake_brain.entered and fake_brain.exited
        assert len(fake_brain.search_calls) == 1
        assert fake_brain.search_calls[0]["query"] == "Goldpreis Euro Feinunze"
        assert fake_brain.search_calls[0]["tier"] == "auto"
        assert fake_brain.search_calls[0]["allow_paid"] is None

        assert len(fake_brain.structured_calls) == 1
        synth_call = fake_brain.structured_calls[0]
        assert synth_call["tier"] == "auto"
        assert synth_call["allow_paid"] is None
        assert "https://finance.example.com/gold" in synth_call["prompt"]
        assert "javascript:" not in synth_call["prompt"]

    def test_generate_grounded_structured_falls_back_on_synthesis_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        search_res = BrainSearchResult(
            query="Wohnungen Berlin",
            tier=BrainTier.FAST,
            citations=[
                BrainCitation(
                    url="https://immo.example.com/1",
                    title="Wohnung Berlin-Mitte",
                    snippet="Frei ab sofort, 1.250,00 € warm",
                )
            ],
        )
        fake_brain = _FakeBrainSession(
            search_result=search_res,
            structured_error=BrainProviderError("quota exhausted"),
        )
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        eval_res = BrainAlertGateway().generate_grounded_structured(
            prompt="Suchfokus: Wohnungen Berlin",
            system_instruction="Suche Angebote.",
            schema_cls=AiSearchEvaluation,
        )
        assert eval_res.condition_met is True
        assert len(eval_res.matches) == 1
        assert eval_res.matches[0].url == "https://immo.example.com/1"

        metric_res = BrainAlertGateway().generate_grounded_structured(
            prompt="Kennzahl / Asset: Miete Berlin",
            system_instruction="Extrahiere Zahl.",
            schema_cls=AiMetricExtraction,
        )
        assert metric_res.value_found is True
        assert metric_res.numeric_value == 1250.0
        assert metric_res.source_url == "https://immo.example.com/1"

        with pytest.raises(LLMProviderError, match="KI-Synthese fehlgeschlagen"):
            BrainAlertGateway().generate_grounded_structured(
                prompt="Suchfokus: Unbekanntes Schema",
                system_instruction="Prüfe Änderung.",
                schema_cls=AiChangeEvaluation,
            )

    def test_generate_grounded_structured_uses_gemini_fallback_on_connect_brain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_connect() -> _FakeBrainSession:
            raise BrainAuthError("invalid key")

        monkeypatch.setattr(ai_brain, "connect", _fail_connect)

        expected = AiSearchEvaluation(condition_met=False, summary="Fallback-OK", matches=[])
        fallback = _FakeFallbackGateway(grounded_result=expected)
        assert (
            BrainAlertGateway(fallback_gateway=fallback).generate_grounded_structured(
                "prompt", "sys", AiSearchEvaluation
            )
            == expected
        )
        assert len(fallback.grounded_calls) == 1

        with pytest.raises(LLMProviderError, match="AI-Brain Websuche fehlgeschlagen"):
            BrainAlertGateway().generate_grounded_structured("prompt", "sys", AiSearchEvaluation)


class TestBrainWebSearchProvider:
    def test_maps_citations_sanitizes_urls_deduplicates_and_uses_auto_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        search_res = BrainSearchResult(
            query="BKK firmus Zusatzbeitrag",
            tier=BrainTier.FAST,
            citations=[
                BrainCitation(
                    url="https://www.bkk-firmus.de/beitraege",
                    title="BKK firmus Beitragssatz",
                    snippet="Der Zusatzbeitrag liegt aktuell bei 2,18 %.",
                ),
                BrainCitation(
                    url="https://www.bkk-firmus.de/beitraege",
                    title="BKK firmus Beitragssatz",
                    snippet="Duplikat derselben Quelle",
                ),
                BrainCitation(
                    url="javascript:alert(1)",
                    title="Unsicherer Treffer",
                    snippet="URL muss bereinigt werden",
                ),
            ],
        )
        fake_brain = _FakeBrainSession(search_result=search_res)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        provider = BrainWebSearchProvider(http_fetcher=_StubHttpFetcher())
        items = provider.search("BKK firmus Zusatzbeitrag", max_results=5)

        assert fake_brain.entered and fake_brain.exited
        assert fake_brain.search_calls == [
            {
                "query": "BKK firmus Zusatzbeitrag",
                "tier": "auto",
                "max_results": 5,
                "allow_paid": None,
            }
        ]
        assert len(items) == 2
        assert items[0].url == "https://www.bkk-firmus.de/beitraege"
        assert items[0].fingerprint == compute_item_fingerprint(
            "https://www.bkk-firmus.de/beitraege",
            "BKK firmus Beitragssatz",
        )
        assert items[0].summary == "Der Zusatzbeitrag liegt aktuell bei 2,18 %."
        assert items[1].title == "Unsicherer Treffer"
        assert items[1].url == ""

    def test_empty_query_returns_empty_without_connecting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = False

        def _should_not_connect() -> _FakeBrainSession:
            nonlocal called
            called = True
            raise AssertionError("connect darf bei leerer Query nicht gerufen werden")

        monkeypatch.setattr(ai_brain, "connect", _should_not_connect)
        assert BrainWebSearchProvider().search("   ") == []
        assert called is False

    def test_falls_back_to_free_search_provider_on_brain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_brain = _FakeBrainSession(search_error=BrainProviderError("search down"))
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        fallback_items = [
            DiscoveredItem(
                fingerprint="fp_fallback",
                title="Fallback-Treffer",
                url="https://fallback.example.com/item",
                summary="Vom FreeWebSearchProvider geliefert",
            )
        ]

        class _StubFallback:
            def __init__(self) -> None:
                self.queries: list[tuple[str, int]] = []

            def search(self, query: str, max_results: int = 10) -> list[DiscoveredItem]:
                self.queries.append((query, max_results))
                return fallback_items

        fallback = _StubFallback()
        provider = BrainWebSearchProvider(fallback_provider=fallback)
        items = provider.search("Goldpreis aktuell", max_results=6)

        assert items == fallback_items
        assert fallback.queries == [("Goldpreis aktuell", 6)]


class TestFactoriesAndWiringWithoutLiveCalls:
    def test_llm_gateway_factory_prefers_brain_then_gemini_then_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings_with_gemini = AppSettings(gemini_api_key="AIzaFakeKey12345")
        settings_empty = AppSettings(gemini_api_key="")

        monkeypatch.setattr(ai_brain, "is_configured", lambda: True)
        gw_with_fb = create_alert_llm_gateway(settings_with_gemini)
        gw_no_fb = create_alert_llm_gateway(settings_empty)
        assert isinstance(gw_with_fb, BrainAlertGateway)
        assert isinstance(gw_no_fb, BrainAlertGateway)
        assert gw_with_fb._settings == settings_with_gemini

        monkeypatch.setattr(ai_brain, "is_configured", lambda: False)
        monkeypatch.setattr(GeminiAlertGateway, "_create_client", staticmethod(lambda _k: object()))
        assert isinstance(create_alert_llm_gateway(settings_with_gemini), GeminiAlertGateway)
        assert create_alert_llm_gateway(settings_empty) is None

    def test_search_provider_factory_switches_cleanly_on_brain_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        http = _StubHttpFetcher()

        monkeypatch.setattr(ai_brain, "is_configured", lambda: True)
        assert isinstance(create_web_search_provider(http), BrainWebSearchProvider)

        monkeypatch.setattr(ai_brain, "is_configured", lambda: False)
        assert isinstance(create_web_search_provider(http), FreeWebSearchProvider)

    def test_engine_and_compiler_wire_brain_and_fallback_automatically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = SqlAlertRepository(SqliteExecutor(":memory:"))
        try:
            http = _StubHttpFetcher()

            monkeypatch.setattr(ai_brain, "is_configured", lambda: True)
            engine_brain = AlertEvaluationEngine(AppSettings(), repo, _NoopNotifier(), http_fetcher=http)
            assert isinstance(engine_brain._llm, BrainAlertGateway)
            assert isinstance(engine_brain._search_provider, BrainWebSearchProvider)
            assert isinstance(engine_brain._web_search._search_provider, BrainWebSearchProvider)
            assert isinstance(engine_brain._price._search_provider, BrainWebSearchProvider)
            assert isinstance(engine_brain._weather._search_provider, BrainWebSearchProvider)
            assert isinstance(_build_compiler(AppSettings())._llm, BrainAlertGateway)

            monkeypatch.setattr(ai_brain, "is_configured", lambda: False)
            engine_fallback = AlertEvaluationEngine(AppSettings(), repo, _NoopNotifier(), http_fetcher=http)
            assert engine_fallback._llm is None
            assert isinstance(engine_fallback._search_provider, FreeWebSearchProvider)
            assert _build_compiler(AppSettings())._llm is None
        finally:
            repo.close()


class TestSettingsViewAiBrainOptions:
    @staticmethod
    def _configure_env(monkeypatch: pytest.MonkeyPatch, db_file: Path) -> None:
        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", "")
        monkeypatch.setenv("GEMINI_API_KEY", "")
        monkeypatch.setenv("WORKER_MODE", "github")

    def test_settings_view_loads_and_saves_ai_brain_policy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._configure_env(monkeypatch, tmp_path / "settings_brain.db")
        fake_brain = _FakeBrainSession(
            policy=AppBrainPolicy(
                llm_tier=BrainTier.FAST,
                search_tier=BrainTier.FAST,
                maps_mode="auto",
                allow_paid=False,
            )
        )
        monkeypatch.setattr(ai_brain, "is_configured", lambda: True)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        at = AppTest.from_file(ENTRYPOINT).run()
        assert not at.exception
        settings_btn = next(b for b in at.sidebar.button if "Settings" in b.label)
        at = settings_btn.click().run()
        assert not at.exception

        assert any(m.value == "🧠 AI-Brain" for m in at.metric)
        assert any("🟢 Online" in c.value for c in at.caption)
        assert at.selectbox(key="ai_brain_llm_tier").value == BrainTier.FAST
        assert at.selectbox(key="ai_brain_search_tier").value == BrainTier.FAST
        assert at.selectbox(key="ai_brain_maps_mode").value == "auto"
        assert at.checkbox(key="ai_brain_allow_paid").value is False

        at.selectbox(key="ai_brain_llm_tier").select(BrainTier.STANDARD)
        at.selectbox(key="ai_brain_search_tier").select(BrainTier.DEEP)
        at.selectbox(key="ai_brain_maps_mode").select("always")
        at.checkbox(key="ai_brain_allow_paid").check()
        at = at.button(key="ai_brain_save_btn").click().run()
        assert not at.exception

        assert len(fake_brain.saved_policies) == 1
        assert fake_brain.saved_policies[0] == AppBrainPolicy(
            llm_tier=BrainTier.STANDARD,
            search_tier=BrainTier.DEEP,
            maps_mode="always",
            allow_paid=True,
        )
        assert any("Gespeichert" in s.value or "gespeichert" in s.value for s in at.success)

    def test_settings_view_shows_offline_hint_when_not_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._configure_env(monkeypatch, tmp_path / "settings_offline.db")
        monkeypatch.setattr(ai_brain, "is_configured", lambda: False)

        at = AppTest.from_file(ENTRYPOINT).run()
        settings_btn = next(b for b in at.sidebar.button if "Settings" in b.label)
        at = settings_btn.click().run()
        assert not at.exception

        assert any(m.label == "Gemini AI" and m.value == "⚪ Heuristic" for m in at.metric)
        assert any("AI_BRAIN_KEY" in info.value for info in at.info)

    def test_settings_view_handles_policy_load_and_save_errors_gracefully(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._configure_env(monkeypatch, tmp_path / "settings_err.db")
        fake_brain = _FakeBrainSession(
            policy_load_error=BrainStorageError("turso timeout"),
            policy_save_error=BrainStorageError("turso write failed"),
        )
        monkeypatch.setattr(ai_brain, "is_configured", lambda: True)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        at = AppTest.from_file(ENTRYPOINT).run()
        settings_btn = next(b for b in at.sidebar.button if "Settings" in b.label)
        at = settings_btn.click().run()
        assert not at.exception

        assert any("turso timeout" in w.value for w in at.warning)
        assert at.selectbox(key="ai_brain_llm_tier").value == BrainTier.FAST
        assert at.checkbox(key="ai_brain_allow_paid").value is False

        at = at.button(key="ai_brain_save_btn").click().run()
        assert not at.exception
        assert any("turso write failed" in e.value for e in at.error)

    def test_brain_gateway_lazy_resolves_gemini_fallback_from_settings_on_brain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = CompiledAlertDraft(
            title="Gemini Lazy Fallback",
            detector_type="web_search",
            condition_description="Aus Gemini erzeugt",
        )
        fake_brain = _FakeBrainSession(structured_error=BrainProviderError("brain unavailable"))
        monkeypatch.setattr(ai_brain, "is_configured", lambda: True)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)
        monkeypatch.setattr(GeminiAlertGateway, "_create_client", staticmethod(lambda _k: object()))
        monkeypatch.setattr(
            GeminiAlertGateway,
            "generate_structured",
            lambda self, prompt, system_instruction, schema_cls: expected,
        )

        gw = create_alert_llm_gateway(AppSettings(gemini_api_key="AIzaFakeKey12345"))
        assert isinstance(gw, BrainAlertGateway)
        assert gw.generate_structured("prompt", "sys", CompiledAlertDraft) == expected

    def test_settings_view_readonly_role_hides_admin_policy_form(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_brain = _FakeBrainSession(
            policy=AppBrainPolicy(
                llm_tier=BrainTier.STANDARD,
                search_tier=BrainTier.DEEP,
                maps_mode="off",
                allow_paid=False,
            )
        )
        monkeypatch.setattr(ai_brain, "is_configured", lambda: True)
        monkeypatch.setattr(ai_brain, "connect", lambda: fake_brain)

        def _render_readonly() -> None:
            from src.domain.enums import AuthRole
            from src.ui.views.settings_view import _render_ai_brain_options

            _render_ai_brain_options(AuthRole.READONLY)

        at = AppTest.from_function(_render_readonly).run()
        assert not at.exception
        assert len(at.selectbox) == 0
        assert len(at.checkbox) == 0
        assert any(
            "Read-Only Policy" in c.value and "Standard" in c.value and "Deep" in c.value
            for c in at.caption
        )

