"""Chronologisches Treffer-Postfach aller ausgelösten Alarme inkl. Angebotslinks & Export."""

from __future__ import annotations

import streamlit as st

from src.domain.models import AlertEvent
from src.security.sanitization import sanitize_display_url
from src.storage.repository import SqlAlertRepository
from src.ui.formatters import DETECTOR_LABELS, format_iso_timestamp


def render_events_view(repository: SqlAlertRepository) -> None:
    """Zeigt alle ausgelösten Alarme und deren gefundene Einzel-Angebote an."""
    st.subheader("🔔 Ausgelöste Alarme & Treffer-Historie")

    focused_id = str(st.query_params.get("event_id", "")).strip()
    if focused_id:
        focused = repository.get_event(focused_id)
        if focused is not None:
            st.info("Direktansicht aus E-Mail-Benachrichtigung:")
            _render_event_card(focused, expanded=True)
            st.divider()

    events = repository.list_events(limit=50)
    if not events:
        st.info("Bisher wurden noch keine Alarme ausgelöst. Sobald eine Bedingung eintritt, erscheinen alle Treffer hier.")
        return

    md_export = _build_markdown_export(events)
    st.download_button(
        "📥 Treffer-Historie als Markdown herunterladen",
        data=md_export.encode("utf-8"),
        file_name="alert_historie.md",
        mime="text/markdown",
    )

    for ev in events:
        _render_event_card(ev, expanded=False)


def _render_event_card(event: AlertEvent, expanded: bool) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([3.5, 1.5])
        with c1:
            st.markdown(f"### 🔔 {event.headline}")
            st.caption(
                f"{format_iso_timestamp(event.created_at)}  •  "
                f"{DETECTOR_LABELS[event.detector_type]}"
            )
        with c2:
            mail_state = "📧 E-Mail zugestellt" if event.notification_succeeded else "💾 Im Dashboard gespeichert"
            st.caption(mail_state)
            if event.measured_display:
                st.markdown(f"**`{event.measured_display}`**")

        st.write(event.summary)

        safe_src = sanitize_display_url(event.source_url)
        if safe_src != "#":
            st.link_button("🔗 Direkt zur Quelle öffnen", safe_src)

        if event.items:
            with st.expander(f"📋 {len(event.items)} gefundene Einzel-Treffer / Inserate", expanded=expanded):
                for item in event.items:
                    price_suffix = f" — **{item.price_or_value}**" if item.price_or_value else ""
                    st.markdown(f"**{item.title}**{price_suffix}")
                    if item.summary:
                        st.caption(item.summary)
                    safe_item_url = sanitize_display_url(item.url)
                    if safe_item_url != "#":
                        st.markdown(f"[Zum Angebot öffnen]({safe_item_url})")
                    st.divider()


def _build_markdown_export(events: list[AlertEvent]) -> str:
    lines = ["# 🔔 Alert-Bot Treffer-Historie", ""]
    for ev in events:
        lines.append(f"## {ev.headline} ({format_iso_timestamp(ev.created_at)})")
        lines.append(ev.summary)
        if ev.source_url:
            lines.append(f"- Quelle: {ev.source_url}")
        for item in ev.items:
            lines.append(f"  - **{item.title}** ({item.price_or_value}): {item.url}")
        lines.append("")
    return "\n".join(lines)
