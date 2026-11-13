"""Ansicht zum Anlegen neuer Alerts per Freitext-KI-Assistent, Schnellvorlagen oder Detailformular."""

from __future__ import annotations

import streamlit as st

from src.config.constants import INTERVAL_PRESETS_MINUTES
from src.config.settings import AppSettings
from src.detectors.llm_gateway import GeminiAlertGateway
from src.detectors.rule_compiler import AlertRuleCompiler
from src.domain.enums import (
    CheckOutcome,
    ComparisonOperator,
    DetectorType,
    TriggerMode,
    WeatherMetric,
)
from src.domain.exceptions import AlertBotError
from src.domain.schemas import CompiledAlertDraft
from src.storage.repository import SqlAlertRepository
from src.ui.formatters import (
    DETECTOR_LABELS,
    OPERATOR_LABELS,
    PAGE_NEW_ALERT,
    TRIGGER_LABELS,
    WEATHER_METRIC_LABELS,
    format_interval_label,
)
from src.worker.dispatcher import AlertWorkDispatcher

_QUICK_TEMPLATES: dict[str, str] = {
    "🌡️ Weather Alert": (
        "Beobachte die Temperatur in Freiburg und teile mir per E-Mail mit, "
        "sobald sie 22 °C erreicht oder überschreitet."
    ),
    "🏠 Apartment Search": (
        "Schaue nach neuen 3-Zimmer-Mietwohnungen in Berlin-Mahlsdorf bis 1.300 € Warmmiete "
        "und sende eine E-Mail, sobald ein neues Inserat online ist."
    ),
    "💰 Price Alert": (
        "Überwache den aktuellen Goldpreis in Euro je Feinunze im Web und melde dich, "
        "sobald er unter 2.400 € fällt."
    ),
    "📄 Page Change": (
        "Überwache https://example.com alle 15 Minuten auf inhaltliche Änderungen "
        "oder neu verfügbare Termine."
    ),
}


def _build_compiler(settings: AppSettings) -> AlertRuleCompiler:
    llm = GeminiAlertGateway(settings) if settings.has_gemini_configured else None
    return AlertRuleCompiler(llm_gateway=llm, compiler_prompt=settings.prompts.compiler_system_prompt)


def render_new_alert_view(
    settings: AppSettings,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
) -> None:
    """Rendert den Freitext-Assistenten und den transparenten Regel-Konfigurator."""
    st.subheader(PAGE_NEW_ALERT)
    st.caption(
        "Beschreibe in normaler Sprache, was überwacht werden soll – der AI-Compiler wählt "
        "automatisch den passenden Detector (Weather, Web Search, Price oder Page Change)."
    )

    _render_template_buttons()

    prompt_val = st.text_area(
        "Prompt",
        key="draft_user_prompt",
        height=95,
        placeholder=(
            "z. B. »Beobachte die Temperatur in München und melde dich sobald sie unter 0 °C fällt« "
            "oder »Schaue nach neuen Mietwohnungen in Münster bis 950 € warm, sobald eine online ist E-Mail senden«"
        ),
    )

    col_ai, col_reset = st.columns([3, 1])
    with col_ai:
        if st.button("✨ AI Auto-Config", type="secondary", use_container_width=True):
            _compile_into_session(settings, prompt_val)
    with col_reset:
        if st.button("🔄 Reset", use_container_width=True):
            st.session_state.pop("compiled_draft", None)
            st.rerun()

    st.divider()
    _render_rule_editor_form(settings, repository, dispatcher, prompt_val)


def _render_template_buttons() -> None:
    st.markdown("**Quick Templates:**")
    cols = st.columns(2)
    for idx, (label, sample_text) in enumerate(_QUICK_TEMPLATES.items()):
        with cols[idx % 2]:
            if st.button(label, key=f"tpl_{idx}", use_container_width=True):
                st.session_state["draft_user_prompt"] = sample_text
                st.rerun()


def _compile_into_session(settings: AppSettings, raw_prompt: str) -> None:
    try:
        with st.spinner("Compiling alert rule..."):
            compiler = _build_compiler(settings)
            draft = compiler.compile_from_prompt(raw_prompt, settings.email_to)
            st.session_state["compiled_draft"] = draft
            st.success(f"Draft erstellt: **{draft.title}** – Details unten prüfen & bei Bedarf anpassen.")
    except AlertBotError as exc:
        st.error(str(exc))


