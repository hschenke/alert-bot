"""Preis-, Finanz- & Kennzahlen-Wächter über Webseiten-Selektor oder kostenfreie Web-Suche."""

from __future__ import annotations

import logging

from src.detectors.comparators import (
    evaluate_numeric_operator,
    format_number_de,
    parse_localized_float,
)
from src.detectors.http_client import SafeHttpFetcher, extract_clean_html_text
from src.detectors.llm_gateway import AlertLLMGateway
from src.detectors.search_provider import FreeWebSearchProvider, WebSearchProvider
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding
from src.domain.schemas import AiMetricExtraction

logger = logging.getLogger(__name__)


class PriceThresholdDetector:
    """Überwacht Produktpreise, Aktien/ETF/Krypto-Kurse, Zinssätze oder beliebige Zahlenwerte."""

    def __init__(
        self,
        http_fetcher: SafeHttpFetcher,
        llm_gateway: AlertLLMGateway | None,
        system_prompt: str = "",
        search_provider: WebSearchProvider | None = None,
    ) -> None:
        self._http = http_fetcher
        self._llm = llm_gateway
        self._system_prompt = system_prompt
        self._search_provider = search_provider or FreeWebSearchProvider(http_fetcher)

    def evaluate(self, rule: AlertRule) -> DetectorFinding:
        """Ermittelt den aktuellen Zahlenwert und gleicht ihn mit Schwellenwert oder Vorwert ab."""
        measured, unit, context, source_url = self._resolve_current_metric(rule)
        final_unit = (rule.threshold.unit or unit).strip()
        unit_suffix = f" {final_unit}" if final_unit else ""
        measured_str = f"{format_number_de(measured)}{unit_suffix}"
        target = rule.threshold.target_value
        detail = f" ({context})" if context else ""

        if target is None:
            prev_val = parse_localized_float(rule.last_value_text)
            if prev_val is not None:
                has_changed = abs(measured - prev_val) > 1e-6
                met = has_changed
                status_txt = (
                    f"Wert geändert (vorher: {format_number_de(prev_val)}{unit_suffix})"
                    if has_changed
                    else "Wert unverändert"
                )
            else:
                met = rule.notify_on_first_match
                status_txt = "Initialer Basiswert erfasst"
            summary = f"Aktueller Wert: {measured_str}{detail} – {status_txt}."
        else:
            op = rule.threshold.operator
            met = evaluate_numeric_operator(measured, op, target)
            target_str = f"{op.value} {format_number_de(target)}{unit_suffix}"
            state_txt = "erfüllt" if met else "nicht erfüllt"
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
        query = rule.search_query.strip() or rule.title
        search_hits = self._search_provider.search(query)
        primary_url = search_hits[0].url if search_hits else ""
        snippets = "\n".join(f"- {it.title}: {it.summary} ({it.url})" for it in search_hits)

        if self._llm is not None and snippets:
            prompt = (
                f"Auftrag: {rule.user_prompt}\n"
                f"Gesuchte Kennzahl / Asset / Produkt: {query}\n\n"
                f"Aktuelle Web-Suchergebnisse:\n{snippets}\n\n"
                "Extrahiere den gesuchten aktuellen Zahlenwert.\n"
                "WICHTIG: Achte zwingend auf das aktuelle Gültigkeitsjahr (2026/2027) und die neuesten Angaben! "
                "Ignoriere veraltete historische Vorjahreswerte (wie z. B. 2020/2021) vollständig."
            )
            try:
                res = self._llm.generate_structured(
                    prompt=prompt,
                    system_instruction=self._system_prompt or "Extrahiere den aktuellen Zahlenwert wahrheitsgemäß.",
                    schema_cls=AiMetricExtraction,
                )
                if res.value_found and res.numeric_value is None:
                    pass
                elif res.value_found and res.numeric_value is not None:
                    src = res.source_url or primary_url
                    return float(res.numeric_value), res.unit, res.context_summary, src
            except Exception as exc:
                logger.warning("KI-Zahlenextraktion fehlgeschlagen (%s), nutze Heuristik: %s", type(exc).__name__, exc)

        if snippets:
            target_unit = rule.threshold.unit or ("%" if "%" in snippets else "")
            parsed = parse_localized_float(snippets, target_unit)
            if parsed is not None:
                return parsed, target_unit, "aus Web-Suche ermittelt", primary_url

        raise DetectorExecutionError(f"Kein aktueller Zahlenwert zu '{query}' im Web ermittelbar.")
