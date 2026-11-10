"""Moderner Google Gemini Interactions API Client (`google-genai` >= 2.30.1) für Alert-Bot."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Protocol, TypeVar

from google import genai
from pydantic import BaseModel

from src.config.constants import (
    CANDIDATE_MODELS,
    LLM_MAX_ATTEMPTS,
    LLM_TIMEOUT_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
)
from src.config.settings import AppSettings
from src.domain.exceptions import LLMProviderError
from src.security.sanitization import sanitize_display_url

logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class AlertLLMGateway(Protocol):
    """Abstrakte KI-Schnittstelle für strukturierte Analysen und Grounded Web Search."""

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT: ...

    def generate_grounded_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT: ...


def _extract_interaction_text(interaction: Any) -> str:
    """Liest `output_text` oder verkettet Text-Blöcke aus `model_output`-Steps."""
    direct = str(getattr(interaction, "output_text", "") or "").strip()
    if direct:
        return direct

    parts: list[str] = []
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for block in getattr(step, "content", None) or []:
            if getattr(block, "type", None) == "text":
                txt = str(getattr(block, "text", "") or "").strip()
                if txt:
                    parts.append(txt)
    return "\n\n".join(parts).strip()


def _extract_verified_citations(interaction: Any) -> list[tuple[str, str]]:
    """Extrahiert verifizierte `(title, url)`-Quellen aus Grounding-Annotationen."""
    found: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for block in getattr(step, "content", None) or []:
            if getattr(block, "type", None) != "text":
                continue
            for ann in getattr(block, "annotations", None) or []:
                if getattr(ann, "type", None) != "url_citation":
                    continue
                safe_url = sanitize_display_url(str(getattr(ann, "url", "") or ""))
                if safe_url == "#" or safe_url in seen_urls:
                    continue
                seen_urls.add(safe_url)
                title = str(getattr(ann, "title", "") or "").strip()
                found.append((title[:160], safe_url))
    return found


def _strip_json_fences(raw_text: str) -> str:
    return _JSON_FENCE_RE.sub("", (raw_text or "").strip()).strip()


class GeminiAlertGateway:
    """Resilienter Gemini-Client mit Modell-Fallback-Kette und verifiziertem Google-Search-Grounding."""

    def __init__(
        self,
        settings: AppSettings,
        client: genai.Client | None = None,
    ) -> None:
        if not settings.has_gemini_configured:
            raise LLMProviderError(
                "Kein gültiger GEMINI_API_KEY konfiguriert. Bitte in .env oder Secrets hinterlegen."
            )
        self._settings = settings
        self._client = client or self._create_client(settings.gemini_api_key)
        self._models = self._build_model_chain(settings)

    @staticmethod
    def _create_client(api_key: str) -> genai.Client:
        http_opts = {"timeout": int(LLM_TIMEOUT_SECONDS * 1000), "retry_options": {"attempts": 1}}
        try:
            return genai.Client(api_key=api_key, http_options=http_opts)
        except TypeError:
            return genai.Client(api_key=api_key)

    @staticmethod
    def _build_model_chain(settings: AppSettings) -> list[str]:
        candidates = [settings.gemini_fast_model, settings.gemini_deep_model, *settings.allowed_models]
        chain: list[str] = []
        for model in candidates:
            clean = model.strip()
            if clean and clean not in chain:
                chain.append(clean)
        return chain or list(CANDIDATE_MODELS)

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        """Erzeugt validierten JSON-Structured-Output gemäß `schema_cls`."""
        last_exc: Exception | None = None
        for attempt in range(LLM_MAX_ATTEMPTS):
            model_name = self._models[attempt % len(self._models)]
            try:
                interaction = self._client.interactions.create(
                    model=model_name,
                    input=prompt,
                    system_instruction=system_instruction,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": schema_cls.model_json_schema(),
                    },
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                raw_json = _strip_json_fences(_extract_interaction_text(interaction))
                if not raw_json:
                    raise ValueError("Leere JSON-Antwort von Gemini erhalten.")
                return schema_cls.model_validate_json(raw_json)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Gemini Struktur-Aufruf (%s, Versuch %d/%d) fehlgeschlagen: %s",
                    model_name,
                    attempt + 1,
                    LLM_MAX_ATTEMPTS,
                    exc,
                )
                if attempt + 1 < LLM_MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_BASE_SECONDS * (attempt + 1))

        raise LLMProviderError(f"KI-Strukturanalyse fehlgeschlagen: {last_exc}")

    def generate_grounded_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        """
        Führt Web-Recherche durch und überführt Fakten + verifizierte Quell-URLs
        typsicher in `schema_cls`. Weicht bei Google-Quota-Limits (429) automatisch
        auf die kostenfreie DuckDuckGo/News-Suche aus.
        """
        try:
            grounded_text, citations = self._run_google_search(prompt, system_instruction)
        except Exception as exc:
            logger.info("Gemini Search-Grounding nicht verfügbar (%s), nutze FreeWebSearcher.", exc)
            grounded_text, citations = self._run_free_web_search(prompt)

        citation_lines = "\n".join(
            f"- {title or 'Quelle'}: {url}" for title, url in citations[:12]
        )
        synthesis_prompt = (
            f"Ursprünglicher Prüfauftrag:\n{prompt}\n\n"
            f"Aktuelle Web-Recherche-Ergebnisse:\n{grounded_text}\n\n"
            f"Verifizierte Quell-URLs:\n{citation_lines or '(keine separaten URLs)'}\n\n"
            "Überführe dieses Ergebnis jetzt wahrheitsgetreu in das geforderte JSON-Schema. "
            "Verwende ausschließlich tatsächlich gefundene Fakten und echte URLs aus obiger Recherche."
        )
        try:
            return self.generate_structured(
                prompt=synthesis_prompt,
                system_instruction=system_instruction,
                schema_cls=schema_cls,
            )
        except Exception as exc:
            return self._fallback_grounded_structured(schema_cls, grounded_text, citations, exc)

    @staticmethod
    def _run_free_web_search(prompt: str) -> tuple[str, list[tuple[str, str]]]:
        from src.detectors.search_provider import FreeWebSearchProvider

        query = ""
        for line in prompt.splitlines():
            if any(k in line for k in ("Suchfokus:", "Suchanfrage:", "Kennzahl / Asset:", "Überwachungsauftrag:")):
                query = line.split(":", 1)[-1].strip()
                break
        clean_q = query or prompt[:120].strip()
        items = FreeWebSearchProvider().search(clean_q)
        if not items:
            return "Keine aktuellen Web-Treffer ermittelbar.", []
        text_lines = [f"- {it.title}: {it.summary} ({it.url})" for it in items]
        citations = [(it.title, it.url) for it in items if it.url]
        return "\n".join(text_lines), citations

    @staticmethod
    def _fallback_grounded_structured(
        schema_cls: type[SchemaT],
        text: str,
        citations: list[tuple[str, str]],
        exc: Exception,
    ) -> SchemaT:
        name = schema_cls.__name__
        if "AiSearchEvaluation" in name:
            from src.domain.schemas import AiDiscoveredMatch

            matches = [AiDiscoveredMatch(title=t, url=u, summary=t) for t, u in citations[:5]]
            return schema_cls(condition_met=bool(matches), summary=f"{len(matches)} Treffer im Web gefunden.", matches=matches)  # type: ignore[return-value]
        if "AiMetricExtraction" in name:
            from src.detectors.comparators import parse_localized_float

            val = parse_localized_float(text)
            src_url = citations[0][1] if citations else ""
            return schema_cls(value_found=(val is not None), numeric_value=val, source_url=src_url)  # type: ignore[return-value]
        raise LLMProviderError(f"KI-Synthese fehlgeschlagen: {exc}") from exc

    def _run_google_search(
        self,
        prompt: str,
        system_instruction: str,
    ) -> tuple[str, list[tuple[str, str]]]:
        last_exc: Exception | None = None
        tools: list[dict[str, str]] = [{"type": "google_search"}]
        if "http://" in prompt or "https://" in prompt:
            tools.append({"type": "url_context"})

        for attempt in range(LLM_MAX_ATTEMPTS):
            model_name = self._models[attempt % len(self._models)]
            try:
                interaction = self._client.interactions.create(
                    model=model_name,
                    input=prompt,
                    system_instruction=system_instruction,
                    tools=tools,
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                text = _extract_interaction_text(interaction)
                if not text:
                    raise ValueError("Leere Suchantwort von Gemini erhalten.")
                return text, _extract_verified_citations(interaction)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Gemini Google-Search (%s, Versuch %d/%d) fehlgeschlagen: %s",
                    model_name,
                    attempt + 1,
                    LLM_MAX_ATTEMPTS,
                    exc,
                )
                if attempt + 1 < LLM_MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_BASE_SECONDS * (attempt + 1))

        raise LLMProviderError(f"KI-Websuche fehlgeschlagen: {last_exc}")
