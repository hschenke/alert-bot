"""Relevanz-Filter, Plausibilitäts- & Grounding-Prüfung für numerische Web-Extraktion."""

from __future__ import annotations

import math
import re

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
_WORD_RE = re.compile(r"[a-zäöüß0-9]{3,}", re.IGNORECASE)


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


def rank_relevant_hits(search_hits: list[DiscoveredItem], query: str) -> list[tuple[DiscoveredItem, int]]:
    """Filtert und sortiert Suchtreffer anhand ihrer Titel-/Snippet-Relevanz zur Anfrage."""
    terms = extract_query_terms(query)
    min_required = 1 if len(terms) <= 2 else 2
    scored: list[tuple[int, int, int, DiscoveredItem]] = []
    for idx, hit in enumerate(search_hits):
        hit_text = f"{hit.title} — {hit.summary}".strip(" —").lower()
        term_hits = count_term_matches(hit_text, terms)
        if terms and term_hits < min_required:
            continue
        has_recency = int(any(w in hit_text for w in ("aktuell", "derzeit", "2026", "2027")))
        scored.append((term_hits, has_recency, -idx, hit))
    scored.sort(reverse=True)
    return [(hit, term_hits) for term_hits, _rec, _neg_idx, hit in scored]


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
