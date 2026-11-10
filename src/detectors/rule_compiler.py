"""Übersetzt natürlichsprachliche Überwachungswünsche per KI (oder Heuristik-Fallback) in AlertRules."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from src.config.constants import DEFAULT_CHECK_INTERVAL_MINUTES, DEFAULT_COOLDOWN_MINUTES
from src.detectors.comparators import parse_localized_float
from src.detectors.llm_gateway import AlertLLMGateway
from src.domain.enums import (
    ChannelType,
    ComparisonOperator,
    DetectorType,
    RuleStatus,
    TriggerMode,
    WeatherMetric,
)
from src.domain.models import AlertRule, ThresholdSpec
from src.domain.schemas import CompiledAlertDraft
from src.security.sanitization import (
    validate_check_interval,
    validate_css_selector,
    validate_json_path,
    validate_optional_email,
    validate_rule_title,
    validate_search_query,
    validate_user_prompt,
)
from src.security.ssrf_guard import validate_safe_external_url

logger = logging.getLogger(__name__)

_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_CITY_PREPOSITION_RE = re.compile(
    r"\b(?:in|für|fuer|bei|ort)\s+([A-ZÄÖÜa-zäöüß][A-ZÄÖÜa-zäöüß\-\s]{1,35}?)(?=\s+(?:via|über|ueber|unter|ab|bei|sobald|wenn|erreicht|steigt|fällt|faellt|oder|und|,|\.)|$)",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    """Liefert den aktuellen UTC-Zeitstempel im ISO-8601-Format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _coerce_enum(enum_cls: type, raw: str, default: object) -> object:
    cleaned = (raw or "").strip().lower()
    for item in enum_cls:
        if item.value == cleaned:
            return item
    return default


