"""Passwortschutz mit Brute-Force-Sperre, 30-Tage-Cookie-Persistenz und Read-Only-Links."""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st

try:
    from streamlit_cookies_controller import CookieController
except ImportError:
    CookieController = None  # type: ignore[assignment, misc]

from src.config.constants import AUTH_COOKIE_MAX_AGE_SECONDS, AUTH_COOKIE_NAME
from src.config.settings import AppSettings
from src.domain.enums import AuthRole
from src.security.auth import (
    LoginRateLimiter,
    generate_admin_token,
    resolve_credential_role,
    verify_admin_token,
)

logger = logging.getLogger(__name__)


@st.cache_resource
def _get_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter()


def _get_cookie_val(controller: Any | None) -> str:
    try:
        if hasattr(st, "context") and hasattr(st.context, "cookies"):
            val = st.context.cookies.get(AUTH_COOKIE_NAME)
            if val:
                return str(val).strip()
    except Exception as exc:
        logger.debug("Fehler beim Lesen aus st.context.cookies: %s", exc)

    if controller is not None:
        try:
            val = controller.get(AUTH_COOKIE_NAME)
            return str(val).strip() if val else ""
        except Exception as exc:
            logger.debug("Fehler beim Lesen des Auth-Cookies via Controller: %s", exc)
    return ""


def _set_cookie_val(controller: Any | None, token: str) -> None:
    if controller is not None:
        try:
            controller.set(AUTH_COOKIE_NAME, token, max_age=AUTH_COOKIE_MAX_AGE_SECONDS)
        except Exception as exc:
            logger.debug("Fehler beim Setzen des Auth-Cookies: %s", exc)


def clear_auth_cookie() -> None:
    """Entfernt das Authentifizierungs-Cookie beim Abmelden."""
    if CookieController is not None:
        try:
            CookieController().remove(AUTH_COOKIE_NAME)
        except Exception as exc:
            logger.debug("Fehler beim Löschen des Auth-Cookies: %s", exc)


def enforce_authentication(settings: AppSettings) -> AuthRole:
    """Prüft Session-State, 30-Tage-Browser-Cookie oder URL-Token und rendert Login bei Bedarf."""
    if not settings.app_password.strip():
        return AuthRole.ADMIN

    # 1. Aktive Session im Speicher
    session_token = str(st.session_state.get("auth_token", ""))
    role = resolve_credential_role(session_token, settings.app_password)
    if role == AuthRole.ADMIN:
        return AuthRole.ADMIN

    # 2. Persistentes 30-Tage-Cookie im Browser
    controller = CookieController() if CookieController is not None else None
    cookie_token = _get_cookie_val(controller)
    if cookie_token and verify_admin_token(
        cookie_token,
        settings.app_password,
        max_age_seconds=AUTH_COOKIE_MAX_AGE_SECONDS,
    ):
        st.session_state["auth_token"] = cookie_token
        return AuthRole.ADMIN

    # 3. URL-Token (z. B. aus E-Mail-Direktlinks)
    url_token = str(st.query_params.get("token", ""))
    if url_token:
        token_role = resolve_credential_role(url_token, settings.app_password)
        if token_role in {AuthRole.ADMIN, AuthRole.READONLY}:
            st.session_state["auth_token"] = url_token
            return token_role

    if role == AuthRole.READONLY:
        return AuthRole.READONLY

    _render_login_form(settings, controller)
    st.stop()
    return AuthRole.GUEST


def _render_login_form(settings: AppSettings, controller: Any | None = None) -> None:
    limiter = _get_rate_limiter()
    st.title("🔐 Alert-Bot Anmeldung")
    st.caption("Geschützter Zugriff – bitte Passwort eingeben.")

    locked, wait_sec = limiter.is_locked_out("web")
    if locked:
        st.error(f"Zu viele Fehlversuche. Bitte {wait_sec} Sekunden warten.")
        return

    with st.form("login_form", clear_on_submit=True):
        entered = st.text_input("App-Passwort", type="password")
        remember_me = False
        if controller is not None:
            remember_me = st.checkbox("🍪 Auf diesem Gerät 30 Tage angemeldet bleiben", value=True)
        submitted = st.form_submit_button("Anmelden", type="primary", use_container_width=True)

    if submitted:
        resolved = resolve_credential_role(entered, settings.app_password)
        if resolved == AuthRole.ADMIN:
            limiter.reset("web")
            token = generate_admin_token(settings.app_password)
            st.session_state["auth_token"] = token
            if remember_me:
                _set_cookie_val(controller, token)
            st.rerun()
        limiter.record_failure("web")
        st.error("Ungültiges Passwort.")
