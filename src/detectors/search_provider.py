"""Web-Such-Provider (`ai_brain` + kostenfreier Multi-Engine-Fallback via DuckDuckGo, Yahoo & Google News)."""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from typing import Protocol

import ai_brain
from ai_brain import BrainError, BrainTier

from src.config.constants import MAX_DISCOVERED_ITEMS_PER_CHECK
from src.detectors.diagnostics import SearchDiagnostics
from src.detectors.free_search_parsers import (
    parse_ddg_html,
    parse_ddg_lite,
    parse_google_news_rss,
    parse_yahoo_html,
)
from src.detectors.http_client import SafeHttpFetcher
from src.detectors.metric_grounding import is_low_signal_metric_source
from src.domain.models import DiscoveredItem
from src.security.sanitization import compute_item_fingerprint, sanitize_display_url

logger = logging.getLogger(__name__)

_DDG_HTML_ENDPOINT: str = "https://html.duckduckgo.com/html/"
_DDG_LITE_ENDPOINT: str = "https://lite.duckduckgo.com/lite/"
_YAHOO_SEARCH_ENDPOINT: str = "https://de.search.yahoo.com/search"
_GOOGLE_NEWS_ENDPOINT: str = "https://news.google.com/rss/search"

_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}


class WebSearchProvider(Protocol):
    """Abstrakter Such-Provider für Web-Ergebnisse."""

    def search(self, query: str, max_results: int = 10) -> list[DiscoveredItem]: ...


def _has_direct_web_hit(items: list[DiscoveredItem]) -> bool:
    return any(not is_low_signal_metric_source(it) for it in items)


def _merge_prioritizing_direct(
    primary: list[DiscoveredItem],
    secondary: list[DiscoveredItem],
    limit: int,
) -> list[DiscoveredItem]:
    direct: list[DiscoveredItem] = []
    low_sig: list[DiscoveredItem] = []
    seen_fp: set[str] = set()
    for it in [*primary, *secondary]:
        if it.fingerprint in seen_fp:
            continue
        seen_fp.add(it.fingerprint)
        (low_sig if is_low_signal_metric_source(it) else direct).append(it)
    return [*direct, *low_sig][:limit]


def _status_note(status_code: int) -> str:
    if status_code in {202, 403, 429}:
        return f"HTTP {status_code} (Anti-Bot)"
    if status_code >= 400:
        return f"HTTP {status_code}"
    return "0 Treffer"


