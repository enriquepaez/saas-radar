# Implementación: #16 — github_actions_pipeline_workflow

## Qué cambió

- **`.github/workflows/pipeline.yml`**: reemplazo completo. Antes: checkout dual main+data, creación condicional de rama `data`, restore manual de `saas.db` desde `persist/`, commit y push a rama `data` con guard `git diff --cached --quiet`, `permissions: contents: write`. Después: un solo checkout de `main`, `actions/cache@v4` para persistir `data/saas.db` entre runs, `actions/upload-artifact@v4` para guardar JSONs de `data/runs/`, `permissions: contents: read`.

- **`src/saas_radar/main.py`**: corregida la llamada a `run_ai_analysis()` que usaba nombres de argumento incorrectos. `top_posts=` → `top_n=`, `output=` → `output_path=`. Añadido `provider=os.getenv("AI_PROVIDER", "claude")` para que el workflow pueda seleccionar el proveedor LLM vía secret. El bug causaba `TypeError` al llegar a la fase IA y el pipeline terminaba en error tras 16 min de scraping.

- **`CLAUDE.md`**: añadida regla explícita `❌ NUNCA hagas commit ni push directamente a main`. La regla incluye que los fixes surgidos durante la verificación van en la rama de feature activa, no en main.

- **`tests/test_pipeline_workflow.py`**: reemplazo completo. Eliminados los tests que validaban la lógica de rama `data` (`test_workflow_job_steps_checkout_data_persist`, `test_workflow_job_steps_copy_outputs`, `test_workflow_job_steps_commit_push`, `test_workflow_job_steps_commit_guard`). Añadidos: `test_has_cache_restore_step`, `test_cache_key_uses_run_id`, `test_has_artifact_upload_step`, `test_artifact_retention_days`, `test_no_data_branch_checkout`, `test_has_required_env_secrets` (valida los 9 secrets), `test_permissions_contents_read`. Mantenidos los tests que siguen siendo válidos. Total: 19 tests (antes: 17).

## Por qué

El diseño original (checkout de la rama `data` + commit/push de `saas.db`) provocaba el error "file exceeds 50 MB recommended limit" en cada push de GitHub porque `saas.db` es un archivo de 79 MB que supera el umbral de advertencia. `actions/cache@v4` almacena el archivo en el cache de Actions (límite 10 GB por repo, TTL 7 días sin actividad) sin tocarlo como objeto git, resolviendo el error en origen.

Alternativa descartada: Git LFS. Requiere activación explícita, migración de histórico, y potencialmente cuota de almacenamiento de pago en GitHub Free. `actions/cache` es gratuito y no requiere cambios estructurales en el repo.

Con el nuevo diseño, `permissions: contents: read` es suficiente porque no hay operaciones git (push, commit) dentro del workflow, lo que sigue el principio de menor privilegio.

## Impacto en el pipeline

- **BD (`saas.db`)**: ya no se persiste en git (rama `data`), sino en el cache de GitHub Actions. La BD sobrevive entre runs consecutivos gracias a `restore-keys: saas-db-`. Si el cache expira (7 días sin actividad), el siguiente run arranca con BD vacía pero sin fallar.
- **Outputs de runs (`data/runs/*.json`)**: antes se copiaban a rama `data`; ahora se suben como artefactos descargables 30 días desde la UI de GitHub Actions.
- **Permisos**: bajados de `write` a `read`. Ya no es necesario autenticar push a ninguna rama.
- **Secrets**: sin cambios funcionales; el test `test_has_required_env_secrets` ahora valida los 9 secrets (antes validaba solo 5).

## Explicación técnica

### `actions/cache@v4` — diseño de key/restore-keys

```yaml
key: saas-db-${{ github.run_id }}
restore-keys: saas-db-
```

