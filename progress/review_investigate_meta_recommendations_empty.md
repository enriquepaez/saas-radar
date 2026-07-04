# Review — feature #28 `investigate_meta_recommendations_empty`

**Veredicto:** APROBADO

**Fecha:** 2026-07-04 · **Rama:** `feat/28-investigate_meta_recommendations_empty` (working tree sin commitear)

## Acceptance criteria (feature_list.json #28)

| # | Criterio | Estado | Evidencia |
|---|----------|--------|-----------|
| 1 | Diagnóstico documentado con causa raíz + evidencia | ✅ | `progress/impl_investigate_meta_recommendations_empty.md` §Diagnóstico, apoyado en `progress/explore_meta_code.md` (grep: 0 callers en `src/` de `generate_meta_analysis`/`save_meta_analysis`) y `progress/explore_meta_runtime.md` (logs de Actions runs 28703615830/28703083871 sin fase meta; BD `db-20260704`: 27 `analysis_runs`, 0 `meta_recommendations`). |
| 2 | Fix aplicado; el error no se traga sin log | ✅ | `src/saas_radar/analysis/ai_analyzer.py:344-356` — Paso 9 cableado tras `persist_run_to_db`; `except Exception` → `logger.warning(..., exc_info=True)` (traceback completo) sin abortar el return. |
| 3 | Test que reproduce el escenario y verifica que `meta_recommendations` se puebla | ✅ | `tests/test_ai_analyzer.py::test_meta_analysis_populates_recommendations_and_writes_json` — run simulado → `COUNT(*) >= 1` en `meta_recommendations` con el `run_id` del run + `<ts>_meta.json` junto al `<ts>_results.json`. |
| 4 | Suite completa verde | ✅ | `.venv/bin/pytest -q` → **exit code 0** (verificado dos veces, incluida captura explícita del exit code). `./init.sh` → exit 0. |

## Verificación de los puntos críticos

1. **Cableado correcto** (`ai_analyzer.py:344-356`): el Paso 9 va DESPUÉS de `persist_run_to_db` (línea 327) → `run_id` real disponible. Argumentos cotejados contra las firmas de `meta_analysis.py`: `generate_meta_analysis(extractions=valid_extractions, opportunities=opps, post_age_days=post_age_days, db_url=db_url)` y `save_meta_analysis(meta, json_path, run_id=run_id, db_url=db_url)` coinciden exactamente (kwargs explícitos). `print_meta_summary(meta, db_url=db_url)` correcto. Los 3 early-returns de fallo devuelven `meta_json_path: None` — contrato homogéneo.
2. **try/except**: `meta_json_path` inicializado a `None` antes del `try` (sin `UnboundLocalError` posible); `except Exception` (no `BaseException`, deja pasar `KeyboardInterrupt`); WARNING con `exc_info=True`. Test 12 (`test_meta_analysis_failure_does_not_abort_run`) verifica con caplog: status `ok`, run persistido en BD, 1 registro WARNING con `exc_info is not None`.
3. **Rutas unificadas**: `_save_results` escribe `<output>/<ts>_results.json`; `_derive_meta_path` (`meta_analysis.py:159-174`) opera solo sobre `Path.name` + `with_name` → `<output>/<ts>_meta.json`; `main.py:265` globa `os.path.join(output, "*_meta.json")` sobre el MISMO `output` que se pasa como `output_path` a `run_ai_analysis` (main.py:254). Escritura y búsqueda derivan del mismo valor: no pueden divergir. El caso `data/ai_analysis.json/` (dir con `.json` en el nombre) cubierto por test 11 de `test_ai_analyzer.py` (misma expresión de glob que main.py) y test 8 de `test_meta_analysis.py`. Fallbacks: `run.json` → `run_meta.json` (comportamiento histórico, test 9) y nombre sin extensión. `os.makedirs(... or ".")` evita `FileNotFoundError` con rutas sin directorio.
4. **Callers antiguos de `save_meta_analysis`**: grep exhaustivo — no existía NINGÚN caller previo en `src/` (esa era la causa raíz) ni test previo de `save_meta_analysis` (el import en `test_meta_analysis.py` es nuevo). Nada que romper.
5. **0 opportunities + N extracciones** (caso mayoritario de producción): `generate_meta_analysis` solo usa `opportunities` en `len()` (meta_analysis.py:123). Cubierto por el test 7 preexistente (`opportunities=[]`) y end-to-end por `test_partial_status_when_no_opportunities`, que ya NO mockea el meta paso y pasa verde.
6. **Wiring fase 4.5**: `test_main.py::test_phase45_glob_finds_meta_json_in_output_dir` verifica que `phase_heuristic_tuner` recibe exactamente la ruta escrita en `output_path`.
7. **Scope limpio**: solo `src/saas_radar/{analysis/ai_analyzer.py, analysis/meta_analysis.py, main.py}`, `tests/{test_ai_analyzer,test_main,test_meta_analysis}.py`, `progress/` y `feature_list.json` (#28 `pending`→`in_progress`, NO `done` — correcto en fase de review). Sin cambios en `.github/`, `data/`, workflows ni schema de BD.
8. **Convenciones**: `ruff check` exit 0 en los 6 archivos tocados; sin `print()` de debug (el `print_meta_summary` es user-output de CLI, permitido por conventions.md §Logging); logging con lazy `%s`; tipos `str | None`; tests con `tmp_path` y mocks de LLM (sin llamadas reales). Limpieza de lint colateral en `test_main.py` (imports muertos) — aceptable.
9. **Informe impl**: incluye diagnóstico con evidencia y explicación línea a línea (regla pedagógica de CLAUDE.md) — completo.

## Checkpoints (CHECKPOINTS.md)

- C1: [x] — arnés completo; `./init.sh` exit 0.
- C2: [x] — solo #28 en `in_progress`; `current.md` describe la sesión activa; dependencia #26 en `done`.
- C3: [x] — capas respetadas (todo en `analysis/` + `main.py`); sin `sys.path.append`; sin mutación de globales de config; sin deps nuevas; logging correcto; sin prints de debug ni TODOs.
- C4: [x] — tests reales (tmp_path, BD temporal, LLM mockeado); `pytest -q` exit 0 (todos verdes, 4 skips preexistentes de dedup-v2).
- C5: [x] — `data/saas.db` intacta (79 MB, sin tocar); sin cambios de schema en esta feature.
- C6: [x] — untracked solo los 3 docs esperados de `progress/` (explore x2 + impl); cierre de sesión pendiente del flujo normal del leader (commit/PR tras confirmación).

## Observación no bloqueante (follow-up sugerido)

El **tuner determinista** (`src/saas_radar/agents/tuner.py:438`, default `--runs-dir data/runs`) y `tuner.yml:78,103` (`--runs-dir persist/data/runs`) siguen buscando meta-JSONs en `data/runs/`, pero ahora se escriben en `data/ai_analysis.json/` (que SÍ va dentro de `runs.tar.gz`, pipeline.yml:122). El tuner ya recibe su input principal vía `meta_recommendations` en la BD (desbloqueado por este fix), pero `load_recent_runs` seguirá devolviendo 0 meta-JSONs hasta alinear ese `--runs-dir`. Fuera del scope de #28 (workflows intocados por diseño); conviene una feature/fix aparte.

## Verificación manual post-merge

Tras el próximo run real del cron: el log de Actions debe mostrar `META-ANALISIS DEL RUN` y `-- FASE 4.5: Sugerencias heurísticas LLM`, y `SELECT COUNT(*) FROM meta_recommendations` ≥ 1 en la BD de la release.
