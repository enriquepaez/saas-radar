# Review — Feature #29 `db_storage_github_releases`

**Veredicto: APROBADO**

- **Fecha:** 2026-07-04
- **Rama:** `feat/29-db_storage_github_releases` (working tree sin commitear)
- **Archivos revisados:** `.github/workflows/pipeline.yml`, `.github/workflows/tuner.yml`,
  `tests/test_pipeline_workflow.py`, `feature_list.json`, `progress/current.md`,
  `progress/impl_db_storage_github_releases.md`

## Criterios de acceptance (11/11)

| # | Criterio | Veredicto | Evidencia |
|---|---|---|---|
| 1 | Restore pipeline: `db-latest` → rama data (`.zst`/plano) → BD nueva | [x] | `pipeline.yml:60-76`. `gh release download` como condición del `if` (errexit no aplica a condiciones → release inexistente no tumba el job; verificado por simulación, ver abajo). Fallbacks en el orden exacto del contrato. `GH_TOKEN` en el env del step (l.62). |
| 2 | Persist: VACUUM + `zstd -T0 -15`; `create` si no existe, `upload --clobber` si existe | [x] | `pipeline.yml:110-119`. `gh release view` como test de existencia; `--notes` presente en el create (sin él `gh` intenta abrir editor en CI). |
| 3 | Snapshot diario `db-YYYYMMDD` + rotación keep-7 con `--cleanup-tag` | [x] | `pipeline.yml:128-141` (snapshot, mismo asset) y `143-155` (rotación). |
| 4 | `runs.tar.gz` con `data/ai_analysis.json/` y/o `data/runs/` como asset del snapshot | [x] | `pipeline.yml:121-132`. Verifiqué en `src/` que `data/ai_analysis.json` es efectivamente un directorio (`ai_analyzer.py:127` hace `out_dir.mkdir`), así que el test `[ -d ]` es correcto. Empaqueta solo lo que exista. |
| 5 | tuner.yml descarga desde `db-latest` con el mismo fallback a rama data | [x] | `tuner.yml:38-51`. Destino `persist/data/saas.db` → los flags `--db-path`/`--runs-dir` del tuner (l.77-79, 102-104) no cambian. `mkdir -p persist/data/runs` (l.42) protege el caso de checkout fallido. Fallback solo `.zst` = "la lógica actual" del tuner pre-cambio (nunca manejó el plano), conforme al criterio. |
| 6 | "Persist to data branch" eliminado; rama data NO se borra; documentado | [x] | El diff elimina el step completo (git config/commit/push). `grep 'git push'` sobre pipeline.yml: 0 resultados. Checkout de data queda como fallback con `continue-on-error: true` (pipeline.yml:47, tuner.yml:32) y sin `persist-credentials`. impl.md documenta el plan de borrado manual post-verificación (secciones "Impacto" y punto 8 del plan manual). |
| 7 | Alerta Telegram `if: failure()` en AMBOS workflows, con link al run, sin fallar si faltan secrets | [x] | `pipeline.yml:166-178` y `tuner.yml:110-124`. Último step en ambos. Guard `[ -n ... ] && [ -n ... ]` + `\|\| true` en el curl + `--max-time 15`. `RUN_URL` compuesto en `env` con `server_url/repository/run_id` (sin inyección de template en bash). En pipeline los secrets vienen del env del job (l.30-31); en tuner se declaran en el step (l.113-114). |
| 8 | `permissions: contents: write`, `concurrency` y `actions/cache` intactos | [x] | `pipeline.yml:14-19` (concurrency `saas-radar`, permissions) y `53-58` (cache) sin cambios en el diff. Artifact `run-outputs` intacto (l.157-164). tuner conserva sus permissions (l.18-20). |
| 9 | Sincronización local documentada en impl.md | [x] | impl.md, sección "Sincronización local de la BD": comando exacto del acceptance (`gh release download db-latest -p saas.db.zst -O data/saas.db.zst --clobber && zstd -d -f ...`). |
| 10 | YAML válido; suite completa verde | [x] | `yaml.safe_load` OK en ambos (13 y 12 steps). `bash -n` sobre los 16 bloques `run`: todos OK. `.venv/bin/pytest -q` → **exit 0** (416 passed, 4 skipped). `./init.sh` → **exit 0**, "[OK] Entorno listo". |
| 11 | Plan de verificación manual documentado (run verde → db-latest + snapshot + runs.tar.gz + prueba alerta) | [x] | impl.md, "Plan de verificación manual": 8 pasos, incluye la prueba de la alerta Telegram (paso 5, con alternativa no destructiva) y la comprobación de idempotencia (paso 6). |