- `key` es único por run porque incluye `github.run_id` (entero monotónico). Esto fuerza a Actions a **guardar** una entrada nueva al final de cada run con la BD actualizada.
- `restore-keys` es un prefijo. Actions busca la entrada de cache más reciente cuyo nombre empiece por `saas-db-`; eso es siempre la del run anterior. Este mecanismo implementa "restaurar la BD del último run exitoso" sin lógica explícita de rama git.
- Por qué no usar una key fija (p.ej. `saas-db-latest`): una key fija nunca se guarda en runs posteriores porque Actions solo guarda el cache cuando no hay exacta coincidencia de key. Con key dinámica + restore-keys se consigue el comportamiento deseado: siempre se restaura la más reciente y siempre se guarda la nueva.

### `actions/upload-artifact@v4`

```yaml
if: always()
if-no-files-found: ignore
```

- `if: always()` asegura que los JSONs de diagnóstico se suben incluso si el pipeline falla, facilitando el debug.
- `if-no-files-found: ignore` evita que el step falle cuando un run parcial no produce JSONs en `data/runs/`.
- `retention-days: 30` es el máximo razonable sin coste adicional en GitHub Free.

### `permissions: contents: read`

El workflow original tenía `contents: write` para poder hacer push a la rama `data`. Sin operaciones git en el nuevo diseño, `read` es suficiente y es la práctica de menor privilegio recomendada por GitHub.

### Step "Prepare data directories"

```yaml
- name: Prepare data directories
  run: mkdir -p data/runs
```

Paso explícito para crear `data/runs/` antes de ejecutar el pipeline. En el diseño anterior esto ocurría implícitamente en el step de restore. Con el cache, el directorio padre `data/` puede existir si la BD fue restaurada, pero `data/runs/` no necesariamente.

### Step "Run pipeline"

```bash
if [ "${{ github.event.inputs.full_scan }}" = "true" ]; then
  python -m saas_radar.main --full-scan
else
  python -m saas_radar.main
fi
```

GitHub Actions pasa los inputs de `workflow_dispatch` como strings; por eso se compara con `"true"` (string) en lugar de `true` (booleano). Si el trigger es el cron (schedule), `github.event.inputs.full_scan` es vacío y la rama `else` ejecuta el pipeline en modo incremental.

### Fix: `run_ai_analysis()` — argumentos incorrectos en `main.py`

Descubierto al ejecutar el primer run real en GitHub Actions. El pipeline completó 16 min de scraping y falló al llegar a la fase IA con:

```
TypeError: run_ai_analysis() got an unexpected keyword argument 'top_posts'
```

La llamada en `main.py` usaba los nombres del legacy (`top_posts`, `output`) pero la función reconstruida en `ai_analyzer.py` usa `top_n` y `output_path`. Corrección:

```python
# Antes (buggy):
run_ai_analysis(
    min_score=min_score,
    top_posts=top_posts,   # nombre incorrecto
    output=output,          # nombre incorrecto
    use_cached_extractions=use_cached_extractions,
    post_age_days=post_age_days,
)

# Después (correcto):
run_ai_analysis(
    min_score=min_score,
    top_n=top_posts,                              # nombre real del parámetro
    output_path=output,                           # nombre real del parámetro
    use_cached_extractions=use_cached_extractions,
    post_age_days=post_age_days,
    provider=os.getenv("AI_PROVIDER", "claude"),  # nuevo: lee env var del workflow
)
```

`provider=os.getenv("AI_PROVIDER", "claude")` es necesario para que el secret `AI_PROVIDER` configurado en GitHub Actions llegue al dispatcher de LLMs. Sin esto, el pipeline siempre usaría Claude independientemente de lo configurado en los secrets.

### Fix: creación de rama `data` — `git remote add` fallaba con "already exists"

En el primer run, el step "Create data branch if it does not exist" fallaba con exit code 3 porque `actions/checkout@v4` ya había inicializado un repositorio git en `persist/` (aunque el checkout de la rama `data` fallara), dejando el remote `origin` ya configurado. El `git remote add origin ...` posterior lanzaba "error: remote origin already exists".

