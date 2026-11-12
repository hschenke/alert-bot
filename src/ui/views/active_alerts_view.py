"""Übersicht aller aktiven & pausierten Alert-Wächter inklusive Live-Check & Prüf-Historie."""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from src.config.constants import INTERVAL_PRESETS_MINUTES
from src.detectors.rule_compiler import utc_now_iso
from src.domain.enums import AuthRole, CheckOutcome, RuleStatus, TriggerMode
from src.domain.models import AlertRule
from src.storage.repository import SqlAlertRepository
from src.ui.formatters import (
    DETECTOR_LABELS,
    OUTCOME_BADGES,
    STATUS_BADGES,
    TRIGGER_LABELS,
    format_interval_label,
    format_iso_timestamp,
)
from src.worker.dispatcher import AlertWorkDispatcher


def render_active_alerts_view(
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    role: AuthRole,
) -> None:
    """Zeigt KPI-Kennzahlen und steuerbare Karten für alle konfigurierten Wächter."""
    top_col, action_col = st.columns([3.2, 1.2])
    with top_col:
        st.subheader("📡 Aktive Wächter")
    with action_col:
        if st.button("➕ Neuer Alert", key="top_add_alert_btn", type="primary", use_container_width=True):
            st.session_state["active_page"] = "➕ Neuer Alert"
            st.rerun()

    rules = repository.list_rules()
    events = repository.list_events(limit=20)

    k1, k2, k3, k4 = st.columns(4)
    active_count = sum(1 for r in rules if r.status == RuleStatus.ACTIVE)
    total_hits = sum(r.total_triggers for r in rules)
    k1.metric("Aktive Wächter", active_count)
    k2.metric("Gesamt-Wächter", len(rules))
    k3.metric("Ausgelöste Alarme", total_hits)
    k4.metric("Letzte Treffer", len(events))

    st.divider()
    if not rules:
        st.info(
            "Noch keine Alert-Wächter angelegt. Klicke oben auf **➕ Neuer Alert**, "
            "um z. B. einen Temperatur- oder Mietwohnungs-Wächter zu starten."
        )
        return

    for rule in rules:
        _render_single_rule_card(rule, repository, dispatcher, role)


def _render_single_rule_card(
    rule: AlertRule,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    role: AuthRole,
) -> None:
    with st.container(border=True):
        top_left, top_right = st.columns([3.8, 1.2])
        with top_left:
            st.markdown(f"**{STATUS_BADGES[rule.status]} · {rule.title}**")
            st.caption(
                f"{DETECTOR_LABELS[rule.detector_type]}  •  "
                f"Takt: **{format_interval_label(rule.check_interval_minutes)}**  •  "
                f"{TRIGGER_LABELS[rule.trigger_mode]}"
            )
        with top_right:
            if rule.last_value_text:
                st.metric("Letzter Wert", rule.last_value_text)
            else:
                st.caption("Noch nicht geprüft")

        user_text = rule.condition_description or rule.user_prompt
        if user_text:
            st.markdown(
                f"<p style='font-size:0.9rem;margin:2px 0 6px 0;color:#e2e8f0;'>{user_text}</p>",
                unsafe_allow_html=True,
            )

        info_items: list[str] = []
        if rule.target_url:
            short_url = rule.target_url[:50] + ("…" if len(rule.target_url) > 50 else "")
            info_items.append(f"🔗 <a href='{rule.target_url}' target='_blank' style='color:#60a5fa;'>{short_url}</a>")
        elif rule.search_query:
            info_items.append(f"🔍 Suche: <code>{rule.search_query}</code>")

        if rule.threshold.target_value is not None:
            info_items.append(f"🎯 Ziel: <b>{rule.threshold.operator.value} {rule.threshold.target_value:g} {rule.threshold.unit}</b>")
        elif rule.trigger_mode == TriggerMode.ON_STATE_CHANGE:
            info_items.append("🎯 Auslöser: <i>Bei Wertänderung zum Basiswert</i>")

        if info_items:
            st.markdown(
                f"<div style='font-size:0.8rem;color:#94a3b8;margin-bottom:8px;'>{' &nbsp;•&nbsp; '.join(info_items)}</div>",
                unsafe_allow_html=True,
            )

        meta1, meta2, meta3 = st.columns(3)
        meta1.caption(f"🕓 Geprüft: **{format_iso_timestamp(rule.last_checked_at)}**")
        meta2.caption(f"⏭️ Nächster Check: **{format_iso_timestamp(rule.next_check_at)}**")
        meta3.caption(
            f"🔔 Alarme ausgelöst: **{rule.total_triggers}**",
            help="Zählt ausgelöste Alarm-Mitteilungen. Die erste Basismessung dient als Referenz und löst noch keinen Alarm aus.",
        )

        if rule.last_error:
            st.warning(f"Letzter Hinweis/Fehler: {rule.last_error}")

        if role == AuthRole.ADMIN:
            _render_admin_controls(rule, repository, dispatcher)

        with st.expander("📋 Letzte Prüf-Protokolle anzeigen"):
            logs = repository.list_check_logs(rule_id=rule.id, limit=8)
            if not logs:
                st.caption("Noch keine Prüf-Logs vorhanden.")
            for log in logs:
                st.markdown(
                    f"- `{format_iso_timestamp(log.created_at)}` — "
                    f"**{OUTCOME_BADGES[log.outcome]}** ({log.duration_ms} ms): {log.message}"
                )


def _render_admin_controls(
    rule: AlertRule,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
) -> None:
    b1, b2, b3, b4 = st.columns([1.4, 1.4, 1.5, 1])
    with b1:
        if st.button("▶️ Jetzt prüfen", key=f"chk_{rule.id}", use_container_width=True):
            with st.spinner(f"Prüfe »{rule.title}«..."):
                outcome, msg = dispatcher.check_rule_now(rule.id, repository=repository)
            if outcome and outcome.outcome == CheckOutcome.TRIGGERED:
                st.toast(f"🔔 Alarm ausgelöst: {rule.title}")
            else:
                st.toast(msg)
            st.rerun()

    with b2:
        is_active = rule.status == RuleStatus.ACTIVE
        toggle_label = "⏸️ Pausieren" if is_active else "▶️ Aktivieren"
        target_status = RuleStatus.PAUSED if is_active else RuleStatus.ACTIVE
        if st.button(toggle_label, key=f"tgl_{rule.id}", use_container_width=True):
            repository.set_rule_status(rule.id, target_status, utc_now_iso())
            st.rerun()

    with b3:
        presets = list(INTERVAL_PRESETS_MINUTES)
        curr_idx = presets.index(rule.check_interval_minutes) if rule.check_interval_minutes in presets else 3
        new_interval = st.selectbox(
            "Takt",
            options=presets,
            index=curr_idx,
            key=f"int_{rule.id}",
            label_visibility="collapsed",
            format_func=lambda m: f"⏱️ Alle {format_interval_label(m)}",
        )
        if new_interval != rule.check_interval_minutes:
            repository.upsert_rule(
                replace(rule, check_interval_minutes=new_interval, updated_at=utc_now_iso())
            )
            st.rerun()

    with b4:
        if st.button("🗑️ Löschen", key=f"del_{rule.id}", use_container_width=True):
            repository.delete_rule(rule.id)
            st.rerun()
