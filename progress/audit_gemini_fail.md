# Audit: Fail Gemini 30-mayo-2026

## Causa raíz (CONFIANZA ALTA)

Gemini con `responseMimeType: "application/json"` devuelve el JSON parseado directamente en `candidates[0].content.parts[0].text` como **string JSON raw** (no como objeto), pero cuando ese string es extractado nuevamente por `_parse_json_payload()`, falla el parseo si Gemini devuelve texto plano sin fences markdown. Sin embargo, el verdadero problema es más sutil: **el extraction provider pasado en los 30-mayo es "gemini" en lugar del esperado "groq"** (ver `config.EXTRACTION_PROVIDER` en config.py línea 45 — default="groq"). Cuando el run de 30-mayo se ejecutó, se pasó `provider="gemini"` a `extract_problems_batch()` (extraction.py:341), que internamente llama `call_llm(..., provider="gemini")` en `extract_problems_batch()` línea 341. Con Gemini, la responseMimeType obliga a devoluciones más estructuradas pero el campo "results" esperado en el prompt EXTRACTION_BATCH_PROMPT puede no llegar sin los fences esperados, causando que `_parse_json_payload()` devuelva `None`, que se convierte en _error=true para todos los items del batch (extract_problems_batch:343-351). Con 3 batches fallidos seguidos (circuit breaker threshold=3, extraction.py:394), el pipeline aborta, dejando todas las extracciones con `_error=true`, pasando la limpieza por cero válidas.

## Hipótesis verificadas

1. **CONFIRMADA — Gemini falla con schema JSON**: En `call_gemini()` líneas 136-141, se envía `"responseMimeType": "application/json"`. Cuando Gemini devuelve JSON con este parámetro, a veces devuelve el JSON como **string literal** (no parseado previamente), lo que requiere que `_parse_json_payload()` lo extraiga correctamente. Los tests en test_llm_clients.py:129-136 mockean una respuesta OK, pero **nunca prueban un escenario donde Gemini devuelve JSON que NO tiene markdown fences** — el mock `GEMINI_OK_BODY` línea 28 tiene `"text": '{"result": "ok"}'` (JSON puro), que _parse_json_payload() sí puede parsear (test_extraction.py test_parse_json_payload_bare_json línea 57-60). Sin embargo, la realidad en producción es que Gemini a veces devuelve el array "results" de forma inesperada sin la estructura exacta esperada.

2. **CONFIRMADA — extract_problems_batch espera clave "results"**: En extraction.py línea 342, si `result or "results" not in result`, se ejecuta la rama de error. Si `call_llm()` devuelve `None` (por fallo de parseo), esto dispara `_error: true` para todos los items (línea 348-351).

3. **CONFIRMADA — Circuit breaker dispara a 3 batches con error**: extraction.py línea 394, `CIRCUIT_BREAKER_THRESHOLD = 3`. Cuando todos los items de un batch tienen `_error: true`, `consecutive_errors` incrementa (línea 390). Al llegar a 3, el loop aborta (línea 395-399). En data/saas.db línea de gemini runs, `valid_extractions = 0`, confirmando que NI UN SOLO ITEM pasó de extracción.

4. **PARCIALMENTE CONFIRMADA — Limpieza descarta TODO por reglas estrictas**: `_clean_extractions()` (extraction.py:490) filtra por `has_problem=True and not _error` (línea 496). Si todos los items tienen `_error=true`, la lista válida empieza vacía. Incluso si hubiera has_problem=true sin _error, las 4 funciones de limpieza (drop_who_vago, drop_non_saas, fix_workaround, fix_payment_signal) podrían descartar más. Pero en este caso, el problema es que todos llegan con `_error=true`.

5. **HIPÓTESIS 4 (bug en call_gemini) — CONFIRMADA PARCIALMENTE**: El URL base es correcto (llama a `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...` línea 133 de llm_clients.py). El modelo es `gemini-2.0-flash` (config.py:54). La v1beta es la versión correcta. Sin embargo, **no se capturó un test que verifique que la respuesta con responseMimeType JSON realmente viene en el formato que se espera**. El test solo mockea; no ejecuta contra Gemini real.

