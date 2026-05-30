# Implementación: #16 — github_actions_pipeline_workflow

## Qué cambió

- **`.github/workflows/pipeline.yml`** (creado): Workflow GitHub Actions con cron diario a las 8 UTC, `workflow_dispatch` con input `full_scan`, checkout dual (rama `main` + rama `data` en `persist/`), restore de `data/saas.db`, install de deps + NLTK, ejecución del pipeline, y commit+push de la BD actualizada a la rama `data` (solo si hay cambios).

- **`tests/test_pipeline_workflow.py`** (creado): 17 tests pytest que validan la estructura YAML del workflow, los triggers, la concurrencia, los steps y los secrets.

- **`pyproject.toml`** (modificado): Se añadió `pyyaml` como dependencia de desarrollo (requerida por los tests). Antes solo incluía `pytest`, `ruff` y `respx` en `[dev]`; después también incluye `pyyaml==6.0.3`.

## Por qué

### Por qué checkout dual (main + data)

El pipeline genera dos artefactos que necesitan persistir entre ejecuciones diarias:
1. `data/saas.db` — la BD SQLite con los posts scraped y las oportunidades encontradas.
2. `data/runs/*.json` — los outputs JSON de cada run.

Si se guardaran en `main`, cada ejecución del pipeline crearía un commit de ~79 MB en la historia principal, inflando el repositorio. La convención estándar en proyectos con GitHub Actions es usar una rama huérfana (sin historia común con `main`) exclusivamente para datos. De esta forma, la historia de `main` permanece limpia y la rama `data` actúa como "storage bucket versionado".

El checkout de `persist/` con `ref: data` es el patrón usado por `actions/gh-pages` y similares para este propósito.

### Por qué `continue-on-error: true` en el checkout de `data`

La primera vez que el workflow se ejecuta en un repo nuevo, la rama `data` no existe. Sin `continue-on-error: true`, el checkout fallaría y el job entero terminaría rojo antes siquiera de correr el pipeline. Con la guarda, el step siguiente detecta `steps.checkout_data.outcome == 'failure'` y crea la rama `data` como rama huérfana (sin historial, con un commit vacío inicial).

El uso de `git checkout --orphan data` crea una rama sin padre, exactamente lo que se necesita para separar históricamente los datos del código.

### Por qué `cancel-in-progress: false`

Un run del pipeline puede tardar 10-30 minutos (scraping + LLM). Si dos ejecuciones se solapan (por ejemplo, un trigger manual mientras ya corre el cron), queremos que la segunda espere a que termine la primera, no cancelarla. Cancelar un run a mitad podría dejar `data/saas.db` en estado inconsistente o incompleto. Con `cancel-in-progress: false`, la segunda espera en la cola y ejecuta después.

### Por qué `git diff --cached --quiet` antes del commit

`git add data/` añade todos los cambios al staging area. Si el pipeline no encontró nuevos posts (p.ej. Reddit devuelve los mismos posts que ya estaban en la BD), el archivo `saas.db` puede haber cambiado en su timestamp interno de SQLite pero no en contenido relevante. O puede que no haya cambiado nada en absoluto. `git diff --cached --quiet` devuelve exit code 0 si no hay diferencias staged y exit code 1 si hay. Se usa en un `if !` para hacer el commit solo cuando realmente hay cambios, evitando commits vacíos que llenan el historial de la rama `data` sin aportar valor.

### Por qué el pipeline no falla con BD vacía (status='partial' ok)

El pipeline de `saas_radar.main` persiste cada run en `analysis_runs` con uno de tres estados: `ok` (se encontraron oportunidades), `partial` (se procesó pero sin oportunidades nuevas), o `failed` (error grave). El pipeline no llama a `sys.exit(1)` en el caso `partial`. Por tanto, el job de GitHub Actions termina verde incluso si la BD está vacía o el run no produjo oportunidades. Esto es un diseño correcto: un pipeline que no encuentra nada en un día concreto no debe alertar como si hubiera fallado.

### Por qué entrecomillar `"on":` en el YAML

En YAML 1.1 (que usa PyYAML por defecto), `on` es una palabra reservada que se interpreta como el booleano `True`. Al parsear el archivo con `yaml.safe_load()`, la clave `on` se convierte en `True` en el dict Python, lo que rompe los tests que buscan `workflow.get("on")`. Entrecomillando la clave como `"on":` se fuerza a PyYAML a tratarla como string. GitHub Actions interpreta correctamente el YAML en ambos casos porque su parser es más permisivo, así que esto no afecta al funcionamiento en CI.

## Impacto en el pipeline

