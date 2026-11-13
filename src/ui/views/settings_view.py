"""Systemstatus, E-Mail-Funktionstest, 1–5-Minuten Cloud-Takt-Hilfe & Kanal-Roadmap."""

from __future__ import annotations

import streamlit as st

from src.config.settings import AppSettings
from src.notifications.email_channel import EmailNotificationChannel
from src.storage.repository import SqlAlertRepository
from src.worker.dispatcher import AlertWorkDispatcher


def render_settings_view(
    settings: AppSettings,
    repository: SqlAlertRepository,
    dispatcher: AlertWorkDispatcher,
) -> None:
    """Zeigt Infrastruktur-Status, Testmail-Versand, Takt-Anleitung und geplante Kanäle."""
    st.subheader("⚙️ Settings & Channels")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Database", repository.backend_name)
    s2.metric("Gemini AI", "✅ Active" if settings.has_gemini_configured else "⚪ Heuristic")
    s3.metric("Email (Resend)", "✅ Ready" if settings.has_email_configured else "⚠️ Not set")
    s4.metric("Worker", "☁️ GitHub Actions" if dispatcher.uses_cloud_worker else "💻 Local")

    st.divider()
    st.markdown("### 📧 Test Email")
    col_mail, col_btn = st.columns([3, 1.2])
    with col_mail:
        test_recipient = st.text_input(
            "Recipient Email",
            value=settings.email_to,
            placeholder="you@example.com",
        )
    with col_btn:
        st.write("")
        if st.button("📨 Send Test Email", use_container_width=True):
            ok, info = EmailNotificationChannel(settings).send_test_email(test_recipient)
            if ok:
                st.success(info)
            else:
                st.error(info)

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