Corrección: reemplazar `git init` + `git remote add` por `git remote set-url` (que funciona sobre un remote ya existente) y eliminar el `git init` redundante. Este fix fue supersedido posteriormente al eliminar toda la lógica de rama `data` en favor de `actions/cache`.

## Tests añadidos

| Test | Qué cubre |
|---|---|
| `test_workflow_file_exists_and_is_valid_yaml` | El archivo existe y es YAML parseable |
| `test_has_cron_schedule` | Trigger schedule con cron `0 8 * * *` |
| `test_has_workflow_dispatch_with_full_scan` | `workflow_dispatch` con input `full_scan` boolean |
| `test_has_concurrency_config` | `group: saas-radar`, `cancel-in-progress: false` |
| `test_has_cache_restore_step` | `actions/cache@v4` con `path: data/saas.db` |
| `test_cache_key_uses_run_id` | `key` contiene `run_id`; `restore-keys` contiene `saas-db-` |
| `test_has_artifact_upload_step` | `actions/upload-artifact@v4` presente |
| `test_artifact_retention_days` | `retention-days: 30` |
| `test_no_data_branch_checkout` | Ningún step con `ref: data` |
| `test_has_required_env_secrets` | Los 9 secrets en `env` del job |
| `test_has_python_setup` | `actions/setup-python@v5` con `python-version: 3.11` |
| `test_run_pipeline_step_handles_full_scan` | Script contiene `--full-scan` e input `full_scan` |
| `test_permissions_contents_read` | `permissions.contents == read` |
| `test_workflow_name` | Nombre exacto `saas-radar pipeline` |
| `test_workflow_job_run_exists` | Job `run` existe |
| `test_workflow_job_steps_checkout_main` | Checkout de main sin `path` |
| `test_workflow_job_steps_install_deps` | `pip install -e .[dev]` |
| `test_workflow_job_steps_nltk_download` | Descarga NLTK stopwords |
| `test_workflow_job_steps_run_pipeline` | `python -m saas_radar.main` |

## Evidencia de run real en GitHub (AC6)

**Run ID:** 26683979527  
**Estado:** ✅ success  
**Duración:** 17m 28s  
**Trigger:** `workflow_dispatch` con `full_scan=true`  
**Rama:** main  
**Fecha:** 2026-05-30T12:36:53Z

### Resumen de fases

| Fase | Resultado |
|---|---|
| Scraping (36 subreddits, modo 365d) | 9.887 posts en 16m 48s |
| Pain search (88 queries) | 3.686 posts únicos en 13m 07s |
| Comentarios (200 posts priorizados) | 4.869 comentarios en 00m 37s |
| Análisis IA | Completado (run parcial sin opps nuevas) |
| Pipeline total | exit code 0 — job verde |

Comando de verificación:
```
gh run view 26683979527
✓ main saas-radar pipeline · 26683979527
JOBS
✓ run in 17m28s (ID 78648980724)
```

**Nota:** ese run usaba aún la lógica de rama `data` (con warning de 64MB). El commit posterior reemplazó esa lógica por `actions/cache`, resolviendo el error.

## Secrets requeridos

| Secret | Obligatorio |
|---|---|
| `REDDIT_CLIENT_ID` | Si (scraping) |
| `REDDIT_CLIENT_SECRET` | Si (scraping) |
| `REDDIT_USER_AGENT` | Si (scraping) |
| `ANTHROPIC_API_KEY` | Si AI_PROVIDER=claude |
| `GEMINI_API_KEY` | Si AI_PROVIDER=gemini |
| `GROQ_API_KEY` | Si AI_PROVIDER=groq |
| `TELEGRAM_BOT_TOKEN` | No (sin el, no-op silencioso) |
| `TELEGRAM_CHAT_ID` | No |
| `AI_PROVIDER` | Si (default: claude) |

## Verificación

```
uv run pytest tests/test_pipeline_workflow.py -v

============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/enriquepaez/projects/saas-radar
configfile: pyproject.toml
collected 19 items

tests/test_pipeline_workflow.py ...................                      [100%]

============================== 19 passed in 0.05s ==============================
```
