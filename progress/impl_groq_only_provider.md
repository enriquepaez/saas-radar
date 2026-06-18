# Implementación: groq_only_provider — Eliminar Claude y Gemini, dejar solo Groq

## Qué cambió

- **`src/saas_radar/config.py`**: eliminadas `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `AI_PROVIDER`, `EXTRACTION_PROVIDER`, `EXTRACTION_PROVIDER_FALLBACK`, `SYNTHESIS_PROVIDER_FALLBACK`, `ANTHROPIC_API_URL`, `CLAUDE_EXTRACTION_MODEL`, `CLAUDE_SYNTHESIS_MODEL`, `GEMINI_API_URL`, `GEMINI_MODEL`. Se mantienen `GROQ_API_KEY`, `GROQ_API_URL`, `GROQ_MODEL` y todas las demás variables del proyecto.

- **`src/saas_radar/analysis/llm_clients.py`**: eliminadas `call_claude()`, `call_gemini()`, `_CLAUDE_URL`, `_GEMINI_BASE_URL`. Simplificada `call_llm()`: ahora acepta solo `(prompt, max_tokens, max_retries)` y delega directamente a `call_groq()`. Eliminados parámetros `provider` y `phase`. Import de `re` se mantiene porque lo usa `call_groq` para parsear el tiempo de espera del rate limit.

- **`src/saas_radar/analysis/extraction.py`**: eliminado parámetro `provider` de `extract_problem_from_post`, `extract_problem_deep`, `extract_problems_batch`, `_run_batches_with_circuit_breaker`, `run_batch_extraction`, `extract_problems`. Eliminada toda la lógica de fallback a provider alternativo en `run_batch_extraction`. Eliminado `from saas_radar import config` (ya no se usa). Las llamadas a `call_llm` ya no pasan `phase=` ni `provider=`.

- **`src/saas_radar/analysis/ai_analyzer.py`**: eliminado `from saas_radar import config`. Eliminado parámetro `provider` de `run_ai_analysis` y `_extract_and_cache`. Eliminada `extraction_provider = config.EXTRACTION_PROVIDER`. Eliminado bloque de fallback de síntesis (`if raw is None: fallback_provider = config.SYNTHESIS_PROVIDER_FALLBACK ...`). Campo `ai_provider` en BD hardcodeado a `"groq"`. Llamadas a `call_llm` sin `phase=` ni `provider=`. Llamadas a funciones de extracción sin `provider=`.

- **`src/saas_radar/main.py`**: eliminado `provider=os.getenv("AI_PROVIDER", "claude")` de las llamadas a `run_ai_analysis`, `phase_heuristic_tuner`, y `run_all_pending`. Simplificadas las firmas de `phase_heuristic_tuner` y `phase_gtm`.

- **`src/saas_radar/agents/gtm_agent.py`**: eliminado parámetro `provider` de `_generate_gtm`, `_process_opportunity`, `run_all_pending`. La llamada a `call_llm` ya no pasa `phase=` ni `provider=`. Eliminado `--provider` del CLI. Actualizado epilog de argparse.

- **`src/saas_radar/agents/heuristic_tuner.py`**: eliminado parámetro `provider` de `generate_heuristic_suggestions`. La llamada a `call_llm` ya no pasa `phase=` ni `provider=`. Eliminado `--provider` del CLI de `_parse_args`.

- **`.github/workflows/pipeline.yml`**: eliminadas las variables de entorno `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `AI_PROVIDER`, `EXTRACTION_PROVIDER` del bloque `env:`. Solo queda `GROQ_API_KEY` como secret de LLM.

- **`tests/test_llm_clients.py`**: eliminados imports y todos los tests de `call_claude` (4 tests) y `call_gemini` (7 tests). Eliminados tests de `call_llm` que dependían de `provider=` o `phase=`. Añadidos tests `test_call_llm_delegates_to_groq` y `test_call_llm_passes_max_tokens_to_groq`.

- **`tests/test_ai_analyzer.py`**: eliminado `test_synthesis_fallback_on_none` (Test 10). Eliminado `test_extraction_uses_extraction_provider` (Test 9). Eliminado parámetro `provider=` de las llamadas a `run_ai_analysis`. Añadida verificación de `ai_provider="groq"` en BD. Añadidos `test_extract_and_cache_uses_deep_for_few_posts` y `test_extract_and_cache_uses_batch_for_many_posts`.

