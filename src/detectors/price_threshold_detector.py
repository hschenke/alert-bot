"""Preis-, Finanz- & Kennzahlen-Wächter über Webseiten-Selektor oder Gemini Web-Suche."""

from __future__ import annotations

from src.detectors.comparators import (
    evaluate_numeric_operator,
    format_number_de,
    parse_localized_float,
)
from src.detectors.http_client import SafeHttpFetcher, extract_clean_html_text
from src.detectors.llm_gateway import AlertLLMGateway
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding
from src.domain.schemas import AiMetricExtraction


class PriceThresholdDetector:
    """Überwacht Produktpreise, Aktien/ETF/Krypto-Kurse oder beliebige Zahlenwerte."""

    def __init__(
        self,
        http_fetcher: SafeHttpFetcher,
        llm_gateway: AlertLLMGateway | None,
        system_prompt: str = "",
    ) -> None:
        self._http = http_fetcher
        self._llm = llm_gateway
        self._system_prompt = system_prompt

    def evaluate(self, rule: AlertRule) -> DetectorFinding:
        """Ermittelt den aktuellen Zahlenwert und gleicht ihn mit dem Zielwert ab."""
        target = rule.threshold.target_value
        if target is None:
            raise DetectorExecutionError("Kein numerischer Zielwert für den Preis-/Zahlen-Wächter gesetzt.")

        measured, unit, context, source_url = self._resolve_current_metric(rule)
        final_unit = (rule.threshold.unit or unit).strip()
        op = rule.threshold.operator
        met = evaluate_numeric_operator(measured, op, target)

        unit_suffix = f" {final_unit}" if final_unit else ""
        measured_str = f"{format_number_de(measured)}{unit_suffix}"
        target_str = f"{op.value} {format_number_de(target)}{unit_suffix}"
        state_txt = "erfüllt" if met else "nicht erfüllt"
        detail = f" ({context})" if context else ""
        summary = f"Aktueller Wert: {measured_str}{detail} – Bedingung {target_str} ist {state_txt}."

        return DetectorFinding(
            condition_met=met,
            summary=summary,
            measured_value=measured,
            measured_display=measured_str,
            source_url=source_url or rule.target_url,
        )

    def _resolve_current_metric(self, rule: AlertRule) -> tuple[float, str, str, str]:
        if rule.target_url.strip():
            return self._extract_from_target_url(rule)
        return self._extract_from_web_search(rule)

    def _extract_from_target_url(self, rule: AlertRule) -> tuple[float, str, str, str]:
        resp = self._http.fetch(rule.target_url)
        extracted_text = extract_clean_html_text(resp.text, rule.threshold.css_selector)
        if not extracted_text:
            raise DetectorExecutionError("Kein lesbarer Text auf der Zielseite gefunden.")

        if rule.threshold.css_selector.strip():
            direct_num = parse_localized_float(extracted_text)
            if direct_num is not None:
                return direct_num, rule.threshold.unit, "direkt per CSS-Selektor gelesen", resp.final_url

        if self._llm is None:
            direct_fallback = parse_localized_float(extracted_text)
            if direct_fallback is not None:
                return direct_fallback, rule.threshold.unit, "aus Seitentext gelesen", resp.final_url
            raise DetectorExecutionError(
                "Zahlenwert konnte ohne CSS-Selektor oder GEMINI_API_KEY nicht eindeutig bestimmt werden."
            )

        prompt = (
            f"Auftrag: {rule.user_prompt}\n"
            f"Ziel-URL: {resp.final_url}\n"
            f"Seitentext-Auszug:\n{extracted_text[:6000]}"
        )
        res = self._llm.generate_structured(
            prompt=prompt,
            system_instruction=self._system_prompt or "Extrahiere den gesuchten aktuellen Zahlenwert.",
            schema_cls=AiMetricExtraction,
        )
        if not res.value_found or res.numeric_value is None:
            raise DetectorExecutionError("KI konnte keinen eindeutigen Zahlenwert auf der Seite finden.")
        return float(res.numeric_value), res.unit, res.context_summary, resp.final_url

    def _extract_from_web_search(self, rule: AlertRule) -> tuple[float, str, str, str]:
        if self._llm is None:
            raise DetectorExecutionError(
                "Für Preis-/Kurs-Abfragen ohne direkte URL wird ein GEMINI_API_KEY benötigt."
            )
        query = rule.search_query.strip() or rule.title
        prompt = (
            f"Auftrag: {rule.user_prompt}\n"
            f"Gesuchte Kennzahl / Asset / Produkt: {query}\n"
            "Ermittle über Google Search den aktuellsten verifizierten Zahlenwert."
        )
        res = self._llm.generate_grounded_structured(
            prompt=prompt,
            system_instruction=self._system_prompt or "Extrahiere den aktuellen Zahlenwert wahrheitsgemäß.",
            schema_cls=AiMetricExtraction,
        )
        if not res.value_found or res.numeric_value is None:
            raise DetectorExecutionError(f"Kein aktueller Zahlenwert zu '{query}' ermittelbar.")
        return float(res.numeric_value), res.unit, res.context_summary, res.source_url
