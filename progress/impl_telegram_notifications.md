# Implementación: #14 — telegram_notifications

## Qué cambió

- **`src/saas_radar/notifications/__init__.py`**: archivo vacío nuevo. Convierte el directorio en un paquete Python importable. Sin él `from saas_radar.notifications import telegram` lanzaría `ModuleNotFoundError`.

- **`src/saas_radar/notifications/telegram.py`**: módulo nuevo con 6 funciones públicas/privadas y un bloque CLI.

- **`tests/test_telegram.py`**: suite nueva con 7 tests que cubren todos los criterios de aceptación.

## Por qué

El pipeline ya persiste oportunidades en BD y genera runs completos (features #11–#13). La feature #14 cierra el ciclo de productización añadiendo el canal de notificación Telegram, que el usuario recibe en tiempo real sin tener que consultar la BD. El comportamiento se replica del legacy tal cual porque no hay deuda técnica documentada en este módulo (ver `docs/legacy-context/lessons-learned.md`): es puro I/O HTTP sin lógica de negocio compleja.

La única diferencia cosmética respecto al legacy es retirar los emojis del texto de alerta (`🎯`, `📊`) para cumplir con la convención del proyecto ("Avoid writing emojis to files unless asked").

## Impacto en el pipeline

- **Notificaciones**: ahora `send_opportunity_alert` puede integrarse en el orquestador (`ai_analyzer.py`) para notificar cada oportunidad que supere el threshold.
- **CI/tests**: ningún test existente se toca; los 7 tests nuevos se añaden a la suite y pasan al 100%.
- **Sin efectos secundarios**: el módulo es completamente inerte si `TELEGRAM_BOT_TOKEN` no está en el entorno. Esto preserva el comportamiento de CI sin secretos.

## Explicación técnica

### `_get_config() -> tuple[str, str, int]`

Lee tres env vars:
- `os.getenv("TELEGRAM_BOT_TOKEN", "")` → devuelve `""` si no existe, lo que activa el guard `if not token` en los callers.
- `os.getenv("TELEGRAM_CHAT_ID", "")` → ídem.
- `int(os.getenv("TELEGRAM_ALERT_THRESHOLD", "8"))` → convierte a entero para comparación numérica. El default 8 replica el legacy y el valor ya declarado en `config.py`. Se lee de env directamente aquí (no de `config`) para que la función sea auto-contenida y testeable con solo `monkeypatch.setenv`.

Por qué `tuple[str, str, int]` y no un dataclass: es un retorno de una sola función privada consumida siempre con destructuring `token, chat_id, threshold = _get_config()`. Un dataclass añadiría boilerplate sin ganancia.

### `send_opportunity_alert(opp: dict) -> bool`

1. Llama a `_get_config()` y sale con `False` si falta token o chat_id — no-op silencioso.
2. Compara `opp.get("priority_score", 0) < threshold` — si el score no alcanza el umbral, también sale con `False`. El default 0 para `.get()` garantiza que opps sin `priority_score` nunca disparan alerta.
3. Extrae campos con `.get(key, "?")` — si el LLM no devolvió algún campo, se muestra `?` en lugar de crashar con `KeyError`.
4. `evidence = opp.get("evidence_items", [])` seguido de `len(evidence)` — si el campo es lista, `len()` devuelve el número de posts de evidencia.
5. Construye un f-string con formato Markdown de Telegram (negrita con `*...*`).
6. Delega en `_send_message(token, chat_id, text)`.

### `send_run_summary(posts_analyzed, opportunities_count, duration_sec, mode) -> bool`

Convierte `duration_sec` a minutos+segundos con división entera `//` y módulo `%`. Firma con 4 parámetros posicionales para que el caller (futuro `main.py`) pueda pasarlos explícitamente sin diccionarios intermedios.

### `send_text(text: str) -> bool`

- Guard `if not token or not chat_id` → no-op.
- `if len(text) > 4000: text = text[:3980] + "\n... [truncado]"` — por qué 3980 y no 4000: el sufijo `"\n... [truncado]"` tiene 15 chars, así que el total queda en 3995, bien por debajo del límite 4096 de Telegram. El límite 4000 (no 4096) es conservador: Telegram puede añadir overhead de protocolo en mensajes con `parse_mode=Markdown`.

### `send_tuner_report(report_path: str) -> bool`

- Guard de credenciales igual que las demás.
- `open(report_path, encoding="utf-8")` dentro de `try/except OSError` — si el fichero no existe o no es legible, imprime el warning con `print(f"  [WARN] no se pudo leer {report_path}: {exc}")` y devuelve `False`. Se usa `print` (no `logger`) porque en este punto el módulo puede ejecutarse como CLI independiente sin `setup_logging` configurado.
- `max_body = 3900` — por qué 3900 y no más: el encabezado `"Tuner dry-run report\n```\n...\n```"` añade ~25 chars. Con 3900 + 25 = 3925, queda margen frente al límite 4096.
- `report[: max_body - 20]` — sustrae 20 adicionales (longitud de `"\n... [truncado]"`) para que el cuerpo truncado + sufijo no supere `max_body`.
- `text = f"Tuner dry-run report\n```\n{report}\n```"` — el fence ` ``` ` activa formato monospace en Telegram, preservando el alineado de columnas del CLI del tuner.

### `_send_message(token: str, chat_id: str, text: str) -> bool`

- Construye la URL con f-string: `f"https://api.telegram.org/bot{token}/sendMessage"`. El token va incrustado en la URL, que es el formato estándar de la Bot API de Telegram (no header de autorización).
- `httpx.post(url, json={...}, timeout=10)` — por qué `httpx` y no `requests`: el proyecto ya usa `httpx` en los clientes LLM (`llm_clients.py`), y está declarado como dependencia en `pyproject.toml`. Añadir `requests` sería redundante.
- `json={...}` en vez de `data=...`: `httpx` serializa el dict a JSON y pone `Content-Type: application/json` automáticamente. La Bot API acepta ambos formatos, pero JSON es más limpio.
- `"parse_mode": "Markdown"` — habilita el subconjunto Markdown de Telegram (`*negrita*`, `` `código` ``, etc.).
- `timeout=10` — 10 segundos es suficiente para una petición HTTP a un servidor de Telegram. El legacy usa el mismo valor.
- `resp.is_success` — atributo de `httpx.Response` que equivale a `200 <= status_code < 300`. Más idiomático que `resp.status_code == 200`.
- El `except Exception` captura cualquier error de red (DNS, timeout, SSL) sin propagarlo. Se imprime un warning y se devuelve `False`, permitiendo al pipeline continuar.

### Bloque `if __name__ == "__main__":`

- `argparse.ArgumentParser(prog="saas_radar.notifications.telegram")` — el nombre del prog refleja cómo se invoca: `python -m saas_radar.notifications.telegram`.
- `add_subparsers(dest="cmd", required=True)` — `required=True` hace que argparse emita un error si no se pasa subcomando, en lugar de salir silenciosamente.
- Subcomando `tuner-report <path>`: argumento posicional `path`.
- Subcomando `send --text X`: flag obligatorio `--text`.
- `sys.exit(0 if ok else 1)` — mapea el bool de retorno al código de salida estándar Unix.

## Tests añadidos

| Test | Qué cubre |
|---|---|
| `test_noop_si_falta_token` | Sin `TELEGRAM_BOT_TOKEN`, `send_tuner_report` devuelve `False` y `_send_message` no se invoca. |
| `test_envia_reporte_corto_entero` | Con credenciales, el texto del fichero llega íntegro; el mensaje empieza con `"Tuner dry-run report"` y contiene ` ``` `. |
| `test_trunca_reporte_largo` | Fichero de 5000 chars → `"[truncado]"` en el texto enviado y `len(text) < 4096`. |
| `test_fichero_inexistente` | Fichero inexistente → `False` + stdout contiene `"no se pudo leer"`. |
| `test_send_text_noop_si_falta_token` | Sin token, `send_text` devuelve `False` sin llamar a `_send_message`. |
| `test_send_text_envia_mensaje_corto` | Texto corto se envía exactamente como se recibe. |
| `test_send_text_trunca_mensaje_largo` | 5000 `"y"` → `"[truncado]"` en texto enviado y `len <= 4000`. |

Todos los tests usan `monkeypatch.setattr(tg, "_send_message", fake_send)` para interceptar la llamada sin hacer peticiones HTTP reales. El fixture `autouse _reset_env` elimina las tres env vars antes de cada test para garantizar aislamiento.

## Verificación

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0, respx-0.23.1
collected 7 items

tests/test_telegram.py .......                                           [100%]

============================== 7 passed in 0.02s ===============================
```

Suite completa (todos los módulos): todos los tests pasan sin regresiones.

## Corrección post-review

El reviewer detectó que faltaban 3 tests. Se añadieron al final de `tests/test_telegram.py`:

### `test_opportunity_alert_noop_sin_token`
Sin env vars, `send_opportunity_alert` con un dict que incluye `priority_score=9` devuelve `False` y `_send_message` no se llama. Cubre el guard de credenciales de esta función, que hasta ahora no tenía test propio.

### `test_opportunity_alert_skip_bajo_score`
Con `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` configurados pero `priority_score=7` (por debajo del threshold por defecto 8), devuelve `False` y `_send_message` no se llama. Cubre la rama de filtrado por score.

Nota de ajuste: la firma real de `send_opportunity_alert` es `(opp: dict) -> bool`, no acepta kwargs individuales. Los tests llaman `tg.send_opportunity_alert({...})` con un dict posicional.

### `test_send_message_payload_markdown`
Usa `respx.mock` como context manager para interceptar la petición HTTP real a `https://api.telegram.org/botmytoken/sendMessage`. Verifica que:
- El resultado de `_send_message` es `True`.
- La ruta fue llamada (`route.called`).
- El cuerpo JSON enviado contiene `parse_mode == "Markdown"`, `chat_id == "mychat"` y `text == "hola"`.

Este es el único test que ejerce `_send_message` de verdad (sin monkey-patch), validando la serialización HTTP real.

### Verificación post-corrección

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
plugins: anyio-4.13.0, respx-0.23.1
collected 10 items

tests/test_telegram.py ..........                                        [100%]

============================== 10 passed in 0.03s ==============================
```
