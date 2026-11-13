"""Übersicht aller aktiven & pausierten Alerts als kompakte, standardmäßig eingeklappte Boxen."""

from __future__ import annotations

import inspect
from dataclasses import replace

import streamlit as st

from src.config.constants import INTERVAL_PRESETS_MINUTES
from src.detectors.rule_compiler import utc_now_iso
from src.domain.enums import AuthRole, CheckOutcome, RuleStatus, TriggerMode
from src.domain.models import AlertRule
from src.security.sanitization import escape_html, sanitize_display_url
from src.storage.repository import SqlAlertRepository
from src.ui.formatters import (
    DETECTOR_LABELS,
    OUTCOME_BADGES,
    PAGE_ACTIVE_ALERTS,
    PAGE_NEW_ALERT,
    STATUS_BADGES,
    TRIGGER_LABELS,
    format_interval_label,
    format_iso_timestamp,
)
from src.worker.dispatcher import AlertWorkDispatcher

_EXPANDER_SUPPORTS_KEY = "key" in inspect.signature(st.expander).parameters
_STATUS_ICONS: dict[RuleStatus, str] = {
    RuleStatus.ACTIVE: "🟢",
    RuleStatus.PAUSED: "⏸️",
    RuleStatus.ERROR: "🔴",
}


def render_active_alerts_view(
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    role: AuthRole,
) -> None:
    """Zeigt KPI-Kennzahlen und eingeklappte Alert-Boxen für alle konfigurierten Regeln."""
    top_col, action_col = st.columns([3.5, 1.2], vertical_alignment="center")
    with top_col:
        st.subheader(PAGE_ACTIVE_ALERTS)
    with action_col:
        if st.button(PAGE_NEW_ALERT, key="top_add_alert_btn", use_container_width=True):
            st.session_state["active_page"] = PAGE_NEW_ALERT
            st.rerun()

    rules = repository.list_rules()
    events = repository.list_events(limit=20)

    k1, k2, k3, k4 = st.columns(4)
    active_count = sum(1 for r in rules if r.status == RuleStatus.ACTIVE)
    total_hits = sum(r.total_triggers for r in rules)
    k1.metric("Active Alerts", active_count)
    k2.metric("Total Alerts", len(rules))
    k3.metric("Triggers", total_hits)
    k4.metric("Recent Events", len(events))

    st.divider()
    if not rules:
        st.info(
            "Noch keine Alerts angelegt. Klicke oben auf **➕ New Alert**, "
            "um z. B. einen Weather-, Price- oder Web-Search-Alert zu starten."
        )
        return

    for rule in rules:
        _render_single_rule_card(rule, repository, dispatcher, role)


def _build_expander_label(rule: AlertRule) -> str:
    icon = _STATUS_ICONS[rule.status]
    interval = format_interval_label(rule.check_interval_minutes)
    warn_suffix = " · ⚠️" if rule.last_error and rule.status != RuleStatus.ERROR else ""
    if rule.last_value_text:
        val = rule.last_value_text.strip().replace("`", "'")
        short_val = val[:28] + ("…" if len(val) > 28 else "")
        return f"{icon} **{rule.title}** · `{short_val}` · ⏱️ {interval}{warn_suffix}"
    return f"{icon} **{rule.title}** · ⏱️ {interval}{warn_suffix}"


def _open_rule_expander(rule: AlertRule):
    label = _build_expander_label(rule)
    if _EXPANDER_SUPPORTS_KEY:
        return st.expander(label, expanded=False, key=f"alert_box_{rule.id}")
    return st.expander(label, expanded=False)


def _render_single_rule_card(
    rule: AlertRule,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    role: AuthRole,
) -> None:
    with _open_rule_expander(rule):
        _render_rule_header(rule)
        _render_rule_details(rule)
        if role == AuthRole.ADMIN:
            _render_admin_controls(rule, repository, dispatcher)
        _render_check_logs(rule, repository)


def _render_rule_header(rule: AlertRule) -> None:
    top_left, top_right = st.columns([3.8, 1.2])
    with top_left:
        st.markdown(f"**{STATUS_BADGES[rule.status]} · {rule.title}**")
        st.caption(
            f"{DETECTOR_LABELS[rule.detector_type]}  •  "
            f"Interval: **{format_interval_label(rule.check_interval_minutes)}**  •  "
            f"{TRIGGER_LABELS[rule.trigger_mode]}"
        )
    with top_right:
        if rule.last_value_text:
            st.metric("Last Value", rule.last_value_text)
        else:
            st.caption("Pending")


