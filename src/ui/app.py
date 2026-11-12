"""Haupt-Controller der Streamlit Web-App (reine Präsentationsschicht)."""

from __future__ import annotations

import streamlit as st

from src.__version__ import __version__
from src.config.settings import AppSettings, load_app_settings
from src.domain.enums import AuthRole
from src.storage.repository import SqlAlertRepository, create_alert_repository
from src.ui.auth_view import clear_auth_cookie, enforce_authentication
from src.ui.styles import inject_global_styles
from src.ui.views.active_alerts_view import render_active_alerts_view
from src.ui.views.events_view import render_events_view
from src.ui.views.new_alert_view import render_new_alert_view
from src.ui.views.settings_view import render_settings_view
from src.worker.dispatcher import AlertWorkDispatcher


@st.cache_resource
def _get_shared_dispatcher(settings: AppSettings) -> AlertWorkDispatcher:
    dispatcher = AlertWorkDispatcher(settings)
    dispatcher.ensure_scheduler_active()
    return dispatcher


def _open_repository(settings: AppSettings) -> SqlAlertRepository:
    return create_alert_repository(settings)


def run_streamlit_app() -> None:
    """Initialisiert Seite, Authentifizierung, Navigation und die gewählte Teilansicht."""
    st.set_page_config(
        page_title="Alert-Bot | 24/7 Smart Watcher",
        page_icon="🔔",
        layout="wide",
    )
    inject_global_styles()

    settings = load_app_settings()
    role = enforce_authentication(settings)
    dispatcher = _get_shared_dispatcher(settings)
    repository = _open_repository(settings)

    try:
        with st.sidebar:
            st.title("🔔 Alert-Bot")
            st.caption(f"v{__version__} • 24/7 Smart Monitoring")
            if role == AuthRole.READONLY:
                st.info("🔓 Lese-Ansicht (E-Mail-Direktlink)")
                render_events_only = True
            else:
                render_events_only = False

            default_view = str(st.query_params.get("view", "")).strip()
            pages = ["📡 Aktive Wächter", "➕ Neuer Alert", "🔔 Treffer-Historie", "⚙️ System & Kanäle"]
            if "active_page" not in st.session_state:
                if render_events_only or default_view == "events":
                    st.session_state["active_page"] = "🔔 Treffer-Historie"
                elif default_view == "new":
                    st.session_state["active_page"] = "➕ Neuer Alert"
                else:
                    st.session_state["active_page"] = "📡 Aktive Wächter"
            elif render_events_only:
                st.session_state["active_page"] = "🔔 Treffer-Historie"

            if not render_events_only:
                st.markdown(
                    "<p style='font-size:0.8rem;font-weight:600;color:#94a3b8;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.05em;'>Navigation</p>",
                    unsafe_allow_html=True,
                )
                for page_name in pages:
                    is_active = st.session_state["active_page"] == page_name
                    if st.button(
                        page_name,
                        key=f"nav_btn_{page_name}",
                        type="primary" if is_active else "secondary",
                        use_container_width=True,
                    ):
                        st.session_state["active_page"] = page_name
                        st.rerun()

            selected_page = st.session_state["active_page"]

            st.divider()
            st.caption(f"Speicher: **{repository.backend_name}**")
            if st.button("🔄 Ansicht aktualisieren", use_container_width=True):
                st.rerun()
            if (
                settings.app_password.strip()
                and role == AuthRole.ADMIN
                and st.button("🚪 Abmelden", use_container_width=True)
            ):
                clear_auth_cookie()
                st.session_state.pop("auth_token", None)
                st.session_state["just_logged_out"] = True
                if "token" in st.query_params:
                    st.query_params.clear()
                st.rerun()

        if selected_page == "📡 Aktive Wächter":
            render_active_alerts_view(repository, dispatcher, role)
        elif selected_page == "➕ Neuer Alert":
            render_new_alert_view(settings, repository, dispatcher)
        elif selected_page == "🔔 Treffer-Historie":
            render_events_view(repository)
        else:
            render_settings_view(settings, repository, dispatcher)
    finally:
        repository.close()
