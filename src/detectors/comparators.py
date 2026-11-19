"""Reine Vergleichs- und Zahlen-Parser-Funktionen für Schwellenwert- und Text-Operatoren."""

from __future__ import annotations

import math
import re

from src.domain.enums import ComparisonOperator

_NUM_CORE = r"[-+]?(?:\d{1,3}(?:\s\d{3})+|\d+(?:[.,']\d+)*)"
_NUMBER_TOKEN_RE = re.compile(rf"(?<![A-Za-z0-9_]){_NUM_CORE}(?![A-Za-z0-9_])")
_PAST_MARKERS: tuple[str, ...] = (
    "2018",
    "2019",
    "2020",
    "2021",
    "2022",
    "2023",
    "2024",
    "früher",
    "damals",
    "ehemals",
    "vorher",
    "zuvor",
    "bisher",
    "von ",
)


def evaluate_numeric_operator(
    measured: float,
    operator: ComparisonOperator,
    target: float,
) -> bool:
    """Prüft einen gemessenen Zahlenwert gegen Operator und Zielwert."""
    if operator == ComparisonOperator.GTE:
        return measured >= target
    if operator == ComparisonOperator.GT:
        return measured > target
    if operator == ComparisonOperator.LTE:
        return measured <= target
    if operator == ComparisonOperator.LT:
        return measured < target
    if operator == ComparisonOperator.EQ:
        return math.isclose(measured, target, rel_tol=1e-4, abs_tol=1e-4)
    if operator == ComparisonOperator.NEQ:
        return not math.isclose(measured, target, rel_tol=1e-4, abs_tol=1e-4)
    return False


def evaluate_text_operator(
    measured_text: str,
    operator: ComparisonOperator,
    target_text: str,
) -> bool:
    """Prüft Textwerte (case-insensitiv) auf Gleichheit oder Teilstring-Vorkommen."""
    haystack = (measured_text or "").strip().lower()
    needle = (target_text or "").strip().lower()
    if not needle:
        return bool(haystack)
    if operator == ComparisonOperator.CONTAINS:
        return needle in haystack
    if operator == ComparisonOperator.NOT_CONTAINS:
        return needle not in haystack
    if operator == ComparisonOperator.EQ:
        return haystack == needle
    if operator == ComparisonOperator.NEQ:
        return haystack != needle
    return False


def _convert_token_to_float(raw_token: str) -> float | None:
    token = raw_token.replace(" ", "").replace("'", "")
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            normalized = token.replace(".", "").replace(",", ".")
        else:
            normalized = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        int_head = parts[0].lstrip("+-")
        is_multi_thousand = len(parts) > 2 and all(len(p) == 3 for p in parts[1:]) and 1 <= len(int_head) <= 3
        is_single_thousand = len(parts) == 2 and len(parts[1]) == 3 and len(int_head) >= 1 and int_head != "0"
        normalized = token.replace(",", "") if (is_multi_thousand or is_single_thousand) else token.replace(",", ".")
    elif token.count(".") >= 2:
        parts = token.split(".")
        int_head = parts[0].lstrip("+-")
        if all(len(p) == 3 for p in parts[1:]) and 1 <= len(int_head) <= 3 and int_head != "0":
            normalized = token.replace(".", "")
        else:
            return None
    else:
        normalized = token

    try:
        return float(normalized)
    except ValueError:
        return None


def _build_unit_suffix_pattern(clean_unit: str) -> str:
    """Liefert ein einheitsspezifisches Regex-Suffix (verhindert z. B. €-Treffer bei %-Regeln)."""
    lower = clean_unit.lower()
    if lower in {"%", "prozent", "pct"}:
        return r"(?:%|prozent(?:punkte?)?\b|pct\b)"
    if lower in {"€", "eur", "euro"}:
        return r"(?:€|euro?\b)"
    if lower in {"$", "usd", "dollar"}:
        return r"(?:\$|usd\b|dollar\b)"
    if lower in {"°c", "°", "grad"}:
        return r"(?:°\s*c\b|°|grad\b)"
    if lower in {"km/h", "kmh"}:
        return r"(?:km/h|kmh\b)"
    if lower == "mm":
        return r"mm\b"
    escaped = re.escape(clean_unit)
    return rf"{escaped}\b" if clean_unit[-1].isalnum() else escaped


