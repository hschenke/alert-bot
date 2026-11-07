"""Input-Whitelist-Validierung, HTML-Escaping, Pfadschutz und Log-Maskierung."""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from urllib.parse import urlparse

from src.config.constants import (
    ALLOWED_URL_SCHEMES,
    MAX_CHECK_INTERVAL_MINUTES,
    MAX_JSON_PATH_LENGTH,
    MAX_PROMPT_LENGTH,
    MAX_QUERY_LENGTH,
    MAX_SELECTOR_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_CHECK_INTERVAL_MINUTES,
)
from src.domain.exceptions import InputValidationError

_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_SAFE_JSON_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_.\[\]-]{0,160}$")
_SAFE_SELECTOR_PATTERN = re.compile(r"^[A-Za-z0-9_#.,:\s>\+\~\[\]='\"-]{0,160}$")
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_chars(text: str) -> str:
    """Entfernt unsichtbare Steuerzeichen aus Benutzereingaben."""
    return _CONTROL_CHARS_PATTERN.sub("", text or "").strip()


def validate_rule_title(raw_title: str) -> str:
    """Prüft den Titel einer Alert-Regel auf Länge und Steuerzeichen."""
    cleaned = strip_control_chars(raw_title)
    if len(cleaned) < 2:
        raise InputValidationError("Bitte einen aussagekräftigen Titel (mind. 2 Zeichen) angeben.")
    return cleaned[:MAX_TITLE_LENGTH]


def validate_user_prompt(raw_prompt: str) -> str:
    """Validiert den Überwachungsauftrag / Freitext des Benutzers."""
    cleaned = strip_control_chars(raw_prompt)
    if len(cleaned) < 4:
        raise InputValidationError("Bitte beschreibe kurz, was überwacht werden soll.")
    if len(cleaned) > MAX_PROMPT_LENGTH:
        raise InputValidationError(f"Beschreibung ist zu lang (max. {MAX_PROMPT_LENGTH} Zeichen).")
    return cleaned


def validate_search_query(raw_query: str) -> str:
    """Bereinigt optionale Suchbegriffe auf zulässige Länge."""
    return strip_control_chars(raw_query)[:MAX_QUERY_LENGTH]


def validate_check_interval(minutes: int) -> int:
    """Stellt sicher, dass das Prüf-Intervall im zulässigen Bereich (1..1440 Min.) liegt."""
    if minutes < MIN_CHECK_INTERVAL_MINUTES or minutes > MAX_CHECK_INTERVAL_MINUTES:
        raise InputValidationError(
            f"Prüf-Intervall muss zwischen {MIN_CHECK_INTERVAL_MINUTES} und {MAX_CHECK_INTERVAL_MINUTES} Minuten liegen."
        )
    return int(minutes)


def validate_email_address(raw_email: str) -> str:
    """Validiert eine E-Mail-Adresse per strikter Whitelist ohne ReDoS-Risiko."""
    cleaned = strip_control_chars(raw_email)
    if not cleaned or len(cleaned) > 254 or not _EMAIL_PATTERN.fullmatch(cleaned):
        raise InputValidationError("Ungültige E-Mail-Adresse.")
    return cleaned


def validate_optional_email(raw_email: str) -> str:
    """Validiert eine optionale E-Mail-Adresse (leer erlaubt)."""
    cleaned = strip_control_chars(raw_email)
    return validate_email_address(cleaned) if cleaned else ""


def validate_css_selector(raw_selector: str) -> str:
    """Validiert einen optionalen CSS-Selektor per Zeichen-Whitelist."""
    cleaned = strip_control_chars(raw_selector)
    if not cleaned:
        return ""
    if len(cleaned) > MAX_SELECTOR_LENGTH or not _SAFE_SELECTOR_PATTERN.fullmatch(cleaned):
        raise InputValidationError("CSS-Selektor enthält nicht erlaubte Sonderzeichen.")
    return cleaned


def validate_json_path(raw_path: str) -> str:
    """Validiert einen optionalen JSON-Punktpfad (z. B. `current.temperature_2m`)."""
    cleaned = strip_control_chars(raw_path)
    if not cleaned:
        return ""
    if len(cleaned) > MAX_JSON_PATH_LENGTH or not _SAFE_JSON_PATH_PATTERN.fullmatch(cleaned):
        raise InputValidationError("JSON-Pfad darf nur Buchstaben, Ziffern, Punkte und Klammern enthalten.")
    return cleaned


def sanitize_display_url(raw_url: str) -> str:
    """Lässt für anklickbare Links in UI und HTML-Mails ausschließlich `http`/`https` zu."""
    cleaned = strip_control_chars(raw_url)
    if not cleaned:
        return "#"
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        return "#"
    return cleaned


def escape_html(raw_text: str) -> str:
    """Maskiert HTML-Sonderzeichen zuverlässig gegen Cross-Site-Scripting (XSS)."""
    return html.escape(raw_text or "", quote=True)


def mask_email_for_log(email: str) -> str:
    """Maskiert E-Mail-Adressen DSGVO-konform für Logs (`h***@domain.de`)."""
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    prefix = local[0] if local else "*"
    return f"{prefix}***@{domain}"


def resolve_safe_path(base_dir: Path, relative_path: str) -> Path:
    """Schützt lokale Dateipfade strikt gegen Path-Traversal (`../`)."""
    resolved_base = base_dir.resolve()
    target = (resolved_base / relative_path).resolve()
    if not target.is_relative_to(resolved_base):
        raise InputValidationError(f"Unzulässiger Dateipfad außerhalb des Projektordners: '{relative_path}'")
    return target


def compute_item_fingerprint(url: str, title: str) -> str:
    """Berechnet einen deterministischen SHA-256-Fingerprint zur Treffer-Deduplizierung."""
    basis = url.strip().lower().rstrip("/") if url.strip() else title.strip().lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
