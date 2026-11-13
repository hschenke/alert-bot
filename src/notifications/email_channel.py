"""E-Mail-Zustellkanal via Resend HTTPS API ohne PII-Leaks in Logs."""

from __future__ import annotations

import logging

import requests

from src.config.constants import EMAIL_HTTP_TIMEOUT_SECONDS
from src.config.settings import AppSettings
from src.domain.enums import ChannelType
from src.domain.exceptions import InputValidationError
from src.domain.models import AlertEvent, AlertRule
from src.notifications.email_renderer import build_event_app_url, render_alert_email_html
from src.security.sanitization import mask_email_for_log, validate_email_address

logger = logging.getLogger(__name__)
_RESEND_ENDPOINT = "https://api.resend.com/emails"


class EmailNotificationChannel:
    """Versendet HTML-Alarm-E-Mails über die Resend REST API."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.EMAIL

    def is_configured(self) -> bool:
        return self._settings.has_email_configured

    def send(self, rule: AlertRule, event: AlertEvent) -> bool:
        """Sendet das ausgelöste `AlertEvent` an die Regel- oder Standard-Empfängeradresse."""
        raw_to = rule.notify_email.strip() or self._settings.email_to.strip()
        if not raw_to:
            logger.info("Kein E-Mail-Empfänger für Regel '%s' hinterlegt.", rule.id)
            return False

        try:
            recipient = validate_email_address(raw_to)
        except InputValidationError as exc:
            logger.warning("Ungültiger E-Mail-Empfänger bei Regel '%s': %s", rule.id, exc)
            return False

        if not self.is_configured():
            logger.info("Kein RESEND_API_KEY konfiguriert – Versand übersprungen.")
            return False

        app_url = build_event_app_url(
            base_url=self._settings.streamlit_app_url,
            event_id=event.id,
            app_password=self._settings.app_password,
        )
        html_body = render_alert_email_html(event=event, app_event_url=app_url)
        subject = f"Alert: {event.headline[:95]}"
        return self._send_via_resend(recipient, subject, html_body)

    def send_test_email(self, override_recipient: str = "") -> tuple[bool, str]:
        """Versendet eine kurze Funktions-Testmail aus dem Einstellungs-Tab."""
        raw_to = override_recipient.strip() or self._settings.email_to.strip()
        if not raw_to:
            return False, "Keine Ziel-E-Mail-Adresse angegeben."
        try:
            recipient = validate_email_address(raw_to)
        except InputValidationError as exc:
            return False, str(exc)

        if not self.is_configured():
            return False, "Kein gültiger RESEND_API_KEY konfiguriert."

        html_body = (
            "<div style='font-family:sans-serif;padding:20px;'>"
            "<h2>✅ Alert-Bot E-Mail-Test erfolgreich</h2>"
            "<p>Dein E-Mail-Kanal (<b>Resend</b>) ist einsatzbereit und sendet künftige "
            "Alarme zuverlässig an dieses Postfach.</p></div>"
        )
        ok = self._send_via_resend(recipient, "✅ Alert-Bot: Test-Benachrichtigung", html_body)
        if ok:
            return True, f"Test-E-Mail erfolgreich an {mask_email_for_log(recipient)} gesendet."
        return False, "Versand fehlgeschlagen – bitte RESEND_API_KEY und Absenderadresse prüfen."

    def _send_via_resend(self, recipient: str, subject: str, html_body: str) -> bool:
        masked = mask_email_for_log(recipient)
        try:
            resp = requests.post(
                _RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self._settings.email_from,
                    "to": [recipient],
                    "subject": subject,
                    "html": html_body,
                },
                timeout=EMAIL_HTTP_TIMEOUT_SECONDS,
            )
            if resp.status_code in {200, 201}:
                logger.info("Alarm-E-Mail via Resend erfolgreich an %s zugestellt.", masked)
                return True
            logger.error("Resend API antwortete mit HTTP %d.", resp.status_code)
            return False
        except requests.RequestException as exc:
            logger.error("Netzwerkfehler beim Resend-Versand an %s: %s", masked, exc)
            return False
