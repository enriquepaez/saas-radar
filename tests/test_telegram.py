"""Tests para saas_radar.notifications.telegram — sin llamadas reales a la API."""

from __future__ import annotations

import pytest

from saas_radar.notifications import telegram as tg


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Elimina las env vars de Telegram para que cada test parta de un estado limpio."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALERT_THRESHOLD", raising=False)


# ── send_tuner_report ─────────────────────────────────────────────────────────


def test_noop_si_falta_token(monkeypatch, tmp_path):
    """Sin token, send_tuner_report devuelve False y _send_message no se llama."""
    calls = []
    monkeypatch.setattr(tg, "_send_message", lambda *a, **kw: calls.append(a) or True)

    report = tmp_path / "report.txt"
    report.write_text("contenido", encoding="utf-8")

    result = tg.send_tuner_report(str(report))

    assert result is False
    assert calls == []


def test_envia_reporte_corto_entero(monkeypatch, tmp_path):
    """Con token+chat_id, envía el contenido completo del fichero dentro de ```."""
    sent_texts = []

    def fake_send(token, chat_id, text):
        sent_texts.append(text)
        return True

    monkeypatch.setattr(tg, "_send_message", fake_send)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat456")

    report = tmp_path / "report.txt"
    report.write_text("linea1\nlinea2", encoding="utf-8")

    result = tg.send_tuner_report(str(report))

    assert result is True
    assert len(sent_texts) == 1
    text = sent_texts[0]
    assert "linea1" in text
    assert text.startswith("Tuner dry-run report")
    assert "```" in text


def test_trunca_reporte_largo(monkeypatch, tmp_path):
    """Un fichero de 5000 chars se trunca: el texto enviado contiene '[truncado]' y mide < 4096."""
    sent_texts = []

    def fake_send(token, chat_id, text):
        sent_texts.append(text)
        return True

    monkeypatch.setattr(tg, "_send_message", fake_send)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")

    report = tmp_path / "big.txt"
    report.write_text("x" * 5000, encoding="utf-8")

    tg.send_tuner_report(str(report))

    assert sent_texts
    text = sent_texts[0]
    assert "[truncado]" in text
    assert len(text) < 4096


def test_fichero_inexistente(monkeypatch, caplog):
    """Si el fichero no existe, devuelve False y el warning menciona 'no se pudo leer'."""
    import logging

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")
    monkeypatch.setattr(tg, "_send_message", lambda *a: True)

    with caplog.at_level(logging.WARNING, logger="saas_radar.notifications.telegram"):
        result = tg.send_tuner_report("/tmp/fichero_que_no_existe_xyzabc.txt")

    assert result is False
    assert any("no se pudo leer" in r.message for r in caplog.records)


# ── send_text ─────────────────────────────────────────────────────────────────


def test_send_text_noop_si_falta_token(monkeypatch):
    """Sin token, send_text devuelve False sin llamar a _send_message."""
    calls = []
    monkeypatch.setattr(tg, "_send_message", lambda *a: calls.append(a) or True)

    result = tg.send_text("hola")

    assert result is False
    assert calls == []


def test_send_text_envia_mensaje_corto(monkeypatch):
    """Con credenciales, el texto corto se envía exactamente como se recibe."""
    sent_texts = []

    def fake_send(token, chat_id, text):
        sent_texts.append(text)
        return True

    monkeypatch.setattr(tg, "_send_message", fake_send)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    result = tg.send_text("mensaje corto")

    assert result is True
    assert sent_texts == ["mensaje corto"]


def test_send_text_trunca_mensaje_largo(monkeypatch):
    """Un texto de 5000 'y' se trunca: el enviado contiene '[truncado]' y mide <= 4000."""
    sent_texts = []

    def fake_send(token, chat_id, text):
        sent_texts.append(text)
        return True

    monkeypatch.setattr(tg, "_send_message", fake_send)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    tg.send_text("y" * 5000)

    assert sent_texts
    text = sent_texts[0]
    assert "[truncado]" in text
    assert len(text) <= 4000


# ── send_opportunity_alert ────────────────────────────────────────────────────


def test_opportunity_alert_noop_sin_token(monkeypatch):
    """Sin env vars, send_opportunity_alert con priority_score=9 devuelve False y no llama _send_message."""
    calls = []
    monkeypatch.setattr(tg, "_send_message", lambda *a, **kw: calls.append(a) or True)

    result = tg.send_opportunity_alert({
        "title": "Título oportunidad",
        "url": "https://reddit.com/r/test/1",
        "priority_score": 9,
        "summary": "Resumen breve",
    })

    assert result is False
    assert calls == []


def test_opportunity_alert_skip_bajo_score(monkeypatch):
    """Con token+chat_id pero priority_score=7 (< threshold=8), devuelve False y no llama _send_message."""
    calls = []
    monkeypatch.setattr(tg, "_send_message", lambda *a, **kw: calls.append(a) or True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")

    result = tg.send_opportunity_alert({
        "title": "Oportunidad baja",
        "url": "https://reddit.com/r/test/2",
        "priority_score": 7,
        "summary": "Resumen breve",
    })

    assert result is False
    assert calls == []


# ── _send_message (HTTP payload) ──────────────────────────────────────────────


def test_send_message_payload_markdown():
    """_send_message envía payload HTTP con parse_mode=Markdown al endpoint correcto."""
    import json

    import httpx
    import respx

    with respx.mock:
        route = respx.post("https://api.telegram.org/botmytoken/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = tg._send_message("mytoken", "mychat", "hola")

    assert result is True
    assert route.called
    body = route.calls[0].request.content
    payload = json.loads(body)
    assert payload["parse_mode"] == "Markdown"
    assert payload["chat_id"] == "mychat"
    assert payload["text"] == "hola"
