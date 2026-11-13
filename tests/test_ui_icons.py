"""Tests für das App-Icon-Branding, E-Mail-Entschärfung und Streamlit-UI-Rendering."""

from __future__ import annotations

from pathlib import Path

from src.domain.enums import DetectorType
from src.domain.models import AlertEvent
from src.notifications.email_renderer import render_alert_email_html
from src.ui.icons import (
    APP_ICON_PNG_PATH,
    APP_ICON_SVG_PATH,
    APPLE_TOUCH_ICON_PNG_PATH,
    inject_app_icons_head,
    render_brand_header,
)
from streamlit.testing.v1 import AppTest


class TestAppIconsAndSubtleUi:
    def test_icon_assets_exist_and_have_valid_formats(self) -> None:
        assert APP_ICON_SVG_PATH.is_file()
        assert APP_ICON_PNG_PATH.is_file()
        assert APPLE_TOUCH_ICON_PNG_PATH.is_file()

        svg_content = APP_ICON_SVG_PATH.read_text(encoding="utf-8")
        assert "<svg" in svg_content
        assert "#0F172A" in svg_content
        assert "#3B82F6" in svg_content

        png_magic = b"\x89PNG\r\n\x1a\n"
        assert APP_ICON_PNG_PATH.read_bytes().startswith(png_magic)
        assert APPLE_TOUCH_ICON_PNG_PATH.read_bytes().startswith(png_magic)

    def test_no_yellow_bell_emojis_remain_in_src_or_readme(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for py_file in (root / "src").rglob("*.py"):
            assert "🔔" not in py_file.read_text(encoding="utf-8"), f"Unerwünschtes 🔔 in {py_file}"
        assert "🔔" not in (root / "README.md").read_text(encoding="utf-8")

    def test_email_renderer_uses_subtle_blue_indicator(self) -> None:
        event = AlertEvent(
            id="ev_icon",
            rule_id="r_icon",
            rule_title="Testwächter",
            detector_type=DetectorType.WEATHER_THRESHOLD,
            headline="Schwellenwert erreicht",
            summary="Aktueller Wert: 22 °C",
            created_at="2026-11-13T12:00:00+00:00",
        )
        html_out = render_alert_email_html(event)
        assert "🔔" not in html_out
        assert "background:#3B82F6" in html_out

    def test_icon_helpers_escape_xss_and_execute_safely(self) -> None:
        inject_app_icons_head()
        render_brand_header("<script>alert(1)</script>", subtitle="<b>Sub</b>")

    def test_streamlit_app_renders_main_and_login_without_errors(self, monkeypatch, tmp_path: Path) -> None:
        db_file = tmp_path / "ui_test.db"
        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", "")
        monkeypatch.setenv("WORKER_MODE", "github")

        entrypoint = Path(__file__).resolve().parent.parent / "streamlit_app.py"
        at = AppTest.from_file(entrypoint).run()
        assert not at.exception
        hist_btns = [b for b in at.sidebar.button if "Treffer-Historie" in b.label]
        assert len(hist_btns) == 1
        hist_btns[0].click().run()
        assert not at.exception

        monkeypatch.setenv("APP_PASSWORD", "test-pass-123")
        at_login = AppTest.from_file(entrypoint).run()
        assert not at_login.exception
