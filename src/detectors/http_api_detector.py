"""Uptime-, HTTP-Status- & JSON-API-Wächter ohne `eval` (reine Pfad-Traversierung)."""

from __future__ import annotations

import json
from typing import Any

from src.detectors.comparators import (
    evaluate_numeric_operator,
    evaluate_text_operator,
    format_number_de,
)
from src.detectors.http_client import SafeHttpFetcher
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding
from src.security.sanitization import validate_json_path


def extract_json_dot_path(payload: Any, raw_path: str) -> Any:
    """Liest verschachtelte Werte sicher anhand eines Punktpfads wie `data.items.0.price`."""
    clean_path = validate_json_path(raw_path)
    if not clean_path:
        return payload

    normalized = clean_path.replace("[", ".").replace("]", "")
    current: Any = payload
    for segment in (seg for seg in normalized.split(".") if seg):
        if isinstance(current, dict):
            if segment not in current:
                raise KeyError(f"Schlüssel '{segment}' nicht im JSON gefunden.")
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit():
            idx = int(segment)
            if idx >= len(current):
                raise IndexError(f"Listen-Index {idx} außerhalb des Bereichs.")
            current = current[idx]
        else:
            raise KeyError(f"Pfadsegment '{segment}' kann nicht aufgelöst werden.")
    return current


class HttpApiDetector:
    """Prüft Erreichbarkeit (HTTP-Status) sowie optionale Text- oder JSON-Feldbedingungen."""

    def __init__(self, http_fetcher: SafeHttpFetcher) -> None:
        self._http = http_fetcher

    def evaluate(self, rule: AlertRule) -> DetectorFinding:
        """Ruft den Endpunkt ab und wertet Statuscode, JSON-Pfad oder Body-Text aus."""
        if not rule.target_url.strip():
            raise DetectorExecutionError("Keine Ziel-URL für den Uptime-/API-Wächter angegeben.")

        try:
            resp = self._http.fetch(rule.target_url)
        except DetectorExecutionError as exc:
            return DetectorFinding(
                condition_met=True,
                summary=f"Endpunkt nicht erreichbar: {exc}",
                measured_display="Offline / Timeout",
                source_url=rule.target_url,
            )

        expected_code = rule.threshold.expected_status_code or 200
        if not rule.threshold.json_path.strip() and not rule.threshold.target_text.strip():
            is_down = resp.status_code != expected_code
            status_label = f"HTTP {resp.status_code} ({resp.elapsed_ms} ms)"
            summary = (
                f"Endpunkt antwortete unerwartet mit HTTP {resp.status_code} (erwartet: {expected_code})."
                if is_down
                else f"Endpunkt online – {status_label}."
            )
            return DetectorFinding(
                condition_met=is_down,
                summary=summary,
                measured_value=float(resp.status_code),
                measured_display=status_label,
                source_url=resp.final_url,
            )

        if rule.threshold.json_path.strip():
            return self._evaluate_json_field(rule, resp.text, resp.final_url)

        matched = evaluate_text_operator(
            resp.text,
            rule.threshold.operator,
            rule.threshold.target_text,
        )
        return DetectorFinding(
            condition_met=matched,
            summary=f"HTTP {resp.status_code} – Textbedingung {'erfüllt' if matched else 'nicht erfüllt'}.",
            measured_display=f"HTTP {resp.status_code} ({resp.elapsed_ms} ms)",
            source_url=resp.final_url,
        )

    @staticmethod
    def _evaluate_json_field(rule: AlertRule, raw_body: str, final_url: str) -> DetectorFinding:
        try:
            parsed = json.loads(raw_body)
            field_val = extract_json_dot_path(parsed, rule.threshold.json_path)
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            raise DetectorExecutionError(f"JSON-Auswertung fehlgeschlagen: {exc}") from exc

        if rule.threshold.target_value is not None and isinstance(field_val, (int, float)):
            num_val = float(field_val)
            met = evaluate_numeric_operator(num_val, rule.threshold.operator, rule.threshold.target_value)
            disp = f"{rule.threshold.json_path} = {format_number_de(num_val)}"
            return DetectorFinding(
                condition_met=met,
                summary=f"JSON-Feld {disp} (Ziel: {rule.threshold.operator.value} {format_number_de(rule.threshold.target_value)}).",
                measured_value=num_val,
                measured_display=disp,
                source_url=final_url,
            )

        str_val = str(field_val)
        met = evaluate_text_operator(str_val, rule.threshold.operator, rule.threshold.target_text)
        disp = f"{rule.threshold.json_path} = {str_val[:60]}"
        return DetectorFinding(
            condition_met=met,
            summary=f"JSON-Feld {disp} – Bedingung {'erfüllt' if met else 'nicht erfüllt'}.",
            measured_display=disp,
            source_url=final_url,
        )