def _build_currency_prefix_pattern(clean_unit: str) -> str | None:
    lower = clean_unit.lower()
    if lower in {"€", "eur", "euro"}:
        return r"(?:€|euro?\b)"
    if lower in {"$", "usd", "dollar"}:
        return r"(?:\$|usd\b|dollar\b)"
    return None


def _is_year_for_unit(value: float, clean_unit: str) -> bool:
    return (
        clean_unit.lower() in {"%", "prozent", "pct", "°c", "°", "grad"}
        and value.is_integer()
        and 1990 <= int(value) <= 2099
    )


def _has_past_context(clean_text: str, pos: int) -> bool:
    window = clean_text[max(0, pos - 45) : pos].lower()
    for sep in (".", ";", ",", "\n"):
        if sep in window:
            window = window.rsplit(sep, 1)[-1]
    for anchor in ("aktuell", "derzeit", "jetzt", "heute"):
        if anchor in window:
            window = window.rsplit(anchor, 1)[-1]
    return any(past in window for past in _PAST_MARKERS)


def extract_all_unit_values(raw_text: str, unit: str) -> list[float]:
    """Extrahiert alle Zahlenwerte aus `raw_text`, die explizit mit `unit` verknüpft sind."""
    clean_text = raw_text or ""
    clean_unit = unit.strip()
    if not clean_text or not clean_unit:
        return []

    unit_suffix = _build_unit_suffix_pattern(clean_unit)
    unit_pat = re.compile(
        rf"(?<![A-Za-z0-9_])({_NUM_CORE})\s*{unit_suffix}",
        re.IGNORECASE,
    )
    values: list[float] = []
    for match in unit_pat.finditer(clean_text):
        if _has_past_context(clean_text, match.start()):
            continue
        val = _convert_token_to_float(match.group(1))
        if val is not None and not _is_year_for_unit(val, clean_unit):
            values.append(val)

    prefix_pat_str = _build_currency_prefix_pattern(clean_unit)
    if prefix_pat_str:
        prefix_pat = re.compile(
            rf"(?<![A-Za-z0-9_]){prefix_pat_str}\s*({_NUM_CORE})(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        for match in prefix_pat.finditer(clean_text):
            if _has_past_context(clean_text, match.start()):
                continue
            val = _convert_token_to_float(match.group(1))
            if val is not None:
                values.append(val)
    return values


def parse_localized_float(raw_text: str, unit: str = "", strict_unit: bool = False) -> float | None:
    """Extrahiert eine Zahl unter Beachtung optionaler Einheiten und Filterung von Jahreszahlen."""
    clean_text = raw_text or ""
    clean_unit = unit.strip()
    if clean_unit:
        unit_suffix = _build_unit_suffix_pattern(clean_unit)
        recency_pat = re.compile(
            rf"(?:aktuell|derzeit|jetzt|heute|2025|2026|2027)\b[^.\n]{{0,60}}?(?<![A-Za-z0-9_])({_NUM_CORE})\s*{unit_suffix}",
            re.IGNORECASE,
        )
        for rec_match in recency_pat.finditer(clean_text):
            if _has_past_context(clean_text, rec_match.start(1)):
                continue
            val = _convert_token_to_float(rec_match.group(1))
            if val is not None and not _is_year_for_unit(val, clean_unit):
                return val

        unit_vals = extract_all_unit_values(clean_text, clean_unit)
        if unit_vals:
            return unit_vals[0]

        if strict_unit:
            return None

    candidates: list[float] = []
    for match in _NUMBER_TOKEN_RE.finditer(clean_text):
        val = _convert_token_to_float(match.group(0))
        if val is not None:
            candidates.append(val)

    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0].is_integer() and 1990 <= int(candidates[0]) <= 2099:
        return candidates[1]
    return candidates[0]


def format_number_de(value: float, decimals: int = 2) -> str:
    """Formatiert eine Zahl kompakt im deutschen Stil (z. B. `21,5` oder `1.200`)."""
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", ".")
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".").rstrip("0").rstrip(",")
