"""Modulare Kanal-Router-Architektur (E-Mail aktiv; ntfy.sh, Telegram & Webhook vorbereitet)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from src.config.settings import AppSettings
from src.domain.enums import ChannelType
from src.domain.models import AlertEvent, AlertRule
from src.notifications.email_channel import EmailNotificationChannel

logger = logging.getLogger(__name__)


class NotificationChannel(Protocol):
    """Einheitliches Protokoll für alle Alarm-Zustellkanäle."""

    @property
    def channel_type(self) -> ChannelType: ...

    def is_configured(self) -> bool: ...

    def send(self, rule: AlertRule, event: AlertEvent) -> bool: ...


class NotificationRouter:
    """Verteilt ausgelöste Alarme an alle für die Regel aktivierten Kanäle."""

    def __init__(self, channels: Sequence[NotificationChannel]) -> None:
        self._channels: dict[ChannelType, NotificationChannel] = {
            ch.channel_type: ch for ch in channels
        }

    def register_channel(self, channel: NotificationChannel) -> None:
        """Registriert zusätzliche Zustellkanäle (z. B. künftig ntfy.sh, Telegram, Webhook)."""
        self._channels[channel.channel_type] = channel

    def dispatch_event(
        self,
        rule: AlertRule,
        event: AlertEvent,
    ) -> tuple[tuple[str, ...], bool]:
        """Sendet `event` über alle angeforderten Kanäle und liefert `(erfolgreiche_kanäle, any_ok)`."""
        requested = rule.channels or (ChannelType.EMAIL,)
        delivered: list[str] = []

        for ch_type in requested:
            adapter = self._channels.get(ch_type)
            if adapter is None:
                logger.info("Kanal '%s' ist noch nicht registriert – überspringe.", ch_type.value)
                continue
            if adapter.send(rule, event):
                delivered.append(ch_type.value)

        return tuple(delivered), bool(delivered)


def create_notification_router(settings: AppSettings) -> NotificationRouter:
    """Erstellt den Standard-Router mit aktivem E-Mail-Kanal."""
    return NotificationRouter(channels=[EmailNotificationChannel(settings)])
