"""Reine Vergleichs- und Zahlen-Parser-Funktionen für Schwellenwert- und Text-Operatoren."""

from __future__ import annotations

import math
import re

from src.domain.enums import ComparisonOperator

_NUMBER_TOKEN_RE = re.compile(r"[-+]?\d+(?:[.,'\s]\d+)*")


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
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0].lstrip("+-")) >= 1:
            normalized = token.replace(",", "")
        else:
            normalized = token.replace(",", ".")
    else:
        normalized = token

    try:
        return float(normalized)
    except ValueError:
        return None


def parse_localized_float(raw_text: str, unit: str = "") -> float | None:
    """Extrahiert eine Zahl unter Beachtung optionaler Einheiten und Filterung von Jahreszahlen."""
    clean_text = raw_text or ""
    clean_unit = unit.strip()
    if clean_unit:
        unit_pat = re.compile(
            rf"([-+]?\d+(?:[.,'\s]\d+)*)\s*(?:{re.escape(clean_unit)}|prozent|euro|€|\$)",
            re.IGNORECASE,
        )
        unit_match = unit_pat.search(clean_text)
        if unit_match:
            val = _convert_token_to_float(unit_match.group(1))
            if val is not None:
                return val

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
