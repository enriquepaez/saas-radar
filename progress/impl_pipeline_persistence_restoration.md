# Implementación: 22 — pipeline_persistence_restoration

## Qué cambió

- **`.github/workflows/pipeline.yml`** (modificado): se restaura el patrón legacy de persistencia a la rama `data` que el commit `8409bb9` había eliminado al sustituirlo por `actions/cache`.
  - Antes: `permissions.contents: read`. Despues: `permissions.contents: write`.
  - Antes: un solo checkout de `main`. Despues: dos checkouts — `main` en el workdir y `data` en `path: persist/` con `token: ${{ secrets.GITHUB_TOKEN }}` y `persist-credentials: true` para que el push posterior reuse las credenciales.
  - Antes: tras `Run pipeline` se saltaba directamente a `Upload run outputs`. Despues: se intercala un nuevo step `Persist to data branch` (con `if: success()`) que copia `data/saas.db` y `data/runs/` a `persist/data/`, configura el bot user de GitHub Actions y hace `git add` + commit/push **solo si hay cambios** (gracias a `git diff --cached --quiet || (...)`).
  - `actions/cache` y `actions/upload-artifact` se conservan tal cual: el cache acelera el arranque del cron y el artefacto sigue disponible para inspeccion manual via GH UI.

- **`progress/impl_pipeline_persistence_restoration.md`** (nuevo): este archivo. Documenta los cambios, las decisiones y como el usuario sincroniza la BD local tras un run del cron.

- **`feature_list.json`** (modificado): status de la feature #22 cambiado de `pending` a `in_progress` (no a `done` — el cierre lo hace el leader despues del review).

- **`progress/current.md`** (modificado): plan del implementer registrado en tiempo real, como obliga `AGENTS.md` §3.

## Por que

### Por que restaurar el push a la rama `data` (vs mantener solo `actions/cache`)

El commit `8409bb9` reemplazo el push por `actions/cache` argumentando que era mas simple. Pero esa simplificacion creo una **regresion silenciosa** documentada en `progress/audit_cron_state.md`:

1. `tuner.yml` (workflow disparado tras `pipeline.yml`) sigue leyendo de la rama `data`. Como nadie escribe en ella desde 2026-05-30, el tuner opera contra una BD congelada y sus `meta_recommendations` no se actualizan.
2. La BD local del usuario solo puede sincronizarse via la rama `data`. El `actions/cache` es opaco al usuario y volatil (GH lo evicta tras 7 dias sin acceso).
3. Sin commit a `data`, no hay historia versionada de los runs.

El patron legacy (ver `docs/legacy-context/lessons-learned.md §1.12`) era exactamente este: dual checkout, run, commit/push a `data` al final. Se reproduce literalmente, manteniendo el `actions/cache` como **complemento** (acelera el arranque sin sustituir la persistencia).

### Por que el segundo checkout va en `path: persist/`

El primer checkout (`ref: main`) se queda en el workdir raiz porque ahi vive el codigo del paquete y `pip install -e .` espera encontrar `pyproject.toml` ahi. El segundo checkout debe convivir sin pisar el primero: `path: persist/` lo aisla en un subdirectorio. Asi el `Run pipeline` opera sobre `data/` del workdir principal (limpio de cualquier cosa de la rama `data`) y el step de persistencia copia los outputs a `persist/data/`.

### Por que `token: ${{ secrets.GITHUB_TOKEN }}` + `persist-credentials: true`

El `actions/checkout@v4` por defecto inyecta credenciales temporales del job en el remoto `origin` del clon que crea. Si `persist-credentials: false`, esas credenciales se borran tras el checkout y el push del step posterior fallaria con `fatal: could not read Username`. Pasar el `GITHUB_TOKEN` explicitamente y `persist-credentials: true` garantiza que `git push origin data` desde dentro de `persist/` use el token del job (no hace falta PAT). Esto solo funciona porque elevamos `permissions.contents` a `write`.

### Por que `git diff --cached --quiet || (commit && push)`

Es el idiom canonico para "commit solo si hay cambios staged":

