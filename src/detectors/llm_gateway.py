"""AI-Gateways (`ai_brain` & Google Gemini Interactions API Fallback) für Alert-Bot."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Protocol, TypeVar

import ai_brain
from ai_brain import BrainError
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
        typsicher in `schema_cls`. Nutzt primär die kostenfreie Multi-Engine-Suche (DuckDuckGo/News),
        um 429-Quota-Limits des Google-Search-Groundings zu vermeiden.
        """
        grounded_text, citations = self._run_free_web_search(prompt)
        if not citations:
            try:
                grounded_text, citations = self._run_google_search(prompt, system_instruction)
            except Exception as exc:
                logger.info("Google-Search Fallback nicht verfügbar (%s).", exc)

        synthesis_prompt = _build_synthesis_prompt(prompt, grounded_text, citations)
        try:
            return self.generate_structured(
                prompt=synthesis_prompt,
                system_instruction=system_instruction,
                schema_cls=schema_cls,
            )
        except Exception as exc:
            return _fallback_grounded_structured(schema_cls, grounded_text, citations, exc)

    @staticmethod
    def _run_free_web_search(prompt: str) -> tuple[str, list[tuple[str, str]]]:
        from src.detectors.search_provider import FreeWebSearchProvider

        items = FreeWebSearchProvider().search(_extract_search_query(prompt))
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
        return _fallback_grounded_structured(schema_cls, text, citations, exc)

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
                err_str = str(exc).lower()
                logger.warning(
                    "Gemini Google-Search (%s, Versuch %d/%d) fehlgeschlagen: %s",
                    model_name,
                    attempt + 1,
                    LLM_MAX_ATTEMPTS,
                    exc,
                )
                if "429" in err_str or "quota" in err_str:
                    break
                if attempt + 1 < LLM_MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_BASE_SECONDS * (attempt + 1))

        raise LLMProviderError(f"KI-Websuche fehlgeschlagen: {last_exc}")


def _extract_search_query(prompt: str) -> str:
    """Extrahiert eine prägnante Suchanfrage aus strukturierten Prüf-Prompts."""
    for line in prompt.splitlines():
        if any(k in line for k in ("Suchfokus:", "Suchanfrage:", "Kennzahl / Asset:", "Überwachungsauftrag:")):
            extracted = line.split(":", 1)[-1].strip()
            if extracted:
                return extracted
    return prompt[:120].strip()


def _build_synthesis_prompt(
    prompt: str,
    grounded_text: str,
    citations: list[tuple[str, str]],
) -> str:
    """Formt den Synthese-Prompt mit verifizierten Web-Ergebnissen und Quell-URLs."""
    citation_lines = "\n".join(f"- {title or 'Quelle'}: {url}" for title, url in citations[:12])
    return (
        f"Ursprünglicher Prüfauftrag:\n{prompt}\n\n"
        f"Aktuelle Web-Recherche-Ergebnisse:\n{grounded_text}\n\n"
        f"Verifizierte Quell-URLs:\n{citation_lines or '(keine separaten URLs)'}\n\n"
        "Überführe dieses Ergebnis jetzt wahrheitsgetreu in das geforderte JSON-Schema. "
        "Verwende ausschließlich tatsächlich gefundene Fakten und echte URLs aus obiger Recherche."
    )


def _format_brain_citations(raw_citations: Any) -> tuple[str, list[tuple[str, str]]]:
    """Normalisiert `BrainCitation`-Einträge zu bereinigtem Kontexttext und `(title, url)`-Liste."""
    citations: list[tuple[str, str]] = []
    text_lines: list[str] = []
    seen_urls: set[str] = set()
    for item in raw_citations or []:
        safe_url = sanitize_display_url(str(getattr(item, "url", "") or ""))
        url = "" if safe_url == "#" else safe_url
        if url and url in seen_urls:
            continue
        title = str(getattr(item, "title", "") or "").strip()[:160]
        snippet = str(getattr(item, "snippet", "") or "").strip()[:280]
        if url:
            seen_urls.add(url)
            citations.append((title or url, url))
        if title or snippet or url:
            label = title or url or "Quelle"
            body = f": {snippet}" if snippet else ""
            suffix = f" ({url})" if url else ""
            text_lines.append(f"- {label}{body}{suffix}")
    if not text_lines:
        return "Keine aktuellen Web-Treffer ermittelbar.", []
    return "\n".join(text_lines), citations


def _fallback_grounded_structured(
    schema_cls: type[SchemaT],
    text: str,
    citations: list[tuple[str, str]],
    exc: Exception,
) -> SchemaT:
    """Erstellt bei fehlgeschlagener KI-Synthese ein sicheres Fallback-Ergebnis für bekannte Schemata."""
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


