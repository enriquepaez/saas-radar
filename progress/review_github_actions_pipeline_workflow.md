# Review — feature #16 github_actions_pipeline_workflow

**Veredicto:** APPROVED

## Acceptance criteria

- AC1: [x] Workflow disparable con `gh workflow run 'saas-radar pipeline' -f full_scan=true` — nombre del workflow es `saas-radar pipeline` (verificado en YAML y en `test_workflow_name`).
- AC2: [x] Job termina verde — run ID 26683979527, success, 17m28s, documentado en `progress/impl_github_actions_pipeline_workflow.md`.
- AC3: [x] Guard `git diff --cached --quiet` — no aplica, lógica de rama `data` eliminada por completo.
- AC4: [x] `concurrency: group: 'saas-radar', cancel-in-progress: false` — presente en `.github/workflows/pipeline.yml` líneas 16-17, cubierto por `test_has_concurrency_config`.
- AC5: [x] Secrets documentados — tabla de 9 secrets en `progress/impl_github_actions_pipeline_workflow.md` y validados por `test_has_required_env_secrets`.
- AC6: [x] Evidencia de run real documentada — run 26683979527, success, 17m28s, 2026-05-30T12:36:53Z.

## Checkpoints CHECKPOINTS.md

- C1: [x] Arnés completo — `./init.sh` termina con exit code 0 (`[OK] Entorno listo`). Los 4 archivos base y los 3 docs del proyecto existen.
- C2: [x] Estado coherente — `feature_list.json` tiene exactamente una feature `in_progress` (#16). No hay features `done` que dependan de `pending`.
- C3: [x] Arquitectura respetada — los únicos archivos modificados en esta feature son `.github/workflows/pipeline.yml` y `tests/test_pipeline_workflow.py`. No hay cambios en `src/`. No hay `sys.path.append`. Los módulos `.py` del paquete no se tocan.
- C4: [x] Tests reales — 19 tests en `tests/test_pipeline_workflow.py`, todos verdes. Suite completa: 282 passed in 232.02s (exit code 0). Ningún test hace llamadas reales a GH Actions (validan estructura YAML estática).
- C5: [x] No aplica directamente a esta feature (no modifica `storage/db.py` ni el schema).
- C6: [ ] Sesión no cerrada aún — `progress/current.md` describe la sesión activa correctamente. `progress/history.md` no tiene entrada para #16 todavía. Esto es esperado: el cierre lo hace el leader tras este veredicto.

## Análisis técnico de los ítems de revisión

### 1. `.github/workflows/pipeline.yml` — diseño `actions/cache@v4`

El patrón `key: saas-db-${{ github.run_id }}` + `restore-keys: saas-db-` es correcto para el objetivo "siempre restaurar la BD más reciente":

- `key` única por run (run_id es entero monotónico) → Actions crea una entrada nueva al final de cada run con la BD actualizada.
- `restore-keys: saas-db-` es prefijo → Actions busca la entrada más reciente que empiece por ese prefijo, que siempre es el run anterior.
- La alternativa con key fija (`saas-db-latest`) no funcionaría: Actions solo guarda cache cuando no hay coincidencia exacta de key, por lo que con key fija el cache nunca se actualizaría tras el primer run.

Punto a notar (no bloqueante): el paso "Restore saas.db from cache" ocurre ANTES de "Prepare data directories" (`mkdir -p data/runs`). Si el cache restaura `data/saas.db`, el directorio `data/` existirá, pero `data/runs/` no se garantiza hasta el paso siguiente. El orden actual es correcto: cache (restaura `data/saas.db`), setup-python, install, NLTK, mkdir `data/runs`, run. No hay race condition.

`permissions: contents: read` es correcto — sin operaciones git de escritura en el workflow, el principio de menor privilegio está bien aplicado.

### 2. `tests/test_pipeline_workflow.py` — 19 tests

Los 19 tests cubren adecuadamente el nuevo diseño:

- Tests nuevos que validan el cache (`test_has_cache_restore_step`, `test_cache_key_uses_run_id`): correctos.
- `test_no_data_branch_checkout`: verifica regresión — que no vuelva la lógica de rama `data`.
- `test_has_required_env_secrets`: valida los 9 secrets (antes validaba solo 5).
- `test_permissions_contents_read`: nuevo, correcto.
- Tests eliminados (`test_workflow_job_steps_commit_push`, `test_workflow_job_steps_commit_guard`, etc.): correctamente removidos al desaparecer la lógica que cubrían.
- Todos pasan: `19 passed in 0.03s`.

### 3. `src/saas_radar/main.py` — fix `run_ai_analysis`

No hay diff en `main.py` en esta rama — el fix fue mergeado previamente a `main`. Verificado en `main`: línea 214 tiene `provider=os.getenv("AI_PROVIDER", "claude")` como argumento explícito. Correcto según `docs/architecture.md` principio 3 ("configuración por argumento, no por mutación global").

### 4. `CLAUDE.md` — regla "NUNCA commitear en main"

No hay diff en `CLAUDE.md` en esta rama — la regla fue añadida en una iteración anterior ya mergeada. Verificado en `main`: la regla existe y está bien expresada: `❌ NUNCA hagas commit ni push directamente a main, ni para features, ni para fixes, ni para correcciones menores. Sin excepción.`

### 5. `pyproject.toml` — `pyyaml` en `[project.optional-dependencies].dev`

No hay diff en `pyproject.toml` en esta rama — el cambio fue mergeado anteriormente. Verificado en `main`: `pyyaml>=6.0.3` está en `[project.optional-dependencies].dev`, NO en `[dependency-groups]`. Correcto.

## Cambios requeridos

Ninguno.
