"""XSS-sicherer, mobil-optimierter HTML-E-Mail-Renderer für ausgelöste Alarme."""

from __future__ import annotations

from urllib.parse import urlencode

from src.domain.models import AlertEvent, DiscoveredItem
from src.security.auth import generate_readonly_token
from src.security.sanitization import escape_html, sanitize_display_url


def build_event_app_url(
    base_url: str,
    event_id: str,
    app_password: str = "",
) -> str:
    """Erzeugt einen optional HMAC-signierten Read-Only-Link zur Web-App für das Smartphone."""
    clean_base = sanitize_display_url(base_url)
    if clean_base == "#":
        return ""
    params: dict[str, str] = {"view": "events", "event_id": event_id}
    if app_password.strip():
        params["token"] = generate_readonly_token(app_password, scope="events")
    return f"{clean_base.rstrip('/')}/?{urlencode(params)}"


def _render_item_card_html(item: DiscoveredItem) -> str:
    title_html = escape_html(item.title)
    summary_html = escape_html(item.summary)
    price_html = escape_html(item.price_or_value)
    safe_url = sanitize_display_url(item.url)

    price_badge = (
        f'<span style="display:inline-block;background:#FEF3C7;color:#92400E;'
        f'padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;margin-left:8px;">'
        f"{price_html}</span>"
        if price_html
        else ""
    )
    link_html = (
        f'<div style="margin-top:8px;"><a href="{escape_html(safe_url)}" '
        f'style="color:#EA580C;font-weight:600;text-decoration:none;font-size:13px;">'
        f"Direkt zum Angebot / zur Quelle &rarr;</a></div>"
        if safe_url != "#"
        else ""
    )
    desc_block = (
        f'<div style="color:#475569;font-size:13px;margin-top:4px;line-height:1.45;">{summary_html}</div>'
        if summary_html
        else ""
    )
    return (
        '<div style="border:1px solid #E2E8F0;border-radius:10px;padding:12px 14px;'
        'margin-bottom:10px;background:#FFFFFF;">'
        f'<div style="font-weight:700;color:#0F172A;font-size:15px;">{title_html}{price_badge}</div>'
        f"{desc_block}{link_html}</div>"
    )


def render_alert_email_html(event: AlertEvent, app_event_url: str = "") -> str:
    """Rendert ein `AlertEvent` als saubere, XSS-geschützte HTML-Benachrichtigung."""
    headline = escape_html(event.headline)
    summary = escape_html(event.summary)
    measured = escape_html(event.measured_display)
    timestamp = escape_html(event.created_at.replace("T", " ")[:19] + " UTC")
    safe_source = sanitize_display_url(event.source_url)

    measured_pill = (
        f'<div style="display:inline-block;background:#FFEDD5;color:#9A3412;'
        f'font-weight:700;font-size:14px;padding:6px 12px;border-radius:999px;margin-bottom:14px;">'
        f"Messwert / Status: {measured}</div>"
        if measured
        else ""
    )

    items_section = ""
    if event.items:
        cards = "".join(_render_item_card_html(it) for it in event.items)
        items_section = (
            '<div style="margin-top:18px;">'
            '<div style="font-size:13px;font-weight:700;text-transform:uppercase;'
            'color:#64748B;margin-bottom:8px;">Gefundene Treffer</div>'
            f"{cards}</div>"
        )

    buttons: list[str] = []
    if safe_source != "#":
        buttons.append(
            f'<a href="{escape_html(safe_source)}" style="display:inline-block;background:#EA580C;'
            'color:#FFFFFF;text-decoration:none;padding:10px 18px;border-radius:8px;'
            'font-weight:600;font-size:14px;margin-right:10px;">Originalquelle öffnen</a>'
        )
    safe_app_url = sanitize_display_url(app_event_url)
    if safe_app_url != "#":
        buttons.append(
            f'<a href="{escape_html(safe_app_url)}" style="display:inline-block;background:#0F172A;'
            'color:#FFFFFF;text-decoration:none;padding:10px 18px;border-radius:8px;'
            'font-weight:600;font-size:14px;">Im Alert-Dashboard ansehen</a>'
        )
    cta_row = f'<div style="margin-top:22px;">{"".join(buttons)}</div>' if buttons else ""

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:20px;background:#F1F5F9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <div style="max-width:620px;margin:0 auto;background:#F8FAFC;border:1px solid #CBD5E1;border-radius:14px;overflow:hidden;">
    <div style="background:#0F172A;color:#F8FAFC;padding:18px 22px;">
      <div style="font-size:12px;color:#93C5FD;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#3B82F6;margin-right:6px;vertical-align:middle;"></span>Alert-Bot Treffer-Meldung
      </div>
      <h1 style="margin:6px 0 0 0;font-size:20px;line-height:1.3;">{headline}</h1>
    </div>
    <div style="padding:22px;">
      {measured_pill}
      <p style="margin:0;color:#1E293B;font-size:15px;line-height:1.6;">{summary}</p>
      {items_section}
      {cta_row}
    </div>
    <div style="padding:12px 22px;background:#E2E8F0;color:#475569;font-size:12px;">
      Ausgelöst am {timestamp} &bull; Regel: {escape_html(event.rule_title)}
    </div>
  </div>
</body>
</html>"""
