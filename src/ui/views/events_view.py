"""Chronologisches Treffer-Postfach aller ausgelösten Alarme inkl. Angebotslinks & Export."""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from src.detectors.rule_compiler import utc_now_iso
from src.domain.enums import AuthRole, RuleStatus, TriggerMode
from src.domain.models import AlertEvent
from src.security.sanitization import sanitize_display_url, strip_control_chars
from src.storage.repository import SqlAlertRepository
from src.ui.auth_view import go_to_start_page, switch_to_admin_login
from src.ui.formatters import DETECTOR_LABELS, PAGE_ALERT_HISTORY, format_iso_timestamp


def render_events_view(
    repository: SqlAlertRepository,
    role: AuthRole = AuthRole.ADMIN,
) -> None:
    """Zeigt alle ausgelösten Alarme und deren gefundene Einzel-Angebote an."""
    st.subheader(PAGE_ALERT_HISTORY)

    if role == AuthRole.READONLY:
        _render_readonly_action_bar()

    focused_id = str(st.query_params.get("event_id", "")).strip()
    if focused_id:
        focused = repository.get_event(focused_id)
        if focused is not None:
            st.info("Direct view from email notification:")
            _render_event_card(focused, expanded=True, repository=repository, role=role, scope="focus")
            st.divider()

    events = repository.list_events(limit=50)
    if not events:
        st.info("Noch keine Alerts ausgelöst. Sobald eine Bedingung eintritt, erscheinen alle Events hier.")
        return

    md_export = _build_markdown_export(events)
    st.download_button(
        "📥 Export Markdown",
        data=md_export.encode("utf-8"),
        file_name="alert_history.md",
        mime="text/markdown",
    )

    for ev in events:
        _render_event_card(ev, expanded=False, repository=repository, role=role, scope="list")


def _render_readonly_action_bar() -> None:
    with st.container(border=True):
        c_info, c_login, c_home = st.columns([2.4, 1.3, 1.3], vertical_alignment="center")
        with c_info:
            st.caption("🔓 **Read-Only View (Email Link)** — Sign in as Admin to edit rules or rectify false alerts.")
        with c_login:
            if st.button("🔐 Admin Login", key="ro_main_admin_login_btn", use_container_width=True):
                switch_to_admin_login()
                st.rerun()
        with c_home:
            if st.button("🏠 Go to Start Page", key="ro_main_home_btn", use_container_width=True):
                go_to_start_page()
                st.rerun()


def _render_event_card(
    event: AlertEvent,
    expanded: bool,
    repository: SqlAlertRepository,
    role: AuthRole,
    scope: str,
) -> None:
    key_prefix = f"{scope}_{event.id}"
    is_rectifying = role == AuthRole.ADMIN and st.session_state.get("rectify_event_key") == key_prefix

    with st.container(border=True):
        c1, c2 = st.columns([3.5, 1.5])
        with c1:
            st.markdown(f"### :material/notifications_active: {event.headline}")
            st.caption(
                f"{format_iso_timestamp(event.created_at)}  •  "
                f"{DETECTOR_LABELS[event.detector_type]}"
            )
        with c2:
            mail_state = "📧 Email Sent" if event.notification_succeeded else "💾 Dashboard Only"
            st.caption(mail_state)
            if event.measured_display:
                st.markdown(f"**`{event.measured_display}`**")

        st.write(event.summary)

        safe_src = sanitize_display_url(event.source_url)
        if role == AuthRole.ADMIN:
            if safe_src != "#":
                a1, a2 = st.columns([1.4, 1.6])
                with a1:
                    st.link_button("🔗 Open Source", safe_src, use_container_width=True)
                with a2:
                    if st.button(
                        "🗑️ Delete / Rectify",
                        key=f"rectify_btn_{key_prefix}",
                        disabled=is_rectifying,
                        use_container_width=True,
                    ):
                        st.session_state["rectify_event_key"] = key_prefix
                        st.rerun()
            elif st.button(
                "🗑️ Delete / Rectify",
                key=f"rectify_btn_{key_prefix}",
                disabled=is_rectifying,
            ):
                st.session_state["rectify_event_key"] = key_prefix
                st.rerun()
        elif safe_src != "#":
            st.link_button("🔗 Open Source", safe_src)

        if is_rectifying:
            _render_rectify_confirmation(event, key_prefix, repository)

        if event.items:
            with st.expander(f"📋 {len(event.items)} Matched Items", expanded=expanded):
                for item in event.items:
                    price_suffix = f" — **{item.price_or_value}**" if item.price_or_value else ""
                    st.markdown(f"**{item.title}**{price_suffix}")
                    if item.summary:
                        st.caption(item.summary)
                    safe_item_url = sanitize_display_url(item.url)
                    if safe_item_url != "#":
                        st.markdown(f"[Open Item]({safe_item_url})")
                    st.divider()


