# Review — feature #19 logging_structured_l1_l2

**Veredicto:** APPROVED

## Checkpoints

- C1: [x] Archivos base presentes. `./init.sh` termina verde (exit 0).
- C2: [x] Una sola feature `in_progress` en `feature_list.json`. `progress/current.md` describe la sesión activa.
- C3: [x] Sin `sys.path.append`. Sin mutación de globals. `logging_setup.py` en raíz del paquete (posición prevista en `docs/architecture.md` línea 69). `telegram.py` sin `print()` sueltos.
- C4: [x] `pytest -q` → 375 tests, todos verdes. `tests/test_logging_setup.py` → 8 tests verdes.
- C5: [x] No aplica cambios a la BD.
- C6: [ ] Sesión no cerrada aún (pendiente de commit/push — normal en review previo al cierre).

## Criterios de aceptación

1. [x] `LOG_LEVEL=DEBUG LOG_FORMAT=json` produce JSON parseable: `_JsonFormatter.format()` emite `{"timestamp":…,"level":…,"logger":…,"message":…}` — verificado con `json.loads()` en `test_json_formatter_produces_parseable_json` y `test_setup_logging_json_format`.

2. [x] Default text produce `'TS LEVEL logger_name: message'`: formato `%(asctime)s %(levelname)s %(name)s: %(message)s` con `datefmt="%Y-%m-%dT%H:%M:%S"` — verificado manualmente (produce `2026-05-30T20:55:46 INFO mymodule: test message`). Test `test_setup_logging_text_format` cubre el patrón con regex.

3. [x] CLI del pipeline mantiene su output (cabeceras de fase con `print`): `main.py` no migra los `print("-- FASE N: …")` a logger. Solo agrega el `setup_logging()` call en `__main__`.

4. [x] Módulos migrados usan `logging.getLogger(__name__)`:
   - `notifications/telegram.py`: migrado de `print()` a `logger.warning()` en 3 puntos (`send_tuner_report` línea 108, `_send_message` líneas 133 y 137). Tiene `logger = logging.getLogger(__name__)` en línea 10.
   - `storage/db.py`, `analysis/dedup.py`, `analysis/llm_clients.py`, `scrapers/reddit_scraper.py`: ya tenían `logger = logging.getLogger(__name__)` — no se tocaron innecesariamente. Confirmado con grep.

5. [x] Tests con caplog: `test_send_tuner_report_missing_file_logs_warning` y `test_fichero_inexistente` en `test_telegram.py` verifican que el warning llega vía logger (no print). Niveles y formato cubiertos en `test_logging_setup.py`.

6. [x] `stream=sys.stdout` con `encoding="utf-8"`: implementado vía `sys.stdout.reconfigure(encoding="utf-8")` (línea 39 de `logging_setup.py`) antes de crear el handler. Es el workaround correcto — `StreamHandler` hereda el encoding del stream ya reconfigurado. `test_setup_logging_stdout_encoding` verifica que `handler.stream is sys.stdout`.

## Convenciones

- [x] `from __future__ import annotations` en `logging_setup.py` y `test_logging_setup.py`.
- [x] Comillas dobles en todo el código nuevo.
- [x] f-strings para interpolación.
- [x] Sin `sys.path.append`.
- [x] Imports ordenados (stdlib primero, luego internos).
- [x] `setup_logging` llamado en `__main__` de `main.py` (línea 242), leyendo `LOG_LEVEL` y `LOG_FORMAT` de env.
- [x] Idempotencia verificada: 3 llamadas consecutivas a `setup_logging()` dejan exactamente 1 handler.

## Observación menor (no bloquea aprobación)

`test_setup_logging_json_format_end_to_end` (líneas 74-92 de `test_logging_setup.py`) llama a `setup_logging(...)` y luego hace `root.handlers.clear()` para añadir su propio handler hacia un buffer. El test funciona y pasa, pero el patrón es un poco enrevesado (llama a la función bajo test y luego la deshace). Queda como deuda de legibilidad menor, no de corrección.
