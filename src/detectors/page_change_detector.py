"""Webseiten-Änderungs- & Verfügbarkeits-Wächter (Diffs, Lagerbestand, freie Termine)."""

from __future__ import annotations

import hashlib

from src.detectors.comparators import evaluate_text_operator
from src.detectors.diagnostics import CheckTraceBuilder
from src.detectors.http_client import SafeHttpFetcher, extract_clean_html_text
from src.detectors.llm_gateway import AlertLLMGateway
from src.domain.enums import ComparisonOperator
from src.domain.exceptions import DetectorExecutionError
from src.domain.models import AlertRule, DetectorFinding
from src.domain.schemas import AiChangeEvaluation


class PageChangeDetector:
    """Überwacht eine Ziel-URL auf Text-Bedingungen oder relevante Inhaltsänderungen."""

    def __init__(
        self,
        http_fetcher: SafeHttpFetcher,
        llm_gateway: AlertLLMGateway | None = None,
        system_prompt: str = "",
    ) -> None:
        self._http = http_fetcher
        self._llm = llm_gateway
        self._system_prompt = system_prompt

    def evaluate(self, rule: AlertRule) -> DetectorFinding:
        """Lädt die Ziel-URL, berechnet den Inhalts-Hash und prüft Text- oder KI-Kriterien."""
        if not rule.target_url.strip():
            raise DetectorExecutionError("Keine Ziel-URL für den Website-Änderungs-Wächter angegeben.")

        trace = CheckTraceBuilder(stage="page_change", query=rule.target_url, provider="direct_http")
        resp = self._http.fetch(rule.target_url)
        if resp.status_code >= 400:
            trace.add_page_checked(f"{resp.final_url}: HTTP {resp.status_code}")
            trace.hint = "Prüfe, ob die Ziel-URL erreichbar ist oder Bot-Zugriffe blockiert."
            raise DetectorExecutionError(
                f"Zielseite antwortete mit HTTP {resp.status_code}.",
                diagnostics=trace.build(),
            )

        clean_text = extract_clean_html_text(resp.text, rule.threshold.css_selector)
        if not clean_text:
            trace.add_page_checked(f"{resp.final_url}: HTTP {resp.status_code}, leerer Inhalt")
            trace.hint = "CSS-Selektor prüfen oder testen, ob Inhalte erst per JavaScript geladen werden."
            raise DetectorExecutionError(
                "Kein Textinhalt auf der überwachten Seite gefunden.",
                diagnostics=trace.build(),
            )

        trace.add_page_checked(f"{resp.final_url}: HTTP {resp.status_code} ({len(clean_text)} Zeichen)")
        current_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()[:24]

        target_phrase = rule.threshold.target_text.strip()
        if target_phrase and rule.threshold.operator in {
            ComparisonOperator.CONTAINS,
            ComparisonOperator.NOT_CONTAINS,
        }:
            matched = evaluate_text_operator(clean_text, rule.threshold.operator, target_phrase)
            op_desc = "enthält" if rule.threshold.operator == ComparisonOperator.CONTAINS else "enthält nicht"
            state_txt = "erfüllt" if matched else "nicht erfüllt"
            return DetectorFinding(
                condition_met=matched,
                summary=f"Textprüfung (Seite {op_desc} '{target_phrase}') ist {state_txt}.",
                measured_display="Treffer" if matched else "Unverändert",
                content_hash=current_hash,
                source_url=resp.final_url,
                diagnostics=trace.build(),
            )

        if not rule.last_content_hash:
            return DetectorFinding(
                condition_met=False,
                summary="Initialer Seiten-Snapshot gespeichert – künftige Änderungen werden überwacht.",
                measured_display="Baseline gespeichert",
                content_hash=current_hash,
                source_url=resp.final_url,
                diagnostics=trace.build(),
            )

        if current_hash == rule.last_content_hash:
            return DetectorFinding(
                condition_met=False,
                summary="Seiteninhalt ist gegenüber der letzten Prüfung unverändert.",
                measured_display="Unverändert",
                content_hash=current_hash,
                source_url=resp.final_url,
                diagnostics=trace.build(),
            )

        if self._llm is not None and rule.condition_description.strip():
            return self._evaluate_change_with_llm(rule, clean_text, current_hash, resp.final_url, trace)

        snippet = clean_text[:220].replace("\n", " ")
        return DetectorFinding(
            condition_met=True,
            summary=f"Inhaltsänderung auf der überwachten Webseite erkannt: {snippet}...",
            measured_display="Änderung erkannt",
            content_hash=current_hash,
            source_url=resp.final_url,
            diagnostics=trace.build(),
        )

    def _evaluate_change_with_llm(
        self,
        rule: AlertRule,
        clean_text: str,
        current_hash: str,
        final_url: str,
        trace: CheckTraceBuilder,
    ) -> DetectorFinding:
        if self._llm is None:
            raise DetectorExecutionError("Kein KI-Gateway verfügbar.", diagnostics=trace.build())
        prompt = (
            f"Überwachungsauftrag: {rule.user_prompt}\n"
            f"Relevante Bedingung: {rule.condition_description}\n"
            f"Aktueller Seitentext ({final_url}):\n{clean_text[:6000]}"
        )
        res = self._llm.generate_structured(
            prompt=prompt,
            system_instruction=self._system_prompt or "Prüfe, ob die relevante Änderung eingetreten ist.",
            schema_cls=AiChangeEvaluation,
        )
        badge = res.extracted_status.strip() or ("Relevante Änderung" if res.relevant_change else "Irrelevante Änderung")
        trace.llm_status = badge[:80]
        return DetectorFinding(
            condition_met=bool(res.relevant_change),
            summary=res.summary.strip(),
            measured_display=badge[:80],
            content_hash=current_hash,
            source_url=final_url,
            diagnostics=trace.build(),
        )
