"""Benachrichtigungspaket für E-Mail und künftige Echtzeit-Push-Kanäle."""

from src.notifications.channels import (
    NotificationChannel,
    NotificationRouter,
    create_notification_router,
)
from src.notifications.email_channel import EmailNotificationChannel
from src.notifications.email_renderer import build_event_app_url, render_alert_email_html

__all__ = [
    "EmailNotificationChannel",
    "NotificationChannel",
    "NotificationRouter",
    "build_event_app_url",
    "create_notification_router",
    "render_alert_email_html",
]
