"""Kostenfreier, resilienter Multi-Engine Web-Such-Provider (DuckDuckGo, Yahoo & Google News)."""

from __future__ import annotations

import datetime
import email.utils
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Protocol

from bs4 import BeautifulSoup

from src.config.constants import MAX_DISCOVERED_ITEMS_PER_CHECK
from src.detectors.http_client import SafeHttpFetcher
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


def _extract_target_url(raw_href: str) -> str:
    """Extrahiert die echte Zieladresse aus DuckDuckGo- (/l/?uddg=...) oder Yahoo-Redirects (/RU=...)."""
    clean = (raw_href or "").strip()
    if not clean:
        return ""
    if "uddg=" in clean:
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(clean).query)
        clean = parsed.get("uddg", [clean])[0]
    elif "/RU=" in clean:
        try:
            target_part = clean.split("/RU=", 1)[1].split("/RK=", 1)[0]
            clean = urllib.parse.unquote(target_part)
        except Exception as exc:
            logger.debug("Yahoo Redirect Unquote fehlgeschlagen: %s", exc)
    safe = sanitize_display_url(clean)
    return "" if safe == "#" else safe


class FreeWebSearchProvider:
    """Kostenfreier Web-Sucher ohne API-Keys oder Quota-Limits (DuckDuckGo, Yahoo + Google News)."""

    def __init__(self, http_fetcher: SafeHttpFetcher | None = None) -> None:
        self._http = http_fetcher or SafeHttpFetcher()

    def search(self, query: str, max_results: int = MAX_DISCOVERED_ITEMS_PER_CHECK) -> list[DiscoveredItem]:
        """Sucht prioritär über DuckDuckGo/Yahoo und ergänzt bei Bedarf frische Google News."""
        clean_query = query.strip()
        if not clean_query:
            return []

        limit = max(1, min(max_results, MAX_DISCOVERED_ITEMS_PER_CHECK))
        results: list[DiscoveredItem] = []
        seen_fp: set[str] = set()

        def _add_items(items: list[DiscoveredItem]) -> None:
            for it in items:
                if it.fingerprint not in seen_fp:
                    seen_fp.add(it.fingerprint)
                    results.append(it)

        # 1. DuckDuckGo HTML/Lite versuchen
        _add_items(self._search_duckduckgo(clean_query, limit))

        # 2. Falls DuckDuckGo keine Treffer liefert (z. B. auf AWS / Streamlit Cloud blockiert), Yahoo nutzen
        if len(results) < 3:
            _add_items(self._search_yahoo(clean_query, limit))

        # 3. Google News mit strenger Zeitfilterung ergänzen
        if len(results) < 3 or any(w in clean_query.lower() for w in ("news", "nachrichten", "aktuell", "bericht")):
            _add_items(self._search_google_news(clean_query, limit))

        return results[:limit]

    def _search_duckduckgo(self, query: str, limit: int) -> list[DiscoveredItem]:
        """Führt eine DuckDuckGo-HTML/Lite-Suche durch."""
        try:
            get_url = f"{_DDG_HTML_ENDPOINT}?{urllib.parse.urlencode({'q': query})}"
            resp = self._http.fetch(get_url, headers=_BROWSER_HEADERS)
            items = self._parse_ddg_html(resp.text, limit)
            if items:
                return items
        except Exception as exc:
            logger.debug("DuckDuckGo HTML-GET fehlgeschlagen (%s), teste Lite-POST...", exc)

        try:
            resp_lite = self._http.post(_DDG_LITE_ENDPOINT, data={"q": query}, headers=_BROWSER_HEADERS)
            return self._parse_ddg_lite(resp_lite.text, limit)
        except Exception as exc:
            logger.warning("DuckDuckGo-Suche komplett fehlgeschlagen: %s", exc)
            return []

    @staticmethod
    def _parse_ddg_html(html_text: str, limit: int) -> list[DiscoveredItem]:
        soup = BeautifulSoup(html_text or "", "html.parser")
        items: list[DiscoveredItem] = []
        for a_tag in soup.select("a.result__a")[:limit]:
            title = a_tag.get_text(" ", strip=True)
            url = _extract_target_url(a_tag.get("href", ""))
            parent = a_tag.find_parent(class_="result") or a_tag.find_parent("div")
            snippet = ""
            if parent:
                snip_node = parent.select_one(".result__snippet")
                if snip_node:
                    snippet = snip_node.get_text(" ", strip=True)[:280]
            if title:
                fp = compute_item_fingerprint(url, title)
                items.append(DiscoveredItem(fingerprint=fp, title=title[:180], url=url, summary=snippet))
        return items

    @staticmethod
    def _parse_ddg_lite(html_text: str, limit: int) -> list[DiscoveredItem]:
        soup = BeautifulSoup(html_text or "", "html.parser")
        links = soup.select(".result-link")
        snippets = soup.select(".result-snippet")
        items: list[DiscoveredItem] = []
        for idx, a_tag in enumerate(links[:limit]):
            title = a_tag.get_text(" ", strip=True)
            url = _extract_target_url(a_tag.get("href", ""))
            snippet = snippets[idx].get_text(" ", strip=True)[:280] if idx < len(snippets) else ""
            if title:
                fp = compute_item_fingerprint(url, title)
                items.append(DiscoveredItem(fingerprint=fp, title=title[:180], url=url, summary=snippet))
        return items

    def _search_yahoo(self, query: str, limit: int) -> list[DiscoveredItem]:
        """Führt eine Yahoo Web-Suche durch (funktioniert resilient auf Cloud-Servern)."""
        url = f"{_YAHOO_SEARCH_ENDPOINT}?{urllib.parse.urlencode({'p': query})}"
        try:
            resp = self._http.fetch(url, headers=_BROWSER_HEADERS)
            return self._parse_yahoo_html(resp.text, limit)
        except Exception as exc:
            logger.debug("Yahoo Web-Suche fehlgeschlagen: %s", exc)
            return []

    @staticmethod
    def _parse_yahoo_html(html_text: str, limit: int) -> list[DiscoveredItem]:
        soup = BeautifulSoup(html_text or "", "html.parser")
        items: list[DiscoveredItem] = []
        for res in soup.select("div.algo")[:limit]:
            h3 = res.select_one("h3")
            a_tag = h3.select_one("a") if h3 else None
            if not a_tag:
                continue
            title = a_tag.get_text(" ", strip=True)
            target_url = _extract_target_url(a_tag.get("href", ""))
            comp = res.select_one(".compText, .compText p, .fc-falcon")
            desc = comp.get_text(" ", strip=True)[:280] if comp else ""
            if title:
                fp = compute_item_fingerprint(target_url, title)
                items.append(DiscoveredItem(fingerprint=fp, title=title[:180], url=target_url, summary=desc))
        return items

    def _search_google_news(self, query: str, limit: int) -> list[DiscoveredItem]:
        """Sucht aktuelle Meldungen über den kostenfreien Google News RSS Feed (zeitlich gefiltert)."""
        timed_query = query if "when:" in query else f"{query} when:180d"
        url = f"{_GOOGLE_NEWS_ENDPOINT}?{urllib.parse.urlencode({'q': timed_query, 'hl': 'de', 'gl': 'DE', 'ceid': 'DE:de'})}"
        try:
            resp = self._http.fetch(url, headers=_BROWSER_HEADERS)
            return self._parse_google_news_rss(resp.text, limit)
        except Exception as exc:
            logger.debug("Google News RSS-Suche fehlgeschlagen: %s", exc)
            return []

    @staticmethod
    def _parse_google_news_rss(xml_text: str, limit: int) -> list[DiscoveredItem]:
        if not xml_text or "<item>" not in xml_text:
            return []
        try:
            root = ET.fromstring(xml_text)  # noqa: S314
        except ET.ParseError:
            return []

        now = datetime.datetime.now(datetime.UTC)
        max_age = datetime.timedelta(days=365)
        items: list[DiscoveredItem] = []
        for node in root.findall(".//item"):
            if len(items) >= limit:
                break
            raw_date = (node.findtext("pubDate") or "").strip()
            pub_year = ""
            if raw_date:
                try:
                    dt = email.utils.parsedate_to_datetime(raw_date)
                    if dt and (now - dt) > max_age:
                        continue
                    pub_year = f"{dt.year} " if dt else ""
                except Exception as exc:
                    logger.debug("Google News pubDate konnte nicht geparst werden: %s", exc)
            title = (node.findtext("title") or "").strip()
            raw_link = (node.findtext("link") or "").strip()
            safe_link = sanitize_display_url(raw_link)
            url = "" if safe_link == "#" else safe_link
            raw_desc = BeautifulSoup((node.findtext("description") or ""), "html.parser").get_text(" ", strip=True)
            desc = f"[{pub_year.strip()}] {raw_desc}"[:280] if pub_year else raw_desc[:280]
            if title:
                fp = compute_item_fingerprint(url, title)
                items.append(DiscoveredItem(fingerprint=fp, title=title[:180], url=url, summary=desc))
        return items
