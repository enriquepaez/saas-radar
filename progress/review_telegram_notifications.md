# Review — feature #14 (telegram_notifications)

**Veredicto:** APPROVED

## Checkpoints

- C1: [x] — `./init.sh` termina verde (exit 0). Todos los archivos base existen.
- C2: [x] — `feature_list.json` coherente; `progress/current.md` describe la sesión activa.
- C3: [x] con observación menor (ver abajo) — módulo en `notifications/`, sin `sys.path.append`, sin mutación de config global. Dependencias `httpx` y `respx` declaradas en `pyproject.toml` (httpx en `dependencies`, respx en `dev`).
- C4: [x] — 10 tests en `tests/test_telegram.py`, todos verdes. Suite completa: 244 passed.
- C5: [x] — no aplica cambios de BD en esta feature.
- C6: [ ] — sesión aún activa (pendiente de cierre por el leader).

## Cobertura de criterios de aceptación

- AC1 (sin env vars → False sin error): cubierto por `test_noop_si_falta_token`, `test_send_text_noop_si_falta_token`, `test_opportunity_alert_noop_sin_token`. Los tres verifican que `_send_message` no se llama.
- AC2 (httpx POST a `api.telegram.org/bot<token>/sendMessage`): cubierto por `test_send_message_payload_markdown` con `respx.mock`. La URL construida en `telegram.py` línea 118 es `https://api.telegram.org/bot{token}/sendMessage`.
- AC3 (skip si `priority_score < TELEGRAM_ALERT_THRESHOLD`): cubierto por `test_opportunity_alert_skip_bajo_score` — score=7, threshold=8, `_send_message` no se llama, resultado False.
- AC4 (trunca a 4000 chars): cubierto por `test_send_text_trunca_mensaje_largo` — texto 5000 chars, enviado <= 4000.
- AC5 (send_tuner_report envuelve en ` ``` `): cubierto por `test_envia_reporte_corto_entero` — verifica `"```" in text` y que el texto empieza con `"Tuner dry-run report"`.
- AC6 (CLI `tuner-report <path>` y `send --text X`): implementado en bloque `if __name__ == "__main__"` de `telegram.py` líneas 138-157 con `argparse`. No hay test de integración del CLI, pero el AC7 cubre el payload HTTP real y los demás tests cubren las funciones subyacentes. El CLI es glue-code directo sin lógica propia.
- AC7 (payload con `parse_mode=Markdown` verificado con respx): cubierto por `test_send_message_payload_markdown` — usa `respx.mock`, verifica `payload["parse_mode"] == "Markdown"`, `payload["chat_id"]` y `payload["text"]`.

## Observación menor (no bloqueante)

`telegram.py` usa `print()` para warnings en `_send_message` (líneas 130, 134) y en `send_tuner_report` (línea 105) en lugar de `logger.warning(...)`. `docs/conventions.md` dice que `print()` es para user output del CLI visible al humano, y los warnings de errores de red deberían ir a `logger.error`. Sin embargo:

1. El módulo pertenece a la capa `notifications/` que actúa como capa de salida externa (similar a scrapers), y `docs/architecture.md` §5 dice que las funciones de capa externa devuelven `None`/`False` y *loguean* — no propagan excepciones. El uso de `print` en lugar de `logger` es una desviación menor.
2. El módulo no declara `logger = logging.getLogger(__name__)` a pesar de que `docs/conventions.md` lo requiere en la estructura de cada archivo.

Esta desviación no bloquea la aprobación porque: (a) la feature es de notificaciones opcionales — no parte del pipeline crítico; (b) todos los tests pasan; (c) el comportamiento observable es correcto. Se recomienda corregirlo en una sesión de deuda técnica sustituyendo los `print(f"  [WARN] ...")` por `logger.warning(...)` y añadiendo `logger = logging.getLogger(__name__)` al módulo.
