"""KI-Websuche- & Angebots-Wächter (z. B. neue Mietwohnungen, News, Events, Kleinanzeigen)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from src.config.constants import MAX_DISCOVERED_ITEMS_PER_CHECK
from src.detectors.http_client import SafeHttpFetcher, extract_clean_html_text
from src.detectors.llm_gateway import AlertLLMGateway
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding, DiscoveredItem
from src.domain.schemas import AiSearchEvaluation
from src.security.sanitization import compute_item_fingerprint, sanitize_display_url


def _try_parse_rss_entries(xml_text: str) -> list[DiscoveredItem]:
    """Extrahiert optional RSS-2.0- oder Atom-Einträge, falls `target_url` ein Feed ist."""
    stripped = (xml_text or "").lstrip()
    if not stripped.startswith("<") or len(stripped) > 500_000:
        return []
    upper_prefix = stripped[:2000].upper()
    if "<!DOCTYPE" in upper_prefix or "<!ENTITY" in upper_prefix:
        return []
    try:
        root = ET.fromstring(stripped)  # noqa: S314 - nur Größen-limitiertes Feed-XML ohne DTD-Expansion
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
    """Sucht per Gemini Google-Search (+ optionaler Portal-/Feed-URL) nach neuen passenden Treffern."""

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
        """Prüft Web-Suchergebnisse und optionale Ziel-URL auf passende Angebote/Ereignisse."""
        page_context = ""
        rss_candidates: list[DiscoveredItem] = []

        if rule.target_url.strip():
            resp = self._http.fetch(rule.target_url)
            rss_candidates = _try_parse_rss_entries(resp.text)
            if not rss_candidates:
                page_context = extract_clean_html_text(resp.text, rule.threshold.css_selector)[:6000]

        if self._llm is None:
            return self._evaluate_without_llm(rule, rss_candidates)

        query = rule.search_query.strip() or rule.title
        condition = rule.condition_description.strip() or rule.user_prompt
        prompt_parts = [
            f"Überwachungsauftrag: {rule.user_prompt}",
            f"Konkrete Auslöse-Bedingung: {condition}",
            f"Suchfokus / Suchanfrage: {query}",
        ]
        if rule.target_url:
            prompt_parts.append(f"Zusätzliche Ziel-URL: {rule.target_url}")
        if rss_candidates:
            rss_lines = "\n".join(f"- {it.title} ({it.url}): {it.summary}" for it in rss_candidates)
            prompt_parts.append(f"Aktuelle Feed-Einträge:\n{rss_lines}")
        elif page_context:
            prompt_parts.append(f"Auszug der Zielseite:\n{page_context}")

        evaluation = self._llm.generate_grounded_structured(
            prompt="\n\n".join(prompt_parts),
            system_instruction=self._system_prompt or "Prüfe aktuelle Web-Treffer präzise und wahrheitsgemäß.",
            schema_cls=AiSearchEvaluation,
        )

        discovered: list[DiscoveredItem] = []
        seen_fp: set[str] = set()
        for match in evaluation.matches[:MAX_DISCOVERED_ITEMS_PER_CHECK]:
            clean_title = match.title.strip()
            if not clean_title:
                continue
            safe_url = sanitize_display_url(match.url)
            final_url = "" if safe_url == "#" else safe_url
            fp = compute_item_fingerprint(final_url, clean_title)
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            discovered.append(
                DiscoveredItem(
                    fingerprint=fp,
                    title=clean_title[:180],
                    url=final_url,
                    summary=match.summary.strip()[:300],
                    price_or_value=match.price_or_value.strip()[:80],
                )
            )

        primary_url = discovered[0].url if discovered else rule.target_url
        measured_label = f"{len(discovered)} Treffer" if discovered else ("Erfüllt" if evaluation.condition_met else "Keine passenden Treffer")
        return DetectorFinding(
            condition_met=bool(evaluation.condition_met),
            summary=evaluation.summary.strip(),
            measured_display=measured_label,
            items=tuple(discovered),
            source_url=primary_url,
        )

    @staticmethod
    def _evaluate_without_llm(
        rule: AlertRule,
        rss_candidates: list[DiscoveredItem],
    ) -> DetectorFinding:
        if not rss_candidates:
            raise DetectorExecutionError(
                "Für KI-Websuchen wird ein konfigurierter GEMINI_API_KEY benötigt."
            )
        keywords = [
            kw.lower()
            for kw in (rule.search_query or "").split()
            if len(kw) >= 3
        ]
        filtered = [
            item
            for item in rss_candidates
            if not keywords or any(kw in f"{item.title} {item.summary}".lower() for kw in keywords)
        ]
        return DetectorFinding(
            condition_met=bool(filtered),
            summary=f"{len(filtered)} passende Feed-Einträge gefunden.",
            measured_display=f"{len(filtered)} Feed-Treffer",
            items=tuple(filtered),
            source_url=rule.target_url,
        )
