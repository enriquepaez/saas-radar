"""Cliente HTTP para Groq con dispatcher unificado."""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from saas_radar import config

logger = logging.getLogger(__name__)

# URL base de la API — hardcodeada, no depende de config en runtime
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
    max_retries: int = 3,
) -> dict | None:
    """Dispatcher unificado — único proveedor: Groq.

    Args:
        prompt: Texto del prompt a enviar al LLM.
        max_tokens: Límite de tokens en la respuesta.
        max_retries: Número máximo de intentos en caso de error.

    Returns:
        dict con el JSON parseado, o None si Groq falla tras max_retries.
    """
    return call_groq(prompt, max_tokens=max_tokens, max_retries=max_retries)
