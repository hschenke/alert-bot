"""Sichere JSON-Serialisierung und Row-Mapping zwischen SQL-Zeilen und Fachmodellen."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.domain.enums import (
    ChannelType,
    CheckOutcome,
    ComparisonOperator,
    DetectorType,
    RuleStatus,
    TriggerMode,
    WeatherMetric,
)
from src.domain.models import (
    AlertEvent,
    AlertRule,
    CheckLogEntry,
    DiscoveredItem,
    ThresholdSpec,
)


def _safe_enum(enum_cls: Any, raw: Any, default: Any) -> Any:
    text = str(raw or "").strip().lower()
    for member in enum_cls:
        if member.value == text:
            return member
    return default


def serialize_threshold(spec: ThresholdSpec) -> str:
    """Serialisiert ein `ThresholdSpec` sicher als JSON-String."""
    payload = {
        "location_name": spec.location_name,
        "weather_metric": spec.weather_metric.value,
        "operator": spec.operator.value,
        "target_value": spec.target_value,
        "target_text": spec.target_text,
        "unit": spec.unit,
        "css_selector": spec.css_selector,
        "json_path": spec.json_path,
        "expected_status_code": spec.expected_status_code,
    }
    return json.dumps(payload, ensure_ascii=False)


def deserialize_threshold(raw_json: str) -> ThresholdSpec:
    """Deserialisiert ein `ThresholdSpec` fehlertolerant aus JSON."""
    try:
        data = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    raw_val = data.get("target_value")
    target_val = float(raw_val) if isinstance(raw_val, (int, float)) else None
    return ThresholdSpec(
        location_name=str(data.get("location_name", "")),
        weather_metric=_safe_enum(WeatherMetric, data.get("weather_metric"), WeatherMetric.TEMPERATURE_C),
        operator=_safe_enum(ComparisonOperator, data.get("operator"), ComparisonOperator.GTE),
        target_value=target_val,
        target_text=str(data.get("target_text", "")),
        unit=str(data.get("unit", "")),
        css_selector=str(data.get("css_selector", "")),
        json_path=str(data.get("json_path", "")),
        expected_status_code=int(data.get("expected_status_code", 200) or 200),
    )


def serialize_channels(channels: tuple[ChannelType, ...]) -> str:
    """Serialisiert Kanäle als JSON-Liste."""
    values = [ch.value for ch in channels] or [ChannelType.EMAIL.value]
    return json.dumps(values)


def deserialize_channels(raw_json: str) -> tuple[ChannelType, ...]:
    """Deserialisiert Kanäle aus JSON."""
    try:
        parsed = json.loads(raw_json or '["email"]')
    except json.JSONDecodeError:
        parsed = ["email"]
    if not isinstance(parsed, list):
        return (ChannelType.EMAIL,)
    result = tuple(
        _safe_enum(ChannelType, entry, ChannelType.EMAIL) for entry in parsed if entry
    )
    return result or (ChannelType.EMAIL,)


def serialize_items(items: tuple[DiscoveredItem, ...]) -> str:
    """Serialisiert gefundene Einzel-Treffer als JSON."""
    return json.dumps([asdict(item) for item in items], ensure_ascii=False)


def deserialize_items(raw_json: str) -> tuple[DiscoveredItem, ...]:
    """Deserialisiert `DiscoveredItem`-Objekte aus JSON."""
    try:
        parsed = json.loads(raw_json or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    items: list[DiscoveredItem] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        items.append(
            DiscoveredItem(
                fingerprint=str(entry.get("fingerprint", "")),
                title=str(entry.get("title", "")),
                url=str(entry.get("url", "")),
                summary=str(entry.get("summary", "")),
                price_or_value=str(entry.get("price_or_value", "")),
            )
        )
    return tuple(items)


def map_row_to_rule(row: dict[str, Any]) -> AlertRule:
    """Wandelt eine `alert_rules`-Zeile in ein unveränderliches `AlertRule`-Objekt um."""
    return AlertRule(
        id=str(row["id"]),
        title=str(row["title"]),
        user_prompt=str(row["user_prompt"]),
        detector_type=_safe_enum(DetectorType, row.get("detector_type"), DetectorType.WEB_SEARCH),
        status=_safe_enum(RuleStatus, row.get("status"), RuleStatus.ACTIVE),
        search_query=str(row.get("search_query") or ""),
        target_url=str(row.get("target_url") or ""),
        condition_description=str(row.get("condition_description") or ""),
        threshold=deserialize_threshold(str(row.get("threshold_json") or "{}")),
        trigger_mode=_safe_enum(TriggerMode, row.get("trigger_mode"), TriggerMode.ON_STATE_CHANGE),
        check_interval_minutes=int(row.get("check_interval_minutes") or 30),
        cooldown_minutes=int(row.get("cooldown_minutes") or 180),
        notify_on_first_match=bool(int(row.get("notify_on_first_match", 1))),
        notify_email=str(row.get("notify_email") or ""),
        channels=deserialize_channels(str(row.get("channels_json") or '["email"]')),
        last_checked_at=str(row.get("last_checked_at") or ""),
        next_check_at=str(row.get("next_check_at") or ""),
        last_triggered_at=str(row.get("last_triggered_at") or ""),
        last_condition_met=bool(int(row.get("last_condition_met", 0))),
        last_value_text=str(row.get("last_value_text") or ""),
        last_content_hash=str(row.get("last_content_hash") or ""),
        last_error=str(row.get("last_error") or ""),
        consecutive_failures=int(row.get("consecutive_failures") or 0),
        total_triggers=int(row.get("total_triggers") or 0),
        baseline_seeded=bool(int(row.get("baseline_seeded", 0))),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def map_row_to_event(row: dict[str, Any]) -> AlertEvent:
    """Wandelt eine `alert_events`-Zeile in ein `AlertEvent`-Objekt um."""
    try:
        ch_list = json.loads(str(row.get("notified_channels_json") or "[]"))
    except json.JSONDecodeError:
        ch_list = []
    channels = tuple(str(c) for c in ch_list) if isinstance(ch_list, list) else ()
    return AlertEvent(
        id=str(row["id"]),
        rule_id=str(row["rule_id"]),
        rule_title=str(row["rule_title"]),
        detector_type=_safe_enum(DetectorType, row.get("detector_type"), DetectorType.WEB_SEARCH),
        headline=str(row["headline"]),
        summary=str(row["summary"]),
        measured_display=str(row.get("measured_display") or ""),
        source_url=str(row.get("source_url") or ""),
        items=deserialize_items(str(row.get("items_json") or "[]")),
        notified_channels=channels,
        notification_succeeded=bool(int(row.get("notification_succeeded", 0))),
        created_at=str(row.get("created_at") or ""),
    )


def map_row_to_check_log(row: dict[str, Any]) -> CheckLogEntry:
    """Wandelt eine `alert_check_logs`-Zeile in ein `CheckLogEntry`-Objekt um."""
    return CheckLogEntry(
        id=str(row["id"]),
        rule_id=str(row["rule_id"]),
        outcome=_safe_enum(CheckOutcome, row.get("outcome"), CheckOutcome.OK_NO_MATCH),
        message=str(row.get("message") or ""),
        measured_display=str(row.get("measured_display") or ""),
        duration_ms=int(row.get("duration_ms") or 0),
        created_at=str(row.get("created_at") or ""),
    )
