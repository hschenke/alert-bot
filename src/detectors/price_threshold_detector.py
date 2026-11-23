"""Preis-, Finanz- & Kennzahlen-Wächter über Webseiten-Selektor oder kostenfreie Web-Suche."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from src.detectors.comparators import (
    evaluate_numeric_operator,
    extract_all_unit_values,
    format_number_de,
    parse_localized_float,
)
from src.detectors.diagnostics import (
    CheckTraceBuilder,
    SearchDiagnostics,
    format_source_label,
)
from src.detectors.http_client import SafeHttpFetcher, extract_clean_html_text
from src.detectors.llm_gateway import AlertLLMGateway
from src.detectors.metric_grounding import (
    build_relaxed_metric_query,
    count_term_matches,
    extract_focused_page_excerpt,
    extract_query_terms,
    find_grounded_hit,
    infer_unit_from_text,
    is_grounded_in_text,
    is_implausible_outlier,
    is_low_signal_metric_source,
    rank_relevant_hits,
)
from src.detectors.search_provider import FreeWebSearchProvider, WebSearchProvider
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding, DiscoveredItem
from src.domain.schemas import AiMetricExtraction

logger = logging.getLogger(__name__)
_ESCALATION_BUDGET_SECONDS: float = 35.0
_MAX_DIRECT_PAGES_PER_PASS: int = 3


def _read_search_diag(provider: WebSearchProvider) -> SearchDiagnostics | None:
    getter = getattr(provider, "get_last_search_diagnostics", None)
    return getter() if callable(getter) else None


def _short_host(url: str) -> str:
    return (urlparse(url).hostname or url[:45]).removeprefix("www.")


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
        trace = CheckTraceBuilder(
            stage="target_url" if rule.target_url.strip() else "web_search",
            query=rule.target_url.strip() or rule.search_query.strip() or rule.title,
        )
        measured, unit, context, source_url = self._resolve_current_metric(rule, trace)
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
            diagnostics=trace.build(),
        )

    def _resolve_current_metric(
        self, rule: AlertRule, trace: CheckTraceBuilder
    ) -> tuple[float, str, str, str]:
        if rule.target_url.strip():
            return self._extract_from_target_url(rule, trace)
        return self._extract_from_web_search(rule, trace)

    def _extract_from_target_url(
        self, rule: AlertRule, trace: CheckTraceBuilder
    ) -> tuple[float, str, str, str]:
        trace.provider = "direct_http"
        final_url, extracted_text = self._fetch_page_text(
            rule.target_url, rule.threshold.css_selector, trace
        )
        if not extracted_text:
            trace.hint = "Prüfe, ob die Ziel-URL erreichbar ist oder ein CSS-Selektor korrigiert werden muss."
            raise DetectorExecutionError(
                f"Kein lesbarer Text auf der Zielseite ({_short_host(rule.target_url)}) gefunden.",
                diagnostics=trace.build(),
            )

        unit = rule.threshold.unit.strip()
        if rule.threshold.css_selector.strip():
            direct_num = parse_localized_float(extracted_text, unit)
            if direct_num is not None:
                trace.add_step(f"Direkt per CSS-Selektor '{rule.threshold.css_selector}' gelesen: {direct_num}")
                return direct_num, rule.threshold.unit, "direkt per CSS-Selektor gelesen", final_url

        if self._llm is None:
            direct_fallback = parse_localized_float(extracted_text, unit, strict_unit=bool(unit))
            if direct_fallback is not None:
                trace.add_step(f"Heuristisch aus Zielseite gelesen: {direct_fallback}")
                return direct_fallback, rule.threshold.unit, "aus Seitentext gelesen", final_url
            trace.hint = "CSS-Selektor unter Edit hinterlegen oder AI-Brain / GEMINI_API_KEY konfigurieren."
            raise DetectorExecutionError(
                "Zahlenwert konnte ohne CSS-Selektor oder KI-Gateway nicht eindeutig bestimmt werden.",
                diagnostics=trace.build(),
            )

        return self._extract_metric_from_page_with_llm(rule, extracted_text, final_url, trace)

    def _fetch_page_text(
        self, url: str, css_selector: str, trace: CheckTraceBuilder
    ) -> tuple[str, str]:
        host = _short_host(url)
        try:
            resp = self._http.fetch(url)
            if resp.status_code < 400:
                text = extract_clean_html_text(resp.text, css_selector)
                if text:
                    trace.add_page_checked(f"{host}: HTTP {resp.status_code} ({len(text)} Zeichen)")
                    return resp.final_url, text
                trace.add_page_checked(f"{host}: HTTP {resp.status_code}, leerer Inhalt")
            else:
                trace.add_page_checked(f"{host}: HTTP {resp.status_code}")
                trace.add_tool_error(f"direct_http ({host}): HTTP {resp.status_code}")
        except Exception as exc:
            trace.add_page_checked(f"{host}: Fehler ({str(exc)[:60]})")
            trace.add_tool_error(f"direct_http ({host}): {str(exc)[:70]}")

        fallback_fn = getattr(self._search_provider, "fetch_page_fallback", None)
        if callable(fallback_fn) and not css_selector.strip():
            fb = fallback_fn(url)
            if fb is not None:
                fb_url, fb_text, extractor = fb
                trace.add_tool(extractor)
                trace.add_page_checked(f"{host}: via {extractor} ({len(fb_text)} Zeichen)")
                return fb_url, fb_text
        return url, ""

    def _extract_metric_from_page_with_llm(
        self,
        rule: AlertRule,
        page_text: str,
        page_url: str,
        trace: CheckTraceBuilder,
    ) -> tuple[float, str, str, str]:
        if self._llm is None:
            raise DetectorExecutionError("Kein KI-Gateway verfügbar.", diagnostics=trace.build())
        excerpt = extract_focused_page_excerpt(
            page_text, rule.search_query or rule.title or rule.user_prompt, max_chars=6000
        )
        prompt = (
            f"Auftrag: {rule.user_prompt}\n"
            f"Ziel-URL: {page_url}\n"
            f"Seitentext-Auszug:\n{excerpt}"
        )
        res = self._llm.generate_structured(
            prompt=prompt,
            system_instruction=self._system_prompt or "Extrahiere den gesuchten aktuellen Zahlenwert.",
            schema_cls=AiMetricExtraction,
        )
        if not res.value_found or res.numeric_value is None:
            trace.llm_status = f"kein Wert auf {_short_host(page_url)}"
            raise DetectorExecutionError(
                f"KI konnte keinen eindeutigen Zahlenwert auf {_short_host(page_url)} finden.",
                diagnostics=trace.build(),
            )
        trace.llm_status = f"ok ({format_number_de(float(res.numeric_value))})"
        return float(res.numeric_value), res.unit or rule.threshold.unit, res.context_summary, page_url

    def _extract_from_web_search(
        self, rule: AlertRule, trace: CheckTraceBuilder
    ) -> tuple[float, str, str, str]:
        started = time.monotonic()
        query = rule.search_query.strip() or rule.title
        explicit_unit = rule.threshold.unit.strip()
        seen_urls: set[str] = set()

        search_hits = self._search_provider.search(query)
        trace.merge_search(_read_search_diag(self._search_provider))
        trace.raw_hits = len(search_hits)
        trace.add_step(f"Erstsuche '{query}': {len(search_hits)} Treffer")

        ranked_hits = rank_relevant_hits(search_hits, query, explicit_unit=explicit_unit)
        trace.relevant_hits = len(ranked_hits)
        self._record_sample_sources(search_hits, ranked_hits, trace)

        found = self._try_extract_from_batch(
            rule, query, search_hits, ranked_hits, explicit_unit, trace, seen_urls
        )
        if found is not None:
            return found

        all_hits = list(search_hits)
        all_ranked = list(ranked_hits)
        if (time.monotonic() - started) < _ESCALATION_BUDGET_SECONDS:
            escalated = self._run_escalation_pass(
                rule, query, explicit_unit, trace, seen_urls, all_hits, all_ranked
            )
            if escalated is not None:
                return escalated
        else:
            trace.add_step("Eskalation übersprungen (Zeitbudget erreicht)")

        raise self._build_not_found_error(query, explicit_unit, trace, all_hits, all_ranked)

    def _run_escalation_pass(
        self,
        rule: AlertRule,
        orig_query: str,
        explicit_unit: str,
        trace: CheckTraceBuilder,
        seen_urls: set[str],
        all_hits: list[DiscoveredItem],
        all_ranked: list[tuple[DiscoveredItem, int]],
    ) -> tuple[float, str, str, str] | None:
        relaxed = build_relaxed_metric_query(orig_query)
        escalate_fn = getattr(self._search_provider, "search_escalated", None)
        if not callable(escalate_fn) and not relaxed:
            return None

        next_query = relaxed or orig_query
        if callable(escalate_fn):
            extra_hits = escalate_fn(next_query)
            label = "Eskalation (höheres Tier)"
        else:
            extra_hits = self._search_provider.search(next_query)
            label = "Zweitsuche (vereinfachte Query)"
        trace.merge_search(_read_search_diag(self._search_provider))

        known_fps = {it.fingerprint for it in all_hits}
        fresh_hits = [it for it in extra_hits if it.fingerprint not in known_fps]
        all_hits.extend(fresh_hits)
        trace.raw_hits = len(all_hits)
        trace.add_step(f"{label} '{next_query}': +{len(fresh_hits)} neue Treffer")

        if not fresh_hits and not relaxed:
            return None

        target_pool = fresh_hits if fresh_hits else all_hits
        extra_ranked = rank_relevant_hits(target_pool, next_query, explicit_unit=explicit_unit)
        all_ranked.extend(extra_ranked)
        trace.relevant_hits = max(trace.relevant_hits, len(extra_ranked))
        self._record_sample_sources(target_pool, extra_ranked, trace)

        return self._try_extract_from_batch(
            rule, next_query, target_pool, extra_ranked, explicit_unit, trace, seen_urls
        )

    def _try_extract_from_batch(
        self,
        rule: AlertRule,
        query: str,
        search_hits: list[DiscoveredItem],
        ranked_hits: list[tuple[DiscoveredItem, int]],
        explicit_unit: str,
        trace: CheckTraceBuilder,
        seen_urls: set[str],
    ) -> tuple[float, str, str, str] | None:
        if not search_hits:
            return None

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
            trace.add_step(f"Heuristik erfolgreich: {format_number_de(val)} {unit} ({_short_host(hit.url)})")
            logger.info("Heuristik für '%s': %s (Einheit: '%s', Quelle: %s)", query, val, unit, hit.url)
            return val, unit, "aus Web-Suche ermittelt", hit.url

        llm_rejected = False
        if self._llm is not None and ranked_hits:
            llm_result, llm_rejected = self._extract_from_hits_with_llm(
                rule, query, [it for it, _ in ranked_hits], explicit_unit, trace
            )
            if llm_result is not None:
                return llm_result

        if heuristic_match is not None and not llm_rejected:
            val, unit, hit = heuristic_match
            if not is_implausible_outlier(val, unit, rule):
                trace.add_step(f"Heuristik-Fallback: {format_number_de(val)} {unit}")
                logger.info("Heuristik-Fallback für '%s': %s (%s)", query, val, unit)
                return val, unit, "aus Web-Suche ermittelt", hit.url

        ranked_items = [it for it, _ in ranked_hits]
        if not ranked_items:
            terms = extract_query_terms(query)
            ranked_items = [
                it for it in search_hits
                if not terms or count_term_matches(f"{it.title} {it.summary} {it.url}".lower(), terms) >= 1
            ]
        return self._try_direct_hit_pages(rule, ranked_items, explicit_unit, trace, seen_urls)

    @staticmethod
    def _record_sample_sources(
        search_hits: list[DiscoveredItem],
        ranked_hits: list[tuple[DiscoveredItem, int]],
        trace: CheckTraceBuilder,
    ) -> None:
        source_pool = [it for it, _ in ranked_hits[:4]] or search_hits[:3]
        for item in source_pool:
            trace.add_source(format_source_label(item))

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
        trace: CheckTraceBuilder,
    ) -> tuple[tuple[float, str, str, str] | None, bool]:
        if self._llm is None or not hits:
            return None, False
        if not any(any(ch.isdigit() for ch in f"{it.title} {it.summary}") for it in hits):
            trace.add_step(f"Snippets ({len(hits)} Treffer) enthalten keine Zahlen – prüfe Quellseiten direkt")
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
            trace.llm_status = f"Fehler ({type(exc).__name__}: {str(exc)[:70]})"
            trace.add_step(f"KI-Snippet-Analyse fehlgeschlagen: {type(exc).__name__}")
            logger.warning("KI-Zahlenextraktion fehlgeschlagen (%s): %s", type(exc).__name__, exc)
            return None, False

        if not res.value_found or res.numeric_value is None:
            trace.llm_status = "kein Wert in Snippets (value_found=false)"
            trace.add_step(f"KI-Snippet-Analyse ({len(hits)} Treffer): kein belegter Wert")
            return None, True

        val = float(res.numeric_value)
        matched_hit = find_grounded_hit(val, hits, explicit_unit)
        if matched_hit is None:
            trace.llm_status = f"Wert {format_number_de(val)} nicht in Snippets belegt (verworfen)"
            trace.add_step(f"Grounding-Guard verwarf unbelegten KI-Wert {format_number_de(val)}")
            logger.warning("KI-Wert %s ist nicht in den Such-Snippets belegt (verworfen).", val)
            return None, False

        trace.llm_status = f"ok ({format_number_de(val)})"
        trace.add_step(f"KI-Snippet-Analyse erfolgreich: {format_number_de(val)} ({_short_host(matched_hit.url)})")
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
        trace: CheckTraceBuilder,
        seen_urls: set[str],
    ) -> tuple[float, str, str, str] | None:
        skipped_news = sum(1 for it in hits if it.url and "news.google.com" in it.url)
        if skipped_news:
            trace.add_step(f"{skipped_news} Google-News-Redirect-Link(s) für Direktabruf übersprungen")

        direct_urls = [
            it.url for it in hits
            if it.url and "news.google.com" not in it.url and it.url not in seen_urls
        ][:_MAX_DIRECT_PAGES_PER_PASS]

        for url in direct_urls:
            seen_urls.add(url)
            final_url, page_text = self._fetch_page_text(url, "", trace)
            if not page_text:
                continue
            if self._llm is not None:
                try:
                    val, unit, ctx, resolved_url = self._extract_metric_from_page_with_llm(
                        rule, page_text, final_url, trace
                    )
                    if is_grounded_in_text(val, page_text):
                        trace.add_step(f"Direktseite erfolgreich: {format_number_de(val)} ({_short_host(resolved_url)})")
                        return val, unit, ctx or "aus Quellseite ermittelt", resolved_url
                    trace.add_step(f"Direktseite {_short_host(resolved_url)}: KI-Wert {val} nicht im Text belegt")
                    continue
                except DetectorExecutionError:
                    continue
                except Exception as exc:
                    logger.debug("Direktabruf-KI fehlgeschlagen (%s), nutze Heuristik: %s", url, exc)

            val_opt = parse_localized_float(page_text, explicit_unit, strict_unit=bool(explicit_unit))
            if val_opt is not None and not is_implausible_outlier(val_opt, explicit_unit, rule):
                trace.add_step(f"Direktseite heuristisch: {format_number_de(val_opt)} ({_short_host(final_url)})")
                return val_opt, explicit_unit, "aus Quellseite ermittelt", final_url
        return None

    @staticmethod
    def _build_not_found_error(
        query: str,
        explicit_unit: str,
        trace: CheckTraceBuilder,
        all_hits: list[DiscoveredItem],
        all_ranked: list[tuple[DiscoveredItem, int]],
    ) -> DetectorExecutionError:
        unit_label = f"{explicit_unit}-Wert" if explicit_unit else "Zahlenwert"
        only_low_signal = bool(all_hits) and all(is_low_signal_metric_source(h) for h in all_hits)

        if not all_hits:
            trace.hint = (
                "Suchanfrage unter Edit vereinfachen, eine direkte Ziel-URL hinterlegen "
                "oder unter Settings das AI-Brain Websearch-Tier auf Standard erhöhen."
            )
            diag = trace.build()
            tool_note = f" (Tools: {', '.join(diag.tools_used[:4])})" if diag.tools_used else ""
            msg = f"Keine Web-Suchtreffer für '{query}'{tool_note}."
            return DetectorExecutionError(msg, diagnostics=diag)

        if not all_ranked:
            trace.hint = "Suchanfrage unter Edit anpassen oder eine direkte Ziel-URL hinterlegen."
            diag = trace.build()
            msg = (
                f"Kein aktueller {unit_label} zu '{query}': "
                f"{len(all_hits)} Web-Treffer gefunden, aber 0 relevant."
            )
            return DetectorExecutionError(msg, diagnostics=diag)

        if only_low_signal:
            trace.hint = (
                "Websuche lieferte nur News-Headlines/Wikipedia ohne Live-Zahl. "
                "Direkte Ziel-URL (offizielle Seite) unter Edit hinterlegen oder "
                "unter Settings das AI-Brain Websearch-Tier auf Standard stellen."
            )
        else:
            trace.hint = (
                "Kein eindeutig belegter Wert in Snippets/Quellseiten gefunden. "
                "Direkte Ziel-URL (optional mit CSS-Selektor) unter Edit macht diesen Alert 100 % stabil."
            )

        diag = trace.build()
        reasons: list[str] = []
        if only_low_signal:
            reasons.append("nur News-/Wikipedia-Treffer ohne Live-Zahl")
        if diag.llm_status:
            reasons.append(f"KI: {diag.llm_status}")
        if diag.pages_checked:
            reasons.append(f"{len(diag.pages_checked)} Quellseite(n) geprüft")
        detail = f" ({'; '.join(reasons)})" if reasons else ""
        msg = (
            f"Kein aktueller {unit_label} zu '{query}' in "
            f"{diag.relevant_hits}/{diag.raw_hits} Web-Treffern ermittelbar{detail}."
        )
        return DetectorExecutionError(msg, diagnostics=diag)
