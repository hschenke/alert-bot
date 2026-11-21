"""Inline-Bearbeitungs- & Berichtigungsformular für bestehende Alert-Regeln."""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from src.detectors.rule_compiler import utc_now_iso
from src.domain.enums import (
    CheckOutcome,
    ComparisonOperator,
    DetectorType,
    RuleStatus,
    TriggerMode,
    WeatherMetric,
)
from src.domain.exceptions import AlertBotError
from src.domain.models import AlertRule, ThresholdSpec
from src.security.sanitization import (
    strip_control_chars,
    validate_css_selector,
    validate_json_path,
    validate_optional_email,
    validate_rule_title,
    validate_search_query,
)
from src.security.ssrf_guard import validate_safe_external_url
from src.storage.repository import SqlAlertRepository
from src.ui.formatters import OPERATOR_LABELS, TRIGGER_LABELS, WEATHER_METRIC_LABELS
from src.worker.dispatcher import AlertWorkDispatcher


def render_rule_editor_panel(
    rule: AlertRule,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
) -> None:
    """Rendert das Editier- & State-Berichtigungs-Panel für eine ausgewählte Regel."""
    kp = f"ed_{rule.id}"
    with st.container(border=True):
        st.markdown("#### ✏️ Edit Alert & Rectify Value")

        title = st.text_input("Alert Name", value=rule.title, key=f"{kp}_title")
        search_query, target_url, threshold = _render_detector_fields(rule, kp)

        condition_desc = st.text_input(
            "Condition / Description",
            value=rule.condition_description or rule.user_prompt,
            key=f"{kp}_cond",
        )

        m1, m2, m3 = st.columns(3)
        with m1:
            trig_options = list(TriggerMode)
            trigger_mode = st.selectbox(
                "Trigger Mode",
                options=trig_options,
                index=trig_options.index(rule.trigger_mode),
                format_func=lambda t: TRIGGER_LABELS[t],
                key=f"{kp}_trig",
            )
        with m2:
            cooldown_minutes = int(
                st.number_input(
                    "Cooldown (min)",
                    min_value=0,
                    max_value=10080,
                    value=int(rule.cooldown_minutes),
                    step=15,
                    key=f"{kp}_cd",
                )
            )
        with m3:
            notify_email = st.text_input(
                "Notify Email (blank = default)",
                value=rule.notify_email,
                key=f"{kp}_email",
            )

        st.divider()
        st.caption("🔧 **Rectify Saved Value / State** (fix bogus readings or reset trigger flags)")
        r1, r2 = st.columns([2, 2])
        with r1:
            last_value_text = st.text_input(
                "Last Value (edit or clear)",
                value=rule.last_value_text,
                key=f"{kp}_lastval",
                placeholder="e.g. 2,18 % (blank to clear)",
            )
        with r2:
            reset_state = st.checkbox(
                "Reset trigger state, cooldown & errors",
                value=False,
                key=f"{kp}_reset_state",
            )
            reset_baseline = st.checkbox(
                "Reset baseline / seen items",
                value=False,
                key=f"{kp}_reset_base",
            )

        c_cancel, c_save, c_save_run = st.columns(3)
        with c_cancel:
            if st.button("✖️ Cancel", key=f"{kp}_cancel", use_container_width=True):
                st.session_state.pop("edit_rule_id", None)
                st.rerun()
        with c_save:
            if st.button("💾 Save Changes", key=f"{kp}_save", use_container_width=True):
                _save_edits(
                    rule, title, search_query, target_url, threshold,
                    condition_desc, trigger_mode, cooldown_minutes, notify_email,
                    last_value_text, reset_state, reset_baseline,
                    repository, dispatcher, run_now=False,
                )
        with c_save_run:
            if st.button("▶️ Save & Check Now", key=f"{kp}_save_run", use_container_width=True):
                _save_edits(
                    rule, title, search_query, target_url, threshold,
                    condition_desc, trigger_mode, cooldown_minutes, notify_email,
                    last_value_text, reset_state, reset_baseline,
                    repository, dispatcher, run_now=True,
                )