- **`tests/test_extraction.py`**: eliminado `from saas_radar import config`. Eliminados tests `test_extract_problem_from_post_passes_provider`, `test_extract_problem_deep_passes_provider`, `test_extract_problems_batch_passes_provider`. Eliminados 4 tests de fallback (`test_run_batch_extraction_fallback_*`). Actualizados tests de warning log (ya no comprueban `provider=gemini` en el mensaje). Añadidos tests `test_extract_*_calls_llm`.

- **`tests/test_config.py`**: eliminados tests `test_ai_provider_default`, `test_ai_provider_env_override`, `test_ai_provider_groq_override`, `test_anthropic_api_key_override`, `test_gemini_api_key_override`, `test_llm_api_urls_present` (referencias a Anthropic/Gemini), `test_claude_model_defaults`, `test_extraction_provider_*`, `test_ai_provider_empty_string_*`. Añadidos `test_llm_api_url_groq_present` y `test_groq_model_present`.

- **`tests/test_pipeline_workflow.py`**: actualizado `test_has_required_env_secrets` para reflejar la lista reducida de secrets y verificar que los eliminados NO están presentes.

- **`tests/test_main_gtm_phase.py`**: reemplazado `test_phase_gtm_uses_env_provider` (que verificaba que `AI_PROVIDER=gemini` se propagaba) por `test_phase_gtm_calls_run_all_pending` (que verifica que `provider` ya no es un parámetro).

- **`tests/test_gtm_agent.py`**: eliminado parámetro `provider="claude"` de todas las llamadas a `_generate_gtm`, `_process_opportunity`, y `run_all_pending`.

- **`tests/test_heuristic_tuner.py`**: eliminado parámetro `provider="claude"` de todas las llamadas a `generate_heuristic_suggestions`. Actualizado CLI test para no pasar `--provider claude`.

## Por qué

El proyecto tenía tres providers LLM con lógica de selección, fallback y configuración por variables de entorno. Esta complejidad (EXTRACTION_PROVIDER separado de AI_PROVIDER, SYNTHESIS_PROVIDER_FALLBACK, EXTRACTION_PROVIDER_FALLBACK) no aportaba valor ya que en producción solo se usaba Groq para extracción y fallback. La decisión de usar solo Groq simplifica el código y elimina dependencias de API keys que no se usaban activamente, reduciendo la superficie de configuración de 6 variables LLM a 1.

Alternativa descartada: mantener la infraestructura de múltiples providers con Groq como único valor posible. Descartada porque deja código muerto con parámetros que no sirven.

## Impacto en el pipeline

- **LLM**: todos los pasos (extracción, síntesis, GTM, heurísticas) usan Groq automáticamente.
- **Scraping**: sin impacto.
- **BD**: el campo `ai_provider` en `analysis_runs` siempre se persiste como `"groq"`.
- **Telegram**: sin impacto.
- **CLI**: `gtm_agent.py` y `heuristic_tuner.py` ya no aceptan `--provider`. Los scripts de CI eliminan las env vars de Claude y Gemini.
- **Circuit breaker**: se mantiene para detectar fallos consecutivos de Groq, pero ya no hace fallback a otro provider.

## Explicación técnica

### `config.py`
Las variables eliminadas leían de `os.getenv(..., "default")` y proveen credenciales y URLs para APIs que ya no se llaman. Su ausencia no rompe ningún import porque ningún módulo las referencia.

### `llm_clients.py`
`call_llm` antes era un dispatcher `if provider == "claude": ... elif provider == "gemini": ... elif provider == "groq": ...`. Ahora es una función de una línea: `return call_groq(prompt, max_tokens=max_tokens, max_retries=max_retries)`. El parámetro `phase` era exclusivo de Claude (elegía entre `CLAUDE_EXTRACTION_MODEL` y `CLAUDE_SYNTHESIS_MODEL`); al no existir Claude, el parámetro carece de sentido.

### `extraction.py`
El parámetro `provider` en las funciones de extracción era simplemente forwarded a `call_llm`. Al eliminar el dispatch, no hay nada que propagar. La función `run_batch_extraction` antes hacía un segundo pase completo con el provider de fallback cuando el circuit breaker disparaba; ahora simplemente devuelve los resultados parciales (que pueden incluir `_error=True`). El circuit breaker en sí (`_run_batches_with_circuit_breaker`) se mantiene porque sigue siendo útil para detener early si Groq falla repetidamente.

