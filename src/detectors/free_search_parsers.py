"""Reine HTML- und RSS-Parser für DuckDuckGo, Yahoo (Dual-DOM) und Google News."""

from __future__ import annotations

import datetime
import email.utils
import logging
import urllib.parse
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup, Tag

from src.domain.models import DiscoveredItem
from src.security.sanitization import compute_item_fingerprint, sanitize_display_url

logger = logging.getLogger(__name__)


def extract_target_url(raw_href: str) -> str:
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


def _extract_yahoo_title(anchor: Tag) -> str:
    aria = str(anchor.get("aria-label") or "").strip()
    if aria:
        return aria[:180]
    inner_h3 = anchor.select_one("h3")
    if inner_h3:
        h3_txt = inner_h3.get_text(" ", strip=True)
        if h3_txt:
            return h3_txt[:180]
    return anchor.get_text(" ", strip=True)[:180]


def parse_ddg_html(html_text: str, limit: int) -> list[DiscoveredItem]:
    """Parst organische Treffer aus `html.duckduckgo.com/html/`."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    items: list[DiscoveredItem] = []
    for a_tag in soup.select("a.result__a")[:limit]:
        title = a_tag.get_text(" ", strip=True)
        url = extract_target_url(str(a_tag.get("href", "")))
        parent = a_tag.find_parent(class_="result") or a_tag.find_parent("div")
        snip_node = parent.select_one(".result__snippet") if parent else None
        snippet = snip_node.get_text(" ", strip=True)[:280] if snip_node else ""
        if title:
            fp = compute_item_fingerprint(url, title)
            items.append(DiscoveredItem(fp, title[:180], url, snippet, source_tool="duckduckgo_html"))
    return items


def parse_ddg_lite(html_text: str, limit: int) -> list[DiscoveredItem]:
    """Parst kompakte Treffer aus `lite.duckduckgo.com/lite/`."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    links = soup.select(".result-link")
    snippets = soup.select(".result-snippet")
    items: list[DiscoveredItem] = []
    for idx, a_tag in enumerate(links[:limit]):
        title = a_tag.get_text(" ", strip=True)
        url = extract_target_url(str(a_tag.get("href", "")))
        snippet = snippets[idx].get_text(" ", strip=True)[:280] if idx < len(snippets) else ""
        if title:
            fp = compute_item_fingerprint(url, title)
            items.append(DiscoveredItem(fp, title[:180], url, snippet, source_tool="duckduckgo_lite"))
    return items


def parse_yahoo_html(html_text: str, limit: int) -> list[DiscoveredItem]:
    """Parst organische Yahoo-Treffer für DE- (`h3 a`) und moderne DOM-Layouts (`div.compTitle a`)."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    items: list[DiscoveredItem] = []
    for res in soup.select("div.algo")[:limit]:
        a_tag = (
            res.select_one("div.compTitle a")
            or res.select_one("h3 a")
            or res.select_one("a:has(h3)")
        )
        if not a_tag:
            continue
        title = _extract_yahoo_title(a_tag)
        target_url = extract_target_url(str(a_tag.get("href", "")))
        comp = res.select_one(".compText, .compText p, .fc-falcon")
        desc = comp.get_text(" ", strip=True)[:280] if comp else ""
        if title:
            fp = compute_item_fingerprint(target_url, title)
            items.append(DiscoveredItem(fp, title[:180], target_url, desc, source_tool="yahoo_search"))
    return items


def parse_google_news_rss(xml_text: str, limit: int) -> list[DiscoveredItem]:
    """Parst Google News RSS-Items mit strenger Altersfilterung (max. 365 Tage)."""
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
            items.append(DiscoveredItem(fp, title[:180], url, desc, source_tool="google_news_rss"))
    return items
