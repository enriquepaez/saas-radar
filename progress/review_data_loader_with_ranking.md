# Review — feature #7 data_loader_with_ranking

**Veredicto:** CHANGES_REQUESTED

## Checkpoints

- C1: [x] — Todos los archivos base existen. `./init.sh` termina verde (el WARN de pytest es esperado porque el script no activa el venv; `python -m pytest` dentro del venv pasa 156/156).
- C2: [x] — Una sola feature en `in_progress`. `progress/current.md` describe la sesión activa.
- C3: [ ] — Ver hallazgo #1: `print()` en módulo de capa `analysis/`, violación de convenciones.
- C4: [x] — 15 tests, todos verdes. BD temporal con `tmp_path`. Monkey-patch correcto de `saas_radar.analysis.data_loader.engine`. Ruff reporta 1 error fixable en el test file (ver hallazgo #2).
- C5: [x] — No aplica directamente a esta feature (no altera el schema).
- C6: [ ] — Sesión aún abierta; no aplicable aún.

## Criterios de aceptación

1. [x] `load_pain_posts(min_score=5, top_n=20)` devuelve DataFrame con ≤20 filas — implementado y cubierto por `test_top_n_limits_result`.
2. [x] Filtros SUBREDDITS, PAIN_CATEGORIES, score>=min_score, len(text)>100, created_utc>=cutoff — líneas 109-117 de `data_loader.py`. Cubiertos por tests `test_temporal_filter_removes_old_posts`, `test_min_score_filter`, `test_category_filter`.
3. [x] Recalcula `_semantic_score` ignorando el valor persistido — líneas 120-122. Cubierto por `test_semantic_score_recalculated_not_from_db`.
4. [x] Filtro semantic_score >= MIN_SEMANTIC_SCORE (1.5) — línea 124. Cubierto por `test_semantic_filter_removes_low_score_posts`.
5. [x] Merge `load_pain_comments_as_posts` con source='comment' y pseudo-título ≤120 chars — líneas 65-83. Cubierto por `test_comments_loaded_as_posts`, `test_comments_pseudo_title_first_sentence`, `test_include_comments_merges_into_posts`.
6. [x] Ranking blend 0.10/0.15/0.75 normalizado por subreddit — líneas 140-150. Cubierto por `test_ranking_formula_applied`.
7. [x] Cap por subreddit: HIGH_SIGNAL→10, default→4 — líneas 152-159. Cubierto por `test_cap_high_signal_subreddit` y `test_cap_default_subreddit`.
8. [x] Tests con BD temporal fixture — fixture `test_engine` en `tests/test_data_loader.py` usa `tmp_path / "test.db"`.

## Cambios requeridos

### 1. `print()` en módulo de capa `analysis/` — violación de `docs/conventions.md`

`docs/conventions.md` dice: "Sí usar `print()` para el 'user output' del CLI: cabeceras de fase visibles al humano. Eso lo lee el humano." y "No usar `print()` para errores / debug." También dice explícitamente: "No mezclar IO con lógica pura: las funciones de `analysis/` que no son orquestadoras NO tocan disco ni red." `data_loader.py` no es el CLI; es la capa `analysis/`. El output de progreso que hoy va a `print()` debe ir a `logger.info()` o `logger.warning()`.

Líneas afectadas en `src/saas_radar/analysis/data_loader.py`:
- Línea 118: `print(f"  Filtro temporal (<{post_age_days}d): ...")` → debe ser `logger.info(...)`
- Línea 125: `print(f"  Pre-filtro semántico posts: ...")` → debe ser `logger.info(...)`
- Línea 133: `print(f"  Comentarios con señal de dolor: ...")` → debe ser `logger.info(...)`
- Línea 137: `print("  [WARN] Sin posts tras el filtro.")` → debe ser `logger.warning(...)`
- Línea 161: `print("  Top posts seleccionados ...")` → debe ser `logger.info(...)`
- Línea 163: `print(f"    [{r['semantic_score']:+.0f}] ...")` → debe ser `logger.debug(...)`

### 2. Import desordenado en `tests/test_data_loader.py` — ruff I001

`ruff check tests/test_data_loader.py` devuelve `I001: Import block is un-sorted or un-formatted`. El docstring de módulo está en la línea 1, pero la directiva `from __future__ import annotations` aparece en la línea 3 (antes del docstring a nivel de archivo no es el problema; el problema es el orden de los bloques según ruff). Ejecutar `ruff check --fix tests/test_data_loader.py` para corregirlo automáticamente.

### Cambios requeridos en resumen

1. En `src/saas_radar/analysis/data_loader.py`: sustituir los 6 `print()` (líneas 118, 125, 133, 137, 161, 163) por llamadas al `logger` ya declarado en la línea 23.
2. En `tests/test_data_loader.py`: ejecutar `ruff check --fix tests/test_data_loader.py` para ordenar los imports (error I001).

Los tests pasan (156/156). La lógica de negocio es correcta. Solo fallan las dos convenciones de estilo citadas.

---

# Review segunda pasada — feature #7 data_loader_with_ranking

**Veredicto:** APROBADO

## Verificaciones

1. `print()` en `src/saas_radar/analysis/data_loader.py`: ninguna ocurrencia — solo llamadas a `logger.*`. OK
2. `ruff check src/saas_radar/analysis/data_loader.py tests/test_data_loader.py`: `All checks passed!` OK
3. `python -m pytest -q`: 156 passed en 0.76s, sin errores ni warnings. OK

Todas las correcciones solicitadas en el primer review han sido aplicadas correctamente.