- `git diff --cached --quiet` sale con codigo 0 si NO hay diferencias staged (silencioso) y codigo 1 si las hay.
- `cmd_a || cmd_b` ejecuta `cmd_b` solo si `cmd_a` falla. Por tanto, `cmd_b` (commit + push) corre solo cuando hay cambios.
- El step entero sigue siendo `success` aunque no haya cambios (porque `cmd_a` salio 0). Evita commits ruidosos cuando el run no produjo deltas en `saas.db` ni `data/runs/`.

Alternativa descartada: `if ! git diff --cached --quiet; then ... fi`. Funciona igual pero el legacy y la mayoria de workflows del ecosistema usan el operador `||`. Mantenemos la convencion.

### Por que `cp -r data/runs/. persist/data/runs/` (con el punto)

El `.` final copia el **contenido** del directorio `data/runs/` en `persist/data/runs/`, no el directorio entero. Sin el punto, `cp -r data/runs persist/data/runs/` crearia `persist/data/runs/runs/` (duplicacion). Pequeno detalle de coreutils que evita un bug sutil de nesting.

### Por que `if: success()` en el step de persistencia

Si `Run pipeline` falla (excepcion no controlada), no queremos persistir un estado parcial corrupto en la rama `data`. El `if: success()` salta el step si cualquier step previo no termino con exito. El cache de GH y el artefacto siguen subiendose (el upload-artifact tiene `if: always()`) para que el usuario pueda inspeccionar el fallo via GH UI.

### Alternativas descartadas

- **PAT (Personal Access Token) via secret en vez de GITHUB_TOKEN**: anadiria un secret mas que mantener y rotar. El GITHUB_TOKEN automatico es suficiente porque el push va al mismo repo y `permissions: contents: write` ya autoriza la operacion.
- **`git ls-files --modified` antes de `git add`**: redundante. `git add` + `git diff --cached --quiet` ya cubre el caso "sin cambios" sin necesidad de un check previo.
- **Sustituir `actions/cache` por solo la rama `data`**: dejaria el primer arranque (cache vacio) bajando ~80 MB de la rama `data` antes de poder usar la BD. Mantener el cache como aceleracion da arranque sub-segundo cuando esta caliente.

## Impacto en el pipeline

1. **Cron diario (`0 8 * * *`)**: tras cada run exitoso, se commitea `data/saas.db` y `data/runs/` a la rama `data` con mensaje `chore: pipeline run YYYY-MM-DDTHH:MM:SSZ`. Eso desbloquea:
   - `tuner.yml` (que sigue leyendo de `data`) podra operar contra la BD actualizada y sus `meta_recommendations` se acumularan correctamente. La regla A4 (PR automatico de tuning) volvera a abrir PRs cuando los thresholds se cumplan.
   - El usuario podra sincronizar la BD local con un `git fetch origin data && git checkout origin/data -- data/saas.db data/runs/`.
   - La historia de runs queda versionada en la rama `data` (no solo en cache GH volatil).

2. **Tuner workflow**: no se modifica en esta feature. Heredara automaticamente la BD fresca de la rama `data` actualizada por cada run del pipeline.

3. **Concurrency**: sin cambios. Sigue siendo `group: 'saas-radar', cancel-in-progress: false`. La rama `data` no requiere lock externo porque solo el job de pipeline escribe en ella.

4. **`actions/cache` y `actions/upload-artifact`**: siguen activos. El cache evita que cada arranque baje la BD entera de la rama `data` (~80 MB → instantaneo desde cache). El artefacto sigue disponible 30 dias en la GH UI para inspeccion ad-hoc.

5. **Modulos del proyecto** (`src/`, `tests/`, etc.): **no se tocan**. Esta feature es 100% infra (workflow YAML) + documentacion.

## Explicacion tecnica

YAML linea por linea de los cambios:

### `permissions.contents: write` (linea 19)

Antes era `read`. El `GITHUB_TOKEN` que GH inyecta automaticamente en el job hereda los permisos declarados aqui. Con `read` se puede clonar pero NO hacer push. Con `write` el token puede commitear/pushear al mismo repo via HTTPS. Es el alcance minimo necesario (no necesitamos `pull-requests: write` porque no creamos PRs en este workflow — eso lo hace el A4 del tuner). Cuanto menos privilegio, mejor.

