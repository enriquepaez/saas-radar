# Implementación: telegram-pipeline-integration

## Qué cambió

- **`src/saas_radar/analysis/ai_analyzer.py`**: añadido `"posts_analyzed"` en los 4 returns de `run_ai_analysis`.
  - Return del early-fail (posts_df vacío): antes devolvía 5 claves, ahora 6 con `"posts_analyzed": 0`.
  - Return del abort (< 2 extracciones válidas): antes devolvía 5 claves, ahora 6 con `"posts_analyzed": len(posts_list)`.
  - Return del abort (LLM None en síntesis): antes devolvía 5 claves, ahora 6 con `"posts_analyzed": len(posts_list)`.
  - Return del éxito: antes devolvía 6 claves, ahora 7 con `"posts_analyzed": len(posts_list)`.

- **`src/saas_radar/main.py`**: 5 cambios en `run_pipeline`:
  1. Import de `send_opportunity_alert` y `send_run_summary` desde `saas_radar.notifications.telegram`.
  2. Variable `ai_result: dict = {}` inicializada antes del bloque `if not skip_ai`.
  3. El resultado de `run_ai_analysis` se captura en `ai_result` en lugar de descartarse.
  4. Loop `for opp in (ai_result.get("opportunities") or []):` que llama `send_opportunity_alert(opp)` tras el análisis IA.
  5. Variable `mode` definida justo después de calcular `post_age_days`.
  6. `send_run_summary(...)` invocado al final del pipeline, antes del print de cierre.

- **`tests/test_main.py`**: 4 tests nuevos + correcciones en tests existentes.
  - Tests 2, 3, 4, 5: añadido `patch("saas_radar.main.send_run_summary")` para evitar llamadas reales.
  - Test 6: `run_ai_analysis` ahora devuelve un dict válido en lugar de `None`.
  - Test 11 (nuevo): verifica que `send_opportunity_alert` se llama N veces.
  - Test 12 (nuevo): verifica que `send_opportunity_alert` NO se llama con `skip_ai=True`.
  - Tests 13 y 14 (nuevos, parametrizados): verifican que `send_run_summary` siempre se llama y recibe el `mode` correcto.

## Por qué

**`posts_analyzed` en todos los returns**: `send_run_summary` necesita este dato para enviar el resumen. Como `run_ai_analysis` puede retornar anticipadamente (sin posts, sin extracciones válidas, con LLM None), el campo debe estar presente en todos los caminos de retorno. El valor 0 en el early-fail es correcto porque no se cargó ningún post.

**`ai_result: dict = {}`**: inicializar antes del bloque `if not skip_ai` garantiza que la variable exista en scope cuando se usa en `send_run_summary` al final. Si `skip_ai=True`, `ai_result` queda como `{}` y `.get("posts_analyzed", 0)` devuelve 0 correctamente.

**`for opp in (ai_result.get("opportunities") or []):`**: el `or []` protege contra `None` si el LLM falla y el dict tiene `"opportunities": None`. Sin él, un `None` rompería el for-loop con `TypeError`.

**`mode` como variable explícita**: se definía implícitamente en los mensajes de print pero no era accesible al final del pipeline para pasarla a `send_run_summary`. Extraerla como variable en el punto exacto donde se decide el modo (justo después de calcular `incremental`) es la ubicación natural y única fuente de verdad.

**Mocks en tests existentes**: los tests 2-5 llamaban `run_pipeline` sin mock de `send_run_summary`, lo que haría una llamada real (no-op silenciosa sin token, pero puede generar logs inesperados en CI). Añadir el mock mantiene los tests aislados. El test 6 necesitaba devolver un dict válido porque el código ahora hace `.get("opportunities")` sobre el resultado.

## Impacto en el pipeline

- **Telegram**: las funciones `send_opportunity_alert` y `send_run_summary` dejan de ser código muerto y se ejecutan en cada run. Sin `TELEGRAM_BOT_TOKEN` siguen siendo no-op silenciosas (devuelven `False`).
- **Scraping** (fases 1-3): sin cambios.
- **BD/LLM** (fase 4): sin cambios en lógica; solo se añade el campo `posts_analyzed` al dict de retorno.
- **GTM** (fase 5): sin cambios.
- **CI/CD**: los tests cubren los nuevos paths; `skip_ai=True` no rompe el pipeline aunque `ai_result` esté vacío.

## Explicación técnica

### `ai_analyzer.py` — `posts_analyzed` en returns

```python
return {
    "status": "failed",
    ...
    "posts_analyzed": 0,          # early-fail: no se llegó a cargar posts
}
```

`0` es semánticamente correcto: el orquestador abortó antes de construir `posts_list`, por lo que no hay posts analizados.

