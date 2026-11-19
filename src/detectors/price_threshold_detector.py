"""Preis-, Finanz- & Kennzahlen-Wächter über Webseiten-Selektor oder kostenfreie Web-Suche."""

from __future__ import annotations

import logging

from src.detectors.comparators import (
    evaluate_numeric_operator,
    extract_all_unit_values,
    format_number_de,
    parse_localized_float,
)
from src.detectors.http_client import SafeHttpFetcher, extract_clean_html_text
from src.detectors.llm_gateway import AlertLLMGateway
from src.detectors.metric_grounding import (
    count_term_matches,
    extract_query_terms,
    find_grounded_hit,
    infer_unit_from_text,
    is_grounded_in_text,
    is_implausible_outlier,
    rank_relevant_hits,
)
from src.detectors.search_provider import FreeWebSearchProvider, WebSearchProvider
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding, DiscoveredItem
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

        unit = rule.threshold.unit.strip()
        if rule.threshold.css_selector.strip():
            direct_num = parse_localized_float(extracted_text, unit)
            if direct_num is not None:
                return direct_num, rule.threshold.unit, "direkt per CSS-Selektor gelesen", resp.final_url

        if self._llm is None:
            direct_fallback = parse_localized_float(extracted_text, unit, strict_unit=bool(unit))
            if direct_fallback is not None:
                return direct_fallback, rule.threshold.unit, "aus Seitentext gelesen", resp.final_url
            raise DetectorExecutionError(
                "Zahlenwert konnte ohne CSS-Selektor oder GEMINI_API_KEY nicht eindeutig bestimmt werden."
            )

        return self._extract_metric_from_page_with_llm(rule, extracted_text, resp.final_url)

    def _extract_metric_from_page_with_llm(
        self,
        rule: AlertRule,
        page_text: str,
        page_url: str,
    ) -> tuple[float, str, str, str]:
        if self._llm is None:
            raise DetectorExecutionError("Kein KI-Gateway verfügbar.")
        prompt = (
            f"Auftrag: {rule.user_prompt}\n"
            f"Ziel-URL: {page_url}\n"
            f"Seitentext-Auszug:\n{page_text[:6000]}"
        )
        res = self._llm.generate_structured(
            prompt=prompt,
            system_instruction=self._system_prompt or "Extrahiere den gesuchten aktuellen Zahlenwert.",
            schema_cls=AiMetricExtraction,
        )
        if not res.value_found or res.numeric_value is None:
            raise DetectorExecutionError("KI konnte keinen eindeutigen Zahlenwert auf der Seite finden.")
        return float(res.numeric_value), res.unit or rule.threshold.unit, res.context_summary, page_url

    def _extract_from_web_search(self, rule: AlertRule) -> tuple[float, str, str, str]:
        query = rule.search_query.strip() or rule.title
        search_hits = self._search_provider.search(query)
        if not search_hits:
            raise DetectorExecutionError(f"Kein aktueller Zahlenwert zu '{query}' im Web ermittelbar.")

        ranked_hits = rank_relevant_hits(search_hits, query)
        explicit_unit = rule.threshold.unit.strip()
        logger.info(
            "Web-Suche '%s': %d Treffer (%d relevant), Snippets: %s",
            query,
            len(search_hits),
            len(ranked_hits),
            " | ".join(f"{it.title}: {it.summary}" for it, _ in ranked_hits[:3])[:380],
        )

        heuristic_match = self._find_best_heuristic_match(ranked_hits, explicit_unit)
        if heuristic_match is not None and not self._should_verify_with_llm(rule, heuristic_match, explicit_unit):
            val, unit, hit = heuristic_match
            logger.info("Heuristik für '%s': %s (Einheit: '%s', Quelle: %s)", query, val, unit, hit.url)
            return val, unit, "aus Web-Suche ermittelt", hit.url

        llm_rejected = False
        if self._llm is not None and ranked_hits:
            llm_result, llm_rejected = self._extract_from_hits_with_llm(
                rule, query, [it for it, _ in ranked_hits], explicit_unit
            )
            if llm_result is not None:
                return llm_result

        if heuristic_match is not None and not llm_rejected:
            val, unit, hit = heuristic_match
            if not is_implausible_outlier(val, unit, rule):
                logger.info("Heuristik-Fallback für '%s': %s (%s)", query, val, unit)
                return val, unit, "aus Web-Suche ermittelt", hit.url

        ranked_items = [it for it, _ in ranked_hits]
        if not ranked_items:
            terms = extract_query_terms(query)
            ranked_items = [
                it for it in search_hits
                if not terms or count_term_matches(f"{it.title} {it.summary} {it.url}".lower(), terms) >= 1
            ]
        direct_result = self._try_direct_hit_pages(rule, ranked_items, explicit_unit)
        if direct_result is not None:
            return direct_result

        raise DetectorExecutionError(f"Kein aktueller Zahlenwert zu '{query}' im Web ermittelbar.")

    @staticmethod
    def _find_best_heuristic_match(
        ranked_hits: list[tuple[DiscoveredItem, int]],
        explicit_unit: str,
    ) -> tuple[float, str, DiscoveredItem] | None:
        for hit, _score in ranked_hits:
            hit_text = f"{hit.title} — {hit.summary}".strip(" —")
            hit_unit = explicit_unit or infer_unit_from_text(hit_text)
            parsed = parse_localized_float(hit_text, hit_unit, strict_unit=bool(hit_unit))
            if parsed is not None:
                return parsed, hit_unit, hit
        return None

    def _should_verify_with_llm(
        self,
        rule: AlertRule,
        heuristic_match: tuple[float, str, DiscoveredItem] | None,
        explicit_unit: str,
    ) -> bool:
        if self._llm is None:
            return False
        if heuristic_match is None:
            return True
        val, unit, hit = heuristic_match
        if is_implausible_outlier(val, unit, rule):
            return True
        if unit:
            hit_text = f"{hit.title} — {hit.summary}"
            distinct = {round(v, 4) for v in extract_all_unit_values(hit_text, unit)}
            if len(distinct) > 1 and not any(w in hit_text.lower() for w in ("aktuell", "derzeit", "jetzt")):
                return True
        return not explicit_unit and not unit

    def _extract_from_hits_with_llm(
        self,
        rule: AlertRule,
        query: str,
        hits: list[DiscoveredItem],
        explicit_unit: str,
    ) -> tuple[tuple[float, str, str, str] | None, bool]:
        if self._llm is None or not hits:
            return None, False
        clean_snippets = "\n".join(f"[{idx + 1}] {it.title}: {it.summary}" for idx, it in enumerate(hits))
        prompt = (
            f"Auftrag: {rule.user_prompt}\n"
            f"Gesuchte Kennzahl / Asset / Produkt: {query}\n"
            f"Erwartete Einheit: {explicit_unit or 'beliebig'}\n\n"
            f"Aktuelle Web-Suchergebnisse:\n{clean_snippets}\n\n"
            "STRIKTE REGEL: Die gesuchte Zahl MUSS zwingend wörtlich und mit passender Bedeutung "
            "in den obigen Suchergebnissen vorkommen! Verwende NIEMALS Trainingswissen oder irrelevante Zahlen.\n"
            "Wenn kein verlässlicher Wert zur gesuchten Kennzahl enthalten ist, setze `value_found` auf false."
        )
        try:
            res = self._llm.generate_structured(
                prompt=prompt,
                system_instruction=self._system_prompt or "Extrahiere den aktuellen Zahlenwert wahrheitsgemäß.",
                schema_cls=AiMetricExtraction,
            )
        except Exception as exc:
            logger.warning("KI-Zahlenextraktion fehlgeschlagen (%s): %s", type(exc).__name__, exc)
            return None, False

        if not res.value_found or res.numeric_value is None:
            return None, True

        val = float(res.numeric_value)
        matched_hit = find_grounded_hit(val, hits, explicit_unit)
        if matched_hit is None:
            logger.warning("KI-Wert %s ist nicht in den Such-Snippets belegt (verworfen).", val)
            return None, False
        return (
            val,
            res.unit or explicit_unit,
            res.context_summary or "aus Web-Suche ermittelt",
            matched_hit.url,
        ), False

    def _try_direct_hit_pages(
        self,
        rule: AlertRule,
        hits: list[DiscoveredItem],
        explicit_unit: str,
    ) -> tuple[float, str, str, str] | None:
        direct_urls = [it.url for it in hits if it.url and "news.google.com" not in it.url][:2]
        for url in direct_urls:
            try:
                resp = self._http.fetch(url)
                if resp.status_code >= 400:
                    continue
                page_text = extract_clean_html_text(resp.text)
                if not page_text:
                    continue
                if self._llm is not None:
                    try:
                        val, unit, ctx, final_url = self._extract_metric_from_page_with_llm(
                            rule, page_text, resp.final_url
                        )
                        if is_grounded_in_text(val, page_text):
                            return val, unit, ctx or "aus Quellseite ermittelt", final_url
                        continue
                    except DetectorExecutionError:
                        continue
                    except Exception as exc:
                        logger.debug("Direktabruf-KI fehlgeschlagen (%s), nutze Heuristik: %s", url, exc)

                val_opt = parse_localized_float(page_text, explicit_unit, strict_unit=bool(explicit_unit))
                if val_opt is not None and not is_implausible_outlier(val_opt, explicit_unit, rule):
                    return val_opt, explicit_unit, "aus Quellseite ermittelt", resp.final_url
            except Exception as exc:
                logger.debug("Direktabruf '%s' fehlgeschlagen: %s", url, exc)
        return None
