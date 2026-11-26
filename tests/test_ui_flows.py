"""UI-End-to-End-Tests für Read-Only-E-Mail-Links, Admin-Login-Wechsel, Event-Berichtigung & Regel-Editor."""

from __future__ import annotations

from pathlib import Path

from src.domain.enums import ComparisonOperator, DetectorType, RuleStatus, TriggerMode
from src.domain.models import AlertEvent, AlertRule, ThresholdSpec
from src.security.auth import generate_readonly_token
from src.storage.repository import SqlAlertRepository
from src.storage.sqlite_executor import SqliteExecutor
from streamlit.testing.v1 import AppTest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "streamlit_app.py"


def _seed_bogus_alert_db(db_file: Path) -> None:
    repo = SqlAlertRepository(SqliteExecutor(db_file))
    try:
        repo.upsert_rule(
            AlertRule(
                id="bkk1",
                title="BKK Firmus Zusatzbeitragssatz überwachen",
                user_prompt="BKK Firmus Zusatzbeitragssatz >= 2,18 %",
                detector_type=DetectorType.PRICE_THRESHOLD,
                status=RuleStatus.ACTIVE,
                search_query="BKK Firmus Zusatzbeitragssatz aktuell",
                threshold=ThresholdSpec(
                    operator=ComparisonOperator.GTE,
                    target_value=2.18,
                    unit="%",
                ),
                trigger_mode=TriggerMode.ON_STATE_CHANGE,
                last_condition_met=True,
                last_value_text="226 %",
                last_triggered_at="2026-11-19T16:31:00+00:00",
                total_triggers=1,
                created_at="2026-11-19T10:00:00+00:00",
                updated_at="2026-11-19T16:31:00+00:00",
            )
        )
        repo.save_event(
            AlertEvent(
                id="ev_bogus",
                rule_id="bkk1",
                rule_title="BKK Firmus Zusatzbeitragssatz überwachen",
                detector_type=DetectorType.PRICE_THRESHOLD,
                headline="BKK Firmus Zusatzbeitragssatz überwachen (226 %)",
                summary="Aktueller Wert: 226 % (aus Web-Suche ermittelt) – Bedingung >= 2,18 % ist erfüllt.",
                measured_display="226 %",
                source_url="https://news.google.com/rss/articles/example",
                notification_succeeded=True,
                created_at="2026-11-19T16:31:00+00:00",
            )
        )
    finally:
        repo.close()


