"""Systemstatus, AI-Brain Optionen, E-Mail-Funktionstest, Cloud-Takt-Hilfe & Kanal-Roadmap."""

from __future__ import annotations

import ai_brain
import streamlit as st
from ai_brain import AppBrainPolicy, BrainError, BrainTier, default_app_policy

from src.config.settings import AppSettings
from src.domain.enums import AuthRole
from src.notifications.email_channel import EmailNotificationChannel
from src.storage.repository import SqlAlertRepository
from src.worker.dispatcher import AlertWorkDispatcher

_TIER_OPTIONS: tuple[BrainTier, ...] = (BrainTier.FAST, BrainTier.STANDARD, BrainTier.DEEP)
_TIER_LABELS: dict[BrainTier, str] = {
    BrainTier.FAST: "Fast",
    BrainTier.STANDARD: "Standard",
    BrainTier.DEEP: "Deep",
}
_MAPS_OPTIONS: tuple[str, ...] = ("auto", "always", "off")
_MAPS_LABELS: dict[str, str] = {"auto": "Auto", "always": "Always", "off": "Off"}


def _load_ai_brain_policy() -> tuple[AppBrainPolicy, str | None]:
    """Lädt die in Turso gespeicherte `AppBrainPolicy` oder fällt sicher auf Free-Only zurück."""
    try:
        with ai_brain.connect() as brain:
            return brain.get_app_policy(), None
    except BrainError as exc:
        return default_app_policy("alert-bot"), str(exc)


def _save_ai_brain_policy(policy: AppBrainPolicy) -> None:
    """Speichert `AppBrainPolicy` über `ai_brain` in Turso und zeigt vollbreites Feedback."""
    try:
        with ai_brain.connect() as brain:
            saved = brain.set_app_policy(policy)
    except BrainError as exc:
        st.error(f"AI-Brain Optionen konnten nicht gespeichert werden: {exc}")
        return
    budget_label = "Paid" if saved.allow_paid else "Free"
    st.success(
        f"AI-Brain Optionen gespeichert: AI-Analyse {_TIER_LABELS[saved.llm_tier]} · "
        f"Websearch {_TIER_LABELS[saved.search_tier]} · "
        f"Maps {_MAPS_LABELS[saved.maps_mode]} · Budget {budget_label}"
    )


