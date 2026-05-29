"""Clientes HTTP para Claude, Gemini y Groq con dispatcher unificado."""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from saas_radar import config

logger = logging.getLogger(__name__)

# URL base de cada API — hardcodeadas, no dependen de config en runtime
_CLAUDE_URL = "https://api.anthropic.com/v1/messages"
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _parse_json_payload(text: str) -> dict | None:
    """Extrae un dict JSON de texto que puede contener fences markdown.

    Tolera:
    - ```json\\n{...}\\n```
    - ```JSON\\n{...}\\n```
    - ```\\n{...}\\n```
    - {... } (JSON pelado, sin fences)

    Devuelve None si el texto no contiene JSON válido.
    """
    text = text.strip()
    if "```" in text:
        # Partir por las marcas de fence y quedarnos con el primer bloque que
        # empiece por '{' tras quitar la etiqueta de lenguaje (json / JSON / vacía).
        for part in text.split("```"):
            # re.sub quita la etiqueta de lenguaje al inicio de cada parte.
            # Usamos re.sub en vez de lstrip("json") porque lstrip actúa a nivel
            # de caracteres, lo que destruiría un JSON que empieza con j, s, o, n.
            cleaned = re.sub(r"^[Jj][Ss][Oo][Nn]\s*", "", part.strip()).strip()
            if cleaned.startswith("{"):
                text = cleaned
                break
        else:
            # Ningún bloque empezaba por '{': intentamos con el texto original
            pass

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def call_claude(
    prompt: str,
    max_tokens: int = 4096,
    model: str | None = None,
    max_retries: int = 3,
) -> dict | None:
    """Llama a Anthropic Messages API. Devuelve dict JSON o None en fallo definitivo."""
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY no configurada")
        return None

    model = model or config.CLAUDE_EXTRACTION_MODEL
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    }

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(_CLAUDE_URL, headers=headers, json=body)

            if response.status_code == 429:
                # Claude incluye el tiempo de espera en segundos en el header
                # 'retry-after'. Puede ser float ("2.5") o estar ausente.
                try:
                    wait = int(float(response.headers.get("retry-after", 30)))
                except (ValueError, TypeError):
                    wait = 30
                logger.warning("Claude rate limit (intento %d/%d). Esperando %ds...", attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                logger.warning("Claude error %d (intento %d/%d)", response.status_code, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(1)
                continue

            if response.status_code != 200:
                logger.error("Claude error %d: %s", response.status_code, response.text[:300])
                return None

            data = response.json()
            # Messages API devuelve content[0].text con el texto generado
            raw_text = data["content"][0]["text"].strip()
            return _parse_json_payload(raw_text)

        except (KeyError, IndexError) as e:
            logger.error("Claude respuesta inesperada: %s", e)
            return None
        except Exception as e:
            logger.error("Claude error inesperado (intento %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(1)

    logger.warning("Claude agotó %d retries. Devolviendo None.", max_retries)
    return None


def call_gemini(
    prompt: str,
    max_tokens: int = 4096,
    max_retries: int = 3,
) -> dict | None:
    """Llama a Google AI Studio (Gemini). Devuelve dict JSON o None en fallo definitivo."""
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY no configurada")
        return None

    model = config.GEMINI_MODEL
    url = f"{_GEMINI_BASE_URL}/{model}:generateContent?key={config.GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(url, headers=headers, json=body)

            if response.status_code == 429:
                # Gemini devuelve retryDelay dentro de error.details, p.ej. "31s"
                wait = None
                try:
                    err = response.json().get("error", {})
                    for det in err.get("details", []) or []:
                        rd = det.get("retryDelay", "")
                        if rd.endswith("s"):
                            # Convertimos "31s" → 31 y añadimos 1s de margen
                            wait = int(float(rd[:-1])) + 1
                            break
                except Exception:
                    pass
                if wait is None:
                    wait = 30 * (attempt + 1)
                logger.warning("Gemini rate limit (intento %d/%d). Esperando %ds...", attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                logger.warning("Gemini error %d (intento %d/%d)", response.status_code, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(1)
                continue

            if response.status_code != 200:
                logger.error("Gemini error %d: %s", response.status_code, response.text[:300])
                return None

            data = response.json()
            # Estructura de respuesta: candidates[0].content.parts[0].text
            candidates = data.get("candidates") or []
            if not candidates:
                logger.error("Gemini sin candidates: %s", str(data)[:300])
                return None
            parts = candidates[0].get("content", {}).get("parts") or []
            if not parts:
                finish = candidates[0].get("finishReason", "?")
                logger.error("Gemini sin parts (finishReason=%s)", finish)
                return None
            raw_text = parts[0].get("text", "").strip()
            return _parse_json_payload(raw_text)

        except (KeyError, IndexError) as e:
            logger.error("Gemini respuesta inesperada: %s", e)
            return None
        except Exception as e:
            logger.error("Gemini error inesperado (intento %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(1)

    logger.warning("Gemini agotó %d retries. Devolviendo None.", max_retries)
    return None


def call_groq(
    prompt: str,
    max_tokens: int = 4096,
    max_retries: int = 3,
) -> dict | None:
    """Llama a Groq (API OpenAI-compatible). Devuelve dict JSON o None en fallo definitivo."""
    if not config.GROQ_API_KEY:
        logger.error("GROQ_API_KEY no configurada")
        return None

    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(_GROQ_URL, headers=headers, json=body)

            if response.status_code == 429:
                # Groq incluye el tiempo en el mensaje de error:
                # "Please try again in 1.5s. ..."
                wait = None
                try:
                    msg = response.json().get("error", {}).get("message", "")
                    # Buscamos el patrón "Please try again in Xs" donde X puede ser float
                    match = re.search(r"Please try again in ([0-9.]+)s", msg)
                    if match:
                        wait = int(float(match.group(1))) + 1
                except Exception:
                    pass
                if wait is None:
                    wait = 60
                logger.warning("Groq rate limit (intento %d/%d). Esperando %ds...", attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                logger.warning("Groq error %d (intento %d/%d)", response.status_code, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(1)
                continue

            if response.status_code != 200:
                logger.error("Groq error %d: %s", response.status_code, response.text[:300])
                return None

            # Formato OpenAI: choices[0].message.content
            raw_text = response.json()["choices"][0]["message"]["content"].strip()
            return _parse_json_payload(raw_text)

        except (KeyError, IndexError) as e:
            logger.error("Groq respuesta inesperada: %s", e)
            return None
        except Exception as e:
            logger.error("Groq error inesperado (intento %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(1)

    logger.warning("Groq agotó %d retries. Devolviendo None.", max_retries)
    return None


def call_llm(
    prompt: str,
    max_tokens: int = 4096,
    phase: str = "extraction",
    max_retries: int = 3,
    provider: str = "claude",
) -> dict | None:
    """Dispatcher unificado para los 3 proveedores LLM.

    Args:
        prompt: Texto del prompt a enviar al LLM.
        max_tokens: Límite de tokens en la respuesta.
        phase: 'extraction' (usa CLAUDE_EXTRACTION_MODEL) o 'synthesis'
               (usa CLAUDE_SYNTHESIS_MODEL). Solo aplica a Claude.
        max_retries: Número máximo de intentos en caso de error.
        provider: 'claude', 'gemini' o 'groq'. NUNCA lee config.AI_PROVIDER —
                  el caller es responsable de pasar el valor correcto.

    Returns:
        dict con el JSON parseado, o None si el proveedor falla tras max_retries.
    """
    if provider == "claude":
        model = config.CLAUDE_SYNTHESIS_MODEL if phase == "synthesis" else config.CLAUDE_EXTRACTION_MODEL
        return call_claude(prompt, max_tokens=max_tokens, model=model, max_retries=max_retries)
    if provider == "gemini":
        return call_gemini(prompt, max_tokens=max_tokens, max_retries=max_retries)
    if provider == "groq":
        return call_groq(prompt, max_tokens=max_tokens, max_retries=max_retries)

    logger.error("Provider desconocido: %r. Usa 'claude', 'gemini' o 'groq'.", provider)
    return None