### `ai_analyzer.py`
Antes `run_ai_analysis` leía `config.EXTRACTION_PROVIDER` al nivel de entrada y lo pasaba a `_extract_and_cache` como `extraction_provider` — siguiendo el principio de architecture.md §3 "solo el nivel de entrada lee config". Al no haber múltiples providers, no hay nada que leer ni propagar. El bloque de fallback de síntesis (que rellamaba a `call_llm` con otro provider si el primero devolvía None) se eliminó; si Groq falla en síntesis, el run se marca directamente como `"failed"`.

### `gtm_agent.py`
`_generate_gtm(opp, provider)` recibía el provider para pasárselo a `call_llm`. Al simplificar `call_llm`, `_generate_gtm` ya no necesita saber el provider. Lo mismo aplica a `_process_opportunity` y `run_all_pending`.

### `heuristic_tuner.py`
`generate_heuristic_suggestions(meta_json_path, top_posts_df, provider)` usaba `provider` solo para la llamada a `call_llm(..., provider=provider, phase="synthesis")`. Con la nueva `call_llm` de firma simplificada, el argumento desaparece.

## Tests añadidos

- `test_call_llm_delegates_to_groq`: verifica que `call_llm` sin argumentos extra enruta a Groq.
- `test_call_llm_passes_max_tokens_to_groq`: verifica que `max_tokens` se transmite correctamente al body de la request.
- `test_extract_problem_from_post_calls_llm`: verifica que `call_llm` es invocado exactamente una vez.
- `test_extract_problem_deep_calls_llm`: ídem para extracción deep.
- `test_extract_problems_batch_calls_llm`: ídem para extracción en batch.
- `test_full_pipeline_ok` (actualizado): verifica también que `ai_provider="groq"` queda en BD.
- `test_extract_and_cache_uses_deep_for_few_posts`: verifica que con N<=30 posts se usa `extract_problem_deep`.
- `test_extract_and_cache_uses_batch_for_many_posts`: verifica que con N>30 posts se usa `run_batch_extraction`.
- `test_phase_gtm_calls_run_all_pending`: verifica que `run_all_pending` se llama sin `provider`.
- `test_has_required_env_secrets` (actualizado): verifica secrets reducidos y ausencia de los eliminados.

## Verificación

Suite completa: 408 passed, 4 skipped in 172.39s. `init.sh` termina con `[OK] Entorno listo`.

## Fix tests ai_analyzer

### Problema

Los tests 1, 2, 7 y 8 de `tests/test_ai_analyzer.py` tenian 3 posts (N=3, menor o igual que `DEEP_EXTRACTION_THRESHOLD`=30) y mockeaban `run_batch_extraction` pero no `extract_problem_deep`. La funcion `_extract_and_cache` en `ai_analyzer.py` toma la rama `len(posts_list) <= DEEP_EXTRACTION_THRESHOLD` y llama a `extract_problem_deep` directamente sin pasar por `run_batch_extraction`. Con `GROQ_API_KEY` configurada en el entorno, los tests sin ese mock hacian llamadas reales a la API y colgaban.

### Tests afectados y fix aplicado

Para cada test se añadio la linea dentro del bloque `with (...)`:

```python
patch("saas_radar.analysis.ai_analyzer.extract_problem_deep", return_value=_make_extraction(0)),
```

Tests modificados:
- `test_full_pipeline_ok` (test 1): 3 posts, sin mock de `extract_problem_deep`.
- `test_abort_too_few_valid` (test 2): 3 posts, sin mock de `extract_problem_deep`.
- `test_partial_status_when_no_opportunities` (test 7): 3 posts, sin mock de `extract_problem_deep`.
- `test_llm_none_in_synthesis` (test 8): 3 posts, sin mock de `extract_problem_deep`.

Tests que NO necesitaban fix:
- Test 3 (`test_defensive_cache`): llama directamente a `_save_extractions_cache`, no a `run_ai_analysis`.
- Test 4 (`test_use_cached_extractions`): usa `use_cached_extractions=True`, la rama de cache se toma antes de llegar a `_extract_and_cache`, y ya tenia `extract_problem_deep` mockeado.
- Tests 5 y 6: llaman a `_save_extractions_cache` directamente.
- Tests 9a y 9b: llaman a `_extract_and_cache` directamente y ya tenian los mocks correctos.

### Verificacion post-fix

`tests/test_ai_analyzer.py`: 10 passed in 0.31s.
Suite completa: exit code 0 (pasada sin errores).