- **Automatización diaria**: el cron `0 8 * * *` (8:00 UTC = 10:00 CEST en verano) ejecuta el pipeline completo sin intervención manual.
- **Persistencia entre runs**: la BD se preserva en la rama `data` y se restaura al inicio de cada run, lo que permite el modo incremental (`has_successful_run()=True` → solo scrapea 24h).
- **Rama `data` como historial de datos**: cada run que produce cambios crea un commit con timestamp UTC, permitiendo rollback manual si algo sale mal.
- **Trigger manual con full_scan**: `gh workflow run 'saas-radar pipeline' -f full_scan=true` permite forzar un scraping completo de 365 días.
- **Sin impacto en módulos Python**: este workflow no modifica ningún módulo de `src/saas_radar/`. Solo orquesta lo que ya existe.

## Explicación técnica

### Workflow: triggers (`"on":`)

```yaml
"on":
  schedule:
    - cron: '0 8 * * *'
  workflow_dispatch:
    inputs:
      full_scan:
        description: 'Forzar modo CARGA COMPLETA (365d)'
        required: false
        default: 'false'
        type: boolean
```

- `schedule.cron: '0 8 * * *'`: ejecuta el job a las 08:00 UTC todos los días (los 5 campos son minuto, hora, día-del-mes, mes, día-de-la-semana). La franja de 8 UTC coincide con el inicio de la jornada laboral en Europa y el cierre de la jornada americana, momento de alta actividad en Reddit.
- `workflow_dispatch`: permite disparo manual desde la UI de GitHub o con `gh workflow run`. El input `full_scan` de tipo `boolean` con `default: 'false'` hace que el flag sea opcional; si no se pasa, el pipeline corre en modo incremental.

### Concurrencia

```yaml
concurrency:
  group: 'saas-radar'
  cancel-in-progress: false
```

`group: 'saas-radar'` es la clave que identifica el lock. Solo puede haber un workflow con este group en ejecución simultánea. Todos los demás workflows que usen el mismo group quedan en cola hasta que el anterior termine.

### Job: env vars de secrets

```yaml
env:
  REDDIT_CLIENT_ID: ${{ secrets.REDDIT_CLIENT_ID }}
  ...
```

Declarar las env vars a nivel de job (no de step) las expone a todos los steps del job sin necesidad de repetirlas. El pipeline de `saas_radar` lee estas vars con `python-dotenv` desde el entorno del proceso.

### Step 1: Checkout main

```yaml
- name: Checkout main
  uses: actions/checkout@v4
  with:
    ref: main
    fetch-depth: 1
```

`fetch-depth: 1` hace un shallow clone (solo el último commit), lo que es mucho más rápido que clonar toda la historia. `ref: main` es redundante (es el default) pero explicita la intención.

### Steps 2-3: Checkout data con fallback

```yaml
- name: Checkout data branch into persist/
  id: checkout_data
  uses: actions/checkout@v4
  continue-on-error: true
  with:
    ref: data
    path: persist
    fetch-depth: 1
    token: ${{ secrets.GITHUB_TOKEN }}

- name: Create data branch if it does not exist
  if: steps.checkout_data.outcome == 'failure'
  run: |
    mkdir -p persist/data/runs
    cd persist
    git init
    git remote add origin https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/${{ github.repository }}.git
    git checkout --orphan data
    git commit --allow-empty -m "chore: init data branch"
    git push origin data
```

`id: checkout_data` permite referenciar el resultado del step con `steps.checkout_data.outcome`. Si el checkout falla (rama `data` no existe), el step de fallback inicializa el directorio `persist/` como un repo Git independiente, crea la rama `data` huérfana y hace push. Tras esto, los steps siguientes pueden usar `persist/` normalmente.

`https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/...` es la forma estándar de autenticarse para push en GitHub Actions usando el token automático del repositorio, que tiene permisos de escritura en todas las ramas del mismo repo.

### Step 4: Restore saas.db

```bash
mkdir -p data/runs
if [ -f persist/data/saas.db ]; then
  cp persist/data/saas.db data/saas.db
fi
```

`mkdir -p data/runs` crea el directorio de runs si no existe (necesario para que el pipeline pueda escribir los JSONs de output). `cp persist/data/saas.db data/saas.db` restaura la BD del run anterior. Si no hay BD previa (primer run), la condición `if` no se cumple y el pipeline arranca con una BD nueva (que `init_db()` creará vacía).

### Steps 5-6: Setup Python + install

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: '3.11'
    cache: 'pip'
