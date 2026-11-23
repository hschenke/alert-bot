"""Relevanz-Filter, Query-Relaxation, Seiten-Fokussierung & Grounding-Prüfung für Kennzahlen."""

from __future__ import annotations

import math
import re
from urllib.parse import urlparse

from src.detectors.comparators import (
    extract_all_unit_values,
    format_number_de,
    parse_localized_float,
)
from src.domain.models import AlertRule, DiscoveredItem

_GENERIC_QUERY_WORDS: frozenset[str] = frozenset(
    {
        "aktuell", "aktuelle", "aktueller", "aktuelles", "derzeit", "heute", "jetzt",
        "neu", "neue", "neuer", "preis", "preise", "kurs", "kurse", "wert", "werte",
        "kosten", "satz", "prozent", "euro", "usd", "überwachen", "ueberwachen",
        "alarm", "check", "news", "bericht", "nachrichten", "je", "pro", "und",
        "oder", "der", "die", "das", "für", "fuer", "von", "mit", "bei", "auf",
        "aus", "im", "in", "an", "ab", "bis", "über", "ueber", "unter",
    }
)
_RELAX_STRIP_WORDS: frozenset[str] = frozenset(
    {"aktuell", "aktuelle", "aktueller", "aktuelles", "derzeit", "heute", "jetzt", "überwachen", "ueberwachen"}
)
LOW_SIGNAL_METRIC_TOOLS: frozenset[str] = frozenset(
    {
        "google_news_rss",
        "wikipedia_api",
        "wikidata_api",
        "arxiv_api",
        "hackernews_algolia",
        "tagesschau_rss",
    }
)
LOW_SIGNAL_METRIC_HOSTS: tuple[str, ...] = (
    "news.google.com",
    "wikipedia.org",
    "wikidata.org",
    "arxiv.org",
)
_WORD_RE = re.compile(r"[a-zäöüß0-9]{3,}", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")


def extract_query_terms(query: str) -> list[str]:
    """Extrahiert bedeutende Schlüsselwörter (ohne generische Füllwörter) aus einer Suchanfrage."""
    terms: list[str] = []
    for raw in _WORD_RE.findall(query.lower()):
        if raw not in _GENERIC_QUERY_WORDS and raw not in terms:
            terms.append(raw)
    return terms


def count_term_matches(text_lower: str, terms: list[str]) -> int:
    """Zählt, wie viele Suchbegriffe (oder >=7-Zeichen-Stämme) im Text vorkommen."""
    return sum(
        1 for term in terms
        if term in text_lower or (len(term) >= 8 and term[:7] in text_lower)
    )


def infer_unit_from_text(text: str) -> str:
    """Erkennt häufige Einheiten aus reinem Treffertext (ohne URLs)."""
    if "%" in text or "prozent" in text.lower():
        return "%"
    if "€" in text or "euro" in text.lower():
        return "€"
    return ""


def is_percent_unit(unit: str) -> bool:
    """True für Prozent-Einheiten."""
    return unit.strip().lower() in {"%", "prozent", "pct"}


def is_low_signal_metric_source(hit: DiscoveredItem) -> bool:
    """Erkennt News-Redirector- und Enzyklopädie-Quellen, die selten Live-Kennzahlen im Snippet tragen."""
    if hit.source_tool.strip().lower() in LOW_SIGNAL_METRIC_TOOLS:
        return True
    host = (urlparse(hit.url).hostname or "").lower()
    return any(blocked in host for blocked in LOW_SIGNAL_METRIC_HOSTS)


def build_relaxed_metric_query(query: str) -> str:
    """Erzeugt eine prägnantere Fallback-Suchanfrage ohne Füllwörter/überlange Wortendungen."""
    clean = " ".join((query or "").split())
    if not clean:
        return ""
    tokens: list[str] = []
    for raw in clean.split():
        low = raw.lower().strip(",.;:!?")
        if low in _RELAX_STRIP_WORDS:
            continue
        if len(low) >= 15 and low.endswith("ssatz"):
            tokens.append(raw[:-5])
        else:
            tokens.append(raw)
    relaxed = " ".join(tokens).strip()
    if not relaxed or relaxed.lower() == clean.lower():
        return ""
    return relaxed


def rank_relevant_hits(
    search_hits: list[DiscoveredItem],
    query: str,
    explicit_unit: str = "",
) -> list[tuple[DiscoveredItem, int]]:
    """Filtert und priorisiert Suchtreffer (direkte Webseiten vor News/Wikipedia, inkl. URL-Terms)."""
    terms = extract_query_terms(query)
    min_required = 1 if len(terms) <= 2 else 2
    scored: list[tuple[int, int, int, int, int, DiscoveredItem]] = []
    for idx, hit in enumerate(search_hits):
        text_no_url = f"{hit.title} — {hit.summary}".strip(" —")
        searchable = f"{text_no_url} {hit.url}".lower()
        term_hits = count_term_matches(searchable, terms)
        is_ai_grounding = hit.source_tool.strip().lower() == "gemini_google_search" and bool(hit.url)
        if terms and term_hits < min_required and not is_ai_grounding:
            continue
        is_direct = int(not is_low_signal_metric_source(hit))
        unit_hint = explicit_unit or infer_unit_from_text(text_no_url)
        has_unit_val = int(bool(unit_hint and extract_all_unit_values(text_no_url, unit_hint)))
        has_recency = int(any(w in text_no_url.lower() for w in ("aktuell", "derzeit", "2026", "2027")))
        effective_hits = max(term_hits, 1) if is_ai_grounding else term_hits
        scored.append((is_direct, effective_hits, has_unit_val, has_recency, -idx, hit))
    scored.sort(reverse=True)
    return [(hit, term_hits) for _dir, term_hits, _uv, _rec, _neg, hit in scored]


def extract_focused_page_excerpt(page_text: str, query: str, max_chars: int = 6000) -> str:
    """Kombiniert Seitenanfang mit Schlüsselwort-Fenstern, damit Tabellen weiter unten nicht abgeschnitten werden."""
    if len(page_text) <= max_chars:
        return page_text

    head_len = min(2200, max_chars // 2)
    terms = extract_query_terms(query)
    if not terms:
        return page_text[:max_chars]

    lower = page_text.lower()
    stems = [t[:7] if len(t) >= 8 else t for t in terms]
    windows: list[tuple[int, int, int]] = []
    pos = head_len
    step = 450
    while pos < len(page_text):
        end = min(len(page_text), pos + 1100)
        chunk = lower[pos:end]
        matches = sum(1 for st in stems if st in chunk)
        if matches > 0:
            has_num = 1 if _DIGIT_RE.search(chunk) else 0
            windows.append((matches * 3 + has_num, pos, end))
        pos += step

    if not windows:
        return page_text[:max_chars]

    windows.sort(key=lambda w: (-w[0], w[1]))
    chosen: list[tuple[int, int]] = []
    budget = max_chars - head_len - 40
    for _score, start, end in windows:
        if any(not (end <= cs or start >= ce) for cs, ce in chosen):
            continue
        span = end - start
        if span + 12 > budget:
            continue
        chosen.append((start, end))
        budget -= span + 12
        if budget < 250:
            break

    chosen.sort()
    parts = [page_text[:head_len].rstrip()]
    parts.extend(page_text[s:e].strip() for s, e in chosen)
    return "\n\n[...]\n\n".join(parts)[:max_chars]


def is_implausible_outlier(value: float, unit: str, rule: AlertRule) -> bool:
    """Erkennt offensichtlich unplausible Prozent-Ausreißer (z. B. 226 % bei Ziel 2,18 % oder Jahreszahlen)."""
    if not is_percent_unit(unit):
        return False
    if value.is_integer() and 1990 <= int(value) <= 2099:
        return True
    prev_val = parse_localized_float(rule.last_value_text)
    ref = rule.threshold.target_value if rule.threshold.target_value is not None else prev_val
    if ref is not None and 0 < abs(ref) < 25 and abs(value) > 50:
        return True
    return ref is None and abs(value) > 100


def is_grounded_in_text(value: float, text: str) -> bool:
    """Prüft mit Token-Grenzen, ob `value` wörtlich im Text (ohne URLs) vorkommt."""
    if not text:
        return False
    de_str = format_number_de(value)
    clean_val = f"{value:g}"
    val_2f = f"{value:.2f}"
    val_2f_de = val_2f.replace(".", ",")
    return any(
        re.search(rf"(?<![A-Za-z0-9_]){re.escape(rep)}(?![A-Za-z0-9_])", text)
        for rep in (de_str, clean_val, val_2f, val_2f_de)
    )


def find_grounded_hit(
    value: float,
    hits: list[DiscoveredItem],
    explicit_unit: str,
) -> DiscoveredItem | None:
    """Ordnet einen extrahierten Zahlenwert exakt dem belegenden Suchtreffer samt Einheit zu."""
    for hit in hits:
        hit_text = f"{hit.title} — {hit.summary}"
        if not is_grounded_in_text(value, hit_text):
            continue
        if explicit_unit:
            unit_vals = extract_all_unit_values(hit_text, explicit_unit)
            if unit_vals and not any(math.isclose(value, uv, rel_tol=1e-4, abs_tol=1e-4) for uv in unit_vals):
                continue
            if not unit_vals and explicit_unit.lower() not in hit_text.lower():
                continue
        return hit
    return None