### Segundo checkout (lineas 44-51)

```yaml
- name: Checkout data branch
  uses: actions/checkout@v4
  with:
    ref: data
    path: persist
    fetch-depth: 1
    token: ${{ secrets.GITHUB_TOKEN }}
    persist-credentials: true
```

- `uses: actions/checkout@v4`: la action oficial de checkout, pinned a v4 (estable).
- `ref: data`: indica la rama remota concreta a clonar (no `main`).
- `path: persist`: el clon se hace dentro del subdirectorio `persist/` del workdir. Sin esto, sobrescribiria el workdir principal (donde vive el checkout de `main`).
- `fetch-depth: 1`: shallow clone, solo el ultimo commit de `data`. Suficiente porque solo necesitamos commitear encima, no inspeccionar historia.
- `token: ${{ secrets.GITHUB_TOKEN }}`: el token automatico del job. Como `permissions.contents: write` esta arriba, ese token tiene permiso de push al mismo repo.
- `persist-credentials: true`: por defecto la action borra las credenciales del `git config` tras checkout. Con `true`, las deja persistidas en `persist/.git/config` para que el `git push` del step posterior las use sin reautenticarse.

### Step "Persist to data branch" (lineas 83-97)

```yaml
- name: Persist to data branch
  if: success()
  run: |
    mkdir -p persist/data/runs
    if [ -f data/saas.db ]; then
      cp data/saas.db persist/data/saas.db
    fi
    if [ -d data/runs ]; then
      cp -r data/runs/. persist/data/runs/
    fi
    cd persist
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git config user.name "github-actions[bot]"
    git add data/saas.db data/runs/
    git diff --cached --quiet || (git commit -m "chore: pipeline run $(date -u +%FT%TZ)" && git push origin data)
```

- `if: success()`: condicion de step, solo corre si todos los steps anteriores terminaron OK. Es la red de seguridad contra persistir un estado parcial si el pipeline crashea a mitad.
- `run: |`: bloque shell multilinea. Por defecto bash con `set -e` (sale al primer error).
- `mkdir -p persist/data/runs`: crea el directorio destino para los outputs (idempotente con `-p`). Si la rama `data` ya tiene `data/runs/`, este comando es no-op.
- `if [ -f data/saas.db ]; then cp ...`: guard defensivo. Si `Run pipeline` no genero la BD por algun motivo (caso edge: scrape vacio + skip-ai), no fallamos. El test `[ -f X ]` es POSIX y devuelve 0 si X existe y es archivo regular.
- `if [ -d data/runs ]; then cp -r ...`: mismo guard para el directorio de runs. `cp -r src/. dst/` copia el contenido de `src/` dentro de `dst/` (el punto es clave; ver "Por que" arriba).
- `cd persist`: entra al clon de la rama `data`. Todos los `git` siguientes operan sobre **ese** repo, no sobre el principal.
- `git config user.email "github-actions[bot]@users.noreply.github.com"`: configura el autor del commit. El email noreply es el oficial para acciones automaticas y GH lo asocia al bot user, asi el commit no se asocia a una cuenta humana real.
- `git config user.name "github-actions[bot]"`: idem para el nombre. Convencion estandar de GH Actions.
- `git add data/saas.db data/runs/`: stagea los archivos modificados. Pasamos rutas concretas en vez de `git add -A` para evitar incluir basura accidental (rule del CLAUDE.md de proyecto).
- `git diff --cached --quiet || (...)`: el patron de "commit solo si hay cambios" explicado arriba.
  - `git diff --cached --quiet`: compara index vs HEAD. `--quiet` significa "no imprimir nada, solo devolver exit code".
  - `||`: shell OR. Si el comando previo devuelve != 0 (hay diferencias), ejecuta el bloque.
  - `(... && ...)`: subshell. Si el commit falla, el push no se ejecuta. Si ambos OK, el step termina con exit 0.
  - `date -u +%FT%TZ`: ISO 8601 en UTC. `-u` fuerza UTC, `%F` es `YYYY-MM-DD`, `%T` es `HH:MM:SS`, `Z` el sufijo literal. Resultado: `2026-06-12T08:30:00Z`.
  - `git push origin data`: push al remoto `origin` rama `data`. `origin` esta configurado por el `actions/checkout@v4` para apuntar a `https://github.com/<owner>/<repo>` y las credenciales persisten gracias a `persist-credentials: true`.

