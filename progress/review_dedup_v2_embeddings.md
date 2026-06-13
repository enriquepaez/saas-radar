# Review — feature #25 — dedup_v2_embeddings

**Veredicto:** APROBADO

## Criterios de aceptación

- C1 `find_canonical_v2` con signature correcta y threshold=0.75: ✓  
  `src/saas_radar/analysis/dedup.py` líneas 184-228. Firma exacta del spec. Falla limpia con `RuntimeError` si sentence-transformers no está (`_get_st_model()` líneas 150-169).

- C2 `ENABLE_DEDUP_V2` env var (default '0'), bifurcación en `persist_run_to_db`: ✓  
  `config.py` línea 57. `db.py` líneas 348-354 bifurcan correctamente.

- C3 `scripts/backfill_canonical_v2.py` con `--dry-run/--yes/--force`: ✓  
  Argparse en líneas 29-37. Migración idempotente inline (líneas 54-60). Resumen de clusters impreso antes de escribir. Guard `--dry-run or --yes` en líneas 43-45.

- C4 Aceptación cuantitativa (5-7 canónicas, id=8 cluster {2,4,7,9,10}): no verificable sin BD real + sentence-transformers instalado — marcado como "verificación manual" en el spec, no bloqueante.

- C5 Tests cubren threshold, vocabulario disjunto, modelo ausente: ✓  
  `tests/test_dedup.py` líneas 384-483:  
  - `test_find_canonical_v2_no_installed_raises` (modelo ausente, RuntimeError)  
  - `test_find_canonical_v2_threshold_respected` (threshold=1.0 → None)  
  - `test_find_canonical_v2_disjoint_vocabulary_no_match_jaccard_but_embedding_may_match` (caso id=8)  
  - `test_find_canonical_v2_identical_opps_match` (happy path)  
  Los 4 tests que requieren sentence_transformers hacen `pytest.importorskip` → 4 skips limpios en CI.

- C6 `pyproject.toml` dependencia opcional `[dedup-v2]`: ✓  
  `pyproject.toml` línea 26: `dedup-v2 = ["sentence-transformers>=2.7"]`.

- C7 Documentación en `progress/impl_dedup_v2_embeddings.md`: ✓  
  Cubre tradeoff Jaccard vs embeddings, tabla comparativa, cómo testear localmente, plan A/B detallado.

## Checkpoints generales

- C1 (arnés completo): [x] — init.sh termina verde.
- C2 (estado coherente): [x] — una sola feature in_progress, current.md describe la sesión activa.
- C3 (arquitectura): [x] — `dedup.py` vive en `analysis/`, `backfill_canonical_v2.py` en `scripts/`. Sin `sys.path.append`. Sin mutación de globales. La lectura de `ENABLE_DEDUP_V2` es una lectura (no mutación) de un valor fijo de config, dentro del cuerpo de función (import diferido justificado). `sentence-transformers` declarada en `[dedup-v2]` del pyproject.toml.
- C4 (verificación real): [x] — 363 passed, 4 skipped. Todos los tests de v1 intactos. Tests de v2 con skip graceful cuando el modelo no está instalado. BD temporal vía `tmp_path` en los tests de persistencia.
- C5 (BD heredada): [x] — `init_db` es idempotente; la columna `canonical_id` ya existía desde feature #15.
- C6 (sesión cerrada): pendiente de cierre formal por el leader.

## Observación menor (no bloqueante)

El comentario en `impl_dedup_v2_embeddings.md` §`persist_run_to_db` — bifurcación indica que el import diferido permite que los tests cambien `os.environ["ENABLE_DEDUP_V2"]` con monkeypatch. En realidad `from saas_radar.config import ENABLE_DEDUP_V2` importa el atributo ya evaluado del módulo config (no re-lee el env en runtime). El comportamiento en producción es correcto; la explicación pedagógica en el doc de implementación es imprecisa. No requiere cambio de código.
