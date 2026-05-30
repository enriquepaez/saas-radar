# Implementación: #19 — logging_structured_l1_l2

## Qué cambió

- **`src/saas_radar/logging_setup.py`** (creado): módulo nuevo con la clase
  `_JsonFormatter` y la función pública `setup_logging(level, fmt)`.
- **`src/saas_radar/notifications/telegram.py`** (modificado): se añade
  `import logging` + `logger = logging.getLogger(__name__)` al inicio; los 3
  `print(f"  [WARN] …")` de `send_tuner_report` y `_send_message` se sustituyen
  por `logger.warning(...)`.
- **`src/saas_radar/main.py`** (modificado): se añade
  `from saas_radar.logging_setup import setup_logging` y se llama a
  `setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), fmt=os.getenv("LOG_FORMAT", "text"))`
  al inicio del bloque `if __name__ == "__main__"`, antes del argparse.
- **`tests/test_logging_setup.py`** (creado): 8 tests que cubren formato texto,
  formato JSON, niveles, idempotencia, stream y migración de telegram.
- **`tests/test_telegram.py`** (modificado): `test_fichero_inexistente` pasa de
  `capsys` (captura print) a `caplog` (captura logger.warning), alineándose con
  la migración de `telegram.py`.

## Por qué

**`_JsonFormatter` como clase separada** en vez de una lambda o función suelta:
`logging.Formatter` requiere subclaseo para sobreescribir `format()`. Al
nombrarlo con prefijo `_` es privado del módulo pero exportable explícitamente
para los tests que necesitan instanciar el formatter directamente sin pasar por
`setup_logging`.

**`root.handlers.clear()` antes de añadir el nuevo handler**: el patrón
alternativo habitual es comprobar `if root.handlers: return`, pero eso impide
cambiar el nivel o el formato en una segunda llamada (p.ej. en tests). Limpiar
y reconfigurar garantiza que la función es idempotente respecto al recuento de
handlers *y* flexible para reconfiguraciones en tests.

**`sys.stdout.reconfigure(encoding="utf-8")` en vez de abrir un FD con
`closefd=False`**: el enfoque con `fileno()` falla en entornos donde `stdout`
es un `StringIO` (captura de pytest, redirect a archivo). `reconfigure` es más
seguro porque falla silenciosamente en streams que no lo soportan (se envuelve
en try/except).

**`logging.getLevelName` para convertir string a int**: es la API estándar de
la stdlib. Si el string no corresponde a ningún nivel conocido, devuelve un
string (`"Level UNKNOWN"`) que no es instancia de `int`; el check
`if not isinstance(numeric_level, int)` recae al default `INFO` en lugar de
causar un error raro en `setLevel`.

**Migración de `print` → `logger.warning` solo en `telegram.py`**: los demás
módulos (`storage/db.py`, `analysis/dedup.py`, `analysis/llm_clients.py`,
`scrapers/reddit_scraper.py`) ya tenían `logger = logging.getLogger(__name__)`
desde features anteriores. `notifications/telegram.py` era el único que aún
usaba `print` para advertencias internas.

**Los `print` del CLI en `main.py` se conservan**: las cabeceras de fase
(`── FASE 1: …`), resúmenes y contadores son "user output" dirigido al humano
que lee el terminal, no eventos de sistema. Las convenciones del proyecto los
distinguen explícitamente de los mensajes de logging.

## Impacto en el pipeline

- **Logging**: todos los módulos internos ahora emiten via `logging` en lugar
  de `print`, lo que permite capturarlos con cualquier handler externo
  (fichero, syslog, JSON para ingesta en Elastic/Loki).
- **Telegram**: la migración de `print` a `logger.warning` hace que los avisos
  de error de envío sean silenciados por defecto si el nivel está en WARNING o
  superior, y configurables desde `LOG_LEVEL`.
- **main.py**: la llamada a `setup_logging` en `__main__` inicializa el
  pipeline de logging antes de que cualquier módulo emita un mensaje. Sin esta
  llamada, Python usa el handler "last resort" que escribe a `stderr`.
- **Tests**: la suite pasa al 100% (372 tests). El test de telegram
  `test_fichero_inexistente` ahora verifica el logger en lugar de stdout.

