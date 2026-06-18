# Review — refactor/groq-only-provider

**Veredicto:** CHANGES_REQUESTED

## Checkpoints

- C1: [x] Archivos base presentes; `./init.sh` termina exit 0.
- C2: [x] Estado coherente; no hay features en `in_progress` afectadas.
- C3: [x] Arquitectura respetada. Sin `sys.path.append`. Sin mutación de globales de config en runtime. Sin capas nuevas.
- C4: [ ] Tests de `test_ai_analyzer.py` cuelgan — ver detalle abajo.
- C5: [x] No aplica directamente a este refactor (no toca BD).
- C6: [ ] `progress/current.md` vacío (la sesión no registró actividad), pero el impl está en `progress/impl_groq_only_provider.md`.

## Verificación de la limpieza (puntos 1-9 del encargo)

**1. Sin referencias a claude/gemini/ANTHROPIC en `src/`:**
`grep -rn "claude\|gemini\|CLAUDE\|GEMINI\|ANTHROPIC" src/ --include="*.py"` → vacío. CORRECTO.

**2. `config.py`:** `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `AI_PROVIDER`, `EXTRACTION_PROVIDER`, `EXTRACTION_PROVIDER_FALLBACK`, `SYNTHESIS_PROVIDER_FALLBACK`, `ANTHROPIC_API_URL`, `CLAUDE_EXTRACTION_MODEL`, `CLAUDE_SYNTHESIS_MODEL`, `GEMINI_API_URL`, `GEMINI_MODEL` eliminadas. Solo quedan `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_API_URL`. CORRECTO.

**3. `llm_clients.py`:** `call_claude()` y `call_gemini()` eliminadas. `call_llm()` simplificada sin `provider` ni `phase`. CORRECTO.

**4. `ai_analyzer.py`:** Sin lógica de fallback de síntesis. Sin lógica de `EXTRACTION_PROVIDER`. `ai_provider` hardcodeado como `"groq"`. CORRECTO.

**5. `extraction.py`:** Sin parámetro `provider` en ninguna función. Sin fallback en `run_batch_extraction`. CORRECTO.

**6. `agents/gtm_agent.py` y `agents/heuristic_tuner.py`:** Sin referencias a `"claude"` o `"gemini"` como providers. CORRECTO.

**7. `.github/workflows/pipeline.yml`:** `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `AI_PROVIDER`, `EXTRACTION_PROVIDER` eliminados del bloque `env:`. CORRECTO.

**8. Tests — FALLO:**
Los tests en `tests/test_ai_analyzer.py` que llaman a `run_ai_analysis` cuelgan haciendo llamadas reales a la API de Groq. Confirmado en tests:
- `test_full_pipeline_ok` — cuelga
- `test_abort_too_few_valid` — cuelga
- `test_use_cached_extractions` — cuelga
- `test_partial_status_when_no_opportunities` — cuelga
- `test_llm_none_in_synthesis` — cuelga

**Causa raíz:** Los tests parchean `saas_radar.analysis.ai_analyzer.run_batch_extraction` pero no parchean `extract_problem_deep`. Con N=3 posts (≤ `DEEP_EXTRACTION_THRESHOLD=30`), `_extract_and_cache` toma el camino deep y llama a `extract_problem_deep` en `extraction.py`, que a su vez llama a `call_llm` en `saas_radar.analysis.extraction` — un namespace distinto al parcheado en ai_analyzer. La llamada real llega a Groq y espera 60s de rate limit indefinidamente.

**Aclaración importante:** estos mismos tests ya colgaban en `main` antes de este PR (verificado). Son un bug pre-existente que este refactor no introdujo. Sin embargo, las reglas del proyecto dicen que los tests deben pasar — y no pasan.

**9. Coherencia de callers:** Ningún caller en `src/` sigue pasando `provider=` ni `phase=` a `call_llm`. Verificado con `grep -rn "provider=\|phase=" src/ --include="*.py"` → vacío. CORRECTO.

## Cambios requeridos

1. **`tests/test_ai_analyzer.py`** — Corregir los 5 tests que cuelgan añadiendo el mock que falta. Para cada test que llama a `run_ai_analysis` con N posts <= 30 (deep path), añadir un patch sobre `extract_problem_deep` en el namespace de ai_analyzer:

   ```python
   patch("saas_radar.analysis.ai_analyzer.extract_problem_deep", return_value=_make_extraction(0))
   ```

   Alternativamente, parchear `saas_radar.analysis.extraction.call_llm` con `respx.mock` para que no haga llamadas reales. La elección es del implementer, pero el resultado debe ser que los 5 tests pasen sin colgar.

   Los tests afectados (en `tests/test_ai_analyzer.py`):
   - `test_full_pipeline_ok` (línea 167)
   - `test_abort_too_few_valid` (línea 211)
   - `test_use_cached_extractions` (línea ~315)
   - `test_partial_status_when_no_opportunities` (línea ~400)
   - `test_llm_none_in_synthesis` (línea ~438)

   Nota: `test_defensive_cache` y los tests de `_extract_and_cache` ya están correctamente mockeados y pasan.

2. No hay más cambios requeridos en código de producción (`src/`). La limpieza es completa y correcta.
