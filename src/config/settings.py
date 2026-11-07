"""Sicheres Laden der Anwendungskonfiguration aus ENV, Streamlit Secrets und YAML."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.config.constants import (
    CANDIDATE_MODELS,
    DEFAULT_CHECK_INTERVAL_MINUTES,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_DEEP_MODEL,
    DEFAULT_FAST_MODEL,
    DEFAULT_LOCAL_POLL_SECONDS,
    DEFAULT_MAX_LOCAL_WORKERS,
    DEFAULT_RULE_LOCK_SECONDS,
    DEFAULT_SQLITE_REL_PATH,
)
from src.domain.enums import WorkerMode

load_dotenv()
logger = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent


def _read_streamlit_secret(key: str) -> str | None:
    """Liest optional einen Schlüssel aus `st.secrets`, sofern Streamlit aktiv ist."""
    if "STREAMLIT_SERVER_PORT" not in os.environ and "streamlit" not in sys.modules:
        return None
    try:
        import streamlit as st

        if hasattr(st, "secrets") and key in st.secrets:
            value = st.secrets[key]
            return str(value).strip() if value is not None else None
    except Exception as exc:
        logger.debug("Streamlit Secret '%s' nicht lesbar: %s", key, exc)
    return None


def get_secret_or_env(key: str, default: str = "") -> str:
    """Ermittelt einen Konfigurationswert priorisiert aus ENV, danach `st.secrets`."""
    env_val = os.getenv(key)
    if env_val is not None and env_val.strip():
        return env_val.strip()
    secret_val = _read_streamlit_secret(key)
    return secret_val if secret_val else default


def _load_yaml_defaults(yaml_path: Path | None = None) -> dict[str, Any]:
    """Lädt deklarative Standardeinstellungen sicher mit `yaml.safe_load`."""
    target = yaml_path or (PROJECT_ROOT / "config" / "settings.yaml")
    if not target.is_file():
        return {}
    try:
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Konnte settings.yaml nicht lesen (%s): %s", target, exc)
        return {}


def _parse_enum(enum_cls: Any, raw_value: str, fallback: Any) -> Any:
    """Konvertiert einen String fehlertolerant in einen Enum-Wert."""
    cleaned = raw_value.strip().lower()
    for member in enum_cls:
        if member.value == cleaned:
            return member
    return fallback


@dataclass(frozen=True)
class PromptSettings:
    """System-Prompts für KI-Compiler, Web-Suche, Zahlenextraktion und Diff-Prüfung."""

    compiler_system_prompt: str = ""
    web_search_system_prompt: str = ""
    metric_extract_system_prompt: str = ""
    page_change_system_prompt: str = ""


@dataclass(frozen=True)
class AppSettings:
    """Zentrales Konfigurationsobjekt (`repr=False` auf allen Geheimnissen)."""

    gemini_api_key: str = field(default="", repr=False)
    gemini_fast_model: str = DEFAULT_FAST_MODEL
    gemini_deep_model: str = DEFAULT_DEEP_MODEL
    allowed_models: tuple[str, ...] = CANDIDATE_MODELS

    turso_url: str = ""
    turso_auth_token: str = field(default="", repr=False)
    sqlite_path: str = DEFAULT_SQLITE_REL_PATH

    worker_mode: WorkerMode = WorkerMode.AUTO
    github_token: str = field(default="", repr=False)
    github_repo: str = "hschenke/alert-bot"
    github_branch: str = "main"
    github_workflow_file: str = "check_alerts.yaml"
    local_scheduler_poll_seconds: int = DEFAULT_LOCAL_POLL_SECONDS
    max_concurrent_local_checks: int = DEFAULT_MAX_LOCAL_WORKERS
    rule_lock_seconds: int = DEFAULT_RULE_LOCK_SECONDS

    resend_api_key: str = field(default="", repr=False)
    email_to: str = ""
    email_from: str = "Alert Bot <onboarding@resend.dev>"

    app_password: str = field(default="", repr=False)
    streamlit_app_url: str = ""
    default_language: str = "de"
    default_check_interval_minutes: int = DEFAULT_CHECK_INTERVAL_MINUTES
    default_cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES
    prompts: PromptSettings = field(default_factory=PromptSettings)

    @property
    def has_turso_configured(self) -> bool:
        """True, sobald echte Turso-Zugangsdaten gesetzt sind."""
        return bool(
            self.turso_url
            and self.turso_auth_token
            and not self.turso_auth_token.startswith("your_")
            and "deine-datenbank" not in self.turso_url
        )

    @property
    def has_gemini_configured(self) -> bool:
        """True, sobald ein echter Gemini API Key gesetzt ist."""
        return bool(self.gemini_api_key and not self.gemini_api_key.startswith("your_"))

    @property
    def has_github_worker_configured(self) -> bool:
        """True, sobald GitHub Actions REST-Dispatch verfügbar ist."""
        return bool(
            self.github_token
            and not self.github_token.startswith("your_")
            and "/" in self.github_repo
        )

    @property
    def has_email_configured(self) -> bool:
        """True, sobald ein gültiger Resend API Key gesetzt ist."""
        return bool(self.resend_api_key and not self.resend_api_key.startswith("your_"))


def _build_prompt_settings(yaml_cfg: dict[str, Any]) -> PromptSettings:
    raw_prompts = yaml_cfg.get("prompts")
    data = raw_prompts if isinstance(raw_prompts, dict) else {}
    return PromptSettings(
        compiler_system_prompt=str(data.get("compiler_system_prompt", "")).strip(),
        web_search_system_prompt=str(data.get("web_search_system_prompt", "")).strip(),
        metric_extract_system_prompt=str(data.get("metric_extract_system_prompt", "")).strip(),
        page_change_system_prompt=str(data.get("page_change_system_prompt", "")).strip(),
    )


def load_app_settings(yaml_path: Path | None = None) -> AppSettings:
    """Lädt und validiert `AppSettings` aus ENV, Streamlit Secrets und YAML."""
    yaml_cfg = _load_yaml_defaults(yaml_path)
    turso_url = get_secret_or_env("TURSO_URL") or get_secret_or_env("TURSO_DATABASE_URL")
    turso_key = get_secret_or_env("TURSO_KEY") or get_secret_or_env("TURSO_AUTH_TOKEN")
    raw_models = yaml_cfg.get("allowed_models")
    models = (
        tuple(str(m).strip() for m in raw_models if str(m).strip())
        if isinstance(raw_models, list) and raw_models
        else CANDIDATE_MODELS
    )

    return AppSettings(
        gemini_api_key=get_secret_or_env("GEMINI_API_KEY"),
        gemini_fast_model=get_secret_or_env("GEMINI_MODEL", DEFAULT_FAST_MODEL),
        gemini_deep_model=get_secret_or_env("GEMINI_DEEP_MODEL", DEFAULT_DEEP_MODEL),
        allowed_models=models,
        turso_url=turso_url,
        turso_auth_token=turso_key,
        sqlite_path=get_secret_or_env("SQLITE_PATH", DEFAULT_SQLITE_REL_PATH),
        worker_mode=_parse_enum(WorkerMode, get_secret_or_env("WORKER_MODE", "auto"), WorkerMode.AUTO),
        github_token=get_secret_or_env("GITHUB_TOKEN"),
        github_repo=get_secret_or_env("GITHUB_REPO", "hschenke/alert-bot"),
        github_branch=get_secret_or_env("GITHUB_BRANCH", "main"),
        github_workflow_file=get_secret_or_env("GITHUB_WORKFLOW_FILE", "check_alerts.yaml"),
        local_scheduler_poll_seconds=int(yaml_cfg.get("local_scheduler_poll_seconds", DEFAULT_LOCAL_POLL_SECONDS)),
        max_concurrent_local_checks=int(yaml_cfg.get("max_concurrent_local_checks", DEFAULT_MAX_LOCAL_WORKERS)),
        rule_lock_seconds=int(yaml_cfg.get("rule_lock_seconds", DEFAULT_RULE_LOCK_SECONDS)),
        resend_api_key=get_secret_or_env("RESEND_API_KEY"),
        email_to=get_secret_or_env("EMAIL_TO"),
        email_from=get_secret_or_env("EMAIL_FROM", "Alert Bot <onboarding@resend.dev>"),
        app_password=get_secret_or_env("APP_PASSWORD"),
        streamlit_app_url=get_secret_or_env("STREAMLIT_APP_URL").rstrip("/"),
        default_language=str(yaml_cfg.get("default_language", "de")),
        default_check_interval_minutes=int(yaml_cfg.get("default_check_interval_minutes", DEFAULT_CHECK_INTERVAL_MINUTES)),
        default_cooldown_minutes=int(yaml_cfg.get("default_cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)),
        prompts=_build_prompt_settings(yaml_cfg),
    )
