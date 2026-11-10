"""Kostenfreier Web-Such- & Angebots-Wächter mit KI-Synthese und resilientem Heuristik-Fallback."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from src.config.constants import MAX_DISCOVERED_ITEMS_PER_CHECK
from src.detectors.comparators import evaluate_numeric_operator, parse_localized_float
from src.detectors.http_client import SafeHttpFetcher, extract_clean_html_text
from src.detectors.llm_gateway import AlertLLMGateway
from src.detectors.search_provider import FreeWebSearchProvider, WebSearchProvider
from src.domain.models import AlertRule, DetectorFinding, DiscoveredItem
from src.domain.schemas import AiSearchEvaluation
from src.security.sanitization import compute_item_fingerprint, sanitize_display_url

logger = logging.getLogger(__name__)


def _try_parse_rss_entries(xml_text: str) -> list[DiscoveredItem]:
    """Extrahiert optional RSS-2.0- oder Atom-Einträge, falls `target_url` ein Feed ist."""
    stripped = (xml_text or "").lstrip()
    if not stripped.startswith("<") or len(stripped) > 500_000:
        return []
    upper_prefix = stripped[:2000].upper()
    if "<!DOCTYPE" in upper_prefix or "<!ENTITY" in upper_prefix:
        return []
    try:
        root = ET.fromstring(stripped)  # noqa: S314
    except ET.ParseError:
        return []

    items: list[DiscoveredItem] = []
    for node in root.findall(".//item")[:MAX_DISCOVERED_ITEMS_PER_CHECK]:
        title = (node.findtext("title") or "").strip()
        link = sanitize_display_url((node.findtext("link") or "").strip())
        desc = (node.findtext("description") or "").strip()[:240]
        if title:
            url_val = "" if link == "#" else link
            items.append(
                DiscoveredItem(
                    fingerprint=compute_item_fingerprint(url_val, title),
                    title=title[:180],
                    url=url_val,
                    summary=desc,
                )
            )
    return items


class WebSearchDetector:
    """Kombiniert kostenfreie Multi-Engine-Websuche (DuckDuckGo/News) mit optionaler KI-Synthese."""

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
        """Prüft Web-Suchergebnisse und optionale Ziel-URL auf passende Treffer."""
        page_context, rss_candidates = self._fetch_target_url_candidates(rule)
        query = rule.search_query.strip() or rule.title.strip()
        search_hits = self._search_provider.search(query) if query else []
        candidates = self._merge_candidates(rss_candidates, search_hits)

        if self._llm is not None:
            try:
                return self._evaluate_with_llm(rule, candidates, page_context)
            except Exception as exc:
                logger.warning("KI-Auswertung fehlgeschlagen (%s), wechsle auf Heuristik: %s", type(exc).__name__, exc)

        return self._evaluate_heuristically(rule, candidates)

    def _fetch_target_url_candidates(self, rule: AlertRule) -> tuple[str, list[DiscoveredItem]]:
        if not rule.target_url.strip():
            return "", []
        try:
            resp = self._http.fetch(rule.target_url)
            rss = _try_parse_rss_entries(resp.text)
            if rss:
                return "", rss
            return extract_clean_html_text(resp.text, rule.threshold.css_selector)[:6000], []
        except Exception as exc:
            logger.debug("Zielseiten-Abruf '%s' nicht möglich: %s", rule.target_url, exc)
            return "", []

    @staticmethod
    def _merge_candidates(primary: list[DiscoveredItem], secondary: list[DiscoveredItem]) -> list[DiscoveredItem]:
        merged: list[DiscoveredItem] = []
        seen_fp: set[str] = set()
        for item in [*primary, *secondary]:
            if item.fingerprint not in seen_fp:
                seen_fp.add(item.fingerprint)
                merged.append(item)
        return merged[:MAX_DISCOVERED_ITEMS_PER_CHECK]

    def _evaluate_with_llm(
        self,
        rule: AlertRule,
        candidates: list[DiscoveredItem],
        page_context: str,
    ) -> DetectorFinding:
        if self._llm is None:
            return self._evaluate_heuristically(rule, candidates)
        condition = rule.condition_description.strip() or rule.user_prompt
        cand_lines = "\n".join(
            f"[{idx+1}] {it.title} | Link: {it.url}\n    Details: {it.summary}"
            for idx, it in enumerate(candidates)
        )
        prompt_parts = [
            f"Überwachungsauftrag: {rule.user_prompt}",
            f"Konkrete Auslöse-Bedingung: {condition}",
            f"Gefundene Web-Treffer:\n{cand_lines or '(keine separaten Treffer)'}",
        ]
        if page_context:
            prompt_parts.append(f"Auszug der Zielseite:\n{page_context}")

        evaluation = self._llm.generate_structured(
            prompt="\n\n".join(prompt_parts),
            system_instruction=self._system_prompt or "Prüfe aktuelle Web-Treffer präzise und wahrheitsgemäß.",
            schema_cls=AiSearchEvaluation,
        )
        return self._build_finding_from_evaluation(rule, evaluation, candidates)

    @staticmethod
    def _build_finding_from_evaluation(
        rule: AlertRule,
        evaluation: AiSearchEvaluation,
        candidates: list[DiscoveredItem],
    ) -> DetectorFinding:
        discovered: list[DiscoveredItem] = []
        seen_fp: set[str] = set()
        for m in evaluation.matches[:MAX_DISCOVERED_ITEMS_PER_CHECK]:
            title = m.title.strip()
            if not title:
                continue
            safe_url = sanitize_display_url(m.url)
            final_url = "" if safe_url == "#" else safe_url
            fp = compute_item_fingerprint(final_url, title)
            if fp not in seen_fp:
                seen_fp.add(fp)
                item = DiscoveredItem(fp, title[:180], final_url, m.summary.strip()[:300], m.price_or_value.strip()[:80])
                discovered.append(item)

        primary_url = discovered[0].url if discovered else (rule.target_url or (candidates[0].url if candidates else ""))
        measured_label = f"{len(discovered)} Treffer" if discovered else ("Erfüllt" if evaluation.condition_met else "0 Treffer")
        return DetectorFinding(
            condition_met=bool(evaluation.condition_met),
            summary=evaluation.summary.strip(),
            measured_display=measured_label,
            items=tuple(discovered),
            source_url=primary_url,
        )

    @staticmethod
    def _evaluate_heuristically(rule: AlertRule, candidates: list[DiscoveredItem]) -> DetectorFinding:
        """Kostenfreie, deterministische Heuristik ohne Quota-Verbrauch."""
        if not candidates:
            return DetectorFinding(
                condition_met=False,
                summary="Keine neuen passenden Web-Treffer gefunden.",
                measured_display="0 Treffer",
                items=(),
                source_url=rule.target_url,
            )

        keywords = [kw.lower() for kw in (rule.search_query or rule.title).split() if len(kw) >= 3]
        matched: list[DiscoveredItem] = []
        target_val = rule.threshold.target_value
        op = rule.threshold.operator

        for item in candidates:
            text = f"{item.title} {item.summary}".lower()
            if keywords and not any(kw in text for kw in keywords):
                continue
            if target_val is not None:
                val = parse_localized_float(f"{item.title} {item.summary} {item.price_or_value}")
                if val is not None and not evaluate_numeric_operator(val, op, target_val):
                    continue
            matched.append(item)

        final_items = matched or candidates
        primary_url = final_items[0].url if final_items else rule.target_url
        return DetectorFinding(
            condition_met=bool(final_items),
            summary=f"{len(final_items)} passende Treffer im Web gefunden (kostenfreie Web-Suche).",
            measured_display=f"{len(final_items)} Web-Treffer",
            items=tuple(final_items[:MAX_DISCOVERED_ITEMS_PER_CHECK]),
            source_url=primary_url,
        )