## Verificación independiente de la lógica shell (bash -e, gh mockeado)

No me fié solo de la simulación del implementer; la repetí por mi cuenta:

- **Rotación** con `db-latest` + 9 snapshots + `v1.0`: borra exactamente
  `db-20260627` y `db-20260626` (los 2 más antiguos), conserva 7, **no toca**
  `db-latest` ni `v1.0` (la regex `^db-[0-9]{8}$` es estructuralmente incapaz
  de matchearlos). Exit 0.
- **Rotación sin snapshots** (día 1): exit 0 gracias al `{ grep ... || true; }`.
- **Restore con release inexistente** (`gh` exit 1): cae al `.zst` de
  `persist/`, descomprime bien. Exit 0.
- **Guard Telegram con secrets vacíos**: rama del `else`, exit 0.

## Puntos críticos revisados (sin hallazgos bloqueantes)

- **Idempotencia same-day:** re-run del mismo día → `gh release view "$snapshot"`
  devuelve 0 → bucle de `upload --clobber` en vez de `create` (que fallaría).
  Correcto (`pipeline.yml:133-141`).
- **`gh release list`:** no se depende de su orden ni formato de tabla:
  `--json tagName --jq` + `sort -r` explícito (lexicográfico = cronológico con
  `YYYYMMDD`). `--limit 100` sobra para ~8 releases.
- **Tags huérfanos:** `--cleanup-tag --yes` presente (`pipeline.yml:154`).
- **`GH_TOKEN`:** presente en los 4 steps que usan `gh` (restore/publish/rotate
  del pipeline y restore del tuner).
- **`saas.db.zst` en la raíz del workspace** (no en `data/`): no puede colarse
  en `runs.tar.gz` ni en el artifact. Bien pensado.
- **Higiene:** se eliminaron `token:` y `persist-credentials: true` del checkout
  de data (ya no hay push); menos superficie de riesgo.

## Alcance

- `git diff --name-only -- src/ data/` → **vacío**. No se tocó código fuente ni la BD.
- `tests/test_pipeline_workflow.py` SÍ se modificó, y es **necesario**: el test
  preexistente `test_has_persist_step` (guard de F22) exigía un step "Persist to
  data branch" que esta feature elimina — sin adaptar los tests, la suite estaría
  roja y el criterio 10 sería incumplible. La sustitución por
  `test_no_push_to_data_branch` + 8 tests estructurales nuevos (incl. cobertura de
  tuner.yml, que no tenía) es coherente con el patrón regression-guard ya
  establecido en el repo. `ruff check` sobre el archivo: All checks passed.
- Feature #29 en `feature_list.json`: `"status": "in_progress"` — **NO marcada
  done**, correcto (la marca el leader al cierre).
- `progress/current.md` actualizado con la sesión activa.

## Observaciones menores (no bloqueantes)

1. El acceptance dice `./venv/bin/pytest -q` pero el venv real del repo es
   `.venv/` — discrepancia preexistente del contrato, no de esta feature. La
   suite se validó con `.venv/bin/pytest -q`, exit 0.
2. El fallback del restore del tuner no cubre un `saas.db` plano en la rama data
   (solo `.zst`). Es fiel a la lógica previa del tuner y la rama solo contiene
   `.zst` desde la #26, así que no hay caso real que lo necesite.
3. En el primer run post-merge, si `actions/cache` restaura una BD más vieja que
   el último `.zst` de la rama data, se usa la del cache (comportamiento
   preexistente de F22, sin cambios en esta feature).

## Conclusión

Los 11 criterios del contrato se cumplen contra el diff real, la suite y
`init.sh` están verdes, la lógica shell resiste `bash -e` en los casos límite
(release inexistente, día 1 sin snapshots, secrets vacíos, re-run same-day) y
`db-latest` es imposible de borrar por la rotación. **APROBADO.**
