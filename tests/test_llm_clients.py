"""Tests para src/saas_radar/analysis/llm_clients.py."""

from __future__ import annotations

import logging
from unittest.mock import patch

import httpx
import respx

from saas_radar import config
from saas_radar.analysis.llm_clients import (
    _parse_json_payload,
    call_groq,
    call_llm,
)

# ── Fixtures de respuesta ────────────────────────────────────────────────────

GROQ_OK_BODY = {
    "choices": [{"message": {"content": '{"result": "ok"}'}}]
}

# ── Tests de _parse_json_payload ─────────────────────────────────────────────


def test_parse_json_payload_fence_json_lowercase():
    """Tolera fence ```json con etiqueta en minúsculas."""
    result = _parse_json_payload('```json\n{"a": 1}\n```')
    assert result == {"a": 1}


def test_parse_json_payload_fence_json_uppercase():
    """Tolera fence ```JSON con etiqueta en mayúsculas."""
    result = _parse_json_payload('```JSON\n{"b": 2}\n```')
    assert result == {"b": 2}


def test_parse_json_payload_fence_no_lang():
    """Tolera fence ``` sin etiqueta de lenguaje."""
    result = _parse_json_payload('```\n{"c": 3}\n```')
    assert result == {"c": 3}


def test_parse_json_payload_bare_json():
    """Tolera JSON pelado sin fences."""
    result = _parse_json_payload('{"d": 4}')
    assert result == {"d": 4}


def test_parse_json_payload_invalid_returns_none():
    """Devuelve None para JSON inválido."""
    result = _parse_json_payload("esto no es json")
    assert result is None


def test_parse_json_payload_empty_fence_no_json():
    """Devuelve None si los fences no contienen JSON válido."""
    result = _parse_json_payload("```\nhola mundo\n```")
    assert result is None


# ── Tests de call_groq ───────────────────────────────────────────────────────


@respx.mock
def test_call_groq_200_ok():
    """200 OK: parsea choices[0].message.content."""
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GROQ_OK_BODY)
    )
    with patch.object(config, "GROQ_API_KEY", "test-key"):
        result = call_groq("prompt de prueba", max_retries=1)
    assert result == {"result": "ok"}


@respx.mock
def test_call_groq_429_retry_text_sleeps_and_retries():
    """429 con 'Please try again in Xs': hace sleep y reintenta."""
    error_body = {
        "error": {"message": "Rate limit exceeded. Please try again in 1s. Retry after: 2026-01-01"}
    }
    route = respx.post("https://api.groq.com/openai/v1/chat/completions")
    route.side_effect = [
        httpx.Response(429, json=error_body),
        httpx.Response(200, json=GROQ_OK_BODY),
    ]
    with patch.object(config, "GROQ_API_KEY", "test-key"):
        with patch("time.sleep") as mock_sleep:
            result = call_groq("prompt", max_retries=3)
    assert result == {"result": "ok"}
    # 1s del mensaje + 1s de margen = 2s
    mock_sleep.assert_called_once_with(2)


@respx.mock
def test_call_groq_500_exhausts_retries():
    """500 repetido → devuelve None."""
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="Internal Error")
    )
    with patch.object(config, "GROQ_API_KEY", "test-key"):
        with patch("time.sleep"):
            result = call_groq("prompt", max_retries=3)
    assert result is None


# ── Tests de call_llm (dispatcher) ───────────────────────────────────────────


@respx.mock
def test_call_llm_delegates_to_groq():
    """call_llm sin argumentos adicionales enruta a call_groq."""
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GROQ_OK_BODY)
    )
    with patch.object(config, "GROQ_API_KEY", "test-key"):
        result = call_llm("prompt", max_retries=1)
    assert result == {"result": "ok"}


@respx.mock
def test_call_llm_passes_max_tokens_to_groq():
    """call_llm transmite max_tokens al body de la request."""
    captured_body: dict = {}

    import json

    def capture_request(request):
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json=GROQ_OK_BODY)

    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=capture_request)

    with patch.object(config, "GROQ_API_KEY", "test-key"):
        result = call_llm("prompt", max_tokens=1024, max_retries=1)

    assert result == {"result": "ok"}
    assert captured_body.get("max_tokens") == 1024
