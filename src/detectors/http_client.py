"""SSRF-geschützter HTTP-Client mit Redirect-Hop-Prüfung, 2-MB-Streaming-Limit & HTML-Parser."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.config.constants import (
    HTTP_TIMEOUT_TUPLE,
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_HTTP_REDIRECTS,
    MAX_RESPONSE_BYTES,
)
from src.domain.exceptions import DetectorExecutionError, SSRFBlockedError
from src.security.sanitization import validate_css_selector
from src.security.ssrf_guard import DnsResolver, validate_safe_external_url

_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})
_NOISE_TAGS: tuple[str, ...] = ("script", "style", "noscript", "svg", "iframe")


@dataclass(frozen=True)
class SafeHttpResponse:
    """Ergebnis eines geprüften, größenbegrenzten HTTP-Abrufs."""

    final_url: str
    status_code: int
    content_type: str
    text: str
    elapsed_ms: int


class SafeHttpFetcher:
    """Führt HTTP-GET-Anfragen mit striktem SSRF-Schutz auf jedem Redirect-Hop aus."""

    def __init__(
        self,
        timeout: tuple[float, float] = HTTP_TIMEOUT_TUPLE,
        max_bytes: int = MAX_RESPONSE_BYTES,
        dns_resolver: DnsResolver | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._dns_resolver = dns_resolver
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; AlertBot/0.1; +https://github.com/hschenke/alert-bot)",
                "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            }
        )

    def _validate(self, url: str) -> str:
        if self._dns_resolver is not None:
            return validate_safe_external_url(url, dns_resolver=self._dns_resolver)
        return validate_safe_external_url(url)

    def fetch(self, url: str) -> SafeHttpResponse:
        """Ruft `url` sicher ab und validiert etwaige HTTP-Weiterleitungen Schritt für Schritt."""
        current_url = self._validate(url)
        started = time.monotonic()

        for _hop in range(MAX_HTTP_REDIRECTS + 1):
            try:
                response = self._session.get(
                    current_url,
                    timeout=self._timeout,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise DetectorExecutionError(f"HTTP-Abruf fehlgeschlagen: {exc}") from exc

            try:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("Location", "").strip()
                    if not location:
                        raise DetectorExecutionError("HTTP-Redirect ohne Zieladresse erhalten.")
                    current_url = self._validate(urljoin(current_url, location))
                    continue

                body_bytes = self._read_bounded_body(response)
                encoding = response.encoding or "utf-8"
                decoded = body_bytes.decode(encoding, errors="replace")
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return SafeHttpResponse(
                    final_url=current_url,
                    status_code=response.status_code,
                    content_type=response.headers.get("Content-Type", ""),
                    text=decoded,
                    elapsed_ms=elapsed_ms,
                )
            finally:
                response.close()

        raise SSRFBlockedError(f"Zu viele HTTP-Weiterleitungen (> {MAX_HTTP_REDIRECTS}).")

    def _read_bounded_body(self, response: requests.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            total += len(chunk)
            if total > self._max_bytes:
                break
            chunks.append(chunk)
        return b"".join(chunks)


def extract_clean_html_text(raw_html: str, css_selector: str = "") -> str:
    """Extrahiert bereinigten sichtbaren Text aus HTML (optional begrenzt auf CSS-Selektor)."""
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    clean_sel = validate_css_selector(css_selector)
    if clean_sel:
        selected = soup.select(clean_sel)
        if not selected:
            return ""
        raw_text = "\n".join(node.get_text(" ", strip=True) for node in selected)
    else:
        raw_text = soup.get_text("\n", strip=True)

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return "\n".join(lines)[:MAX_EXTRACTED_TEXT_CHARS]
