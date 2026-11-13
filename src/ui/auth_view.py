"""Passwortschutz mit Brute-Force-Sperre, 30-Tage-Cookie/Storage-Persistenz und Read-Only-Links."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
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
from src.ui.icons import render_brand_header

logger = logging.getLogger(__name__)


@st.cache_resource
def _get_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter()


def _safe_html(js: str) -> None:
    try:
        st.html(js, unsafe_allow_javascript=True)
    except Exception as exc:
        logger.debug("Fehler beim Rendern des JS-Snippets: %s", exc)


def _render_save_storage_script(token: str) -> None:
    """Persistiert das Token in clientseitigem localStorage und document.cookie."""
    if not token or not token.startswith("adm:") or not re.fullmatch(r"adm:\d+:[0-9a-f]{64}", token):
        return
    js = (
        f'<script>try{{localStorage.setItem("{AUTH_COOKIE_NAME}","{token}");'
        f'const s=location.protocol==="https:"?"; Secure":"";'
        f'document.cookie="{AUTH_COOKIE_NAME}={token}; max-age={AUTH_COOKIE_MAX_AGE_SECONDS}; path=/; SameSite=Lax"+s;'
        f'}}catch(e){{console.debug("save err",e);}}</script>'
    )
    _safe_html(js)


def _render_clear_storage_script() -> None:
    """Entfernt Token aus clientseitigem localStorage und document.cookie."""
    js = (
        f'<script>try{{localStorage.removeItem("{AUTH_COOKIE_NAME}");'
        f'const s=location.protocol==="https:"?"; Secure":"";'
        f'document.cookie="{AUTH_COOKIE_NAME}=; max-age=0; path=/; SameSite=Lax"+s;'
        f'}}catch(e){{console.debug("clear err",e);}}</script>'
    )
    _safe_html(js)


def _render_auto_restore_script() -> None:
    """Stellt gespeichertes Token aus localStorage/Cookie beim App-Start wieder her."""
    js = (
        f'<script>(function(){{try{{'
        f'let t=localStorage.getItem("{AUTH_COOKIE_NAME}");'
        f'if(!t){{const m=document.cookie.match(new RegExp("(^|;\\\\s*){AUTH_COOKIE_NAME}=([^;]*)"));if(m)t=decodeURIComponent(m[2]);}}'
        f'if(t&&t.startsWith("adm:")){{const u=new URL(location.href);'
        f'if(u.searchParams.get("token")!==t){{u.searchParams.set("token",t);location.replace(u.toString());}}'
        f'}}}}catch(e){{console.debug("restore err",e);}}}})();</script>'
    )
    _safe_html(js)


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
            expires_at = datetime.now(UTC) + timedelta(seconds=AUTH_COOKIE_MAX_AGE_SECONDS)
            controller.set(
                AUTH_COOKIE_NAME,
                token,
                max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
                expires=expires_at,
                same_site="lax",
            )
        except Exception as exc:
            logger.debug("Fehler beim Setzen des Auth-Cookies: %s", exc)


def clear_auth_cookie() -> None:
    """Entfernt das Authentifizierungs-Cookie und bereinigt den clientseitigen Speicher."""
    _render_clear_storage_script()
    if CookieController is not None:
        try:
            CookieController().remove(AUTH_COOKIE_NAME, same_site="lax")
        except Exception as exc:
            logger.debug("Fehler beim Löschen des Auth-Cookies: %s", exc)


def enforce_authentication(settings: AppSettings) -> AuthRole:
    """Prüft Session-State, 30-Tage-Browser-Persistenz oder URL-Token und rendert Login bei Bedarf."""
    if not settings.app_password.strip():
        return AuthRole.ADMIN

    if st.session_state.pop("just_logged_out", False):
        clear_auth_cookie()
        if "token" in st.query_params:
            st.query_params.clear()
        _render_login_form(settings)
        st.stop()
        return AuthRole.GUEST

    # 1. Aktive Session im Speicher
    session_token = str(st.session_state.get("auth_token", ""))
    role = resolve_credential_role(
        session_token,
        settings.app_password,
        max_age_seconds=AUTH_COOKIE_MAX_AGE_SECONDS,
    )
    if role == AuthRole.ADMIN:
        if st.session_state.get("remember_me") and not st.session_state.get("storage_synced"):
            _render_save_storage_script(session_token)
            st.session_state["storage_synced"] = True
        return AuthRole.ADMIN

    # 2. URL-Token (z. B. aus E-Mail-Direktlinks oder Mobile-Bookmark/Home-Screen)
    url_token = str(st.query_params.get("token", "")).strip()
    if url_token:
        token_role = resolve_credential_role(
            url_token,
            settings.app_password,
            max_age_seconds=AUTH_COOKIE_MAX_AGE_SECONDS,
        )
        if token_role in {AuthRole.ADMIN, AuthRole.READONLY}:
            st.session_state["auth_token"] = url_token
            if token_role == AuthRole.ADMIN:
                _render_save_storage_script(url_token)
                st.session_state["storage_synced"] = True
            return token_role
        st.query_params.clear()
        _render_clear_storage_script()

    # 3. Persistentes 30-Tage-Cookie im Browser
    controller = CookieController() if CookieController is not None else None
    cookie_token = _get_cookie_val(controller)
    if cookie_token and verify_admin_token(
        cookie_token,
        settings.app_password,
        max_age_seconds=AUTH_COOKIE_MAX_AGE_SECONDS,
    ):
        st.session_state["auth_token"] = cookie_token
        st.query_params["token"] = cookie_token
        _render_save_storage_script(cookie_token)
        st.session_state["storage_synced"] = True
        return AuthRole.ADMIN

    if role == AuthRole.READONLY:
        return AuthRole.READONLY

    # 4. Automatisches Wiederherstellen aus localStorage/Cookie anstoßen
    _render_auto_restore_script()

    _render_login_form(settings, controller)
    st.stop()
    return AuthRole.GUEST


def _render_login_form(settings: AppSettings, controller: Any | None = None) -> None:
    limiter = _get_rate_limiter()
    render_brand_header(
        "Alert-Bot Login",
        subtitle="Protected Access – please enter password.",
        icon_size_px=38,
        title_size_rem=1.85,
    )

    locked, wait_sec = limiter.is_locked_out("web")
    if locked:
        st.error(f"Too many failed attempts. Please wait {wait_sec}s.")
        return

    with st.form("login_form", clear_on_submit=True):
        entered = st.text_input("Password", type="password")
        remember_me = st.checkbox("🍪 Remember me (30 days)", value=True)
        submitted = st.form_submit_button("Login", type="primary", use_container_width=True)

    if submitted:
        resolved = resolve_credential_role(entered, settings.app_password)
        if resolved == AuthRole.ADMIN:
            limiter.reset("web")
            token = generate_admin_token(settings.app_password)
            st.session_state["auth_token"] = token
            if remember_me:
                st.query_params["token"] = token
                st.session_state["remember_me"] = True
                _set_cookie_val(controller, token)
            else:
                st.session_state["remember_me"] = False
                if "token" in st.query_params:
                    st.query_params.clear()
                _render_clear_storage_script()
            st.rerun()
        limiter.record_failure("web")
        st.error("Invalid password.")