class AlertRuleCompiler:
    """Kompiliert Freitext-Aufträge in saubere, validierte `AlertRule`-Objekte."""

    def __init__(
        self,
        llm_gateway: AlertLLMGateway | None = None,
        compiler_prompt: str = "",
    ) -> None:
        self._llm = llm_gateway
        self._compiler_prompt = compiler_prompt

    def compile_from_prompt(
        self,
        user_prompt: str,
        notify_email: str = "",
    ) -> CompiledAlertDraft:
        """Erzeugt einen strukturierten Regel-Entwurf per Gemini oder lokaler Heuristik."""
        clean_prompt = validate_user_prompt(user_prompt)
        if self._llm is not None:
            try:
                return self._llm.generate_structured(
                    prompt=f"Benutzerwunsch:\n{clean_prompt}",
                    system_instruction=self._compiler_prompt or "Übersetze den Wunsch in eine präzise Alert-Regel.",
                    schema_cls=CompiledAlertDraft,
                )
            except Exception as exc:
                logger.warning("KI-Regel-Compiler fehlgeschlagen (%s), nutze Heuristik-Fallback: %s", type(exc).__name__, exc)
        return self._heuristic_draft(clean_prompt)

    @staticmethod
    def build_validated_rule(
        draft: CompiledAlertDraft,
        user_prompt: str,
        notify_email: str = "",
        notify_on_first_match: bool = True,
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
        existing_id: str | None = None,
    ) -> AlertRule:
        """Baut aus einem Entwurf eine vollständig validierte, sofort ausführbare `AlertRule`."""
        clean_prompt = validate_user_prompt(user_prompt)
        clean_title = validate_rule_title(draft.title or clean_prompt[:80])
        clean_email = validate_optional_email(notify_email)
        clean_url = validate_safe_external_url(draft.target_url) if draft.target_url.strip() else ""

        detector = _coerce_enum(DetectorType, draft.detector_type, DetectorType.WEB_SEARCH)
        trigger = _coerce_enum(TriggerMode, draft.trigger_mode, TriggerMode.ON_STATE_CHANGE)
        metric = _coerce_enum(WeatherMetric, draft.weather_metric, WeatherMetric.TEMPERATURE_C)
        operator = _coerce_enum(ComparisonOperator, draft.operator, ComparisonOperator.GTE)

        interval = validate_check_interval(
            max(1, min(1440, int(draft.suggested_interval_minutes or DEFAULT_CHECK_INTERVAL_MINUTES)))
        )
        now_iso = utc_now_iso()

        threshold = ThresholdSpec(
            location_name=draft.location_name.strip()[:100],
            weather_metric=metric,  # type: ignore[arg-type]
            operator=operator,  # type: ignore[arg-type]
            target_value=draft.target_value,
            unit=draft.unit.strip()[:20],
            css_selector=validate_css_selector(draft.css_selector),
            json_path=validate_json_path(draft.json_path),
        )

        return AlertRule(
            id=existing_id or uuid.uuid4().hex[:12],
            title=clean_title,
            user_prompt=clean_prompt,
            detector_type=detector,  # type: ignore[arg-type]
            status=RuleStatus.ACTIVE,
            search_query=validate_search_query(draft.search_query),
            target_url=clean_url,
            condition_description=(draft.condition_description or clean_prompt).strip()[:500],
            threshold=threshold,
            trigger_mode=trigger,  # type: ignore[arg-type]
            check_interval_minutes=interval,
            cooldown_minutes=max(0, int(cooldown_minutes)),
            notify_on_first_match=notify_on_first_match,
            notify_email=clean_email,
            channels=(ChannelType.EMAIL,),
            next_check_at=now_iso,
            created_at=now_iso,
            updated_at=now_iso,
        )

    @staticmethod
    def _heuristic_draft(prompt: str) -> CompiledAlertDraft:
        """Deterministischer Offline-Fallback für Wetter-, URL- und Angebots-Wächter."""
        lower = prompt.lower()
        url_match = _URL_IN_TEXT_RE.search(prompt)
        found_url = url_match.group(0).rstrip(".,;)") if url_match else ""
        number_val = parse_localized_float(prompt)

        op = "<=" if any(w in lower for w in ("unter", "kleiner", "maximal", "bis ", "sinkt", "<=")) else ">="
        if any(w in lower for w in ("temperatur", "grad", "°c", "wetter", "frost", "hitze", "wind")):
            city_match = _CITY_PREPOSITION_RE.search(prompt)
            city = city_match.group(1).strip() if city_match else "Berlin"
            target = number_val if number_val is not None else 20.0
            return CompiledAlertDraft(
                title=f"Wetter-Wächter: {city} ({op} {target:g} °C)",
                detector_type=DetectorType.WEATHER_THRESHOLD.value,
                condition_description=prompt,
                location_name=city,
                weather_metric=WeatherMetric.TEMPERATURE_C.value,
                operator=op,
                target_value=target,
                unit="°C",
                trigger_mode=TriggerMode.ON_STATE_CHANGE.value,
                suggested_interval_minutes=15,
            )

        if found_url and number_val is not None and any(w in lower for w in ("€", "euro", "preis", "usd", "kurs")):
            return CompiledAlertDraft(
                title=f"Preis-Wächter ({op} {number_val:g})",
                detector_type=DetectorType.PRICE_THRESHOLD.value,
                condition_description=prompt,
                target_url=found_url,
                operator=op,
                target_value=number_val,
                unit="€" if ("€" in lower or "euro" in lower) else "",
                trigger_mode=TriggerMode.ON_STATE_CHANGE.value,
                suggested_interval_minutes=30,
            )

        if found_url:
            return CompiledAlertDraft(
                title=f"Website-Wächter: {found_url[:55]}",
                detector_type=DetectorType.PAGE_CHANGE.value,
                condition_description=prompt,
                target_url=found_url,
                trigger_mode=TriggerMode.ON_STATE_CHANGE.value,
                suggested_interval_minutes=15,
            )

        return CompiledAlertDraft(
            title=prompt[:75],
            detector_type=DetectorType.WEB_SEARCH.value,
            condition_description=prompt,
            search_query=prompt[:160],
            trigger_mode=TriggerMode.EVERY_NEW_ITEM.value,
            suggested_interval_minutes=30,
        )
