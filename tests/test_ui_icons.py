"""Tests für das App-Icon-Branding, E-Mail-Entschärfung und Streamlit-UI-Rendering."""

from __future__ import annotations

from pathlib import Path

from src.domain.enums import CheckOutcome, ComparisonOperator, DetectorType, RuleStatus, TriggerMode
from src.domain.models import AlertEvent, AlertRule, CheckLogEntry, ThresholdSpec
from src.notifications.email_renderer import render_alert_email_html
from src.storage.repository import SqlAlertRepository
from src.storage.sqlite_executor import SqliteExecutor
from src.ui.formatters import format_interval_label
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
            rule_title="Test Alert",
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

    def test_format_interval_label_uses_compact_english_units(self) -> None:
        assert format_interval_label(15) == "15m"
        assert format_interval_label(60) == "1h"
        assert format_interval_label(720) == "12h"
        assert format_interval_label(1440) == "1d"
        assert format_interval_label(2880) == "2d"

    def test_streamlit_app_renders_alerts_title_compact_kpis_logs_expander_and_safe_delete(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "ui_test.db"
        repo = SqlAlertRepository(SqliteExecutor(db_file))
        for idx, status in enumerate((RuleStatus.ACTIVE, RuleStatus.PAUSED, RuleStatus.ERROR), start=1):
            repo.upsert_rule(
                AlertRule(
                    id=f"rule_{idx}",
                    title=f"Sample Alert {idx}",
                    user_prompt=f"Prompt {idx} <script>alert(1)</script>",
                    detector_type=DetectorType.PRICE_THRESHOLD,
                    status=status,
                    threshold=ThresholdSpec(operator=ComparisonOperator.GTE, target_value=2.5, unit="%"),
                    trigger_mode=TriggerMode.ON_STATE_CHANGE,
                    check_interval_minutes=720,
                    last_value_text="2.45 %",
                    last_error="Temporary timeout" if status == RuleStatus.ERROR else "",
                    created_at=f"2026-11-13T10:0{idx}:00+00:00",
                    updated_at=f"2026-11-13T10:0{idx}:00+00:00",
                )
            )
        repo.append_check_log(
            CheckLogEntry(
                id="log_r1",
                rule_id="rule_1",
                outcome=CheckOutcome.OK_NO_MATCH,
                message="Measured 2.45 % (below 2.5 %)",
                duration_ms=88,
                created_at="2026-11-13T10:05:00+00:00",
            )
        )
        repo.close()

        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", "")
        monkeypatch.setenv("WORKER_MODE", "github")

        entrypoint = Path(__file__).resolve().parent.parent / "streamlit_app.py"
        at = AppTest.from_file(entrypoint).run()
        assert not at.exception

        sidebar_labels = [b.label for b in at.sidebar.button]
        assert any("Active Alerts" in lbl for lbl in sidebar_labels)
        assert any("New Alert" in lbl for lbl in sidebar_labels)
        assert any("Alert History" in lbl for lbl in sidebar_labels)
        assert any("Settings" in lbl for lbl in sidebar_labels)
        assert not any("Ansicht aktualisieren" in lbl for lbl in sidebar_labels)

        # Seitentitel der Active-Alerts-Seite ist kurz "📡 Alerts"
        assert [s.value for s in at.subheader] == ["📡 Alerts"]

        # Kompakte 4er-KPI-Leiste ohne riesige Einzelmetriken
        kpi_html = "".join(m.value for m in at.markdown if "ab-kpi-grid" in m.value)
        assert "ab-kpi-grid" in kpi_html
        for label in ("Active", "Total", "Triggers", "Events"):
            assert label in kpi_html

        # 3 Alert-Boxen + 3 innere Check-Logs-Boxen: alle standardmäßig eingeklappt, kein Log-Toggle
        assert len(at.toggle) == 0
        assert len(at.expander) == 6
        assert all(exp.proto.expanded is False for exp in at.expander)
        log_expanders = [exp for exp in at.expander if exp.label == "📋 Check Logs"]
        assert len(log_expanders) == 3
        assert any("Measured 2.45 %" in m.value for m in at.markdown)

        # Lösch-Absicherung: erster Klick löscht nicht, sondern fordert Checkbox + Bestätigung an
        at = at.button(key="del_rule_3").click().run()
        assert not at.exception
        assert len(at.expander) == 6
        assert len(at.warning) >= 1
        assert at.button(key="confirm_del_rule_3").disabled is True

        # Abbrechen behält die Regel unverändert
        at = at.button(key="cancel_del_rule_3").click().run()
        assert not at.exception
        assert len(at.expander) == 6

        # Erneutes Delete + Bestätigungs-Checkbox -> erst danach wird dauerhaft gelöscht
        at = at.button(key="del_rule_3").click().run()
        at = at.checkbox(key="confirm_del_chk_rule_3").check().run()
        assert at.button(key="confirm_del_rule_3").disabled is False
        at = at.button(key="confirm_del_rule_3").click().run()
        assert not at.exception
        assert len(at.expander) == 4

        verify_repo = SqlAlertRepository(SqliteExecutor(db_file))
        try:
            assert verify_repo.get_rule("rule_3") is None
            assert verify_repo.get_rule("rule_1") is not None
        finally:
            verify_repo.close()

        # Navigation zu allen anderen Tabs ohne Fehler
        for tab_name in ("New Alert", "Alert History", "Settings", "Active Alerts"):
            btns = [b for b in at.sidebar.button if tab_name in b.label]
            assert len(btns) == 1
            at = btns[0].click().run()
            assert not at.exception

        monkeypatch.setenv("APP_PASSWORD", "test-pass-123")
        at_login = AppTest.from_file(entrypoint).run()
        assert not at_login.exception