class TestEmailLinkAdminFlowAndRectification:
    def test_readonly_email_link_provides_admin_login_and_start_page_and_allows_rectification(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "email_flow.db"
        _seed_bogus_alert_db(db_file)
        secret_pw = "AdminPass!2026"
        ro_token = generate_readonly_token(secret_pw, scope="events")

        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", secret_pw)
        monkeypatch.setenv("WORKER_MODE", "github")

        at = AppTest.from_file(ENTRYPOINT)
        at.query_params["view"] = "events"
        at.query_params["event_id"] = "ev_bogus"
        at.query_params["token"] = ro_token
        at = at.run()
        assert not at.exception

        # Read-Only-Modus zeigt Admin-Login- und Start-Page-Buttons sowohl in Sidebar als auch oben im Content
        assert at.sidebar.button(key="ro_sidebar_admin_login_btn") is not None
        assert at.sidebar.button(key="ro_sidebar_home_btn") is not None
        assert at.button(key="ro_main_admin_login_btn") is not None
        assert at.button(key="ro_main_home_btn") is not None
        assert not any("rectify_btn" in (b.key or "") for b in at.button)

        # Klick auf "Admin Login" öffnet das Login-Formular (inkl. Back & Go to Start Page)
        at = at.button(key="ro_main_admin_login_btn").click().run()
        assert not at.exception
        assert at.button(key="login_back_ro_btn") is not None
        assert at.button(key="login_start_page_btn") is not None

        # Anmeldung als Admin über das Login-Formular
        at.text_input[0].set_value(secret_pw)
        at = at.button[0].click().run()
        assert not at.exception

        # Nach erfolgreichem Admin-Login ist die volle Navigation sowie Delete/Rectify sichtbar
        sidebar_labels = [b.label for b in at.sidebar.button]
        assert any("Active Alerts" in lbl for lbl in sidebar_labels)
        assert at.button(key="rectify_btn_focus_ev_bogus") is not None

        # Falschen Event löschen und Basiswert auf 2,18 % berichtigen
        at = at.button(key="rectify_btn_focus_ev_bogus").click().run()
        assert not at.exception
        at.text_input(key="corr_val_focus_ev_bogus").set_value("2,18 %")
        at = at.button(key="confirm_rectify_focus_ev_bogus").click().run()
        assert not at.exception

        verify_repo = SqlAlertRepository(SqliteExecutor(db_file))
        try:
            assert verify_repo.get_event("ev_bogus") is None
            fixed_rule = verify_repo.get_rule("bkk1")
            assert fixed_rule is not None
            assert fixed_rule.last_value_text == "2,18 %"
            assert fixed_rule.last_condition_met is False
            assert fixed_rule.total_triggers == 0
        finally:
            verify_repo.close()

    def test_readonly_go_to_start_page_clears_deep_link_params(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "start_page_flow.db"
        _seed_bogus_alert_db(db_file)
        secret_pw = "AdminPass!2026"
        ro_token = generate_readonly_token(secret_pw, scope="events")

        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", secret_pw)
        monkeypatch.setenv("WORKER_MODE", "github")

        at = AppTest.from_file(ENTRYPOINT)
        at.query_params["view"] = "events"
        at.query_params["event_id"] = "ev_bogus"
        at.query_params["token"] = ro_token
        at = at.run()
        assert not at.exception

        at = at.sidebar.button(key="ro_sidebar_home_btn").click().run()
        assert not at.exception
        assert "token" not in at.query_params
        assert "view" not in at.query_params
        assert "event_id" not in at.query_params
        # Zeigt jetzt sauber den normalen Login-Screen für die Startseite
        assert len(at.text_input) == 1

    def test_admin_can_edit_rule_target_url_and_reset_bogus_state(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "edit_rule_flow.db"
        _seed_bogus_alert_db(db_file)

        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", "")
        monkeypatch.setenv("WORKER_MODE", "github")

        at = AppTest.from_file(ENTRYPOINT).run()
        assert not at.exception

        at = at.button(key="edit_bkk1").click().run()
        assert not at.exception

        at.text_input(key="ed_bkk1_url").set_value("https://www.bkk-firmus.de/beitraege")
        at.text_input(key="ed_bkk1_lastval").set_value("")
        at.checkbox(key="ed_bkk1_reset_state").check()
        at = at.button(key="ed_bkk1_save").click().run()
        assert not at.exception

        verify_repo = SqlAlertRepository(SqliteExecutor(db_file))
        try:
            updated = verify_repo.get_rule("bkk1")
            assert updated is not None
            assert updated.target_url == "https://www.bkk-firmus.de/beitraege"
            assert updated.last_value_text == ""
            assert updated.last_condition_met is False
            assert updated.last_triggered_at == ""
            assert updated.last_triggered_value is None
        finally:
            verify_repo.close()

    def test_admin_can_configure_on_new_extreme_and_step_width(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "extreme_step_flow.db"
        _seed_bogus_alert_db(db_file)

        monkeypatch.setenv("SQLITE_PATH", str(db_file))
        monkeypatch.setenv("TURSO_URL", "")
        monkeypatch.setenv("TURSO_KEY", "")
        monkeypatch.setenv("APP_PASSWORD", "")
        monkeypatch.setenv("WORKER_MODE", "github")

        at = AppTest.from_file(ENTRYPOINT).run()
        assert not at.exception

        at = at.button(key="edit_bkk1").click().run()
        assert not at.exception

        at = at.selectbox(key="ed_bkk1_trig").select(TriggerMode.ON_NEW_EXTREME).run()
        assert not at.exception
        at.number_input(key="ed_bkk1_step").set_value(0.5)
        at = at.button(key="ed_bkk1_save").click().run()
        assert not at.exception

        verify_repo = SqlAlertRepository(SqliteExecutor(db_file))
        try:
            updated = verify_repo.get_rule("bkk1")
            assert updated is not None
            assert updated.trigger_mode == TriggerMode.ON_NEW_EXTREME
            assert updated.threshold.extreme_step == 0.5
        finally:
            verify_repo.close()

