# Review — fix: provider no se propaga a extraction.py

**Veredicto:** APPROVED

## Checkpoints

- C1: [x] Las tres funciones públicas (`extract_problem_from_post`, `extract_problem_deep`, `extract_problems_batch`) tienen `provider: str = "claude"` y lo pasan a `call_llm(..., provider=provider)`. (extracción.py líneas 247, 292, 326)
- C2: [x] `run_batch_extraction` (circuit breaker) también recibe y propaga `provider` (línea 379), necesario para que el flujo normal con >30 posts no pierda el parámetro.
- C3: [x] `_extract_and_cache` en `ai_analyzer.py` recibe `provider` (línea 345) y lo pasa a `extract_problem_deep` y `run_batch_extraction`.
- C4: [x] Las dos llamadas a `_extract_and_cache` dentro de `run_ai_analysis` (líneas 224 y 226) pasan `provider=provider`.
- C5: [x] Tres tests nuevos en `tests/test_extraction.py` (líneas 376-445): `test_extract_problem_from_post_passes_provider`, `test_extract_problem_deep_passes_provider`, `test_extract_problems_batch_passes_provider`. Usan `mock_llm.call_args` para verificar `kwargs.get("provider") == "gemini"`.
- C6: [x] 19 tests pasan, 0 fallan (`pytest tests/test_extraction.py -v` → 19 passed in 0.26s).
- C7: [x] No se introdujeron cambios de comportamiento: todos los parámetros tienen default `"claude"`, preservando retrocompatibilidad.
- C8: [x] Cumple `architecture.md §3`: provider por argumento explícito, nunca mutación de estado global.
- C9: [x] Cumple `conventions.md`: snake_case, comillas dobles, `logger = logging.getLogger(__name__)`, sin `sys.path.append`.

## Cambios requeridos

Ninguno.
