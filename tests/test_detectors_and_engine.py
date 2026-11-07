"""Tests für alle 5 Wächter-Detektoren, den KI-Regel-Compiler und die Anti-Spam-Engine."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from pydantic import BaseModel
from src.config.settings import AppSettings
from src.detectors.comparators import parse_localized_float
from src.detectors.engine import AlertEvaluationEngine
from src.detectors.http_client import SafeHttpFetcher, SafeHttpResponse
from src.detectors.rule_compiler import AlertRuleCompiler
from src.domain.enums import (
    CheckOutcome,
    ComparisonOperator,
    DetectorType,
    RuleStatus,
    TriggerMode,
    WeatherMetric,
)
from src.domain.models import AlertEvent, AlertRule, ThresholdSpec
from src.domain.schemas import AiDiscoveredMatch, AiMetricExtraction, AiSearchEvaluation
from src.storage.repository import SqlAlertRepository
from src.storage.sqlite_executor import SqliteExecutor

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StubHttpFetcher(SafeHttpFetcher):
    """Deterministischer HTTP-Stub ohne echte Netzwerkzugriffe."""

    def __init__(self, responses: dict[str, tuple[int, str]]) -> None:
        self._responses = responses

    def fetch(self, url: str) -> SafeHttpResponse:
        for pattern, (code, body) in self._responses.items():
            if pattern in url:
                return SafeHttpResponse(
                    final_url=url,
                    status_code=code,
                    content_type="application/json",
                    text=body,
                    elapsed_ms=25,
                )
        return SafeHttpResponse(
            final_url=url,
            status_code=404,
            content_type="text/plain",
            text="Not Found",
            elapsed_ms=10,
        )


class StubLLMGateway:
    """Deterministischer KI-Stub für Structured- & Grounded-Tests."""

    def __init__(self, canned: BaseModel) -> None:
        self.canned = canned

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        return self.canned  # type: ignore[return-value]

    def generate_grounded_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        return self.canned  # type: ignore[return-value]


class RecordingNotifier:
    """Zeichnet ausgelöste Benachrichtigungen für Assertions auf."""

    def __init__(self) -> None:
        self.sent_events: list[AlertEvent] = []

    def dispatch_event(
        self,
        rule: AlertRule,
        event: AlertEvent,
    ) -> tuple[tuple[str, ...], bool]:
        self.sent_events.append(event)
        return ("email",), True


class TestRuleCompilerAndParsers:
    def test_localized_float_parsing(self) -> None:
        assert parse_localized_float("Preis: 1.249,95 € inkl. MwSt.") == 1249.95
        assert parse_localized_float("Aktuell -3,5 °C in München") == -3.5
        assert parse_localized_float("USD 2,450.50") == 2450.50

    def test_heuristic_compiler_recognizes_weather_and_apartments(self) -> None:
        compiler = AlertRuleCompiler(llm_gateway=None)
        wx_draft = compiler.compile_from_prompt(
            "Beobachte die Temperatur in Freiburg sobald sie über 24 Grad steigt"
        )
        assert wx_draft.detector_type == DetectorType.WEATHER_THRESHOLD.value
        assert wx_draft.location_name == "Freiburg"
        assert wx_draft.target_value == 24.0

        apt_draft = compiler.compile_from_prompt(
            "Schaue nach neuen Mietwohnungen in Leipzig und sende eine E-Mail sobald eine online ist"
        )
        assert apt_draft.detector_type == DetectorType.WEB_SEARCH.value
        assert apt_draft.trigger_mode == TriggerMode.EVERY_NEW_ITEM.value


class TestDetectorsAndAntiSpamEngine:
    def test_weather_threshold_triggers_once_on_state_change(self) -> None:
        geo_json = json.dumps(
            {"results": [{"name": "Freiburg", "country": "Deutschland", "latitude": 47.99, "longitude": 7.85}]}
        )
        wx_hot = json.dumps({"current": {"temperature_2m": 23.4}})
        wx_cool = json.dumps({"current": {"temperature_2m": 18.0}})

        http = StubHttpFetcher({"geocoding-api": (200, geo_json), "forecast": (200, wx_hot)})
        repo = SqlAlertRepository(SqliteExecutor(":memory:"))
        notifier = RecordingNotifier()
        engine = AlertEvaluationEngine(AppSettings(), repo, notifier, http_fetcher=http)

        rule = AlertRule(
            id="wx1",
            title="Wärme-Alarm Freiburg",
            user_prompt="Temperatur Freiburg >= 22 °C",
            detector_type=DetectorType.WEATHER_THRESHOLD,
            threshold=ThresholdSpec(
                location_name="Freiburg",
                weather_metric=WeatherMetric.TEMPERATURE_C,
                operator=ComparisonOperator.GTE,
                target_value=22.0,
            ),
            trigger_mode=TriggerMode.ON_STATE_CHANGE,
            created_at="2026-11-07T10:00:00+00:00",
            updated_at="2026-11-07T10:00:00+00:00",
        )
        repo.upsert_rule(rule)

        # 1. Lauf (23,4 °C >= 22 °C) -> Löst E-Mail aus!
        t0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
        res1 = engine.evaluate_rule(rule, now=t0)
        assert res1.outcome == CheckOutcome.TRIGGERED
        assert len(notifier.sent_events) == 1

        # 2. Lauf (weiterhin 23,4 °C) -> Kein Spam dank ON_STATE_CHANGE!
        res2 = engine.evaluate_rule(res1.rule, now=t0 + timedelta(minutes=15))
        assert res2.outcome == CheckOutcome.SUPPRESSED
        assert len(notifier.sent_events) == 1

        # 3. Lauf (kühlt ab auf 18 °C) -> Zustand setzt sich zurück
        http_cool = StubHttpFetcher({"geocoding-api": (200, geo_json), "forecast": (200, wx_cool)})
        engine_cool = AlertEvaluationEngine(AppSettings(), repo, notifier, http_fetcher=http_cool)
        res3 = engine_cool.evaluate_rule(res2.rule, now=t0 + timedelta(minutes=30))
        assert res3.outcome == CheckOutcome.OK_NO_MATCH
        assert res3.rule.last_condition_met is False
        repo.close()

    def test_apartment_search_deduplicates_seen_listings(self) -> None:
        batch_1 = AiSearchEvaluation(
            condition_met=True,
            summary="Ein neues Wohnungsangebot gefunden.",
            matches=[
                AiDiscoveredMatch(
                    title="Helle 3-Zimmer-Wohnung mit Balkon",
                    url="https://immoportal.example.com/expose/101",
                    summary="78 m², 2. OG, ruhig gelegen",
                    price_or_value="1.150 € warm",
                )
            ],
        )
        repo = SqlAlertRepository(SqliteExecutor(":memory:"))
        notifier = RecordingNotifier()
        http = StubHttpFetcher({})
        engine1 = AlertEvaluationEngine(
            AppSettings(), repo, notifier, http_fetcher=http, llm_gateway=StubLLMGateway(batch_1)
        )

        rule = AlertRule(
            id="apt1",
            title="Mietwohnungen Berlin",
            user_prompt="Neue 3-Zimmer-Mietwohnung Berlin",
            detector_type=DetectorType.WEB_SEARCH,
            search_query="3 Zimmer Mietwohnung Berlin",
            trigger_mode=TriggerMode.EVERY_NEW_ITEM,
            notify_on_first_match=True,
            created_at="2026-11-07T10:00:00+00:00",
            updated_at="2026-11-07T10:00:00+00:00",
        )
        repo.upsert_rule(rule)

        # 1. Lauf -> Exposé 101 ist neu -> E-Mail!
        r1 = engine1.evaluate_rule(rule)
        assert r1.outcome == CheckOutcome.TRIGGERED
        assert len(notifier.sent_events) == 1

        # 2. Lauf -> Gleiches Exposé 101 -> Bereits bekannt -> KEIN erneuter Alarm!
        r2 = engine1.evaluate_rule(r1.rule)
        assert r2.outcome == CheckOutcome.OK_NO_MATCH
        assert len(notifier.sent_events) == 1

        # 3. Lauf -> Exposé 101 + NEUES Exposé 202 -> Meldet NUR Exposé 202!
        batch_2 = AiSearchEvaluation(
            condition_met=True,
            summary="Weiteres Angebot online.",
            matches=[
                *batch_1.matches,
                AiDiscoveredMatch(
                    title="Altbau 3 Zimmer nahe S-Bahn",
                    url="https://immoportal.example.com/expose/202",
                    summary="82 m², Dielenboden",
                    price_or_value="1.240 € warm",
                ),
            ],
        )
        engine2 = AlertEvaluationEngine(
            AppSettings(), repo, notifier, http_fetcher=http, llm_gateway=StubLLMGateway(batch_2)
        )
        r3 = engine2.evaluate_rule(r2.rule)
        assert r3.outcome == CheckOutcome.TRIGGERED
        assert len(notifier.sent_events) == 2
        assert len(notifier.sent_events[-1].items) == 1
        assert notifier.sent_events[-1].items[0].title == "Altbau 3 Zimmer nahe S-Bahn"
        repo.close()

    def test_price_page_change_and_http_api_detectors(self) -> None:
        html_v1 = "<html><body><div class='price'>899,00 €</div><p>Derzeit ausverkauft</p></body></html>"
        html_v2 = "<html><body><div class='price'>749,00 €</div><p>Sofort lieferbar</p></body></html>"
        api_json = json.dumps({"status": "healthy", "metrics": {"queue_depth": 42}})

        repo = SqlAlertRepository(SqliteExecutor(":memory:"))
        notifier = RecordingNotifier()
        http = StubHttpFetcher(
            {
                "shop.example.com": (200, html_v2),
                "termin.example.com": (200, html_v1),
                "api.example.com": (200, api_json),
            }
        )
        llm_stub = StubLLMGateway(
            AiMetricExtraction(value_found=True, numeric_value=749.0, unit="€")
        )
        engine = AlertEvaluationEngine(AppSettings(), repo, notifier, http_fetcher=http, llm_gateway=llm_stub)

        # Preis-Check via CSS-Selektor + TriggerMode.ONCE (pausiert nach Treffer automatisch!)
        price_rule = AlertRule(
            id="pr1",
            title="Monitor unter 800 €",
            user_prompt="Monitor Preis <= 800 €",
            detector_type=DetectorType.PRICE_THRESHOLD,
            target_url="https://shop.example.com/monitor",
            threshold=ThresholdSpec(
                operator=ComparisonOperator.LTE,
                target_value=800.0,
                unit="€",
                css_selector=".price",
            ),
            trigger_mode=TriggerMode.ONCE,
            created_at="2026-11-07T10:00:00+00:00",
            updated_at="2026-11-07T10:00:00+00:00",
        )
        repo.upsert_rule(price_rule)
        p_out = engine.evaluate_rule(price_rule)
        assert p_out.outcome == CheckOutcome.TRIGGERED
        assert p_out.rule.status == RuleStatus.PAUSED

        # Page-Change: 1. Lauf speichert Baseline, 2. Lauf erkennt Änderung
        page_rule = AlertRule(
            id="pg1",
            title="Termin-Wächter",
            user_prompt="Seite auf Änderung überwachen",
            detector_type=DetectorType.PAGE_CHANGE,
            target_url="https://termin.example.com",
            created_at="2026-11-07T10:00:00+00:00",
            updated_at="2026-11-07T10:00:00+00:00",
        )
        repo.upsert_rule(page_rule)
        pg1 = engine.evaluate_rule(page_rule)
        assert pg1.outcome == CheckOutcome.OK_NO_MATCH
        assert pg1.rule.last_content_hash != ""

        http_changed = StubHttpFetcher({"termin.example.com": (200, html_v2)})
        engine_changed = AlertEvaluationEngine(AppSettings(), repo, notifier, http_fetcher=http_changed)
        pg2 = engine_changed.evaluate_rule(replace(pg1.rule))
        assert pg2.outcome == CheckOutcome.TRIGGERED

        # JSON-API Feld-Check
        api_rule = AlertRule(
            id="api1",
            title="Queue-Tiefe >= 40",
            user_prompt="JSON queue_depth >= 40",
            detector_type=DetectorType.HTTP_API,
            target_url="https://api.example.com/status",
            threshold=ThresholdSpec(
                operator=ComparisonOperator.GTE,
                target_value=40.0,
                json_path="metrics.queue_depth",
            ),
            created_at="2026-11-07T10:00:00+00:00",
            updated_at="2026-11-07T10:00:00+00:00",
        )
        repo.upsert_rule(api_rule)
        api_out = engine.evaluate_rule(api_rule)
        assert api_out.outcome == CheckOutcome.TRIGGERED
        repo.close()
