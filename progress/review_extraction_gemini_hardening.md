# Review — feature #23 extraction_gemini_hardening

**Veredicto:** APPROVED

## Checkpoints A1-A7

- A1: ✅ `call_gemini` emite `logger.warning` con `body[:500]` cuando el envelope carece de `candidates` (llm_clients.py:188), `parts` vacíos (llm_clients.py:193-197), `text` vacío (llm_clients.py:201) o `text` no parseable como JSON (llm_clients.py:205-206). Todas las ramas devuelven `None`.
- A2: ✅ `extract_problems_batch` emite `logger.warning` cuando `call_llm` devuelve `None` (extraction.py:349-352), con el `provider` incluido en el mensaje.
- A3: ✅ `extract_problems_batch` emite `logger.warning` cuando el resultado no tiene clave `"results"` (extraction.py:354-358), con `repr(result)[:500]` y el `provider`.
- A4: ✅ `EXTRACTION_PROVIDER_FALLBACK` existe en `config.py` línea 52: `(os.getenv("EXTRACTION_PROVIDER_FALLBACK") or "groq").lower()`. Default `"groq"`, sobreescribible por env var.
- A5: ✅ `run_batch_extraction` (extraction.py:432-480) verifica si el circuit breaker disparó, lee el fallback de `config.EXTRACTION_PROVIDER_FALLBACK`, compara con el provider original y, si son distintos y no está vacío, reintenta TODOS los posts desde 0 con el provider de respaldo exactamente UNA sola vez.
- A6: ✅ `test_call_gemini_envelope_without_candidates_logs_warning_and_returns_none` (test_llm_clients.py:301-313) usa `caplog.at_level` para verificar que aparece WARNING con "sin candidates" y "body[:500]" y que el resultado es `None`.
- A7: ✅ `test_run_batch_extraction_fallback_activates_when_circuit_breaker_fires_with_non_groq_provider` (test_extraction.py:579-621) mockea `call_llm` con `side_effect=fake_call_llm` que devuelve `None` para gemini y dict válido para groq; verifica que los 15 resultados finales son válidos (`has_problem=True`, sin `_error`).

## Suite de tests

- `tests/test_llm_clients.py`: 26 tests, todos verdes.
- `tests/test_extraction.py`: 28 tests, todos verdes.
- Total: 54 passed, 0 failed en 1.61s.

## `./init.sh`

Termina verde: `[OK] Entorno listo. Puedes empezar a trabajar.`

## Observaciones

Ningún cambio requerido. Los 2 fallos pre-existentes en `tests/test_pipeline_workflow.py` (de la feature #22) son ajenos al scope de esta feature, como documenta el implementer en `/home/enriquepaez/projects/saas-radar/progress/impl_extraction_gemini_hardening.md`.
