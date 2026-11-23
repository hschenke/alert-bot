"""Strukturierte Prüf-Diagnose, Trace-Builder & kompakte Serialisierung für UI und Worker-Logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.domain.models import CheckDiagnostics, DiscoveredItem
from src.security.sanitization import sanitize_display_url, strip_control_chars

_MAX_DETAILS_JSON_CHARS = 3800
_MAX_LIST_ITEMS = 8


@dataclass(frozen=True)
class SearchDiagnostics:
    """Telemetrie eines einzelnen Web-Suchlaufs (`ai_brain` oder `FreeWebSearchProvider`)."""

    provider: str = ""
    tier: str = ""
    query: str = ""
    tools_executed: tuple[str, ...] = ()
    tool_errors: tuple[str, ...] = ()
    raw_count: int = 0
    source_tools: tuple[str, ...] = ()
    fallback_used: bool = False
    latency_ms: int = 0


def _clean_text(raw: Any, max_len: int = 220) -> str:
    return strip_control_chars(str(raw or "")).strip()[:max_len]


def _append_unique(target: list[str], value: str, max_items: int = _MAX_LIST_ITEMS) -> None:
    clean = _clean_text(value)
    if clean and clean not in target and len(target) < max_items:
        target.append(clean)


def format_source_label(item: DiscoveredItem) -> str:
    """Erstellt ein kompaktes, sicheres Quellen-Label ohne sensible oder ungeprüfte URL-Fragmente."""
    safe_url = sanitize_display_url(item.url) if item.url else "#"
    host = (urlparse(safe_url).hostname or "").removeprefix("www.") if safe_url != "#" else ""
    title = _clean_text(item.title, 75)
    tool = _clean_text(item.source_tool, 30)
    meta_parts = [p for p in (host, tool) if p]
    meta = f" ({' · '.join(meta_parts)})" if meta_parts else ""
    return f"{title}{meta}" if title else (host or "Unbekannte Quelle")


class CheckTraceBuilder:
    """Sammelt Schritt-für-Schritt-Diagnosedaten eines einzelnen Regel-Checks (nicht thread-geteilt)."""

    def __init__(self, stage: str = "", query: str = "", provider: str = "") -> None:
        self.stage = _clean_text(stage, 60)
        self.provider = _clean_text(provider, 80)
        self.query = _clean_text(query, 180)
        self.raw_hits = 0
        self.relevant_hits = 0
        self.llm_status = ""
        self.hint = ""
        self._tools_used: list[str] = []
        self._tool_errors: list[str] = []
        self._sample_sources: list[str] = []
        self._pages_checked: list[str] = []
        self._steps: list[str] = []

    def add_step(self, text: str) -> None:
        _append_unique(self._steps, text, max_items=10)

    def add_tool(self, tool_name: str) -> None:
        _append_unique(self._tools_used, tool_name, max_items=10)

    def add_tool_error(self, error_text: str) -> None:
        _append_unique(self._tool_errors, error_text, max_items=8)

    def add_source(self, label: str) -> None:
        _append_unique(self._sample_sources, label, max_items=6)

    def add_page_checked(self, summary: str) -> None:
        _append_unique(self._pages_checked, summary, max_items=6)

    def merge_search(self, info: SearchDiagnostics | None) -> None:
        """Übernimmt Such-Telemetrie (Provider, Tier, Tools, Fehler) in den aktuellen Trace."""
        if info is None:
            return
        prov = f"{info.provider} ({info.tier})" if info.provider and info.tier else info.provider
        if prov:
            if not self.provider:
                self.provider = _clean_text(prov, 80)
            elif prov not in self.provider:
                self.provider = _clean_text(f"{self.provider} -> {prov}", 80)
        for tool in info.tools_executed:
            self.add_tool(tool)
        for err in info.tool_errors:
            self.add_tool_error(err)

    def build(self) -> CheckDiagnostics:
        return CheckDiagnostics(
            stage=_clean_text(self.stage, 60),
            provider=_clean_text(self.provider, 80),
            query=_clean_text(self.query, 180),
            tools_used=tuple(self._tools_used),
            tool_errors=tuple(self._tool_errors),
            raw_hits=max(0, int(self.raw_hits)),
            relevant_hits=max(0, int(self.relevant_hits)),
            sample_sources=tuple(self._sample_sources),
            llm_status=_clean_text(self.llm_status, 180),
            pages_checked=tuple(self._pages_checked),
            steps=tuple(self._steps),
            hint=_clean_text(self.hint, 260),
        )


def serialize_diagnostics(diag: CheckDiagnostics | None) -> str:
    """Serialisiert `CheckDiagnostics` kompakt und größenbegrenzt als JSON für `alert_check_logs`."""
    if diag is None:
        return ""
    payload: dict[str, Any] = {
        "stage": diag.stage,
        "provider": diag.provider,
        "query": diag.query,
        "tools_used": list(diag.tools_used[:10]),
        "tool_errors": list(diag.tool_errors[:8]),
        "raw_hits": diag.raw_hits,
        "relevant_hits": diag.relevant_hits,
        "sample_sources": list(diag.sample_sources[:6]),
        "llm_status": diag.llm_status,
        "pages_checked": list(diag.pages_checked[:6]),
        "steps": list(diag.steps[:10]),
        "hint": diag.hint,
    }
    compact = {k: v for k, v in payload.items() if v not in ("", [], 0)}
    if not compact:
        return ""
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= _MAX_DETAILS_JSON_CHARS:
        return encoded
    compact.pop("steps", None)
    compact["sample_sources"] = compact.get("sample_sources", [])[:3]
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    return encoded[:_MAX_DETAILS_JSON_CHARS] if len(encoded) <= _MAX_DETAILS_JSON_CHARS else ""


def deserialize_diagnostics(raw_json: str) -> CheckDiagnostics | None:
    """Deserialisiert gespeicherte Check-Diagnosedaten fehlertolerant."""
    if not raw_json or not raw_json.strip():
        return None
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    def _str_tuple(key: str) -> tuple[str, ...]:
        val = data.get(key)
        if not isinstance(val, list):
            return ()
        return tuple(_clean_text(entry, 240) for entry in val if entry)

    return CheckDiagnostics(
        stage=_clean_text(data.get("stage", ""), 60),
        provider=_clean_text(data.get("provider", ""), 80),
        query=_clean_text(data.get("query", ""), 180),
        tools_used=_str_tuple("tools_used"),
        tool_errors=_str_tuple("tool_errors"),
        raw_hits=int(data.get("raw_hits") or 0),
        relevant_hits=int(data.get("relevant_hits") or 0),
        sample_sources=_str_tuple("sample_sources"),
        llm_status=_clean_text(data.get("llm_status", ""), 180),
        pages_checked=_str_tuple("pages_checked"),
        steps=_str_tuple("steps"),
        hint=_clean_text(data.get("hint", ""), 260),
    )


def format_diagnostics_one_liner(diag: CheckDiagnostics | None) -> str:
    """Formatiert eine prägnante Ein-Zeilen-Diagnose für UI-Captions und Worker-Logs."""
    if diag is None:
        return ""
    parts: list[str] = []
    if diag.provider:
        parts.append(f"Engine: {diag.provider}")
    if diag.tools_used:
        parts.append(f"Tools: {', '.join(diag.tools_used[:5])}")
    if diag.raw_hits or diag.relevant_hits:
        parts.append(f"Treffer: {diag.relevant_hits}/{diag.raw_hits} relevant")
    if diag.llm_status:
        parts.append(f"KI: {diag.llm_status}")
    if diag.pages_checked:
        parts.append(f"Seiten: {len(diag.pages_checked)}")
    if diag.tool_errors:
        parts.append(f"Engpässe: {'; '.join(diag.tool_errors[:2])}")
    return " · ".join(parts)
