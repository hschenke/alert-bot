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
    st.subheader("⚙️ Systemstatus, Taktung & Kanäle")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Datenbank", repository.backend_name)
    s2.metric("Gemini KI", "✅ Aktiv" if settings.has_gemini_configured else "⚪ Heuristik-Modus")
    s3.metric("E-Mail-Versand", "✅ Resend" if settings.has_email_configured else "⚠️ Nicht gesetzt")
    s4.metric("Hintergrund-Worker", "☁️ GitHub Actions" if dispatcher.uses_cloud_worker else "💻 Lokal")

    st.divider()
    st.markdown("### 📧 E-Mail-Benachrichtigung testen")
    col_mail, col_btn = st.columns([3, 1.2])
    with col_mail:
        test_recipient = st.text_input(
            "Zieladresse für Testmail",
            value=settings.email_to,
            placeholder="deine-adresse@example.com",
        )
    with col_btn:
        st.write("")
        if st.button("📨 Test-Mail senden", use_container_width=True):
            ok, info = EmailNotificationChannel(settings).send_test_email(test_recipient)
            if ok:
                st.success(info)
            else:
                st.error(info)

    st.divider()
    st.markdown("### ⏱️ 15–60 Min. Automatik + optionaler 1–5 Min. Echtzeit-Takt (0 €)")
    st.markdown(
        "Standardmäßig prüft `.github/workflows/check_alerts.yaml` kostenlos über GitHub Actions alle **15 Minuten** "
        "sämtliche fälligen Wächter. Wenn du für einzelne Regeln einen **punktgenauen 1–5-Minuten-Takt** ohne "
        "GitHub-Cron-Verzögerung möchtest, kannst du den Workflow kostenlos auf **[cron-job.org](https://cron-job.org)** antriggern:"
    )
    st.code(
        f"POST https://api.github.com/repos/{settings.github_repo}/actions/workflows/{settings.github_workflow_file}/dispatches\n"
        "Headers:\n"
        "  Accept: application/vnd.github+json\n"
        "  Authorization: Bearer <DEIN_FINE_GRAINED_GITHUB_TOKEN>\n"
        'Body (JSON):\n  {"ref": "main", "inputs": {"rule_id": "", "force": "false"}}',
        language="http",
    )

    st.divider()
    st.markdown("### ☕ Streamlit Cloud Einschlaf-Schutz (24/7 Keep-Alive)")
    st.info(
        "**Warum schläft Streamlit Cloud ein?**\n\n"
        "Streamlit Community Cloud versetzt Apps nach Inaktivität in den Ruhezustand (\"Zzz\"). "
        "Dadurch pausiert auch der integrierte Hintergrund-Prüfer.\n\n"
        "**Dauerhafte 24/7-Lösung (kostenlos in 1 Minute eingerichtet):**\n"
        "1. In Streamlit Cloud (`share.streamlit.io`) in den App-Einstellungen die Sichtbarkeit auf **\"Public\"** stellen "
        "(sicher, da Alert-Bot seine eigene Passwort-Schranke hat) ODER in cron-job.org unter *Erweitert* Weiterleitungen aktivieren.\n"
        "2. Auf **[cron-job.org](https://cron-job.org)** einen Job anlegen, der deine Streamlit-URL alle **5 bis 10 Minuten per HTTP GET** aufruft.\n"
        "3. **Ergebnis:** Deine App schläft nie wieder ein, muss morgens nicht aufgeweckt werden und führt stündliche/geplante Checks zuverlässig aus!"
    )

    st.divider()
    st.markdown("### 🗺️ Vorbereitete Ausbaustufen für weitere Alarm-Kanäle")
    st.caption(
        "Die Kanal-Architektur (`NotificationRouter`) ist bereits modular vorbereitet. "
        "Sobald deine E-Mail-Alerts rund laufen, können wir jederzeit folgende 0-€-Kanäle zuschalten:"
    )
    r1, r2, r3 = st.columns(3)
    with r1, st.container(border=True):
        st.markdown("#### 📱 ntfy.sh Push")
        st.caption("Sofort-Push auf iOS & Android mit Notfall-Prioritätsstufen – komplett ohne Account nutzbar.")
    with r2, st.container(border=True):
        st.markdown("#### 💬 Telegram Bot")
        st.caption("Echtzeit-Nachricht aufs Smartphone inklusive anklickbarer Angebots-Buttons.")
    with r3, st.container(border=True):
        st.markdown("#### 🔗 Webhook / Discord / Slack")
        st.caption("Generischer JSON-POST für Discord, Slack, Microsoft Teams oder Home Assistant.")