```

`cache: 'pip'` activa el cache de GitHub Actions para el directorio de paquetes pip, evitando reinstalar dependencias en cada run. `pip install -e .[dev]` instala el paquete en modo editable incluyendo las dependencias de desarrollo (pytest, ruff, pyyaml).

### Step 7: NLTK

```bash
python -c "import nltk; nltk.download('stopwords', quiet=True)"
```

NLTK requiere descargar el corpus de stopwords en el primer uso. En un runner limpio, este corpus no existe. El download con `quiet=True` suprime el output de progreso. El pipeline llama a `clean_text()` que usa las stopwords; sin este download, el step de run fallaría.

### Step 8: Run pipeline

```bash
if [ "${{ github.event.inputs.full_scan }}" = "true" ]; then
  python -m saas_radar.main --full-scan
else
  python -m saas_radar.main
fi
```

El shell script evalúa el input `full_scan`. GitHub Actions pasa los inputs de `workflow_dispatch` como strings; por eso se compara con `"true"` (string) en lugar de `true` (booleano). Si el trigger es el cron (schedule), `github.event.inputs.full_scan` es vacío y la rama `else` ejecuta el pipeline en modo incremental.

### Steps 9-10: Copy + commit

```bash
# Copy outputs to persist/
mkdir -p persist/data/runs
cp data/saas.db persist/data/saas.db
if ls data/runs/*.json 2>/dev/null | head -1 > /dev/null; then
  cp data/runs/*.json persist/data/runs/ 2>/dev/null || true
fi
```

`2>/dev/null || true` evita que el step falle si no hay archivos JSON (el pipeline puede no generar runs JSON si hay errores tempranos). `ls data/runs/*.json | head -1 > /dev/null` comprueba si hay al menos un JSON antes de intentar copiarlos.

```bash
# Commit y push
cd persist
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add data/
if ! git diff --cached --quiet; then
  git commit -m "chore: pipeline run $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push origin data
else
  echo "No changes to commit"
fi
```

`git config user.name/email` es obligatorio para que git acepte el commit (los runners de GitHub Actions no tienen configuración git global por defecto). El usuario `github-actions[bot]` es la identidad estándar usada por las GitHub Actions oficiales. `date -u +%Y-%m-%dT%H:%M:%SZ` genera un timestamp ISO 8601 UTC para el mensaje de commit, lo que facilita localizar el run exacto en el historial de la rama `data`.

## Tests añadidos

`tests/test_pipeline_workflow.py` — 17 tests:

| Test | Qué cubre |
|------|-----------|
| `test_workflow_file_exists_and_is_valid_yaml` | El archivo existe y es YAML parseable sin error |
| `test_workflow_has_schedule_cron` | El trigger `schedule` incluye el cron `0 8 * * *` |
| `test_workflow_has_workflow_dispatch_with_full_scan` | `workflow_dispatch` tiene input `full_scan` de tipo `boolean` con default `'false'` |
| `test_workflow_has_concurrency_group` | `concurrency.group == 'saas-radar'` y `cancel-in-progress == False` |
| `test_workflow_job_run_exists` | Existe un job llamado `run` |
| `test_workflow_job_steps_checkout_main` | Hay al menos un checkout sin `path` (checkout de main) |
| `test_workflow_job_steps_checkout_data_persist` | Hay un checkout con `path: persist` y `ref: data` |
| `test_workflow_job_steps_setup_python` | Hay un step de `setup-python` con versión `'3.11'` |
| `test_workflow_job_steps_install_deps` | Hay un step con `pip install -e .[dev]` |
| `test_workflow_job_steps_nltk_download` | Hay un step con `nltk` y `stopwords` en el script |
| `test_workflow_job_steps_run_pipeline` | Hay un step que ejecuta `python -m saas_radar.main` |
| `test_workflow_job_steps_full_scan_conditional` | El step de run incluye lógica condicional para `--full-scan` |
| `test_workflow_job_steps_copy_outputs` | Hay un step que copia `saas.db` a `persist/data/` |
| `test_workflow_job_steps_commit_push` | Hay un step con `git commit` y `git push` |
| `test_workflow_job_steps_commit_guard` | El step de commit incluye la guarda `git diff --cached --quiet` |
| `test_workflow_job_env_secrets` | El job declara env vars para los secrets esenciales (Reddit, Anthropic, Telegram, AI_PROVIDER) |
| `test_workflow_name` | El nombre del workflow es exactamente `'saas-radar pipeline'` (requerido por `gh workflow run 'saas-radar pipeline'`) |

## Secrets requeridos

Estos secrets deben configurarse en **GitHub → Settings → Secrets and variables → Actions → Repository secrets**:

| Secret | Descripción | Obligatorio |
|--------|-------------|-------------|
| `REDDIT_CLIENT_ID` | ID de la app Reddit (obtenido en https://www.reddit.com/prefs/apps) | Sí (para scraping) |
| `REDDIT_CLIENT_SECRET` | Secret de la app Reddit | Sí (para scraping) |
| `REDDIT_USER_AGENT` | User-agent PRAW (p.ej. `saas-radar:v0.1 by u/tuusuario`) | Sí (para scraping) |
| `ANTHROPIC_API_KEY` | API key de Anthropic Claude | Sí si `AI_PROVIDER=claude` |
| `GEMINI_API_KEY` | API key de Google Gemini | Sí si `AI_PROVIDER=gemini` |
| `GROQ_API_KEY` | API key de Groq | Sí si `AI_PROVIDER=groq` |
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram (obtenido con @BotFather) | No (sin él las notificaciones son no-op) |
| `TELEGRAM_CHAT_ID` | Chat ID donde enviar las notificaciones | No |
| `AI_PROVIDER` | Proveedor LLM a usar: `claude`, `gemini` o `groq` | Sí (default si ausente: `claude`) |

**Nota sobre `AI_PROVIDER`**: técnicamente es una variable de entorno, no un secret. Sin embargo, al declararlo en los GitHub Secrets se mantiene junto al resto de la configuración sensible y se puede cambiar sin tocar el código del workflow. El valor recomendado para producción es `claude` (mayor calidad de síntesis).

## Verificación manual: cómo ejecutar el primer run real en GitHub

### Prerrequisitos

1. Configurar los secrets en el repositorio (ver tabla de arriba).
2. Asegurarse de que la rama con el workflow está en `main` (o la rama default del repo).
3. Tener instalado `gh` (GitHub CLI): `gh auth login`.

### Paso 1: Disparar manualmente con full_scan

```bash
gh workflow run 'saas-radar pipeline' -f full_scan=true
```

### Paso 2: Ver el progreso en tiempo real

```bash
gh run watch
```

O abrir la URL directamente:
```bash
gh run list --workflow=pipeline.yml --limit=1
```

### Paso 3: Verificar que el job terminó verde

El job debe mostrar status `completed` con conclusion `success`.

Si hay errores, ver los logs:
```bash
gh run view --log
```

### Paso 4: Verificar que la rama `data` tiene el commit

```bash
git fetch origin data
git log origin/data --oneline -5
```

Deberías ver algo como:
```
a1b2c3d chore: pipeline run 2026-05-30T08:00:01Z
abcdef0 chore: init data branch
```

### Paso 5: Verificar el contenido de la BD

```bash
git show origin/data:data/saas.db > /tmp/saas_check.db
sqlite3 /tmp/saas_check.db "SELECT COUNT(*) FROM analysis_runs;"
sqlite3 /tmp/saas_check.db "SELECT status, completed_at FROM analysis_runs ORDER BY completed_at DESC LIMIT 1;"
```

El resultado debe mostrar al menos 1 fila en `analysis_runs` con status `ok`, `partial` o (si no hay API keys configuradas) `failed`. **Un status `partial` o `failed` no hace fallar el job** — el pipeline gestiona estos estados internamente.

### Paso 6: Verificar el cron diario (opcional)

Esperar al día siguiente a las 08:00 UTC y comprobar con:
```bash
gh run list --workflow=pipeline.yml --limit=5
```

## Verificación

Salida de `./init.sh` (último bloque):

```
── 5. Ejecutando tests ─────────────────────────────────
[OK]    Todos los tests pasan

── 6. Verificando anti-patrones del legacy ────────────
[OK]    Sin sys.path.append en src/

── 7. Resumen ──────────────────────────────────────────
[OK]    Entorno listo. Puedes empezar a trabajar.
```

Salida de `python -m pytest tests/test_pipeline_workflow.py -v` (17/17 passing):

```
tests/test_pipeline_workflow.py::test_workflow_file_exists_and_is_valid_yaml PASSED
tests/test_pipeline_workflow.py::test_workflow_has_schedule_cron PASSED
tests/test_pipeline_workflow.py::test_workflow_has_workflow_dispatch_with_full_scan PASSED
tests/test_pipeline_workflow.py::test_workflow_has_concurrency_group PASSED
tests/test_pipeline_workflow.py::test_workflow_job_run_exists PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_checkout_main PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_checkout_data_persist PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_setup_python PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_install_deps PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_nltk_download PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_run_pipeline PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_full_scan_conditional PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_copy_outputs PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_commit_push PASSED
tests/test_pipeline_workflow.py::test_workflow_job_steps_commit_guard PASSED
tests/test_pipeline_workflow.py::test_workflow_job_env_secrets PASSED
tests/test_pipeline_workflow.py::test_workflow_name PASSED

17 passed in 0.05s
```

Suite completa: 288 tests, 0 failed (suite completa del proyecto incluyendo los 17 nuevos).
