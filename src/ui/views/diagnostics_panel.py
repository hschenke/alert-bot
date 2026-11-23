"""Präsentations-Helfer für strukturierte Check-Diagnosen und Fehlerdetails in der Streamlit-UI."""

from __future__ import annotations

import streamlit as st

from src.detectors.diagnostics import deserialize_diagnostics, format_diagnostics_one_liner
from src.domain.enums import CheckOutcome
from src.domain.models import AlertRule, CheckDiagnostics, CheckLogEntry


def render_last_error_diagnostics(rule: AlertRule, logs: list[CheckLogEntry]) -> None:
    """Zeigt den letzten Fehler inkl. kompakter Diagnose-Zusammenfassung und Lösungstipp."""
    if not rule.last_error:
        return
    st.warning(f"Last Error: {rule.last_error}")

    latest_err_log = next((lg for lg in logs if lg.outcome == CheckOutcome.ERROR and lg.details_json), None)
    if latest_err_log is None:
        return
    diag = deserialize_diagnostics(latest_err_log.details_json)
    if diag is None:
        return

    one_liner = format_diagnostics_one_liner(diag)
    if one_liner:
        st.caption(f"🔎 {one_liner}")
    if diag.hint:
        st.caption(f"💡 {diag.hint}")


def render_check_log_diagnostics(diag: CheckDiagnostics) -> None:
    """Rendert den detaillierten Diagnose-Trace eines einzelnen Prüf-Laufs im kompakten Rahmen."""
    with st.container(border=True):
        meta: list[str] = []
        if diag.stage:
            meta.append(f"**Stage:** `{diag.stage}`")
        if diag.provider:
            meta.append(f"**Engine:** `{diag.provider}`")
        if diag.query:
            meta.append(f"**Query/Target:** `{diag.query}`")
        if meta:
            st.markdown(" · ".join(meta))

        if diag.tools_used:
            st.caption(f"🛠️ **Tools:** {', '.join(diag.tools_used)}")
        if diag.tool_errors:
            st.caption(f"⚠️ **Engpässe / Tool-Fehler:** {' | '.join(diag.tool_errors)}")
        if diag.raw_hits or diag.relevant_hits:
            st.caption(f"🎯 **Suchtreffer:** {diag.relevant_hits} relevant von {diag.raw_hits} gesamt")
        if diag.sample_sources:
            st.caption("🌐 **Top-Quellen:** " + " · ".join(diag.sample_sources))
        if diag.llm_status:
            st.caption(f"🧠 **KI-Analyse:** {diag.llm_status}")
        if diag.pages_checked:
            st.caption("📄 **Direktseiten:** " + " · ".join(diag.pages_checked))
        if diag.steps:
            timeline = "\n".join(f"  - {step}" for step in diag.steps)
            st.markdown(f"**Ablauf:**\n{timeline}")
        if diag.hint:
            st.info(f"💡 {diag.hint}")
