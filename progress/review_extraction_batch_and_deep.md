# Review — feature #9 (extraction_batch_and_deep) — revisión 2

**Veredicto:** APPROVED

## Checkpoints

- C1: [x] — Archivos base presentes. `./init.sh` termina verde (sale con code 0; el WARN de pytest es el mensaje histórico cuando el venv no está instalado, pero el script no falla por eso).
- C2: [x] — Solo la feature #9 en `in_progress`. `progress/current.md` describe la sesión activa.
- C3: [x] — `extraction.py` vive en `src/saas_radar/analysis/`. Sin `sys.path.append`. Sin mutación de globales de `config.py`. Logging vía `logging.getLogger(__name__)`. Sin `print()` de debug. Cabecera `from __future__ import annotations` presente.
- C4: [x] — 16 tests en `tests/test_extraction.py`, todos verdes. Suite completa: 194 passed. Los tests que tocan LLM usan `unittest.mock.patch`, no llamadas reales. Los tests que tocan BD mockan `_fetch_comments_for_post`. Nombres descriptivos, estilo función (no clases).
- C5: [x] — No aplica cambios de esquema en esta feature.
- C6: [ ] — Sesión aún activa; no evaluable hasta cierre.

## Criterios de aceptación

1. [x] `len(posts) <= 30 → usa extract_problem_deep` — líneas 406-407: `if len(posts) <= DEEP_EXTRACTION_THRESHOLD: return [extract_problem_deep(row) for row in posts]`. Tests `test_extract_problems_uses_deep_when_few_posts` lo verifica con mock de 5 posts.

2. [x] `len(posts) > 30 → batch de 5 posts/llamada con TEXT_SNIPPET_LEN=500` — líneas 408 y 331: `return run_batch_extraction(posts)` + `text = str(row.get("text", "")).strip()[:TEXT_SNIPPET_LEN]`. `TEXT_SNIPPET_LEN = 500` en `config.py`. Test `test_extract_problems_uses_batch_when_many_posts` lo verifica.

3. [x] `_clean_extractions` encadena 4 funciones puras — líneas 496-501: `_drop_who_vago → _drop_non_saas → _fix_workaround → _fix_payment_signal`. Cada función pura devuelve `(lista, contadores)` sin efectos secundarios externos.

4. [x] Workaround inferido desde texto — `_fix_workaround` (líneas 453-478): busca `_WORKAROUND_KEYWORDS` en el haystack y asigna `f"{label} (inferred)"`. Test `test_fix_workaround_inference` lo verifica con "spreadsheets".

5. [x] Posts sin workaround pero con dolor cuantificable se mantienen con `_weak_workaround=True` — líneas 475-477: `ex["current_workaround"] = "no explicit workaround mentioned"; ex["_weak_workaround"] = True`. Test `test_fix_workaround_kept_as_weak` lo verifica.

6. [x] Circuit breaker — `run_batch_extraction` (líneas 389-399): `consecutive_errors >= CIRCUIT_BREAKER_THRESHOLD (3)` → `break`. Test `test_circuit_breaker_fires`: 20 posts, batch_size=5, todos con `call_llm=None` → solo 15 resultados (3 batches procesados).

7. [x] Tests con mocks de `call_llm` cubriendo: schema válido (`test_extract_problems_batch_ok`), batch parcial (`test_extract_problems_batch_partial_results`), who vago descartado (`test_drop_who_vago`), dolor físico descartado (`test_drop_non_saas`), circuit breaker dispara (`test_circuit_breaker_fires`).

## Cambios requeridos

Ninguno.