def _render_rule_editor_form(
    settings: AppSettings,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    prompt_val: str,
) -> None:
    draft: CompiledAlertDraft = st.session_state.get("compiled_draft") or CompiledAlertDraft(
        title="",
        detector_type=DetectorType.WEATHER_THRESHOLD.value,
        condition_description="",
        suggested_interval_minutes=settings.default_check_interval_minutes,
    )

    det_options = list(DetectorType)
    default_det = next(
        (d for d in det_options if d.value == draft.detector_type),
        DetectorType.WEATHER_THRESHOLD,
    )
    selected_det = st.selectbox(
        "Detector",
        options=det_options,
        index=det_options.index(default_det),
        format_func=lambda d: DETECTOR_LABELS[d],
    )

    with st.form("save_alert_rule_form"):
        c1, c2 = st.columns([2, 1])
        with c1:
            title = st.text_input("Alert Name", value=draft.title, placeholder="z. B. Temperatur Freiburg >= 22 °C")
        with c2:
            presets = list(INTERVAL_PRESETS_MINUTES)
            nearest = min(presets, key=lambda p: abs(p - int(draft.suggested_interval_minutes or 30)))
            interval = st.selectbox(
                "Interval",
                options=presets,
                index=presets.index(nearest),
                format_func=format_interval_label,
            )

        location_name, wx_metric, operator, target_val, unit, search_query, target_url, css_sel, json_path = (
            _render_detector_specific_inputs(selected_det, draft)
        )

        condition_desc = st.text_input(
            "Condition (AI Context & Email Summary)",
            value=draft.condition_description or prompt_val,
        )

        t1, t2 = st.columns(2)
        with t1:
            trig_options = list(TriggerMode)
            default_trig = (
                TriggerMode.EVERY_NEW_ITEM
                if selected_det == DetectorType.WEB_SEARCH and draft.trigger_mode == "on_state_change"
                else next((t for t in trig_options if t.value == draft.trigger_mode), TriggerMode.ON_STATE_CHANGE)
            )
            trigger_mode = st.selectbox(
                "Trigger Mode",
                options=trig_options,
                index=trig_options.index(default_trig),
                format_func=lambda t: TRIGGER_LABELS[t],
            )
        with t2:
            notify_email = st.text_input(
                "Notify Email (blank = default)",
                value=settings.email_to,
                placeholder="name@example.com",
            )

        notify_first = True
        if trigger_mode == TriggerMode.EVERY_NEW_ITEM:
            notify_first = st.checkbox(
                "Vorhandene Treffer beim ersten Check direkt senden (sonst erst ab neuen Items)",
                value=True,
            )

        run_immediately = st.checkbox("Run initial Live Check immediately", value=True)
        submitted = st.form_submit_button(
            "🚀 Create Alert",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        _handle_save_submission(
            settings=settings,
            repository=repository,
            dispatcher=dispatcher,
            final_draft=CompiledAlertDraft(
                title=title or prompt_val[:80],
                detector_type=selected_det.value,
                condition_description=condition_desc,
                search_query=search_query,
                target_url=target_url,
                location_name=location_name,
                weather_metric=wx_metric.value,
                operator=operator.value,
                target_value=target_val,
                unit=unit,
                css_selector=css_sel,
                json_path=json_path,
                trigger_mode=trigger_mode.value,
                suggested_interval_minutes=interval,
            ),
            user_prompt=prompt_val or condition_desc or title,
            notify_email=notify_email,
            notify_first=notify_first,
            run_immediately=run_immediately,
        )


def _render_detector_specific_inputs(
    detector: DetectorType,
    draft: CompiledAlertDraft,
) -> tuple[str, WeatherMetric, ComparisonOperator, float | None, str, str, str, str, str]:
    location_name = draft.location_name
    wx_metric = WeatherMetric.TEMPERATURE_C
    operator = next((o for o in ComparisonOperator if o.value == draft.operator), ComparisonOperator.GTE)
    target_val: float | None = draft.target_value
    unit = draft.unit
    search_query = draft.search_query
    target_url = draft.target_url
    css_sel = draft.css_selector
    json_path = draft.json_path

    if detector == DetectorType.WEATHER_THRESHOLD:
        w1, w2, w3, w4 = st.columns([2, 2, 2, 1.5])
        with w1:
            location_name = st.text_input("Location / City", value=draft.location_name or "Berlin")
        with w2:
            metrics = list(WeatherMetric)
            default_m = next((m for m in metrics if m.value == draft.weather_metric), WeatherMetric.TEMPERATURE_C)
            wx_metric = st.selectbox("Metric", metrics, index=metrics.index(default_m), format_func=lambda m: WEATHER_METRIC_LABELS[m])
        with w3:
            num_ops = [ComparisonOperator.GTE, ComparisonOperator.GT, ComparisonOperator.LTE, ComparisonOperator.LT]
            op_idx = num_ops.index(operator) if operator in num_ops else 0
            operator = st.selectbox("Operator", num_ops, index=op_idx, format_func=lambda o: OPERATOR_LABELS[o])
        with w4:
            target_val = st.number_input("Target", value=float(draft.target_value if draft.target_value is not None else 20.0), step=0.5)
            unit = "°C" if "temp" in wx_metric.value else ("km/h" if wx_metric == WeatherMetric.WIND_SPEED_KMH else "mm")

    elif detector == DetectorType.WEB_SEARCH:
        search_query = st.text_input(
            "Search Query",
            value=draft.search_query,
            placeholder="z. B. 3 Zimmer Mietwohnung Berlin Mahlsdorf bis 1300 Euro",
        )
        target_url = st.text_input(
            "Target / RSS URL (optional, leave blank for Web Search)",
            value=draft.target_url,
            placeholder="https://...",
        )

    elif detector == DetectorType.PRICE_THRESHOLD:
        p1, p2, p3 = st.columns([2, 1.5, 1])
        with p1:
            search_query = st.text_input("Asset / Product / Metric", value=draft.search_query, placeholder="z. B. Goldpreis Euro Feinunze")
        with p2:
            num_ops = [ComparisonOperator.LTE, ComparisonOperator.LT, ComparisonOperator.GTE, ComparisonOperator.GT]
            op_idx = num_ops.index(operator) if operator in num_ops else 0
            operator = st.selectbox("Operator", num_ops, index=op_idx, format_func=lambda o: OPERATOR_LABELS[o])
        with p3:
            target_val = st.number_input("Threshold", value=float(draft.target_value if draft.target_value is not None else 100.0), step=1.0)
        u1, u2, u3 = st.columns([2, 1, 1])
        with u1:
            target_url = st.text_input("Product URL (optional)", value=draft.target_url, placeholder="https://...")
        with u2:
            css_sel = st.text_input("CSS Selector (optional)", value=draft.css_selector, placeholder=".price")
        with u3:
            unit = st.text_input("Unit", value=draft.unit or "€")

    elif detector == DetectorType.PAGE_CHANGE:
        target_url = st.text_input("Target URL", value=draft.target_url, placeholder="https://...")
        css_sel = st.text_input("CSS Selector (optional)", value=draft.css_selector)

    elif detector == DetectorType.HTTP_API:
        target_url = st.text_input("API Endpoint URL", value=draft.target_url, placeholder="https://api.example.com/health")
        json_path = st.text_input("JSON Path (optional, e.g. data.status)", value=draft.json_path)

    return location_name, wx_metric, operator, target_val, unit, search_query, target_url, css_sel, json_path


def _handle_save_submission(
    settings: AppSettings,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    final_draft: CompiledAlertDraft,
    user_prompt: str,
    notify_email: str,
    notify_first: bool,
    run_immediately: bool,
) -> None:
    try:
        rule = AlertRuleCompiler.build_validated_rule(
            draft=final_draft,
            user_prompt=user_prompt,
            notify_email=notify_email,
            notify_on_first_match=notify_first,
            cooldown_minutes=settings.default_cooldown_minutes,
        )
        repository.upsert_rule(rule)
        st.session_state.pop("compiled_draft", None)
        st.success(f"Alert **{rule.title}** saved!")

        if run_immediately:
            with st.spinner("Running initial Live Check..."):
                outcome, msg = dispatcher.check_rule_now(rule.id, repository=repository)
            if outcome and outcome.outcome == CheckOutcome.TRIGGERED:
                st.balloons()
                st.success(
                    f"Instant Match! {outcome.finding.summary if outcome.finding else msg}",
                    icon=":material/notifications_active:",
                )
            elif outcome and outcome.finding:
                st.info(f"Initial Check finished: {outcome.finding.summary}")
            elif outcome and outcome.error_message:
                st.warning(f"Alert saved, initial Check reported: {outcome.error_message}")
    except AlertBotError as exc:
        st.error(f"Failed to save rule: {exc}")