## Como sincronizar la BD local tras un run del cron

Tras un run exitoso del cron (o un `workflow_dispatch` manual), la rama `origin/data` tiene la BD actualizada. Para traerla al workdir local:

```bash
git fetch origin data
git checkout origin/data -- data/saas.db data/runs/
```

Esto:
1. `git fetch origin data` actualiza la referencia local `refs/remotes/origin/data` sin cambiar de rama.
2. `git checkout origin/data -- data/saas.db data/runs/` extrae **solo** los paths `data/saas.db` y `data/runs/` de la rama `data` y los pone en el workdir actual (sin cambiar HEAD ni la rama actual). Es la operacion segura para "sincronizar BD sin mezclar codigo".

Importante: NO hacer `git checkout data` (cambiaria la rama actual) ni `git pull origin data` (haria merge en la rama actual y mezclaria el codigo de `main` con el blob de la BD).

Si el usuario quiere automatizar esto, puede anadirlo a un script `scripts/sync_data.sh` (fuera del scope de esta feature).

## Tests anadidos

Ninguno automatizado.

**Limitacion conocida y aceptada por la feature**: los GitHub Actions workflows YAML solo pueden validarse en su totalidad ejecutandose en GH Actions. No existe un mock local fiable para `actions/checkout@v4` + `actions/cache@v4` + el entorno de runners. Las opciones disponibles son:

- **Validacion sintactica YAML**: `python -c "import yaml; yaml.safe_load(open('.github/workflows/pipeline.yml'))"` → ejecutada en local, sin excepcion. Confirma que la estructura YAML es parseable pero NO que GH Actions la acepte.
- **`actionlint`** (no instalado en el repo): podria detectar errores de schema GH Actions sin ejecutar. Fuera de scope para esta feature; podria anadirse como pre-commit en el futuro.
- **Verificacion en GH Actions real**: requiere mergear la PR y esperar al siguiente cron (o disparar `workflow_dispatch`). El acceptance de la feature lo lista como "verificacion manual: tras 1 cron real, origin/data tiene commit nuevo con timestamp de hoy y data/saas.db actualizado".

Por tanto, el reviewer debe validar:

1. La sintaxis YAML pasa (`./.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/pipeline.yml'))"` → exit 0).
2. Los acceptances de la feature se cumplen al inspeccionar el YAML (dual checkout, permissions write, step Persist con la logica correcta, cache y artifact preservados).
3. El usuario hara la verificacion en runtime tras mergear la PR (`gh run watch` + `git fetch origin data && git log origin/data -1`).

## Verificacion

```
$ ./.venv/bin/python -c "import yaml; data = yaml.safe_load(open('.github/workflows/pipeline.yml')); print('YAML valid; jobs:', list(data['jobs'].keys()))"
YAML valid; jobs: ['run']
```

YAML valido y parseable. Los acceptances 1-7 de la feature #22 se cumplen al inspeccionar el archivo:

1. Dual checkout main + data en `path: persist/` ✓ (lineas 38-51)
2. `permissions.contents: write` ✓ (linea 19)
3. Step "Persist to data branch" tras "Run pipeline" con copia, git config, add, commit/push condicional ✓ (lineas 83-97)
4. Usa `GITHUB_TOKEN` (no PAT) ✓ (linea 50)
5. `actions/cache` y `actions/upload-artifact` preservados ✓ (lineas 53-58, 99-106)
6. Sync local documentado arriba ✓
7. Limitacion de no-tests automatizados documentada ✓

`./init.sh` no se ejecuta como verificacion en esta feature porque no hay codigo Python tocado — solo YAML + docs. Los tests existentes del proyecto siguen pasando trivialmente (no hay cambios en `src/` ni `tests/`).
