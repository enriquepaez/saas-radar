"""Tests para src/saas_radar/analysis/llm_clients.py."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import respx

from saas_radar import config
from saas_radar.analysis.llm_clients import (
    _parse_json_payload,
    call_claude,
    call_gemini,
    call_groq,
    call_llm,
)

# ── Fixtures de respuesta ────────────────────────────────────────────────────

CLAUDE_OK_BODY = {
    "content": [{"text": '{"result": "ok"}'}],
}

GEMINI_OK_BODY = {
    "candidates": [
        {"content": {"parts": [{"text": '{"result": "ok"}'}]}, "finishReason": "STOP"}
    ]
}

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


# ── Tests de call_claude ─────────────────────────────────────────────────────


@respx.mock
def test_call_claude_200_ok():
    """200 OK: parsea y devuelve el dict correctamente."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=CLAUDE_OK_BODY)
    )
    with patch.object(config, "ANTHROPIC_API_KEY", "test-key"):
        result = call_claude("prompt de prueba", max_retries=1)
    assert result == {"result": "ok"}


@respx.mock
def test_call_claude_429_sleeps_and_retries():
    """429 con header retry-after: hace sleep y reintenta."""
    route = respx.post("https://api.anthropic.com/v1/messages")
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "1"}, json={}),
        httpx.Response(200, json=CLAUDE_OK_BODY),
    ]
    with patch.object(config, "ANTHROPIC_API_KEY", "test-key"):
        with patch("time.sleep") as mock_sleep:
            result = call_claude("prompt", max_retries=3)
    assert result == {"result": "ok"}
    # Verificar que sleep fue llamado con 1 segundo (el valor del header)
    mock_sleep.assert_called_once_with(1)


@respx.mock
def test_call_claude_500_exhausts_retries():
    """500 repetido max_retries veces → devuelve None."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with patch.object(config, "ANTHROPIC_API_KEY", "test-key"):
        with patch("time.sleep"):
            result = call_claude("prompt", max_retries=3)
    assert result is None


@respx.mock
def test_call_claude_no_api_key_returns_none():
    """Sin API key devuelve None sin hacer llamada HTTP."""
    with patch.object(config, "ANTHROPIC_API_KEY", ""):
        result = call_claude("prompt", max_retries=1)
    assert result is None


# ── Tests de call_gemini ─────────────────────────────────────────────────────


@respx.mock
def test_call_gemini_200_ok():
    """200 OK: parsea estructura candidates[0].content.parts[0].text."""
    respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(200, json=GEMINI_OK_BODY)
    )
    with patch.object(config, "GEMINI_API_KEY", "test-key"):
        result = call_gemini("prompt de prueba", max_retries=1)
    assert result == {"result": "ok"}


@respx.mock
def test_call_gemini_429_retry_delay_sleeps_and_retries():
    """429 con retryDelay en error.details: hace sleep y reintenta."""
    error_body = {
        "error": {
            "details": [{"retryDelay": "1s"}]
        }
    }
    route = respx.post(url__startswith="https://generativelanguage.googleapis.com")
    route.side_effect = [
        httpx.Response(429, json=error_body),
        httpx.Response(200, json=GEMINI_OK_BODY),
    ]
    with patch.object(config, "GEMINI_API_KEY", "test-key"):
        with patch("time.sleep") as mock_sleep:
            result = call_gemini("prompt", max_retries=3)
    assert result == {"result": "ok"}
    # 1s del retryDelay + 1s de margen = 2s
    mock_sleep.assert_called_once_with(2)


@respx.mock
def test_call_gemini_500_exhausts_retries():
    """500 repetido → devuelve None tras agotar retries."""
    respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(500, text="Service Error")
    )
    with patch.object(config, "GEMINI_API_KEY", "test-key"):
        with patch("time.sleep"):
            result = call_gemini("prompt", max_retries=3)
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
def test_call_llm_synthesis_uses_synthesis_model():
    """phase='synthesis' manda CLAUDE_SYNTHESIS_MODEL en el body."""
    captured_body: dict = {}

    def capture_request(request):
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json=CLAUDE_OK_BODY)

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=capture_request)

    with patch.object(config, "ANTHROPIC_API_KEY", "test-key"):
        result = call_llm("prompt", provider="claude", phase="synthesis", max_retries=1)

    assert result == {"result": "ok"}
    assert captured_body.get("model") == config.CLAUDE_SYNTHESIS_MODEL


@respx.mock
def test_call_llm_extraction_uses_extraction_model():
    """phase='extraction' manda CLAUDE_EXTRACTION_MODEL en el body."""
    captured_body: dict = {}

    def capture_request(request):
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json=CLAUDE_OK_BODY)

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=capture_request)

    with patch.object(config, "ANTHROPIC_API_KEY", "test-key"):
        result = call_llm("prompt", provider="claude", phase="extraction", max_retries=1)

    assert result == {"result": "ok"}
    assert captured_body.get("model") == config.CLAUDE_EXTRACTION_MODEL


@respx.mock
def test_call_llm_provider_gemini_routes_correctly():
    """provider='gemini' enruta a call_gemini."""
    respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=httpx.Response(200, json=GEMINI_OK_BODY)
    )
    with patch.object(config, "GEMINI_API_KEY", "test-key"):
        result = call_llm("prompt", provider="gemini", max_retries=1)
    assert result == {"result": "ok"}


@respx.mock
def test_call_llm_provider_groq_routes_correctly():
    """provider='groq' enruta a call_groq."""
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=GROQ_OK_BODY)
    )
    with patch.object(config, "GROQ_API_KEY", "test-key"):
        result = call_llm("prompt", provider="groq", max_retries=1)
    assert result == {"result": "ok"}


def test_call_llm_does_not_mutate_config_ai_provider():
    """Llamar a call_llm no muta config.AI_PROVIDER (cambio clave vs legacy)."""
    original_provider = config.AI_PROVIDER

    # Simulamos una llamada que falla (sin API key real) para no necesitar mocks HTTP
    with patch.object(config, "ANTHROPIC_API_KEY", ""):
        call_llm("prompt", provider="claude", max_retries=1)

    # config.AI_PROVIDER debe seguir siendo exactamente el mismo objeto/valor
    assert config.AI_PROVIDER == original_provider


def test_call_llm_unknown_provider_returns_none():
    """Provider desconocido devuelve None sin lanzar excepción."""
    result = call_llm("prompt", provider="unknown_provider", max_retries=1)
    assert result is None