def _render_detector_fields(rule: AlertRule, kp: str) -> tuple[str, str, ThresholdSpec]:
    det = rule.detector_type
    spec = rule.threshold
    search_query = rule.search_query
    target_url = rule.target_url
    location_name = spec.location_name
    wx_metric = spec.weather_metric
    operator = spec.operator
    target_val = spec.target_value
    target_text = spec.target_text
    unit = spec.unit
    css_selector = spec.css_selector
    json_path = spec.json_path

    if det == DetectorType.PRICE_THRESHOLD:
        p1, p2, p3 = st.columns([2, 1.4, 1.2])
        with p1:
            search_query = st.text_input("Asset / Metric Query", value=rule.search_query, key=f"{kp}_q")
        with p2:
            use_threshold = st.checkbox(
                "Use numeric threshold",
                value=spec.target_value is not None,
                key=f"{kp}_use_thresh",
            )
        with p3:
            unit = st.text_input("Unit", value=spec.unit, key=f"{kp}_unit")

        if use_threshold:
            t1, t2 = st.columns(2)
            with t1:
                num_ops = [
                    ComparisonOperator.LTE,
                    ComparisonOperator.LT,
                    ComparisonOperator.GTE,
                    ComparisonOperator.GT,
                    ComparisonOperator.EQ,
                    ComparisonOperator.NEQ,
                ]
                op_idx = num_ops.index(spec.operator) if spec.operator in num_ops else 0
                operator = st.selectbox("Operator", num_ops, index=op_idx, format_func=lambda o: OPERATOR_LABELS[o], key=f"{kp}_op")
            with t2:
                target_val = float(
                    st.number_input("Threshold", value=float(spec.target_value if spec.target_value is not None else 0.0), key=f"{kp}_tval")
                )
        else:
            target_val = None

        u1, u2 = st.columns([2.2, 1])
        with u1:
            target_url = st.text_input("Direct Source URL (recommended if known)", value=rule.target_url, key=f"{kp}_url", placeholder="https://...")
        with u2:
            css_selector = st.text_input("CSS Selector (optional)", value=spec.css_selector, key=f"{kp}_css")

    elif det == DetectorType.WEB_SEARCH:
        search_query = st.text_input("Search Query", value=rule.search_query, key=f"{kp}_q")
        target_url = st.text_input("Target / RSS URL (optional)", value=rule.target_url, key=f"{kp}_url")

    elif det == DetectorType.WEATHER_THRESHOLD:
        w1, w2, w3, w4 = st.columns([1.8, 1.8, 1.6, 1.2])
        with w1:
            location_name = st.text_input("Location / City", value=spec.location_name, key=f"{kp}_loc")
        with w2:
            metrics = list(WeatherMetric)
            wx_metric = st.selectbox("Metric", metrics, index=metrics.index(spec.weather_metric), format_func=lambda m: WEATHER_METRIC_LABELS[m], key=f"{kp}_wxm")
        with w3:
            num_ops = [ComparisonOperator.GTE, ComparisonOperator.GT, ComparisonOperator.LTE, ComparisonOperator.LT]
            op_idx = num_ops.index(spec.operator) if spec.operator in num_ops else 0
            operator = st.selectbox("Operator", num_ops, index=op_idx, format_func=lambda o: OPERATOR_LABELS[o], key=f"{kp}_op")
        with w4:
            target_val = float(
                st.number_input(
                    "Target",
                    value=float(spec.target_value if spec.target_value is not None else 0.0),
                    step=0.5,
                    key=f"{kp}_tval",
                )
            )

    elif det == DetectorType.PAGE_CHANGE:
        u1, u2 = st.columns([2.2, 1])
        with u1:
            target_url = st.text_input("Target URL", value=rule.target_url, key=f"{kp}_url")
        with u2:
            css_selector = st.text_input("CSS Selector (optional)", value=spec.css_selector, key=f"{kp}_css")
        target_text = st.text_input("Required Text Phrase (optional)", value=spec.target_text, key=f"{kp}_ttxt")

    elif det == DetectorType.HTTP_API:
        u1, u2 = st.columns([2.2, 1.2])
        with u1:
            target_url = st.text_input("API Endpoint URL", value=rule.target_url, key=f"{kp}_url")
        with u2:
            json_path = st.text_input("JSON Path (optional)", value=spec.json_path, key=f"{kp}_jpath")

    updated_spec = replace(
        spec,
        location_name=strip_control_chars(location_name)[:100],
        weather_metric=wx_metric,
        operator=operator,
        target_value=target_val,
        target_text=strip_control_chars(target_text)[:200],
        unit=strip_control_chars(unit)[:20],
        css_selector=css_selector,
        json_path=json_path,
    )
    return search_query, target_url, updated_spec


def _save_edits(
    rule: AlertRule,
    title: str,
    search_query: str,
    target_url: str,
    threshold: ThresholdSpec,
    condition_desc: str,
    trigger_mode: TriggerMode,
    cooldown_minutes: int,
    notify_email: str,
    last_value_text: str,
    reset_state: bool,
    reset_baseline: bool,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    run_now: bool,
) -> None:
    try:
        clean_title = validate_rule_title(title)
        clean_query = validate_search_query(search_query)
        clean_url = validate_safe_external_url(target_url) if target_url.strip() else ""
        if rule.detector_type in {DetectorType.PAGE_CHANGE, DetectorType.HTTP_API} and not clean_url:
            st.error("Target URL is required for this detector.")
            return
        clean_email = validate_optional_email(notify_email)
        clean_spec = replace(
            threshold,
            css_selector=validate_css_selector(threshold.css_selector),
            json_path=validate_json_path(threshold.json_path),
        )
        clean_last_val = strip_control_chars(last_value_text)[:120]
        next_status = RuleStatus.ACTIVE if reset_state and rule.status == RuleStatus.ERROR else rule.status

        updated = replace(
            rule,
            title=clean_title,
            search_query=clean_query,
            target_url=clean_url,
            condition_description=strip_control_chars(condition_desc)[:500],
            threshold=clean_spec,
            trigger_mode=trigger_mode,
            cooldown_minutes=max(0, cooldown_minutes),
            notify_email=clean_email,
            status=next_status,
            last_value_text=clean_last_val,
            last_condition_met=False if reset_state else rule.last_condition_met,
            last_triggered_at="" if reset_state else rule.last_triggered_at,
            last_error="" if reset_state else rule.last_error,
            consecutive_failures=0 if reset_state else rule.consecutive_failures,
            last_content_hash="" if reset_baseline else rule.last_content_hash,
            baseline_seeded=False if reset_baseline else rule.baseline_seeded,
            updated_at=utc_now_iso(),
        )
        repository.upsert_rule(updated)
        if reset_baseline:
            repository.clear_seen_items(rule.id)

        st.session_state.pop("edit_rule_id", None)
        if run_now:
            with st.spinner(f"Checking »{clean_title}«..."):
                outcome, msg = dispatcher.check_rule_now(rule.id, repository=repository)
            if outcome and outcome.outcome == CheckOutcome.TRIGGERED:
                st.toast(f"Alert triggered: {clean_title}", icon=":material/notifications_active:")
            else:
                st.toast(msg)
        else:
            st.toast(f"Saved changes: {clean_title}")
        st.rerun()
    except AlertBotError as exc:
        st.error(str(exc))