class BrainAlertGateway:
    """Zentrales AI-Gateway auf Basis von `ai_brain` (`AI_BRAIN_KEY`, dynamisches `tier="auto"`-Routing)."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        fallback_gateway: AlertLLMGateway | None = None,
    ) -> None:
        self._settings = settings
        self._fallback = fallback_gateway

    def _resolve_fallback(self) -> AlertLLMGateway | None:
        if self._fallback is not None:
            return self._fallback
        if self._settings is not None and self._settings.has_gemini_configured:
            self._fallback = GeminiAlertGateway(self._settings)
            return self._fallback
        return None

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        """Erzeugt validierten JSON-Structured-Output über `ai_brain` (`tier="auto"`, App-Policy)."""
        try:
            with ai_brain.connect() as brain:
                return brain.generate_structured(
                    prompt,
                    schema_cls,
                    tier="auto",
                    system_instruction=system_instruction,
                    allow_paid=None,
                )[0]
        except BrainError as exc:
            logger.warning("AI-Brain Struktur-Aufruf fehlgeschlagen: %s", exc)
            fallback = self._resolve_fallback()
            if fallback is not None:
                logger.info("Nutze Gemini-Fallback für Strukturanalyse nach AI-Brain-Fehler.")
                return fallback.generate_structured(prompt, system_instruction, schema_cls)
            raise LLMProviderError(f"AI-Brain Strukturanalyse fehlgeschlagen: {exc}") from exc
        except Exception as exc:
            logger.warning("AI-Brain Struktur-Aufruf fehlgeschlagen: %s", exc)
            raise LLMProviderError(f"AI-Brain Strukturanalyse fehlgeschlagen: {exc}") from exc

    def _synthesize_grounded(
        self,
        brain: Any,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
        grounded_text: str,
        citations: list[tuple[str, str]],
    ) -> SchemaT:
        synthesis_prompt = _build_synthesis_prompt(prompt, grounded_text, citations)
        try:
            return brain.generate_structured(
                synthesis_prompt,
                schema_cls,
                tier="auto",
                system_instruction=system_instruction,
                allow_paid=None,
            )[0]
        except BrainError as exc:
            fallback = self._resolve_fallback()
            if fallback is not None:
                logger.info("Nutze Gemini-Fallback für AI-Brain-Synthese nach Fehler: %s", exc)
                try:
                    return fallback.generate_structured(synthesis_prompt, system_instruction, schema_cls)
                except Exception as fb_exc:
                    return _fallback_grounded_structured(schema_cls, grounded_text, citations, fb_exc)
            return _fallback_grounded_structured(schema_cls, grounded_text, citations, exc)
        except Exception as exc:
            return _fallback_grounded_structured(schema_cls, grounded_text, citations, exc)

    def generate_grounded_structured(
        self,
        prompt: str,
        system_instruction: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        """Führt Websuche + Synthese über `ai_brain` (`tier="auto"`, App-Policy) aus."""
        query = _extract_search_query(prompt)
        grounded_text = "Keine aktuellen Web-Treffer ermittelbar."
        citations: list[tuple[str, str]] = []
        try:
            with ai_brain.connect() as brain:
                try:
                    search_res = brain.search(query, tier="auto", allow_paid=None)
                    grounded_text, citations = _format_brain_citations(search_res.citations)
                except Exception as exc:
                    logger.warning("AI-Brain Websuche fehlgeschlagen: %s", exc)
                return self._synthesize_grounded(
                    brain, prompt, system_instruction, schema_cls, grounded_text, citations
                )
        except LLMProviderError:
            raise
        except BrainError as exc:
            logger.warning("AI-Brain Verbindung fehlgeschlagen: %s", exc)
            fallback = self._resolve_fallback()
            if fallback is not None:
                logger.info("Nutze Gemini-Fallback für Grounded-Websuche nach AI-Brain-Fehler.")
                return fallback.generate_grounded_structured(prompt, system_instruction, schema_cls)
            raise LLMProviderError(f"AI-Brain Websuche fehlgeschlagen: {exc}") from exc
        except Exception as exc:
            logger.warning("AI-Brain Verbindung fehlgeschlagen: %s", exc)
            raise LLMProviderError(f"AI-Brain Websuche fehlgeschlagen: {exc}") from exc


def create_alert_llm_gateway(settings: AppSettings) -> AlertLLMGateway | None:
    """Liefert `BrainAlertGateway` bei aktivem `AI_BRAIN_KEY`, sonst Gemini-Fallback oder `None`."""
    if ai_brain.is_configured():
        return BrainAlertGateway(settings=settings)
    if settings.has_gemini_configured:
        return GeminiAlertGateway(settings)
    return None
