# Review — feature #11 (ai_analyzer_orchestrator)

**Veredicto:** APROBADO

## Checkpoints generales

- C1: [x] Archivos base del arnés completos. `./init.sh` termina verde.
- C2: [x] Solo feature #11 en `in_progress`. Dependencias #2 y #10 en `done`.
- C3: [x] `ai_analyzer.py` vive en `src/saas_radar/analysis/` (capa correcta). Sin `sys.path.append`. Sin mutación de globals de `config.py`. Provider recibido como argumento explícito.
- C4: [x] 8 tests en `tests/test_ai_analyzer.py`. 217 passed en suite completa. Tests usan `tmp_path` (no mock del filesystem). Tests de LLM usan `unittest.mock.patch` (no llamadas reales).
- C5: [x] No aplica directamente (no cambia schema). `init_db` es llamada en `run_ai_analysis` paso 1.
- C6: [ ] Sesión aún activa (pending cierre tras este review).

## Acceptance criteria de la feature #11

- AC1 [x]: `--use_cached_extractions` salta la extracción si existe `extractions_cache.json`. Verificado en `ai_analyzer.py` líneas 218-224 y cubierto por `test_use_cached_extractions` (verifica con `.assert_not_called()` que `run_batch_extraction` y `extract_problem_deep` no se invocan).

- AC2 [x]: Cache defensivo correcto. `_save_extractions_cache` (líneas 31-72): si `new_data` está vacío y `valid_old > 0`, no sobrescribe el cache original y escribe `<path>.failed.json`. Cubierto por `test_defensive_cache` (verifica contenido original intacto y existencia de `.failed.json`).

- AC3 [x]: Aborta antes de síntesis si `len(valid_extractions) < 2` (línea 232). Persiste `status='failed'` y retorna sin llamar a `call_llm`. Cubierto por `test_abort_too_few_valid` (verifica `mock_call_llm.assert_not_called()`).

- AC4 [x]: Persiste `analysis_runs` con status `'ok'`/`'partial'`/`'failed'` en los 4 puntos de salida del orquestador (líneas 195, 243, 275, 310). Cubierto por `test_full_pipeline_ok` (verifica `ok` en BD), `test_partial_status_when_no_opportunities` (verifica `partial` en BD), `test_llm_none_in_synthesis` (verifica `failed` en BD y `error_message` contiene "None"), `test_abort_too_few_valid` (verifica `status='failed'` en retorno).

- AC5 [x]: Tests cubren flujo completo (`test_full_pipeline_ok`), cache defensivo (`test_defensive_cache`), abort por len<2 (`test_abort_too_few_valid`), y `use_cached_extractions` (`test_use_cached_extractions`). Tests adicionales: `test_save_extractions_cache_no_prev`, `test_save_extractions_cache_with_new_data`, `test_partial_status_when_no_opportunities`, `test_llm_none_in_synthesis`.

## Verificación de convenciones

- Docstring de módulo presente. `from __future__ import annotations` en línea 3.
- Imports ordenados: stdlib → third-party (`pandas`) → internos (`saas_radar.*`).
- `logger = logging.getLogger(__name__)` en línea 25.
- `print()` usado únicamente en `_print_results` con justificación documentada (architecture.md §9: output visible al usuario). No hay `print()` sueltos de debug.
- Nombres en `snake_case`. Funciones privadas con prefijo `_` (`_save_extractions_cache`, `_print_results`, `_save_results`, `_extract_and_cache`, `_serialize_opportunities`).
- Sin `TODO` sin contexto.
- Ruff check: sin errores.

## Observaciones (no bloqueantes)

Ninguna.
