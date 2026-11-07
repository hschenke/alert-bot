"""Tests für XSS-sicheres E-Mail-Rendering, Kanal-Routing und den CLI-Worker."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.config.settings import AppSettings
from src.domain.enums import ChannelType, DetectorType, EmailProvider
from src.domain.models import AlertEvent, AlertRule, DiscoveredItem
from src.notifications.channels import NotificationRouter
from src.notifications.email_channel import EmailNotificationChannel
from src.notifications.email_renderer import build_event_app_url, render_alert_email_html
from src.worker.cli_worker import run_cli_worker


class DummyFutureChannel:
    """Simuliert einen künftigen Push-Kanal (z. B. ntfy.sh) für das Router-Protokoll."""

    def __init__(self) -> None:
        self.called = False

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.NTFY

    def is_configured(self) -> bool:
        return True

    def send(self, rule: AlertRule, event: AlertEvent) -> bool:
        self.called = True
        return True


class TestNotificationsAndWorker:
    def test_html_email_escapes_xss_and_blocks_javascript_links(self) -> None:
        malicious_event = AlertEvent(
            id="ev_xss",
            rule_id="r1",
            rule_title="<img src=x onerror=alert(1)>",
            detector_type=DetectorType.WEB_SEARCH,
            headline="<script>alert('pwn')</script>",
            summary="Text mit <b>HTML</b>",
            measured_display="1 Treffer",
            source_url="javascript:alert(1)",
            items=(
                DiscoveredItem(
                    fingerprint="fp1",
                    title="<svg onload=alert(2)>Wohnung",
                    url="javascript:evil()",
                    summary="Details",
                    price_or_value="999 €",
                ),
            ),
            created_at="2026-11-07T12:00:00+00:00",
        )
        app_url = build_event_app_url("https://my-alerts.streamlit.app", "ev_xss", "geheim")
        html_out = render_alert_email_html(malicious_event, app_event_url=app_url)

        assert "<script>" not in html_out
        assert "&lt;script&gt;" in html_out
        assert "javascript:" not in html_out
        assert "token=ro%3Aevents%3A" in html_out

    @patch("src.notifications.email_channel.requests.post")
    def test_resend_channel_and_router_extensibility(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        settings = AppSettings(
            email_provider=EmailProvider.RESEND,
            resend_api_key="re_real_looking_test_key",
            email_to="empfaenger@example.de",
        )
        email_ch = EmailNotificationChannel(settings)
        ntfy_ch = DummyFutureChannel()
        router = NotificationRouter(channels=[email_ch, ntfy_ch])

        rule = AlertRule(
            id="r1",
            title="Test",
            user_prompt="Test",
            detector_type=DetectorType.WEATHER_THRESHOLD,
            channels=(ChannelType.EMAIL, ChannelType.NTFY),
            created_at="2026-11-07T12:00:00+00:00",
            updated_at="2026-11-07T12:00:00+00:00",
        )
        event = AlertEvent(
            id="e1",
            rule_id="r1",
            rule_title="Test",
            detector_type=DetectorType.WEATHER_THRESHOLD,
            headline="Temperatur erreicht",
            summary="22 °C erreicht",
            created_at="2026-11-07T12:00:00+00:00",
        )

        delivered, ok = router.dispatch_event(rule, event)
        assert ok is True
        assert delivered == ("email", "ntfy")
        assert ntfy_ch.called is True
        mock_post.assert_called_once()

    def test_cli_worker_exits_cleanly_when_no_due_rules(self, monkeypatch) -> None:
        monkeypatch.setenv("SQLITE_PATH", ":memory:")
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        exit_code = run_cli_worker(["--max-rules", "5"])
        assert exit_code == 0