class FreeWebSearchProvider:
    """Kostenfreier Web-Sucher ohne API-Keys oder Quota-Limits (DuckDuckGo, Yahoo + Google News)."""

    def __init__(self, http_fetcher: SafeHttpFetcher | None = None) -> None:
        self._http = http_fetcher or SafeHttpFetcher()
        self._local = threading.local()

    def get_last_search_diagnostics(self) -> SearchDiagnostics | None:
        return getattr(self._local, "last_diag", None)

    def search(self, query: str, max_results: int = MAX_DISCOVERED_ITEMS_PER_CHECK) -> list[DiscoveredItem]:
        """Sucht prioritär über DuckDuckGo/Yahoo und ergänzt bei Bedarf frische Google News."""
        clean_query = query.strip()
        if not clean_query:
            self._local.last_diag = None
            return []

        started = time.monotonic()
        limit = max(1, min(max_results, MAX_DISCOVERED_ITEMS_PER_CHECK))
        results: list[DiscoveredItem] = []
        seen_fp: set[str] = set()
        tools_exec: list[str] = []
        tool_errs: list[str] = []

        def _add_items(items: list[DiscoveredItem]) -> None:
            for it in items:
                if it.fingerprint not in seen_fp:
                    seen_fp.add(it.fingerprint)
                    results.append(it)

        _add_items(self._search_duckduckgo(clean_query, limit, tools_exec, tool_errs))
        if len(results) < 3:
            _add_items(self._search_yahoo(clean_query, limit, tools_exec, tool_errs))
        if len(results) < 3 or any(w in clean_query.lower() for w in ("news", "nachrichten", "aktuell", "bericht")):
            _add_items(self._search_google_news(clean_query, limit, tools_exec, tool_errs))

        final_items = results[:limit]
        self._local.last_diag = SearchDiagnostics(
            provider="free_web_search",
            query=clean_query,
            tools_executed=tuple(tools_exec),
            tool_errors=tuple(tool_errs),
            raw_count=len(final_items),
            source_tools=tuple(dict.fromkeys(it.source_tool for it in final_items if it.source_tool)),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return final_items

    def _search_duckduckgo(
        self, query: str, limit: int, tools_exec: list[str], tool_errs: list[str]
    ) -> list[DiscoveredItem]:
        tools_exec.append("duckduckgo_html")
        try:
            get_url = f"{_DDG_HTML_ENDPOINT}?{urllib.parse.urlencode({'q': query})}"
            resp = self._http.fetch(get_url, headers=_BROWSER_HEADERS)
            items = parse_ddg_html(resp.text, limit)
            if items:
                return items
            tool_errs.append(f"duckduckgo_html: {_status_note(resp.status_code)}")
        except Exception as exc:
            tool_errs.append(f"duckduckgo_html: {str(exc)[:80]}")
            logger.debug("DuckDuckGo HTML-GET fehlgeschlagen (%s), teste Lite-POST...", exc)

        tools_exec.append("duckduckgo_lite")
        try:
            resp_lite = self._http.post(_DDG_LITE_ENDPOINT, data={"q": query}, headers=_BROWSER_HEADERS)
            items_lite = parse_ddg_lite(resp_lite.text, limit)
            if not items_lite:
                tool_errs.append(f"duckduckgo_lite: {_status_note(resp_lite.status_code)}")
            return items_lite
        except Exception as exc:
            tool_errs.append(f"duckduckgo_lite: {str(exc)[:80]}")
            logger.warning("DuckDuckGo-Suche komplett fehlgeschlagen: %s", exc)
            return []

    def _search_yahoo(
        self,
        query: str,
        limit: int,
        tools_exec: list[str] | None = None,
        tool_errs: list[str] | None = None,
    ) -> list[DiscoveredItem]:
        if tools_exec is not None:
            tools_exec.append("yahoo_search")
        url = f"{_YAHOO_SEARCH_ENDPOINT}?{urllib.parse.urlencode({'p': query})}"
        try:
            resp = self._http.fetch(url, headers=_BROWSER_HEADERS)
            items = parse_yahoo_html(resp.text, limit)
            if not items and tool_errs is not None:
                tool_errs.append(f"yahoo_search: {_status_note(resp.status_code)}")
            return items
        except Exception as exc:
            if tool_errs is not None:
                tool_errs.append(f"yahoo_search: {str(exc)[:80]}")
            logger.debug("Yahoo Web-Suche fehlgeschlagen: %s", exc)
            return []

    def _search_google_news(
        self,
        query: str,
        limit: int,
        tools_exec: list[str] | None = None,
        tool_errs: list[str] | None = None,
    ) -> list[DiscoveredItem]:
        if tools_exec is not None:
            tools_exec.append("google_news_rss")
        timed_query = query if "when:" in query else f"{query} when:180d"
        url = f"{_GOOGLE_NEWS_ENDPOINT}?{urllib.parse.urlencode({'q': timed_query, 'hl': 'de', 'gl': 'DE', 'ceid': 'DE:de'})}"
        try:
            resp = self._http.fetch(url, headers=_BROWSER_HEADERS)
            items = parse_google_news_rss(resp.text, limit)
            if not items and tool_errs is not None:
                tool_errs.append(f"google_news_rss: {_status_note(resp.status_code)}")
            return items
        except Exception as exc:
            if tool_errs is not None:
                tool_errs.append(f"google_news_rss: {str(exc)[:80]}")
            logger.debug("Google News RSS-Suche fehlgeschlagen: %s", exc)
            return []


def _map_brain_citation(citation: object) -> DiscoveredItem | None:
    """Mappt eine `BrainCitation` sicher auf ein deduplizierbares `DiscoveredItem`."""
    safe_url = sanitize_display_url(str(getattr(citation, "url", "") or ""))
    url = "" if safe_url == "#" else safe_url
    raw_title = str(getattr(citation, "title", "") or "").strip()
    title = (raw_title or url)[:180]
    if not title:
        return None
    snippet = str(getattr(citation, "snippet", "") or "").strip()[:280]
    source_tool = str(getattr(citation, "source_tool", "") or "").strip()[:60]
    fp = compute_item_fingerprint(url, title)
    return DiscoveredItem(fp, title, url, snippet, source_tool=source_tool)


def _format_brain_tool_errors(search_res: object) -> list[str]:
    raw_errors = getattr(search_res, "tool_errors", None) or {}
    errors = [f"{tid}: {str(msg)[:90]}" for tid, msg in raw_errors.items()]
    executed = list(getattr(search_res, "tools_executed", None) or [])
    hit_tools = {
        str(getattr(c, "source_tool", "") or "").strip()
        for c in (getattr(search_res, "citations", None) or [])
    }
    for tid in executed:
        if tid not in raw_errors and tid not in hit_tools:
            errors.append(f"{tid}: 0 Treffer")
    return errors


class BrainWebSearchProvider:
    """Web-Such-Provider über `ai_brain` (`tier="auto"`, App-Policy inkl. Maps) mit Free-Fallback."""

    def __init__(
        self,
        http_fetcher: SafeHttpFetcher | None = None,
        fallback_provider: WebSearchProvider | None = None,
    ) -> None:
        self._fallback = fallback_provider or FreeWebSearchProvider(http_fetcher)
        self._local = threading.local()

    def get_last_search_diagnostics(self) -> SearchDiagnostics | None:
        return getattr(self._local, "last_diag", None)

    def search(
        self, query: str, max_results: int = MAX_DISCOVERED_ITEMS_PER_CHECK
    ) -> list[DiscoveredItem]:
        """Führt `ai_brain`-Suche gemäß App-Policy aus und ergänzt bei Fehler/Low-Signal via Free-Fallback."""
        clean_query = query.strip()
        if not clean_query:
            self._local.last_diag = None
            return []
        limit = max(1, min(max_results, MAX_DISCOVERED_ITEMS_PER_CHECK))
        try:
            with ai_brain.connect() as brain:
                search_res = brain.search(clean_query, tier="auto", max_results=limit, allow_paid=None)
        except BrainError as exc:
            logger.warning("AI-Brain Websuche fehlgeschlagen (%s) – nutze FreeWebSearchProvider.", exc)
            return self._fallback_with_diag(clean_query, limit, f"ai_brain: {exc}")
        return self._process_brain_result(clean_query, search_res, limit)

    def search_escalated(
        self, query: str, max_results: int = MAX_DISCOVERED_ITEMS_PER_CHECK
    ) -> list[DiscoveredItem]:
        """Eskaliert bei leerer/schwacher Erstsuche gezielt auf das nächsthöhere Tier (Policy-Budget bleibt gewahrt)."""
        clean_query = query.strip()
        if not clean_query:
            return []
        limit = max(1, min(max_results, MAX_DISCOVERED_ITEMS_PER_CHECK))
        next_tier = BrainTier.STANDARD
        try:
            with ai_brain.connect() as brain:
                policy = brain.get_app_policy()
                next_tier = BrainTier.STANDARD if policy.search_tier == BrainTier.FAST else BrainTier.DEEP
                search_res = brain.search(
                    clean_query, tier=next_tier, max_results=limit, allow_paid=bool(policy.allow_paid)
                )
        except BrainError as exc:
            logger.warning("AI-Brain Eskalation (%s) fehlgeschlagen (%s) – nutze Free-Fallback.", next_tier.value, exc)
            return self._fallback_with_diag(clean_query, limit, f"ai_brain ({next_tier.value}): {exc}")
        return self._process_brain_result(clean_query, search_res, limit)

    def fetch_page_fallback(self, url: str) -> tuple[str, str, str] | None:
        """Lädt blockierte/JS-lastige Seiten über `ai_brain.fetch_page` (inkl. Jina Reader, 0 €-Policy)."""
        try:
            with ai_brain.connect() as brain:
                policy = brain.get_app_policy()
                page = brain.fetch_page(url, allow_paid=bool(policy.allow_paid))
                if page.text_content.strip():
                    return page.url, page.text_content, page.extractor_used or "ai_brain_fetch"
        except Exception as exc:
            logger.debug("AI-Brain fetch_page Fallback fehlgeschlagen (%s): %s", url, exc)
        return None

    def _process_brain_result(
        self, clean_query: str, search_res: object, limit: int
    ) -> list[DiscoveredItem]:
        items: list[DiscoveredItem] = []
        seen_fp: set[str] = set()
        for citation in getattr(search_res, "citations", None) or []:
            mapped = _map_brain_citation(citation)
            if mapped is not None and mapped.fingerprint not in seen_fp:
                seen_fp.add(mapped.fingerprint)
                items.append(mapped)

        tier_obj = getattr(search_res, "tier", None)
        tier_label = tier_obj.value if hasattr(tier_obj, "value") else str(tier_obj or "auto")
        tools_exec = list(getattr(search_res, "tools_executed", None) or [])
        tool_errors = _format_brain_tool_errors(search_res)
        latency_ms = int(getattr(search_res, "latency_ms", 0) or 0)
        fallback_used = False

        logger.info(
            "AI-Brain Websuche '%s' (tier=%s, %d ms): %d Treffer, Tools=%s%s",
            clean_query, tier_label, latency_ms, len(items), tools_exec or ["(keine)"],
            f", Engpässe={tool_errors}" if tool_errors else "",
        )

        if not items or not _has_direct_web_hit(items):
            logger.info(
                "AI-Brain (%s) lieferte %d Treffer ohne direkte Web-SERP-Quellen – ergänze via FreeWebSearchProvider.",
                tier_label, len(items),
            )
            fb_items = self._fallback.search(clean_query, max_results=limit)
            fallback_used = True
            items = _merge_prioritizing_direct(fb_items, items, limit)
            fb_diag = getattr(self._fallback, "get_last_search_diagnostics", lambda: None)()
            if fb_diag is not None:
                tools_exec.extend(t for t in fb_diag.tools_executed if t not in tools_exec)
                tool_errors.extend(e for e in fb_diag.tool_errors if e not in tool_errors)

        final_items = items[:limit]
        self._local.last_diag = SearchDiagnostics(
            provider="ai_brain+free" if fallback_used else "ai_brain",
            tier=tier_label,
            query=clean_query,
            tools_executed=tuple(tools_exec),
            tool_errors=tuple(tool_errors),
            raw_count=len(final_items),
            source_tools=tuple(dict.fromkeys(it.source_tool for it in final_items if it.source_tool)),
            fallback_used=fallback_used,
            latency_ms=latency_ms,
        )
        return final_items

    def _fallback_with_diag(self, query: str, limit: int, brain_err: str) -> list[DiscoveredItem]:
        fb_items = self._fallback.search(query, max_results=limit)
        fb_diag = getattr(self._fallback, "get_last_search_diagnostics", lambda: None)()
        self._local.last_diag = SearchDiagnostics(
            provider="free_web_search (Fallback)",
            query=query,
            tools_executed=fb_diag.tools_executed if fb_diag else (),
            tool_errors=(brain_err[:120], *(fb_diag.tool_errors if fb_diag else ())),
            raw_count=len(fb_items),
            source_tools=tuple(dict.fromkeys(it.source_tool for it in fb_items if it.source_tool)),
            fallback_used=True,
            latency_ms=fb_diag.latency_ms if fb_diag else 0,
        )
        return fb_items


def create_web_search_provider(
    http_fetcher: SafeHttpFetcher | None = None,
) -> WebSearchProvider:
    """Liefert `BrainWebSearchProvider` bei aktivem `AI_BRAIN_KEY`, sonst `FreeWebSearchProvider`."""
    if ai_brain.is_configured():
        return BrainWebSearchProvider(http_fetcher=http_fetcher)
    return FreeWebSearchProvider(http_fetcher=http_fetcher)