def _render_rectify_confirmation(
    event: AlertEvent,
    key_prefix: str,
    repository: SqlAlertRepository,
) -> None:
    with st.container(border=True):
        st.warning("Remove this event from Alert History and optionally reset/correct the rule's saved state.")
        corrected_val = st.text_input(
            "Corrected Last Value for rule (blank = clear false reading)",
            key=f"corr_val_{key_prefix}",
            placeholder="e.g. 2,18 % (leave blank to clear)",
        )
        reset_rule = st.checkbox(
            "Reset rule trigger state & decrement trigger count",
            value=True,
            key=f"reset_rule_{key_prefix}",
        )
        c_cancel, c_confirm = st.columns(2)
        with c_cancel:
            if st.button("✖️ Cancel", key=f"cancel_rectify_{key_prefix}", use_container_width=True):
                st.session_state.pop("rectify_event_key", None)
                st.rerun()
        with c_confirm:
            if st.button(
                "🗑️ Delete Event",
                key=f"confirm_rectify_{key_prefix}",
                type="primary",
                use_container_width=True,
            ):
                _apply_event_rectification(event, corrected_val, reset_rule, repository)
                st.session_state.pop("rectify_event_key", None)
                if st.query_params.get("event_id") == event.id:
                    del st.query_params["event_id"]
                st.toast(f"Event deleted: {event.rule_title}")
                st.rerun()


def _apply_event_rectification(
    event: AlertEvent,
    corrected_val: str,
    reset_rule: bool,
    repository: SqlAlertRepository,
) -> None:
    repository.delete_event(event.id)
    if not reset_rule:
        return
    rule = repository.get_rule(event.rule_id)
    if rule is None:
        return

    clean_corr = strip_control_chars(corrected_val)[:100]
    if clean_corr:
        next_val_text = clean_corr
    elif rule.last_value_text == event.measured_display:
        next_val_text = ""
    else:
        next_val_text = rule.last_value_text

    next_status = (
        RuleStatus.ACTIVE
        if rule.status == RuleStatus.PAUSED and rule.trigger_mode == TriggerMode.ONCE
        else rule.status
    )
    updated_rule = replace(
        rule,
        status=next_status,
        last_value_text=next_val_text,
        last_condition_met=False,
        last_triggered_at="",
        last_error="",
        consecutive_failures=0,
        total_triggers=max(0, rule.total_triggers - 1),
        updated_at=utc_now_iso(),
    )
    repository.upsert_rule(updated_rule)


def _build_markdown_export(events: list[AlertEvent]) -> str:
    lines = ["# Alert-Bot History", ""]
    for ev in events:
        lines.append(f"## {ev.headline} ({format_iso_timestamp(ev.created_at)})")
        lines.append(ev.summary)
        if ev.source_url:
            lines.append(f"- Source: {ev.source_url}")
        for item in ev.items:
            lines.append(f"  - **{item.title}** ({item.price_or_value}): {item.url}")
        lines.append("")
    return "\n".join(lines)