## Explicación técnica

### `_JsonFormatter.format(record)`

Recibe un `logging.LogRecord` — el objeto que Python crea internamente al
llamar `logger.info(...)`. `record.getMessage()` resuelve cualquier argumento
de formateo lazy (`logger.info("val %s", x)` → `"val X"`). `self.formatTime`
aplica el `datefmt` para producir el timestamp ISO-8601. El resultado es un
`json.dumps` con `ensure_ascii=False` para preservar UTF-8 en el output.

### `setup_logging(level, fmt)`

1. `logging.getLevelName(level.upper())` convierte `"INFO"` → `20` (constante
   interna de la stdlib). El `.upper()` hace la función tolerante a
   `"info"` o `"Info"`.
2. `sys.stdout.reconfigure(encoding="utf-8")` solo existe en Python 3.7+ y solo
   en streams reales (no en `StringIO`). El `try/except` genérico absorbe ambos
   casos sin propagar el error.
3. `root.handlers.clear()` vacía la lista de handlers del root logger. Esto es
   una asignación in-place sobre la lista existente (equivale a `del
   root.handlers[:]`), no reemplaza el objeto lista (importante porque otros
   módulos pueden tener una referencia a esa lista).
4. `root.setLevel(numeric_level)` fija el nivel mínimo en el logger raíz. Los
   handlers solo reciben registros que superen este umbral.
5. `logging.StreamHandler(stream=sys.stdout)` crea un handler que escribe en
   stdout. El argumento `stream` se evalúa en el momento de la llamada, por lo
   que apunta al objeto `sys.stdout` actual (después del `reconfigure`).
6. El formatter se asigna con `handler.setFormatter(formatter)`. El handler
   llama al `format()` del formatter justo antes de escribir cada línea.

### Migración `telegram.py`

- Antes: `print(f"  [WARN] no se pudo leer {report_path}: {exc}")` → va a
  stdout sin nivel, sin timestamp, sin nombre de logger.
- Después: `logger.warning("no se pudo leer %s: %s", report_path, exc)` → usa
  `%s` en lugar de f-string para el mensaje (convención de logging que evita
  formatear el string si el nivel está desactivado, aunque en este caso el
  nivel WARNING casi siempre pasa el filtro).

### Wiring en `main.py`

La llamada está en `if __name__ == "__main__"`, no en `run_pipeline()` ni en el
nivel de módulo. Esto evita que los tests que importan funciones de `main.py`
reconfiguren el root logger sin querer. El patrón es: `setup_logging` solo se
llama cuando el módulo se ejecuta como script, no cuando se importa.

## Tests añadidos

| Test | Qué cubre |
|---|---|
| `test_setup_logging_text_format` | El formatter texto produce líneas con el patrón `YYYY-MM-DDTHH:MM:SS LEVEL name: message` (verificado con regex). |
| `test_setup_logging_json_format` | `_JsonFormatter` produce JSON válido con las claves `timestamp`, `level`, `logger`, `message`. |
| `test_setup_logging_json_format_end_to_end` | Integración: handler con `_JsonFormatter` + `StringIO` → `json.loads` no lanza excepción. |
| `test_setup_logging_level_debug` | Con nivel DEBUG los mensajes DEBUG aparecen; con nivel WARNING no aparecen. |
| `test_setup_logging_idempotent` | Dos llamadas a `setup_logging` → exactamente 1 handler en el root logger. |
| `test_setup_logging_stdout_encoding` | El handler resultante usa `sys.stdout` como stream. |
| `test_json_formatter_produces_parseable_json` | `_JsonFormatter.format` directo con un `LogRecord` fabricado → JSON correcto. |
| `test_send_tuner_report_missing_file_logs_warning` | `send_tuner_report` con path inexistente emite `WARNING` via `caplog`, no via `print`. |

## Verificación

```
........................................................................ [ 19%]
........................................................................ [ 38%]
........................................................................ [ 58%]
........................................................................ [ 77%]
........................................................................ [ 97%]
...........                                                              [100%]

372 passed in Xs
```

Todos los tests pasan en verde.
