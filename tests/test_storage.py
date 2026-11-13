"""Unit- & Integrationstests für SQLite-Storage, atomares Claiming und Turso-Protokoll."""

from __future__ import annotations

from src.domain.enums import CheckOutcome, DetectorType, RuleStatus, TriggerMode
from src.domain.models import AlertEvent, AlertRule, CheckLogEntry, DiscoveredItem, ThresholdSpec
from src.storage.repository import SqlAlertRepository
from src.storage.sqlite_executor import SqliteExecutor
from src.storage.turso_executor import (
    _decode_turso_cell,
    _encode_turso_arg,
    normalize_turso_pipeline_url,
)


def _sample_rule(rule_id: str = "r1", next_check_at: str = "2026-11-07T10:00:00+00:00") -> AlertRule:
    return AlertRule(
        id=rule_id,
        title="Mietwohnungen Freiburg",
        user_prompt="Neue 3-Zimmer-Wohnung in Freiburg",
        detector_type=DetectorType.WEB_SEARCH,
        status=RuleStatus.ACTIVE,
        search_query="3 Zimmer Wohnung Freiburg",
        threshold=ThresholdSpec(target_value=1200.0, unit="€"),
        trigger_mode=TriggerMode.EVERY_NEW_ITEM,
        check_interval_minutes=15,
        next_check_at=next_check_at,
        created_at="2026-11-07T09:00:00+00:00",
        updated_at="2026-11-07T09:00:00+00:00",
    )


class TestSqlAlertRepository:
    def test_crud_atomic_claiming_and_deduplication(self) -> None:
        repo = SqlAlertRepository(SqliteExecutor(":memory:"))
        try:
            rule = _sample_rule("r1", "2026-11-07T10:00:00+00:00")
            future_rule = _sample_rule("r2", "2026-11-07T15:00:00+00:00")
            repo.upsert_rule(rule)
            repo.upsert_rule(future_rule)

            due = repo.claim_due_rules(
                now_iso="2026-11-07T10:05:00+00:00",
                lock_until_iso="2026-11-07T10:10:00+00:00",
                worker_id="worker-a",
            )
            assert [r.id for r in due] == ["r1"]

            # Zweiter paralleler Worker darf dieselbe gesperrte Regel NICHT doppelt claimen
            second_claim = repo.claim_due_rules(
                now_iso="2026-11-07T10:06:00+00:00",
                lock_until_iso="2026-11-07T10:11:00+00:00",
                worker_id="worker-b",
            )
            assert second_claim == []

            repo.release_rule_lock("r1")

            items = [
                DiscoveredItem(fingerprint="fp1", title="Wohnung A", url="https://example.com/a"),
                DiscoveredItem(fingerprint="fp2", title="Wohnung B", url="https://example.com/b"),
            ]
            repo.record_seen_items("r1", items, "2026-11-07T10:05:00+00:00")
            assert repo.get_seen_fingerprints("r1") == {"fp1", "fp2"}

            ev = AlertEvent(
                id="ev1",
                rule_id="r1",
                rule_title=rule.title,
                detector_type=DetectorType.WEB_SEARCH,
                headline="2 neue Wohnungen",
                summary="Zwei Treffer gefunden",
                items=tuple(items),
                notified_channels=("email",),
                notification_succeeded=True,
                created_at="2026-11-07T10:05:00+00:00",
            )
            repo.save_event(ev)
            loaded_events = repo.list_events(rule_id="r1")
            assert len(loaded_events) == 1
            assert len(loaded_events[0].items) == 2

            repo.append_check_log(
                CheckLogEntry(
                    id="log1",
                    rule_id="r1",
                    outcome=CheckOutcome.TRIGGERED,
                    message="OK",
                    duration_ms=142,
                    created_at="2026-11-07T10:05:00+00:00",
                )
            )
            repo.append_check_log(
                CheckLogEntry(
                    id="log2",
                    rule_id="r1",
                    outcome=CheckOutcome.OK_NO_MATCH,
                    message="Latest R1",
                    duration_ms=90,
                    created_at="2026-11-07T10:10:00+00:00",
                )
            )
            repo.append_check_log(
                CheckLogEntry(
                    id="log3",
                    rule_id="r2",
                    outcome=CheckOutcome.OK_NO_MATCH,
                    message="R2 Log",
                    duration_ms=55,
                    created_at="2026-11-07T10:12:00+00:00",
                )
            )
            assert len(repo.list_check_logs("r1")) == 2
            grouped = repo.list_recent_check_logs_by_rule(per_rule_limit=1)
            assert [e.id for e in grouped["r1"]] == ["log2"]
            assert [e.id for e in grouped["r2"]] == ["log3"]

            assert repo.delete_rule("r1") is True
            assert repo.get_rule("r1") is None
        finally:
            repo.close()

    def test_turso_hrana_helpers(self) -> None:
        assert (
            normalize_turso_pipeline_url("libsql://alert-bot-hschenke.turso.io")
            == "https://alert-bot-hschenke.turso.io/v2/pipeline"
        )
        assert _encode_turso_arg(True) == {"type": "integer", "value": "1"}
        assert _encode_turso_arg(21.5) == {"type": "float", "value": 21.5}
        assert _encode_turso_arg(None) == {"type": "null"}
        assert _decode_turso_cell({"type": "integer", "value": "42"}) == 42
        assert _decode_turso_cell({"type": "float", "value": 19.8}) == 19.8
