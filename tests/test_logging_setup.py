"""Tests para saas_radar.logging_setup — cobertura de formatos, niveles e idempotencia."""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from saas_radar.logging_setup import _JsonFormatter, setup_logging


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Restaura el root logger a su estado original después de cada test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_setup_logging_text_format():
    """Formato text produce líneas con el patrón YYYY-MM-DDTHH:MM:SS LEVEL name: message."""
    import re

    buf = io.StringIO()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    logger = logging.getLogger("test.text")
    logger.info("mensaje de prueba")

    output = buf.getvalue()
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} INFO test\.text: mensaje de prueba"
    assert re.match(pattern, output), f"Formato inesperado: {output!r}"


def test_setup_logging_json_format():
    """El _JsonFormatter produce una línea JSON con las claves requeridas."""
    fmt = _JsonFormatter()
    record = logging.LogRecord(
        name="test.json",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hola mundo",
        args=(),
        exc_info=None,
    )
    line = fmt.format(record)
    data = json.loads(line)

    assert data["message"] == "hola mundo"
    assert data["level"] == "INFO"
    assert data["logger"] == "test.json"
    assert "timestamp" in data
    assert "T" in data["timestamp"]


def test_setup_logging_json_format_end_to_end():
    """setup_logging con fmt='json' emite JSON parseable en el handler de salida."""
    buf = io.StringIO()
    setup_logging(level="DEBUG", fmt="json")

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)

    logger = logging.getLogger("test.json.e2e")
    logger.info("mensaje json")

    output = buf.getvalue().strip()
    data = json.loads(output)
    assert data["message"] == "mensaje json"
    assert data["level"] == "INFO"


def test_setup_logging_level_debug():
    """Con level='DEBUG', mensajes DEBUG aparecen; con level='WARNING', no."""
    buf = io.StringIO()

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)

    logger = logging.getLogger("test.level.debug")
    logger.debug("mensaje debug")
    assert "mensaje debug" in buf.getvalue()

    buf2 = io.StringIO()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    handler2 = logging.StreamHandler(buf2)
    handler2.setLevel(logging.WARNING)
    root.addHandler(handler2)

    logger2 = logging.getLogger("test.level.warning")
    logger2.debug("no deberia aparecer")
    assert "no deberia aparecer" not in buf2.getvalue()


def test_setup_logging_idempotent():
    """Llamar setup_logging() dos veces deja exactamente 1 handler en el root logger."""
    setup_logging(level="INFO", fmt="text")
    setup_logging(level="INFO", fmt="text")

    root = logging.getLogger()
    assert len(root.handlers) == 1


def test_setup_logging_stdout_encoding():
    """El handler configurado por setup_logging usa sys.stdout como stream."""
    setup_logging(level="INFO", fmt="text")

    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout


def test_json_formatter_produces_parseable_json():
    """_JsonFormatter.format devuelve JSON válido con las 4 claves requeridas."""
    fmt = _JsonFormatter()
    record = logging.LogRecord(
        name="mylogger",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="algo salió mal",
        args=(),
        exc_info=None,
    )
    line = fmt.format(record)
    data = json.loads(line)

    assert data["message"] == "algo salió mal"
    assert data["level"] == "WARNING"
    assert data["logger"] == "mylogger"
    assert "timestamp" in data


def test_send_tuner_report_missing_file_logs_warning(tmp_path, caplog):
    """send_tuner_report con path inexistente emite WARNING via logger, no print."""
    import os

    from saas_radar.notifications.telegram import send_tuner_report

    os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
    os.environ["TELEGRAM_CHAT_ID"] = "cid"

    with caplog.at_level(logging.WARNING, logger="saas_radar.notifications.telegram"):
        result = send_tuner_report(str(tmp_path / "noexiste.txt"))

    del os.environ["TELEGRAM_BOT_TOKEN"]
    del os.environ["TELEGRAM_CHAT_ID"]

    assert result is False
    assert any("no se pudo leer" in r.message for r in caplog.records)
