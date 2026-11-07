"""Unit-Tests für SSRF-Schutz, Input-Sanitization, Path-Guard und HMAC-Authentifizierung."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.domain.enums import AuthRole
from src.domain.exceptions import InputValidationError, SSRFBlockedError
from src.security.auth import (
    LoginRateLimiter,
    generate_admin_token,
    generate_readonly_token,
    resolve_credential_role,
    verify_admin_token,
    verify_readonly_token,
)
from src.security.sanitization import (
    compute_item_fingerprint,
    escape_html,
    mask_email_for_log,
    resolve_safe_path,
    sanitize_display_url,
    validate_check_interval,
    validate_css_selector,
    validate_email_address,
    validate_json_path,
)
from src.security.ssrf_guard import is_public_ip_address, validate_safe_external_url


class TestSsrfGuard:
    def test_blocks_private_and_loopback_ips(self) -> None:
        assert not is_public_ip_address("127.0.0.1")
        assert not is_public_ip_address("::1")
        assert not is_public_ip_address("10.0.0.5")
        assert not is_public_ip_address("192.168.178.1")
        assert not is_public_ip_address("172.20.0.1")
        assert not is_public_ip_address("169.254.169.254")
        assert is_public_ip_address("93.184.216.34")

    def test_blocks_unsafe_schemes_credentials_and_hosts(self) -> None:
        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url("file:///etc/passwd")
        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url("http://user:secret@example.com/test")
        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url("http://localhost:8080/api")
        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url("https://metadata.google.internal/computeMetadata/v1/")
        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url("https://example.com:22/ssh")

    def test_blocks_dns_rebinding_to_internal_ip(self) -> None:
        def fake_rebind_resolver(host: str, port: int) -> list[str]:
            return ["93.184.216.34", "169.254.169.254"]

        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url("https://evil.example.com/feed", dns_resolver=fake_rebind_resolver)

    def test_allows_valid_public_https_url(self) -> None:
        url = validate_safe_external_url(
            "https://api.open-meteo.com/v1/forecast",
            dns_resolver=lambda _h, _p: ["93.184.216.34"],
        )
        assert url == "https://api.open-meteo.com/v1/forecast"


class TestSanitizationAndAuth:
    def test_email_and_selector_whitelists(self) -> None:
        assert validate_email_address("holger.test@example.de") == "holger.test@example.de"
        with pytest.raises(InputValidationError):
            validate_email_address("kein-at-zeichen")
        assert validate_css_selector("div.price > span#main") == "div.price > span#main"
        with pytest.raises(InputValidationError):
            validate_css_selector("div{background:url(javascript:alert(1))}")
        assert validate_json_path("current.temperature_2m") == "current.temperature_2m"
        with pytest.raises(InputValidationError):
            validate_json_path("data; DROP TABLE")

    def test_interval_bounds_and_path_traversal(self, tmp_path: Path) -> None:
        assert validate_check_interval(15) == 15
        with pytest.raises(InputValidationError):
            validate_check_interval(0)
        safe = resolve_safe_path(tmp_path, "data/alert.db")
        assert safe.is_relative_to(tmp_path.resolve())
        with pytest.raises(InputValidationError):
            resolve_safe_path(tmp_path, "../../outside.db")

    def test_url_escaping_and_masking(self) -> None:
        assert sanitize_display_url("javascript:alert(1)") == "#"
        assert sanitize_display_url("https://immoscout24.de/expose/1") == "https://immoscout24.de/expose/1"
        assert "&lt;script&gt;" in escape_html("<script>alert(1)</script>")
        assert mask_email_for_log("holger@example.de") == "h***@example.de"
        assert compute_item_fingerprint("https://a.de/1/", "Titel") == compute_item_fingerprint("https://a.de/1", "Andere")

    def test_hmac_tokens_and_rate_limiter(self) -> None:
        secret = "SuperSicher!2026"
        adm = generate_admin_token(secret)
        ro = generate_readonly_token(secret)
        assert verify_admin_token(adm, secret)
        assert not verify_admin_token(adm, "Falsch")
        assert verify_readonly_token(ro, secret)
        assert resolve_credential_role(adm, secret) == AuthRole.ADMIN
        assert resolve_credential_role(ro, secret) == AuthRole.READONLY
        assert resolve_credential_role("wrong", secret) == AuthRole.GUEST

        limiter = LoginRateLimiter(max_attempts=2, lockout_seconds=60)
        limiter.record_failure("ip1")
        assert not limiter.is_locked_out("ip1")[0]
        limiter.record_failure("ip1")
        locked, remaining = limiter.is_locked_out("ip1")
        assert locked and remaining > 0
        limiter.reset("ip1")
        assert not limiter.is_locked_out("ip1")[0]
