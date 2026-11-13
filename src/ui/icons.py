"""Zentrale Marken- und App-Icon-Helfer (Radar-Glocke auf Dark-Slate-Kachel)."""

from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from pathlib import Path

import streamlit as st

from src.security.sanitization import escape_html

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
APP_ICON_SVG_PATH = ASSETS_DIR / "app_icon.svg"
APP_ICON_PNG_PATH = ASSETS_DIR / "app_icon.png"
APPLE_TOUCH_ICON_PNG_PATH = ASSETS_DIR / "apple_touch_icon.png"


@lru_cache(maxsize=1)
def _load_svg_markup() -> str:
    return APP_ICON_SVG_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _load_icon_data_uris() -> tuple[str, str]:
    svg_b64 = base64.b64encode(_load_svg_markup().encode("utf-8")).decode("ascii")
    apple_b64 = base64.b64encode(APPLE_TOUCH_ICON_PNG_PATH.read_bytes()).decode("ascii")
    return (
        f"data:image/svg+xml;base64,{svg_b64}",
        f"data:image/png;base64,{apple_b64}",
    )


def inject_app_icons_head() -> None:
    """Registriert SVG-Tab-Icon und 180x180 Apple-Touch-Icon im Dokument-Header."""
    try:
        svg_uri, apple_uri = _load_icon_data_uris()
        js = (
            "<script>(function(){try{"
            "const setLink=(rel,attrs)=>{"
            "let el=document.head.querySelector('link[rel=\"'+rel+'\"]');"
            "if(!el){el=document.createElement('link');el.rel=rel;document.head.appendChild(el);}"
            "Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));"
            "};"
            f"setLink('apple-touch-icon',{{sizes:'180x180',href:{json.dumps(apple_uri)}}});"
            f"setLink('icon',{{type:'image/svg+xml',href:{json.dumps(svg_uri)}}});"
            "}catch(e){console.debug('icon head err',e);}})();</script>"
        )
        st.html(js, unsafe_allow_javascript=True)
    except Exception as exc:
        logger.debug("App-Icon-Header konnte nicht injiziert werden: %s", exc)


def render_brand_header(
    title: str = "Alert-Bot",
    subtitle: str = "",
    icon_size_px: int = 36,
    title_size_rem: float = 1.65,
) -> None:
    """Rendert das dezente Radar-Glocken-Logo neben dem App-Titel."""
    svg = _load_svg_markup().replace(
        "<svg ",
        f'<svg width="{icon_size_px}" height="{icon_size_px}" style="display:block;" ',
        1,
    )
    subtitle_html = (
        f"<p style='font-size:0.84rem;color:#94A3B8;margin:4px 0 12px 0;'>{escape_html(subtitle)}</p>"
        if subtitle
        else ""
    )
    st.markdown(
        "<div style='display:flex;align-items:center;gap:11px;margin:0 0 2px 0;'>"
        f"<div style='flex-shrink:0;'>{svg}</div>"
        f"<div style='font-size:{title_size_rem}rem;font-weight:700;line-height:1.15;"
        f"letter-spacing:-0.02em;color:#F8FAFC;'>{escape_html(title)}</div>"
        f"</div>{subtitle_html}",
        unsafe_allow_html=True,
    )