def _render_policy_form(policy: AppBrainPolicy) -> AppBrainPolicy | None:
    """Rendert die kompakten Admin-Steuerelemente für Tiers, Maps Grounding & Budget."""
    llm_idx = _TIER_OPTIONS.index(policy.llm_tier) if policy.llm_tier in _TIER_OPTIONS else 0
    search_idx = _TIER_OPTIONS.index(policy.search_tier) if policy.search_tier in _TIER_OPTIONS else 0
    maps_idx = _MAPS_OPTIONS.index(policy.maps_mode) if policy.maps_mode in _MAPS_OPTIONS else 0

    with st.form("ai_brain_policy_form", border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            llm_tier = st.selectbox(
                "AI-Analyse Tier", list(_TIER_OPTIONS), index=llm_idx,
                format_func=lambda t: _TIER_LABELS[t], key="ai_brain_llm_tier",
            )
        with c2:
            search_tier = st.selectbox(
                "Websearch Tier", list(_TIER_OPTIONS), index=search_idx,
                format_func=lambda t: _TIER_LABELS[t], key="ai_brain_search_tier",
            )
        with c3:
            maps_mode = st.selectbox(
                "Maps Grounding", list(_MAPS_OPTIONS), index=maps_idx,
                format_func=lambda m: _MAPS_LABELS[m], key="ai_brain_maps_mode",
            )
        allow_paid = st.checkbox(
            "💳 Paid-Modelle bei Bedarf erlauben (allow_paid)",
            value=bool(policy.allow_paid),
            key="ai_brain_allow_paid",
        )
        st.caption(
            "Ohne Haken sind strikt **0 % Kosten (Free-Only)** garantiert – "
            "es werden ausschließlich kostenfreie Modelle & Tools genutzt."
        )
        submitted = st.form_submit_button("💾 Save AI-Brain Options", key="ai_brain_save_btn")

    if not submitted:
        return None
    return AppBrainPolicy(
        llm_tier=llm_tier,
        search_tier=search_tier,
        maps_mode=maps_mode,
        allow_paid=allow_paid,
    )


def _render_ai_brain_options(role: AuthRole) -> None:
    """Rendert Status (`🟢 Online` / Offline) und Admin-Policy-Optionen für `ai_brain`."""
    st.markdown("### 🧠 AI-Brain Optionen")
    if not ai_brain.is_configured():
        st.caption("Status: ⚪ Offline")
        st.info(
            "`AI_BRAIN_KEY` fehlt – hinterlege das Secret in `.env` bzw. Streamlit Secrets, "
            "um das zentrale AI-Brain (`Fast` / `Standard` / `Deep`, Maps Grounding & Policy-Sync) zu aktivieren."
        )
        return

    policy, load_err = _load_ai_brain_policy()
    st.caption("Status: 🟢 Online · Policy wird zentral in Turso synchronisiert (Streamlit UI & GitHub Actions).")
    if load_err:
        st.warning(f"AI-Brain Policy konnte nicht geladen werden ({load_err}) – zeige Free-Only-Defaults.")

    if role != AuthRole.ADMIN:
        budget = "Paid" if policy.allow_paid else "Free"
        st.caption(
            f"Read-Only Policy: AI-Analyse **{_TIER_LABELS[policy.llm_tier]}** · "
            f"Websearch **{_TIER_LABELS[policy.search_tier]}** · "
            f"Maps **{_MAPS_LABELS[policy.maps_mode]}** · Budget **{budget}**"
        )
        return

    updated_policy = _render_policy_form(policy)
    if updated_policy is not None:
        _save_ai_brain_policy(updated_policy)


def _render_infrastructure_guides(settings: AppSettings) -> None:
    """Zeigt Cron-Dispatch-Anleitung, Streamlit Keep-Alive-Hilfe und Kanal-Roadmap."""
    st.divider()
    st.markdown("### ⏱️ Cron & High-Frequency Polling (1–5m via GitHub Actions, 0 €)")
    st.markdown(
        "Standardmäßig prüft `.github/workflows/check_alerts.yaml` kostenlos über GitHub Actions alle **15 Minuten** "
        "sämtliche fälligen Alerts. Für ein **punktgenaues 1–5m-Interval** ohne "
        "GitHub-Cron-Delay lässt sich der Workflow kostenlos über **[cron-job.org](https://cron-job.org)** triggern:"
    )
    st.code(
        f"POST https://api.github.com/repos/{settings.github_repo}/actions/workflows/{settings.github_workflow_file}/dispatches\n"
        "Headers:\n"
        "  Accept: application/vnd.github+json\n"
        "  Authorization: Bearer <YOUR_FINE_GRAINED_GITHUB_TOKEN>\n"
        'Body (JSON):\n  {"ref": "main", "inputs": {"rule_id": "", "force": "false"}}',
        language="http",
    )

    st.divider()
    st.markdown("### ☕ Streamlit Cloud Keep-Alive (24/7 Uptime)")
    st.info(
        "**Warum schläft Streamlit Cloud ein?**\n\n"
        "Streamlit Community Cloud versetzt Apps nach Inaktivität in den Sleep-Mode (\"Zzz\"). "
        "Dadurch pausiert auch der integrierte Background-Worker.\n\n"
        "**Dauerhafte 24/7-Lösung (kostenlos in 1 Minute eingerichtet):**\n"
        "1. In Streamlit Cloud (`share.streamlit.io`) die Sichtbarkeit auf **\"Public\"** stellen "
        "(sicher durch eigene Passwort-Auth) ODER in cron-job.org unter *Advanced* Redirects erlauben.\n"
        "2. Auf **[cron-job.org](https://cron-job.org)** einen Job anlegen, der die Streamlit-URL alle **5–10m per HTTP GET** pingt.\n"
        "3. **Ergebnis:** Die App bleibt dauerhaft wach und führt alle geplanten Checks zuverlässig aus!\n\n"
        "📱 **Mobile App Shortcut (iOS & Android):** Nach dem Login im Browser **\"Zum Home-Bildschirm hinzufügen\"** "
        "(iOS Safari: Share → Add to Home Screen; Android Chrome: Menu → Install App) wählen – "
        "Alert-Bot startet dann wie eine native App und bleibt angemeldet!"
    )

    st.divider()
    st.markdown("### 🗺️ Notification Channels Roadmap")
    st.caption(
        "Der `NotificationRouter` ist bereits modular aufgebaut. "
        "Sobald deine Email-Alerts rund laufen, lassen sich folgende 0-€-Channels jederzeit ergänzen:"
    )
    r1, r2, r3 = st.columns(3)
    with r1, st.container(border=True):
        st.markdown("#### 📱 ntfy.sh Push")
        st.caption("Instant Push auf iOS & Android mit Priority Levels – komplett ohne Account.")
    with r2, st.container(border=True):
        st.markdown("#### 💬 Telegram Bot")
        st.caption("Realtime Message aufs Smartphone inkl. direkter Deep-Links.")
    with r3, st.container(border=True):
        st.markdown("#### 🔗 Webhook / Discord / Slack")
        st.caption("Generischer JSON-POST für Discord, Slack, MS Teams oder Home Assistant.")


def render_settings_view(
    settings: AppSettings,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
    role: AuthRole = AuthRole.ADMIN,
) -> None:
    """Zeigt Infrastruktur-Status, AI-Brain Optionen, Testmail-Versand, Takt-Anleitung und Roadmap."""
    st.subheader("⚙️ Settings & Channels")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Database", repository.backend_name)
    if ai_brain.is_configured():
        s2.metric("AI Engine", "🧠 AI-Brain")
    else:
        s2.metric("Gemini AI", "✅ Active" if settings.has_gemini_configured else "⚪ Heuristic")
    s3.metric("Email (Resend)", "✅ Ready" if settings.has_email_configured else "⚠️ Not set")
    s4.metric("Worker", "☁️ GitHub Actions" if dispatcher.uses_cloud_worker else "💻 Local")

    st.divider()
    _render_ai_brain_options(role)

    st.divider()
    st.markdown("### 📧 Test Email")
    col_mail, col_btn = st.columns([3, 1.2], vertical_alignment="bottom")
    with col_mail:
        test_recipient = st.text_input("Recipient Email", value=settings.email_to, placeholder="you@example.com")
    with col_btn:
        send_clicked = st.button("📨 Send Test Email", use_container_width=True)

    if send_clicked:
        ok, info = EmailNotificationChannel(settings).send_test_email(test_recipient)
        if ok:
            st.success(info)
        else:
            st.error(info)

    _render_infrastructure_guides(settings)