def _format_rule_info_html(rule: AlertRule) -> str:
    items: list[str] = []
    safe_url = sanitize_display_url(rule.target_url) if rule.target_url else "#"
    if safe_url != "#":
        short_url = rule.target_url[:50] + ("…" if len(rule.target_url) > 50 else "")
        items.append(f"🔗 <a href='{escape_html(safe_url)}' target='_blank' style='color:#60a5fa;'>{escape_html(short_url)}</a>")
    elif rule.search_query:
        items.append(f"🔍 Query: <code>{escape_html(rule.search_query)}</code>")
    if rule.threshold.target_value is not None:
        op_txt, unit_txt = escape_html(rule.threshold.operator.value), escape_html(rule.threshold.unit)
        items.append(f"🎯 Target: <b>{op_txt} {rule.threshold.target_value:g} {unit_txt}</b>")
    elif rule.trigger_mode == TriggerMode.ON_STATE_CHANGE:
        items.append("🎯 Trigger: <i>On Value Change</i>")
    return " &nbsp;•&nbsp; ".join(items)


def _render_rule_details(rule: AlertRule) -> None:
    user_text = rule.condition_description or rule.user_prompt
    if user_text:
        st.markdown(
            f"<p style='font-size:0.9rem;margin:2px 0 6px 0;color:#e2e8f0;'>{escape_html(user_text)}</p>",
            unsafe_allow_html=True,
        )
    info_html = _format_rule_info_html(rule)
    if info_html:
        st.markdown(f"<div style='font-size:0.8rem;color:#94a3b8;margin-bottom:8px;'>{info_html}</div>", unsafe_allow_html=True)

    meta1, meta2, meta3 = st.columns(3)
    meta1.caption(f"🕓 Last Check: **{format_iso_timestamp(rule.last_checked_at)}**")
    meta2.caption(f"⏭️ Next Check: **{format_iso_timestamp(rule.next_check_at)}**")
    meta3.caption(
        f":material/notifications: Triggers: **{rule.total_triggers}**",
        help="Zählt ausgelöste Alerts. Die erste Baseline-Messung dient als Referenz und löst noch keinen Alarm aus.",
    )
    if rule.last_error:
        st.warning(f"Last Error: {rule.last_error}")


def _render_admin_controls(
    rule: AlertRule,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
) -> None:
    b1, b2, b3, b4 = st.columns([1.4, 1.4, 1.5, 1])
    with b1:
        if st.button("▶️ Check Now", key=f"chk_{rule.id}", use_container_width=True):
            with st.spinner(f"Checking »{rule.title}«..."):
                outcome, msg = dispatcher.check_rule_now(rule.id, repository=repository)
            if outcome and outcome.outcome == CheckOutcome.TRIGGERED:
                st.toast(f"Alert triggered: {rule.title}", icon=":material/notifications_active:")
            else:
                st.toast(msg)
            st.rerun()

    with b2:
        is_active = rule.status == RuleStatus.ACTIVE
        toggle_label = "⏸️ Pause" if is_active else "▶️ Resume"
        target_status = RuleStatus.PAUSED if is_active else RuleStatus.ACTIVE
        if st.button(toggle_label, key=f"tgl_{rule.id}", use_container_width=True):
            repository.set_rule_status(rule.id, target_status, utc_now_iso())
            st.rerun()

    with b3:
        presets = list(INTERVAL_PRESETS_MINUTES)
        curr_idx = presets.index(rule.check_interval_minutes) if rule.check_interval_minutes in presets else 3
        new_interval = st.selectbox(
            "Interval",
            options=presets,
            index=curr_idx,
            key=f"int_{rule.id}",
            label_visibility="collapsed",
            format_func=lambda m: f"⏱️ {format_interval_label(m)}",
        )
        if new_interval != rule.check_interval_minutes:
            repository.upsert_rule(
                replace(rule, check_interval_minutes=new_interval, updated_at=utc_now_iso())
            )
            st.rerun()

    with b4:
        if st.button("🗑️ Delete", key=f"del_{rule.id}", use_container_width=True):
            repository.delete_rule(rule.id)
            st.rerun()


def _render_check_logs(rule: AlertRule, repository: SqlAlertRepository) -> None:
    if not st.toggle("📋 Check Logs", key=f"logs_toggle_{rule.id}", value=False):
        return
    logs = repository.list_check_logs(rule_id=rule.id, limit=8)
    if not logs:
        st.caption("No check logs yet.")
        return
    for log in logs:
        st.markdown(
            f"- `{format_iso_timestamp(log.created_at)}` — "
            f"**{OUTCOME_BADGES[log.outcome]}** ({log.duration_ms} ms): {log.message}"
        )
