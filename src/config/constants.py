"""Zentrale, benannte Konstanten für Timeouts, Grenzwerte, Modelle und Sicherheit."""

from __future__ import annotations

# Gemini KI-Modelle (aktuelle Generation gem. SDK >= 2.30.1)
DEFAULT_FAST_MODEL: str = "gemini-3.5-flash-lite"
DEFAULT_DEEP_MODEL: str = "gemini-3.8-flash"
CANDIDATE_MODELS: tuple[str, ...] = (
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-4-argon",
)

# Zeitlimits & Netzwerk-Resilienz
HTTP_CONNECT_TIMEOUT_SECONDS: float = 5.0
HTTP_READ_TIMEOUT_SECONDS: float = 15.0
HTTP_TIMEOUT_TUPLE: tuple[float, float] = (HTTP_CONNECT_TIMEOUT_SECONDS, HTTP_READ_TIMEOUT_SECONDS)
MAX_HTTP_REDIRECTS: int = 5
MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024  # 2 MB Streaming-Obergrenze gegen Memory-DoS
MAX_EXTRACTED_TEXT_CHARS: int = 12_000

TURSO_HTTP_TIMEOUT_SECONDS: float = 12.0
TURSO_BATCH_SIZE: int = 20
DEFAULT_HTTP_RETRIES: int = 2
RETRY_BACKOFF_BASE_SECONDS: float = 0.8

LLM_TIMEOUT_SECONDS: float = 45.0
LLM_MAX_ATTEMPTS: int = 3

EMAIL_HTTP_TIMEOUT_SECONDS: float = 15.0
GITHUB_API_TIMEOUT_SECONDS: float = 12.0

# SSRF-Whitelist
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
ALLOWED_HTTP_PORTS: frozenset[int] = frozenset({80, 443, 8080, 8443})

# Fachliche Grenzwerte & Intervalle
MIN_CHECK_INTERVAL_MINUTES: int = 1
MAX_CHECK_INTERVAL_MINUTES: int = 1440
DEFAULT_CHECK_INTERVAL_MINUTES: int = 30
DEFAULT_COOLDOWN_MINUTES: int = 180
INTERVAL_PRESETS_MINUTES: tuple[int, ...] = (1, 5, 15, 30, 60, 180, 360, 720, 1440)

MAX_TITLE_LENGTH: int = 120
MAX_PROMPT_LENGTH: int = 2000
MAX_QUERY_LENGTH: int = 400
MAX_SELECTOR_LENGTH: int = 160
MAX_JSON_PATH_LENGTH: int = 160
MAX_DISCOVERED_ITEMS_PER_CHECK: int = 10

# Persistenz & Worker
DEFAULT_SQLITE_REL_PATH: str = "data/alert_bot.db"
DEFAULT_RULE_LOCK_SECONDS: int = 300
DEFAULT_LOCAL_POLL_SECONDS: int = 30
DEFAULT_MAX_LOCAL_WORKERS: int = 2
MAX_CONSECUTIVE_FAILURES_BEFORE_ERROR: int = 5

# Auth & Brute-Force-Schutz
ADMIN_TOKEN_MAX_AGE_SECONDS: int = 86_400
CLOCK_SKEW_TOLERANCE_SECONDS: int = 120
MAX_FAILED_LOGIN_ATTEMPTS: int = 5
LOGIN_LOCKOUT_SECONDS: int = 300
