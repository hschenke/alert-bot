"""Passwortschutz mit Brute-Force-Sperre und Read-Only-Token-Freigabe für E-Mail-Links."""

from __future__ import annotations

import streamlit as st

from src.config.settings import AppSettings
from src.domain.enums import AuthRole
from src.security.auth import LoginRateLimiter, generate_admin_token, resolve_credential_role


@st.cache_resource
def _get_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter()


def enforce_authentication(settings: AppSettings) -> AuthRole:
    """Prüft Login-Session oder URL-Token und rendert bei Bedarf die Anmeldeschranke."""
    if not settings.app_password.strip():
        return AuthRole.ADMIN

    session_token = str(st.session_state.get("auth_token", ""))
    role = resolve_credential_role(session_token, settings.app_password)
    if role == AuthRole.ADMIN:
        return AuthRole.ADMIN

    url_token = str(st.query_params.get("token", ""))
    if url_token:
        token_role = resolve_credential_role(url_token, settings.app_password)
        if token_role in {AuthRole.ADMIN, AuthRole.READONLY}:
            st.session_state["auth_token"] = url_token
            return token_role

    if role == AuthRole.READONLY:
        return AuthRole.READONLY

    _render_login_form(settings)
    st.stop()
    return AuthRole.GUEST


def _render_login_form(settings: AppSettings) -> None:
    limiter = _get_rate_limiter()
    st.title("🔐 Alert-Bot Anmeldung")
    st.caption("Geschützter Zugriff – bitte Passwort eingeben.")

    locked, wait_sec = limiter.is_locked_out("web")
    if locked:
        st.error(f"Zu viele Fehlversuche. Bitte {wait_sec} Sekunden warten.")
        return

    with st.form("login_form", clear_on_submit=True):
        entered = st.text_input("App-Passwort", type="password")
        submitted = st.form_submit_button("Anmelden", type="primary", use_container_width=True)

    if submitted:
        resolved = resolve_credential_role(entered, settings.app_password)
        if resolved == AuthRole.ADMIN:
            limiter.reset("web")
            st.session_state["auth_token"] = generate_admin_token(settings.app_password)
            st.rerun()
        limiter.record_failure("web")
        st.error("Ungültiges Passwort.")