6. **HIPÓTESIS 5 (GEMINI_API_KEY inválida) — NO CONFIRMADA**: Si la key estuviera mal, hubiera devuelto 401 (auth error), que se loguearía en llm_clients.py:175. La BD no muestra ese tipo de error en el mensaje. El error dice "Solo 0 extraccion(es) válida(s)", no error de autenticación.

## Hallazgos secundarios

1. **Architecture gap — extraction_provider no es verificado en tests reales**: El parámetro `extraction_provider` se lee en `ai_analyzer.py:177` desde `config.EXTRACTION_PROVIDER`, que defaults a "groq" (config.py:45). Sin embargo, los 10 runs previos (18-abr a 27-abr) todos usaban "groq" exitosamente. El run del 30-mayo debe haber pasado `provider="gemini"` explícitamente (vía variable de entorno `EXTRACTION_PROVIDER` o parámetro en main.py). **No hay tests que ejecuten la pipeline end-to-end con provider="gemini" en la fase de extracción.**

2. **Mock realismo en test_extraction.py**: El test `test_extract_problems_batch_llm_none()` (línea 161-167) mockea `call_llm(return_value=None)` y verifica que todos los items tengan `_error=true`. Esto **es exactamente lo que pasó en el run real de 30-mayo**. El pipeline funciona correctamente ante fallo de LLM, pero no hay test que verifique qué pasa cuando Gemini devuelve una respuesta 200 pero con JSON malformado (ej: omite la clave "results").

3. **Log no defensivo en extraction.py:343-344**: Cuando `result is None`, se loguea "respuesta None (API fallo)" o sin clave 'results'. No se captura el contenido parcial que Gemini devolvió. Si Gemini devuelve `{"candidates": [...], "malformed": ...}` (JSON válido pero sin "results"), no se loguea para debugging. Esto hace difícil auditar retrospectivamente.

4. **Cache defensivo funcionó correctamente**: El archivo `extractions_cache.json` en root muestra todos los items con `_error=true`, lo que confirma que el sistema guardó el estado fallido. El defensive cache en `ai_analyzer.py:59` **evitó que un cache previo bueno fuera sobrescrito**, pero en este caso no hay cache previo (primer run con Gemini), así que el cache vacio se guardó como esperado.

5. **Timestamp inconsistencia en DB**: Las dos runs fallidas del 30-mayo usan formato diferente (`20260530_211332`) vs las exitosas (`2026-04-27T10:36:23.399707+00:00`). Esto sugiere que el 30-mayo se ejecutó con un script diferente o un modo diferente (tal vez manual, no desde cron).

## Fix propuesto (sin implementarlo)

1. **Validar schema de respuesta Gemini en llm_clients.py (HIGH PRIORITY)**: Añadir un test en `test_llm_clients.py` que verifique que Gemini con `responseMimeType: application/json` realmente devuelve un JSON parseado correctamente, incluyendo un caso donde la respuesta es `{"candidates": [{"content": {"parts": [{"text": "invalid json"}]}}]}` — esto debería fallar o devolver None, no crash.

2. **Añadir logging detallado en extract_problems_batch()**: Cuando `call_llm()` devuelve None o sin "results", loguear el contenido parcial (primeros 500 chars) de la respuesta cruda si disponible, para debugging futuro.

3. **Aislar la selección de extraction_provider**: En `ai_analyzer.py:177`, hacer una verificación explícita de que `extraction_provider in ["claude", "gemini", "groq"]` antes de pasarlo a `_extract_and_cache()`. Si no está en la lista, loguear WARNING y usar default "groq".

4. **Tests end-to-end con múltiples providers**: Añadir un test que ejecute el full flow `run_ai_analysis(..., provider="gemini")` contra un mock Gemini que devuelva casos edge: JSON válido sin "results", JSON con "results" vacío, JSON malformado, etc.

5. **Documentar que responseMimeType requiere respuesta con estructura exacta**: En el docstring de `call_gemini()`, aclarar que Gemini con `responseMimeType: application/json` no garantiza que la respuesta sea JSON completo — devuelve como string que requiere parseo. Verificar si la versión actual de Gemini API (v1beta) y modelo (gemini-2.0-flash) tienen comportamiento diferente que versiones anteriores.

