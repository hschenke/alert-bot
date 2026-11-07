"""Idempotentes Datenbankschema für SQLite und Turso LibSQL."""

from __future__ import annotations

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS alert_rules (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        user_prompt TEXT NOT NULL,
        detector_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        search_query TEXT NOT NULL DEFAULT '',
        target_url TEXT NOT NULL DEFAULT '',
        condition_description TEXT NOT NULL DEFAULT '',
        threshold_json TEXT NOT NULL DEFAULT '{}',
        trigger_mode TEXT NOT NULL DEFAULT 'on_state_change',
        check_interval_minutes INTEGER NOT NULL DEFAULT 30,
        cooldown_minutes INTEGER NOT NULL DEFAULT 180,
        notify_on_first_match INTEGER NOT NULL DEFAULT 1,
        notify_email TEXT NOT NULL DEFAULT '',
        channels_json TEXT NOT NULL DEFAULT '["email"]',
        last_checked_at TEXT NOT NULL DEFAULT '',
        next_check_at TEXT NOT NULL DEFAULT '',
        last_triggered_at TEXT NOT NULL DEFAULT '',
        last_condition_met INTEGER NOT NULL DEFAULT 0,
        last_value_text TEXT NOT NULL DEFAULT '',
        last_content_hash TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT '',
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        total_triggers INTEGER NOT NULL DEFAULT 0,
        baseline_seeded INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT NOT NULL DEFAULT '',
        locked_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_rules_due
    ON alert_rules (status, next_check_at, locked_until)
    """,
    """
    CREATE TABLE IF NOT EXISTS alert_seen_items (
        rule_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        first_seen_at TEXT NOT NULL,
        PRIMARY KEY (rule_id, fingerprint)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alert_events (
        id TEXT PRIMARY KEY,
        rule_id TEXT NOT NULL,
        rule_title TEXT NOT NULL,
        detector_type TEXT NOT NULL,
        headline TEXT NOT NULL,
        summary TEXT NOT NULL,
        measured_display TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        items_json TEXT NOT NULL DEFAULT '[]',
        notified_channels_json TEXT NOT NULL DEFAULT '[]',
        notification_succeeded INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_events_created
    ON alert_events (created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_events_rule
    ON alert_events (rule_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS alert_check_logs (
        id TEXT PRIMARY KEY,
        rule_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        message TEXT NOT NULL,
        measured_display TEXT NOT NULL DEFAULT '',
        duration_ms INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_check_logs_rule
    ON alert_check_logs (rule_id, created_at DESC)
    """,
)
