"""Übersicht aller konfigurierten Alerts als kompakte, standardmäßig eingeklappte Boxen."""

from __future__ import annotations

import inspect
from dataclasses import replace

import streamlit as st

from src.config.constants import INTERVAL_PRESETS_MINUTES
from src.detectors.rule_compiler import utc_now_iso
from src.domain.enums import AuthRole, CheckOutcome, RuleStatus
from src.domain.models import AlertRule, CheckLogEntry
from src.security.sanitization import escape_html
from src.storage.repository import SqlAlertRepository
from src.ui.formatters import (
    DETECTOR_LABELS,
    OUTCOME_BADGES,
    PAGE_NEW_ALERT,
    STATUS_BADGES,
    TRIGGER_LABELS,
    build_kpi_grid_html,
    build_rule_info_html,
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
    """Zeigt kompakte KPI-Kacheln und eingeklappte Alert-Boxen für alle Regeln."""
    top_col, action_col = st.columns([3.5, 1.2], vertical_alignment="center")
    with top_col:
        st.subheader("📡 Alerts")
    with action_col:
        if st.button(PAGE_NEW_ALERT, key="top_add_alert_btn", use_container_width=True):
            st.session_state["active_page"] = PAGE_NEW_ALERT
            st.rerun()

    rules = repository.list_rules()
    events = repository.list_events(limit=20)
    kpi_html = build_kpi_grid_html(
        active_count=sum(1 for r in rules if r.status == RuleStatus.ACTIVE),
        total_count=len(rules),
        total_hits=sum(r.total_triggers for r in rules),
        recent_events_count=len(events),
    )
    st.markdown(kpi_html, unsafe_allow_html=True)

    st.divider()
    if not rules:
        st.info(
            "Noch keine Alerts angelegt. Klicke oben auf **➕ New Alert**, "
            "um z. B. einen Weather-, Price- oder Web-Search-Alert zu starten."
        )
        return

    logs_by_rule = repository.list_recent_check_logs_by_rule(per_rule_limit=8)
    for rule in rules:
        _render_single_rule_card(rule, logs_by_rule.get(rule.id, []), repository, dispatcher, role)


def _build_expander_label(rule: AlertRule) -> str:
    icon = _STATUS_ICONS[rule.status]
    interval = format_interval_label(rule.check_interval_minutes)
    warn_suffix = " · ⚠️" if rule.last_error and rule.status != RuleStatus.ERROR else ""
    if rule.last_value_text:
        val = rule.last_value_text.strip().replace("`", "'")
        short_val = val[:28] + ("…" if len(val) > 28 else "")
        return f"{icon} **{rule.title}** · `{short_val}` · ⏱️ {interval}{warn_suffix}"
    return f"{icon} **{rule.title}** · ⏱️ {interval}{warn_suffix}"


def _open_expander(label: str, key: str, expanded: bool = False):
    if _EXPANDER_SUPPORTS_KEY:
        return st.expander(label, expanded=expanded, key=key)
    return st.expander(label, expanded=expanded)


def _render_single_rule_card(
    rule: AlertRule,
    logs: list[CheckLogEntry],
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    role: AuthRole,
) -> None:
    is_confirming_delete = st.session_state.get("confirm_delete_rule_id") == rule.id
    with _open_expander(_build_expander_label(rule), f"alert_box_{rule.id}", is_confirming_delete):
        _render_rule_header(rule)
        _render_rule_details(rule)
        if role == AuthRole.ADMIN:
            _render_admin_controls(rule, repository, dispatcher, is_confirming_delete)
        _render_check_logs(rule.id, logs)


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


def _render_rule_details(rule: AlertRule) -> None:
    user_text = rule.condition_description or rule.user_prompt
    if user_text:
        st.markdown(
            f"<p style='font-size:0.9rem;margin:2px 0 6px 0;color:#e2e8f0;'>{escape_html(user_text)}</p>",
            unsafe_allow_html=True,
        )
    info_html = build_rule_info_html(rule)
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
    is_confirming_delete: bool,
) -> None:
    b1, b2, b3, b4 = st.columns([1.4, 1.4, 1.5, 1])
    with b1:
        if st.button("▶️ Check Now", key=f"chk_{rule.id}", use_container_width=True):
            _trigger_manual_check(rule, repository, dispatcher)
    with b2:
        is_active = rule.status == RuleStatus.ACTIVE
        label = "⏸️ Pause" if is_active else "▶️ Resume"
        next_status = RuleStatus.PAUSED if is_active else RuleStatus.ACTIVE
        if st.button(label, key=f"tgl_{rule.id}", use_container_width=True):
            repository.set_rule_status(rule.id, next_status, utc_now_iso())
            st.rerun()
    with b3:
        _render_interval_selector(rule, repository)
    with b4:
        if st.button("🗑️ Delete", key=f"del_{rule.id}", disabled=is_confirming_delete, use_container_width=True):
            st.session_state["confirm_delete_rule_id"] = rule.id
            st.session_state.pop(f"confirm_del_chk_{rule.id}", None)
            st.rerun()

    if is_confirming_delete:
        _render_delete_confirmation(rule, repository)


def _trigger_manual_check(
    rule: AlertRule,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
) -> None:
    with st.spinner(f"Checking »{rule.title}«..."):
        outcome, msg = dispatcher.check_rule_now(rule.id, repository=repository)
    if outcome and outcome.outcome == CheckOutcome.TRIGGERED:
        st.toast(f"Alert triggered: {rule.title}", icon=":material/notifications_active:")
    else:
        st.toast(msg)
    st.rerun()


def _render_interval_selector(rule: AlertRule, repository: SqlAlertRepository) -> None:
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
        repository.upsert_rule(replace(rule, check_interval_minutes=new_interval, updated_at=utc_now_iso()))
        st.rerun()


def _render_delete_confirmation(rule: AlertRule, repository: SqlAlertRepository) -> None:
    with st.container(border=True):
        st.warning(
            f"⚠️ Permanently delete **{rule.title}**? "
            "All check logs and seen items for this alert will be removed."
        )
        confirmed = st.checkbox("Yes, permanently delete this alert", key=f"confirm_del_chk_{rule.id}")
        c_cancel, c_confirm = st.columns(2)
        with c_cancel:
            if st.button("✖️ Cancel", key=f"cancel_del_{rule.id}", use_container_width=True):
                st.session_state.pop("confirm_delete_rule_id", None)
                st.session_state.pop(f"confirm_del_chk_{rule.id}", None)
                st.rerun()
        with c_confirm:
            if st.button(
                "🗑️ Delete Permanently",
                key=f"confirm_del_{rule.id}",
                type="primary",
                disabled=not confirmed,
                use_container_width=True,
            ):
                st.session_state.pop("confirm_delete_rule_id", None)
                st.session_state.pop(f"confirm_del_chk_{rule.id}", None)
                repository.delete_rule(rule.id)
                st.toast(f"Alert deleted: {rule.title}")
                st.rerun()


def _render_check_logs(rule_id: str, logs: list[CheckLogEntry]) -> None:
    with _open_expander("📋 Check Logs", key=f"alert_logs_{rule_id}"):
        if not logs:
            st.caption("No check logs yet.")
            return
        for log in logs:
            st.markdown(
                f"- `{format_iso_timestamp(log.created_at)}` — "
                f"**{OUTCOME_BADGES[log.outcome]}** ({log.duration_ms} ms): {log.message}"
            )