```python
return {
    "status": "failed",
    ...
    "posts_analyzed": len(posts_list),    # aborts tardíos: posts cargados pero análisis fallido
}
```

`len(posts_list)` refleja cuántos posts se cargaron y procesaron hasta el punto de fallo. Esto permite a `send_run_summary` reportar un número real aunque no se produjeran oportunidades.

### `main.py` — imports

```python
from saas_radar.notifications.telegram import send_opportunity_alert, send_run_summary
```

Import estático (no lazy) porque estas funciones se llaman siempre al final del pipeline y no hay riesgo de importación circular. El módulo `telegram.py` no importa nada de `main.py`.

### `main.py` — `mode`

```python
mode = "INCREMENTAL" if incremental else "CARGA COMPLETA"
```

Operador ternario de Python: si `incremental` es `True`, `mode` vale `"INCREMENTAL"`; si es `False`, `"CARGA COMPLETA"`. Se define justo después de fijar `incremental` para que sea la única fuente de verdad sobre el modo del run. Los strings del print existente coinciden con esta variable.

### `main.py` — captura de `ai_result` y alertas

```python
ai_result: dict = {}
```

Anotación de tipo `dict` (PEP 526 variable annotation). Inicializar con `{}` es más explícito que con `None` y permite usar `.get()` sin comprobar `if ai_result is not None`.

```python
ai_result = run_ai_analysis(...)
```

`run_ai_analysis` siempre devuelve un `dict` (nunca `None`), pero si en el futuro devolviera `None`, la inicialización a `{}` protege el código posterior. La asignación reemplaza el `{}` vacío con el resultado real del análisis.

```python
for opp in (ai_result.get("opportunities") or []):
    send_opportunity_alert(opp)
```

`ai_result.get("opportunities")` devuelve la lista de oportunidades o `None` si la clave no existe. El `or []` convierte `None` en lista vacía, haciendo el loop seguro. `send_opportunity_alert(opp)` es no-op silencioso sin token; con token envía una alerta Markdown a Telegram solo si `priority_score >= TELEGRAM_ALERT_THRESHOLD` (default 8).

### `main.py` — `send_run_summary`

```python
send_run_summary(
    posts_analyzed=ai_result.get("posts_analyzed", 0),
    opportunities_count=len(ai_result.get("opportunities") or []),
    duration_sec=int(time.time() - t_total),
    mode=mode,
)
```

- `posts_analyzed`: extrae el valor del dict de resultado; si está vacío (skip_ai=True) devuelve 0 como default.
- `opportunities_count`: cuenta las oportunidades del resultado; el `or []` protege contra `None`; `len([])` devuelve 0.
- `duration_sec`: `time.time()` devuelve float con microsegundos; `int()` trunca a segundos enteros. `t_total` se fijó al inicio de `run_pipeline`.
- `mode`: la variable definida al inicio del pipeline.

Se llama *antes* del print de cierre para que si Telegram falla, el print de cierre siga apareciendo.

## Tests añadidos

- **`test_skip_all_flags_no_exception`** (corregido): añadido mock de `send_run_summary` para aislar el test de la llamada HTTP.
- **`test_incremental_mode_when_previous_run_exists`** (corregido): ídem.
- **`test_full_load_mode_when_no_previous_run`** (corregido): ídem.
- **`test_full_scan_flag_forces_full_load`** (corregido): ídem.
- **`test_e2e_full_pipeline_with_mocks`** (corregido): `run_ai_analysis` ahora devuelve dict válido con todas las claves incluyendo `posts_analyzed`.
- **`test_send_opportunity_alert_called_n_times`** (nuevo): lanza `run_pipeline` con 3 oportunidades en `ai_result`; verifica que `mock_alert.call_count == 3`.
- **`test_send_opportunity_alert_not_called_when_skip_ai`** (nuevo): con `skip_ai=True`, verifica que `mock_alert.assert_not_called()`.
- **`test_send_run_summary_always_called`** (nuevo, parametrizado `skip_ai=False/True`): verifica que `mock_summary.assert_called_once()` en ambos casos.
- **`test_send_run_summary_receives_correct_mode`** (nuevo, parametrizado 3 casos): verifica que `call_args.kwargs["mode"]` sea el string correcto según `has_successful_run` y `full_scan`.

## Verificación

```
tests/test_main.py .................                                     [100%]

======================== 17 passed in 228.81s (0:03:48) ========================
```

Suite completa (`/home/enriquepaez/projects/saas-radar/.venv/bin/python -m pytest -q`):
```
.............................................................  [100%]
exit code 0
```
Todos los tests pasan.
